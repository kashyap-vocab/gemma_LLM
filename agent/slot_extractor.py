"""
Lightweight regex / keyword extractor for the L&T Finance payment-feedback
survey.

This exists because the local Gemma model cannot reliably do native tool
calls, which means our normal `store_*` function tools never fire and the
agent has no structured memory of which questions have already been
answered. Without that, Gemma re-asks the same question turn after turn.

The extractor scans every user transcript and writes whatever it finds
into the existing `feedback_sessions[call_id]` dict (same schema the
DB persistence path already understands), and a `build_collected_block`
helper formats the live state as a checklist that gets injected into the
LLM system prompt every turn so Gemma sees authoritative ground truth.

This is intentionally permissive — false negatives are fine (Gemma will
just ask the question), but we try to avoid false positives that would
let it skip a real question. When in doubt, do NOT mark a slot.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from agent.db_storage import _default_feedback_session, feedback_sessions

logger = logging.getLogger("slot-extractor")

# ── Hindi number words → integer ─────────────────────────────────────────────
_HINDI_NUM_WORDS: dict[str, int] = {
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पाँच": 5, "पांच": 5,
    "छह": 6, "छः": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
    "ग्यारह": 11, "बारह": 12, "तेरह": 13, "चौदह": 14, "पंद्रह": 15,
    "सोलह": 16, "सत्रह": 17, "अठारह": 18, "उन्नीस": 19, "बीस": 20,
    "इक्कीस": 21, "बाईस": 22, "तेईस": 23, "चौबीस": 24, "पच्चीस": 25,
    "छब्बीस": 26, "सत्ताईस": 27, "अट्ठाईस": 28, "उन्तीस": 29, "तीस": 30,
    "इकतीस": 31,
}

_MONTHS: dict[str, int] = {
    "जनवरी": 1, "january": 1, "jan": 1,
    "फ़रवरी": 2, "फरवरी": 2, "february": 2, "feb": 2,
    "मार्च": 3, "march": 3, "mar": 3,
    "अप्रैल": 4, "april": 4, "apr": 4,
    "मई": 5, "may": 5,
    "जून": 6, "june": 6, "jun": 6,
    "जुलाई": 7, "july": 7, "jul": 7,
    "अगस्त": 8, "august": 8, "aug": 8,
    "सितंबर": 9, "सितम्बर": 9, "september": 9, "sep": 9, "sept": 9,
    "अक्टूबर": 10, "october": 10, "oct": 10,
    "नवंबर": 11, "नवम्बर": 11, "november": 11, "nov": 11,
    "दिसंबर": 12, "दिसम्बर": 12, "december": 12, "dec": 12,
}

_AFFIRM = ("हाँ", "हां", "जी हाँ", "जी हां", "जी", "yes", "हाँजी", "हांजी", "बिल्कुल", "सही", "हा")
_NEGATE = ("नहीं", "ना", "नही", "no", "not")

_PAYEE_SELF = ("ख़ुद", "खुद", "मैंने भर", "मैंने पे", "मैंने किया", "मैंने भुगतान", "स्वयं", "सेल्फ", "self")
_PAYEE_RELATIVE = ("रिश्तेदार", "भाई", "बहन", "पिता", "पति", "पत्नी", "बेटा", "बेटी", "मामा", "चाचा", "परिवार")
_PAYEE_FRIEND = ("दोस्त", "मित्र", "friend")
_PAYEE_THIRD = ("थर्ड पार्टी", "third party", "तीसर")

_REASON_MAP = (
    ("emi", "EMI"), ("ईएमआई", "EMI"), ("ई एम आई", "EMI"), ("किस्त", "EMI"),
    ("foreclosure", "FORECLOSURE"), ("फ़ोरक्लोज़र", "FORECLOSURE"), ("फोरक्लोजर", "FORECLOSURE"),
    ("settlement", "SETTLEMENT"), ("सेटलमेंट", "SETTLEMENT"),
    ("part payment", "PART_PAYMENT"), ("पार्ट पेमेंट", "PART_PAYMENT"),
)

_MODE_MAP = (
    ("upi", "UPI"), ("यूपीआई", "UPI"), ("यू पी आई", "UPI"),
    ("phonepe", "UPI"), ("फोनपे", "UPI"), ("gpay", "UPI"), ("g pay", "UPI"),
    ("paytm", "UPI"), ("पेटीएम", "UPI"),
    ("net banking", "ONLINE"), ("netbanking", "ONLINE"), ("नेट बैंकिंग", "ONLINE"),
    ("online", "ONLINE"), ("ऑनलाइन", "ONLINE"),
    ("nach", "NACH"), ("नैच", "NACH"), ("ऑटो डेबिट", "NACH"), ("auto debit", "NACH"),
    ("cash", "CASH"), ("नकद", "CASH"), ("नकदी", "CASH"), ("कैश", "CASH"),
    ("branch", "BRANCH"), ("ब्रांच", "BRANCH"), ("शाखा", "BRANCH"),
    ("field executive", "FIELD_EXECUTIVE"), ("field agent", "FIELD_EXECUTIVE"),
    ("फ़ील्ड", "FIELD_EXECUTIVE"), ("फील्ड", "FIELD_EXECUTIVE"), ("एजेंट", "FIELD_EXECUTIVE"),
)


def _session_for(call_id: str) -> dict:
    sess = feedback_sessions.setdefault(call_id, _default_feedback_session())
    if "payment" not in sess or not isinstance(sess.get("payment"), dict):
        sess["payment"] = {}
    return sess


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def _last_month_ref() -> tuple[int, int]:
    """Returns (month, year) for last month relative to today."""
    today = datetime.now().date()
    if today.month == 1:
        return 12, today.year - 1
    return today.month - 1, today.year


def _extract_date(text: str) -> str | None:
    """Returns dd-mm-yyyy if a clear date is found."""
    t = text
    # 1) digit + month name (e.g., "26 march", "26 मार्च")
    m = re.search(
        r"(\d{1,2})\s*(?:tareekh|tarikh|तारीख|को)?\s*(जनवरी|फ़रवरी|फरवरी|मार्च|अप्रैल|मई|जून|जुलाई|अगस्त|सितंबर|सितम्बर|अक्टूबर|नवंबर|नवम्बर|दिसंबर|दिसम्बर|january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)",
        t,
        flags=re.IGNORECASE,
    )
    if m:
        day = int(m.group(1))
        month = _MONTHS.get(m.group(2).lower())
        if month and 1 <= day <= 31:
            ref_m, ref_y = _last_month_ref()
            year = ref_y if month == ref_m else datetime.now().year
            return f"{day:02d}-{month:02d}-{year}"

    # 2) Hindi number-word + month (e.g., "छब्बीस मार्च", "तीन january")
    word_pattern = "|".join(re.escape(w) for w in _HINDI_NUM_WORDS.keys())
    month_pattern = "|".join(re.escape(mo) for mo in _MONTHS.keys())
    m = re.search(
        rf"({word_pattern})\s*(?:tareekh|tarikh|तारीख|को)?\s*({month_pattern})",
        t,
        flags=re.IGNORECASE,
    )
    if m:
        day = _HINDI_NUM_WORDS.get(m.group(1))
        month = _MONTHS.get(m.group(2).lower())
        if day and month:
            ref_m, ref_y = _last_month_ref()
            year = ref_y if month == ref_m else datetime.now().year
            return f"{day:02d}-{month:02d}-{year}"

    # 3) dd/mm or dd-mm digits
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})\b", t)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        if 1 <= day <= 31 and 1 <= month <= 12:
            ref_m, ref_y = _last_month_ref()
            year = ref_y if month == ref_m else datetime.now().year
            return f"{day:02d}-{month:02d}-{year}"

    # 4) day-only: Hindi word or digit + "तारीख" (no month) → assume last month
    #    e.g. "बीस तारीख को", "20 तारीख"
    m = re.search(rf"(\d{{1,2}}|{word_pattern})\s*(?:ko\s*)?तारीख", t, flags=re.IGNORECASE)
    if m:
        raw_day = m.group(1)
        try:
            day = int(raw_day)
        except ValueError:
            day = _HINDI_NUM_WORDS.get(raw_day)
        if day and 1 <= day <= 31:
            ref_m, ref_y = _last_month_ref()
            return f"{day:02d}-{ref_m:02d}-{ref_y}"

    return None


_HINDI_LARGE: dict[str, int] = {
    "हज़ार": 1000, "हजार": 1000,
    "सौ": 100,
    "लाख": 100000,
}


def _parse_hindi_number(word: str) -> int | None:
    """Parse a single Hindi number word or digit string to int."""
    if not word:
        return None
    w = word.strip()
    if w.isdigit():
        return int(w)
    return _HINDI_NUM_WORDS.get(w)


def _extract_amount_hindi_words(text: str) -> str | None:
    """
    Parse amounts expressed as Hindi number words, e.g.:
      "दो हज़ार छब्बीस"  → 2026
      "पाँच हज़ार"        → 5000
      "पंद्रह सौ"         → 1500
      "एक लाख"           → 100000
    """
    num_pat = "|".join(re.escape(w) for w in _HINDI_NUM_WORDS.keys())
    digit_or_word = rf"(?:{num_pat}|\d+)"

    for unit_word, multiplier in _HINDI_LARGE.items():
        pattern = rf"({digit_or_word})\s*{re.escape(unit_word)}(?:\s+({digit_or_word}))?"
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            base = _parse_hindi_number(m.group(1))
            if base is None:
                continue
            total = base * multiplier
            if m.group(2):
                remainder = _parse_hindi_number(m.group(2))
                if remainder and remainder < multiplier:
                    total += remainder
            if total >= 100:
                return str(total)
    return None


def _extract_amount(text: str) -> str | None:
    """Look for digit amount near 'रुपये/रुपए/rs/रू', then Hindi word amounts."""
    t = text
    # 1) digits next to currency keyword
    m = re.search(r"(\d{2,7})\s*(?:rupee|rupees|rs\.?|रुपये|रुपए|रू|₹)", t, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    # 2) Hindi word amounts (दो हज़ार, पाँच सौ, etc.)
    hindi_amt = _extract_amount_hindi_words(t)
    if hindi_amt:
        return hindi_amt
    # 3) Bare digits >= 100 if payment context word present
    if any(k in t for k in ("भुगतान", "पेमेंट", "payment", "भर")):
        m = re.search(r"\b(\d{3,7})\b", t)
        if m:
            return m.group(1)
    return None


def _extract_payee(text: str) -> str | None:
    t = _norm(text)
    if any(k in text for k in _PAYEE_SELF) or any(k in t for k in ("self", "khud")):
        return "self"
    if any(k in text for k in _PAYEE_RELATIVE):
        return "relative"
    if any(k in text for k in _PAYEE_FRIEND):
        return "friend"
    if any(k in text for k in _PAYEE_THIRD):
        return "third_party"
    return None


def _extract_reason(text: str) -> str | None:
    t = _norm(text)
    for needle, label in _REASON_MAP:
        if needle in t or needle in text:
            return label
    return None


def _extract_mode(text: str) -> str | None:
    t = _norm(text)
    for needle, label in _MODE_MAP:
        if needle in t or needle in text:
            return label
    return None


def _extract_loan_taken(text: str) -> bool | None:
    t = _norm(text)
    if "लोन" in text or "loan" in t:
        if any(n in text for n in _NEGATE) or any(n in t for n in _NEGATE):
            return False
        if any(a in text for a in _AFFIRM) or any(a in t for a in _AFFIRM):
            return True
    return None


def _extract_last_month_payment(text: str) -> str | None:
    t = _norm(text)
    if "पिछले महीने" in text or "last month" in t or "पिछला महीना" in text:
        if any(n in text for n in _NEGATE):
            return "no"
        if any(a in text for a in _AFFIRM):
            return "yes"
    # If they explicitly mentioned a payment date or amount, treat last-month
    # payment as confirmed implicitly.
    return None


def update_slots_from_user(call_id: str, text: str) -> dict[str, Any]:
    """
    Run all extractors over a single user utterance and merge findings into
    feedback_sessions[call_id]. Returns the dict of newly-set slots for
    logging/observability.
    """
    if not call_id or not text:
        return {}
    sess = _session_for(call_id)
    payment = sess["payment"]
    newly: dict[str, Any] = {}

    # Loan
    if sess.get("loan_taken") is None:
        v = _extract_loan_taken(text)
        if v is not None:
            sess["loan_taken"] = v
            newly["loan_taken"] = v

    # Last-month payment
    if sess.get("last_month_payment") is None:
        v = _extract_last_month_payment(text)
        if v is not None:
            sess["last_month_payment"] = v
            newly["last_month_payment"] = v

    # Payment date
    if not payment.get("date"):
        v = _extract_date(text)
        if v is not None:
            payment["date"] = v
            newly["payment.date"] = v

    # Amount
    if not payment.get("amount"):
        v = _extract_amount(text)
        if v is not None:
            payment["amount"] = v
            newly["payment.amount"] = v

    # Payee
    if not payment.get("payee"):
        v = _extract_payee(text)
        if v is not None:
            payment["payee"] = v
            newly["payment.payee"] = v

    # Reason
    if not payment.get("reason"):
        v = _extract_reason(text)
        if v is not None:
            payment["reason"] = v
            newly["payment.reason"] = v

    # Mode
    if not payment.get("mode"):
        v = _extract_mode(text)
        if v is not None:
            payment["mode"] = v
            newly["payment.mode"] = v

    # Implicit confirmations: if we now have a date or amount, treat
    # last_month_payment as confirmed (the customer just told us about it).
    if (payment.get("date") or payment.get("amount")) and sess.get("last_month_payment") is None:
        sess["last_month_payment"] = "yes"
        newly["last_month_payment"] = "yes"

    # Implicit confirmation: any payment detail implies a loan exists.
    if payment and sess.get("loan_taken") is None:
        sess["loan_taken"] = True
        newly["loan_taken"] = True

    # Infer identity_confirmed: if the user confirmed their loan, they must have
    # already confirmed their identity. Gemma can't call the function tool so we
    # infer it here.
    if sess.get("identity_confirmed") is None and sess.get("loan_taken") is True:
        sess["identity_confirmed"] = "YES"
        newly["identity_confirmed"] = "YES"

    # Promote disposition to "connected" once identity is confirmed so that
    # DB persistence writes all survey data.
    if sess.get("identity_confirmed") == "YES" and sess.get("disposition") != "connected":
        sess["disposition"] = "connected"
        newly["disposition"] = "connected"

    if newly:
        logger.info("[%s] 🧩 slot updates: %s", call_id, newly)
    return newly


# ── Live state injected into the LLM system prompt every turn ────────────────

_QUESTION_LABELS: list[tuple[str, str]] = [
    ("identity_confirmed", "Q1 — Identity confirmation"),
    ("loan_taken", "Q2 — Loan taken from L&T Finance"),
    ("last_month_payment", "Q3 — Made last month's payment"),
    ("payment.date", "Q4 — Payment date"),
    ("payment.payee", "Q5 — Who paid (self/relative/friend/third_party)"),
    ("payment.reason", "Q6 — Payment reason (EMI / FORECLOSURE / SETTLEMENT)"),
    ("payment.amount", "Q7 — Payment amount"),
    ("payment.mode", "(extracted) Payment mode"),
]


def _slot_value(sess: dict, key: str) -> Any:
    if "." in key:
        head, tail = key.split(".", 1)
        return (sess.get(head) or {}).get(tail)
    return sess.get(key)


def build_collected_block(call_id: str) -> str:
    """
    Render the live slot tracker as a system-message block to inject into
    the chat context every turn. This is the LLM's authoritative ground
    truth — it MUST NOT re-ask anything listed under COLLECTED.
    """
    if not call_id:
        return ""
    sess = feedback_sessions.get(call_id)
    if not sess:
        return ""

    collected_lines = []
    pending_lines = []
    for key, label in _QUESTION_LABELS:
        val = _slot_value(sess, key)
        if val is None or val == "" or val == {}:
            pending_lines.append(f"  - {label}")
        else:
            collected_lines.append(f"  - {label}: {val}")

    # Conditional questions
    payment = sess.get("payment") or {}
    payee = payment.get("payee")
    if payee in ("relative", "friend", "third_party"):
        if payment.get("payee_name"):
            collected_lines.append(
                f"  - Q8 — Payee name/contact: {payment.get('payee_name')} / {payment.get('payee_contact') or '?'}"
            )
        else:
            pending_lines.append("  - Q8 — Payee name and contact (REQUIRED because payee is not self)")
    mode = payment.get("mode")
    if mode in ("CASH", "FIELD_EXECUTIVE"):
        if payment.get("field_executive_name"):
            collected_lines.append(
                f"  - Q9 — Field executive: {payment.get('field_executive_name')} / {payment.get('field_executive_contact') or '?'}"
            )
        else:
            pending_lines.append("  - Q9 — Field executive name and contact (REQUIRED because mode is cash/field exec)")

    block = ["📋 LIVE SURVEY STATE — TREAT AS GROUND TRUTH"]
    block.append("COLLECTED (do NOT ask these again, they are already answered):")
    block.extend(collected_lines or ["  (nothing yet)"])
    block.append("")
    block.append("STILL NEED (ask the FIRST one of these next, in order):")
    block.extend(pending_lines or ["  (all mandatory questions answered — give the SUMMARY now and ask for confirmation)"])
    return "\n".join(block)
