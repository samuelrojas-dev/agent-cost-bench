# ADR 0009 — Served model version and sanitized request bodies

- Status: accepted
- Date: 2026-09-26

## Context
The Gemini smoke run (ADR 0008) could not answer two audit questions from its files: which
model version actually served each call (only the requested ID was stored), and what the
replayed assistant turn looked like on the wire (for example, whether it carried a thought
signature). Both are needed before publishing any real result.

## Decision
- `Response.model_version` is the version the provider reports (Gemini `model_version`,
  Anthropic `message.model`). It is stored per call in `calls.jsonl`, and `run.json` gets
  `served_model_versions` (requested model → versions seen), rebuilt from `calls.jsonl` at
  the end of every invocation, also when the run stops early, so it covers resumes.
- `Response.request_body` is the JSON body the adapter handed to the SDK: Gemini
  `{model, contents, config}` (bytes such as thought signatures as base64), Anthropic the
  Messages API parameters (replayed blocks dumped as JSON). It is written per call to
  `requests.jsonl` with the same `episode_id`, `attempt` and `turn` as `calls.jsonl`.
- Requests pass through one sanitizer in the store before reaching disk: fields named like
  credentials or headers (`api_key`, `key`, `authorization`, `x-api-key`,
  `x-goog-api-key`, `headers`, `http_options`, tokens) are dropped at any depth, and values
  shaped like Google (`AIza…`, `AQ.…`) or Anthropic (`sk-ant-…`) keys are masked.

## Why the body, not the HTTP request
Recording at the HTTP layer would capture headers, where both SDKs put the key, and would
need a recording library per SDK. The adapter-level body never contains the key and needs
no dependency. The sanitizer is a second line of defense, not the first.

## Limitations
- `requests.jsonl` is the request side only: it is an audit log, not a replayable cassette.
  Responses are summarized in `calls.jsonl`, not stored whole.
- It is the body given to the SDK, not the bytes on the wire; the SDK's own request
  transformation (field renaming, defaults) is not captured.
- Token-count requests are not recorded (they are not billed).
- A call that fails validation after being billed (`errored`, ADR 0007) keeps its usage but
  not its request body.
- Bodies repeat the whole conversation every turn, so the file grows with turns squared;
  fine at the current ceilings, to revisit for long episodes.
