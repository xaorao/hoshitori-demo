"""Checks of the model arms of one candidate and one horizon (``hoshitori.model_arms``), runnable against a mutant. The
same answers a provider or a model could send are also run through a whole cycle of the real league (``check_cycle_survives_any_answer``),
since that is where they used to end the cycle."""

from __future__ import annotations

import dataclasses
import json
import tempfile
import threading
import types
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from hoshitori import arm_l1, arm_l1x, arm_l2, arm_run_record, canonical, league, model_call_record, model_client, protocol, snapshot
from hoshitori.capture import RawCapture
from tests import league_models_checks
from tests.arm_l1_checks import reply as call_reply
from tests.arm_l2_checks import Case as BaseCase
from tests.arm_l2_checks import answer, side
from tests.protocol_checks import make
from tests.snapshot_checks import S, World

HOUR = 3_600_000
SINGLE = arm_l1.Config("m")
DEBATE = arm_l2.Config(SINGLE, SINGLE, SINGLE, 100_000)
CONTROL = arm_l1x.Config(arm_l1.Config("m", temperature="0.7"))
CUTOFFS = {"L1": ((1, 200_000), (4, 600_000), (24, 700_000)), "L2": ((4, 700_000),), "L1x": ((4, 780_000),)}


class Case(BaseCase):
    def refuses(self, label: str, call: Callable[[], object], says: str) -> None:
        try:
            call()
        except self.mod.ModelArmsError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


class Fetch:
    """Answers each stage from a function of how many times it was asked, notes the time allowed, and may move the clock."""

    def __init__(self, clock: list[int], w: World) -> None:
        self.clock, self.w = clock, w
        self.lock = threading.Lock()
        self.n = 0
        self.timeouts: dict[str, list[float]] = {}
        self.move: dict[str, int] = {}  # stage -> the time (ms after the snapshot) the clock is at once it has been asked
        self.gone: set[str] = set()
        self.decision: dict[str, Any] = {}
        self.asked: list[str] = []
        self.meet: threading.Barrier | None = None  # the first request of the single judgment and of the bull wait for each other
        self.met: set[str] = set()
        self.bull: tuple[list[str], list[str]] | None = None  # (supports, opposes) of the bull, when the check wants others
        self.bear: tuple[list[str], list[str]] | None = None

    def __call__(self, args: Any, stage: str, timeout_s: float) -> Any:
        with self.lock:
            self.n += 1
            number = self.n
            self.timeouts.setdefault(stage, []).append(timeout_s)
            self.asked.append(stage)
        if self.meet is not None and stage in (arm_l1.STAGE, arm_l2.STAGES["bull"]):
            with self.lock:
                first = stage not in self.met
                self.met.add(stage)
            if first:
                self.meet.wait(timeout=5)
        if stage in self.move:
            self.clock[0] = S + self.move[stage]
        if stage in self.gone:
            return model_client.Gone(model_client.Outcome.UNAVAILABLE, "down")
        if stage == arm_l2.STAGES["bull"]:
            for_, against = self.bull or ([self.w.btc_bars], [self.w.market])
            return answer(side(for_, against), number)
        if stage == arm_l2.STAGES["bear"]:
            for_, against = self.bear or ([self.w.market], [self.w.btc_bars])
            return answer(side(for_, against, "Down" if self.bear is None else "Up"), number)
        return answer(call_reply(self.w, **self.decision), number)


def models_for(fetch: Fetch, **over: Any) -> Any:
    values: dict[str, Any] = {"client": model_client.ModelClient(fetch), "l1": SINGLE, "l2": DEBATE, "l1x": CONTROL}
    values.update(over)
    return values


def pinned(mod: types.ModuleType, arms: dict[str, tuple[tuple[int, int], ...]] | None = None, **kw: Any) -> Any:
    chosen = CUTOFFS if arms is None else arms
    hashes = {
        "L1": arm_l1.agent_version_hash(SINGLE),
        "L2": arm_l2.agent_version_hash(DEBATE),
        "L1x": arm_l1x.agent_version_hash(CONTROL),
    }
    return make(protocol, snapshot_freshness_ms=900_000, model_arms=tuple(sorted((a, hashes[a], h) for a, h in chosen.items())), **kw)


def group(
    mod: types.ModuleType,
    fetch: Fetch,
    clock: list[int],
    w: World,
    hours: int = 4,
    arms: Any = None,
    candidate: tuple[str, str] = ("cd-btc", "tk-btc"),
    frozen: dict[str, Any] | None = None,
    **over: Any,
) -> Any:
    reader = snapshot.Reader(w.freeze(snapshot, **(frozen or {})), snapshot.Access.INDEPENDENT)
    given = mod.ModelArms(**models_for(fetch, **over))
    return mod.run_group(given, pinned(mod, arms), lambda: clock[0], reader, snapshot.Candidate(*candidate), "BTC", hours, S)


def fresh() -> tuple[list[int], World, Fetch]:
    clock = [S]
    w = World()
    return clock, w, Fetch(clock, w)


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "names",
        (mod.LATE_REASON, mod.NO_TARGET_REASON, mod.MIN_TIMEOUT_S, mod.ORDER),
        ("FINISHED_AFTER_CUTOFF", "NO_MATCH_TARGET", 1.0, ("L1", "L2", "L1x")),
    )
    for cls in (mod.ModelArms, mod.Slotted):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    c.eq("workers by default", mod.ModelArms(model_client.ModelClient(lambda *a: None)).workers, 4)
    return c.bad


def check_versions_and_pins(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    _, _, fetch = fresh()
    client = model_client.ModelClient(fetch)
    c.eq("no arm configured", mod.versions(mod.ModelArms(client)), {})
    c.eq("one", mod.versions(mod.ModelArms(client, l1=SINGLE)), {"L1": arm_l1.agent_version_hash(SINGLE)})
    c.eq(
        "all three",
        mod.versions(mod.ModelArms(client, l1=SINGLE, l2=DEBATE, l1x=CONTROL)),
        {"L1": arm_l1.agent_version_hash(SINGLE), "L2": arm_l2.agent_version_hash(DEBATE), "L1x": arm_l1x.agent_version_hash(CONTROL)},
    )
    full = mod.ModelArms(**models_for(fetch))
    p = pinned(mod)
    c.eq("the arms the protocol pins are accepted", mod.check_against(full, p), None)
    c.eq("no arms for a protocol with none", mod.check_against(None, make(protocol)), None)
    c.refuses(
        "none given for a protocol with arms",
        lambda: mod.check_against(None, p),
        "the protocol has model arms and no model arms were given",
    )
    c.refuses(
        "arms given for a protocol with none",
        lambda: mod.check_against(full, make(protocol)),
        "the model arms are those of the protocol, no more and no fewer",
    )
    c.refuses(
        "one fewer",
        lambda: mod.check_against(mod.ModelArms(client, l1=SINGLE, l2=DEBATE), p),
        "the model arms are those of the protocol, no more and no fewer",
    )
    c.refuses(
        "one more",
        lambda: mod.check_against(full, pinned(mod, {"L1": CUTOFFS["L1"]})),
        "the model arms are those of the protocol, no more and no fewer",
    )
    for arm, changed in (
        ("L1", replace(full, l1=arm_l1.Config("other"))),
        ("L2", replace(full, l2=replace(DEBATE, cap_tokens=99))),
        ("L1x", replace(full, l1x=replace(CONTROL, seed_base=7))),
    ):
        c.refuses(
            f"another {arm}",
            lambda changed=changed: mod.check_against(changed, p),
            f"the model arm {arm} is not the version the protocol pins",
        )
    for workers in (0, 65, True, 1.5):
        c.refuses(
            f"{workers!r} workers",
            lambda workers=workers: mod.check_against(replace(full, workers=workers), p),
            "the number of workers is a whole number from 1 to 64",
        )
    c.eq(
        "one worker and sixty-four are allowed",
        (mod.check_against(replace(full, workers=1), p), mod.check_against(replace(full, workers=64), p)),
        (None, None),
    )
    return c.bad


def check_group(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    clock, w, fetch = fresh()
    got = group(mod, fetch, clock, w)
    c.eq("the arms admitted to four hours, in order", [s.arm for s in got], ["L1", "L2", "L1x"])
    l1, l2, l1x = got
    c.eq("all call", (l1.verdict, l2.verdict, l1x.verdict), ("CALL", "CALL", "CALL"))
    c.eq(
        "the probability, proposed and final",
        [(s.proposed_p, s.p) for s in got],
        [(Decimal("0.62"), Decimal("0.62"))] * 2 + [(Decimal("0.620000"), Decimal("0.620000"))],
    )
    c.eq("no reason and the gate that did not act", {(s.reason, s.gate) for s in got}, {(None, "NONE")})
    c.eq("they cite the item they used", {s.evidence_ids for s in got}, {(w.btc_bars,)})
    c.eq(
        "they say what they are pinned to",
        [s.version for s in got],
        [arm_l1.agent_version_hash(SINGLE), arm_l2.agent_version_hash(DEBATE), arm_l1x.agent_version_hash(CONTROL)],
    )
    c.eq("the cutoffs of the horizon", [s.cutoff_ms for s in got], [600_000, 700_000, 780_000])
    c.eq("and the time they finished", {s.finished_ms for s in got}, {S})
    c.eq("calls and tokens", [(len(s.calls), s.tokens) for s in got], [(1, 60), (3, 180), (3, 180)])
    c.eq("a single judgment has neither note", (l1.debate, l1.control), (None, None))
    c.eq(
        "the debate's note",
        l2.debate,
        {
            "collapsed": False,
            "failed_role": None,
            "disagreement": {
                "bull_only": [],
                "bear_only": [],
                "shared": sorted([w.btc_bars, w.market]),
                "judge_on_bull": [],
                "judge_on_bear": [],
            },
        },
    )
    c.eq("the control's note", l1x.control, {"target_tokens": 180, "samples": 3, "mismatch": False, "collapsed": True})
    c.eq("and the debate has no control note nor the control a debate note", (l2.control, l1x.debate), (None, None))
    c.eq("the invalidation is kept", l1.invalidation, ("last_close below 78000",))
    clock1, w1, fetch1 = fresh()
    c.eq("an hour: only the single judgment is admitted", [s.arm for s in group(mod, fetch1, clock1, w1, hours=1)], ["L1"])
    clock2, w2, fetch2 = fresh()
    only = {"L1": ((1, 200_000),)}
    c.eq("and none is admitted where none is pinned", group(mod, fetch2, clock2, w2, hours=4, arms=only, l2=None, l1x=None), ())
    c.eq("and the model was not asked", fetch2.asked, [])
    return c.bad


def by_arm(got: Any) -> dict[str, Any]:
    return {s.arm: s for s in got}


def check_oversized_candidate(mod: types.ModuleType) -> list[str]:
    """A candidate with more items than one request can hold is not a defect that stops the cycle: every model arm of that group ends
    ``FAILED_EVIDENCE`` with the reason ``TOO_MANY_ITEMS`` before any model is asked, and the other candidates are untouched."""
    c = Case(mod)
    clock, w, fetch = fresh()
    ids = [w.add(w.bars(f"extra{i}", "tk-btc")) for i in range(40)]
    got = group(mod, fetch, clock, w, frozen={"by_candidate": {"cd-btc": ids}, "required": []})
    c.eq("every arm admitted to the horizon has a slot", [s.arm for s in got], ["L1", "L2", "L1x"])
    c.eq(
        "each failed for its evidence, with the reason, and called nothing",
        [(s.verdict, s.reason, len(s.calls), s.tokens) for s in got],
        [("FAILED_EVIDENCE", "TOO_MANY_ITEMS", 0, 0)] * 3,
    )
    c.eq(
        "each says which version it is",
        [s.version for s in got],
        [arm_l1.agent_version_hash(SINGLE), arm_l2.agent_version_hash(DEBATE), arm_l1x.agent_version_hash(CONTROL)],
    )
    c.eq(
        "the control says it ran no sample and matched no target; the others have no control note",
        [s.control for s in got],
        [None, None, {"target_tokens": 0, "samples": 0, "mismatch": False, "collapsed": False}],
    )
    c.eq("none has a debate note and none a probability", [(s.debate, s.p, s.proposed_p) for s in got], [(None, None, None)] * 3)
    c.eq("each finished at the time of the check, under its cutoff", {(s.finished_ms) for s in got}, {S})
    c.eq("the cutoffs of the horizon", [s.cutoff_ms for s in got], [600_000, 700_000, 780_000])
    return c.bad


def check_concurrency(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    clock, w, fetch = fresh()
    fetch.meet = threading.Barrier(2)
    got = group(mod, fetch, clock, w)
    c.eq("the single judgment and the debate are asked at the same time", [s.verdict for s in got], ["CALL", "CALL", "CALL"])
    clock, w, fetch = fresh()
    got = group(mod, fetch, clock, w, arms={"L1": CUTOFFS["L1"], "L2": CUTOFFS["L2"]}, l1x=None)
    c.eq("a debate with no control is just the debate", [s.arm for s in got], ["L1", "L2"])
    clock, w, fetch = fresh()
    got = group(mod, fetch, clock, w, arms={"L2": CUTOFFS["L2"]}, l1=None, l1x=None)
    c.eq("and the debate alone", [(s.arm, s.verdict) for s in got], [("L2", "CALL")])
    return c.bad


def check_deadline_binds_every_request(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    clock, w, fetch = fresh()
    fetch.move[arm_l2.STAGES["bull"]] = 700_000  # the debaters use all the time the debate has
    got = by_arm(group(mod, fetch, clock, w))
    c.eq("a judge with no time left is not asked", arm_l2.STAGES["judge"] in fetch.asked, False)
    c.eq("and the debate is late, not failed", (got["L2"].verdict, got["L2"].reason), ("FAILED_LATE", "FINISHED_AFTER_CUTOFF"))
    c.eq("the control has no time of its own to spend either: it was given until 780000", got["L1x"].cutoff_ms, 780_000)
    clock, w, fetch = fresh()
    fetch.move[arm_l1.STAGE] = 599_500  # half a second left to a 600000 ms cutoff
    single = by_arm(group(mod, fetch, clock, w))["L1"]
    c.eq("an answer 500 ms before the cutoff is kept", (len(single.calls), single.verdict), (1, "CALL"))
    return c.bad


def check_late(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for moment, verdict in ((600_000, "CALL"), (600_001, "FAILED_LATE")):
        clock, w, fetch = fresh()
        fetch.move[arm_l1.STAGE] = moment
        single = by_arm(group(mod, fetch, clock, w))["L1"]
        c.eq(f"a single judgment answering {moment} ms after the snapshot", (single.verdict, single.finished_ms), (verdict, S + moment))
    clock, w, fetch = fresh()
    fetch.move[arm_l1.STAGE] = 600_001
    late = by_arm(group(mod, fetch, clock, w))["L1"]
    c.eq(
        "a late answer is thrown away: no reason of the model, no probability, nothing cited, but its spend is kept",
        (late.reason, late.gate, late.proposed_p, late.p, late.evidence_ids, late.invalidation, len(late.calls), late.tokens),
        ("FINISHED_AFTER_CUTOFF", "NONE", None, None, (), (), 1, 60),
    )
    c.eq(
        "and it still says which evidence it read and what it is",
        (late.observation_set_hash != "", late.version, late.cutoff_ms),
        (True, arm_l1.agent_version_hash(SINGLE), 600_000),
    )
    # the debate
    clock, w, fetch = fresh()
    fetch.move[arm_l2.STAGES["judge"]] = 700_001
    got = by_arm(group(mod, fetch, clock, w))
    c.eq(
        "a late debate keeps its note and its spend",
        (got["L2"].verdict, got["L2"].debate["failed_role"], len(got["L2"].calls), got["L2"].tokens),
        ("FAILED_LATE", None, 3, 180),
    )
    c.eq(
        "the control still runs, matched to that spend, with the time left",
        (got["L1x"].verdict, got["L1x"].control["target_tokens"], got["L1x"].control["samples"]),
        ("CALL", 180, 3),
    )
    for moment, verdict, samples in ((779_000, "CALL", 3), (779_001, "FAILED_LATE", 0), (780_000, "FAILED_LATE", 0)):
        clock, w, fetch = fresh()
        fetch.move[arm_l2.STAGES["judge"]] = moment
        ctl = by_arm(group(mod, fetch, clock, w))["L1x"]
        c.eq(
            f"the debate ends {moment} ms after the snapshot: the control",
            (ctl.verdict, ctl.control["samples"], ctl.control["target_tokens"]),
            (verdict, samples, 180),
        )
        c.eq(
            f"{moment}: the control's whole note",
            ctl.control,
            {"target_tokens": 180, "samples": samples, "mismatch": False, "collapsed": samples == 3},
        )
        if samples == 0:
            c.eq(
                "and a control that did not run made no call",
                (len(ctl.calls), ctl.tokens, ctl.reason, ctl.finished_ms),
                (0, 0, "FINISHED_AFTER_CUTOFF", S + moment),
            )
            c.eq("nor proposed anything", (ctl.proposed_p, ctl.p, ctl.evidence_ids, ctl.gate), (None, None, (), "NONE"))
    return c.bad


def check_time_allowed(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    clock, w, fetch = fresh()
    group(mod, fetch, clock, w)
    c.eq("a long cutoff leaves the configured time", {t for v in fetch.timeouts.values() for t in v}, {120.0})
    short = {"L1": ((4, 50_000),), "L2": ((4, 60_000),), "L1x": ((4, 80_000),)}
    clock, w, fetch = fresh()
    group(mod, fetch, clock, w, arms=short)
    c.eq("a short cutoff is the time allowed to each request", sorted(fetch.timeouts[arm_l1.STAGE]), [50.0, 80.0, 80.0, 80.0])
    c.eq(
        "the debate's sides and its judge have the debate's",
        (fetch.timeouts[arm_l2.STAGES["bull"]], fetch.timeouts[arm_l2.STAGES["bear"]], fetch.timeouts[arm_l2.STAGES["judge"]]),
        ([60.0], [60.0], [60.0]),
    )
    clock, w, fetch = fresh()
    group(mod, fetch, clock, w, arms={"L1": ((4, 500),)}, l2=None, l1x=None)
    c.eq("with under a second left no request is sent", fetch.timeouts.get(arm_l1.STAGE, []), [])
    clock, w, fetch = fresh()
    group(mod, fetch, clock, w, arms={"L1": ((4, 1_000),)}, l2=None, l1x=None)
    c.eq("a second is a second", fetch.timeouts[arm_l1.STAGE], [1.0])
    clock, w, fetch = fresh()
    group(mod, fetch, clock, w, arms={"L1": ((4, 1_500),)}, l2=None, l1x=None)
    c.eq("and a second and a half is that", fetch.timeouts[arm_l1.STAGE], [1.5])
    # the time left is read when the request is made, not when the group began
    clock, w, fetch = fresh()
    fetch.move[arm_l2.STAGES["judge"]] = 70_000
    group(mod, fetch, clock, w, arms=short)
    c.eq(
        "a control that starts late has the time that is left",
        sorted(fetch.timeouts[arm_l1.STAGE]),
        [10.0, 10.0, 10.0, 50.0],
    )
    return c.bad


def check_stops(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    clock, w, fetch = fresh()
    fetch.gone.add(arm_l1.STAGE)
    got = by_arm(group(mod, fetch, clock, w))
    c.eq(
        "a model that is down: the single judgment fails with the reason",
        (got["L1"].verdict, got["L1"].reason, got["L1"].tokens, len(got["L1"].calls)),
        ("FAILED", "MODEL_UNAVAILABLE", 0, 1),
    )
    c.eq(
        "so does the control, all of whose samples came to nothing",
        (got["L1x"].verdict, got["L1x"].reason, got["L1x"].control["samples"]),
        ("FAILED", "MODEL_UNAVAILABLE", 3),
    )
    c.eq("the debate is untouched", got["L2"].verdict, "CALL")
    # a debate with nothing spent
    clock, w, fetch = fresh()
    fetch.gone.update({arm_l2.STAGES["bull"], arm_l2.STAGES["bear"]})
    got = by_arm(group(mod, fetch, clock, w))
    c.eq(
        "a debate with both sides down",
        (got["L2"].verdict, got["L2"].reason, got["L2"].debate["failed_role"]),
        ("FAILED", "MODEL_UNAVAILABLE", "bull"),
    )
    c.eq("leaves nothing to match", (got["L1x"].verdict, got["L1x"].reason), ("FAILED", "NO_MATCH_TARGET"))
    c.eq(
        "the control made no call, and its note says so",
        (len(got["L1x"].calls), got["L1x"].tokens, got["L1x"].control, got["L1x"].finished_ms),
        (0, 0, {"target_tokens": 0, "samples": 0, "mismatch": False, "collapsed": False}, S),
    )
    c.eq("no judgment was asked of the control's stage", fetch.asked.count(arm_l1.STAGE), 1)
    # one side down
    clock, w, fetch = fresh()
    fetch.gone.add(arm_l2.STAGES["bear"])
    got = by_arm(group(mod, fetch, clock, w))
    c.eq("a side down: the failed role is named", got["L2"].debate["failed_role"], "bear")
    c.eq(
        "and the control is matched to what the other side cost",
        (got["L1x"].control["target_tokens"], got["L1x"].control["mismatch"]),
        (60, True),
    )
    # evidence missing
    clock, w, fetch = fresh()
    got = by_arm(group(mod, fetch, clock, w, candidate=("cd-eth", "tk-eth"), frozen={"required": ["binance-spot", "ryo"]}))
    c.eq(
        "a source missing: every arm fails for want of evidence",
        {(a, s.verdict, s.reason, len(s.calls), s.tokens) for a, s in got.items()},
        {
            ("L1", "FAILED_EVIDENCE", "MISSING_EVIDENCE", 0, 0),
            ("L2", "FAILED_EVIDENCE", "MISSING_EVIDENCE", 0, 0),
            ("L1x", "FAILED_EVIDENCE", "MISSING_EVIDENCE", 0, 0),
        },
    )
    c.eq("and nobody was asked", fetch.asked, [])
    c.eq(
        "the control's note, where it did not run",
        got["L1x"].control,
        {"target_tokens": 0, "samples": 0, "mismatch": False, "collapsed": False},
    )
    return c.bad


def check_decisions(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    clock, w, fetch = fresh()
    fetch.decision = {
        "decision": "NO_CALL",
        "no_call_reason": "EVIDENCE_CONFLICT_UNRESOLVED",
        "p": None,
        "invalidations": [],
        "assertions": [],
    }
    got = by_arm(group(mod, fetch, clock, w))
    c.eq(
        "an abstention",
        {(a, s.verdict, s.reason, s.p, s.proposed_p, s.gate) for a, s in got.items() if a != "L2"},
        {
            ("L1", "NO_CALL", "EVIDENCE_CONFLICT_UNRESOLVED", None, None, "NONE"),
            ("L1x", "NO_CALL", "EVIDENCE_CONFLICT_UNRESOLVED", None, None, "NONE"),
        },
    )
    c.eq("the judge's abstention is the debate's", (got["L2"].verdict, got["L2"].reason), ("NO_CALL", "EVIDENCE_CONFLICT_UNRESOLVED"))
    clock, w, fetch = fresh()
    false = {
        "id": "a1",
        "kind": "NUMERIC",
        "op": "eq",
        "value": 80000,
        "unit": "price",
        "window": "spot",
        "cites": [{"evidence_id": w.btc_bars, "field_path": "last_close"}],
    }
    fetch.decision = {"assertions": [false]}
    one = by_arm(group(mod, fetch, clock, w, arms={"L1": CUTOFFS["L1"]}, l2=None, l1x=None))["L1"]
    c.eq(
        "a false statement stops the call, and the proposal stays beside the final answer",
        (one.verdict, one.reason, one.gate, one.proposed_p, one.p),
        ("NO_CALL", "ESSENTIAL_PREMISE_UNPROVEN", "GROUNDING", Decimal("0.62"), None),
    )
    c.eq("the evidence it cited stays with the stopped call", one.evidence_ids, (w.btc_bars,))
    # the debate's sides
    clock, w, fetch = fresh()
    fetch.bull = ([w.btc_bars], [])
    fetch.bear = ([w.market], [])
    note = by_arm(group(mod, fetch, clock, w))["L2"].debate
    c.eq(
        "the sides cite different items",
        (note["collapsed"], note["disagreement"]["bull_only"], note["disagreement"]["bear_only"], note["disagreement"]["shared"]),
        (False, [w.btc_bars], [w.market], []),
    )
    c.eq(
        "and the judge leaned on the bull's",
        (note["disagreement"]["judge_on_bull"], note["disagreement"]["judge_on_bear"]),
        ([w.btc_bars], []),
    )
    clock, w, fetch = fresh()
    fetch.bull = ([w.btc_bars], [])
    fetch.bear = ([w.btc_bars], [])
    c.eq("two sides that say the same are a collapsed debate", by_arm(group(mod, fetch, clock, w))["L2"].debate["collapsed"], True)
    return c.bad


def check_body(mod: types.ModuleType) -> list[str]:
    from hoshitori import arm_run_record

    c = Case(mod)
    clock, w, fetch = fresh()
    got = by_arm(group(mod, fetch, clock, w))
    bodies = {a: mod.run_body(s, "cy1", f"fc-{a}", "cd-btc", 4) for a, s in got.items()}
    c.eq("every body is one the record takes", [arm_run_record.check_body(b) for b in bodies.values()], [None, None, None])
    c.eq(
        "the single judgment's body",
        bodies["L1"],
        {
            "cycle_id": "cy1", "forecast_id": "fc-L1", "arm": "L1", "candidate_id": "cd-btc", "hours": 4, "verdict": "CALL", "reason": None,
            "gate": "NONE", "proposed_p": "0.62", "final_p": "0.62", "version": arm_l1.agent_version_hash(SINGLE), "cutoff_ms": 600_000,
            "finished_at_ms": S, "calls": 1, "tokens": 60, "debate": None, "control": None,
        },
    )  # fmt: skip
    c.eq(
        "the control's probability keeps its six places", (bodies["L1x"]["proposed_p"], bodies["L1x"]["final_p"]), ("0.620000", "0.620000")
    )
    c.eq(
        "and the notes are carried",
        (bodies["L2"]["debate"] is not None, bodies["L1x"]["control"] is not None, bodies["L1x"]["debate"]),
        (True, True, None),
    )
    clock, w, fetch = fresh()
    fetch.move[arm_l1.STAGE] = 600_001
    late = by_arm(group(mod, fetch, clock, w))["L1"]
    body = mod.run_body(late, "cy1", "fc-L1", "cd-btc", 4)
    c.eq(
        "a late body",
        (body["verdict"], body["reason"], body["proposed_p"], body["final_p"], body["finished_at_ms"]),
        ("FAILED_LATE", "FINISHED_AFTER_CUTOFF", None, None, S + 600_001),
    )
    c.eq("which the record takes", arm_run_record.check_body(body), None)
    return c.bad


# --- an answer that a record downstream could not hold --------------------------------------------------------------------------
Change = Callable[[dict[str, Any], str], None]  # changes the provider's answer to a request of a stage, in place


def _content(outer: dict[str, Any], change: Callable[[dict[str, Any]], None]) -> None:
    message = outer["choices"][0]["message"]
    body = json.loads(message["content"])
    change(body)
    message["content"] = json.dumps(body, ensure_ascii=False)  # the model's own text, as it wrote it


def trailing_zeros(outer: dict[str, Any], stage: str) -> None:
    """Trigger a: a probability written with a trailing zero beyond its six places."""
    message = outer["choices"][0]["message"]
    message["content"] = message["content"].replace('"p": 0.62', '"p": 0.6200000')


def empty_model(outer: dict[str, Any], stage: str) -> None:
    """Trigger b: the provider reports an empty model id."""
    outer["model"] = ""


def long_finish(outer: dict[str, Any], stage: str) -> None:
    """Trigger c: a finish reason of 65 characters."""
    outer["choices"][0]["finish_reason"] = "stop" + "_" * 61


def non_nfc_member(outer: dict[str, Any], stage: str) -> None:
    """Trigger d: a judgment with one member too many, whose name is not NFC ("e" and a combining acute accent)."""
    _content(outer, lambda body: body.update({"note\u0065\u0301": "x"}) if "decision" in body else None)


def post_15_claim(outer: dict[str, Any], stage: str) -> None:
    """A debater's claim with a code point assigned only after Unicode 15.0 (U+1FAE9), which the judge would be shown."""
    if stage == arm_l2.STAGES["bull"]:
        _content(outer, lambda body: body.update({"claim": "Up \U0001fae9"}))


def post_15_member(outer: dict[str, Any], stage: str) -> None:
    """A judgment with one member too many, whose name holds U+1FAE9."""
    _content(outer, lambda body: body.update({"note\U0001fae9": "x"}) if "decision" in body else None)


def changed(capture: Any, change: Change, stage: str) -> Any:
    if not isinstance(capture, RawCapture):
        return capture
    outer = json.loads(capture.body)
    change(outer, stage)
    return dataclasses.replace(capture, body=json.dumps(outer).encode())


class Changed(Fetch):
    """The fetch of these checks, with every answer changed as a provider or a model could change it."""

    def __init__(self, clock: list[int], w: World, change: Change) -> None:
        super().__init__(clock, w)
        self.change = change

    def __call__(self, args: Any, stage: str, timeout_s: float) -> Any:
        return changed(super().__call__(args, stage, timeout_s), self.change, stage)


INVALID, UNAVAILABLE = "MODEL_OUTPUT_INVALID", "MODEL_UNAVAILABLE"
SOURCE = "urn:hoshitori:league:test"
# what each arm of a candidate and a horizon ends in, for each change: a failed run with its reason, or what it would have been
VARIANTS: tuple[tuple[str, Change, dict[str, tuple[str, str | None]]], ...] = (
    ("a trailing zero", trailing_zeros, {"L1": ("FAILED", INVALID), "L2": ("FAILED", INVALID), "L1x": ("FAILED", INVALID)}),
    ("an empty model", empty_model, {"L1": ("FAILED", UNAVAILABLE), "L2": ("FAILED", UNAVAILABLE), "L1x": ("FAILED", "NO_MATCH_TARGET")}),
    ("a long finish", long_finish, {"L1": ("FAILED", UNAVAILABLE), "L2": ("FAILED", UNAVAILABLE), "L1x": ("FAILED", "NO_MATCH_TARGET")}),
    ("a non-NFC member", non_nfc_member, {"L1": ("FAILED", INVALID), "L2": ("FAILED", INVALID), "L1x": ("FAILED", INVALID)}),
    ("a debater's U+1FAE9", post_15_claim, {"L1": ("CALL", None), "L2": ("FAILED", INVALID), "L1x": ("CALL", None)}),
    ("a member named with U+1FAE9", post_15_member, {"L1": ("FAILED", INVALID), "L2": ("FAILED", INVALID), "L1x": ("FAILED", INVALID)}),
)


def check_answers_no_record_holds(mod: types.ModuleType) -> list[str]:
    """for one candidate and one horizon: each answer below is a failed run of its arm with its reason, never an exception, and
    every record the run makes of it (the arm run and each model call) is one its writer takes and the canonical module can hold."""
    c = Case(mod)
    for label, change, ends in VARIANTS:
        clock, w = [S], World()
        fetch = Changed(clock, w, change)
        try:
            got = by_arm(group(mod, fetch, clock, w))
        except Exception as exc:
            c.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            continue
        c.eq(f"{label}: how each arm ends", {a: (s.verdict, s.reason) for a, s in got.items()}, ends)
        try:
            for arm, slot in got.items():
                canonical.dumps(arm_run_record.to_record(mod.run_body(slot, "cy1", f"fc-{arm}", "cd-btc", 4), SOURCE, S))
                for call in slot.calls:
                    canonical.dumps(model_call_record.to_record(call, SOURCE, S))
        except Exception as exc:
            c.bad.append(f"{label}: a record of the run cannot be written: {type(exc).__name__}: {exc}")
    clock, w = [S], World()
    try:
        debate = by_arm(group(mod, Changed(clock, w, post_15_claim), clock, w))["L2"]
        c.eq(
            "the debater whose answer could not be shown to the judge is named",
            debate.debate,
            {"collapsed": False, "failed_role": "bull", "disagreement": None},
        )
    except Exception as exc:
        c.bad.append(f"the debater: raised {type(exc).__name__}: {exc}")
    return c.bad


def check_cycle_survives_any_answer(mod: types.ModuleType) -> list[str]:
    """in a whole cycle (the real league, the scripted model of ``league_models_checks``, as the finding's script runs it): each
    answer of ``VARIANTS`` fails the model arms' slots with their reasons, the cycle is committed, and the rules arm's slots end as they do
    when the model answers well. Before the fix every one of these closed the cycle ABANDONED(PROCESS_LOST), the rules arm's slots too."""
    c = Case(mod)

    def run(change: Change | None) -> tuple[Any, list[dict[str, Any]]]:
        with tempfile.TemporaryDirectory() as tmp:
            w = league_models_checks.world(Path(tmp))
            model: league_models_checks.Model = league_models_checks.Model(w) if change is None else ChangedModel(w, change)
            given = mod.ModelArms(
                model_client.ModelClient(model), league_models_checks.SINGLE, league_models_checks.DEBATE, league_models_checks.CONTROL
            )
            try:
                return w.run(league, models=given), w.records()
            finally:
                w.close()

    control, _ = run(None)
    rules = {(s.forecast_id, s.fate.value, s.reason, s.level) for s in control.slots if s.arm_id == "L0"}
    c.eq("the control: the cycle is committed", (control.state.value, control.reason), ("COMMITTED", None))
    for label, change, ends in VARIANTS:
        try:
            out, records = run(change)
        except Exception as exc:
            c.bad.append(f"{label}: the cycle raised {type(exc).__name__}: {exc}")
            continue
        c.eq(f"{label}: the cycle is committed", (out.state.value, out.reason), ("COMMITTED", None))
        fates = {"CALL": "FORECAST", "FAILED": "FAILED"}
        c.eq(
            f"{label}: every model slot ends as its arm did",
            {(s.arm_id, s.fate.value, s.reason) for s in out.slots if s.arm_id != "L0"},
            {(arm, fates[verdict], reason) for arm, (verdict, reason) in ends.items()},
        )
        c.eq(
            f"{label}: the rules arm's slots end as they do when the model answers well",
            {(s.forecast_id, s.fate.value, s.reason, s.level) for s in out.slots if s.arm_id == "L0"},
            rules,
        )
        try:
            for record in records:
                if record["type"] == "model_call":
                    model_call_record.from_record(record)
                elif record["type"] == "arm_run":
                    arm_run_record.from_record(record)
        except Exception as exc:
            c.bad.append(f"{label}: a record in the ledger does not read back: {type(exc).__name__}: {exc}")
    return c.bad


class ChangedModel(league_models_checks.Model):
    """The scripted model of the cycle checks, with every answer changed."""

    def __init__(self, w: Any, change: Change) -> None:
        super().__init__(w)
        self.change = change

    def __call__(self, args: Any, stage: str, timeout_s: float) -> Any:
        return changed(super().__call__(args, stage, timeout_s), self.change, stage)


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_versions_and_pins,
        check_group,
        check_oversized_candidate,
        check_concurrency,
        check_late,
        check_deadline_binds_every_request,
        check_time_allowed,
        check_stops,
        check_decisions,
        check_body,
        check_answers_no_record_holds,
        check_cycle_survives_any_answer,
    ):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}")
    return bad
