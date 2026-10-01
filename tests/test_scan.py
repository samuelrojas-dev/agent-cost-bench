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
from pydantic import BaseModel
from typer.testing import CliRunner

from dowbench.cli import app
from dowbench.providers.base import ToolSpec
from dowbench.scan import (
    ToolLoadError,
    from_langchain,
    from_openai_tools,
    from_openapi,
    load_tools,
    render,
    scan_tools,
)
from dowbench.scan.engine import ScanReport

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "tools_openai.json"
OPENAPI_FIXTURE = Path(__file__).parent / "fixtures" / "openapi.json"
# The toolset the README's 60-second demo scans; this test pins the counts it documents.
README_DEMO = Path(__file__).parent.parent / "examples" / "agent_tools.json"


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


# --- loaders: langchain ----------------------------------------------------------------------


class _FakeTool:
    """A duck-typed stand-in for a LangChain BaseTool (no LangChain dependency in tests)."""

    def __init__(
        self, name: str, description: str, *, args: object = None, args_schema: object = None
    ):
        self.name = name
        self.description = description
        if args is not None:
            self.args = args
        if args_schema is not None:
            self.args_schema = args_schema


def test_from_langchain_reads_live_objects_with_an_args_mapping() -> None:
    tools = from_langchain(
        [_FakeTool("web_search", "Search the web", args={"query": {"type": "string"}})]
    )
    assert tools[0].name == "web_search"
    assert list(tools[0].parameters["properties"]) == ["query"]


def test_from_langchain_reads_a_pydantic_args_schema() -> None:
    class Args(BaseModel):
        query: str
        cursor: str

    tools = from_langchain([_FakeTool("list_items", "List items", args_schema=Args)])
    assert set(tools[0].parameters["properties"]) == {"query", "cursor"}


def test_from_langchain_reads_the_serialized_dict_shape() -> None:
    tools = from_langchain(
        [{"name": "summarize", "description": "s", "args": {"history": {"type": "string"}}}]
    )
    assert tools[0].name == "summarize"
    assert list(tools[0].parameters["properties"]) == ["history"]


def test_from_langchain_tool_without_args_gets_an_empty_schema() -> None:
    tools = from_langchain([_FakeTool("ping", "no args")])
    assert tools[0].parameters == {"type": "object", "properties": {}}


def test_from_langchain_rejects_an_object_without_a_name() -> None:
    with pytest.raises(ToolLoadError, match="no 'name'"):
        from_langchain([_FakeTool("", "nameless")])


# --- loaders: openapi ------------------------------------------------------------------------


def test_from_openapi_maps_each_operation_to_a_tool() -> None:
    spec = json.loads(OPENAPI_FIXTURE.read_text(encoding="utf-8"))
    tools = {t.name: t for t in from_openapi(spec)}
    assert set(tools) == {"searchArticles", "listArticles", "createReply"}
    # $ref parameter (cursor) and $ref request body (Reply.history) are resolved.
    assert "cursor" in tools["listArticles"].parameters["properties"]
    assert "history" in tools["createReply"].parameters["properties"]


def test_from_openapi_findings_match_the_operation_shapes() -> None:
    spec = json.loads(OPENAPI_FIXTURE.read_text(encoding="utf-8"))
    report = scan_tools(from_openapi(spec))
    by_tool = {(f.tool, f.rule) for f in report.findings}
    assert ("searchArticles", "unbounded-result") in by_tool  # retrieval, no limit
    assert ("listArticles", "unbounded-pagination") in by_tool  # cursor, no page cap
    assert ("createReply", "result-relay") in by_tool  # history field relayed
    # listArticles has a `limit`, so it must NOT be flagged unbounded-result.
    assert ("listArticles", "unbounded-result") not in by_tool


def test_from_openapi_generates_a_name_when_operationid_is_absent() -> None:
    spec = {"paths": {"/things": {"get": {"summary": "no id"}}}}
    tools = from_openapi(spec)
    assert tools[0].name == "get__things"


def test_from_openapi_ignores_a_broken_ref_without_raising() -> None:
    spec = {
        "paths": {"/x": {"get": {"operationId": "x", "parameters": [{"$ref": "#/nope/missing"}]}}}
    }
    tools = from_openapi(spec)
    assert tools[0].parameters["properties"] == {}


@pytest.mark.parametrize("bad", [42, {}, {"paths": "not a dict"}, []])
def test_from_openapi_rejects_a_non_spec(bad: object) -> None:
    with pytest.raises(ToolLoadError, match="OpenAPI"):
        from_openapi(bad)


# --- loaders: registry dispatch for the new loaders ------------------------------------------


@pytest.mark.parametrize("loader", ["openai", "langchain", "openapi"])
def test_all_builtin_loaders_are_registered(loader: str) -> None:
    from dowbench import registry

    assert loader in registry.names("dowbench.tool_loaders")


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


def test_has_at_least_compares_by_severity_rank() -> None:
    report = scan_tools([_tool("web_search", "search")])  # one HIGH finding
    assert report.has_at_least("high") is True
    assert report.has_at_least("low") is True
    empty = ScanReport(tool_count=0, findings=[])
    assert empty.has_at_least("low") is False


def test_cli_scan_fail_on_high_exits_nonzero_when_a_high_finding_exists() -> None:
    result = runner.invoke(app, ["scan", str(FIXTURE), "--fail-on", "high", "--format", "plain"])
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_cli_scan_fail_on_none_is_the_default_and_exits_zero() -> None:
    result = runner.invoke(app, ["scan", str(FIXTURE), "--format", "plain"])
    assert result.exit_code == 0
    assert "FAIL" not in result.output


def test_cli_scan_fail_on_high_exits_zero_when_only_lower_findings_exist(tmp_path: Path) -> None:
    # A single relay tool: one MEDIUM finding, no HIGH, and too few tools for no-call-budget.
    f = tmp_path / "t.json"
    f.write_text(
        '[{"type":"function","function":{"name":"draft","description":"d",'
        '"parameters":{"type":"object","properties":{"history":{"type":"string"}}}}}]',
        encoding="utf-8",
    )
    result = runner.invoke(app, ["scan", str(f), "--fail-on", "high", "--format", "plain"])
    assert result.exit_code == 0
    result_medium = runner.invoke(app, ["scan", str(f), "--fail-on", "medium", "--format", "plain"])
    assert result_medium.exit_code == 1


def test_cli_scan_reads_an_openapi_spec_with_the_openapi_loader() -> None:
    result = runner.invoke(
        app, ["scan", str(OPENAPI_FIXTURE), "--loader", "openapi", "--format", "plain"]
    )
    assert result.exit_code == 0, result.output
    assert "searchArticles" in result.output
    assert "unbounded-result" in result.output


def test_readme_demo_toolset_scans_as_documented() -> None:
    # Guards the counts the README's 60-second demo shows, so the doc cannot drift from reality.
    report = scan_tools(from_openai_tools(json.loads(README_DEMO.read_text(encoding="utf-8"))))
    assert report.counts() == {"high": 3, "medium": 2, "low": 1}
    # send_email returns nothing an attacker can inflate: it must not be flagged.
    assert all(f.tool != "send_email" for f in report.findings)
