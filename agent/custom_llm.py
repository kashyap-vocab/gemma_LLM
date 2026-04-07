"""
Local Gemma-2 LLM plugin for LiveKit Agents.

Wraps the HTTP API exposed by the local Gemma-2 server
(POST /chat  →  {"role": "assistant", "content": "..."}).
"""

from __future__ import annotations

import httpx
from livekit.agents import llm
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN

LOCAL_LLM_URL = "http://192.168.30.239:6000"


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

    @property
    def model(self) -> str:
        return "gemma-2-9b-it"

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

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._llm._base_url}/chat",
                json={
                    "messages": messages,
                    "max_new_tokens": self._llm._max_new_tokens,
                    "temperature": self._llm._temperature,
                    "stream": False,
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            content = resp.json().get("content", "")

        self._event_ch.send_nowait(
            llm.ChatChunk(
                id="local-gemma",
                delta=llm.ChoiceDelta(role="assistant", content=content),
            )
        )
