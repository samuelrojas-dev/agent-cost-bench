# ADR 0012 — Bring your own agent

- Status: accepted
- Date: 2026-09-26

## Context
dowbench can only benchmark its own minimal agent loop. People who build agents want to
know how *their* agent — their model, prompt, loop and defenses — behaves under
denial-of-wallet attacks. That needs the harness to drive an agent it does not own,
without giving up provider-billed costs (ADR 0002) or spend safety (ADR 0003, 0005, 0010).

## Decision
The benchmark owns the environment; the user owns the agent (the AgentDojo split).

- **Interface.** A run config may set `agent: "module:attr"`. `attr` is a class or a
  zero-argument factory returning an object with `run(task) -> str`. The task carries the
  user prompt (with a `user_prompt` attack already rendered in), the benchmark's tools
  (a `tool_result` attack is injected into their output), a suggested system prompt, the
  per-call output limit, and a meter.
- **Metering.** The agent passes every provider response to `task.meter.record(response)`:
  a `google.genai` `GenerateContentResponse`, an `anthropic` `Message`, or a dowbench
  `Response` for the mock. The meter maps usage with the adapters' strict mappers, writes
  the call to `calls.jsonl` at once (ADR 0007), and stops the episode when the ceiling is
  reached by raising `StopEpisode`, a `BaseException`, so an agent's `except Exception`
  cannot swallow it.
- **Scope of a run.** Agent runs use exactly one model (the one the agent calls, for
  pricing) and only the `none` defense: the agent's own defenses are what is measured, so
  comparing defenses means comparing agent variants in separate runs.
- **Ceiling and budget.** The harness no longer sees a call before it is made, so the
  ceiling is checked after each call. The worst case per episode is therefore
  `2 × max_total_tokens`, under an explicit assumption: no single call bills more than
  `max_total_tokens`. The meter checks that assumption on every call; a violation ends the
  episode as `errored` and stops the run (ADR 0007), so it cannot repeat unnoticed.
- **Trust.** Costs come from the provider's usage objects, which the agent hands over. An
  agent that does not record a call hides its cost; the harness cannot detect that. The
  report states that the run is an agent run.

## Alternatives rejected
- **An HTTP endpoint wrapping the agent**: language-agnostic, but tool calls would need a
  callback channel and usage would arrive as untyped JSON. A benchmark-hosted MCP tool
  server is the better language-agnostic path and is planned separately.
- **An LLM API proxy that meters traffic**: sees every call, but must handle each SDK's
  streaming and auth, and would hold the user's API key — the thing ADR 0005 avoids.
- **Self-reported token counts** instead of provider objects: easy to get wrong or fake,
  and loses the strict usage mapping.

## Consequences
- Spend safety for agent runs is weaker than for the built-in loop: bounded by the
  ceiling plus one call, and dependent on the agent recording every call.
- The built-in defenses do not apply to agent runs.
