# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-02

First public release — `pip install dowbench`
([pypi.org/project/dowbench](https://pypi.org/project/dowbench/)).

### Added

- **PyPI packaging (Phase 7b).** The version is single-sourced from `src/dowbench/__init__.py`
  (hatchling dynamic version, **0.1.0**); `project.urls` gains Homepage and Changelog; an sdist file
  set is declared. A `release` workflow (`.github/workflows/release.yml`) builds the sdist/wheel,
  runs `twine check`, and publishes via **Trusted Publishing (OIDC)** — to TestPyPI on a manual
  dispatch (dry run) and to PyPI on a `vX.Y.Z` tag, with no stored token. Release process documented
  in [`docs/RELEASING.md`](docs/RELEASING.md). The README now carries a PyPI version badge and leads
  with `pip install dowbench`; the `git clone` + editable install is unchanged.
- **`dowbench scan`** — an offline, key-free static tool-risk scan that reads tool definitions
  and flags the cost-amplification shapes the pilot measured (`unbounded-result`,
  `unbounded-pagination`, `result-relay`, `no-call-budget`), each tied to an attack family and
  its fix. Findings are pattern matches, not cost predictions. Built on a neutral `ToolSpec`
  with a built-in OpenAI function-calling loader; loaders are `dowbench.tool_loaders` plugins.
  Phase S-A, [ADR 0022](docs/adr/0022-static-tool-risk-scan.md) (#43).
- `examples/agent_tools.json` — a small OpenAI-format toolset used by the README's
  60-second scan demo.
- Two more `dowbench.tool_loaders` for `dowbench scan`: **`langchain`** (reads LangChain tool
  objects by duck typing — `.name`/`.description`/`.args`/`.args_schema` — so no LangChain
  dependency, plus its serialized dict shape) and **`openapi`** (maps each OpenAPI 3.x operation
  to a tool, resolving local `$ref`s). Phase S-C,
  [ADR 0022](docs/adr/0022-static-tool-risk-scan.md).
- **`dowbench scan --fail-on high|medium|low`** — exits non-zero when a finding reaches that
  severity (default `none`, report-only), and a **`dowbench-scan` composite GitHub Action**
  (`.github/actions/dowbench-scan`) wrapping it so a cost-amplifying pattern fails a user's CI.
  A `scan-action` CI job dog-foods the Action against the example toolset. Phase S-D,
  [ADR 0022](docs/adr/0022-static-tool-risk-scan.md).
- **Results (pilot)** section in the README, reporting the first real findings from the
  maintainer's local `gemini-3.5-flash-lite` runs (pilot v2 — 10 attacks × 5 defenses; and a
  focus run of `bloat-verify-001` × 5 defenses, 5 repeats). Numbers are attributed to those
  local runs and marked as not reproduced in CI (#26).
- Published the replay files for both runs under `results/cassettes/` (`cassette.jsonl`,
  `run.json`, `episodes.jsonl`, `summary.json`), so `dowbench replay` reproduces each
  `summary.json` byte for byte offline — no key, no spend (#27).
- Pilot v1 recorded in the README as an antecedent: the earlier 4 attacks × 4 defenses run
  (ASR 0 % in every cell) whose null result motivated the amplifying attacks of ADR 0016
  (#28).
- `dowbench estimate` output for `configs/pilot-gemini-v2.yaml` added to the README's
  Results (pilot) section — its worst-case budget envelope (75 episodes, 1.2M tokens / $3.00,
  900 requests across 2 days), noted as a ceiling, not the billed cost (#31).

### Changed

- README rewritten around `dowbench scan` as the front door: a new hero and a 60-second scan
  demo lead, "Run the full benchmark" follows, and the former "Results (pilot)" section is
  reframed as "The evidence behind the scan" — the measured findings that justify the scan's
  heuristics. No pilot number changed. Phase S-B, [ADR 0022](docs/adr/0022-static-tool-risk-scan.md).
- README replay commands now point at the published `results/cassettes/` directories instead
  of only showing a hash to verify against (#27).
- Consolidated the two overlapping README result sections: "Replaying a real run" is now a
  brief pointer to "Results (pilot)", removing the duplicated, stale pilot narrative (#28).
- README status line now reflects that preliminary pilot findings are published, framed as
  early pilots (`n = 1`–`5`, directional not settled) rather than "no results yet" (#29).
