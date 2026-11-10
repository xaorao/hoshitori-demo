"""The post-snapshot pass over a real cycle: the registry's later changes become events and revisions, once, and change nothing earlier."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from hoshitori import canonical, post_snapshot, post_snapshot_job, today_items, view_models
from hoshitori.timeline import from_ms
from tests.registry_checks import inst, lst
from tests.settle_job_checks import Rig


def _parsed(ledger: Any) -> list[dict[str, Any]]:
    return [canonical.parse_record(ledger.read_record(seq)) for seq in range(ledger.size)]  # type: ignore[misc]


def _remove_btc_from_binance(rig: Rig, at_ms: int) -> None:
    rig.w.registry.admit(
        lst("binance-spot", *[inst("binance-spot", f"{n}USDT", n) for n in ("ETH", "SOL")]),
        scope="spot",
        known_at=from_ms(at_ms),
        capture_id="cap-b-later",
    )


def test_a_removal_after_the_snapshot_revises_the_theses_that_settle_on_it_and_only_those() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        rig = Rig(Path(tmp) / "world")
        ledger = rig.w.ledger
        records = _parsed(ledger)
        sealed = post_snapshot_job.sealed_snapshots(records)
        assert len(sealed) == 1
        snap_ms, cycle_id, snap_hash = sealed[0]
        _remove_btc_from_binance(rig, snap_ms + 120_000)
        now = snap_ms + 600_000
        before_bytes = [ledger.read_record(seq) for seq in range(ledger.size)]
        views_before = view_models.build(records, rig.w.protocol, now_ms=now)
        events = post_snapshot_job.post_snapshot_pass(ledger, rig.w.registry, now_ms=now, source="urn:hoshitori:cycle:test")
        assert [(e.kind, e.venue, e.relation) for e in events] == [("REGISTRY_INSTRUMENT_REMOVED", "binance-spot", "NEW")]
        assert events[0].previous_snapshot == (cycle_id, snap_hash)
        after = _parsed(ledger)
        revisions = [r["body"] for r in after if r["type"] == post_snapshot.REVISION_TYPE]
        theses = {t.thesis_id: t for t in post_snapshot.theses_of(records)}
        assert revisions and {r["kind"] for r in revisions} == {post_snapshot.MATERIAL}
        assert {theses[r["thesis_id"]].asset_id for r in revisions} == {events[0].asset_id}
        assert [ledger.read_record(seq) for seq in range(len(before_bytes))] == before_bytes
        assert view_models.build(after, rig.w.protocol, now_ms=now) == views_before
        shown = [i for i in today_items.items(after, []) if i.state == today_items.MATERIAL_UPDATE]
        assert shown and {i.symbol for i in shown} == {"BTC"}
        assert {i.link for i in shown} == {r["thesis_id"] for r in revisions}
        assert all(i.reason == post_snapshot.PREMISE for i in shown)
        size = ledger.size
        again = post_snapshot_job.post_snapshot_pass(ledger, rig.w.registry, now_ms=now + 1_000, source="urn:hoshitori:cycle:test")
        assert [e.event_id for e in again] == [e.event_id for e in events] and ledger.size == size


def test_without_a_sealed_snapshot_nothing_is_an_event() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        rig = Rig(Path(tmp) / "world")
        empty = rig.w.ledger
        # a ledger with no cycle in it: the registry's changes are not "after a snapshot" of anything
        from tests.commit_checks import ledger_in

        fresh = ledger_in(Path(tmp) / "fresh", rig.w.clock)
        assert post_snapshot_job.post_snapshot_pass(fresh, rig.w.registry, now_ms=10**12, source="urn:hoshitori:cycle:test") == ()
        assert fresh.size == 0 and empty.size > 0
        fresh.close()


def test_a_clock_that_reads_before_the_snapshot_records_nothing_and_does_not_fail() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        rig = Rig(Path(tmp) / "world")
        snap_ms = post_snapshot_job.sealed_snapshots(_parsed(rig.w.ledger))[0][0]
        size = rig.w.ledger.size
        assert (
            post_snapshot_job.post_snapshot_pass(rig.w.ledger, rig.w.registry, now_ms=snap_ms - 1, source="urn:hoshitori:cycle:test") == ()
        )
        assert rig.w.ledger.size == size
