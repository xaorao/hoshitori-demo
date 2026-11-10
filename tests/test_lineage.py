"""The evidence store: every check passes on the real module, every small change of its code is noticed, and random graphs keep its laws."""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hoshitori import evidence, lineage
from tests.evidence_checks import R, derived, observation
from tests.lineage_checks import digest_of, failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(lineage) == []


def test_every_change_of_the_code_is_noticed() -> None:
    alive, total = survivors("hoshitori.lineage", "tests.lineage_checks")
    assert total > 120, f"only {total} places to change: the sweep is not looking at the module"
    assert alive == [], f"unexplained survivors: {alive}"


FAMILIES = (
    evidence.RYO,
    evidence.Family(evidence.FamilyKind.EXCHANGE, "binance-spot"),
    evidence.Family(evidence.FamilyKind.EXCHANGE, "okx"),
    evidence.Family(evidence.FamilyKind.NEWS, "coindesk.com"),
)


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(st.integers(min_value=0, max_value=len(FAMILIES) - 1), min_size=1, max_size=5),
    st.lists(st.lists(st.integers(min_value=0, max_value=1000), min_size=1, max_size=3), max_size=8),
)
def test_random_graphs_keep_the_laws(root_families: list[int], derivations: list[list[int]]) -> None:
    store = lineage.EvidenceStore()
    items: list[evidence.EvidenceItem] = []
    for index, family in enumerate(root_families):
        item = observation(
            evidence,
            capture_id=f"cap-{index}",
            origin=FAMILIES[family],
            known_at_ms=R + index,
            received_at_ms=R + index,
            request=(("n", index),),
        )
        store.add(item, digest_of(item))
        items.append(item)
    for index, picks in enumerate(derivations):
        chosen = {items[pick % len(items)].evidence_id: items[pick % len(items)] for pick in picks}
        at = R + 1_000 + index
        pairs = tuple(sorted((ident, digest_of(item)) for ident, item in chosen.items()))
        item = derived(evidence, path=f"d{index}", derived_from=pairs, received_at_ms=at, known_at_ms=at, transform=(f"t{index}", "1"))
        store.add(item, digest_of(item))
        items.append(item)
    ids = [item.evidence_id for item in items]
    for ident in ids:
        for other in store.descendants(ident):
            assert ident in store.ancestors(other), "descendants and ancestors disagree"
        for other in store.ancestors(ident):
            assert ident in store.descendants(other), "ancestors and descendants disagree"
        assert ident not in store.ancestors(ident) and ident not in store.descendants(ident), "an item is its own ancestor"
        roots = store.roots(ident)
        assert roots and all(store.get(root).kind is not evidence.Kind.DERIVED for root in roots)
        assert roots <= (store.ancestors(ident) | {ident})
        assert store.families(ident) and all(f.is_origin for f in store.families(ident))
        closure = store.withheld([ident])
        assert ident in closure and closure == {ident} | store.descendants(ident)
        assert store.leaks([i for i in ids if i not in closure], [ident]) == (), "something outside the closure is a leak"
        assert set(store.leaks(ids, [ident])) == set(closure)
    everything = store.independence(ids)
    assert everything.calls == len(ids)
    assert set(everything.origins) == {FAMILIES[f] for f in root_families}, "the origins are those of the roots"
    # reading later never loses a key
    first = {item.key for item in store.as_of(R + 500)}
    last = {item.key for item in store.as_of(R + 10**6)}
    assert first <= last
