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
  A token covers at least one byte, so the system and tool terms are upper bounds; the
  margin covers the chat template. In the smoke run the bound was 280 for a 65-token prompt.
  Over-counting only makes the ceiling censor earlier; the post-call check of ADR 0003
  still catches any residual under-count.
- `ToolCall.provider_data` carries opaque per-provider data (here the thought signature)
  back to the same provider. It is excluded from serialization and from `signature()`, so
  loop detection and the mock provider are unaffected.
- Usage mapping (ADR 0002): `input = prompt − cached + tool_use_prompt`,
  `cache_read = cached`, `output = candidates`, `reasoning = thoughts`; the adapter fails if
  the sum differs from `total_token_count` or an unknown usage field appears. The smoke run
  matched on every call. It also showed `max_output_tokens` bounds visible plus thinking
  tokens (64 → 2 + 58), which the ceiling formula relies on.
- No Gemini prices are added until they are copied from the official pricing page with
  its URL and retrieval date; `estimate` and `run` refuse to start without them.

## Consequences
- The ceiling is conservative by a few hundred tokens per call on Gemini.
- Re-check the counting limitation when upgrading `google-genai`; if the Developer API
  gains full-request counting, switch to it and drop the bound.
