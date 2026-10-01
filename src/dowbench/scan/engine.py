"""Heuristic engine: match tool-schema shapes to the cost-amplification patterns (ADR 0022).

Each rule maps a shape visible in a ``ToolSpec`` (name, description, parameter names) to a
measured attack family and the defense that stops it (ADR 0013/0016/0017). A finding is a
*pattern match*, not a cost prediction — the scan reads schemas only (CLAUDE.md). The one real
number cited (8.8x) belongs to the pilot's ``bloat-verify-001`` finding and is reported as
"this shape amplified cost 8.8x in our pilot", never as "your tool will cost 8.8x".
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dowbench.providers.base import ToolSpec

Severity = Literal["high", "medium", "low"]
SEVERITY_RANK: dict[Severity, int] = {"high": 3, "medium": 2, "low": 1}

# A tool whose name/description reads like it returns external data: the shape behind
# context_bloat. Deliberately curated — generic verbs like "get" are left out to avoid
# flagging a small bounded lookup (e.g. get_weather) as an unbounded result.
_RETRIEVAL_TOKENS = (
    "search",
    "fetch",
    "scrape",
    "crawl",
    "download",
    "retrieve",
    "browse",
    "query",
    "list",
    "readfile",
    "readpage",
    "http",
    "url",
    "web",
    "grep",
    "dump",
    "export",
    "lookup",
)
# A parameter that bounds how much a tool returns. Only unambiguous bounding tokens, so a
# field like "account_id" does not read as a bound.
_RESULT_BOUND_TOKENS = (
    "limit",
    "maxresult",
    "pagesize",
    "perpage",
    "topk",
    "topn",
    "maxlength",
    "maxchar",
    "maxbyte",
    "maxtoken",
    "maxlen",
    "truncate",
)
# A parameter that walks pages: the shape behind tool_loop.
_PAGINATION_TOKENS = (
    "cursor",
    "offset",
    "nextpage",
    "pagetoken",
    "continuation",
    "startindex",
    "pagenumber",
)
# A parameter that caps how many pages an agent may pull.
_PAGE_CAP_TOKENS = ("maxpage", "pagelimit", "maxiteration", "maxpages")
# A free-text field whose name says it carries prior output back in: the shape behind
# growing_arguments. Narrow on purpose — "text"/"input"/"prompt" are too generic to flag.
_RELAY_TOKENS = (
    "content",
    "context",
    "history",
    "messages",
    "transcript",
    "conversation",
    "previous",
    "prior",
    "accumulated",
    "memory",
)
# A toolset with at least this many tools and no overall call cap is advised to add one.
_MANY_TOOLS = 3


def _norm(text: str) -> str:
    """Lowercase and strip non-alphanumerics, so ``max_results`` and ``maxResults`` match."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _has_token(haystack: str, tokens: tuple[str, ...]) -> bool:
    normalized = _norm(haystack)
    return any(tok in normalized for tok in tokens)


def _property_names(tool: ToolSpec) -> list[str]:
    props = tool.parameters.get("properties")
    return list(props) if isinstance(props, dict) else []


def _param_type(tool: ToolSpec, name: str) -> Any:
    props = tool.parameters.get("properties")
    if isinstance(props, dict) and isinstance(props.get(name), dict):
        return props[name].get("type")
    return None


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule: str
    tool: str | None
    severity: Severity
    message: str
    pattern: str
    evidence: str
    fix: str
    adr: str


class ScanReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_count: int
    findings: list[Finding] = Field(default_factory=list)

    @property
    def max_severity(self) -> Severity | None:
        if not self.findings:
            return None
        return max((f.severity for f in self.findings), key=lambda s: SEVERITY_RANK[s])

    def counts(self) -> dict[Severity, int]:
        out: dict[Severity, int] = {"high": 0, "medium": 0, "low": 0}
        for f in self.findings:
            out[f.severity] += 1
        return out

    def has_at_least(self, severity: Severity) -> bool:
        """Whether any finding is at or above ``severity`` — the gate behind ``scan --fail-on``."""
        threshold = SEVERITY_RANK[severity]
        return any(SEVERITY_RANK[f.severity] >= threshold for f in self.findings)


def _unbounded_result(tool: ToolSpec) -> Finding | None:
    looks_like_retrieval = _has_token(f"{tool.name} {tool.description}", _RETRIEVAL_TOKENS)
    if not looks_like_retrieval:
        return None
    if any(_has_token(p, _RESULT_BOUND_TOKENS) for p in _property_names(tool)):
        return None
    return Finding(
        rule="unbounded-result",
        tool=tool.name,
        severity="high",
        message=(
            f"'{tool.name}' looks like it returns external data but has no parameter that "
            "limits the result size, so one call can flood the context with billed input tokens."
        ),
        pattern="context_bloat (bloat-verify-001)",
        evidence=(
            "This shape amplified episode cost 8.8x in our pilot; only a result cap stopped it."
        ),
        fix="Add a size/limit parameter, or apply the `result_cap` defense.",
        adr="ADR 0016, ADR 0017",
    )


def _unbounded_pagination(tool: ToolSpec) -> Finding | None:
    names = _property_names(tool)
    if not any(_has_token(p, _PAGINATION_TOKENS) for p in names):
        return None
    if any(_has_token(p, _PAGE_CAP_TOKENS) for p in names):
        return None
    return Finding(
        rule="unbounded-pagination",
        tool=tool.name,
        severity="medium",
        message=(
            f"'{tool.name}' takes a pagination cursor/offset but nothing caps how many pages an "
            "agent may pull, so it can loop fetching page after page."
        ),
        pattern="tool_loop",
        evidence="Unbounded iteration is the tool_loop family measured in the benchmark.",
        fix="Cap total calls with `loop_detect` (max_total_tool_calls), or add a max-pages bound.",
        adr="ADR 0016, ADR 0017",
    )


def _result_relay(tool: ToolSpec) -> Finding | None:
    for name in _property_names(tool):
        if not _has_token(name, _RELAY_TOKENS):
            continue
        ptype = _param_type(tool, name)
        if ptype not in (None, "string"):
            continue
        return Finding(
            rule="result-relay",
            tool=tool.name,
            severity="medium",
            message=(
                f"'{tool.name}' takes a free-text field ('{name}') whose name says it carries "
                "prior output back in, which grows every turn and is re-billed as input."
            ),
            pattern="growing_arguments",
            evidence="Relaying prior output into the next call is the growing_arguments family.",
            fix="Cap or omit the relayed field so each call does not re-send the history.",
            adr="ADR 0016",
        )
    return None


def _no_call_budget(tools: list[ToolSpec]) -> Finding | None:
    if len(tools) < _MANY_TOOLS:
        return None
    return Finding(
        rule="no-call-budget",
        tool=None,
        severity="low",
        message=(
            f"This toolset has {len(tools)} tools and nothing in the schemas bounds the total "
            "number of calls, so a susceptible agent can run up calls without limit."
        ),
        pattern="output_flood / loop",
        evidence="A missing overall call cap is what the output_flood and loop families exploit.",
        fix="Set an overall call/turn cap at the harness level (`loop_detect`, `turn_limit`).",
        adr="ADR 0017",
    )


_PER_TOOL_RULES = (_unbounded_result, _unbounded_pagination, _result_relay)


def scan_tools(tools: list[ToolSpec]) -> ScanReport:
    """Run every heuristic over ``tools`` and collect the findings, worst severity first."""
    findings: list[Finding] = []
    for tool in tools:
        for rule in _PER_TOOL_RULES:
            found = rule(tool)
            if found is not None:
                findings.append(found)
    run_level = _no_call_budget(tools)
    if run_level is not None:
        findings.append(run_level)
    findings.sort(key=lambda f: SEVERITY_RANK[f.severity], reverse=True)
    return ScanReport(tool_count=len(tools), findings=findings)
