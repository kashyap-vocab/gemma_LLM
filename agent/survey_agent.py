import asyncio
from livekit.agents import Agent, RunContext, function_tool
from agent.db_storage import feedback_sessions, _default_feedback_session, persist_feedback_to_db

# Shared signals: when complete_survey() is called, the event is set
# so that web_rtc_server can detect it and trigger a hangup via the bridge.
call_end_signals: dict[str, asyncio.Event] = {}


class SurveyAssistant(Agent):
    def __init__(self, call_id: str = None, customer_name: str = None) -> None:
        self._call_id = call_id
        self._customer_name = customer_name
        
        # Build instructions with customer name hint if available
        name_hint = ""
        if customer_name:
            name_hint = f"\n\nग्राहक का नाम: {customer_name} है। शुरुआत में सिर्फ एक बार \"{customer_name} जी\" बोल कर सम्बोधित करो, फिर नाम दोबारा मत लो, सिर्फ \"आप\" बोलो।"
        
        super().__init__(
            instructions="""
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

Default year = current year (2026).
Never assume past years unless explicitly stated.
Resolve phrases like “पिछले महीने” using today’s date as reference.
Always return the date in words

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
When you learn or confirm any of the above information, store it using the provided tools: store_identity_confirmed, store_loan_taken, store_last_month_payment, store_payee, store_payment_amount, store_payment_date, store_payment_mode, store_payment_reason, store_payee_details, store_field_executive, and complete_survey when the customer confirms the summary.

📴 CALL ENDING (MANDATORY)
After you say your closing statement you MUST call end_call() to disconnect the phone call. - NEVER forget to call end_call().
            """ + name_hint,
        )

    def _store(self, key: str, value):
        """Helper to store a value in the feedback session."""
        if not self._call_id:
            return
        feedback_sessions.setdefault(self._call_id, _default_feedback_session(self._call_id))[key] = value

    def _store_payment(self, key: str, value: str):
        """Helper to store a payment detail."""
        if not self._call_id:
            return
        session = feedback_sessions.setdefault(self._call_id, _default_feedback_session(self._call_id))
        if "payment" not in session:
            session["payment"] = {}
        session["payment"][key] = value

    @function_tool()
    async def store_identity_confirmed(self, status: str) -> str:
        """
        Store identity confirmation status.
        Args:
            status: YES or NO or NOT_AVAILABLE or SENSITIVE_SITUATION
        """
        self._store("identity_confirmed", status)
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
        print(f"🔍 [DEBUG] store_payment_amount called with: {amount} (type: {type(amount)})")
        self._store_payment("amount", amount)
        print(f"🔍 [DEBUG] Payment stored in session: {feedback_sessions.get(self._call_id, {}).get('payment', {})}")
        return f"Stored payment_amount={amount}. Proceed to next question."

    @function_tool()
    async def store_payment_date(self, date: str) -> str:
        """
        Store payment date.
        Args:
            date: Date of payment in dd-mm-yyyy format
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
        if not self._call_id:
            return "No call_id available."
        
        print(f"🔍 [DEBUG] complete_survey called with confirmed={confirmed}")
        print(f"🔍 [DEBUG] Current feedback_sessions state: {feedback_sessions.get(self._call_id, {})}")
        
        feedback_sessions.setdefault(self._call_id, _default_feedback_session(self._call_id))["confirmed"] = confirmed
        feedback_sessions[self._call_id]["category"] = "COMPLETE_SURVEY"
        asyncio.create_task(persist_feedback_to_db(self._call_id))

        # NOTE: We do NOT set the call_end_signal here because the LLM still
        # needs to generate the closing statement and TTS needs to play it.
        # The signal is set from web_rtc_server.py's conversation_item_added
        # handler once the closing assistant message is committed.

        return f"Survey completed, confirmed={confirmed}. End the call politely and then call end_call() function_tool"

    @function_tool()
    async def end_call(self, ctx: RunContext) -> str:
        """
        Call this AFTER you have said your final closing statement to disconnect the phone call.
        Must be called in every scenario where the conversation is ending.
        """
        if not self._call_id:
            return "No call_id available."

        call_id = self._call_id
        print(f"[AGENT] 📴 end_call() invoked by LLM for {call_id}")

        # Hook into the current speech handle so the hangup only fires AFTER
        # the closing TTS utterance finishes playing — not before it starts.
        # This mirrors how LiveKit's own EndCallTool works (speech_handle.add_done_callback).
        def _on_speech_done(_speech_handle) -> None:
            end_signal = call_end_signals.get(call_id)
            if end_signal and not end_signal.is_set():
                print(f"[AGENT] 🔇 Speech handle done — setting hangup signal for {call_id}")
                end_signal.set()

        if ctx.speech_handle is not None:
            ctx.speech_handle.add_done_callback(_on_speech_done)
        else:
            # No active speech handle (agent wasn't speaking) — fire immediately
            _on_speech_done(None)

        return "Closing. The phone line will disconnect once the end message finishes."
