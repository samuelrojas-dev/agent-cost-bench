# ADR 0013 — Malicious MCP tool source as a `tool_description` attack

- Status: accepted
- Date: 2026-09-26

## Context
"Beyond Max Tokens" (arXiv:2601.10955) shows a malicious MCP server driving an agent's cost
up by extending its tool-calling chain. The instruction rides in the metadata the server
advertises — the tool's declared description — which the model reads before it ever calls
the tool. dowbench's two existing vectors, `user_prompt` and `tool_result`, do not cover
this surface: both inject into content the agent receives after acting, not into the tool
catalog it is offered up front.

This ADR adds that surface to the benchmark. It does not build, ship, or describe how to
operate a real MCP server against third parties: the attack is one synthetic, low-power
payload evaluated entirely offline against the mock provider, exactly like every other
`seed.yaml` entry, purely to measure which defenses contain it.

## Decision
- New attack **family** `mcp_chain` and **vector** `tool_description`. A `tool_description`
  attack names a `target_tool` (validated like `tool_result`); its payload is appended to
  that tool's declared description in the toolbox the agent sees, and never to tool results.
- `Injection` gains `where: "result" | "description"` (default `"result"`), so one
  mechanism carries both surfaces. The mock provider now reads tool descriptions as well as
  message content when deciding whether an attack triggers.
- The seed attack `mcp-chain-extend-001` reuses the existing `repeated_tool_calls` signal:
  a susceptible agent obeys the injected "fetch the confirmed revision" policy and keeps
  calling the tool. No new mock behaviour, so the change stays small.

## Alternatives rejected
- A deployable MCP server or client: out of scope and unnecessary. The research finding is
  the *tool-description injection surface*, which the existing harness can model directly.
- A new signal for chain extension: `repeated_tool_calls` already expresses it; a new signal
  would add mock complexity for no measurement gain.
- Adding a distinct untrusted tool to the catalog: more moving parts than appending to an
  existing tool's description, with the same effect on what the model reads.

## Consequences
- Defenses are now scored against an attack that arrives through the tool catalog, not just
  through prompts and results. Verified offline: undefended, the seed attack runs to the
  safety ceiling; `loop_detect` aborts it early at lower cost.
- Bring-your-own-agent runs (ADR 0012) exercise this automatically, since the payload is
  part of the `Tool.description` the agent is handed.
