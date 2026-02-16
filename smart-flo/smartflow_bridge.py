from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from jinja2.ext import debug
from livekit import api, rtc
import asyncio
import base64
import json
import os
from dotenv import load_dotenv
import logging
import audioop

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("root").setLevel(logging.WARNING)

load_dotenv()

app = FastAPI()

LIVEKIT_URL = os.getenv('LIVEKIT_URL')
LIVEKIT_API_KEY = os.getenv('LIVEKIT_API_KEY')
LIVEKIT_API_SECRET = os.getenv('LIVEKIT_API_SECRET')


class SmartfloLiveKitBridge:
    def __init__(self, stream_sid: str, call_sid: str, account_sid: str, customer_phone: str = None):
        self.stream_sid = stream_sid
        self.call_sid = call_sid
        self.account_sid = account_sid
        self.customer_phone = customer_phone
        self.room_name = f"smartflo-{call_sid}"
        self.room = None
        self.audio_source = None
        self.audio_track = None
        self.ws = None
        self.chunk_counter = 1
        self.sequence_number = 1

    async def setup_livekit(self):
        """Connect to LiveKit room and publish audio track"""
        logger.info(f"🚀 Setting up LiveKit for call: {self.call_sid}")

        # Prepare metadata with customer phone for agent access
        metadata = {}
        if self.customer_phone:
            metadata["customer_phone"] = self.customer_phone
            metadata["call_sid"] = self.call_sid
            logger.info(f"📝 Setting room metadata with customer_phone: {self.customer_phone}")

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
            logger.info(f"📢 Subscribed to {track.kind} track from {participant.identity}")

            if track.kind == rtc.TrackKind.KIND_AUDIO:
                asyncio.create_task(self.forward_livekit_to_smartflo(track))

        await self.room.connect(LIVEKIT_URL, token.to_jwt())
        logger.info(f"✅ Connected to LiveKit room: {self.room_name}")

        # Create audio source for Smartflo audio (8kHz mulaw)
        self.audio_source = rtc.AudioSource(8000, 1)  # 8kHz, mono
        self.audio_track = rtc.LocalAudioTrack.create_audio_track(
            "smartflo-audio",
            self.audio_source
        )

        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.SOURCE_MICROPHONE

        await self.room.local_participant.publish_track(self.audio_track, options)
        logger.info("🎤 Published audio track to LiveKit")

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
            logger.error(f"❌ Error processing audio: {e}")

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
    logger.info("📞 Smartflo WebSocket connected")

    bridge = None

    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            event = data.get("event")
            if event == "connected":
                logger.info("✅ Smartflo connected event received")

            elif event == "start":
                # Call started - extract metadata
                start_data = data.get("start", {})
                stream_sid = start_data.get("streamSid")
                call_sid = start_data.get("callSid")
                account_sid = start_data.get("accountSid")
                from_number = data.get("from")
                to_number = data.get("to")

                logger.info(f"📞 Call started:")
                logger.info(f"   Stream SID: {stream_sid}")
                logger.info(f"   Call SID: {call_sid}")
                logger.info(f"   From: {from_number} → To: {to_number}")

                # Pass customer phone to bridge for metadata
                bridge = SmartfloLiveKitBridge(stream_sid, call_sid, account_sid, customer_phone=from_number)
                bridge.ws = websocket
                await bridge.setup_livekit()

            elif event == "media":
                # Incoming audio from caller
                if bridge:
                    media_data = data.get("media", {})
                    payload = media_data.get("payload")
                    if payload:
                        await bridge.send_smartflo_audio_to_livekit(payload)

            elif event == "dtmf":
                # DTMF tone received
                dtmf_data = data.get("dtmf", {})
                digit = dtmf_data.get("digit")
                logger.info(f"🔢 DTMF received: {digit}")

            elif event == "stop":
                logger.info("📴 Call ended")
                stop_data = data.get("stop", {})
                reason = stop_data.get("reason", "Unknown")
                logger.info(f"   Reason: {reason}")

                if bridge and bridge.room:
                    await bridge.room.disconnect()
                break

            elif event == "mark":
                # Mark event received - audio playback complete
                mark_data = data.get("mark", {})
                mark_name = mark_data.get("name")
                logger.info(f"✅ Mark received: {mark_name}")

    except WebSocketDisconnect:
        logger.info("🔌 WebSocket disconnected")
        if bridge and bridge.room:
            await bridge.room.disconnect()
    except Exception as e:
        logger.error(f"❌ Error in WebSocket handler: {e}")
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