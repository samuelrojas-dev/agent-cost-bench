"""Static, offline tool-risk scan (ADR 0022).

``dowbench scan`` reads a user's tool definitions and flags the cost-amplification shapes the
pilot measured — no API key, no provider call, no spend. It reads tool *schemas* only, so a
finding is a pattern match ("this shape amplified cost 8.8x in our pilot"), never a prediction
of the user's actual cost (CLAUDE.md: no invented numbers).
"""

from __future__ import annotations

from dowbench.scan.engine import Finding, ScanReport, Severity, scan_tools
from dowbench.scan.loaders import (
    LOADER_GROUP,
    ToolLoadError,
    from_langchain,
    from_openai_tools,
    from_openapi,
    load_tools,
)
from dowbench.scan.report import render

__all__ = [
    "LOADER_GROUP",
    "Finding",
    "ScanReport",
    "Severity",
    "ToolLoadError",
    "from_langchain",
    "from_openai_tools",
    "from_openapi",
    "load_tools",
    "render",
    "scan_tools",
]
