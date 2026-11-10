"""Checks of ``ryo_verdict_record.skill``, runnable against a mutant: the Wilson interval against known values and an independent form of the
formula, the scoring and hit rule over every reading and direction, every refusal of an argument with its exact words, whole answers for
records built so that one rule decides each, and the rules text."""

from __future__ import annotations

import datetime as dt
import math
import re
import types
from typing import Any

from ryo_verdict_record.errors import InputError
from ryo_verdict_record.schema import EXAMPLE_CALL, MAX_OUTCOMES
from tests.skill_expected import EXPECTED
from tests.skill_scenarios import SCENARIOS, many, o

NOW = dt.datetime(2026, 11, 9, 12, 0, 30, tzinfo=dt.UTC)
READINGS = ("UP", "DOWN", "NOT_LONG", "NONE", "UNREAD")
DIRECTIONS = ("ROSE", "FELL", "TIE", None)
SAME_WAY = (
    "every scored window in this record went the same way, and the market's rate is taken from these windows; no reading can be told from "
    "luck here, so none is called above or below the market"
)
# (hits, n) -> (low, high), from the Wilson score formula worked by hand to four places
WILSON_KNOWN = {
    (5, 10): (0.2366, 0.7634),
    (1, 1): (0.2065, 1.0),
    (0, 1): (0.0, 0.7935),
    (0, 10): (0.0, 0.2775),
    (10, 10): (0.7225, 1.0),
    (20, 20): (0.8389, 1.0),
    (3, 7): (0.1582, 0.7495),
    (50, 100): (0.4038, 0.5962),
}
RULES = [
    "a reading is right when it said up and the window rose, or said down or against buying and the window fell",
    "a tie, a window not yet settled and a reading that is not a direction are not scored; they are counted with a reason and are never a miss",
    "the hit rate is hits over scored reads, with a 95 percent Wilson interval",
    "it is compared with the market's own rate for that side, taken from every scored window in the record: the share that rose for up, "
    "the share that fell for down and against buying",
    "before min_scored (default 10) scored reads of a reading there is no comparison: insufficient",
    "above_market or below_market only when the whole interval is on that side of the market's rate; otherwise not_distinguishable",
    "the recorded hit is checked against the rule; a disagreement is reported and the rule's hit is used",
    "the plan counts say which of RYO's own levels came first (target, stop, timeout, ambiguous) or why there is no answer",
    "limits: at most 5000 outcomes; nothing is sent anywhere; the rates describe the record, not the future",
]


def _wilson_other_form(hits: int, n: int) -> tuple[float, float]:
    """The same interval written the other way round (the quadratic's two roots), as an independent check of the code's form."""
    z, p = 1.959963984540054, hits / n
    a = n + z * z
    b = 2 * n * p + z * z
    root = z * math.sqrt(z * z + 4 * n * p * (1 - p))
    return max(0.0, (b - root) / (2 * a)), min(1.0, (b + root) / (2 * a))


def _same(got: Any, want: Any) -> bool:
    return bool(got == want) and type(got) is type(want)


def _refusal(mod: types.ModuleType, label: str, args: object, field: str, message: str, out: list[str], **kw: Any) -> None:
    try:
        got = mod.run(args, now=NOW, **kw)
    except InputError as exc:
        if exc.field != field or exc.detail != message or str(exc) != f"{field}: {message}":
            out.append(f"{label}: refused as {exc.field!r} {exc.detail!r}, expected {field!r} {message!r}")
        if exc.example != EXAMPLE_CALL:
            out.append(f"{label}: the refusal does not carry the working example call")
    except Exception as exc:
        out.append(f"{label}: raised {type(exc).__name__}, expected a refusal")
    else:
        out.append(f"{label}: accepted ({got.get('status')}), expected a refusal")


def _accepted(mod: types.ModuleType, label: str, args: object, out: list[str]) -> dict[str, Any] | None:
    try:
        return mod.run(args, now=NOW)  # type: ignore[no-any-return]
    except Exception as exc:
        out.append(f"{label}: refused ({type(exc).__name__}: {exc}), expected an answer")
        return None


def check_wilson(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    for n in (0, -1, -5):
        if mod.wilson(0, n) != (0.0, 1.0) or mod.wilson(3, n) != (0.0, 1.0):
            out.append(f"wilson(.., {n}) is {mod.wilson(0, n)}, expected (0.0, 1.0)")
    for (hits, n), (low, high) in WILSON_KNOWN.items():
        got = mod.wilson(hits, n)
        if abs(got[0] - low) > 5e-5 or abs(got[1] - high) > 5e-5:
            out.append(f"wilson({hits}, {n}) is {got}, expected about {(low, high)}")
    for n in (1, 2, 3, 7, 10, 19, 20, 64, 100, 399):
        for hits in range(n + 1):
            got = mod.wilson(hits, n)
            want = _wilson_other_form(hits, n)
            if abs(got[0] - want[0]) > 1e-12 or abs(got[1] - want[1]) > 1e-12:
                out.append(f"wilson({hits}, {n}) is {got}, the other form of the formula gives {want}")
                break
            if (hits == 0 and got[0] != 0.0) or (hits == n and got[1] != 1.0):
                out.append(f"wilson({hits}, {n}) is {got}: an end that is exactly 0 or 1 must be exactly that, not 1e-17 away")
            if not 0.0 <= got[0] <= got[1] <= 1.0 or not got[0] <= hits / n <= got[1]:
                out.append(f"wilson({hits}, {n}) is {got}: not an interval inside 0..1 that holds the rate")
    if not all(isinstance(x, float) for x in mod.wilson(4, 8)):
        out.append("wilson returns something other than floats")
    if mod.Z95 != 1.959963984540054:
        out.append("Z95 is not the 97.5th percentile of the normal distribution")
    return out


def check_scoring_rule(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    why = {"NONE": "not_a_direction", "UNREAD": "not_a_direction"}
    right = {("UP", "ROSE"), ("DOWN", "FELL"), ("NOT_LONG", "FELL")}
    for reading in READINGS:
        for direction in DIRECTIONS:
            if reading in why:
                want_why: str | None = why[reading]
            elif direction is None:
                want_why = "not_settled"
            elif direction == "TIE":
                want_why = "tie"
            else:
                want_why = None
            got_why = mod.scored_direction(reading, direction)
            if got_why != want_why:
                out.append(f"scored_direction({reading}, {direction}) is {got_why!r}, expected {want_why!r}")
            want_hit = None if want_why else ((reading, direction) in right)
            if not _same(mod.hit_of(reading, direction), want_hit):
                out.append(f"hit_of({reading}, {direction}) is {mod.hit_of(reading, direction)!r}, expected {want_hit!r}")
    if mod.scored_direction("up", "ROSE") != "not_a_direction":
        out.append("a lower-case reading is taken as a direction")
    if mod.RIGHT_DIRECTION != {"UP": "ROSE", "DOWN": "FELL", "NOT_LONG": "FELL"}:
        out.append(f"the table of right directions is {mod.RIGHT_DIRECTION}")
    return out


def check_refusals(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    rec = {"record": []}
    for label, args in (("none", None), ("list", []), ("text", "x"), ("number", 5), ("empty-list", [])):
        _refusal(mod, f"arguments {label}", args, "arguments", "must be an object", out)
    _refusal(mod, "unknown", {**rec, "foo": 1}, "foo", "is not an argument of this tool", out)
    _refusal(mod, "unknown sorted", {**rec, "zeta": 1, "alpha": 2, "mid": 3}, "alpha", "is not an argument of this tool", out)
    _refusal(mod, "no record", {}, "record", "is required", out)
    _refusal(mod, "no record, symbol only", {"symbol": "SOL"}, "record", "is required", out)
    shape = "must be an object with an outcomes list, or a list of outcomes"
    for label, record in (("text", "x"), ("number", 5), ("none", None), ("no outcomes", {}), ("outcomes text", {"outcomes": "x"})):
        _refusal(mod, f"record {label}", {"record": record}, "record", shape, out)
    _refusal(mod, "outcomes dict", {"record": {"outcomes": {}}}, "record", shape, out)
    _refusal(
        mod, "too many", {"record": [o("A", "UP", "ROSE")] * (MAX_OUTCOMES + 1)}, "record", f"has more than {MAX_OUTCOMES} outcomes", out
    )
    _refusal(mod, "too many, in object", {"record": {"outcomes": [0] * (MAX_OUTCOMES + 1)}}, "record", "has more than 5000 outcomes", out)
    sym = "must be 1 to 20 capital letters or digits"
    for label, symbol in (
        ("number", 5),
        ("lower", "sol"),
        ("empty", ""),
        ("21", "A" * 21),
        ("space", "SO L"),
        ("newline", "SOL\n"),
        ("dash", "A-B"),
    ):
        _refusal(mod, f"symbol {label}", {**rec, "symbol": symbol}, "symbol", sym, out)
    _refusal(mod, "symbol list", {**rec, "symbol": ["SOL"]}, "symbol", sym, out)
    low = "must be a whole number from 1 to 1000"
    for label, minimum in (
        ("true", True),
        ("false", False),
        ("text", "5"),
        ("float", 5.0),
        ("zero", 0),
        ("negative", -1),
        ("1001", 1001),
        ("none", None),
    ):
        _refusal(mod, f"min_scored {label}", {**rec, "min_scored": minimum}, "min_scored", low, out)
    # the order of the checks: arguments, unknown, record, outcomes, symbol, min_scored
    _refusal(mod, "unknown before record", {"foo": 1}, "foo", "is not an argument of this tool", out)
    _refusal(mod, "record before symbol", {"record": 5, "symbol": "x", "min_scored": 0}, "record", shape, out)
    _refusal(mod, "symbol before min_scored", {**rec, "symbol": "x", "min_scored": 0}, "symbol", sym, out)
    # accepted edges
    for label, args in (
        ("5000 outcomes", {"record": [o("A", "UP", "ROSE")] * MAX_OUTCOMES}),
        ("20 letters", {**rec, "symbol": "A" * 20}),
        ("one letter", {**rec, "symbol": "A"}),
        ("digits", {**rec, "symbol": "1INCH"}),
        ("min 1", {**rec, "min_scored": 1}),
        ("min 1000", {**rec, "min_scored": 1000}),
        ("symbol none", {**rec, "symbol": None}),
    ):
        _accepted(mod, label, args, out)
    wide = _accepted(mod, "5000 outcomes", {"record": [o("A", "UP", "ROSE")] * MAX_OUTCOMES}, out)
    if wide is not None and wide["data"]["reads"] != MAX_OUTCOMES:
        out.append(f"5000 outcomes were read as {wide['data']['reads']}")
    for kw in ({"extra": 1}, {"sleep": None}):
        if mod.run(rec, now=NOW, **kw)["status"] != "unavailable":
            out.append(f"run does not ignore the keyword {kw}")
    return out


def check_answers(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    for name, args in SCENARIOS.items():
        got = mod.run(args, now=NOW)
        want = EXPECTED[name]
        if got != want:
            diffs = [k for k in want if got.get(k) != want[k]]
            deeper = [k for k in want["data"] if got["data"].get(k) != want["data"][k]] if "data" in diffs else []
            out.append(f"scenario {name}: differs in {diffs} {deeper}")
        elif (
            list(got) != list(want)
            or list(got["data"]) != list(want["data"])
            or list(got["data"]["by_reading"]) != list(want["data"]["by_reading"])
        ):
            out.append(f"scenario {name}: same values, different order of keys")
        elif list(got["data"]["plans"]) != list(want["data"]["plans"]) or list(got["data"]["unscored"]) != list(want["data"]["unscored"]):
            out.append(f"scenario {name}: same values, different order of the counts")
        elif json_types(got) != json_types(want):
            out.append(f"scenario {name}: same values, different types (a count that is a float, or a rate that is a count)")
    return out


def json_types(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: json_types(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_types(v) for v in value]
    return type(value).__name__


def check_rules_of_record(mod: types.ModuleType) -> list[str]:
    """Properties that hold for any record, so that a change which keeps the fixed scenarios right still shows."""
    out: list[str] = []
    # rates are the share of scored windows; a hit is what the rule says, whatever the record says
    outcomes = [o("A", r, d, hit=False) for r in ("UP", "DOWN", "NOT_LONG") for d in ("ROSE", "FELL", "ROSE", "FELL", "FELL")]
    got = mod.run({"record": outcomes, "min_scored": 1}, now=NOW)
    data = got["data"]
    if data["market_rate_rose"] != 0.4 or data["scored"] != 15 or data["reads"] != 15:
        out.append(f"15 reads (6 rose, 9 fell): rose {data['market_rate_rose']}, scored {data['scored']}, reads {data['reads']}")
    if [data["by_reading"][k]["hits"] for k in ("UP", "DOWN", "NOT_LONG")] != [2, 3, 3]:
        out.append("hits of UP, DOWN and NOT_LONG over ROSE FELL ROSE FELL FELL are not 2, 3 and 3")
    if [data["by_reading"][k]["market_rate"] for k in ("UP", "DOWN", "NOT_LONG")] != [0.4, 0.6, 0.6]:
        out.append("the market's rate is not the share that rose for UP and the share that fell for DOWN and NOT_LONG")
    if not any("disagree" in w for w in got["warnings"]) or got["status"] != "partial":
        out.append("a recorded hit that is false against a right read raised no warning")
    # a recorded hit that agrees raises none; a recorded hit that is not a bool is not compared
    agree = [o("A", "UP", "ROSE", True), o("A", "UP", "FELL", False), o("A", "DOWN", "FELL", True), o("A", "NOT_LONG", "ROSE", False)]
    if mod.run({"record": agree}, now=NOW)["warnings"]:
        out.append("hits that agree with the rule raised a warning")
    other = [o("A", "UP", "ROSE", 1), o("A", "UP", "ROSE", "True"), o("A", "UP", "FELL", 0), o("A", "UP", "FELL", None)]
    if mod.run({"record": other}, now=NOW)["warnings"]:
        out.append("a recorded hit that is not a bool was compared")
    # a read that is not scored is not compared either, whatever its recorded hit says
    unscored_hit = [o("A", "UP", None, True), o("A", "NONE", "ROSE", False), o("A", "DOWN", "TIE", True)]
    if mod.run({"record": unscored_hit}, now=NOW)["warnings"]:
        out.append("a recorded hit on a read that is not scored was compared")
    # exactly one mismatch is counted once; the count is of reads
    one = mod.run({"record": [o("A", "UP", "ROSE", False), o("A", "UP", "FELL", False)]}, now=NOW)
    if one["warnings"] != ["1 recorded hit(s) disagree with the rule; the rule's hit is used, and the record should be looked at"]:
        out.append(f"one disagreeing hit: warnings {one['warnings']}")
    # the same-way warning: two scored, all one way (either way); one scored; mixed
    for label, directions, warned in (
        ("two rose", ["ROSE", "ROSE"], True),
        ("two fell", ["FELL", "FELL"], True),
        ("one rose", ["ROSE"], False),
        ("one fell", ["FELL"], False),
        ("rose fell", ["ROSE", "FELL"], False),
        ("fell rose rose", ["FELL", "ROSE", "ROSE"], False),
    ):
        w = mod.run({"record": many("A", "UP", directions), "min_scored": 1}, now=NOW)["warnings"]
        if (SAME_WAY in w) is not warned:
            out.append(f"{label}: same-way warning is {SAME_WAY in w}, expected {warned}")
    # an unscored read does not count toward the two
    w = mod.run({"record": [o("A", "UP", "ROSE"), o("A", "UP", None), o("A", "UP", "TIE")]}, now=NOW)["warnings"]
    if SAME_WAY in w:
        out.append("reads that are not scored counted toward the same-way warning")
    # a verdict is never above or below the market when every window went one way, however many reads
    for direction in ("ROSE", "FELL"):
        for reading in ("UP", "DOWN", "NOT_LONG"):
            for n in (1, 2, 3, 8, 30, 120):
                res = mod.run({"record": many("A", reading, [direction] * n), "min_scored": 1}, now=NOW)
                verdict = res["data"]["by_reading"][reading]["verdict"]
                if verdict not in ("not_distinguishable",):
                    out.append(f"{n} x {reading} over windows that all {direction}: verdict {verdict}")
    # the minimum: n < min is insufficient, n == min compares
    for n, minimum, word in ((9, 10, "insufficient"), (10, 10, "above_market"), (11, 10, "above_market"), (1, 1, "above_market")):
        record = (
            many("A", "UP", ["ROSE"] * n)
            + many("A", "DOWN", ["ROSE"] * 0)
            + many("B", "NOT_LONG", ["ROSE"] * 0)
            + many("C", "DOWN", ["FELL"] * 60)
        )
        res = mod.run({"record": record, "min_scored": minimum}, now=NOW)
        if res["data"]["by_reading"]["UP"]["verdict"] != word and n >= 10:
            out.append(f"{n} reads, minimum {minimum}: verdict {res['data']['by_reading']['UP']['verdict']}, expected {word}")
        if n < 10 and res["data"]["by_reading"]["UP"]["verdict"] != word and minimum > n:
            out.append(f"{n} reads, minimum {minimum}: verdict {res['data']['by_reading']['UP']['verdict']}, expected {word}")
    # hit rate is rounded to 4 places, not truncated
    rounded = mod.run({"record": many("A", "UP", ["ROSE"] * 2 + ["FELL"])}, now=NOW)["data"]["by_reading"]["UP"]
    if rounded["hit_rate"] != 0.6667 or rounded["interval_95"] != [0.2077, 0.9385]:
        out.append(f"2 of 3: {rounded['hit_rate']} {rounded['interval_95']}")
    # symbol filter leaves other symbols out but counts their malformed neighbours
    filt = mod.run({"record": [o("SOL", "UP", "ROSE"), o("ETH", "UP", "FELL"), o("SOLX", "UP", "ROSE")], "symbol": "SOL"}, now=NOW)
    if filt["data"]["reads"] != 1 or filt["request"] != {"symbol": "SOL", "min_scored": 10} or filt["data"]["symbol"] != "SOL":
        out.append(f"symbol filter: {filt['data']['reads']} reads, request {filt['request']}")
    # malformed items
    junk: list[Any] = [
        None, 5, "x", [], {}, {"symbol": "A"}, {"reading": "UP"}, {"symbol": "A", "reading": "SIDEWAYS", "direction": None},
        {"symbol": "A", "reading": ["UP"], "direction": None}, {"symbol": "A", "reading": "UP", "direction": "UP"},
        {"symbol": "A", "reading": "UP", "direction": ["ROSE"]}, {"symbol": 1, "reading": "UP"}, {"symbol": None, "reading": "UP"},
        {"symbol": "A", "reading": "UP", "direction": "rose"},
    ]  # fmt: skip
    res = mod.run({"record": [*junk, o("A", "UP", "ROSE")]}, now=NOW)
    want_warning = (
        f"{len(junk)} outcome(s) could not be read (a missing symbol, an unknown reading or an unknown direction) and are left out"
    )
    if res["data"]["reads"] != 1 or res["warnings"] != [want_warning] or res["status"] != "partial":
        out.append(f"{len(junk)} malformed items: reads {res['data']['reads']}, warnings {res['warnings']}")
    only_junk = mod.run({"record": junk}, now=NOW)
    if only_junk["status"] != "unavailable" or only_junk["availability"] != {"outcomes": "unavailable"} or len(only_junk["warnings"]) != 1:
        out.append(f"only malformed items: status {only_junk['status']}, availability {only_junk['availability']}")
    # items that read: a missing direction, each reading, each direction
    fine = [{"symbol": "A", "reading": r} for r in READINGS] + [
        {"symbol": "A", "reading": "UP", "direction": d} for d in ("ROSE", "FELL", "TIE", None)
    ]
    res = mod.run({"record": fine}, now=NOW)
    if res["data"]["reads"] != len(fine) or [w for w in res["warnings"] if "could not be read" in w]:
        out.append(f"well-formed items: {res['data']['reads']} read of {len(fine)}")
    # plans: closed list counted by word, anything else (also a missing or odd value) is OTHER
    plans = [
        o("A", "NONE", None, plan=p)
        for p in ("TARGET", "STOP", "TIMEOUT", "AMBIGUOUS", "ALREADY_PAST", "NOT_SETTLED", "WAITING", "NO_PLAN")
    ]
    plans += [
        o("A", "NONE", None, plan="target"),
        o("A", "NONE", None, plan=None),
        o("A", "NONE", None, plan=["TARGET"]),
        {"symbol": "A", "reading": "NONE"},
    ]
    res = mod.run({"record": plans}, now=NOW)
    counts = res["data"]["plans"]
    if list(counts) != ["TARGET", "STOP", "TIMEOUT", "AMBIGUOUS", "ALREADY_PAST", "NOT_SETTLED", "WAITING", "NO_PLAN", "OTHER"]:
        out.append(f"plan keys {list(counts)}")
    if any(counts[k] != 1 for k in list(counts)[:8]) or counts["OTHER"] != 4:
        out.append(f"plan counts {counts}")
    if res["warnings"] != ["4 plan word(s) are not in the closed list and are counted as OTHER"]:
        out.append(f"plan warning {res['warnings']}")
    # as_of
    if mod.run({"record": []}, now=dt.datetime(2030, 1, 2, 3, 4, 5, tzinfo=dt.UTC))["as_of"] != "2030-01-02T03:04:05Z":
        out.append("as_of is not the given time in the form YYYY-MM-DDTHH:MM:SSZ")
    before = dt.datetime.now(dt.UTC).replace(microsecond=0)
    stamp = mod.run({"record": []})["as_of"]
    after = dt.datetime.now(dt.UTC)
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp):
        out.append(f"as_of without a given time is {stamp!r}")
    else:
        made = dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)
        if not before <= made <= after:
            out.append(f"as_of without a given time is {stamp}, not the time of the call")
    return out


def check_rules_tool(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    got = mod.run_rules({}, now=NOW)
    want = {
        "schema_version": "1.0",
        "tool": "ryo_verdict_record_rules",
        "status": "ok",
        "data_mode": "unknown",
        "as_of": "2026-11-09T12:00:30Z",
        "request": {},
        "data": {"rules": RULES},
        "summary": {"headline": "ryo_verdict_record never counts an unscored read as a miss.", "key_points": RULES[:3]},
        "availability": {"rules": "available"},
        "warnings": [],
    }
    if got != want:
        out.append(f"run_rules differs in {[k for k in want if got.get(k) != want[k]]}")
    elif list(got) != list(want):
        out.append("run_rules answers with its keys in another order")
    for label, args in (("none", None), ("list", []), ("text", "x"), ("filled", {"a": 1}), ("record", {"record": []})):
        try:
            mod.run_rules(args, now=NOW)
        except InputError as exc:
            if exc.field != "arguments" or exc.detail != "this tool takes none" or exc.example != {}:
                out.append(f"run_rules({label}) refused as {exc.field!r} {exc.detail!r} {exc.example!r}")
        except Exception as exc:
            out.append(f"run_rules({label}) raised {type(exc).__name__}")
        else:
            out.append(f"run_rules({label}) was accepted")
    if mod.run_rules({}, extra=1)["status"] != "ok":
        out.append("run_rules does not ignore other keywords")
    stamp = mod.run_rules({})["as_of"]
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp):
        out.append(f"run_rules as_of without a given time is {stamp!r}")
    else:
        made = dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)
        if abs((dt.datetime.now(dt.UTC) - made).total_seconds()) > 5:
            out.append(f"run_rules as_of {stamp} is not the time of the call")
    return out


def failures(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    for check in (check_wilson, check_scoring_rule, check_refusals, check_answers, check_rules_of_record, check_rules_tool):
        try:
            out.extend(check(mod))
        except Exception as exc:
            out.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    return out
