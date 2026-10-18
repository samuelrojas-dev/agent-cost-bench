"""Render a ``ScanReport`` as Markdown, plain text, or SARIF (ADR 0022).

The report leads with plain language and an honesty note — the scan is a pattern match, not a
cost prediction — and links the attack/defense vocabulary for those who want it, so a newcomer
needs no new terms at the door.

SARIF output (``--format sarif``) produces a file that GitHub Code Scanning and other SARIF
consumers understand, so ``dowbench scan`` can gate a CI pipeline.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from dowbench.scan.engine import Finding, ScanReport, Severity

Format = Literal["markdown", "plain", "sarif"]

_HONESTY = (
    "dowbench scan matches the shape of your tool definitions against cost-amplification "
    "patterns measured in our pilot. It reads schemas only and makes no calls, so a finding is "
    "a pattern match, not a prediction of your actual cost."
)
_MARK: dict[Severity, str] = {"high": "[HIGH]  ", "medium": "[MEDIUM]", "low": "[LOW]   "}


def _summary_line(report: ScanReport) -> str:
    c = report.counts()
    return (
        f"Scanned {report.tool_count} tool(s): "
        f"{c['high']} high, {c['medium']} medium, {c['low']} low."
    )


def _render_plain(report: ScanReport) -> str:
    lines = ["dowbench scan", _HONESTY, "", _summary_line(report)]
    if not report.findings:
        lines += [
            "",
            "No cost-amplification patterns matched. (Absence of a match is not a proof"
            " of safety — the scan checks known shapes only.)",
        ]
        return "\n".join(lines)
    for f in report.findings:
        scope = f.tool if f.tool is not None else "(toolset)"
        lines += [
            "",
            f"{_MARK[f.severity]} {f.rule}  —  {scope}",
            f"  {f.message}",
            f"  pattern: {f.pattern}  [{f.adr}]",
            f"  why: {f.evidence}",
            f"  fix: {f.fix}",
        ]
    return "\n".join(lines)


def _render_markdown(report: ScanReport) -> str:
    lines = ["# dowbench scan", "", f"> {_HONESTY}", "", f"**{_summary_line(report)}**"]
    if not report.findings:
        lines += [
            "",
            "No cost-amplification patterns matched. Absence of a match is not a proof of "
            "safety — the scan checks known shapes only.",
        ]
        return "\n".join(lines) + "\n"
    lines += ["", "| Severity | Rule | Tool | Pattern | Fix |", "|---|---|---|---|---|"]
    for f in report.findings:
        scope = f.tool if f.tool is not None else "_(toolset)_"
        lines.append(f"| {f.severity} | {f.rule} | {scope} | {f.pattern} | {f.fix} |")
    lines.append("")
    lines.append("## Details")
    for f in report.findings:
        scope = f.tool if f.tool is not None else "(toolset)"
        lines += [
            "",
            f"### {f.severity.upper()} — {f.rule} ({scope})",
            "",
            f"{f.message}",
            "",
            f"- **Pattern:** {f.pattern} ({f.adr})",
            f"- **Why it matters:** {f.evidence}",
            f"- **Fix:** {f.fix}",
        ]
    return "\n".join(lines) + "\n"


_SARIF_LEVEL: dict[Severity, str] = {"high": "error", "medium": "warning", "low": "note"}

_INFO_URI = "https://github.com/samuelrojas-dev/agent-cost-bench"


def _sarif_rule(finding: Finding) -> dict[str, Any]:
    return {
        "id": finding.rule,
        "shortDescription": {"text": finding.pattern},
        "helpUri": _INFO_URI,
        "properties": {"adr": finding.adr},
    }


def _sarif_result(finding: Finding, tools_file: str) -> dict[str, Any]:
    return {
        "ruleId": finding.rule,
        "level": _SARIF_LEVEL[finding.severity],
        "message": {"text": finding.message},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": tools_file},
                },
            }
        ],
        "fixes": [{"description": {"text": finding.fix}}],
    }


def _render_sarif(report: ScanReport, tools_file: str = "tools.json") -> str:
    """Produce a SARIF 2.1.0 document for GitHub Code Scanning / compatible consumers."""
    seen_rules: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for f in report.findings:
        if f.rule not in seen_rules:
            seen_rules[f.rule] = _sarif_rule(f)
        results.append(_sarif_result(f, tools_file))
    sarif: dict[str, Any] = {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "dowbench-scan",
                        "informationUri": _INFO_URI,
                        "rules": list(seen_rules.values()),
                    },
                },
                "results": results,
            },
        ],
    }
    return json.dumps(sarif, ensure_ascii=False, indent=1) + "\n"


def render(
    report: ScanReport,
    fmt: Format = "markdown",
    *,
    tools_file: str = "tools.json",
) -> str:
    """Render ``report`` in the requested format."""
    if fmt == "sarif":
        return _render_sarif(report, tools_file=tools_file)
    return _render_markdown(report) if fmt == "markdown" else _render_plain(report)
