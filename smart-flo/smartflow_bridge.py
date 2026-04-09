import asyncio
import base64
import json
import os
from datetime import datetime, timezone

import audioop
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from livekit import api, rtc

# Import ORM components
from db.database import SessionLocal
from db.models import CallMetadata

load_dotenv()


def _set_call_status_active(room_name: str) -> None:
    """Update call_metadata status to 'active' using call_id (Primary Key)."""
    with SessionLocal() as db:
        try:
            # Optimized PK lookup
            call = db.query(CallMetadata).get(room_name)
            if call:
                call.call_status = 'active'
                call.updated_at = datetime.now(timezone.utc)
                db.commit()
                print(f"[BRIDGE] 📞 [ORM] status → 'active' for room: {room_name}")
        except Exception as e:
            db.rollback()
            print(f"[BRIDGE] ⚠️ ORM Error (Active): {e}")


def _set_call_status_terminal(room_name: str, status: str = "completed") -> None:
    """Ensure the Auto-Dialer stops polling by setting a terminal status via ORM."""
    with SessionLocal() as db:
        try:
            call = db.query(CallMetadata).get(room_name)
            if call:
                call.call_status = status
                call.updated_at = datetime.now(timezone.utc)
                db.commit()
                print(f"[BRIDGE] 🏁 [ORM] status → '{status}' for room: {room_name}")
        except Exception as e:
            db.rollback()
            print(f"[BRIDGE] ⚠️ ORM Error (Terminal): {e}")


# ── App Logic ────────────────────────────────────────────────────────────────

app = FastAPI()

LIVEKIT_URL = os.getenv('LIVEKIT_URL')
LIVEKIT_API_KEY = os.getenv('LIVEKIT_API_KEY')
LIVEKIT_API_SECRET = os.getenv('LIVEKIT_API_SECRET')
SMARTFLO_FROM_NUMBER = os.getenv('SMARTFLO_FROM_NUMBER') or os.getenv('SMARTFLO_PHONE_NUMBER', '')
SMARTFLO_INPUT_GAIN = float(os.getenv('SMARTFLO_INPUT_GAIN', '1.0'))
SMARTFLO_AUDIO_STATS_EVERY = max(int(os.getenv('SMARTFLO_AUDIO_STATS_EVERY', '0')), 0)
SMARTFLO_LIVEKIT_SAMPLE_RATE = int(os.getenv('SMARTFLO_LIVEKIT_SAMPLE_RATE', '16000'))
SMARTFLO_PSTN_SAMPLE_RATE = int(os.getenv('SMARTFLO_PSTN_SAMPLE_RATE', '8000'))


def normalize_phone(number: str) -> str:
    if not number: return number
    clean = str(number).strip().replace(" ", "").replace("-", "").lstrip('+')
    if clean.startswith('91') and len(clean) > 10:
        clean = clean[2:]
    return clean


class SmartfloLiveKitBridge:
    def __init__(self, stream_sid: str, call_sid: str, account_sid: str,
                 from_number: str = None, to_number: str = None):
        self.stream_sid = stream_sid
        self.call_sid = call_sid
        self.account_sid = account_sid

        # Resolve customer phone to match Route 1 naming convention
        self.customer_phone = self._resolve_customer_phone(from_number, to_number)
        self.room_name = f"call-{self.customer_phone}"
        self.room = None
        self.audio_source = None
        self.audio_track = None
        self.ws = None
        self.chunk_counter = 1
        self._closed = asyncio.Event()
        # Used by the mark_and_close path: we send a SmartFlo `mark` event
        # after the closing TTS audio and only tear down the WS once SmartFlo
        # echoes that same mark name back (= audio has actually played out).
        self._pending_mark: str | None = None
        self._mark_echo: asyncio.Event | None = None
        self._in_resample_state = None
        self._out_resample_state = None
        self._audio_in_counter = 0
        self._audio_out_counter = 0

    @staticmethod
    def _resolve_customer_phone(from_number: str = None, to_number: str = None) -> str:
        smartflo_num = normalize_phone(SMARTFLO_FROM_NUMBER) if SMARTFLO_FROM_NUMBER else ''
        for num in [to_number, from_number]:
            if num:
                clean = normalize_phone(num)
                if clean and clean != smartflo_num:
                    return clean
        return normalize_phone(to_number) or normalize_phone(from_number) or "unknown"

    async def setup_livekit(self):
        token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
            .with_identity(f"smartflo-caller-{self.call_sid}") \
            .with_name("Phone Caller") \
            .with_grants(api.VideoGrants(
            room_create=True,
            room_join=True,
            room=self.room_name,
            can_publish=True,
            can_subscribe=True,
        ))

        self.room = rtc.Room()

        @self.room.on("track_subscribed")
        def on_track_subscribed(track: rtc.Track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                asyncio.create_task(self.forward_livekit_to_smartflo(track))

        @self.room.on("data_received")
        def on_data_received(data: rtc.DataPacket):
            try:
                payload = json.loads(data.data.decode("utf-8"))
                action = payload.get("action")
                if action == "hangup":
                    self._closed.set()
                elif action == "mark_and_close":
                    name = payload.get("mark") or "end_of_call"
                    asyncio.create_task(self._mark_and_close(name))
            except:
                pass

        await self.room.connect(LIVEKIT_URL, token.to_jwt())
        self.audio_source = rtc.AudioSource(SMARTFLO_LIVEKIT_SAMPLE_RATE, 1)
        self.audio_track = rtc.LocalAudioTrack.create_audio_track("smartflo-audio", self.audio_source)
        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.SOURCE_MICROPHONE
        await self.room.local_participant.publish_track(self.audio_track, options)

    async def teardown(self):
        self._closed.set()
        if self.room:
            await self.room.disconnect()

    async def _mark_and_close(self, name: str):
        """
        Send a SmartFlo `mark` event and wait for SmartFlo to echo it back
        before tearing down. Per the bi-directional audio streaming spec,
        SmartFlo only echoes a mark when all media queued before that mark
        has finished playing on the customer's phone — so this gives us an
        accurate end-of-playback signal instead of a fixed delay.

        Closing the WebSocket here causes SmartFlo to send its own `stop`
        event upstream, which terminates the PSTN leg cleanly.
        """
        if not self.ws:
            self._closed.set()
            return
        self._pending_mark = name
        self._mark_echo = asyncio.Event()
        try:
            await self.ws.send_json({
                "event": "mark",
                "streamSid": self.stream_sid,
                "mark": {"name": name},
            })
            print(f"[BRIDGE] 📍 sent mark '{name}', awaiting echo from SmartFlo")
        except Exception as e:
            print(f"[BRIDGE] ⚠️ failed to send mark: {e}")
            self._closed.set()
            return
        try:
            await asyncio.wait_for(self._mark_echo.wait(), timeout=15.0)
            print(f"[BRIDGE] ✅ mark '{name}' echoed by SmartFlo — closing")
        except asyncio.TimeoutError:
            print(f"[BRIDGE] ⏱️ mark '{name}' echo timed out — closing anyway")
        self._closed.set()

    async def forward_livekit_to_smartflo(self, track: rtc.AudioTrack):
        audio_stream = rtc.AudioStream(
            track,
            sample_rate=SMARTFLO_LIVEKIT_SAMPLE_RATE,
            num_channels=1,
            frame_size_ms=20,
        )
        async for event in audio_stream:
            if self._closed.is_set(): break
            pcm = event.frame.data.tobytes()
            if SMARTFLO_LIVEKIT_SAMPLE_RATE != SMARTFLO_PSTN_SAMPLE_RATE:
                pcm, self._out_resample_state = audioop.ratecv(
                    pcm,
                    2,
                    1,
                    SMARTFLO_LIVEKIT_SAMPLE_RATE,
                    SMARTFLO_PSTN_SAMPLE_RATE,
                    self._out_resample_state,
                )
            mulaw = audioop.lin2ulaw(pcm, 2)
            self._audio_out_counter += 1
            if SMARTFLO_AUDIO_STATS_EVERY and self._audio_out_counter % SMARTFLO_AUDIO_STATS_EVERY == 0:
                print(
                    f"[BRIDGE] 🔊 LK→PSTN frame={self._audio_out_counter} "
                    f"sr={SMARTFLO_LIVEKIT_SAMPLE_RATE}->{SMARTFLO_PSTN_SAMPLE_RATE} "
                    f"rms={audioop.rms(pcm, 2)} peak={audioop.max(pcm, 2)}"
                )
            try:
                await self.ws.send_json({
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {"payload": base64.b64encode(mulaw).decode(), "chunk": self.chunk_counter}
                })
                self.chunk_counter += 1
            except:
                break

    async def send_smartflo_audio_to_livekit(self, audio_payload: str):
        try:
            mulaw_data = base64.b64decode(audio_payload)
            pcm_data = audioop.ulaw2lin(mulaw_data, 2)
            if SMARTFLO_PSTN_SAMPLE_RATE != SMARTFLO_LIVEKIT_SAMPLE_RATE:
                pcm_data, self._in_resample_state = audioop.ratecv(
                    pcm_data,
                    2,
                    1,
                    SMARTFLO_PSTN_SAMPLE_RATE,
                    SMARTFLO_LIVEKIT_SAMPLE_RATE,
                    self._in_resample_state,
                )
            if SMARTFLO_INPUT_GAIN > 0 and abs(SMARTFLO_INPUT_GAIN - 1.0) > 1e-3:
                pcm_data = audioop.mul(pcm_data, 2, SMARTFLO_INPUT_GAIN)

            self._audio_in_counter += 1
            if SMARTFLO_AUDIO_STATS_EVERY and self._audio_in_counter % SMARTFLO_AUDIO_STATS_EVERY == 0:
                print(
                    f"[BRIDGE] 🎤 PSTN→LK frame={self._audio_in_counter} "
                    f"sr={SMARTFLO_PSTN_SAMPLE_RATE}->{SMARTFLO_LIVEKIT_SAMPLE_RATE} "
                    f"gain={SMARTFLO_INPUT_GAIN:.2f} rms={audioop.rms(pcm_data, 2)} "
                    f"peak={audioop.max(pcm_data, 2)}"
                )

            frame = rtc.AudioFrame(data=pcm_data, sample_rate=SMARTFLO_LIVEKIT_SAMPLE_RATE, num_channels=1,
                                   samples_per_channel=len(pcm_data) // 2)
            await self.audio_source.capture_frame(frame)
        except Exception as e:
            print(f"[BRIDGE] Audio capture error: {e}")


# ── WebSocket Endpoint ────────────────────────────────────────────────────────

@app.websocket("/smartflo/stream")
async def smartflo_websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    bridge = None
    try:
        while True:
            if bridge and bridge._closed.is_set(): break

            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except:
                break

            data = json.loads(message)
            event = data.get("event")

            if event == "start":
                start_data = data.get("start", {})
                bridge = SmartfloLiveKitBridge(
                    start_data.get("streamSid"), start_data.get("callSid"), start_data.get("accountSid"),
                    from_number=data.get("from") or start_data.get("from"),
                    to_number=data.get("to") or start_data.get("to")
                )
                bridge.ws = websocket
                await bridge.setup_livekit()
                # Use the room_name (Primary Key) for the ORM update
                _set_call_status_active(bridge.room_name)

            elif event == "media" and bridge:
                payload = data.get("media", {}).get("payload")
                if payload: await bridge.send_smartflo_audio_to_livekit(payload)

            elif event == "mark" and bridge:
                # SmartFlo echoes a mark only after all media queued before
                # it has finished playing on the customer's phone. We use
                # this as the precise end-of-playback signal for hangup.
                name = (data.get("mark") or {}).get("name")
                if name and name == bridge._pending_mark and bridge._mark_echo is not None:
                    bridge._mark_echo.set()

            elif event == "stop":
                if bridge: _set_call_status_terminal(bridge.room_name, "completed")
                break

    finally:
        if bridge:
            # Ensure DB is updated even if WebSocket drops unexpectedly
            _set_call_status_terminal(bridge.room_name, "completed")
            await bridge.teardown()
