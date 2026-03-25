import asyncio
import logging
import os
import re
import sys
from pathlib import Path

from livekit.plugins.turn_detector.multilingual import MultilingualModel

# Ensure project root is on sys.path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Keep low-level library loggers quiet; show agent-level logs
# Note: do NOT call logging.basicConfig() here — the LiveKit agents framework sets up its own
# handlers (text + JSON) for each worker process. Calling basicConfig adds an extra text handler
# that causes every log line to appear twice.
for _noisy in ("livekit", "livekit.rtc", "livekit.agents", "livekit.plugins.sarvam", "livekit.plugins.sarvam.log",
               "livekit.plugins.elevenlabs","livekit.plugins.deepgram", "livekit.plugins.google", "livekit.plugins.silero",
               "livekit.plugins.turn_detector", "livekit.plugins.noise_cancellation", "httpx", "httpcore",
               "google_genai", "google.genai", "grpc",):
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
    UserInputTranscribedEvent,
    room_io,
)
from livekit.plugins import deepgram, elevenlabs, google, noise_cancellation, silero, sarvam

from agent.metrics import MetricsTracker
from agent.survey_agent import SurveyAssistant

load_dotenv()

# Import database storage helpers


# Module-level dict for end-call signal events (per call_id)
call_end_signals: dict[str, asyncio.Event] = {}


def _sanitize_assistant_text(text: str) -> str:
    """Remove accidental tool/markdown payloads from assistant text."""
    cleaned = re.sub(r"```[\s\S]*?```", "", text).strip()
    cleaned = re.sub(r"\{[^{}]*tool[^{}]*\}", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


# ============================================================================
# Prewarming
# ============================================================================

def prewarm(proc: agents.JobProcess):
    print("🔥 PREWARMING MODELS...")
    proc.userdata["vad"] = silero.VAD.load()
    proc.userdata["stt"] = deepgram.STT(model="nova-3", language="hi")
    proc.userdata["llm"] = google.LLM(
        model="gemini-2.0-flash",
        temperature=0.1,
        thinking_config=types.ThinkingConfig(include_thoughts=False),
    )
    # MultilingualModel is NOT pre-warmed here — its __init__ calls
    # get_job_context().inference_executor which is unavailable outside a job.
    # It is instantiated per-session inside my_agent() instead.


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

    from agent.db_storage import (
        _load_call_metadata as load_call_metadata,
        feedback_sessions,
        _default_feedback_session,
        buffer_transcript_turn,
    )

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

    # Set up end-call signal (moved here so _signal_hangup can reference it)
    call_end_signals[call_id] = asyncio.Event()

    # Event set when the SmartFlo bridge joins the room (customer answered)
    bridge_connected = asyncio.Event()

    def _signal_hangup() -> None:
        """Called by SurveyAssistant when the LLM invokes end_call()."""
        if call_id in call_end_signals:
            call_end_signals[call_id].set()

    # ElevenLabs TTS — eleven_turbo_v2_5 supports Hindi natively.
    # Voice: Aria (default). Change voice_id to swap voices.
    session_tts = elevenlabs.TTS(
        model="eleven_turbo_v2_5",
        language="hi",
        voice_id="XswejgPhV7IAyZmwhk56"
    )
    session = AgentSession(
        turn_detection=MultilingualModel(),  # type: ignore[arg-type]
        min_endpointing_delay=0.1,
        max_endpointing_delay=0.4,
        stt=ctx.proc.userdata["stt"],
        llm=ctx.proc.userdata["llm"],
        tts=session_tts,
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(ev: UserInputTranscribedEvent):
        if ev.is_final:
            logger.info(f"[{call_id}] 👤 User Speech: {ev.transcript}")

    @session.on("error")
    def on_session_error(err):
        logger.error(f"[{call_id}] ❌ Session Error: {err}")

    transcript_buffer = []

    @session.on("conversation_item_added")
    def on_conversation_item_added(event: ConversationItemAddedEvent):
        from datetime import datetime, timezone
        item = event.item
        text_to_store = (item.text_content or "").strip()
        if not text_to_store:
            return

        role = getattr(item, "role", None)
        role_str_to_store = (getattr(role, "value", None) or getattr(role, "name", None) or str(role)).lower()
        speaker_id_to_store = getattr(item, "speaker_id", None)
        ts_to_store = datetime.now(timezone.utc)

        transcript_buffer.append((role_str_to_store, text_to_store, speaker_id_to_store, ts_to_store))
        logger.info(f"[{call_id}] [{role_str_to_store.upper()}] {text_to_store[:120]}")

        if role_str_to_store in ("user", "assistant"):
            buffer_transcript_turn(call_id=call_id, role=role_str_to_store, text=text_to_store,
                                   speaker_id=speaker_id_to_store if role_str_to_store == "user" else None, )

    @session.on("metrics_collected")
    def on_metrics_collected(ev: MetricsCollectedEvent):
        tracker.on_metrics(ev)

    # ── Step 3: Register participant_connected BEFORE session.start() ─────────
    # If the customer answers while the greeting TTS is playing, this event fires
    # concurrently. Registering it early ensures we never miss it.
    @ctx.room.on("participant_connected")
    def on_participant_connected(participant_details):
        participant_identity = str(participant_details.identity or "")
        logger.info(
            f"[{call_id}] 📞 Participant connected: {participant_identity} (kind={participant_details.kind})")
        bridge_connected.set()  # Unblock greeting — customer is now on the line
        import threading
        threading.Thread(target=_update_call_status_in_bg, args=(call_id,), daemon=True).start()
        if call_id in feedback_sessions:
            feedback_sessions[call_id]["call_answered"] = True
            # Smartflow participant identity format:
            #   smartflo-caller-<callSid>
            # Keep this ID for downstream persistence (customer_feedback.conversation_id).
            prefix = "smartflo-caller-"
            if participant_identity.startswith(prefix):
                feedback_sessions[call_id]["smartflow_call_id"] = participant_identity[len(prefix):]

    # Handle race: bridge may have joined before the handler was registered
    if ctx.room.remote_participants:
        bridge_connected.set()

    try:
        # ── Step 4: Start session ──────────────────────────────────────────────
        # session.start() internally calls ctx.connect() which is now a no-op
        # since we already connected above. The room is fully live here.
        transliterate_task = asyncio.create_task(_transliterate_name(customer_name)) if customer_name else None

        await session.start(
            room=ctx.room,
            agent=SurveyAssistant(call_id=call_id, customer_name=customer_name, agreement_no=agreement_no,
                                  on_end_call=_signal_hangup),
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
        logger.info(f"[{call_id}] ✅ AgentSession started, waiting for SmartFlo bridge...")

        # ── Wait for the SmartFlo bridge (customer answered the phone) ─────────
        # The bridge joins the LiveKit room only AFTER the customer picks up.
        # Saying the greeting before the bridge is present means the customer
        # hears nothing. We block here until the audio path is live.
        try:
            await asyncio.wait_for(bridge_connected.wait(), timeout=120.0)
            logger.info(f"[{call_id}] ✅ Bridge connected — warming up audio path")
            # Short pause for the audio pipeline (track subscription → μ-law forwarding)
            # to fully establish before we start TTS. Without this the customer
            # misses the first ~0.5 s of the greeting.
            await asyncio.sleep(0.35)
        except asyncio.TimeoutError:
            logger.warning(f"[{call_id}] ⚠️ SmartFlo bridge never connected within 120s — aborting")
            return

        # Get transliterated name (should be done by now — customer took time to answer)
        hindi_name = customer_name
        if transliterate_task:
            try:
                hindi_name = await asyncio.wait_for(transliterate_task, timeout=1.0)
            except Exception:
                hindi_name = customer_name

        name_part = f"{hindi_name} जी" if hindi_name else "आप"
        greeting_text = (
            f"नमस्ते, मैं एल एंड टी फाइनेंस की तरफ़ से बात कर रही हूँ। "
            f"यह कॉल आपके पेमेंट अनुभव को जानने के लिए है। "
            f"क्या मेरी बात {name_part} से हो रही है?"
        )

        buffer_transcript_turn(call_id=call_id, role="assistant", text=greeting_text)
        logger.info(f"[{call_id}] 🎙️ Playing greeting for {customer_name}")
        try:
            await asyncio.wait_for(session.say(greeting_text, allow_interruptions=True), timeout=30.0)
        except Exception as e:
            logger.warning(f"[{call_id}] Greeting say() error: {e}")

        # ── Step 5: Set up end-call signal and wait for room disconnect ────────
        # call_end_signals[call_id] was already created before session.start().
        # _signal_hangup() (passed to SurveyAssistant) will set it when end_call()
        # is invoked by the LLM, unblocking the task below to publish hangup.
        async def _wait_and_signal_hangup():
            await call_end_signals[call_id].wait()
            await asyncio.sleep(8.0)
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
            print(f"📋 Transcript summary:")
            prev_ts = None
            for idx, entry in enumerate(transcript_buffer):
                role_str, text, speaker_id = entry[0], entry[1], entry[2]
                ts = entry[3] if len(entry) > 3 else None
                if ts and prev_ts:
                    gap = (ts - prev_ts).total_seconds()
                    gap_str = f" (+{gap:.1f}s)" if gap >= 0.5 else ""
                else:
                    gap_str = ""
                ts_str = ts.strftime("%H:%M:%S") if ts else ""
                print(f"   {idx + 1}. [{role_str}] [{ts_str}{gap_str}] {text}")
                prev_ts = ts


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
)

if __name__ == "__main__":
    agents.cli.run_app(server)
