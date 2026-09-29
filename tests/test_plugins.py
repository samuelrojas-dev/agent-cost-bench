"""The entry-point plugin system (ADR 0021): a third party adds a defense/provider/attack
without editing the core. The example plugin under examples/plugin_example is loaded here
through an injected entry-point source, which stands in for installed package metadata."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest

from dowbench import registry
from dowbench.defenses.base import Defense
from dowbench.providers.base import Request

EXAMPLE = Path(__file__).parent.parent / "examples" / "plugin_example"
sys.path.insert(0, str(EXAMPLE))


def _source(*eps: EntryPoint) -> Callable[[str], Iterable[EntryPoint]]:
    def source(group: str) -> Iterable[EntryPoint]:
        return [ep for ep in eps if ep.group == group]

    return source


_DEF = EntryPoint("head_cap", "dowbench_plugin_example.defense:HeadCap", "dowbench.defenses")
_PROV = EntryPoint("echo", "dowbench_plugin_example.provider:build_echo", "dowbench.providers")


def test_registry_lists_and_loads_a_plugin_component() -> None:
    source = _source(_DEF, _PROV)
    assert registry.names("dowbench.defenses", source=source) == ["head_cap"]
    cls = registry.load("dowbench.defenses", "head_cap", source=source)
    assert issubclass(cls, Defense) and cls.name == "head_cap"


def test_registry_unknown_name_lists_alternatives() -> None:
    source = _source(_DEF)
    with pytest.raises(LookupError, match="head_cap"):
        registry.load("dowbench.defenses", "nope", source=source)


def test_plugin_defense_builds_and_caps_a_tool_result() -> None:
    cls = registry.load("dowbench.defenses", "head_cap", source=_source(_DEF))
    defense = cls(max_chars=5)
    # on_tool_result keeps only the head; state is unused by this defense.
    assert defense.on_tool_result(call=None, result="0123456789", state=None) == "01234"


def test_plugin_provider_factory_returns_a_usable_provider() -> None:
    build = registry.load("dowbench.providers", "echo", source=_source(_PROV))
    provider = build(None, None)
    assert provider.name == "echo" and provider.simulated is True
    response = provider.complete(Request(model="echo", system="", messages=[], max_tokens=16))
    assert response.usage.output_tokens > 0  # a real, mappable usage
