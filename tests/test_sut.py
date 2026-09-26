"""Bring-your-own-agent tests (ADR 0012). Offline: mock provider and fake SDK clients."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from anthropic.types import Message as AnthropicMessage
from examples.gemini_agent import GeminiAgent
from examples.mock_agent import MockAgent
from google.genai import types
from pydantic import ValidationError
from typer.testing import CliRunner

from dowbench.agent.loop import Ceiling
from dowbench.agent.tools import Injection, ToolBox
from dowbench.attacks.schema import load_dataset
from dowbench.cli import app
from dowbench.metering.pricing import PriceTable
from dowbench.metering.usage import Usage
from dowbench.providers.base import Response
from dowbench.runner.config import RunConfig
from dowbench.runner.execute import estimate, execute
from dowbench.runner.matrix import plan_episodes
from dowbench.sut import Meter, StopEpisode, Task, load_agent, run_agent_episode

AGENT_CONFIG = Path(__file__).parent.parent / "configs" / "agent-mock.yaml"
CEILING = Ceiling(max_turns=3, max_tokens_per_call=100, max_total_tokens=1000)


def _mock_response(tokens: int, raw: bool = True) -> Response:
    usage = Usage(input_tokens=tokens)
    return Response(
        stop_reason="end_turn",
        usage=usage,
        raw={"usage": usage.model_dump()} if raw else {},
        model_version="mock-1-simulated",
    )


def _meter(simulated: bool = True, provider: str = "mock", **kw: Any) -> Meter:
    return Meter(provider=provider, simulated=simulated, ceiling=kw.pop("ceiling", CEILING), **kw)


# --- meter ---


def test_meter_records_each_call_as_it_arrives() -> None:
    seen: list[Any] = []
    meter = _meter(on_call=seen.append)
    meter.record(_mock_response(100))
    meter.record(_mock_response(200))
    assert [c.usage.total_tokens for c in seen] == [100, 200]
    assert [c.turn for c in seen] == [1, 2]
    assert meter.usage.total_tokens == 300
    assert seen[0].model_version == "mock-1-simulated"


def test_meter_stops_at_the_token_ceiling_after_recording_the_call() -> None:
    meter = _meter()
    meter.record(_mock_response(600))
    with pytest.raises(StopEpisode) as stop:
        meter.record(_mock_response(600))
    assert stop.value.status == "censored"
    assert meter.usage.total_tokens == 1200  # the overshooting call is billed and kept


def test_meter_stops_after_one_call_beyond_max_turns() -> None:
    meter = _meter()
    for _ in range(CEILING.max_turns):
        meter.record(_mock_response(1))
    with pytest.raises(StopEpisode, match="turns") as stop:
        meter.record(_mock_response(1))
    assert stop.value.status == "censored"


def test_one_call_over_the_ceiling_breaks_the_estimate_assumption() -> None:
    with pytest.raises(StopEpisode, match="over the 1,000 ceiling") as stop:
        _meter().record(_mock_response(1001))
    assert stop.value.status == "errored"


@pytest.mark.parametrize(
    ("meter", "response", "match"),
    [
        (_meter(simulated=False), _mock_response(5, raw=False), "has no usage"),
        (_meter(provider="gemini", simulated=False), object(), "cannot meter a object"),
        (_meter(provider="anthropic", simulated=False), _mock_response(5), "cannot meter"),
    ],
)
def test_unusable_responses_are_recorded_and_end_the_episode(
    meter: Meter, response: object, match: str
) -> None:
    with pytest.raises(StopEpisode, match=match) as stop:
        meter.record(response)
    assert stop.value.status == "errored"
    assert meter.calls[0].error is not None


def test_meter_maps_real_sdk_responses_with_the_strict_mappers() -> None:
    gemini = types.GenerateContentResponse(
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=10, candidates_token_count=5, total_token_count=15
        ),
        model_version="gemini-x-001",
    )
    meter = _meter(provider="gemini", simulated=False)
    meter.record(gemini)
    assert meter.calls[0].usage == Usage(input_tokens=10, output_tokens=5)
    assert meter.calls[0].raw_usage["total_token_count"] == 15
    assert meter.calls[0].model_version == "gemini-x-001"

    claude = AnthropicMessage.model_validate(
        {
            "id": "m",
            "type": "message",
            "role": "assistant",
            "model": "claude-x",
            "content": [],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 7, "output_tokens": 3, "service_tier": "priority"},
        }
    )
    meter = _meter(provider="anthropic", simulated=False)
    with pytest.raises(StopEpisode, match="service tier"):
        meter.record(claude)  # unpriceable usage is refused, with its raw usage kept
    assert meter.calls[0].raw_usage["service_tier"] == "priority"


# --- episodes ---


class _SwallowingAgent:
    """Catches every Exception and keeps calling: the ceiling must still stop it."""

    def run(self, task: Task) -> str:
        while True:
            try:
                task.meter.record(_mock_response(400))
            except Exception:
                continue


class _SilentAgent:
    def run(self, task: Task) -> str:
        return "answer, with no calls recorded"


def _episode(agent: Any, simulated: bool = True) -> Any:
    return run_agent_episode(
        agent,
        provider="mock",
        simulated=simulated,
        system="s",
        user_prompt="p",
        toolbox=ToolBox("search"),
        ceiling=CEILING,
    )


def test_an_agent_cannot_swallow_the_stop_signal() -> None:
    result = _episode(_SwallowingAgent())
    assert result.status == "censored"
    assert result.turns == 3


def test_a_real_agent_that_records_nothing_is_errored() -> None:
    assert _episode(_SilentAgent(), simulated=False).status == "errored"
    assert _episode(_SilentAgent(), simulated=True).status == "completed"


def test_tools_carry_the_injected_payload() -> None:
    box = ToolBox("search", Injection("search", "INJECTED"))
    seen: list[str] = []

    class Probe:
        def run(self, task: Task) -> str:
            seen.append(task.tool("search")(query="refunds"))
            return "done"

    run_agent_episode(
        Probe(),
        provider="mock",
        simulated=True,
        system="s",
        user_prompt="p",
        toolbox=box,
        ceiling=CEILING,
    )
    assert "INJECTED" in seen[0]


# --- loading and config ---


class _Instance:
    def run(self, task: Task) -> str:
        return ""


INSTANCE = _Instance()


def _factory() -> _Instance:
    return _Instance()


@pytest.mark.parametrize("attr", ["_Instance", "INSTANCE", "_factory"])
def test_load_agent_accepts_a_class_an_instance_or_a_factory(attr: str) -> None:
    assert isinstance(load_agent(f"tests.test_sut:{attr}"), _Instance)


@pytest.mark.parametrize("path", ["no_colon", "tests.test_sut:json"])
def test_load_agent_rejects_bad_targets(path: str) -> None:
    with pytest.raises((ValueError, TypeError)):
        load_agent(path)


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"models": ["a", "b"]}, "exactly one model"),
        ({"defenses": [{"name": "none"}, {"name": "turn_limit"}]}, "only use the 'none'"),
        ({"agent": "not a path"}, "agent"),
    ],
)
def test_agent_configs_are_validated(overrides: dict[str, Any], match: str) -> None:
    data = {**RunConfig.from_yaml(AGENT_CONFIG).model_dump(mode="json"), **overrides}
    with pytest.raises(ValidationError, match=match):
        RunConfig.model_validate(data)


# --- end to end ---


def test_agent_run_end_to_end(tmp_path: Path) -> None:
    config, dataset = RunConfig.from_yaml(AGENT_CONFIG), load_dataset()
    specs = plan_episodes(config, dataset)
    worst = estimate(config, specs, PriceTable.load(), done=set())
    assert worst.worst_case_tokens == 2 * config.ceiling.max_total_tokens * len(specs)
    assert worst.max_model_calls == (config.ceiling.max_turns + 1) * len(specs)

    summary = execute(
        config, dataset, agent=MockAgent(), prices=PriceTable.load(), out_dir=tmp_path
    )
    run_dir = tmp_path / config.run_name
    calls = [json.loads(line) for line in (run_dir / "calls.jsonl").read_text().splitlines()]
    episodes = [json.loads(line) for line in (run_dir / "episodes.jsonl").read_text().splitlines()]
    assert len(episodes) == len(specs)
    assert len(calls) == sum(e["turns"] for e in episodes)
    assert [d.defense for d in summary.defenses] == ["none"]
    # The mock agent follows every payload, as the built-in mock loop does.
    assert summary.defenses[0].asr == 1.0
    assert json.loads((run_dir / "run.json").read_text())["config"]["agent"] == config.agent


def test_execute_refuses_mixed_or_missing_runners(tmp_path: Path) -> None:
    config, dataset = RunConfig.from_yaml(AGENT_CONFIG), load_dataset()
    with pytest.raises(ValueError, match="not a provider"):
        execute(
            config,
            dataset,
            provider=MockAgent()._model,
            prices=PriceTable.load(),
            out_dir=tmp_path,
        )


def test_cli_runs_an_agent_config_and_reports_it(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run", str(AGENT_CONFIG), "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    report = runner.invoke(app, ["report", str(tmp_path / "agent-mock")])
    assert "| Agent under test | `examples.mock_agent:MockAgent` |" in report.output


# --- the Gemini example agent, against a fake client ---


class _FakeModels:
    def __init__(self, responses: list[types.GenerateContentResponse]) -> None:
        self._responses = responses
        self.sent: list[list[types.Content]] = []

    def generate_content(self, *, model: str, contents: Any, config: Any) -> Any:
        self.sent.append(list(contents))
        return self._responses[len(self.sent) - 1]


def _gemini(parts: list[types.Part], prompt: int) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt, candidates_token_count=5, total_token_count=prompt + 5
        ),
        model_version="gemini-3.5-flash-lite",
    )


def test_gemini_example_agent_uses_the_tools_and_meters_every_call() -> None:
    call = types.Part(
        function_call=types.FunctionCall(id="c1", name="search", args={"query": "refunds"}),
        thought_signature=b"sig",
    )
    models = _FakeModels([_gemini([call], 50), _gemini([types.Part(text="30 days")], 90)])
    client = type("Client", (), {"models": models})()
    box = ToolBox("search", Injection("search", "INJECTED"))
    result = run_agent_episode(
        GeminiAgent(client=client),
        provider="gemini",
        simulated=False,
        system="s",
        user_prompt="What is the refund deadline?",
        toolbox=box,
        ceiling=CEILING,
    )
    assert result.status == "completed"
    assert result.final_text == "30 days"
    assert [c.usage.total_tokens for c in result.calls] == [55, 95]
    replayed, results = models.sent[1][1], models.sent[1][2]
    assert replayed.parts and replayed.parts[0].thought_signature == b"sig"  # verbatim
    function_response = results.parts[0].function_response if results.parts else None
    assert function_response is not None and function_response.response is not None
    assert "INJECTED" in function_response.response["result"]
