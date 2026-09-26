# Provider response fixtures

These JSON bodies are **hand-written** to match the response types of the official SDKs
(`anthropic` 1.8, `google-genai` 2.25). They are not recordings of real API calls: ids and
signatures are fake and every token count is an arbitrary test input, not a measurement.

Tests serve them through an in-process HTTP transport, so each test exercises the real SDK
request serialization and response parsing without opening a socket.

Phase 3 adds sanitized recordings of real calls next to these; results are only ever
computed from those.
