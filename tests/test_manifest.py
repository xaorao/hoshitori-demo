"""The candidate manifest: every check passes on the real module, every small change is noticed, and random cycles keep its laws."""

from __future__ import annotations

import random
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hoshitori import evidence, manifest
from tests.manifest_checks import BIN, OKX, H, T, failures, inputs, resolver, rules, trigger
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(manifest) == []


# Changes that no check can notice because the code does the same thing either way. Each one was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "|statement dropped|Resolver = Callable[[str], Resolved]|0": "a type alias that only the type checker reads",
    "_text|str+~|'v'|0": "the name of the one member of the throw-away record that the canonical profile checks the text with",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.manifest", "tests.manifest_checks")
    assert total > 300, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


POOL = ("S01", "S02", "S03", "S04", "S05", "S06", "N01", "N02", "X01", "Q01", "s01", "s03")
FAMILIES = (BIN, OKX, evidence.RYO, evidence.DERIVED, evidence.Family(evidence.FamilyKind.NEWS, "coindesk.com"))


@settings(max_examples=250, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(st.sampled_from(POOL), max_size=14),
    st.lists(
        st.tuples(
            st.sampled_from(POOL),
            st.booleans(),
            st.sets(st.integers(min_value=0, max_value=len(FAMILIES) - 1), max_size=3),
            st.integers(min_value=-3, max_value=3),
            st.integers(min_value=0, max_value=20),
            st.sampled_from(("LISTING_ANNOUNCED", "DELISTING_ANNOUNCED")),
        ),
        max_size=10,
    ),
    st.lists(st.sampled_from(POOL), max_size=6),
    st.integers(min_value=1, max_value=12),
    st.integers(min_value=0, max_value=6),
    st.integers(min_value=0, max_value=10**6),
)
def test_random_cycles_keep_the_laws(
    scan_symbols: list[str],
    raw_triggers: list[tuple[str, bool, set[int], int, int, str]],
    watch: list[str],
    scan_n: int,
    cap: int,
    seed: int,
) -> None:
    ranks = random.Random(seed).sample(range(1, 40), len(scan_symbols))
    scan = tuple(manifest.ScanRow(rank, symbol) for rank, symbol in zip(ranks, scan_symbols, strict=True))
    triggers = tuple(
        trigger(
            manifest,
            f"t{n}",
            symbol,
            primary=primary,
            families=frozenset(FAMILIES[i] for i in fams),
            impact=Decimal(impact),
            first_seen_at_ms=T + seen,
            rule=rule,
        )
        for n, (symbol, primary, fams, impact, seen, rule) in enumerate(raw_triggers)
    )
    resolve = resolver(manifest)
    the_rules = rules(manifest, scan_n=scan_n, event_cap=cap)
    built = manifest.build(inputs(manifest, scan=scan, triggers=triggers, watchlist=tuple(watch)), the_rules, resolve)
    # every proposal is in the manifest, exactly once, with a decision
    assert sum(len(entry.routes) for entry in built.entries) == len(scan) + len(triggers) + len(watch)
    keys = {resolve(sym).asset_id or f"unresolved:{sym.upper()}" for sym in [*scan_symbols, *(t.symbol for t in triggers), *watch]}
    assert len(built.entries) == len(keys), "an asset reached by more than one route is one candidate"
    for entry in built.entries:
        assert entry.included == bool(entry.tags)
        assert (entry.reason is None) == entry.included, "an excluded candidate says why, an included one does not"
        for route in entry.routes:
            assert (route.reason is None) == route.included
    # the routes obey their limits
    in_scan = [r for e in built.entries for r in e.routes if r.route is manifest.Route.RYO_SCAN and r.included]
    in_event = [r for e in built.entries for r in e.routes if r.route is manifest.Route.EVENT and r.included]
    assert len(in_scan) <= scan_n and all((r.rank or 0) <= scan_n for r in in_scan)
    assert len(in_event) <= cap
    assert {e.candidate_id for e in built.union()} == {e.candidate_id for e in built.entries if e.included}
    # nothing the registry cannot settle is in
    for entry in built.union():
        assert entry.asset_id is not None and resolve(entry.symbol).has_oracle
    # a pure function: the same again, and the input order of rows and triggers does not matter
    again = manifest.build(
        inputs(manifest, scan=tuple(reversed(scan)), triggers=tuple(reversed(triggers)), watchlist=tuple(watch)), the_rules, resolve
    )
    assert again.sha256 == built.sha256
    record = manifest.to_record(built, "urn:hoshitori:scheduler:test", T + 9_000)
    assert manifest.matches(record, manifest.build(built.inputs, the_rules, resolve))
    assert built.inputs.protocol_hash == H
