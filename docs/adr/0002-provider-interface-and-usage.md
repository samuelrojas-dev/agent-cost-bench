# ADR 0002 — Provider-neutral interface and normalized usage

- Status: accepted
- Date: 2026-09-25

## Context
Claude and Gemini expose different request shapes and report usage with different fields
and different inclusion rules (for example, whether cached or reasoning tokens are part of
another counter). Cost comparisons are only valid if every adapter maps usage the same way.

## Decision
- The agent loop and defenses talk only to `dowbench.providers.base`:
  `Request` (model, system, messages, tools, max_tokens) → `Response` (text, tool calls,
  stop reason, `Usage`, latency, `raw`). `Provider.count_tokens` returns `None` when the
  provider cannot count before sending.
- Each adapter wraps the provider's **official SDK**; no OpenAI-compatible shims.
- `Usage` fields are disjoint, so the billed total is their sum:
  - `input_tokens`: uncached input only.
  - `cache_read_tokens`, `cache_write_tokens`: cached input, priced separately.
  - `output_tokens`: visible output, excluding reasoning.
  - `reasoning_tokens`: thinking/reasoning tokens, priced at the output rate. When a
    provider does not report reasoning separately, it stays inside `output_tokens` and
    this field is 0; adapters must never count the same tokens twice.
- `Response.raw` keeps the provider's original usage object for auditing.
- Providers declare `simulated`; results from simulated providers are never published.

## Consequences
- Adapter tests (Phase 2) must cover every usage field, including cache and reasoning, and
  fail on unknown fields so SDK changes are noticed.
- Prices live in a versioned table with a source URL and retrieval date per entry.
