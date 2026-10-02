# Roadmap

Where dowbench is headed and why. The reasoning, the definition of "1.0", and the ordering
live in [ADR 0019 — Path to 1.0](docs/adr/0019-path-to-1.0.md); this file tracks status.

Each phase ships as its own PR, with an ADR where the decision is non-obvious. Correctness
lands before architecture, which lands before scale, rigor, breadth and release.

### Delivered

| Phase | What | ADR | Status |
|---|---|---|---|
| 0 | This roadmap + ADR 0019; the two known bugs filed as issues; `scripts/` under mypy | [0019](docs/adr/0019-path-to-1.0.md) | ✅ done |
| 1 | Correctness: durable spend + resume semantics for the two known bugs, with regression tests | [0020](docs/adr/0020-durability-and-resume.md) | ✅ done |
| 2 | Extensible plugins via entry points (providers / defenses / attacks) | [0021](docs/adr/0021-plugin-entry-points.md) | ✅ done |
| 7a | OSS hygiene: SECURITY, CONTRIBUTING, CODE_OF_CONDUCT, issue/PR templates, CODEOWNERS | — | ✅ done |

### The adoption pivot (delivered)

A stranger needs a reason to care in two minutes, no API key. `dowbench scan` reads their tool
definitions and flags the cost-amplification patterns the pilot measured, and a GitHub Action
fails their CI on a new one. See [ADR 0022](docs/adr/0022-static-tool-risk-scan.md). All four
phases below have landed; the scan is now the project's front door.

| Phase | What | ADR | Status |
|---|---|---|---|
| S-A | `load_tools` + `from_openai_tools` + heuristic engine + `dowbench scan` + offline tests | [0022](docs/adr/0022-static-tool-risk-scan.md) | ✅ done |
| S-B | README hero + 60-second demo rewritten around `scan`; "Results (pilot)" becomes the evidence | [0022](docs/adr/0022-static-tool-risk-scan.md) | ✅ done |
| S-C | `from_langchain` loader + `from_openapi`, as `dowbench.tool_loaders` plugins | [0022](docs/adr/0022-static-tool-risk-scan.md) | ✅ done |
| S-D | `dowbench-scan` GitHub Action: fail a user's CI on a new cost-amplifying pattern | [0022](docs/adr/0022-static-tool-risk-scan.md) | ✅ done |

### Deferred / planned (internal value, after the pivot)

| Phase | What | ADR | Status |
|---|---|---|---|
| 3 | Concurrency & scale: parallel episodes honouring rate limits | tbd | ⏸️ deferred |
| 4 | Observability & resilience: structured logs, error taxonomy, transient retries | tbd | ⬜ planned |
| 5 | Statistical rigor: bootstrap CIs, sample-size guidance, model×defense comparison, `A ≥ 5` sensitivity | tbd | ⏸️ deferred |
| 6 | External validity: OpenAI adapter (offline-tested; real pilot is a separate authorized step) | tbd | ⏸️ deferred |
| 7b | Release: `ARCHITECTURE.md` ✅, semver + PyPI packaging + first publish (v0.1.0 live) ✅, coverage gate (⬜) | tbd | 🚧 in progress |

Ordering note: foundation (0–2), OSS hygiene (7a) and the **scan pivot (S-A…S-D)** have landed —
the scan is the first thing a newcomer touches. Next up are the internal-value phases: concurrency
(3) and statistical rigor (5) are value a stranger does not see in two minutes, and OpenAI (6)
stays deferred. ADR numbers are assigned at creation, so the provisional numbers in ADR 0019's
table shifted (0022 is the scan). Phases 3–5 still start only after a per-phase time/cost estimate
is agreed.

## Known bugs (Phase 1)

- [#36](https://github.com/samuelrojas-dev/agent-cost-bench/issues/36) **Spend not durable
  across a kill** — a provider call billed but not yet written to `calls.jsonl` when the
  process dies is lost from cumulative spend, so `--resume` under-counts and can exceed
  `--budget-usd`.
- [#37](https://github.com/samuelrojas-dev/agent-cost-bench/issues/37) **Transient failure
  drops an episode on resume** — an episode that ends `errored` (e.g. a 429 mid-run) is
  recorded and then skipped by every later `--resume`; transient and terminal errors are not
  distinguished and nothing retries.
