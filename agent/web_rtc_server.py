import wave
from pathlib import Path

from dotenv import load_dotenv
from google.genai import types
from livekit import agents, rtc
from livekit.agents import (
    AgentSession,
    MetricsCollectedEvent,
    room_io,
)
from livekit.plugins import deepgram, google, noise_cancellation, silero
from livekit.plugins import sarvam
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from metrics import MetricsTracker
from survey_agent import SurveyAssistant

load_dotenv()


# ============================================================================
# Prewarming
# ============================================================================

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


# ============================================================================
# Main Agent Session
# ============================================================================

async def my_agent(ctx: agents.JobContext):
    """Main agent session with official LiveKit metrics"""

    # Create metrics tracker
    tracker = MetricsTracker()
    tracker.start_turn()

    print(f"\n🎯 SESSION START")
    print(f"Room: {ctx.room.name}\n")

    # Use prewarmed models
    session = AgentSession(
        turn_detection=MultilingualModel(),
        min_endpointing_delay=0.1,
        max_endpointing_delay=0.4,
        stt=ctx.proc.userdata["stt"],
        llm=ctx.proc.userdata["llm"],
        tts=ctx.proc.userdata["tts"],
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    # Subscribe to official LiveKit metrics
    @session.on("metrics_collected")
    def on_metrics_collected(ev: MetricsCollectedEvent):
        tracker.on_metrics(ev)

    try:
        await session.start(
            room=ctx.room,
            agent=SurveyAssistant(),
            room_options=room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(
                    noise_cancellation=lambda params: (
                        noise_cancellation.BVCTelephony()
                        if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                        else noise_cancellation.BVC()
                    ),
                ),
            ),
        )

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
    finally:
        print("\n\n🛑 Session ending...")
        tracker.print_session_summary()


if __name__ == "__main__":
    server = agents.WorkerOptions(
        agent_name="LTFS_SurveyAgent-Soma",
        entrypoint_fnc=my_agent,
        prewarm_fnc=prewarm,
    )

    agents.cli.run_app(server)
