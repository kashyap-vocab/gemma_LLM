import os
import logging
import time
import wave
from pathlib import Path

from dotenv import load_dotenv
from livekit import agents, rtc
from livekit.agents import (
    AgentServer,
    AgentSession,
    Agent,
    room_io,
    UserInputTranscribedEvent,
    MetricsCollectedEvent,
)
from livekit.agents.metrics import (
    AgentMetrics,
    LLMMetrics,
    STTMetrics,
    TTSMetrics,
    EOUMetrics,
)
from livekit.plugins import deepgram, google, noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from google.genai import types
from livekit.plugins import sarvam

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# ---- Metrics: same as livkit_agent.py ----
class ConversationLogger:
    """
    Logs transcripts and latency metrics to the standard Python logger.
    """

    def __init__(self) -> None:
        self._per_speech: dict[str, dict[str, dict]] = {}

    def log_user_transcript(self, event: UserInputTranscribedEvent) -> None:
        logger.info(
            "user_transcript language=%s speaker=%s text=%s",
            event.language,
            event.speaker_id,
            event.transcript,
        )

    def log_metrics(self, metrics: AgentMetrics) -> None:
        component = None
        latencies = {}

        if isinstance(metrics, LLMMetrics):
            component = "llm"
            latencies = {
                "duration_ms": metrics.duration * 1000.0,
                "ttft_ms": metrics.ttft * 1000.0,
                "tokens_per_second": metrics.tokens_per_second,
            }
        elif isinstance(metrics, TTSMetrics):
            component = "tts"
            latencies = {
                "duration_ms": metrics.duration * 1000.0,
                "ttfb_ms": metrics.ttfb * 1000.0,
                "audio_duration_ms": metrics.audio_duration * 1000.0,
            }
        elif isinstance(metrics, STTMetrics):
            component = "stt"
            latencies = {
                "duration_ms": metrics.duration * 1000.0,
                "audio_duration_ms": metrics.audio_duration * 1000.0,
                "streamed": metrics.streamed,
            }
        elif isinstance(metrics, EOUMetrics):
            eou_ms = metrics.end_of_utterance_delay * 1000.0
            stt_delay_ms = metrics.transcription_delay * 1000.0
            callback_ms = metrics.on_user_turn_completed_delay * 1000.0
            component = "eou"
            latencies = {
                "end_of_utterance_delay_ms": eou_ms,
                "transcription_delay_ms": stt_delay_ms,
                "on_user_turn_completed_delay_ms": callback_ms,
                "stt_total_latency_ms": eou_ms + stt_delay_ms,
            }

        if component:
            primary_ms = (
                latencies.get("stt_total_latency_ms")
                or latencies.get("duration_ms")
                or latencies.get("ttfb_ms")
            )
            logger.info(
                "metrics_component=%s latency_ms=%s latencies=%s",
                component,
                primary_ms,
                latencies,
            )
            self._update_latency_matrix(component, metrics, latencies)

    def _update_latency_matrix(
        self,
        component: str,
        metrics: AgentMetrics,
        latencies: dict,
    ) -> None:
        speech_id = getattr(metrics, "speech_id", None)
        if not speech_id:
            return

        speech_row = self._per_speech.setdefault(speech_id, {})
        speech_row[component] = latencies

        eou = speech_row.get("eou") or {}
        llm = speech_row.get("llm") or {}
        tts = speech_row.get("tts") or {}

        stt_total = eou.get("stt_total_latency_ms")
        llm_ms = llm.get("duration_ms")
        tts_ms = tts.get("duration_ms")
        tts_ttfb_ms = tts.get("ttfb_ms")

        if stt_total is None or llm_ms is None or tts_ms is None:
            return

        total_ms = stt_total + llm_ms + tts_ms

        logger.info(
            "latency_matrix speech_id=%s stt_total_ms=%.1f llm_ms=%.1f tts_ms=%.1f tts_ttfb_ms=%s total_ms=%.1f",
            speech_id,
            stt_total,
            llm_ms,
            tts_ms,
            tts_ttfb_ms,
            total_ms,
        )
        logger.info(
            "component_latencies STT_ms=%.1f LLM_ms=%.1f TTS_ms=%.1f TTS_TTFB_ms=%s total_ms=%.1f",
            stt_total,
            llm_ms,
            tts_ms,
            tts_ttfb_ms,
            total_ms,
        )


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""
            You are an intelligent AI voice assistant acting as an experienced, empathetic FEMALE customer service representative from एल एंड टी फाइनेंस, calling customers for payment feedback.

You are speaking in real time over a phone call.
Behave like a real human agent, not a script.

🌐 LANGUAGE & TONE RULES (STRICT)

ALL spoken responses MUST be in देवनागरी script only (even English words).

Always refer to the company as "एल एंड टी फाइनेंस".

Always use feminine grammar for yourself (कर रही हूँ, समझ गई हूँ).

Address the customer respectfully using "आप" only.

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

If the customer's response already confirms identity, mark it mentally.

Possible internal values:

YES / NO / NOT_AVAILABLE / SENSITIVE_SITUATION

2️⃣ Loan Confirmation (SECOND PRIORITY)

Ask about loan ONLY after identity is confirmed.

3️⃣ Last Month Payment (THIRD PRIORITY)

Ask about last month's payment ONLY after loan is confirmed.

4️⃣ Remaining Questions (Flexible)

Ask remaining payment-related questions naturally, one at a time.

Never ask something that is already answered.

🧠 INFORMATION TO COLLECT (TRACK INTERNALLY)

identity_confirmed

loan_taken

last_month_payment

payee (self / relative / friend / third_party)

payee_name, payee_contact (if applicable)

payment_date (dd-mm-yyyy)

payment_mode (online / cash / branch / field executive / NACH, etc.)

field_executive_name, field_executive_contact (if applicable)

payment_reason (EMI, settlement, foreclosure, etc.)

payment_amount (numeric)

⚠️ Never guess or assume anything. Only accept what is clearly said.

📅 DATE HANDLING RULES

Default year = current year (2026).

Never assume past years unless explicitly stated.

Resolve phrases like "पिछले महीने" using today's date as reference.
Always return the date in dd-mm-yyyy format.

🗣️ CONVERSATION RULES (VERY IMPORTANT)
✔ Acknowledgments

Use ONLY 1–2 word acknowledgments when needed
Examples: "ठीक है", "समझ गई", "जी"

NEVER repeat or paraphrase what the customer just said.

After acknowledgment → ask the next required question.

Always write the abbreviation in Capital letters or in Devanagari. For example, say "EMI" instead of emi.

❌ Bad:
"आपने कहा कि आपने 5000 रुपये दिए…"

✅ Good:
"ठीक है, किस तारीख को भुगतान किया था?"

✔ Question Discipline

Ask ONLY ONE question at a time.

Never repeat answered questions.

Accept information in any order.

If corrected, update mentally and move on gracefully.

🧍 NAME USAGE (STRICT)

Use customer name ONLY once in the first greeting.

After identity confirmation → NEVER use the name again, only "आप".

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

Naturally summarize the payment details in Hindi.

Follow this order:

किसने भुगतान किया

राशि

भुगतान का कारण

तारीख

माध्यम

किसे भुगतान किया गया (यदि फील्ड एग्ज़ीक्यूटिव)

End with:
"क्या यह जानकारी सही है?"

✏️ CORRECTIONS

If customer says it's wrong:

Ask: "कौन सी जानकारी बदलनी है?"

Update mentally and repeat the full summary again.

Ask for confirmation again.

☎️ CALL ENDING

If confirmed:

Thank politely and close:
"आपके मूल्यवान फ़ीडबैक और समय देने के लिए धन्यवाद। आपका दिन शुभ हो।"

End immediately for sensitive situations or refusal.

🚫 NEVER DO

Never use masculine grammar.

Never ask multiple questions together.

Never repeat customer statements.

Never use English script.

Never argue or pressure.
            """,
        )


server = AgentServer()


# PREWARM ALL MODELS FOR ZERO LATENCY
def prewarm(proc: agents.JobProcess):
    """Prewarm VAD, STT, LLM, and TTS models to reduce initial connection latency"""
    print("🔥 PREWARMING MODELS...")

    proc.userdata["vad"] = silero.VAD.load()
    print("✅ VAD prewarmed")

    proc.userdata["stt"] = deepgram.STT(model="nova-2", language="hi")
    print("✅ STT prewarmed")

    proc.userdata["llm"] = google.LLM(
        model="gemini-2.0-flash",
        temperature=0.1,
        thinking_config=types.ThinkingConfig(include_thoughts=False),
    )
    print("✅ LLM prewarmed")

    proc.userdata["tts"] = sarvam.TTS(
        target_language_code="hi-IN",
        speaker="manisha",
        model="bulbul:v2",
        pace=1.0,
        pitch=0.0,
        loudness=1.0,
    )
    print("✅ TTS prewarmed")

    print("🚀 ALL MODELS READY - ZERO LATENCY MODE ACTIVATED\n")


server.setup_fnc = prewarm


@server.rtc_session()
async def my_agent(ctx: agents.JobContext):
    conv_logger = ConversationLogger()

    session = AgentSession(
        min_endpointing_delay=0.1,
        max_endpointing_delay=0.4,
        stt=ctx.proc.userdata["stt"],
        llm=ctx.proc.userdata["llm"],
        tts=ctx.proc.userdata["tts"],
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
        preemptive_generation=True,
    )

    @session.on("user_input_transcribed")
    def _on_user_input_transcribed(event: UserInputTranscribedEvent) -> None:
        if not event.is_final or not event.transcript:
            return
        conv_logger.log_user_transcript(event)

    @session.on("metrics_collected")
    def _on_metrics_collected(event: MetricsCollectedEvent) -> None:
        conv_logger.log_metrics(event.metrics)

    await session.start(
        room=ctx.room,
        agent=Assistant(),
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

    await session.say("", audio=greeting_audio(), allow_interruptions=True)


if __name__ == "__main__":
    print("🚀 Ultra-Low Latency Agent - WITH MODEL PREWARMING + METRICS")
    print("=" * 60)
    print("⚡ Optimizations:")
    print("   • VAD prewarmed (Silero)")
    print("   • STT prewarmed (Deepgram Nova-2)")
    print("   • LLM prewarmed (Gemini 2.0 Flash)")
    print("   • TTS prewarmed (Sarvam Bulbul v2)")
    print("   • Preemptive generation enabled")
    print("   • Hardcoded audio greeting")
    print("   • Min endpointing delay: 0.1s")
    print("   • Latency metrics: metrics_component=*, component_latencies, latency_matrix")
    print("\n▶ Starting server with prewarming...\n")

    agents.cli.run_app(server)
