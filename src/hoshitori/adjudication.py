"""Adjudication of evidence contributions (contract section 32.1a; ``SOCIALFI_CONTRACT``).

Who decides whether a contribution is accepted, and how a manufactured consensus is stopped, are rules of the product.

* The states are visible to the contributor: SUBMITTED, TRIAGED, then MACHINE_CHECKED (a deterministic check could decide) or NEEDS_REVIEW
  and PENDING, then DECIDED with an outcome and a reason, then APPEAL_OPEN and APPEAL_DECIDED. The original decision stays in the record.
* A machine decides an item only when its decisive source is one of the first four classes, the claimed fact can be checked
  deterministically and the check has a result. A social post is never machine-accepted; anything else waits for a reviewer.
* A quote that is not in the source, or a source that cannot be fetched, leaves the item unresolved and its origin submitter-asserted.
* A later submission of the same claim is a duplicate of the first. It is recorded as independent corroboration only when it rests on a
  different origin family.
* No vote, endorsement or count of agreeing identities is an input to anything. Support between identities is recorded only to find
  coordination.
* Identities that share a network, report one claim inside a window, support each other both ways or arrive in a burst are one
  contributor for credit and rate limits. The grouping is private to moderation and never published as an accusation.
* A reviewer is never the contributor or one of the contributor's group. An appeal is one per decision, inside the window, and is decided
  by a reviewer other than the first. Credit is posted only after the window has closed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

SOURCE_CLASSES: Final = (
    "PRIMARY_DOCUMENT",
    "OFFICIAL_ANNOUNCEMENT",
    "ONCHAIN_RECORD",
    "EXCHANGE_DATA",
    "REPUTABLE_REPORTING",
    "SOCIAL_POST",
    "ANONYMOUS_OR_UNKNOWN",
)
MACHINE_CLASSES: Final = SOURCE_CLASSES[:4]
KINDS: Final = ("CORRECT_FACT", "ADD_SOURCE", "DISPUTE_CLAIM", "ORIGIN_PROBLEM")
OUTCOMES: Final = ("ACCEPTED_CORRECTION", "CORROBORATED", "REJECTED", "DUPLICATE", "UNRESOLVED")
CREDITED: Final = ("ACCEPTED_CORRECTION", "CORROBORATED")
APPEAL_WINDOW_S: Final = 7 * 86_400.0
DAILY_CAP: Final = 5  # submissions a contributor, with the identities grouped to them, may make in a day
CLAIM_WINDOW_S: Final = 3600.0  # one claim from two identities inside this many seconds
BURST_COUNT: Final = 5  # submissions from this many identities inside BURST_WINDOW_S
BURST_WINDOW_S: Final = 60.0
DAY_S: Final = 86_400.0


class AdjudicationError(ValueError):
    """A submission, a review or an appeal that is refused."""


@dataclass
class Case:
    case_id: str
    contributor: str
    kind: str
    claim: str
    url: str
    source_class: str
    origin: str
    origin_class: str
    state: str = "SUBMITTED"
    outcome: str | None = None
    reason: str | None = None
    decided_by: str | None = None
    decided_at: float | None = None
    duplicate_of: str | None = None
    corroborates: str | None = None
    appeals: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)


class Adjudicator:
    def __init__(self, clock: Callable[[], float], reviewers: Sequence[str], appeal_window_s: float = APPEAL_WINDOW_S) -> None:
        self._now = clock
        self._reviewers = tuple(reviewers)
        self._window = appeal_window_s
        self._cases: dict[str, Case] = {}
        self._submissions: list[tuple[float, str, str, str]] = []  # time, identity, network, claim
        self._support: set[tuple[str, str]] = set()

    # --- reading --------------------------------------------------------------------------------------------------------------------
    def case(self, case_id: str) -> Case:
        if case_id not in self._cases:
            raise AdjudicationError(f"no case {case_id}")
        return self._cases[case_id]

    def _step(self, case: Case, state: str) -> None:
        case.state = state
        case.history.append({"state": state, "at": self._now()})

    # --- coordination ---------------------------------------------------------------------------------------------------------------
    def _groups(self) -> dict[str, str]:
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)

        by_network: dict[str, str] = {}
        for _, who, network, _ in self._submissions:
            find(who)
            if network in by_network:
                union(who, by_network[network])
            by_network[network] = find(by_network.get(network, who))
        for i, (ta, a, _, claim_a) in enumerate(self._submissions):
            for tb, b, _, claim_b in self._submissions[i + 1 :]:
                if claim_a == claim_b and abs(tb - ta) <= CLAIM_WINDOW_S:
                    union(a, b)
        for a, b in self._support:
            if (b, a) in self._support:
                union(a, b)
        ordered = sorted(self._submissions)
        for i, (start, *_rest) in enumerate(ordered):
            window = [s for s in ordered[i:] if s[0] - start <= BURST_WINDOW_S]
            if len({s[1] for s in window}) >= BURST_COUNT:
                for s in window[1:]:
                    union(window[0][1], s[1])
        return {who: find(who) for who in parent}

    def group_of(self, identity: str) -> str:
        return self._groups().get(identity, identity)

    def record_support(self, supporter: str, supported: str) -> None:
        """Support is recorded only to find coordination; it is never an input to a status or to credit."""
        self._support.add((supporter, supported))

    # --- submitting -----------------------------------------------------------------------------------------------------------------
    def submit(
        self,
        case_id: str,
        contributor: str,
        *,
        network: str,
        kind: str,
        claim: str,
        url: str,
        source_class: str,
        quote_found: bool | None,
        origin: str,
        checkable: bool,
        machine_result: str | None,
    ) -> Case:
        if case_id in self._cases:
            raise AdjudicationError("a case id is used once")
        if kind not in KINDS:
            raise AdjudicationError(f"a kind is one of {list(KINDS)}")
        if source_class not in SOURCE_CLASSES:
            raise AdjudicationError(f"a source class is one of {list(SOURCE_CLASSES)}")
        if machine_result not in (None, "CONFIRMED", "REFUTED"):
            raise AdjudicationError("a machine result is CONFIRMED, REFUTED or none")
        now = self._now()
        self._submissions.append((now, contributor, network, claim))
        group = self._groups()
        members = {who for who, g in group.items() if g == group[contributor]}
        made = sum(1 for t, who, _, _ in self._submissions[:-1] if who in members and now - t < DAY_S)
        if made >= DAILY_CAP:
            self._submissions.pop()
            raise AdjudicationError(f"the contributor (with the identities grouped to them) has made {DAILY_CAP} submissions today")
        found = quote_found is True
        case = Case(case_id, contributor, kind, claim, url, source_class, origin, "ORIGIN_FETCHED_BY_US" if found else "SUBMITTER_ASSERTED")
        self._cases[case_id] = case
        self._step(case, "SUBMITTED")
        self._step(case, "TRIAGED")
        if not found:
            self._decide(case, "UNRESOLVED", "QUOTE_NOT_FOUND" if quote_found is False else "UNFETCHABLE", "machine")
            return case
        first = next((c for c in self._cases.values() if c.claim == claim and c.case_id != case_id and c.outcome != "DUPLICATE"), None)
        if first is not None:
            case.duplicate_of = first.case_id
            case.corroborates = first.case_id if first.origin != origin else None
            self._decide(case, "DUPLICATE", f"DUPLICATE_OF:{first.case_id}", "machine")
            return case
        if source_class in MACHINE_CLASSES and checkable and machine_result is not None:
            self._step(case, "MACHINE_CHECKED")
            if machine_result == "REFUTED":
                self._decide(case, "REJECTED", "CLAIM_REFUTED", "machine")
            else:
                self._decide(case, "ACCEPTED_CORRECTION" if kind == "CORRECT_FACT" else "CORROBORATED", None, "machine")
            return case
        self._step(case, "NEEDS_REVIEW")
        self._step(case, "PENDING")
        case.reason = "NEEDS_REVIEW"
        return case

    def _decide(self, case: Case, outcome: str, reason: str | None, by: str, state: str = "DECIDED") -> None:
        case.outcome, case.reason, case.decided_by, case.decided_at = outcome, reason, by, self._now()
        case.decisions.append({"outcome": outcome, "by": by, "reason": reason, "at": case.decided_at})
        self._step(case, state)

    # --- reviewing ------------------------------------------------------------------------------------------------------------------
    def _conflicted(self, reviewer: str, case: Case) -> bool:
        return self.group_of(reviewer) == self.group_of(case.contributor)

    def reviewers_for(self, case_id: str) -> tuple[str, ...]:
        case = self.case(case_id)
        return tuple(r for r in self._reviewers if not self._conflicted(r, case))

    def appeal_reviewers(self, case_id: str) -> tuple[str, ...]:
        case = self.case(case_id)
        return tuple(r for r in self.reviewers_for(case_id) if r != self._first_decider(case))

    def pending_reason(self, case_id: str) -> str | None:
        if self.case(case_id).state != "PENDING":
            return None
        if not self.reviewers_for(case_id):
            return "no reviewer other than the contributor and their group is available"
        return "waiting for a reviewer"

    def _check_decision(self, reviewer: str, case: Case, outcome: str, reason: str) -> None:
        if reviewer not in self._reviewers:
            raise AdjudicationError(f"{reviewer} is not a reviewer")
        if self._conflicted(reviewer, case):
            raise AdjudicationError("a reviewer never reviews their own item or their group's")
        if outcome not in OUTCOMES:
            raise AdjudicationError(f"an outcome is one of {list(OUTCOMES)}")
        if not reason:
            raise AdjudicationError("a decision states its reason")

    def review(self, case_id: str, reviewer: str, outcome: str, reason: str) -> Case:
        case = self.case(case_id)
        if case.state != "PENDING":
            raise AdjudicationError(f"only a pending item is reviewed, not {case.state}")
        self._check_decision(reviewer, case, outcome, reason)
        self._decide(case, outcome, reason, reviewer)
        return case

    @staticmethod
    def _first_decider(case: Case) -> str | None:
        return case.decisions[0]["by"] if case.decisions else None

    def appeal(self, case_id: str, contributor: str, reason: str) -> Case:
        case = self.case(case_id)
        if contributor != case.contributor:
            raise AdjudicationError("only the contributor appeals")
        if case.appeals:
            raise AdjudicationError("one appeal per decision")
        if case.state != "DECIDED" or case.decided_at is None:
            raise AdjudicationError(f"only a decided item is appealed, not {case.state}")
        if self._now() >= case.decided_at + self._window:
            raise AdjudicationError("the appeal window has closed")
        if not reason:
            raise AdjudicationError("an appeal states its reason")
        case.appeals = 1
        self._step(case, "APPEAL_OPEN")
        return case

    def review_appeal(self, case_id: str, reviewer: str, outcome: str, reason: str) -> Case:
        case = self.case(case_id)
        if case.state != "APPEAL_OPEN":
            raise AdjudicationError(f"only an open appeal is reviewed, not {case.state}")
        if reviewer == self._first_decider(case):
            raise AdjudicationError("an appeal is decided by a different reviewer")
        self._check_decision(reviewer, case, outcome, reason)
        self._decide(case, outcome, reason, reviewer, "APPEAL_DECIDED")
        return case

    # --- credit ---------------------------------------------------------------------------------------------------------------------
    def _earned(self, contributor: str) -> list[Case]:
        return [
            c
            for c in self._cases.values()
            if c.contributor == contributor and c.outcome in CREDITED and c.state in ("DECIDED", "APPEAL_DECIDED")
        ]

    def _closed(self, case: Case) -> bool:
        return case.decided_at is not None and self._now() >= case.decided_at + self._window

    def credit(self, contributor: str) -> int:
        return sum(self._closed(c) for c in self._earned(contributor))

    def pending_credit(self, contributor: str) -> int:
        open_appeal = [
            c for c in self._cases.values() if c.contributor == contributor and c.state == "APPEAL_OPEN" and c.outcome in CREDITED
        ]
        return sum(not self._closed(c) for c in self._earned(contributor)) + len(open_appeal)

    def group_credit(self, group: str) -> int:
        groups = self._groups()
        return sum(self.credit(who) for who, g in groups.items() if g == group)

    # --- views ----------------------------------------------------------------------------------------------------------------------
    def public_view(self) -> dict[str, Any]:
        rows = [
            {
                "case": c.case_id,
                "kind": c.kind,
                "state": c.state,
                "outcome": c.outcome,
                "reason": c.reason,
                "decided_by": c.decided_by,
                "source_class": c.source_class,
                "origin_class": c.origin_class,
            }
            for c in self._cases.values()
        ]
        return {"cases": rows}

    def moderation_view(self) -> dict[str, Any]:
        members: dict[str, list[str]] = {}
        for who, g in self._groups().items():
            members.setdefault(g, []).append(who)
        return {"groups": [sorted(m) for m in members.values() if len(m) > 1]}
