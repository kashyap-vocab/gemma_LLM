"""
Backend auto-dialer: orchestrates concurrent outbound calls (up to CONCURRENCY
at a time) and streams live status updates to the frontend via Server-Sent Events (SSE).

Flow per customer:
  1. Dispatch LiveKit agent (Route 1 logic)
  2. Wait 2 seconds for agent init
  3. Trigger SmartFlo outbound call (Route 2 logic)
  4. Poll DB until call_status = completed/failed (max 3 minutes)
  5. Push SSE event, decrement active-call slot (semaphore released)

Up to CONCURRENCY calls run simultaneously; the rest wait for a slot to open.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import AsyncGenerator, List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db.database import get_db
from db.models import ActiveCallContext, CallMetadata, CustomerData
from api.smartflo_client import get_smartflo_client

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Concurrency setting — must match num_idle_processes in web_rtc_server.py
# ---------------------------------------------------------------------------
# CONCURRENCY=4  ←→  num_idle_processes=4  (zero cold-start latency)
# SmartFlo: confirm your account has ≥4 outbound PSTN channels provisioned.
CONCURRENCY = 4

# ---------------------------------------------------------------------------
# Shared state (single process — uvicorn with one worker is the assumed setup)
# ---------------------------------------------------------------------------
_state = {
    "active": False,
    "stop_requested": False,
    "current_index": -1,
    "total": 0,
    "completed": 0,
    "failed": 0,
    "active_calls": 0,        # how many calls are in-flight right now
    "current_customer": None, # kept for backward compatibility (last started)
}

# Async queue — background task pushes dicts, SSE endpoint reads them
_event_queue: asyncio.Queue = asyncio.Queue()


# ---------------------------------------------------------------------------
# Helper: push an SSE event dict into the queue
# ---------------------------------------------------------------------------
def _push_event(event: dict) -> None:
    _event_queue.put_nowait(event)


# ---------------------------------------------------------------------------
# Helper: normalize phone number (mirrors customer_api.normalize_phone)
# ---------------------------------------------------------------------------
def _normalize_phone(number: str) -> str:
    if not number:
        return number
    clean = number.strip().replace(" ", "").replace("-", "")
    clean = clean.lstrip("+")
    if clean.startswith("91") and len(clean) > 10:
        clean = clean[2:]
    return clean


# ---------------------------------------------------------------------------
# Helper: poll DB for call completion
# ---------------------------------------------------------------------------
async def _wait_for_call_completion(
    phone_number: str,
    unanswered_timeout: int = 60,
    max_call_duration: int = 600,
) -> str:
    """
    Poll active_call_context every 1 s.

    Logic:
      - If status reaches 'completed'/'failed' → return immediately.
      - If status stays 'calling' (not picked up) for > unanswered_timeout s → 'timeout'.
      - If status is 'active' (call answered), keep waiting up to max_call_duration.
      - Hard ceiling of max_call_duration seconds total regardless of status.

    Returns one of: "completed", "failed", "timeout", "stopped"
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        logger.warning("DATABASE_URL not set — cannot poll call status")
        await asyncio.sleep(unanswered_timeout)
        return "timeout"

    import psycopg2

    poll_interval = 1
    elapsed = 0
    conn = None

    try:
        conn = psycopg2.connect(database_url)
    except Exception as exc:
        logger.warning(f"DB connect error for {phone_number}: {exc}")
        await asyncio.sleep(unanswered_timeout)
        return "timeout"

    try:
        while elapsed < max_call_duration:
            if _state["stop_requested"]:
                return "stopped"

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT call_status FROM active_call_context "
                        "WHERE phone_number = %s ORDER BY updated_at DESC LIMIT 1",
                        (phone_number,),
                    )
                    row = cur.fetchone()
                    if row:
                        status = row[0]
                        logger.debug(f"Poll {phone_number}: status={status} ({elapsed}s)")
                        if status in ("completed", "failed"):
                            return status
                        # No answer: still ringing after unanswered_timeout
                        if status in ("calling", "agent_ready") and elapsed >= unanswered_timeout:
                            return "timeout"
                        # 'active' → call was answered; keep waiting
            except Exception as exc:
                logger.warning(f"Poll error for {phone_number}: {exc}")
                try:
                    conn.close()
                except Exception:
                    pass
                try:
                    conn = psycopg2.connect(database_url)
                except Exception as reconnect_exc:
                    logger.warning(f"Reconnect failed for {phone_number}: {reconnect_exc}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return "timeout"


# ---------------------------------------------------------------------------
# Per-customer coroutine — runs inside a semaphore slot
# ---------------------------------------------------------------------------
async def _call_one_customer(
    idx: int,
    customer: dict,
    total: int,
    semaphore: asyncio.Semaphore,
) -> None:
    """
    Handle a single customer call end-to-end.
    The semaphore limits how many of these can run concurrently.
    """
    async with semaphore:
        if _state["stop_requested"]:
            _push_event({"type": "stopped", "index": idx, "total": total})
            return

        # Stagger: 150 ms × slot-position within each batch of CONCURRENCY.
        # Prevents all 4 calls hitting SmartFlo + LiveKit APIs at the exact
        # same instant (thundering-herd) while remaining effectively parallel.
        stagger_ms = (idx % CONCURRENCY) * 0.150   # 0 ms, 150 ms, 300 ms, 450 ms
        if stagger_ms > 0:
            await asyncio.sleep(stagger_ms)

        if _state["stop_requested"]:
            _push_event({"type": "stopped", "index": idx, "total": total})
            return

        customer_id = customer["id"]
        customer_name = customer["customer_name"]
        phone_number = customer["contact_number"]
        phone_normalized = _normalize_phone(phone_number)
        room_name = f"call-{phone_normalized}"

        _state["current_index"] = idx          # last-started (informational)
        _state["current_customer"] = customer_name
        _state["active_calls"] += 1

        logger.info(
            f"[AutoDialer] [{idx+1}/{total}] Calling {customer_name} ({phone_number}) "
            f"[active={_state['active_calls']}]"
        )

        _push_event(
            {
                "type": "calling",
                "index": idx,
                "total": total,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "phone_number": phone_number,
                "active_calls": _state["active_calls"],
            }
        )

        lk_url = os.getenv("LIVEKIT_URL", "")
        lk_api_key = os.getenv("LIVEKIT_API_KEY", "")
        lk_api_secret = os.getenv("LIVEKIT_API_SECRET", "")
        api_url = lk_url.replace("wss://", "https://")
        database_url = os.getenv("DATABASE_URL")

        try:
            import psycopg2
            import json as _json

            # ------------------------------------------------------------------
            # Step 1: Route 1 — store context + dispatch agent to LiveKit room
            # ------------------------------------------------------------------
            if database_url:
                try:
                    conn = psycopg2.connect(database_url)
                    try:
                        with conn.cursor() as cur:
                            # Upsert active_call_context
                            cur.execute(
                                "SELECT id FROM active_call_context WHERE phone_number = %s",
                                (phone_number,),
                            )
                            existing = cur.fetchone()
                            if existing:
                                cur.execute(
                                    """
                                    UPDATE active_call_context
                                    SET customer_name = %s, customer_id = %s,
                                        call_status = 'agent_ready', call_id = %s,
                                        updated_at = NOW()
                                    WHERE phone_number = %s
                                    """,
                                    (customer_name, customer_id, room_name, phone_number),
                                )
                            else:
                                cur.execute(
                                    """
                                    INSERT INTO active_call_context
                                        (phone_number, customer_name, customer_id, call_status, call_id)
                                    VALUES (%s, %s, %s, 'agent_ready', %s)
                                    """,
                                    (phone_number, customer_name, customer_id, room_name),
                                )

                            # Upsert call_metadata
                            cur.execute(
                                "SELECT id FROM call_metadata WHERE call_id = %s",
                                (room_name,),
                            )
                            meta_existing = cur.fetchone()
                            if meta_existing:
                                cur.execute(
                                    """
                                    UPDATE call_metadata
                                    SET customer_phone = %s, customer_name = %s
                                    WHERE call_id = %s
                                    """,
                                    (phone_number, customer_name, room_name),
                                )
                            else:
                                cur.execute(
                                    """
                                    INSERT INTO call_metadata (call_id, customer_phone, customer_name)
                                    VALUES (%s, %s, %s)
                                    """,
                                    (room_name, phone_number, customer_name),
                                )

                        conn.commit()
                    finally:
                        conn.close()
                except Exception as db_exc:
                    logger.error(f"DB upsert error for {phone_number}: {db_exc}")

            # Create LiveKit room and dispatch agent
            from livekit import api as livekit_api

            room_metadata = _json.dumps(
                {
                    "customer_phone": phone_normalized,
                    "customer_name": customer_name,
                    "customer_id": customer_id,
                }
            )

            async with livekit_api.LiveKitAPI(
                url=api_url,
                api_key=lk_api_key,
                api_secret=lk_api_secret,
            ) as lk:
                # Delete stale room first
                try:
                    await lk.room.delete_room(
                        livekit_api.DeleteRoomRequest(room=room_name)
                    )
                except Exception:
                    pass

                await lk.room.create_room(
                    livekit_api.CreateRoomRequest(
                        name=room_name,
                        empty_timeout=300,
                        metadata=room_metadata,
                        agents=[
                            livekit_api.RoomAgentDispatch(
                                agent_name="LTFS_SurveyAgent-Soma",
                                metadata=room_metadata,
                            )
                        ],
                    )
                )

            logger.info(f"[AutoDialer] Agent dispatched to {room_name}")

            # ------------------------------------------------------------------
            # Step 2: Wait 2 seconds for agent to initialize
            # ------------------------------------------------------------------
            await asyncio.sleep(2)

            if _state["stop_requested"]:
                _state["active_calls"] -= 1
                _push_event({"type": "stopped", "index": idx, "total": total})
                return

            # ------------------------------------------------------------------
            # Step 3: Route 2 — update status to 'calling' + trigger SmartFlo
            # ------------------------------------------------------------------
            if database_url:
                try:
                    conn = psycopg2.connect(database_url)
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE active_call_context
                                SET call_status = 'calling', updated_at = NOW()
                                WHERE phone_number = %s
                                """,
                                (phone_number,),
                            )
                        conn.commit()
                    finally:
                        conn.close()
                except Exception as db_exc:
                    logger.error(f"Status update error for {phone_number}: {db_exc}")

            smartflo_client = get_smartflo_client()
            call_result = await smartflo_client.initiate_call(
                to_number=phone_number,
                custom_params={
                    "customer_name": customer_name,
                    "customer_id": customer_id,
                },
            )

            if not call_result["success"]:
                logger.error(
                    f"[AutoDialer] SmartFlo failed for {customer_name}: {call_result.get('error')}"
                )
                # Mark as failed in DB
                if database_url:
                    try:
                        conn = psycopg2.connect(database_url)
                        try:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "UPDATE active_call_context SET call_status='failed', updated_at=NOW() WHERE phone_number=%s",
                                    (phone_number,),
                                )
                            conn.commit()
                        finally:
                            conn.close()
                    except Exception:
                        pass

                _state["failed"] += 1
                _state["active_calls"] -= 1
                _push_event(
                    {
                        "type": "failed",
                        "index": idx,
                        "total": total,
                        "customer_id": customer_id,
                        "customer_name": customer_name,
                        "reason": call_result.get("message", "SmartFlo error"),
                        "completed": _state["completed"],
                        "failed": _state["failed"],
                        "active_calls": _state["active_calls"],
                    }
                )
                return

            logger.info(
                f"[AutoDialer] SmartFlo call initiated for {customer_name}, "
                f"sid={call_result.get('call_sid')}"
            )

            # ------------------------------------------------------------------
            # Step 4: Wait for call to complete
            # ------------------------------------------------------------------
            final_status = await _wait_for_call_completion(phone_number)

            if final_status == "completed":
                _state["completed"] += 1
                _state["active_calls"] -= 1
                _push_event(
                    {
                        "type": "completed",
                        "index": idx,
                        "total": total,
                        "customer_id": customer_id,
                        "customer_name": customer_name,
                        "phone_number": phone_number,
                        "completed": _state["completed"],
                        "failed": _state["failed"],
                        "active_calls": _state["active_calls"],
                    }
                )
            elif final_status == "stopped":
                _state["active_calls"] -= 1
                _push_event(
                    {
                        "type": "stopped",
                        "index": idx,
                        "total": total,
                        "customer_id": customer_id,
                        "active_calls": _state["active_calls"],
                    }
                )
            else:
                # failed or timeout
                _state["failed"] += 1
                _state["active_calls"] -= 1
                _push_event(
                    {
                        "type": "failed" if final_status == "failed" else "timeout",
                        "index": idx,
                        "total": total,
                        "customer_id": customer_id,
                        "customer_name": customer_name,
                        "reason": "Call failed" if final_status == "failed" else "No answer / timeout",
                        "completed": _state["completed"],
                        "failed": _state["failed"],
                        "active_calls": _state["active_calls"],
                    }
                )

        except Exception as exc:
            logger.error(
                f"[AutoDialer] Unexpected error for {customer_name}: {exc}", exc_info=True
            )
            _state["failed"] += 1
            _state["active_calls"] = max(0, _state["active_calls"] - 1)
            _push_event(
                {
                    "type": "failed",
                    "index": idx,
                    "total": total,
                    "customer_id": customer_id,
                    "customer_name": customer_name,
                    "reason": str(exc),
                    "completed": _state["completed"],
                    "failed": _state["failed"],
                    "active_calls": _state["active_calls"],
                }
            )


# ---------------------------------------------------------------------------
# Core background task: dispatch all customers concurrently (max CONCURRENCY)
# ---------------------------------------------------------------------------
async def _run_auto_dialer(customers: list) -> None:
    """
    Concurrent auto-dialer.  Runs as a background asyncio task.
    Uses a semaphore to cap at CONCURRENCY simultaneous calls.
    """
    global _state

    _state.update(
        {
            "active": True,
            "stop_requested": False,
            "current_index": -1,
            "total": len(customers),
            "completed": 0,
            "failed": 0,
            "active_calls": 0,
            "current_customer": None,
        }
    )

    # Short pause so the frontend's new SSE connection can be established
    # before the first 'calling' event is pushed into the queue.
    await asyncio.sleep(0.4)

    semaphore = asyncio.Semaphore(CONCURRENCY)

    # Create one coroutine per customer and run them all; the semaphore keeps
    # at most CONCURRENCY running at any instant.
    tasks = [
        _call_one_customer(idx, customer, len(customers), semaphore)
        for idx, customer in enumerate(customers)
    ]

    await asyncio.gather(*tasks, return_exceptions=True)

    # Loop finished
    _push_event(
        {
            "type": "finished",
            "total": len(customers),
            "completed": _state["completed"],
            "failed": _state["failed"],
        }
    )

    _state["active"] = False
    _state["active_calls"] = 0
    _state["current_customer"] = None
    _state["current_index"] = -1
    logger.info(
        f"[AutoDialer] Done. completed={_state['completed']}, failed={_state['failed']}"
    )


# ---------------------------------------------------------------------------
# SSE generator
# ---------------------------------------------------------------------------
async def _sse_generator() -> AsyncGenerator[str, None]:
    """
    Yield SSE-formatted messages from the event queue until a terminal event
    (finished / stopped) or a client disconnect.
    Note: queue is cleared at /start, not here — so events pushed between
    /start and this SSE connect (e.g. the first 'calling' event) are preserved.
    """
    # Send current state immediately on connect
    yield f"data: {json.dumps({'type': 'status', **_state_snapshot()})}\n\n"

    while True:
        try:
            event = await asyncio.wait_for(_event_queue.get(), timeout=20.0)
        except asyncio.TimeoutError:
            # Heartbeat to keep connection alive
            yield "data: {\"type\": \"heartbeat\"}\n\n"
            continue

        yield f"data: {json.dumps(event)}\n\n"

        if event.get("type") in ("finished", "stopped"):
            break


def _state_snapshot() -> dict:
    return {
        "active": _state["active"],
        "current_index": _state["current_index"],
        "total": _state["total"],
        "completed": _state["completed"],
        "failed": _state["failed"],
        "active_calls": _state["active_calls"],
        "current_customer": _state["current_customer"],
        "concurrency": CONCURRENCY,
    }


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class AutoDialerStartRequest(BaseModel):
    customer_ids: Optional[List[int]] = None  # if None → call all customers
    concurrency: Optional[int] = None          # override CONCURRENCY at runtime


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@router.post("/auto-dialer/start")
async def start_auto_dialer(
    request: AutoDialerStartRequest = AutoDialerStartRequest(),
    db: Session = Depends(get_db),
):
    """
    Start the auto-dialer.  Fetches customers from DB and launches
    a background task that calls up to CONCURRENCY customers at a time.
    You can override the concurrency limit per-request via the 'concurrency' field.
    """
    global CONCURRENCY

    if _state["active"]:
        return {"success": False, "message": "Auto-dialer is already running"}

    # Allow runtime override of concurrency (clamped to 1–20)
    if request.concurrency is not None:
        CONCURRENCY = max(1, min(20, request.concurrency))
        logger.info(f"[AutoDialer] Concurrency set to {CONCURRENCY}")

    # Clear any stale events from a previous run so the SSE client receives
    # only events from this run (including the very first 'calling' event).
    while not _event_queue.empty():
        try:
            _event_queue.get_nowait()
        except asyncio.QueueEmpty:
            break

    # Fetch customers and preserve display order
    if request.customer_ids:
        rows = db.query(CustomerData).filter(
            CustomerData.id.in_(request.customer_ids)
        ).all()
        id_to_customer = {c.id: c for c in rows}
        ordered = [id_to_customer[cid] for cid in request.customer_ids if cid in id_to_customer]
    else:
        ordered = db.query(CustomerData).order_by(
            CustomerData.uploaded_at.desc(),
            CustomerData.id.asc(),
        ).all()

    if not ordered:
        return {"success": False, "message": "No customers found to call"}

    customer_list = [
        {
            "id": int(c.id),
            "customer_name": c.customer_name,
            "contact_number": c.contact_number,
        }
        for c in ordered
    ]

    # Launch background task
    asyncio.create_task(_run_auto_dialer(customer_list))

    logger.info(
        f"[AutoDialer] Started — {len(customer_list)} customers queued, "
        f"concurrency={CONCURRENCY}"
    )
    return {
        "success": True,
        "message": f"Auto-dialer started for {len(customer_list)} customers (concurrency={CONCURRENCY})",
        "total": len(customer_list),
        "concurrency": CONCURRENCY,
    }


@router.get("/auto-dialer/events")
async def auto_dialer_events():
    """
    Server-Sent Events stream.  Connect here to receive real-time updates
    while the auto-dialer is running.
    """
    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable Nginx buffering
        },
    )


@router.post("/auto-dialer/stop")
async def stop_auto_dialer():
    """
    Request the auto-dialer to stop.
    All in-flight calls complete normally; no new slots are opened.
    """
    if not _state["active"]:
        return {"success": False, "message": "Auto-dialer is not running"}

    _state["stop_requested"] = True
    logger.info("[AutoDialer] Stop requested")
    return {"success": True, "message": "Stop signal sent — in-flight calls will finish"}


@router.get("/auto-dialer/status")
async def get_auto_dialer_status():
    """
    Returns the current auto-dialer state snapshot.
    Useful for reconnecting the frontend after a page refresh.
    """
    return _state_snapshot()
