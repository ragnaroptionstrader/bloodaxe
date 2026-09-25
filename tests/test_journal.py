"""Tests for Bloodaxe journal (Rule 9)."""
import json
import os
import tempfile
from pathlib import Path

import pytest

from bloodaxe_pkg.journal import log


@pytest.fixture
def tmp_journal(monkeypatch):
    """Use a temp journal path for the test."""
    tmp = Path(tempfile.mkstemp(suffix=".jsonl")[1])
    monkeypatch.setattr(log, "JOURNAL_PATH", tmp)
    yield tmp
    if tmp.exists():
        tmp.unlink()


def test_log_event_open(tmp_journal):
    record = log.log_event(
        "open",
        order_id="12345",
        underlying="SPY",
        short_put=480, long_put=475, short_call=520, long_call=525,
        entry_credit=1.0, max_loss=500,
        notes="opening trade",
    )
    assert record["event"] == "open"
    assert record["order_id"] == "12345"
    assert "ts" in record
    # File should have one line
    assert len(tmp_journal.read_text().strip().splitlines()) == 1


def test_log_event_exit(tmp_journal):
    record = log.log_event(
        "exit",
        order_id="12345",
        exit_reason="profit_target",
        realized_pnl=50.0,
        notes="closed at 50% profit",
    )
    assert record["event"] == "exit"
    assert record["exit_reason"] == "profit_target"
    assert record["realized_pnl"] == 50.0


def test_read_recent(tmp_journal):
    log.log_event("open", order_id="1", notes="first")
    log.log_event("open", order_id="2", notes="second")
    log.log_event("exit", order_id="1", notes="closed")
    recent = log.read_recent(n=10)
    assert len(recent) == 3
    # Most recent first
    assert recent[0]["event"] == "exit"
    assert recent[2]["notes"] == "first"


def test_log_event_appends(tmp_journal):
    log.log_event("open", order_id="1")
    log.log_event("open", order_id="2")
    log.log_event("open", order_id="3")
    lines = tmp_journal.read_text().strip().splitlines()
    assert len(lines) == 3
    parsed = [json.loads(l) for l in lines]
    assert [r["order_id"] for r in parsed] == ["1", "2", "3"]
