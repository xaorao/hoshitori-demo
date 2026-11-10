"""Outcome review: planted settled calls get the observations their records support, and no word of cause."""

from __future__ import annotations

import pytest

from hoshitori import outcome_review as o
from hoshitori.view_models import Receipt, Row, SettlementLine, UnreadLine, Views


def row(fid: str, symbol: str, chance: str | None, contract: str = "D", cycle: str = "c1", hours: int = 1) -> Row:
    return Row(fid, cycle, "a", "Rules", symbol, hours, contract, "Settled", chance, "Rose", None, None, "t", "C3", None, None, None)


def receipt(r: Row, ref: str | None, out: str | None, state: str = "SETTLED", unread: tuple[UnreadLine, ...] = ()) -> Receipt:
    settlement = SettlementLine(state, "1", ref, out, "x", "settle/1", ())
    return Receipt(r, (), (), (), None, "", "", "", settlement, None, unread)


def views(*receipts: Receipt) -> Views:
    return Views(None, (), tuple(r.row for r in receipts), receipts, ())  # type: ignore[arg-type]


def test_a_call_near_one_half_is_low_conviction_and_nothing_else() -> None:
    out = o.build(views(receipt(row("f1", "ETH", "52% up"), "100", "101")))
    (rev,) = out.reviews
    assert [x.code for x in rev.observations] == ["LOW_CONVICTION"] and rev.agreed == "with the call" and rev.move == "+1.00%"


def test_a_clear_call_with_a_sound_record_has_no_defect_identified() -> None:
    (rev,) = o.build(views(receipt(row("f1", "ETH", "80% up"), "100", "90"))).reviews
    assert (
        [x.code for x in rev.observations] == ["NO_PROCESS_DEFECT_IDENTIFIED"]
        and rev.agreed == "against the call"
        and rev.outcome == "fell"
    )


def test_an_unread_source_is_an_evidence_gap() -> None:
    gap = (UnreadLine("news", "TIMEOUT", "no answer", 60),)
    (rev,) = o.build(views(receipt(row("f1", "ETH", "70% up"), "100", "99", unread=gap))).reviews
    assert [x.code for x in rev.observations] == ["EVIDENCE_GAP_AT_DECISION"] and "news" in rev.observations[0].text


def test_a_market_wide_move_is_consistent_with_an_alternative_and_never_a_reason() -> None:
    v = views(receipt(row("b", "BTC", "70% up"), "100", "99"), receipt(row("e", "ETH", "70% up"), "100", "98"))
    reviews = {r.symbol: r for r in o.build(v).reviews}
    assert [x.code for x in reviews["ETH"].observations] == ["MARKET_MOVEMENT_CONSISTENT_WITH_ALTERNATIVE"]
    assert [x.code for x in reviews["BTC"].observations] == ["NO_PROCESS_DEFECT_IDENTIFIED"]
    text = reviews["ETH"].observations[0].text
    assert "does not establish a reason" in text


def test_btc_moving_the_other_way_or_far_less_is_not_consistent() -> None:
    for btc_out in ("101", "99.99"):
        v = views(receipt(row("b", "BTC", "70% up"), "100", btc_out), receipt(row("e", "ETH", "70% up"), "100", "98"))
        assert "MARKET_MOVEMENT_CONSISTENT_WITH_ALTERNATIVE" not in [
            x.code for r in o.build(v).reviews if r.symbol == "ETH" for x in r.observations
        ]


def test_a_market_move_of_another_cycle_or_horizon_is_not_used() -> None:
    v = views(receipt(row("b", "BTC", "70% up", cycle="c2"), "100", "99"), receipt(row("e", "ETH", "70% up"), "100", "98"))
    assert all(x.code != "MARKET_MOVEMENT_CONSISTENT_WITH_ALTERNATIVE" for r in o.build(v).reviews for x in r.observations)


def test_only_settled_direction_calls_are_reviewed_and_the_rest_is_counted() -> None:
    v = views(
        receipt(row("f1", "ETH", "70% up"), "100", "101"),
        receipt(row("f2", "ETH", "target 4%, stop 4%", contract="B"), "100", "101"),
        receipt(row("f3", "ETH", "70% up"), None, None, state="VOID"),
    )
    out = o.build(v)
    assert [r.forecast_id for r in out.reviews] == ["f1"] and out.ledger.reviewed == 1 and out.ledger.not_reviewed == 1


def test_the_ledger_says_nothing_was_learned_and_no_text_names_a_cause() -> None:
    v = views(*[receipt(row(f"f{i}", "ETH", "50% up"), "100", "101") for i in range(9)])
    out = o.build(v)
    assert (out.ledger.proposed, out.ledger.tested, out.ledger.promoted, out.ledger.rejected) == (0, 0, 0, 0)
    assert "No agent has been changed" in out.ledger.statement and out.ledger.n_support == 10
    words = " ".join(x.text for r in out.reviews for x in r.observations) + out.ledger.statement
    assert not any(b in words.lower() for b in o.BANNED)


def test_every_code_is_listed() -> None:
    assert set(o.CODES) == {
        "LOW_CONVICTION",
        "EVIDENCE_GAP_AT_DECISION",
        "MARKET_MOVEMENT_CONSISTENT_WITH_ALTERNATIVE",
        "NO_PROCESS_DEFECT_IDENTIFIED",
    }


def test_the_words_banned_from_a_review_are_these() -> None:
    assert o.BANNED == ("caused", "explains", "unlucky", "bad luck")


def test_a_reference_of_zero_or_missing_prices_are_not_reviewed_and_do_not_fail() -> None:
    v = views(
        receipt(row("z", "ETH", "70% up"), "0", "5"),
        receipt(row("n", "ETH", "70% up"), None, "5"),
        receipt(row("m", "ETH", "70% up"), "5", None),
        receipt(row("x", "ETH", "70% up"), "x", "5"),
        receipt(row("same", "ETH", "70% up"), "5", "5"),
        receipt(row("ok", "ETH", "70% up"), "1", "2"),
    )
    out = o.build(v)
    assert [r.forecast_id for r in out.reviews] == ["ok"] and out.reviews[0].move == "+100.00%" and out.ledger.not_reviewed == 5


def test_a_call_that_is_not_settled_is_not_reviewed_even_with_prices() -> None:
    v = views(receipt(row("v", "ETH", "70% up"), "100", "101", state="VOID"), receipt(row("s", "ETH", "70% up"), "100", "101"))
    assert [r.forecast_id for r in o.build(v).reviews] == ["s"]
    nothing = Receipt(row("q", "ETH", "70% up"), (), (), (), None, "", "", "", None, None, ())
    assert o.build(views(nothing)).reviews == ()


def test_a_call_with_no_chance_or_a_chance_in_other_words_is_not_reviewed() -> None:
    v = views(
        receipt(row("a", "ETH", None), "100", "101"),
        receipt(row("b", "ETH", "no call"), "100", "101"),
        receipt(row("c", "ETH", "100% up"), "100", "99"),
    )
    assert [r.forecast_id for r in o.build(v).reviews] == ["c"]


def test_the_low_conviction_band_is_five_points_either_side() -> None:
    def codes(chance: str) -> list[str]:
        (rev,) = o.build(views(receipt(row("f", "ETH", chance), "100", "101"))).reviews
        return [x.code for x in rev.observations]

    assert codes("55% up") == ["LOW_CONVICTION"] and codes("45% up") == ["LOW_CONVICTION"] and codes("50% up") == ["LOW_CONVICTION"]
    assert codes("56% up") == ["NO_PROCESS_DEFECT_IDENTIFIED"] and codes("44% up") == ["NO_PROCESS_DEFECT_IDENTIFIED"]


def test_agreement_with_the_call_in_each_direction_and_the_words_used() -> None:
    def one(chance: str, out: str) -> o.Review:
        return o.build(views(receipt(row("f", "ETH", chance), "100", out))).reviews[0]

    assert one("70% up", "101").agreed == "with the call" and one("70% up", "101").outcome == "rose"
    assert one("70% up", "99").agreed == "against the call" and one("70% up", "99").outcome == "fell"
    assert one("30% up", "99").agreed == "with the call" and one("30% up", "101").agreed == "against the call"
    assert one("50% up", "101").agreed == "neither" and one("51% up", "101").agreed == "with the call"
    assert one("70% up", "99").observations[0].text == "Nothing in the stored record points to a defect in how this call was made."


def test_unread_sources_are_named_once_each_in_order() -> None:
    gap = (UnreadLine("news", "TIMEOUT", "x", 60), UnreadLine("funding", "TIMEOUT", "x", 60), UnreadLine("news", "TIMEOUT", "x", 60))
    (rev,) = o.build(views(receipt(row("f", "ETH", "70% up"), "100", "99", unread=gap))).reviews
    assert rev.observations[0].text == "A source asked for evidence of this call brought nothing: funding, news."


def test_the_market_consistency_rule_at_its_edges() -> None:
    def codes(btc_out: str, eth_out: str) -> list[str]:
        v = views(receipt(row("b", "BTC", "70% up"), "100", btc_out), receipt(row("e", "ETH", "70% up"), "100", eth_out))
        return [x.code for r in o.build(v).reviews if r.symbol == "ETH" for x in r.observations]

    assert "MARKET_MOVEMENT_CONSISTENT_WITH_ALTERNATIVE" in codes("98", "99")  # BTC fell twice as far, same way
    assert "MARKET_MOVEMENT_CONSISTENT_WITH_ALTERNATIVE" in codes("99.5", "99")  # exactly half as far
    assert "MARKET_MOVEMENT_CONSISTENT_WITH_ALTERNATIVE" not in codes("99.6", "99")
    assert "MARKET_MOVEMENT_CONSISTENT_WITH_ALTERNATIVE" not in codes("100", "99")  # BTC did not move
    assert "MARKET_MOVEMENT_CONSISTENT_WITH_ALTERNATIVE" not in codes("101", "99")
    assert "MARKET_MOVEMENT_CONSISTENT_WITH_ALTERNATIVE" in codes("102", "101")


def test_the_records_are_frozen() -> None:
    import pytest

    (rev,) = o.build(views(receipt(row("f", "ETH", "70% up"), "100", "101"))).reviews
    for thing, attribute in (
        (rev, "move"),
        (rev.observations[0], "code"),
        (o.build(views()), "reviews"),
        (o.build(views()).ledger, "proposed"),
    ):
        with pytest.raises(AttributeError):
            setattr(thing, attribute, "x")


def test_a_move_of_exactly_one_hundred_percent_is_still_a_move() -> None:
    v = views(receipt(row("b", "BTC", "70% up"), "100", "300"), receipt(row("e", "ETH", "70% up"), "100", "200"))
    reviews = {r.symbol: r for r in o.build(v).reviews}
    assert reviews["ETH"].move == "+100.00%" and "MARKET_MOVEMENT_CONSISTENT_WITH_ALTERNATIVE" in [
        x.code for x in reviews["ETH"].observations
    ]


def test_the_unread_sources_are_listed_in_name_order_and_the_chance_is_kept_as_stated() -> None:
    names = ["zeta", "alpha", "mu", "beta", "omega", "delta", "kappa", "gamma"]
    gap = tuple(UnreadLine(n, "TIMEOUT", "x", 60) for n in names)
    (rev,) = o.build(views(receipt(row("f", "ETH", "70% up"), "100", "99", unread=gap))).reviews
    assert rev.observations[0].text.endswith(": alpha, beta, delta, gamma, kappa, mu, omega, zeta.") and rev.chance == "70% up"


def many(n: int, chance: str = "52% up", out: str = "99", symbol: str = "ETH") -> list[Receipt]:
    return [receipt(row(f"f{k}", symbol, chance), "100", out) for k in range(n)]


def test_a_pattern_is_watched_below_the_support_and_proposed_at_it() -> None:
    watching = o.build(views(*many(9))).ledger
    (pattern,) = watching.patterns
    assert (pattern.arm_name, pattern.code, pattern.support, pattern.against_the_call, pattern.status) == (
        "Rules",
        "LOW_CONVICTION",
        9,
        9,
        "WATCHING",
    )
    assert pattern.change == {"kind": "ABSTAIN_WHEN_CHANCE_NEAR_ONE_HALF", "points": "5"} and watching.proposed == 0
    assert "No lesson has been proposed" in watching.statement and "1 pattern is being watched" in watching.statement
    proposing = o.build(views(*many(10))).ledger
    assert (
        proposing.patterns[0].status == "PROPOSED"
        and proposing.proposed == 1
        and (proposing.tested, proposing.promoted, proposing.rejected) == (0, 0, 0)
    )
    assert (
        "1 lesson is proposed" in proposing.statement
        and "None has been tested" in proposing.statement
        and "0 patterns are being watched" in proposing.statement
    )


def test_a_pattern_with_no_typed_change_is_only_a_description_however_often_it_matches() -> None:
    receipts = [receipt(row("b", "BTC", "70% up"), "100", "99")] + [receipt(row(f"e{k}", "ETH", "70% up"), "100", "98") for k in range(12)]
    ledger = o.build(views(*receipts)).ledger
    market = [p for p in ledger.patterns if p.code == "MARKET_MOVEMENT_CONSISTENT_WITH_ALTERNATIVE"]
    assert (
        len(market) == 1
        and market[0].support == 12
        and market[0].change is None
        and market[0].status == "WATCHING"
        and ledger.proposed == 0
    )


def test_patterns_are_kept_apart_by_agent_and_count_how_many_went_against_the_call() -> None:
    a = [receipt(row(f"a{k}", "ETH", "52% up", "D", "c1", 1), "100", "99") for k in range(3)]
    b = [
        receipt(
            Row(f"b{k}", "c1", "x", "Debate", "ETH", 1, "D", "Settled", "52% up", "Rose", None, None, "t", "C3", None, None, None),
            "100",
            "101",
        )
        for k in range(2)
    ]
    patterns = {(p.arm_name, p.code): p for p in o.build(views(*a, *b)).ledger.patterns}
    assert (patterns["Rules", "LOW_CONVICTION"].support, patterns["Rules", "LOW_CONVICTION"].against_the_call) == (3, 3)
    assert (patterns["Debate", "LOW_CONVICTION"].support, patterns["Debate", "LOW_CONVICTION"].against_the_call) == (2, 0)
    assert list(patterns) == sorted(patterns) and o.build(views()).ledger.patterns == ()


def test_a_call_with_no_defect_adds_to_no_pattern_and_the_statement_names_no_cause() -> None:
    ledger = o.build(views(*many(12, "80% up", "101"))).ledger
    assert ledger.patterns == () and not any(b in ledger.statement.lower() for b in o.BANNED)
    assert o.CHANGES.keys() == {"LOW_CONVICTION", "EVIDENCE_GAP_AT_DECISION"}


def test_the_typed_changes_and_the_patterns_are_pinned_and_frozen() -> None:
    assert o.CHANGES == {
        "LOW_CONVICTION": {"kind": "ABSTAIN_WHEN_CHANCE_NEAR_ONE_HALF", "points": "5"},
        "EVIDENCE_GAP_AT_DECISION": {"kind": "REQUIRE_EVERY_ASKED_SOURCE_OR_NO_CALL"},
    }
    pattern = o.build(views(*many(2))).ledger.patterns[0]
    with pytest.raises(AttributeError):
        pattern.support = 5  # type: ignore[misc]
    gap = (UnreadLine("news", "TIMEOUT", "x", 60),)
    gaps = [receipt(row(f"g{k}", "ETH", "80% up"), "100", "99", unread=gap) for k in range(10)]
    (found,) = o.build(views(*gaps)).ledger.patterns
    assert (found.code, found.support, found.status, found.change) == (
        "EVIDENCE_GAP_AT_DECISION",
        10,
        "PROPOSED",
        {"kind": "REQUIRE_EVERY_ASKED_SOURCE_OR_NO_CALL"},
    )
