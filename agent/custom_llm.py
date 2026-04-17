"""
Custom LLM plugin for LiveKit agents — Gemma 2 via vLLM.

Uses /v1/completions (raw text) instead of /v1/chat/completions so that we
build the Gemma 2 native prompt ourselves. This avoids all of vLLM's chat-
template constraints:
  - No 'system role not supported' error
  - No 'roles must alternate' error
  - No 'tool_choice auto requires flags' error

Gemma 2 prompt format
---------------------
<bos><start_of_turn>user
{content}<end_of_turn>
<start_of_turn>model
{content}<end_of_turn>
<start_of_turn>user
...
<start_of_turn>model   ← the open model turn we want the model to complete
"""

from __future__ import annotations

import json
import uuid
import logging
from typing import Any

import aiohttp
from livekit.agents import llm
from livekit.agents.llm import ChatChunk, ChoiceDelta
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectOptions,
    NotGivenOr,
)

logger = logging.getLogger(__name__)

_BOS = "<bos>"
_USER_START = "<start_of_turn>user\n"
_MODEL_START = "<start_of_turn>model\n"
_TURN_END = "<end_of_turn>\n"


def _build_gemma_prompt(messages: list[dict]) -> str:
    """
    Convert an OpenAI-style messages list to the Gemma 2 raw prompt.

    Rules:
    - 'system' and 'user' roles both map to <start_of_turn>user
    - Consecutive same-side turns are merged with a newline separator
    - The prompt ends with an open <start_of_turn>model to elicit the response
    """
    # Normalise roles: system → user
    normalised: list[tuple[str, str]] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = "\n".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
        content = content.strip()
        side = "model" if role == "assistant" else "user"
        normalised.append((side, content))

    # Merge consecutive same-side turns
    merged: list[tuple[str, str]] = []
    for side, text in normalised:
        if merged and merged[-1][0] == side:
            merged[-1] = (side, merged[-1][1] + "\n" + text)
        else:
            merged.append([side, text])

    # Build prompt string
    prompt = _BOS
    for side, text in merged:
        turn_start = _MODEL_START if side == "model" else _USER_START
        prompt += turn_start + text + _TURN_END

    # Open the model turn for completion
    prompt += _MODEL_START
    return prompt


class LocalVLLM(llm.LLM):
    """
    LiveKit LLM plugin for an in-house vLLM/Gemma 2 server.
    Uses the raw /v1/completions endpoint with the native Gemma prompt format.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 512,
    ) -> None:
        """
        Args:
            base_url:   e.g. http://192.168.30.239:9000/v1
            model:      e.g. google/gemma-2-9b-it
            temperature: sampling temperature
            max_tokens: max tokens to generate per turn
        """
        super().__init__()
        self._base_url = base_url.rstrip("/")
        self._completions_url = f"{self._base_url}/completions"
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    @property
    def model(self) -> str:
        return self._model

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[llm.ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> "_LocalVLLMStream":
        return _LocalVLLMStream(
            llm=self,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=conn_options,
        )


class _LocalVLLMStream(llm.LLMStream):

    def __init__(
        self,
        *,
        llm: LocalVLLM,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(llm, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._llm_obj = llm

    async def _run(self) -> None:
        # Convert LiveKit ChatContext → OpenAI messages → Gemma raw prompt
        raw_messages, _ = self._chat_ctx.to_provider_format("openai")
        prompt = _build_gemma_prompt(raw_messages)

        payload = {
            "model": self._llm_obj._model,
            "prompt": prompt,
            "stream": True,
            "temperature": self._llm_obj._temperature,
            "max_tokens": self._llm_obj._max_tokens,
            # Stop at the next turn boundary so the model doesn't hallucinate a user turn
            "stop": ["<end_of_turn>", "<start_of_turn>"],
        }

        request_id = str(uuid.uuid4())
        logger.debug(f"[LocalVLLM] POST {self._llm_obj._completions_url} (id={request_id})")

        timeout = aiohttp.ClientTimeout(
            total=self._conn_options.timeout,
            connect=10,
        )

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                self._llm_obj._completions_url,
                json=payload,
                headers={"Accept": "text/event-stream"},
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"[LocalVLLM] HTTP {resp.status}: {body}")

                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8").strip()

                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if not line or line == "[DONE]":
                        continue

                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning(f"[LocalVLLM] Non-JSON line: {line!r}")
                        continue

                    chunk_id = chunk.get("id", request_id)
                    for choice in chunk.get("choices", []):
                        # /v1/completions uses "text", not "delta.content"
                        text: str | None = choice.get("text")
                        if text:
                            self._event_ch.send_nowait(
                                ChatChunk(
                                    id=chunk_id,
                                    delta=ChoiceDelta(
                                        role="assistant",
                                        content=text,
                                    ),
                                )
                            )

        logger.debug(f"[LocalVLLM] Stream complete (id={request_id})")
