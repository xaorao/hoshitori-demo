"""Checks of the replay of a committed cycle (``hoshitori.replay``), runnable against a mutant.

A real cycle is run (the world of ``league_models_checks``: the real registry, collector, snapshot, ledger and commitment, the rules arm,
and the three model arms in front of a scripted model whose answers are kept as captures); the replay is then asked about it with the
ledger's records and those captures only.
"""

from __future__ import annotations

import copy
import json
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hoshitori import league, model_client
from hoshitori.capture import RawCapture
from tests.league_checks import World
from tests.league_models_checks import CONTROL, DEBATE, SINGLE, Model, models, world
from tests.rules_arm_checks import rates

HOURS = 3_600_000


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str) -> None:
        try:
            call()
        except self.mod.ReplayError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def answers(captures: list[RawCapture], skip: set[str] | None = None) -> model_client.ReplayFetcher:
    fetcher = model_client.ReplayFetcher()
    for capture in captures:
        if skip is None or capture.capture_id not in skip:
            fetcher.add(capture)
    return fetcher


def inputs(mod: types.ModuleType, w: World, captures: list[RawCapture], **over: Any) -> Any:
    values: dict[str, Any] = {
        "protocol": w.protocol,
        "mappings": {"L0": w.mapping},
        "barrier_mappings": {},
        "answers": answers(captures),
        "l1": SINGLE,
        "l2": DEBATE,
        "l1x": CONTROL,
    }
    values.update(over)
    return mod.Inputs(**values)


def ran(root: Path, **kw: Any) -> tuple[World, Model, Any]:
    w = world(root, **kw.pop("world", {}))
    model = Model(w)
    for key, value in kw.pop("model", {}).items():
        getattr(model, key).update(value) if isinstance(value, dict | set) else setattr(model, key, value)
    out = w.run(league, models=models(model))
    return w, model, out


def by(report: Any) -> dict[tuple[str, int, str], Any]:
    return {(s.arm_id, s.hours, s.symbol): s for s in report.slots}


def check_happy(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w, model, out = ran(root)
    report = mod.replay_cycle(w.records(), out.cycle_id, inputs(mod, w, model.captures))
    c.eq("the cycle", report.cycle_id, out.cycle_id)
    c.eq("every slot is replayed: the rules arm, the single judgment, the debate and the control", len(report.slots), 16)
    c.eq("and every one is the committed one", {s.status.value for s in report.slots}, {"MATCH"})
    c.eq("no detail is given for a match", {s.detail for s in report.slots}, {None})
    c.eq("the report is clean", (report.clean, report.count(mod.Status.MATCH), report.count(mod.Status.DIFFERENT)), (True, 16, 0))
    c.eq(
        "by arm",
        {a: sum(1 for s in report.slots if s.arm_id == a) for a in ("L0", "L1", "L2", "L1x")},
        {"L0": 6, "L1": 6, "L2": 2, "L1x": 2},
    )
    c.eq(
        "the slots are named by what they are",
        {(s.arm_id, s.symbol, s.hours, s.contract) for s in report.slots if s.arm_id == "L2"},
        {("L2", "BTC", 4, "D"), ("L2", "ETH", 4, "D")},
    )
    c.eq("the forecast id is the cycle's own", {s.forecast_id for s in report.slots} == {s.forecast_id for s in out.slots}, True)
    return c.bad


def check_other_endings(mod: types.ModuleType, root: Path) -> list[str]:
    from hoshitori import arm_l1

    c = Case(mod)
    # a model that is down: nothing was answered when the cycle ran, and nothing is stored, and that is consistent
    w, model, out = ran(root, model={"gone": {(arm_l1.STAGE, 4)}})
    report = mod.replay_cycle(w.records(), out.cycle_id, inputs(mod, w, model.captures))
    c.eq("a slot that failed for want of a model replays as the same failure", (report.clean, report.count(mod.Status.MATCH)), (True, 16))
    # a model that abstains
    w, model, out = ran(
        root / "n",
        model={"decision": {"decision": "NO_CALL", "no_call_reason": "EVIDENCE_CONFLICT_UNRESOLVED", "p": None, "invalidations": []}},
    )
    report = mod.replay_cycle(w.records(), out.cycle_id, inputs(mod, w, model.captures))
    c.eq("an abstention replays as the same abstention", (report.clean, report.count(mod.Status.MATCH)), (True, 16))
    # a late answer
    w, model, out = ran(root / "l", model={"late": {(arm_l1.STAGE, 1): 241}})
    report = mod.replay_cycle(w.records(), out.cycle_id, inputs(mod, w, model.captures))
    c.eq(
        "a late answer was discarded, so there is nothing to compare",
        {(s.arm_id, s.hours, s.status.value, s.detail) for s in report.slots if s.status is not mod.Status.MATCH},
        {("L1", 1, "NOT_COMPARED", "the answer was discarded as late")},
    )
    c.eq("the report with those is clean", report.clean, True)
    return c.bad


def check_tampering(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w, model, out = ran(root)
    records = w.records()
    base = inputs(mod, w, model.captures)
    # a stored answer that is gone
    next(x for x in model.captures if json.loads(x.request["arguments"]["messages"][1]["content"].split("Citable")[0] and "{}") is not None)
    first_l1 = next(x for x in model.captures if x.request["arguments"]["response_format"]["json_schema"]["name"] == "l1_judgment")
    report = mod.replay_cycle(records, out.cycle_id, inputs(mod, w, model.captures, answers=answers(model.captures, {first_l1.capture_id})))
    gone = [s for s in report.slots if s.status is mod.Status.NOT_RECONSTRUCTIBLE]
    c.eq("a stored answer that is gone: the slots that needed it", len(gone) >= 1, True)
    c.eq("they say so", {s.detail for s in gone}, {"1 stored answer(s) to requests that were answered are gone"})
    c.eq("and the others still match", (len(report.slots), report.count(mod.Status.MATCH)), (16, 16 - len(gone)))
    c.eq("the slot is the one that is named", {(s.contract, s.arm_id) for s in gone} <= {("D", "L1"), ("D", "L2"), ("D", "L1x")}, True)
    # an answer that was changed
    body = json.loads(first_l1.body)
    content = json.loads(body["choices"][0]["message"]["content"])
    content["p"] = 0.71
    body["choices"][0]["message"]["content"] = json.dumps(content)
    edited = RawCapture(
        first_l1.capture_id,
        first_l1.source,
        first_l1.request,
        first_l1.status,
        first_l1.headers,
        json.dumps(body).encode(),
        first_l1.t_send,
        first_l1.received_at,
    )
    swapped = [edited if x.capture_id == first_l1.capture_id else x for x in model.captures]
    report = mod.replay_cycle(records, out.cycle_id, inputs(mod, w, swapped))
    different = [s for s in report.slots if s.status is mod.Status.DIFFERENT]
    c.eq("an answer that was changed: the probability differs", {s.detail for s in different}, {"differs in the probability"})
    c.eq("in the slots that used it", len(different) >= 1, True)
    c.eq("and the report is not clean", report.clean, False)
    # another rules mapping
    other = rates(__import__("hoshitori.rules_arm", fromlist=["x"]), lambda h, s: (200, 100, 0))
    report = mod.replay_cycle(records, out.cycle_id, inputs(mod, w, model.captures, mappings={"L0": other}))
    c.eq(
        "another mapping than the one the cycle ran: the rules arm differs",
        {s.arm_id for s in report.slots if s.status is mod.Status.DIFFERENT},
        {"L0"},
    )
    c.eq(
        "and says why",
        {s.detail for s in report.slots if s.arm_id == "L0" and s.status is mod.Status.DIFFERENT},
        {"differs in the probability, the agent version the forecast names", "differs in the agent version the forecast names"},
    )
    # a run record that is gone
    for arm in ("L2", "L1x"):
        run_ids = [r["id"] for r in records if r["type"] == "arm_run" and r["body"]["arm"] == arm]
        fewer = [r for r in records if r["id"] not in run_ids[:1]]
        report = mod.replay_cycle(fewer, out.cycle_id, base)
        c.eq(
            f"a run record of {arm} that is gone: that slot cannot be rebuilt",
            [(s.arm_id, s.contract, s.status.value, s.detail) for s in report.slots if s.status is not mod.Status.MATCH],
            [(arm, "D", "NOT_RECONSTRUCTIBLE", "there is no run record of this slot")],
        )
        c.eq("and it is not counted twice", len(report.slots), 16)
    return c.bad


def check_refusals(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w, model, out = ran(root)
    records = w.records()
    base = inputs(mod, w, model.captures)
    arms = "the model arms given are those of the protocol, with their stored answers, no more and no fewer"
    c.refuses(
        "none of the model arms given",
        lambda: mod.replay_cycle(records, out.cycle_id, inputs(mod, w, model.captures, l1=None, l2=None, l1x=None)),
        arms,
    )
    c.refuses("one of them missing", lambda: mod.replay_cycle(records, out.cycle_id, inputs(mod, w, model.captures, l2=None)), arms)
    c.refuses("no stored answers", lambda: mod.replay_cycle(records, out.cycle_id, inputs(mod, w, model.captures, answers=None)), arms)
    c.refuses(
        "model arms for a protocol with none",
        lambda: mod.replay_cycle(records, out.cycle_id, inputs(mod, World(root / "none"), model.captures)),
        arms,
    )
    from hoshitori import arm_l1

    c.refuses(
        "another model than the one pinned",
        lambda: mod.replay_cycle(records, out.cycle_id, inputs(mod, w, model.captures, l1=arm_l1.Config("another"))),
        "the model arm L1 is not the version the protocol pins",
    )
    c.refuses(
        "a cycle that the ledger does not hold",
        lambda: mod.replay_cycle(records, "cy-none", base),
        "the ledger holds no snapshot and manifest of this cycle",
    )
    c.refuses(
        "a ledger with no records",
        lambda: mod.replay_cycle([], out.cycle_id, base),
        "the ledger holds no snapshot and manifest of this cycle",
    )
    fewer = [r for r in records if r["type"] != "manifest"]
    c.refuses(
        "a ledger with no manifest",
        lambda: mod.replay_cycle(fewer, out.cycle_id, base),
        "the ledger holds no snapshot and manifest of this cycle",
    )
    tampered = copy.deepcopy(records)
    victim = next(r for r in tampered if r["type"] == "evidence")
    victim["body"]["fields"]["last_close"] = "1"
    says = f"the snapshot of the cycle cannot be rebuilt: {victim['id']} is not the record the snapshot names"
    c.refuses("an evidence record that was changed", lambda: mod.replay_cycle(tampered, out.cycle_id, base), says)
    lacking = [r for r in records if r["id"] != victim["id"]]
    c.refuses(
        "an evidence record that is gone",
        lambda: mod.replay_cycle(lacking, out.cycle_id, base),
        f"the snapshot of the cycle cannot be rebuilt: the evidence record {victim['id']} was not given",
    )
    return c.bad


def check_rules_abstention(mod: types.ModuleType, root: Path) -> list[str]:
    from hoshitori import rules_arm

    c = Case(mod)
    from tests.league_checks import before_the_schedule

    thin = before_the_schedule(rates(rules_arm, lambda h, s: (50, 25, 0)))
    w, model, out = ran(root, world={"mapping": thin})
    c.eq("the rules arm abstained in this cycle", {s.fate.value for s in out.slots if s.arm_id == "L0"}, {"NO_CALL"})
    report = mod.replay_cycle(w.records(), out.cycle_id, inputs(mod, w, model.captures))
    c.eq(
        "and its abstentions replay as the same abstentions",
        (report.clean, report.count(mod.Status.MATCH), len(report.slots)),
        (True, 16, 16),
    )
    return c.bad


def check_barrier(mod: types.ModuleType, root: Path) -> list[str]:
    from tests.commit_checks import TickClock
    from tests.league_checks import NOW, barrier_map, barrier_world

    c = Case(mod)
    bm = barrier_map()
    w = barrier_world(root / "b", bm, ledger_clock=TickClock(NOW, 0.001))
    out = w.run(league, barrier_mappings={"L0": bm})
    plain = inputs(mod, w, [], barrier_mappings={"L0": bm}, answers=None, l1=None, l2=None, l1x=None)
    report = mod.replay_cycle(w.records(), out.cycle_id, plain)
    c.eq("a cycle with barrier slots: twelve replayed", (len(report.slots), report.count(mod.Status.MATCH)), (12, 12))
    c.eq(
        "six of each contract",
        sorted((s.contract, s.hours) for s in report.slots),
        sorted([(k, h) for k in "DB" for h in (1, 4, 24) for _ in (0, 1)]),
    )
    other = barrier_map(target=10, stop=50, timeout=40)
    report = mod.replay_cycle(
        w.records(), out.cycle_id, inputs(mod, w, [], barrier_mappings={"L0": other}, answers=None, l1=None, l2=None, l1x=None)
    )
    different = [s for s in report.slots if s.status is mod.Status.DIFFERENT]
    c.eq("another barrier mapping differs in the barrier slots only", {(s.contract, s.arm_id) for s in different}, {("B", "L0")})
    c.eq("and says what", {s.detail for s in different}, {"differs in the probability, the agent version the forecast names"})
    c.eq("the directional slots are untouched", {s.status.value for s in report.slots if s.contract == "D"}, {"MATCH"})
    return c.bad


def _history(*steps: Any) -> Any:
    from hoshitori import lifecycle

    history = lifecycle.begin("fc", "D", HOURS, 0)
    for step in steps:
        history = lifecycle.advance(history, step)
    return history


def check_compare(mod: types.ModuleType, root: Path) -> list[str]:
    from hoshitori import forecast, lifecycle
    from tests.commit_checks import TickClock  # noqa: F401

    c = Case(mod)
    S = mod.Status
    w, _model, _out = ran(root)
    forecasts = [forecast.from_record(r) for r in w.records("forecast")]
    made = next(f for f in forecasts if f.common.arm_id == "L1")

    def decided(f: Any = made, **over: Any) -> Any:
        values: dict[str, Any] = {
            "verdict": "CALL",
            "reason": None,
            "p": (f.p,),
            "atr": None,
            "evidence_ids": tuple(i for i, _ in f.common.evidence),
            "observation_set_hash": f.common.observation_set_hash,
            "invalidation": f.common.invalidation,
            "version": f.common.agent_version_hash,
        }
        values.update(over)
        return mod._Decided(**values)

    version = made.common.agent_version_hash
    c.eq("a decision that is the forecast has no differences", mod._differences(made, decided(), version), [])
    other_hash = "0" * 64
    for label, over, wants in (
        ("another probability", {"p": (made.p / 2,)}, ["the probability"]),
        ("another invalidation", {"invalidation": ("x below 1",)}, ["the invalidation"]),
        ("another observation set", {"observation_set_hash": other_hash}, ["the observation set"]),
        ("other evidence", {"evidence_ids": ("evaaaaaaaaaaaaaaaaaaaaaaaaaa",)}, ["the evidence cited"]),
        ("evidence in another order", {"evidence_ids": tuple(reversed([i for i, _ in made.common.evidence]))}, ["the evidence cited"]),
        ("another version in the decision", {"version": other_hash}, ["the agent version"]),
        ("no version in the decision", {"version": None}, []),
    ):
        c.eq(
            f"{label} is named",
            mod._differences(made, decided(**over), version),
            wants if len(made.common.evidence) > 1 or label != "evidence in another order" else [],
        )
    c.eq(
        "a forecast of another version than the arm's",
        mod._differences(made, decided(), other_hash),
        ["the agent version the forecast names"],
    )
    c.eq(
        "several differences are all named, in order",
        mod._differences(made, decided(p=(made.p / 2,), invalidation=("x",), observation_set_hash=other_hash), version),
        ["the probability", "the invalidation", "the observation set"],
    )
    # a barrier forecast
    from tests.commit_checks import TickClock as Tick
    from tests.league_checks import NOW, barrier_map, barrier_world

    bm = barrier_map()
    bw = barrier_world(root / "b", bm, ledger_clock=Tick(NOW, 0.001))
    bw.run(league, barrier_mappings={"L0": bm})
    barrier = next(forecast.from_record(r) for r in bw.records("forecast") if r["body"]["contract"] == "B")
    want_p = (barrier.p_target, barrier.p_stop, barrier.p_timeout)

    def barrier_decided(**over: Any) -> Any:
        values: dict[str, Any] = {
            "verdict": "CALL",
            "reason": None,
            "p": want_p,
            "atr": barrier.atr,
            "evidence_ids": tuple(i for i, _ in barrier.common.evidence),
            "observation_set_hash": barrier.common.observation_set_hash,
            "invalidation": barrier.common.invalidation,
            "version": None,
        }
        values.update(over)
        return mod._Decided(**values)

    bv = barrier.common.agent_version_hash
    c.eq("a barrier decision that is the forecast", mod._differences(barrier, barrier_decided(), bv), [])
    c.eq("another ATR", mod._differences(barrier, barrier_decided(atr=barrier.atr + 1), bv), ["the ATR"])
    c.eq(
        "another probability of the target",
        mod._differences(barrier, barrier_decided(p=(want_p[0] / 2, want_p[1], want_p[2])), bv),
        ["the probability"],
    )
    c.eq(
        "a directional probability for a barrier forecast",
        mod._differences(barrier, barrier_decided(p=(want_p[0],)), bv),
        ["the probability"],
    )
    # the comparison with a slot's history
    fid = "fc"
    ok = {fid: _history(lifecycle.Step(lifecycle.State.CREATED, 1))}

    def compare(history: Any, got: Any, committed: Any = None, *, rules: bool = False) -> Any:
        histories = {} if history is None else {fid: history}
        return mod._compare(fid, "L1", "BTC", 1, "D", histories, {} if committed is None else {fid: committed}, got, version, rules=rules)

    def blank(**over: Any) -> Any:
        return decided(made, p=None, evidence_ids=(), invalidation=(), **over)

    r = compare(None, decided())
    c.eq("no history at all", (r.status, r.detail), (S.NOT_RECONSTRUCTIBLE, "there is no record of this slot"))
    r = compare(ok[fid], decided())
    c.eq("a history that has not ended", (r.status, r.detail), (S.NOT_RECONSTRUCTIBLE, "the slot has no ending in the ledger"))
    c.eq("the identity of the slot", (r.forecast_id, r.arm_id, r.symbol, r.hours, r.contract), ("fc", "L1", "BTC", 1, "D"))
    frozen = lifecycle.Step(lifecycle.State.EVIDENCE_FROZEN, 2)
    created = lifecycle.Step(lifecycle.State.CREATED, 1)
    late = _history(created, frozen, lifecycle.Step(lifecycle.State.FAILED_LATE, 3, reason="FINISHED_AFTER_CUTOFF"))
    r = compare(late, decided())
    c.eq("a late answer is not compared", (r.status, r.detail), (S.NOT_COMPARED, "the answer was discarded as late"))
    level = forecast.Level.C3
    decided_step = lifecycle.Step(lifecycle.State.DECIDED, 3)

    def abstained(reason: str) -> Any:
        return _history(created, frozen, decided_step, lifecycle.Step(lifecycle.State.NO_CALL, 4, reason=reason, level=level))

    r = compare(abstained("BELOW_MINIMUM_EDGE"), blank(verdict="NO_CALL", reason="BELOW_MINIMUM_EDGE"))
    c.eq("the same abstention", (r.status, r.detail), (S.MATCH, None))
    r = compare(abstained("BELOW_MINIMUM_EDGE"), blank(verdict="NO_CALL", reason="OUT_OF_SCOPE"))
    c.eq(
        "another reason",
        (r.status, r.detail),
        (S.DIFFERENT, "the cycle abstained (BELOW_MINIMUM_EDGE) and the replay says NO_CALL OUT_OF_SCOPE"),
    )
    r = compare(abstained("BELOW_MINIMUM_EDGE"), decided())
    c.eq(
        "a call where the cycle abstained",
        (r.status, r.detail),
        (S.DIFFERENT, "the cycle abstained (BELOW_MINIMUM_EDGE) and the replay says CALL None"),
    )
    r = compare(abstained("OUT_OF_SCOPE"), blank(verdict="NO_CALL", reason="UNMAPPED"), rules=True)
    c.eq("a rules arm abstains as out of scope whatever its own reason", (r.status, r.detail), (S.MATCH, None))
    r = compare(abstained("BELOW_MINIMUM_EDGE"), blank(verdict="NO_CALL", reason="UNMAPPED"), rules=True)
    c.eq("and any other reason is not that", r.status, S.DIFFERENT)
    r = compare(abstained("OUT_OF_SCOPE"), blank(verdict="FAILED", reason="UNMAPPED"), rules=True)
    c.eq("an abstention is not a failure", r.status, S.DIFFERENT)
    for state, verdict in ((lifecycle.State.FAILED_EVIDENCE, "FAILED_EVIDENCE"), (lifecycle.State.FAILED, "FAILED")):
        history = _history(created, frozen, lifecycle.Step(state, 3, reason="NO_FEATURES"))
        r = compare(history, blank(verdict=verdict, reason="NO_FEATURES"))
        c.eq(f"{verdict} with the same reason", (r.status, r.detail), (S.MATCH, None))
        r = compare(history, blank(verdict=verdict, reason="OTHER"))
        c.eq(
            f"{verdict} with another reason",
            (r.status, r.detail),
            (S.DIFFERENT, f"the cycle ended {state.value} (NO_FEATURES) and the replay says {verdict} OTHER"),
        )
        other_verdict = "FAILED" if verdict == "FAILED_EVIDENCE" else "FAILED_EVIDENCE"
        r = compare(history, blank(verdict=other_verdict, reason="NO_FEATURES"))
        c.eq(f"{verdict} is not {other_verdict}", r.status, S.DIFFERENT)
        r = compare(history, decided())
        c.eq(f"{verdict} where the replay calls", r.status, S.DIFFERENT)
    # a forecast that was committed
    r = compare(ok[fid], decided(), made)
    c.eq("a forecast that is replayed whole", (r.status, r.detail), (S.MATCH, None))
    r = compare(ok[fid], blank(verdict="NO_CALL", reason="BELOW_MINIMUM_EDGE"), made)
    c.eq(
        "a forecast where the replay abstains",
        (r.status, r.detail),
        (S.DIFFERENT, "the cycle committed a forecast and the replay says NO_CALL"),
    )
    r = compare(ok[fid], decided(invalidation=("x",), p=(made.p / 2,)), made)
    c.eq("a forecast that differs", (r.status, r.detail), (S.DIFFERENT, "differs in the probability, the invalidation"))
    return c.bad


def check_report(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    slot = lambda status: mod.SlotReplay("f", "L0", "BTC", 1, "D", status, None)  # noqa: E731
    S = mod.Status
    c.eq("statuses", [x.value for x in S], ["MATCH", "DIFFERENT", "NOT_RECONSTRUCTIBLE", "NOT_COMPARED"])
    c.eq("clean with matches and a late one", mod.Report("c", (slot(S.MATCH), slot(S.NOT_COMPARED))).clean, True)
    c.eq("not clean with a difference", mod.Report("c", (slot(S.MATCH), slot(S.DIFFERENT))).clean, False)
    c.eq("not clean with a slot that cannot be rebuilt", mod.Report("c", (slot(S.NOT_RECONSTRUCTIBLE),)).clean, False)
    c.eq("an empty report is clean", mod.Report("c", ()).clean, True)
    c.eq("counts", mod.Report("c", (slot(S.MATCH), slot(S.MATCH), slot(S.DIFFERENT))).count(S.MATCH), 2)
    for cls in (mod.Inputs, mod.SlotReplay, mod.Report, mod._Decided):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    c.eq("the name the lifecycle gives a rules arm's abstention", mod.RULES_NO_CALL, "OUT_OF_SCOPE")
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    import tempfile

    bad: list[str] = []
    for check in (
        check_happy,
        check_other_endings,
        check_tampering,
        check_refusals,
        check_rules_abstention,
        check_barrier,
        check_compare,
        check_report,
    ):
        try:
            if check is check_report:
                found = check(mod)
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    found = check(mod, Path(tmp))
            bad += [f"{check.__name__}: {message}" for message in found]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}")
    return bad
