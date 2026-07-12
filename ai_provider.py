"""Provider-neutral structured JSON inference with safe failover.

Gemini remains the primary provider. Groq and OpenRouter are optional server-side
fallbacks so a Gemini quota or transient outage does not stop Fontify.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

import requests


DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_OPENROUTER_MODEL = "openrouter/free"


class AiProviderError(Exception):
    def __init__(self, message: str, status_code: int = 503, provider: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.provider = provider


def _clean_key(value: Any) -> str:
    key = str(value or "").strip()
    return key if 20 <= len(key) <= 512 else ""


def configured_providers(config: dict[str, Any] | None) -> list[str]:
    values = config or {}
    return [
        name for name in ("gemini", "groq", "openrouter")
        if _clean_key(values.get(f"{name}_key"))
    ]


def _openai_schema(value: Any) -> Any:
    """Convert Gemini's upper-case schema types to regular JSON Schema."""
    if isinstance(value, list):
        return [_openai_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    converted = {key: _openai_schema(item) for key, item in value.items()}
    if isinstance(converted.get("type"), str):
        converted["type"] = converted["type"].lower()
    return converted


def _safe_upstream_message(data: Any, fallback: str) -> str:
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            fallback = str(error.get("message") or fallback)
        elif isinstance(error, str):
            fallback = error
    return re.sub(r"[\r\n\x00-\x1f]+", " ", fallback)[:240]


def _parse_openai_response(response: requests.Response, provider: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise AiProviderError(f"{provider} geçersiz bir yanıt döndürdü.", 502, provider) from exc
    if not response.ok:
        message = _safe_upstream_message(data, f"{provider} isteği reddedildi.")
        status = 429 if response.status_code == 429 else 401 if response.status_code in {401, 403} else 502
        raise AiProviderError(message, status, provider)
    try:
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            )
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(content).strip(), flags=re.IGNORECASE)
        parsed = json.loads(raw)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise AiProviderError(f"{provider} JSON çıktısı doğrulanamadı.", 502, provider) from exc
    if not isinstance(parsed, dict):
        raise AiProviderError(f"{provider} JSON nesnesi döndürmedi.", 502, provider)
    return parsed


def _call_openai_compatible(
    *,
    provider: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    schema_name: str,
    max_tokens: int,
) -> dict[str, Any]:
    key = _clean_key(api_key)
    if not key:
        raise AiProviderError(f"{provider} API anahtarı geçersiz.", 401, provider)
    clean_messages = []
    for item in messages[-14:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role == "model":
            role = "assistant"
        if role not in {"system", "user", "assistant"}:
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            clean_messages.append({"role": role, "content": content[:60_000]})
    if not clean_messages:
        raise AiProviderError("AI mesajı boş olamaz.", 400, provider)

    normalized_schema = _openai_schema(schema)
    if provider == "groq":
        url = "https://api.groq.com/openai/v1/chat/completions"
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": False, "schema": normalized_schema},
        }
        payload = {
            "model": model or DEFAULT_GROQ_MODEL,
            "messages": clean_messages,
            "temperature": 0.35,
            "max_completion_tokens": min(max_tokens, 8_000),
            "reasoning_effort": "low",
            "reasoning_format": "hidden",
            "response_format": response_format,
        }
    else:
        url = "https://openrouter.ai/api/v1/chat/completions"
        selected_model = model or DEFAULT_OPENROUTER_MODEL
        payload = {
            "model": selected_model,
            "messages": clean_messages,
            "temperature": 0.35,
            "max_tokens": max_tokens,
            "response_format": (
                {"type": "json_object"}
                if selected_model == "openrouter/free"
                else {
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "strict": False, "schema": normalized_schema},
                }
            ),
        }
        if selected_model != "openrouter/free":
            payload["provider"] = {"require_parameters": True}

    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=(6, 75),
        )
    except requests.Timeout as exc:
        raise AiProviderError(f"{provider} zaman aşımına uğradı.", 504, provider) from exc
    except requests.RequestException as exc:
        raise AiProviderError(f"{provider} servisine ulaşılamıyor.", 503, provider) from exc
    return _parse_openai_response(response, provider)


def call_structured_with_fallback(
    *,
    config: dict[str, Any],
    gemini_call: Callable[[str], dict[str, Any]],
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    schema_name: str,
    max_tokens: int,
) -> tuple[dict[str, Any], str, str]:
    """Try configured providers in a deterministic, cost-conscious order."""
    attempts: list[tuple[str, int]] = []
    requested_order = str(
        config.get("provider_order") or "gemini,groq,openrouter"
    ).lower().split(",")
    order = []
    for name in requested_order + ["gemini", "groq", "openrouter"]:
        name = name.strip()
        if name in {"gemini", "groq", "openrouter"} and name not in order:
            order.append(name)

    for provider in order:
        key = _clean_key(config.get(f"{provider}_key"))
        if not key:
            continue
        if provider == "gemini":
            try:
                return gemini_call(key), "gemini", str(config.get("gemini_model") or "")
            except Exception as exc:  # The caller owns the Gemini-specific error type.
                attempts.append(("gemini", int(getattr(exc, "status_code", 503))))
            continue
        default_model = DEFAULT_GROQ_MODEL if provider == "groq" else DEFAULT_OPENROUTER_MODEL
        model = str(config.get(f"{provider}_model") or default_model).strip()
        try:
            parsed = _call_openai_compatible(
                provider=provider,
                api_key=key,
                model=model,
                messages=messages,
                schema=schema,
                schema_name=schema_name,
                max_tokens=max_tokens,
            )
            return parsed, provider, model
        except AiProviderError as exc:
            attempts.append((provider, exc.status_code))

    if not attempts:
        raise AiProviderError(
            "AI sağlayıcısı yapılandırılmamış. Render'a GEMINI_API_KEY, GROQ_API_KEY veya OPENROUTER_API_KEY ekleyin.",
            503,
        )
    providers = ", ".join(name for name, _ in attempts)
    status = 429 if attempts and all(code == 429 for _, code in attempts) else 503
    raise AiProviderError(f"AI sağlayıcıları şu anda yanıt veremiyor: {providers}.", status)


def test_provider_chain(
    *,
    config: dict[str, Any],
    gemini_test: Callable[[str], Any],
) -> tuple[str, str]:
    schema = {
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
    }
    parsed, provider, model = call_structured_with_fallback(
        config=config,
        gemini_call=lambda key: ({"status": str(gemini_test(key) or "OK")}),
        messages=[{"role": "user", "content": 'Yalnızca {"status":"OK"} JSON nesnesini döndür.'}],
        schema=schema,
        schema_name="fontify_connection_test",
        max_tokens=64,
    )
    if not isinstance(parsed.get("status"), str):
        raise AiProviderError("AI bağlantı testi geçersiz yanıt döndürdü.", 502, provider)
    return provider, model
