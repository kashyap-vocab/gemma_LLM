"""
Auto-dialer: orchestrates concurrent outbound calls.
100% ORM-based approach.

call_status values used here:
  pending    → call set up, agent dispatched, awaiting SmartFlo trigger
  calling    → SmartFlo has initiated the outbound call (ringing)
  active     → customer picked up, conversation in progress
  incomplete → call connected but survey not completed
  completed  → call ended with a connected conversation
  missedcall → call attempted but customer did not connect
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

from db.database import get_db, SessionLocal
from db.models import Customer, CallMetadata
from api.smartflo_client import get_smartflo_client

logger = logging.getLogger(__name__)

router = APIRouter()

CONCURRENCY = 5

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
_state = {
    "active": False,
    "stop_requested": False,
    "current_index": -1,
    "total": 0,
    "completed": 0,
    "failed": 0,
    "active_calls": 0,
    "current_customer": None,
}

_event_queue: asyncio.Queue = asyncio.Queue()

def _push_event(event: dict) -> None:
    _event_queue.put_nowait(event)

def _normalize_phone(number: str) -> str:
    if not number: return ""
    clean = str(number).strip().replace(" ", "").replace("-", "").lstrip("+")
    if clean.startswith("91") and len(clean) > 10:
        clean = clean[2:]
    return clean

# ---------------------------------------------------------------------------
# Poll DB for call completion using SQLAlchemy ORM
# ---------------------------------------------------------------------------
async def _wait_for_call_completion(
    room_name: str,
    unanswered_timeout: int = 60,
    max_call_duration: int = 600,
) -> str:
    """
    Poll call_metadata until a terminal status is detected.
    Terminal states: completed | incomplete | missedcall
    Force-writes 'missedcall' to DB if timeout is reached without a terminal state.
    """
    poll_interval = 2
    elapsed = 0

    while elapsed < max_call_duration:
        if _state["stop_requested"]:
            return "stopped"

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        with SessionLocal() as db:
            try:
                call = db.query(CallMetadata).get(room_name)
                if call:
                    status = call.call_status
                    logger.debug(f"Poll {room_name}: status={status} ({elapsed}s)")

                    if status in ("completed", "incomplete", "missedcall"):
                        return status

                    # Still pending/active after unanswered_timeout — mark as missedcall
                    if status in ("calling", "pending") and elapsed >= unanswered_timeout:
                        call.call_status = "missedcall"
                        call.updated_at = datetime.now()
                        db.commit()
                        return "missedcall"
            except Exception as exc:
                logger.warning(f"Poll error for {room_name}: {exc}")

    # Hard cap reached without terminal status
    with SessionLocal() as db:
        try:
            call = db.query(CallMetadata).get(room_name)
            if call and call.call_status not in ("completed", "incomplete", "missedcall"):
                call.call_status = "missedcall"
                call.updated_at = datetime.now()
                db.commit()
        except Exception:
            pass

    return "missedcall"

# ---------------------------------------------------------------------------
# Per-customer coroutine
# ---------------------------------------------------------------------------
async def _call_one_customer(
    idx: int,
    customer: dict,
    total: int,
    semaphore: asyncio.Semaphore,
) -> None:
    async with semaphore:
        if _state["stop_requested"]:
            _push_event({"type": "stopped", "index": idx, "total": total})
            return

        # Slight stagger to prevent hammering the API/DB simultaneously
        await asyncio.sleep((idx % CONCURRENCY) * 0.2)

        agreement_no = customer["agreement_no"]
        customer_name = customer["customer_name"]
        phone_number = customer["contact_number"]
        phone_normalized = _normalize_phone(phone_number)
        room_name = f"call-{phone_normalized}"

        _state["current_index"] = idx
        _state["current_customer"] = customer_name
        _state["active_calls"] += 1

        _push_event({
            "type": "calling",
            "index": idx,
            "total": total,
            "agreement_no": agreement_no,
            "customer_name": customer_name,
            "phone_number": phone_number,
            "active_calls": _state["active_calls"],
        })

        try:
            # ── STEP 1: Create/update CallMetadata (status = pending) ──────────
            with SessionLocal() as db:
                call_meta = db.query(CallMetadata).get(room_name)
                if not call_meta:
                    call_meta = CallMetadata(call_id=room_name, call_count=0)
                    db.add(call_meta)

                call_meta.agreement_no = agreement_no
                call_meta.phone_number = phone_number
                call_meta.call_status = "pending"
                call_meta.call_count += 1
                call_meta.last_call_at = datetime.now()
                call_meta.updated_at = datetime.now()
                db.commit()

            # ── STEP 2: Dispatch LiveKit Agent ────────────────────────────────
            from livekit import api as livekit_api
            lk_url = os.getenv("LIVEKIT_URL", "").replace("wss://", "https://")
            lk_meta = json.dumps({
                "customer_phone": phone_normalized,
                "customer_name": customer_name,
                "agreement_no": agreement_no,
            })

            async with livekit_api.LiveKitAPI(
                url=lk_url,
                api_key=os.getenv("LIVEKIT_API_KEY", ""),
                api_secret=os.getenv("LIVEKIT_API_SECRET", ""),
            ) as lk:
                try:
                    await lk.room.delete_room(livekit_api.DeleteRoomRequest(room=room_name))
                except Exception:
                    pass

                await lk.room.create_room(livekit_api.CreateRoomRequest(
                    name=room_name,
                    empty_timeout=300,
                    metadata=lk_meta,
                    agents=[livekit_api.RoomAgentDispatch(
                        agent_name="LTFS_SurveyAgent-Soma",
                        metadata=lk_meta,
                    )],
                ))

            # Wait for agent to fully connect to the room before triggering SmartFlo.
            # The agent process needs to: receive the job dispatch → connect to the
            # room WebSocket → run prewarm → register session. 2.5s was too short.
            await asyncio.sleep(5.0)

            if _state["stop_requested"]:
                return

            # ── STEP 3: Trigger SmartFlo (status = active) ───────────────────
            with SessionLocal() as db:
                call_meta = db.query(CallMetadata).get(room_name)
                if call_meta:
                    call_meta.call_status = "calling"
                    call_meta.updated_at = datetime.now()
                    db.commit()

            smartflo_client = get_smartflo_client()
            call_result = await smartflo_client.initiate_call(
                to_number=phone_number,
                custom_params={"customer_name": customer_name, "agreement_no": agreement_no},
            )

            if not call_result["success"]:
                with SessionLocal() as db:
                    call_meta = db.query(CallMetadata).get(room_name)
                    if call_meta:
                        call_meta.call_status = "missedcall"
                        call_meta.updated_at = datetime.now()
                        db.commit()
                raise Exception(call_result.get("message", "SmartFlo trigger failed"))

            # ── STEP 4: Poll for terminal status ──────────────────────────────
            final_status = await _wait_for_call_completion(room_name)

            # ── STEP 5: Emit SSE event ────────────────────────────────────────
            if final_status in ("completed", "incomplete"):
                _state["completed"] += 1
            else:
                _state["failed"] += 1

            _push_event({
                "type": final_status,
                "index": idx,
                "total": total,
                "agreement_no": agreement_no,
                "customer_name": customer_name,
                "completed": _state["completed"],
                "failed": _state["failed"],
                "active_calls": _state["active_calls"] - 1,
            })

        except Exception as exc:
            logger.error(f"AutoDialer error for {customer_name}: {exc}")
            _state["failed"] += 1
            _push_event({
                "type": "missedcall",
                "index": idx,
                "total": total,
                "agreement_no": agreement_no,
                "reason": str(exc),
                "completed": _state["completed"],
                "failed": _state["failed"],
                "active_calls": _state["active_calls"] - 1,
            })
        finally:
            _state["active_calls"] = max(0, _state["active_calls"] - 1)


# ---------------------------------------------------------------------------
# Background Task & SSE
# ---------------------------------------------------------------------------

async def _run_auto_dialer(customers: list) -> None:
    global _state
    _state.update({
        "active": True, "stop_requested": False, "total": len(customers),
        "completed": 0, "failed": 0, "active_calls": 0
    })

    semaphore = asyncio.Semaphore(CONCURRENCY)
    tasks = [_call_one_customer(i, c, len(customers), semaphore) for i, c in enumerate(customers)]
    await asyncio.gather(*tasks)

    _push_event({"type": "finished", "total": len(customers), "completed": _state["completed"], "failed": _state["failed"]})
    _state["active"] = False

async def _sse_generator() -> AsyncGenerator[str, None]:
    yield f"data: {json.dumps({'type': 'status', **_state_snapshot()})}\n\n"
    while True:
        try:
            event = await asyncio.wait_for(_event_queue.get(), timeout=15.0)
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") in ("finished", "stopped"): break
        except asyncio.TimeoutError:
            yield "data: {\"type\": \"heartbeat\"}\n\n"

def _state_snapshot() -> dict:
    return {k: v for k, v in _state.items()} | {"concurrency": CONCURRENCY}

# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

class AutoDialerStartRequest(BaseModel):
    agreement_nos: Optional[List[str]] = None
    concurrency: Optional[int] = None

@router.post("/auto-dialer/start")
async def start_auto_dialer(
    request: AutoDialerStartRequest = AutoDialerStartRequest(),
    db: Session = Depends(get_db),
):
    global CONCURRENCY
    if _state["active"]:
        return {"success": False, "message": "Already running"}

    if request.concurrency:
        CONCURRENCY = max(1, min(20, request.concurrency))

    query = db.query(Customer)
    if request.agreement_nos:
        query = query.filter(Customer.agreement_no.in_(request.agreement_nos))

    ordered = query.order_by(Customer.uploaded_at.desc()).all()
    if not ordered:
        return {"success": False, "message": "No customers found"}

    customer_list = [
        {"agreement_no": c.agreement_no, "customer_name": c.customer_name, "contact_number": c.contact_number}
        for c in ordered
    ]

    asyncio.create_task(_run_auto_dialer(customer_list))
    return {"success": True, "total": len(customer_list)}

@router.get("/auto-dialer/events")
async def auto_dialer_events():
    return StreamingResponse(_sse_generator(), media_type="text/event-stream")

@router.post("/auto-dialer/stop")
async def stop_auto_dialer():
    _state["stop_requested"] = True
    return {"success": True}

@router.get("/auto-dialer/status")
async def get_auto_dialer_status():
    return _state_snapshot()