"""Support policy. This is the source of truth the agent must follow."""

QUEUES = ("billing", "technical", "account", "sales")

PRIORITY_POLICY = """\
P1: production outage or data loss for an enterprise customer.
P2: production outage for a pro customer, any issue for an enterprise customer, or a past_due account that cannot pay.
P3: everything else (free plan questions, how-to questions, sales enquiries)."""
