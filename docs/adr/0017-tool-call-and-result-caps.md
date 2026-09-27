# ADR 0017 — Total tool-call cap and per-result size cap

- Status: accepted
- Date: 2026-09-26

## Context
ADR 0016 found two gaps in the defenses at the pilot's settings:

- `loop_detect` counts identical calls and calls per tool. An agent that alternates tools,
  with a new argument each time, makes up to `max_calls_per_tool × tools` calls before any
  check fires (4 + 4 = 8 with two tools), which fits under the 12-turn ceiling.
- No defense bounds the size of a single tool result. A large result is re-billed on every
  later turn; one extra call over a padded result can cross 5x while staying under every
  current limit (`bloat-verify-001`).

## Decision
1. **`loop_detect.max_total_tool_calls`** (optional, default unset). When set, the episode
   aborts once the total number of tool calls, across all tools, exceeds it. It is checked
   in `after_call`, like the other two counters, so the call that crosses the cap is billed
   and never executed. Unset keeps `loop_detect` exactly as before, so existing configs,
   episode ids and results are unchanged.
2. **`result_cap`**, a new defense with `max_result_chars` (default 2000). In
   `on_tool_result` it keeps the first `max_result_chars` characters of each result and
   appends `[result_cap: N characters removed]`, so the model knows the result was cut.
   - **Characters, not tokens:** a character cap means the same for every provider, needs no
     token count (a real `count_tokens` would be a network call) and is deterministic. At
     roughly 4 characters per token the default is about 500 tokens.
   - **Head, not tail:** the answer to a benign task is at the head of the simulated tool's
     result; injected payloads are appended after it.
   - **Truncate, not abort:** the task can still be answered, so the benign cost of the
     defense is a shorter context, not a failed task. Its benign overhead is still measured
     as for any defense.

A defense spec builds one defense, so combining `result_cap` with another defense in one
arm is not supported; that is unchanged here.

## Consequences
- Both are tested with the mock provider only. Whether they stop the ADR 0016 attacks on a
  real model needs a real run with explicit permission.
- `result_cap` also cuts a legitimately long result. Its benign completion rate and overhead
  are reported like any other defense; they are not assumed to be free.
- The cap's placement (head) is known to an attacker who controls the source: a payload
  placed before the answer survives it. That is the fully controlled source mode that
  ADR 0016 leaves as an open question.
