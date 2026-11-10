"""Checks of ``evidence_delta.skill``, runnable against a mutant: exact values for every branch, boundary and error message."""

from __future__ import annotations

import datetime as dt
import math
import types
from typing import Any

from evidence_delta.schema import EXAMPLE_CALL
from tests.checks_support import Case

NOW = dt.datetime(2026, 11, 9, 12, 0, 0, tzinfo=dt.UTC)
STAMP = "2026-11-09T12:00:00Z"


def _run(mod: types.ModuleType, args: Any) -> dict[str, Any]:
    return mod.run(args, now=NOW)  # type: ignore[no-any-return]


def check_parse_read(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.raises("a list", lambda: mod.parse_read("before", []), "before", "must be an object: an RYO envelope or its data", EXAMPLE_CALL)
    c.raises("a string", lambda: mod.parse_read("after", "x"), "after", "must be an object: an RYO envelope or its data", EXAMPLE_CALL)
    r = mod.parse_read("before", {"tool": "t", "as_of": "a", "status": "ok", "data_mode": "live", "data": {"x": 1}})
    c.eq("full envelope", (r.tool, r.as_of, r.status, r.data_mode, r.data), ("t", "a", "ok", "live", {"x": 1}))
    for key in ("tool", "as_of", "status", "schema_version", "data_mode"):
        r = mod.parse_read("before", {key: "v", "data": {"x": 1}})
        c.eq(f"{key} alone makes an envelope", r.data, {"x": 1})
    r = mod.parse_read("before", {"data": {"x": 1}})
    c.eq("a data member alone is bare data", r.data, {"data": {"x": 1}})
    c.eq("bare data has no meta", (r.tool, r.as_of, r.status, r.data_mode), (None, None, None, None))
    try:
        r.tool = "other"
        c.bad.append("a Read can be changed")
    except Exception as exc:
        c.eq("a Read is frozen", type(exc).__name__, "FrozenInstanceError")
    r = mod.parse_read("before", {"tool": "t", "data": [1]})
    c.eq("a non-object data member is not an envelope", (r.tool, r.data), (None, {"tool": "t", "data": [1]}))
    r = mod.parse_read("before", {"tool": "  ", "as_of": 5, "status": "", "data_mode": None, "data": {}})
    c.eq("blank and non-text meta are None", (r.tool, r.as_of, r.status, r.data_mode), (None, None, None, None))
    r = mod.parse_read("before", {"tool": " t ", "data": {}})
    c.eq("meta text is kept as given", r.tool, " t ")
    r = mod.parse_read("before", {"a": 1, "tool": "x"})
    c.eq("tool key without data is data", (r.tool, r.data), (None, {"a": 1, "tool": "x"}))
    r = mod.parse_read("before", {})
    c.eq("empty object", (r.tool, r.data), (None, {}))
    return c.bad


def check_shown_and_flatten(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("shown", mod.shown(("a", 0, "b")), "a.0.b")
    c.eq("shown empty", mod.shown(()), "")
    c.eq("scalar", mod.flatten(5), {(): 5})
    c.eq("nested", mod.flatten({"a": {"b": 1}, "c": [10, {"d": 2}]}), {("a", "b"): 1, ("c", 0): 10, ("c", 1, "d"): 2})
    c.eq("empty containers are leaves", mod.flatten({"a": {}, "b": [], "c": [[]]}), {("a",): {}, ("b",): [], ("c", 0): []})
    c.eq("empty root dict", mod.flatten({}), {(): {}})
    c.eq("empty root list", mod.flatten([]), {(): []})
    c.eq("list indexes stay ints", list(mod.flatten({"a": [0]})), [("a", 0)])
    c.eq("a numeric key is text", list(mod.flatten({1: 0})), [("1",)])
    c.eq("None leaf", mod.flatten({"a": None}), {("a",): None})
    deep: Any = 1
    for _ in range(12):
        deep = {"k": deep}
    c.eq("depth 12 is allowed", len(mod.flatten(deep)), 1)
    deeper = {"k": deep}
    c.raises("depth 13 at the root", lambda: mod.flatten(deeper), "k", "is nested deeper than 12 levels", EXAMPLE_CALL)
    c.raises("depth 13 in a list", lambda: mod.flatten([deeper]), "0", "is nested deeper than 12 levels", EXAMPLE_CALL)
    deep_list: Any = 1
    for _ in range(13):
        deep_list = [deep_list]
    c.raises("depth 13 of lists", lambda: mod.flatten(deep_list), "0", "is nested deeper than 12 levels", EXAMPLE_CALL)
    ok_list: Any = 1
    for _ in range(12):
        ok_list = [ok_list]
    c.eq("depth 12 of lists is allowed", list(mod.flatten(ok_list)), [(0,) * 12])
    c.raises("a scalar cannot be too deep", lambda: mod.flatten(1, (), 13), "before", "is nested deeper than 12 levels", EXAMPLE_CALL)
    c.eq("2000 fields are allowed", len(mod.flatten({str(i): i for i in range(2000)})), 2000)
    c.raises("2001 fields", lambda: mod.flatten({str(i): i for i in range(2001)}), "before", "has more than 2000 fields", EXAMPLE_CALL)
    c.raises("2001 list items", lambda: mod.flatten(list(range(2001))), "before", "has more than 2000 fields", EXAMPLE_CALL)
    out: dict[Any, object] = {("x",): 1}
    got = mod.flatten({"y": 2}, (), 0, out)
    c.eq("a given accumulator is filled and returned", (got is out, out), (True, {("x",): 1, ("y",): 2}))
    c.eq("an empty accumulator is still used", (lambda o: mod.flatten(1, (), 0, o) is o)({}), True)
    return c.bad


def check_compare_leaf(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    u = mod._UNSET
    f = mod.compare_leaf
    c.eq("absent before", f(u, 1, 0), {"outcome": "unknown", "reasons": {"before": "absent"}})
    c.eq("absent after", f(1, u, 0), {"outcome": "unknown", "reasons": {"after": "absent"}})
    c.eq("absent both", f(u, u, 0), {"outcome": "unknown", "reasons": {"before": "absent", "after": "absent"}})
    c.eq("null before", f(None, 1, 0), {"outcome": "unknown", "reasons": {"before": "null"}})
    c.eq("null after", f(1, None, 0), {"outcome": "unknown", "reasons": {"after": "null"}})
    c.eq("null both", f(None, None, 0), {"outcome": "unknown", "reasons": {"before": "null", "after": "null"}})
    c.eq("absent and null", f(u, None, 0), {"outcome": "unknown", "reasons": {"before": "absent", "after": "null"}})
    c.eq("nan", f(math.nan, 1, 0), {"outcome": "unknown", "reasons": {"before": "unusable_number"}})
    c.eq("inf", f(1, math.inf, 0), {"outcome": "unknown", "reasons": {"after": "unusable_number"}})
    c.eq(
        "-inf both", f(-math.inf, math.inf, 0), {"outcome": "unknown", "reasons": {"before": "unusable_number", "after": "unusable_number"}}
    )
    c.eq("equal numbers", f(5, 5, 0), {"outcome": "unchanged"})
    c.eq("equal int and float", f(5, 5.0, 0), {"outcome": "unchanged"})
    c.eq("a move", f(100, 110, 0), {"outcome": "changed", "change_pct": 10.0})
    c.eq("a fall is signed", f(100, 90, 0), {"outcome": "changed", "change_pct": -10.0})
    c.eq("relative to the absolute before", f(-100, -90, 0), {"outcome": "changed", "change_pct": 10.0})
    c.eq("from zero there is no percentage", f(0, 5, 0), {"outcome": "changed", "change_pct": None})
    c.eq("zero float", f(0.0, 1, 50), {"outcome": "changed", "change_pct": None})
    c.eq("below the threshold", f(100, 101, 5), {"outcome": "unchanged", "within_min_change": True, "change_pct": 1.0})
    c.eq("below the threshold, falling", f(100, 99, 5), {"outcome": "unchanged", "within_min_change": True, "change_pct": -1.0})
    c.eq("exactly the threshold is a change", f(100, 105, 5), {"outcome": "changed", "change_pct": 5.0})
    c.eq("just under the threshold", f(100, 104, 5), {"outcome": "unchanged", "within_min_change": True, "change_pct": 4.0})
    c.eq("threshold of zero moves nothing under it", f(1000, 1001, 0), {"outcome": "changed", "change_pct": 0.1})
    c.eq("integers too large to divide", f(1, 10**400, 5), {"outcome": "changed", "change_pct": None})
    c.eq("big integers that divide", f(10**400, 10**400 + 1, 5), {"outcome": "unchanged", "within_min_change": True, "change_pct": 0.0})
    c.eq("bool is not a number", f(True, 1, 0), {"outcome": "changed", "change_pct": None})
    c.eq("bool after is not a number", f(1, True, 0), {"outcome": "changed", "change_pct": None})
    c.eq("equal bools", f(True, True, 0), {"outcome": "unchanged"})
    c.eq("different bools", f(True, False, 0), {"outcome": "changed", "change_pct": None})
    c.eq("False is not 0", f(False, 0, 0), {"outcome": "changed", "change_pct": None})
    c.eq("equal text", f("a", "a", 0), {"outcome": "unchanged"})
    c.eq("different text", f("a", "b", 0), {"outcome": "changed", "change_pct": None})
    c.eq("text is not a number", f("1", 1, 0), {"outcome": "changed", "change_pct": None})
    c.eq("a number and text", f(1, "1", 0), {"outcome": "changed", "change_pct": None})
    c.eq("equal empty lists", f([], [], 0), {"outcome": "unchanged"})
    c.eq("empty list vs empty dict", f([], {}, 0), {"outcome": "changed", "change_pct": None})
    c.eq("empty dicts", f({}, {}, 0), {"outcome": "unchanged"})
    c.eq("a list is not a number", f([], 1, 0), {"outcome": "changed", "change_pct": None})
    c.eq("a text vs a list", f("a", [], 0), {"outcome": "changed", "change_pct": None})
    c.eq("a float vs a bool", f(1.0, True, 0), {"outcome": "changed", "change_pct": None})
    c.eq("a number vs None-like text", f(5, "x", 99), {"outcome": "changed", "change_pct": None})
    c.eq(
        "usable",
        (mod._usable(1.5), mod._usable(math.nan), mod._usable(math.inf), mod._usable(-math.inf), mod._usable("x")),
        (True, False, False, False, True),
    )
    c.eq("usable int", mod._usable(10**400), True)
    return c.bad


def check_helpers(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("_shown unset", mod._shown(mod._UNSET), None)
    c.eq("_shown value", mod._shown(0), 0)
    keys = {("b",), ("a", 1), ("a", 0), ("a", "1"), ("a", "b"), ("ab",)}
    order = mod._select({("a", 1), ("a", "1"), ("a", 0)}, None)
    c.eq("twins are told apart by repr", order, [("a", 0), ("a", "1"), ("a", 1)])
    c.eq("select exact", mod._select(keys, ["b"]), [("b",)])
    c.eq("select a prefix", mod._select(keys, ["a"]), [("a", 0), ("a", "1"), ("a", 1), ("a", "b")])
    c.eq("a prefix needs the dot", mod._select({("ab",), ("a", "b")}, ["a"]), [("a", "b")])
    c.eq("select a nested exact", mod._select(keys, ["a.b"]), [("a", "b")])
    c.eq("a missing path is reported as itself", mod._select(keys, ["zz.y"]), [("zz.y",)])
    c.eq("several wanted are united and sorted", mod._select(keys, ["b", "a.0"]), [("a", 0), ("b",)])
    c.eq("the same hit twice is once", mod._select(keys, ["a", "a.b"]), [("a", 0), ("a", "1"), ("a", 1), ("a", "b")])
    c.eq("select from nothing", mod._select(set(), None), [])
    c.eq("select with an empty root key", mod._select({()}, ["x"]), [("x",)])
    t = mod._parse_time
    c.eq("no time", t(None), None)
    c.eq("Z suffix", t("2026-11-09T10:00:00Z").isoformat(), "2026-11-09T10:00:00+00:00")
    c.eq("offset", t("2026-11-09T10:00:00+02:00").isoformat(), "2026-11-09T10:00:00+02:00")
    c.eq("offset instant", t("2026-11-09T10:00:00+02:00").timestamp(), dt.datetime(2026, 11, 9, 8, 0, tzinfo=dt.UTC).timestamp())
    c.eq("naive is UTC", t("2026-11-09T10:00:00").isoformat(), "2026-11-09T10:00:00+00:00")
    c.eq("naive keeps wall time", t("2026-11-09T10:30:15").utcoffset(), dt.timedelta(0))
    c.eq("junk", t("yesterday"), None)
    c.eq("empty", t(""), None)
    m = mod._mode
    c.eq("live live", m("live", "live"), "live")
    c.eq("live none", m("live", None), "mixed")
    c.eq("none live", m(None, "live"), "mixed")
    c.eq("none none", m(None, None), "unknown")
    c.eq("simulated wins", (m("simulated", "live"), m("live", "simulated"), m("simulated", None), m(None, "simulated")), ("simulated",) * 4)
    c.eq("other known combos are mixed", (m("cached", "cached"), m("live", "cached"), m("cached", None)), ("mixed",) * 3)
    c.eq("simulated both", m("simulated", "simulated"), "simulated")
    c.eq("a live pair needs both", m("cached", "live"), "mixed")
    s = mod._span
    c.eq("span 0", s(0), "0 seconds")
    c.eq("span 119", s(119), "119 seconds")
    c.eq("span 120", s(120), "2 minutes")
    c.eq("span 90 min", s(5400), "90 minutes")
    c.eq("span 7199", s(7199), "120 minutes")
    c.eq("span 7200", s(7200), "2.0 hours")
    c.eq("span 5400 s rounding", s(150), "2 minutes")
    c.eq("span 171999", s(171999), "47.8 hours")
    c.eq("span 172800", s(172800), "2.0 days")
    c.eq("span days", s(259200), "3.0 days")
    c.eq("top with percent", mod._top({"path": "p", "change_pct": 4.0, "before": 1, "after": 2}), "p +4.00%")
    c.eq("top with negative percent", mod._top({"path": "p", "change_pct": -2.5, "before": 1, "after": 2}), "p -2.50%")
    c.eq("top without percent", mod._top({"path": "p", "change_pct": None, "before": "a", "after": None}), "p 'a' to None")
    c.eq("top percent zero", mod._top({"path": "p", "change_pct": 0.0, "before": 1, "after": 2}), "p +0.00%")
    return c.bad


def check_validate(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    v = mod._validate
    ex = EXAMPLE_CALL
    c.raises("not an object", lambda: v([]), "arguments", "must be an object", ex)
    c.raises("none", lambda: v(None), "arguments", "must be an object", ex)
    c.raises(
        "unknown argument", lambda: v({"before": {}, "after": {}, "zeta": 1, "alpha": 2}), "alpha", "is not an argument of this tool", ex
    )
    c.raises("before missing", lambda: v({"after": {}}), "before", "is required", ex)
    c.raises("after missing", lambda: v({"before": {}}), "after", "is required", ex)
    c.raises("both missing names before", lambda: v({}), "before", "is required", ex)
    c.raises("a bad before", lambda: v({"before": 1, "after": {}}), "before", "must be an object: an RYO envelope or its data", ex)
    c.raises("a bad after", lambda: v({"before": {}, "after": 1}), "after", "must be an object: an RYO envelope or its data", ex)
    c.raises(
        "different tools",
        lambda: v({"before": {"tool": "a", "data": {}}, "after": {"tool": "b", "data": {}}}),
        "after",
        "is a read of b, not of a; compare two reads of the same tool",
        ex,
    )
    b, a, fields, pct = v({"before": {"tool": "a", "data": {}}, "after": {"tool": "a", "data": {}}})
    c.eq("same tool", (b.tool, a.tool, fields, pct), ("a", "a", None, 0.0))
    b, a, fields, pct = v({"before": {"tool": "a", "data": {}}, "after": {"data": {}}})
    c.eq("a tool on one side only is fine", (b.tool, a.tool), ("a", None))
    b, a, fields, pct = v({"before": {"data": {}, "as_of": "x"}, "after": {"tool": "b", "data": {}}})
    c.eq("a tool on the other side only is fine", (b.tool, a.tool), (None, "b"))
    bad = "must be a list of 1 to 200 dotted paths"
    base = {"before": {}, "after": {}}
    for label, value in (
        ("a string", "a"),
        ("an empty list", []),
        ("201 entries", [str(i) for i in range(201)]),
        ("a number entry", [1]),
        ("an empty entry", [""]),
        ("a 201 character entry", ["x" * 201]),
        ("a bool", True),
        ("a dict", {"a": 1}),
        ("a mixed list", ["a", 1]),
    ):
        c.raises(label, lambda value=value: v({**base, "fields": value}), "fields", bad, ex)
    c.raises("a repeated path", lambda: v({**base, "fields": ["a", "b", "a"]}), "fields", "must not repeat a path", ex)
    got = v({**base, "fields": [str(i) for i in range(200)]})[2]
    c.eq("200 entries", len(got), 200)
    got = v({**base, "fields": ["x" * 200]})[2]
    c.eq("a 200 character entry", got, ["x" * 200])
    c.eq("a one-character entry", v({**base, "fields": ["a"]})[2], ["a"])
    c.eq("fields null is the default", v({**base, "fields": None})[2], None)
    pct_bad = "must be a number from 0 to 100"
    for label, value in (
        ("a bool", True),
        ("false", False),
        ("text", "5"),
        ("None", None),
        ("nan", math.nan),
        ("inf", math.inf),
        ("negative", -0.1),
        ("over 100", 100.1),
        ("a list", [1]),
    ):
        c.raises(f"min_change_pct {label}", lambda value=value: v({**base, "min_change_pct": value}), "min_change_pct", pct_bad, ex)
    for value in (0, 0.0, 50, 99.9, 100, 100.0):
        got_pct = v({**base, "min_change_pct": value})[3]
        c.eq(f"min_change_pct {value!r}", (got_pct, type(got_pct).__name__), (float(value), "float"))
    c.eq("default min_change_pct", v(base)[3], 0.0)
    return c.bad


def check_run_basic(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    out = _run(mod, EXAMPLE_CALL)
    c.eq(
        "keys",
        sorted(out),
        sorted(["schema_version", "tool", "status", "data_mode", "as_of", "request", "data", "summary", "availability", "warnings"]),
    )
    c.eq(
        "envelope head",
        (out["schema_version"], out["tool"], out["status"], out["data_mode"], out["as_of"]),
        ("1.0", "evidence_delta", "partial", "unknown", STAMP),
    )
    c.eq("request", out["request"], {"fields": None, "min_change_pct": 0.0, "before_tool": "analyze_token", "after_tool": "analyze_token"})
    d = out["data"]
    c.eq("verdict", d["verdict"], "changed")
    c.eq("counts", d["counts"], {"compared": 3, "changed": 1, "unchanged": 1, "unknown": 1})
    c.eq("interval", d["interval_seconds"], 3600)
    c.eq("before meta", d["before"], {"tool": "analyze_token", "as_of": "2026-11-09T10:00:00Z", "status": None, "data_mode": None})
    c.eq("after meta", d["after"], {"tool": "analyze_token", "as_of": "2026-11-09T11:00:00Z", "status": None, "data_mode": None})
    c.eq("changes", d["changes"], [{"path": "price_usd", "before": 100.0, "after": 104.0, "change_pct": 4.0}])
    c.eq("unknown", d["unknown"], [{"path": "rsi_14", "before": 55.2, "after": None, "reasons": {"after": "null"}}])
    c.eq("unchanged", d["unchanged"], ["verdict"])
    c.eq("truncated", d["truncated"], {"changes": False, "unknown": False, "unchanged": False})
    c.eq("min", d["min_change_pct"], 0.0)
    c.eq("headline", out["summary"]["headline"], "analyze_token: 1 of 3 fields changed over 60 minutes: price_usd +4.00%.")
    c.eq("key points", out["summary"]["key_points"], ["1 changed, 1 unchanged, 1 could not be compared.", "Not compared: rsi_14."])
    c.eq("availability", out["availability"], {"comparison": "available"})
    c.eq("warnings", out["warnings"], [])
    c.eq("the clock is read when now is not given", mod.run({"before": {"a": 1}, "after": {"a": 1}})["as_of"][:2], "20")
    c.eq(
        "now is formatted as given",
        mod.run({"before": {"a": 1}, "after": {"a": 1}}, now=dt.datetime(2030, 1, 2, 3, 4, 5, 999, tzinfo=dt.UTC))["as_of"],
        "2030-01-02T03:04:05Z",
    )
    c.eq(
        "extra keywords are ignored", mod.run({"before": {"a": 1}, "after": {"a": 1}}, now=NOW, transport=None, sleep=None)["status"], "ok"
    )
    return c.bad


def check_run_verdicts(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    r = _run(mod, {"before": {"a": 1, "b": "x"}, "after": {"a": 1, "b": "x"}})
    c.eq("unchanged", (r["data"]["verdict"], r["status"], r["availability"]), ("unchanged", "ok", {"comparison": "available"}))
    c.eq("unchanged headline", r["summary"]["headline"], "the evidence: no field changed; all 2 compared fields are known in both reads.")
    c.eq("unchanged points", r["summary"]["key_points"], ["0 changed, 2 unchanged, 0 could not be compared."])
    c.eq("unchanged counts", r["data"]["counts"], {"compared": 2, "changed": 0, "unchanged": 2, "unknown": 0})
    c.eq("no interval without times", r["data"]["interval_seconds"], None)
    c.eq("no-time warning", r["warnings"], ["a read has no as_of, so the time between the reads is not known"])
    r = _run(
        mod,
        {
            "before": {"tool": "T", "as_of": "2026-11-09T10:00:00Z", "data": {"a": 1, "b": None}},
            "after": {"tool": "T", "as_of": "2026-11-09T10:00:30Z", "data": {"a": 1, "b": 2}},
        },
    )
    c.eq(
        "unchanged where known",
        (r["data"]["verdict"], r["status"], r["availability"]),
        ("unchanged_where_known", "partial", {"comparison": "available"}),
    )
    c.eq(
        "uwk headline",
        r["summary"]["headline"],
        "T: no known field changed over 30 seconds, but 1 of 2 could not be compared. That is not no change.",
    )
    c.eq("uwk points", r["summary"]["key_points"], ["0 changed, 1 unchanged, 1 could not be compared.", "Not compared: b."])
    c.eq("uwk warnings", r["warnings"], [])
    r = _run(
        mod,
        {
            "before": {"tool": "T", "as_of": "2026-11-09T10:00:00Z", "data": {"a": None}},
            "after": {"tool": "T", "as_of": "2026-11-09T10:10:00Z", "data": {"a": None}},
        },
    )
    c.eq(
        "insufficient",
        (r["data"]["verdict"], r["status"], r["availability"]),
        ("insufficient_evidence", "unavailable", {"comparison": "unavailable"}),
    )
    c.eq(
        "insufficient headline",
        r["summary"]["headline"],
        "T: nothing could be compared over 10 minutes; no field is known in both reads. That is not no change.",
    )
    r = _run(mod, {"before": {}, "after": {}})
    c.eq(
        "empty reads are equal empty objects",
        (r["data"]["verdict"], r["data"]["counts"]),
        ("unchanged", {"compared": 1, "changed": 0, "unchanged": 1, "unknown": 0}),
    )
    r = _run(mod, {"before": {"a": 1}, "after": {"b": 1}})
    c.eq("disjoint fields are unknown", (r["data"]["verdict"], r["data"]["counts"]["unknown"]), ("insufficient_evidence", 2))
    c.eq(
        "absent reasons",
        r["data"]["unknown"],
        [
            {"path": "a", "before": 1, "after": None, "reasons": {"after": "absent"}},
            {"path": "b", "before": None, "after": 1, "reasons": {"before": "absent"}},
        ],
    )
    r = _run(mod, {"before": {"a": 1, "b": None}, "after": {"a": 2, "b": None}})
    c.eq("a change beats unknowns", (r["data"]["verdict"], r["status"]), ("changed", "partial"))
    r = _run(mod, {"before": {"a": 1}, "after": {"a": 2}})
    c.eq("changed and fully known is ok", r["status"], "ok")
    c.eq("changed headline without a tool or span", r["summary"]["headline"], "the evidence: 1 of 1 fields changed: a +100.00%.")
    r = _run(mod, {"before": {"a": 1, "b": 1, "c": 1}, "after": {"a": 2, "b": 3, "c": "z"}})
    c.eq("a change without a percentage sorts first", [x["path"] for x in r["data"]["changes"]], ["c", "b", "a"])
    c.eq("top three are shown", r["summary"]["headline"], "the evidence: 3 of 3 fields changed: c 1 to 'z', b +200.00%, a +100.00%.")
    r = _run(mod, {"before": {"a": 1, "b": 1, "c": 1, "d": 1}, "after": {"a": 2, "b": 2, "c": 2, "d": 2}})
    c.eq(
        "no more than three in the headline",
        r["summary"]["headline"],
        "the evidence: 4 of 4 fields changed: a +100.00%, b +100.00%, c +100.00%.",
    )
    r = _run(mod, {"before": {"a": 1, "b": 1}, "after": {"a": 3, "b": -1}})
    c.eq("sorted by size of move, either way", [x["change_pct"] for x in r["data"]["changes"]], [200.0, -200.0])
    r = _run(mod, {"before": {"a": 1, "b": 1}, "after": {"a": 2, "b": 0}})
    c.eq("a bigger fall sorts by absolute size", [x["path"] for x in r["data"]["changes"]], ["a", "b"])
    r = _run(mod, {"before": {"a": 1.0, "b": 1.0}, "after": {"a": 1.00000049, "b": 1.5}})
    c.eq("the percentage is rounded to six places", [x["change_pct"] for x in r["data"]["changes"]], [50.0, 4.9e-05])
    r = _run(mod, {"before": {"a": 3}, "after": {"a": 4}})
    c.eq("six places exactly", r["data"]["changes"][0]["change_pct"], 33.333333)
    r = _run(mod, {"before": {"a": 0, "b": "p"}, "after": {"a": 5, "b": "q"}})
    c.eq("two text-like changes keep path order", [x["path"] for x in r["data"]["changes"]], ["a", "b"])
    c.eq("a change from zero has no percent", r["data"]["changes"][0], {"path": "a", "before": 0, "after": 5, "change_pct": None})
    r = _run(mod, {"before": {"a": 100, "b": 100}, "after": {"a": 101, "b": 120}, "min_change_pct": 5})
    c.eq(
        "min change",
        (r["data"]["counts"], r["data"]["unchanged"], r["request"]["min_change_pct"], r["data"]["min_change_pct"]),
        ({"compared": 2, "changed": 1, "unchanged": 1, "unknown": 0}, ["a"], 5.0, 5.0),
    )
    return c.bad


def check_run_warnings(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    base = {"as_of": "2026-11-09T10:00:00Z", "data": {"a": 1}}
    r = _run(mod, {"before": {**base, "status": "partial"}, "after": {**base, "status": "unavailable"}})
    c.eq(
        "partial and unavailable reads",
        r["warnings"],
        [
            "before is a partial read: its missing fields are unknown, not unchanged",
            "after is a unavailable read: its missing fields are unknown, not unchanged",
        ],
    )
    c.eq("a partial read makes the status partial", r["status"], "partial")
    c.eq("status echoed", (r["data"]["before"]["status"], r["data"]["after"]["status"]), ("partial", "unavailable"))
    r = _run(mod, {"before": {**base, "status": "ok", "data_mode": "live"}, "after": {**base, "status": "ok", "data_mode": "live"}})
    c.eq("live ok", (r["status"], r["data_mode"], r["warnings"]), ("ok", "live", []))
    r = _run(mod, {"before": {**base, "status": "ok"}, "after": {**base, "status": "unavailable"}})
    c.eq(
        "only the after partial",
        (r["status"], r["warnings"]),
        ("partial", ["after is a unavailable read: its missing fields are unknown, not unchanged"]),
    )
    r = _run(mod, {"before": {**base, "status": "partial"}, "after": {**base, "status": "ok"}})
    c.eq(
        "only the before partial",
        (r["status"], r["warnings"]),
        ("partial", ["before is a partial read: its missing fields are unknown, not unchanged"]),
    )
    r = _run(mod, {"before": {**base, "data_mode": "simulated"}, "after": {**base, "data_mode": "live"}})
    c.eq("simulated before", (r["data_mode"], r["warnings"]), ("simulated", ["before is simulated data"]))
    r = _run(mod, {"before": {**base, "data_mode": "live"}, "after": {**base, "data_mode": "simulated"}})
    c.eq("simulated after", r["warnings"], ["after is simulated data"])
    r = _run(mod, {"before": {**base, "data_mode": "live"}, "after": {**base, "data_mode": "cached"}})
    c.eq("no warning for other modes", (r["data_mode"], r["warnings"]), ("mixed", []))
    r = _run(
        mod, {"before": {"as_of": "2026-11-09T11:00:00Z", "data": {"a": 1}}, "after": {"as_of": "2026-11-09T10:00:00Z", "data": {"a": 2}}}
    )
    c.eq(
        "after earlier",
        (r["data"]["interval_seconds"], r["warnings"]),
        (-3600, ["after is dated earlier than before; the comparison is made as given"]),
    )
    c.eq("no span when negative", r["summary"]["headline"], "the evidence: 1 of 1 fields changed: a +100.00%.")
    r = _run(
        mod, {"before": {"as_of": "2026-11-09T10:00:00Z", "data": {"a": 1}}, "after": {"as_of": "2026-11-09T10:00:00Z", "data": {"a": 2}}}
    )
    c.eq("same instant", (r["data"]["interval_seconds"], r["warnings"]), (0, []))
    c.eq("a zero span is shown", r["summary"]["headline"], "the evidence: 1 of 1 fields changed over 0 seconds: a +100.00%.")
    r = _run(mod, {"before": {"as_of": "2026-11-09T10:00:00Z", "data": {"a": 1}}, "after": {"data": {"a": 1}}})
    c.eq(
        "one as_of only",
        (r["data"]["interval_seconds"], r["warnings"]),
        (None, ["a read has no as_of, so the time between the reads is not known"]),
    )
    r = _run(mod, {"before": {"data": {"a": 1}, "tool": "x"}, "after": {"as_of": "2026-11-09T10:00:00Z", "data": {"a": 1}}})
    c.eq("the other as_of only", r["warnings"], ["a read has no as_of, so the time between the reads is not known"])
    r = _run(mod, {"before": {"as_of": "junk", "data": {"a": 1}}, "after": {"as_of": "2026-11-09T10:00:00Z", "data": {"a": 1}}})
    c.eq(
        "an unparsable as_of gives no interval and no warning",
        (r["data"]["interval_seconds"], r["warnings"], r["data"]["before"]["as_of"]),
        (None, [], "junk"),
    )
    r = _run(mod, {"before": {"as_of": "2026-11-09T10:00:00Z", "data": {"a": 1}}, "after": {"as_of": "junk", "data": {"a": 1}}})
    c.eq("an unparsable after as_of", r["data"]["interval_seconds"], None)
    r = _run(
        mod, {"before": {"as_of": "2026-11-09T10:00:00Z", "data": {"a": 1}}, "after": {"as_of": "2026-11-09T10:00:00.6Z", "data": {"a": 1}}}
    )
    c.eq("interval is rounded", r["data"]["interval_seconds"], 1)
    r = _run(
        mod, {"before": {"as_of": "2026-11-09T10:00:00Z", "data": {"a": 1}}, "after": {"as_of": "2026-11-09T10:00:00.4Z", "data": {"a": 1}}}
    )
    c.eq("interval is rounded down", r["data"]["interval_seconds"], 0)
    ok = {"as_of": "2026-11-09T10:00:00Z"}
    r = _run(mod, {"before": {**ok, "data": {"a": 1, "b": 2}}, "after": {**ok, "data": {"a": 1, "b": 2}}, "fields": ["a", "x", "y"]})
    c.eq(
        "requested fields in neither read",
        (r["warnings"], r["data"]["verdict"], r["request"]["fields"]),
        (["2 requested field(s) are in neither read"], "unchanged_where_known", ["a", "x", "y"]),
    )
    c.eq(
        "their entries",
        r["data"]["unknown"],
        [
            {"path": "x", "before": None, "after": None, "reasons": {"before": "absent", "after": "absent"}},
            {"path": "y", "before": None, "after": None, "reasons": {"before": "absent", "after": "absent"}},
        ],
    )
    r = _run(mod, {"before": {**ok, "data": {"a": 1}}, "after": {**ok, "data": {"a": 1}}, "fields": ["zz"]})
    c.eq(
        "one requested field in neither read",
        (r["warnings"], r["data"]["verdict"]),
        (["1 requested field(s) are in neither read"], "insufficient_evidence"),
    )
    r = _run(mod, {"before": {**ok, "data": {"a": 1}}, "after": {**ok, "data": {"b": 1}}, "fields": ["a"]})
    c.eq("absent in one read only is not 'neither'", r["warnings"], [])
    r = _run(mod, {"before": {**ok, "data": {"a": None}}, "after": {**ok, "data": {"a": None}}, "fields": ["a"]})
    c.eq("null in both reads is not 'neither'", r["warnings"], [])
    r = _run(mod, {"before": {**ok, "data": {"a": 1}}, "after": {**ok, "data": {"a": None}}, "fields": ["a"]})
    c.eq("null after, present before", r["warnings"], [])
    r = _run(mod, {"before": {**ok, "data": {"a": None}}, "after": {**ok, "data": {}}, "fields": ["a"]})
    c.eq("null before, absent after is not 'neither'", r["warnings"], [])
    r = _run(mod, {"before": {**ok, "data": {"a": 1, "b": 2}}, "after": {**ok, "data": {"a": 1, "b": 3}}, "fields": ["b"]})
    c.eq(
        "fields limit the comparison",
        (r["data"]["counts"], r["data"]["changes"][0]["path"]),
        ({"compared": 1, "changed": 1, "unchanged": 0, "unknown": 0}, "b"),
    )
    r = _run(mod, {"before": {**ok, "data": {"a": 1}}, "after": {**ok, "data": {"a": 1}}})
    c.eq("no fields, no neither-warning", r["warnings"], [])
    c.eq("fields null in the request", r["request"]["fields"], None)
    r = _run(mod, {"before": {**ok, "data": {"a.b": 1, "a": {"b": 1}}}, "after": {**ok, "data": {"a.b": 1, "a": {"b": 2}}}})
    c.eq(
        "twin paths",
        r["warnings"],
        [
            "1 path(s) are written the same as another but differ in structure (a key that holds a dot or a number, or a list index); each is compared on its own and they are told apart by their values and places"
        ],
    )
    c.eq("twins are both counted", r["data"]["counts"], {"compared": 2, "changed": 1, "unchanged": 1, "unknown": 0})
    r = _run(mod, {"before": {**ok, "data": {"a": {"0": 1}}}, "after": {**ok, "data": {"a": [1]}}})
    c.eq(
        "a key and a list index look alike",
        (r["warnings"][0][:8], r["data"]["verdict"], r["data"]["counts"]["unknown"]),
        ("1 path(s", "insufficient_evidence", 2),
    )
    r = _run(mod, {"before": {**ok, "data": {"a": 1, "b": 1}}, "after": {**ok, "data": {"a": 1, "b": 1}}})
    c.eq("no twins", r["warnings"], [])
    return c.bad


def check_run_limits(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    n = 205
    before = {f"k{i:03d}": i + 1 for i in range(n)}
    after = {f"k{i:03d}": i + 2 for i in range(n)}
    r = _run(mod, {"before": before, "after": after})
    d = r["data"]
    c.eq("counts are the full counts", d["counts"], {"compared": n, "changed": n, "unchanged": 0, "unknown": 0})
    c.eq("changes cut at 200", (len(d["changes"]), d["truncated"]), (200, {"changes": True, "unknown": False, "unchanged": False}))
    c.eq("the biggest moves are kept", d["changes"][0]["path"], "k000")
    r = _run(mod, {"before": before, "after": before})
    d = r["data"]
    c.eq("unchanged cut at 200", (len(d["unchanged"]), d["truncated"]), (200, {"changes": False, "unknown": False, "unchanged": True}))
    for label, key, edit in (
        ("changes", "changes", lambda m: (dict.fromkeys(m, 1), dict.fromkeys(m, 2))),
        ("unknown", "unknown", lambda m: (dict.fromkeys(m, 1), dict.fromkeys(m))),
    ):
        left, right = edit({f"k{i:03d}": 0 for i in range(200)})
        r = _run(mod, {"before": left, "after": right})
        c.eq(f"exactly 200 {label} are not truncated", (len(r["data"][key]), r["data"]["truncated"][key]), (200, False))
    r = _run(mod, {"before": before, "after": dict.fromkeys(before)})
    d = r["data"]
    c.eq(
        "unknown cut at 200",
        (len(d["unknown"]), d["truncated"], d["counts"]["unknown"]),
        (200, {"changes": False, "unknown": True, "unchanged": False}, n),
    )
    c.eq("not compared list is cut at five with an ellipsis", r["summary"]["key_points"][1], "Not compared: k000, k001, k002, k003, k004….")
    exact = {f"k{i:03d}": 1 for i in range(200)}
    r = _run(mod, {"before": exact, "after": exact})
    c.eq("exactly 200 is not truncated", (len(r["data"]["unchanged"]), r["data"]["truncated"]["unchanged"]), (200, False))
    five = {f"k{i}": None for i in range(5)}
    r = _run(mod, {"before": {**five, "z": 1}, "after": {**five, "z": 1}})
    c.eq("five not compared have no ellipsis", r["summary"]["key_points"][1], "Not compared: k0, k1, k2, k3, k4.")
    r = _run(mod, {"before": {**five, "k5": None, "z": 1}, "after": {**five, "k5": None, "z": 1}})
    c.eq("six not compared have one", r["summary"]["key_points"][1], "Not compared: k0, k1, k2, k3, k4….")
    r = _run(mod, {"before": {"a": 1}, "after": {"a": 1}})
    c.eq("one point when nothing is unknown", len(r["summary"]["key_points"]), 1)
    c.raises(
        "a deep read",
        lambda: _run(mod, {"before": {"a": {"b": {}}}, "after": _deep(13)}),
        "k",
        "is nested deeper than 12 levels",
        EXAMPLE_CALL,
    )
    c.raises(
        "too many leaves",
        lambda: _run(mod, {"before": {str(i): i for i in range(2001)}, "after": {}}),
        "before",
        "has more than 2000 fields",
        EXAMPLE_CALL,
    )
    return c.bad


def _deep(levels: int) -> dict[str, Any]:
    deep: Any = 1
    for _ in range(levels):
        deep = {"k": deep}
    return deep  # type: ignore[no-any-return]


def check_rules(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    r = mod.run_rules({}, now=NOW)
    c.eq(
        "head",
        (r["schema_version"], r["tool"], r["status"], r["data_mode"], r["as_of"], r["request"], r["warnings"], r["availability"]),
        ("1.0", "evidence_delta_rules", "ok", "unknown", STAMP, {}, [], {"rules": "available"}),
    )
    rules = r["data"]["rules"]
    c.eq("rule count", len(rules), 10)
    c.eq(
        "first rule",
        rules[0],
        "changed: both reads have a value there and the values differ (a number moves by at least min_change_pct percent)",
    )
    c.eq("second rule", rules[1], "unchanged: both reads have a value there and they are equal")
    c.eq(
        "third rule",
        rules[2],
        "unknown: a read has no value there (null, absent, or a number that is not finite); neither changed nor unchanged is inferred",
    )
    c.eq(
        "verdict rules",
        rules[3:7],
        [
            "verdict changed: at least one field changed",
            "verdict unchanged: nothing changed and every compared field is known in both reads",
            "verdict unchanged_where_known: nothing known changed, and some fields could not be compared",
            "verdict insufficient_evidence: no field is known in both reads",
        ],
    )
    c.eq(
        "lists and strings",
        rules[7:9],
        [
            "lists are compared index by index: an inserted element shifts the ones after it",
            "strings are compared exactly, with no case or space normalisation; booleans and numbers are never equal to each other",
        ],
    )
    c.eq(
        "limits rule", rules[9], "limits: depth 12, 2000 fields per read, 200 entries shown per list; a call reads nothing from the network"
    )
    c.eq("summary", r["summary"], {"headline": "evidence_delta never reads a missing value as no change.", "key_points": rules[:3]})
    c.eq("clock when now is not given", mod.run_rules({})["as_of"][:2], "20")
    c.eq("extra keywords are ignored", mod.run_rules({}, now=NOW, x=1)["status"], "ok")
    for label, args in (("a non-empty object", {"a": 1}), ("a list", []), ("None", None), ("text", "x")):
        c.raises(label, lambda args=args: mod.run_rules(args), "arguments", "this tool takes none", {})
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    for check in (
        check_parse_read,
        check_shown_and_flatten,
        check_compare_leaf,
        check_helpers,
        check_validate,
        check_run_basic,
        check_run_verdicts,
        check_run_warnings,
        check_run_limits,
        check_rules,
    ):
        try:
            out.extend(check(mod))
        except Exception as exc:
            out.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    return out
