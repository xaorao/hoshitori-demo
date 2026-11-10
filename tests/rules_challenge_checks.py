"""Checks of the challenge of the rules arm (``hoshitori.rules_challenge``), runnable against a mutant."""

from __future__ import annotations

import dataclasses
import types
from decimal import Decimal
from typing import Any

from hoshitori import rules_arm
from tests.rules_arm_checks import FEATURES, graded, rates, reader_with

D = Decimal


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def decision(**over: Any) -> Any:
    base = rules_arm.Decision(
        "c",
        1,
        rules_arm.Verdict.CALL,
        None,
        rules_arm.Action.LONG,
        D("0.6"),
        "UP",
        ("e1",),
        "h",
        "m",
        (),
    )
    return dataclasses.replace(base, **over)


def check_classify(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    call = decision()
    c.eq("the same call", mod.classify(call, decision()), "SAME_DECISION")
    c.eq("another action", mod.classify(call, decision(action=rules_arm.Action.FLAT)), "DECISION_CHANGED")
    c.eq("another probability", mod.classify(call, decision(p=D("0.55"))), "DECISION_CHANGED")
    c.eq("another state does not change a call by itself", mod.classify(call, decision(state="DOWN")), "SAME_DECISION")
    lost = decision(verdict=rules_arm.Verdict.NO_CALL, reason=rules_arm.Reason.NO_RANGE, action=None, p=None, state=None)
    c.eq("a call lost to no call", mod.classify(call, lost), "DECISION_LOST")
    failed = decision(verdict=rules_arm.Verdict.FAILED_EVIDENCE, reason=rules_arm.Reason.NO_FEATURES, action=None, p=None, state=None)
    c.eq("a call lost to missing evidence is evidence the rule requires", mod.classify(call, failed), "EVIDENCE_REQUIRED")
    c.eq("no call to a call", mod.classify(lost, call), "DECISION_GAINED")
    c.eq("a failure to a call", mod.classify(failed, call), "DECISION_GAINED")
    c.eq("the same non-call", mod.classify(lost, lost), "SAME_DECISION")
    # A no call is the rule's own choice to abstain; a failure is the rule unable to decide for lack of evidence it requires
    # (RULES_ARM_SPEC section 5), and that is EVIDENCE_REQUIRED whatever the rule said with it (EVIDENCE_CHALLENGE_SPEC section 4).
    c.eq(
        "a no call that becomes a failure for missing evidence is evidence the rule requires",
        mod.classify(lost, failed),
        "EVIDENCE_REQUIRED",
    )
    unmapped = decision(verdict=rules_arm.Verdict.NO_CALL, reason=rules_arm.Reason.UNMAPPED, action=None, p=None, state="S3")
    c.eq("an unmapped no call left without its features", mod.classify(unmapped, failed), "EVIDENCE_REQUIRED")
    unknown = decision(verdict=rules_arm.Verdict.FAILED_EVIDENCE, reason=rules_arm.Reason.FEATURE_UNKNOWN, action=None, p=None, state=None)
    c.eq("an unmapped no call left with a feature unknown", mod.classify(unmapped, unknown), "EVIDENCE_REQUIRED")
    c.eq("the same failure is the same", mod.classify(failed, failed), "SAME_DECISION")
    other = decision(verdict=rules_arm.Verdict.NO_CALL, reason=rules_arm.Reason.UNMAPPED, action=None, p=None, state=None)
    # Only a change of what the rule does (its call, or whether it makes one) is a changed decision: a rule that could not
    # decide and still cannot, or that still makes no call, for a different reason, has not changed its decision.
    c.eq("a different reason for no call is the same decision", mod.classify(lost, other), "SAME_DECISION")
    c.eq("a failure that stays a failure for another reason is the same decision", mod.classify(failed, unknown), "SAME_DECISION")
    c.eq("a failure that becomes a no call is the same decision", mod.classify(failed, lost), "SAME_DECISION")
    c.eq(
        "a no call that becomes a call is gained, however it began",
        (mod.classify(lost, call), mod.classify(failed, call)),
        ("DECISION_GAINED",) * 2,
    )
    note = " What the rule said changed: before, {}; now, {}."
    base = "Removing it did not change the decision."

    def said(original: Any, changed: Any) -> str:
        return mod.Challenge(mod.classify(original, changed), original, changed, None).words()  # type: ignore[arg-type]

    c.eq(
        "the words of a no call for another reason name both reasons",
        said(lost, other),
        base
        + note.format(
            "it made no call because the hourly range is zero", "it made no call because the state has too few windows behind it"
        ),
    )
    c.eq(
        "and of a failure that becomes a no call",
        said(failed, lost),
        base
        + note.format("it could not decide because the candidate has no features item", "it made no call because the hourly range is zero"),
    )
    c.eq(
        "and of a failure for another reason",
        said(failed, unknown),
        base
        + note.format(
            "it could not decide because the candidate has no features item", "it could not decide because a feature it needs is unknown"
        ),
    )
    c.eq("a decision that did not move needs no note", (said(call, decision()), said(lost, lost)), (base, base))
    c.eq(
        "a call that moved is a change, with the words it always had",
        said(call, decision(action=rules_arm.Action.FLAT)),
        "Removing it changed the decision.",
    )
    c.eq("the words", mod.WORDS[mod.SAME], "Removing it did not change the decision.")
    c.eq(
        "all words",
        sorted(mod.WORDS),
        sorted(["SAME_DECISION", "DECISION_CHANGED", "DECISION_LOST", "DECISION_GAINED", "EVIDENCE_REQUIRED", "NOT_EVALUATED"]),
    )
    c.eq(
        "the sentences",
        [mod.WORDS[k] for k in ("DECISION_CHANGED", "DECISION_LOST", "DECISION_GAINED", "EVIDENCE_REQUIRED", "NOT_EVALUATED")],
        [
            "Removing it changed the decision.",
            "Removing it left the rule with no call.",
            "Removing it let the rule make a call it could not make with it.",
            "The rule cannot decide without it: it is evidence the rule requires.",
            "This could not be evaluated: something left in the evidence still stands on what was removed.",
        ],
    )
    c.eq("frozen", mod.Challenge.__dataclass_params__.frozen, True)
    return c.bad


def snap_of(extra_features: bool = False, decoy: bool = False) -> tuple[Any, Any, Any, str, list[str]]:
    w, reader, _, feats = reader_with(dict(FEATURES), extra_features=extra_features)
    base = reader._snapshot
    extra = []
    if decoy:
        extra = [w.add(w.bars("dec", "tk-btc", source="decoy-source"))]
        base = dataclasses.replace(
            base,
            slots=(dataclasses.replace(base.slots[0], item_ids=(*base.slots[0].item_ids, *extra)),),
            items=(*base.items, *((w.store.get(i), w.store.digest(i)) for i in extra)),
        )
    return w, base, graded(rules_arm), feats, extra


def check_challenge(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w, snap, mapping, feats, _ = snap_of(decoy=False)
    minute = next(item.evidence_id for item, _ in snap.items if item.source == "binance-spot" and item.evidence_id != feats)
    r = mod.challenge(snap, w.store, (minute,), mapping, "cd-btc", 1)
    c.eq(
        "the raw candles go and the rule cannot decide",
        (r.outcome, r.original.verdict.value, r.changed.verdict.value, r.changed.reason.value),
        ("EVIDENCE_REQUIRED", "CALL", "FAILED_EVIDENCE", "NO_FEATURES"),
    )
    c.eq("the words", r.words(), "The rule cannot decide without it: it is evidence the rule requires.")
    c.eq(
        "the intervention is kept",
        (r.intervention.status, minute in r.intervention.withheld, feats in r.intervention.withheld),
        ("READY", True, True),
    )
    r = mod.challenge(snap, w.store, (feats,), mapping, "cd-btc", 1)
    c.eq("the features item itself", r.outcome, "EVIDENCE_REQUIRED")
    # the state of the snapshot (a return of nothing) has too few windows: the rule's answer is a no call, as on every 24-hour row
    sparse = rates(rules_arm, lambda h, s: (99 if s == "S3" else 100, 50, 0))
    r = mod.challenge(snap, w.store, (minute,), sparse, "cd-btc", 1)
    c.eq(
        "a no call whose candles go is evidence the rule requires, not a change of decision",
        (r.outcome, r.original.verdict.value, r.original.reason.value, r.changed.verdict.value, r.changed.reason.value),
        ("EVIDENCE_REQUIRED", "NO_CALL", "UNMAPPED", "FAILED_EVIDENCE", "NO_FEATURES"),
    )
    c.eq("and says so", r.words(), "The rule cannot decide without it: it is evidence the rule requires.")
    r = mod.challenge(snap, w.store, (feats,), sparse, "cd-btc", 1)
    c.eq("a no call whose features item goes", r.outcome, "EVIDENCE_REQUIRED")
    w2, snap2, mapping2, _feats2, decoy = snap_of(decoy=True)
    r = mod.challenge(snap2, w2.store, tuple(decoy), mapping2, "cd-btc", 1)
    c.eq("an item the rule never read", (r.outcome, r.original == r.changed), ("SAME_DECISION", True))
    c.eq("and the original is a call", r.original.verdict.value, "CALL")
    r = mod.challenge(snap2, w2.store, tuple(decoy), rates(rules_arm, lambda h, s: (99 if s == "S3" else 100, 50, 0)), "cd-btc", 1)
    c.eq("an item the rule never read leaves a no call a no call", (r.outcome, r.original.verdict.value), ("SAME_DECISION", "NO_CALL"))
    w3, snap3, mapping3, feats3, _ = snap_of(extra_features=True)
    second = next(x.evidence_id for x, _ in snap3.items if x.evidence_id != feats3 and x.source == "hoshitori.features")
    r = mod.challenge(snap3, w3.store, (second,), mapping3, "cd-btc", 1)
    c.eq(
        "a duplicate that stopped the rule from deciding",
        (r.original.verdict.value, r.changed.verdict.value, r.outcome),
        ("FAILED_EVIDENCE", "CALL", "DECISION_GAINED"),
    )
    from tests.intervention_checks import Liar

    liar = Liar()
    for ident in sorted(w.store.ids(), key=lambda i: len(w.store.ancestors(i))):
        liar.add(w.store.get(ident), w.store.digest(ident))
    r = mod.challenge(snap, liar, (minute,), mapping, "cd-btc", 1)
    c.eq(
        "a lineage that cannot be trusted is not evaluated",
        (r.outcome, r.changed, r.original.verdict.value, r.intervention.status),
        ("NOT_EVALUATED", None, "CALL", "NOT_EVALUATED"),
    )
    c.eq("and says so", r.words(), "This could not be evaluated: something left in the evidence still stands on what was removed.")
    c.eq("the original snapshot is untouched", len(snap.items), 3)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (check_classify, check_challenge)
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
