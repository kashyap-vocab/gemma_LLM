"""
Backfill customer_feedback rows from conversation transcripts using Gemini.

Usage:
  python scripts/backfill_feedback_with_llm.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from sqlalchemy import func

# Ensure project root is on sys.path when running as a script.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db.database import SessionLocal
from db.models import Conversation, CustomerFeedback

CONNECTED_SUB_DISPOSITIONS = {
    "Complete Call",
    "Incomplete Call",
    "Technical Issue",
    "Wrong Number",
    "Call Back",
}

NOT_CONNECTED_SUB_DISPOSITIONS = {
    "Network Issue",
    "No Response",
    "Invalid Number",
}


def _normalize_disposition(value: Any) -> str:
    text = (str(value or "").strip().lower()).replace("-", "_").replace(" ", "_")
    if text in {"connected", "connect"}:
        return "connected"
    if text in {"not_connect", "not_connected", "notconnect", "notconnected"}:
        return "not_connected"
    return "connected"


def _normalize_sub_disposition(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Preserve your naming format from sheet.
    return text


def _normalize_sub_sub_disposition(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    return text or None


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "yes", "y", "1"}:
        return True
    if text in {"false", "no", "n", "0"}:
        return False
    return None


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    # Accept YYYY-MM-DD only to avoid ambiguous parsing.
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


_LOCAL_LLM_URL = os.getenv("LOCAL_LLM_URL", "http://192.168.30.239:6000")


def _infer_feedback_from_transcript(transcript: str) -> dict[str, Any]:
    ref = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    ref_year = ref.year
    this_month_label = date(ref.year, ref.month, 1).strftime("%B %Y")
    if ref.month == 1:
        prev_y, prev_m = ref.year - 1, 12
    else:
        prev_y, prev_m = ref.year, ref.month - 1
    last_month_label = date(prev_y, prev_m, 1).strftime("%B %Y")
    prompt = f"""
You are extracting structured call feedback for a loan payment call.
Given transcript lines, return STRICT JSON only with this exact schema:
{{
  "identity_confirmed": true|false|null,
  "loan_taken": true|false|null,
  "last_month_payment": true|false|null,
  "payee": string|null,
  "payee_name": string|null,
  "payee_contact": string|null,
  "payment_date": "YYYY-MM-DD"|null,
  "payment_amount": string|null,
  "payment_mode": string|null,
  "payment_reason": string|null,
  "field_executive_name": string|null,
  "field_executive_contact": string|null,
  "customer_name": string|null,
  "confirmed": true|false|null,
  "disposition": "connected"|"not_connected"|null,
  "sub_disposition": string|null,
  "sub_sub_disposition": string|null
}}

Rules:
- Use null when unknown. Never invent facts.
- Reference "today" for this extraction: {ref.isoformat()}. Current calendar month: **{this_month_label}** (year {ref_year}). "Last month" / equivalent Hindi phrases mean **{last_month_label}**; "this month" means **{this_month_label}**. Resolve relative months only from this reference — never a stale month/year from training defaults.
- When the transcript implies a date but does not name a year, use {ref_year}. When it implies "last month" with only a day, use month/year of **{last_month_label}**. Do not default payment_date to 2024 (or any stale year) unless the transcript explicitly states that year.
- last_month_payment must be boolean or null.
- payment_date must be YYYY-MM-DD or null.
- Classify call using this exact taxonomy:
  Connected:
    - Sub-Disposition: Complete Call
      - Sub-Sub-Disposition: Full Survey Completed | Partial Questions Answered
    - Sub-Disposition: Incomplete Call
      - Sub-Sub-Disposition: Customer Refusal | Language Barrier | Call Disconnected (Mid)
    - Sub-Disposition: Technical Issue
      - Sub-Sub-Disposition: BOT Silence/No Audio | Speech Recognition Error | Latency/Delay
    - Sub-Disposition: Wrong Number
      - Sub-Sub-Disposition: Not the Customer
    - Sub-Disposition: Call Back
      - Sub-Sub-Disposition: Scheduled Call Back
  Not Connect:
    - Sub-Disposition: Network Issue
      - Sub-Sub-Disposition: Busy/Congested | Not Reachable
    - Sub-Disposition: No Response
      - Sub-Sub-Disposition: Ringing (No Response) | Call Disconnected (Pre)
    - Sub-Disposition: Invalid Number
      - Sub-Sub-Disposition: Invalid Number
- If meaningful conversation occurred, disposition should be "connected"; otherwise "not_connected".
- Return JSON only, no markdown.

Transcript:
{transcript}
""".strip()

    resp = httpx.post(
        f"{_LOCAL_LLM_URL}/chat",
        json={
            "messages": [{"role": "user", "content": prompt}],
            "max_new_tokens": 512,
            "temperature": 0.1,
            "stream": False,
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    raw = (resp.json().get("content") or "").strip()
    # Guard against fenced responses.
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.replace("json", "", 1).strip()
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _pick_latest_turn_set(db, agreement_no: str) -> tuple[str | None, list[Conversation]]:
    """
    Return (call_id, turns) for the latest conversation group for an agreement.
    Works even when call_id is NULL.
    """
    latest_call = (
        db.query(
            Conversation.call_id,
            func.max(Conversation.created_at).label("latest_ts"),
        )
        .filter(Conversation.agreement_no == agreement_no)
        .group_by(Conversation.call_id)
        .order_by(func.max(Conversation.created_at).desc())
        .first()
    )

    if not latest_call:
        return None, []

    call_id = latest_call.call_id
    if call_id is None:
        turns = (
            db.query(Conversation)
            .filter(Conversation.agreement_no == agreement_no, Conversation.call_id.is_(None))
            .order_by(Conversation.created_at.asc(), Conversation.id.asc())
            .all()
        )
    else:
        turns = (
            db.query(Conversation)
            .filter(Conversation.call_id == call_id)
            .order_by(Conversation.created_at.asc(), Conversation.id.asc())
            .all()
        )
    return call_id, turns


def run_backfill_once() -> dict[str, int]:
    """Run one LLM backfill pass and return counters."""
    load_dotenv()

    inserted = 0
    updated = 0
    skipped = 0

    with SessionLocal() as db:
        # Process all agreements present in conversation (not just missing),
        # so incomplete and technical/no-response style outcomes are updated too.
        conv_agreements = {
            row[0]
            for row in db.query(Conversation.agreement_no)
            .filter(Conversation.agreement_no.isnot(None))
            .distinct()
            .all()
        }
        all_agreements = sorted(conv_agreements)
        print(f"[LLM-BACKFILL] Total agreement_no in conversation to classify: {len(all_agreements)}")

        for agreement_no in all_agreements:
            call_id, turns = _pick_latest_turn_set(db, agreement_no)
            if not turns:
                skipped += 1
                continue

            transcript_lines: list[str] = []
            for t in turns:
                if t.customer_transcript:
                    transcript_lines.append(f"USER: {t.customer_transcript}")
                if t.agent_transcript:
                    transcript_lines.append(f"AGENT: {t.agent_transcript}")
            transcript = "\n".join(transcript_lines).strip()
            if not transcript:
                skipped += 1
                continue

            try:
                inferred = _infer_feedback_from_transcript(transcript)
            except Exception as e:
                print(
                    f"[LLM-BACKFILL][WARN] {agreement_no} ({call_id}) - "
                    f"LLM parse error: {e}; using fallback defaults."
                )
                inferred = {}

            # Upsert key: agreement_no only (requested coverage is agreement-based).
            # Do not fallback by call_id, because multiple agreements can share
            # a call_id in this dataset and that would overwrite rows.
            feedback = (
                db.query(CustomerFeedback)
                .filter(CustomerFeedback.agreement_no == agreement_no)
                .first()
            )
            is_new = feedback is None
            if is_new:
                feedback = CustomerFeedback(call_id=call_id)
                db.add(feedback)

            feedback.agreement_no = agreement_no
            feedback.customer_phone = turns[-1].customer_phone
            feedback.identity_confirmed = _to_bool(inferred.get("identity_confirmed"))
            feedback.loan_taken = _to_bool(inferred.get("loan_taken"))
            feedback.last_month_payment = _to_bool(inferred.get("last_month_payment"))
            feedback.payee = _safe_str(inferred.get("payee"))
            feedback.payee_name = _safe_str(inferred.get("payee_name"))
            feedback.payee_contact = _safe_str(inferred.get("payee_contact"))
            feedback.payment_date = _to_date(inferred.get("payment_date"))
            feedback.payment_amount = _safe_str(inferred.get("payment_amount"))
            feedback.payment_mode = _safe_str(inferred.get("payment_mode"))
            feedback.payment_reason = _safe_str(inferred.get("payment_reason"))
            feedback.field_executive_name = _safe_str(inferred.get("field_executive_name"))
            feedback.field_executive_contact = _safe_str(inferred.get("field_executive_contact"))
            feedback.customer_name = _safe_str(inferred.get("customer_name"))
            feedback.confirmed = _to_bool(inferred.get("confirmed"))
            disposition = _normalize_disposition(inferred.get("disposition"))
            sub_disp = _normalize_sub_disposition(inferred.get("sub_disposition"))
            sub_sub_disp = _normalize_sub_sub_disposition(inferred.get("sub_sub_disposition"))

            # Guardrails: ensure sub-disposition aligns with top-level disposition.
            if disposition == "connected" and sub_disp not in CONNECTED_SUB_DISPOSITIONS:
                sub_disp = "Incomplete Call"
            if disposition == "not_connected" and sub_disp not in NOT_CONNECTED_SUB_DISPOSITIONS:
                sub_disp = "No Response"

            feedback.disposition = disposition
            feedback.sub_disposition = sub_disp
            # Use existing `category` column for "Sub-Sub-Disposition".
            feedback.category = sub_sub_disp
            feedback.conversation_json = [
                {"role": "user", "text": t.customer_transcript}
                if t.customer_transcript
                else {"role": "assistant", "text": t.agent_transcript}
                for t in turns
                if t.customer_transcript or t.agent_transcript
            ]

            if is_new:
                inserted += 1
            else:
                updated += 1

        db.commit()

    print(f"[LLM-BACKFILL] Done. inserted={inserted}, updated={updated}, skipped={skipped}")
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


if __name__ == "__main__":
    run_backfill_once()

