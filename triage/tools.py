"""Tools the triage agent can call."""

from __future__ import annotations

import json

import neatlogs

from triage import data

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_customer",
            "description": "Look up a customer's name, company, plan and account status by email.",
            "parameters": {
                "type": "object",
                "properties": {"email": {"type": "string"}},
                "required": ["email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_kb",
            "description": "Search the knowledge base. Returns matching articles with ids and titles.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]


@neatlogs.span(kind="TOOL", tool_name="lookup_customer")
def lookup_customer(email: str) -> dict:
    record = data.customer(email)
    if record is None:
        raise LookupError(f"no customer with email {email!r}")
    return record


@neatlogs.span(kind="TOOL", tool_name="search_kb")
def search_kb(query: str) -> list[dict]:
    q = query.lower()
    return [
        {"id": a["id"], "title": a["title"]}
        for a in data.kb_articles()
        if q in a["title"].lower() or any(q in tag.lower() for tag in a["tags"])
    ]


TOOLS = {"lookup_customer": lookup_customer, "search_kb": search_kb}


def run_tool(name: str, arguments: str) -> str:
    try:
        args = json.loads(arguments or "{}")
        return json.dumps(TOOLS[name](**args))
    except Exception as exc:  # surfaced to the model so it can react
        return f"ERROR: {type(exc).__name__}: {exc}"
