"""Checks of the view models (``hoshitori.view_models``), runnable against a mutant: the views of a real league run on loopback (one cycle,
then an hour later its one-hour forecasts settled) are compared with what the raw records say, read a second way."""

from __future__ import annotations

import atexit
import copy
import dataclasses
import functools
import inspect
import itertools
import json
import math
import shutil
import tempfile
import types
from contextlib import ExitStack
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

from hoshitori import arm_run_record, forecast, labels, lifecycle, scheduler, settlement_record
from hoshitori.evidence import record_digest
from hoshitori.ids import natural_id
from hoshitori.settlement import Settlement, State
from hoshitori.timeline import to_ms
from tests.e2e_league import league
from tests.league_checks import barrier_map

D = Decimal


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


@functools.cache
def ledger() -> tuple[list[dict[str, Any]], Any, int]:
    """A league that has run a cycle and then an hour: its records, its protocol and the time of the last read; kept for the process."""
    stack = ExitStack()
    atexit.register(stack.close)
    directory = Path(tempfile.mkdtemp(prefix="view-models-"))
    stack.callback(shutil.rmtree, directory, ignore_errors=True)
    # at_minute: started at hh:01, the two settled 1-hour calls score the same (BTC 89% up and up, ETH 11% up and down), so leaving
    # one out of the score could not be told from keeping it (check_headline)
    lg = stack.enter_context(league(directory, barrier=barrier_map(), at_minute=30))
    scheduler.tick(lg.services, lg.config)
    lg.clock.jump(62 * 60)
    scheduler.tick(lg.services, lg.config)
    return scheduler.read_all(lg.services), lg.services.protocol, to_ms(lg.clock.now())


def views(mod: types.ModuleType, **kw: Any) -> Any:
    records, protocol, now = ledger()
    return mod.build(records, protocol, now_ms=now, **kw)


def raw(records: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [r for r in records if r["type"] == kind]


SOURCE = "urn:hoshitori:test"


@functools.cache
def extended() -> tuple[list[dict[str, Any]], Any, int, dict[str, str]]:
    """The ledger of ``ledger()`` with some open slots carried to the ends a loopback league does not reach: a barrier call that reached its
    target, one that reached its stop, one that timed out, one that touched both levels, a void and an unsettleable direction call."""
    records, protocol, now = ledger()
    histories = lifecycle.rebuild(raw(records, "forecast_state"))
    forecasts = {r["id"]: r for r in raw(records, "forecast")}
    live = [f for f, h in histories.items() if lifecycle.current_state(h) is lifecycle.State.OPEN and f in forecasts]
    barrier = [f for f in sorted(live) if forecasts[f]["body"]["contract"] == "B"]
    direct = [f for f in sorted(live) if forecasts[f]["body"]["contract"] == "D"]
    out = list(records)
    at = now
    ends: dict[str, str] = {}

    def settle(fid: str, state: State, outcome: str | None, reason: str | None, touch: int | None, flags: tuple[str, ...] = ()) -> str:
        body = forecasts[fid]["body"]
        sid = natural_id("st", {"forecast": fid, "pair": "A", "rule": "settle/1"})
        price = state is State.SETTLED and body["contract"] == "D"
        s = Settlement(
            sid,
            fid,
            body["contract"],
            "A",
            "settle/1",
            state,
            outcome,
            reason,
            "100.5" if outcome else None,
            "101" if price else None,
            touch,
            flags,
        )
        clean = {k: v for k, v in forecasts[fid].items() if k != "committed_at"}
        out.append(settlement_record.to_record(s, record_digest(clean), SOURCE, at))
        return sid

    def due(fid: str) -> int:
        origin = next(t.step.origin_ms for t in histories[fid] if t.step.origin_ms is not None)
        return origin + forecasts[fid]["body"]["horizon_ms"] + 1000

    def go(fid: str, state: lifecycle.State, word: str, **kw: Any) -> None:
        seq = len(histories[fid])
        for i, step in enumerate((lifecycle.Step(lifecycle.State.SETTLING, due(fid)), lifecycle.Step(state, due(fid) + 1000, **kw))):
            out.append(lifecycle.to_record(lifecycle.Transition(fid, seq + 1 + i, step), SOURCE, step.at_ms))
        ends[fid] = word

    touch = now - 600_000
    go(barrier[0], lifecycle.State.SETTLED, "TARGET", settlement_id=settle(barrier[0], State.SETTLED, "TARGET", None, touch))
    go(barrier[1], lifecycle.State.SETTLED, "STOP", settlement_id=settle(barrier[1], State.SETTLED, "STOP", None, touch, ("BASIS_STRESS",)))
    go(barrier[2], lifecycle.State.SETTLED, "TIMEOUT", settlement_id=settle(barrier[2], State.SETTLED, "TIMEOUT", None, None))
    go(
        barrier[3],
        lifecycle.State.AMBIGUOUS,
        "AMBIGUOUS",
        settlement_id=settle(barrier[3], State.AMBIGUOUS, "AMBIGUOUS", "SAME_CANDLE", touch),
    )
    go(direct[0], lifecycle.State.VOID, "VOID", reason="VOID_TIE", settlement_id=settle(direct[0], State.VOID, None, "VOID_TIE", None))
    go(
        direct[1],
        lifecycle.State.UNSETTLEABLE,
        "UNSETTLEABLE",
        reason="UNSETTLEABLE_DISAGREE",
        settlement_id=settle(direct[1], State.UNSETTLEABLE, None, "UNSETTLEABLE_DISAGREE", None),
    )
    return out, protocol, max(due(f) for f in ends) + 60_000, ends


def extended_views(mod: types.ModuleType) -> Any:
    records, protocol, now, _ = extended()
    return mod.build(records, protocol, now_ms=now)


def check_endings(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    _, _, _, ends = extended()
    v = extended_views(mod)
    rows = {r.forecast_id: r for r in v.rows}
    want = {"TARGET": ("Settled", "Target first"), "STOP": ("Settled", "Stop first"), "TIMEOUT": ("Settled", "Neither")}
    for fid, word in ends.items():
        row = rows[fid]
        if word in want:
            c.eq(f"{word}: a barrier call that ended so", (row.state, row.result, row.reason), (*want[word], None))
        elif word == "AMBIGUOUS":
            c.eq("both levels in one minute", (row.state, row.result, row.reason), ("Not settled", None, None))
        elif word == "VOID":
            c.eq("a void", (row.state, row.result, row.reason, row.reason_code), ("Void", None, "The price did not change", "VOID_TIE"))
        else:
            c.eq(
                "an unsettleable call",
                (row.state, row.result, row.reason, row.reason_code),
                ("Not settled", None, "The two exchanges disagreed", "UNSETTLEABLE_DISAGREE"),
            )
    base = {a.arm + a.contract: a for a in views(mod).record}
    after = {a.arm + a.contract: a for a in v.record}
    c.eq(
        "the barrier record counts them",
        (after["L0B"].settled - base["L0B"].settled, after["L0B"].not_settled, after["L0B"].open - base["L0B"].open),
        (3, 1, -4),
    )
    c.eq(
        "the direction record counts a void and an unsettleable call",
        (after["L0D"].not_settled - base["L0D"].not_settled, after["L0D"].open - base["L0D"].open),
        (2, -2),
    )
    for arm_key, reference in (
        ("L0B", D(2) / D(3)),
        ("L0D", D("0.25")),
    ):  # a fixed reference: three equal parts for a barrier, a coin for a rise
        row = after[arm_key]
        if row.mean_brier is not None:
            want = format((1 - D(row.mean_brier) / reference).quantize(D("0.0001"), rounding=ROUND_HALF_EVEN), "f")
            # the printed mean is rounded to four places, so the skill read from it may differ from the exact one in the last place
            c.eq(f"{arm_key}: the skill is one minus the score over the reference", abs(D(row.skill) - D(want)) <= D("0.0003"), True)
    c.eq(
        "a barrier call carries a score and a skill",
        (after["L0B"].mean_brier is not None, after["L0B"].brier_n, after["L0B"].skill is not None),
        (True, after["L0B"].settled, True),
    )
    receipt = next(r for r in v.receipts if r.row.forecast_id == next(f for f, w in ends.items() if w == "STOP"))
    c.eq(
        "a flag on a settlement is kept",
        (receipt.settlement.flags, receipt.settlement.state, receipt.settlement.outcome),
        (("BASIS_STRESS",), "SETTLED", "STOP"),
    )
    return c.bad


@functools.cache
def model_ledger() -> tuple[list[dict[str, Any]], Any, int]:
    """One cycle with the rules arm and the three model arms on a scripted model: the one-hour single judgment answers after its cutoff,
    and the single judgment of four hours gets no answer at all."""
    from hoshitori import arm_l1, league
    from tests import league_models_checks as m

    directory = Path(tempfile.mkdtemp(prefix="view-models-arms-"))
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    w = m.world(directory, watchlist=("BTC",))
    model = m.Model(w)
    model.late[(arm_l1.STAGE, 1)] = 241
    model.gone.add((arm_l1.STAGE, 4))
    w.run(league, models=m.models(model))
    records = scheduler.read_ledger(w.ledger)
    w.close()
    return records, w.protocol, m.NOW_MS + 3_000_000


def check_model_arms(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    records, protocol, now = model_ledger()
    v = mod.build(records, protocol, now_ms=now)
    rows = {(r.arm, r.hours): r for r in v.rows}
    c.eq(
        "the names of the arms",
        {r.arm: r.arm_name for r in v.rows},
        {"L0": "Rules", "L1": "Single model", "L2": "Debate", "L1x": "Matched control"},
    )
    late = rows[("L1", 1)]
    c.eq(
        "a late single judgment",
        (late.state, late.chance, late.reason, late.reason_code, late.trust),
        ("Too late", None, "The answer came after the deadline", "FINISHED_AFTER_CUTOFF", "No forecast recorded"),
    )
    gone = rows[("L1", 4)]
    c.eq(
        "a single judgment that got no answer",
        (gone.state, gone.reason, gone.reason_code),
        ("Failed", "The model did not answer in time", "MODEL_UNAVAILABLE"),
    )
    called = rows[("L1", 24)]
    c.eq("a single judgment that called", (called.state, called.chance, called.contract), ("Open", "62% up", "D"))
    c.eq("the debate and the control called at four hours", (rows[("L2", 4)].chance, rows[("L1x", 4)].state), ("62% up", "Failed"))
    runs = {
        (b["forecast_id"]): b
        for b in (arm_run_record.from_record({k: x for k, x in r.items() if k != "committed_at"}) for r in raw(records, "arm_run"))
    }
    c.eq("a run for every slot of a model arm", sorted(runs), sorted(r.forecast_id for r in v.rows if r.arm != "L0"))
    for r in v.receipts:
        if r.row.arm == "L0":
            c.eq("the rules arm has no run", r.run, None)
            continue
        b = runs[r.row.forecast_id]
        c.eq(
            f"{r.row.arm} {r.row.hours}h: the run line",
            (r.run.verdict, r.run.reason, r.run.gate, r.run.calls, r.run.tokens, r.run.cutoff_s),
            (b["verdict"], mod.reason_words(b["reason"]), b["gate"], b["calls"], b["tokens"], b["cutoff_ms"] // 1000),
        )
        c.eq(
            f"{r.row.arm} {r.row.hours}h: its chances",
            (r.run.proposed_chance, r.run.final_chance),
            (mod.percent(D(b["proposed_p"])), mod.percent(D(b["final_p"]))),
        )
        debate = b.get("debate")
        said = None if debate is None or debate["collapsed"] or debate["failed_role"] is not None else debate["disagreement"]
        got = r.run.challenge
        if said is None:
            c.eq(f"{r.row.arm} {r.row.hours}h: no challenge without a debate that reached the judge", got, None)
        else:
            c.eq(
                f"{r.row.arm} {r.row.hours}h: the Bear's challenge is the disagreement the run recorded",
                (
                    [e.evidence_id for e in got.bear_only],
                    list(got.judge_cited),
                    got.shared,
                    got.bull_only,
                ),
                (said["bear_only"], said["judge_on_bear"], len(said["shared"]), len(said["bull_only"])),
            )
    c.eq("an agent challenge is a frozen record", mod.AgentChallenge.__dataclass_params__.frozen, True)
    for change, label in (
        ({"collapsed": True}, "a debate that collapsed"),
        ({"failed_role": "bull", "disagreement": None}, "a debate in which a role failed"),
    ):
        altered = copy.deepcopy(records)
        for r in altered:
            if r["type"] == "arm_run" and r["body"]["arm"] == "L2" and r["body"].get("debate") is not None:
                r["body"]["debate"] = {**r["body"]["debate"], **change}
        w = mod.build(altered, protocol, now_ms=now)
        c.eq(
            f"{label} has no challenge",
            [x.run.challenge for x in w.receipts if x.row.arm == "L2"],
            [None for x in w.receipts if x.row.arm == "L2"],
        )
    c.eq(
        "a call under a model arm has a receipt, a late one does not",
        (any(r.row.forecast_id == late.forecast_id for r in v.receipts), any(r.row.forecast_id == called.forecast_id for r in v.receipts)),
        (False, True),
    )
    arms = {(a.arm, a.contract): a for a in v.record}
    c.eq(
        "the late and failed slots are in the record",
        (arms[("L1", "D")].failed, arms[("L1", "D")].calls, arms[("L1", "D")].scheduled),
        (2, 1, 3),
    )
    c.eq("availability counts a late slot as a failure", arms[("L1", "D")].availability, "33%")
    c.eq(
        "a model call with no settlement has no score yet",
        (arms[("L1", "D")].mean_brier, arms[("L1", "D")].label),
        (None, "No settled calls yet"),
    )
    return c.bad


def check_shapes_and_edges(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    records, protocol, now = ledger()
    for cls in (
        mod.Row,
        mod.EvidenceLine,
        mod.TimelineLine,
        mod.SettlementLine,
        mod.RunLine,
        mod.Receipt,
        mod.ArmRecord,
        mod.CycleSummary,
        mod.Today,
        mod.Views,
        mod._Slot,
    ):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    c.eq("the default number of settled rows shown lately", inspect.signature(mod.build).parameters["recent"].default, 8)
    c.eq("scored is off by default", inspect.signature(mod.build).parameters["scored"].default, False)
    v = mod.build(records, protocol, now_ms=now)
    # evidence the ledger does not hold is named as missing and never invented
    gone = next(r["id"] for r in raw(records, "forecast"))
    first = next(e for e in next(r for r in raw(records, "forecast") if r["id"] == gone)["body"]["evidence_ids"])
    cut = [r for r in records if not (r["type"] == "evidence" and r["id"] == first)]
    line = next(e for rc in mod.build(cut, protocol, now_ms=now).receipts for e in rc.evidence if e.evidence_id == first)
    c.eq(
        "evidence that is not in the ledger",
        (line.source, line.path, line.kind, line.event_time, line.data_mode, line.origin_family, line.raw_sha256, line.shown),
        ("unknown",) * 3 + (None,) + ("unknown",) * 2 + (None, "not in this ledger"),
    )
    # public evidence may be shown; the rest is by digest only
    opened = [{**r, "body": {**r["body"], "redistribution": "PUBLIC"}} if r["id"] == first else r for r in records]
    shown = {e.evidence_id: e.shown for rc in mod.build(opened, protocol, now_ms=now).receipts for e in rc.evidence}
    c.eq("public evidence is shown", (shown[first], {x for k, x in shown.items() if k != first}), ("shown", {"digest only"}))
    # order: cycles by the time they were planned, whatever order their records came in; receipts by id; arms by name
    second = v.cycles[-1].cycle_id
    moved = [r for r in records if r["type"] == "cycle_state" and r["body"]["cycle_id"] == second] + [
        r for r in records if not (r["type"] == "cycle_state" and r["body"]["cycle_id"] == second)
    ]
    w = mod.build(moved, protocol, now_ms=now)
    c.eq("cycles in the order planned", [x.cycle_id for x in w.cycles], [x.cycle_id for x in v.cycles])
    c.eq("and the rows follow them", [r.cycle_id for r in w.rows], [r.cycle_id for r in v.rows])
    c.eq("receipts in the order of their ids", [r.row.forecast_id for r in v.receipts], sorted(r.row.forecast_id for r in v.receipts))
    for cyc in v.cycles:
        mine = [r for r in v.rows if r.cycle_id == cyc.cycle_id]
        c.eq(
            f"{cyc.cycle_id}: the counts are those of its own rows",
            (cyc.slots, sum(n for _, n in cyc.rows_by_state)),
            (len(mine), len(mine)),
        )
        c.eq(
            f"{cyc.cycle_id}: by state",
            dict(cyc.rows_by_state),
            {st: sum(1 for r in mine if r.state == st) for st in {r.state for r in mine}},
        )
    mr, mp, mn = model_ledger()
    # the slots of the arms come in an order of the ledger's own (here: the last id first); the record is by arm and contract all the same
    steps: dict[str, list[dict[str, Any]]] = {}
    for r in mr:
        if r["type"] == "forecast_state":
            steps.setdefault(r["body"]["forecast_id"], []).append(r)
    shuffled = [r for r in mr if r["type"] != "forecast_state"] + [r for fid in sorted(steps, key=lambda f: f[::-1]) for r in steps[fid]]
    seen = list(dict.fromkeys((u.arm, u.contract) for u in _slots_of(mod, mr, mp, shuffled)))
    c.eq("the order the arms first appear in is not already sorted", seen != sorted(seen), True)
    c.eq(
        "the record in the order of arm and contract",
        [(a.arm, a.contract) for a in mod.build(shuffled, mp, now_ms=mn).record],
        [("L0", "D"), ("L1", "D"), ("L1x", "D"), ("L2", "D")],
    )
    # a ledger that does not hold together is refused with a reason, not shown
    c.eq("a manifest whose cycle has no history", _refused_with(mod, [r for r in records if r["type"] != "cycle_state"], protocol), "cycle")
    c.eq(
        "a call whose slot has no history", _refused_with(mod, [r for r in records if r["type"] != "forecast_state"], protocol), "forecast"
    )
    return c.bad


def _slots_of(mod: types.ModuleType, records: list[dict[str, Any]], protocol: Any, ordered: list[dict[str, Any]]) -> list[Any]:
    """The (arm, contract) of each slot in the order the histories of ``ordered`` first mention it."""
    slots = mod._slots(records, protocol)
    first = [r["body"]["forecast_id"] for r in ordered if r["type"] == "forecast_state" and r["body"]["seq"] == 1]
    return [slots[f] for f in first]


def _refused_with(mod: types.ModuleType, records: list[dict[str, Any]], protocol: Any) -> str:
    try:
        mod.build(records, protocol, now_ms=0)
    except Exception as exc:
        return str(exc).split()[0] if isinstance(exc, mod.ViewError) else f"{type(exc).__name__}: {exc}"[:80]
    return "accepted"


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("modes", (mod.MODE_EXPLORATORY, mod.MODE_SCORED), ("Exploratory run: not a scored period", "Scored period"))
    c.eq("places of a score", mod.PLACES, D("0.0001"))
    c.eq("every step of a slot has a word", sorted(labels.STEP_WORDS), sorted(s.value for s in lifecycle.State))
    c.eq(
        "every state of a slot but a level change is a row state",
        [s.value for s in lifecycle.State if s.value != "LEVEL" and mod.row_state(s) not in labels.ROW_STATES],
        [],
    )
    c.eq(
        "every row state is used",
        sorted({mod.row_state(s) for s in lifecycle.State if s is not lifecycle.State.LEVEL}),
        sorted(labels.ROW_STATES),
    )
    return c.bad


def check_words(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for p, want in (
        ("0.485925", "49%"),
        ("0.5", "50%"),
        ("0.505", "50%"),
        ("0.515", "52%"),
        ("0.004", "1%"),
        ("0.996", "99%"),
        ("0.5249", "52%"),
    ):
        c.eq(f"{p} as a percentage", mod.percent(D(p)), want)
    c.eq("a certain probability is not rounded away", (mod.percent(D(0)), mod.percent(D(1))), ("0%", "100%"))
    c.eq("a reason in words", mod.reason_words("MODEL_UNAVAILABLE"), "The model did not answer in time")
    c.eq("a reason nobody worded", mod.reason_words("NEW_REASON"), "Other reason")
    c.eq("no reason", mod.reason_words(None), None)
    try:
        mod.row_state(lifecycle.State.LEVEL)
    except mod.ViewError as exc:
        c.eq("a level change is not a state of a slot", str(exc), "LEVEL is not a state of a slot")
    else:
        c.bad.append("a level change was given a row state")
    c.eq("a label for none", mod._label(0), "No settled calls yet")
    c.eq("a label for one", mod._label(1), "Provisional: 1 settled call; nothing is ranked")
    c.eq("a label for two", mod._label(2), "Provisional: 2 settled calls; nothing is ranked")
    return c.bad


def check_json(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    v = views(mod)
    out = mod.to_json(v)
    c.eq("a view is plain JSON", json.loads(json.dumps(out)), out)
    c.eq("tuples become lists and dataclasses objects", (type(out["rows"]).__name__, type(out["rows"][0]).__name__), ("list", "dict"))
    c.eq("a leaf stays", mod.to_json("x"), "x")
    c.eq("none stays", mod.to_json(None), None)
    return c.bad


def check_rows(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    records, protocol, _ = ledger()
    v = views(mod)
    last: dict[str, str] = {}
    for r in raw(records, "forecast_state"):
        last[r["body"]["forecast_id"]] = r["body"]["state"]
    c.eq("one row for every slot of the ledger", sorted(r.forecast_id for r in v.rows), sorted(last))
    c.eq(
        "a row's state is its slot's last state, in words",
        {r.forecast_id: r.state for r in v.rows},
        {f: mod.row_state(lifecycle.State(s)) for f, s in last.items()},
    )
    forecasts = {r["id"]: r["body"] for r in raw(records, "forecast")}
    settled = {r["body"]["forecast_id"]: r["body"] for r in raw(records, "settlement")}
    c.eq("a call has a chance and an abstention has none", {r.forecast_id for r in v.rows if r.chance is not None}, set(forecasts))
    for r in v.rows:
        body = forecasts.get(r.forecast_id)
        if body is None:
            continue
        c.eq(
            f"{r.forecast_id}: the arm, the symbol and the horizon",
            (r.arm, r.symbol, r.hours, r.contract),
            (body["arm_id"], body["symbol"], body["horizon_ms"] // 3_600_000, body["contract"]),
        )
        if body["contract"] == "D":
            c.eq(f"{r.forecast_id}: the chance", r.chance, f"{mod.percent(D(body['p']))} up")
        else:
            c.eq(
                f"{r.forecast_id}: the chances of a barrier call",
                r.chance,
                split_second_way((D(body["p_target"]), D(body["p_stop"]), D(body["p_timeout"]))),
            )
        s = settled.get(r.forecast_id)
        if s is not None and s["state"] == "SETTLED":
            want = (
                ("Rose" if s["outcome"] == "1" else "Fell")
                if r.contract == "D"
                else {"TARGET": "Target first", "STOP": "Stop first", "TIMEOUT": "Neither"}[s["outcome"]]
            )
            c.eq(f"{r.forecast_id}: the result", r.result, want)
        else:
            c.eq(f"{r.forecast_id}: no result before a settlement", r.result, None)
        c.eq(f"{r.forecast_id}: the trust phrase follows the level", r.trust, labels.trust_phrase(r.level))
    c.eq("the arm names", {r.arm: r.arm_name for r in v.rows}, {"L0": "Rules"})
    c.eq("some rows settled and some wait", sorted({r.state for r in v.rows if r.chance is not None}), ["Open", "Settled"])
    c.eq("a settled row has the origin it started from", all(r.origin is not None for r in v.rows if r.state == "Settled"), True)
    histories = lifecycle.rebuild(raw(records, "forecast_state"))
    started = {r.forecast_id: any(t.step.origin_ms for t in histories[r.forecast_id]) for r in v.rows}
    c.eq("a row has an origin exactly when its history has one", {r.forecast_id: r.origin is not None for r in v.rows}, started)
    abstained = [r for r in v.rows if r.state == "Abstained"]
    c.eq(
        "an abstention has a reason in words and the code", all(r.reason is not None and r.reason_code is not None for r in abstained), True
    )
    c.eq("a call has no reason", all(r.reason is None and r.reason_code is None for r in v.rows if r.state in ("Open", "Settled")), True)
    per_cycle = [[r.hours for r in v.rows if r.cycle_id == cyc.cycle_id] for cyc in v.cycles]
    c.eq(
        "rows are in cycle order, then by horizon",
        ([r.cycle_id for r in v.rows], per_cycle),
        (sorted((r.cycle_id for r in v.rows), key=[c_.cycle_id for c_ in v.cycles].index), [sorted(h) for h in per_cycle]),
    )
    c.eq(
        "a protocol with no cycle for a slot is refused", _refused(mod, [r for r in records if r["type"] != "manifest"], protocol), "slot "
    )
    return c.bad


def _refused(mod: types.ModuleType, records: list[dict[str, Any]], protocol: Any) -> str:
    try:
        mod.build(records, protocol, now_ms=0)
    except mod.ViewError as exc:
        return str(exc)[:5]
    return "accepted"


def split_second_way(parts: tuple[Decimal, ...]) -> str:
    """The chances of a barrier call as a row shows them, found a second way, by trying every way of rounding each one down or up: of the
    ways that add up to 100, keep those that leave each share within one point of its exact value and never show 0% or 100% for a chance
    that is neither; of those, take the one that rounds up the largest remainders, the earlier chances first on a tie. When no way is
    left, each chance is rounded on its own, half to even, and the row says that they are rounded."""
    exact = [Fraction(p) * 100 for p in parts]
    best: tuple[Fraction, tuple[int, ...], list[int]] | None = None
    for ups in itertools.product((0, 1), repeat=len(exact)):
        shown = [math.floor(x) + u for x, u in zip(exact, ups, strict=True)]
        if sum(shown) != 100 or any(abs(s - x) >= 1 for s, x in zip(shown, exact, strict=True)):
            continue
        if any((s == 0 and x > 0) or (s == 100 and x < 100) for s, x in zip(shown, exact, strict=True)):
            continue
        gain = sum((x - math.floor(x) for x, u in zip(exact, ups, strict=True) if u), Fraction(0))
        if best is None or (gain, ups) > (best[0], best[1]):
            best = (gain, ups, shown)
    if best is None:
        alone = [min(99, max(1, round(x))) if 0 < x < 100 else round(x) for x in exact]
        return f"target first {alone[0]}%, stop first {alone[1]}%, neither {alone[2]}% (rounded)"
    return f"target first {best[2][0]}%, stop first {best[2][1]}%, neither {best[2][2]}%"


# the three chances of the one-, four- and twenty-four-hour barrier calls of the fresh quickstart run (rows.json showed the first as
# "target first 4%, stop first 6%, neither 91%": 101), each with the row it must give
FRESH_BARRIER: tuple[tuple[tuple[str, str, str], str], ...] = (
    (("0.035820", "0.058154", "0.906026"), "target first 3%, stop first 6%, neither 91%"),
    (("0.246622", "0.261824", "0.491554"), "target first 25%, stop first 26%, neither 49%"),
    (("0.578431", "0.362745", "0.058824"), "target first 58%, stop first 36%, neither 6%"),
)
EDGE_BARRIER: tuple[tuple[tuple[str, str, str], str], ...] = (
    (("0.335", "0.335", "0.33"), "target first 34%, stop first 33%, neither 33%"),  # a tie goes to the earlier chance
    (("0.5", "0.495", "0.005"), "target first 50%, stop first 49%, neither 1%"),  # under a point, never 0%
    (("0.003", "0.507", "0.49"), "target first 1%, stop first 50%, neither 49%"),  # under a point first, before a larger remainder
    (("0.012", "0.498", "0.49"), "target first 1%, stop first 50%, neither 49%"),  # just over a point takes its turn by remainder
    (("0.005", "0", "0.995"), "target first 1%, stop first 0%, neither 99%"),  # never 100% for a chance under one
    (("0.6", "0.4", "0"), "target first 60%, stop first 40%, neither 0%"),  # a chance of none is 0%
    (("1", "0", "0"), "target first 100%, stop first 0%, neither 0%"),
    (("0.004", "0.006", "0.99"), "target first 1%, stop first 1%, neither 99% (rounded)"),  # no split adds up to 100 that way
)
GRID: tuple[str, ...] = (
    "0", "0.001", "0.003", "0.004", "0.005", "0.006", "0.0099", "0.01", "0.012", "0.015", "0.035820", "0.058154", "0.125", "0.246622",
    "0.25", "0.261824", "0.333333", "0.335", "0.362745", "0.45", "0.495", "0.498", "0.499", "0.5", "0.507", "0.578431", "0.6", "0.75",
    "0.98", "0.99", "0.995", "1",
)  # fmt: skip


def check_barrier_split(mod: types.ModuleType) -> list[str]:
    """A barrier row's three shares add up to 100 (the largest-remainder method), each within one point of its exact value."""
    c = Case(mod)
    records, _, _ = ledger()
    record = next(r for r in raw(records, "forecast") if r["body"]["contract"] == "B")
    base = forecast.from_record({k: x for k, x in record.items() if k != "committed_at"})

    def row(parts: tuple[str, str, str]) -> str:
        p = tuple(D(x) for x in parts)
        return str(mod._chance(dataclasses.replace(base, p_target=p[0], p_stop=p[1], p_timeout=p[2])))

    for parts, want in FRESH_BARRIER + EDGE_BARRIER:
        c.eq(f"the barrier chances {'/'.join(parts)}", row(parts), want)
        c.eq(f"the barrier chances {'/'.join(parts)}, found a second way", split_second_way(tuple(D(x) for x in parts)), want)
    for t, s in itertools.product(GRID, repeat=2):
        if D(t) + D(s) <= 1:
            parts = (t, s, str(1 - D(t) - D(s)))
            c.eq(f"the barrier chances {'/'.join(parts)}", row(parts), split_second_way(tuple(D(x) for x in parts)))
    return c.bad


def check_receipts(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    records, _, _ = ledger()
    v = views(mod)
    forecasts = {r["id"]: r for r in raw(records, "forecast")}
    c.eq("a receipt for every call and no other", sorted(r.row.forecast_id for r in v.receipts), sorted(forecasts))
    evidence = {r["id"]: r["body"] for r in raw(records, "evidence")}
    for r in v.receipts:
        record = forecasts[r.row.forecast_id]
        body = record["body"]
        clean = {k: x for k, x in record.items() if k != "committed_at"}
        c.eq(f"{r.row.forecast_id}: the digest of the forecast record", r.forecast_digest, record_digest(clean))
        c.eq(f"{r.row.forecast_id}: what would prove it wrong", list(r.invalidations), body["invalidation"])
        c.eq(f"{r.row.forecast_id}: the evidence it read, in order", [e.evidence_id for e in r.evidence], body["evidence_ids"])
        c.eq(f"{r.row.forecast_id}: evidence is shown by digest only", {e.shown for e in r.evidence}, {"digest only"})
        for e in r.evidence:
            src = evidence[e.evidence_id]
            c.eq(
                f"{e.evidence_id}: its line",
                (e.source, e.path, e.kind, e.event_time, e.data_mode, e.origin_family, e.raw_sha256),
                (src["source"], src["path"], src["kind"], src["event_time"], src["data_mode"], src["origin_family"], src["raw_sha256"]),
            )
        c.eq(f"{r.row.forecast_id}: the oracles", r.oracles, f"{body['oracle_primary']} and {body['oracle_second']}")
        c.eq(f"{r.row.forecast_id}: as of", r.as_of, body["as_of"])
        c.eq(f"{r.row.forecast_id}: committed", r.committed_at is not None and r.committed_at.endswith("Z"), True)
        c.eq(f"{r.row.forecast_id}: no model run for the rules arm", r.run, None)
        steps = [t.state for t in r.timeline]
        c.eq(
            f"{r.row.forecast_id}: the history starts scheduled and is in order",
            (steps[0], [t.at for t in r.timeline] == sorted(t.at for t in r.timeline)),
            ("Scheduled", True),
        )
        if r.row.state == "Settled":
            c.eq(
                f"{r.row.forecast_id}: a settled call ends settled, with its settlement",
                (steps[-1], r.settlement is not None and r.settlement.state),
                ("Settled", "SETTLED"),
            )
        else:
            c.eq(f"{r.row.forecast_id}: an open call has no settlement", r.settlement, None)
    settled = next(r for r in v.receipts if r.settlement is not None)
    raw_s = next(x["body"] for x in raw(records, "settlement") if x["body"]["forecast_id"] == settled.row.forecast_id)
    c.eq(
        "a settlement line",
        (
            settled.settlement.outcome,
            settled.settlement.reference_price,
            settled.settlement.outcome_price,
            settled.settlement.rule_version,
            settled.settlement.flags,
        ),
        (raw_s["outcome"], raw_s["p0"], raw_s["outcome_price"], raw_s["rule_version"], tuple(raw_s["flags"])),
    )
    return c.bad


def check_unread(mod: types.ModuleType) -> list[str]:
    """What a call's evidence could not read is on its receipt: the snapshot of the cycle names, for each candidate, the sources that were asked
    and brought nothing, and the receipt of a call about that asset says so in words; the receipt of a call about another asset does not."""
    import copy

    c = Case(mod)
    records, protocol, now = ledger()
    v0 = views(mod)
    c.eq("a league that read everything has nothing unread on any receipt", [r.unread for r in v0.receipts if r.unread], [])
    patched = copy.deepcopy(records)
    snapshot = next(r for r in patched if r["type"] == "snapshot")
    first = snapshot["body"]["candidates"][0]
    first["unavailable"] = [
        {"source": "kraken", "reason": "CALL_FAILED", "interval_s": 3600},
        {"source": "binance-funding", "reason": "NO_PERPETUAL", "interval_s": None},
        {"source": "ryo", "reason": "TIMED_OUT", "interval_s": None},
    ]
    v = mod.build(patched, protocol, now_ms=now)
    asset = first["asset_id"]
    slots = {r.row.forecast_id: r.row for r in v.receipts}
    mine = [
        r
        for r in v.receipts
        if r.row.cycle_id == snapshot["body"]["cycle_id"] and slots[r.row.forecast_id].symbol == _symbol_of(v0, records, asset)
    ]
    others = [r for r in v.receipts if r not in mine]
    c.eq("there are receipts of the asset", len(mine) > 0, True)
    c.eq(
        "each receipt of a call about it says what was asked and not read, in the order the snapshot gives",
        {tuple((u.source, u.reason_code, u.reason, u.interval_s) for u in r.unread) for r in mine},
        {
            (
                ("kraken", "CALL_FAILED", "no answer came", 3600),
                ("binance-funding", "NO_PERPETUAL", "the token has no perpetual to read funding from", None),
                ("ryo", "TIMED_OUT", "no answer came in time", None),
            )
        },
    )
    c.eq("the receipts of the other asset say nothing", [r.unread for r in others if r.unread], [])
    c.eq("the line is frozen", mod.UnreadLine.__dataclass_params__.frozen, True)
    smallest = copy.deepcopy(patched)
    next(r for r in smallest if r["type"] == "snapshot")["body"]["candidates"][0]["unavailable"] = [
        {"source": "kraken", "reason": "TIMED_OUT", "interval_s": 1}
    ]
    shortest = mod.build(smallest, protocol, now_ms=now)
    c.eq(
        "a window of one second is a window",
        {tuple((u.source, u.interval_s) for u in r.unread) for r in shortest.receipts if r.unread},
        {(("kraken", 1),)},
    )
    c.eq("and the rest of the receipt is as it was", [dataclasses.replace(r, unread=()) for r in v.receipts], list(v0.receipts))
    for label, bad in (
        ("a reason that is not a collector's", {"source": "kraken", "reason": "SAD", "interval_s": None}),
        ("a member too many", {"source": "kraken", "reason": "CALL_FAILED", "interval_s": None, "note": "x"}),
        ("a source that is not text", {"source": 5, "reason": "CALL_FAILED", "interval_s": None}),
        ("an interval that is a boolean", {"source": "kraken", "reason": "CALL_FAILED", "interval_s": True}),
        ("an interval of nothing", {"source": "kraken", "reason": "CALL_FAILED", "interval_s": 0}),
        ("an interval below nothing", {"source": "kraken", "reason": "CALL_FAILED", "interval_s": -60}),
        ("an interval that is text", {"source": "kraken", "reason": "CALL_FAILED", "interval_s": "3600"}),
        ("a source that is empty", {"source": "", "reason": "CALL_FAILED", "interval_s": None}),
        ("a member missing", {"source": "kraken", "reason": "CALL_FAILED"}),
        ("an item that is no object", "kraken"),
    ):
        broken = copy.deepcopy(patched)
        next(r for r in broken if r["type"] == "snapshot")["body"]["candidates"][0]["unavailable"] = [bad]
        try:
            mod.build(broken, protocol, now_ms=now)
        except mod.ViewError:
            continue
        c.bad.append(f"{label}: was accepted")
    return c.bad


def _symbol_of(views_: Any, records: list[dict[str, Any]], asset: str) -> str:
    """The symbol of the calls about ``asset``, read from the forecasts' own slots (the manifest names the asset of each symbol)."""
    manifest = next(r for r in records if r["type"] == "manifest")
    entry = next(e for e in manifest["body"]["entries"] if e.get("asset_id") == asset)
    return str(entry["symbol"])


def check_record(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    records, _, _ = ledger()
    v = views(mod)
    forecasts = {r["id"]: r["body"] for r in raw(records, "forecast")}
    settled = {r["body"]["forecast_id"]: r["body"] for r in raw(records, "settlement") if r["body"]["state"] == "SETTLED"}
    last: dict[str, str] = {}
    for r in raw(records, "forecast_state"):
        last[r["body"]["forecast_id"]] = r["body"]["state"]
    for a in v.record:
        mine = {f for f, body in forecasts.items() if body["arm_id"] == a.arm and body["contract"] == a.contract}
        c.eq(f"{a.arm} {a.contract}: the parts add up to the slots", a.scheduled, a.calls + a.abstained + a.failed + a.deciding)
        c.eq(f"{a.arm} {a.contract}: calls", a.calls, len(mine))
        c.eq(f"{a.arm} {a.contract}: settled", a.settled, sum(1 for f in mine if f in settled))
        c.eq(f"{a.arm} {a.contract}: calls are settled, open or not settled", a.calls, a.settled + a.open + a.not_settled)
        if a.calls + a.abstained:
            c.eq(f"{a.arm} {a.contract}: coverage", a.coverage, mod.percent(D(a.calls) / D(a.calls + a.abstained)))
        c.eq(
            f"{a.arm} {a.contract}: availability",
            a.availability,
            mod.percent(1 - D(a.failed) / D(a.scheduled - a.deciding)) if a.scheduled - a.deciding else None,
        )
        if a.contract == "D":
            want = scores_second_way(mod, records, a.arm)
            c.eq(f"{a.arm} D: the score of the calls that count, and of every settled call, and the share left out", _scores(a), want)
            c.eq(f"{a.arm} D: n", _scores(a)[3], sum(1 for f in mine if f in settled))
            c.eq(f"{a.arm} D: the label", a.label, mod._label(sum(1 for f in mine if f in settled)))
        else:
            want = scores_second_way(mod, records, a.arm, "B")
            c.eq(
                f"{a.arm} B: the three-class score of the calls that count, and of every settled call, and the share left out",
                _scores(a),
                want,
            )
            c.eq(f"{a.arm} B: the label", a.label, mod._label(want[3]))
    c.eq("an arm and a contract once each", len({(a.arm, a.contract) for a in v.record}), len(v.record))
    c.eq("both contracts", sorted(a.contract for a in v.record), ["B", "D"])
    c.eq(
        "a record's fields",
        [f.name for f in dataclasses.fields(mod.ArmRecord)],
        [
            "arm", "arm_name", "contract", "scheduled", "calls", "settled", "open", "not_settled", "abstained", "failed", "deciding",
            "coverage", "availability", "mean_brier", "brier_n", "all_mean_brier", "all_brier_n", "missed_c3", "label", "skill", "strata",
            "log_score", "climate_skill", "climate_n", "reliability", "exclusions", "bounds",
        ],
    )  # fmt: skip
    c.eq("every call of the loopback league reached C3 by its origin and holds it", _scores(_arm(v, "L0", "D"))[4], "0%")
    return c.bad


def _scores(a: Any) -> tuple[Any, ...]:
    return tuple(getattr(a, name, "no such field") for name in ("mean_brier", "brier_n", "all_mean_brier", "all_brier_n", "missed_c3"))


def _arm(v: Any, arm: str, contract: str) -> Any:
    return next(a for a in v.record if (a.arm, a.contract) == (arm, contract))


def scores_second_way(mod: types.ModuleType, records: list[dict[str, Any]], arm: str, contract: str = "D") -> tuple[Any, ...]:
    """An arm's up-or-down scores read a second way from the raw records: the mean Brier score of the settled calls that count towards a
    headline, by the single eligibility service (``lifecycle.headline_eligible``, INVARIANTS S-1; contract sections 8 and 17: reached C3 by
    its origin and still holds C3 or more), with their number; the same over every settled call; and the share of those that do not count."""
    forecasts = {r["id"]: r["body"] for r in raw(records, "forecast")}
    settled = {r["body"]["forecast_id"]: r["body"] for r in raw(records, "settlement") if r["body"]["state"] == "SETTLED"}
    histories = lifecycle.rebuild(raw(records, "forecast_state"))
    scored = ("0", "1") if contract == "D" else ("TARGET", "STOP", "TIMEOUT")
    every = sorted(
        f
        for f, b in forecasts.items()
        if b["arm_id"] == arm and b["contract"] == contract and f in settled and settled[f]["outcome"] in scored
    )
    counted = [f for f in every if lifecycle.headline_eligible(histories[f])]

    def one(f: str) -> Any:
        if contract == "D":
            return (D(forecasts[f]["p"]) - D(int(settled[f]["outcome"]))) ** 2
        hit = settled[f]["outcome"]  # the three-class Brier score, read from the three stated chances and the one class that happened
        return sum(
            (
                (D(forecasts[f][key]) - (1 if name == hit else 0)) ** 2
                for key, name in (("p_target", "TARGET"), ("p_stop", "STOP"), ("p_timeout", "TIMEOUT"))
            ),
            D(0),
        )

    def mean(ids: list[str]) -> str | None:
        found = [one(f) for f in ids]
        return None if not found else format((sum(found, D(0)) / len(found)).quantize(D("0.0001"), rounding=ROUND_HALF_EVEN), "f")

    missed = None if not every else mod.percent(D(len(every) - len(counted)) / D(len(every)))
    return (mean(counted), len(counted), mean(every), len(every), missed)


def lowered(records: list[dict[str, Any]], how_many: int) -> tuple[list[dict[str, Any]], list[str]]:
    """The records with the level of the first ``how_many`` settled up-or-down calls of the rules arm lowered to C1 after they settled
    (a record beside the states, with its reason, as contract section 17 allows): they reached C3 by their origin and no longer hold it."""
    forecasts = {r["id"]: r["body"] for r in raw(records, "forecast")}
    settled = {r["body"]["forecast_id"] for r in raw(records, "settlement") if r["body"]["state"] == "SETTLED"}
    histories = lifecycle.rebuild(raw(records, "forecast_state"))
    chosen = sorted(f for f, b in forecasts.items() if b["arm_id"] == "L0" and b["contract"] == "D" and f in settled)[:how_many]
    out = list(records)
    for fid in chosen:
        h = histories[fid]
        step = lifecycle.Step(
            lifecycle.State.LEVEL, h[-1].step.at_ms + 1000, level=forecast.Level.C1, reason="a token's certificate is no longer trusted"
        )
        out.append(lifecycle.to_record(lifecycle.Transition(fid, len(h) + 1, step), SOURCE, step.at_ms))
    return out, chosen


@functools.cache
def c1_ledger() -> tuple[list[dict[str, Any]], Any, int]:
    """A loopback league whose attestor asks one of its three authorities, so its cycle commits at C1 (one token, never C3), and an hour
    later its one-hour calls settle: a bundle below the third level is not counted as a scored call."""
    from hoshitori import attest, tsa_gateway
    from hoshitori.tsa import PinnedRoot, TokenPolicy

    stack = ExitStack()
    atexit.register(stack.close)
    directory = Path(tempfile.mkdtemp(prefix="view-models-c1-"))
    stack.callback(shutil.rmtree, directory, ignore_errors=True)
    lg = stack.enter_context(league(directory))
    name, _chain, server = lg.sites[0]
    one = tsa_gateway.Authority(name, server.url)
    policy = TokenPolicy(tuple(PinnedRoot(cert.root.fingerprint, f"operator-{n}") for n, cert, _ in lg.sites))
    runner = lg.services.env.runner

    def attestor(digest: bytes, committed_at_ms: int) -> Any:
        return attest.attest(
            runner, lg.captures, lg.clock, [one], digest, committed_at_ms=committed_at_ms, policy=policy, rules=lg.root.time_stamp
        )

    services = dataclasses.replace(lg.services, attestor=attestor)
    scheduler.tick(services, lg.config)
    lg.clock.jump(62 * 60)
    scheduler.tick(services, lg.config)
    return scheduler.read_all(lg.services), lg.services.protocol, to_ms(lg.clock.now())


def check_headline(mod: types.ModuleType) -> list[str]:
    """The score counts a settled call only if it reached C3 by its origin and still holds C3 or more; beside it, the score of every settled
    call and the share that missed C3 (contract sections 8, 17 and 27; INVARIANTS S-1)."""
    c = Case(mod)
    records, protocol, now = ledger()
    base = _arm(mod.build(records, protocol, now_ms=now), "L0", "D")
    n = base.settled  # every settled up-or-down call of the rules arm
    down, chosen = lowered(records, 1)
    a = _arm(mod.build(down, protocol, now_ms=now), "L0", "D")
    c.eq("a call lowered below C3 after it settled is left out of the score", _scores(a), scores_second_way(mod, down, "L0"))
    c.eq(
        "and only out of the score",
        (_scores(a)[1], _scores(a)[2:4], a.settled),
        (n - 1, _scores(base)[2:4], n),
    )
    c.eq("the fixture lowers one of several settled calls", (len(chosen), n > 1, _scores(a)[0] != _scores(a)[2]), (1, True, True))
    c.eq("its share is the share of the settled calls", _scores(a)[4], mod.percent(D(1) / D(n)))
    every, _ = lowered(records, n)
    gone = _arm(mod.build(every, protocol, now_ms=now), "L0", "D")
    c.eq(
        "all of them lowered: no score, every settled call beside it, all of them left out",
        (*_scores(gone), gone.label),
        (None, 0, _scores(base)[2], n, "100%", mod._label(n)),
    )
    c1, c1_protocol, c1_now = c1_ledger()
    histories = lifecycle.rebuild(raw(c1, "forecast_state"))
    levels = sorted({level.value for h in histories.values() if (level := lifecycle.current_level(h)) is not None})
    c.eq("the one-authority league commits at C1", levels, ["C1"])
    one = _arm(mod.build(c1, c1_protocol, now_ms=c1_now, scored=True), "L0", "D")
    c.eq("calls that never reached C3 are not in the score", _scores(one), scores_second_way(mod, c1, "L0"))
    c.eq("none counts, two settled, all left out", tuple(_scores(one)[i] for i in (0, 1, 3, 4)), (None, 0, 2, "100%"))
    c.eq("and the label says what settled", one.label, "Provisional: 2 settled calls; nothing is ranked")
    return c.bad


def expected_since(v: Any, records: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """What settled after the previous cycle was committed, counted from the raw records: each settlement's outcome and the time of the step."""
    since = v.cycles[-2].committed_at
    when = {r["body"]["forecast_id"]: r["body"]["at"] for r in raw(records, "forecast_state") if r["body"]["state"] == "SETTLED"}
    words: list[str] = []
    contract = {r["id"]: r["body"]["contract"] for r in raw(records, "forecast")}
    for r in raw(records, "settlement"):
        b = r["body"]
        if b["state"] != "SETTLED" or b["forecast_id"] not in when or when[b["forecast_id"]] <= since:
            continue
        if contract[b["forecast_id"]] == "D":
            words.append("Rose" if b["outcome"] == "1" else "Fell")
        else:
            words.append({"TARGET": "Target first", "STOP": "Stop first", "TIMEOUT": "Neither"}[b["outcome"]])
    return [(w, words.count(w)) for w in ("Rose", "Fell", "Target first", "Stop first", "Neither") if w in words]


def check_changes(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    records, _, _ = ledger()
    v = views(mod)
    c.eq("what changed is counted from when the previous cycle was committed", v.today.since, v.cycles[-2].committed_at)
    want = expected_since(v, records)
    c.eq("and what settled since, by result, in a fixed order", [list(x) for x in v.today.settled_since], [list(x) for x in want])
    c.eq("some calls did settle in the fixture", sum(n for _, n in v.today.settled_since) > 0, True)
    c.eq("a settled row says when", all(r.settled_at is not None for r in v.rows if r.state == "Settled"), True)
    c.eq("an unsettled row does not", all(r.settled_at is None for r in v.rows if r.state != "Settled"), True)
    at = {r["body"]["forecast_id"]: r["body"]["at"] for r in raw(records, "forecast_state") if r["body"]["state"] == "SETTLED"}
    c.eq("the time is the one of its settled step", {r.forecast_id: r.settled_at for r in v.rows if r.state == "Settled"}, at)
    er, ep, en, _ = extended()
    ev = mod.build(er, ep, now_ms=en)
    c.eq(
        "with barrier results too, in the fixed order", [list(x) for x in ev.today.settled_since], [list(x) for x in expected_since(ev, er)]
    )
    c.eq(
        "they include a target, a stop and a timeout",
        [w for w, _ in ev.today.settled_since if w in ("Target first", "Stop first", "Neither")],
        ["Target first", "Stop first", "Neither"],
    )
    settled = next(r for r in v.rows if r.state == "Settled")
    edge = "2026-10-30T12:00:00.000Z"
    on = dataclasses.replace(settled, settled_at=edge)
    after = dataclasses.replace(settled, settled_at="2026-10-30T12:00:00.001Z")
    c.eq("a call settled at the very moment is not after it", mod._settled_since([on], edge), ())
    c.eq("one a millisecond later is", mod._settled_since([after], edge), ((settled.result, 1),))
    c.eq("an open call is never counted", mod._settled_since([dataclasses.replace(settled, result=None, settled_at=None)], edge), ())
    mr, mp, mn = model_ledger()
    one = mod.build(mr, mp, now_ms=mn)
    c.eq("with one cycle there is no earlier moment and nothing is counted", (one.today.since, one.today.settled_since), (None, ()))
    c.eq("the results are these words", mod.RESULTS, ("Rose", "Fell", "Target first", "Stop first", "Neither"))
    return c.bad


def check_today(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    v = views(mod)
    c.eq("exploratory by default", v.today.mode, "Exploratory run: not a scored period")
    c.eq("a scored period says so", views(mod, scored=True).today.mode, "Scored period")
    c.eq("two cycles, and the latest is today's", (len(v.cycles), v.today.latest), (2, v.cycles[-1]))
    c.eq("they are in the order they were planned", [x.planned_for for x in v.cycles], sorted(x.planned_for for x in v.cycles))
    cyc = v.cycles[-1]
    c.eq("the cycle is committed", (cyc.state, cyc.committed_at is not None), ("COMMITTED", True))
    c.eq(
        "its slots are the rows of its cycle",
        (cyc.slots, len(v.today.rows), {r.cycle_id for r in v.today.rows}),
        (len([r for r in v.rows if r.cycle_id == cyc.cycle_id]), cyc.slots, {cyc.cycle_id}),
    )
    c.eq("all the slots are the rows", sum(x.slots for x in v.cycles), len(v.rows))
    c.eq("the counts by state add up", sum(n for _, n in cyc.rows_by_state), cyc.slots)
    c.eq(
        "the states are in the list's order",
        [s for s, _ in cyc.rows_by_state],
        [s for s in labels.ROW_STATES if s in dict(cyc.rows_by_state)],
    )
    c.eq(
        "settled lately are settled rows", (len(v.today.settled_lately) > 0, {r.state for r in v.today.settled_lately}), (True, {"Settled"})
    )
    c.eq("and at most the number asked for", len(views(mod, recent=2).today.settled_lately), 2)
    c.eq("generated at the time given", v.today.generated_at.endswith("Z"), True)
    return c.bad + check_sources_read(mod) + check_cycle_in_progress(mod)


def sources_second_way(records: list[dict[str, Any]], cycle_id: str) -> list[tuple[str, int, int]]:
    """What each source brought into a cycle's snapshot and for how many candidates it brought nothing, read straight from the records."""
    body = next(r["body"] for r in records if r["type"] == "snapshot" and r["body"]["cycle_id"] == cycle_id)
    source_of = {r["id"]: r["body"]["source"] for r in records if r["type"] == "evidence"}
    ids = {i["evidence_id"] for i in body["shared"]}
    for cand in body["candidates"]:
        ids |= {i["evidence_id"] for i in cand["items"]}
    items: dict[str, int] = {}
    for i in ids:
        items[source_of[i]] = items.get(source_of[i], 0) + 1
    unread: dict[str, int] = {}
    for cand in body["candidates"]:
        for u in cand.get("unavailable", []):
            unread[u["source"]] = unread.get(u["source"], 0) + 1
    return [(s, items.get(s, 0), unread.get(s, 0)) for s in sorted(set(items) | set(unread))]


def check_sources_read(mod: types.ModuleType) -> list[str]:
    """Today says, for the latest cycle, what each source brought and where it brought nothing: the counts are the snapshot's own."""
    c = Case(mod)
    records, protocol, now = ledger()
    v = mod.build(records, protocol, now_ms=now)
    cyc = v.cycles[-1]
    c.eq(
        "each source's count of items and of candidates it did not answer for, from the snapshot",
        [(s.source, s.items, s.unread) for s in cyc.sources],
        sources_second_way(records, cyc.cycle_id),
    )
    c.eq(
        "the summaries of the earlier cycle say the same of theirs",
        [(s.source, s.items, s.unread) for s in v.cycles[0].sources],
        sources_second_way(records, v.cycles[0].cycle_id),
    )
    planted = copy.deepcopy(records)
    shot = next(r for r in planted if r["type"] == "snapshot" and r["body"]["cycle_id"] == cyc.cycle_id)
    shot["body"]["candidates"][0]["unavailable"] = [{"source": "ryo", "reason": "TIMED_OUT", "interval_s": None}]
    w = mod.build(planted, protocol, now_ms=now)
    c.eq(
        "a source that brought nothing for a candidate is counted as such, and appears even with no items",
        [(s.source, s.items, s.unread) for s in w.cycles[-1].sources],
        sources_second_way(planted, cyc.cycle_id),
    )
    c.eq("and it is named ryo with one unread", next(s for s in w.cycles[-1].sources if s.source == "ryo").unread, 1)
    many = copy.deepcopy(records)
    shot = next(r for r in many if r["type"] == "snapshot" and r["body"]["cycle_id"] == cyc.cycle_id)
    shot["body"]["candidates"][0]["unavailable"] = [
        {"source": name, "reason": "TIMED_OUT", "interval_s": None} for name in ("zeta", "mu", "alpha", "omega", "kappa", "beta")
    ]
    names = [s.source for s in mod.build(many, protocol, now_ms=now).cycles[-1].sources]
    c.eq("the sources are listed in alphabetical order", names, sorted(names))
    shared = copy.deepcopy(records)
    shot = next(r for r in shared if r["type"] == "snapshot" and r["body"]["cycle_id"] == cyc.cycle_id)
    moved = shot["body"]["candidates"][0]["items"].pop()
    shot["body"]["shared"] = [moved]
    c.eq(
        "an item held for every candidate is counted with the source that brought it",
        [(s.source, s.items, s.unread) for s in mod.build(shared, protocol, now_ms=now).cycles[-1].sources],
        sources_second_way(shared, cyc.cycle_id),
    )
    try:
        cyc.sources[0].items = 0  # type: ignore[misc]
        c.eq("a source's reading cannot be changed", "changed", "refused")
    except AttributeError:
        pass
    return c.bad


def cut_cycle(records: list[dict[str, Any]], cycle_id: str, *, keep_steps: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """The records without what one cycle wrote: its forecasts and their states and settlements, its snapshot and manifest, and its steps
    (except the named ones, which stay: a cycle that has been planned and is being collected has steps and no calls)."""
    fids = {r["id"] for r in records if r["type"] == "forecast" and r["body"]["cycle_id"] == cycle_id}
    kept = []
    for r in records:
        b, kind = r["body"], r["type"]
        if kind == "cycle_state" and b["cycle_id"] == cycle_id and b["state"] not in keep_steps:
            continue
        if kind in ("forecast", "forecast_state", "settlement") and (r["id"] in fids or b.get("forecast_id") in fids):
            continue
        if kind in ("snapshot", "manifest") and b["cycle_id"] == cycle_id and not keep_steps:
            continue
        if kind == "snapshot" and b["cycle_id"] == cycle_id:
            continue
        kept.append(r)
    return kept


def check_cycle_in_progress(mod: types.ModuleType) -> list[str]:
    """A cycle that is being collected has no calls yet: Today keeps showing the last committed cycle and says that another is coming,
    never an empty page for the minutes every hour in which a cycle runs."""
    c = Case(mod)
    records, protocol, now = ledger()
    order: list[str] = []
    for r in records:
        if r["type"] == "cycle_state" and r["body"]["cycle_id"] not in order:
            order.append(r["body"]["cycle_id"])
    last = order[-1]
    running = cut_cycle(records, last, keep_steps=("PLANNED", "DISCOVERING", "SEALED", "COLLECTING"))
    alone = mod.build(cut_cycle(records, last), protocol, now_ms=now)
    v = mod.build(running, protocol, now_ms=now)
    c.eq("both cycles are listed", [(x.cycle_id, x.state) for x in v.cycles][-1][1], "COLLECTING")
    c.eq("Today shows the cycle that was committed", (v.today.latest, v.today.rows), (alone.today.latest, alone.today.rows))
    c.eq(
        "and what changed is counted as it is for a ledger with that cycle alone",
        (v.today.since, v.today.rule, v.today.changes, v.today.settled_since, v.today.settled_lately),
        (alone.today.since, alone.today.rule, alone.today.changes, alone.today.settled_since, alone.today.settled_lately),
    )
    c.eq("it says another cycle is being collected, and which", v.today.in_progress, v.cycles[-1].planned_for)
    c.eq("a ledger with no cycle in progress says nothing of one", (alone.today.in_progress, views(mod).today.in_progress), (None, None))
    abandoned = [dict(r) for r in running]
    for r in abandoned:
        if r["type"] == "cycle_state" and r["body"]["cycle_id"] == last and r["body"]["state"] == "COLLECTING":
            body = dict(r["body"])
            body["state"] = "ABANDONED"
            body["reason"] = "CLOCK_OFFSET"
            r["body"] = body
    done = mod.build(abandoned, protocol, now_ms=now)
    c.eq("a cycle that was abandoned is not in progress", (done.today.latest, done.today.in_progress), (alone.today.latest, None))
    return c.bad


def _plus(stamp: str, hours: int) -> str:
    moment = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ") + timedelta(hours=hours)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def check_due(mod: types.ModuleType) -> list[str]:
    """``due`` is the earliest the call can settle, read a second way from the raw state records: the start of its window plus its
    horizon, or its commitment plus its horizon while the start is not yet known."""
    c = Case(mod)
    records, _, _ = ledger()
    v = views(mod)
    committed = {r["body"]["forecast_id"]: r["body"]["at"] for r in raw(records, "forecast_state") if r["body"]["state"] == "COMMITTED"}
    want = {}
    for row in v.rows:
        if row.forecast_id in committed:
            start = row.origin if row.origin is not None else committed[row.forecast_id]
            want[row.forecast_id] = _plus(start, row.hours)
    c.eq(
        "every row that was committed says when it can first settle",
        {r.forecast_id: r.due for r in v.rows if r.forecast_id in committed},
        want,
    )
    c.eq(
        "a row that was never committed says nothing",
        {r.due for r in v.rows if r.forecast_id not in committed},
        {None} if any(r.forecast_id not in committed for r in v.rows) else set(),
    )
    c.eq(
        "some rows still wait for their window and some have started it",
        ({r.origin is None for r in v.rows}, len(want) > 0),
        ({True, False}, True),
    )
    return c.bad


def check_today_changes(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    records, _, _ = ledger()
    v = views(mod)
    t = v.today
    c.eq(
        "one sentence says what this is",
        t.product,
        "Agents forecast crypto in public, and every call is time-stamped before its outcome.",
    )
    c.eq(
        "with an earlier cycle on record the printed rule is the order of the kinds",
        t.rule,
        "Flips first, then settled against, stopped, settled as said, then moves; the larger first within each.",
    )
    p_of = {r["id"]: Decimal(r["body"]["p"]) for r in raw(records, "forecast") if "p" in r["body"]}
    rows = {r.forecast_id: r for r in v.rows}
    # the fixture: the first cycle's four one-hour calls settled since (BTC and ETH, up-or-down and target-or-stop); nothing else changed
    best = sorted(
        (-(p_of[r.forecast_id] if r.result == "Rose" else 1 - p_of[r.forecast_id]), r.symbol, r.forecast_id)
        for r in v.rows
        if r.state == "Settled" and r.contract == "D" and r.cycle_id == v.cycles[0].cycle_id
    )
    ids = [fid for _, _, fid in best]
    c.eq("the best are the settled up-or-down calls that went as said, the surest first", [x.forecast_id for x in t.changes][:2], ids[:2])
    c.eq("all of them hits, with their closed action", {(x.kind, x.action) for x in t.changes}, {("HIT", "See the record")})
    c.eq("at most three are shown", len(t.changes), 3)
    c.eq(
        "each is about its own receipt",
        [(x.symbol, x.hours, x.arm_name) for x in t.changes],
        [(rows[x.forecast_id].symbol, rows[x.forecast_id].hours, rows[x.forecast_id].arm_name) for x in t.changes],
    )
    c.eq("and a receipt that exists", {x.forecast_id for x in t.changes} <= {r.row.forecast_id for r in v.receipts}, True)
    c.eq("the first of them in words", t.changes[0].sentence, "Said 11% up; the result: fell.")
    try:
        t.changes[0].sentence = "changed"  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        c.bad.append("a change can be edited after it is made")
    mr, mp, mn = model_ledger()
    one = mod.build(mr, mp, now_ms=mn)
    c.eq(
        "one cycle on record: the first cycle's rule, and the calls it made",
        one.today.rule,
        "First cycle on record: nothing has changed yet. Strongest lean first.",
    )
    c.eq("they are all new, and at most three", ({x.kind for x in one.today.changes}, len(one.today.changes) <= 3), ({"NEW"}, True))
    c.eq("the product sentence is the same", one.today.product, t.product)
    c.eq(
        "Today takes no personal input: build asks only for the ledger, the protocol and the time",
        sorted(inspect.signature(mod.build).parameters),
        ["now_ms", "protocol", "recent", "records", "scored"],
    )
    return c.bad


def check_compared_with(mod: types.ModuleType) -> list[str]:
    """What changed is counted against the latest earlier cycle that made a call, never against one whose every slot failed."""
    from types import SimpleNamespace as N

    c = Case(mod)
    a, b, failed = N(cycle_id="a"), N(cycle_id="b"), N(cycle_id="f")
    rows = [N(cycle_id="a", forecast_id="fa"), N(cycle_id="b", forecast_id="fb"), N(cycle_id="f", forecast_id="ff")]
    calls = {"fa": object(), "fb": object()}  # the failed cycle's slot has no call
    c.eq("the latest earlier cycle with a call", mod._compared_with([a, b], rows, calls), b)
    c.eq("a cycle whose slots all failed is passed over", mod._compared_with([a, b, failed], rows, calls), b)
    c.eq("and an earlier one is used when the later ones made none", mod._compared_with([a, failed], rows, calls), a)
    c.eq("no earlier cycle with a call: nothing to compare", mod._compared_with([failed], rows, calls), None)
    c.eq("no earlier cycle at all: nothing to compare", mod._compared_with([], rows, calls), None)
    return c.bad


def check_gap_and_strata(mod: types.ModuleType) -> list[str]:
    """The age of the evidence and the drift on the receipts, and the thirds of the record (contract section 25, item 8)."""
    from tests import gap_record_checks

    c = Case(mod)
    records, protocol, now, _ = extended()
    seen = mod.build(records, protocol, now_ms=now)
    want = gap_record_checks._second_way(records)
    for receipt in seen.receipts:
        age, drift = want[receipt.row.forecast_id]
        c.eq(f"{receipt.row.forecast_id}: the age in seconds", receipt.evidence_age_s, None if age is None else age // 1000)
        c.eq(
            f"{receipt.row.forecast_id}: the drift to four places",
            receipt.gap_drift_atr,
            None if drift is None else format(drift.quantize(D("0.0001"), rounding=ROUND_HALF_EVEN), "f"),
        )
    c.eq("a world this small shows no thirds", [a.strata for a in seen.record], [()] * len(seen.record))
    # thirds of twelve settled calls: the cuts are the values a third and two thirds of the way through the sorted twelve
    gaps = {f"f{n:02d}": mod.gap_record.Gap(60_000 * (n + 1), D(n) / 10 if n % 4 else None) for n in range(12)}
    scored = {f"f{n:02d}": D(n) / 100 for n in range(12)}
    cuts = {"age": (D(5), D(9)), "drift": (D("0.5"), D("0.9"))}
    lines = mod._strata(scored, gaps, cuts)
    c.eq(
        "the age thirds: how many, their range and their mean score",
        [(x.group, x.n, x.low, x.high, x.mean_brier) for x in lines if x.by == "age"],
        [(0, 4, "1.0000", "4.0000", "0.0150"), (1, 4, "5.0000", "8.0000", "0.0550"), (2, 4, "9.0000", "12.0000", "0.0950")],
    )
    c.eq(
        "the drift thirds leave out the calls with no drift",
        [(x.group, x.n, x.low, x.high, x.mean_brier) for x in lines if x.by == "drift"],
        [(0, 3, "0.1000", "0.3000", "0.0200"), (1, 3, "0.5000", "0.7000", "0.0600"), (2, 3, "0.9000", "1.1000", "0.1000")],
    )
    c.eq("no cuts, no lines", mod._strata(scored, gaps, {}), ())
    c.eq("a split without cuts is left out", [x.by for x in mod._strata(scored, gaps, {"age": cuts["age"]})], ["age"] * 3)
    return c.bad


def check_gap_cuts(mod: types.ModuleType) -> list[str]:
    """The cuts are made from the settled calls of every arm of a contract together, and from nothing else."""
    from types import SimpleNamespace

    from hoshitori import scoring

    c = Case(mod)
    gap = mod.gap_record.Gap

    def settled(outcome: str, state: str = "SETTLED") -> Any:
        return SimpleNamespace(state=SimpleNamespace(value=state), outcome=outcome)

    ids = [f"d{n:02d}" for n in range(12)]
    gaps = {i: gap(60_000 * (n + 1), D(n) / 10 if n % 4 else None) for n, i in enumerate(ids)}
    settlements = {i: settled("1" if n % 2 else "0") for n, i in enumerate(ids)}
    calls = {i: object() for i in ids}
    groups: dict[tuple[str, str], list[str]] = {("L0", "D"): ids[:6], ("L1", "D"): ids[6:]}
    # what must not count: a call that did not settle, one with an outcome the event has not, one with no call and one with no gap
    for extra, state, outcome, call, has_gap in (
        ("voided", "VOID", "1", True, True),
        ("ambiguous", "SETTLED", "AMBIGUOUS", True, True),
        ("nocall", "SETTLED", "1", False, True),
        ("nogap", "SETTLED", "1", True, False),
    ):
        groups[("L0", "D")].append(extra)
        settlements[extra] = settled(outcome, state)
        if call:
            calls[extra] = object()
        if has_gap:
            gaps[extra] = gap(60_000 * 1000, D(1000))
    got = mod._gap_cuts(groups, calls, settlements, gaps)
    c.eq(
        "the thirds of twelve calls, by age in minutes and by drift where there is one",
        got,
        {"D": {"age": (D(5), D(9)), "drift": (D("0.5"), D("0.9"))}},
    )
    # a contract with too few calls has no cuts, and one with enough has its own
    barrier = [f"b{n:02d}" for n in range(9)]
    groups[("L0", "B")] = barrier
    for n, i in enumerate(barrier):
        gaps[i], calls[i], settlements[i] = gap(60_000 * (n + 1), None), object(), settled(sorted(scoring.BARRIER_CLASSES)[0])
    got = mod._gap_cuts(groups, calls, settlements, gaps)
    c.eq("a barrier contract of nine calls is cut by its own", got["B"], {"age": (D(4), D(7))})
    c.eq("and has no drift cuts without drifts", "drift" in got["B"], False)
    few = {("L0", "B"): barrier[:5]}
    c.eq("five calls are too few", mod._gap_cuts(few, calls, settlements, gaps), {})
    c.eq("the contracts that carry no probability are not cut", mod._gap_cuts({("L0", "P"): ids}, calls, settlements, gaps), {})
    c.eq(
        "a gap's defaults",
        (mod.Today.__dataclass_fields__["scheduled_cycles"].default, mod.Today.__dataclass_fields__["missed_cycles"].default),
        (0, ()),
    )
    c.eq("a third of the record is a value", mod.StratumLine.__dataclass_params__.frozen, True)
    return c.bad


def check_receipt_age(mod: types.ModuleType) -> list[str]:
    """The age on a receipt is in whole seconds, also for evidence that is hours old."""
    from datetime import datetime, timedelta

    from tests import gap_record_checks

    c = Case(mod)
    records, protocol, now, _ = extended()

    def earlier(text: str) -> str:
        d = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ") - timedelta(hours=3)
        return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"

    old = [dict(r, body={**r["body"], "as_of": earlier(r["body"]["as_of"])}) if r["type"] == "forecast" else r for r in records]
    want = gap_record_checks._second_way(old)
    got = {r.row.forecast_id: r.evidence_age_s for r in mod.build(old, protocol, now_ms=now).receipts}
    c.eq("whole seconds", got, {f: None if a is None else a // 1000 for f, (a, _) in want.items()})
    c.eq("and some are hours old", any(v is not None and v > 10_000 for v in got.values()), True)
    return c.bad


def check_more_scores(mod: types.ModuleType) -> list[str]:
    """The logarithmic score, the skill against the climatological rate, the reliability groups and the exclusions, each read a second way."""
    import math

    from hoshitori import scoring

    c = Case(mod)
    ps = ["0.30", "0.35", "0.40", "0.45", "0.50", "0.55", "0.60", "0.65", "0.70", "0.20", "0.80", "0.50"]
    ys = [0, 0, 1, 0, 1, 0, 1, 1, 1, 0, 1, 0]
    rows = [
        scoring.Directional(f"f{n:02d}", "L1", "BTC", "all", D(p), y, D("0.6") if n % 3 else None)
        for n, (p, y) in enumerate(zip(ps, ys, strict=True))
    ]
    log_score, skill, with_reference, groups = mod._more_scores(rows)
    floats = [float(p) for p in ps]

    def loss(p: float, y: int) -> float:
        return -(y * math.log(p) + (1 - y) * math.log(1 - p))

    want_log = sum(loss(p, y) for p, y in zip(floats, ys, strict=True)) / len(ys)
    c.eq("the logarithmic score, from the definition", abs(float(log_score or "nan") - want_log) < 6e-5, True)
    referenced = [(p, y) for n, (p, y) in enumerate(zip(floats, ys, strict=True)) if n % 3]
    mine = sum((p - y) ** 2 for p, y in referenced) / len(referenced)
    theirs = sum((0.6 - y) ** 2 for _, y in referenced) / len(referenced)
    c.eq(
        "the skill against the climatological rate, only over the calls that had one",
        abs(float(skill or "nan") - (1 - mine / theirs)) < 6e-5,
        True,
    )
    c.eq("and how many had one", with_reference, len(referenced))
    c.eq("three groups of four", [(g.group, g.n) for g in groups], [(0, 4), (1, 4), (2, 4)])
    order = sorted(range(len(ys)), key=lambda k: (floats[k], f"f{k:02d}"))
    for g in groups:
        members = order[g.group * 4 : g.group * 4 + 4]
        c.eq(f"group {g.group}: the mean said", abs(float(g.mean_p) - sum(floats[k] for k in members) / 4) < 6e-5, True)
        c.eq(f"group {g.group}: how often it rose", float(g.frequency), sum(ys[k] for k in members) / 4)
        c.eq(f"group {g.group}: the range said", (float(g.low), float(g.high)), (floats[members[0]], floats[members[-1]]))
    c.eq("nothing settled, nothing to show", mod._more_scores([]), (None, None, 0, ()))
    c.eq("eight calls are too few for three groups", mod._more_scores(rows[:8])[3], ())
    c.eq("nine are enough", len(mod._more_scores(rows[:9])[3]), 3)
    no_reference = [scoring.Directional(f"g{n}", "L1", "BTC", "all", D("0.4"), n % 2) for n in range(4)]
    c.eq("without a reference there is no skill against it", mod._more_scores(no_reference)[1:3], (None, 0))
    c.eq(
        "exclusions are counted by reason, in order",
        mod._exclusions(["VOID_TIE", "UNSETTLEABLE_DISAGREE", "VOID_TIE", "VOID_GAP"]),
        (
            mod.ExclusionLine("UNSETTLEABLE_DISAGREE", "The two exchanges disagreed", 1),
            mod.ExclusionLine("VOID_GAP", "Too long between the evidence and the start", 1),
            mod.ExclusionLine("VOID_TIE", "The price did not change", 2),
        ),
    )
    c.eq("none excluded", mod._exclusions([]), ())
    c.eq(
        "the new lines are values",
        (mod.ReliabilityLine.__dataclass_params__.frozen, mod.ExclusionLine.__dataclass_params__.frozen),
        (True, True),
    )
    c.eq("a record that has no directional figures says so", mod.ArmRecord.__dataclass_fields__["climate_n"].default, 0)
    return c.bad


def check_bounds(mod: types.ModuleType) -> list[str]:
    """The score with every excluded call counted each way, read a second way: every assignment of outcomes, tried one by one."""
    import itertools

    from hoshitori import scoring

    c = Case(mod)
    settled_p, settled_y = ["0.70", "0.60", "0.30", "0.55", "0.40"], [1, 1, 0, 0, 1]
    rows = [scoring.Directional(f"s{n}", "L1", "BTC", "all", D(p), y) for n, (p, y) in enumerate(zip(settled_p, settled_y, strict=True))]
    gone = ["0.90", "0.20", "0.65"]
    excluded = [scoring.ExcludedDirectional(f"x{n}", "L1", "BTC", "all", "VOID_TIE", D(p)) for n, p in enumerate(gone)]
    got = mod._bounds(rows, excluded)
    means, skills = [], []
    for outcomes in itertools.product((0, 1), repeat=len(gone)):
        pairs = list(zip(settled_p, settled_y, strict=True)) + list(zip(gone, outcomes, strict=True))
        mine = sum((float(p) - y) ** 2 for p, y in pairs) / len(pairs)
        coin = sum((0.5 - y) ** 2 for _, y in pairs) / len(pairs)
        means.append(mine)
        skills.append(1 - mine / coin)
    c.eq("three excluded calls", got.excluded if got else None, 3)
    c.eq(
        "the Brier score, the least and the greatest it could be",
        (float(got.brier_low), float(got.brier_high)) if got else None,
        (round(min(means), 4), round(max(means), 4)),
    )
    c.eq(
        "its skill against a coin, the least and the greatest",
        (float(got.skill_low), float(got.skill_high)) if got else None,
        (round(min(skills), 4), round(max(skills), 4)),
    )
    head = 1 - (sum((float(p) - y) ** 2 for p, y in zip(settled_p, settled_y, strict=True)) / 5) / (0.25)
    c.eq(
        "the result depends on what was left out only when the skill changes sign",
        got.sensitive if got else None,
        min([*skills, head]) < 0 < max([*skills, head]),
    )
    sure = [scoring.ExcludedDirectional("x0", "L1", "BTC", "all", "VOID_TIE", D("0.5"))]  # a coin's own call: skill moves little
    c.eq("a sign that never changes is not called sensitive", mod._bounds(rows, sure).sensitive is False, True)
    c.eq("nothing excluded, no bounds", mod._bounds(rows, []), None)
    c.eq("nothing settled, no bounds", mod._bounds([], excluded), None)
    c.eq("the line is a value", mod.BoundsLine.__dataclass_params__.frozen, True)
    return c.bad


def check_known_outcomes(mod: types.ModuleType) -> list[str]:
    """The outcomes a climatological rate is read from: settled rises and falls of the directional contract, by horizon, with the time known."""
    from types import SimpleNamespace

    from hoshitori import scoring

    c = Case(mod)

    def call(horizon: int) -> Any:
        return SimpleNamespace(common=SimpleNamespace(horizon_ms=horizon))

    def settled(outcome: str, state: str = "SETTLED") -> Any:
        return SimpleNamespace(state=SimpleNamespace(value=state), outcome=outcome)

    def history(at: int) -> Any:
        return [SimpleNamespace(step=SimpleNamespace(at_ms=at - 5)), SimpleNamespace(step=SimpleNamespace(at_ms=at))]

    groups = {("L0", "D"): ["a", "b", "c"], ("L1", "D"): ["d", "e", "f", "g"], ("L0", "B"): ["h"]}
    calls = {
        "a": call(3_600_000),
        "b": call(3_600_000),
        "c": call(14_400_000),
        "d": call(3_600_000),
        "e": call(3_600_000),
        "f": call(3_600_000),
        "h": call(3_600_000),
    }
    settlements = {
        "a": settled("1"),
        "b": settled("0"),
        "c": settled("1"),
        "d": settled("1", "VOID"),
        "e": settled("AMBIGUOUS"),
        "g": settled("1"),  # no call
        "h": settled("1"),  # a call of the barrier contract is never counted as a rise, whatever its outcome says
    }
    histories = {
        "a": history(1000),
        "b": history(2000),
        "c": history(3000),
        "d": history(4000),
        "e": history(5000),
        "g": history(6000),
        "h": history(7000),
    }
    got = mod._known_outcomes(groups, calls, settlements, histories)
    c.eq(
        "by horizon, the time known and the outcome, of settled rises and falls only",
        {k: sorted(v) for k, v in got.items()},
        {3_600_000: [(1000, 1), (2000, 0)], 14_400_000: [(3000, 1)]},
    )
    c.eq("nothing settled, nothing known", dict(mod._known_outcomes({("L0", "D"): ["a"]}, calls, {}, histories)), {})
    # a rate is read only from outcomes known in the 30 days before a call: twenty of them make a reference, and a later one is never read
    month = 30 * 86_400_000
    start = 10 * month
    old = [(start - month + 1 + n, 1 if n % 4 else 0) for n in range(20)]  # 15 rises of 20, all inside the 30 days
    c.eq("twenty outcomes in the 30 days are a reference", scoring.climatology(old, start), D(15) / 20)
    later = [*old, (start, 0), (start + 5, 0)]
    c.eq("an outcome known at the creation time or after is never read", scoring.climatology(later, start), D(15) / 20)
    return c.bad


def check_climate_history(mod: types.ModuleType) -> list[str]:
    """A call is compared with the outcomes known before it, and an arm's record shows what the loopback league made of them."""
    c = Case(mod)
    records, protocol, now, _ = extended()
    v = mod.build(records, protocol, now_ms=now)
    d = [a for a in v.record if a.contract == "D"]
    c.eq(
        "every directional arm carries a logarithmic score when it has a score",
        all((a.log_score is None) == (a.brier_n == 0) for a in d),
        True,
    )
    c.eq(
        "and no reference with fewer than twenty outcomes before a call", all(a.climate_skill is None and a.climate_n == 0 for a in d), True
    )
    c.eq("a world this small shows no reliability groups", all(a.reliability == () for a in d), True)
    # the bounds of the directional arm, from the raw records: every way the excluded calls could have gone, tried one by one
    import itertools

    forecasts = {r["id"]: r["body"] for r in records if r["type"] == "forecast" and r["body"].get("contract") == "D"}
    states = {r["body"]["forecast_id"]: r["body"] for r in records if r["type"] == "settlement" and r["body"]["forecast_id"] in forecasts}
    settled_pairs = [(float(forecasts[f]["p"]), int(s["outcome"])) for f, s in sorted(states.items()) if s["state"] == "SETTLED"]
    excluded_p = [float(forecasts[f]["p"]) for f, s in sorted(states.items()) if s["state"] in ("VOID", "UNSETTLEABLE")]
    means = []
    for outcomes in itertools.product((0, 1), repeat=len(excluded_p)):
        pairs = settled_pairs + list(zip(excluded_p, outcomes, strict=True))
        means.append(sum((p - y) ** 2 for p, y in pairs) / len(pairs))
    shown = [a for a in d if a.bounds is not None]
    c.eq("one directional arm shows bounds", len(shown), 1)
    if shown and shown[0].bounds is not None:
        got = shown[0].bounds
        c.eq("they count the calls left out", got.excluded, len(excluded_p))
        c.eq(
            "and bound the score as the records say",
            (float(got.brier_low), float(got.brier_high)),
            (round(min(means), 4), round(max(means), 4)),
        )
    c.eq("the arms without exclusions show none", all(a.bounds is None for a in v.record if not a.exclusions), True)
    b = [a for a in v.record if a.contract == "B"]
    c.eq(
        "a barrier arm has none of the directional figures",
        all((a.log_score, a.climate_skill, a.climate_n, a.reliability) == (None, None, 0, ()) for a in b),
        True,
    )
    voids = sorted(
        str(r["body"]["reason"]) for r in records if r["type"] == "settlement" and r["body"]["state"] in ("VOID", "UNSETTLEABLE")
    )
    shown = sorted(line.reason for a in v.record for line in a.exclusions for _ in range(line.n))
    c.eq("every void or unsettleable settlement is shown by its reason", shown, voids)
    return c.bad


def check_missed_cycles(mod: types.ModuleType) -> list[str]:
    """The cycles the schedule put due that left no record, counted from the schedule and the cycle records read a second way."""
    c = Case(mod)
    records, protocol, now, _ = extended()
    ran = {r["body"]["planned_for"] for r in records if r["type"] == "cycle_state" and r["body"]["state"] == "PLANNED"}

    def stamp(ms: int) -> str:
        return (datetime(1970, 1, 1) + timedelta(milliseconds=ms)).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"

    every, start = protocol.schedule_every_ms, protocol.schedule_start_ms
    due = [start + k * every for k in range((now - start) // every + 1)]
    want = tuple(stamp(ms) for ms in due if stamp(ms) not in ran and now >= ms + every)
    seen = mod.build(records, protocol, now_ms=now).today
    c.eq("the cycles due", seen.scheduled_cycles, len(due))
    c.eq("those that left no record, each once the next is due", seen.missed_cycles, want)
    c.eq("some did run and some did not", (len(ran) > 0, len(want) > 0), (True, True))
    # a protocol written again after cycles had run keeps those cycles: the slots from the first of them are due, and a gap before the new
    # start is listed, not left out (the counts must equal those of the original protocol, whose start was the first cycle's slot)
    later_start = dataclasses.replace(protocol, schedule_start_ms=start + 5 * every)
    moved = mod.build(records, later_start, now_ms=now).today
    c.eq("a later start still counts every due slot", moved.scheduled_cycles, len(due))
    c.eq("and lists the same missed cycles", moved.missed_cycles, want)
    off_grid = dataclasses.replace(protocol, schedule_start_ms=start + 5 * every + 1)
    c.eq(
        "a start off the grid of the first cycle counts only from the start",
        mod.build(records, off_grid, now_ms=now).today.scheduled_cycles,
        len(off_grid.slots_through(now)),
    )
    # a clock before the first slot: nothing is due and nothing is missed
    early = mod.build(records, protocol, now_ms=start - 1).today
    c.eq("before the first slot", (early.scheduled_cycles, early.missed_cycles), (0, ()))
    # at the instant a slot is due it is not yet missed: it is given until the next one
    first = mod.build([], protocol, now_ms=start).today
    c.eq("a slot that has just fallen due is not missed", (first.scheduled_cycles, first.missed_cycles), (1, ()))
    later = mod.build([], protocol, now_ms=start + every).today
    c.eq("and is once the next one is due", (later.scheduled_cycles, len(later.missed_cycles)), (2, 1))
    return c.bad


@functools.cache
def ryo_ledger() -> tuple[list[dict[str, Any]], Any, int]:
    """A league that reads RYO and makes barrier calls, run through its first cycle's 24-hour windows: reads, windows and plans settled."""
    stack = ExitStack()
    atexit.register(stack.close)
    directory = Path(tempfile.mkdtemp(prefix="view-models-ryo-"))
    stack.callback(shutil.rmtree, directory, ignore_errors=True)
    lg = stack.enter_context(league(directory, barrier=barrier_map(), ryo=True))
    scheduler.tick(lg.services, lg.config)
    lg.clock.jump(62 * 60)
    scheduler.tick(lg.services, lg.config)
    lg.clock.jump(23 * 3600)
    scheduler.tick(lg.services, lg.config)
    return scheduler.read_all(lg.services), lg.services.protocol, to_ms(lg.clock.now())


def check_ryo(mod: types.ModuleType) -> list[str]:
    """Arm R in the views, worked out a second way from the records: each read joined to its cycle's settled 24-hour directional window."""
    c = Case(mod)
    c.eq("a ledger without a read of RYO has no RYO record", views(mod).ryo, None)
    records, protocol, now = ryo_ledger()
    v = mod.build(records, protocol, now_ms=now)
    reads = [r["body"] for r in raw(records, "ryo_read")]
    assets = {(m["body"]["cycle_id"], e["candidate_id"]): e["asset_id"] for m in raw(records, "manifest") for e in m["body"]["entries"]}
    planned = {r["body"]["cycle_id"]: r["body"]["planned_for"] for r in raw(records, "cycle_state") if r["body"]["state"] == "PLANNED"}
    made = {r["id"]: r["body"] for r in raw(records, "forecast")}
    windows: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for st in raw(records, "settlement"):
        f = made[st["body"]["forecast_id"]]
        if f["contract"] == "D" and f["horizon_ms"] == 24 * 3_600_000:
            windows.setdefault((f["cycle_id"], f["asset_id"]), []).append(st["body"])
    committed = {(f["cycle_id"], f["asset_id"]) for f in made.values() if f["contract"] == "D" and f["horizon_ms"] == 24 * 3_600_000}
    plans = {r["body"]["read_id"]: r["body"] for r in raw(records, "ryo_plan_settlement")}

    def second_way(body: dict[str, Any]) -> tuple[Any, ...]:
        key = (body["cycle_id"], assets[(body["cycle_id"], body["candidate_id"])])
        got = windows.get(key, [])
        direction = None
        if got:
            direction = {"1": "ROSE", "0": "FELL"}.get(got[0]["outcome"]) if got[0]["state"] == "SETTLED" else None
        state = "Settled" if direction else ("Waiting" if key in committed else "No window")
        hit = None if direction is None else direction == "FELL"  # every read here is "avoid": advice against buying
        rid = natural_id("rr", {"cycle": body["cycle_id"], "candidate": body["candidate_id"]})
        plan = plans.get(rid)
        answer = "WAITING" if plan is None else (plan["outcome"] if plan["state"] == "SETTLED" else plan["state"])
        return (body["cycle_id"], body["symbol"], state, direction, hit, answer)

    want = sorted((second_way(b) for b in reads), key=lambda t: (planned[t[0]], t[1]))
    assert v.ryo is not None
    c.eq(
        "every read, oldest cycle first, with the state, direction and hit its window gives and its plan's answer",
        [(o.cycle_id, o.symbol, o.state, o.direction, o.hit, o.plan) for o in v.ryo.outcomes],
        want,
    )
    c.eq("the first cycle's reads are settled and scored", sum(1 for t in want if t[2] == "Settled"), 2)
    c.eq("and their plans settled", {t[5] for t in want if t[2] == "Settled"}, {"TARGET", "STOP"})
    c.eq(
        "the table of readings",
        v.ryo.table,
        (
            ("UP", "read as up", ("accumulate", "bullish", "constructive", "positive")),
            ("DOWN", "read as down", ("bearish", "negative")),
            ("NOT_LONG", "read as advice against buying", ("avoid", "cautious")),
            ("NONE", "no direction", ("neutral",)),
            ("UNREAD", "a word our table does not read", ()),
        ),
    )
    c.eq("and its version", v.ryo.table_version, "ryo-reading-1")
    c.eq(
        "the time of each cycle that has a read, in order",
        v.ryo.cycle_times,
        tuple((cid, planned[cid]) for cid in sorted({b["cycle_id"] for b in reads}, key=lambda x: planned[x])),
    )
    c.eq("more than one cycle read RYO", len(v.ryo.cycle_times) > 1, True)
    c.eq("the summary is the summary of the outcomes", (v.ryo.summary.reads, v.ryo.summary.scored), (len(reads), 2))
    try:
        v.ryo.table = ()  # type: ignore[misc]
        c.bad.append("RYO's record can be changed after it is made")
    except dataclasses.FrozenInstanceError:
        pass
    # the order of the outcomes is the cycle order, whatever order the reads sit in the ledger: swap the places of the reads and compare
    places = [i for i, r in enumerate(records) if r["type"] == "ryo_read"]
    swapped = list(records)
    for i, j in zip(places, reversed(places), strict=True):
        swapped[i] = records[j]
    assert places != list(reversed(places)), "the check needs more than one read"
    c.eq(
        "the same outcomes in the same order from reads stored in another order",
        mod.build(swapped, protocol, now_ms=now).ryo.outcomes,
        v.ryo.outcomes,
    )  # type: ignore[union-attr]
    # only the 24-hour directional window counts: a 1-hour settlement turned the other way changes nothing
    flipped = copy.deepcopy(records)
    for r in flipped:
        if (
            r["type"] == "settlement"
            and made[r["body"]["forecast_id"]]["horizon_ms"] == 3_600_000
            and made[r["body"]["forecast_id"]]["contract"] == "D"
        ):
            r["body"]["outcome"] = {"1": "0", "0": "1"}.get(r["body"]["outcome"], r["body"]["outcome"])
    c.eq("a 1-hour window is never a read's", mod.build(flipped, protocol, now_ms=now).ryo.outcomes, v.ryo.outcomes)
    c.eq(
        "the ledger has barrier calls on the same tokens, and they do not count either",
        any(f["contract"] == "B" for f in made.values()),
        True,
    )
    for label, change in (
        ("a read naming a candidate no cycle had", {"candidate_id": "cdnobody"}),
        ("a read naming a cycle the ledger does not hold", {"cycle_id": "cynowhere"}),
    ):
        bent = copy.deepcopy(records)
        target = next(r for r in bent if r["type"] == "ryo_read")
        target["body"].update(change)
        target["id"] = natural_id("rr", {"cycle": target["body"]["cycle_id"], "candidate": target["body"]["candidate_id"]})
        try:
            mod.build(bent, protocol, now_ms=now)
            c.bad.append(f"{label}: accepted")
        except mod.ViewError as exc:
            c.eq(label, str(exc), f"the read {target['id']} names a candidate no cycle of this ledger had")
    return c.bad


@functools.cache
def hours_ledger() -> tuple[list[dict[str, Any]], Any, int]:
    """A league run through eight hourly cycles: enough settled one-hour calls of the rules arm to be compared."""
    stack = ExitStack()
    atexit.register(stack.close)
    directory = Path(tempfile.mkdtemp(prefix="view-models-hours-"))
    stack.callback(shutil.rmtree, directory, ignore_errors=True)
    lg = stack.enter_context(league(directory, at_minute=30))
    scheduler.tick(lg.services, lg.config)
    for _ in range(8):
        lg.clock.jump(60 * 60)
        scheduler.tick(lg.services, lg.config)
    return scheduler.read_all(lg.services), lg.services.protocol, to_ms(lg.clock.now())


def check_comparisons(mod: types.ModuleType) -> list[str]:
    """The rules arm against the fixed half at one hour, worked out a second way from the raw records."""
    from hoshitori import anytime

    c = Case(mod)
    records, protocol, now = hours_ledger()
    v = mod.build(records, protocol, now_ms=now)
    assert v.comparisons is not None
    made = {r["id"]: r["body"] for r in raw(records, "forecast")}
    from hoshitori.timeutil import parse_rfc3339

    origins = {
        r["body"]["forecast_id"]: to_ms(parse_rfc3339(r["body"]["origin"])) for r in raw(records, "forecast_state") if "origin" in r["body"]
    }
    settled = [
        (origins[s["body"]["forecast_id"]] + made[s["body"]["forecast_id"]]["horizon_ms"], made[s["body"]["forecast_id"]], s["body"])
        for s in raw(records, "settlement")
        if s["body"]["state"] == "SETTLED"
        and made[s["body"]["forecast_id"]]["contract"] == "D"
        and made[s["body"]["forecast_id"]]["horizon_ms"] == 3_600_000
        and made[s["body"]["forecast_id"]]["arm_id"] == "L0"
    ]
    settled.sort(key=lambda t: (t[0], t[1]["cycle_id"], t[1]["asset_id"]))
    diffs = []
    for _, f, s in settled:
        p = min(max(Decimal(f["p"]), Decimal("0.01")), Decimal("0.99"))
        o = 1 if s["outcome"] == "1" else 0
        diffs.append(float((Decimal("0.5") - o) ** 2 - (p - o) ** 2))
    line = next(x for x in v.comparisons.lines if (x.first, x.second, x.hours) == ("L0", "HALF", 1))
    c.eq("enough calls settled to be shown", len(diffs) >= 10, True)
    c.eq("the shared calls are the rules arm's settled one-hour calls", line.shared, len(diffs))
    last = anytime.sequence(diffs)[-1]
    c.eq(
        "the mean and interval of their Brier differences, to four places",
        (line.mean, line.lower, line.upper),
        tuple("0.0000" if f"{x:.4f}" == "-0.0000" else f"{x:.4f}" for x in (last.mean, last.lower, last.upper)),
    )
    state = "First better on average" if last.lower > 0 else "Second better on average" if last.upper < 0 else "Not distinguishable yet"
    c.eq("and its state", line.state, state)
    c.eq("the document's settings", (v.comparisons.alpha, v.comparisons.v_opt, v.comparisons.min_shown), ("0.05", "10.0", 10))
    c.eq("one line a pair a horizon", len(v.comparisons.lines), len(protocol.horizons_hours) * 1)
    planned = {r["body"]["cycle_id"]: r["body"]["planned_for"] for r in raw(records, "cycle_state") if r["body"]["state"] == "PLANNED"}
    from hoshitori.timeutil import parse_rfc3339 as when

    four = [
        st
        for st in raw(records, "settlement")
        if st["body"]["state"] == "SETTLED"
        and made[st["body"]["forecast_id"]]["contract"] == "D"
        and made[st["body"]["forecast_id"]]["horizon_ms"] == 4 * 3_600_000
        and made[st["body"]["forecast_id"]]["arm_id"] == "L0"
        and when(planned[made[st["body"]["forecast_id"]]["cycle_id"]]).hour % 4 == 0
    ]
    line4 = next(x for x in v.comparisons.lines if (x.first, x.second, x.hours) == ("L0", "HALF", 4))
    every4 = sum(1 for st in raw(records, "settlement") if made[st["body"]["forecast_id"]]["horizon_ms"] == 4 * 3_600_000)
    c.eq("at four hours only the cycles planned at an hour divisible by four count", line4.shared, len(four))
    c.eq("and there were other four-hour calls left out", every4 > len(four), True)
    for obj in (v.comparisons, line):
        try:
            obj.state = "x"  # type: ignore[misc]
            c.bad.append(f"{type(obj).__name__} can be changed after it is made")
        except AttributeError:
            pass
    c.eq(
        "a negative zero is written without its sign",
        (mod._four(-0.00001), mod._four(0.00001), mod._four(None), mod._four(-0.5)),
        ("0.0000", "0.0000", None, "-0.5000"),
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_comparisons,
        check_ryo,
        check_gap_and_strata,
        check_gap_cuts,
        check_receipt_age,
        check_more_scores,
        check_bounds,
        check_known_outcomes,
        check_climate_history,
        check_missed_cycles,
        check_constants,
        check_words,
        check_json,
        check_rows,
        check_receipts,
        check_unread,
        check_record,
        check_today,
        check_endings,
        check_model_arms,
        check_shapes_and_edges,
        check_changes,
        check_barrier_split,
        check_headline,
        check_due,
        check_today_changes,
        check_compared_with,
    ):
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
