# ADR 0005 — Provider spend and key safety

- Status: accepted
- Date: 2026-09-26

## Context
Spend and keys are the main risks of running real providers. A review of the Gemini adapter
(ADR 0004) found four gaps: the SDK picks `GOOGLE_API_KEY` over `GEMINI_API_KEY` and can be
switched to Vertex by environment variables, so a run could bill another account; the
client had no timeout; the price table has one rate per model while some providers charge
more above a prompt size; and every install pulled a provider SDK that only real runs need.

## Decision
1. **Key**: each adapter reads exactly one variable (`GEMINI_API_KEY`) and passes it to the
   SDK explicitly, with `vertexai=False`. A missing or blank key raises
   `ProviderSetupError` and `run` exits with code 2. The SDK still logs "Using
   GOOGLE_API_KEY" when both are set; checked against the constructed client, the explicit
   key wins.
2. **No retries** in adapters. A retried call that was billed but not answered is spent twice
   and recorded once. `google-genai` 2.25 does not retry unless `retry_options` is set
   (read in `_api_client.retry_args`); the adapter leaves it unset and a test pins that.
   A failed call stops the run; `run --resume` continues from the last recorded episode.
3. **Timeout** so a hung call cannot block a run forever. Amended by ADR 0006: 600 s, not
   120 s, because a timeout that fires mid-generation leaves a billed, unrecorded call.
4. **Price tiers**: every real price entry must declare `max_prompt_tokens`, the upper edge
   of the tier its rates describe (the context window if pricing is flat). `estimate`
   and `execute` refuse a config whose `ceiling.max_total_tokens` exceeds it: under ADR 0003
   no prompt can be larger than the ceiling, so every call is billed at the listed rates.
5. **Optional SDKs**: provider SDKs are extras (`pip install 'dowbench[gemini]'`). The mock
   path installs none; a missing SDK raises `ProviderSetupError` with the install hint.

## Alternatives rejected
- Model price tiers in the table: more schema and cost code for a case the ceiling already
  rules out. Refusing is simpler and fails safe.
- Retries with backoff for 429 on free tiers: convenient, but risks unrecorded double
  billing. Resume covers the same need.
- A configurable timeout: no current need; a constant is one line to change.
- Retries were also rejected for Anthropic, whose SDK retries twice by default (ADR 0006).

## Consequences
- A timed-out call may still be billed without being recorded. The run stops at that point,
  so at most one call per run is unrecorded, and it is bounded by the ceiling.
- Free-tier 429s stop the run; the operator resumes it.
- The tier check relies on providers that can count tokens before sending (mock, Gemini).
  An adapter whose `count_tokens` returns `None` needs its own tier argument in its ADR.
