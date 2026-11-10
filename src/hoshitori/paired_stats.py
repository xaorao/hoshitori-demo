"""The statistics of an evidence challenge on a model arm (``EVIDENCE_CHALLENGE_SPEC`` section 4).

A challenge runs the arm ``n`` times on the full evidence and ``n`` times on the evidence with something taken out, and pairs the
runs. For each pair the question is whether the arm gave the original action. The four counts of the pairs are ``same_same``,
``same_changed`` (the full run gave the original action, the other did not), ``changed_same`` and ``changed_changed``. This module turns
those counts into a test, an interval and a label; it knows nothing about models, markets or evidence.

* The test is the exact McNemar test on the pairs that disagree.
* The interval is Tango's paired score interval. It is never narrower than the runs allow: with no pair disagreeing it still has width,
  where the Newcombe form used by the first spike collapsed to zero and would have passed the stability test on a handful of runs.
* A family of tests (the standard set of a receipt) is corrected with Holm's step-down method.
* Labels are derived from measured widths and counts, never from a fixed threshold on a point estimate. A source is never called
  irrelevant from a few stochastic runs, so there is no "ruled out".
* The estimand is the whole paired table of actions: with more than two actions in play, each action is tested on its own
  with Holm's method over them (``label_table``), so a move between two actions that are not the original one is not lost.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

CHANGED: Final = "DECISION_CHANGED"
STABLE: Final = "DECISION_STABLE"
UNCERTAIN: Final = "EFFECT_UNCERTAIN"
UNSTABLE_BASELINE: Final = "NOT_EVALUATED(unstable_baseline)"
STRUCTURAL: Final = "NOT_EVALUATED(structural_failure)"

Z: Final = 1.959964  # two-sided 95 percent
MIN_CHANGED: Final = 20  # runs needed before a change can be called
MIN_STABLE: Final = 60  # runs needed to call a source stable...
MIN_STABLE_AGREEING: Final = 40  # ...or this many when the pairs agree at least CONCORDANT of the time
CONCORDANT: Final = 0.8
STABLE_WIDTH: Final = 0.15  # the interval must lie inside plus or minus this
MAX_FAILED_SHARE: Final = 0.2  # more failed runs than this share of the attempted ones and nothing is evaluated
ALPHA: Final = 0.05


class StatsError(ValueError):
    """Counts that are not counts of pairs."""


@dataclass(frozen=True)
class Pairs:
    same_same: int
    same_changed: int
    changed_same: int
    changed_changed: int

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise StatsError(f"{name} is a count of pairs: a whole number of at least 0")
        if self.n == 0:
            raise StatsError("at least one pair is needed")

    @property
    def n(self) -> int:
        return self.same_same + self.same_changed + self.changed_same + self.changed_changed

    @property
    def shift(self) -> float:
        """The share of full runs that gave the original action, minus the share of the intervened runs that did."""
        return (self.same_changed - self.changed_same) / self.n

    @property
    def concordance(self) -> float:
        return (self.same_same + self.changed_changed) / self.n


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact p of the pairs that disagree (b one way, c the other); 1 when none disagree (the doubled tail, 2, is cut to 1)."""
    m = b + c
    return min(1.0, 2 * sum(math.comb(m, i) for i in range(min(b, c) + 1)) / (1 << m))


def _score(pairs: Pairs, delta: float) -> float:
    b, c, n = pairs.same_changed, pairs.changed_same, pairs.n
    bb = -b - c + (2 * n - b + c) * delta
    cc = -c * delta * (1 - delta)
    q = (math.sqrt(max(0.0, bb * bb - 8 * n * cc)) - bb) / (4 * n)
    var = n * (2 * q + delta * (1 - delta))
    diff = b - c - n * delta
    if var > 0:
        return diff / math.sqrt(var)
    return math.copysign(math.inf, diff) if diff else 0.0


def _bound(pairs: Pairs, lo: float, hi: float, level: float) -> float:
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _score(pairs, mid) > level else (lo, mid)
    return (lo + hi) / 2


def tango(pairs: Pairs, z: float = Z) -> tuple[float, float]:
    """Tango's score interval for the shift of the pairs."""
    edge = 1 - 1e-12
    lower = -1.0 if _score(pairs, -edge) <= z else _bound(pairs, -edge, pairs.shift, z)
    upper = 1.0 if _score(pairs, edge) >= -z else _bound(pairs, pairs.shift, edge, -z)
    return lower, upper


def holm(p_values: list[float], alpha: float = ALPHA) -> list[bool]:
    """Which hypotheses Holm's step-down method rejects, in the order given."""
    order = sorted(range(len(p_values)), key=lambda i: (p_values[i], i))
    rejected = [False] * len(p_values)
    for step, index in enumerate(order):
        if p_values[index] > alpha / (len(p_values) - step):
            break
        rejected[index] = True
    return rejected


def holm_adjusted(p_values: Sequence[float]) -> list[float]:
    """Holm's adjusted p-values, in the order given: a hypothesis is rejected at a level exactly when its adjusted value is at most it."""
    order = sorted(range(len(p_values)), key=lambda i: (p_values[i], i))
    adjusted = [1.0] * len(p_values)
    running = 0.0
    for step, index in enumerate(order):
        running = max(running, min(1.0, (len(p_values) - step) * p_values[index]))
        adjusted[index] = running
    return adjusted


Run = tuple[str, str, str]  # the actions of one pair: its first run, its run with the change, and its control run on the unchanged input
CHANGED_RUN: Final = 1
CONTROL_RUN: Final = 2


def table(runs: Sequence[Run], action: str, other: int) -> Pairs:
    """The pairs of "gave ``action``": each pair's first run against its changed run (``CHANGED_RUN``) or its control (``CONTROL_RUN``)."""
    if other not in (CHANGED_RUN, CONTROL_RUN):
        raise StatsError("a pair's first run is compared with its changed run or its control run")
    return Pairs(
        sum(1 for run in runs if run[0] == action and run[other] == action),
        sum(1 for run in runs if run[0] == action and run[other] != action),
        sum(1 for run in runs if run[0] != action and run[other] == action),
        sum(1 for run in runs if run[0] != action and run[other] != action),
    )


@dataclass(frozen=True)
class Verdict:
    label: str
    n: int
    shift: float
    low: float
    high: float
    p: float
    note: str


def label(treated: Pairs, control: Pairs, failed: int = 0, rejected: bool | None = None, *, attempted: int | None = None) -> Verdict:
    """The label of one challenge.

    ``control`` is the same comparison with nothing taken out (the model against itself); when it already shifts, the model is too
    unstable to read. ``failed`` is the number of runs that errored or returned nothing usable, which count against the evidence, out of
    ``attempted`` runs (by default the pairs and the failed runs, for a caller that leaves a failed pair out of the counts).
    ``rejected`` is the answer of Holm's method when the challenge is one of a family; alone it is the plain test at ``ALPHA``.
    """
    if isinstance(failed, bool) or not isinstance(failed, int) or failed < 0:
        raise StatsError("the failed runs are a whole number of at least 0")
    asked = treated.n + failed if attempted is None else attempted
    if isinstance(asked, bool) or not isinstance(asked, int) or asked < max(failed, 1):
        raise StatsError("the runs attempted are a whole number, at least one and at least the runs that failed")
    low, high = tango(treated)
    p = mcnemar_exact(treated.same_changed, treated.changed_same)
    if failed / asked > MAX_FAILED_SHARE:
        return Verdict(STRUCTURAL, treated.n, treated.shift, low, high, p, f"{failed} of {asked} runs failed")
    c_low, c_high = tango(control)
    if c_low > 0 or c_high < 0:
        return Verdict(UNSTABLE_BASELINE, treated.n, treated.shift, low, high, p, "the unchanged input already shifts the answer")
    significant = p <= ALPHA if rejected is None else rejected
    if treated.n >= MIN_CHANGED and significant:
        return Verdict(CHANGED, treated.n, treated.shift, low, high, p, "the shift is larger than the model's own variation")
    enough = treated.n >= MIN_STABLE or (treated.n >= MIN_STABLE_AGREEING and treated.concordance >= CONCORDANT)
    if enough and low >= -STABLE_WIDTH and high <= STABLE_WIDTH:
        return Verdict(STABLE, treated.n, treated.shift, low, high, p, "any shift is inside the stated width")
    return Verdict(UNCERTAIN, treated.n, treated.shift, low, high, p, "too few runs to say")


def label_table(runs: Sequence[Run], reference: str, failed: int = 0, *, attempted: int | None = None) -> Verdict:
    """The label over the whole paired table of actions. ``runs`` holds, for each pair, the action of its first run, of
    its changed run and of its control run; ``reference`` is the original action.

    With at most two actions in play (the reference and one other) this is ``label`` on "gave the reference", the headline test. With
    more, each action is tested on its own ("gave this action" in the first run against the changed run) with Holm's method over the
    actions, and the labels are combined: a failure of the runs, then an unchanged input that already shifts any action, then a change of
    any action; stable only when every action's share is stable. The shift, interval and ``n`` reported are the reference's; ``p`` is the
    smallest adjusted value of the family.
    """
    head = label(table(runs, reference, CHANGED_RUN), table(runs, reference, CONTROL_RUN), failed, attempted=attempted)
    actions = sorted({action for run in runs for action in run} | {reference})
    if len(actions) <= 2 or head.label == STRUCTURAL:  # too many failed runs is the same verdict for every action
        return head
    tables = [(table(runs, action, CHANGED_RUN), table(runs, action, CONTROL_RUN)) for action in actions]
    adjusted = holm_adjusted([mcnemar_exact(changed.same_changed, changed.changed_same) for changed, _ in tables])
    verdicts = [label(changed, control, failed, adjusted[k] <= ALPHA, attempted=attempted) for k, (changed, control) in enumerate(tables)]
    found = [verdict.label for verdict in verdicts]
    for outcome in (UNSTABLE_BASELINE, CHANGED, UNCERTAIN):
        if outcome in found:
            which = ", ".join(actions[k] for k, verdict in enumerate(verdicts) if verdict.label == outcome)
            return replace(head, label=outcome, p=min(adjusted), note=f"{verdicts[found.index(outcome)].note} ({which})")
    return replace(head, label=STABLE, p=min(adjusted), note=f"any shift of each of {', '.join(actions)} is inside the stated width")
