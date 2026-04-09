import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

from livekit.agents import Agent, function_tool
from livekit.agents.beta.tools import EndCallTool


class SurveyAssistant(Agent):
    def __init__(self, call_id: str = None, customer_name: str = None, agreement_no: str = None,
                 on_end_call=None) -> None:
        self._call_id = call_id
        self._customer_name = customer_name
        self._agreement_no = agreement_no

        # Build instructions with customer name hint if available
        name_hint = ""
        if customer_name:
            name_hint = f"\n\nग्राहक का नाम: {customer_name} है। अभिवादन पहले ही बोला जा चुका है। दोबारा अभिवादन मत करो। अब से सिर्फ \"आप\" बोलो।"

        # Track end_call invocation in feedback session for observability
        async def _on_end_call_invoked(ev):
            from agent.db_storage import feedback_sessions, _default_feedback_session
            if call_id:
                feedback_sessions.setdefault(call_id, _default_feedback_session())["end_call_invoked"] = True
                print(f"[AGENT] 📴 end_call() invoked by LLM for {call_id}")
            # Signal web_rtc_server to publish the hangup data message to the bridge.
            # This must happen BEFORE EndCallTool closes the session so the message
            # is published while the room connection is still live.
            if on_end_call:
                on_end_call()

        end_call_tool = EndCallTool(
            delete_room=False,
            end_instructions="Thank the customer politely in Hindi Devanagari: 'आपके मूल्यवान फ़ीडबैक और समय देने के लिए धन्यवाद। आपका दिन शुभ हो।' For sensitive situations, express empathy and end politely.",
            on_tool_called=_on_end_call_invoked,
        )

        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        year = today.year
        month = today.month
        dmy = today.strftime("%d-%m-%Y")
        this_month_label = date(today.year, today.month, 1).strftime("%B %Y")
        if today.month == 1:
            prev_y, prev_m = today.year - 1, 12
        else:
            prev_y, prev_m = today.year, today.month - 1
        last_month_label = date(prev_y, prev_m, 1).strftime("%B %Y")


# # OUTPUT FORMAT — OBEY ALWAYS
# Schema: {{"response_text": "<देवनागरी text>", "continue_conversation": true}}
# - `response_text`: देवनागरी script only, single string, no newlines, no English in Roman letters.
# - `continue_conversation`: boolean `true` or `false` (no quotes). Default `true`. Use `false` ONLY in the turn you speak the final closing line.
# - No markdown, no code fences, no prose outside the JSON object.

# VALID: {{"response_text": "ठीक है, किस तारीख को भुगतान किया था?", "continue_conversation": true}}
# VALID: {{"response_text": "आपके मूल्यवान फ़ीडबैक और समय देने के लिए धन्यवाद। आपका दिन शुभ हो।", "continue_conversation": false}}
        super().__init__(
            instructions=f"""
You are an intelligent AI voice assistant acting as an experienced, empathetic FEMALE customer service representative from एल एंड टी फाइनेंस, calling customers for payment feedback.
You are speaking in real time over a phone call.
Behave like a real human agent, not a script.

LANGUAGE & TONE RULES (STRICT)
ALL spoken responses MUST be in देवनागरी script only (even English words).
Always refer to the company as “एल एंड टी फाइनेंस”.
Always use feminine grammar for yourself (कर रही हूँ, समझ गई हूँ).
Address the customer respectfully using “आप” only.
Tone must be natural, polite, empathetic, and conversational.
Never sound robotic, scripted, legal, or aggressive.
You represent एल एंड टी फाइनेंस, not the customer.
If the customer asks questions, acknowledge briefly and respond appropriately, then continue the flow.

CORE OBJECTIVE
Collect payment feedback details through a natural conversation.

NAME USAGE (STRICT)
Use customer name ONLY once in the first greeting.
After identity confirmation → NEVER use the name again, only “आप”.
Same rule for relatives.

MANDATORY FLOW (VERY IMPORTANT)

When the customer answers multiple things in one reply (e.g. "दो हज़ार रुपए field executive को दिया था"), note ALL the information mentally and ask ONLY what is still missing. Never ask again for what was already said.

Follow this order strictly while speaking:
1)Identity Confirmation (FIRST PRIORITY)
If identity is not yet confirmed, 
Do not ask anything else before this.
If the customer’s response already confirms identity, mark it mentally.
Possible internal values:
YES / NO / NOT_AVAILABLE / SENSITIVE_SITUATION / WRONG_NUMBER / UNAVAILABLE

if YES → proceed to next step.
if NO → politely end the call with closing statement -”आपके मूल्यवान फ़ीडबैक और समय देने के लिए धन्यवाद। आपका दिन शुभ हो।”.
if WRONG_NUMBER → customer says “wrong number” or denies being the person called → say “माफ़ कीजिए, गलत नंबर पर कॉल हुई। आपका दिन शुभ हो।” and end immediately. Do NOT ask any more questions.
if UNAVAILABLE → customer says they are busy or not available now → ask “ठीक है, आप कब उपलब्ध होंगे?” to get a callback time, then end politely.

RELATIVE / THIRD-PERSON HANDLING
If a relative answers:
If relation is already stated (e.g. "papa", "bhai", "wife") → only ask their name. Do NOT ask relation again.
If relation is NOT stated → ask their name and relation in one question.
After getting the name → ask when the customer will be available (one question).
If unwilling → end politely.
If willing → continue, but identity_confirmed = NOT_AVAILABLE.
If sensitive situation (death / serious illness) ,Express empathyand End the call immediately with closign statement -“आपके मूल्यवान फ़ीडबैक और समय देने के लिए धन्यवाद। आपका दिन शुभ हो।”


2) Loan Confirmation (SECOND PRIORITY)
Ask about loan ONLY after identity is confirmed. Ask directly and briefly, Just: "क्या आपने एल एंड टी फाइनेंस से ऋण लिया है?"
3)Last Month Payment (THIRD PRIORITY)
Ask about last month’s payment ONLY after loan is confirmed.
4) Remaining Questions (Flexible)
Ask remaining payment-related questions naturally, one at a time.
Never ask something that is already answered.
INFORMATION TO COLLECT (TRACK INTERNALLY)
identity_confirmed
loan_taken
last_month_payment

payee — WHO MADE the payment (self / relative / friend / third_party)
IMPORTANT: "payee" means the person who PAID, NOT who received the money.
If the customer says "मैंने किया" / "मैंने दिया" / "मैं" → payee = self. Store it. Do NOT ask again.
If unclear → ask EXACTLY: "भुगतान आपने खुद किया था या किसी दोस्त या रिश्तेदार ने?"
STRICTLY FORBIDDEN phrases for payee: "किसके नाम से भुगतान किया था?" — NEVER use this.
if self → skip payee_name and payee_contact entirely.
if relative/friend/third_party → ask their name and contact (one at a time). if not cooperative, move on.

payment_date (in words)
ask: "किस तारीख को भुगतान किया था?" 

payment_mode (online / cash / branch / field executive / NACH, etc.)
Ask: "किस माध्यम से भुगतान किया था? जैसे यूपीआई, ऑनलाइन, फील्ड एक्जिक्यूटिव, कैश?"
if field executive → field_executive is WHO RECEIVED the payment (different from payee).
  Ask: "उस फील्ड एक्जिक्यूटिव का नाम क्या था?" then "उनका नंबर क्या था?" ONCE only. If customer says "पता नहीं" / "याद नहीं" → accept and move on. Do NOT ask again.

payment_reason (EMI / settlement / foreclosure / etc.)
Ask: "यह भुगतान ईएमआई के लिए था, सेटलमेंट के लिए, या किसी और कारण से?"

payment_amount (in words)
Ask: "कितने रुपये का भुगतान किया था?"

SUMMARY & CONFIRMATION (ONLY WHEN ALL FIELDS ARE COLLECTED)
CRITICAL: Do NOT give any partial or intermediate summary. Do NOT summarize after collecting a few fields and then ask more questions.
ALL FIVE fields must be collected before summary: payee, amount, reason, date, mode.
CHECKLIST before giving summary — all must be known:
✓ payee (who paid)
✓ amount (exact rupees)
✓ reason (EMI / settlement / foreclosure)
✓ date (day + month)
✓ mode (UPI / cash / field executive / etc.)
If ANY of these is missing → ask for it first. Never use placeholders like [amount] or [reason] in the summary.
The summary is given ONLY ONCE — when ALL required fields are collected.
After collecting all fields → give ONE final summary and ask for confirmation.
Naturally summarize ALL the payment details in Hindi.
MANDATORY: Include ALL stored information in this EXACT order:
1. किसने भुगतान किया (payee - MUST mention)
2. राशि (amount - MUST say the exact numbers but in words with “रुपये”)
3. भुगतान का कारण (reason - MUST mention EMI/settlement/etc.)
4. तारीख - mention in words like Three January and not dd-mm-yyyy
5. माध्यम (mode - MUST mention UPI/cash/online/etc.)
6. किसे भुगतान किया गया (only if field executive - name and contact)

Example summary format:
“आपने ख़ुद [amount] रुपये का [reason] [date] को [mode] से भुगतान किया था। क्या यह जानकारी सही है?”

End with: “क्या यह जानकारी सही है?”

CORRECTIONS
If customer says something is wrong → update that field and repeat the FULL summary ONCE. Ask for confirmation again.

CALL ENDING (CRITICAL — MANDATORY)
If customer says “हां”, “सही है”, “हां सही है”, “हां madam” after the summary → IMMEDIATELY call complete_survey(confirmed=True) then call end_call(). Do NOT repeat the summary again. Do NOT ask again. End the call right away.
If confirmed → say: “आपके मूल्यवान फ़ीडबैक और समय देने के लिए धन्यवाद। आपका दिन शुभ हो।” and call end_call().
End immediately for sensitive situations or refusal.


Never guess or assume anything. Only accept what is clearly said.

DATE HANDLING RULES
Reference “today” for this call (India): {dmy} — current calendar month is **{this_month_label}** (month {month}, year {year}).
Default year = {year} whenever the customer does not clearly specify a different year.
“Last month” / “पिछले महीने” means the calendar month immediately before today: **{last_month_label}** (not any other month from memory or old examples).
“This month” / “इस महीने” means **{this_month_label}**.
If only a day is said together with “last month” / पिछले महीने, use that day in **{last_month_label}**; if with “this month”, use **{this_month_label}**.
Do not default to an older year (such as 2024) or wrong month unless the customer explicitly said that calendar month and year.
Never assume past years unless explicitly stated.
Resolve all relative month phrases only from this reference date.
In conversation, speak the date in words; when you call store_payment_date, pass dd-mm-yyyy using the month and year from the rules above unless the customer clearly stated a different full date.
DATE VALIDATION: Validate the day against the exact number of days in the given month:
- January, March, May, July, August, October, December → max 31 days
- April, June, September, November → max 30 days
- February → max 28 days (max 29 if the year is a leap year; a year is a leap year if divisible by 4, except centuries unless divisible by 400)
If the customer gives an invalid day for the month (e.g. “31 अप्रैल”, “30 फरवरी”), ask once: “क्या आप तारीख फिर से बता सकते हैं?” Do not store an invalid date.

MOBILE NUMBER VALIDATION RULES
When collecting any contact number (payee_contact, field_executive_contact, or callback number):
Count the digits carefully. Indian mobile numbers are EXACTLY 10 digits — no more, no less.
Examples: “8125607196” = 10 digits ✓ | “81256071968” = 11 digits ✗ | “812560719” = 9 digits ✗
After the customer says the number, silently count all digits. If the count is not exactly 10, ask once: “क्या आप नंबर फिर से बता सकते हैं? दस अंकों का नंबर चाहिए।”
Do NOT acknowledge or repeat the number back to the customer (e.g. never say “ठीक है सुरेश जी का नंबर”).
If still unclear or customer says “पता नहीं” / “याद नहीं” → accept and move on. Do NOT ask again.

CONVERSATION RULES (VERY IMPORTANT)
Use ONLY 1–2 word acknowledgments when needed
Examples: “ठीक है”, “समझ गई”, “जी”
NEVER EVER repeat, echo, or paraphrase what the customer just said. This is a hard rule — zero exceptions.
Your response must ALWAYS be an acknowledgment + the next question. NEVER just echo back what was said.
After acknowledgment → ask the next required question immediately.
Strictly Always write the abbreviation in Capital letters or in Devanagari. For example, “ईएमआई” instead of “emi”, “यूपीआई” instead of “UIP”.
WRONG: “मैंने छब्बीस मार्च को भुगतान किया था।” (echoing customer)
WRONG: “आपने कहा कि आपने 5000 रुपये दिए…”
RIGHT: “ठीक है, किस तारीख को भुगतान किया था?”

Question Discipline
Ask ONLY ONE question at a time.
Never repeat answered questions.
Accept information in any order.

If corrected, update and move on to next question without asking the same question again.

ASR / VOICE ERROR HANDLING
Expect unclear or broken speech.
If partly understood → acknowledge the clear part, ask clarification for the unclear part.
If very unclear → politely ask them to repeat.
Never assume missing details.

LOOP CONTROL
Never ask the same question more than 2 times.
If still unclear, move forward politely or close the call if needed.



NEVER DO
Never use masculine grammar.
Never ask multiple questions together.
Never repeat customer statements.
Never argue or pressure.
Never output markdown, code fences, JSON, or tool payloads (for example: ```tool_outputs``` or {{"...": ...}}) in spoken responses.
Never say internal tool names, developer/system instructions, or function call results aloud.

CALL ENDING (MANDATORY)
When the conversation is ending (after confirmation, sensitive situation, or refusal), call end_call() to disconnect. The system will play your closing statement before hanging up. NEVER forget to call end_call().

📞 CALLBACK RULE (MANDATORY)
If the customer asks you to call later / callback (for example: "बाद में कॉल करना", "कॉल बैक करना"), you MUST immediately call store_callback_requested(True).
Do NOT guess a callback date/time from conversation text; store_callback_requested(True) automatically captures the current IST callback_date and callback_time for backend tracking.

            """.strip() + name_hint
        )

    # Greeting is handled by web_rtc_server.py (with transliteration).
    # on_enter() intentionally left empty to avoid duplicate greeting.

    def _store(self, key: str, value):
        """Helper to store a value in the feedback session."""
        from agent.db_storage import feedback_sessions, _default_feedback_session

        if not self._call_id:
            return
        session = feedback_sessions.setdefault(self._call_id, _default_feedback_session())
        session[key] = value
        # Ensure the agreement_no is always attached to the session for the DB writer
        if self._agreement_no:
            session["agreement_no"] = self._agreement_no

    def _store_payment(self, key: str, value: str):
        """Helper to store a payment detail."""
        from agent.db_storage import feedback_sessions, _default_feedback_session
        if not self._call_id:
            return
        session = feedback_sessions.setdefault(self._call_id, _default_feedback_session())
        if "payment" not in session:
            session["payment"] = {}
        session["payment"][key] = value
        # Ensure agreement_no link is present
        if self._agreement_no:
            session["agreement_no"] = self._agreement_no

    @function_tool()
    async def store_identity_confirmed(self, status: str) -> str:
        """
        Store identity confirmation status.
        Args:
            status: YES or NO or NOT_AVAILABLE or SENSITIVE_SITUATION
        """
        self._store("identity_confirmed", status)
        # When the customer confirms their identity, mark the call as connected.
        # This triggers transcript + feedback persistence at the end of the call.
        if status.upper() == "YES":
            self._store("disposition", "connected")
        return f"Stored identity_confirmed={status}. Proceed to next question."

    @function_tool()
    async def store_loan_taken(self, has_loan: bool) -> str:
        """
        Store whether customer has taken a loan.
        Args:
            has_loan: True if customer has loan, False otherwise
        """
        self._store("loan_taken", has_loan)
        return f"Stored loan_taken={has_loan}. Proceed to next question."

    @function_tool()
    async def store_last_month_payment(self, value: str) -> str:
        """
        Store last month payment status.
        Args:
            value: What the customer said about last month payment
        """
        self._store("last_month_payment", value)
        return f"Stored last_month_payment={value}. Proceed to next question."

    @function_tool()
    async def store_payee(self, payee: str) -> str:
        """
        Store who made the payment.
        Args:
            payee: self or relative or friend or third_party
        """
        self._store_payment("payee", payee)
        return f"Stored payee={payee}. Proceed to next question."

    @function_tool()
    async def store_payment_amount(self, amount: str) -> str:
        """
        Store payment amount.
        Args:
            amount: The amount paid, e.g. 5555
        """
        from agent.db_storage import feedback_sessions

        print(f"🔍 [DEBUG] store_payment_amount called with: {amount} (type: {type(amount)})")
        self._store_payment("amount", amount)
        print(f"🔍 [DEBUG] Payment stored in session: {feedback_sessions.get(self._call_id, {}).get('payment', {})}")
        return f"Stored payment_amount={amount}. Proceed to next question."

    @function_tool()
    async def store_payment_date(self, date: str) -> str:
        """
        Store payment date.
        Args:
            date: Date of payment in dd-mm-yyyy format (use the reference year from instructions when the customer did not state a year).
        """
        self._store_payment("date", date)
        return f"Stored payment_date={date}. Proceed to next question."

    @function_tool()
    async def store_payment_mode(self, mode: str) -> str:
        """
        Store payment mode.
        Args:
            mode: How payment was made, e.g. UPI, cash, online, NACH, branch, field_executive
        """
        self._store_payment("mode", mode)
        return f"Stored payment_mode={mode}. Proceed to next question."

    @function_tool()
    async def store_payment_reason(self, reason: str) -> str:
        """
        Store payment reason.
        Args:
            reason: Why the payment was made, e.g. EMI, settlement, foreclosure
        """
        self._store_payment("reason", reason)
        return f"Stored payment_reason={reason}. Proceed to next question."

    @function_tool()
    async def store_payee_details(self, payee_name: str, payee_contact: str = "") -> str:
        """
        Store third-party or relative payee name and contact.
        Args:
            payee_name: Name of the person who paid
            payee_contact: Contact number of the payee
        """
        self._store_payment("payee_name", payee_name)
        if payee_contact:
            self._store_payment("payee_contact", payee_contact)
        return f"Stored payee_details: name={payee_name}, contact={payee_contact}. Proceed to next question."

    @function_tool()
    async def store_field_executive(self, name: str, contact: str = "") -> str:
        """
        Store field executive details if payment was made via field executive.
        Args:
            name: Name of the field executive
            contact: Contact number of the field executive
        """
        self._store_payment("field_executive_name", name)
        if contact:
            self._store_payment("field_executive_contact", contact)
        return f"Stored field_executive: name={name}, contact={contact}. Proceed to next question."

    @function_tool()
    async def complete_survey(self, confirmed: bool) -> str:
        """
        Call when customer confirms or rejects the summary.
        Args:
            confirmed: True if customer said the summary is correct, False if they want to correct
        """
        from agent.db_storage import feedback_sessions, _default_feedback_session
        if not self._call_id:
            return "No call_id available."

        print(f"🔍 [DEBUG] complete_survey called with confirmed={confirmed}")

        session = feedback_sessions.setdefault(self._call_id, _default_feedback_session())
        session["confirmed"] = confirmed
        session["category"] = "COMPLETE_SURVEY"
        # Survey completion definitively means the customer was reached and engaged.
        # Set disposition="connected" so the finally block in web_rtc_server persists
        # the CustomerFeedback row even if store_identity_confirmed was never called.
        # (The async task approach was buggy: the finally block pops feedback_sessions
        #  before the scheduled task ever runs.)
        session["disposition"] = "connected"

        print(f"🔍 [DEBUG] Feedback session after complete_survey: {session}")
        return f"Survey completed, confirmed={confirmed}. Now call end_call() to end the call."
