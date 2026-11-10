"""Checks of the arm run record (``hoshitori.arm_run_record``), runnable against a mutant."""

from __future__ import annotations

import copy
import types
from collections.abc import Callable
from typing import Any

from hoshitori import envelope
from hoshitori.ids import natural_id

SOURCE = "urn:hoshitori:league:test"
WHEN = 1_793_358_000_000
VERSION = "ab" * 32
E1, E2, E3 = "ev" + "a" * 26, "ev" + "b" * 26, "ev" + "c" * 26


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want or type(got) is not type(want):
            self.bad.append(f"{label}: got {got!r} ({type(got).__name__}), expected {want!r} ({type(want).__name__})")

    def refuses(self, label: str, call: Callable[[], object], says: str) -> None:
        try:
            call()
        except self.mod.ArmRunRecordError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def body(**over: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "cycle_id": "cy1",
        "forecast_id": "fc1",
        "arm": "L1",
        "candidate_id": "cd-btc",
        "hours": 4,
        "verdict": "CALL",
        "reason": None,
        "gate": "NONE",
        "proposed_p": "0.62",
        "final_p": "0.62",
        "version": VERSION,
        "cutoff_ms": 600_000,
        "finished_at_ms": WHEN,
        "calls": 1,
        "tokens": 60,
        "debate": None,
        "control": None,
    }
    values.update(over)
    return values


def debate(**over: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "collapsed": False,
        "failed_role": None,
        "disagreement": {"bull_only": [E1], "bear_only": [E2], "shared": [E3], "judge_on_bull": [E1], "judge_on_bear": []},
    }
    values.update(over)
    return values


def control(**over: Any) -> dict[str, Any]:
    values: dict[str, Any] = {"target_tokens": 300, "samples": 5, "mismatch": False, "collapsed": False}
    values.update(over)
    return values


def l2(**over: Any) -> dict[str, Any]:
    return body(**{"arm": "L2", "debate": debate(), **over})


def l1x(**over: Any) -> dict[str, Any]:
    return body(**{"arm": "L1x", "control": control(), **over})


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "constants",
        (mod.TYPE, mod.TYPE_VERSION, mod.ARMS, mod.VERDICTS, mod.GATES, mod.ROLES),
        (
            "arm_run",
            1,
            ("L1", "L1x", "L2"),
            ("CALL", "NO_CALL", "FAILED", "FAILED_EVIDENCE", "FAILED_LATE"),
            ("NONE", "GROUNDING"),
            ("bull", "bear"),
        ),
    )
    c.eq("limits", (mod.MAX_INSTANT_MS, mod.MAX_COUNT), (253_402_300_799_999, 10**12))
    return c.bad


def check_roundtrip(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    cases = (
        ("a single judgment that called", body()),
        (
            "a single judgment stopped by a gate",
            body(verdict="NO_CALL", reason="ESSENTIAL_PREMISE_UNPROVEN", gate="GROUNDING", final_p=None),
        ),
        ("a model that was down", body(verdict="FAILED", reason="MODEL_UNAVAILABLE", proposed_p=None, final_p=None, calls=2, tokens=0)),
        ("a slot that finished late", body(verdict="FAILED_LATE", reason="FINISHED_AFTER_CUTOFF", final_p=None)),
        (
            "a slot with a source missing",
            body(verdict="FAILED_EVIDENCE", reason="MISSING_EVIDENCE", proposed_p=None, final_p=None, calls=0, tokens=0),
        ),
        ("a debate that called", l2(calls=3, tokens=180)),
        (
            "a debate that lost a side",
            l2(
                verdict="FAILED",
                reason="MODEL_UNAVAILABLE",
                proposed_p=None,
                final_p=None,
                debate=debate(failed_role="bull", disagreement=None),
            ),
        ),
        ("a collapsed debate", l2(debate=debate(collapsed=True))),
        (
            "a debate with nothing cited",
            l2(debate=debate(disagreement={n: [] for n in ("bull_only", "bear_only", "shared", "judge_on_bull", "judge_on_bear")})),
        ),
        ("a control that called", l1x(calls=5, tokens=300)),
        ("a control that missed its match and collapsed", l1x(control=control(mismatch=True, collapsed=True, samples=0, target_tokens=0))),
        ("a probability of one", body(proposed_p="1", final_p="1")),
        ("a probability of one with places", body(proposed_p="1.000000", final_p="1.0")),
        ("a probability of nothing", body(proposed_p="0", final_p="0.000000")),
        ("a probability of six places", body(proposed_p="0.123456", final_p="0.000001")),
    )
    for label, original in cases:
        rec = mod.to_record(copy.deepcopy(original), SOURCE, WHEN)
        c.eq(f"{label}: the body is kept", rec["body"], original)
        c.eq(f"{label}: and reads back", mod.from_record(rec), original)
        c.eq(f"{label}: the id is natural", rec["id"], natural_id("ar", {"forecast": original["forecast_id"]}))
        c.eq(
            f"{label}: the envelope",
            (rec["spec"], rec["type"], rec["type_version"], rec["source"], rec["privacy"]),
            (envelope.SPEC, "arm_run", 1, SOURCE, "public"),
        )
        c.eq(f"{label}: and the time", rec["created_at"], "2026-10-30T11:00:00.000Z")
    a, b = mod.to_record(body(), SOURCE, WHEN), mod.to_record(body(cycle_id="cy2"), SOURCE, WHEN + 5)
    c.eq("one record per slot whatever else differs", a["id"], b["id"])
    c.eq("another slot is another record", mod.to_record(body(forecast_id="fc2"), SOURCE, WHEN)["id"] != a["id"], True)
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)

    def refuse(label: str, b: dict[str, Any], says: str) -> None:
        c.refuses(label, lambda: mod.to_record(b, SOURCE, WHEN), says)

    members = sorted(body())
    refuse("a missing member", {k: v for k, v in body().items() if k != "gate"}, f"the body has the members {members}")
    refuse("a further member", body(extra=1), f"the body has the members {members}")
    form = "{} is not in the form this record takes"
    for name in ("cycle_id", "forecast_id", "candidate_id"):
        refuse(f"{name} empty", body(**{name: ""}), form.format(name))
        refuse(f"{name} not text", body(**{name: 3}), form.format(name))
        refuse(f"{name} missing", body(**{name: None}), form.format(name))
        refuse(f"{name} with a space", body(**{name: "a b"}), form.format(name))
        refuse(f"{name} too long", body(**{name: "a" * 101}), form.format(name))
    c.eq(
        "an id of a hundred characters is taken",
        mod.to_record(body(candidate_id="a" * 100), SOURCE, WHEN)["body"]["candidate_id"],
        "a" * 100,
    )
    refuse("an arm that is not a model arm", body(arm="L0"), "the arm is one of ('L1', 'L1x', 'L2')")
    refuse("hours of zero", body(hours=0), "the horizon in hours is a whole number from 1 to 8784")
    refuse("hours too many", body(hours=8785), "the horizon in hours is a whole number from 1 to 8784")
    refuse("hours that are a flag", body(hours=True), "the horizon in hours is a whole number from 1 to 8784")
    c.eq("hours of a year", mod.to_record(body(hours=8784), SOURCE, WHEN)["body"]["hours"], 8784)
    refuse(
        "a verdict that is unknown",
        body(verdict="MAYBE"),
        "the verdict is one of ('CALL', 'NO_CALL', 'FAILED', 'FAILED_EVIDENCE', 'FAILED_LATE')",
    )
    refuse("a reason in lower case", body(verdict="NO_CALL", reason="low", final_p=None), "the reason is not in the form this record takes")
    refuse("a reason that is not text", body(verdict="NO_CALL", reason=3, final_p=None), "the reason is not in the form this record takes")
    refuse("a gate that is unknown", body(gate="OTHER"), "the gate is one of ('NONE', 'GROUNDING')")
    for name in ("proposed_p", "final_p"):
        what = "the proposed probability" if name == "proposed_p" else "the final probability"
        for bad in ("0.1234567", "2", "-0.5", "0.", ".5", "1.1", "1.0000001", "01", "00.5", "1.", 0.5, 1):
            refuse(f"{name} of {bad!r}", body(**{name: bad}), form.format(what))
    refuse("a call with no probability", body(final_p=None), "a call has a probability and nothing else has")
    refuse(
        "a no-call with a probability",
        body(verdict="NO_CALL", reason="BELOW_MINIMUM_EDGE"),
        "a call has a probability and nothing else has",
    )
    refuse("a call with a reason", body(reason="BELOW_MINIMUM_EDGE"), "a call has no reason and everything else has one")
    refuse("a failure with no reason", body(verdict="FAILED", final_p=None), "a call has no reason and everything else has one")
    refuse("a version that is nothing", body(version=None), "the agent version is not in the form this record takes")
    refuse("a version that is not a hash", body(version="xyz"), "the agent version is not in the form this record takes")
    refuse("a cutoff of nothing", body(cutoff_ms=0), "the cutoff is a whole number from 1 to 86400000")
    refuse("a cutoff of more than a day", body(cutoff_ms=86_400_001), "the cutoff is a whole number from 1 to 86400000")
    refuse("a finishing time before the epoch", body(finished_at_ms=-1), "the finishing time is a whole number from 0 to 253402300799999")
    refuse(
        "a finishing time after year 9999",
        body(finished_at_ms=253_402_300_800_000),
        "the finishing time is a whole number from 0 to 253402300799999",
    )
    refuse("negative calls", body(calls=-1), "the number of calls is a whole number from 0 to 1000000000000")
    refuse("calls beyond the count limit", body(calls=10**12 + 1), "the number of calls is a whole number from 0 to 1000000000000")
    refuse("negative tokens", body(tokens=-1), "the billed tokens is a whole number from 0 to 1000000000000")
    c.eq("tokens at the limit", mod.to_record(body(tokens=10**12), SOURCE, WHEN)["body"]["tokens"], 10**12)
    note = "the debate has a debate note, the control a control note, and the single judgment neither"
    refuse("a judgment with a debate note", body(debate=debate()), note)
    refuse("a judgment with a control note", body(control=control()), note)
    refuse("a debate with no note", body(arm="L2"), note)
    refuse("a control with no note", body(arm="L1x"), note)
    refuse("a debate with a control note", body(arm="L2", control=control()), note)
    refuse("a control with a debate note", body(arm="L1x", debate=debate()), note)
    refuse("a note that is not an object", body(arm="L2", debate=[1]), "a note is an object")
    refuse("a control note that is not an object", body(arm="L1x", control="x"), "a note is an object")
    members_d = sorted(debate())
    refuse(
        "a debate note with a missing member",
        l2(debate={"collapsed": False, "failed_role": None}),
        f"a debate note has the members {members_d}",
    )
    refuse("a debate note with a further member", l2(debate=debate(extra=1)), f"a debate note has the members {members_d}")
    refuse("a collapsed flag that is a number", l2(debate=debate(collapsed=1)), "collapsed is true or false")
    refuse(
        "a failed role that is unknown",
        l2(debate=debate(failed_role="judge", disagreement=None)),
        "the failed role is one of ('bull', 'bear')",
    )
    refuse(
        "a disagreement that is not an object",
        l2(debate=debate(disagreement=[])),
        f"a disagreement has the members {sorted(debate()['disagreement'])}",
    )
    refuse(
        "a disagreement with a member short",
        l2(debate=debate(disagreement={"bull_only": []})),
        f"a disagreement has the members {sorted(debate()['disagreement'])}",
    )
    for name in debate()["disagreement"]:
        for bad, label in (([E2, E1], "out of order"), ([E1, E1], "twice"), ("x", "not a list")):
            refuse(
                f"{name} {label}",
                l2(debate=debate(disagreement={**debate()["disagreement"], name: bad})),
                f"{name} is a sorted list of evidence ids, once each",
            )
        refuse(
            f"{name} with an id that is not one",
            l2(debate=debate(disagreement={**debate()["disagreement"], name: ["nope"]})),
            f"{name} is not in the form this record takes",
        )
    refuse(
        "a lost side with a disagreement", l2(debate=debate(failed_role="bear")), "a debate that lost a side has no disagreement to record"
    )
    members_c = sorted(control())
    refuse("a control note with a missing member", l1x(control={"target_tokens": 1}), f"a control note has the members {members_c}")
    refuse(
        "a control note with a further member",
        l1x(control=control(extra=1)),
        f"a control note has the members {members_c}",
    )
    refuse("negative target tokens", l1x(control=control(target_tokens=-1)), "the target tokens is a whole number from 0 to 1000000000000")
    refuse("too many samples", l1x(control=control(samples=16)), "the samples is a whole number from 0 to 15")
    c.eq("fifteen samples", mod.to_record(l1x(control=control(samples=15)), SOURCE, WHEN)["body"]["control"]["samples"], 15)
    refuse("a mismatch flag that is a number", l1x(control=control(mismatch=0)), "mismatch is true or false")
    refuse("a control collapsed flag that is a number", l1x(control=control(collapsed=0)), "collapsed is true or false")
    c.refuses(
        "a creation time before the epoch", lambda: mod.to_record(body(), SOURCE, -1), "the creation time is a whole number of milliseconds"
    )
    c.refuses(
        "a creation time that is a flag", lambda: mod.to_record(body(), SOURCE, True), "the creation time is a whole number of milliseconds"
    )
    c.refuses(
        "a creation time after year 9999",
        lambda: mod.to_record(body(), SOURCE, 253_402_300_800_000),
        "the creation time is a whole number of milliseconds",
    )
    c.eq("a creation time at the epoch", mod.to_record(body(), SOURCE, 0)["created_at"], "1970-01-01T00:00:00.000Z")
    c.eq("a creation time at the limit", mod.to_record(body(), SOURCE, 253_402_300_799_999)["created_at"], "9999-12-31T23:59:59.999Z")
    c.refuses(
        "a source that is not a urn",
        lambda: mod.to_record(body(), "league", WHEN),
        "the record does not satisfy the envelope: source must be an ASCII urn",
    )
    return c.bad


def check_reading(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    good = mod.to_record(body(), SOURCE, WHEN)

    def changed(change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        r = copy.deepcopy(good)
        change(r)
        return r

    c.eq("a record the ledger has committed is read", mod.from_record({**good, "committed_at": "2026-10-30T11:01:00.000Z"}), body())
    c.refuses(
        "another type",
        lambda: mod.from_record(changed(lambda r: r.update(type="model_call"))),
        "not an arm run record of a version this reader knows",
    )
    c.refuses(
        "another version",
        lambda: mod.from_record(changed(lambda r: r.update(type_version=2))),
        "not an arm run record of a version this reader knows",
    )
    c.refuses(
        "another id",
        lambda: mod.from_record(changed(lambda r: r.update(id="ar" + "a" * 26))),
        "the id is not the id of what the record says",
    )
    c.refuses(
        "a body that was edited",
        lambda: mod.from_record(changed(lambda r: r["body"].update(verdict="MAYBE"))),
        "the verdict is one of ('CALL', 'NO_CALL', 'FAILED', 'FAILED_EVIDENCE', 'FAILED_LATE')",
    )
    c.refuses(
        "a body that lost a member",
        lambda: mod.from_record(changed(lambda r: r["body"].pop("tokens"))),
        f"the body has the members {sorted(body())}",
    )
    c.refuses(
        "a bad envelope",
        lambda: mod.from_record(changed(lambda r: r.update(created_at="yesterday"))),
        "the envelope: created_at: " + _why(good, changed),
    )
    c.refuses(
        "an envelope member that should not be there",
        lambda: mod.from_record(changed(lambda r: r.update(stray=1))),
        "the envelope: unknown envelope members: ['stray']",
    )
    return c.bad


def _why(good: dict[str, Any], changed: Callable[..., dict[str, Any]]) -> str:
    try:
        envelope.validate_producer_record(changed(lambda r: r.update(created_at="yesterday")))
    except envelope.EnvelopeError as exc:
        return str(exc).removeprefix("created_at: ")
    return ""


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_roundtrip, check_refusals, check_reading):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
