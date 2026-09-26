"""An agent under test on Gemini with the official `google-genai` SDK and function calling.

Point a run config at it with `agent: "examples.gemini_agent:GeminiAgent"`, `provider:
gemini` and the model below in `models`. Replace the body of `run` with your own agent:
the only contract is to use `task.tools` and to pass every response to `task.meter.record`.
"""

from __future__ import annotations

import os
from typing import Any

from google import genai
from google.genai import types

from dowbench.sut import Task

MODEL = "gemini-3.5-flash-lite"


class GeminiAgent:
    def __init__(self, client: Any = None) -> None:
        self._client = client or genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    def run(self, task: Task) -> str:
        config = types.GenerateContentConfig(
            system_instruction=task.system_prompt,
            max_output_tokens=task.max_tokens_per_call,
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=t.name,
                            description=t.description,
                            parameters_json_schema=t.parameters,
                        )
                        for t in task.tools
                    ]
                )
            ],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        contents: list[types.Content] = [
            types.Content(role="user", parts=[types.Part(text=task.prompt)])
        ]
        while True:
            response = self._client.models.generate_content(
                model=MODEL, contents=contents, config=config
            )
            task.meter.record(response)  # every call, before acting on it
            candidate = response.candidates[0] if response.candidates else None
            if candidate is None or candidate.content is None:
                return ""
            calls = [p.function_call for p in candidate.content.parts or [] if p.function_call]
            if not calls:
                return response.text or ""
            contents.append(candidate.content)  # verbatim: keeps thought signatures
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=call.id,
                                name=call.name,
                                response={
                                    "result": task.tool(call.name or "")(**(call.args or {}))
                                },
                            )
                        )
                        for call in calls
                    ],
                )
            )
