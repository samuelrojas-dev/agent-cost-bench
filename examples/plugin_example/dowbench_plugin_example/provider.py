"""A toy provider contributed by a plugin (ADR 0021).

Synthetic and marked ``simulated``, so its usage is never taken for a real result
(CLAUDE.md, ADR 0002). It shows the shape of a provider entry point; a real adapter would
call an SDK and map its usage strictly.
"""

from __future__ import annotations

from dowbench.metering.usage import Usage
from dowbench.providers.base import Request, Response


class EchoProvider:
    name = "echo"
    simulated = True

    def count_tokens(self, request: Request) -> int:
        return sum(len(m.content) for m in request.messages) // 4

    def complete(self, request: Request) -> Response:
        answer = "ok"
        usage = Usage(input_tokens=self.count_tokens(request), output_tokens=len(answer) // 4 + 1)
        return Response(text=answer, stop_reason="end_turn", usage=usage)


def build_echo(config: object, dataset: object) -> EchoProvider:
    """``dowbench.providers`` factory for the toy echo provider."""
    return EchoProvider()
