"""Checks of the model arms inside a cycle (``hoshitori.league`` with ``hoshitori.model_arms``), runnable against a mutant.

The world is that of ``league_checks`` (the real registry, collector, snapshot, ledger and commitment) with the three model arms added
and a scripted model in front of the real client. What is checked: which slots exist, what each ends in, what is committed with them,
and what a late, a failed and a silent model does to the cycle.
"""

from __future__ import annotations

import json
import re
import threading
import types
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from hoshitori import arm_l1, arm_l1x, arm_l2, arm_run_record, forecast, lifecycle, model_arms, model_call_record, model_client
from hoshitori.capture import RawCapture
from hoshitori.gateway import Call, Outcome
from hoshitori.timeline import to_ms
from tests.collect_checks import Reply
from tests.commit_checks import TickClock
from tests.league_checks import NOW, SLOT, World, _strip, histories

NOW_MS = to_ms(NOW)

IDS = re.compile(r"ev[a-z2-7]{26}")
HORIZON = re.compile(r"Horizon: (\d+) hour")
SINGLE = arm_l1.Config("m")
DEBATE = arm_l2.Config(SINGLE, SINGLE, SINGLE, 100_000)
CONTROL = arm_l1x.Config(arm_l1.Config("m", temperature="0.7"))
HOURS = ((1, 200_000), (4, 600_000), (24, 700_000))
ARMS = (
    ("L1", arm_l1.agent_version_hash(SINGLE), HOURS),
    ("L1x", arm_l1x.agent_version_hash(CONTROL), ((4, 780_000),)),
    ("L2", arm_l2.agent_version_hash(DEBATE), ((4, 700_000),)),
)


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
        except self.mod.LeagueError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


class Model:
    """A scripted model: it reads the ids the prompt offers and answers with the first of them, in the shape its stage asks for."""

    def __init__(self, world: World | None = None, *, p: float = 0.62) -> None:
        self.world, self.p = world, p
        self.lock = threading.Lock()
        self.n = 0
        self.seen: list[tuple[str, dict[str, Any]]] = []
        self.late: dict[tuple[str, int], int] = {}  # (stage, hours) -> seconds the clock jumps to when this is asked
        self.gone: set[tuple[str, int]] = set()
        self.captures: list[RawCapture] = []  # every answer given, as the gateway would have stored it
        self.decision: dict[str, Any] = {}

    def __call__(self, args: Any, stage: str, timeout_s: float) -> Any:
        text = "\n".join(m["content"] for m in args["messages"])
        hours = int(HORIZON.search(text).group(1))  # type: ignore[union-attr]
        with self.lock:
            self.n += 1
            number = self.n
            self.seen.append((stage, args))
        if self.world is not None and (stage, hours) in self.late:
            self.world.clock.set(NOW + timedelta(seconds=self.late[(stage, hours)]))
        if (stage, hours) in self.gone:
            return model_client.Gone(model_client.Outcome.UNAVAILABLE, "down")
        ident = sorted(set(IDS.findall(text)))[0]
        if stage in (arm_l2.STAGES["bull"], arm_l2.STAGES["bear"]):
            body: dict[str, Any] = {
                "claim": f"{stage} claim",
                "support": [ident] if stage == arm_l2.STAGES["bull"] else [],
                "oppose": [] if stage == arm_l2.STAGES["bull"] else [ident],
                "uncertainty": "thin",
                "would_change_if": "a close below the last",
            }
        else:
            body = {
                "decision": "CALL",
                "p": self.p,
                "invalidations": [{"field": "last_close", "op": "below", "level": 1}],
                "cites": [ident],
                "assertions": [],
                "summary": f"seed {args.get('seed')} call {number}",
            }
            body.update(self.decision)
            body = {k: v for k, v in body.items() if v is not None}
        capture = RawCapture(
            f"cap-{number:032x}",
            "model-t",
            {"operation": "chat", "arguments": args},
            200,
            {},
            json.dumps(
                {
                    "id": "c",
                    "model": "fake/model",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(body)}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
                }
            ).encode(),
            NOW,
            NOW + timedelta(seconds=1),
        )
        with self.lock:
            self.captures.append(capture)
        return capture


def models(model: Model, **over: Any) -> Any:
    values: dict[str, Any] = {"client": model_client.ModelClient(model), "l1": SINGLE, "l2": DEBATE, "l1x": CONTROL}
    values.update(over)
    return model_arms.ModelArms(**values)


def world(root: Path, arms: Any = ARMS, **kw: Any) -> World:
    # one clock for the league and the ledger: a forecast is never committed earlier than it was decided, so a model that
    # answers late must move the ledger's time too. It ticks a millisecond at every read, so the records of a batch keep an order.
    tick = TickClock(NOW, 0.001)
    return World(root, clock=tick, ledger_clock=tick, protocol={"model_arms": arms, "snapshot_freshness_ms": 900_000}, **kw)


def slots_of(out: Any, arm: str, hours: int | None = None) -> list[Any]:
    return [s for s in out.slots if s.arm_id == arm and (hours is None or s.hours == hours)]


def check_happy_path(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = world(root)
    model = Model(w)
    out = w.run(mod, models=models(model))
    c.eq("committed", (out.state.value, out.reason), ("COMMITTED", None))
    counts = {arm: len(slots_of(out, arm)) for arm in ("L0", "L1", "L2", "L1x")}
    c.eq("the slots: the rules arm everywhere, the others where they are admitted", counts, {"L0": 6, "L1": 6, "L2": 2, "L1x": 2})
    c.eq(
        "an arm is only where it is admitted",
        {(s.arm_id, s.hours) for s in out.slots if s.arm_id in ("L2", "L1x")},
        {("L2", 4), ("L1x", 4)},
    )
    model_slots = [s for s in out.slots if s.arm_id != "L0"]
    c.eq("the model arms' forecasts", {(s.fate.value, s.level, s.reason, s.contract) for s in model_slots}, {("FORECAST", "C3", None, "D")})
    forecasts = {}
    for rec in w.records("forecast"):
        f = forecast.from_record(_strip(rec))
        forecasts[(f.common.arm_id, f.common.symbol, f.common.horizon_ms // 3_600_000)] = f
    c.eq(
        "one forecast per model slot",
        sorted(k for k in forecasts if k[0] != "L0"),
        sorted((s.arm_id, s.symbol, s.hours) for s in model_slots),
    )
    sample = forecasts[("L1", "BTC", 4)]
    c.eq("the probability the model gave", str(sample.p), "0.62")
    c.eq("and what the arm is pinned to", sample.common.agent_version_hash, w.protocol.model_arm_of("L1")[0])
    c.eq(
        "and the other arms",
        {a: forecasts[(a, "BTC", 4)].common.agent_version_hash for a in ("L2", "L1x")},
        {a: w.protocol.model_arm_of(a)[0] for a in ("L2", "L1x")},
    )
    c.eq("the evidence it cites is evidence of the snapshot", all(f.common.evidence for k, f in forecasts.items() if k[0] != "L0"), True)
    c.eq("no arm is shown an interpretation", {f.common.interpretation_visible for f in forecasts.values()}, {False})
    c.eq("the control's probability is the median of its samples", str(forecasts[("L1x", "BTC", 4)].p), "0.620000")
    kinds: dict[str, int] = {}
    for r in w.records():
        kinds[r["type"]] = kinds.get(r["type"], 0) + 1
    c.eq("a record for every call and one for every model slot", (kinds["model_call"], kinds["arm_run"]), (6 + 6 + 6, 10))
    calls = [model_call_record.from_record(r) for r in w.records("model_call")]
    c.eq("every call is readable", sorted({x.stage for x in calls}), sorted({arm_l1.STAGE, *arm_l2.STAGES.values()}))
    runs = {(b["arm"], b["candidate_id"], b["hours"]): b for b in (arm_run_record.from_record(r) for r in w.records("arm_run"))}
    c.eq("one run record per model slot", len(runs), 10)
    l2_run = next(b for k, b in runs.items() if k[0] == "L2")
    c.eq(
        "the debate: three calls, 180 tokens, not collapsed",
        (l2_run["calls"], l2_run["tokens"], l2_run["debate"]["collapsed"]),
        (3, 180, False),
    )
    l1x_run = next(b for k, b in runs.items() if k[0] == "L1x")
    c.eq(
        "the control is matched to the debate's spend",
        l1x_run["control"],
        {"target_tokens": 180, "samples": 3, "mismatch": False, "collapsed": False},
    )
    c.eq("and it took as many tokens as it was matched to", (l1x_run["calls"], l1x_run["tokens"]), (3, 180))
    c.eq("the single judgment: one call", {(b["calls"], b["tokens"]) for k, b in runs.items() if k[0] == "L1"}, {(1, 60)})
    c.eq(
        "each run names the cutoff of its arm and horizon",
        {(k[0], k[2], b["cutoff_ms"]) for k, b in runs.items()},
        {("L1", 1, 200_000), ("L1", 4, 600_000), ("L1", 24, 700_000), ("L2", 4, 700_000), ("L1x", 4, 780_000)},
    )
    order = [(b["candidate_id"], b["hours"]) for b in (arm_run_record.from_record(r) for r in w.records("arm_run"))]
    c.eq("the run records are in the order of candidate and horizon, whatever the order the groups finished in", order, sorted(order))
    _, by_slot = histories(w)
    c.eq(
        "every model slot ends awaiting its origin",
        {lifecycle.current_state(by_slot[s.forecast_id]).value for s in model_slots},
        {"AWAITING_ORIGIN"},
    )
    c.eq(
        "the debate's two sides were asked without each other",
        sum(
            1
            for stage, a in model.seen
            if stage in (arm_l2.STAGES["bull"], arm_l2.STAGES["bear"]) and "debate" in a["messages"][1]["content"]
        ),
        0,
    )
    c.eq(
        "and the judge read them as untrusted data",
        sum(1 for stage, a in model.seen if stage == arm_l2.STAGES["judge"] and "<<<DATA debate" in a["messages"][1]["content"]),
        2,
    )
    return c.bad


def check_late(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = world(root, watchlist=("BTC",))  # one candidate: the shared clock a late model moves would otherwise be the other candidate's
    model = Model(w)
    model.late[(arm_l1.STAGE, 1)] = 241  # the one-hour single judgment answers after its cutoff of 200 seconds
    out = w.run(mod, models=models(model))
    c.eq("the cycle is committed all the same", (out.state.value, out.reason), ("COMMITTED", None))
    one_hour = slots_of(out, "L1", 1)
    c.eq(
        "a late answer is a failed slot, with its reason",
        {(s.fate.value, s.reason, s.level) for s in one_hour},
        {("FAILED_LATE", "FINISHED_AFTER_CUTOFF", None)},
    )
    c.eq("the other horizons of the same arm are not touched", {s.fate.value for s in slots_of(out, "L1") if s.hours != 1}, {"FORECAST"})
    c.eq(
        "and no forecast was made for the late ones",
        sorted(
            f.common.horizon_ms
            for f in (forecast.from_record(_strip(r)) for r in w.records("forecast"))
            if f.common.arm_id == "L1" and f.common.symbol == "BTC"
        ),
        [4 * 3_600_000, 24 * 3_600_000],
    )
    runs = {(b["candidate_id"], b["hours"]): b for b in (arm_run_record.from_record(r) for r in w.records("arm_run")) if b["arm"] == "L1"}
    late = [b for (cand, hours), b in runs.items() if hours == 1]
    c.eq(
        "its run is recorded as late, with no probability and its spend",
        {(b["verdict"], b["reason"], b["final_p"], b["proposed_p"], b["calls"], b["tokens"]) for b in late},
        {("FAILED_LATE", "FINISHED_AFTER_CUTOFF", None, None, 1, 60)},
    )
    c.eq(
        "and the time it finished",
        {b["finished_at_ms"] for b in late},
        {NOW_MS + 241_000},
    )
    _, by_slot = histories(w)
    c.eq(
        "the history of a late slot ends in FAILED_LATE after the evidence was frozen",
        [t.step.state.value for t in by_slot[one_hour[0].forecast_id]],
        ["SCHEDULED", "CREATED", "EVIDENCE_FROZEN", "FAILED_LATE"],
    )
    # exactly at the cutoff is in time
    w2 = world(root / "edge", watchlist=("BTC",))
    edge = Model(w2)
    edge.late[(arm_l1.STAGE, 1)] = 200
    out2 = w2.run(mod, models=models(edge))
    c.eq("an answer at the cutoff is in time", {s.fate.value for s in slots_of(out2, "L1", 1)}, {"FORECAST"})
    return c.bad


def runs_of(w: World, arm: str) -> dict[tuple[str, int], dict[str, Any]]:
    return {(b["candidate_id"], b["hours"]): b for b in (arm_run_record.from_record(r) for r in w.records("arm_run")) if b["arm"] == arm}


def check_model_down(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = world(root)
    model = Model(w)
    model.gone.add((arm_l1.STAGE, 4))  # the single judgment's stage is also the stage of the control's samples
    out = w.run(mod, models=models(model))
    c.eq("the cycle is committed", (out.state.value, out.reason), ("COMMITTED", None))
    c.eq(
        "a model that is down is a failure with its reason",
        {(s.fate.value, s.reason, s.level) for s in slots_of(out, "L1", 4)},
        {("FAILED", "MODEL_UNAVAILABLE", None)},
    )
    c.eq(
        "and so is the control, whose samples all came to nothing",
        {(s.fate.value, s.reason) for s in slots_of(out, "L1x")},
        {("FAILED", "MODEL_UNAVAILABLE")},
    )
    c.eq(
        "the other horizons and the debate go on",
        {(s.arm_id, s.hours, s.fate.value) for s in out.slots if s.arm_id in ("L1", "L2") and (s.arm_id == "L2" or s.hours != 4)},
        {("L1", 1, "FORECAST"), ("L1", 24, "FORECAST"), ("L2", 4, "FORECAST")},
    )
    _, by_slot = histories(w)
    c.eq(
        "a failed slot's history",
        [t.step.state.value for t in by_slot[slots_of(out, "L1", 4)[0].forecast_id]],
        ["SCHEDULED", "CREATED", "EVIDENCE_FROZEN", "FAILED"],
    )
    failed = next(iter(runs_of(w, "L1").values()))
    failed = next(b for (cand, hours), b in runs_of(w, "L1").items() if hours == 4)
    c.eq(
        "the run record keeps what it cost",
        (failed["verdict"], failed["reason"], failed["final_p"], failed["calls"]),
        ("FAILED", "MODEL_UNAVAILABLE", None, 1),
    )
    c.eq(
        "the failing calls are in the ledger, with their outcome",
        {
            call.outcome.value
            for call in (model_call_record.from_record(r) for r in w.records("model_call"))
            if call.stage == arm_l1.STAGE and call.capture_id is None
        },
        {"UNAVAILABLE"},
    )
    return c.bad


def check_debate_down(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = world(root)
    model = Model(w)
    model.gone.add((arm_l2.STAGES["bull"], 4))
    out = w.run(mod, models=models(model))
    c.eq(
        "a debate with a side down is a failure",
        {(s.fate.value, s.reason) for s in slots_of(out, "L2")},
        {("FAILED", "MODEL_UNAVAILABLE")},
    )
    l2 = next(iter(runs_of(w, "L2").values()))
    c.eq("and the note names the side", l2["debate"], {"collapsed": False, "failed_role": "bull", "disagreement": None})
    c.eq("what the side that answered cost is counted", (l2["calls"], l2["tokens"]), (2, 60))
    c.eq(
        "the control is matched to what was spent, and says it did not match",
        {(b["control"]["target_tokens"], b["control"]["samples"], b["control"]["mismatch"]) for b in runs_of(w, "L1x").values()},
        {(60, 3, True)},
    )
    c.eq("and still forecasts", {s.fate.value for s in slots_of(out, "L1x")}, {"FORECAST"})
    # both sides down: nothing was spent, so there is nothing to match
    w2 = world(root / "both")
    both = Model(w2)
    both.gone.update({(arm_l2.STAGES["bull"], 4), (arm_l2.STAGES["bear"], 4)})
    out2 = w2.run(mod, models=models(both))
    c.eq(
        "a debate with nothing spent",
        {(s.fate.value, s.reason) for s in slots_of(out2, "L2")},
        {("FAILED", "MODEL_UNAVAILABLE")},
    )
    c.eq(
        "leaves the control with nothing to match: it fails, and is not run",
        {(s.fate.value, s.reason) for s in slots_of(out2, "L1x")},
        {("FAILED", "NO_MATCH_TARGET")},
    )
    ctl = next(iter(runs_of(w2, "L1x").values()))
    c.eq(
        "with no call made",
        (ctl["calls"], ctl["tokens"], ctl["control"]),
        (0, 0, {"target_tokens": 0, "samples": 0, "mismatch": False, "collapsed": False}),
    )
    return c.bad


def check_slow_debate(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = world(root, watchlist=("BTC",))  # one candidate: the shared clock a late model moves would otherwise be the other candidate's
    model = Model(w)
    model.late[(arm_l2.STAGES["judge"], 4)] = 701  # the debate ends after its cutoff of 700 seconds
    out = w.run(mod, models=models(model))
    c.eq(
        "a late debate is failed late",
        {(s.fate.value, s.reason) for s in slots_of(out, "L2")},
        {("FAILED_LATE", "FINISHED_AFTER_CUTOFF")},
    )
    ctl = next(iter(runs_of(w, "L1x").values()))
    c.eq("its spend still sets the control's size", ctl["control"]["target_tokens"], 180)
    c.eq("and the control, with 79 seconds left, runs", {s.fate.value for s in slots_of(out, "L1x")}, {"FORECAST"})
    # no time left at all: the clock is past the control's cutoff when the debate ends
    w2 = world(root / "none", watchlist=("BTC",))
    slow = Model(w2)
    slow.late[(arm_l2.STAGES["judge"], 4)] = 781
    out2 = w2.run(mod, models=models(slow))
    c.eq(
        "a control with no time left is not run",
        {(s.fate.value, s.reason) for s in slots_of(out2, "L1x")},
        {("FAILED_LATE", "FINISHED_AFTER_CUTOFF")},
    )
    ctl2 = next(iter(runs_of(w2, "L1x").values()))
    c.eq(
        "it made no call and says what it was to match",
        (ctl2["calls"], ctl2["control"]),
        (0, {"target_tokens": 180, "samples": 0, "mismatch": False, "collapsed": False}),
    )
    return c.bad


def check_no_call_arm(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = world(root)
    model = Model(w)
    model.decision = {"decision": "NO_CALL", "no_call_reason": "EVIDENCE_CONFLICT_UNRESOLVED", "p": None, "invalidations": []}
    out = w.run(mod, models=models(model))
    c.eq(
        "a model that abstains is an abstention, with its own reason and a level",
        {(s.fate.value, s.reason, s.level) for s in out.slots if s.arm_id != "L0"},
        {("NO_CALL", "EVIDENCE_CONFLICT_UNRESOLVED", "C3")},
    )
    _, by_slot = histories(w)
    mine = slots_of(out, "L1", 4)[0]
    c.eq(
        "its history",
        [t.step.state.value for t in by_slot[mine.forecast_id]],
        ["SCHEDULED", "CREATED", "EVIDENCE_FROZEN", "DECIDED", "NO_CALL"],
    )
    c.eq(
        "the reason is the one in the lifecycle's closed list",
        {t.step.reason for h in by_slot.values() for t in h if t.step.state is lifecycle.State.NO_CALL and t.step.reason != "OUT_OF_SCOPE"},
        {"EVIDENCE_CONFLICT_UNRESOLVED"},
    )
    c.eq(
        "no forecast was made for a model slot",
        {f.common.arm_id for f in (forecast.from_record(_strip(r)) for r in w.records("forecast"))},
        {"L0"},
    )
    run = next(iter(runs_of(w, "L1").values()))
    c.eq("its run", (run["verdict"], run["reason"], run["final_p"], run["gate"]), ("NO_CALL", "EVIDENCE_CONFLICT_UNRESOLVED", None, "NONE"))
    return c.bad


def check_evidence_missing(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)

    def script(call: Call, world_: World) -> Reply:
        if call.operation == "candles" and call.args["instrument"] == "ETHUSDT":
            return Reply(outcome=Outcome.FAILED, reason="UPSTREAM")
        return world_.default(call)

    w = world(root, script=script)
    model = Model(w)
    out = w.run(mod, models=models(model))
    c.eq(
        "a token without evidence is a failure of evidence for every model arm",
        {(s.arm_id, s.fate.value, s.reason) for s in out.slots if s.symbol == "ETH" and s.arm_id != "L0"},
        {
            ("L1", "FAILED_EVIDENCE", "MISSING_EVIDENCE"),
            ("L2", "FAILED_EVIDENCE", "MISSING_EVIDENCE"),
            ("L1x", "FAILED_EVIDENCE", "MISSING_EVIDENCE"),
        },
    )
    c.eq("and the model was not asked about it", sum(1 for _, a in model.seen if "Candidate: ETH" in a["messages"][1]["content"]), 0)
    c.eq("the others are forecasts", {s.fate.value for s in out.slots if s.symbol == "BTC" and s.arm_id != "L0"}, {"FORECAST"})
    return c.bad


def check_validation(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = world(root)
    model = Model(w)
    good = models(model)

    def run(**over: Any) -> Any:
        return mod.run_cycle(w.services(mod, **over), SLOT, since_ms=SLOT - 3_600_000)

    c.refuses("the protocol has model arms and none were given", lambda: run(), "the protocol has model arms and no model arms were given")
    c.refuses(
        "a model arm that is missing",
        lambda: run(models=models(model, l2=None, l1x=None)),
        "the model arms are those of the protocol, no more and no fewer",
    )
    plain = World(root / "x", ledger_clock=TickClock(NOW, 0.001))
    c.refuses(
        "a model arm the protocol does not pin",
        lambda: mod.run_cycle(plain.services(mod, models=good), SLOT, since_ms=SLOT - 3_600_000),
        "the model arms are those of the protocol, no more and no fewer",
    )
    c.refuses(
        "another model than the one pinned",
        lambda: run(models=models(model, l1=arm_l1.Config("another-model"))),
        "the model arm L1 is not the version the protocol pins",
    )
    c.refuses(
        "a number of workers of none",
        lambda: run(models=models(model, workers=0)),
        "the number of workers is a whole number from 1 to 64",
    )
    c.eq("and nothing was written for any of them", w.records(), [])
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    import tempfile

    bad: list[str] = []
    for check in (
        check_happy_path,
        check_late,
        check_model_down,
        check_debate_down,
        check_slow_debate,
        check_no_call_arm,
        check_evidence_missing,
        check_validation,
    ):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                bad += [f"{check.__name__}: {message}" for message in check(mod, Path(tmp))]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}")
    return bad
