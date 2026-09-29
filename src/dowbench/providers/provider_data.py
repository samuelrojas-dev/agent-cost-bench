"""Serialize a provider's opaque per-turn data for a cassette (ADR 0015).

``Response.provider_data`` carries the assistant turn verbatim for replay (ADR 0006):
thinking blocks and thought signatures the same provider needs back on the next turn. It is
provider-specific and may hold SDK objects, so each provider may register a ``dowbench.codecs``
entry (ADR 0021). Without one the data is assumed JSON-safe and passes through unchanged;
Gemini stores a ``types.Content`` object and registers ``GeminiCodec``.
"""

from __future__ import annotations

from functools import cache
from typing import Any, Protocol, cast

from dowbench.registry import load

CODEC_GROUP = "dowbench.codecs"


class Codec(Protocol):
    @staticmethod
    def encode(data: dict[str, Any]) -> dict[str, Any]: ...

    @staticmethod
    def decode(data: dict[str, Any]) -> dict[str, Any]: ...


@cache
def _codec(provider: str) -> type[Codec] | None:
    try:
        return cast("type[Codec]", load(CODEC_GROUP, provider))
    except LookupError:
        return None


def encode_provider_data(provider: str, data: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe copy of ``data`` for the cassette."""
    if not data:
        return {}
    codec = _codec(provider)
    return codec.encode(data) if codec is not None else data


def decode_provider_data(provider: str, data: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the provider's ``provider_data`` from the cassette copy."""
    if not data:
        return {}
    codec = _codec(provider)
    return codec.decode(data) if codec is not None else data


class GeminiCodec:
    """Codec for Gemini's ``types.Content`` provider_data (registered as ``dowbench.codecs``)."""

    @staticmethod
    def encode(data: dict[str, Any]) -> dict[str, Any]:
        from google.genai import types

        out = dict(data)
        content = out.get("content")
        if isinstance(content, types.Content):
            out["content"] = content.model_dump(mode="json", exclude_none=True)
        return out

    @staticmethod
    def decode(data: dict[str, Any]) -> dict[str, Any]:
        from google.genai import types

        out = dict(data)
        content = out.get("content")
        if isinstance(content, dict):
            out["content"] = types.Content.model_validate(content)
        return out
