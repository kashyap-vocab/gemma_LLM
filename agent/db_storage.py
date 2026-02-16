"""
Database storage helpers for conversation and feedback data.

NOTE: This module uses raw psycopg2 for database operations.
It can be migrated to use SQLAlchemy ORM models (db/models.py) in the future.
"""
import os
import asyncio
from datetime import datetime, timezone
from typing import Optional

# Global session store for feedback data
feedback_sessions: dict[str, dict] = {}


def _default_feedback_session(call_id: str) -> dict:
    """Create default feedback session dict."""
    return {
        "stage": "greeting",
        "started": datetime.now(timezone.utc).isoformat(),
        "started_at": datetime.now(timezone.utc),
        "customer_name": None,
        "identity_confirmed": None,
        "loan_taken": None,
        "last_month_payment": None,
        "payment": {},
        "confirmed": None,
        "category": None,
    }


def _to_numeric(v) -> Optional[float]:
    """Convert value to numeric, return None if invalid."""
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _load_call_metadata(call_id: str, customer_phone: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """
    Load customer phone and name from database.

    Priority 1: Try active_call_context by phone number (most reliable)
    Priority 2: Try call_metadata by call_id (fallback)

    Args:
        call_id: Call identifier from room name
        customer_phone: Customer phone number from room metadata (if available)

    Returns:
        Tuple of (customer_phone, customer_name)
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return None, None

    try:
        import psycopg2
        conn = psycopg2.connect(database_url)
        try:
            with conn.cursor() as cur:
                # Priority 1: Lookup by phone number in active_call_context
                if customer_phone:
                    print(f"🔍 Looking up customer by phone: {customer_phone}")
                    cur.execute(
                        """
                        SELECT phone_number, customer_name, call_status
                        FROM active_call_context
                        WHERE phone_number = %s
                        ORDER BY updated_at DESC
                        LIMIT 1
                        """,
                        (customer_phone,)
                    )
                    row = cur.fetchone()
                    if row:
                        phone, name, status = row
                        print(f"✅ Found in active_call_context: {name} ({phone}), status={status}")

                        # Update status to 'active' and store call_id
                        cur.execute(
                            """
                            UPDATE active_call_context
                            SET call_status = 'active', call_id = %s, updated_at = NOW()
                            WHERE phone_number = %s
                            """,
                            (call_id, customer_phone)
                        )
                        conn.commit()
                        print(f"📝 Updated call status to 'active' for {customer_phone}")

                        return (phone, name)

                # Priority 2: Fallback to call_metadata by call_id
                print(f"🔍 Looking up customer by call_id: {call_id}")
                cur.execute(
                    "SELECT customer_phone, customer_name FROM call_metadata WHERE call_id = %s ORDER BY created_at DESC LIMIT 1",
                    (call_id,)
                )
                row = cur.fetchone()
                if row:
                    print(f"✅ Found in call_metadata: {row[1]} ({row[0]})")
                    return (row[0], row[1])

                print(f"⚠️ No customer metadata found for call_id={call_id}, phone={customer_phone}")
                return (None, None)
        finally:
            conn.close()
    except Exception as e:
        print(f"Warning: Could not load call metadata: {e}")
        import traceback
        traceback.print_exc()
        return None, None


def _store_conversation_turn_sync(
    call_id: str,
    customer_phone: Optional[str],
    customer_transcript: Optional[str] = None,
    agent_transcript: Optional[str] = None,
    speaker_id: Optional[str] = None,
    language: Optional[str] = None,
) -> None:
    """Synchronous DB write for conversation."""
    if not customer_transcript and not agent_transcript:
        return
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return
    try:
        import psycopg2
        conn = psycopg2.connect(database_url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO conversation (call_id, customer_phone, customer_transcript, agent_transcript, speaker_id, language)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        call_id,
                        customer_phone or None,
                        customer_transcript or None,
                        agent_transcript or None,
                        speaker_id or None,
                        language or None,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"Error storing conversation turn: {e}")


async def store_conversation_turn(
    call_id: str,
    customer_phone: Optional[str],
    customer_transcript: Optional[str] = None,
    agent_transcript: Optional[str] = None,
    speaker_id: Optional[str] = None,
    language: Optional[str] = None,
) -> None:
    """Async wrapper for conversation storage."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _store_conversation_turn_sync,
        call_id,
        customer_phone,
        customer_transcript,
        agent_transcript,
        speaker_id,
        language,
    )


def _persist_feedback_to_db_sync(call_id: str, customer_phone: Optional[str] = None) -> None:
    """Synchronous DB write for feedback."""
    data = feedback_sessions.get(call_id)
    if not data:
        return
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return
    phone = customer_phone or call_id
    payment = data.get("payment") or {}
    row = (
        phone,
        call_id,
        data.get("identity_confirmed"),
        data.get("loan_taken"),
        data.get("last_month_payment"),
        payment.get("payee") or data.get("payee"),
        payment.get("payee_name") or data.get("payee_name"),
        payment.get("payee_contact") or data.get("payee_contact"),
        payment.get("date") or payment.get("payment_date") or data.get("payment_date"),
        payment.get("mode") or payment.get("payment_mode") or data.get("payment_mode"),
        payment.get("reason") or payment.get("payment_reason") or data.get("payment_reason"),
        _to_numeric(payment.get("amount") or data.get("payment_amount")),
        payment.get("field_executive_name") or data.get("field_executive_name"),
        payment.get("field_executive_contact") or data.get("field_executive_contact"),
        data.get("stage"),
        data.get("customer_name"),
        data.get("confirmed"),
        data.get("category"),
        data.get("started_at"),
    )
    try:
        import psycopg2
        conn = psycopg2.connect(database_url)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM customer_feedback_data WHERE call_id = %s ORDER BY created_at DESC LIMIT 1", (call_id,))
                existing = cur.fetchone()
                if existing:
                    cur.execute(
                        """
                        UPDATE customer_feedback_data SET
                            customer_phone = %s, identity_confirmed = %s, loan_taken = %s, last_month_payment = %s,
                            payee = %s, payee_name = %s, payee_contact = %s, payment_date = %s, payment_mode = %s,
                            payment_reason = %s, payment_amount = %s, field_executive_name = %s, field_executive_contact = %s,
                            stage = %s, customer_name = %s, confirmed = %s, category = %s, started_at = %s
                        WHERE id = %s
                        """,
                        (row[0],) + row[2:] + (existing[0],),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO customer_feedback_data (
                            customer_phone, call_id, identity_confirmed, loan_taken, last_month_payment,
                            payee, payee_name, payee_contact, payment_date, payment_mode, payment_reason,
                            payment_amount, field_executive_name, field_executive_contact,
                            stage, customer_name, confirmed, category, started_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        row,
                    )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"Error persisting feedback: {e}")


async def persist_feedback_to_db(call_id: str, customer_phone: Optional[str] = None) -> None:
    """Async wrapper for feedback persistence."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _persist_feedback_to_db_sync, call_id, customer_phone)


def _update_call_status_sync(customer_phone: str, status: str) -> None:
    """Update call status in active_call_context table."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url or not customer_phone:
        return
    try:
        import psycopg2
        conn = psycopg2.connect(database_url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE active_call_context
                    SET call_status = %s, updated_at = NOW()
                    WHERE phone_number = %s
                    """,
                    (status, customer_phone)
                )
            conn.commit()
            print(f"📝 Updated call status to '{status}' for {customer_phone}")
        finally:
            conn.close()
    except Exception as e:
        print(f"Warning: Could not update call status: {e}")


async def update_call_status(customer_phone: str, status: str) -> None:
    """Async wrapper for updating call status."""
    if not customer_phone:
        return
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _update_call_status_sync, customer_phone, status)
