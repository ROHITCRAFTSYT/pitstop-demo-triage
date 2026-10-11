"""Triage: a tool-using support-ticket routing agent."""

from __future__ import annotations

import json
import re

import neatlogs

from triage.prompts import SYSTEM, USER
from triage.tools import TOOL_SPECS, run_tool

MAX_STEPS = 4


def _parse(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    return json.loads(match.group(0) if match else text)


@neatlogs.span(kind="AGENT", name="triage.handle", role="support triage", goal="route a ticket")
def handle(email: str, body: str, *, client, model: str) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": USER.format(email=email, body=body)},
    ]
    for _ in range(MAX_STEPS):
        response = client.chat.completions.create(model=model, messages=messages, tools=TOOL_SPECS, temperature=0)
        message = response.choices[0].message
        if message.tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                        for c in message.tool_calls
                    ],
                }
            )
            for call in message.tool_calls:
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": run_tool(call.function.name, call.function.arguments)}
                )
            continue
        return _parse(message.content)
    raise RuntimeError(f"no answer after {MAX_STEPS} steps")
