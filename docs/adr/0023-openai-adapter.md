# ADR 0023 — OpenAI provider adapter

- Status: accepted
- Date: 2026-10-02

## Context

dowbench benchmarks tool-using agents against real providers; Gemini (ADR 0004) and Anthropic
(ADR 0006) adapters exist. Phase 6 of [ADR 0019](0019-path-to-1.0.md) adds OpenAI for external
validity. The adapter must satisfy the same rails as the others: strict usage mapping with no
invented numbers (ADR 0002, 0007, 0008), key safety (ADR 0005), offline replay (ADR 0015), and
the safety ceiling (ADR 0003). This ADR records the three decisions specific to OpenAI.

The adapter ships **offline-tested only**; a real pilot is a separate, explicitly authorized step.

## Decision

### 1. Usage mapping (ADR 0002)

OpenAI Chat Completions `usage` maps to the neutral `Usage` disjoint counters, their sum being
the billed total:

| `Usage` field | from OpenAI |
|---|---|
| `cache_read_tokens` | `prompt_tokens_details.cached_tokens` |
| `input_tokens` | `prompt_tokens − cached_tokens` (uncached prompt only) |
| `reasoning_tokens` | `completion_tokens_details.reasoning_tokens` (billed at the output rate) |
| `output_tokens` | `completion_tokens − reasoning_tokens` |
| `cache_write_tokens` | `0` — OpenAI's prompt caching charges no separate write; recorded as 0 because there is no price, not as a measured value |

As with Anthropic, anything the price table cannot price **fails** (`UsageMappingError`, ADR
0007) rather than being mispriced: an unknown top-level usage key, audio tokens, non-zero
accepted/rejected prediction tokens, or `total_tokens` not equal to `prompt + completion`.

### 2. No replay codec needed (ADR 0015)

The assistant turn kept for replay (`Response.provider_data`) is the assistant message as a
**plain JSON dict** (`role`, `content`, `tool_calls`). Plain JSON survives the cassette's
`json.dumps` → base64 round-trip unchanged, so OpenAI registers **no `dowbench.codecs` entry** —
like Anthropic, and unlike Gemini (which stores an SDK `types.Content` object). The `replay-verify`
job therefore needs no OpenAI SDK to rebuild an OpenAI run.

### 3. Post-call safety ceiling for a provider that cannot pre-count (ADR 0003)

Gemini and Anthropic expose a server-side token count, so the ceiling is enforced *before* each
call. **OpenAI has no token-counting endpoint.** Rather than count locally (tiktoken is an
approximation that could under-count message/tool-schema framing and silently weaken the
ceiling), the adapter declares it does not count: `count_tokens` returns `None` and
`counts_tokens = False`.

The episode loop already re-checks the cumulative total *after* each call and censors the
episode when it exceeds `max_total_tokens` (ADR 0003). For a non-counting provider the loop
skips the pre-call gate and relies on that post-call check plus the per-call `max_tokens` cap —
exactly the "checked after each call" path a bring-your-own-agent run already uses (ADR 0012).
Consequently the **worst case is ~2× the ceiling per episode** (one call may overshoot), and the
estimate and budget guard account for that `2×` for any provider that does not count tokens,
just as they already do for agent runs. This keeps `--budget-usd` a hard bound (ADR 0010) — the
estimate never under-counts — at the cost of a looser (but honest) ceiling for OpenAI.

A provider that *claims* to count (`counts_tokens = True`) but returns `None` is still a bug and
still raises: the change only recognizes a provider that declares it cannot.

## Consequences

- One new optional dependency `dowbench[openai]` (the `openai` SDK). The adapter imports it
  lazily, so loading the module to read its capability (for the offline estimate) needs no SDK.
- Key safety mirrors Anthropic: only `OPENAI_API_KEY` is read, the official base URL is pinned,
  `max_retries=0`, and a set `OPENAI_BASE_URL` is refused so the key cannot be redirected.
- OpenAI episodes carry a 2× worst-case budget envelope; a priced run still refuses to start
  unless that worst case fits the budget.
- The `Provider` protocol gains a `counts_tokens` flag (default `True`); the three existing
  providers set it `True`, so their behavior and published numbers are unchanged.
