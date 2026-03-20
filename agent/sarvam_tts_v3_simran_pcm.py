import asyncio
import base64
import logging
import re
import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any

import aiohttp

from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIError,
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    tts,
    utils,
)

logger = logging.getLogger(__name__)


SARVAM_TTS_WS_URL = "wss://api.sarvam.ai/text-to-speech/ws"

# Devanagari unicode block (covers Hindi script used in this flow).
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


@dataclass(frozen=True)
class SarvamTTSConfig:
    target_language_code: str
    speaker: str
    model: str
    pace: float
    speech_sample_rate: int
    enable_preprocessing: bool
    # Sarvam TTS supports pitch/loudness for some models; we keep defaults but
    # only include them if requested by the caller.
    pitch: float
    loudness: float
    include_pitch_loudness: bool
    send_completion_event: bool


def _mp3_bytes_to_wav_s16le(mp3_bytes: bytes, *, sample_rate: int) -> bytes:
    """
    Convert MP3 bytes to WAV (PCM s16le, mono) using ffmpeg.

    LiveKit's WAV decoding expects the bytes to form a valid WAV stream
    (with a RIFF header). Sarvam returns MP3 bytes, so we convert before
    pushing them to LiveKit.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "mp3",
        "-i",
        "pipe:0",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "wav",
        "-acodec",
        "pcm_s16le",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, input=mp3_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg mp3->pcm conversion failed: {proc.stderr.decode('utf-8', 'ignore')}")
    return proc.stdout


class SarvamFixedSynthesizeStream(tts.SynthesizeStream):
    """
    Streaming Sarvam TTS adapter that:
    - receives MP3 frames from Sarvam
    - converts the full MP3 byte stream to a single WAV payload
    - pushes them to LiveKit as `audio/wav`

    This avoids LiveKit's WAV decoder errors ("missing RIFF/WAVE").
    """

    def __init__(self, *, tts_instance: "SarvamFixedTTS", conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts_instance, conn_options=conn_options)
        self._tts: SarvamFixedTTS = tts_instance
        # Defensive compatibility: some older variants referenced `_input_text`.
        # Ensuring the attribute exists avoids AttributeError crashes.
        self._input_text = ""

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        request_id = utils.shortuuid()
        output_emitter.initialize(
            request_id=request_id,
            sample_rate=self._tts.sample_rate,
            num_channels=self._tts.num_channels,
            mime_type="audio/wav",
            stream=True,
            frame_size_ms=50,
        )

        segment_id = utils.shortuuid()
        output_emitter.start_segment(segment_id=segment_id)

        ws_url = (
            f"{SARVAM_TTS_WS_URL}?model={self._tts._config.model}"
            f"&send_completion_event={str(self._tts._config.send_completion_event).lower()}"
        )

        headers = {
            "api-subscription-key": self._tts._api_key,
            "User-Agent": "LiveKit-Sarvam-TTS-Fixed/1.0",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
        }

        session = utils.http_context.http_session()
        ws: aiohttp.ClientWebSocketResponse | None = None

        async def _send_task() -> None:
            assert ws is not None
            config_msg: dict[str, Any] = {
                "type": "config",
                "data": {
                    "target_language_code": self._tts._config.target_language_code,
                    "speaker": self._tts._config.speaker,
                    "pace": self._tts._config.pace,
                    "pitch": self._tts._config.pitch,
                    "loudness": self._tts._config.loudness,
                    "enable_preprocessing": self._tts._config.enable_preprocessing,
                    "model": self._tts._config.model,
                },
            }

            await ws.send_str(json.dumps(config_msg))

            # LiveKit streams text tokens; Sarvam validates *each* "text" message.
            # Sending per-token can fail when a token is whitespace/punctuation only.
            # So we buffer everything until FlushSentinel, then send as one request.
            text_buf: list[str] = []
            started = False
            async for chunk in self._input_ch:
                if isinstance(chunk, self._FlushSentinel):
                    break
                if isinstance(chunk, str) and chunk:
                    if not started:
                        self._mark_started()
                        started = True
                    text_buf.append(chunk)

            text = "".join(text_buf)
            # Sarvam rejects websocket "text" payloads that contain no characters
            # from the allowed languages (hi-IN => Devanagari). Prevent that here.
            if text.strip() and _DEVANAGARI_RE.search(text):
                await ws.send_str(json.dumps({"type": "text", "data": {"text": text}}))

            await ws.send_str(json.dumps({"type": "flush"}))

        async def _recv_task() -> None:
            assert ws is not None
            mp3_acc = bytearray()
            try:
                while True:
                    msg = await ws.receive(timeout=self._conn_options.timeout)
                    if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                        break

                    if msg.type == aiohttp.WSMsgType.TEXT:
                        resp = json.loads(msg.data)
                        msg_type = resp.get("type")
                        if not msg_type:
                            continue

                        if msg_type == "audio":
                            audio_data = resp.get("data", {}).get("audio", "")
                            if not audio_data:
                                continue
                            mp3_bytes = base64.b64decode(audio_data)
                            mp3_acc.extend(mp3_bytes)

                        elif msg_type == "error":
                            err = resp.get("data", {}).get("message", "Sarvam TTS error")
                            raise APIStatusError(message=err, status_code=500)

                        elif msg_type == "event":
                            event_data = resp.get("data", {}) or {}
                            event_type = event_data.get("event_type")
                            if event_type == "final":
                                if not mp3_acc:
                                    raise APIError("no audio frames produced by Sarvam")
                                # Convert in a thread to avoid blocking the event loop.
                                wav_bytes = await asyncio.to_thread(
                                    _mp3_bytes_to_wav_s16le,
                                    bytes(mp3_acc),
                                    sample_rate=self._tts._config.speech_sample_rate,
                                )
                                output_emitter.push(wav_bytes)
                                output_emitter.end_input()
                                return

                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        raise APIConnectionError(f"WebSocket error: {msg.data}")
            except asyncio.TimeoutError as e:
                raise APITimeoutError("WebSocket receive timeout") from e
            except APIStatusError:
                raise
            except Exception as e:
                raise APIConnectionError(f"TTS WebSocket session failed: {e}") from e

        try:
            try:
                ws = await session.ws_connect(ws_url, headers=headers)
            except Exception as e:
                raise APIConnectionError(f"WebSocket connection failed: {e}") from e

            send_task = asyncio.create_task(_send_task())
            recv_task = asyncio.create_task(_recv_task())

            try:
                await asyncio.gather(send_task, recv_task)
            finally:
                if not send_task.done():
                    send_task.cancel()
                if not recv_task.done():
                    recv_task.cancel()
        finally:
            if ws is not None and not ws.closed:
                await ws.close()


class SarvamFixedTTS(tts.TTS):
    """
    Sarvam TTS adapter (bulbul:v3 + simran by default) that outputs PCM so
    LiveKit can decode reliably.
    """

    def __init__(
        self,
        *,
        target_language_code: str | None = None,
        speaker: str | None = None,
        model: str | None = None,
        pace: float = 1.0,
        speech_sample_rate: int = 22050,
        enable_preprocessing: bool = False,
        pitch: float = 0.0,
        loudness: float = 1.0,
        send_completion_event: bool = True,
        api_key: str | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("SARVAM_API_KEY")
        if not self._api_key:
            raise ValueError("Sarvam API key is required. Set SARVAM_API_KEY env var.")

        cfg = SarvamTTSConfig(
            target_language_code=target_language_code or os.getenv("SARVAM_TTS_LANG", "hi-IN"),
            speaker=speaker or os.getenv("SARVAM_TTS_SPEAKER", "simran"),
            model=model or os.getenv("SARVAM_TTS_MODEL", "bulbul:v3"),
            pace=float(os.getenv("SARVAM_TTS_PACE", pace)),
            speech_sample_rate=speech_sample_rate,
            enable_preprocessing=enable_preprocessing,
            pitch=float(pitch),
            loudness=float(loudness),
            # bulbul:v3 seems to return MP3; we keep pitch/loudness optional.
            include_pitch_loudness=(model or os.getenv("SARVAM_TTS_MODEL", "bulbul:v3")) == "bulbul:v2",
            send_completion_event=send_completion_event,
        )
        self._config = cfg
        logger.info(
            "SarvamFixedTTS init: model=%s speaker=%s lang=%s pace=%s sample_rate=%s",
            cfg.model,
            cfg.speaker,
            cfg.target_language_code,
            cfg.pace,
            cfg.speech_sample_rate,
        )

        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=cfg.speech_sample_rate,
            num_channels=1,
        )

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def provider(self) -> str:
        return "Sarvam"

    def stream(
        self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> SarvamFixedSynthesizeStream:
        return SarvamFixedSynthesizeStream(tts_instance=self, conn_options=conn_options)  # type: ignore[arg-type]

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        # Implement synthesize() by using streaming and letting the base helper
        # convert it into a chunked stream.
        return self._synthesize_with_stream(text, conn_options=conn_options)

