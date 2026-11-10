"""The protocol document: the checks pass on the real module, every small change of its code is noticed, and a document survives a round trip."""

from __future__ import annotations

import unicodedata

from hypothesis import given, settings
from hypothesis import strategies as st

from hoshitori import protocol
from tests.mutation_sweep import survivors
from tests.protocol_checks import failures, make


def test_real_module_passes_every_check() -> None:
    assert failures(protocol) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "standard|sorted removed|sorted((barrier_mappings or {}).items())|0": "there is one arm today (ARMS is ('L0',)), so its order cannot differ; the order is kept for the day there are two",
    "standard|sorted removed|sorted(mappings.items())|0": "there is one arm today (ARMS is ('L0',)), so its order cannot differ; the order is kept for the day there are two",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.protocol", "tests.protocol_checks")
    assert total > 200, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


_HEX = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)


@settings(max_examples=60, deadline=None)
@given(
    name=st.text(alphabet=st.characters(exclude_categories=["Cs", "Cn"]), min_size=1, max_size=40).filter(
        # the record profile's strings: NFC, no lone surrogate, only code points assigned in Unicode 15.0 (Cn left out above: Python 3.12's
        # tables are Unicode 15.0)
        lambda t: unicodedata.normalize("NFC", t) == t and "\x00" not in t and all(not 0xD800 <= ord(c) <= 0xDFFF for c in t)
    ),
    horizons=st.sets(st.sampled_from([1, 4, 24])).filter(bool).map(lambda s: tuple(sorted(s))),
    watchlist=st.sets(st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=10), max_size=6).map(
        lambda s: tuple(sorted(s))
    ),
    every_minutes=st.integers(min_value=1, max_value=10_000),
    scan_hash=_HEX,
    mapping=_HEX,
)
def test_any_valid_document_survives_a_round_trip(
    name: str, horizons: tuple[int, ...], watchlist: tuple[str, ...], every_minutes: int, scan_hash: str, mapping: str
) -> None:
    p = make(
        protocol,
        name=name,
        horizons_hours=horizons,
        watchlist=watchlist,
        schedule_every_ms=every_minutes * 60_000,
        scan_rule_hash=scan_hash,
        arms=(("L0", mapping),),
    )
    again = protocol.load(protocol.dump(p))
    assert again == p and again.sha256 == p.sha256
    assert protocol.dump(again) == protocol.dump(p)
