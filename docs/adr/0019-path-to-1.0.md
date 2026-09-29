# ADR 0019 — Path to 1.0

- Status: accepted
- Date: 2026-09-29

## Context

dowbench works: a CLI, 14 attacks, 5 defenses, a replay/cassette system with hash
verification, a CI matrix (Ubuntu/Windows × Python 3.11/3.13) with gitleaks, one published
real pilot (`bloat-verify-001`, ~8.8× amplification, `result_cap` the only effective
defense), and eighteen prior ADRs. Test coverage is ~96 % and mypy already runs `strict`.

That is a working benchmark, not yet a project a staff engineer would point at as serious
work. For **this** project the gap to "1.0" is not more attacks or features; it is:

- **(a) Reliable correctness** — a cost benchmark that can lose recorded spend or drop an
  episode is not trustworthy, whatever its numbers say.
- **(b) Clean extension boundaries** — a third party should be able to add an attack, a
  defense or a provider without editing the core.
- **(c) Scale with statistical rigor** — comparisons must hold at more than `n = 5`, with a
  defensible design, not a single fixed threshold asserted without analysis.
- **(d) Operability** — structured logs, run metrics, and sane handling of transient
  provider errors.
- **(e) Project maturity** — architecture documented as a whole (not eighteen loose ADRs),
  a real release, and the open-source hygiene contributors expect.

Two correctness bugs are known and, until now, untracked (filed as issues alongside this
ADR):

1. **Spend is not durable across a kill.** `budget.spent()` sums `calls.jsonl`, which is
   appended per call, but there is no `fsync` and a window exists between a provider
   response (already billed) and its record reaching disk. A process killed in that window
   loses that call from the cumulative spend, so a later `--resume` under-counts what was
   already spent and can exceed `--budget-usd`.
2. **A transient failure permanently drops an episode on resume.** In
   `runner/execute.py`, an episode that ends `errored` (e.g. a 429 mid-run) is written with
   `append_episode` and then raises; `--resume` builds its "done" set from every episode id
   that has a record, so the errored episode is skipped forever. Transient (429/503/timeout)
   and terminal errors are not distinguished, and there is no retry/backoff anywhere.

## Decision

Adopt a phased path to 1.0. Each phase ships as its own PR (with an ADR where the decision
is non-obvious) and is tracked in [`ROADMAP.md`](../../ROADMAP.md). Correctness comes before
architecture, which comes before scale, rigor, breadth and release — in that order, because
each enables the next (parallel execution is what makes a large-`n` statistical phase
worth running).

| Phase | Scope | ADR |
|---|---|---|
| **0 — Baseline** | This ADR + `ROADMAP.md`; file the two bugs as issues; put `scripts/` under mypy (ruff already covers it). | 0019 |
| **1 — Correctness (the two bugs)** | Durable spend accounting and resume semantics; classify transient vs terminal outcomes so resume re-runs transient failures; bounded retry/backoff. Regression tests for a mid-call kill and a mid-episode 429. | 0020 |
| **2 — Extensible plugins** | Providers, defenses and attacks resolved through `importlib.metadata` entry points (`dowbench.providers` / `.defenses` / `.attacks`); built-ins registered the same way; the core's `if provider == …` branches removed. An example third-party plugin proves "no core edit", with a test. | 0021 |
| **3 — Concurrency & scale** | Bounded parallel episode execution with the existing `RateLimiter` as the shared gate; atomic budget checks; ordered writes so `summary.json` stays byte-for-byte reproducible. | 0022 |
| **4 — Observability & resilience** | Structured logging (stdlib, optional JSON), run/episode/call events, an error taxonomy, transient retries surfaced in logs and metrics. | 0023 |
| **5 — Statistical rigor** | Bootstrap CIs on amplification, sample-size / power guidance, model×defense comparison (paired, multiple-comparison correction), and a sensitivity analysis of the `A ≥ 5` threshold — justify it or report it as a curve. | 0024 |
| **6 — External validity** | An OpenAI adapter (usage mapping, pinned endpoint, key safety per ADR 0005) with a mock and offline tests. A real OpenAI pilot is a separate, authorized step, not part of the code PR. | 0025 |
| **7 — Maturity & release** | `ARCHITECTURE.md` (component diagram + design synthesis); SECURITY / CONTRIBUTING / CODE_OF_CONDUCT / issue+PR templates / CODEOWNERS; semver policy and a PyPI release workflow (trusted publishing); a coverage gate in CI. | — |

Priority ordering agreed with the maintainer: **Phases 0 and 1 land first, no exceptions**,
then Phase 2. Phases 3–5 proceed only after a per-phase time/cost estimate is approved.
Phase 6 (OpenAI) and Phase 7 (release) come last, except that the cheap, text-only parts of
Phase 7 (SECURITY, CONTRIBUTING, templates) may be brought forward in parallel when they
cost little.

### What this ADR deliberately does *not* include

- **"Harden typing/lint" as a phase.** mypy is already `strict` over `src`, `tests` and
  `examples`, and coverage is ~96 %. The only gap was `scripts/`, closed in this phase. We
  do not chase a coverage percentage as a goal; new tests exist to pin correctness
  (regressions) and statistics.
- **A real provider pilot inside any code PR.** Every real run stays gated on explicit
  maintainer authorization with `--budget-usd` (CLAUDE.md). Adapters are built and tested
  offline against the mock.

## Consequences

- The repository gains a single, reasoned narrative of where it is going, and each phase is
  reviewable in isolation.
- The two known bugs stop being folklore: they are issues with a committed plan and will
  ship with regression tests before Phase 2.
- Some phases (3–5) are open-ended; gating them on an explicit estimate keeps the work from
  quietly expanding. If a phase turns out not to earn its cost, `ROADMAP.md` records that it
  was dropped and why, rather than leaving it half-done.
