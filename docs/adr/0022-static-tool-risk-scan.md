# ADR 0022 — Static tool-risk scan (the adoption pivot)

- Status: accepted
- Date: 2026-10-01

## Context

The engineering is sound (Phases 0–2, plus OSS hygiene), but a stranger has no reason to use
dowbench today: everything runs against our own harness, mocks and providers. The first-run
experience demands an API key, a config, and our attack/defense vocabulary before it returns
anything. That is backwards for adoption.

The one finding the project already owns is concrete and alarming: in the real pilot,
`bloat-verify-001` amplified episode cost **8.8×**, and of five defenses only `result_cap`
stopped it — the amplification lived entirely in an unbounded tool result (billed input
tokens 5832 → 2349 once capped). That insight does not need a live run to be useful to
someone else: it can be matched **statically** against the *shape* of their tools.

Research into where those tools live (2026):

- **OpenAI Assistants API** is deprecated — sunset **Aug 26, 2026**, no grace period. Not a
  target.
- **LangChain / LangGraph** has the largest production footprint (~141k stars; LangGraph
  ~34.5M downloads/month), and its tools serialize to the OpenAI function-calling schema.
- **CrewAI** is smaller but growing fast; also emits OpenAI-format tools.
- The **OpenAI function-calling tool schema** is the de-facto common denominator every one of
  these emits.

So the highest-leverage target is not a framework runtime — it is the **tool schema**.

## Decision

Add a **static, offline, key-free `dowbench scan`** that reads a user's tool definitions and
reports which ones match the cost-amplification patterns the pilot measured. This becomes the
project's front door; the existing benchmark becomes the *evidence* behind the scan's
heuristics, not the first thing a newcomer must run.

### Principles

1. **Offline and free.** `scan` makes no provider call, needs no key, and spends nothing. It
   reads tool schemas only.
2. **No invented numbers (CLAUDE.md).** A finding says *"matches `bloat-verify-001`, which
   amplified cost 8.8× in our pilot"* — a **pattern match**, never *"your tool will cost
   8.8×"*. The scan predicts a risk shape; it does not measure the user's cost. That honesty
   is stated in the output and the docs.
3. **No new vocabulary at the door.** The report leads with plain language ("this tool's
   result has no size limit") and links the attack/defense terms for those who want them.
4. **Framework-agnostic core, thin loaders.** The engine consumes the neutral `ToolSpec`
   (already in `providers/base.py`: `name`, `description`, `parameters` JSON schema). Loaders
   turn a source into `list[ToolSpec]` and register as `dowbench.tool_loaders` entry points
   (ADR 0021), so a framework is added without touching the engine.

### Heuristics (derived from the real attack families, ADR 0013/0016/0017)

Each rule maps a tool-schema shape to a measured pattern and its fix:

| Rule | Shape detected in the tool schema | Pattern (evidence) | Fix |
|---|---|---|---|
| unbounded-result | no size/▁`maxLength`/pagination bound on the result | `context_bloat` / `bloat-verify-001` (8.8×) | `result_cap` |
| unbounded-pagination | a `page`/`cursor`/`offset` input with no page cap | `tool_loop` | `loop_detect.max_total_tool_calls` |
| result-relay | a large free-text input that echoes prior output (`content`, `context`, `history`) | `growing_arguments` | cap/omit the relayed field |
| no-call-budget (run-level) | many tools, none bounding total calls | `output_flood` / loop | `loop_detect` / `turn_limit` |

Severity is `high` / `medium` / `low`; the set is small, explainable, and each rule cites the
ADR and the pilot. Rules are themselves pluggable later if useful.

### Phased delivery (each phase its own PR, ADR where non-obvious)

| Phase | Scope |
|---|---|
| **S-A** | `load_tools` + `from_openai_tools` loader + the heuristic engine + `dowbench scan <tools.json>` + a Markdown/plain report + offline tests with tool fixtures. The whole value, no key. |
| **S-B** | Rewrite the README hero and the 60-second demo around `scan`; demote "Results (pilot)" to the evidence that justifies the heuristics. |
| **S-C** | `from_langchain` loader (headline adoption) + one more (`from_openapi` or CrewAI), as `dowbench.tool_loaders` plugins with tests. |
| **S-D** | A `dowbench-scan` GitHub Action: run `scan --fail-on high` in the user's CI so a new tool/prompt that reintroduces a cost-amplifying pattern fails the build. This turns "a benchmark I ran" into "a guardrail you use." |

### Out of scope (deliberately)

- **Running the user's agent against a real provider.** That is "another eval harness,"
  expensive, and needs a key — the opposite of the 2-minute promise. It stays an optional,
  documented extension (the existing bring-your-own-agent path, ADR 0012), not part of this
  pivot.
- Exhaustive per-framework support. One common format plus two loaders is enough signal.

## Consequences

- A newcomer gets a useful, specific answer in ~10 seconds with no key: *"this tool could
  blow up your bill, here's why and the fix."* That is the reason-to-care the repo lacked.
- The pilot's single real finding is leveraged, not inflated: the scan is honest about being
  a pattern match, which keeps it within the no-invented-numbers rule.
- Priority shifts: **Phase 3 (concurrency)** and **Phase 5 (statistical rigor)** are internal
  value a stranger does not see in two minutes; both move behind the scan work. Phase 6
  (OpenAI) stays deferred.
- ADR numbers are assigned at creation, so the provisional numbers in ADR 0019's table shift:
  this is 0022; concurrency, observability and statistical rigor take later numbers when
  written.
- New surface to maintain (loaders, rules), but it reuses the entry-point registry (ADR 0021)
  and the neutral `ToolSpec`, so the core stays closed to modification.
