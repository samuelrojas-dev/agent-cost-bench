# Related work and positioning

Checked on 2026-09-25 against the `main` branch of each repository and the papers' abstracts.
Re-check before any release: these tools move fast.

## Tools

| Tool | What it covers for denial of wallet | How it decides pass/fail | Gap dowbench fills |
|---|---|---|---|
| promptfoo, [`reasoningDos.ts`](https://github.com/promptfoo/promptfoo/blob/main/src/redteam/plugins/reasoningDos.ts) | Generates prompts meant to cause excessive reasoning. | An LLM judge reads the response against a rubric ("signs of ... excessive computation"). The grader does not read token usage. | Measures billed tokens and cost instead of a judge's opinion; compares defenses. |
| promptfoo, [`divergentRepetition.ts`](https://github.com/promptfoo/promptfoo/blob/main/src/redteam/plugins/divergentRepetition.ts) | Repetition prompts that can cause long outputs. | Grader not checked in source yet. | Same as above, pending that check. |
| garak, [`probes/divergence.py`](https://github.com/NVIDIA/garak/blob/main/garak/probes/divergence.py) | Repeat-a-word and repeated-token prompts. | Detectors look for divergence (training-data leakage, instability), not cost. It caps `max_tokens` but does not measure spend. | Cost is the outcome, not a side effect. |
| garak, [`probes/agent_breaker.py`](https://github.com/NVIDIA/garak/blob/main/garak/probes/agent_breaker.py) | Multi-turn attacks on tool-using agents. | Targets excessive agency through tool manipulation; no DoS or token-consumption objective. | Agent-level cost attacks. |
| PyRIT | Red-teaming orchestration. | Not checked in source; no denial-of-wallet component found in its docs. | — |

garak's probe list (checked 2026-09-25) has no module dedicated to denial of service or
resource consumption. The plan's earlier note that garak had a DoS probe came from a
third-party article and is **not confirmed** by the source.

## Papers

| Work | Setting | Defenses | Code |
|---|---|---|---|
| OTora, [arXiv:2605.08876](https://arxiv.org/abs/2605.08876) | Reasoning-level DoS on WebShop, email and OS agents; LLaMA-70B and GPT-OSS-120B; up to 10x reasoning tokens. | Discussed, not compared. | Released. |
| Beyond Max Tokens, [arXiv:2601.10955](https://arxiv.org/abs/2601.10955) | Malicious MCP server extends tool-calling chains; six LLMs; up to 658x cost. | Reports that prompt filters and trajectory monitors rarely detect it. | "Coming soon" at publication. |
| OverThink, [arXiv:2502.02542](https://arxiv.org/abs/2502.02542) | Decoy reasoning problems in retrieved context. | — | — |
| ReasoningBomb, [arXiv:2602.00154](https://arxiv.org/abs/2602.00154); BenchOverflow, [arXiv:2601.08490](https://arxiv.org/abs/2601.08490) | Long reasoning or long output on single models. | — | — |

## Positioning

The attacks themselves are known. What none of the above provides is a reproducible
**defense bench**: an attack × defense × commercial-provider matrix scored on provider-billed
tokens and USD, that also charges each defense for its cost on benign tasks, and that can be
recomputed from recorded runs without spending quota.
