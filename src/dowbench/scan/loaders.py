"""Turn a tool-definition source into the neutral ``ToolSpec`` list the engine scans (ADR 0022).

Loaders are ``dowbench.tool_loaders`` entry points (ADR 0021), so a framework is added without
touching the engine. Built-ins:

- ``openai`` — the OpenAI function-calling schema, the de-facto format LangChain, CrewAI and the
  raw SDK all emit, and the common denominator the scan targets first.
- ``langchain`` — LangChain tool *objects* (``BaseTool``/``StructuredTool``), read by duck typing
  so dowbench needs no LangChain dependency; it is the programmatic path for the framework with
  the largest production footprint.
- ``openapi`` — an OpenAPI 3.x spec, mapping each operation to a tool. A file-based path for the
  very common case of an agent that wraps a REST API.
"""

from __future__ import annotations

import re
from typing import Any

from dowbench import registry
from dowbench.providers.base import ToolSpec

LOADER_GROUP = "dowbench.tool_loaders"


def _empty_schema() -> dict[str, Any]:
    """A fresh empty object schema; never share one instance across tools."""
    return {"type": "object", "properties": {}}


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


def _schema_from_args_schema(args_schema: Any) -> dict[str, Any] | None:
    """A JSON schema from a LangChain tool's ``args_schema`` (a Pydantic model), or None.

    Supports Pydantic v2 (``model_json_schema``) and v1 (``schema``) without importing either.
    Any failure yields None so the caller falls back to an empty schema rather than raising.
    """
    for attr in ("model_json_schema", "schema"):
        method = getattr(args_schema, attr, None)
        if callable(method):
            try:
                result = method()
            except Exception:
                return None
            return result if isinstance(result, dict) else None
    return None


def from_langchain(tools: Any) -> list[ToolSpec]:
    """Parse LangChain tools into ``ToolSpec`` objects, by duck typing (no LangChain dependency).

    Accepts the live tool objects (``BaseTool``/``StructuredTool``: ``.name``, ``.description``,
    and either ``.args`` — the properties mapping — or ``.args_schema`` — a Pydantic model), so a
    LangChain user scans programmatically with ``load_tools("langchain", agent.tools)``. Also
    accepts the serialized dict shape (``name``, ``description``, and one of ``args`` /
    ``args_schema`` / ``parameters``).
    """
    tools_out: list[ToolSpec] = []
    for index, item in enumerate(_as_tool_list(tools)):
        if isinstance(item, dict):
            name = item.get("name")
            description = item.get("description") or ""
            parameters = _langchain_params(
                item.get("parameters"), item.get("args"), item.get("args_schema")
            )
        else:
            name = getattr(item, "name", None)
            description = getattr(item, "description", "") or ""
            parameters = _langchain_params(
                None, getattr(item, "args", None), getattr(item, "args_schema", None)
            )
        if not isinstance(name, str) or not name:
            raise ToolLoadError(f"LangChain tool #{index} has no 'name'")
        if not isinstance(description, str):
            raise ToolLoadError(f"LangChain tool {name!r} has a non-string 'description'")
        tools_out.append(ToolSpec(name=name, description=description, parameters=parameters))
    return tools_out


def _langchain_params(parameters: Any, args: Any, args_schema: Any) -> dict[str, Any]:
    """Resolve a LangChain tool's parameters schema from the first source that fits."""
    if isinstance(parameters, dict):
        return parameters
    if isinstance(args, dict):
        return {"type": "object", "properties": args}
    if args_schema is not None:
        schema = (
            args_schema if isinstance(args_schema, dict) else _schema_from_args_schema(args_schema)
        )
        if isinstance(schema, dict):
            return schema
    return _empty_schema()


_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "options", "head", "trace")


def _resolve_ref(node: Any, root: dict[str, Any], seen: set[str]) -> Any:
    """Follow local ``$ref`` pointers (``#/...``) within ``root``, guarding against cycles.

    Returns the referenced node, or ``{}`` when a pointer cannot be resolved. Only same-document
    references are followed; external refs are left unresolved (treated as empty).
    """
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            return {}
        seen.add(ref)
        target: Any = root
        for raw in ref[2:].split("/"):
            part = raw.replace("~1", "/").replace("~0", "~")
            if isinstance(target, dict) and part in target:
                target = target[part]
            else:
                return {}
        node = target
    return node


def _openapi_properties(
    operation: dict[str, Any], path_params: Any, root: dict[str, Any]
) -> dict[str, Any]:
    """Collect the parameter and request-body property names of one OpenAPI operation."""
    properties: dict[str, Any] = {}
    raw_params = list(path_params) if isinstance(path_params, list) else []
    op_params = operation.get("parameters")
    if isinstance(op_params, list):
        raw_params += op_params
    for param in raw_params:
        param = _resolve_ref(param, root, set())
        if isinstance(param, dict) and isinstance(param.get("name"), str):
            schema = _resolve_ref(param.get("schema", {}), root, set())
            properties[param["name"]] = schema if isinstance(schema, dict) else {}
    body = _resolve_ref(operation.get("requestBody", {}), root, set())
    content = body.get("content") if isinstance(body, dict) else None
    if isinstance(content, dict):
        media = content.get("application/json") or next(
            (v for v in content.values() if isinstance(v, dict)), None
        )
        if isinstance(media, dict):
            schema = _resolve_ref(media.get("schema", {}), root, set())
            body_props = schema.get("properties") if isinstance(schema, dict) else None
            if isinstance(body_props, dict):
                properties.update(body_props)
    return properties


def from_openapi(data: Any) -> list[ToolSpec]:
    """Map each operation of an OpenAPI 3.x spec to a ``ToolSpec``.

    An agent that wraps a REST API has one tool per operation; its name is the ``operationId``
    (or ``method_path``), its description the operation's summary/description, and its parameters
    the query/path parameters plus the JSON request-body properties. Local ``$ref``s are resolved.
    """
    if not isinstance(data, dict) or not isinstance(data.get("paths"), dict):
        raise ToolLoadError("expected an OpenAPI document with a 'paths' object")
    tools: list[ToolSpec] = []
    for path, item in data["paths"].items():
        if not isinstance(item, dict):
            continue
        path_params = item.get("parameters")
        for method in _HTTP_METHODS:
            operation = item.get(method)
            if not isinstance(operation, dict):
                continue
            op_id = operation.get("operationId")
            name = op_id if isinstance(op_id, str) and op_id else f"{method}_{path}"
            name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
            summary = operation.get("summary") or operation.get("description") or ""
            description = summary if isinstance(summary, str) else ""
            properties = _openapi_properties(operation, path_params, data)
            tools.append(
                ToolSpec(
                    name=name,
                    description=description,
                    parameters={"type": "object", "properties": properties},
                )
            )
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
