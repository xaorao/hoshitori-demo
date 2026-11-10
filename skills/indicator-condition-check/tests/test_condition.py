"""Grammar validation, three-valued logic, cross events, boundary equality, history statistics."""
from __future__ import annotations

import pytest

from indicator_condition_check.condition import Bars, evaluate, normalize, parse_condition, required_bars
from indicator_condition_check.errors import InputError


def bars_from_closes(closes, volume=100.0):
    n = len(closes)
    return Bars(ts=list(range(n)), open=list(closes), high=[c + 1 for c in closes], low=[c - 1 for c in closes], close=list(closes), volume=[volume] * n)


def clause(left, op, right):
    return {"left": left, "op": op, "right": right}


CLOSE = {"indicator": "close"}


def val(x):
    return {"value": x}


# ----- malformed input is rejected with a field path and a working example -----------------------------------------

BAD_CONDITIONS = [
    ({}, "condition"),
    ([], "condition"),
    ("rsi<30", "condition"),
    ({"left": CLOSE, "op": "<"}, "condition"),
    ({"left": CLOSE, "op": "~", "right": val(1)}, "condition.op"),
    ({"left": {"indicator": "nope"}, "op": "<", "right": val(1)}, "condition.left.indicator"),
    ({"left": {"indicator": "rsi", "period": "14"}, "op": "<", "right": val(30)}, "condition.left.period"),
    ({"left": {"indicator": "rsi", "period": 1}, "op": "<", "right": val(30)}, "condition.left.period"),
    ({"left": {"indicator": "rsi", "period": True}, "op": "<", "right": val(30)}, "condition.left.period"),
    ({"left": {"indicator": "ema"}, "op": "<", "right": val(30)}, "condition.left.period"),
    ({"left": {"indicator": "rsi", "bogus": 1}, "op": "<", "right": val(30)}, "condition.left"),
    ({"left": {"indicator": "macd", "fast": 26, "slow": 12}, "op": ">", "right": val(0)}, "condition.left"),
    ({"left": {"indicator": "bbands", "output": "sideways"}, "op": ">", "right": val(0)}, "condition.left.output"),
    ({"left": CLOSE, "op": "<", "right": {"value": float("inf")}}, "condition.right.value"),
    ({"left": CLOSE, "op": "<", "right": {"value": "30"}}, "condition.right.value"),
    ({"left": CLOSE, "op": "<", "right": {"value": 1, "extra": 2}}, "condition.right"),
    ({"all": []}, "condition.all"),
    ({"all": [clause(CLOSE, "<", val(1))], "any": []}, "condition"),
    ({"not": clause(CLOSE, "<", val(1)), "extra": 1}, "condition"),
]


@pytest.mark.parametrize("raw,field", BAD_CONDITIONS)
def test_malformed_conditions_are_rejected_with_a_field_path(raw, field):
    with pytest.raises(InputError) as e:
        parse_condition(raw)
    assert e.value.field == field
    assert e.value.example is not None  # a working example always accompanies the refusal


def test_too_many_clauses_and_too_deep():
    nine = {"all": [clause(CLOSE, "<", val(i + 1)) for i in range(9)]}
    with pytest.raises(InputError, match="more than 8 clauses"):
        parse_condition(nine)
    deep = clause(CLOSE, "<", val(1))
    for _ in range(3):
        deep = {"not": deep}
    with pytest.raises(InputError, match="deeper than 3"):
        parse_condition(deep)
    parse_condition({"not": {"not": clause(CLOSE, "<", val(1))}})  # depth 3 is allowed


def test_defaults_are_made_explicit_in_the_normalized_form():
    node = parse_condition(clause({"indicator": "rsi"}, "<", val(30)))
    assert normalize(node)["left"] == {"indicator": "rsi", "period": 14, "source": "close"}


def test_required_bars_reflect_the_longest_warmup_and_cross_needs_one_more():
    assert required_bars(parse_condition(clause({"indicator": "rsi", "period": 14}, "<", val(30)))) == 15
    assert required_bars(parse_condition(clause(CLOSE, "cross_above", {"indicator": "ema", "period": 50}))) == 51
    assert required_bars(parse_condition(clause({"indicator": "macd", "output": "hist"}, ">", val(0)))) == 34


# ----- evaluation -------------------------------------------------------------------------------------------------

def test_cross_above_fires_only_on_the_bar_of_crossing():
    closes = [10, 10, 10, 9, 9, 11, 12, 12, 12]  # crosses above 10 at index 5
    ev = evaluate(parse_condition(clause(CLOSE, "cross_above", val(10))), bars_from_closes(closes))
    assert [i for i, v in enumerate(ev.series) if v is True] == [5]
    assert ev.series[0] is None  # no previous bar: undecided, not false
    assert ev.clauses[0].bars_since_last_true == len(closes) - 1 - 5


def test_cross_below_and_touching_is_not_a_cross():
    closes = [10, 11, 10, 9]
    ev = evaluate(parse_condition(clause(CLOSE, "cross_below", val(10))), bars_from_closes(closes))
    assert [i for i, v in enumerate(ev.series) if v is True] == [3]  # 11 -> 10 touches, 10 -> 9 crosses
    ev2 = evaluate(parse_condition(clause(CLOSE, "cross_above", val(10))), bars_from_closes([9, 10, 10]))
    assert ev2.series[1] is False  # reaching the level is not exceeding it


def test_comparison_boundaries():
    closes = [5.0, 5.0]
    b = bars_from_closes(closes)
    for op, want in (("<", False), ("<=", True), (">", False), (">=", True), ("==", True)):
        assert evaluate(parse_condition(clause(CLOSE, op, val(5))), b).series[-1] is want, op


def test_insufficient_history_is_null_not_false_and_says_why():
    ev = evaluate(parse_condition(clause({"indicator": "rsi", "period": 14}, "<", val(30))), bars_from_closes([1.0 + i for i in range(10)]))
    assert ev.series[-1] is None
    assert ev.clauses[0].satisfied is None
    assert "needs 15 bars" in ev.clauses[0].null_reason and "have 10" in ev.clauses[0].null_reason
    assert ev.defined_bars == 0 and ev.true_bars == 0


def test_flat_series_leaves_rsi_undefined_rather_than_inventing_a_value():
    ev = evaluate(parse_condition(clause({"indicator": "rsi"}, "<", val(30))), bars_from_closes([50.0] * 40))
    assert ev.series[-1] is None
    assert "undefined" in ev.clauses[0].null_reason


def test_kleene_logic_for_all_any_not():
    # clause A is true on the last bar; clause B is undecided (needs 200 bars)
    a = clause(CLOSE, ">", val(0))
    b = clause({"indicator": "sma", "period": 200}, ">", val(0))
    bars = bars_from_closes([5.0] * 30)
    assert evaluate(parse_condition({"all": [a, b]}), bars).series[-1] is None
    assert evaluate(parse_condition({"any": [a, b]}), bars).series[-1] is True
    f = clause(CLOSE, "<", val(0))
    assert evaluate(parse_condition({"all": [f, b]}), bars).series[-1] is False  # a certain false wins in all
    assert evaluate(parse_condition({"any": [f, b]}), bars).series[-1] is None
    assert evaluate(parse_condition({"not": b}), bars).series[-1] is None  # not(null) stays null
    assert evaluate(parse_condition({"not": f}), bars).series[-1] is True


def test_history_counts_and_last_true():
    closes = [1, 2, 3, 2, 1, 2, 3, 3]
    ev = evaluate(parse_condition(clause(CLOSE, ">=", val(3))), bars_from_closes(closes))
    assert ev.true_bars == 3 and ev.defined_bars == 8
    assert ev.last_true_index == 7
    assert ev.clauses[0].true_count == 3


def test_breakout_condition_uses_prior_bars_only():
    # highest_prior excludes the current bar, so a close above the previous 5 highs is a true breakout
    closes = [10, 10, 10, 10, 10, 20]
    ev = evaluate(parse_condition(clause(CLOSE, ">", {"indicator": "highest_prior", "period": 5})), bars_from_closes(closes))
    assert ev.series[-1] is True and ev.series[-2] is None
