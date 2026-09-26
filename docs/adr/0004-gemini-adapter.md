# ADR 0004 — Gemini adapter: token counting bound and thought signatures

- Status: accepted
- Date: 2026-09-26

## Context
ADR 0003 needs `count_tokens` to return the input tokens of the whole request before sending
it. The `google-genai` SDK (2.25) rejects `system_instruction` and `tools` in `count_tokens`
on the Gemini Developer API; it only accepts them on Vertex. Counting only the messages
would undercount and let an episode exceed `max_total_tokens` silently.

A smoke run on 2026-09-26 (`gemini-3.5-flash`, `gemini-3.5-flash-lite`, a few hundred tokens,
not a benchmark result) also showed that Gemini 3 rejects a follow-up turn whose
`functionCall` parts lack the `thought_signature` the model returned.

## Decision
- `GeminiProvider.count_tokens` = SDK count of the messages + UTF-8 bytes of the system
  prompt + UTF-8 bytes of the tool declarations as JSON + 64 tokens of template margin.
  Over-counting only makes the ceiling censor earlier; the post-call check of ADR 0003
  still catches any residual under-count, bounded by one call.
- `ToolCall.provider_data` carries opaque per-provider data (here the thought signature)
  back to the same provider. It is excluded from serialization and from `signature()`, so
  loop detection and the mock provider are unaffected.
- Usage mapping (ADR 0002): `input = prompt − cached + tool_use_prompt`,
  `cache_read = cached`, `output = candidates`, `reasoning = thoughts`; the adapter fails if
  the sum differs from `total_token_count` or an unknown usage field appears.
- No Gemini prices are added until they are copied from the official pricing page with
  its URL and retrieval date; `estimate` and `run` refuse to start without them.

## Alternatives rejected
- Count only the messages: under-counts system and tools, so the ceiling could be exceeded
  silently.
- `count_tokens` returns `None`: allowed by ADR 0003, but then the pre-call check is off and
  a single call can overshoot the ceiling by its whole input plus `max_tokens`.
- Call the REST `countTokens` endpoint with a full `generateContentRequest`: exact, but
  bypasses the official SDK (ADR 0002) through private client internals.
- Learn the system+tools overhead from the first real call: near exact, but more state and
  no bound before that first call.

## Evidence
Verified (5 calls, 2 models, 2026-09-26; a few hundred tokens, not a benchmark result):
- The mapped sum equals `total_token_count` on every call, with and without thoughts.
- The count was an upper bound on every call (e.g. 280 counted for a 65-token prompt).
- Gemini 3 returns HTTP 400 on a follow-up turn whose `functionCall` lacks its
  `thought_signature`; with the signature echoed back the turn succeeds.
- `gemini-3.5-flash` with `max_output_tokens=64` stopped at `MAX_TOKENS` after 2 visible +
  58 thinking tokens.

Assumed, not verified:
- Every Gemini token covers at least one UTF-8 byte, so bytes bound tokens. It holds for
  byte-fallback tokenizers, but Google does not document it for Gemini.
- 64 tokens cover the chat template around system and declarations. Chosen, not measured;
  it only needs to cover what the byte terms do not.
- `max_output_tokens` bounds visible + thinking tokens on every Gemini model. Observed on one
  call of one model. If false, the pre-call check of ADR 0003 is no longer a bound and only
  the post-call check (overshoot of one call) protects the ceiling.

## Consequences
- The ceiling is conservative by a few hundred tokens per call on Gemini.
- Before the first paid run, re-check the third assumption on every model in the config.
- Re-check the counting limitation when upgrading `google-genai`; if the Developer API
  gains full-request counting, switch to it and drop the bound.
