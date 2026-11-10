"""Did taking this evidence away change what a model arm decided? (``EVIDENCE_CHALLENGE_SPEC`` sections 3 to 5, arm L1.)

A model gives a different answer every time it is asked, so one run says nothing. The question is asked in pairs: the arm decides
on the full snapshot and, as the pair's other half, on the snapshot with the evidence and everything derived from it taken out.
A third run on the full snapshot, again, measures the model against itself (the control): a shift smaller than that is variation.
The actions go to ``paired_stats``, which returns the test, the interval and the label over the whole paired table of actions.

Two things happen before any model is asked. If taking the evidence out leaves something that still stands on it, nothing is
evaluated (``intervention``). If taking it out leaves the arm without a source it requires, the answer is known without a run: the
arm ends the slot in ``FAILED_EVIDENCE``, and that is reported as evidence the arm requires, not as a model's change of mind.

A run that errors or gives nothing usable is never left out: it is the action ``RUN_FAILED`` in its pair, so a removal that makes
the arm fail is a change like any other, and the share of failed runs decides whether anything is evaluated at all.

When the label is a change, the pair's other layer says who changed it: the model's own proposal, or the gate that decides what a
proposal becomes. Each is asked in the pair itself (the in-pair design): did it change when the evidence was taken out more often
than it changed when the arm was simply asked again? Only a change of the proposal is the model changing its judgment.

Every run is its own request (section 2, steps 3 to 5; section 6 stores the seeds): pair ``i`` asks its full run and its run without the
evidence with the seed index ``i``, so the two differ in the evidence alone, and its control with the extra index ``runs + i``, so the
control is the model asked again and not the full run's request repeated. ``seeds`` says the seed of each run. A run that repeated
another's request would be one answer twice: the gateway gives identical calls in flight one fetch, and a tape keeps one answer per request.

The decision function is passed in, so this module asks no model itself; the league's arm, a scripted stand-in and a replay all fit.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Final, cast

from hoshitori import paired_stats
from hoshitori.arm_l1 import Decision, Gate, Reason, Verdict
from hoshitori.intervention import NOT_EVALUATED, READY, Withheld, add_placebo, withhold
from hoshitori.lineage import EvidenceStore
from hoshitori.model_gateway import MAX_SEED
from hoshitori.snapshot import Access, Reader, Snapshot

REQUIRED: Final = "EVIDENCE_REQUIRED"
FULL: Final = "full"
CONTROL: Final = "control"
WITHHELD: Final = "withheld"
RUN_FAILED: Final = "RUN_FAILED"  # the action of a run that errored or gave nothing usable: kept in its pair, never dropped
MODEL_REVISED: Final = "MODEL_REVISED"
GATE_BLOCKED: Final = "GATE_BLOCKED"
BOTH: Final = "BOTH"
VARIATION_ONLY: Final = "VARIATION_ONLY"
HALF: Final = Decimal("0.5")
RUNS_PER_PAIR: Final = 3  # the full run, the run without the evidence, and the control

DecideFn = Callable[[Reader, int, str], Decision]


class ChallengeError(ValueError):
    """A challenge that asks for nothing it can answer."""


@dataclass(frozen=True)
class ModelChallenge:
    outcome: str  # a label of paired_stats, EVIDENCE_REQUIRED, or NOT_EVALUATED(...)
    verdict: paired_stats.Verdict | None
    mechanism: str | None  # who changed the action, when it changed
    treated: paired_stats.Pairs | None  # "gave the original action": full run against the run without the evidence
    control: paired_stats.Pairs | None  # the same, full run against the control run
    failed: int  # runs that errored or gave nothing usable, each kept in its pair as RUN_FAILED
    runs: int  # pairs asked for; each is RUNS_PER_PAIR runs
    intervention: Withheld


def action_class(decision: Decision) -> str | None:
    """The action a decision amounts to; none for a run that failed for a reason of its own, which is not an answer."""
    if decision.verdict is Verdict.FAILED:
        return None
    if decision.verdict is Verdict.CALL and decision.p is not None:
        return "UP" if decision.p > HALF else "DOWN" if decision.p < HALF else "FLAT"
    return decision.verdict.value


def proposal_class(decision: Decision) -> str | None:
    """The action the model proposed before the gate decided what it became."""
    if decision.verdict is Verdict.FAILED:
        return None
    if decision.verdict is Verdict.FAILED_EVIDENCE:
        return decision.verdict.value
    proposal = decision.proposal
    if proposal is None:
        return action_class(decision)
    if proposal.decision == "CALL" and proposal.p is not None:
        return "UP" if proposal.p > HALF else "DOWN" if proposal.p < HALF else "FLAT"
    return "NO_CALL"


def _gated(decision: Decision) -> bool:
    return decision.gate is not Gate.NONE


def seeds(configured: int | None, runs: int) -> dict[tuple[str, int], int]:
    """The seed of every run of a challenge of ``runs`` pairs, by its arm and pair, pair by pair (section 2, steps 3 to 5).

    The full run and the run without the evidence of pair ``i`` have the seed index ``i``; its control has the extra index ``runs + i``. A
    seed is the arm's configured seed, or 0 when it has none, plus the index, and every seed is one a call takes (0 to ``MAX_SEED``).
    """
    if runs < 1:
        raise ChallengeError("a challenge needs at least one pair of runs")
    base = 0 if configured is None else configured
    last = base + 2 * runs - 1
    if base < 0 or last > MAX_SEED:
        raise ChallengeError(f"the seeds of {runs} pairs from {base} are {base} to {last}, and a call takes a seed from 0 to {MAX_SEED}")
    return {(arm, i): base + (runs + i if arm == CONTROL else i) for i in range(runs) for arm in (FULL, WITHHELD, CONTROL)}


def challenge(
    snapshot: Snapshot, store: EvidenceStore, removed: tuple[str, ...], decide: DecideFn, original: str, runs: int
) -> ModelChallenge:
    """Ask the arm ``runs`` times in pairs, with and without the evidence, and say what changed.

    ``original`` is the action class of the committed decision; a pair counts as unchanged when its run gave that action. ``decide`` is
    called with the reader, the pair and the arm; a decision function that asks a model asks run ``i`` of ``arm`` with the seed
    ``seeds(configured, runs)[(arm, i)]``, where ``configured`` is the arm's own seed.
    """
    if runs < 1:
        raise ChallengeError("a challenge needs at least one pair of runs")
    taken = withhold(snapshot, store, removed)
    if taken.snapshot is None:
        return ModelChallenge(NOT_EVALUATED, None, None, None, None, 0, 0, taken)
    return _pairs(snapshot, taken, decide, original, runs, required_check=True)


def placebo_challenge(
    snapshot: Snapshot, store: EvidenceStore, candidate_id: str, like: tuple[str, ...], decide: DecideFn, original: str, runs: int
) -> ModelChallenge:
    """The control of a challenge that takes evidence out: ask the arm in pairs on the evidence and on the evidence with a neutral item
    added to the candidate (``intervention.add_placebo``, as long as the text of the items ``like``), and say whether the decision
    moved. A decision that moves when only text of that size is added moves for a reason that has nothing to do with what the evidence
    said. Nothing is taken out, so nothing can be required and no mechanism is looked for."""
    if runs < 1:
        raise ChallengeError("a challenge needs at least one pair of runs")
    placed = add_placebo(snapshot, candidate_id, like)
    shown = Withheld(READY, placed.snapshot, (), (), ())
    return _pairs(snapshot, shown, decide, original, runs, required_check=False)


def _pairs(snapshot: Snapshot, taken: Withheld, decide: DecideFn, original: str, runs: int, *, required_check: bool) -> ModelChallenge:
    """The pairs: the full run, the run on the what-if snapshot and the control, ``runs`` times, and what they say."""
    what_if = cast(Snapshot, taken.snapshot)
    full = Reader(snapshot, Access.INDEPENDENT)
    without = Reader(what_if, Access.INDEPENDENT)
    probe = decide(without, 0, WITHHELD)
    if required_check and probe.verdict is Verdict.FAILED_EVIDENCE and probe.reason is Reason.MISSING_EVIDENCE:
        return ModelChallenge(REQUIRED, None, None, None, None, 0, 0, taken)
    actions: list[paired_stats.Run] = []  # per pair: the action of the full run, of the run without the evidence, of the control
    proposals: list[tuple[bool, bool]] = []  # per pair: the proposal moved without the evidence; it moved when asked again
    gates: list[tuple[bool, bool]] = []  # the same for whether the gate stepped in
    failed = 0
    for i in range(runs):
        trio = (decide(full, i, FULL), probe if i == 0 else decide(without, i, WITHHELD), decide(full, i, CONTROL))
        failed += [action_class(x) for x in trio].count(None)
        a, b, c = (action_class(x) or RUN_FAILED for x in trio)
        actions.append((a, b, c))
        pa, pb, pc = (proposal_class(x) or RUN_FAILED for x in trio)
        proposals.append((pb != pa, pc != pa))
        ga, gb, gc = (_gated(x) for x in trio)
        gates.append((gb != ga, gc != ga))
    verdict = paired_stats.label_table(actions, original, failed, attempted=RUNS_PER_PAIR * runs)
    mechanism = _mechanism(proposals, gates) if verdict.label == paired_stats.CHANGED else None
    treated = paired_stats.table(actions, original, paired_stats.CHANGED_RUN)
    control = paired_stats.table(actions, original, paired_stats.CONTROL_RUN)
    return ModelChallenge(verdict.label, verdict, mechanism, treated, control, failed, runs, taken)


def _moved(moves: list[tuple[bool, bool]]) -> bool:
    """Whether the pairs changed when the evidence was taken out more often than when the arm was simply asked again: the exact test on
    the pairs where only one of the two changed, at the level of a change of the decision (which already has the pairs it needs)."""
    only_removed = sum(1 for removed, anyway in moves if removed and not anyway)
    only_anyway = sum(1 for removed, anyway in moves if anyway and not removed)
    return only_removed > only_anyway and paired_stats.mcnemar_exact(only_removed, only_anyway) <= paired_stats.ALPHA


def _mechanism(proposals: list[tuple[bool, bool]], gates: list[tuple[bool, bool]]) -> str:
    """Who changed the action: the model's proposal moved beyond its own variation, the gate moved, both or neither."""
    moved, gated = _moved(proposals), _moved(gates)
    if moved and gated:
        return BOTH
    if moved:
        return MODEL_REVISED
    if gated:
        return GATE_BLOCKED
    return VARIATION_ONLY


@dataclass(frozen=True)
class Member:
    """One removal of a standard set: what it took out, what the challenge found, and the value of its test alone and corrected."""

    name: str
    removed: tuple[str, ...]
    result: ModelChallenge  # relabelled after the correction where the correction changes it
    raw_p: float | None  # none when the removal was not evaluated or the arm requires what was taken out
    adjusted_p: float | None  # Holm's value over the removals that were evaluated


def standard_set(
    snapshot: Snapshot,
    store: EvidenceStore,
    groups: Sequence[tuple[str, tuple[str, ...]]],
    decide: DecideFn,
    original: str,
    runs: int,
) -> tuple[Member, ...]:
    """Each group of evidence taken out in its own challenge, and the family of tests corrected with Holm's method.

    Trying several removals and reading the best is how a shift that is only variation looks like a finding; so the value of each test is
    corrected for the others. A removal that was not evaluated, or that takes out something the arm requires, has no test and is not one
    of the family. A challenge that was a change alone and is not after the correction is uncertain, with the reason, and its
    mechanism (who changed the action) is dropped: there is no change to explain.
    """
    if not groups:
        raise ChallengeError("a standard set has at least one group")
    names = [name for name, _ in groups]
    if len(set(names)) != len(names):
        raise ChallengeError("each group of a standard set is named once")
    results = [challenge(snapshot, store, removed, decide, original, runs) for _, removed in groups]
    tested = [k for k, r in enumerate(results) if r.verdict is not None and r.treated is not None]
    raw = {k: float(results[k].verdict.p) for k in tested}  # type: ignore[union-attr]
    adjusted = dict(zip(tested, paired_stats.holm_adjusted([raw[k] for k in tested]), strict=True))
    members: list[Member] = []
    for k, (name, removed) in enumerate(groups):
        result = results[k]
        if (
            k in adjusted
            and result.verdict is not None
            and result.verdict.label == paired_stats.CHANGED
            and adjusted[k] > paired_stats.ALPHA
        ):
            result = replace(
                result,
                outcome=paired_stats.UNCERTAIN,
                mechanism=None,
                verdict=replace(
                    result.verdict,
                    label=paired_stats.UNCERTAIN,
                    note="significant alone, not after the correction over the removals tried",
                ),
            )
        members.append(Member(name, tuple(removed), result, raw.get(k), adjusted.get(k)))
    return tuple(members)
