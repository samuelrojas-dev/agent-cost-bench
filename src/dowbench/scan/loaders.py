"""Turn a tool-definition source into the neutral ``ToolSpec`` list the engine scans (ADR 0022).

Loaders are ``dowbench.tool_loaders`` entry points (ADR 0021), so a framework is added without
touching the engine. The built-in ``openai`` loader reads the OpenAI function-calling schema —
the de-facto format LangChain, CrewAI and the raw SDK all emit — which is why it is the common
denominator the scan targets first.
"""

from __future__ import annotations

from typing import Any

from dowbench import registry
from dowbench.providers.base import ToolSpec

LOADER_GROUP = "dowbench.tool_loaders"


class ToolLoadError(ValueError):
    """A tool-definition source could not be parsed into ``ToolSpec`` objects."""


def _as_tool_list(data: Any) -> list[Any]:
    """Accept a bare list of tools, or a mapping with a ``tools`` list."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("tools"), list):
        return list(data["tools"])
    raise ToolLoadError(
        "expected a JSON array of tools or an object with a 'tools' array, "
        f"got {type(data).__name__}"
    )


def from_openai_tools(data: Any) -> list[ToolSpec]:
    """Parse OpenAI function-calling tool definitions into ``ToolSpec`` objects.

    Handles the nested Chat Completions shape ``{"type": "function", "function": {...}}``, the
    flattened Responses shape ``{"type": "function", "name": ...}``, and a bare function object
    ``{"name": ..., "parameters": {...}}``. ``description`` defaults to empty and ``parameters``
    to an empty object schema, matching how the SDKs treat omitted fields.
    """
    tools: list[ToolSpec] = []
    for index, item in enumerate(_as_tool_list(data)):
        if not isinstance(item, dict):
            raise ToolLoadError(f"tool #{index} is not an object")
        raw_fn = item.get("function")
        fn = raw_fn if isinstance(raw_fn, dict) else item
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            raise ToolLoadError(f"tool #{index} has no 'name'")
        description = fn.get("description") or ""
        parameters = fn.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}}
        if not isinstance(description, str):
            raise ToolLoadError(f"tool {name!r} has a non-string 'description'")
        tools.append(ToolSpec(name=name, description=description, parameters=parameters))
    return tools


def load_tools(
    name: str, data: Any, *, source: registry.EntryPointSource | None = None
) -> list[ToolSpec]:
    """Load the ``name`` tool loader from the registry and apply it to ``data``.

    ``source`` defaults to the installed entry points; tests inject a fake one. Raises
    ``LookupError`` (listing the registered loaders) for an unknown name, and ``ToolLoadError``
    when the source is malformed.
    """
    loader = (
        registry.load(LOADER_GROUP, name)
        if source is None
        else registry.load(LOADER_GROUP, name, source=source)
    )
    result = loader(data)
    if not isinstance(result, list) or not all(isinstance(t, ToolSpec) for t in result):
        raise ToolLoadError(f"loader {name!r} did not return a list of ToolSpec")
    return result
