"""The file of a challenge of the single-judgment model arm (``model_challenge``): one writer, one strict reader, and the rows the What if
page shows.

A challenge asks the arm many times, in pairs, with and without a source (``EVIDENCE_CHALLENGE_SPEC`` sections 2 to 5), so its result is
only as good as its runs. The file holds every run: its arm and pair, its seed, what the arm decided and what its proposal was, whether
the gate stepped in, the items it cited, and the tape of its model calls (the digest of each request, and the capture and digest of each
answer, which the capture store keeps; section 6: a result can be shown again without asking again). ``record`` writes the file from
the challenge, the decision of every run and what was asked. ``read`` refuses anything ``record`` would not write and re-derives, from the
runs alone, everything the file states: each run's action, the failed runs, the two paired tables, the label with its interval and its
test (``paired_stats``), who changed the action (``model_challenge``) and the seed of every run (``model_challenge.seeds`` of the arm's
configured seed). A file whose stated result its runs do not give is refused, so a page can never show a result its runs did not produce.

The file is a canonical record (``canonical``, the record profile) and one newline: one content has one byte form, so ``read`` can refuse
every other form of the same content, a key given twice and a number with a fraction, and the file can be named by its digest. The
numbers of the verdict are the shortest text of the float that round-trips (Python's ``repr``), compared exactly with what the runs give.
Every text in it is an id, a digest, a code of the arm or a version: no prompt, no answer and no free text.

``bind`` holds a file to the ledger a site is made from: its forecast must be the single judgment's committed forecast of that cycle,
asset and horizon, with the same agent version and action, and the items it says were taken out must be the ones the ledger's own
snapshot gives for that source. ``for_site`` binds every file given and makes the rows of the What if page. What a challenge says is
decision sensitivity only (section 1): whether this arm's decision moved without the source on this evidence, never that the model used
the source or anything about the market.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Final, cast

from hoshitori import (
    arm_l1,
    assertions,
    canonical,
    evidence,
    forecast,
    intervention,
    labels,
    lineage,
    model_challenge,
    model_client,
    model_gateway,
    paired_stats,
    snapshot,
)
from hoshitori.arm_l1 import Gate, Reason, Verdict
from hoshitori.capture import CAPTURE_ID
from hoshitori.model_challenge import CONTROL, FULL, RUN_FAILED, WITHHELD
from hoshitori.model_client import Outcome
from hoshitori.rules_arm import HORIZONS

FORMAT: Final = 1
HOUR_MS: Final = 3_600_000
KEYS: Final = tuple(
    sorted(
        (
            "format",
            "arm",
            "cycle",
            "forecast",
            "symbol",
            "hours",
            "source",
            "original",
            "pairs",
            "model",
            "prompt_version",
            "temperature",
            "seed",
            "agent_version",
            "removed",
            "withheld",
            "leaks",
            "outcome",
            "mechanism",
            "failed",
            "treated",
            "control",
            "verdict",
            "runs",
        )
    )
)
RUN_KEYS: Final = ("action", "arm", "calls", "cites", "gate", "i", "p", "proposal_p", "reason", "seed", "verdict")
CALL_KEYS: Final = ("args_digest", "attempt", "capture_id", "outcome", "request_digest", "response_digest")
TABLE_KEYS: Final = ("changed_changed", "changed_same", "same_changed", "same_same")
VERDICT_KEYS: Final = ("high", "low", "n", "note", "p", "shift")
ORIGINALS: Final = ("UP", "DOWN", "FLAT")
OUTCOMES: Final = (
    paired_stats.CHANGED,
    paired_stats.STABLE,
    paired_stats.UNCERTAIN,
    paired_stats.UNSTABLE_BASELINE,
    paired_stats.STRUCTURAL,
    model_challenge.REQUIRED,
    intervention.NOT_EVALUATED,
)
MECHANISMS: Final = (model_challenge.MODEL_REVISED, model_challenge.GATE_BLOCKED, model_challenge.BOTH, model_challenge.VARIATION_ONLY)
NO_ANSWER: Final = frozenset({Outcome.UNAVAILABLE, Outcome.REPLAY_MISSING, Outcome.BUDGET})  # a call that brought no answer has no capture
ARMS: Final = (FULL, WITHHELD, CONTROL)
_VERDICTS: Final = tuple(v.value for v in Verdict)
_REASONS: Final = tuple(r.value for r in Reason)
_GATES: Final = tuple(g.value for g in Gate)
_OUTCOMES: Final = tuple(o.value for o in Outcome)
_CYCLE: Final = re.compile(r"cy[a-z2-7]{26}")
_FORECAST: Final = re.compile(r"fc[a-z2-7]{26}")
_EVIDENCE: Final = re.compile(assertions.EVIDENCE_ID)
_HEX: Final = re.compile(r"[0-9a-f]{64}")
_P: Final = re.compile(r"(?:0|1)(?:\.[0-9]{1,6})?")  # the arm's p: a number from 0 to 1 with at most six places (arm_l1.SCHEMA)
# The rules of the modules that make these values, used here and not copied: a source name (evidence), a model id and a temperature (the
# model gateway, which refuses a call with any other) and a prompt version (the model client).
_SOURCE: Final = evidence._SOURCE
_MODEL: Final = model_gateway._MODEL
_TEMPERATURE: Final = model_gateway._TEMPERATURE
_VERSION: Final = model_client._VERSION


class ChallengeFileError(ValueError):
    """A challenge file that cannot be written or shown."""


@dataclass(frozen=True)
class Asked:
    """What was challenged and how the arm was asked: the committed forecast, the source taken out, the pairs and the arm's settings."""

    cycle: str
    forecast: str
    symbol: str
    hours: int
    source: str
    original: str  # the committed action: UP, DOWN or FLAT
    pairs: int
    model: str
    prompt_version: str
    temperature: str | None
    seed: int | None  # the arm's configured seed; every run's seed is model_challenge.seeds of it
    agent_version: str  # arm_l1.agent_version_hash of the arm as configured (each run's own differs by its seed)


@dataclass(frozen=True)
class Call:
    attempt: int
    outcome: str
    request_digest: str  # of the first attempt of the run
    args_digest: str  # of this attempt
    capture_id: str | None
    response_digest: str | None


@dataclass(frozen=True)
class Run:
    arm: str
    i: int
    seed: int
    verdict: str
    reason: str | None
    p: str | None
    proposal_p: str | None  # the probability the model proposed; none for a proposal of no call, and for a run with no proposal
    gate: str
    action: str
    cites: tuple[str, ...]
    calls: tuple[Call, ...]


@dataclass(frozen=True)
class Challenge:
    asked: Asked
    removed: tuple[str, ...]
    withheld: tuple[str, ...]  # what left the snapshot: what was removed and everything derived from it
    leaks: tuple[str, ...]  # what stayed and still stands on what was removed; when there is any, nothing is evaluated
    outcome: str
    mechanism: str | None
    failed: int
    attempted: int  # the runs asked: three a pair, one when the first run without the source finds it required, none for a leak
    treated: paired_stats.Pairs | None
    control: paired_stats.Pairs | None
    verdict: paired_stats.Verdict | None
    runs: tuple[Run, ...]


# --- writing -------------------------------------------------------------------------------------------------------------------------
def _seeds(asked: Asked) -> dict[tuple[str, int], int]:
    try:
        return model_challenge.seeds(asked.seed, asked.pairs)
    except model_challenge.ChallengeError as exc:
        raise ChallengeFileError(str(exc)) from exc


def _table(pairs: paired_stats.Pairs | None) -> dict[str, int] | None:
    return None if pairs is None else {name: getattr(pairs, name) for name in TABLE_KEYS}


def _verdict(verdict: paired_stats.Verdict | None) -> dict[str, Any] | None:
    if verdict is None:
        return None
    return {
        "high": repr(verdict.high),
        "low": repr(verdict.low),
        "n": verdict.n,
        "note": verdict.note,
        "p": repr(verdict.p),
        "shift": repr(verdict.shift),
    }


def _run(arm: str, i: int, seed: int, d: arm_l1.Decision) -> dict[str, Any]:
    return {
        "action": model_challenge.action_class(d) or RUN_FAILED,
        "arm": arm,
        "calls": [
            {
                "args_digest": c.args_digest,
                "attempt": c.attempt,
                "capture_id": c.capture_id,
                "outcome": c.outcome.value,
                "request_digest": c.request_digest,
                "response_digest": c.response_digest,
            }
            for c in d.calls
        ],
        "cites": list(d.evidence_ids),
        "gate": d.gate.value,
        "i": i,
        "p": None if d.p is None else str(d.p),
        "proposal_p": None if d.proposal is None or d.proposal.p is None else str(d.proposal.p),
        "reason": None if d.reason is None else d.reason.value,
        "seed": seed,
        "verdict": d.verdict.value,
    }


def record(asked: Asked, result: model_challenge.ModelChallenge, decisions: Mapping[tuple[str, int], arm_l1.Decision]) -> bytes:
    """The file of one challenge: ``result`` as ``model_challenge.challenge`` returned it, every run the decision function gave
    (``decisions``, by arm and pair) and what was asked. It is read back before it is returned, so no file is made that ``read`` refuses."""
    seeds = _seeds(asked)
    if not set(decisions) <= set(seeds):
        raise ChallengeFileError("a decision is of no run of this challenge")
    runs = []
    for (arm, i), seed in seeds.items():  # pair by pair: full, withheld, control
        if (arm, i) in decisions:
            decision = decisions[(arm, i)]
            if any(c.seed != seed for c in decision.calls):
                raise ChallengeFileError(f"run {arm} {i}: a call was not asked with the run's seed {seed}")
            runs.append(_run(arm, i, seed, decision))
    removed, withheld = list(result.intervention.removed), list(result.intervention.withheld)
    if (
        asked.source == intervention.PLACEBO_SOURCE and result.intervention.snapshot is not None
    ):  # nothing was taken out: the file lists the item added
        removed = withheld = sorted(i.evidence_id for i, _ in result.intervention.snapshot.items if i.source == intervention.PLACEBO_SOURCE)
    doc: dict[str, Any] = {
        "agent_version": asked.agent_version,
        "arm": arm_l1.ARM,
        "control": _table(result.control),
        "cycle": asked.cycle,
        "failed": result.failed,
        "forecast": asked.forecast,
        "format": FORMAT,
        "hours": asked.hours,
        "leaks": list(result.intervention.leaks),
        "mechanism": result.mechanism,
        "model": asked.model,
        "original": asked.original,
        "outcome": result.outcome,
        "pairs": asked.pairs,
        "prompt_version": asked.prompt_version,
        "removed": removed,
        "runs": runs,
        "seed": asked.seed,
        "source": asked.source,
        "symbol": asked.symbol,
        "temperature": asked.temperature,
        "treated": _table(result.treated),
        "verdict": _verdict(result.verdict),
        "withheld": withheld,
    }
    data = canonical.dumps(doc) + b"\n"
    read(data)
    return data


# --- reading -------------------------------------------------------------------------------------------------------------------------
def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value: object, pattern: re.Pattern[str], what: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ChallengeFileError(what)
    return value


def _ids(value: object, what: str, least: int) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or len(value) < least
        or not all(isinstance(x, str) and _EVIDENCE.fullmatch(x) for x in value)
        or value != sorted(set(value))
    ):
        raise ChallengeFileError(f"the {what} items are {'at least one ' if least else ''}evidence ids, sorted and once each")
    return tuple(value)


def _asked(doc: Mapping[str, Any]) -> Asked:
    symbol, hours, temperature, seed = doc["symbol"], doc["hours"], doc["temperature"], doc["seed"]
    if not isinstance(symbol, str) or not symbol or symbol != symbol.upper() or len(symbol) > 40:  # the protocol's rule for a symbol
        raise ChallengeFileError("the symbol is an upper-case symbol of at most 40 characters")
    if not _whole(hours) or hours not in HORIZONS:
        raise ChallengeFileError("the horizon is 1, 4 or 24 hours")
    if doc["original"] not in ORIGINALS:
        raise ChallengeFileError("the committed action is UP, DOWN or FLAT")
    if not _whole(doc["pairs"]) or doc["pairs"] < 1:
        raise ChallengeFileError("the pairs are a whole number of at least 1")
    if temperature is not None and (not isinstance(temperature, str) or _TEMPERATURE.fullmatch(temperature) is None):
        raise ChallengeFileError("the temperature is null or a decimal from 0 to 2 with at most three places")
    if seed is not None and (not _whole(seed) or seed < 0):
        raise ChallengeFileError("the seed is null or a whole number of at least 0")
    return Asked(
        _text(doc["cycle"], _CYCLE, "the cycle is a cycle id"),
        _text(doc["forecast"], _FORECAST, "the forecast is a forecast id"),
        symbol,
        hours,
        _text(doc["source"], _SOURCE, "the source is a source name"),
        doc["original"],
        doc["pairs"],
        _text(doc["model"], _MODEL, "the model is a model id"),
        _text(doc["prompt_version"], _VERSION, "the prompt version is a version name"),
        temperature,
        seed,
        _text(doc["agent_version"], _HEX, "the agent version is a SHA-256 digest in lower-case hexadecimal"),
    )


def _calls(value: object, where: str) -> tuple[Call, ...]:
    """The tape of one run, in the form ``model_client.ModelClient.generate`` records it."""
    if not isinstance(value, list) or len(value) > 2:
        raise ChallengeFileError(f"{where}: a run has at most two calls: the answer and its one repair")
    out: list[Call] = []
    for attempt, c in enumerate(value, 1):
        if not isinstance(c, dict) or tuple(c) != CALL_KEYS:  # a canonical record's members are in order
            raise ChallengeFileError(f"{where}: a call has {', '.join(CALL_KEYS)}")
        if not _whole(c["attempt"]) or c["attempt"] != attempt:
            raise ChallengeFileError(f"{where}: the calls are attempt 1 and then attempt 2")
        if c["outcome"] not in _OUTCOMES:
            raise ChallengeFileError(f"{where}: a call's outcome is one of the client's")
        if not all(isinstance(c[name], str) and _HEX.fullmatch(c[name]) for name in ("request_digest", "args_digest")):
            raise ChallengeFileError(f"{where}: a digest is 64 hexadecimal digits in lower case")
        came = Outcome(c["outcome"]) not in NO_ANSWER
        if came != (c["capture_id"] is not None) or came != (c["response_digest"] is not None):
            raise ChallengeFileError(f"{where}: a call has a capture and the digest of its answer exactly when an answer came")
        if came and (
            not isinstance(c["capture_id"], str)
            or CAPTURE_ID.fullmatch(c["capture_id"]) is None
            or not isinstance(c["response_digest"], str)
            or _HEX.fullmatch(c["response_digest"]) is None
        ):
            raise ChallengeFileError(f"{where}: a capture is named by its id and an answer by its digest")
        out.append(Call(attempt, c["outcome"], c["request_digest"], c["args_digest"], c["capture_id"], c["response_digest"]))
    if out:
        first = out[0]
        if any(c.request_digest != first.args_digest for c in out):
            raise ChallengeFileError(f"{where}: every call names the first attempt's request")
        repaired = len(out) == 2
        if repaired != (first.outcome == Outcome.INVALID.value) or (repaired and out[1].args_digest == first.args_digest):
            raise ChallengeFileError(f"{where}: an invalid answer is repaired once, with a request of its own, and no other answer is")
    return tuple(out)


def _decision(run: Run) -> arm_l1.Decision:
    """A run as the arm's decision, enough for ``model_challenge`` to class its action and its proposal. A run that decided has a proposal
    (``arm_l1.propose``), and a proposal has a probability exactly when it is a call (``arm_l1.make_check``)."""
    proposal = None
    if run.verdict in (Verdict.CALL.value, Verdict.NO_CALL.value):
        decision = (Verdict.NO_CALL if run.proposal_p is None else Verdict.CALL).value  # the schema's words for a proposal
        proposal = arm_l1.Proposal(decision, None if run.proposal_p is None else Decimal(run.proposal_p), None, (), run.cites, (), None)
    return arm_l1.Decision(
        "",
        0,
        Verdict(run.verdict),
        None if run.reason is None else Reason(run.reason),
        None if run.p is None else Decimal(run.p),
        run.cites,
        "",
        (),
        "",
        proposal,
        Gate(run.gate),
        None,
        (),
    )


def _shape(run: Run, where: str) -> None:
    """What the arm can decide (``arm_l1.decide`` and ``arm_l1.propose``), with the calls that led to it."""
    last = run.calls[-1].outcome if run.calls else None
    answered = last == Outcome.VALID.value
    bare = run.p is None and run.proposal_p is None and run.gate == Gate.NONE.value and not run.cites
    if run.verdict == Verdict.CALL.value:
        if not (
            answered and run.p is not None and run.proposal_p == run.p and run.cites and run.reason is None and run.gate == Gate.NONE.value
        ):
            raise ChallengeFileError(
                f"{where}: a call comes from a valid answer, with its proposal's probability, at least one cite, no reason and no gate"
            )
    elif run.verdict == Verdict.NO_CALL.value and run.gate == Gate.GROUNDING.value:
        if not (answered and run.reason == Reason.ESSENTIAL_PREMISE_UNPROVEN.value and run.p is None):
            raise ChallengeFileError(
                f"{where}: a no-call of the grounding gate comes from a valid answer, with the reason ESSENTIAL_PREMISE_UNPROVEN and no "
                "probability"
            )
    elif run.verdict == Verdict.NO_CALL.value:
        if not (answered and run.reason in arm_l1.ABSTENTIONS and run.p is None and run.proposal_p is None):
            raise ChallengeFileError(
                f"{where}: the model's own no-call comes from a valid answer, with one of its reasons and no probability"
            )
    elif run.verdict == Verdict.FAILED.value:
        if not (last is not None and not answered and run.reason == arm_l1.FAILURE_REASON[Outcome(last)].value and bare):
            raise ChallengeFileError(
                f"{where}: a failed run has the reason of its last call's outcome, no probability, no proposal, no gate and no cite"
            )
    elif not (run.reason == Reason.MISSING_EVIDENCE.value and bare and not run.calls):
        raise ChallengeFileError(f"{where}: a run that lacks evidence asked no model and has the reason MISSING_EVIDENCE and nothing else")


def _run_of(value: object, seeds: Mapping[tuple[str, int], int]) -> Run:
    if not isinstance(value, dict) or tuple(value) != RUN_KEYS:  # a canonical record's members are in order
        raise ChallengeFileError(f"a run has {', '.join(RUN_KEYS)}")
    arm, i = value["arm"], value["i"]
    if arm not in ARMS or not _whole(i) or (arm, i) not in seeds:
        raise ChallengeFileError("a run is the full, withheld or control run of one of the challenge's pairs")
    where = f"run {arm} {i}"
    if not _whole(value["seed"]) or value["seed"] != seeds[(arm, i)]:
        raise ChallengeFileError(f"{where}: its seed is not {seeds[(arm, i)]}, the one the challenge gives it")
    reason = value["reason"]
    if value["verdict"] not in _VERDICTS or value["gate"] not in _GATES or (reason is not None and reason not in _REASONS):
        raise ChallengeFileError(f"{where}: the verdict, the reason and the gate are the arm's own words")
    for name in ("p", "proposal_p"):
        p = value[name]
        if p is not None and (not isinstance(p, str) or _P.fullmatch(p) is None or Decimal(p) > 1):
            raise ChallengeFileError(f"{where}: a probability is null or a decimal from 0 to 1 with at most six places")
    cites = value["cites"]
    if (
        not isinstance(cites, list)
        or len(cites) > arm_l1.MAX_CITES
        or not all(isinstance(x, str) and _EVIDENCE.fullmatch(x) for x in cites)
        or len(set(cites)) != len(cites)
    ):
        raise ChallengeFileError(f"{where}: the cites are at most {arm_l1.MAX_CITES} evidence ids, once each")
    calls = _calls(value["calls"], where)
    run = Run(arm, i, value["seed"], value["verdict"], reason, value["p"], value["proposal_p"], value["gate"], "", tuple(cites), calls)
    _shape(run, where)
    action = model_challenge.action_class(_decision(run)) or RUN_FAILED
    if value["action"] != action:
        raise ChallengeFileError(f"{where}: the action is not the one its decision gives")
    return Run(run.arm, run.i, run.seed, run.verdict, run.reason, run.p, run.proposal_p, run.gate, action, run.cites, run.calls)


Derived = tuple[str, str | None, int, paired_stats.Pairs | None, paired_stats.Pairs | None, paired_stats.Verdict | None]


def _tally(asked: Asked, runs: Sequence[Run]) -> Derived:
    """The result of the runs, as ``model_challenge.challenge`` makes it from the same decisions: the pairs' actions, the failed runs,
    the label over the whole paired table, the two tables of the original action and, for a change, who changed it."""
    by = {(r.arm, r.i): _decision(r) for r in runs}
    actions: list[paired_stats.Run] = []
    proposals: list[tuple[bool, bool]] = []
    gates: list[tuple[bool, bool]] = []
    for i in range(asked.pairs):
        trio = (by[(FULL, i)], by[(WITHHELD, i)], by[(CONTROL, i)])
        a, b, c = (model_challenge.action_class(x) or RUN_FAILED for x in trio)
        actions.append((a, b, c))
        pa, pb, pc = (model_challenge.proposal_class(x) or RUN_FAILED for x in trio)
        proposals.append((pb != pa, pc != pa))
        ga, gb, gc = (x.gate is not Gate.NONE for x in trio)
        gates.append((gb != ga, gc != ga))
    failed = sum(1 for d in by.values() if model_challenge.action_class(d) is None)
    verdict = paired_stats.label_table(actions, asked.original, failed, attempted=model_challenge.RUNS_PER_PAIR * asked.pairs)
    # who changed the action: the module's own in-pair test, used here and not copied
    mechanism = model_challenge._mechanism(proposals, gates) if verdict.label == paired_stats.CHANGED else None
    treated = paired_stats.table(actions, asked.original, paired_stats.CHANGED_RUN)
    control = paired_stats.table(actions, asked.original, paired_stats.CONTROL_RUN)
    return verdict.label, mechanism, failed, treated, control, verdict


def _same(stated: object, given: object) -> bool:
    """Whether two values are the same record values: true is not 1, as their canonical bytes say."""
    return canonical.dumps({"v": cast(canonical.JsonValue, stated)}) == canonical.dumps({"v": cast(canonical.JsonValue, given)})


def read(data: bytes) -> Challenge:
    """The challenge a file holds, every number and label in it derived again from its runs."""
    try:
        doc = canonical.parse_record(data)
    except canonical.CanonicalError as exc:
        raise ChallengeFileError(f"the challenge file is not a canonical record: {exc}") from exc
    if canonical.dumps(doc) + b"\n" != data:
        raise ChallengeFileError("the challenge file is not written in canonical form with one newline at its end")
    if tuple(doc) != KEYS:  # in canonical form, so its members are in order
        raise ChallengeFileError(f"a challenge file has {', '.join(KEYS)}")
    if not _whole(doc["format"]) or doc["format"] != FORMAT:
        raise ChallengeFileError(f"the challenge file is of format {FORMAT}")
    if doc["arm"] != arm_l1.ARM:
        raise ChallengeFileError("the arm is L1, the single judgment")
    asked = _asked(doc)
    seeds = _seeds(asked)
    removed = _ids(doc["removed"], "removed", 1)
    withheld = _ids(doc["withheld"], "withheld", 1)
    leaks = _ids(doc["leaks"], "leaking", 0)
    placebo = asked.source == intervention.PLACEBO_SOURCE  # nothing was taken out: the file lists the one neutral item that was added
    if placebo and (len(removed) != 1 or withheld != removed):
        raise ChallengeFileError("a placebo file lists the one item that was added")
    if placebo and leaks:
        raise ChallengeFileError("a placebo adds an item and removes nothing, so nothing can leak")
    if not set(removed) <= set(withheld) or set(leaks) & set(withheld):
        raise ChallengeFileError("what was removed was withheld, and nothing that still stands on it was")
    if not isinstance(doc["runs"], list):
        raise ChallengeFileError("the runs are a list")
    runs = tuple(_run_of(value, seeds) for value in doc["runs"])
    keys = [(r.arm, r.i) for r in runs]
    lacking = [r for r in runs if r.verdict == Verdict.FAILED_EVIDENCE.value]
    derived: Derived
    if leaks:  # the intervention leaks: nothing is asked (model_challenge.challenge)
        if runs:
            raise ChallengeFileError("nothing is evaluated when something left still stands on what was removed, so no run is asked")
        derived = (intervention.NOT_EVALUATED, None, 0, None, None, None)
    elif (
        keys == [(WITHHELD, 0)] and not placebo
    ):  # the first run without the source is asked alone, and the challenge stops there only if it is required
        if not lacking:
            raise ChallengeFileError("a challenge stops at its first run without the source only when that source is required")
        derived = (model_challenge.REQUIRED, None, 0, None, None, None)
    elif keys != list(seeds):
        raise ChallengeFileError("the runs are every pair's full, withheld and control run, pair by pair")
    elif (
        lacking and not placebo
    ):  # a lack of evidence is decided before any model is asked, from the snapshot alone (arm_l1.decide), so it is all or none
        raise ChallengeFileError("a run lacks evidence only when the source is required, and then it is the challenge's only run")
    else:
        derived = _tally(asked, runs)
    for run in runs:  # a run without the source can cite only what it was shown (arm_l1.make_check)
        if placebo:
            if run.arm != WITHHELD and set(run.cites) & set(withheld):
                raise ChallengeFileError(f"run {run.arm} {run.i}: it cites an item that was not shown to it")
        elif run.arm == WITHHELD and set(run.cites) & set(withheld):
            raise ChallengeFileError(f"run {run.arm} {run.i}: it cites an item that was taken out")
    requests = [r.calls[0].request_digest for r in runs if r.calls]
    if len(set(requests)) != len(requests):
        raise ChallengeFileError("two runs share one request, so one answer would stand for both")
    captures = [c.capture_id for r in runs for c in r.calls if c.capture_id is not None]
    if len(set(captures)) != len(captures):
        raise ChallengeFileError("two calls name one capture")
    outcome, mechanism, failed, treated, control, verdict = derived
    for name, given in (
        ("outcome", outcome),
        ("mechanism", mechanism),
        ("failed", failed),
        ("treated", _table(treated)),
        ("control", _table(control)),
        ("verdict", _verdict(verdict)),
    ):
        if not _same(doc[name], given):
            raise ChallengeFileError(f"the {name} the file states is not the one its runs give")
    return Challenge(asked, removed, withheld, leaks, outcome, mechanism, failed, len(runs), treated, control, verdict, runs)


# --- against the ledger, and for the page --------------------------------------------------------------------------------------------
def _action_of(p: Decimal) -> str:
    """The action class of a committed probability, as ``model_challenge.action_class`` classes a call."""
    return "UP" if p > model_challenge.HALF else "DOWN" if p < model_challenge.HALF else "FLAT"


def bind(challenge: Challenge, records: Sequence[Mapping[str, Any]]) -> None:
    """Refuse a challenge that is not of this ledger: its forecast must be the single judgment's committed one, and what it took out must be
    what the ledger's snapshot of that cycle gives for its source (taken out with everything derived from it, ``intervention.withhold``)."""
    a = challenge.asked
    found = next((r for r in records if r.get("type") == forecast.TYPE and r.get("id") == a.forecast), None)
    if found is None:
        raise ChallengeFileError(f"{a.forecast}: this ledger holds no such forecast")
    body = found["body"]
    said = (body.get("cycle_id"), body.get("arm_id"), body.get("symbol"), body.get("contract"), body.get("horizon_ms"))
    if said != (a.cycle, arm_l1.ARM, a.symbol, "D", a.hours * HOUR_MS) or body.get("agent_version_hash") != a.agent_version:
        raise ChallengeFileError(f"{a.forecast}: the ledger's forecast is not the one the file challenges")
    if _action_of(Decimal(body["p"])) != a.original:
        raise ChallengeFileError(f"{a.forecast}: the ledger's forecast is not the one the file challenges")
    shot = next((r for r in records if r.get("type") == snapshot.TYPE and r["body"]["cycle_id"] == a.cycle), None)
    manifest = next((r for r in records if r.get("type") == "manifest" and r["body"]["cycle_id"] == a.cycle), None)
    if shot is None or manifest is None:
        raise ChallengeFileError(f"{a.forecast}: this ledger holds no snapshot and manifest of its cycle")
    kept = [r for r in records if r.get("type") == "evidence"]
    try:
        frozen = snapshot.rebuild(shot, {r["id"]: r for r in kept})
    except snapshot.SnapshotError as exc:
        raise ChallengeFileError(f"{a.forecast}: the snapshot of its cycle cannot be rebuilt: {exc}") from exc
    store = lineage.rebuild(cast(list[dict[str, canonical.JsonValue]], [{k: v for k, v in r.items() if k != "committed_at"} for r in kept]))
    entry = next((e for e in manifest["body"]["entries"] if e["inclusion"] == "INCLUDED" and e["symbol"] == a.symbol), None)
    slot = None if entry is None else next((s for s in frozen.slots if s.candidate_id == entry["candidate_id"]), None)
    if slot is None:
        raise ChallengeFileError(f"{a.forecast}: {a.symbol} is not a candidate of its cycle")
    if (
        a.source == intervention.PLACEBO_SOURCE
    ):  # what the file lists is the neutral item that adds to this snapshot, which is one item whatever its size
        try:
            placed = intervention.add_placebo(frozen, slot.candidate_id, slot.item_ids[:1])
        except intervention.InterventionError as exc:
            raise ChallengeFileError(f"{a.forecast}: a placebo cannot be added to this ledger's snapshot: {exc}") from exc
        if challenge.removed != (placed.item_id,) or challenge.withheld != challenge.removed or challenge.leaks:
            raise ChallengeFileError(f"{a.forecast}: the item added is not the one this ledger's snapshot gives for a placebo")
        return
    items = {item.evidence_id: item for item, _ in frozen.items}
    removed = tuple(ident for ident in slot.item_ids if items[ident].source == a.source)  # withhold sorts them
    if not removed:
        raise ChallengeFileError(f"{a.forecast}: the snapshot holds nothing from {a.source} for {a.symbol}")
    taken = intervention.withhold(frozen, store, removed)
    if (taken.removed, taken.withheld, taken.leaks) != (challenge.removed, challenge.withheld, challenge.leaks):
        raise ChallengeFileError(f"{a.forecast}: the items taken out are not the ones this ledger's snapshot gives without {a.source}")


@dataclass(frozen=True)
class Row:
    """One challenge as the What if page shows it; the sentences are the page's (``web/src/format.ts``), from these codes and counts."""

    cycle_id: str
    forecast_id: str
    arm_name: str
    symbol: str
    hours: int
    source: str
    items: int  # how many items left the snapshot with the source, derived ones included
    original: str
    outcome: str
    mechanism: str | None
    pairs: int
    attempted: int
    failed: int
    treated: paired_stats.Pairs | None  # "gave the committed action": each pair's full run against its run without the source
    control: paired_stats.Pairs | None  # the same, against its control run on the full evidence
    shift: str | None  # the shift and its interval in whole percentage points, signed
    low: str | None
    high: str | None
    p: str | None  # the exact test, to two places
    model: str
    temperature: str | None


def points(value: float) -> str:
    """A share as signed whole percentage points, half to even; one that rounds to nothing is written without a sign."""
    shown = (Decimal(repr(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    return "0" if shown == 0 else f"{shown:+}"


def p_text(value: float) -> str:
    """A p-value to two places, half to even; one too small to show at two places says so instead of reading as zero."""
    shown = Decimal(repr(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    return "<0.01" if shown == 0 else str(shown)


def row(challenge: Challenge) -> Row:
    a, v = challenge.asked, challenge.verdict
    return Row(
        a.cycle,
        a.forecast,
        labels.ARM_NAMES[arm_l1.ARM],
        a.symbol,
        a.hours,
        a.source,
        len(challenge.withheld),
        a.original,
        challenge.outcome,
        challenge.mechanism,
        a.pairs,
        challenge.attempted,
        challenge.failed,
        challenge.treated,
        challenge.control,
        None if v is None else points(v.shift),
        None if v is None else points(v.low),
        None if v is None else points(v.high),
        None if v is None else p_text(v.p),
        a.model,
        a.temperature,
    )


def for_site(challenges: Sequence[Challenge], records: Sequence[Mapping[str, Any]]) -> tuple[Row, ...]:
    """The rows of the What if page: every challenge held to the ledger, each question once, in the order symbol, horizon, source."""
    asked: set[tuple[str, str]] = set()
    for challenge in challenges:
        bind(challenge, records)
        question = (challenge.asked.forecast, challenge.asked.source)
        if question in asked:
            raise ChallengeFileError(f"{question[0]} without {question[1]} is challenged twice")
        asked.add(question)
    ordered = sorted(challenges, key=lambda c: (c.asked.symbol, c.asked.hours, c.asked.source, c.asked.forecast))
    return tuple(row(c) for c in ordered)
