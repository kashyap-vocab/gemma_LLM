import asyncio

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

from metrics import MetricsTracker
from survey_agent import SurveyAssistant

load_dotenv()

# Import database storage helpers
from db_storage import (
    _load_call_metadata as load_call_metadata,
    store_conversation_turn,
    persist_feedback_to_db,
    update_call_status,
    feedback_sessions,
    _default_feedback_session,
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

    proc.userdata["tts"] = sarvam.TTS(
        target_language_code="hi-IN",
        speaker="simran",
        model="bulbul:v3",
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

    # Buffer transcripts in memory, flush to DB only at session end
    transcript_buffer = []

    @session.on("conversation_item_added")
    def on_conversation_item_added(event: ConversationItemAddedEvent):
        item = event.item
        text = (item.text_content or "").strip()
        if not text:
            return
        role = getattr(item, "role", None)
        role_str = (getattr(role, "value", None) or getattr(role, "name", None) or str(role)).lower()
        transcript_buffer.append((role_str, text, getattr(item, "speaker_id", None)))

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

        # Keep session alive until the room disconnects
        # Without this, the function returns and kills the agent mid-conversation
        disconnect_event = asyncio.Event()

        @ctx.room.on("disconnected")
        def on_room_disconnect():
            disconnect_event.set()

        await disconnect_event.wait()

    finally:
        print("\n\n🛑 Session ending...")
        tracker.print_session_summary()

        # Flush transcripts + feedback to DB (awaited, not fire-and-forget)
        try:
            print(f"💾 Flushing {len(transcript_buffer)} transcripts + feedback to DB...")
            for role_str, text, speaker_id in transcript_buffer:
                if role_str == "user":
                    await store_conversation_turn(call_id, customer_phone, customer_transcript=text, speaker_id=speaker_id)
                elif role_str == "assistant":
                    await store_conversation_turn(call_id, customer_phone, agent_transcript=text)
            await persist_feedback_to_db(call_id, customer_phone)
            if customer_phone:
                await update_call_status(customer_phone, "completed")
            feedback_sessions.pop(call_id, None)
            print(f"💾 Done flushing to DB")
        except Exception as e:
            print(f"❌ Error flushing to DB: {e}")


if __name__ == "__main__":
    server = agents.WorkerOptions(
        agent_name="LTFS_SurveyAgent-Soma",
        entrypoint_fnc=my_agent,
        prewarm_fnc=prewarm,
    )

    agents.cli.run_app(server)
