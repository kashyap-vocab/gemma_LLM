"""
Local Gemma-2 LLM plugin for LiveKit Agents.

Wraps the HTTP API exposed by the local Gemma-2 server
(POST /chat  →  {"role": "assistant", "content": "..."}).
"""

from __future__ import annotations

import json
import logging
import os
import re

import httpx
from livekit.agents import llm
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN

LOCAL_LLM_URL = os.getenv("LOCAL_LLM_URL", "http://192.168.30.239:9000")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "google/gemma-2-9b-it")

logger = logging.getLogger("local-gemma-llm")

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _merge_system_into_user(messages: list[dict]) -> list[dict]:
    """
    Gemma-2 chat template hard requirements (raises 400 otherwise):
      - No 'system' role at all.
      - Must strictly alternate: user, assistant, user, assistant, ...
      - Must start with 'user'.

    Strategy:
      1. Fold every system message into the next user message's content
         (prepend, separated by blank line).  Trailing system content that
         has no following user turn is appended to the last user turn found,
         or added as a new user turn of last resort.
      2. Merge any consecutive same-role messages (can appear after step 1
         e.g. two user turns in a row).
      3. If the sequence still starts with 'assistant' (agent sent the
         greeting before the customer spoke), insert a minimal placeholder
         user turn so the template is satisfied.
    """
    # ── Step 1: fold system messages into the next user turn ─────────────
    out: list[dict] = []
    pending_system: list[str] = []

    for msg in messages:
        role = msg["role"]  # may be str or StrEnum — == comparisons work either way
        content = str(msg.get("content") or "")

        if role == "system":
            pending_system.append(content)
        elif role == "user":
            if pending_system:
                content = "\n\n".join(pending_system) + "\n\n" + content
                pending_system = []
            out.append({"role": "user", "content": content})
        else:  # assistant / model
            out.append({"role": "assistant", "content": content})

    # Flush any trailing system content into the last user turn (or new turn)
    if pending_system:
        system_text = "\n\n".join(pending_system)
        for i in reversed(range(len(out))):
            if out[i]["role"] == "user":
                out[i] = {"role": "user", "content": out[i]["content"] + "\n\n" + system_text}
                break
        else:
            out.append({"role": "user", "content": system_text})

    # ── Step 2: merge consecutive same-role messages ──────────────────────
    merged: list[dict] = []
    for msg in out:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1] = {"role": msg["role"],
                          "content": merged[-1]["content"] + "\n\n" + msg["content"]}
        else:
            merged.append(dict(msg))

    # ── Step 3: ensure conversation starts with 'user' ────────────────────
    if merged and merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": "[call started]"})

    return merged

# Phrases that indicate the agent is closing the call. We scan the spoken
# text (response_text or raw fallback) for any of these and force the
# hangup signal — this is the intent-based safety net for when the local
# LLM forgets to set continue_conversation=false in the JSON.
_END_CALL_PHRASES = (
    "धन्यवाद",
    "धन्यबाद",
    "दिन शुभ हो",
    "शुभ दिन",
    "आपका दिन",
    "अलविदा",
    "नमस्ते जी",
    "फ़ीडबैक और समय",
    "फीडबैक और समय",
    "बात करूंगी",       # callback rescheduling
    "बाद में कॉल",
    "बाद में बात",
    "फिर कॉल",
    "फिर बात",
)


def _is_closing_intent(text: str) -> bool:
    if not text:
        return False
    lowered = text.strip()
    return any(phrase in lowered for phrase in _END_CALL_PHRASES)


def _parse_llm_response(content: str) -> tuple[str, bool]:
    """
    Parse an LLM response that should look like:
        {"response_text": "...", "continue_conversation": true|false}

    Returns (response_text, continue_conversation). Falls back to the raw
    content with continue_conversation=True if parsing fails so a malformed
    turn never silently hangs up the call.
    """
    raw = (content or "").strip()
    if not raw:
        return "", True

    # Strip optional ```json ... ``` fences the model may emit.
    stripped = _FENCE_RE.sub("", raw).strip()

    # Try to find the first JSON object in the string.
    candidates = [stripped]
    brace_start = stripped.find("{")
    brace_end = stripped.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        candidates.append(stripped[brace_start : brace_end + 1])

    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except Exception:
            continue
        if isinstance(obj, dict) and "response_text" in obj:
            text = str(obj.get("response_text") or "").strip()
            cont_val = obj.get("continue_conversation", True)
            if isinstance(cont_val, str):
                cont = cont_val.strip().lower() not in ("false", "0", "no")
            else:
                cont = bool(cont_val)
            # Intent-based safety net: even if the model said continue=true,
            # if the spoken line is clearly a closing one, end the call.
            if cont and _is_closing_intent(text):
                logger.info("Closing intent detected in response_text; forcing continue_conversation=false")
                cont = False
            return text, cont

    logger.warning("LLM response was not valid JSON; forwarding raw text. Snippet: %s", raw[:200])
    # Pure intent-based fallback: if JSON parsing failed but the raw text
    # contains a closing phrase, still hang up cleanly.
    cont = not _is_closing_intent(raw)
    if not cont:
        logger.info("Closing intent detected in raw (non-JSON) response; forcing hangup")
    return raw, cont


class LocalGemmaLLM(llm.LLM):
    def __init__(
        self,
        base_url: str = LOCAL_LLM_URL,
        temperature: float = 0.1,
        max_new_tokens: int = 1024,
    ):
        super().__init__()
        self._base_url = base_url.rstrip("/")
        self._temperature = temperature
        self._max_new_tokens = max_new_tokens
        # Set by web_rtc_server per-call. Invoked (sync, no args) when the LLM
        # emits continue_conversation=false. Used to trigger call termination
        # without depending on tool-calling support in the local model.
        self.on_end_conversation = None
        # Set by web_rtc_server per-call. A zero-arg callable that returns
        # a string to inject as a fresh system message before every LLM
        # request — used to feed Gemma a live "📋 LIVE SURVEY STATE" slot
        # checklist so it stops re-asking already-answered questions.
        self.get_dynamic_context = None

    @property
    def model(self) -> str:
        return LOCAL_LLM_MODEL

    @property
    def provider(self) -> str:
        return "local"

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools=None,
        conn_options=DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls=NOT_GIVEN,
        tool_choice=NOT_GIVEN,
        extra_kwargs=NOT_GIVEN,
    ) -> "LocalGemmaLLMStream":
        return LocalGemmaLLMStream(
            self,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=conn_options,
        )


class LocalGemmaLLMStream(llm.LLMStream):
    async def _run(self) -> None:
        messages = []
        for item in self._chat_ctx.items:
            if not hasattr(item, "role"):
                continue
            text = getattr(item, "text_content", None)
            if not text:
                continue
            role = item.role
            if role == "developer":
                role = "system"
            messages.append({"role": role, "content": text})

        # Inject the live slot tracker as the FINAL system message so it
        # is the most recent thing the model sees and can never be missed
        # in a long conversation history.
        get_ctx = getattr(self._llm, "get_dynamic_context", None)
        if callable(get_ctx):
            try:
                extra = get_ctx()
                if extra:
                    messages.append({"role": "system", "content": extra})
                    logger.info("📋 Injected live slot state into LLM context:\n%s", extra)
            except Exception as exc:
                logger.error("get_dynamic_context failed: %s", exc)

        # Gemma-2 chat template rejects system role — fold into user turns
        vllm_messages = _merge_system_into_user(messages)
        logger.info("📤 vLLM request messages (%d turns): %s",
                    len(vllm_messages),
                    [(m["role"], m["content"][:80]) for m in vllm_messages])

        async with httpx.AsyncClient() as client:
            # --- vLLM OpenAI-compatible endpoint ---
            resp = await client.post(
                f"{self._llm._base_url}/v1/chat/completions",
                json={
                    "model": LOCAL_LLM_MODEL,
                    "messages": vllm_messages,
                    "max_tokens": self._llm._max_new_tokens,
                    "temperature": self._llm._temperature,
                    "stream": False,
                },
                timeout=60.0,
            )
            if resp.status_code >= 400:
                logger.error("vLLM %s body: %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]

            # --- Old custom /chat endpoint (plain Python server) ---
            # resp = await client.post(
            #     f"{self._llm._base_url}/chat",
            #     json={
            #         "messages": messages,
            #         "max_new_tokens": self._llm._max_new_tokens,
            #         "temperature": self._llm._temperature,
            #         "stream": False,
            #     },
            #     timeout=60.0,
            # )
            # resp.raise_for_status()
            # content = resp.json().get("content", "")

        logger.info("🤖 LLM raw response: %s", content)
        response_text, continue_conversation = _parse_llm_response(content)
        logger.info(
            "🔊 → TTS: %r | continue_conversation=%s",
            response_text,
            continue_conversation,
        )

        self._event_ch.send_nowait(
            llm.ChatChunk(
                id="local-gemma",
                delta=llm.ChoiceDelta(role="assistant", content=response_text),
            )
        )

        if not continue_conversation:
            cb = getattr(self._llm, "on_end_conversation", None)
            if cb is not None:
                try:
                    cb()
                except Exception as exc:
                    logger.error("on_end_conversation callback failed: %s", exc)
