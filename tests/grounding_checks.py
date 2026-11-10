"""Checks of the grounding layers (``hoshitori.grounding``), runnable against a mutant: every kind of assertion against items that hold the
number, hold another number, hold nothing, belong to another asset, cover another window or are another unit."""

from __future__ import annotations

import types
from decimal import Decimal
from typing import Any

from hoshitori import assertions, evidence
from hoshitori.evidence import Unknown
from tests.evidence_checks import observation

D = Decimal
BTC = "tk-btc"
ETH = "tk-eth"
EVIDENCE = {
    name: "ev" + chr(ord("a") + i) * 26
    for i, name in enumerate(
        (
            "feat",
            "feat2",
            "series",
            "text",
            "other",
            "market",
            "ambiguous",
            "badtext",
            "badflag",
            "badnan",
            "warned",
            "pair",
            "up_edge",
            "down_edge",
            "over",
            "big",
            "edge",
        )
    )
}


def item(name: str, fields: tuple[tuple[str, Any], ...], asset: str | None = BTC, status: str = "RESOLVED", **over: Any) -> Any:
    refs = () if asset is None else ((asset, f"venue/{name}", status),)
    return observation(
        evidence,
        path=name,
        fields=fields,
        unknown_fields=over.pop("unknown_fields", ()),
        asset_refs=refs,
        capture_id=f"cap-{name}",
        warnings=over.pop("warnings", ()),
        **over,
    )


def world() -> tuple[dict[str, Any], dict[str, str]]:
    """The items an arm was shown, by their ids, and the id of each by name."""
    feat = item(
        "feat",
        (
            ("atr14", "30.5"),
            ("atr14_share", "0.003"),
            ("close", "10000.5"),
            ("ret_1h", "0.0042"),
            ("ret_24h", "-0.01"),
            ("ret_4h", "0.0000"),
        ),
    )
    feat2 = item("feat2", (("atr14", "40"), ("close", "10001")), unknown_fields=(("ret_1h", Unknown.NULL),))
    series = item("series", (("count", "60"), ("last_close", "10000.5"), ("note", "abc"), ("ok", True), ("zz", "NaN")))
    text = item(
        "text",
        (
            ("contraction", "The listing isn't confirmed"),
            ("curly", "The listing isn\u2019t confirmed"),
            ("headline", "Exchange will list BTC perpetual"),
            ("limit", "volume at least doubled over the last day"),
            ("neg", "The listing is not confirmed"),
            ("rumor", "Reportedly a listing may come"),
            ("tricky", "The mayor said we know the perpetual plan"),
        ),
    )
    other = item("other", (("ret_1h", "0.0042"),), asset=ETH)
    market = item("market", (("last_close", "55"), ("ret_1h", "0.0042")), asset=None)
    ambiguous = item("ambiguous", (("ret_1h", "0.0042"),), status="AMBIGUOUS")
    badtext = item("badtext", (("close", "abc"),))
    badflag = item("badflag", (("count", True),))
    badnan = item("badnan", (("atr14", "NaN"),))
    warned = item("warned", (("a", "1"),), warnings=("one section is partial",))
    pair = observation(
        evidence,
        path="pair",
        fields=(("ret_1h", "0.0042"),),
        unknown_fields=(),
        asset_refs=((BTC, "v/a", "RESOLVED"), (ETH, "v/b", "RESOLVED")),
        capture_id="cap-pair",
    )
    up_edge = item("up_edge", (("ret_1h", "0.0005"),))
    down_edge = item("down_edge", (("ret_1h", "-0.0005"),))
    over = item("over", (("ret_1h", "0.0006"), ("ret_4h", "-0.0006")))
    digits = item("digits", (("atr14", "0.0000123"), ("close", "79000.99"), ("ret_1h", "-0.007851934941"), ("ret_4h", "0.019")))
    big = item("big", (("close", "14999"),))
    edge = item("edge", (("close", "101"),))
    built = {
        "pair": pair,
        "up_edge": up_edge,
        "down_edge": down_edge,
        "over": over,
        "big": big,
        "edge": edge,
        "feat": feat,
        "digits": digits,
        "feat2": feat2,
        "series": series,
        "text": text,
        "other": other,
        "market": market,
        "ambiguous": ambiguous,
        "badtext": badtext,
        "badflag": badflag,
        "badnan": badnan,
        "warned": warned,
    }
    ids = {name: it.evidence_id for name, it in built.items()}
    return {it.evidence_id: it for it in built.values()}, ids


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want or type(got) is not type(want):
            self.bad.append(f"{label}: got {got!r} ({type(got).__name__}), expected {want!r} ({type(want).__name__})")


def a_numeric(ev: str, path: str, op: str, value: str, unit: str, window: str) -> Any:
    return assertions.Assertion(
        "a1", assertions.AssertionKind.NUMERIC, op, D(value), unit, window, (assertions.Cite(ev, path),), None, None
    )


def a_direction(ev: str, path: str, op: str, window: str) -> Any:
    return assertions.Assertion("a1", assertions.AssertionKind.DIRECTION, op, None, None, window, (assertions.Cite(ev, path),), None, None)


def a_compare(left: tuple[str, str], right: tuple[str, str], op: str, unit: str, window: str) -> Any:
    cites = (assertions.Cite(*left), assertions.Cite(*right))
    return assertions.Assertion("a1", assertions.AssertionKind.COMPARISON, op, None, unit, window, cites, None, None)


def a_statement(kind: str, ev: str, quote: str, text: str, path: str = "headline") -> Any:
    return assertions.Assertion("a1", assertions.AssertionKind[kind], None, None, None, None, (assertions.Cite(ev, path),), quote, text)


def check_registry(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    from hoshitori import features

    c.eq("the band and the tolerance", (mod.FLAT_BAND, mod.APPROX_REL), (D("0.0005"), D("0.01")))
    c.eq("every feature has a meaning", set(features.NAMES) <= set(mod.FIELD_FACTS), True)
    c.eq(
        "the changes are the returns, and the percentage changes RYO gives",
        sorted(k for k, f in mod.FIELD_FACTS.items() if f.change),
        [
            "performance.change_1h_pct",
            "performance.change_24h_pct",
            "performance.change_30d_pct",
            "performance.change_7d_pct",
            "ret_1h",
            "ret_24h",
            "ret_4h",
            "technical_analysis.momentum_30d_pct",
        ],
    )
    c.eq(
        "the meanings of the features",
        {k: (f.window, f.unit) for k, f in mod.FIELD_FACTS.items() if k in features.NAMES},
        {
            "atr14": ("spot", "price"),
            "atr14_share": ("spot", "ratio"),
            "close": ("spot", "price"),
            "ret_1h": ("1h", "ratio"),
            "ret_24h": ("24h", "ratio"),
            "ret_4h": ("4h", "ratio"),
        },
    )
    c.eq(
        "the series counts are counts",
        {k: mod.FIELD_FACTS[k].unit for k in ("count", "missing", "pending", "excluded_unfinished", "interval_s")},
        dict.fromkeys(("count", "missing", "pending", "excluded_unfinished", "interval_s"), "count"),
    )
    c.eq("a series close is a price", (mod.FIELD_FACTS["last_close"].unit, mod.FIELD_FACTS["last_close"].window), ("price", "spot"))
    c.eq(
        "labels that are false statements",
        sorted(x.value for x in mod.FALSE_STATEMENTS),
        ["REFUTES", "WRONG_SCOPE_ASSET", "WRONG_SCOPE_QUALIFIER", "WRONG_SCOPE_SIGN", "WRONG_SCOPE_TIME", "WRONG_SCOPE_UNIT"],
    )
    c.eq(
        "every label",
        [x.value for x in mod.Label],
        [
            "SUPPORTS",
            "REFUTES",
            "NOT_ENOUGH_INFO",
            "WRONG_SCOPE_ASSET",
            "WRONG_SCOPE_TIME",
            "WRONG_SCOPE_UNIT",
            "WRONG_SCOPE_SIGN",
            "WRONG_SCOPE_QUALIFIER",
            "SUPPORT_UNVERIFIED",
            "OPINION_CONTEXT",
        ],
    )
    c.eq(
        "frozen",
        (mod.Fact.__dataclass_params__.frozen, mod.Verdict.__dataclass_params__.frozen, mod.Report.__dataclass_params__.frozen),
        (True, True, True),
    )
    return c.bad


def run(mod: types.ModuleType, a: Any, asset: str = BTC) -> Any:
    items, _ = world()
    return mod.ground_one(a, items, asset)


def check_numeric(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    _, ids = world()
    f = ids["feat"]
    L = mod.Label

    def says(a: Any) -> tuple[Any, int, bool, str]:
        v = run(mod, a)
        return v.label, v.layer, v.citation_exists, v.reason

    c.eq(
        "equal at the precision stated",
        says(a_numeric(f, "ret_1h", "eq", "0.42", "percent", "1h")),
        (L.SUPPORTS, 1, True, "ret_1h is 0.42 percent; the assertion says 0.42"),
    )
    c.eq("rounded to one place", says(a_numeric(f, "ret_1h", "eq", "0.4", "percent", "1h"))[0], L.SUPPORTS)
    c.eq(
        "the wrong number",
        says(a_numeric(f, "ret_1h", "eq", "0.5", "percent", "1h")),
        (L.REFUTES, 1, True, "ret_1h is 0.42 percent; the assertion says 0.5"),
    )
    c.eq("a ratio", says(a_numeric(f, "ret_1h", "eq", "0.0042", "ratio", "1h"))[0], L.SUPPORTS)
    c.eq("a ratio to three places", says(a_numeric(f, "ret_1h", "eq", "0.004", "ratio", "1h"))[0], L.SUPPORTS)
    dec = __import__("decimal").Decimal
    c.eq(
        "units convert by a factor of exactly one hundred, or not at all",
        [mod._unit_value(dec(50), a, b) for a, b in (("percent", "ratio"), ("ratio", "percent"), ("ratio", "ratio"), ("percent", "price"))],
        [dec("0.5"), dec(5000), dec(50), None],
    )
    c.eq(
        "a ratio to six places is exactly the percent over a hundred",
        says(a_numeric(f, "ret_1h", "eq", "0.004200", "ratio", "1h"))[0],
        L.SUPPORTS,
    )
    c.eq("a ratio that is not that ratio", says(a_numeric(f, "ret_1h", "eq", "0.0043", "ratio", "1h"))[0], L.REFUTES)
    c.eq("a price", says(a_numeric(f, "close", "eq", "10000.5", "price", "spot"))[0], L.SUPPORTS)
    c.eq("a price to an integer", says(a_numeric(f, "close", "eq", "10000", "price", "spot"))[0], L.SUPPORTS)
    d = ids["digits"]
    for label, field, value, unit, window, want in (
        ("digits copied and cut off early", "ret_1h", "-0.007851", "ratio", "1h", L.SUPPORTS),
        ("digits rounded", "ret_1h", "-0.007852", "ratio", "1h", L.SUPPORTS),
        ("trailing zeros are not places", "ret_1h", "-0.007850", "ratio", "1h", L.SUPPORTS),
        ("a digit changed below the cut", "ret_1h", "-0.007849", "ratio", "1h", L.REFUTES),
        ("a digit changed above the rounding", "ret_1h", "-0.007853", "ratio", "1h", L.REFUTES),
        ("a cut with the sign kept", "ret_1h", "-0.00785", "ratio", "1h", L.SUPPORTS),
        ("a cut that loses the sign", "ret_1h", "0.007851", "ratio", "1h", L.WRONG_SCOPE_SIGN),
        ("a number cut to three places", "ret_4h", "0.019", "ratio", "4h", L.SUPPORTS),
        ("a number cut to two places", "ret_4h", "0.01", "ratio", "4h", L.SUPPORTS),
        ("the same rounded to two places", "ret_4h", "0.02", "ratio", "4h", L.SUPPORTS),
        ("one place beyond", "ret_4h", "0.03", "ratio", "4h", L.REFUTES),
        ("a price cut to the integer", "close", "79000", "price", "spot", L.SUPPORTS),
        ("a price rounded to the integer", "close", "79001", "price", "spot", L.SUPPORTS),
        ("a price two off", "close", "79002", "price", "spot", L.REFUTES),
        ("a price with its cents cut", "close", "79000.9", "price", "spot", L.SUPPORTS),
        ("a price with its cents rounded", "close", "79001.0", "price", "spot", L.SUPPORTS),
        ("a small number cut", "atr14", "0.0000123", "price", "spot", L.SUPPORTS),
        ("a small number with the last digit changed", "atr14", "0.0000124", "price", "spot", L.REFUTES),
    ):
        c.eq(label, says(a_numeric(d, field, "eq", value, unit, window))[0], want)
    c.eq("a percent cut to the places stated", says(a_numeric(d, "ret_1h", "eq", "-0.78", "percent", "1h"))[0], L.SUPPORTS)
    c.eq("and rounded", says(a_numeric(d, "ret_1h", "eq", "-0.79", "percent", "1h"))[0], L.SUPPORTS)
    c.eq("and one off", says(a_numeric(d, "ret_1h", "eq", "-0.77", "percent", "1h"))[0], L.REFUTES)
    c.eq("a price that is not it", says(a_numeric(f, "close", "eq", "10001", "price", "spot"))[0], L.REFUTES)
    c.eq("a count", says(a_numeric(ids["series"], "count", "eq", "60", "count", "spot"))[0], L.SUPPORTS)
    c.eq("zero against zero", says(a_numeric(f, "ret_4h", "eq", "0", "ratio", "4h"))[0], L.SUPPORTS)
    c.eq(
        "a negative number",
        says(a_numeric(f, "ret_24h", "eq", "-1", "percent", "24h")),
        (L.SUPPORTS, 1, True, "ret_24h is -1 percent; the assertion says -1"),
    )
    c.eq(
        "the sign dropped",
        says(a_numeric(f, "ret_24h", "eq", "1", "percent", "24h")),
        (L.WRONG_SCOPE_SIGN, 1, True, "ret_24h is -1 percent; the assertion says 1 (the sign was dropped)"),
    )
    c.eq("the sign dropped on an about", says(a_numeric(f, "ret_24h", "approx", "1", "percent", "24h"))[0], L.WRONG_SCOPE_SIGN)
    c.eq("zero is not a sign", says(a_numeric(f, "ret_4h", "eq", "1", "percent", "4h"))[0], L.REFUTES)
    c.eq("about: close enough", says(a_numeric(f, "ret_1h", "approx", "0.00421", "ratio", "1h"))[0], L.SUPPORTS)
    c.eq("about: the edge of one percent", says(a_numeric(f, "close", "approx", "10100", "price", "spot"))[0], L.SUPPORTS)
    c.eq("about: just beyond it", says(a_numeric(f, "close", "approx", "10102", "price", "spot"))[0], L.REFUTES)
    c.eq("about: too far", says(a_numeric(f, "ret_1h", "approx", "0.0043", "ratio", "1h"))[0], L.REFUTES)
    c.eq(
        "about zero",
        (says(a_numeric(f, "ret_4h", "approx", "0", "ratio", "4h"))[0], says(a_numeric(f, "ret_1h", "approx", "0", "ratio", "1h"))[0]),
        (L.SUPPORTS, L.REFUTES),
    )
    for op, value, want in (
        ("gt", "0.0041", L.SUPPORTS),
        ("gt", "0.0042", L.REFUTES),
        ("ge", "0.0042", L.SUPPORTS),
        ("ge", "0.0043", L.REFUTES),
        ("lt", "0.0043", L.SUPPORTS),
        ("lt", "0.0042", L.REFUTES),
        ("le", "0.0042", L.SUPPORTS),
        ("le", "0.0041", L.REFUTES),
    ):
        c.eq(f"{op} {value}", says(a_numeric(f, "ret_1h", op, value, "ratio", "1h"))[0], want)
    c.eq("a percent against a ratio field is converted", says(a_numeric(f, "ret_1h", "gt", "0.4", "percent", "1h"))[0], L.SUPPORTS)
    c.eq(
        "a window other than the field's",
        says(a_numeric(f, "ret_1h", "eq", "0.42", "percent", "4h")),
        (L.WRONG_SCOPE_TIME, 1, True, "ret_1h covers 1h, the assertion says 4h"),
    )
    c.eq("a spot field asked about over an hour", says(a_numeric(f, "close", "eq", "10000.5", "price", "1h"))[0], L.WRONG_SCOPE_TIME)
    c.eq("a return asked about as spot", says(a_numeric(f, "ret_1h", "eq", "0.42", "percent", "spot"))[0], L.WRONG_SCOPE_TIME)
    c.eq(
        "a ratio called a price",
        says(a_numeric(f, "ret_1h", "eq", "0.0042", "price", "1h")),
        (L.WRONG_SCOPE_UNIT, 1, True, "ret_1h is a ratio, the assertion says price"),
    )
    c.eq("a price called a percent", says(a_numeric(f, "close", "eq", "10000.5", "percent", "spot"))[0], L.WRONG_SCOPE_UNIT)
    c.eq("a count called a ratio", says(a_numeric(ids["series"], "count", "eq", "60", "ratio", "spot"))[0], L.WRONG_SCOPE_UNIT)
    c.eq("a ratio called a count", says(a_numeric(f, "atr14_share", "eq", "0", "count", "spot"))[0], L.WRONG_SCOPE_UNIT)
    c.eq("a price called a ratio", says(a_numeric(f, "close", "eq", "1", "ratio", "spot"))[0], L.WRONG_SCOPE_UNIT)
    c.eq(
        "another asset",
        says(a_numeric(ids["other"], "ret_1h", "eq", "0.42", "percent", "1h")),
        (L.WRONG_SCOPE_ASSET, 1, True, "the cited item is about another asset than tk-btc"),
    )
    c.eq(
        "an asset that is only ambiguously this one",
        says(a_numeric(ids["ambiguous"], "ret_1h", "eq", "0.42", "percent", "1h"))[0],
        L.WRONG_SCOPE_ASSET,
    )
    c.eq("an item about the whole market", says(a_numeric(ids["market"], "last_close", "eq", "55", "price", "spot"))[0], L.SUPPORTS)
    c.eq("the same item for another asset", run(mod, a_numeric(f, "ret_1h", "eq", "0.42", "percent", "1h"), ETH).label, L.WRONG_SCOPE_ASSET)
    c.eq(
        "a field with no meaning",
        says(a_numeric(f, "rsi", "eq", "50", "count", "spot")),
        (L.NOT_ENOUGH_INFO, 1, True, "rsi has no registered meaning, so its unit and window cannot be checked"),
    )
    c.eq(
        "a field the item does not have",
        says(a_numeric(ids["feat2"], "ret_4h", "eq", "0", "ratio", "4h")),
        (L.NOT_ENOUGH_INFO, 1, True, "ret_4h is not a field of the cited item"),
    )
    c.eq(
        "a field that was not measured",
        says(a_numeric(ids["feat2"], "ret_1h", "eq", "0", "ratio", "1h")),
        (L.NOT_ENOUGH_INFO, 1, True, "ret_1h is not measured in the cited item (NULL)"),
    )
    c.eq("a price that is not the series close", says(a_numeric(ids["series"], "last_close", "eq", "1", "price", "spot"))[0], L.REFUTES)
    c.eq(
        "a text field has no registered meaning",
        run(mod, a_numeric(ids["series"], "note", "eq", "1", "price", "spot")).reason,
        "note has no registered meaning, so its unit and window cannot be checked",
    )
    c.eq(
        "a field that is text",
        says(a_numeric(ids["badtext"], "close", "eq", "1", "price", "spot")),
        (L.NOT_ENOUGH_INFO, 1, True, "close is text, not a number"),
    )
    c.eq(
        "a field that is a flag",
        says(a_numeric(ids["badflag"], "count", "eq", "1", "count", "spot")),
        (L.NOT_ENOUGH_INFO, 1, True, "count is a flag, not a number"),
    )
    c.eq(
        "a field that is not finite",
        says(a_numeric(ids["badnan"], "atr14", "eq", "1", "price", "spot")),
        (L.NOT_ENOUGH_INFO, 1, True, "atr14 is not a finite number"),
    )
    c.eq(
        "a missing cited item",
        (lambda v: (v.label, v.layer, v.citation_exists, v.reason))(
            run(mod, a_numeric("ev" + "z" * 26, "ret_1h", "eq", "0.42", "percent", "1h"))
        ),
        (L.NOT_ENOUGH_INFO, 1, False, "a cited item was not among the items shown"),
    )
    c.eq(
        "the verdict names the assertion, the kind and the sentence",
        (lambda v: (v.assertion_id, v.kind.value, v.sentence))(run(mod, a_numeric(f, "ret_1h", "eq", "0.42", "percent", "1h"))),
        ("a1", "NUMERIC", "ret_1h is 0.42% over the last 1h"),
    )
    return c.bad


def check_non_numbers(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    _, ids = world()
    f = ids["feat"]
    L = mod.Label

    def one(a: Any, asset: str = BTC) -> tuple[Any, int, bool, str]:
        v = run(mod, a, asset)
        return v.label, v.layer, v.citation_exists, v.reason

    # direction
    c.eq("rose", one(a_direction(f, "ret_1h", "rose", "1h")), (L.SUPPORTS, 1, True, "ret_1h is 0.0042"))
    c.eq("rose against a fall", one(a_direction(f, "ret_24h", "rose", "24h"))[0], L.REFUTES)
    c.eq("fell", one(a_direction(f, "ret_24h", "fell", "24h"))[0], L.SUPPORTS)
    c.eq("fell against a rise", one(a_direction(f, "ret_1h", "fell", "1h"))[0], L.REFUTES)
    c.eq("flat when it is zero", one(a_direction(f, "ret_4h", "flat", "4h"))[0], L.SUPPORTS)
    c.eq("flat when it is not", one(a_direction(f, "ret_1h", "flat", "1h"))[0], L.REFUTES)
    c.eq("rose when it is zero", one(a_direction(f, "ret_4h", "rose", "4h"))[0], L.REFUTES)
    c.eq("not a rise inside the band", one(a_direction(ids["feat"], "ret_4h", "rose", "4h"))[0], L.REFUTES)
    c.eq(
        "a direction in the wrong window",
        one(a_direction(f, "ret_1h", "rose", "4h")),
        (L.WRONG_SCOPE_TIME, 1, True, "ret_1h covers 1h, the assertion says 4h"),
    )
    c.eq(
        "a direction of a price",
        one(a_direction(f, "close", "rose", "spot")),
        (L.NOT_ENOUGH_INFO, 1, True, "close is not a registered signed change, so a direction cannot be read from it"),
    )
    c.eq("a direction of a field with no meaning", one(a_direction(f, "rsi", "rose", "1h"))[0], L.NOT_ENOUGH_INFO)
    c.eq(
        "a direction of another asset",
        one(a_direction(ids["other"], "ret_1h", "rose", "1h")),
        (L.WRONG_SCOPE_ASSET, 1, True, "the cited item is about another asset than tk-btc"),
    )
    c.eq(
        "a direction of a field not measured",
        one(a_direction(ids["feat2"], "ret_1h", "rose", "1h")),
        (L.NOT_ENOUGH_INFO, 1, True, "ret_1h is not measured in the cited item (NULL)"),
    )
    c.eq("a direction with a missing item", one(a_direction("ev" + "z" * 26, "ret_1h", "rose", "1h"))[2], False)
    # comparison
    c.eq(
        "one price above another",
        one(a_compare((f, "close"), (f, "atr14"), "gt", "price", "spot")),
        (L.SUPPORTS, 1, True, "close is 10000.5, atr14 is 30.5"),
    )
    c.eq("and below", one(a_compare((f, "close"), (f, "atr14"), "lt", "price", "spot"))[0], L.REFUTES)
    c.eq("across items", one(a_compare((ids["feat2"], "atr14"), (f, "atr14"), "gt", "price", "spot"))[0], L.SUPPORTS)
    for op, want in (("ge", L.SUPPORTS), ("le", L.REFUTES)):
        c.eq(f"the operator {op}", one(a_compare((f, "close"), (f, "atr14"), op, "price", "spot"))[0], want)
    c.eq("equal values satisfy at least", one(a_compare((f, "close"), (ids["series"], "last_close"), "ge", "price", "spot"))[0], L.SUPPORTS)
    c.eq("and not strictly greater", one(a_compare((f, "close"), (ids["series"], "last_close"), "gt", "price", "spot"))[0], L.REFUTES)
    c.eq(
        "a price against a ratio",
        one(a_compare((f, "close"), (f, "atr14_share"), "gt", "price", "spot")),
        (L.WRONG_SCOPE_UNIT, 1, True, "the compared fields do not share the unit the assertion states"),
    )
    c.eq("an assertion that names another unit", one(a_compare((f, "close"), (f, "atr14"), "gt", "ratio", "spot"))[0], L.WRONG_SCOPE_UNIT)
    c.eq(
        "a return against a return of another window",
        one(a_compare((f, "ret_1h"), (f, "ret_4h"), "gt", "ratio", "1h")),
        (L.WRONG_SCOPE_TIME, 1, True, "the compared fields do not cover the window the assertion states"),
    )
    c.eq("a window the assertion does not share", one(a_compare((f, "close"), (f, "atr14"), "gt", "price", "1h"))[0], L.WRONG_SCOPE_TIME)
    c.eq(
        "a field with no meaning",
        one(a_compare((f, "close"), (f, "rsi"), "gt", "price", "spot")),
        (L.NOT_ENOUGH_INFO, 1, True, "a compared field has no registered meaning, so its unit and window cannot be checked"),
    )
    c.eq("the first with no meaning", one(a_compare((f, "rsi"), (f, "close"), "gt", "price", "spot"))[0], L.NOT_ENOUGH_INFO)
    c.eq(
        "a compared item of another asset",
        one(a_compare((f, "close"), (ids["other"], "ret_1h"), "gt", "price", "spot")),
        (L.WRONG_SCOPE_ASSET, 1, True, "a cited item is about another asset than tk-btc"),
    )
    c.eq("the first of another asset", one(a_compare((ids["other"], "ret_1h"), (f, "ret_4h"), "gt", "ratio", "1h"))[0], L.WRONG_SCOPE_ASSET)
    c.eq(
        "a compared field not measured",
        one(a_compare((ids["feat2"], "ret_1h"), (f, "ret_1h"), "gt", "ratio", "1h")),
        (L.NOT_ENOUGH_INFO, 1, True, "ret_1h is not measured in the cited item (NULL)"),
    )
    c.eq("and the second", one(a_compare((f, "ret_1h"), (ids["feat2"], "ret_1h"), "gt", "ratio", "1h"))[0], L.NOT_ENOUGH_INFO)
    c.eq("a compared item that was not shown", one(a_compare((f, "close"), ("ev" + "z" * 26, "atr14"), "gt", "price", "spot"))[2], False)
    return c.bad


def check_statements(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    _, ids = world()
    L = mod.Label
    tex = ids["text"]

    def one(
        kind: str, quote: str, text: str, path: str = "headline", ev: str | None = None, asset: str = BTC
    ) -> tuple[Any, int, bool, str]:
        v = run(mod, a_statement(kind, ev or tex, quote, text, path), asset)
        return v.label, v.layer, v.citation_exists, v.reason

    unverified = (L.SUPPORT_UNVERIFIED, 3, True, "the quote is in the cited items and no validated judge has read the claim")
    c.eq("an event whose quote is in the item", one("EVENT", "will list BTC", "A listing is planned"), unverified)
    c.eq("a mechanism the same", one("MECHANISM", "will list BTC", "Listing lifts demand"), unverified)
    c.eq(
        "a quote that is not in any cited text",
        one("EVENT", "will delist BTC", "x"),
        (L.NOT_ENOUGH_INFO, 1, True, "the quoted words are not in any text of the cited items"),
    )
    c.eq(
        "a quote in another field of the item",
        one("EVENT", "not confirmed", "The listing is not confirmed", "headline"),
        (L.SUPPORT_UNVERIFIED, 3, True, unverified[3]),
    )
    c.eq("a quote is matched as written (case)", one("EVENT", "WILL LIST BTC", "x")[0], L.NOT_ENOUGH_INFO)
    c.eq("composed and decomposed forms are the same quote", one("EVENT", "café", "x")[0], L.NOT_ENOUGH_INFO)
    c.eq(
        "a negation dropped",
        one("EVENT", "The listing is not confirmed", "The listing is confirmed"),
        (L.REFUTES, 2, True, "the quoted words are negated and the assertion is not"),
    )
    c.eq("a negation kept", one("EVENT", "The listing is not confirmed", "The listing is not yet certain")[0], L.SUPPORT_UNVERIFIED)
    c.eq(
        "a contraction negates, with either apostrophe",
        (
            one("EVENT", "The listing isn't confirmed", "The listing is confirmed")[0],
            one("EVENT", "The listing isn\u2019t confirmed", "The listing is confirmed")[0],
            one("EVENT", "The listing isn't confirmed", "The listing isn't certain")[0],
        ),
        (L.REFUTES, L.REFUTES, L.SUPPORT_UNVERIFIED),
    )
    c.eq(
        "a hedge dropped",
        one("EVENT", "Reportedly a listing may come", "A listing is coming"),
        (L.WRONG_SCOPE_QUALIFIER, 2, True, "the quoted words are hedged and the assertion is not"),
    )
    c.eq("a hedge kept", one("EVENT", "Reportedly a listing may come", "A listing may come")[0], L.SUPPORT_UNVERIFIED)
    c.eq(
        "a limit dropped",
        one("EVENT", "volume at least doubled over the last day", "Volume doubled"),
        (L.WRONG_SCOPE_QUALIFIER, 2, True, "the quoted words carry a limit that the assertion drops"),
    )
    c.eq(
        "a limit kept",
        one("EVENT", "volume at least doubled over the last day", "Volume at least doubled over the last day")[0],
        L.SUPPORT_UNVERIFIED,
    )
    c.eq(
        "whole words only: a mayor is not a hedge, know is not a negation, perpetual is not a limit",
        one("EVENT", "The mayor said we know the perpetual plan", "The plan was said", "tricky")[0],
        L.SUPPORT_UNVERIFIED,
    )
    c.eq("a negation outranks a hedge", one("EVENT", "It may not happen", "It happens", "headline")[0], L.NOT_ENOUGH_INFO)
    items, _ = world()
    both = item("both", (("h", "It may not happen"),))
    items2 = {**items, both.evidence_id: both}
    a = a_statement("EVENT", both.evidence_id, "It may not happen", "It happens", "h")
    c.eq("and is reported first", mod.ground_one(a, items2, BTC).label, L.REFUTES)
    c.eq(
        "a quote from a warning",
        mod.ground_one(a_statement("EVENT", ids["warned"], "one section is partial", "A section is partial", "x"), items, BTC).label,
        L.SUPPORT_UNVERIFIED,
    )
    c.eq(
        "a statement about another asset",
        one("EVENT", "will list BTC", "x", ev=ids["other"]),
        (L.WRONG_SCOPE_ASSET, 1, True, "a cited item is about another asset than tk-btc"),
    )
    c.eq(
        "a statement with a missing cite",
        one("EVENT", "will list BTC", "x", ev="ev" + "z" * 26),
        (L.NOT_ENOUGH_INFO, 1, False, "a cited item was not among the items shown"),
    )
    c.eq("an opinion is context", one("OPINION", "", "Looks heavy", ev=tex)[:3], (L.OPINION_CONTEXT, 3, True))
    op = assertions.Assertion("a1", assertions.AssertionKind.OPINION, None, None, None, None, (), None, "Looks heavy")
    v = run(mod, op)
    c.eq(
        "an opinion with no cite",
        (v.label, v.citation_exists, v.reason),
        (L.OPINION_CONTEXT, True, "an opinion is shown as context and is never support"),
    )
    gone = assertions.Assertion(
        "a1", assertions.AssertionKind.OPINION, None, None, None, None, (assertions.Cite("ev" + "z" * 26, "x"),), None, "x"
    )
    c.eq("an opinion that cites what was not shown", (run(mod, gone).label, run(mod, gone).citation_exists), (L.NOT_ENOUGH_INFO, False))
    return c.bad


def check_edges(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    items, ids = world()
    L = mod.Label
    f = ids["feat"]

    def one(a: Any, asset: str = BTC) -> tuple[Any, int, bool, str]:
        v = mod.ground_one(a, items, asset)
        return v.label, v.layer, v.citation_exists, v.reason

    c.eq("the whole registry", {k: (x.window, x.unit, x.change) for k, x in mod.FIELD_FACTS.items()}, {
        "ret_1h": ("1h", "ratio", True), "ret_4h": ("4h", "ratio", True), "ret_24h": ("24h", "ratio", True),
        "close": ("spot", "price", False), "atr14": ("spot", "price", False), "atr14_share": ("spot", "ratio", False),
        "last_close": ("spot", "price", False), "count": ("spot", "count", False), "missing": ("spot", "count", False),
        "pending": ("spot", "count", False), "excluded_unfinished": ("spot", "count", False), "interval_s": ("spot", "count", False),
        "performance.change_1h_pct": ("1h", "percent", True), "performance.change_24h_pct": ("24h", "percent", True),
        "performance.change_7d_pct": ("7d", "percent", True), "performance.change_30d_pct": ("30d", "percent", True),
        "technical_analysis.momentum_30d_pct": ("30d", "percent", True), "market.price_usd": ("spot", "price", False),
    })  # fmt: skip
    # rounding is to the places stated, in units of one place
    c.eq("a value rounded to a coarser place than the field", one(a_numeric(f, "ret_1h", "eq", "0.003", "ratio", "1h"))[0], L.REFUTES)
    c.eq("a value that is a round number of thousands", one(a_numeric(ids["big"], "close", "eq", "10000", "price", "spot"))[0], L.REFUTES)
    c.eq("and the same for the nearest thousand", one(a_numeric(ids["big"], "close", "eq", "15000", "price", "spot"))[0], L.REFUTES)
    c.eq("about, at exactly one percent", one(a_numeric(ids["edge"], "close", "approx", "100", "price", "spot"))[0], L.SUPPORTS)
    c.eq("about, just over one percent", one(a_numeric(ids["edge"], "close", "approx", "99.9", "price", "spot"))[0], L.REFUTES)
    # the flat band
    up, down, over = ids["up_edge"], ids["down_edge"], ids["over"]
    c.eq("not a rise at exactly the band", one(a_direction(up, "ret_1h", "rose", "1h"))[0], L.REFUTES)
    c.eq("flat at exactly the band", one(a_direction(up, "ret_1h", "flat", "1h"))[0], L.SUPPORTS)
    c.eq("not a fall at exactly minus the band", one(a_direction(down, "ret_1h", "fell", "1h"))[0], L.REFUTES)
    c.eq("flat at exactly minus the band", one(a_direction(down, "ret_1h", "flat", "1h"))[0], L.SUPPORTS)
    c.eq("a rise just beyond the band", one(a_direction(over, "ret_1h", "rose", "1h"))[0], L.SUPPORTS)
    c.eq("not flat just beyond it", one(a_direction(over, "ret_1h", "flat", "1h"))[0], L.REFUTES)
    c.eq("a fall just beyond the band", one(a_direction(over, "ret_4h", "fell", "4h"))[0], L.SUPPORTS)
    c.eq("not flat just below minus the band", one(a_direction(over, "ret_4h", "flat", "4h"))[0], L.REFUTES)
    # an item about more than one asset
    c.eq(
        "an item about two assets, one of them this one",
        one(a_numeric(ids["pair"], "ret_1h", "eq", "0.42", "percent", "1h"))[0],
        L.SUPPORTS,
    )
    c.eq(
        "and about the other", mod.ground_one(a_numeric(ids["pair"], "ret_1h", "eq", "0.42", "percent", "1h"), items, ETH).label, L.SUPPORTS
    )
    c.eq(
        "but not about a third",
        mod.ground_one(a_numeric(ids["pair"], "ret_1h", "eq", "0.42", "percent", "1h"), items, "tk-sol").label,
        L.WRONG_SCOPE_ASSET,
    )
    two = assertions.Assertion(
        "a1",
        assertions.AssertionKind.EVENT,
        None,
        None,
        None,
        None,
        (assertions.Cite(ids["text"], "headline"), assertions.Cite(ids["other"], "ret_1h")),
        "will list BTC",
        "x",
    )
    c.eq(
        "a statement citing one right item and one of another asset",
        one(two),
        (L.WRONG_SCOPE_ASSET, 1, True, "a cited item is about another asset than tk-btc"),
    )
    # a comparison, outcome by outcome, with everything it reports
    asset = (L.WRONG_SCOPE_ASSET, 1, True, "a cited item is about another asset than tk-btc")
    c.eq("comparison, an item of another asset", one(a_compare((f, "close"), (ids["other"], "ret_1h"), "gt", "price", "spot")), asset)
    unit = (L.WRONG_SCOPE_UNIT, 1, True, "the compared fields do not share the unit the assertion states")
    c.eq("comparison, the units differ", one(a_compare((f, "close"), (f, "atr14_share"), "gt", "price", "spot")), unit)
    window = (L.WRONG_SCOPE_TIME, 1, True, "the compared fields do not cover the window the assertion states")
    c.eq("comparison, the windows differ", one(a_compare((f, "ret_1h"), (f, "ret_4h"), "gt", "ratio", "1h")), window)
    c.eq("comparison, the window is not theirs", one(a_compare((f, "close"), (f, "atr14"), "gt", "price", "1h")), window)
    none = (L.NOT_ENOUGH_INFO, 1, True, "a compared field has no registered meaning, so its unit and window cannot be checked")
    c.eq("comparison, no meaning", one(a_compare((f, "close"), (f, "rsi"), "gt", "price", "spot")), none)
    c.eq("comparison, the first with no meaning", one(a_compare((f, "rsi"), (f, "close"), "gt", "price", "spot")), none)
    shown = (L.SUPPORTS, 1, True, "close is 10000.5, atr14 is 30.5")
    c.eq("comparison, true", one(a_compare((f, "close"), (f, "atr14"), "gt", "price", "spot")), shown)
    c.eq("comparison, false", one(a_compare((f, "close"), (f, "atr14"), "lt", "price", "spot")), (L.REFUTES, 1, True, shown[3]))
    c.eq(
        "comparison, a missing item",
        one(a_compare((f, "close"), ("ev" + "z" * 26, "atr14"), "gt", "price", "spot")),
        (L.NOT_ENOUGH_INFO, 1, False, "a cited item was not among the items shown"),
    )
    c.eq(
        "comparison, the second field not measured",
        one(a_compare((f, "ret_1h"), (ids["feat2"], "ret_1h"), "gt", "ratio", "1h")),
        (L.NOT_ENOUGH_INFO, 1, True, "ret_1h is not measured in the cited item (NULL)"),
    )
    # the words that change what a quote says
    c.eq(
        "the words that hedge",
        mod.HEDGES,
        (
            "reportedly",
            "allegedly",
            "alleged",
            "rumor",
            "rumour",
            "unconfirmed",
            "may",
            "might",
            "could",
            "possibly",
            "expected to",
            "plans to",
            "said to",
        ),
    )
    c.eq(
        "the words that negate",
        mod.NEGATIONS,
        (
            "not",
            "no",
            "none",
            "never",
            "without",
            "denies",
            "denied",
            "unless",
            "cannot",
            "neither",
            "nor",
            "isn't",
            "aren't",
            "wasn't",
            "weren't",
            "doesn't",
            "don't",
            "didn't",
            "won't",
            "can't",
            "couldn't",
            "shouldn't",
            "hasn't",
            "haven't",
            "hadn't",
            "fails to",
            "failed to",
            "rejected",
            "rejects",
            "refuted",
            "false",
        ),
    )
    c.eq("the words that limit", mod.QUALIFIERS, ("at least", "up to", "only", "so far", "over the last", "per"))
    for words, label, reason, quote in (
        (mod.NEGATIONS, L.REFUTES, "the quoted words are negated and the assertion is not", "The move does {w} happen"),
        (mod.HEDGES, L.WRONG_SCOPE_QUALIFIER, "the quoted words are hedged and the assertion is not", "Reports say it {w} happen"),
        (mod.QUALIFIERS, L.WRONG_SCOPE_QUALIFIER, "the quoted words carry a limit that the assertion drops", "Volume rose {w} the day"),
    ):
        for word in words:
            held = item("w", (("h", quote.format(w=word)),))
            all_items = {held.evidence_id: held}
            a = a_statement("EVENT", held.evidence_id, quote.format(w=word), "The move happens", "h")
            v = mod.ground_one(a, all_items, BTC)
            c.eq(f"the word '{word}' in a quote", (v.label, v.layer, v.reason), (label, 2, reason))
            kept = a_statement("EVENT", held.evidence_id, quote.format(w=word), f"Note {word} here", "h")
            c.eq("and kept in the words", mod.ground_one(kept, all_items, BTC).label, L.SUPPORT_UNVERIFIED)
    return c.bad


def check_report(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    items, ids = world()
    f = ids["feat"]
    good = a_numeric(f, "ret_1h", "eq", "0.42", "percent", "1h")
    bad = assertions.Assertion(
        "a2", assertions.AssertionKind.NUMERIC, "eq", D("5"), "percent", "1h", (assertions.Cite(f, "ret_1h"),), None, None
    )
    wrong_unit = assertions.Assertion(
        "a3", assertions.AssertionKind.NUMERIC, "eq", D("1"), "price", "1h", (assertions.Cite(f, "ret_1h"),), None, None
    )
    gone = assertions.Assertion(
        "a4", assertions.AssertionKind.NUMERIC, "eq", D("1"), "price", "spot", (assertions.Cite("ev" + "z" * 26, "close"),), None, None
    )
    opinion = assertions.Assertion("a5", assertions.AssertionKind.OPINION, None, None, None, None, (), None, "x")
    report = mod.ground((good, bad, wrong_unit, gone, opinion), items, BTC)
    c.eq("one verdict per assertion, in order", [v.assertion_id for v in report.verdicts], ["a1", "a2", "a3", "a4", "a5"])
    c.eq("the citations that exist", report.citations_exist, 4)
    c.eq("the assertions that are supported", report.supported, 1)
    c.eq("the false statements", report.false_statements, ("a2", "a3"))
    c.eq(
        "the counts by label",
        report.counts(),
        {"SUPPORTS": 1, "REFUTES": 1, "WRONG_SCOPE_UNIT": 1, "NOT_ENOUGH_INFO": 1, "OPINION_CONTEXT": 1},
    )
    empty = mod.ground((), items, BTC)
    c.eq(
        "an answer with no assertions",
        (empty.verdicts, empty.ungrounded_numbers, empty.citations_exist, empty.supported, empty.false_statements, empty.counts()),
        ((), (), 0, 0, (), {}),
    )
    c.eq("free words are scanned only when given", mod.ground((), items, BTC, "rose 99%").ungrounded_numbers, ("99%",))
    return c.bad


def check_scan(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    items, ids = world()
    scan = lambda text: mod.scan_numbers(text, items)  # noqa: E731
    c.eq("numbers the items hold", scan("Price 10000.5, ATR 30.5, count 60"), ())
    c.eq("a percent of a ratio", scan("The hour was +0.42% and the day -1%"), ())
    c.eq("a number with a comma", scan("It closed at 10,000.5"), ())
    c.eq("a number that nothing holds", scan("It was 7 and then 12.5"), ("7", "12.5"))
    c.eq("a comma number that nothing holds", scan("About 1,000"), ("1,000",))
    c.eq("a percent that nothing holds", scan("down 3%"), ("3%",))
    c.eq("windows and names with digits are not numbers", scan("over 24h and 4h, BTC2 and ret_1h"), ())
    c.eq("an evidence id is not a number", scan(f"see {ids['feat']} and {ids['series']}"), ())
    c.eq("a trailing full stop", scan("It closed at 10000.5."), ())
    c.eq("a number inside a word", scan("abc123def"), ())
    c.eq("a negative that nothing holds", scan("fell -7"), ("-7",))
    c.eq("a decimal written differently", scan("30.50 and 0.0042"), ())
    c.eq("a zero is not held unless a field is zero", (scan("flat at 0"), scan("0.0")), ((), ()))
    c.eq("nothing in an empty text", scan(""), ())
    return c.bad


def check_too_many_digits(mod: types.ModuleType) -> list[str]:
    """A cited field with more digits than the module's decimal context (28, that of ``features`` and Python's default) holds at the places
    the assertion states cannot be compared: that is ``NOT_ENOUGH_INFO`` at layer 1, never an error that ends the cycle. The context is the
    module's own, whatever context the caller is in."""
    from decimal import Context, localcontext

    c = Case(mod)
    L = mod.Label
    long = item("long", (("atr14", "1234567890123456"), ("close", "12345678901234567")))
    items = {long.evidence_id: long}
    cannot = (L.NOT_ENOUGH_INFO, 1, True, "close has too many digits to be compared at the places the assertion states")

    def one(path: str, op: str, value: str) -> tuple[Any, int, bool, str]:
        v = mod.ground_one(a_numeric(long.evidence_id, path, op, value, "price", "spot"), items, BTC)
        return v.label, v.layer, v.citation_exists, v.reason

    try:
        c.eq("seventeen digits before the point, twelve places stated", one("close", "eq", "0.123456789012"), cannot)
        c.eq("and the sign checked as well", one("close", "eq", "-0.123456789012"), cannot)
        c.eq("eleven places are compared", one("close", "eq", "0.12345678901")[0], L.REFUTES)
        c.eq("sixteen digits are compared at twelve places", one("atr14", "eq", "0.123456789012")[0], L.REFUTES)
        c.eq("an about is compared", one("close", "approx", "0.123456789012")[0], L.REFUTES)
        c.eq("and an inequality", one("close", "gt", "0.123456789012")[0], L.SUPPORTS)
        with localcontext(Context(prec=60)):
            c.eq("a caller's wider context changes nothing", one("close", "eq", "0.123456789012"), cannot)
        with localcontext(Context(prec=10)):
            c.eq("nor does a narrower one", one("atr14", "eq", "0.123456789012")[0], L.REFUTES)
    except Exception as exc:
        c.bad.append(f"raised {type(exc).__name__}: {exc}")
    return c.bad


def check_ryo_fields(mod: types.ModuleType) -> list[str]:
    """A number asserted about an RYO observation is held to the value RYO gave, as one asserted about an exchange feature is."""
    c = Case(mod)
    L = mod.Label
    ryo = item(
        "ryo",
        (
            ("market.price_usd", "75000.12"),
            ("performance.change_1h_pct", "-0.30"),
            ("performance.change_24h_pct", "2.5"),
            ("performance.change_30d_pct", "-8.4"),
            ("performance.change_7d_pct", "1.1"),
            ("technical_analysis.momentum_30d_pct", "-7"),
            ("technical_analysis.rsi_14", "44.2"),
        ),
    )
    items = {ryo.evidence_id: ryo}

    def says(path: str, op: str, value: str, unit: str, window: str) -> Any:
        a = a_numeric(ryo.evidence_id, path, op, value, unit, window)
        return mod.ground_one(a, items, BTC).label

    c.eq("the 24 hour change as RYO gave it", says("performance.change_24h_pct", "eq", "2.5", "percent", "24h"), L.SUPPORTS)
    c.eq("a different number", says("performance.change_24h_pct", "eq", "5", "percent", "24h"), L.REFUTES)
    c.eq("the sign dropped", says("performance.change_30d_pct", "eq", "8.4", "percent", "30d"), L.WRONG_SCOPE_SIGN)
    c.eq("the wrong window", says("performance.change_24h_pct", "eq", "2.5", "percent", "1h"), L.WRONG_SCOPE_TIME)
    c.eq("as a ratio the same number", says("performance.change_24h_pct", "eq", "0.025", "ratio", "24h"), L.SUPPORTS)
    c.eq("as a ratio, not that number", says("performance.change_24h_pct", "eq", "0.25", "ratio", "24h"), L.REFUTES)
    c.eq("the unit of a price is not a percentage", says("performance.change_24h_pct", "eq", "2.5", "price", "24h"), L.WRONG_SCOPE_UNIT)
    c.eq("above a number it is above", says("performance.change_1h_pct", "lt", "0", "percent", "1h"), L.SUPPORTS)
    c.eq("the 7 day change", says("performance.change_7d_pct", "eq", "1.1", "percent", "7d"), L.SUPPORTS)
    c.eq("the 30 day momentum", says("technical_analysis.momentum_30d_pct", "eq", "-7", "percent", "30d"), L.SUPPORTS)
    c.eq("the price", says("market.price_usd", "eq", "75000.12", "price", "spot"), L.SUPPORTS)
    c.eq("a price that is not the price", says("market.price_usd", "eq", "7500.12", "price", "spot"), L.REFUTES)
    c.eq(
        "the RSI has no unit the schema can state: not enough to say",
        says("technical_analysis.rsi_14", "eq", "44.2", "count", "spot"),
        L.NOT_ENOUGH_INFO,
    )
    c.eq("a field RYO did not give", says("performance.change_4h_pct", "eq", "1", "percent", "4h"), L.NOT_ENOUGH_INFO)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_registry,
        check_numeric,
        check_non_numbers,
        check_statements,
        check_edges,
        check_report,
        check_scan,
        check_too_many_digits,
        check_ryo_fields,
    ):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1000:]}")
    return bad
