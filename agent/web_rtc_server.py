import asyncio
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path so 'from agent.xxx import' always works,
# regardless of which directory the script is launched from.
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# ── Logging configuration ────────────────────────────────────────────────
# Suppress noisy third-party DEBUG/INFO logs so our prints are visible.
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# Silence the chattiest loggers explicitly
for _noisy in (
        "livekit",
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
        "google.genai",
        "grpc",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

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
from livekit.plugins import deepgram, google, noise_cancellation, silero
from livekit.plugins import sarvam
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from agent.metrics import MetricsTracker
from agent.survey_agent import SurveyAssistant, call_end_signals

load_dotenv()

# Import database storage helpers
from agent.db_storage import (
    _load_call_metadata as load_call_metadata,
    feedback_sessions,
    _default_feedback_session,
    store_conversation_turn,          # ← real-time per-turn async writer
)


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

    # NOTE: sarvam.TTS is intentionally NOT prewarmed/shared here.
    # It holds a stateful WebSocket connection internally. Sharing one TTS
    # instance across concurrent sessions causes 'Cannot write to closing
    # transport' errors when any session ends and closes that shared socket.
    # Each session creates its own TTS instance in my_agent() below.
    print("ℹ️  TTS will be created per-session (not shared)")


# ============================================================================
# Main Agent Session
# ============================================================================

async def my_agent(ctx: agents.JobContext):
    """Main agent session with official LiveKit metrics"""
    import json as _json

    # Create metrics tracker
    tracker = MetricsTracker()
    tracker.start_turn()

    call_id = ctx.room.name or "unknown"

    # === Extract customer info ===
    # Priority 1: Room metadata (set when room was created by /api/calls/context)
    customer_phone = None
    customer_name = None

    try:
        room_meta_str = ctx.room.metadata
        if room_meta_str:
            room_meta = _json.loads(room_meta_str)
            customer_phone = room_meta.get("customer_phone")
            customer_name = room_meta.get("customer_name")
            if customer_phone or customer_name:
                print(f"✅ Got customer info from room metadata: {customer_name} ({customer_phone})")
    except Exception as e:
        print(f"Warning: Could not parse room metadata: {e}")

    # Priority 2: Participant metadata (fallback for backward compatibility)
    if not customer_phone:
        try:
            await asyncio.sleep(0.5)
            for participant in ctx.room.remote_participants.values():
                if participant.metadata:
                    try:
                        metadata = _json.loads(participant.metadata)
                        customer_phone = metadata.get("customer_phone")
                        if customer_phone:
                            print(f"📞 Got customer_phone from participant metadata: {customer_phone}")
                            break
                    except _json.JSONDecodeError:
                        pass
        except Exception as e:
            print(f"Warning: Could not extract customer_phone from participant metadata: {e}")

    # Priority 3: DB lookup (fallback)
    if not customer_name:
        db_phone, db_name = load_call_metadata(call_id, customer_phone=customer_phone)
        customer_phone = customer_phone or db_phone
        customer_name = db_name or customer_name

    # Initialize feedback session
    feedback_sessions[call_id] = _default_feedback_session(call_id)
    if customer_name:
        feedback_sessions[call_id]["customer_name"] = customer_name

    print(f"\n🎯 SESSION START")
    print(f"Room: {call_id}")
    if customer_name:
        print(f"Customer: {customer_name} ({customer_phone})")
    else:
        print(f"⚠️ No customer name found - agent will proceed without personalization")
    print()

    # Use prewarmed models — VAD, STT, LLM are safely shareable.
    # TTS gets a fresh instance per session: sarvam.TTS holds a stateful
    # WebSocket connection. Sharing it across concurrent sessions causes
    # 'Cannot write to closing transport' errors when any one session ends.
    session_tts = sarvam.TTS(
        target_language_code="hi-IN",
        speaker="simran",
        model="bulbul:v3",
        pace=1.0,
        pitch=0.0,
        loudness=1.0,
    )

    session = AgentSession(
        turn_detection=MultilingualModel(),
        min_endpointing_delay=0.1,
        max_endpointing_delay=0.4,
        stt=ctx.proc.userdata["stt"],
        llm=ctx.proc.userdata["llm"],
        tts=session_tts,
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    # Buffer transcripts in memory for logging; each turn is ALSO written to DB
    # immediately (real-time) so no data is lost if the server crashes mid-call.
    transcript_buffer = []

    @session.on("conversation_item_added")
    def on_conversation_item_added(event: ConversationItemAddedEvent):
        item = event.item
        text = (item.text_content or "").strip()
        print(
            f"🔔 CONVERSATION_ITEM_ADDED: role={getattr(item, 'role', None)}, text_length={len(text)}, text_preview={text[:50] if text else 'EMPTY'}")
        if not text:
            return

        role = getattr(item, "role", None)
        role_str = (getattr(role, "value", None) or getattr(role, "name", None) or str(role)).lower()
        speaker_id = getattr(item, "speaker_id", None)

        # Keep buffer for end-of-call logging
        transcript_buffer.append((role_str, text, speaker_id))
        print(f"📝 [{len(transcript_buffer)}] Real-time saving [{role_str}]: {text[:50]}...")

        # ── Real-time DB write ───────────────────────────────────────────────
        # asyncio.ensure_future() schedules the async coroutine on the running
        # event loop without blocking the sync event handler.
        if role_str == "user":
            asyncio.ensure_future(
                store_conversation_turn(
                    call_id,
                    customer_phone,
                    customer_transcript=text,
                    agent_transcript=None,
                    speaker_id=speaker_id,
                    language="hi",
                )
            )
        elif role_str == "assistant":
            asyncio.ensure_future(
                store_conversation_turn(
                    call_id,
                    customer_phone,
                    customer_transcript=None,
                    agent_transcript=text,
                    speaker_id=None,
                    language="hi",
                )
            )

    # Subscribe to official LiveKit metrics
    @session.on("metrics_collected")
    def on_metrics_collected(ev: MetricsCollectedEvent):
        tracker.on_metrics(ev)

    try:
        await session.start(
            room=ctx.room,
            agent=SurveyAssistant(call_id=call_id, customer_name=customer_name),
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

        # Debug: log remote participants and their published tracks right after session start
        try:
            print("🔎 Remote participants at session start:")
            for pid, participant in ctx.room.remote_participants.items():
                try:
                    pubs = getattr(participant, 'tracks', None) or getattr(participant, 'published_tracks', None) or []
                    track_info = []
                    for t in pubs:
                        try:
                            track_info.append(
                                f"{getattr(t, 'name', getattr(t, 'sid', 'unknown'))}:{getattr(t, 'kind', 'unknown')}")
                        except Exception:
                            pass
                    print(f" - {participant.identity} ({participant.sid}) tracks={track_info}")
                except Exception:
                    print(f" - {participant} (could not list tracks)")
        except Exception as e:
            print(f"Warning: could not enumerate remote participants: {e}")
        # Transliterate customer name to Devanagari for TTS (with 5s timeout)
        hindi_name = None
        if customer_name:
            try:
                client = genai.Client()
                resp = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=f"Convert this Indian name from English to Hindi Devanagari script. Reply with ONLY the Devanagari name, nothing else: {customer_name}",
                    ),
                    timeout=5.0,
                )
                hindi_name = resp.text.strip()
                print(f"📝 Transliterated name: {customer_name} → {hindi_name}")
            except asyncio.TimeoutError:
                print(f"⚠️ Transliteration timed out after 5s, using original name")
                hindi_name = customer_name
            except Exception as e:
                print(f"⚠️ Transliteration failed, using original: {e}")
                hindi_name = customer_name

        # Dynamic greeting with customer name via TTS
        name_part = f"{hindi_name} जी" if hindi_name else "आप"
        greeting_text = f"नमस्ते, मैं एल एंड टी फाइनेंस की तरफ़ से बात कर रही हूँ। यह कॉल आपके पेमेंट अनुभव को जानने के लिए है। क्या मेरी बात {name_part} से हो रही है?"
        print(f"🗣️ Greeting: {greeting_text}")
        try:
            await asyncio.wait_for(
                session.say(greeting_text, allow_interruptions=True),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            print(f"⚠️ Greeting TTS timed out after 15s - session will continue without greeting")
        except Exception as e:
            print(f"⚠️ Greeting failed: {e} - session will continue")

        # Register a call-end signal so complete_survey() can trigger hangup
        call_end_signals[call_id] = asyncio.Event()

        async def _wait_and_signal_hangup():
            """Wait for complete_survey() signal, then tell the bridge to hang up."""
            await call_end_signals[call_id].wait()
            print(f"[AGENT] 📴 Call end signal received for {call_id}, waiting 1.5s for TTS to finish...")
            await asyncio.sleep(1.5)  # Allow closing statement TTS to play
            try:
                import json as _j
                hangup_msg = _j.dumps({"action": "hangup"}).encode("utf-8")
                await ctx.room.local_participant.publish_data(hangup_msg, reliable=True)
                print(f"[AGENT] 📴 Hangup data message sent to room {call_id}")
            except Exception as e:
                print(f"[AGENT] ❌ Failed to send hangup data message: {e}")

        asyncio.create_task(_wait_and_signal_hangup())

        # Keep session alive until the room disconnects
        # Without this, the function returns and kills the agent mid-conversation
        disconnect_event = asyncio.Event()

        @ctx.room.on("disconnected")
        def on_room_disconnect():
            disconnect_event.set()

        await disconnect_event.wait()

    finally:
        # Clean up call-end signal
        call_end_signals.pop(call_id, None)

        print("\n\n🛑 Session ending...")
        tracker.print_session_summary()

        # Always update call status first so the auto-dialer can advance immediately,
        # even if transcript storage fails below.
        if customer_phone:
            try:
                from agent.db_storage import _update_call_status_sync
                _update_call_status_sync(customer_phone, "completed")
            except Exception as e:
                print(f"❌ Error updating call status: {e}")

        # Transcripts were already written to DB in real-time during the call.
        # Here we only need to:
        #   1. Log the full buffer for debugging
        #   2. Persist the final feedback/survey data
        try:
            print(f"💾 Call ended — {len(transcript_buffer)} turns were saved in real-time during call.")
            print(f"📋 Transcript summary:")
            for idx, (role_str, text, speaker_id) in enumerate(transcript_buffer):
                print(f"   {idx + 1}. [{role_str}] {text[:60]}...")

            from agent.db_storage import _persist_feedback_to_db_sync

            # Persist structured feedback (identity, payment details, confirmation)
            _persist_feedback_to_db_sync(call_id, customer_phone)
            feedback_sessions.pop(call_id, None)
            print(f"💾 Feedback data saved for call_id={call_id}")
        except Exception as e:
            print(f"❌ Error saving feedback to DB: {e}")
            import traceback
            traceback.print_exc()


# ---------------------------------------------------------------------------
# Server configuration — exported for Docker entrypoint and start.sh
# ---------------------------------------------------------------------------
# num_idle_processes=5  → keep 5 pre-warmed agent processes ready at all times
#                          so all 5 concurrent calls are accepted with zero
#                          cold-start latency (VAD/STT/LLM/TTS already loaded).
# Each LiveKit room gets its own process slot; the semaphore in auto_dialer.py
# (CONCURRENCY=5) ensures we never dispatch more than 5 rooms simultaneously.
server = agents.WorkerOptions(
    agent_name="LTFS_SurveyAgent-Soma",
    entrypoint_fnc=my_agent,
    prewarm_fnc=prewarm,
    num_idle_processes=5,   # pre-warm 5 slots = 5 simultaneous calls, zero latency
)

if __name__ == "__main__":
    agents.cli.run_app(server)
