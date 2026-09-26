# ADR 0006 — Anthropic adapter and verbatim replay of assistant turns

- Status: accepted
- Date: 2026-09-26

## Context
The Anthropic adapter must meet ADR 0002, 0003 and 0005. Three things differ from Gemini:
- Thinking blocks must be passed back unchanged within a tool loop, and on the newest
  models editing an earlier turn invalidates them. Some models (Opus 5.5, Fable 5.1)
  cannot disable thinking. `ToolCall.provider_data` (ADR 0004) cannot carry this: the
  blocks belong to the assistant turn, not to a call, and their order matters.
- The SDK retries twice by default and resolves credentials from several sources
  (`ANTHROPIC_AUTH_TOKEN`, profiles, federation) and the base URL from
  `ANTHROPIC_BASE_URL`.
- `output_tokens` includes thinking; `output_tokens_details.thinking_tokens` gives an
  approximate breakdown.

## Decision
1. **Verbatim replay.** `Response.provider_data` holds the assistant turn exactly as the
   provider returned it; the loop copies it to the assistant `Message`, and the same
   adapter sends it back unchanged. It is never serialized. Gemini moves to the same
   mechanism (its model `Content`, with thought signatures on any part), and
   `ToolCall.provider_data` is removed. One mechanism for both providers.
2. **API defaults are what we measure.** No `thinking`, `effort`, `betas` or server-side
   `fallbacks` are sent. Fallbacks would let a different model answer a refused request and
   bill at its rates, so a refusal is recorded as `refusal` instead. Results must name the
   model ID; its default thinking behavior is part of what is measured.
3. **Client.** Key only from `ANTHROPIC_API_KEY`, passed explicitly (the SDK then ignores
   every other credential source); `base_url` fixed to `https://api.anthropic.com`;
   `max_retries=0`; a set `ANTHROPIC_CUSTOM_HEADERS` refuses to build, since it can enable
   betas. The SDK's default timeout is kept: with it, the SDK refuses before sending a
   non-streaming request too long to finish (read in `_calculate_nonstreaming_timeout`).
4. **Usage.** `input = input_tokens`, `cache_read = cache_read_input_tokens`,
   `cache_write = cache_creation_input_tokens`, `reasoning = thinking_tokens`,
   `output = output_tokens − thinking_tokens`. The split is approximate; the sum, which is
   what is billed, is exact. The adapter fails on what the price table cannot price:
   service tier other than standard, `inference_geo` other than global (US-only is 1.1x),
   1-hour cache writes (2x input), non-zero server tool use, and unknown usage fields.
5. **Counting.** `count_tokens` sends the same model, system, tools and messages as the
   request. Anthropic documents the result as an estimate that "might differ by a small
   amount"; the post-call check of ADR 0003 bounds the residual. No margin is added, since
   any number would be invented.
6. **Prices.** `claude-sonnet-5` and `claude-opus-5` from the official pricing page
   (2026-09-26), `max_prompt_tokens` = 1M because that page states Claude 4.6 and later are
   priced flat across the 1M window. Other models are added the same way when needed.
7. **Timeout, amending ADR 0005.** A timeout that fires while the server keeps generating
   leaves a billed, unrecorded call. Gemini's timeout goes from 120 s to 600 s, matching
   Anthropic's default.

## Alternatives rejected
- Rebuild the assistant turn from text and tool calls: loses thinking blocks and their
  order, which Anthropic rejects or silently drops.
- Keep `ToolCall.provider_data` for Gemini and add a second mechanism for Anthropic: two
  ways to do one thing.
- Enable server-side fallbacks as the SDK guide recommends for applications: right for a
  product, wrong for a benchmark that must measure one model.
- Streaming: needed only for large `max_tokens`; the SDK refuses those without streaming,
  which fails safe. Revisit if a ceiling needs more than the non-streaming limit.

## Evidence
Verified without calling the API (no real call has been made to Anthropic):
- SDK 1.8.0 source: explicit `api_key` disables environment credentials; `base_url`
  argument beats `ANTHROPIC_BASE_URL`; default `max_retries` is 2; `output_tokens` is
  documented as "the inclusive, authoritative total used for billing".
- Unit test on the real SDK client: with `ANTHROPIC_AUTH_TOKEN` and
  `ANTHROPIC_BASE_URL=https://attacker.example` set, the client uses only the given key,
  the official URL and no retries.
- Pricing page and token-counting page, read 2026-09-26.
- Gemini accepts its model `Content` replayed verbatim: run `smoke-gemini` (commit 324d17f,
  `gemini-3.5-flash-lite`, 2026-09-26) made a tool call on turn 1 and answered on turn 2
  after the replay (159 + 16 and 249 + 18 tokens). Whether that turn carried a thought
  signature was not recorded.

Assumed, not verified:
- `max_tokens` bounds thinking plus visible output on every model (documented behaviour of
  the Messages API; not observed here).
- The API reports `inference_geo` as absent or `"global"` for default routing. If it reports
  something else, every call fails loudly, which is safe but blocks runs.
- `thinking_tokens` is close enough to split reasoning from output for analysis. Costs do
  not depend on it.

## Consequences
- Defenses must not rewrite assistant messages: a provider replays its own turn and would
  ignore the edit. No current defense does; a new one that needs to must drop
  `provider_data` explicitly and accept that thinking is lost.
- Before the first paid run on each provider, one approved smoke call per model should
  confirm the assumptions above.
