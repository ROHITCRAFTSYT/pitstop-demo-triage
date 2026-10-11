"""Triage eval harness.

Runs every ticket in evals/tasks.json through ``triage.agent.handle`` and checks the
routing against the support policy. Writes the JSON report Pitstop reads.

    python -m evals.run --out results.json [--model claude-haiku] [--only password]
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import uuid
from types import SimpleNamespace

import neatlogs

ROOT = pathlib.Path(__file__).resolve().parent.parent
LITELLM_BASE_URL = "https://litellm.vision.aivar.app/v1"
DEFAULT_TOKEN_BUDGET = 6000
PRICES = {"claude-haiku": (1.0, 5.0), "claude-sonnet-4.6": (3.0, 15.0), "nova-lite": (0.06, 0.24), "nova-pro": (0.8, 3.2)}


def cost_usd(model: str, pt: int, ct: int) -> float:
    pin, pout = PRICES.get(model, (3.0, 15.0))
    return (pt * pin + ct * pout) / 1_000_000


def make_client():
    from openai import OpenAI

    client = OpenAI(base_url=os.environ.get("LLM_BASE_URL", LITELLM_BASE_URL), api_key=os.environ["LITELLM_API_KEY"],
                    max_retries=2, timeout=120)
    return neatlogs.wrap(client)


class MeteredClient:
    """Wraps a chat client and records each step without relying on agent code."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.steps: list[dict] = []
        self._seen = 0
        self._names: dict[str, tuple[str, str]] = {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        messages = kwargs.get("messages", [])
        tool_results = []
        for m in messages[self._seen:]:
            m = m if isinstance(m, dict) else m.model_dump()
            for c in m.get("tool_calls") or []:
                self._names[c["id"]] = (c["function"]["name"], c["function"]["arguments"])
            if m.get("role") == "tool":
                name, args = self._names.get(m.get("tool_call_id"), ("?", ""))
                content = str(m.get("content", ""))
                tool_results.append({"tool": name, "args": args, "chars": len(content),
                                     "error": content.startswith("ERROR"), "excerpt": content[:240]})
        self._seen = len(messages)
        started = time.perf_counter()
        response = self.inner.chat.completions.create(**kwargs)
        usage = getattr(response, "usage", None)
        message = response.choices[0].message
        self.steps.append({
            "step": len(self.steps) + 1,
            "tool_results_in": tool_results,
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "latency_s": round(time.perf_counter() - started, 3),
            "tool_calls_out": [{"tool": c.function.name, "args": c.function.arguments} for c in (message.tool_calls or [])],
            "content_excerpt": (message.content or "")[:400],
        })
        return response

    @property
    def prompt_tokens(self) -> int:
        return sum(s["prompt_tokens"] for s in self.steps)

    @property
    def completion_tokens(self) -> int:
        return sum(s["completion_tokens"] for s in self.steps)


def score(task: dict, out, error: str | None, metered: MeteredClient, budget: int) -> dict:
    checks, failures = {}, []
    checks["completed"] = isinstance(out, dict)
    if not checks["completed"]:
        failures.append(f"no usable answer: {error}")
        out = {}
    for field, want in (("queue", task["queue"]), ("priority", task["priority"]), ("kb_article", task["kb"])):
        got = str(out.get(field, "")).strip()
        checks[f"correct_{field}"] = got.upper() == want.upper()
        if checks["completed"] and not checks[f"correct_{field}"]:
            failures.append(f"{field}={got!r}, expected {want!r}")
    reply = str(out.get("reply", ""))
    checks["personal_reply"] = task["first_name"].lower() in reply.lower()
    if checks["completed"] and not checks["personal_reply"]:
        failures.append(f"reply does not address the customer as {task['first_name']!r}")
    total = metered.prompt_tokens + metered.completion_tokens
    checks["within_token_budget"] = total <= budget
    if not checks["within_token_budget"]:
        failures.append(f"used {total} tokens, budget is {budget}")
    return {"checks": checks, "failures": failures, "passed": all(checks.values())}


def _trace_id() -> str | None:
    try:
        from neatlogs._wrap_utils import active_neatlogs_context
        from opentelemetry import trace

        ctx = active_neatlogs_context()
        span = trace.get_current_span(ctx) if ctx is not None else trace.get_current_span()
        trace_id = span.get_span_context().trace_id
        return format(trace_id, "032x") if trace_id else None
    except Exception:
        return None


@neatlogs.span(kind="WORKFLOW", name="triage.eval_task")
def run_task(task: dict, client, model: str, budget: int) -> dict:
    from triage.agent import handle

    metered = MeteredClient(client)
    started = time.perf_counter()
    out, error = None, None
    try:
        out = handle(task["email"], task["body"], client=metered, model=model)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    result = score(task, out, error, metered, budget)
    neatlogs.log("triage task {task_id} passed={passed}", task_id=task["id"], passed=result["passed"])
    return {
        "id": task["id"], "input": task["body"][:80], **result, "error": error, "output": out,
        "steps": metered.steps, "n_steps": len(metered.steps),
        "prompt_tokens": metered.prompt_tokens, "completion_tokens": metered.completion_tokens,
        "total_tokens": metered.prompt_tokens + metered.completion_tokens,
        "cost_usd": round(cost_usd(model, metered.prompt_tokens, metered.completion_tokens), 6),
        "latency_s": round(time.perf_counter() - started, 3),
        "tool_errors": sum(r["error"] for s in metered.steps for r in s["tool_results_in"]),
        "trace_id": _trace_id(),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=os.environ.get("TRIAGE_MODEL", "claude-haiku"))
    ap.add_argument("--only", action="append")
    ap.add_argument("--budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    args = ap.parse_args(argv)

    run_id = os.environ.get("PITSTOP_RUN_ID", uuid.uuid4().hex[:8])
    variant = os.environ.get("PITSTOP_VARIANT", "manual")
    if os.environ.get("NEATLOGS_API_KEY"):
        neatlogs.init(workflow_name="triage", tags=[f"run:{run_id}", f"variant:{variant}", f"model:{args.model}"])

    tasks = json.loads((ROOT / "evals" / "tasks.json").read_text(encoding="utf-8"))
    if args.only:
        tasks = [t for t in tasks if t["id"] in args.only]
    client = make_client()
    with neatlogs.identify(session_id=f"{run_id}-{variant}"):
        results = [run_task(t, client, args.model, args.budget) for t in tasks]

    n = len(results) or 1
    summary = {
        "run_id": run_id, "variant": variant, "model": args.model, "data": "fixture",
        "tasks": len(results), "passed": sum(r["passed"] for r in results),
        "pass_rate": round(sum(r["passed"] for r in results) / n, 3),
        "total_tokens": sum(r["total_tokens"] for r in results),
        "cost_usd": round(sum(r["cost_usd"] for r in results), 6),
        "avg_latency_s": round(sum(r["latency_s"] for r in results) / n, 3),
        "avg_steps": round(sum(r["n_steps"] for r in results) / n, 2),
        "tool_errors": sum(r["tool_errors"] for r in results),
    }
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "tasks": results}, indent=2, default=str), encoding="utf-8")
    if os.environ.get("NEATLOGS_API_KEY"):
        neatlogs.flush()
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
