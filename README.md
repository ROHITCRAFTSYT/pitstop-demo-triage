# Triage (second patient)

A support-ticket routing agent: it looks up the customer, searches the knowledge
base and returns a queue, priority, KB article and reply. Its evals check the
routing against `triage/policy.py`. Like Scout, it ships with faults Pitstop
isn't told about.

```bash
python -m evals.run --out results.json --model claude-haiku
```
