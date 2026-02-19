from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from livekit import api, rtc
import asyncio
import base64
import json
import os
from dotenv import load_dotenv
import audioop

load_dotenv()


def _set_call_status_active(customer_phone: str) -> None:
    """Update active_call_context.call_status to 'active' when call is answered."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url or not customer_phone:
        return
    try:
        import psycopg2
        conn = psycopg2.connect(database_url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE active_call_context SET call_status = 'active', updated_at = NOW() "
                    "WHERE phone_number = %s",
                    (customer_phone,),
                )
            conn.commit()
            print(f"[BRIDGE] 📞 call_status → 'active' for {customer_phone}")
        finally:
            conn.close()
    except Exception as e:
        print(f"[BRIDGE] ⚠️ Could not set call_status active: {e}")

app = FastAPI()

LIVEKIT_URL = os.getenv('LIVEKIT_URL')
LIVEKIT_API_KEY = os.getenv('LIVEKIT_API_KEY')
LIVEKIT_API_SECRET = os.getenv('LIVEKIT_API_SECRET')
SMARTFLO_FROM_NUMBER = os.getenv('SMARTFLO_FROM_NUMBER', '')


def normalize_phone(number: str) -> str:
    """Strip +, country code 91, spaces, dashes to get bare 10-digit number."""
    if not number:
        return number
    clean = number.strip().replace(" ", "").replace("-", "")
    clean = clean.lstrip('+')
    if clean.startswith('91') and len(clean) > 10:
        clean = clean[2:]
    return clean


class SmartfloLiveKitBridge:
    def __init__(self, stream_sid: str, call_sid: str, account_sid: str,
                 from_number: str = None, to_number: str = None):
        self.stream_sid = stream_sid
        self.call_sid = call_sid
        self.account_sid = account_sid

        # Determine customer phone: in an outbound click-to-call,
        # 'to' is the customer being called, 'from' is SmartFlo caller ID.
        # Pick whichever is NOT the SmartFlo system number.
        self.customer_phone = self._resolve_customer_phone(from_number, to_number)

        # Join the pre-created room 'call-{customer_phone}'
        # This room was created by Route 1 (/api/calls/context)
        self.room_name = f"call-{self.customer_phone}"
        print(f"[BRIDGE] 📞 Resolved customer_phone={self.customer_phone}, room={self.room_name}")
        print(f"[BRIDGE]    Raw from={from_number}, to={to_number}")

        self.room = None
        self.audio_source = None
        self.audio_track = None
        self.ws = None
        self.chunk_counter = 1
        self.sequence_number = 1
        self._hangup_requested = False

    @staticmethod
    def _resolve_customer_phone(from_number: str = None, to_number: str = None) -> str:
        """Determine which number is the customer (not the SmartFlo system number).
        Returns normalized 10-digit number to match room naming in Route 1."""
        smartflo_num = normalize_phone(SMARTFLO_FROM_NUMBER) if SMARTFLO_FROM_NUMBER else ''

        for num in [to_number, from_number]:
            if num:
                clean = normalize_phone(num)
                if clean and clean != smartflo_num:
                    return clean  # Return normalized number
        # Fallback
        return normalize_phone(to_number) or normalize_phone(from_number) or "unknown"

    async def setup_livekit(self):
        """Connect to LiveKit room and publish audio track"""
        print(f"[BRIDGE] 🚀 Setting up LiveKit for call: {self.call_sid}")

        # Prepare metadata with customer phone for agent access
        metadata = {}
        if self.customer_phone:
            metadata["customer_phone"] = self.customer_phone
            metadata["call_sid"] = self.call_sid
            print(f"[BRIDGE] 📝 Setting participant metadata with customer_phone: {self.customer_phone}")

        # Generate token
        token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
            .with_identity(f"smartflo-caller-{self.call_sid}") \
            .with_name("Phone Caller") \
            .with_grants(api.VideoGrants(
            room_join=True,
            room=self.room_name,
            can_publish=True,
            can_subscribe=True,
        ))

        # Add metadata to token if available
        if metadata:
            token = token.with_metadata(json.dumps(metadata))

        # Connect to room
        self.room = rtc.Room()

        # Set up event handlers BEFORE connecting
        @self.room.on("track_subscribed")
        def on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication,
                                participant: rtc.RemoteParticipant):
            print(f"[BRIDGE] 📢 Subscribed to {track.kind} track from {participant.identity}")

            if track.kind == rtc.TrackKind.KIND_AUDIO:
                asyncio.create_task(self.forward_livekit_to_smartflo(track))

        @self.room.on("data_received")
        def on_data_received(data: rtc.DataPacket):
            try:
                payload = json.loads(data.data.decode("utf-8"))
                if payload.get("action") == "hangup":
                    print(f"[BRIDGE] 📴 Received hangup signal from agent, disconnecting...")
                    self._hangup_requested = True
                    asyncio.create_task(self._handle_hangup())
            except Exception as e:
                pass  # Ignore non-JSON data messages

        print(f"[BRIDGE] Connecting to LiveKit room: {self.room_name} at {LIVEKIT_URL}")
        await self.room.connect(LIVEKIT_URL, token.to_jwt())
        print(f"[BRIDGE] ✅ Connected to LiveKit room: {self.room_name}")

        # Create audio source for Smartflo audio (8kHz mulaw)
        self.audio_source = rtc.AudioSource(8000, 1)  # 8kHz, mono
        self.audio_track = rtc.LocalAudioTrack.create_audio_track(
            "smartflo-audio",
            self.audio_source
        )

        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.SOURCE_MICROPHONE

        await self.room.local_participant.publish_track(self.audio_track, options)
        print("[BRIDGE] 🎤 Published audio track to LiveKit")

    async def _handle_hangup(self):
        """Disconnect the SmartFlo WebSocket and LiveKit room after agent signals hangup."""
        try:
            # Close the SmartFlo WebSocket — this ends the telephony leg
            if self.ws:
                await self.ws.close()
                print(f"[BRIDGE] 📴 SmartFlo WebSocket closed")
        except Exception as e:
            print(f"[BRIDGE] ⚠️ Error closing SmartFlo WebSocket: {e}")
        try:
            if self.room:
                await self.room.disconnect()
                print(f"[BRIDGE] 📴 LiveKit room disconnected")
        except Exception as e:
            print(f"[BRIDGE] ⚠️ Error disconnecting LiveKit room: {e}")

    async def send_smartflo_audio_to_livekit(self, audio_payload: str):
        """Convert Smartflo mulaw audio to PCM and send to LiveKit"""
        try:
            # Decode base64
            mulaw_data = base64.b64decode(audio_payload)

            # Convert mulaw to PCM (16-bit linear)
            pcm_data = audioop.ulaw2lin(mulaw_data, 2)  # 2 bytes per sample (16-bit)

            # Create audio frame
            samples_per_channel = len(pcm_data) // 2  # 16-bit = 2 bytes per sample

            frame = rtc.AudioFrame(
                data=pcm_data,
                sample_rate=8000,
                num_channels=1,
                samples_per_channel=samples_per_channel
            )

            await self.audio_source.capture_frame(frame)

        except Exception as e:
            print(f"[BRIDGE] ❌ Error processing audio: {e}")

    async def forward_livekit_to_smartflo(self, track: rtc.AudioTrack):
        """Forward audio from LiveKit agent back to Smartflo"""

        audio_stream = rtc.AudioStream(
            track=track,
            sample_rate=8000,
            num_channels=1,
            frame_size_ms=20,  # 🔥 CRITICAL (160 samples)
        )

        async for event in audio_stream:
            frame = event.frame
            pcm = frame.data.tobytes()  # already 8kHz PCM16

            mulaw = audioop.lin2ulaw(pcm, 2)

            await self.ws.send_json({
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {
                    "payload": base64.b64encode(mulaw).decode(),
                    "chunk": self.chunk_counter,
                }
            })

            self.chunk_counter += 1


@app.websocket("/smartflo/stream")
async def smartflo_websocket_endpoint(websocket: WebSocket):
    """Handle Smartflo WebSocket connection - Main endpoint"""
    await websocket.accept()
    print("[BRIDGE] 📞 Smartflo WebSocket connected")

    bridge = None
    media_count = 0

    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            event = data.get("event")

            if event == "connected":
                print(f"[BRIDGE] ✅ Smartflo 'connected' event received")
                print(f"[BRIDGE]    Full data: {json.dumps(data, indent=2)}")

            elif event == "start":
                # Call started - extract metadata
                start_data = data.get("start", {})
                stream_sid = start_data.get("streamSid")
                call_sid = start_data.get("callSid")
                account_sid = start_data.get("accountSid")

                # Try both top-level and inside 'start' for from/to numbers
                from_number = data.get("from") or start_data.get("from")
                to_number = data.get("to") or start_data.get("to")

                print(f"[BRIDGE] 📞 Call 'start' event:")
                print(f"[BRIDGE]    Stream SID: {stream_sid}")
                print(f"[BRIDGE]    Call SID: {call_sid}")
                print(f"[BRIDGE]    From: {from_number} → To: {to_number}")
                print(f"[BRIDGE]    Full start data: {json.dumps(data, indent=2)}")

                # Pass both numbers so bridge can determine which is the customer
                bridge = SmartfloLiveKitBridge(
                    stream_sid, call_sid, account_sid,
                    from_number=from_number, to_number=to_number
                )
                bridge.ws = websocket
                await bridge.setup_livekit()

                # Mark call as 'active' so the auto-dialer poller knows the
                # call was answered and stops the unanswered-timeout counter.
                _set_call_status_active(bridge.customer_phone)

            elif event == "media":
                # Incoming audio from caller
                media_count += 1
                if media_count <= 3:
                    print(f"[BRIDGE] 🎵 Receiving media packet #{media_count}")
                elif media_count == 4:
                    print(f"[BRIDGE] 🎵 (suppressing further media logs...)")
                if bridge:
                    media_data = data.get("media", {})
                    payload = media_data.get("payload")
                    if payload:
                        await bridge.send_smartflo_audio_to_livekit(payload)

            elif event == "dtmf":
                dtmf_data = data.get("dtmf", {})
                digit = dtmf_data.get("digit")
                print(f"[BRIDGE] 🔢 DTMF received: {digit}")

            elif event == "stop":
                print(f"[BRIDGE] 📴 Call ended")
                stop_data = data.get("stop", {})
                reason = stop_data.get("reason", "Unknown")
                print(f"[BRIDGE]    Reason: {reason}")
                print(f"[BRIDGE]    Total media packets received: {media_count}")

                if bridge and bridge.room:
                    await bridge.room.disconnect()
                break

            elif event == "mark":
                mark_data = data.get("mark", {})
                mark_name = mark_data.get("name")
                print(f"[BRIDGE] ✅ Mark received: {mark_name}")

            else:
                print(f"[BRIDGE] ❓ Unknown event: {event}")
                print(f"[BRIDGE]    Data: {json.dumps(data, indent=2)}")

    except WebSocketDisconnect:
        print(f"[BRIDGE] 🔌 WebSocket disconnected (media packets: {media_count})")
        if bridge and bridge.room:
            await bridge.room.disconnect()
    except Exception as e:
        print(f"[BRIDGE] ❌ Error in WebSocket handler: {e}")
        import traceback
        traceback.print_exc()
        if bridge and bridge.room:
            await bridge.room.disconnect()


@app.get("/")
async def health_check():
    return {
        "status": "ok",
        "service": "smartflo-livekit-bridge",
        "version": "1.0"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8319)