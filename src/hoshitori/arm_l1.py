"""Arm L1: one typed judgment per candidate and horizon (``LEAGUE_EXPERIMENT`` section 2; ``AGENT_PROTOCOL`` sections 2 and 3).

The arm reads the frozen snapshot through ``arm_input`` (observations only, every item shown as data), asks the model once
for a typed answer (``model_client``: a decision, a probability, invalidation conditions, the items it used, typed assertions
and a short summary of its own words), and then lets deterministic code decide what that proposal becomes:

* **The proposal and the final action are both kept.** The model proposes; the gate disposes. A proposal whose assertions are
  false against the items it was shown (``grounding``: a refuted number, a wrong unit or window, a dropped sign, a negation
  or a hedge dropped) is never turned into a forecast: the final action is an abstention for an unproven essential premise,
  and the report says which assertion. Assertions that are merely unverified are shown and marked, and decide nothing.
* **Failures are failures, not abstentions.** A model that could not answer (unavailable, truncated, empty, refused, invalid
  after its one repair, budget spent) ends the slot in a failure with its reason; a required source missing ends it in
  ``FAILED_EVIDENCE`` before any model call. Only the model's own choice, or the grounding gate, is an abstention.
* **The model never picks a tool, a size or a price.** Citations are checked against the items shown, an invalidation can
  only name a field whose meaning is registered, and the probability is a number in a stage-checked range that is clipped
  only by the scoring.

The version hash that stands for the arm in a forecast covers the arm, the prompt version, the system text, the answer
schema, the model and its parameters, so a change of any of them is a new agent.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final, cast

from hoshitori import assertions, canonical, crosscheck, grounding, typed
from hoshitori.arm_input import ArmView, build_view
from hoshitori.evidence import EvidenceItem
from hoshitori.model_client import Failure, ModelCall, ModelClient, ModelRequest, Outcome, Section, TokenBudget
from hoshitori.snapshot import Candidate, Reader

ARM: Final = "L1"
STAGE: Final = "l1_judgment"
PROMPT_VERSION: Final = "l1-v5"
SYSTEM: Final = (
    "You are one forecaster in a controlled comparison. You are given observations about one asset, taken at one moment, and nothing "
    "else. You forecast a probability; you do not trade. Use only the items given. Give a probability whenever the items let you form any "
    "view: 0.5 is the right answer when they show no edge either way. Abstain only if the items contradict one another in a way you "
    "cannot weigh, if a premise you need is not shown by them, or if the question is out of scope, and say which. "
    "A CALL has p, one to three invalidations (a field of an item, above or below a level that would prove you wrong) and the ids "
    "of the items you used in cites. "
    "Every number you state about the evidence goes in a typed assertion that names the item and the field; your own words go only in "
    "the summary and in event, mechanism or opinion assertions. Cite only the ids listed in the task. "
    + assertions.GUIDE
    + " Reply with the JSON object and nothing else."
)
ABSTENTIONS: Final = (
    "EVIDENCE_CONFLICT_UNRESOLVED",
    "ESSENTIAL_PREMISE_UNPROVEN",
    "OUT_OF_SCOPE",
)  # a thin edge is a probability near 0.5, not an abstention
MAX_INVALIDATIONS: Final = 3
MAX_CITES: Final = 16
MAX_SUMMARY: Final = 600


class Verdict(StrEnum):
    CALL = "CALL"
    NO_CALL = "NO_CALL"
    FAILED_EVIDENCE = "FAILED_EVIDENCE"
    FAILED = "FAILED"


class Reason(StrEnum):
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    MODEL_TRUNCATED = "MODEL_TRUNCATED"
    MODEL_EMPTY = "MODEL_EMPTY"
    MODEL_REFUSED = "MODEL_REFUSED"
    BUDGET_SPENT = "BUDGET_SPENT"
    EVIDENCE_CONFLICT_UNRESOLVED = "EVIDENCE_CONFLICT_UNRESOLVED"
    ESSENTIAL_PREMISE_UNPROVEN = "ESSENTIAL_PREMISE_UNPROVEN"
    BELOW_MINIMUM_EDGE = "BELOW_MINIMUM_EDGE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class Gate(StrEnum):
    NONE = "NONE"
    GROUNDING = "GROUNDING"


FAILURE_REASON: Final[Mapping[Outcome, Reason]] = {
    Outcome.UNAVAILABLE: Reason.MODEL_UNAVAILABLE,
    Outcome.REPLAY_MISSING: Reason.MODEL_UNAVAILABLE,
    Outcome.UNREADABLE: Reason.MODEL_UNAVAILABLE,
    Outcome.INVALID: Reason.MODEL_OUTPUT_INVALID,
    Outcome.TRUNCATED: Reason.MODEL_TRUNCATED,
    Outcome.EMPTY: Reason.MODEL_EMPTY,
    Outcome.REFUSED: Reason.MODEL_REFUSED,
    Outcome.BUDGET: Reason.BUDGET_SPENT,
}


@dataclass(frozen=True)
class Config:
    model: str
    max_tokens: int = 6000
    temperature: str | None = "0"
    seed: int | None = None
    response_format: str | None = "json_schema"
    timeout_s: float = 120.0
    thinking: bool | None = None  # whether the model reasons before it answers; none leaves it to the provider


# The fields an invalidation may name: those with a registered meaning (``grounding``). The schema itself carries the list, so a provider
# that enforces its schema (the endpoint does; C1) cannot name another, and the one rule is the schema's.
INVALIDATION_FIELDS: Final = tuple(grounding.FIELD_FACTS)
_INVALIDATION = typed.Object(
    (
        typed.Field("field", typed.closed(INVALIDATION_FIELDS)),
        typed.Field("op", typed.closed(("above", "below"))),
        typed.Field("level", typed.Number(Decimal(-(10**12)), Decimal(10**12), 8)),
    )
)
SCHEMA: Final = typed.Object(
    (
        typed.Field("decision", typed.closed(("CALL", "NO_CALL"))),
        typed.Field("p", typed.Number(Decimal(0), Decimal(1), 6), False),
        typed.Field("no_call_reason", typed.closed(ABSTENTIONS), False),
        typed.Field("invalidations", typed.Items(_INVALIDATION, 0, MAX_INVALIDATIONS)),
        typed.Field("cites", typed.Items(typed.Text(28, 28, None, assertions.EVIDENCE_ID), 0, MAX_CITES, True)),
        typed.Field("assertions", assertions.ASSERTIONS_SCHEMA),
        typed.Field("summary", typed.Text(MAX_SUMMARY), False),
    )
)


@dataclass(frozen=True)
class Proposal:
    """What the model said, after the stage's checks."""

    decision: str
    p: Decimal | None
    no_call_reason: str | None
    invalidations: tuple[str, ...]  # rendered
    cites: tuple[str, ...]
    assertions: tuple[assertions.Assertion, ...]
    summary: str | None


@dataclass(frozen=True)
class Decision:
    candidate_id: str
    horizon_hours: int
    verdict: Verdict
    reason: Reason | None
    p: Decimal | None
    evidence_ids: tuple[str, ...]
    observation_set_hash: str
    invalidation: tuple[str, ...]
    agent_version: str
    proposal: Proposal | None
    gate: Gate
    report: grounding.Report | None
    calls: tuple[ModelCall, ...]


def agent_version_hash(config: Config) -> str:
    """The hash that stands for this arm in a forecast: the arm, the prompt, the answer schema, the model, how it is asked and the tolerance
    of the price-conflict gate."""
    return hashlib.sha256(
        canonical.dumps(
            {
                "arm": ARM,
                "prompt_version": PROMPT_VERSION,
                "system_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
                "schema": typed.json_schema(SCHEMA),
                "model": config.model,
                "max_tokens": config.max_tokens,
                "temperature": config.temperature,
                "seed": config.seed,
                "response_format": config.response_format,
                "thinking": config.thinking,
                "price_conflict_gate": crosscheck.TOLERANCE,  # two sources that state one price and differ by more stand the arm down
            }
        )
    ).hexdigest()


def render_invalidation(raw: Mapping[str, Any]) -> str:
    return f"{raw['field']} {raw['op']} {assertions.plain(raw['level'])}"


def make_check(view: ArmView) -> Any:
    """The stage's own rules across members, as a check the client runs on an answer that already fits the schema."""
    shown = set(view.evidence_ids)

    def check(value: dict[str, Any]) -> None:
        if value["decision"] == "CALL":
            if "p" not in value:
                raise typed.TypedError("$.p: a call has a probability")
            if "no_call_reason" in value:
                raise typed.TypedError("$.no_call_reason: a call has no reason to abstain")
            if not value["invalidations"]:
                raise typed.TypedError("$.invalidations: a call has at least one invalidation condition")
            if not value["cites"]:
                raise typed.TypedError("$.cites: a call cites the items it used")
        else:
            if "no_call_reason" not in value:
                raise typed.TypedError("$.no_call_reason: an abstention has a reason")
            if "p" in value:
                raise typed.TypedError("$.p: an abstention has no probability")
        for i, ident in enumerate(value["cites"]):
            if ident not in shown:
                raise typed.TypedError(f"$.cites[{i}]: is not one of the citable ids")
        try:
            assertions.from_values(value["assertions"])
        except assertions.AssertionShapeError as exc:
            raise typed.TypedError(f"$.assertions: {exc}") from None

    return check


def build_request(
    config: Config,
    view: ArmView,
    *,
    stage: str = STAGE,
    prompt_version: str = PROMPT_VERSION,
    system: str = SYSTEM,
    extra: tuple[Section, ...] = (),
) -> ModelRequest:
    """The request for a judgment: the view's sections, then any further sections (a judge's debate), under the stage's own prompt."""
    return ModelRequest(
        stage=stage,
        prompt_version=prompt_version,
        system=system,
        sections=(*view.sections, *extra),
        schema=SCHEMA,
        model=config.model,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        seed=config.seed,
        response_format=config.response_format,
        thinking=config.thinking,
        timeout_s=config.timeout_s,
        check=make_check(view),
    )


def _proposal(value: dict[str, Any]) -> Proposal:
    return Proposal(
        value["decision"],
        value.get("p"),
        value.get("no_call_reason"),
        tuple(render_invalidation(c) for c in value["invalidations"]),
        tuple(value["cites"]),
        assertions.from_values(value["assertions"]),
        value.get("summary"),
    )


def decide(
    client: ModelClient,
    reader: Reader,
    candidate: Candidate,
    symbol: str,
    horizon_ms: int,
    snapshot_at_ms: int,
    config: Config,
    budget: TokenBudget | None = None,
) -> Decision:
    """The decision of arm L1 for one candidate and one horizon."""
    view = build_view(reader, candidate, symbol, horizon_ms, snapshot_at_ms)
    version = agent_version_hash(config)
    if not view.complete:
        return missing(candidate, view, version)
    stood = crosscheck.conflicts(reader.items(candidate.candidate_id))
    if stood:
        return conflicted(candidate, view, version, stood, reader.set_hash(candidate.candidate_id, stood))
    return propose(client, reader, candidate, view, build_request(config, view), version, budget)


def missing(candidate: Candidate, view: ArmView, version: str) -> Decision:
    """The slot of a candidate with a required source missing: ``FAILED_EVIDENCE`` before any model is asked."""
    return Decision(
        candidate.candidate_id,
        view.horizon_hours,
        Verdict.FAILED_EVIDENCE,
        Reason.MISSING_EVIDENCE,
        None,
        (),
        view.set_hash,
        (),
        version,
        None,
        Gate.NONE,
        None,
        (),
    )


def conflicted(candidate: Candidate, view: ArmView, version: str, stood: tuple[str, ...], seen: str) -> Decision:
    """The slot of a candidate on which two sources state one fact and disagree: a no-call before any model is asked, standing on the price
    checks that found it. A conflict between sources is not something a model's wording can settle."""
    return Decision(
        candidate.candidate_id,
        view.horizon_hours,
        Verdict.NO_CALL,
        Reason.EVIDENCE_CONFLICT_UNRESOLVED,
        None,
        stood,
        seen,
        (),
        version,
        None,
        Gate.NONE,
        None,
        (),
    )


def propose(
    client: ModelClient,
    reader: Reader,
    candidate: Candidate,
    view: ArmView,
    request: ModelRequest,
    version: str,
    budget: TokenBudget | None = None,
) -> Decision:
    """Ask for a judgment and let deterministic code decide what it becomes (the proposal and the final action are both kept)."""
    base = (candidate.candidate_id, view.horizon_hours)
    out = client.generate(request, budget)
    if isinstance(out, Failure):
        return Decision(
            *base, Verdict.FAILED, FAILURE_REASON[out.outcome], None, (), view.set_hash, (), version, None, Gate.NONE, None, out.calls
        )
    answer = out
    proposal = _proposal(answer.value)
    items: dict[str, EvidenceItem] = {ident: reader.get(ident, candidate.candidate_id) for ident in view.evidence_ids}
    report = grounding.ground(proposal.assertions, items, candidate.asset_id, proposal.summary or "")
    used = reader.set_hash(candidate.candidate_id, proposal.cites)
    if report.false_statements:
        return Decision(
            *base,
            Verdict.NO_CALL,
            Reason.ESSENTIAL_PREMISE_UNPROVEN,
            None,
            proposal.cites,
            used,
            (),
            version,
            proposal,
            Gate.GROUNDING,
            report,
            answer.calls,
        )
    if proposal.decision == "NO_CALL":
        reason = Reason(cast(str, proposal.no_call_reason))
        return Decision(*base, Verdict.NO_CALL, reason, None, proposal.cites, used, (), version, proposal, Gate.NONE, report, answer.calls)
    return Decision(
        *base,
        Verdict.CALL,
        None,
        proposal.p,
        proposal.cites,
        used,
        proposal.invalidations,
        version,
        proposal,
        Gate.NONE,
        report,
        answer.calls,
    )
