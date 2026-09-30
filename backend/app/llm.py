"""Thin LLM client over the OpenAI-compatible chat-completions API.

Anthropic, Groq, Gemini and OpenAI all expose this format, so the provider is a config choice
(LLM_BASE_URL / LLM_MODEL / LLM_API_KEY), not a code change. The key lives only here, server-side.
"""
import logging
from typing import Protocol

import httpx

from app.config import get_settings

log = logging.getLogger("healtrip.llm")


class LLMUnavailable(Exception):
    """Raised when the model can't be reached after a retry. Callers must degrade safely."""


class LLM(Protocol):
    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             tool_choice: str | dict | None = None) -> dict: ...


class OpenAICompatLLM:
    def __init__(self) -> None:
        s = get_settings()
        self.url = s.llm_base_url.rstrip("/") + "/chat/completions"
        self.model = s.llm_model
        self.headers = {"Authorization": f"Bearer {s.llm_api_key}"}
        self.timeout = s.llm_timeout_s
        self.disable_thinking = s.llm_disable_thinking
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}  # cost visibility per turn

    def chat(self, messages, tools=None, tool_choice=None) -> dict:
        body = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": 600}
        if self.disable_thinking:
            body["thinking"] = {"type": "disabled"}
        if tools:
            body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
        for attempt in (1, 2):  # one retry, then give up and let the caller degrade
            try:
                r = httpx.post(self.url, json=body, headers=self.headers, timeout=self.timeout)
                r.raise_for_status()
                data = r.json()
                u = data.get("usage") or {}
                self.usage["calls"] += 1
                self.usage["prompt_tokens"] += u.get("prompt_tokens", 0)
                self.usage["completion_tokens"] += u.get("completion_tokens", 0)
                return data["choices"][0]["message"]
            except (httpx.HTTPError, KeyError, IndexError) as e:
                log.warning("llm_call_failed attempt=%s error=%s", attempt, type(e).__name__)
        raise LLMUnavailable()
