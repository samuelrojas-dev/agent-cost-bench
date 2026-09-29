# Roadmap

Where dowbench is headed and why. The reasoning, the definition of "1.0", and the ordering
live in [ADR 0019 — Path to 1.0](docs/adr/0019-path-to-1.0.md); this file tracks status.

Each phase ships as its own PR, with an ADR where the decision is non-obvious. Correctness
lands before architecture, which lands before scale, rigor, breadth and release.

| Phase | What | ADR | Status |
|---|---|---|---|
| 0 | This roadmap + ADR 0019; the two known bugs filed as issues; `scripts/` under mypy | [0019](docs/adr/0019-path-to-1.0.md) | ✅ done |
| 1 | Correctness: durable spend + resume semantics for the two known bugs, with regression tests | [0020](docs/adr/0020-durability-and-resume.md) | ✅ done |
| 2 | Extensible plugins via entry points (providers / defenses / attacks) | [0021](docs/adr/0021-plugin-entry-points.md) | 🚧 in progress |
| 3 | Concurrency & scale: parallel episodes honouring rate limits | 0022 | ⏸️ deferred |
| 4 | Observability & resilience: structured logs, error taxonomy, transient retries | 0023 | ⬜ planned |
| 5 | Statistical rigor: bootstrap CIs, sample-size guidance, model×defense comparison, `A ≥ 5` sensitivity | 0024 | ⬜ planned |
| 6 | External validity: OpenAI adapter (offline-tested; real pilot is a separate authorized step) | 0025 | ⏸️ deferred |
| 7 | Maturity & release: `ARCHITECTURE.md`, OSS hygiene, semver + PyPI release, coverage gate | — | 🚧 hygiene subset |

Ordering note: Phases 0 and 1 landed first. Phases 3 (concurrency) and 6 (OpenAI) are
deferred for now. Phase 7's text-only OSS hygiene (SECURITY, CONTRIBUTING, CODE_OF_CONDUCT,
issue/PR templates, CODEOWNERS) is brought forward as a cheap, standalone step; its heavier
parts (`ARCHITECTURE.md`, PyPI release, coverage gate) remain planned. Phases 3–5 start only
after a per-phase time/cost estimate is agreed.

## Known bugs (Phase 1)

- [#36](https://github.com/samuelrojas-dev/agent-cost-bench/issues/36) **Spend not durable
  across a kill** — a provider call billed but not yet written to `calls.jsonl` when the
  process dies is lost from cumulative spend, so `--resume` under-counts and can exceed
  `--budget-usd`.
- [#37](https://github.com/samuelrojas-dev/agent-cost-bench/issues/37) **Transient failure
  drops an episode on resume** — an episode that ends `errored` (e.g. a 429 mid-run) is
  recorded and then skipped by every later `--resume`; transient and terminal errors are not
  distinguished and nothing retries.
