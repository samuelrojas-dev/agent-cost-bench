from pathlib import Path

import pytest
from pydantic import ValidationError

from dowbench.agent.tools import Injection, ToolBox
from dowbench.attacks.schema import Attack, Dataset, load_dataset
from dowbench.providers.base import ToolCall


def test_seed_dataset_loads() -> None:
    dataset = load_dataset()
    assert len(dataset.benign) == 5
    assert len(dataset.attacks) == 6
    assert {a.family for a in dataset.attacks} == {
        "tool_loop",
        "output_flood",
        "reasoning_bomb",
        "context_bloat",
        "mcp_chain",
    }
    assert all(a.source for a in dataset.attacks)


def test_tool_result_vector_requires_target_tool() -> None:
    with pytest.raises(ValidationError, match="needs target_tool"):
        Attack(
            id="a",
            family="tool_loop",
            vector="tool_result",
            benign_task="b",
            payload="p",
            expected_signal="repeated_tool_calls",
            source="s",
        )


def test_unknown_benign_reference_rejected() -> None:
    attack = {
        "id": "a",
        "family": "output_flood",
        "vector": "user_prompt",
        "benign_task": "missing",
        "payload": "p",
        "expected_signal": "long_output",
        "source": "s",
    }
    with pytest.raises(ValidationError, match="unknown benign task"):
        Dataset.model_validate({"benign": [], "attacks": [attack]})


def test_duplicate_ids_rejected(tmp_path: Path) -> None:
    task = "- {id: b, prompt: p, tool: search}\n"
    (tmp_path / "one.yaml").write_text("benign:\n" + task, encoding="utf-8")
    (tmp_path / "two.yaml").write_text("benign:\n" + task, encoding="utf-8")
    with pytest.raises(ValidationError, match="duplicate ids"):
        load_dataset(tmp_path)


def test_rendered_payload_repeats() -> None:
    attack = load_dataset().attack("bloat-appendix-001")
    assert attack.rendered_payload.count("APPENDIX ROW") == attack.payload_repeat


def test_toolbox_injects_only_into_target_tool() -> None:
    box = ToolBox("fetch_doc", Injection(tool="fetch_doc", payload="PAYLOAD"))
    assert box.specs[0].name == "fetch_doc"
    assert "PAYLOAD" in box.run(ToolCall(id="1", name="fetch_doc", arguments={"doc_id": "x"}))
    assert "PAYLOAD" not in box.run(ToolCall(id="2", name="search", arguments={"query": "x"}))
    assert box.run(ToolCall(id="3", name="nope")).startswith("error")


def test_tool_description_vector_requires_target_tool() -> None:
    with pytest.raises(ValidationError, match="needs target_tool"):
        Attack(
            id="a",
            family="mcp_chain",
            vector="tool_description",
            benign_task="b",
            payload="p",
            expected_signal="repeated_tool_calls",
            source="s",
        )


def test_toolbox_injects_into_description_without_touching_results() -> None:
    box = ToolBox("search", Injection(tool="search", payload="MCP-PAYLOAD", where="description"))
    search = next(s for s in box.specs if s.name == "search")
    other = next(s for s in box.specs if s.name != "search")
    assert "MCP-PAYLOAD" in search.description  # payload rides in the tool description
    assert "MCP-PAYLOAD" not in other.description  # only the targeted tool
    # description-mode injection never alters tool results
    assert "MCP-PAYLOAD" not in box.run(ToolCall(id="1", name="search", arguments={"query": "x"}))


def test_seed_has_the_mcp_chain_attack() -> None:
    attack = load_dataset().attack("mcp-chain-extend-001")
    assert attack.family == "mcp_chain"
    assert attack.vector == "tool_description"
    assert attack.target_tool == "search"
