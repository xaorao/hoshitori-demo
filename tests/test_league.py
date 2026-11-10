"""One cycle of the league: the checks pass on the real module, every small change of its code is noticed, and any mix of candidates,
horizons and mappings leaves every slot accounted for."""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hoshitori import league, lifecycle, rules_arm
from tests.league_all_checks import failures
from tests.league_checks import World, before_the_schedule, histories, states
from tests.mutation_sweep import survivors
from tests.rules_arm_checks import rates


def test_real_module_passes_every_check() -> None:
    assert failures(league) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_price_checks|sorted removed|sorted(by_candidate.items())|0": "the candidates are visited in the order they were read, which the collector already fixes; sorting only makes it independent of that order",
    "_price_checks|statement dropped|continue|0": "a candidate with no price check gets an empty entry, which adds nothing to its evidence",
    "_decide_models|True<->False|True|0": "the two lists are made together and are always as long as each other; the flag only asserts it",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.league", "tests.league_all_checks")
    assert total > 150, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    watchlist=st.sets(st.sampled_from(["BTC", "ETH", "SOL"])).map(lambda s: tuple(sorted(s))),
    horizons=st.sets(st.sampled_from([1, 4, 24])).filter(bool).map(lambda s: tuple(sorted(s))),
    thin=st.sets(st.sampled_from(rules_arm.STATES)),
)
def test_every_slot_of_every_cycle_is_accounted_for(watchlist: tuple[str, ...], horizons: tuple[int, ...], thin: set[str]) -> None:
    """Whatever is watched, over whichever horizons, with whichever states of the mapping too thin to call: a committed cycle has one
    slot per candidate and horizon, each one a forecast, a no-call or a failure of evidence, none still undecided, and the forecasts are exactly
    the records that name a probability."""
    mapping = before_the_schedule(rates(rules_arm, lambda h, s: (50, 25, 0) if s in thin else (100, 60, 0)))
    with tempfile.TemporaryDirectory() as tmp:
        w = World(Path(tmp), watchlist=watchlist, mapping=mapping, protocol={"horizons_hours": horizons})
        out = w.run(league)
        candidates = {s for s in watchlist if s in ("BTC", "ETH")}  # SOL has no second oracle, so it is never a candidate
        assert out.state.value == "COMMITTED"
        assert len(out.slots) == len(candidates) * len(horizons)
        _, slots = histories(w)
        assert len(slots) == len(out.slots)
        ends = {states(h)[-1] for h in slots.values()}
        assert ends <= {"AWAITING_ORIGIN", "NO_CALL", "FAILED_EVIDENCE"}, ends
        calls = [s for s in out.slots if s.fate is league.Fate.FORECAST]
        assert len(w.records("forecast")) == len(calls)
        for h in slots.values():
            assert lifecycle.current_state(h) is not lifecycle.State.DECIDED
        w.close()
