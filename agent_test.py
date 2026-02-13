"""
Ultra-Fast LiveKit Agent - Payment Feedback Survey
- <1s greeting latency (hardcoded)
- Function tools for data storage
- State-based conversation flow
- Colloquial Hindi for natural conversation
- Optimized for minimal latency (<2s per turn)
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict

import wave

from dotenv import load_dotenv
from livekit import agents, rtc
from livekit.agents import (
    AgentServer,
    AgentSession,
    Agent,
    room_io,
    function_tool,
)
from livekit.plugins import deepgram, elevenlabs, google, noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from google.genai import types

load_dotenv()

# Session storage
sessions: Dict[str, Dict] = {}
rooms: Dict[str, rtc.Room] = {}
session_agents: Dict[str, "PaymentAgent"] = {}


def _save_json(sid: str):
    """Save session data to JSON file."""
    if sid in sessions:
        filename = f"call_{sid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(sessions[sid], f, ensure_ascii=False, indent=2)


BASE_INSTRUCTIONS = """तुम एल एंड टी फाइनेंस की महिला कस्टमर सर्विस एजेंट हो। पेमेंट फीडबैक ले रही हो।

जरूरी नियम:
- सिर्फ देवनागरी में बोलो
- महिला शब्द इस्तेमाल करो (कर रही हूँ, बोल रही हूँ)
- ग्राहक को "आप" कहो
- जवाब छोटा दो (10-15 शब्द)
- एक बार में एक ही सवाल पूछो
- ग्राहक की बात मत दोहराओ
- नाम मत लो (सिर्फ "आप" बोलो)
- बातचीत आम हिंदी में हो, फॉर्मल नहीं

डेटा स्टोर करने के लिए tools का इस्तेमाल करो।"""


# === PAYMENT FEEDBACK AGENT ===


class PaymentAgent(Agent):
    def __init__(self, sid: str) -> None:
        self.sid = sid
        self.stage = "identity"
        super().__init__(instructions=self._get_instructions())

    def _get_instructions(self) -> str:
        """Get stage-specific instructions."""
        base = BASE_INSTRUCTIONS

        if self.stage == "identity":
            return (
                base
                + "\n\nअभी: ग्राहक ने पहचान कन्फर्म की या नहीं चेक करो। हाँ = store_identity('YES'), ना = store_identity('NO')।"
            )

        elif self.stage == "loan":
            return (
                base
                + "\n\nअभी: पूछो उन्होंने लोन लिया है या नहीं। फिर store_loan(True/False) करो।"
            )

        elif self.stage == "payment":
            payment = sessions[self.sid].get("payment", {})
            missing = []
            if "amount" not in payment:
                missing.append("राशि")
            if "date" not in payment:
                missing.append("तारीख")
            if "mode" not in payment:
                missing.append("माध्यम")
            if "reason" not in payment:
                missing.append("कारण")

            return (
                base
                + f"\n\nअभी: {missing[0] if missing else 'सब मिल गया'} पूछो। फिर add_payment_detail(field, value) करो।"
            )

        elif self.stage == "summary":
            p = sessions[self.sid].get("payment", {})
            return (
                base
                + f"\n\nअभी: सारांश दो। बोलो: '{p.get('amount', '?')} रुपये {p.get('date', '?')} को {p.get('mode', '?')} से दिए थे। सही है?' फिर कन्फर्म करवाओ।"
            )

        elif self.stage == "complete":
            return (
                base
                + "\n\nअभी: धन्यवाद बोलो। complete_survey(True) करो। बोलो: 'बहुत धन्यवाद। अच्छा दिन रहे।'"
            )

        elif self.stage == "end":
            return base + "\n\nअभी: विदाई दो। बोलो: 'धन्यवाद। शुभ दिन।'"

        return base

    async def update_stage(self, new_stage: str):
        """Update conversation stage and instructions."""
        self.stage = new_stage
        sessions[self.sid]["stage"] = new_stage
        new_instructions = self._get_instructions()
        await self.update_instructions(new_instructions)

    @function_tool()
    async def store_identity(self, status: str):
        """
        Store identity confirmation status.
        Args:
            status: YES (customer confirmed) / NO (wrong person) / NOT_AVAILABLE (relative answered) / SENSITIVE_SITUATION
        """
        sessions[self.sid]["identity_confirmed"] = status

        if status == "YES":
            await self.update_stage("loan")
        elif status == "NO":
            await self.update_stage("end")
        elif status == "NOT_AVAILABLE":
            await self.update_stage("end")
        elif status == "SENSITIVE_SITUATION":
            await self.update_stage("end")

        return None

    @function_tool()
    async def store_loan(self, has_loan: bool):
        """
        Store whether customer has taken loan.
        Args:
            has_loan: True if customer has loan, False if not
        """
        sessions[self.sid]["loan_taken"] = has_loan

        if has_loan:
            sessions[self.sid]["payment"] = {}
            await self.update_stage("payment")
        else:
            await self.update_stage("end")

        return None

    @function_tool()
    async def add_payment_detail(self, field: str, value: str):
        """
        Add payment information field by field.
        Args:
            field: amount / date / mode / reason / payee / payee_name / field_executive_name
            value: the value for that field
        """
        if "payment" not in sessions[self.sid]:
            sessions[self.sid]["payment"] = {}

        sessions[self.sid]["payment"][field] = value

        # Check if all required fields collected
        required = ["amount", "date", "mode", "reason"]
        payment = sessions[self.sid]["payment"]
        if all(f in payment for f in required):
            await self.update_stage("summary")

        return None

    @function_tool()
    async def complete_survey(self, confirmed: bool):
        """
        Complete the survey after summary confirmation.
        Args:
            confirmed: True if customer confirmed summary, False if they want to edit
        """
        sessions[self.sid]["confirmed"] = confirmed
        sessions[self.sid]["category"] = "COMPLETE_SURVEY"
        _save_json(self.sid)

        if confirmed:
            await self.update_stage("end")
        else:
            # Go back to payment collection for corrections
            await self.update_stage("payment")

        return None


# === SERVER ===

server = AgentServer()


# PREWARM ALL MODELS FOR MINIMAL LATENCY
def prewarm(proc: agents.JobProcess):
    """Prewarm VAD, STT, LLM, and TTS models to reduce initial connection latency"""
    print("🔥 PREWARMING MODELS...")

    # Prewarm VAD (Voice Activity Detection)
    proc.userdata["vad"] = silero.VAD.load()
    print("✅ VAD prewarmed")

    # Prewarm STT (Speech-to-Text) - Deepgram
    proc.userdata["stt"] = deepgram.STT(model="nova-2", language="hi")
    print("✅ STT prewarmed")

    # Prewarm LLM - Google Gemini
    proc.userdata["llm"] = google.LLM(
        model="gemini-2.0-flash",
        max_output_tokens=60,
        temperature=0.3,
        thinking_config=types.ThinkingConfig(include_thoughts=False),
    )
    print("✅ LLM prewarmed")

    # Prewarm TTS - ElevenLabs
    proc.userdata["tts"] = elevenlabs.TTS(
        voice_id="Ukfq9vQ0QNLZ4MGK0Uxc",
        model="eleven_multilingual_v2",
    )
    print("✅ TTS prewarmed")

    print("🚀 ALL MODELS READY - ULTRA-LOW LATENCY MODE\n")


server.setup_fnc = prewarm


@server.rtc_session()
async def payment_feedback_agent(ctx: agents.JobContext):

    sid = ctx.room.name

    # Initialize session data
    sessions[sid] = {
        "stage": "greeting",
        "started": datetime.now().isoformat(),
        "customer_name": "सुरेश",  # Hardcoded for demo
    }
    rooms[sid] = ctx.room

    # Create agent instance
    agent = PaymentAgent(sid=sid)
    session_agents[sid] = agent

    # Use prewarmed models from userdata
    session = AgentSession(
        min_endpointing_delay=0.5,
        max_endpointing_delay=0.8,
        stt=ctx.proc.userdata["stt"],
        llm=ctx.proc.userdata["llm"],
        tts=ctx.proc.userdata["tts"],
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
        preemptive_generation=True,  # Enable preemptive generation
    )

    # Start the session and keep it running
    await session.start(
        room=ctx.room,
        agent=agent,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params: (
                    noise_cancellation.BVCTelephony()
                    if params.participant.kind
                    == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                    else noise_cancellation.BVC()
                ),
            ),
        ),
    )

    # HARDCODED AUDIO GREETING - Play pre-recorded WAV file
    audio_path = Path(__file__).parent / "manisha_tts_audio.wav"

    with wave.open(str(audio_path), "rb") as wav_file:
        num_channels = wav_file.getnchannels()
        sample_rate = wav_file.getframerate()
        num_frames = wav_file.getnframes()
        frames = wav_file.readframes(num_frames)

    audio_frame = rtc.AudioFrame(
        data=frames,
        sample_rate=sample_rate,
        num_channels=num_channels,
        samples_per_channel=num_frames,
    )

    async def greeting_audio():
        yield audio_frame

    # Play audio file directly (bypasses TTS = instant playback)
    await session.say("", audio=greeting_audio(), allow_interruptions=True)

    # Update stage after greeting
    await agent.update_stage("identity")


if __name__ == "__main__":
    print("🚀 Payment Feedback Agent - Ultra Fast")
    print("=" * 50)
    print("📊 Performance Targets:")
    print("   • Greeting latency: <1s (hardcoded)")
    print("   • Response latency: <2s (60 token limit)")
    print("   • Colloquial Hindi for natural conversation")
    print("\n🔧 Features:")
    print("   ✓ State-based conversation flow")
    print("   ✓ Function tools for data storage")
    print("   ✓ Real-time SmartFlo data sync")
    print("   ✓ Preemptive generation enabled")
    print("   ✓ Dynamic async instruction updates")
    print("   ✓ Optimized streaming TTS")
    print("   ✓ Simplified prompts for reliability")
    print("\n▶ Starting server...\n")

    agents.cli.run_app(server)
