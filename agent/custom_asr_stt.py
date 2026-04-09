from __future__ import annotations

import asyncio
import audioop
import json
import logging
import os
from dataclasses import dataclass

import aiohttp

from livekit import rtc
from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    NotGivenOr,
    stt,
    utils,
)
from livekit.agents.utils import AudioBuffer

logger = logging.getLogger(__name__)


@dataclass
class CustomASROptions:
    base_url: str
    language: str
    model: str | None
    sample_rate: int
    chunk_size: int
    input_audio_codec: str = "audio/wav"


class CustomASRSTT(stt.STT):
    def __init__(
        self,
        *,
        base_url: str,
        language: str = "hi-IN",
        model: str | None = None,
        sample_rate: int = 16000,
        chunk_size: int = 8000,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True,
                interim_results=True,
                aligned_transcript=False,
            )
        )
        self._opts = CustomASROptions(
            base_url=base_url,
            language=language,
            model=model,
            sample_rate=sample_rate,
            chunk_size=chunk_size,
        )
        self._session = http_session

    @property
    def model(self) -> str:
        return self._opts.model or "streaming-websocket-asr"

    @property
    def provider(self) -> str:
        return "CustomASR"

    def _ensure_session(self) -> aiohttp.ClientSession:
        if not self._session:
            self._session = utils.http_context.http_session()
        return self._session

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        frame = rtc.combine_audio_frames(buffer)
        stream = self.stream(language=language, conn_options=conn_options)
        stream.push_frame(frame)
        stream.end_input()

        async for ev in stream:
            if ev.type == stt.SpeechEventType.FINAL_TRANSCRIPT and ev.alternatives:
                await stream.aclose()
                return ev

        await stream.aclose()
        return stt.SpeechEvent(type=stt.SpeechEventType.FINAL_TRANSCRIPT, alternatives=[])

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "CustomASRSpeechStream":
        lang = str(language) if isinstance(language, str) else self._opts.language
        return CustomASRSpeechStream(
            stt=self,
            conn_options=conn_options,
            opts=CustomASROptions(
                base_url=self._opts.base_url,
                language=lang,
                model=self._opts.model,
                sample_rate=self._opts.sample_rate,
                chunk_size=self._opts.chunk_size,
                input_audio_codec=self._opts.input_audio_codec,
            ),
            http_session=self._ensure_session(),
        )


class CustomASRSpeechStream(stt.SpeechStream):
    def __init__(
        self,
        *,
        stt: CustomASRSTT,
        conn_options: APIConnectOptions,
        opts: CustomASROptions,
        http_session: aiohttp.ClientSession,
    ) -> None:
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=opts.sample_rate)
        self._opts = opts
        self._session = http_session
        self._chunk_size_samples = max(int(opts.chunk_size), 1)
        self._resample_state = None
        self._input_frame_counter = 0
        self._audio_stats_every = max(int(os.getenv("ASR_AUDIO_STATS_EVERY", "50")), 0)
        self._fixed_input_gain = float(os.getenv("ASR_INPUT_GAIN", "1.0"))
        self._target_rms = max(int(os.getenv("ASR_TARGET_RMS", "2200")), 0)
        self._max_agc_gain = max(float(os.getenv("ASR_MAX_GAIN", "12.0")), 1.0)
        self._noise_gate_rms = max(int(os.getenv("ASR_NOISE_GATE_RMS", "30")), 0)
        self._agc_min_rms = max(int(os.getenv("ASR_AGC_MIN_RMS", "120")), 0)
        self._log_audio_transform = os.getenv("ASR_LOG_TRANSFORMS", "1") != "0"
        self._limiter_peak = max(int(os.getenv("ASR_LIMITER_PEAK", "28000")), 1)

    def _ws_url(self) -> str:
        """Build WebSocket URL - use base_url directly without parameters."""
        return self._opts.base_url

    async def _run(self) -> None:
        headers = {"User-Agent": "LiveKit-Custom-ASR/1.0"}

        ws: aiohttp.ClientWebSocketResponse | None = None
        try:
            logger.info(f"Connecting to ASR endpoint: {self._ws_url()}")
            try:
                ws = await self._session.ws_connect(self._ws_url(), headers=headers, timeout=aiohttp.ClientTimeout(total=30))
                logger.info("WebSocket connected successfully")
            except asyncio.TimeoutError as e:
                logger.error(f"ASR WebSocket connect timeout: {e}")
                raise APITimeoutError("ASR WebSocket connect timeout") from e
            except Exception as e:
                logger.error(f"ASR WebSocket connect failed: {e}")
                raise APIConnectionError(f"ASR WebSocket connect failed: {e}") from e

            sender = asyncio.create_task(self._send_audio(ws), name="custom-asr-send")
            receiver = asyncio.create_task(self._recv_messages(ws), name="custom-asr-recv")

            done, pending = await asyncio.wait(
                [sender, receiver],
                return_when=asyncio.FIRST_EXCEPTION,
            )
            
            for task in done:
                exc = task.exception()
                if exc:
                    logger.error(f"Task failed: {exc}")
                    raise exc
                    
            for task in pending:
                task.cancel()
            await utils.aio.cancel_and_wait(*pending)
        except Exception as e:
            logger.error(f"ASR stream error: {e}")
            raise
        finally:
            if ws and not ws.closed:
                await ws.close()
                logger.info("WebSocket closed")

    def _prepare_audio_frame(self, frame: rtc.AudioFrame) -> bytes:
        audio_bytes = frame.data.tobytes()
        channels = int(getattr(frame, "num_channels", 1) or 1)
        input_sample_rate = int(getattr(frame, "sample_rate", self._opts.sample_rate) or self._opts.sample_rate)

        if input_sample_rate != self._opts.sample_rate:
            if self._log_audio_transform:
                logger.info(
                    "ASR transform: %s -> %s (%s channel(s))",
                    input_sample_rate,
                    self._opts.sample_rate,
                    channels,
                )
            audio_bytes, self._resample_state = audioop.ratecv(
                audio_bytes,
                2,
                channels,
                input_sample_rate,
                self._opts.sample_rate,
                self._resample_state,
            )

        in_rms = audioop.rms(audio_bytes, 2)
        in_peak = audioop.max(audio_bytes, 2)

        # Do not amplify comfort noise / near-silence from PSTN packets.
        if self._noise_gate_rms > 0 and in_rms < self._noise_gate_rms:
            audio_bytes = b"\x00" * len(audio_bytes)
            in_rms = 0
            in_peak = 0

        total_gain = max(self._fixed_input_gain, 0.0)

        if self._target_rms > 0 and in_rms >= self._agc_min_rms:
            agc_gain = min(self._max_agc_gain, self._target_rms / float(in_rms))
            total_gain *= agc_gain

        # Clip-safe gain ceiling based on current peak; avoids saturating at int16 max.
        if in_peak > 0:
            clip_safe_gain = self._limiter_peak / float(in_peak)
            total_gain = min(total_gain, clip_safe_gain)

        if total_gain > 0 and abs(total_gain - 1.0) > 1e-3:
            audio_bytes = audioop.mul(audio_bytes, 2, total_gain)

        out_peak = audioop.max(audio_bytes, 2)
        if out_peak > self._limiter_peak:
            limiter_gain = self._limiter_peak / float(out_peak)
            audio_bytes = audioop.mul(audio_bytes, 2, limiter_gain)

        self._input_frame_counter += 1
        if self._audio_stats_every > 0 and self._input_frame_counter % self._audio_stats_every == 0:
            rms = audioop.rms(audio_bytes, 2)
            peak = audioop.max(audio_bytes, 2)
            logger.info(
                "ASR input stats: frame=%s in_sr=%s out_sr=%s gain=%.2f target_rms=%s rms_in=%s peak_in=%s rms=%s peak=%s chunk=%s",
                self._input_frame_counter,
                input_sample_rate,
                self._opts.sample_rate,
                total_gain,
                self._target_rms,
                in_rms,
                in_peak,
                rms,
                peak,
                self._chunk_size_samples,
            )

        return audio_bytes

    async def _send_audio(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Send PCM audio chunks as raw WebSocket binary frames."""
        frames_sent = 0
        bytes_sent = 0
        pending = bytearray()
        try:
            async for frame in self._input_ch:
                if isinstance(frame, rtc.AudioFrame):
                    audio_bytes = self._prepare_audio_frame(frame)
                    pending.extend(audio_bytes)

                    bytes_per_sample = 2  # int16 PCM
                    chunk_size_bytes = self._chunk_size_samples * bytes_per_sample
                    while len(pending) >= chunk_size_bytes:
                        await ws.send_bytes(bytes(pending[:chunk_size_bytes]))
                        del pending[:chunk_size_bytes]

                    frames_sent += 1
                    bytes_sent += len(audio_bytes)
                    if frames_sent % 10 == 0:
                        logger.debug(f"Sent {frames_sent} audio frames ({bytes_sent} bytes)")
                elif isinstance(frame, self._FlushSentinel):
                    if pending:
                        await ws.send_bytes(bytes(pending))
                        pending.clear()
                    logger.info(f"Audio stream complete: {frames_sent} frames ({bytes_sent} bytes)")
                    return
        except Exception as e:
            logger.error(f"Error in _send_audio: {e}")
            raise

    async def _recv_messages(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        msg_count = 0
        try:
            async for msg in ws:
                msg_count += 1
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        # Try to parse as JSON first
                        data = json.loads(msg.data)
                        logger.debug(f"Received JSON response: {data}")
                        if isinstance(data, dict):
                            await self._handle_message(data)
                        elif isinstance(data, str):
                            transcript = data.strip()
                            if transcript:
                                self._event_ch.send_nowait(
                                    stt.SpeechEvent(
                                        type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                                        alternatives=[
                                            stt.SpeechData(
                                                language=self._opts.language,
                                                text=transcript,
                                            )
                                        ],
                                    )
                                )
                        else:
                            # Some ASR servers emit numeric heartbeat/control payloads (e.g. 20).
                            logger.debug(f"Ignoring non-dict ASR JSON payload: {data!r}")
                    except json.JSONDecodeError:
                        # If not JSON, treat as plain text transcript
                        transcript = msg.data.strip()
                        logger.info(f"Received transcript: {transcript}")
                        if transcript:
                            self._event_ch.send_nowait(
                                stt.SpeechEvent(
                                    type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                                    alternatives=[
                                        stt.SpeechData(
                                            language=self._opts.language,
                                            text=transcript,
                                        )
                                    ],
                                )
                            )
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    # Handle binary data as plain text transcript
                    try:
                        transcript = msg.data.decode("utf-8").strip()
                        logger.info(f"Received binary transcript: {transcript}")
                        if transcript:
                            self._event_ch.send_nowait(
                                stt.SpeechEvent(
                                    type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                                    alternatives=[
                                        stt.SpeechData(
                                            language=self._opts.language,
                                            text=transcript,
                                        )
                                    ],
                                )
                            )
                    except Exception as e:
                        logger.warning(f"Could not decode binary message: {e}")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    error = f"ASR WebSocket error: {ws.exception()}"
                    logger.error(error)
                    raise APIConnectionError(error)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    logger.info(f"WebSocket closed after {msg_count} messages")
                    return
        except Exception as e:
            logger.error(f"Error in _recv_messages: {e}")
            raise

    async def _handle_message(self, data: dict) -> None:
        """Handle ASR response messages."""
        if not isinstance(data, dict):
            logger.debug(f"Ignoring ASR payload with unexpected type: {type(data).__name__}")
            return

        # Handle error messages
        if data.get("type") == "error" or "error" in data:
            err_msg = data.get("error") or data.get("message") or json.dumps(data)
            raise APIStatusError(message=f"ASR error: {err_msg}", status_code=500, body=data)

        # Event can be 'partial', 'final', 'ready', etc.
        event = data.get("event", "").lower()
        
        # Skip ready and other non-transcript events
        if event in ("ready", ""):
            return
            
        # Extract transcript from 'text' field
        transcript = (data.get("text") or data.get("transcript") or "").strip()
        
        if not transcript:
            return

        event_type = stt.SpeechEventType.INTERIM_TRANSCRIPT if event == "partial" else stt.SpeechEventType.FINAL_TRANSCRIPT

        self._event_ch.send_nowait(
            stt.SpeechEvent(
                type=event_type,
                request_id=str(data.get("request_id", "")),
                alternatives=[
                    stt.SpeechData(
                        language=self._opts.language,
                        text=transcript,
                        start_time=float(data.get("speech_start", 0.0) or 0.0),
                        end_time=float(data.get("speech_end", 0.0) or 0.0),
                    )
                ],
            )
        )
        logger.debug(f"Sent transcript: {transcript[:100]}")