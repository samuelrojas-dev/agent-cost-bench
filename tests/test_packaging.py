"""Packaging guards: the installed distribution metadata stays consistent with the source.

These catch a broken build wiring (e.g. the dynamic version no longer resolving, or the entry
points dropping out of the wheel) without publishing anything — they read the metadata of the
dowbench already installed in the test environment.
"""

from __future__ import annotations

from importlib.metadata import entry_points, version

import dowbench


def test_distribution_version_matches_dunder_version() -> None:
    # Single source of truth (pyproject [tool.hatch.version] reads __init__.__version__):
    # the built/installed metadata must equal the in-package value.
    assert version("dowbench") == dowbench.__version__


def test_console_script_is_registered() -> None:
    scripts = {ep.name: ep.value for ep in entry_points(group="console_scripts")}
    assert scripts.get("dowbench") == "dowbench.cli:app"


def test_plugin_entry_point_groups_ship_their_builtins() -> None:
    expected = {
        "dowbench.providers": {"mock", "gemini", "anthropic"},
        "dowbench.defenses": {"token_budget", "turn_limit", "loop_detect", "result_cap"},
        "dowbench.tool_loaders": {"openai", "langchain", "openapi"},
    }
    for group, names in expected.items():
        registered = {ep.name for ep in entry_points(group=group)}
        assert names <= registered, f"{group} missing {names - registered}"
