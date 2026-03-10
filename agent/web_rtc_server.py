import asyncio
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# Keep low-level library loggers quiet; show agent-level logs
for _noisy in (
        "livekit",
        "livekit.rtc",
        "livekit.agents",
        "livekit.plugins.sarvam",
        "livekit.plugins.sarvam.log",
        "livekit.plugins.deepgram",
        "livekit.plugins.google",
        "livekit.plugins.silero",
        "livekit.plugins.turn_detector",
        "livekit.plugins.noise_cancellation",
        "httpx",
        "httpcore",
        "google_genai",
        "google.genai",
        "grpc",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

from dotenv import load_dotenv
from google import genai
from google.genai import types
from livekit import agents, rtc
from livekit.agents import (
    AgentSession,
    MetricsCollectedEvent,
    ConversationItemAddedEvent,
    room_io,
)
from livekit.plugins import deepgram, google, noise_cancellation, silero, sarvam
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from agent.metrics import MetricsTracker
from agent.survey_agent import SurveyAssistant

load_dotenv()

# Import database storage helpers
from agent.db_storage import (
    _load_call_metadata as load_call_metadata,
    feedback_sessions,
    _default_feedback_session,
    buffer_transcript_turn,
)

# Module-level dict for end-call signal events (per call_id)
call_end_signals: dict[str, asyncio.Event] = {}

# ============================================================================
# Prewarming
# ============================================================================

def prewarm(proc: agents.JobProcess):
    print("🔥 PREWARMING MODELS...")
    proc.userdata["vad"] = silero.VAD.load(
        sample_rate=8000, 
        activation_threshold=0.6 
    )
    proc.userdata["stt"] = deepgram.STT(model="nova-2", language="hi")
    proc.userdata["llm"] = google.LLM(
        model="gemini-2.0-flash",
        temperature=0.1,
        thinking_config=types.ThinkingConfig(include_thoughts=False),
    )

# ============================================================================
# Main Agent Session
# ============================================================================

async def my_agent(ctx: agents.JobContext):
    print(f"✅ Job accepted for room: {ctx.room.name}")
    import json as _json
    tracker = MetricsTracker()
    tracker.start_turn()

    # call_id in the new schema is the room name
    call_id = ctx.room.name or "unknown"

    # ── Step 0: Connect to the room FIRST ────────────────────────────────────
    # Must be called before accessing room properties (metadata, participants)
    # or registering event handlers that depend on a live room.
    # session.start() also calls connect() internally but only AFTER we've
    # already tried to read room.metadata / remote_participants below.
    logger.info(f"[{call_id}] Connecting to LiveKit room...")
    await ctx.connect()
    logger.info(f"[{call_id}] ✅ Connected to LiveKit room")

    # ── Step 1: Set up network-resilient disconnect tracking ─────────────────
    # Register ALL room lifecycle handlers here, BEFORE session.start() and
    # BEFORE session.say(), so we never miss events due to timing races.
    disconnect_event = asyncio.Event()

    @ctx.room.on("reconnecting")
    def on_reconnecting():
        logger.warning(f"[{call_id}] ⚠️ Network fluctuation — room reconnecting, staying alive...")

    @ctx.room.on("reconnected")
    def on_reconnected():
        logger.info(f"[{call_id}] ✅ Room reconnected — resuming call")

    @ctx.room.on("disconnected")
    def on_room_disconnect(reason=None):
        # Accept optional `reason` arg that LiveKit SDK passes with this event.
        # Previously this handler had no args, causing a silent TypeError that
        # left disconnect_event unset and the agent hanging forever.
        logger.info(f"[{call_id}] 🔴 Room disconnected (reason={reason})")
        disconnect_event.set()

    # ── Step 2: Extract customer info from room metadata ─────────────────────
    customer_phone = None
    customer_name = None
    agreement_no = None

    try:
        room_meta_str = ctx.room.metadata
        if room_meta_str:
            room_meta = _json.loads(room_meta_str)
            customer_phone = room_meta.get("customer_phone")
            customer_name = room_meta.get("customer_name")
            agreement_no = room_meta.get("agreement_no")
            if agreement_no:
                print(f"✅ Relational ID found in room metadata: {agreement_no}")
    except Exception as e:
        print(f"Warning: Could not parse room metadata: {e}")

    # Fallback: check any participants already in the room
    if not customer_phone:
        try:
            for participant in ctx.room.remote_participants.values():
                if participant.metadata:
                    try:
                        metadata = _json.loads(participant.metadata)
                        customer_phone = metadata.get("customer_phone")
                        agreement_no = agreement_no or metadata.get("agreement_no")
                        if customer_phone:
                            break
                    except Exception:
                        pass
        except Exception:
            pass

    # DB lookup
    if not agreement_no:
        db_phone, db_name, db_agreement = load_call_metadata(call_id, customer_phone=customer_phone)
        customer_phone = customer_phone or db_phone
        customer_name = db_name or customer_name
        agreement_no = db_agreement

    # Initialize feedback session
    feedback_sessions[call_id] = _default_feedback_session()
    feedback_sessions[call_id]["agreement_no"] = agreement_no
    feedback_sessions[call_id]["customer_phone"] = customer_phone
    if customer_name:
        feedback_sessions[call_id]["customer_name"] = customer_name

    print(f"\n🎯 SESSION START | Room: {call_id} | Agreement: {agreement_no}")

    session_tts = sarvam.TTS(
        target_language_code="hi-IN",
        speaker="simran",
        model="bulbul:v3",
        pace=1.0,
    )

    session = AgentSession(
        turn_detection=agents.vad.VADTurnDetector(
            min_endpointing_delay=0.1,
            max_endpointing_delay=0.5,
        ),
        stt=ctx.proc.userdata["stt"],
        llm=ctx.proc.userdata["llm"],
        tts=session_tts,
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    @session.on("user_speech_committed")
    def on_user_speech_committed(msg: agents.stt.SpeechEvent):
        logger.info(f"[{call_id}] 👤 User Speech: {msg.alternatives[0].text}")

    @session.on("error")
    def on_session_error(err):
        logger.error(f"[{call_id}] ❌ Session Error: {err}")

    transcript_buffer = []

    @session.on("conversation_item_added")
    def on_conversation_item_added(event: ConversationItemAddedEvent):
        item = event.item
        text = (item.text_content or "").strip()
        if not text:
            return

        role = getattr(item, "role", None)
        role_str = (getattr(role, "value", None) or getattr(role, "name", None) or str(role)).lower()
        speaker_id = getattr(item, "speaker_id", None)

        transcript_buffer.append((role_str, text, speaker_id))
        logger.info(f"[{call_id}] [{role_str.upper()}] {text[:120]}")

        if role_str in ("user", "assistant"):
            buffer_transcript_turn(
                call_id=call_id,
                role=role_str,
                text=text,
                speaker_id=speaker_id if role_str == "user" else None,
            )

    @session.on("metrics_collected")
    def on_metrics_collected(ev: MetricsCollectedEvent):
        tracker.on_metrics(ev)

    # ── Step 3: Register participant_connected BEFORE session.start() ─────────
    # If the customer answers while the greeting TTS is playing, this event fires
    # concurrently. Registering it early ensures we never miss it.
    @ctx.room.on("participant_connected")
    def on_participant_connected(participant):
        logger.info(f"[{call_id}] 📞 Participant connected: {participant.identity} (kind={participant.kind})")
        # Run DB update in a background thread so we don't block the event loop
        asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _update_call_status_in_bg(call_id),
        )
        if call_id in feedback_sessions:
            feedback_sessions[call_id]["call_answered"] = True

    try:
        # ── Step 4: Start session ──────────────────────────────────────────────
        # session.start() internally calls ctx.connect() which is now a no-op
        # since we already connected above. The room is fully live here.
        transliterate_task = asyncio.create_task(_transliterate_name(customer_name)) if customer_name else None

        await session.start(
            room=ctx.room,
            agent=SurveyAssistant(call_id=call_id, customer_name=customer_name, agreement_no=agreement_no),
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
        logger.info(f"[{call_id}] ✅ AgentSession started, ready to greet")

        # Get transliterated name (should be done by now)
        hindi_name = customer_name
        if transliterate_task:
            try:
                hindi_name = await asyncio.wait_for(transliterate_task, timeout=3.0)
            except Exception:
                hindi_name = customer_name

        name_part = f"{hindi_name} जी" if hindi_name else "आप"
        greeting_text = (
            f"नमस्ते, मैं एल एंड टी फाइनेंस की तरफ़ से बात कर रही हूँ। "
            f"यह कॉल आपके पेमेंट अनुभव को जानने के लिए है। "
            f"क्या मेरी बात {name_part} से हो रही है?"
        )

        # Explicitly buffer the greeting — ensures it appears in the transcript
        # even if session.say() is interrupted or the call drops during TTS.
        buffer_transcript_turn(call_id=call_id, role="assistant", text=greeting_text)

        logger.info(f"[{call_id}] 🎙️ Playing greeting for {customer_name}")
        try:
            await asyncio.wait_for(session.say(greeting_text, allow_interruptions=True), timeout=30.0)
        except Exception as e:
            logger.warning(f"[{call_id}] Greeting say() error: {e}")

        # ── Step 5: Set up end-call signal and wait for room disconnect ────────
        call_end_signals[call_id] = asyncio.Event()

        async def _wait_and_signal_hangup():
            await call_end_signals[call_id].wait()
            await asyncio.sleep(1.5)
            try:
                import json as _j
                hangup_msg = _j.dumps({"action": "hangup"}).encode("utf-8")
                await ctx.room.local_participant.publish_data(hangup_msg, reliable=True)
            except Exception:
                pass

        asyncio.create_task(_wait_and_signal_hangup())

        # When EndCallTool triggers session.shutdown(), session emits "close".
        # Unblock so we proceed to cleanup.
        def on_session_close(ev):
            disconnect_event.set()
        session.once("close", on_session_close)

        # disconnect_event was registered at the TOP of this function, before
        # session.start(), so we can never miss the event.
        logger.info(f"[{call_id}] Waiting for room disconnect...")
        await disconnect_event.wait()

    finally:
        call_end_signals.pop(call_id, None)
        tracker.print_session_summary(call_id=call_id, agreement_no=agreement_no)
        try:
            from agent.db_storage import _persist_feedback_to_db_sync
            _persist_feedback_to_db_sync(call_id, agreement_no)
        except Exception as e:
            print(f"❌ Session Flush Error: {e}")
        finally:
            feedback_sessions.pop(call_id, None)


# ── Helpers used by my_agent ──────────────────────────────────────────────────

def _update_call_status_in_bg(call_id: str) -> None:
    """Thread-safe DB status update — runs in executor to avoid blocking event loop."""
    from agent.db_storage import _update_call_status_sync
    try:
        _update_call_status_sync(call_id, "active")
    except Exception as exc:
        logger.error(f"[{call_id}] Failed to update call status: {exc}")


async def _transliterate_name(name: str) -> str:
    """Convert an English Indian name to Hindi Devanagari via Gemini."""
    try:
        client = genai.Client()
        resp = await asyncio.wait_for(
            client.aio.models.generate_content(
                model="gemini-2.0-flash",
                contents=(
                    "Convert this Indian name from English to Hindi Devanagari script. "
                    f"Reply with ONLY the Devanagari name: {name}"
                ),
            ),
            timeout=5.0,
        )
        return resp.text.strip()
    except Exception as e:
        logger.warning(f"Transliteration failed for '{name}': {e}")
        return name

server = agents.WorkerOptions(
    agent_name="LTFS_SurveyAgent-Soma",
    entrypoint_fnc=my_agent,
    prewarm_fnc=prewarm,
    num_idle_processes=5,
    # ROOM type ensures the agent is optimized for the Jobs API flow
    worker_type=agents.WorkerType.ROOM, 
)

if __name__ == "__main__":
    agents.cli.run_app(server)