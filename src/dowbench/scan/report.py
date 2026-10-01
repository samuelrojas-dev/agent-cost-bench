"""Render a ``ScanReport`` as Markdown or plain text (ADR 0022).

The report leads with plain language and an honesty note — the scan is a pattern match, not a
cost prediction — and links the attack/defense vocabulary for those who want it, so a newcomer
needs no new terms at the door.
"""

from __future__ import annotations

from typing import Literal

from dowbench.scan.engine import ScanReport, Severity

Format = Literal["markdown", "plain"]

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


def render(report: ScanReport, fmt: Format = "markdown") -> str:
    """Render ``report`` in the requested format."""
    return _render_markdown(report) if fmt == "markdown" else _render_plain(report)
