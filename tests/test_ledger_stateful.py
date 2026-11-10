"""A model-based test: random sequences of ledger operations, including restarts and crashes, against a simple model."""

from __future__ import annotations

import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from hoshitori import canonical, ledger, merkle
from hoshitori.clock import ManualClock
from tests.ledger_checks import ORIGIN, SIGNER, START, invariants, rec


class LedgerMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.dir = Path(tempfile.mkdtemp(prefix="hoshitori-stateful-"))
        self.clock = ManualClock(START)
        self.led = self.open()
        self.next_id = 0
        self.acknowledged: dict[str, dict] = {}  # type: ignore[type-arg]

    def open(self) -> ledger.Ledger:
        return ledger.Ledger.open(self.dir, signer=SIGNER, origin=ORIGIN, clock=self.clock, segment_records=3)

    def teardown(self) -> None:
        self.led.abandon()
        shutil.rmtree(self.dir, ignore_errors=True)

    @rule(count=st.integers(1, 4))
    def submit_new(self, count: int) -> None:
        for _ in range(count):
            record = rec(self.next_id)
            self.next_id += 1
            self.led.submit(record)
            self.acknowledged[str(record["id"])] = record

    @precondition(lambda self: bool(self.acknowledged))
    @rule(data=st.data())
    def submit_duplicate(self, data: st.DataObject) -> None:
        ident = data.draw(st.sampled_from(sorted(self.acknowledged)))
        first = self.led.submit(self.acknowledged[ident])
        second = self.led.submit(self.acknowledged[ident])
        assert first == second

    @rule()
    def append(self) -> None:
        self.led.append_pending()

    @rule()
    def checkpoint(self) -> None:
        if self.led.size:
            self.led.checkpoint()

    @rule(seconds=st.integers(0, 5))
    def tick(self, seconds: int) -> None:
        self.clock.advance(timedelta(seconds=seconds))

    @rule()
    def restart(self) -> None:
        self.led.close()
        self.led = self.open()

    @rule()
    def crash_and_recover(self) -> None:
        self.led.abandon()
        self.led = self.open()

    @invariant()
    def everything_acknowledged_is_committed_or_pending(self) -> None:
        for ident in self.acknowledged:
            committed = self.led.find("urn:hoshitori:test", ident) is not None
            queued = self.led._db.execute("SELECT 1 FROM outbox WHERE rec_id=? AND state='pending'", (ident,)).fetchone()
            assert committed or queued, f"{ident} is neither committed nor queued"

    @invariant()
    def the_log_is_healthy_and_consistent(self) -> None:
        assert self.led.state == "OK"
        assert invariants(self.dir, self.led) == []
        times = [canonical.parse_record(self.led.read_record(i))["committed_at"] for i in range(self.led.size)]
        assert times == sorted(times)
        if self.led.size:
            root = self.led.root()
            index = self.led.size - 1
            assert merkle.verify_inclusion(self.led.leaf_hash_at(index), index, self.led.size, self.led.inclusion_proof(index), root)


LedgerMachine.TestCase.settings = settings(max_examples=200, stateful_step_count=40, deadline=None)
TestLedgerModel = LedgerMachine.TestCase
