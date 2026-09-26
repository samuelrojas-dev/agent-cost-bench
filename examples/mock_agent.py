"""The smallest agent under test: a tool-calling loop on the offline mock provider.

Run it with `dowbench run configs/agent-mock.yaml`. A real agent looks the same: call your
model, run the tools it asks for, and pass every response to `task.meter.record`.
"""

from __future__ import annotations

from dowbench.attacks.schema import load_dataset
from dowbench.providers.base import Message, Request, ToolSpec
from dowbench.providers.mock import MockProvider
from dowbench.sut import Task


class MockAgent:
    def __init__(self) -> None:
        # The mock follows any attack payload it sees: a maximally susceptible model.
        attacks = load_dataset().attacks
        self._model = MockProvider([(a.payload, a.expected_signal) for a in attacks])

    def run(self, task: Task) -> str:
        specs = [
            ToolSpec(name=t.name, description=t.description, parameters=t.parameters)
            for t in task.tools
        ]
        messages = [Message(role="user", content=task.prompt)]
        while True:
            response = self._model.complete(
                Request(
                    model="mock-1",
                    system=task.system_prompt,
                    messages=messages,
                    tools=specs,
                    max_tokens=task.max_tokens_per_call,
                )
            )
            task.meter.record(response)  # every call, before acting on it
            if not response.tool_calls:
                return response.text
            messages.append(
                Message(role="assistant", content=response.text, tool_calls=response.tool_calls)
            )
            for call in response.tool_calls:
                result = task.tool(call.name)(**call.arguments)
                messages.append(Message(role="tool", content=result, tool_call_id=call.id))
