"""Scenarios for whether a source improves the calls (``hoshitori.source_value``), runnable against the real module or a mutant of it.

The rules arm's re-run is planted (the module's ``rules_challenge``, ``challenge_view`` and ``source_ids`` are replaced for the scenario),
so every case is worked out by hand: which slots are scored, which are counted as required, not evaluated or not reproduced, and that the
scored pairs reach ``comparison.compare`` as the call with the source against the call without it. The statistics themselves are
``comparison``'s and tested there.
"""

from __future__ import annotations

import types
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from hoshitori import comparison, forecast

HOUR = 3_600_000
T0 = 1_794_225_600_000  # 2026-11-09T12:00:00Z


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


@dataclass(frozen=True)
class Item:
    source: str


@dataclass(frozen=True)
class Slot:
    candidate_id: str
    asset_id: str


@dataclass(frozen=True)
class Frozen:
    slots: tuple[Slot, ...]
    items: tuple[tuple[Item, str], ...]


@dataclass(frozen=True)
class Decision:
    p: Decimal | None


@dataclass(frozen=True)
class Result:
    original: Decision
    changed: Decision | None


def iso(ms: int) -> str:
    from hoshitori.timeline import from_ms
    from hoshitori.timeutil import format_rfc3339_ms

    return format_rfc3339_ms(from_ms(ms))


def world(
    cycles: int, plan: Callable[[str, str, int, str], Result], committed: str = "0.7", horizons: tuple[int, ...] = (1,)
) -> tuple[list[dict[str, Any]], Any, Any]:
    """``cycles`` hourly cycles, one candidate each, every 1-hour call of arm L0 settled to a rise; ``plan(cycle, asset, hours, source)``
    is the planted re-run. Returns the records and a stand-in protocol."""
    records: list[dict[str, Any]] = []
    for n in range(cycles):
        cycle, asset, at = f"cy{n:02d}", f"as{n:02d}", T0 + n * HOUR
        records.append({"type": "cycle_state", "body": {"cycle_id": cycle, "state": "PLANNED", "planned_for": iso(at)}})
        for hours in horizons:
            fid = forecast.forecast_id(cycle, "L0", asset, "D", hours * HOUR)
            records += [
                {"type": "forecast", "id": fid, "body": {"cycle_id": cycle, "arm_id": "L0", "contract": "D", "p": committed}},
                {"type": "forecast_state", "body": {"forecast_id": fid, "state": "OPEN", "origin": iso(at + 60_000)}},
                {"type": "settlement", "body": {"forecast_id": fid, "state": "SETTLED", "outcome": "1"}},
            ]
    records.append({"type": "forecast", "id": "fc-b", "body": {"cycle_id": "cy00", "arm_id": "L0", "contract": "B", "p": "x"}})
    protocol = types.SimpleNamespace(arms=(("L0", "h"),), horizons_hours=horizons)
    return records, protocol, plan


def run(
    mod: types.ModuleType, records: list[dict[str, Any]], protocol: Any, plan: Callable[[str, str, int, str], Result], model_runs: Any = ()
) -> Any:
    saved = (mod.rules_challenge, mod.challenge_view, mod.source_ids)
    frozen = {
        r["body"]["cycle_id"]: Frozen(
            (Slot("cd", f"as{r['body']['cycle_id'][2:]}"),), ((Item("A"), "d1"), (Item("B"), "d2"), (Item("A"), "d3"))
        )
        for r in records
        if r.get("type") == "cycle_state"
    }
    mod.challenge_view = types.SimpleNamespace(
        cycle_of=lambda recs, cycle_id: (None, None, frozen[cycle_id]),
        store_of=lambda recs: "store",
        ordered=lambda names: sorted(set(names)),
    )
    mod.source_ids = lambda snap, source: (snap, source)
    cycle_of = {id(snap): cycle for cycle, snap in frozen.items()}

    def challenge(snap: Any, store: Any, ids: Any, mapping: Any, candidate: str, hours: int) -> Result:
        cycle = cycle_of[id(snap)]
        return plan(cycle, snap.slots[0].asset_id, hours, ids[1])

    mod.rules_challenge = types.SimpleNamespace(challenge=challenge)
    try:
        return mod.build(records, protocol, {"L0": "mapping"}, model_runs)
    finally:
        mod.rules_challenge, mod.challenge_view, mod.source_ids = saved


def check_scored_and_counted(mod: types.ModuleType) -> list[str]:
    c = Case(mod)

    def plan(cycle: str, asset: str, hours: int, source: str) -> Result:
        n = int(cycle[2:])
        if source == "B":
            return Result(Decision(Decimal("0.7")), Decision(None))  # without B the rule makes no call: required
        if n == 12:
            return Result(Decision(Decimal("0.6")), Decision(Decimal("0.5")))  # the re-run is not the committed call: differs
        if n == 13:
            return Result(Decision(Decimal("0.7")), None)  # cannot be evaluated
        if n == 14:
            return Result(Decision(None), Decision(Decimal("0.5")))  # the rule made no call: not scored, not counted
        return Result(Decision(Decimal("0.7")), Decision(Decimal("0.5")))  # without A it says one half

    records, protocol, _ = world(15, plan)
    got = run(mod, records, protocol, plan)
    a, b = (next(line for line in got.lines if line.source == s) for s in ("A", "B"))
    c.eq(
        "A: twelve scored, one not reproduced, one not evaluated, none required",
        (a.shared, a.differs, a.not_evaluated, a.required),
        (12, 1, 1, 0),
    )
    c.eq("B: required everywhere it was asked, nothing scored", (b.shared, b.required, b.state), (0, 15, comparison.TOO_FEW))
    want = comparison.compare(
        "with",
        "without",
        1,
        {
            "with": [
                comparison.Call(f"cy{n:02d}", f"as{n:02d}", 12 + n, T0 + n * HOUR + 60_000 + HOUR, Decimal("0.7"), 1) for n in range(12)
            ],
            "without": [
                comparison.Call(f"cy{n:02d}", f"as{n:02d}", 12 + n, T0 + n * HOUR + 60_000 + HOUR, Decimal("0.5"), 1) for n in range(12)
            ],
        },
    )
    c.eq("the pairs are the call with the source against the call without it", (a.state, a.since), (want.state, want.since))
    c.eq("the mean difference, four places: 0.25 - 0.09", a.mean, "0.1600")
    c.eq("its interval as comparison gives it", (a.lower, a.upper), (f"{want.lower:.4f}", f"{want.upper:.4f}"))
    c.eq(
        "the method's constants",
        (got.alpha, got.v_opt, got.min_shown),
        (str(comparison.ALPHA), str(comparison.V_OPT), comparison.MIN_SHOWN),
    )
    c.eq("one line per source and horizon, in order", [(line.source, line.hours) for line in got.lines], [("A", 1), ("B", 1)])
    c.eq("each line names the agent whose calls were made again", {(line.arm, line.arm_name) for line in got.lines}, {("L0", "Rules")})
    model = mod.read_model_runs(runs_bytes([pair_doc(n) for n in range(3)]))
    with_model = run(mod, records, protocol, plan, model_runs=[model])
    c.eq(
        "recorded model runs add their lines after the rules agent's",
        [(x.arm, x.source) for x in with_model.lines][-1],
        ("L1", "hoshitori.features"),
    )
    records2, protocol2, _ = world(3, plan, horizons=(1, 4))
    both = run(mod, records2, protocol2, plan)
    c.eq("by source, then by horizon", [(line.source, line.hours) for line in both.lines], [("A", 1), ("A", 4), ("B", 1), ("B", 4)])
    try:
        both.alpha = "x"  # type: ignore[misc]
        c.bad.append("the document can be changed after it is made")
    except AttributeError:
        pass
    return c.bad


def check_what_is_not_read(mod: types.ModuleType) -> list[str]:
    c = Case(mod)

    def plan(cycle: str, asset: str, hours: int, source: str) -> Result:
        return Result(Decision(Decimal("0.7")), Decision(Decimal("0.5")))

    records, protocol, _ = world(3, plan)
    unsettled = [dict(r) for r in records]
    for r in unsettled:
        if r.get("type") == "settlement":
            r["body"] = {**r["body"], "state": "VOID"}
    c.eq("a slot that did not settle to a rise or a fall is never asked", run(mod, unsettled, protocol, plan).lines, ())
    no_origin = [r for r in records if r.get("type") != "forecast_state"]
    c.eq("nor one whose window never opened", run(mod, no_origin, protocol, plan).lines, ())
    other_arm = types.SimpleNamespace(arms=(("L9", "h"),), horizons_hours=(1,))
    c.eq("nor an arm with no mapping", run(mod, records, other_arm, plan).lines, ())
    c.eq(
        "a horizon of the protocol with no settled call is not asked",
        run(mod, records, types.SimpleNamespace(arms=(("L0", "h"),), horizons_hours=(4,)), plan).lines,
        (),
    )
    zero = [*records, {"type": "settlement", "body": {"forecast_id": "fc-nope", "state": "SETTLED", "outcome": "1"}}]
    c.eq("a settlement of a call that is not up-or-down is not read", len(run(mod, zero, protocol, plan).lines), 2)
    return c.bad


def pair_doc(
    n: int, *, hours: int = 1, with_p: str | None = "0.7", without_p: str | None = "0.5", failed: bool = False, hour: int | None = None
) -> dict[str, Any]:
    return {
        "cycle_id": f"cy{n:02d}",
        "asset_id": f"as{n:02d}",
        "symbol": "BTC",
        "hours": hours,
        "planned_hour": (12 + n) % 24 if hour is None else hour,
        "close_ms": T0 + n * HOUR + HOUR,
        "outcome": 1,
        "seed": n,
        "with_p": with_p,
        "without_p": without_p,
        "failed": failed,
    }


def runs_bytes(pairs: list[dict[str, Any]], **over: Any) -> bytes:
    import json

    doc = {"format": 1, "arm": "L1", "model": "m", "agent_version": "v", "source": "hoshitori.features", "pairs": pairs, **over}
    return json.dumps(doc).encode()


def check_model_runs(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    pairs = [pair_doc(n) for n in range(12)]
    pairs += [
        pair_doc(20, without_p=None),
        pair_doc(21, failed=True, with_p=None, without_p=None),
        pair_doc(22, with_p=None),
        pair_doc(23, hours=4, hour=16),
    ]
    runs = mod.read_model_runs(runs_bytes(pairs))
    one, four = mod.model_lines(runs)
    c.eq(
        "a line per horizon, for the arm and the source of the file",
        (one.arm, one.source, one.hours, four.hours),
        ("L1", "hoshitori.features", 1, 4),
    )
    c.eq(
        "twelve scored, one required, one not evaluated; a pair with no call on the full snapshot is left out",
        (one.shared, one.required, one.not_evaluated, one.differs),
        (12, 1, 1, 0),
    )
    want = comparison.compare(
        "with",
        "without",
        1,
        {
            "with": [comparison.Call(f"cy{n:02d}", f"as{n:02d}", 12 + n, T0 + n * HOUR + HOUR, Decimal("0.7"), 1) for n in range(12)],
            "without": [comparison.Call(f"cy{n:02d}", f"as{n:02d}", 12 + n, T0 + n * HOUR + HOUR, Decimal("0.5"), 1) for n in range(12)],
        },
    )
    c.eq(
        "the pairs reach the comparison as the call with the source against the call without it",
        (one.mean, one.state),
        ("0.1600", want.state),
    )
    c.eq("a horizon with one pair is too few", (four.shared, four.state), (1, comparison.TOO_FEW))
    first_four = mod.model_lines(mod.read_model_runs(runs_bytes([pair_doc(23, hours=4, hour=16), pair_doc(1)])))
    c.eq("horizons in order, whatever the order of the pairs", [line.hours for line in first_four], [1, 4])
    kept = mod.model_lines(mod.read_model_runs(runs_bytes([pair_doc(n, failed=True) for n in range(12)])))[0]
    c.eq("a failed pair is not scored even when it carries chances", (kept.shared, kept.not_evaluated), (0, 12))
    fell = mod.read_model_runs(runs_bytes([{**pair_doc(0), "outcome": 0}]))
    c.eq("an outcome of 0 is read", fell.pairs[0].outcome, 0)
    for value in (runs, runs.pairs[0]):
        try:
            value.__setattr__(next(iter(value.__dataclass_fields__)), None)
            c.bad.append(f"{type(value).__name__} can be changed after it is made")
        except AttributeError:
            pass
    c.eq(
        "the file's pairs are read as written",
        runs.pairs[0],
        mod.ModelPair("cy00", "as00", "BTC", 1, 12, T0 + 2 * HOUR - HOUR, 1, 0, "0.7", "0.5", False),
    )
    return c.bad


def check_model_file_is_strict(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    good = pair_doc(0)
    for label, data in (
        ("another format", runs_bytes([good], format=2)),
        ("no arm", runs_bytes([good], arm="")),
        ("a chance above one", runs_bytes([pair_doc(0, with_p="1.2")])),
        ("a chance as a number", runs_bytes([{**good, "with_p": 0.7}])),
        ("an outcome of two", runs_bytes([{**good, "outcome": 2}])),
        ("an outcome as true", runs_bytes([{**good, "outcome": True}])),
        ("a negative hour", runs_bytes([{**good, "planned_hour": -1}])),
        ("an hour past the day", runs_bytes([{**good, "planned_hour": 24}])),
        ("a count as true", runs_bytes([{**good, "seed": True}])),
        ("a flag as a word", runs_bytes([{**good, "failed": "no"}])),
        ("a missing member", runs_bytes([{k: v for k, v in good.items() if k != "seed"}])),
        ("not JSON", b"{"),
    ):
        try:
            mod.read_model_runs(data)
            c.bad.append(f"{label}: read")
        except mod.RunsError:
            pass
    try:
        mod.read_model_runs(runs_bytes([good], format=2))
    except mod.RunsError as exc:
        c.eq("another format says so", str(exc), "a model-run file of format 2, not 1")
    c.eq(
        "a chance of exactly 0 and 1 is read",
        mod.read_model_runs(runs_bytes([pair_doc(0, with_p="0", without_p="1")])).pairs[0].with_p,
        "0",
    )
    return c.bad


def check_against_the_ledger(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    runs = mod.read_model_runs(runs_bytes([pair_doc(0)]))
    fid = forecast.forecast_id("cy00", "L1", "as00", "D", HOUR)
    base = [
        {"type": "forecast", "id": fid, "body": {"agent_version_hash": "v"}},
        {"type": "settlement", "body": {"forecast_id": fid, "state": "SETTLED", "outcome": "1"}},
    ]
    c.eq("a pair the ledger bears out passes", mod.check_against(runs, base), None)
    for label, records, words in (
        ("no such call", base[1:], "no settled call of L1 in cy00 on BTC over 1 hours"),
        ("not settled", [base[0], {"type": "settlement", "body": {**base[1]["body"], "state": "VOID"}}], "no settled call of L1"),
        ("another version", [{"type": "forecast", "id": fid, "body": {"agent_version_hash": "w"}}, base[1]], "another version of L1"),
        ("another outcome", [base[0], {"type": "settlement", "body": {**base[1]["body"], "outcome": "0"}}], "is not the ledger's"),
    ):
        try:
            mod.check_against(runs, records)
            c.bad.append(f"{label}: accepted")
        except mod.RunsError as exc:
            if words not in str(exc):
                c.bad.append(f"{label}: said {exc}")
    return c.bad


def check_four(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("none", mod._four(None), None)
    c.eq("four places", mod._four(0.123456), "0.1235")
    c.eq("no negative zero", mod._four(-0.00001), "0.0000")
    c.eq("a negative", mod._four(-0.25), "-0.2500")
    try:
        mod.SourceLine("L0", "Rules", "A", 1, 0, None, None, None, "x", None, 0, 0, 0).shared = 1  # type: ignore[misc]
        c.bad.append("a line can be changed after it is made")
    except AttributeError:
        pass
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType], list[str]]] = {
    "scored_and_counted": check_scored_and_counted,
    "what_is_not_read": check_what_is_not_read,
    "model_runs": check_model_runs,
    "model_file_is_strict": check_model_file_is_strict,
    "against_the_ledger": check_against_the_ledger,
    "four": check_four,
}


def failures(mod: types.ModuleType, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        try:
            bad += [f"{name}: {message}" for message in scenario(mod)]
        except Exception as exc:  # a crash in the scenario is a failure of the module under test
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
    return bad
