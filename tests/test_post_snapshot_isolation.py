"""Events after a snapshot never change what the cycle decided or showed: the earlier records are byte-identical, the league's views are
equal before and after, and a revision names the thesis it revises without editing it (plan, part A)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from hoshitori import canonical, post_snapshot, view_models
from hoshitori.timeline import to_ms
from tests.settle_job_checks import Rig


def _parsed(ledger: Any) -> list[dict[str, Any]]:
    return [canonical.parse_record(ledger.read_record(seq)) for seq in range(ledger.size)]  # type: ignore[misc]


def test_an_event_after_the_snapshot_changes_no_earlier_record_and_no_league_view() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        rig = Rig(Path(tmp) / "world")
        ledger = rig.w.ledger
        now = to_ms(rig.w.clock.now())
        before_bytes = [ledger.read_record(seq) for seq in range(ledger.size)]
        before_records = _parsed(ledger)
        views_before = view_models.build(before_records, rig.w.protocol, now_ms=now)
        theses = post_snapshot.theses_of(before_records)
        btc = next(r["body"] for r in before_records if r["type"] == "forecast" and r["body"]["symbol"] == "BTC")
        snapshot_record = next(r for r in before_records if r["type"] == "snapshot")
        event = post_snapshot.Event(
            "REGISTRY_INSTRUMENT_REMOVED",
            btc["asset_id"],
            "inst-btc-binance",
            btc["oracle_primary"],
            None,
            now + 1_000,
            now + 2_000,
            (str(snapshot_record["id"]), "a" * 64),
            "NEW",
            None,
            (),
        )
        _, revisions = post_snapshot.ingest(ledger, event, theses, source=str(snapshot_record["source"]))
        assert revisions and {r.kind for r in revisions} == {post_snapshot.MATERIAL}
        assert {r.thesis_id for r in revisions} <= {t.thesis_id for t in theses if t.asset_id == btc["asset_id"]}
        assert [ledger.read_record(seq) for seq in range(len(before_bytes))] == before_bytes
        after = _parsed(ledger)
        assert next(r["type"] for r in after[len(before_bytes) :]) == post_snapshot.EVENT_TYPE
        assert view_models.build(after, rig.w.protocol, now_ms=now) == views_before
        eth = [t for t in theses if t.asset_id != btc["asset_id"]]
        assert eth and not any(r.thesis_id in {t.thesis_id for t in eth} for r in revisions)
