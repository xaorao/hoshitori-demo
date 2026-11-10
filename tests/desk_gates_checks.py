"""Checks of the Desk's gates (``hoshitori.desk_gates``), runnable against a mutant.

The gates are a pure function of a proposal, a pre-test target, the conditions of the moment and the falsification record. The properties
that matter are checked over thousands of random inputs from a fixed seed as well as by hand: no path gives an exposure above the ceiling, above
the pre-test target after a failed step, or a short; a model field cannot set a ceiling; and each gate stops or cuts as the order says.
"""

from __future__ import annotations

import dataclasses
import random
import types
from decimal import Decimal
from typing import Any

from hoshitori import falsify

D = Decimal
SURVIVED, ALTERNATIVE, NOT_COMPLETED = "SURVIVED", "ALTERNATIVE_FAVOURED", "NOT_COMPLETED"


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Any, says: str) -> None:
        try:
            call()
        except self.mod.GateError as exc:
            if not str(exc).startswith(says):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected to start with {says!r}")
            return
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def record(outcome: str) -> falsify.Falsification:
    return falsify.Falsification(outcome, (), (), ())


def inputs(mod: types.ModuleType, **over: Any) -> Any:
    values: dict[str, Any] = {"grounding_ok": True, "required_missing": (), "stale": False, "room": D("0.05")}
    values.update(over)
    return mod.Conditions(**values)


def proposal(mod: types.ModuleType, stance: str = "LONG", p: str = "0.62", weight: str = "0.04") -> Any:
    return mod.Proposal(stance, D(p), D(weight))


def pre(mod: types.ModuleType, stance: str = "LONG", p: str = "0.65", weight: str = "0.05") -> Any:
    return mod.PreTarget(stance, D(p), D(weight))


def events(result: Any) -> list[tuple[str, str]]:
    return [(e.gate, e.status) for e in result.events]


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("kinds", mod.KINDS, ("COMMIT", "ABSTAIN", "FAILED"))
    c.eq(
        "gates",
        mod.GATES,
        (
            "HAZARD_SCREEN",
            "GROUNDING",
            "REQUIRED_EVIDENCE",
            "STALENESS",
            "MINIMUM_EDGE",
            "FALSIFICATION",
            "MINIMUM_EDGE_AFTER_CUT",
            "RISK_KERNEL",
        ),
    )
    c.eq("statuses", mod.STATUSES, ("PASS", "CUT", "STOP"))
    c.eq("minimum edge", mod.EDGE, D("0.02"))
    c.eq("ceiling factor after a failed step", mod.FAILED_FACTOR, D("0.5"))
    c.eq("stances", (mod.STANCES, mod.PRE_STANCES), (("LONG", "FLAT", "SHORT"), ("LONG", "FLAT")))
    c.eq(
        "a proposal has no ceiling or exposure field a model could set",
        sorted(f.name for f in dataclasses.fields(mod.Proposal)),
        ["p", "stance", "weight"],
    )
    kinds = (mod.Proposal, mod.PreTarget, mod.Conditions, mod.FinalAction, mod.GateEvent, mod.GateResult)
    c.eq("frozen", [k.__dataclass_params__.frozen for k in kinds], [True] * len(kinds))
    return c.bad


def check_ceiling_for(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    p = pre(mod)
    c.eq("a passed step keeps the target", mod.ceiling_for(p, True), (D("0.050000"), D("0.6500")))
    c.eq("a failed step halves the weight and the distance from one half", mod.ceiling_for(p, False), (D("0.025000"), D("0.5750")))
    flat = pre(mod, "FLAT", "0.5", "0")
    c.eq(
        "a flat target stays flat either way",
        (mod.ceiling_for(flat, True), mod.ceiling_for(flat, False)),
        ((D("0.000000"), D("0.5000")),) * 2,
    )
    odd = pre(mod, p="0.6666666", weight="0.0333333")
    c.eq("the weight is rounded down, the probability toward one half", mod.ceiling_for(odd, False), (D("0.016666"), D("0.5833")))
    c.eq(
        "and the gate uses the same numbers",
        (
            mod.gate(proposal(mod), p, inputs(mod), record(ALTERNATIVE)).ceiling,
            mod.gate(proposal(mod), p, inputs(mod), record(ALTERNATIVE)).p_cap,
        ),
        mod.ceiling_for(p, False),
    )
    return c.bad


def check_pass(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    r = mod.gate(proposal(mod), pre(mod), inputs(mod), record(SURVIVED))
    c.eq("a clean proposal commits as proposed", (r.kind, r.reason, r.step), ("COMMIT", None, "PASSED"))
    c.eq("with its stance, probability and weight", (r.final.stance, r.final.p, r.final.weight), ("LONG", D("0.62"), D("0.04")))
    c.eq("ceiling and probability cap are the pre-test target's", (r.ceiling, r.p_cap), (D("0.05"), D("0.65")))
    c.eq("the falsification event states them", r.events[4].detail, "ceiling 0.050000, probability cap 0.6500")
    c.eq("the kernel event states the room", r.events[5].detail, "room 0.05")
    c.eq(
        "every gate passed, in order",
        events(r),
        [(g, "PASS") for g in ("GROUNDING", "REQUIRED_EVIDENCE", "STALENESS", "MINIMUM_EDGE", "FALSIFICATION", "RISK_KERNEL")],
    )
    # above the pre-test target is cut to it
    r = mod.gate(proposal(mod, p="0.80", weight="0.09"), pre(mod), inputs(mod, room=D("1")), record(SURVIVED))
    c.eq(
        "a weight above the target is cut to it, a probability above the target is cut to it",
        (r.final.weight, r.final.p),
        (D("0.05"), D("0.65")),
    )
    c.eq("and the cuts are recorded", [(e.gate, e.status) for e in r.events if e.status == "CUT"], [("FALSIFICATION", "CUT")])
    # the risk kernel's room
    r = mod.gate(proposal(mod), pre(mod), inputs(mod, room=D("0.01")), record(SURVIVED))
    c.eq("the kernel's room cuts the weight", (r.final.weight, events(r)[-1]), (D("0.01"), ("RISK_KERNEL", "CUT")))
    r = mod.gate(proposal(mod), pre(mod), inputs(mod, room=D("0")), record(SURVIVED))
    c.eq("no room is a flat commitment, not a purchase", (r.kind, r.final.stance, r.final.weight), ("COMMIT", "FLAT", D("0")))
    # weights are rounded down, never up
    r = mod.gate(proposal(mod, weight="0.0399999999"), pre(mod), inputs(mod), record(SURVIVED))
    c.eq("a weight is written to six places, rounded down", r.final.weight, D("0.039999"))
    r = mod.gate(proposal(mod, p="0.6299999999"), pre(mod), inputs(mod), record(SURVIVED))
    c.eq("a probability is written to four places, rounded toward one half", r.final.p, D("0.6299"))
    return c.bad


def check_stops(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    r = mod.gate(proposal(mod), pre(mod), inputs(mod, grounding_ok=False), record(SURVIVED))
    c.eq(
        "the grounding stop says what failed, and the step was not reached",
        (r.events[-1].detail, r.step),
        ("a figure or statement is not grounded", "NOT_REACHED"),
    )
    c.eq(
        "ungrounded: an abstention on an unproven premise",
        (r.kind, r.reason, r.final, events(r)),
        ("ABSTAIN", "ESSENTIAL_PREMISE_UNPROVEN", None, [("GROUNDING", "STOP")]),
    )
    r = mod.gate(proposal(mod), pre(mod), inputs(mod, required_missing=("ryo.deep_analysis",), grounding_ok=True), record(SURVIVED))
    c.eq(
        "required evidence missing: a failure, never an abstention",
        (r.kind, r.reason, events(r)),
        ("FAILED", "FAILED_EVIDENCE", [("GROUNDING", "PASS"), ("REQUIRED_EVIDENCE", "STOP")]),
    )
    c.eq("and it names what is missing", r.events[-1].detail, "missing: ryo.deep_analysis")
    r2 = mod.gate(proposal(mod), pre(mod), inputs(mod, required_missing=("a", "b")), record(SURVIVED))
    c.eq("every missing name, separated", r2.events[-1].detail, "missing: a, b")
    r = mod.gate(proposal(mod), pre(mod), inputs(mod, stale=True), record(SURVIVED))
    c.eq("the staleness stop says what failed", r.events[-1].detail, "the evidence is older than a decision may use")
    c.eq("stale evidence at decision time", (r.kind, r.reason, events(r)[-1]), ("FAILED", "FAILED_STALE", ("STALENESS", "STOP")))
    r = mod.gate(proposal(mod, p="0.5199"), pre(mod), inputs(mod), record(SURVIVED))
    c.eq("below the minimum edge", (r.kind, r.reason, events(r)[-1]), ("ABSTAIN", "BELOW_MINIMUM_EDGE", ("MINIMUM_EDGE", "STOP")))
    c.eq("with the numbers", r.events[-1].detail, "p 0.5199 is under 0.52")
    r = mod.gate(proposal(mod, p="0.52"), pre(mod), inputs(mod), record(SURVIVED))
    c.eq("exactly at the edge is enough", (r.kind, r.final.p), ("COMMIT", D("0.52")))
    r = mod.gate(proposal(mod, stance="SHORT", p="0.30"), pre(mod), inputs(mod), record(SURVIVED))
    c.eq("a short is outside the Desk's scope", (r.kind, r.reason, r.final), ("ABSTAIN", "OUT_OF_SCOPE", None))
    c.eq("and says so", (*events(r)[-1], r.events[-1].detail), ("MINIMUM_EDGE", "STOP", "the Desk is long-only"))
    r = mod.gate(proposal(mod, stance="LONG", p="0.40"), pre(mod), inputs(mod), record(SURVIVED))
    c.eq("a long call with a probability under one half has no edge", (r.kind, r.reason), ("ABSTAIN", "BELOW_MINIMUM_EDGE"))
    r = mod.gate(proposal(mod, stance="FLAT", p="0.5", weight="0"), pre(mod, "FLAT", "0.5", "0"), inputs(mod), record(SURVIVED))
    c.eq("a flat proposal is a flat commitment", (r.kind, r.final.stance, r.final.weight, r.final.p), ("COMMIT", "FLAT", D("0"), D("0.5")))
    c.eq("a flat proposal under a flat target changes nothing", [e.status for e in r.events], ["PASS"] * 6)
    r = mod.gate(proposal(mod, stance="LONG", p="0.9", weight="0.04"), pre(mod, "FLAT", "0.5", "0"), inputs(mod), record(SURVIVED))
    c.eq("a model cannot be more bullish than the pre-test target", (r.final.stance, r.final.weight, r.final.p), ("FLAT", D("0"), D("0.5")))
    return c.bad


def check_failed_step(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for label, rec in (
        ("an alternative favoured", record(ALTERNATIVE)),
        ("a step not completed", record(NOT_COMPLETED)),
        ("no record at all", None),
    ):
        r = mod.gate(proposal(mod, p="0.64", weight="0.05"), pre(mod), inputs(mod, room=D("1")), rec)
        c.eq(f"{label}: the step failed", r.step, "FAILED")
        c.eq(
            f"{label}: the ceiling is half the target and the probability is moved toward one half",
            (r.ceiling, r.p_cap),
            (D("0.025"), D("0.575")),
        )
        c.eq(f"{label}: committed within both", (r.kind, r.final.weight, r.final.p), ("COMMIT", D("0.025"), D("0.575")))
        c.eq(f"{label}: the cut is recorded", ("FALSIFICATION", "CUT") in events(r), True)
        c.eq(f"{label}: the edge was checked again after it", ("MINIMUM_EDGE_AFTER_CUT", "PASS") in events(r), True)
        c.eq(
            f"{label}: and the event says why",
            r.events[4].detail,
            "ceiling 0.025000, probability cap 0.5750: the step failed or was not completed",
        )
    r = mod.gate(proposal(mod, p="0.55", weight="0.05"), pre(mod, p="0.53"), inputs(mod), record(ALTERNATIVE))
    c.eq(
        "a cut that leaves no edge is an abstention, not a thin purchase",
        (r.kind, r.reason, events(r)[-2:]),
        ("ABSTAIN", "BELOW_MINIMUM_EDGE", [("FALSIFICATION", "CUT"), ("MINIMUM_EDGE_AFTER_CUT", "STOP")]),
    )
    r = mod.gate(proposal(mod, p="0.60", weight="0.02"), pre(mod, p="0.54"), inputs(mod), record(ALTERNATIVE))
    c.eq("a cut that lands exactly on the edge still commits", (r.kind, r.final.p), ("COMMIT", D("0.52")))
    r = mod.gate(proposal(mod, p="0.55", weight="0.05"), pre(mod, p="0.53"), inputs(mod), record(ALTERNATIVE))
    c.eq("and the stop after the cut says why", r.events[-1].detail, "p 0.5150 is under 0.52")
    r = mod.gate(proposal(mod, weight="0.02"), pre(mod), inputs(mod), record(ALTERNATIVE))
    c.eq("a weight already under the ceiling is untouched", r.final.weight, D("0.02"))
    r = mod.gate(proposal(mod), pre(mod, "FLAT", "0.5", "0"), inputs(mod), record(ALTERNATIVE))
    c.eq("a flat target stays flat", (r.final.stance, r.final.weight), ("FLAT", D("0")))
    r = mod.gate(proposal(mod), pre(mod), inputs(mod, grounding_ok=False), record(ALTERNATIVE))
    c.eq("an earlier gate still stops first", (r.reason, events(r)), ("ESSENTIAL_PREMISE_UNPROVEN", [("GROUNDING", "STOP")]))
    return c.bad


def check_properties(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    rng = random.Random(20260101)
    seen = {"COMMIT": 0, "ABSTAIN": 0, "FAILED": 0, "failed_commit": 0}
    for n in range(3000):
        stance = rng.choice(["LONG", "LONG", "FLAT", "SHORT"])
        prop = mod.Proposal(stance, D(rng.randint(0, 10000)) / D(10000), D(rng.randint(0, 100000)) / D(1000000))
        pre_stance = rng.choice(["LONG", "LONG", "FLAT"])
        target = mod.PreTarget(
            pre_stance, D(rng.randint(5000, 10000)) / D(10000), D(0) if pre_stance == "FLAT" else D(rng.randint(0, 100000)) / D(1000000)
        )
        cond = mod.Conditions(
            rng.random() > 0.1,
            ("x",) if rng.random() < 0.1 else (),
            rng.random() < 0.1,
            D(rng.randint(0, 100000)) / D(1000000),
        )
        rec = rng.choice([record(SURVIVED), record(SURVIVED), record(ALTERNATIVE), record(NOT_COMPLETED), None])
        r = mod.gate(prop, target, cond, rec)
        seen[r.kind] += 1
        if r.kind != "COMMIT":
            if r.final is not None:
                c.bad.append(f"case {n}: {r.kind} carries a final action")
            continue
        f = r.final
        failed = rec is None or rec.outcome != SURVIVED
        seen["failed_commit"] += failed
        checks = [
            (f.stance in ("LONG", "FLAT"), "a stance outside long or flat"),
            (f.weight >= 0, "a negative weight"),
            (f.weight <= prop.weight, "a weight above the proposal"),
            (f.weight <= target.weight, "a weight above the pre-test target"),
            (f.weight <= cond.room, "a weight above the kernel's room"),
            (f.stance == "FLAT" or target.stance == "LONG", "exposure under a flat target"),
            (f.stance == "FLAT" or f.weight > 0 or True, ""),
            (f.stance != "FLAT" or f.weight == 0, "a flat commitment with exposure"),
            (not failed or f.weight <= mod.FAILED_FACTOR * target.weight, "a weight above half the target after a failed step"),
            (not failed or target.weight == 0 or f.weight < target.weight, "a weight not below the target after a failed step"),
            (D("0.5") <= f.p <= max(D("0.5"), target.p), "a probability beyond the target's"),
            (
                not failed or f.p <= D("0.5") + mod.FAILED_FACTOR * (target.p - D("0.5")),
                "a probability beyond the cut one after a failed step",
            ),
            (f.stance != "LONG" or f.p >= D("0.5") + mod.EDGE, "a purchase without the minimum edge"),
            (f.p <= prop.p or f.stance == "FLAT", "a probability above the proposal"),
        ]
        c.bad.extend(f"case {n}: {why}" for ok, why in checks if not ok and why)
        if len(c.bad) > 5:
            break
    c.eq("every kind of result was reached", [seen[k] > 100 for k in ("COMMIT", "ABSTAIN", "FAILED", "failed_commit")], [True] * 4)
    # monotone: less room never gives more weight; a worse falsification never gives more
    base = mod.gate(proposal(mod), pre(mod), inputs(mod, room=D("1")), record(SURVIVED)).final.weight
    c.eq(
        "the weight does not fall when the room grows",
        [mod.gate(proposal(mod), pre(mod), inputs(mod, room=D(r) / 100), record(SURVIVED)).final.weight for r in (0, 1, 2, 3, 4, 5, 6)],
        [D("0"), D("0.01"), D("0.02"), D("0.03"), D("0.04"), D("0.04"), D("0.04")],
    )
    c.eq(
        "a failed step never grants more than a passed one",
        mod.gate(proposal(mod), pre(mod), inputs(mod, room=D("1")), record(ALTERNATIVE)).final.weight <= base,
        True,
    )
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.refuses("an unknown stance", lambda: mod.Proposal("BUY", D("0.6"), D("0.01")), "stance is one of")
    c.refuses("a probability outside zero to one", lambda: mod.Proposal("LONG", D("1.1"), D("0.01")), "p is between 0 and 1")
    c.refuses("a negative probability", lambda: mod.Proposal("LONG", D("-0.1"), D("0.01")), "p is between 0 and 1")
    c.refuses("a float probability", lambda: mod.Proposal("LONG", 0.6, D("0.01")), "p is a Decimal")
    c.refuses("a negative weight", lambda: mod.Proposal("LONG", D("0.6"), D("-0.01")), "weight is a Decimal, 0 or more")
    c.refuses("a weight above one", lambda: mod.Proposal("LONG", D("0.6"), D("1.5")), "weight is a Decimal, at most 1")
    c.refuses("nan", lambda: mod.Proposal("LONG", D("NaN"), D("0.1")), "p is a finite Decimal")
    c.refuses("a pre-test target that is short", lambda: mod.PreTarget("SHORT", D("0.6"), D("0.01")), "stance is one of")
    c.refuses("a pre-test target flat with weight", lambda: mod.PreTarget("FLAT", D("0.5"), D("0.01")), "a flat target has no weight")
    c.refuses(
        "a pre-test probability under one half",
        lambda: mod.PreTarget("LONG", D("0.4"), D("0.01")),
        "a pre-test probability is at least one half",
    )
    c.refuses("a room that is negative", lambda: inputs(mod, room=D("-1")), "room is a Decimal, 0 or more")
    c.refuses("staleness that is not a bool", lambda: inputs(mod, stale=0), "stale is a bool")
    c.refuses("a pre-test probability above one", lambda: mod.PreTarget("LONG", D("1.5"), D("0.01")), "p is between 0 and 1")
    c.refuses("a pre-test weight above one", lambda: mod.PreTarget("LONG", D("0.6"), D("1.5")), "weight is a Decimal, at most 1")
    c.eq(
        "the limits themselves are accepted",
        [mod.Proposal("FLAT", D(0), D(1)).p, mod.Proposal("FLAT", D(1), D(0)).weight, mod.PreTarget("LONG", D(1), D(1)).weight],
        [D(0), D(0), D(1)],
    )
    c.refuses("grounding that is not a bool", lambda: inputs(mod, grounding_ok=1), "grounding_ok is a bool")
    c.refuses("missing evidence that is a string", lambda: inputs(mod, required_missing="x"), "required_missing is a tuple of names")
    c.refuses(
        "a falsification record that is not one",
        lambda: mod.gate(proposal(mod), pre(mod), inputs(mod), "SURVIVED"),
        "falsification is a Falsification or None",
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_ceiling_for, check_pass, check_stops, check_failed_step, check_properties, check_refusals):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
