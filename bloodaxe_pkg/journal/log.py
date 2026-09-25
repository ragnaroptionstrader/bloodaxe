"""Bloodaxe trade journal — Rule 9.

Every trade event (open, exit, manual close) is logged to journal.jsonl
with full context so the operator can review decisions after the fact.

Each line is a JSON object with:
- ts: ISO timestamp
- event: "open" | "exit" | "manual_close"
- order_id: Tiger order id
- underlying, expiry, strikes
- entry_credit, max_loss, quantity
- exit_reason (for exits)
- realized_pnl (for exits)
- notes (free-form)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


JOURNAL_PATH = Path(os.environ.get(
    "BLOODAXE_JOURNAL_PATH",
    "/home/freya/bloodaxe/journal.jsonl",
))


def log_event(event: str, **fields) -> dict:
    """Append a journal event to journal.jsonl.

    Args:
        event: one of "open", "exit", "manual_close".
        **fields: arbitrary structured fields to include.

    Returns:
        The full event dict that was written (useful for caller logging).
    """
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def read_recent(n: int = 20) -> list[dict]:
    """Read the most recent n journal entries (most recent first)."""
    if not JOURNAL_PATH.exists():
        return []
    lines = JOURNAL_PATH.read_text().splitlines()
    out = []
    for line in lines[-n:]:
        try:
            out.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue
    return list(reversed(out))
