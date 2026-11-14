"""OpenRouter Chat Completions adapter (Week 11).

The API key is read from OPENROUTER_API_KEY; it is never printed, logged,
or committed. Loads a local .env file via python-dotenv without overriding existing environment variables.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "nvidia/nemotron-3.5-super-vl:free"


class LLMError(RuntimeError):
    """OpenRouter request or response failure."""


class OpenRouterClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 45.0,
    ) -> None:
        # The local .env stays on the user's machine; .gitignore excludes it.
        load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env", override=False)
        self.api_key = api_key if api_key is not None else os.getenv("OPENROUTER_API_KEY", "")
        self.model = model if model is not None else os.getenv("LLM_MODEL", DEFAULT_MODEL)
        self.timeout = timeout
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise LLMError("OPENROUTER_API_KEY is missing; set it locally, never in GitHub")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("LLM_MODEL must be nonempty")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages must be a nonempty list")
        allowed_roles = {"system", "user", "assistant", "tool"}
        for message in messages:
            if not isinstance(message, dict) or message.get("role") not in allowed_roles:
                raise ValueError("invalid message role")
            if "content" not in message:
                raise ValueError("each message requires content")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            # Reasoning-capable models can exhaust small completion budgets
            # before producing a final answer.
            "max_tokens": 2048,
            "reasoning": {"enabled": False},
            "temperature": 0,
        }
        if tools is not None:
            if not isinstance(tools, list):
                raise ValueError("tools must be a list")
            payload["tools"] = tools
        request = Request(
            ENDPOINT,
            method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read(2_000_001)
                if len(body) > 2_000_000:
                    raise LLMError("OpenRouter response exceeded size limit")
        except HTTPError as exc:
            # Do not include request headers or provider response body in errors.
            raise LLMError(f"OpenRouter HTTP {exc.code}") from None
        except (URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"OpenRouter connection failed: {type(exc).__name__}") from None

        try:
            result = json.loads(body.decode("utf-8"))
        except (UnicodeError, ValueError):
            raise LLMError("OpenRouter response body is not valid JSON") from None

        if not isinstance(result, dict):
            raise LLMError("OpenRouter response JSON was not an object")
        if result.get("error"):
            # Do not log the provider-supplied message: it can contain user text.
            error = result["error"]
            code = error.get("code", "unknown") if isinstance(error, dict) else "unknown"
            code = code if isinstance(code, int) else "unknown"
            raise LLMError(f"OpenRouter returned a provider error (code={code})")

        choices = result.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMError("OpenRouter returned no choices (HTTP 200)")
        choice = choices[0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise LLMError("OpenRouter choice is missing a message")
        msg = choice["message"]
        content = msg.get("content")
        if isinstance(content, list):
            # Some providers represent text as structured content parts.
            parts = []
            for part in content:
                if not isinstance(part, dict) or not isinstance(part.get("text"), str):
                    raise LLMError("OpenRouter returned unsupported content parts")
                parts.append(part["text"])
            content = "".join(parts)
        if content is not None and not isinstance(content, str):
            raise LLMError("OpenRouter message content was not text")

        raw_calls = msg.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise LLMError("OpenRouter tool_calls was not a list")
        tool_calls = []
        try:
            for call in raw_calls:
                fn = call["function"]
                tool_calls.append({
                    "id": call.get("id"),
                    "name": fn["name"],
                    "arguments": fn.get("arguments", "{}"),
                })
        except (KeyError, TypeError, AttributeError):
            raise LLMError("OpenRouter tool call had invalid structure") from None

        if not content and not tool_calls:
            finish_reason = choice.get("finish_reason")
            finish_reason = finish_reason if finish_reason in {
                "stop", "length", "content_filter", "tool_calls"
            } else "unknown"
            usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
            details = usage.get("completion_tokens_details")
            details = details if isinstance(details, dict) else {}
            token_info = []
            for label, value in [
                ("completion_tokens", usage.get("completion_tokens")),
                ("reasoning_tokens", details.get("reasoning_tokens")),
            ]:
                if isinstance(value, int) and value >= 0:
                    token_info.append(f"{label}={value}")
            suffix = ", ".join(token_info)
            raise LLMError(
                f"OpenRouter returned empty completion (finish_reason={finish_reason}"
                + (f", {suffix}" if suffix else "") + ")"
            )
        return {
            "text": content or "",
            "toolCalls": tool_calls,
            "model": result.get("model", self.model),
            "request_id": result.get("id"),
            "usage": result.get("usage"),
        }
