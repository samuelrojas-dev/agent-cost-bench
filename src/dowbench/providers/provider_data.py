"""Serialize a provider's opaque per-turn data for a cassette (ADR 0015).

``Response.provider_data`` carries the assistant turn verbatim for replay (ADR 0006):
thinking blocks and thought signatures the same provider needs back on the next turn. It
is provider-specific and may hold SDK objects, so each provider gets a small codec here.
The default assumes the data is already JSON-safe; Gemini stores a ``types.Content`` object
and overrides both directions. The codec is the only provider-specific part of the cassette
format, which is otherwise the neutral ``Request``/``Response`` (ADR 0015, "Gemini first
behind a provider-generic interface").
"""

from __future__ import annotations

from typing import Any


def encode_provider_data(provider: str, data: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe copy of ``data`` for the cassette."""
    if not data:
        return {}
    if provider == "gemini":
        return _gemini_encode(data)
    return data  # mock/synthetic and future providers store JSON-safe data already


def decode_provider_data(provider: str, data: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the provider's ``provider_data`` from the cassette copy."""
    if not data:
        return {}
    if provider == "gemini":
        return _gemini_decode(data)
    return data


def _gemini_encode(data: dict[str, Any]) -> dict[str, Any]:
    from google.genai import types

    out = dict(data)
    content = out.get("content")
    if isinstance(content, types.Content):
        out["content"] = content.model_dump(mode="json", exclude_none=True)
    return out


def _gemini_decode(data: dict[str, Any]) -> dict[str, Any]:
    from google.genai import types

    out = dict(data)
    content = out.get("content")
    if isinstance(content, dict):
        out["content"] = types.Content.model_validate(content)
    return out
