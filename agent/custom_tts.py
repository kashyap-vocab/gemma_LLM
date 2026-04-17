"""
Custom Match TTS implementation for LiveKit agents.
Integrates with HTTP-based Match TTS API.
"""

import uuid
import aiohttp
import io
import wave
import time
import asyncio
from pathlib import Path
from urllib.parse import urljoin
from livekit.agents import tts
from livekit.agents.tts import AudioEmitter


class MatchTTSPlugin(tts.TTS):
    """
    LiveKit-compatible TTS plugin for Match TTS HTTP API.
    """

    def __init__(self, api_url: str, sample_rate: int = 22050):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._api_url = api_url
    # Note: we create a short-lived ClientSession per call in _async_synthesize
    # to keep shutdown/cleanup simple in this environment.
    pass

    def synthesize(self, text: str, *, conn_options=None):
        """
        Returns a ChunkedStream for the given text.
        Matches LiveKit's streaming interface.
        """
        return _MatchTTSChunkedStream(
            tts_plugin=self,
            input_text=text,
            conn_options=conn_options,
        )

    # no __del__ cleanup needed with per-call sessions

    async def _async_synthesize(self, text: str) -> bytes:
        """
        Call the Match TTS HTTP API and return raw int16 PCM bytes.
        """
        # Helper: read bundled fallback WAV (PCM) and return raw frames
        def _read_fallback_wav() -> bytes:
            try:
                fallback = Path(__file__).parent / "manisha_tts_audio.wav"
                with wave.open(str(fallback), "rb") as wf:
                    frames = wf.readframes(wf.getnframes())
                    return frames
            except Exception as exc:
                # Keep the helper for possible future use, but don't fall back.
                # Log the failure and return empty bytes. The caller will
                # no longer use this as a fallback path.
                print("[MATCH TTS FALLBACK ERROR] failed to load fallback wav:", exc)
                return b""

        start = time.time()

        attempts = 2
        backoff = 0.5
        for attempt in range(1, attempts + 1):
            try:
                # Use a short-lived session per call to keep the code simple and
                # avoid unclosed session warnings during tests.
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self._api_url,
                        json={"text": text},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status != 200:
                            raise RuntimeError(f"MatchTTS API failed: {resp.status}")

                        ctype = resp.headers.get("Content-Type", "")
                        body = await resp.read()

                elapsed = time.time() - start
                print(
                    f"🔊 [MATCH TTS API] POST {self._api_url} -> {len(body)} bytes in {elapsed:.3f}s (attempt {attempt})"
                )

                # If the API returned JSON with an audio_path, fetch that file
                if (isinstance(ctype, str) and ctype.startswith("application/json")):
                    try:
                        import json

                        parsed = json.loads(body.decode())
                        audio_path = parsed.get("audio_path") or parsed.get("audio_url")
                        if audio_path:
                            # Try multiple candidate URLs for the audio asset in case
                            # the server returns a filesystem path that isn't directly
                            # served at the same path.
                            from os.path import basename

                            fname = basename(audio_path)
                            candidates = [
                                audio_path,
                                "/" + audio_path,
                                "static/" + audio_path,
                                "static/" + fname,
                                "api_outputs/" + fname,
                                "outputs/" + fname,
                                "files/" + fname,
                                "download/" + fname,
                            ]

                            audio_bytes = None
                            last_exc = None
                            for cand in candidates:
                                audio_url = urljoin(self._api_url, cand)
                                try:
                                    async with aiohttp.ClientSession() as session:
                                        async with session.get(audio_url, timeout=aiohttp.ClientTimeout(total=10)) as aresp:
                                            if aresp.status == 200:
                                                audio_bytes = await aresp.read()
                                                break
                                            else:
                                                last_exc = RuntimeError(f"{aresp.status}")
                                except Exception as exc:
                                    last_exc = exc

                            if audio_bytes is None:
                                raise RuntimeError(f"Failed to fetch audio asset: {last_exc}")

                            # If WAV container, extract frames
                            if audio_bytes[:4] == b"RIFF":
                                with io.BytesIO(audio_bytes) as bio:
                                    with wave.open(bio, "rb") as wf:
                                        return wf.readframes(wf.getnframes())
                            return audio_bytes
                        else:
                            # No audio asset returned by the TTS API. Do not fall back
                            # to a bundled greeting; surface an error instead so the
                            # caller can decide how to handle it.
                            raise RuntimeError("MatchTTS JSON response had no audio_path")
                    except Exception as exc:
                        # Propagate parsing/handling errors instead of falling back.
                        print("[MATCH TTS ERROR] failed to handle JSON response:", exc)
                        raise RuntimeError(f"Failed to handle MatchTTS JSON response: {exc}")

                # If the response itself is a WAV file, strip header and return raw frames
                if body[:4] == b"RIFF" or (isinstance(ctype, str) and ctype.startswith("audio/")):
                    try:
                        with io.BytesIO(body) as bio:
                            with wave.open(bio, "rb") as wf:
                                return wf.readframes(wf.getnframes())
                    except Exception as exc:
                        # If WAV parsing fails, return raw bytes (not a bundled fallback).
                        print("[MATCH TTS WARNING] Failed to parse WAV response, returning raw bytes:", exc)
                        return body

                # Otherwise return the raw bytes and hope it's PCM
                return body

            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[MATCH TTS ERROR] attempt {attempt} failed: {e}")
                if attempt < attempts:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                # Do NOT fall back to bundled audio. Raise a clear error so the
                # caller can decide how to handle TTS unavailability.
                raise RuntimeError(f"MatchTTS failed after {attempts} attempts: {e}")


class _MatchTTSChunkedStream(tts.ChunkedStream):
    """
    Internal ChunkedStream implementation for Match TTS.
    Bridges async HTTP API to LiveKit streaming interface.
    """

    def __init__(self, *, tts_plugin: MatchTTSPlugin, input_text: str, conn_options=None):
        super().__init__(tts=tts_plugin, input_text=input_text, conn_options=conn_options)
        self._tts_obj = tts_plugin

    async def _run(self, emitter: AudioEmitter):
        import time

        req_id = str(uuid.uuid4())

        emitter.initialize(
            request_id=req_id,
            sample_rate=self._tts_obj.sample_rate,
            num_channels=self._tts_obj.num_channels,
            mime_type="audio/pcm",
        )

        start = time.time()

        audio_bytes = await self._tts_obj._async_synthesize(self._input_text)

        api_time = time.time() - start

        emitter.push(audio_bytes)

        total_time = time.time() - start

        print(
            f"🎙️ [MATCH STREAM] Text='{self._input_text}...' -> "
            f"API:{api_time:.3f}s Total:{total_time:.3f}s "
            f"({len(audio_bytes)} bytes)"
        )