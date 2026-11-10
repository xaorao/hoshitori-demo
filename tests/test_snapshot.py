"""The frozen snapshot: every check passes on the real module, every small change is noticed, and random collections keep the laws."""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hoshitori import snapshot
from hoshitori.evidence import Kind
from tests.mutation_sweep import survivors
from tests.snapshot_checks import R, World, failures


def test_real_module_passes_every_check() -> None:
    assert failures(snapshot) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_text|str+~|'v'|0": "the name of the one member of the throw-away record that the canonical profile checks the text with",
    "rebuild|True<->False|True|0": "the candidates and their item lists are made together and are as long as each other; the flag only asserts it",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.snapshot", "tests.snapshot_checks")
    assert total > 250, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(st.integers(min_value=0, max_value=120_000), min_size=1, max_size=6),
    st.lists(st.tuples(st.integers(min_value=0, max_value=5), st.booleans()), max_size=6),
    st.lists(st.booleans(), min_size=14, max_size=14),
    st.randoms(use_true_random=False),
)
def test_random_collections_keep_the_laws(ages: list[int], children: list[tuple[int, bool]], mask: list[bool], rnd: object) -> None:
    w = World()
    bases = [w.add(w.bars(f"b{n}", "tk-btc", event_time_ms=R - age)) for n, age in enumerate(ages)]  # age at receipt
    pool = list(bases)
    for n, (parent, second) in enumerate(children):
        parents = [pool[parent % len(pool)]]
        if second:
            parents.append(pool[(parent + 1) % len(pool)])
        pool.append(w.derive(f"d{n}", sorted(set(parents))))
    pool.append(w.btc_int)
    listed = [ident for ident, keep in zip(pool, mask, strict=False) if keep] or [pool[0]]
    snap = w.freeze(snapshot, by_candidate={"cd-btc": listed}, shared=[], required=[])
    slot = snap.slots[0]
    admitted, rejected = set(slot.item_ids), {r.evidence_id: r.reason for r in slot.rejected}
    assert admitted | set(rejected) == set(listed) and not admitted & set(rejected), "every listed item is admitted or rejected, once"
    stale = {i for i, age in zip(bases, ages, strict=True) if i in listed and age > 60_000}
    assert {i for i, r in rejected.items() if r is snapshot.Reject.STALE} == stale
    for ident in listed:
        item = w.store.get(ident)
        parents = [p for p, _ in item.derived_from]
        expected = ident not in stale and all(p in admitted for p in parents)
        assert (ident in admitted) == expected, "an item is admitted exactly when it is fresh and stands only on admitted items"
    reader = snapshot.Reader(snap, snapshot.Access.INDEPENDENT)
    seen = reader.items("cd-btc")
    assert all(item.kind is not Kind.INTERPRETATION for item in seen)
    assert {item.evidence_id for item in seen} <= admitted
    for item in seen:
        assert all(p in {i.evidence_id for i in seen} for p, _ in item.derived_from), "a reader never sees a child without its parents"
    shuffled = list(listed)
    rnd.shuffle(shuffled)  # type: ignore[attr-defined]
    again = w.freeze(snapshot, by_candidate={"cd-btc": shuffled}, shared=[], required=[])
    assert again.sha256 == snap.sha256
