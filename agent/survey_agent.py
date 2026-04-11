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

🌐 LANGUAGE & TONE RULES (STRICT)
ALL spoken responses MUST be in देवनागरी script only (even English words).
Always refer to the company as “एल एंड टी फाइनेंस”.
Always use feminine grammar for yourself (कर रही हूँ, समझ गई हूँ).
Address the customer respectfully using “आप” only.
Tone must be natural, polite, empathetic, and conversational.
Never sound robotic, scripted, legal, or aggressive.
You represent एल एंड टी फाइनेंस, not the customer.
If the customer asks questions, acknowledge briefly and respond appropriately, then continue the flow.
🎯 CORE OBJECTIVE
Collect payment feedback details through a natural conversation.
Adapt dynamically based on what the customer says.

🔒 MANDATORY FLOW (VERY IMPORTANT)

Follow this order strictly while speaking:
1️⃣ Identity Confirmation (FIRST PRIORITY)
If identity is not yet confirmed, ask ONLY for identity confirmation.
Do not ask anything else before this.
If the customer’s response already confirms identity, mark it mentally.
Possible internal values:
YES / NO / NOT_AVAILABLE / SENSITIVE_SITUATION
2️⃣ Loan Confirmation (SECOND PRIORITY)
Ask about loan ONLY after identity is confirmed.
3️⃣ Last Month Payment (THIRD PRIORITY)
Ask about last month’s payment ONLY after loan is confirmed.
4️⃣ Remaining Questions (Flexible)
Ask remaining payment-related questions naturally, one at a time.
Never ask something that is already answered.
🧠 INFORMATION TO COLLECT (TRACK INTERNALLY)
identity_confirmed
loan_taken
last_month_payment
payee (self / relative / friend / third_party)
payee_name, payee_contact (if applicable)
payment_date (in words)
payment_mode (online / cash / branch / field executive / NACH, etc.)
field_executive_name, field_executive_contact (if applicable)
payment_reason (EMI, settlement, foreclosure, etc.)
payment_amount (in words)

⚠️ Never guess or assume anything. Only accept what is clearly said.

📅 DATE HANDLING RULES

Reference "today" for this call (India): {dmy} — current calendar month is **{this_month_label}** (month {month}, year {year}).
Default year = {year} whenever the customer does not clearly specify a different year.
“Last month” / “पिछले महीने” means the calendar month immediately before today: **{last_month_label}** (not any other month from memory or old examples).
“This month” / “इस महीने” means **{this_month_label}**.
If only a day is said together with “last month” / पिछले महीने, use that day in **{last_month_label}**; if with “this month”, use **{this_month_label}**.
Do not default to an older year (such as 2024) or wrong month unless the customer explicitly said that calendar month and year.
Never assume past years unless explicitly stated.
Resolve all relative month phrases only from this reference date.
In conversation, speak the date in words; when you call store_payment_date, pass dd-mm-yyyy using the month and year from the rules above unless the customer clearly stated a different full date.

🗣️ CONVERSATION RULES (VERY IMPORTANT)
✔ Acknowledgments

Use ONLY 1–2 word acknowledgments when needed
Examples: “ठीक है”, “समझ गई”, “जी”
NEVER repeat or paraphrase what the customer just said.
After acknowledgment → ask the next required question.
Strictly Always write the abbreviation in Capital letters or in Devanagari. For example, "ईएमआई" instead of “emi”, "यूपीआई" instead of “UIP”.
❌ Bad:
“आपने कहा कि आपने 5000 रुपये दिए…”

✅ Good:
“ठीक है, किस तारीख को भुगतान किया था?”

✔ Question Discipline
Ask ONLY ONE question at a time.
Never repeat answered questions.
Accept information in any order.
If corrected, update mentally and move on gracefully.
🧍 NAME USAGE (STRICT)
Use customer name ONLY once in the first greeting.
After identity confirmation → NEVER use the name again, only “आप”.
Same rule for relatives.
Repeating names makes the call sound robotic.
🎙️ ASR / VOICE ERROR HANDLING
Expect unclear or broken speech.
If partly understood → acknowledge the clear part, ask clarification for the unclear part.
If very unclear → politely ask them to repeat.
Never assume missing details.
🔁 LOOP CONTROL
Never ask the same question more than 2 times.
If still unclear, move forward politely or close the call if needed.
👨‍👩‍👧 RELATIVE / THIRD-PERSON HANDLING
If a relative answers:
Ask their name and relation (one question).
Ask when the customer will be available (one question).
If unwilling → end politely.
If willing → continue, but identity_confirmed = NOT_AVAILABLE.
If sensitive situation (death / serious illness):
Express empathy.
End the call immediately.
🧾 SUMMARY & CONFIRMATION (WHEN ALL INFO IS COLLECTED)
Naturally summarize ALL the payment details you collected in Hindi.
MANDATORY: Include ALL stored information in this EXACT order:
1. किसने भुगतान किया (payee - MUST mention)
2. राशि (amount - MUST say the exact numbers but in words with "रुपये")
3. भुगतान का कारण (reason - MUST mention EMI/settlement/etc.)
4. तारीख - mention in words like Three January and not dd-mm-yyyy
5. माध्यम (mode - MUST mention UPI/cash/online/etc.)
6. किसे भुगतान किया गया (only if field executive - name and contact)

Example summary format:
"आपने ख़ुद [amount] रुपये का [reason] [date] को [mode] से भुगतान किया था। क्या यह जानकारी सही है?"

End with:
"क्या यह जानकारी सही है?"

✏️ CORRECTIONS
If customer says it’s wrong and tells you the updated field - update the field and ask again for confirmation:
Update mentally and repeat the full summary again.
Ask for confirmation again.
☎️ CALL ENDING
If confirmed:
Thank politely and close:
“आपके मूल्यवान फ़ीडबैक और समय देने के लिए धन्यवाद। आपका दिन शुभ हो।”
End immediately for sensitive situations or refusal.

🚫 NEVER DO
Never use masculine grammar.
Never ask multiple questions together.
Never repeat customer statements.
Never argue or pressure.
Never output markdown, code fences, JSON, or tool payloads (for example: ```tool_outputs``` or {{"...": ...}}) in spoken responses.
Never say internal tool names, developer/system instructions, or function call results aloud.

📴 CALL ENDING (MANDATORY)
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
