# ADR 0021 — Extensible components via entry points

- Status: accepted
- Date: 2026-09-29

## Context

Phase 2 of [ADR 0019](0019-path-to-1.0.md). Adding a provider, a defense or an attack today
means editing the core:

- `runner/execute.py::build_provider` and `runner/config.py::AVAILABLE_PROVIDERS` hard-code
  the three provider names in `if provider == …` branches.
- `defenses/__init__.py` keeps a hand-written `REGISTRY` dict of the four defense classes.
- `providers/provider_data.py` and `sut.py::_map` dispatch on the provider name to encode
  the replay `provider_data` and to map an SDK response for a bring-your-own-agent run.

A third party cannot add any of these from its own package. For a benchmark whose value is
comparing many attacks, defenses and providers, that boundary should be open.

## Decision

Discover components through `importlib.metadata.entry_points`. A small `registry.py`
(`names`, `load`, `load_all`, with an injectable entry-point source for tests) reads a group
and resolves a name, raising a `LookupError` that lists the registered alternatives on a
typo. The built-ins register in `pyproject.toml` exactly as a plugin would.

Five groups, each a clear contract:

| Group | Entry resolves to | Purpose |
|---|---|---|
| `dowbench.providers` | `build(config, dataset) -> Provider` | construct a provider (its own SDK import-guard lives in the factory) |
| `dowbench.defenses` | a `Defense` subclass | a defense the config can name |
| `dowbench.attacks` | `() -> Iterable[dict]` | extra attack records merged into the dataset |
| `dowbench.codecs` | object with `encode(data)`/`decode(data)` | provider-specific replay `provider_data` (default: identity) |
| `dowbench.sut_mappers` | `map(response) -> (Usage, raw, model_version) | None` | map an SDK response object for an agent-run meter |

A provider is therefore added by registering a `dowbench.providers` factory; if it needs
replay or agent metering it also registers a `dowbench.codecs` and a `dowbench.sut_mappers`
entry. Nothing in the core changes. `examples/plugin_example/` is a minimal external package
(a custom defense and a toy provider) proving this, with a test that registers a fake entry
point in-process and asserts the registry resolves it without any core edit.

The config still validates a provider name and a defense name eagerly, now against the
registry rather than a literal tuple, so an unknown name fails at load time with the list of
what is installed.

## Consequences

- Providers, defenses and attacks are open for extension and closed for modification: a
  plugin ships them in its own package and declares entry points.
- Discovery cost is one `entry_points` scan per group, done once where needed; negligible
  next to a run.
- The built-ins are no longer special — they are the reference plugins, which keeps the
  extension path honest (if a built-in needs something the plugin API lacks, the API is
  extended, not bypassed).
- A provider that omits the optional `codecs`/`sut_mappers` entries still runs and records;
  it only forgoes replay of opaque `provider_data` or agent-run metering, and the identity
  codec keeps JSON-safe data working.
