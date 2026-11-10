"""The validation harness of the impact ranking (plan, part B): the ranking against three simpler orders on labelled cases.

A case is a set of candidates that existed at one moment, each with an adjudicated research-decision grade (0 to 3: would an
informed researcher change their triage on seeing it). The ranking under test and three baselines (newest first, largest price move
first, the cycle manifest's order) each order the candidates; the harness scores the four orders on precision at k, nDCG, subtopic
recall, the rank of the worst missed consequential candidate and discovery recall, and compares the ranking with each baseline per case.

Two kinds of case are kept apart and never pooled: ``FIXTURE`` (invented, for testing the harness itself) and ``PILOT`` (from a primary
source, graded by at least two independent reviewers, with the disagreement kept). The report says how many of each it holds and states no
agreement figure for the ranking from fixtures. Nothing here is an independent validation: that needs people and sources the author does not
control (``PILOT_TARGET`` cases and two independent reviewers).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from hoshitori import impact
from hoshitori.today_items import TodayItem

FIXTURE: Final = "FIXTURE"
PILOT: Final = "PILOT"
KINDS: Final = (FIXTURE, PILOT)
PILOT_TARGET: Final = 30
CONSEQUENTIAL: Final = 2  # a grade at or above this changes an informed researcher's triage
MAX_GRADE: Final = 3
KS: Final = (1, 3, 5)
RANKERS: Final = ("IMPACT", "CHRONOLOGICAL", "PRICE_MOVE", "MANIFEST")


class ValidationError(ValueError):
    """A case that cannot be scored, or a pilot case without what a pilot case needs."""


@dataclass(frozen=True)
class Candidate:
    item: TodayItem
    arrival: int  # when the candidate reached us: larger is newer
    price_move: float | None  # the absolute price move, or none when there is no price
    manifest_rank: int  # the cycle manifest's own order: smaller first
    event_class: str  # the kind of event, for subtopic recall
    grades: tuple[int, ...]  # one grade per reviewer; the first is the adjudicated one when they differ

    @property
    def grade(self) -> int:
        return self.grades[0]


@dataclass(frozen=True)
class Case:
    case_id: str
    kind: str
    source: str  # the primary source of a pilot case (a URL or an identifier); empty for a fixture
    candidates: tuple[Candidate, ...]
    undiscovered: int = 0  # consequential events on the independent list that the candidate set does not hold


def check_case(case: Case) -> None:
    """Raise unless the case can be scored and, for a pilot, has its source and two reviewers on every candidate."""
    if case.kind not in KINDS:
        raise ValidationError(f"{case.case_id}: a case is a FIXTURE or a PILOT")
    if not case.candidates:
        raise ValidationError(f"{case.case_id}: a case has candidates")
    ids = [c.item.item_id for c in case.candidates]
    if len(set(ids)) != len(ids):
        raise ValidationError(f"{case.case_id}: candidate ids are distinct")
    if case.undiscovered < 0:
        raise ValidationError(f"{case.case_id}: undiscovered is not negative")
    for c in case.candidates:
        if not c.grades or any(isinstance(g, bool) or not 0 <= g <= MAX_GRADE for g in c.grades):
            raise ValidationError(f"{case.case_id}: every candidate has grades from 0 to {MAX_GRADE}")
        if case.kind == PILOT and len(c.grades) < 2:
            raise ValidationError(f"{case.case_id}: a pilot candidate is graded by two reviewers")
    if case.kind == PILOT and not case.source:
        raise ValidationError(f"{case.case_id}: a pilot case names its primary source")


def rank_impact(case: Case) -> list[str]:
    return [i.item_id for i in impact.slate(tuple(c.item for c in case.candidates))]


def rank_chronological(case: Case) -> list[str]:
    return [c.item.item_id for c in sorted(case.candidates, key=lambda c: -c.arrival)]


def rank_price_move(case: Case) -> list[str]:
    known = sorted((c for c in case.candidates if c.price_move is not None), key=lambda c: -(c.price_move or 0.0))
    return [c.item.item_id for c in known] + [c.item.item_id for c in case.candidates if c.price_move is None]


def rank_manifest(case: Case) -> list[str]:
    return [c.item.item_id for c in sorted(case.candidates, key=lambda c: c.manifest_rank)]


ORDERS: Final[dict[str, Callable[[Case], list[str]]]] = {
    "IMPACT": rank_impact,
    "CHRONOLOGICAL": rank_chronological,
    "PRICE_MOVE": rank_price_move,
    "MANIFEST": rank_manifest,
}


def _by_id(case: Case) -> dict[str, Candidate]:
    return {c.item.item_id: c for c in case.candidates}


def precision_at_k(case: Case, order: Sequence[str], k: int) -> float:
    by = _by_id(case)
    top = order[:k]
    return float(sum(by[i].grade >= CONSEQUENTIAL for i in top)) / k


def ndcg_at_k(case: Case, order: Sequence[str], k: int) -> float:
    by = _by_id(case)

    def dcg(grades: Sequence[int]) -> float:
        return float(sum((2**g - 1) / math.log2(n + 2) for n, g in enumerate(grades[:k])))

    ideal = dcg(sorted((c.grade for c in case.candidates), reverse=True))
    return 0.0 if ideal == 0 else dcg([by[i].grade for i in order]) / ideal


def subtopic_recall_at_k(case: Case, order: Sequence[str], k: int) -> float | None:
    """Share of the event classes that hold a consequential candidate which appear among the top k; none when no class does."""
    by = _by_id(case)
    wanted = {c.event_class for c in case.candidates if c.grade >= CONSEQUENTIAL}
    if not wanted:
        return None
    return len({by[i].event_class for i in order[:k] if by[i].grade >= CONSEQUENTIAL}) / len(wanted)


def worst_missed_rank(case: Case, order: Sequence[str]) -> int | None:
    """The 1-based rank of the lowest-ranked consequential candidate; none when the case has none."""
    by = _by_id(case)
    ranks = [n + 1 for n, i in enumerate(order) if by[i].grade >= CONSEQUENTIAL]
    return max(ranks) if ranks else None


def discovery_recall(case: Case) -> float | None:
    """Of the consequential events on the independent list, the share the candidate set holds; none when the list is empty."""
    held = sum(c.grade >= CONSEQUENTIAL for c in case.candidates)
    total = held + case.undiscovered
    return None if total == 0 else held / total


def reviewer_agreement(cases: Sequence[Case]) -> tuple[int, float | None, float | None]:
    """``(pairs, raw agreement, Cohen's kappa)`` on 'consequential or not' between the first two reviewers, over candidates with two."""
    pairs = [(c.grades[0] >= CONSEQUENTIAL, c.grades[1] >= CONSEQUENTIAL) for k in cases for c in k.candidates if len(c.grades) >= 2]
    n = len(pairs)
    if n == 0:
        return 0, None, None
    observed = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    kappa = None if expected == 1 else (observed - expected) / (1 - expected)
    return n, observed, kappa


def sign_test_p(wins: int, losses: int) -> float:
    """The exact two-sided sign test over the cases that differ (ties are dropped); 1.0 when none differ."""
    n = wins + losses
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(0, min(wins, losses) + 1)) / 2**n
    return float(min(1.0, 2 * tail))


def evaluate(cases: Sequence[Case]) -> dict[str, dict[str, float]]:
    """The mean of each metric for each order over the cases that define it, keyed ``order -> metric@k``."""
    for case in cases:
        check_case(case)
    table: dict[str, dict[str, float]] = {}
    for name, rank in ORDERS.items():
        sums: dict[str, list[float]] = {}
        for case in cases:
            order = rank(case)
            for k in KS:
                kk = min(k, len(order))
                sums.setdefault(f"precision@{k}", []).append(precision_at_k(case, order, kk))
                sums.setdefault(f"ndcg@{k}", []).append(ndcg_at_k(case, order, kk))
                sub = subtopic_recall_at_k(case, order, kk)
                if sub is not None:
                    sums.setdefault(f"subtopic_recall@{k}", []).append(sub)
            worst = worst_missed_rank(case, order)
            if worst is not None:
                sums.setdefault("worst_missed_rank", []).append(float(worst))
        table[name] = {key: sum(v) / len(v) for key, v in sums.items()}
    return table


def compare(cases: Sequence[Case], metric: str = "ndcg", k: int = 3) -> dict[str, tuple[int, int, int, float]]:
    """For each baseline: ``(cases the ranking is better, worse, equal, sign-test p)`` on one metric at k, per case."""
    for case in cases:
        check_case(case)
    out: dict[str, tuple[int, int, int, float]] = {}
    functions = {"precision": precision_at_k, "ndcg": ndcg_at_k}
    if metric not in functions:
        raise ValidationError(f"the metric is one of {sorted(functions)}")
    score = functions[metric]
    for name in RANKERS:
        if name == "IMPACT":
            continue
        wins = losses = ties = 0
        for case in cases:
            kk = min(k, len(case.candidates))
            ours = score(case, ORDERS["IMPACT"](case), kk)
            theirs = score(case, ORDERS[name](case), kk)
            if math.isclose(ours, theirs, abs_tol=1e-12):
                ties += 1
            elif ours > theirs:
                wins += 1
            else:
                losses += 1
        out[name] = (wins, losses, ties, sign_test_p(wins, losses))
    return out


def _kappa_words(kappa: float | None) -> str:
    return "undefined" if kappa is None else f"{kappa:.2f}"


def report(cases: Sequence[Case]) -> str:
    """The plain-text account: what the cases are, the four orders side by side, and what it does not show."""
    for case in cases:
        check_case(case)
    pilots = sum(c.kind == PILOT for c in cases)
    fixtures = len(cases) - pilots
    n, agree, kappa = reviewer_agreement([c for c in cases if c.kind == PILOT])
    lines = [
        f"{impact.LABEL} Formula {impact.FORMULA_VERSION}.",
        f"Cases: {pilots} pilot of {PILOT_TARGET} wanted, {fixtures} fixture. Fixtures test this harness, not the ranking.",
        "Reviewer agreement on the pilot cases: "
        + ("none yet." if agree is None else f"{agree:.2f} raw, kappa {_kappa_words(kappa)} over {n} candidates."),
    ]
    table = evaluate([c for c in cases if c.kind == PILOT]) if pilots else {}
    if not table:
        lines.append("No comparison is shown: there are no pilot cases, and fixture cases are not evidence for the ranking.")
    else:
        for name in RANKERS:
            row = table[name]
            lines.append(name + ": " + ", ".join(f"{key} {value:.2f}" for key, value in sorted(row.items())))
        for name, (w, lo, t, p) in compare([c for c in cases if c.kind == PILOT]).items():
            lines.append(f"IMPACT against {name} on nDCG@3 per case: better {w}, worse {lo}, equal {t}, sign test p {p:.3f}.")
        if pilots < PILOT_TARGET:
            lines.append(f"Fewer than {PILOT_TARGET} pilot cases: no ranking claim is made.")
    return "\n".join(lines)
