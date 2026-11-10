"""Did taking this evidence away change what the rules arm decided? (contract 13; exact for this arm.)

The rules arm is a fixed function of the evidence it is shown, so the question has an exact answer and needs no repeated runs, no
interval and no control: take the evidence out (with everything derived from it), ask the same function again, and compare. The answer
is a statement about the rule on this snapshot. It is not a statement about the market, and not about any model arm, which are asked
differently and have not been evaluated here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from hoshitori import rules_arm
from hoshitori.intervention import NOT_EVALUATED, Withheld, withhold
from hoshitori.lineage import EvidenceStore
from hoshitori.snapshot import Access, Reader, Snapshot

SAME: Final = "SAME_DECISION"
CHANGED: Final = "DECISION_CHANGED"
LOST: Final = "DECISION_LOST"
GAINED: Final = "DECISION_GAINED"
REQUIRED: Final = "EVIDENCE_REQUIRED"
WORDS: Final = {
    SAME: "Removing it did not change the decision.",
    CHANGED: "Removing it changed the decision.",
    LOST: "Removing it left the rule with no call.",
    GAINED: "Removing it let the rule make a call it could not make with it.",
    REQUIRED: "The rule cannot decide without it: it is evidence the rule requires.",
    NOT_EVALUATED: "This could not be evaluated: something left in the evidence still stands on what was removed.",
}


REASON_WORDS: Final = {
    rules_arm.Reason.UNMAPPED: "the state has too few windows behind it",
    rules_arm.Reason.NO_RANGE: "the hourly range is zero",
    rules_arm.Reason.NO_FEATURES: "the candidate has no features item",
    rules_arm.Reason.FEATURE_UNKNOWN: "a feature it needs is unknown",
}


def _said(decision: rules_arm.Decision) -> str:
    """What a rule that did not make a call said, in words: it could not decide, or chose no call, and why."""
    start = "it could not decide" if decision.verdict is rules_arm.Verdict.FAILED_EVIDENCE else "it made no call"
    return start if decision.reason is None else f"{start} because {REASON_WORDS[decision.reason]}"


@dataclass(frozen=True)
class Challenge:
    outcome: str
    original: rules_arm.Decision
    changed: rules_arm.Decision | None  # none when the intervention could not be evaluated
    intervention: Withheld

    def words(self) -> str:
        """The label in words; where the rule made no call before and after, or could not decide, and what it said is not what it said
        before, the sentence names both (that is not a changed decision, and it is not hidden)."""
        text = WORDS[self.outcome]
        changed = self.changed
        if (
            self.outcome == SAME
            and changed is not None
            and self.original.verdict is not rules_arm.Verdict.CALL
            and (self.original.verdict, self.original.reason) != (changed.verdict, changed.reason)
        ):
            text += f" What the rule said changed: before, {_said(self.original)}; now, {_said(changed)}."
        return text


def classify(original: rules_arm.Decision, changed: rules_arm.Decision) -> str:
    """How the decision after the intervention differs from the one before.

    A rule left unable to decide for lack of evidence (``FAILED_EVIDENCE``) when it was not before has lost evidence it requires: that
    is ``EVIDENCE_REQUIRED`` whether it had made a call or chosen no call (EVIDENCE_CHALLENGE_SPEC section 4 names the label by the
    source removed, not by the decision; RULES_ARM_SPEC section 5: a no call is the rule's choice, a failure is not), never a change of
    the decision."""
    failed = rules_arm.Verdict.FAILED_EVIDENCE
    if changed.verdict is failed and original.verdict is not failed:
        return REQUIRED
    was, now = original.verdict is rules_arm.Verdict.CALL, changed.verdict is rules_arm.Verdict.CALL
    if was and not now:
        return LOST
    if now and not was:
        return GAINED
    if not was:  # neither made a call: a no call, or a failure to decide, for another reason is not a change of the decision
        return SAME
    same = (original.verdict, original.action, original.p, original.reason) == (changed.verdict, changed.action, changed.p, changed.reason)
    return SAME if same else CHANGED


def challenge(
    snapshot: Snapshot, store: EvidenceStore, removed: tuple[str, ...], mapping: rules_arm.BaseRates, candidate_id: str, hours: int
) -> Challenge:
    """Ask the rules arm the same question with the evidence removed, and say what changed."""
    original = rules_arm.decide(Reader(snapshot, Access.INDEPENDENT), candidate_id, mapping, hours)
    taken = withhold(snapshot, store, removed)
    if taken.snapshot is None:
        return Challenge(NOT_EVALUATED, original, None, taken)
    changed = rules_arm.decide(Reader(taken.snapshot, Access.INDEPENDENT), candidate_id, mapping, hours)
    return Challenge(classify(original, changed), original, changed, taken)
