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
DEFAULT_PROVIDER_ORDER = ("gemini", "groq", "openrouter")
TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_SECRET_PATTERNS = (
    re.compile(r"AIza[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b(?:gsk|sk-or-v1|sk|rk|xai)-[A-Za-z0-9._-]{16,}\b", re.IGNORECASE),
    re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/-]{16,}"),
)


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
    message = re.sub(r"[\r\n\x00-\x1f]+", " ", fallback)
    for pattern in _SECRET_PATTERNS:
        if pattern.pattern.startswith("(?i)(\\bBearer"):
            message = pattern.sub(r"\1[redacted]", message)
        else:
            message = pattern.sub("[redacted]", message)
    return message[:240]


def _is_transient_error(exc: Exception) -> bool:
    """Only retry/fail over errors that are safe to retry with another provider."""
    try:
        status = int(getattr(exc, "status_code", 503))
    except (TypeError, ValueError):
        status = 503
    return status in TRANSIENT_STATUS_CODES


def _normalized_error(provider: str, exc: Exception) -> AiProviderError:
    try:
        status = int(getattr(exc, "status_code", 503))
    except (TypeError, ValueError):
        status = 503
    message = _safe_upstream_message(
        {"error": {"message": str(exc)}},
        f"{provider} isteği tamamlanamadı.",
    )
    return AiProviderError(message, status, provider)


def _bounded_content(content: str, limit: int = 60_000) -> str:
    """Keep both the context opening and final user constraints within a cap."""
    if len(content) <= limit:
        return content
    marker = "\n\n[... ara bağlam kısaltıldı; son talimatlar korunuyor ...]\n\n"
    head = min(45_000, limit - len(marker))
    tail = max(0, limit - head - len(marker))
    return content[:head] + marker + content[-tail:]


def _validate_provider_result(
    parsed: Any,
    provider: str,
    result_validator: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    """Reject structurally valid JSON that is not valid for this product flow.

    JSON-mode providers can still return an object that omits application-level
    fields. Treat that as a retryable provider-quality failure so a fallback
    never gets accepted only to fail later in the document renderer.
    """
    if not isinstance(parsed, dict):
        raise AiProviderError(f"{provider} JSON nesnesi döndürmedi.", 502, provider)
    if result_validator is None:
        return parsed
    try:
        result_validator(parsed)
    except Exception as exc:
        raise AiProviderError(
            f"{provider} geçerli bir belge planı döndürmedi.", 502, provider
        ) from exc
    return parsed


def _parse_openai_response(response: requests.Response, provider: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise AiProviderError(f"{provider} geçersiz bir yanıt döndürdü.", 502, provider) from exc
    if not response.ok:
        message = _safe_upstream_message(data, f"{provider} isteği reddedildi.")
        status = response.status_code if 400 <= response.status_code < 500 else 502
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
            clean_messages.append({"role": role, "content": _bounded_content(content)})
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
            timeout=(6, 45),
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
    result_validator: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], str, str]:
    """Try configured providers in a deterministic, cost-conscious order."""
    attempts: list[tuple[str, int]] = []
    configured_order = str(config.get("provider_order") or "").strip()
    requested_order = (
        configured_order.lower().split(",")
        if configured_order
        else list(DEFAULT_PROVIDER_ORDER)
    )
    order = []
    for name in requested_order:
        name = name.strip()
        if name in DEFAULT_PROVIDER_ORDER and name not in order:
            order.append(name)

    for provider in order:
        key = _clean_key(config.get(f"{provider}_key"))
        if not key:
            continue
        if provider == "gemini":
            try:
                parsed = _validate_provider_result(
                    gemini_call(key), "gemini", result_validator
                )
                return parsed, "gemini", str(config.get("gemini_model") or "")
            except Exception as exc:  # The caller owns the Gemini-specific error type.
                normalized = _normalized_error("gemini", exc)
                if not _is_transient_error(normalized):
                    raise normalized from exc
                attempts.append(("gemini", normalized.status_code))
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
            parsed = _validate_provider_result(parsed, provider, result_validator)
            return parsed, provider, model
        except AiProviderError as exc:
            if not _is_transient_error(exc):
                raise exc
            attempts.append((provider, exc.status_code))

    if not attempts:
        raise AiProviderError(
            "AI sağlayıcısı yapılandırılmamış. Render'a GEMINI_API_KEY, GROQ_API_KEY veya OPENROUTER_API_KEY ekleyin.",
            503,
        )
    providers = ", ".join(name for name, _ in attempts)
    if attempts and all(code == 429 for _, code in attempts):
        status = 429
    elif attempts and all(code == 502 for _, code in attempts):
        status = 502
    else:
        status = 503
    raise AiProviderError(
        f"AI sağlayıcıları geçerli bir belge planı üretemedi: {providers}.", status
    )


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
