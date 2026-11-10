"""Writers racing on one event, and a restart between two passes: one event, one revision, nothing twice (plan, part A)."""

from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path

from hoshitori import post_snapshot
from hoshitori.clock import ManualClock
from tests.commit_checks import START, ledger_in
from tests.post_snapshot_checks import NOW, SOURCE, event, thesis


def _types(ledger: object) -> list[str]:
    return [json.loads(ledger.read_record(seq))["type"] for seq in range(ledger.size)]  # type: ignore[attr-defined]


def test_six_writers_of_one_event_leave_one_event_and_one_revision() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ledger = ledger_in(Path(tmp), ManualClock(START))
        theses = [thesis(post_snapshot, "fc-a")]
        errors: list[BaseException] = []

        def write() -> None:
            try:
                post_snapshot.ingest(ledger, event(post_snapshot), theses, source=SOURCE)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=write) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert _types(ledger) == ["post_snapshot_event", "thesis_revision"]
        ledger.close()


def test_a_restart_between_two_passes_writes_nothing_twice() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        clock = ManualClock(START)
        theses = [thesis(post_snapshot, "fc-a")]
        first = ledger_in(Path(tmp), clock)
        _, revisions = post_snapshot.ingest(first, event(post_snapshot), theses, source=SOURCE)
        size = first.size
        first.close()
        reopened = ledger_in(Path(tmp), clock)
        _, again = post_snapshot.ingest(reopened, event(post_snapshot), theses, source=SOURCE)
        assert reopened.size == size and again == revisions
        reopened.close()


def _kept(ledger: object) -> set[tuple[str, str, str]]:
    """What a ledger holds of events and revisions, without the order they were written in."""
    out: set[tuple[str, str, str]] = set()
    for seq in range(ledger.size):  # type: ignore[attr-defined]
        rec = json.loads(ledger.read_record(seq))  # type: ignore[attr-defined]
        body = rec["body"]
        if rec["type"] == "post_snapshot_event":
            out.add(("event", rec["id"], body["venue"]))
        elif rec["type"] == "thesis_revision":
            out.add(("revision", body["thesis_id"], body["event_id"] + ":" + body["kind"]))
    return out


def test_events_that_arrive_in_the_other_order_leave_the_same_records() -> None:
    first = event(post_snapshot, venue="binance-spot", first_seen_ms=NOW + 5_000, ingested_ms=NOW + 20_000)
    second = event(post_snapshot, venue="okx", instrument_id="inst-btc-okx", first_seen_ms=NOW + 10_000, ingested_ms=NOW + 20_000)
    theses = [thesis(post_snapshot, "fc-a"), thesis(post_snapshot, "fc-b", venues=("kraken",))]
    kept = []
    for order in ((first, second), (second, first)):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ledger_in(Path(tmp), ManualClock(START))
            for item in order:
                post_snapshot.ingest(ledger, item, theses, source=SOURCE)
            kept.append(_kept(ledger))
            ledger.close()
    assert kept[0] == kept[1]
    revised = {(k, t) for k, t, _ in kept[0] if k == "revision"}
    assert revised == {("revision", "fc-a")}  # two events on two venues of one asset revise one thesis twice; the thesis on kraken, never


def test_two_events_about_one_asset_are_two_events_and_revise_each_thesis_once_per_event() -> None:
    a = event(post_snapshot, venue="binance-spot", first_seen_ms=NOW + 5_000, ingested_ms=NOW + 20_000)
    b = event(post_snapshot, venue="okx", instrument_id="inst-btc-okx", first_seen_ms=NOW + 10_000, ingested_ms=NOW + 20_000)
    with tempfile.TemporaryDirectory() as tmp:
        ledger = ledger_in(Path(tmp), ManualClock(START))
        theses = [thesis(post_snapshot, "fc-a")]
        for item in (a, b):
            post_snapshot.ingest(ledger, item, theses, source=SOURCE)
        kinds = _types(ledger)
        assert kinds.count("post_snapshot_event") == 2 and kinds.count("thesis_revision") == 2
        ledger.close()
