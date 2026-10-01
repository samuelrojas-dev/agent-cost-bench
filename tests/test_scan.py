"""Static tool-risk scan (ADR 0022): loaders, heuristic engine, report and CLI, all offline.

The scan reads tool schemas only — it makes no provider call and needs no key — so every test
here runs against in-memory fixtures and the autouse network guard stays untriggered.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dowbench.cli import app
from dowbench.providers.base import ToolSpec
from dowbench.scan import (
    ToolLoadError,
    from_openai_tools,
    load_tools,
    render,
    scan_tools,
)
from dowbench.scan.engine import ScanReport

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "tools_openai.json"


def _tool(name: str, description: str = "", **props: object) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        parameters={"type": "object", "properties": dict(props)},
    )


def _rules(report: ScanReport) -> set[str]:
    return {f.rule for f in report.findings}


# --- loaders ---------------------------------------------------------------------------------


def test_from_openai_tools_reads_the_nested_chat_completions_shape() -> None:
    data = [
        {
            "type": "function",
            "function": {
                "name": "do_thing",
                "description": "does a thing",
                "parameters": {"type": "object", "properties": {"x": {"type": "string"}}},
            },
        }
    ]
    tools = from_openai_tools(data)
    assert [t.name for t in tools] == ["do_thing"]
    assert tools[0].description == "does a thing"
    assert "x" in tools[0].parameters["properties"]


def test_from_openai_tools_reads_the_flattened_and_bare_shapes() -> None:
    flat = [{"type": "function", "name": "flat", "parameters": {"type": "object"}}]
    bare = [{"name": "bare", "description": "d"}]
    assert from_openai_tools(flat)[0].name == "flat"
    bare_tool = from_openai_tools(bare)[0]
    assert bare_tool.name == "bare"
    # A missing parameters schema defaults to an empty object schema, not an error.
    assert bare_tool.parameters == {"type": "object", "properties": {}}


def test_from_openai_tools_accepts_a_mapping_with_a_tools_array() -> None:
    data = {"tools": [{"type": "function", "function": {"name": "t"}}]}
    assert from_openai_tools(data)[0].name == "t"


@pytest.mark.parametrize(
    "bad",
    [
        42,
        {"no_tools": []},
        [{"type": "function"}],  # no name
        [{"type": "function", "function": {"name": ""}}],  # empty name
        ["not an object"],
        [{"name": "x", "description": 5}],  # non-string description
    ],
)
def test_from_openai_tools_rejects_malformed_input(bad: object) -> None:
    with pytest.raises(ToolLoadError):
        from_openai_tools(bad)


def test_load_tools_dispatches_through_an_injected_registry_source() -> None:
    ep = EntryPoint("openai", "dowbench.scan.loaders:from_openai_tools", "dowbench.tool_loaders")

    def source(group: str) -> Iterable[EntryPoint]:
        return [ep] if group == "dowbench.tool_loaders" else []

    tools = load_tools("openai", [{"name": "t"}], source=source)
    assert [t.name for t in tools] == ["t"]


def test_load_tools_unknown_loader_lists_alternatives() -> None:
    def source(group: str) -> Iterable[EntryPoint]:
        return []

    with pytest.raises(LookupError, match="tool_loaders"):
        load_tools("nope", [], source=source)


def bad_loader(_data: object) -> list[object]:
    """A loader that violates the contract by returning non-ToolSpec objects (test fixture)."""
    return ["not a toolspec"]


def test_load_tools_rejects_a_loader_that_returns_non_toolspec() -> None:
    ep = EntryPoint("bad", "tests.test_scan:bad_loader", "dowbench.tool_loaders")

    def source(group: str) -> Iterable[EntryPoint]:
        return [ep]

    with pytest.raises(ToolLoadError, match="ToolSpec"):
        load_tools("bad", [], source=source)


# --- engine: positive matches ----------------------------------------------------------------


def test_unbounded_result_flags_a_retrieval_tool_without_a_size_bound() -> None:
    report = scan_tools([_tool("web_search", "search the web")])
    assert "unbounded-result" in _rules(report)
    high = next(f for f in report.findings if f.rule == "unbounded-result")
    assert high.severity == "high"
    # Honesty (CLAUDE.md): the 8.8x number is framed as a pilot pattern, not a prediction.
    assert "8.8x in our pilot" in high.evidence


def test_unbounded_pagination_flags_a_cursor_without_a_page_cap() -> None:
    report = scan_tools([_tool("list_issues", "list issues", cursor={"type": "string"})])
    assert "unbounded-pagination" in _rules(report)


def test_result_relay_flags_a_free_text_field_that_carries_prior_output() -> None:
    report = scan_tools([_tool("summarize", "summarize", history={"type": "string"})])
    relay = next(f for f in report.findings if f.rule == "result-relay")
    assert relay.severity == "medium"
    assert "history" in relay.message


def test_no_call_budget_fires_once_for_a_many_tool_set() -> None:
    tools = [_tool(f"get_weather_{i}", "temperature only") for i in range(3)]
    report = scan_tools(tools)
    budget = [f for f in report.findings if f.rule == "no-call-budget"]
    assert len(budget) == 1 and budget[0].tool is None


# --- engine: no false positives --------------------------------------------------------------


def test_a_bounded_retrieval_tool_is_not_flagged_unbounded() -> None:
    report = scan_tools([_tool("search_docs", "search", max_results={"type": "integer"})])
    assert "unbounded-result" not in _rules(report)


def test_a_small_lookup_tool_is_not_flagged() -> None:
    report = scan_tools([_tool("get_weather", "current temperature", city={"type": "string"})])
    assert _rules(report) == set()


def test_pagination_with_a_max_pages_bound_is_not_flagged() -> None:
    report = scan_tools(
        [
            _tool(
                "paged",
                "paged fetch with a cap",
                cursor={"type": "string"},
                max_pages={"type": "integer"},
            )
        ]
    )
    assert "unbounded-pagination" not in _rules(report)


def test_a_relay_named_field_that_is_not_text_is_not_flagged() -> None:
    report = scan_tools([_tool("x", "no retrieval", history={"type": "integer"})])
    assert "result-relay" not in _rules(report)


def test_findings_are_sorted_worst_severity_first() -> None:
    report = scan_tools(from_openai_tools(json.loads(FIXTURE.read_text(encoding="utf-8"))))
    rank = {"high": 3, "medium": 2, "low": 1}
    severities = [f.severity for f in report.findings]
    assert severities == sorted(severities, key=lambda s: rank[s], reverse=True)
    assert report.max_severity == "high"


# --- report ----------------------------------------------------------------------------------


def test_render_markdown_has_the_honesty_note_and_a_table() -> None:
    report = scan_tools([_tool("web_search", "search")])
    md = render(report, "markdown")
    assert "pattern match, not a prediction" in md
    assert "| Severity | Rule | Tool | Pattern | Fix |" in md
    assert "unbounded-result" in md


def test_render_plain_has_the_honesty_note() -> None:
    report = scan_tools([_tool("web_search", "search")])
    plain = render(report, "plain")
    assert "pattern match, not a prediction" in plain
    assert "[HIGH]" in plain


def test_render_empty_report_says_no_match_is_not_proof_of_safety() -> None:
    report = ScanReport(tool_count=1, findings=[])
    assert "not a proof of safety" in render(report, "markdown")
    assert "not a proof" in render(report, "plain")


# --- CLI -------------------------------------------------------------------------------------


def test_cli_scan_reports_findings_and_exits_zero() -> None:
    result = runner.invoke(app, ["scan", str(FIXTURE), "--format", "plain"])
    assert result.exit_code == 0, result.output
    assert "unbounded-result" in result.output
    assert "8.8x in our pilot" in result.output


def test_cli_scan_markdown_is_the_default() -> None:
    result = runner.invoke(app, ["scan", str(FIXTURE)])
    assert result.exit_code == 0
    assert result.output.startswith("# dowbench scan")


def test_cli_scan_rejects_an_unknown_loader(tmp_path: Path) -> None:
    f = tmp_path / "t.json"
    f.write_text("[]", encoding="utf-8")
    result = runner.invoke(app, ["scan", str(f), "--loader", "nope"])
    assert result.exit_code == 2
    assert "tool_loaders" in result.output


def test_cli_scan_rejects_malformed_json(tmp_path: Path) -> None:
    f = tmp_path / "bad.json"
    f.write_text("{not json", encoding="utf-8")
    result = runner.invoke(app, ["scan", str(f)])
    assert result.exit_code == 2
    assert "cannot read" in result.output


def test_cli_scan_rejects_malformed_tool_structure(tmp_path: Path) -> None:
    f = tmp_path / "t.json"
    f.write_text('[{"type": "function"}]', encoding="utf-8")
    result = runner.invoke(app, ["scan", str(f)])
    assert result.exit_code == 2
