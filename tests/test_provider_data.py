"""Direct tests for the provider_data codec machinery (ADR 0015/0021).

The GeminiCodec paths need ``google.genai``, so we mock the types rather than
importing the SDK.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from dowbench.providers.provider_data import (
    GeminiCodec,
    _codec,
    decode_provider_data,
    encode_provider_data,
)


# --- passthrough (no codec registered) ---


def test_encode_passthrough_when_no_codec() -> None:
    """Without a registered codec the data passes through unchanged."""
    data = {"content": {"parts": [{"text": "hello"}]}}
    assert encode_provider_data("mock", data) is data


def test_decode_passthrough_when_no_codec() -> None:
    data = {"content": {"parts": [{"text": "hello"}]}}
    assert decode_provider_data("mock", data) is data


def test_encode_empty_returns_empty() -> None:
    assert encode_provider_data("mock", {}) == {}


def test_decode_empty_returns_empty() -> None:
    assert decode_provider_data("mock", {}) == {}


# --- codec cache ---


def test_codec_cache_returns_none_for_unknown_provider() -> None:
    _codec.cache_clear()
    assert _codec("no_such_provider_xyz") is None


# --- GeminiCodec with mocked google.genai.types ---


@dataclass
class _FakeContent:
    """Stand-in for ``google.genai.types.Content``."""

    parts: list[dict[str, Any]]

    def model_dump(self, *, mode: str = "json", exclude_none: bool = False) -> dict[str, Any]:
        return {"parts": self.parts}

    @classmethod
    def model_validate(cls, data: dict[str, Any]) -> "_FakeContent":
        return cls(parts=data.get("parts", []))


def _patch_genai():
    """Context manager that makes ``from google.genai import types`` return our fake."""
    fake_types = MagicMock()
    fake_types.Content = _FakeContent
    fake_genai = MagicMock()
    fake_genai.types = fake_types
    fake_google = MagicMock()
    fake_google.genai = fake_genai
    import sys
    return patch.dict(sys.modules, {
        "google": fake_google,
        "google.genai": fake_genai,
        "google.genai.types": fake_types,
    })


def test_gemini_codec_encode_content_object() -> None:
    """encode serializes a Content object to a dict via model_dump."""
    with _patch_genai():
        content_obj = _FakeContent(parts=[{"text": "hello"}])
        data = {"content": content_obj, "other": "kept"}
        encoded = GeminiCodec.encode(data)
        assert isinstance(encoded["content"], dict)
        assert encoded["content"] == {"parts": [{"text": "hello"}]}
        assert encoded["other"] == "kept"


def test_gemini_codec_decode_dict_to_content() -> None:
    """decode rebuilds a Content object from a dict via model_validate."""
    with _patch_genai():
        data = {"content": {"parts": [{"text": "hello"}]}, "other": "kept"}
        decoded = GeminiCodec.decode(data)
        assert isinstance(decoded["content"], _FakeContent)
        assert decoded["content"].parts == [{"text": "hello"}]
        assert decoded["other"] == "kept"


def test_gemini_codec_round_trip() -> None:
    """encode then decode reproduces the original Content object."""
    with _patch_genai():
        original = _FakeContent(parts=[{"text": "round trip"}])
        data = {"content": original}
        encoded = GeminiCodec.encode(data)
        decoded = GeminiCodec.decode(encoded)
        assert isinstance(decoded["content"], _FakeContent)
        assert decoded["content"].parts == original.parts


def test_gemini_codec_encode_already_dict_passthrough() -> None:
    """When content is already a dict (not a Content), encode keeps it as-is."""
    with _patch_genai():
        data = {"content": {"parts": [{"text": "already dict"}]}}
        encoded = GeminiCodec.encode(data)
        assert encoded["content"] == {"parts": [{"text": "already dict"}]}


def test_gemini_codec_decode_already_content_passthrough() -> None:
    """When content is already a Content object (not a dict), decode keeps it as-is."""
    with _patch_genai():
        content_obj = _FakeContent(parts=[{"text": "already obj"}])
        data = {"content": content_obj}
        decoded = GeminiCodec.decode(data)
        assert decoded["content"] is content_obj


def test_gemini_codec_no_content_key() -> None:
    """When the dict has no 'content' key, encode/decode return a copy."""
    with _patch_genai():
        data = {"thought": "sig123"}
        encoded = GeminiCodec.encode(data)
        assert encoded == {"thought": "sig123"}
        decoded = GeminiCodec.decode(data)
        assert decoded == {"thought": "sig123"}
