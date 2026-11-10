"""Forward validation of lessons (contract section 55.3 and 55.3a; ``OUTCOME_LEARNING_SPEC``).

A lesson is a typed change proposed by the outcome review. It never reaches a live agent as text. When a pattern has enough
settled cases and none of them lies in the validation period, the lesson becomes a candidate version beside its parent and
beside a null lesson (an irrelevant typed change of similar size) on future days. Each day gives one paired score difference of
the candidate against the parent, and of the null against the parent.

* Before the endpoint the differences are watched with a normal-mixture sequential test, whose confidence sequence holds at
  every stopping time. The watch can stop a lesson for harm or for futility. It cannot promote.
* At the endpoint, once, the improvement is tested with a Newey-West standard error (daily differences are not independent),
  Holm-adjusted over every lesson tested in the period (a lesson that stopped early or has not reached its endpoint counts with
  a p-value of one, so the family never shrinks by stopping a lesson). A guard-rail regression blocks promotion whatever the
  primary endpoint shows.
* A promoted version keeps its parent beside it for a holdback. A regression beyond the margin, or any guard-rail breach, rolls
  it back by a new locked version that has the parent's configuration; nothing is edited or rescored.
* The null lesson is measured the same way, so the rate at which an irrelevant change would have been promoted is a number, not
  an assumption. It also gates: a candidate whose null lesson passes the same test at the same endpoint is held back as
  ``INCONCLUSIVE`` (``NULL_CROSSED``), because the period then rewards any change and the improvement shows nothing.

The monitoring rule, its error level and its thresholds are fields of the protocol, whose hash is the one thing a scored period
is pinned to.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Final

from hoshitori import outcome_review, paired_stats

N_SUPPORT: Final = outcome_review.N_SUPPORT
MAX_CONCURRENT: Final = 3
NULL_KIND: Final = "NULL_LESSON"
KINDS: Final = frozenset({change["kind"] for change in outcome_review.CHANGES.values()} | {NULL_KIND})
_VALUE = re.compile(r"[A-Z0-9_.]{1,40}")
_KEY = re.compile(r"[a-z_]{1,20}")
MAX_KEYS: Final = 4
GUARDS: Final = ("coverage", "abstention", "reasoning_vector", "risk_limit_breach", "failure_rate")


class LessonError(ValueError):
    """A lesson, a test or a day that is refused."""


@dataclass(frozen=True)
class Protocol:
    """What a scored period of lesson testing is pinned to."""

    endpoint_days: int  # independent market days to the one decision
    alpha: float  # the error level of the endpoint and of the watch
    sigma: float  # the standard deviation of a daily paired difference, from the exploratory period
    tau: float  # the spread of the mixing distribution of the sequential test
    smallest_effect: float  # the least improvement worth having; a stream that cannot reach it stops for futility
    min_days: int  # no early stop before this many days
    margin: float  # a holdback regression beyond this rolls back

    def __post_init__(self) -> None:
        if not (isinstance(self.endpoint_days, int) and self.endpoint_days >= 2 and isinstance(self.min_days, int) and self.min_days >= 2):
            raise LessonError("the endpoint and the first look are whole numbers of days, two or more")
        if self.min_days > self.endpoint_days:
            raise LessonError("the first look is not after the endpoint")
        if not (0 < self.alpha < 1) or not self.sigma > 0 or not self.tau > 0 or self.smallest_effect < 0 or self.margin < 0:
            raise LessonError("alpha is between 0 and 1, sigma and tau are positive, the effect and the margin are not negative")

    def hash(self) -> str:
        text = json.dumps(self.__dict__, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()


def check_guards(guards: dict[str, bool] | None) -> list[str]:
    """The guard rails that are breached, in order; a name that is not a guard rail of the protocol is refused, never ignored."""
    named = guards or {}
    unknown = sorted(set(named) - set(GUARDS))
    if unknown:
        raise LessonError(f"unknown guard rail {unknown[0]}; the guard rails are {list(GUARDS)}")
    return sorted(g for g, bad in named.items() if bad)


def check_change(change: dict[str, str]) -> None:
    """A change is a few closed words and numbers, never a sentence."""
    if not isinstance(change, dict) or "kind" not in change or len(change) > MAX_KEYS:
        raise LessonError("a change has a kind and at most four members")
    if change["kind"] not in KINDS:
        raise LessonError(f"the kind of a change is one of {sorted(KINDS)}")
    for key, value in change.items():
        if not isinstance(key, str) or not _KEY.fullmatch(key) or not isinstance(value, str) or not _VALUE.fullmatch(value):
            raise LessonError("a change holds closed words and numbers (capitals, digits, underscores and points), never free text")


@dataclass(frozen=True)
class Lesson:
    lesson_id: str
    error_class: str
    change: dict[str, str]
    source_cases: tuple[tuple[str, int], ...]  # the settled cases that matched, each with the market day it settled on

    @property
    def support(self) -> int:
        return len({case for case, _ in self.source_cases})


@dataclass(frozen=True)
class VersionView:
    version_hash: str
    parent: str | None
    lesson: str | None
    diff: dict[str, str]
    status: str  # SHADOW, LIVE, ROLLED_BACK or RETIRED


# --- the statistics -------------------------------------------------------------------------------------------------------------------
def _log_ratio(n: int, total: float, protocol: Protocol) -> float:
    s2, t2 = protocol.sigma**2, protocol.tau**2
    return 0.5 * math.log(s2 / (s2 + n * t2)) + t2 * total * total / (2 * s2 * (s2 + n * t2))


def mixture_p(diffs: list[float], protocol: Protocol) -> float:
    """The p-value of the mixture test at this many days, as it stands at this moment."""
    if not diffs:
        return 1.0
    return min(1.0, math.exp(-_log_ratio(len(diffs), math.fsum(diffs), protocol)))


def always_valid_p(diffs: list[float], protocol: Protocol) -> float:
    """The smallest p-value over every look so far: valid at any stopping time."""
    return min((mixture_p(diffs[:k], protocol) for k in range(1, len(diffs) + 1)), default=1.0)


def confidence_sequence(diffs: list[float], protocol: Protocol) -> tuple[float, float]:
    """The interval for the true mean difference that holds at every stopping time with probability one minus alpha."""
    n = len(diffs)
    if n == 0:
        return (-math.inf, math.inf)
    s2, t2 = protocol.sigma**2, protocol.tau**2
    mean = math.fsum(diffs) / n
    width = math.sqrt(2 * s2 * (s2 + n * t2) * (math.log(1 / protocol.alpha) + 0.5 * math.log((s2 + n * t2) / s2)) / (t2 * n * n))
    return (mean - width, mean + width)


def interim(diffs: list[float], protocol: Protocol) -> str:
    """CONTINUE, HARM (the whole interval lies below zero) or FUTILITY (the whole interval lies below the smallest effect worth having)."""
    if len(diffs) < protocol.min_days:
        return "CONTINUE"
    _, high = confidence_sequence(diffs, protocol)
    if high < 0:
        return "HARM"
    if high < protocol.smallest_effect:
        return "FUTILITY"
    return "CONTINUE"


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    """The continued fraction of the regularised incomplete beta function (modified Lentz's method)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        for numerator in (m * (b - m) * x / ((qam + m2) * (a + m2)), -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1.0 + numerator * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + numerator / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1.0) < 1e-14:
            break
    return h


def t_upper_tail(t: float, df: int) -> float:
    """P(T > t) for Student's t with ``df`` degrees of freedom."""
    x = df / (df + t * t)
    if x >= 1.0:  # |t| is too small for the tail to differ from one half at double precision
        return 0.5
    a, b = df / 2.0, 0.5
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        half = front * _beta_continued_fraction(a, b, x) / a
    else:
        half = 1.0 - front * _beta_continued_fraction(b, a, 1.0 - x) / b
    return 0.5 * half if t > 0 else 1.0 - 0.5 * half


def newey_west_p(diffs: list[float]) -> float:
    """The one-sided p-value that the mean difference is not above zero, with a Bartlett-weighted standard error of the mean. The tail is
    Student's t with n minus one degrees of freedom: the normal tail is too liberal for a few dozen days (simulated, see the tests)."""
    n = len(diffs)
    if n < 5:
        raise LessonError("five days at least")
    mean = math.fsum(diffs) / n
    resid = [d - mean for d in diffs]
    lags = int(n ** (1 / 3))
    variance = math.fsum(r * r for r in resid) / n
    for lag in range(1, lags + 1):
        variance += 2 * (1 - lag / (lags + 1)) * math.fsum(resid[t] * resid[t - lag] for t in range(lag, n)) / n
    se = math.sqrt(max(variance, 1e-12) / n)
    return t_upper_tail(mean / se, n - 1)


# --- the book -------------------------------------------------------------------------------------------------------------------------
@dataclass
class _Entry:
    lesson: Lesson
    status: str = "PROPOSED"
    reason: str | None = None
    candidate: str | None = None
    null: str | None = None
    first_day: int | None = None
    last_day: int | None = None
    diffs: list[float] | None = None
    null_diffs: list[float] | None = None
    holdback: list[float] | None = None
    last_holdback_day: int | None = None
    promoted_ever: bool = False


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Book:
    """Every lesson, every version and every event of a testing period. Nothing is edited: a status change is an event."""

    def __init__(self, protocol: Protocol, root: str) -> None:
        self.protocol = protocol
        self._entries: dict[str, _Entry] = {}
        self._versions: dict[str, tuple[str | None, str | None, dict[str, str]]] = {root: (None, None, {})}
        self._status: dict[str, str] = {root: "LIVE"}
        self._live = root
        self._events: list[dict[str, Any]] = []

    # reading
    def events(self) -> list[dict[str, Any]]:
        return [dict(e) for e in self._events]

    def live(self) -> str:
        return self._live

    def status(self, name: str) -> str:
        return self._entry(name).status

    def reason(self, name: str) -> str | None:
        return self._entry(name).reason

    def candidate_of(self, name: str) -> str:
        found = self._entry(name).candidate
        if found is None:
            raise LessonError("the lesson has no candidate: no test was started")
        return found

    def null_of(self, name: str) -> str:
        found = self._entry(name).null
        if found is None:
            raise LessonError("the lesson has no null lesson: no test was started")
        return found

    def versions(self) -> dict[str, VersionView]:
        return {h: VersionView(h, parent, lesson, dict(diff), self._status[h]) for h, (parent, lesson, diff) in self._versions.items()}

    def _entry(self, name: str) -> _Entry:
        if name not in self._entries:
            raise LessonError(f"no lesson {name}")
        return self._entries[name]

    def _log(self, event: str, name: str, **fields: Any) -> None:
        self._events.append({"event": event, "lesson": name, **fields})

    # proposing and starting
    def propose(self, lesson: Lesson) -> None:
        if lesson.lesson_id in self._entries:
            raise LessonError("a lesson id is used once")
        check_change(lesson.change)
        if lesson.change["kind"] == NULL_KIND:
            raise LessonError("a null lesson is made by the test, never proposed")
        self._entries[lesson.lesson_id] = _Entry(lesson)
        self._log("PROPOSED", lesson.lesson_id, support=lesson.support)

    def start_test(self, name: str, first_day: int) -> None:
        entry = self._entry(name)
        if entry.status != "PROPOSED":
            raise LessonError(f"a lesson is tested from PROPOSED, not {entry.status}")
        if entry.lesson.support < N_SUPPORT:
            raise LessonError(f"the pattern has {entry.lesson.support} settled cases; a test needs {N_SUPPORT} (support)")
        if any(day >= first_day for _, day in entry.lesson.source_cases):
            entry.status, entry.reason = "REJECTED", "OVERLAP"
            self._log("REJECTED", name, reason="OVERLAP")
            raise LessonError("the source cases overlap the validation period")
        if sum(e.status == "TESTING" for e in self._entries.values()) >= MAX_CONCURRENT:
            raise LessonError("at most three candidates run at once")
        parent = self._live
        change = dict(entry.lesson.change)
        null_change = {"kind": NULL_KIND, "size": str(len(change))}
        candidate = _digest({"parent": parent, "lesson": name, "diff": change})
        null = _digest({"parent": parent, "lesson": name, "diff": null_change, "null": True})
        self._versions[candidate] = (parent, name, change)
        self._versions[null] = (parent, name, null_change)
        self._status[candidate] = self._status[null] = "SHADOW"
        entry.status, entry.candidate, entry.null, entry.first_day = "TESTING", candidate, null, first_day
        entry.diffs, entry.null_diffs = [], []
        self._log("TEST_STARTED", name, candidate=candidate, null=null, parent=parent, first_day=first_day, protocol=self.protocol.hash())

    # the days of a test
    def record_day(self, name: str, day: int, candidate: float, parent: float, null: float) -> None:
        entry = self._entry(name)
        if entry.status != "TESTING" or entry.diffs is None or entry.null_diffs is None or entry.first_day is None:
            raise LessonError(f"a day is recorded for a lesson in test, not {entry.status}")
        if day < entry.first_day or (entry.last_day is not None and day <= entry.last_day):
            raise LessonError("days come in order, from the first day of the test, one score each")
        if len(entry.diffs) >= self.protocol.endpoint_days:
            raise LessonError("the endpoint has been reached: decide the lesson")
        for value in (candidate, parent, null):
            if not math.isfinite(value):
                raise LessonError("a score is a finite number")
        entry.last_day = day
        entry.diffs.append(candidate - parent)
        entry.null_diffs.append(null - parent)
        verdict = interim(entry.diffs, self.protocol)
        if verdict != "CONTINUE":
            entry.status, entry.reason = "REJECTED", verdict
            self._log("INTERIM_STOP", name, reason=verdict, days=len(entry.diffs))

    def endpoint_p(self, name: str) -> tuple[float, float]:
        """The raw one-sided p-value of the improvement and its Holm adjustment over the whole family."""
        entry = self._entry(name)
        if entry.diffs is None or len(entry.diffs) < self.protocol.endpoint_days:
            raise LessonError("the lesson has not reached its endpoint")
        names, family = self._family()
        return family[names.index(name)], paired_stats.holm_adjusted(family)[names.index(name)]

    def _family(self) -> tuple[list[str], list[float]]:
        names, values = [], []
        for key, e in self._entries.items():
            if e.candidate is None:
                continue
            names.append(key)
            done = e.diffs is not None and len(e.diffs) >= self.protocol.endpoint_days and e.reason not in ("HARM", "FUTILITY")
            values.append(newey_west_p(e.diffs) if done and e.diffs is not None else 1.0)
        return names, values

    def family_p_values(self) -> list[float]:
        return self._family()[1]

    def decide(self, name: str, guards: dict[str, bool] | None = None) -> None:
        entry = self._entry(name)
        if entry.status != "TESTING" or entry.diffs is None or entry.null_diffs is None or entry.candidate is None:
            raise LessonError(f"a lesson is decided from TESTING, not {entry.status}")
        if len(entry.diffs) < self.protocol.endpoint_days:
            raise LessonError("a lesson is decided only at its endpoint: promotion never comes early")
        raw, adjusted = self.endpoint_p(name)
        null_p = newey_west_p(entry.null_diffs)
        mean = math.fsum(entry.diffs) / len(entry.diffs)
        self._log("ENDPOINT", name, raw_p=raw, adjusted_p=adjusted, mean=mean, null_p=null_p, null_crossed=null_p <= self.protocol.alpha)
        breached = check_guards(guards)
        if breached:
            entry.status, entry.reason = "INCONCLUSIVE", "GUARD:" + breached[0]
            self._log("GUARD_BLOCKED", name, guards=breached)
        elif mean > 0 and adjusted <= self.protocol.alpha and null_p <= self.protocol.alpha:
            # an irrelevant change of similar size passed the same test: the period rewards any change, so this improvement shows nothing
            entry.status, entry.reason = "INCONCLUSIVE", "NULL_CROSSED"
            self._log("INCONCLUSIVE", name, reason="NULL_CROSSED")
        elif mean > 0 and adjusted <= self.protocol.alpha:
            self._promote(name, entry)
        elif mean <= 0:
            entry.status, entry.reason = "REJECTED", "NO_IMPROVEMENT"
            self._log("REJECTED", name, reason="NO_IMPROVEMENT")
        else:
            entry.status, entry.reason = "INCONCLUSIVE", "NOT_SIGNIFICANT"
            self._log("INCONCLUSIVE", name, reason="NOT_SIGNIFICANT")

    def _promote(self, name: str, entry: _Entry) -> None:
        assert entry.candidate is not None
        previous = self._live
        self._status[previous] = "SHADOW"  # the parent stays beside the promoted version for the holdback
        self._status[entry.candidate] = "LIVE"
        self._live = entry.candidate
        entry.status, entry.reason, entry.promoted_ever = "PROMOTED", None, True
        entry.holdback = []
        self._log("PROMOTED", name, version=entry.candidate, parent=previous)

    # the holdback
    def holdback_day(self, name: str, day: int, candidate: float, parent: float, guards: dict[str, bool] | None = None) -> None:
        entry = self._entry(name)
        if entry.status != "PROMOTED" or entry.holdback is None:
            raise LessonError(f"a holdback day is recorded for a promoted lesson, not {entry.status}")
        if entry.last_holdback_day is not None and day <= entry.last_holdback_day:
            raise LessonError("days come in order")
        entry.last_holdback_day = day
        entry.holdback.append(candidate - parent)
        breached = check_guards(guards)
        if breached:
            self._rollback(name, entry, "GUARD:" + breached[0])
            return
        if len(entry.holdback) >= self.protocol.min_days:
            _, high = confidence_sequence(entry.holdback, self.protocol)
            if high < -self.protocol.margin:
                self._rollback(name, entry, "REGRESSION")

    def _rollback(self, name: str, entry: _Entry, reason: str) -> None:
        assert entry.candidate is not None
        promoted = entry.candidate
        old_parent = self._versions[promoted][0]
        assert old_parent is not None
        diff = dict(self._versions[old_parent][2])  # the parent's own configuration
        new = _digest({"parent": promoted, "rollback_of": name, "diff": diff, "reason": reason})
        self._versions[new] = (promoted, None, diff)
        self._status[new] = "LIVE"
        self._status[promoted] = "ROLLED_BACK"
        self._status[old_parent] = "RETIRED"
        self._live = new
        entry.status, entry.reason = "ROLLED_BACK", reason
        self._log("ROLLBACK", name, reason=reason, version=new, rolled_back=promoted)

    # the public record
    def ledger(self) -> dict[str, Any]:
        entries = self._entries.values()
        rows = [
            {
                "lesson": e.lesson.lesson_id,
                "status": e.status,
                "reason": e.reason,
                "support": e.lesson.support,
                "days": 0 if e.diffs is None else len(e.diffs),
                "change": dict(e.lesson.change),
            }
            for e in entries
        ]
        return {
            "proposed": len(self._entries),
            "tested": sum(e.candidate is not None for e in entries),
            "promoted": sum(e.promoted_ever for e in entries),
            "rejected": sum(e.status == "REJECTED" for e in entries),
            "inconclusive": sum(e.status == "INCONCLUSIVE" for e in entries),
            "rolled_back": sum(e.status == "ROLLED_BACK" for e in entries),
            "null_crossed": sum(1 for ev in self._events if ev["event"] == "ENDPOINT" and ev["null_crossed"]),
            "protocol": self.protocol.hash(),
            "lessons": rows,
        }
