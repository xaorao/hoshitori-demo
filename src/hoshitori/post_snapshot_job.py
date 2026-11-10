"""One pass that turns the registry's changes after a cycle's snapshot into recorded events and thesis revisions (plan, part A).

The pass reads what the ledger already holds, asks the registry for every change since the first sealed snapshot, and records each as an
event (idempotently: the same change is the same event) with the revisions it causes. Each event names the latest snapshot that had been
sealed when it was first seen. Nothing here touches a snapshot, a decision or an evidence store.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from hoshitori import canonical, cycle, post_snapshot
from hoshitori.ledger import Ledger
from hoshitori.registry import Registry


def sealed_snapshots(records: Sequence[dict[str, Any]]) -> list[tuple[int, str, str]]:
    """``(snapshot time, cycle id, snapshot hash)`` of every cycle that sealed one, earliest first."""
    histories = cycle.rebuild(cast(list[dict[str, canonical.JsonValue]], [r for r in records if r.get("type") == cycle.TYPE]))
    found = []
    for cycle_id, history in histories.items():
        for transition in history:
            step = transition.step
            if step.state is cycle.State.SNAPSHOT_FROZEN:
                found.append((cast(int, step.snapshot_at_ms), cycle_id, cast(str, step.snapshot_hash)))
    return sorted(found)


def post_snapshot_pass(ledger: Ledger, registry: Registry, *, now_ms: int, source: str) -> tuple[post_snapshot.Event, ...]:
    """Record the changes the registry saw after the first sealed snapshot, as of ``now_ms``; return the events as recorded."""
    records = [cast(dict[str, Any], canonical.parse_record(ledger.read_record(seq))) for seq in range(ledger.size)]
    snapshots = sealed_snapshots(records)
    if not snapshots or snapshots[0][0] > now_ms:  # no snapshot has been sealed yet, as of now
        return ()
    found, _unresolved = post_snapshot.events_of(
        registry, after_ms=snapshots[0][0], until_ms=now_ms, previous_snapshot=None, ingested_ms=now_ms
    )
    theses = post_snapshot.theses_of(records)
    recorded = []
    for event in found:
        sealed = [s for s in snapshots if s[0] <= event.first_seen_ms]
        previous = (sealed[-1][1], sealed[-1][2]) if sealed else None
        placed = post_snapshot.Event(
            event.kind,
            event.asset_id,
            event.instrument_id,
            event.venue,
            event.status,
            event.first_seen_ms,
            event.ingested_ms,
            previous,
            event.relation,
            event.related,
            event.captures,
        )
        recorded.append(post_snapshot.ingest(ledger, placed, theses, source=source)[0])
    return tuple(recorded)
