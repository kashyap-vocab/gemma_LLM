"""
Database storage helpers for the agent.

Session-based call state management:
  - feedback_sessions holds all in-memory state per call (transcript buffer,
    survey data, timing, disposition).
  - On call disconnect the session is flushed according to disposition:
      connected     → write transcript to Conversation + write CustomerFeedback
      not_connected → skip both (only call_metadata is finalized)
  - call_metadata is ALWAYS updated regardless of outcome.
"""
import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any, Optional, Tuple

from db.database import SessionLocal
from db.models import CallMetadata, Conversation, CustomerFeedback

logger = logging.getLogger(__name__)

# ── In-memory session store ───────────────────────────────────────────────────
# Keyed by call_id (LiveKit room name). Holds all transient data for the call.
feedback_sessions: dict[str, dict] = {}


def _default_feedback_session() -> dict:
    """Blank session for a new call."""
    return {
        # Relational identifiers
        "agreement_no": None,
        "customer_phone": None,
        "customer_name": None,
        # Disposition: "connected" (identity confirmed YES) | "not_connected"
        "disposition": "not_connected",
        # True once participant_connected fires — customer physically answered the phone.
        # Used to distinguish "answered but hung up early" from "never answered".
        "call_answered": False,
        # Buffered transcript — list of dicts: {role, text, speaker_id}
        # Flushed to the Conversation table at the end of the call (if call_answered).
        "transcript_buffer": [],
        # Survey / feedback fields
        "stage": "greeting",
        "started_at": datetime.now(timezone.utc),
        "identity_confirmed": None,
        "loan_taken": None,
        "last_month_payment": None,
        "payment": {},
        "confirmed": None,
        "category": None,
    }


def _to_numeric(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coerce_payment_date(value: Any) -> Optional[date]:
    """Normalize agent/session payment dates (e.g. dd-mm-yyyy) to a DB date."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    logger.warning("Could not parse payment_date %r; storing null", value)
    return None


def _coerce_bool(value: Any) -> Optional[bool]:
    """Normalize booleans from mixed agent/session payloads."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("yes", "y", "true", "1", "haan", "ha", "h", "हाँ", "हां"):
        return True
    if text in ("no", "n", "false", "0", "nah", "नहीं", "ना"):
        return False
    return None


# ── Call metadata lookup ──────────────────────────────────────────────────────

def _load_call_metadata(
    call_id: str,
    customer_phone: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    ORM-based lookup. Returns (phone_number, customer_name, agreement_no).
    Does NOT update call_status — status transitions are owned by the API layer.
    """
    with SessionLocal() as db:
        try:
            # Step 1: primary-key lookup (fastest)
            call = db.query(CallMetadata).get(call_id)

            # Step 2: fallback by phone number
            if not call and customer_phone:
                call = (
                    db.query(CallMetadata)
                    .filter(CallMetadata.phone_number == customer_phone)
                    .order_by(CallMetadata.updated_at.desc())
                    .first()
                )

            if call:
                cust_name = call.customer.customer_name if call.customer else None
                return call.phone_number, cust_name, call.agreement_no

            return None, None, None
        except Exception as e:
            logger.error(f"Error in _load_call_metadata: {e}")
            return None, None, None


# ── Transcript buffer ─────────────────────────────────────────────────────────

def buffer_transcript_turn(
    call_id: str,
    role: str,
    text: str,
    speaker_id: Optional[str] = None,
) -> None:
    """Append a conversation turn to the in-memory buffer for this call."""
    if not text:
        return
    session = feedback_sessions.setdefault(call_id, _default_feedback_session())
    session["transcript_buffer"].append({
        "role": role,
        "text": text,
        "speaker_id": speaker_id,
    })


def _flush_transcript_buffer_sync(
    call_id: str,
    agreement_no: Optional[str],
    customer_phone: Optional[str],
) -> None:
    """Write buffered transcript turns to the Conversation table (bulk insert)."""
    session = feedback_sessions.get(call_id, {})
    transcript_buffer = session.get("transcript_buffer", [])
    if not transcript_buffer:
        return

    with SessionLocal() as db:
        try:
            for turn in transcript_buffer:
                role = turn.get("role", "")
                text = turn.get("text", "")
                speaker_id = turn.get("speaker_id")
                db.add(Conversation(
                    call_id=call_id,
                    agreement_no=agreement_no,
                    customer_phone=customer_phone,
                    customer_transcript=text if role == "user" else None,
                    agent_transcript=text if role == "assistant" else None,
                    speaker_id=speaker_id if role == "user" else None,
                    language="hi",
                ))
            db.commit()
            logger.info(f"Flushed {len(transcript_buffer)} transcript turns for {call_id}")
        except Exception as e:
            db.rollback()
            logger.error(f"Error flushing transcript buffer for {call_id}: {e}")


# ── Feedback persistence ──────────────────────────────────────────────────────

def _persist_feedback_to_db_sync(call_id: str, agreement_no: Optional[str] = None) -> None:
    """
    Disposition-based persistence:
      connected     → flush transcript buffer → write CustomerFeedback
      not_connected → skip both
      always        → finalize call_metadata (status + duration)
    """
    data = feedback_sessions.get(call_id)
    if not data:
        return

    disposition = data.get("disposition", "not_connected")
    call_answered = data.get("call_answered", False)
    ano = agreement_no or data.get("agreement_no")
    customer_phone = data.get("customer_phone")
    started_at = data.get("started_at")

    # ── Determine final call_status ──────────────────────────────────────────
    # connected + survey confirmed  → completed
    # connected + survey not done   → incomplete
    # call_answered but no identity → incomplete  (customer picked up, hung up early)
    # never answered                → missedcall
    if disposition == "connected":
        final_status = "completed" if data.get("category") == "COMPLETE_SURVEY" else "incomplete"
    elif call_answered:
        final_status = "incomplete"   # Picked up, heard greeting, but no survey data
    else:
        final_status = "missedcall"   # Phone never answered

    # ── Always: finalize call_metadata ───────────────────────────────────────
    with SessionLocal() as db:
        try:
            call = db.query(CallMetadata).get(call_id)
            if call:
                call.call_status = final_status
                call.updated_at = datetime.now(timezone.utc)
                if started_at:
                    elapsed = (datetime.now(timezone.utc) - started_at).seconds
                    call.call_duration = elapsed
                db.commit()
        except Exception as e:
            logger.error(f"Error finalizing call_metadata for {call_id}: {e}")

    # ── Conditional: transcript + feedback only when call was answered ────────
    if not call_answered:
        logger.info(f"Call {call_id} never answered — skipping transcript/feedback writes ({final_status})")
        return

    # Flush transcript buffer to Conversation table (greeting + any turns collected)
    _flush_transcript_buffer_sync(call_id, ano, customer_phone)

    # Write CustomerFeedback for all answered calls (connected + not_connected).
    with SessionLocal() as db:
        try:
            # Upsert key:
            # - Prefer agreement_no when available (more stable for repeated retries)
            # - Fallback to call_id otherwise.
            feedback = None
            if ano:
                feedback = (
                    db.query(CustomerFeedback)
                    .filter(CustomerFeedback.agreement_no == ano)
                    .order_by(CustomerFeedback.created_at.desc())
                    .first()
                )
            if feedback is None:
                feedback = (
                    db.query(CustomerFeedback)
                    .filter(CustomerFeedback.call_id == call_id)
                    .order_by(CustomerFeedback.created_at.desc())
                    .first()
                )
            if not feedback:
                feedback = CustomerFeedback(call_id=call_id)
                db.add(feedback)

            payment = data.get("payment") or {}

            feedback.agreement_no = ano
            feedback.customer_phone = customer_phone
            feedback.customer_name = data.get("customer_name")
            feedback.disposition = "connected" if disposition == "connected" else "not_connected"
            # Keep sub_disposition if already set by backfill; otherwise allow session value.
            if data.get("sub_disposition"):
                feedback.sub_disposition = data.get("sub_disposition")

            # Identity & loan
            identity_val = str(data.get("identity_confirmed") or "").upper()
            feedback.identity_confirmed = identity_val == "YES"
            feedback.loan_taken = data.get("loan_taken") is True
            feedback.last_month_payment = _coerce_bool(data.get("last_month_payment"))

            # Payment details
            feedback.payee = payment.get("payee") or data.get("payee")
            feedback.payee_name = payment.get("payee_name") or data.get("payee_name")
            feedback.payee_contact = payment.get("payee_contact") or data.get("payee_contact")
            feedback.payment_date = _coerce_payment_date(
                payment.get("date") or data.get("payment_date")
            )
            feedback.payment_amount = payment.get("amount") or data.get("payment_amount")
            feedback.payment_mode = payment.get("mode") or data.get("payment_mode")
            feedback.payment_reason = payment.get("reason") or data.get("payment_reason")

            # Field executive
            feedback.field_executive_name = (
                payment.get("field_executive_name") or data.get("field_executive_name")
            )
            feedback.field_executive_contact = (
                payment.get("field_executive_contact") or data.get("field_executive_contact")
            )

            # Survey outcome
            feedback.stage = data.get("stage")
            feedback.confirmed = data.get("confirmed")
            feedback.category = data.get("category")
            feedback.started_at = started_at
            # Persist Smartflow callSid (if captured from participant identity).
            # We keep call_id as the LiveKit room key for relational integrity.
            feedback.conversation_id = data.get("smartflow_call_id")
            feedback.call_timestamp = datetime.now(timezone.utc)

            db.commit()
            logger.info(f"Feedback persisted for {call_id} (agreement={ano})")
        except Exception as e:
            db.rollback()
            logger.error(f"Error persisting feedback for {call_id}: {e}")


async def persist_feedback_to_db(call_id: str, agreement_no: Optional[str] = None) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _persist_feedback_to_db_sync, call_id, agreement_no)


# ── Call status updates ───────────────────────────────────────────────────────

def _update_call_status_sync(call_id: str, status: str) -> None:
    """Direct status update — used for intermediate states only."""
    with SessionLocal() as db:
        try:
            call = db.query(CallMetadata).get(call_id)
            if call:
                call.call_status = status
                call.updated_at = datetime.now(timezone.utc)
                db.commit()
        except Exception as e:
            logger.error(f"Status update failed for {call_id}: {e}")


async def update_call_status(call_id: str, status: str) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _update_call_status_sync, call_id, status)
