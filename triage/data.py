"""Fixture-backed CRM and knowledge base."""

from __future__ import annotations

import functools
import json
import pathlib

DATA = pathlib.Path(__file__).parent / "fixtures" / "data.json"


@functools.lru_cache(maxsize=1)
def load() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def customer(email: str) -> dict | None:
    return load()["customers"].get(email.strip().lower())


def kb_articles() -> list[dict]:
    return load()["kb"]
