"""Checks of the typed assertion (``hoshitori.assertions``), runnable against a mutant."""

from __future__ import annotations

import json
import types
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from hoshitori import jsonnum, typed

D = Decimal
EV = "ev" + "a" * 26
EV2 = "ev" + "b" * 26


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want or type(got) is not type(want):
            self.bad.append(f"{label}: got {got!r} ({type(got).__name__}), expected {want!r} ({type(want).__name__})")

    def refuses(self, label: str, call: Callable[[], object], says: str, error: str = "AssertionShapeError") -> None:
        want = getattr(self.mod, error) if error != "TypedError" else typed.TypedError
        try:
            call()
        except want as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def cite(ev: str = EV, path: str = "ret_1h") -> dict[str, str]:
    return {"evidence_id": ev, "field_path": path}


def numeric(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": "a1", "kind": "NUMERIC", "op": "eq", "value": 0.4, "unit": "percent", "window": "1h", "cites": [cite()]}
    base.update(over)
    return base


def read(mod: types.ModuleType, *items: dict[str, Any]) -> Any:
    """The assertions of an answer written as JSON: through the schema, then to shapes."""
    raw = typed.parse(mod.ASSERTIONS_SCHEMA, json.dumps(list(items)).encode())
    return mod.from_values(raw)


def check_schema(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "the guide a model is given",
        mod.GUIDE,
        "Assertions are optional: an empty list is allowed, and one assertion of the wrong shape makes the whole answer "
        "invalid, so state only what you are sure of. Give each assertion an id a1, a2 and so on. The kinds, and "
        "exactly what each holds: NUMERIC has op (eq, approx, gt, ge, lt or le), value, unit (ratio, percent, price or "
        "count), window (spot, 1h, 4h, 24h, 7d or 30d) and cites with exactly ONE entry. Copy every number exactly as "
        "it appears in the item, with all its digits. A field's window is the one its name says (ret_1h is 1h, ret_4h "
        "is 4h, ret_24h is 24h); a price, a count and atr14 are spot. COMPARISON has op (gt, ge, lt or le), unit, "
        "window and cites with exactly TWO entries, and no value. DIRECTION has op (rose, fell or flat), window and "
        "cites with exactly ONE entry, and no value and no unit. EVENT and MECHANISM have text, quote (words copied "
        "exactly from a cited item) and at least one cite, and no op, value, unit or window; if you have no quote, use "
        "OPINION. OPINION has only id, kind, text and an empty cites list. A cite is an object with evidence_id and "
        "field_path. Write one NUMERIC assertion for each number you state about the evidence.",
    )
    c.eq(
        "constants",
        (mod.MAX_ASSERTIONS, mod.NUMERIC_OPS, mod.COMPARE_OPS, mod.DIRECTION_OPS, mod.UNITS, mod.WINDOWS),
        (
            12,
            ("eq", "approx", "gt", "ge", "lt", "le"),
            ("gt", "ge", "lt", "le"),
            ("rose", "fell", "flat"),
            ("ratio", "percent", "price", "count"),
            ("spot", "1h", "4h", "24h", "7d", "30d"),
        ),
    )
    c.eq("the kinds", [k.value for k in mod.AssertionKind], ["NUMERIC", "COMPARISON", "DIRECTION", "EVENT", "MECHANISM", "OPINION"])
    c.eq("an assertion is frozen", (mod.Assertion.__dataclass_params__.frozen, mod.Cite.__dataclass_params__.frozen), (True, True))
    parse = lambda *items: typed.parse(mod.ASSERTIONS_SCHEMA, json.dumps(list(items)).encode())  # noqa: E731
    c.eq("a list of none is fine", parse(), [])
    for label, item, says in (
        ("an id not in the form", numeric(id="b1"), "$[0].id: the text does not match the pattern"),
        ("an id of one digit past two", numeric(id="a123"), "$[0].id: the text is 4 characters, not 2 to 3"),
        (
            "a kind that does not exist",
            numeric(kind="CLAIM"),
            "$[0].kind: the text is not one of ['NUMERIC', 'COMPARISON', 'DIRECTION', 'EVENT', 'MECHANISM', 'OPINION']",
        ),
        (
            "an operator that does not exist",
            numeric(op="near"),
            "$[0].op: the text is not one of ['eq', 'approx', 'gt', 'ge', 'lt', 'le', 'rose', 'fell', 'flat']",
        ),
        ("a unit that does not exist", numeric(unit="usd"), "$[0].unit: the text is not one of ['ratio', 'percent', 'price', 'count']"),
        (
            "a window that does not exist",
            numeric(window="2h"),
            "$[0].window: the text is not one of ['spot', '1h', '4h', '24h', '7d', '30d']",
        ),
        ("a value that is text", numeric(value="0.4"), "$[0].value: expected a number"),
        (
            "a value of twelve digits and a thirteenth place",
            numeric(value=0.0000000000001),
            "$[0].value: the number has more than 12 decimal places",
        ),
        (
            "a value above a million million",
            numeric(value=1000000000000.5),
            "$[0].value: the number is not from -1000000000000 to 1000000000000",
        ),
        (
            "a value below minus a million million",
            numeric(value=-1000000000000.5),
            "$[0].value: the number is not from -1000000000000 to 1000000000000",
        ),
        ("three cites", numeric(cites=[cite(), cite(), cite()]), "$[0].cites: the list has 3 items, not 0 to 2"),
        (
            "an evidence id that is not one",
            numeric(cites=[cite(ev="xx")]),
            "$[0].cites[0].evidence_id: the text is 2 characters, not 28 to 28",
        ),
        (
            "an evidence id in capitals",
            numeric(cites=[cite(ev="EV" + "A" * 26)]),
            "$[0].cites[0].evidence_id: the text does not match the pattern",
        ),
        (
            "a field path with a space",
            numeric(cites=[cite(path="ret 1h")]),
            "$[0].cites[0].field_path: the text does not match the pattern",
        ),
        (
            "a field path of 121 characters",
            numeric(cites=[cite(path="a" * 121)]),
            "$[0].cites[0].field_path: the text is 121 characters, not 1 to 120",
        ),
        ("a quote of 301 characters", numeric(quote="q" * 301), "$[0].quote: the text is 301 characters, not 1 to 300"),
        ("words of 401 characters", numeric(text="t" * 401), "$[0].text: the text is 401 characters, not 1 to 400"),
        ("an unknown member", numeric(note="x"), "$[0].note: is not a member of this answer"),
    ):
        c.refuses(label, lambda item=item: parse(item), says, "TypedError")
    c.refuses(
        "thirteen assertions",
        lambda: parse(*[numeric(id=f"a{i}") for i in range(13)]),
        "$: the list has 13 items, not 0 to 12",
        "TypedError",
    )
    parse(*[numeric(id=f"a{i}") for i in range(12)])
    parse(
        numeric(cites=[cite(path="a" * 120)], id="a99"),
        numeric(value=-1e12, id="a2"),
        numeric(value=1e12, id="a3"),
        numeric(id="a4", cites=[cite(path="a.b[0]-c_d")]),
    )
    c.eq("the boundaries are accepted", True, True)
    return c.bad


def check_provider_shapes(mod: types.ModuleType) -> list[str]:
    """C1: the schema a provider is told holds one closed shape per kind, and each shape accepts exactly what the rule of its kind
    (``from_value``) accepts, over every combination of members, every operator and every number of cites. The check still reads the flat
    base, so no answer is accepted or refused differently by the check itself."""
    c = Case(mod)
    tagged = mod.ASSERTION_SCHEMA
    c.eq("the schema is tagged by kind", (type(tagged).__name__, tagged.tag), ("Tagged", "kind"))
    shown = typed.json_schema(tagged)
    c.eq(
        "a provider is told one closed shape per kind",
        [s["properties"]["kind"]["enum"] for s in shown.get("anyOf", [])],
        [[k.value] for k in mod.AssertionKind],
    )
    c.eq(
        "every member of a shape is required",
        all(s["required"] == list(s["properties"]) and s["additionalProperties"] is False for s in shown.get("anyOf", [])),
        True,
    )
    variants = dict(zip((k.value for k in mod.AssertionKind), tagged.variants, strict=False))
    optional = {"op": None, "value": 0.4, "unit": "percent", "window": "1h", "quote": "q", "text": "t"}
    ops = tuple(dict.fromkeys((*mod.NUMERIC_OPS, *mod.DIRECTION_OPS)))
    differ: list[str] = []
    for kind in mod.AssertionKind:
        for mask in range(64):
            present = [name for i, name in enumerate(optional) if mask >> i & 1]
            for op in ops if "op" in present else (None,):
                for n in (0, 1, 2, 3):  # three is past the base's limit, so a shape that allowed it would differ
                    item: dict[str, Any] = {"id": "a1", "kind": kind.value, "cites": [cite()] * n}
                    item.update({name: optional[name] for name in present})
                    if op is not None:
                        item["op"] = op
                    raw = jsonnum.parse(json.dumps(item).encode())
                    try:
                        typed.validate(variants[kind.value], raw)
                        told = True
                    except typed.TypedError:
                        told = False
                    try:
                        mod.from_value(typed.validate(tagged, raw))
                        rule = True
                    except (mod.AssertionShapeError, typed.TypedError):
                        rule = False
                    if told != rule:
                        differ.append(f"{kind.value} {sorted(present)} op={op} cites={n}: told {told}, rule {rule}")
    c.eq("a shape a provider is told accepts exactly what the rule of its kind accepts", differ[:5], [])
    return c.bad


def check_shapes(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    a = read(mod, numeric())[0]
    c.eq(
        "a numeric assertion",
        (a.assertion_id, a.kind, a.op, a.value, a.unit, a.window, a.cites, a.quote, a.text),
        ("a1", mod.AssertionKind.NUMERIC, "eq", D("0.4"), "percent", "1h", (mod.Cite(EV, "ret_1h"),), None, None),
    )
    comparison = {
        "id": "a2",
        "kind": "COMPARISON",
        "op": "gt",
        "unit": "ratio",
        "window": "4h",
        "cites": [cite(path="ret_1h"), cite(EV2, "ret_4h")],
    }
    b = read(mod, comparison)[0]
    c.eq(
        "a comparison",
        (b.kind, b.op, b.value, b.unit, b.window, b.cites),
        (mod.AssertionKind.COMPARISON, "gt", None, "ratio", "4h", (mod.Cite(EV, "ret_1h"), mod.Cite(EV2, "ret_4h"))),
    )
    direction = {"id": "a3", "kind": "DIRECTION", "op": "rose", "window": "24h", "cites": [cite(path="ret_24h")]}
    d = read(mod, direction)[0]
    c.eq("a direction", (d.kind, d.op, d.value, d.unit, d.window), (mod.AssertionKind.DIRECTION, "rose", None, None, "24h"))
    event = {"id": "a4", "kind": "EVENT", "text": "Listing announced", "quote": "will list BTC", "cites": [cite(path="headline")]}
    e = read(mod, event)[0]
    c.eq(
        "an event",
        (e.kind, e.text, e.quote, e.op, e.value, e.unit, e.window),
        (mod.AssertionKind.EVENT, "Listing announced", "will list BTC", None, None, None, None),
    )
    mechanism = {**event, "id": "a5", "kind": "MECHANISM"}
    c.eq("a mechanism", read(mod, mechanism)[0].kind, mod.AssertionKind.MECHANISM)
    opinion = {"id": "a6", "kind": "OPINION", "text": "Looks heavy", "cites": []}
    o = read(mod, opinion)[0]
    c.eq("an opinion with no cite", (o.kind, o.text, o.cites, o.quote), (mod.AssertionKind.OPINION, "Looks heavy", (), None))
    c.eq("and with a cite", len(read(mod, {**opinion, "cites": [cite()]})[0].cites), 1)
    c.eq(
        "many at once, in order",
        [x.assertion_id for x in read(mod, numeric(), comparison, direction, event, opinion)],
        ["a1", "a2", "a3", "a4", "a6"],
    )
    numeric_says = "assertion a1: a numeric assertion has an operator, a value, a unit, a window and one cite and nothing else"
    for label, item in (
        ("no value", {k: v for k, v in numeric().items() if k != "value"}),
        ("no unit", {k: v for k, v in numeric().items() if k != "unit"}),
        ("no window", {k: v for k, v in numeric().items() if k != "window"}),
        ("no operator", {k: v for k, v in numeric().items() if k != "op"}),
        ("a direction word as its operator", numeric(op="rose")),
        ("no cite", numeric(cites=[])),
        ("two cites", numeric(cites=[cite(), cite(EV2)])),
        ("a quote", numeric(quote="q")),
        ("words", numeric(text="t")),
    ):
        c.refuses(f"numeric with {label}", lambda item=item: read(mod, item), numeric_says)
    for op in ("eq", "approx", "gt", "ge", "lt", "le"):
        read(mod, numeric(op=op))
    c.eq("every numeric operator is accepted", True, True)
    comparison_says = "assertion a2: a comparison has an operator of greater or less, a unit, a window and two cites and nothing else"
    for label, change in (
        ("an equal operator", {"op": "eq"}),
        ("a value", {"value": 1}),
        ("no unit", {"unit": None}),
        ("no window", {"window": None}),
        ("one cite", {"cites": [cite()]}),
        ("three cites cannot be written but one fewer can", {"cites": []}),
        ("a quote", {"quote": "q"}),
        ("words", {"text": "t"}),
    ):
        item = {k: v for k, v in {**comparison, **change}.items() if v is not None}
        c.refuses(f"a comparison with {label}", lambda item=item: read(mod, item), comparison_says)
    for op in ("gt", "ge", "lt", "le"):
        read(mod, {**comparison, "op": op})
    direction_says = "assertion a3: a direction has rose, fell or flat, a window and one cite and nothing else"
    for label, change in (
        ("a numeric operator", {"op": "gt"}),
        ("a value", {"value": 1}),
        ("a unit", {"unit": "ratio"}),
        ("no window", {"window": None}),
        ("no cite", {"cites": []}),
        ("two cites", {"cites": [cite(), cite(EV2)]}),
        ("a quote", {"quote": "q"}),
        ("words", {"text": "t"}),
    ):
        item = {k: v for k, v in {**direction, **change}.items() if v is not None}
        c.refuses(f"a direction with {label}", lambda item=item: read(mod, item), direction_says)
    for op in ("rose", "fell", "flat"):
        read(mod, {**direction, "op": op})
    opinion_says = "assertion a6: an opinion has words and no operator, value, unit, window or quote"
    for label, change in (
        ("no words", {"text": None}),
        ("an operator", {"op": "eq"}),
        ("a value", {"value": 1}),
        ("a unit", {"unit": "ratio"}),
        ("a window", {"window": "1h"}),
        ("a quote", {"quote": "q"}),
    ):
        item = {k: v for k, v in {**opinion, **change}.items() if v is not None}
        c.refuses(f"an opinion with {label}", lambda item=item: read(mod, item), opinion_says)
    event_says = "assertion a4: an event or a mechanism has words, a quote and at least one cite and no operator, value, unit or window"
    for label, change in (
        ("no words", {"text": None}),
        ("no quote", {"quote": None}),
        ("no cite", {"cites": []}),
        ("an operator", {"op": "eq"}),
        ("a value", {"value": 1}),
        ("a unit", {"unit": "ratio"}),
        ("a window", {"window": "1h"}),
    ):
        item = {k: v for k, v in {**event, **change}.items() if v is not None}
        c.refuses(f"an event with {label}", lambda item=item: read(mod, item), event_says)
        mech = {**item, "kind": "MECHANISM"}
        c.refuses(f"a mechanism with {label}", lambda mech=mech: read(mod, mech), event_says.replace("a4", "a4"))
    c.refuses("two assertions with one id", lambda: read(mod, numeric(), numeric()), "the ids of assertions are all different")
    return c.bad


def check_render(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    r = lambda item: mod.render(read(mod, item)[0])  # noqa: E731
    c.eq("a numeric sentence", r(numeric()), "ret_1h is 0.4% over the last 1h")
    for op, words in (
        ("eq", "is"),
        ("approx", "is about"),
        ("gt", "is above"),
        ("ge", "is at least"),
        ("lt", "is below"),
        ("le", "is at most"),
    ):
        c.eq(f"the operator {op}", r(numeric(op=op)), f"ret_1h {words} 0.4% over the last 1h")
    for unit, suffix in (("ratio", " (ratio)"), ("percent", "%"), ("price", " (price units)"), ("count", " (count)")):
        c.eq(f"the unit {unit}", r(numeric(unit=unit)), f"ret_1h is 0.4{suffix} over the last 1h")
    c.eq("a spot window", r(numeric(window="spot")), "ret_1h is 0.4% now")
    c.eq("a window of days", r(numeric(window="7d")), "ret_1h is 0.4% over the last 7d")
    for value, text in (
        (0.50, "0.5"),
        (100, "100"),
        (-0.0, "0"),
        (0, "0"),
        (-2.5, "-2.5"),
        (1e12, "1000000000000"),
        (0.000001, "0.000001"),
    ):
        c.eq(f"the value {value}", r(numeric(value=value)), f"ret_1h is {text}% over the last 1h")
    c.eq("the number is the one of the assertion", mod.plain(D("100.00")), "100")
    c.eq(
        "a comparison sentence",
        r(
            {
                "id": "a2",
                "kind": "COMPARISON",
                "op": "gt",
                "unit": "ratio",
                "window": "4h",
                "cites": [cite(path="ret_1h"), cite(EV2, "ret_4h")],
            }
        ),
        "ret_1h is above ret_4h (ratio) over the last 4h",
    )
    c.eq(
        "a direction sentence",
        r({"id": "a3", "kind": "DIRECTION", "op": "rose", "window": "24h", "cites": [cite(path="ret_24h")]}),
        "ret_24h rose over the last 24h",
    )
    c.eq(
        "fell and flat",
        [r({"id": "a3", "kind": "DIRECTION", "op": op, "window": "spot", "cites": [cite()]}) for op in ("fell", "flat")],
        ["ret_1h fell now", "ret_1h was flat now"],
    )
    event = {"id": "a4", "kind": "EVENT", "text": "Listing announced", "quote": "will list", "cites": [cite()]}
    c.eq(
        "an event, a mechanism and an opinion say the model's words",
        [r({**event, "kind": k}) for k in ("EVENT", "MECHANISM")]
        + [r({"id": "a6", "kind": "OPINION", "text": "Looks heavy", "cites": []})],
        ["Listing announced", "Listing announced", "Looks heavy"],
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_schema, check_provider_shapes, check_shapes, check_render):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1000:]}")
    return bad
