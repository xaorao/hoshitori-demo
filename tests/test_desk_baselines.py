"""The fixed-order baseline and the Desk's dependency rule, side by side on planted worlds (``hoshitori.desk_baselines``).

The real menu of a token, in the order the menu lists it, is: derivatives funding (1), the three price checks (2, 3, 4), market overview (5,
a context check), sentiment shift (6, context), RYO deep analysis (7, an interpretation that needs the token read first) and the RYO
token read (8). The Desk's own rule offers only checks that can resolve an observation, preferring the price checks, then derivatives,
then the token read; so it opens them as spot price, USD-M price, Kraken price, funding, token read."""

from __future__ import annotations

from decimal import Decimal

import pytest

from hoshitori import claim_graph as cg
from hoshitori import desk_baselines as db
from hoshitori import desk_menu
from tests.claim_next_check_checks import golden
from tests.desk_menu_checks import menu

SPOT = "INDEPENDENT_PRICE_CHECK:binance-spot:candles"
USDM = "INDEPENDENT_PRICE_CHECK:binance-usdm:candles"
KRAKEN = "INDEPENDENT_PRICE_CHECK:kraken:candles"
FUNDING = "DERIVATIVES_CONTEXT:binance-usdm:funding"
READ = "RYO_TOKEN_READ:ryo:analyze_token"
OVERVIEW = "MARKET_CONTEXT_OVERVIEW:ryo:market_overview"
SENTIMENT = "MARKET_SENTIMENT_SHIFT:ryo:monitor_market_sentiment_shift"
DEEP = "RYO_DEEP_ANALYSIS:ryo:deep_analysis"
MENU_ORDER = [FUNDING, SPOT, USDM, KRAKEN, OVERVIEW, SENTIMENT, DEEP, READ]
DEPENDENCY_ORDER = [SPOT, USDM, KRAKEN, FUNDING, READ]
LOW = "CONTRADICTED"


def world(answers: dict[tuple[str, str], str], budget: int) -> db.World:
    return db.World(golden(cg), menu(desk_menu), answers, budget)


def test_the_menu_and_the_rules_order_are_what_the_tests_assume() -> None:
    assert [c.check_id for c in menu(desk_menu).checks] == MENU_ORDER
    from hoshitori import claim_next_check as nc

    assert [c.check_id for c in nc.registry_from_menu(menu(desk_menu))] == DEPENDENCY_ORDER


def test_where_the_first_check_decides_the_dependency_rule_stops_and_the_fixed_agent_spends_its_whole_budget() -> None:
    w = world({(SPOT, "obs"): LOW}, 3)
    fixed, dep = db.run(w, db.FIXED), db.run(w, db.DEPENDENCY)
    assert fixed.opened == (FUNDING, SPOT, USDM) and fixed.reached == {"obs"}
    assert dep.opened == (SPOT,) and dep.reached == {"obs"}
    assert dep.statuses == {"obs": LOW}
    assert fixed.seconds == Decimal("0.250") + Decimal("0.300") + Decimal("0.300")
    assert dep.seconds == Decimal("0.300")


def test_a_decisive_check_late_in_the_menu_is_missed_by_the_fixed_agent_and_found_by_the_dependency_rule() -> None:
    w = world({(READ, "obs"): LOW}, 5)
    fixed, dep = db.run(w, db.FIXED), db.run(w, db.DEPENDENCY)
    assert fixed.opened == (FUNDING, SPOT, USDM, KRAKEN, OVERVIEW) and fixed.reached == frozenset()
    assert dep.opened == tuple(DEPENDENCY_ORDER) and dep.reached == {"obs"}


def test_the_fixed_agent_never_opens_a_check_before_its_prerequisite() -> None:
    w = world({(DEEP, "obs"): LOW}, 8)
    fixed = db.run(w, db.FIXED)
    assert DEEP not in fixed.opened and fixed.opened == (
        FUNDING,
        SPOT,
        USDM,
        KRAKEN,
        OVERVIEW,
        SENTIMENT,
        READ,
    )  # deep analysis is skipped, 7 opened
    assert fixed.reached == frozenset()


def test_nothing_to_find_means_nothing_is_reached_and_the_rules_spend_what_they_spend() -> None:
    w = world({(SPOT, "obs"): "SUPPORTED"}, 6)
    fixed, dep = db.run(w, db.FIXED), db.run(w, db.DEPENDENCY)
    assert fixed.reached == dep.reached == frozenset() and db.gold(w) == frozenset()
    assert len(fixed.opened) == 6
    assert dep.opened == (SPOT,)  # a part that is supported is not looked at again, and nothing else is open


def test_a_budget_too_small_for_the_decisive_check_defeats_both() -> None:
    w = world({(KRAKEN, "obs"): LOW}, 2)
    assert db.run(w, db.FIXED).reached == frozenset() and db.run(w, db.DEPENDENCY).reached == frozenset()
    assert db.gold(w) == {"obs"}


def test_a_decision_that_a_later_check_would_overturn_is_not_overturned_and_a_part_the_claim_does_not_need_is_not_gold() -> None:
    w = world({(SPOT, "obs"): LOW, (USDM, "obs"): "SUPPORTED"}, 3)
    assert db.run(w, db.FIXED).statuses["obs"] == LOW  # opened spot first, then usdm said supported: the decision stays
    only_the_forecast = world({(SPOT, "up"): LOW}, 3)
    assert db.gold(only_the_forecast) == frozenset()  # nothing depends on the forecast: it is not a needed part


def test_the_comparison_counts_over_every_world_with_wilson_intervals_and_names_the_worlds_each_policy_missed() -> None:
    worlds = [
        world({(SPOT, "obs"): LOW}, 3),  # both reach it
        world({(READ, "obs"): LOW}, 5),  # only the dependency rule does
        world({(KRAKEN, "obs"): LOW}, 2),  # neither does
        world({(SPOT, "obs"): "SUPPORTED"}, 4),  # nothing to find
    ]
    fixed, dep = db.compare(worlds)
    assert (fixed.policy, fixed.worlds, fixed.gold, fixed.reached, fixed.missed_worlds) == (db.FIXED, 4, 3, 1, (1, 2))
    assert (dep.policy, dep.worlds, dep.gold, dep.reached, dep.missed_worlds) == (db.DEPENDENCY, 4, 3, 2, (2,))
    assert fixed.reached_low is not None and dep.reached_low is not None and dep.reached_low > fixed.reached_low
    assert fixed.opened == 3 + 5 + 2 + 4 and dep.opened == 1 + 5 + 2 + 1
    assert dep.seconds < fixed.seconds
    assert fixed.seconds == Decimal("124.30")  # 0.85 + 121.45 + 0.55 + 1.45, world by world
    assert dep.seconds == Decimal("57.65")  # 0.30 + 56.45 + 0.60 + 0.30
    empty = db.compare([world({(SPOT, "obs"): "SUPPORTED"}, 2)])
    assert (empty[0].gold, empty[0].reached_low, empty[0].reached_high) == (0, None, None)


def test_a_world_and_a_policy_that_cannot_be_run_are_refused_in_words() -> None:
    def refuses(call: object, says: str) -> None:
        with pytest.raises(db.BaselineError) as raised:
            call()  # type: ignore[operator]
        assert str(raised.value) == says

    assert db.run(world({}, 1), db.FIXED).opened == (FUNDING,) and db.run(world({}, 1), db.DEPENDENCY).opened == (
        SPOT,
    )  # a budget of one is a budget
    refuses(lambda: world({}, 0), "a budget is a whole number of checks, one or more")
    refuses(lambda: world({}, True), "a budget is a whole number of checks, one or more")
    refuses(lambda: world({("nope", "obs"): LOW}, 2), "an answer is given for a check that is not on the menu: nope")
    refuses(lambda: world({(SPOT, "zzz"): LOW}, 2), "an answer is given for a part the claim does not have: zzz")
    refuses(lambda: db.run(world({}, 2), "P-OTHER"), "a policy is one of ('P-FIXED', 'P-DEPENDENCY')")
