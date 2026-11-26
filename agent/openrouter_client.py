"""Minimal OpenRouter client: explicit free-only allowlist, no silent paid fallback.

Never logs or persists API keys or raw provider errors; model availability and
capability claims are checked live against /api/v1/models before inference.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

BASE_URL = "https://openrouter.ai/api/v1"
FREE_MODELS = (
    "nvidia/nemotron-3.5-super-vl:free",
    "google/gemma-4-26b-a4b-it:free",
)


class ModelUnavailable(RuntimeError):
    pass


class MissingKey(RuntimeError):
    pass


class ProviderError(RuntimeError):
    pass


def _numeric_zero(value: Any) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def verify_model(model: str, entries: list[dict], *, image: bool = False, tools: bool = False) -> dict:
    """Fail closed on changing offerings or an absent/paid model."""
    if model not in FREE_MODELS or not model.endswith(":free"):
        raise ModelUnavailable("Model is not in the explicitly allowed free-model set")
    meta = next((x for x in entries if x.get("id") == model), None)
    if not meta:
        raise ModelUnavailable("Exact free model ID is not currently listed")
    prices = meta.get("pricing") or {}
    if not (_numeric_zero(prices.get("prompt")) and _numeric_zero(prices.get("completion"))):
        raise ModelUnavailable("Model's current API listing does not show zero token prices")
    if image and "image" not in (meta.get("architecture") or {}).get("input_modalities", []):
        raise ModelUnavailable("Model does not advertise image input")
    if tools and not {"tools", "tool_choice"}.issubset(set(meta.get("supported_parameters") or [])):
        raise ModelUnavailable("Model does not advertise native tool calling")
    return {
        "id": model,
        "name": meta.get("name"),
        "modalities": (meta.get("architecture") or {}).get("input_modalities", []),
        "tools_declared": "tools" in (meta.get("supported_parameters") or []),
        "zero_token_price": True,
        "max_completion_tokens": (meta.get("top_provider") or {}).get("max_completion_tokens"),
    }


@dataclass(frozen=True)
class Completion:
    model: str
    message: dict
    finish_reason: str | None
    usage: dict
    cost_usd: float | None
    elapsed_seconds: float
    response_id: str | None


class OpenRouterClient:
    def __init__(self, *, api_key: str | None = None, timeout: float = 45.0):
        self._api_key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY")
        self.timeout = min(max(timeout, 1), 120)
        self._catalog: list[dict] | None = None

    def _request(self, url: str, *, payload: dict | None = None, auth: bool = False) -> dict:
        if auth and not self._api_key:
            raise MissingKey("OPENROUTER_API_KEY is not configured")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer " + self._api_key
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, headers=headers, data=body, method="POST" if body else "GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read(5_000_000)
            answer = json.loads(raw)
            if not isinstance(answer, dict):
                raise ProviderError("OpenRouter returned a non-object response")
            if answer.get("error"):
                raise ProviderError("OpenRouter returned an API error")
            return answer
        except urllib.error.HTTPError as exc:
            # Do not echo provider response: it may contain a request or user data.
            raise ProviderError(f"OpenRouter HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError(f"OpenRouter network error: {type(exc).__name__}") from None
        except (json.JSONDecodeError, ValueError):
            raise ProviderError("OpenRouter returned invalid JSON") from None

    def catalog(self, *, refresh: bool = False) -> list[dict]:
        if self._catalog is None or refresh:
            obj = self._request(BASE_URL + "/models")
            if not isinstance(obj.get("data"), list):
                raise ProviderError("OpenRouter models response has no data array")
            self._catalog = obj["data"]
        return self._catalog

    def check(self, model: str, *, image: bool = False, tools: bool = False) -> dict:
        return verify_model(model, self.catalog(), image=image, tools=tools)

    def complete(self, model: str, messages: list[dict], *, tools: list[dict] | None = None,
                 max_tokens: int = 800, image: bool = False) -> Completion:
        self.check(model, image=image, tools=bool(tools))
        # Never use implicit model selection, default paid models, or automatic paid fallbacks.
        request = {"model": model, "messages": messages, "max_tokens": max_tokens,
                   "temperature": 0, "provider": {"allow_fallbacks": False}}
        if tools:
            request.update(tools=tools, tool_choice="auto")
        begin = time.monotonic()
        obj = self._request(BASE_URL + "/chat/completions", payload=request, auth=True)
        elapsed = round(time.monotonic() - begin, 3)
        choices = obj.get("choices") or []
        if not choices or not isinstance(choices[0].get("message"), dict):
            raise ProviderError("OpenRouter completion has no model message")
        usage = obj.get("usage") or {}
        # The response field may be absent. Never substitute model-advertised $0
        # as though the API returned a per-request cost measurement.
        cost = usage.get("cost")
        try: cost = float(cost) if cost is not None else None
        except (ValueError, TypeError): cost = None
        if cost is not None and cost > 0:
            raise ProviderError("Provider reported nonzero cost; free-only policy violated")
        return Completion(model=obj.get("model") or model,
                          message=choices[0]["message"],
                          finish_reason=choices[0].get("finish_reason"),
                          usage={key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens") if key in usage},
                          cost_usd=cost, elapsed_seconds=elapsed,
                          response_id=obj.get("id"))
