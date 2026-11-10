"""Post-snapshot events and thesis revisions: every check passes on the real module and every small change of its code is noticed or harmless."""

from __future__ import annotations

from hoshitori import post_snapshot
from tests.mutation_sweep import survivors
from tests.post_snapshot_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(post_snapshot) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_check|statement dropped|envelope.validate_producer_record(record)|0": (
        "the event and the revision validate every field of what they carry, so the envelope check is a second guard that cannot fail for a "
        "record made from them"
    ),
    "events_of|Or->And|not joined.ok or joined.asset is None|0": "a join that is OK always carries its asset, so the two tests say the same thing",
    "events_of|int+1|1|0": (
        "the removal is joined one millisecond before the list lost it; any instant between the previous list and the change gives the same join"
    ),
    "event_record|statement dropped|_check(record)|0": "the same second guard, at the event's call",
    "revision_record|statement dropped|_check(record)|0": "the same second guard, at the revision's call",
    "ingest|Eq->NotEq|r.get('type') == EVENT_TYPE|0": (
        "event ids and revision ids have different prefixes, so a record whose id is the event's id is an event whatever its type says"
    ),
    "ingest|str+~|'type'|0": "the same: the id already says which kind of record it is",
    "_check|statement dropped|raise PostSnapshotError(f'the record does not satisfy the envelope: {exc}') from exc|0": (
        "the same second guard: with it dropped a record that would have failed the envelope check is accepted, and none can be made"
    ),
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.post_snapshot", "tests.post_snapshot_checks")
    assert total > 60, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
