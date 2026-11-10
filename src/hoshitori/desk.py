"""A run of the Research Desk: from a trigger to a commitment, an abstention or a failure, leaving the decision log as it goes.

The steps, in this order: a hypothesis written from a closed template; checks chosen one at a time by the one next-check rule over the menu,
each run through the gateway and read by fixed rules; the falsification step, run before any decision; the decision, which a model may only
propose; the gates, which can only lower the proposal; and the commitment, which the caller makes. Everything outside the run is a port that
the caller passes in (what a check brings, the history, the model, the kernel's room, the commitment, where records go), so the same run
serves a live binding and a recorded one and is checked on planted data with a scripted model.

A run ends in exactly one terminal state: COMMITTED, ABSTAINED(reason), FAILED(reason), or FAILED_FINAL when a port broke. A required source
that is unavailable is a failure and never an abstention. A part that one origin family supports is put to a second family before the run
goes on, so that two sources that would dispute it are found out (a disagreement is MIXED, never averaged); corroboration is asked for
and not required: when no other family can be read or paid for, the part stands on the one that supports it and the record says so.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Final, cast

from hoshitori import canonical, desk_acquire, desk_gates, desk_interpret, desk_plan, desk_record, falsify
from hoshitori.claim_graph import Graph
from hoshitori.claim_next_check import Check, NotOnMenu, outcome, registry_from_menu, resolve_request, select_next_check
from hoshitori.clock import Clock
from hoshitori.desk_menu import Menu, MenuCheck, check_family
from hoshitori.ids import natural_id
from hoshitori.lineage import EvidenceStore, observation_set_hash
from hoshitori.timeline import to_ms

MODEL_FAILURES: Final = ("MODEL_UNAVAILABLE", "MODEL_OUTPUT_INVALID")
EXPLANATION_CLASS: Final = "ANNOUNCEMENT"
COMPETING: Final = (
    ("MARKET_WIDE_MOVE", "The token moves with the market."),
    ("EARLIER_ONSET", "The move began before the announcement."),
)
METHODS: Final = {"T_ONSET": "one-look", "T_MARKET": "beta-window", "T_SOURCE": "support-label", "T_PLACEBO_TIME": "placebo-time"}
EMPTY_DIGEST: Final = hashlib.sha256(b"").hexdigest()
SCREEN_DETAILS: Final = {  # what the gate says of each reading; a screen that could not be made earns no gate
    "FLAGGED": "the screen of the token's contract lists it",
    "NO_FLAG_FOUND": "no source lists the address; this is not a safety rating",
    "INCOMPLETE": "some sources did not answer: not a clean screen",
}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class DeskError(ValueError):
    """A run that cannot be started, or a commitment that must not be made."""


def _count(value: object, what: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DeskError(f"{what} is a whole number, 0 or more")


@dataclass(frozen=True)
class Budget:
    calls: Mapping[str, int]  # by provider
    seconds: int
    tokens: int

    def __post_init__(self) -> None:
        for provider, count in self.calls.items():
            _count(count, f"the calls of {provider}")
        for name in ("seconds", "tokens"):
            _count(getattr(self, name), name)


@dataclass(frozen=True)
class Binding:
    """Where a run reads and what its records say: the only difference between a live run and a replay of recordings."""

    label: str

    def __post_init__(self) -> None:
        if self.label not in desk_record.LABELS:
            raise DeskError(f"the binding is one of {desk_record.LABELS}")


@dataclass(frozen=True)
class Versions:
    agent_version_hash: str
    code_version: str
    protocol_hash: str

    def __post_init__(self) -> None:
        for name in ("agent_version_hash", "code_version", "protocol_hash"):
            value = getattr(self, name)
            if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                raise DeskError(f"{name} is a SHA-256 digest")


@dataclass(frozen=True)
class History:
    """One-minute returns of the token (and of a market proxy) up to the horizon, with the minute of the announcement in them."""

    token: tuple[float, ...] | list[float]
    market: tuple[float, ...] | list[float] | None
    ta: int
    h: int


@dataclass(frozen=True)
class ProposeRequest:
    """What the model is shown: the statement, how each part stands, the tests run and the evidence there is. Never a ceiling."""

    hypothesis: desk_plan.Hypothesis
    statuses: Mapping[str, str]
    tests: tuple[tuple[str, str], ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class Proposed:
    proposal: desk_gates.Proposal
    cites: tuple[str, ...]
    model_outputs_digest: str
    tokens: int


@dataclass(frozen=True)
class ModelFailure:
    reason: str

    def __post_init__(self) -> None:
        if self.reason not in MODEL_FAILURES:
            raise DeskError(f"a model failure is one of {MODEL_FAILURES}")


@dataclass(frozen=True)
class CommitRequest:
    run_id: str
    final: desk_gates.FinalAction
    falsification: dict[str, canonical.JsonValue]
    decision: dict[str, canonical.JsonValue]
    label: str


@dataclass(frozen=True)
class Ports:
    acquire: Callable[[MenuCheck, desk_acquire.RunContext], desk_acquire.Acquired]
    store: EvidenceStore
    history: Callable[[desk_acquire.RunContext], History | None]
    propose: Callable[[ProposeRequest], Proposed | ModelFailure]
    grounded: Callable[[Proposed, tuple[str, ...]], bool]
    stale: Callable[[desk_acquire.RunContext], bool]
    room: Callable[[desk_acquire.RunContext], Decimal]
    commit: Callable[[CommitRequest], str]
    sink: Callable[[str, dict[str, canonical.JsonValue]], None]
    calibration: falsify.Calibration | None
    clock: Clock


@dataclass(frozen=True)
class RunOutcome:
    state: str
    reason: str | None
    forecast_id: str | None
    records: tuple[tuple[str, dict[str, canonical.JsonValue]], ...]


def guard_commit(weight: Decimal, ceiling: Decimal) -> None:
    """A forecast whose weight is above the falsification record's ceiling is refused before it is committed."""
    if weight > ceiling:
        raise DeskError("a commitment may not be above the ceiling of its falsification record")


def _text(value: Decimal) -> str:
    return format(value, "f")


def _items(values: Iterable[canonical.JsonValue]) -> list[canonical.JsonValue]:
    """A list of JSON values from any iterable of them (a list of strings is not a list of JSON values to the type checker)."""
    return list(values)


class _Run:
    """The state of one run: what it has spent, what it has written and how each part stands."""

    def __init__(
        self, ports: Ports, trigger: desk_plan.Trigger, menu: Menu, budget: Budget, run_id: str, binding: Binding, versions: Versions
    ) -> None:
        self.ports, self.trigger, self.menu, self.budget = ports, trigger, menu, budget
        self.run_id, self.binding, self.versions = run_id, binding, versions
        self.started = ports.clock.now()
        self.calls: dict[str, int] = {}
        self.tokens = 0
        self.seq = 0  # the number of the next check record, whatever kind of check it is
        self.records: list[tuple[str, dict[str, canonical.JsonValue]]] = []
        # the hypotheses have one part a check can settle, so a check record never lists settled parts
        self.ctx = desk_acquire.RunContext(run_id, menu.asset_id or "", menu.symbol or "", self.started + timedelta(seconds=budget.seconds))

    # --- records ---------------------------------------------------------------------------------------------------------------
    def write(self, kind: str, body: dict[str, canonical.JsonValue]) -> None:
        desk_record.check_body(kind, body)
        self.records.append((kind, body))
        self.ports.sink(kind, body)

    def seconds_spent(self) -> int:
        return int((self.ports.clock.now() - self.started).total_seconds())

    def state(self, state: str, reason: str | None = None, forecast_id: str | None = None) -> None:
        spent: dict[str, canonical.JsonValue] = {
            "calls": dict(sorted(self.calls.items())),
            "seconds": self.seconds_spent(),
            "tokens": self.tokens,
        }
        budget: dict[str, canonical.JsonValue] = {
            "calls": dict(sorted(self.budget.calls.items())),
            "seconds": self.budget.seconds,
            "tokens": self.budget.tokens,
        }
        self.write(
            "desk_run_state",
            {
                "run_id": self.run_id,
                "state": state,
                "reason": reason,
                "at_ms": to_ms(self.ports.clock.now()),
                "regime": "OPERATIONAL",
                "label": self.binding.label,
                "asset_id": self.menu.asset_id,
                "symbol": self.menu.symbol,
                "discovery_route": self.trigger.route,
                "trigger_id": self.trigger.trigger_id,
                "policy": "P-DEPENDENCY",
                "calibration": "UNCALIBRATED",
                "menu_sha256": self.menu.sha256,
                "budget": budget,
                "spent": spent,
                "agent_version_hash": self.versions.agent_version_hash,
                "code_version": self.versions.code_version,
                "protocol_hash": self.versions.protocol_hash,
                "forecast_id": forecast_id,
            },
        )

    def end(self, state: str, reason: str | None = None, forecast_id: str | None = None) -> RunOutcome:
        self.state(state, reason, forecast_id)
        return RunOutcome(state, reason, forecast_id, tuple(self.records))

    # --- the checks ------------------------------------------------------------------------------------------------------------
    def affordable(self, check: MenuCheck) -> bool:
        left = self.budget.calls.get(check.provider, 0) - self.calls.get(check.provider, 0)
        remaining = self.budget.seconds - self.seconds_spent()
        worst = Decimal(check.cost.worst_seconds) if check.cost.worst_seconds is not None else Decimal(0)
        return check.cost.calls <= left and remaining > 0 and worst <= remaining

    def check_loop(self, hypothesis: desk_plan.Hypothesis) -> tuple[dict[str, str], list[str], str | None]:
        """Run checks until the next-check rule has none to offer. Returns each part's status, the evidence ids, and the closed word that
        ended the run if it ended there (a stop is returned as ``None``)."""
        by_id = {c.check_id: c for c in self.menu.checks}
        registry = registry_from_menu(self.menu)
        graph = hypothesis.graph
        seen: dict[str, list[str]] = {}
        own: dict[str, str] = {}
        holding: dict[str, set[str]] = {}  # the origin families whose check supports a part
        tried: set[str] = set()
        evidence_ids: list[str] = []
        first = True  # no check of the claim has been made yet
        while True:
            offered = tuple(c for c in registry if c.check_id in tried or self.affordable(by_id[c.check_id]))
            wanted = self.corroboration(graph, own, holding, offered, by_id, tried)
            asked = own
            if wanted is not None:  # the part stays open to the rule, and only a check that can corroborate it is offered for it
                node_for, candidates = wanted
                asked = {**own, node_for: "UNPROVEN"}
                kind = by_id[candidates[0]].node_type
                offered = tuple(
                    c for c in offered if by_id[c.check_id].node_type != kind or c.check_id in candidates or c.check_id in tried
                )
            next_check = select_next_check(graph, asked, offered, frozenset(tried))
            if next_check.status != "CHECK" and select_next_check(graph, own, registry, frozenset(tried)).status == "CHECK":
                self.state("STOPPED", "BUDGET_EXHAUSTED")  # a check is wanted and the budget cannot pay for any
                return own, evidence_ids, "ESSENTIAL_PREMISE_UNPROVEN"
            ended = outcome(next_check)
            if ended is not None:
                return own, evidence_ids, None if ended[0] == "STOP" else ended[1]
            node = cast(str, next_check.node_id)
            check = resolve_request(registry, cast(Check, next_check.check).check_id)
            menu_check = by_id[check.check_id]
            before = own.get(node, "UNCHECKED")
            corroborating = wanted is not None and node == wanted[0]
            acquired = self.ports.acquire(menu_check, self.ctx)
            self.calls[menu_check.provider] = self.calls.get(menu_check.provider, 0) + acquired.spent
            tried.add(check.check_id)
            update = desk_interpret.interpret(menu_check, node, hypothesis.direction, hypothesis.hours, acquired, self.ports.store)
            seen.setdefault(node, []).append(update.status)
            own[node] = desk_interpret.combine(seen[node])
            if update.status == "SUPPORTED":
                holding.setdefault(node, set()).add(check_family(menu_check))
            evidence_ids.extend(acquired.evidence_ids)
            self.write(
                "desk_check",
                {
                    "run_id": self.run_id,
                    "seq": self.seq,
                    "node_id": node,
                    "node_type": "OBSERVATION",
                    "check_id": check.check_id,
                    "check_type": menu_check.check_type,
                    "provider": menu_check.provider,
                    "operation": menu_check.operation,
                    "origin_family": check_family(menu_check),
                    "args": [_items(pair) for pair in menu_check.args],
                    "chosen_by": "DEPENDENCY_ORDER",
                    "why": {
                        "template_id": "CORROBORATE" if corroborating else "FIRST_OPEN_PART" if first else "NEXT_UNTRIED_CHECK",
                        "settled": [],
                    },
                    "alternatives": _items({"check_id": a, "rule": "LATER_IN_PREFERENCE_ORDER"} for a in next_check.alternatives),
                    "cost_expected": {"calls": menu_check.cost.calls, "seconds": menu_check.cost.seconds},
                    "cost_spent": {"calls": acquired.spent, "attempts": acquired.spent, "seconds": None},
                    "outcome": acquired.outcome,
                    "error_class": acquired.error_class,
                    "capture_ids": _items(acquired.capture_ids),
                    "evidence_ids": _items(sorted(set(acquired.evidence_ids))),
                    "status_before": before,
                    "own_status": update.status,
                    "status_after": own[node],
                    "layer": update.layer,
                },
            )
            self.seq += 1
            first = False

    def corroboration(
        self,
        graph: Graph,
        own: dict[str, str],
        holding: dict[str, set[str]],
        offered: tuple[Check, ...],
        by_id: dict[str, MenuCheck],
        tried: set[str],
    ) -> tuple[str, tuple[str, ...]] | None:
        """The part, if any, that exactly one origin family supports while a check of another family that can read it is untried and
        affordable, with the ids of those checks in menu order. Two families settle a part; a third is not sought."""
        for node in graph.nodes:
            families = holding.get(node.node_id, set())
            if own.get(node.node_id) != "SUPPORTED" or len(families) != 1:
                continue
            found = tuple(
                c.check_id
                for c in offered
                if c.check_id not in tried
                and c.node_type is node.type
                and by_id[c.check_id].check_type in desk_interpret.INTERPRETED
                and check_family(by_id[c.check_id]) not in families
            )
            if found:
                return node.node_id, found
        return None

    # --- the screen ------------------------------------------------------------------------------------------------------------
    def screen_step(self) -> tuple[list[str], dict[str, canonical.JsonValue] | None]:
        """Screen the token's contract, if the menu offers a screen of it and its provider has a call left: the evidence ids it brought, and
        the gate event it earned (none when the screen could not be made, which would claim a pass that was never given). A listing is the
        only reading that stops a run; a clean or incomplete one changes nothing about how the claim is judged."""
        ids: list[str] = []
        event: dict[str, canonical.JsonValue] | None = None
        for check in (c for c in self.menu.checks if c.check_type == "ADDRESS_FLAG_CHECK"):
            if not self.affordable(check):
                continue
            acquired = self.ports.acquire(check, self.ctx)
            self.calls[check.provider] = self.calls.get(check.provider, 0) + acquired.spent
            reading = acquired.reading if acquired.outcome == "OK" else None
            own = reading or "NOT_SCREENED"
            self.write(
                "desk_check",
                {
                    "run_id": self.run_id,
                    "seq": self.seq,
                    "node_id": "screen",
                    "node_type": "OBSERVATION",
                    "check_id": check.check_id,
                    "check_type": check.check_type,
                    "provider": check.provider,
                    "operation": check.operation,
                    "origin_family": check_family(check),
                    "args": [_items(pair) for pair in check.args],
                    "chosen_by": "ADDRESS_SCREEN",
                    "why": {"template_id": "ADDRESS_SCREEN", "settled": []},
                    "alternatives": [],
                    "cost_expected": {"calls": check.cost.calls, "seconds": check.cost.seconds},
                    "cost_spent": {"calls": acquired.spent, "attempts": acquired.spent, "seconds": None},
                    "outcome": acquired.outcome,
                    "error_class": acquired.error_class,
                    "capture_ids": _items(acquired.capture_ids),
                    "evidence_ids": _items(sorted(set(acquired.evidence_ids))),
                    "status_before": "UNCHECKED",
                    "own_status": own,
                    "status_after": own,
                    "layer": "DETERMINISTIC" if reading is not None else "NONE",
                },
            )
            self.seq += 1
            ids.extend(acquired.evidence_ids)
            detail = SCREEN_DETAILS.get(own)
            if detail is not None:
                status = "STOP" if own == "FLAGGED" else "PASS"
                event = {"gate": "HAZARD_SCREEN", "status": status, "detail": detail}
        return ids, event

    # --- the decision ----------------------------------------------------------------------------------------------------------
    def early_decision(self, kind: str, reason: str, events: list[canonical.JsonValue], evidence_ids: list[str]) -> None:
        """The decision record of a run that ended before any model was asked."""
        self.write(
            "decision",
            self.decision_body(
                {"stance": "FLAT", "p": "0.5", "weight": "0", "cites": [], "no_call_reason": reason if kind == "ABSTAIN" else None},
                events,
                {"kind": kind, "reason": reason, "stance": None, "p": None, "weight": None},
                evidence_ids,
                EMPTY_DIGEST,
            ),
        )

    def decision_body(
        self,
        proposal: dict[str, canonical.JsonValue],
        events: list[canonical.JsonValue],
        final: dict[str, canonical.JsonValue],
        evidence_ids: list[str],
        model_digest: str,
    ) -> dict[str, canonical.JsonValue]:
        unique = sorted(set(evidence_ids))
        store = self.ports.store
        return {
            "decision_id": natural_id("dn", {"run": self.run_id}),
            "run_id": self.run_id,
            "arm_id": "DESK",
            "candidate_id": self.trigger.trigger_id or cast(str, self.menu.asset_id),
            "proposal": proposal,
            "gate_events": events,
            "final_action": final,
            "evidence_ids": _items(unique),
            "observation_set_hash": observation_set_hash((i, store.digest(i)) for i in unique),
            "model_outputs_digest": model_digest,
        }

    def falsify_step(self, hypothesis: desk_plan.Hypothesis, history: History | None) -> falsify.Falsification:
        """Run the tests and write the record, before any decision that follows."""
        trigger = self.trigger
        if trigger.event_time_ms is None:
            results = list(falsify.without_event_time())
        else:
            ta, h, token = (history.ta, history.h, history.token) if history is not None else (0, 60 * trigger.hours, [])
            market = history.market if history is not None else None
            onset = falsify.t_onset(token, ta, h)
            if market is None:
                moved = falsify.Result("T_MARKET", falsify.VERSION, "NOT_TESTABLE", "series_too_short", {}, {})
            else:
                moved = falsify.t_market(token, market, ta, h)
            results = [onset, moved]
        results += [falsify.t_source(None), falsify.t_placebo_time()]
        result = falsify.run(results, self.ports.calibration)
        passed = result.outcome == "SURVIVED"
        ceiling, p_cap = desk_gates.ceiling_for(hypothesis.pre_target, passed)
        completed = result.outcome != "NOT_COMPLETED"
        calibration = self.ports.calibration
        tests: list[canonical.JsonValue] = []
        for test in result.tests:
            allowed = test.test == "T_SOURCE" or (calibration is not None and calibration.allows(test.test))
            tests.append(
                {
                    "type": test.test,
                    "method": METHODS[test.test],
                    "version": test.version,
                    "parameters": dict(test.parameters),
                    "statistics": dict(test.statistics),
                    "result": test.result,
                    "reason": test.reason,
                    "calibrated": allowed,
                    "calibration_sha256": calibration.sha256 if allowed and test.test != "T_SOURCE" and calibration is not None else None,
                }
            )
        pre = hypothesis.pre_target
        self.write(
            "falsification",
            {
                "run_id": self.run_id,
                "hypothesis_id": hypothesis.hypothesis_id,
                "target_assumption": desk_plan.TARGET_NODE,
                "proposed_explanation": EXPLANATION_CLASS,
                "competing_explanations": [{"klass": klass, "predicts": predicts} for klass, predicts in COMPETING],
                "tests": tests,
                "unresolved": list(result.unresolved),
                "completed": completed,
                "label": None if completed else "NOT_FALSIFIED_ATTEMPTED",
                "pre_test_target": {"stance": pre.stance, "p": _text(pre.p), "weight": _text(pre.weight)},
                "outcome": "PASSED" if passed else "FAILED",
                "ceiling": {"weight": _text(ceiling), "p_cap": _text(p_cap)},
            },
        )
        return result


def run(
    ports: Ports, trigger: desk_plan.Trigger, menu: Menu, budget: Budget, run_id: str, binding: Binding, versions: Versions
) -> RunOutcome:
    """One run of the Desk for a trigger, from its first record to its terminal state."""
    if not isinstance(run_id, str) or _ID.fullmatch(run_id) is None:
        raise DeskError("run_id is an id")
    if menu.symbol is None or menu.symbol != trigger.symbol:
        raise DeskError("the menu is the menu of the trigger's symbol")
    hypothesis = desk_plan.hypotheses(trigger)[0]
    state = _Run(ports, trigger, menu, budget, run_id, binding, versions)
    state.state("RUNNING")
    try:
        return _go(state, hypothesis)
    except (DeskError, NotOnMenu):
        raise
    except Exception:  # a port broke: the run is closed and says so, whatever the port was
        return state.end("FAILED_FINAL", "RUN_INTERRUPTED")


def _go(state: _Run, hypothesis: desk_plan.Hypothesis) -> RunOutcome:
    ports = state.ports
    state.write("hypothesis", desk_plan.record_body(state.run_id, hypothesis))
    screened, hazard = state.screen_step()
    if hazard is not None and hazard["status"] == "STOP":
        state.early_decision("ABSTAIN", "OUT_OF_SCOPE", [hazard], screened)
        return state.end("ABSTAINED", "OUT_OF_SCOPE")
    own, evidence_ids, ended = state.check_loop(hypothesis)
    evidence_ids = [*screened, *evidence_ids]
    leading: list[canonical.JsonValue] = [] if hazard is None else [hazard]
    statuses = {n.node_id: own.get(n.node_id, "UNCHECKED") for n in hypothesis.graph.nodes}
    required = {r for m in hypothesis.graph.nodes for r in hypothesis.graph.requires(m.node_id)}
    unavailable = sorted(r for r in required if statuses[r] == "SOURCE_UNAVAILABLE")
    ok_events: list[canonical.JsonValue] = [*leading, {"gate": "GROUNDING", "status": "PASS", "detail": ""}]
    if unavailable:
        events: list[canonical.JsonValue] = [
            *ok_events,
            {"gate": "REQUIRED_EVIDENCE", "status": "STOP", "detail": "missing: " + ", ".join(unavailable)},
        ]
        state.early_decision("FAILED", "FAILED_EVIDENCE", events, evidence_ids)
        return state.end("FAILED", "FAILED_EVIDENCE")
    if ended is not None:
        reason = "EVIDENCE_CONFLICT_UNRESOLVED" if ended == "ESSENTIAL_PREMISE_UNPROVEN" and "MIXED" in statuses.values() else ended
        detail = (
            "a required part is contradicted or disputed" if reason == "EVIDENCE_CONFLICT_UNRESOLVED" else "a required part is unproven"
        )
        state.early_decision("ABSTAIN", reason, [*leading, {"gate": "GROUNDING", "status": "STOP", "detail": detail}], evidence_ids)
        return state.end("ABSTAINED", reason)
    history = ports.history(state.ctx)
    falsification = state.falsify_step(hypothesis, history)
    if state.budget.tokens <= 0:  # no model token has been spent before this point
        return state.end("FAILED", "MODEL_UNAVAILABLE")
    tests = tuple((t.test, t.result) for t in falsification.tests)
    answer = ports.propose(ProposeRequest(hypothesis, statuses, tests, tuple(sorted(set(evidence_ids)))))
    if isinstance(answer, ModelFailure):
        return state.end("FAILED", answer.reason)
    state.tokens += answer.tokens
    conditions = desk_gates.Conditions(
        ports.grounded(answer, tuple(sorted(set(evidence_ids)))), (), ports.stale(state.ctx), ports.room(state.ctx)
    )
    gated = desk_gates.gate(answer.proposal, hypothesis.pre_target, conditions, falsification)
    proposal = answer.proposal
    final: dict[str, canonical.JsonValue]
    if gated.final is not None:
        committed = gated.final
        final = {"kind": "COMMIT", "reason": None, "stance": committed.stance, "p": _text(committed.p), "weight": _text(committed.weight)}
    else:
        final = {"kind": gated.kind, "reason": gated.reason, "stance": None, "p": None, "weight": None}
    body = state.decision_body(
        {
            "stance": proposal.stance,
            "p": _text(proposal.p),
            "weight": _text(proposal.weight),
            "cites": _items(sorted(set(answer.cites))),
            "no_call_reason": None,
        },
        [*leading, *_items({"gate": e.gate, "status": e.status, "detail": e.detail} for e in gated.events)],
        final,
        evidence_ids,
        answer.model_outputs_digest,
    )
    state.write("decision", body)
    if gated.kind == "ABSTAIN":
        return state.end("ABSTAINED", gated.reason)
    if gated.final is None:
        return state.end("FAILED", gated.reason)
    guard_commit(gated.final.weight, gated.ceiling)
    falsification_body = next(b for k, b in reversed(state.records) if k == "falsification")
    forecast_id = ports.commit(CommitRequest(state.run_id, gated.final, falsification_body, body, state.binding.label))
    return state.end("COMMITTED", None, forecast_id)
