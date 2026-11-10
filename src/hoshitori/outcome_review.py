"""A review of settled calls: what can be observed about each, and what has and has not been learned from them.

A settled outcome is evidence about a forecast, not proof about the reasoning: an unfavourable outcome does not show faulty
reasoning, nor a favourable one sound reasoning (contract section 55). So a review only *describes*, from stored records and
nothing a model said:

* ``LOW_CONVICTION``: the committed chance was within five points of one half, so neither outcome contradicts it;
* ``EVIDENCE_GAP_AT_DECISION``: a source was asked for evidence of the call and brought nothing;
* ``MARKET_MOVEMENT_CONSISTENT_WITH_ALTERNATIVE``: BTC, over the same window, moved the same way as the asset, by at least half as much;
  consistent with a market-wide move, never a reason;
* ``NO_PROCESS_DEFECT_IDENTIFIED``: none of the above applies.

The words *caused*, *explains*, *unlucky* and *bad luck* are never written. A lesson, a typed change to an agent, is only
proposed once at least ``N_SUPPORT`` settled calls match one pattern, and it reaches an agent only through a forward test
(section 55.3): none is built, so the ledger reports its counts and says so. Reviews cover contract D calls; a barrier call is
listed as not reviewed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

from hoshitori.view_models import Receipt, SettlementLine, Views

N_SUPPORT: Final = 10  # settled matching calls before a lesson can even be tested (a prior, fixed by the contract)
LOW_CONVICTION_BAND: Final = Decimal("5")  # points either side of 50%
BTC_SHARE: Final = Decimal("0.5")  # the share of the asset's move that BTC's must reach, in the same direction (a prior)
_CHANCE: Final = re.compile(r"([0-9]{1,3})% up")
CODES: Final = (
    "LOW_CONVICTION",
    "EVIDENCE_GAP_AT_DECISION",
    "MARKET_MOVEMENT_CONSISTENT_WITH_ALTERNATIVE",
    "NO_PROCESS_DEFECT_IDENTIFIED",
)
BANNED: Final = ("caused", "explains", "unlucky", "bad luck")


@dataclass(frozen=True)
class Observation:
    code: str
    text: str


@dataclass(frozen=True)
class Review:
    forecast_id: str
    cycle_id: str
    symbol: str
    hours: int
    arm_name: str
    chance: str  # as the call stated it
    outcome: str  # "rose" or "fell"
    move: str  # the asset's change over the window, as a signed percentage
    agreed: str  # "with the call" when the outcome went the way the call leaned, "against the call" when not, "neither" at one half
    observations: tuple[Observation, ...]


CHANGES: Final = {  # the typed change a pattern could propose, from a closed list; a pattern that has none is only a description
    "LOW_CONVICTION": {"kind": "ABSTAIN_WHEN_CHANCE_NEAR_ONE_HALF", "points": "5"},
    "EVIDENCE_GAP_AT_DECISION": {"kind": "REQUIRE_EVERY_ASKED_SOURCE_OR_NO_CALL"},
}


@dataclass(frozen=True)
class Pattern:
    """A kind of observation that keeps coming back for one agent, with how many settled calls match it and how they went. ``change`` is the
    typed change it could propose, or none; a pattern is ``WATCHING`` below ``N_SUPPORT`` matching calls and ``PROPOSED`` at or above it."""

    arm_name: str
    code: str
    support: int
    against_the_call: int  # of the matching calls, how many went against the way the call leaned
    change: dict[str, str] | None
    status: str


@dataclass(frozen=True)
class Ledger:
    reviewed: int
    not_reviewed: int  # settled calls of a contract this review does not cover
    proposed: int
    tested: int
    promoted: int
    rejected: int
    n_support: int
    statement: str
    patterns: tuple[Pattern, ...] = ()


@dataclass(frozen=True)
class Reviews:
    reviews: tuple[Review, ...]
    ledger: Ledger


def _dec(text: object) -> Decimal | None:
    try:
        value = Decimal(str(text))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _percent(value: Decimal) -> str:
    return f"{value * 100:+.2f}%"


def _move(s: SettlementLine) -> Decimal | None:
    """The asset change over the window from the settlement prices; none when one is missing or the reference is not above zero."""
    ref, out = _dec(s.reference_price), _dec(s.outcome_price)
    return None if ref is None or out is None or ref <= 0 else out / ref - 1


def build(views: Views) -> Reviews:
    settled = [r for r in views.receipts if r.settlement is not None and r.settlement.state == "SETTLED"]
    covered: list[tuple[Receipt, Decimal, Decimal]] = []  # the receipt, the chance in points and the asset's move
    for r in settled:
        assert r.settlement is not None
        match = _CHANCE.fullmatch(r.row.chance or "")
        move = _move(r.settlement)
        if r.row.contract == "D" and match is not None and move is not None and move != 0:  # a price that did not move is a tie: not scored
            covered.append((r, Decimal(match.group(1)), move))
    btc = {(r.row.cycle_id, r.row.hours): move for r, _, move in covered if r.row.symbol == "BTC"}
    out: list[Review] = []
    for r, chance, move in covered:
        row = r.row
        rose = move > 0
        agreed = "neither" if chance == 50 else "with the call" if (chance > 50) == rose else "against the call"
        found: list[Observation] = []
        if abs(chance - 50) <= LOW_CONVICTION_BAND:
            found.append(
                Observation(
                    "LOW_CONVICTION",
                    f"The call put the chance of a rise at {int(chance)}%, close to one half, so neither outcome contradicts it.",
                )
            )
        if r.unread:
            names = ", ".join(sorted({u.source for u in r.unread}))
            found.append(Observation("EVIDENCE_GAP_AT_DECISION", f"A source asked for evidence of this call brought nothing: {names}."))
        market = btc.get((row.cycle_id, row.hours))
        if row.symbol != "BTC" and market is not None and (move > 0) == (market > 0) and abs(market) >= abs(move) * BTC_SHARE:
            found.append(
                Observation(
                    "MARKET_MOVEMENT_CONSISTENT_WITH_ALTERNATIVE",
                    f"BTC moved {_percent(market)} over the same window and {row.symbol} {_percent(move)}, the same way. "
                    "That is consistent with a market-wide move; it does not establish a reason.",
                )
            )
        if not found:
            found.append(
                Observation("NO_PROCESS_DEFECT_IDENTIFIED", "Nothing in the stored record points to a defect in how this call was made.")
            )
        out.append(
            Review(
                row.forecast_id,
                row.cycle_id,
                row.symbol,
                row.hours,
                row.arm_name,
                row.chance or "",
                "rose" if rose else "fell",
                _percent(move),
                agreed,
                tuple(found),
            )
        )
    counts: dict[tuple[str, str], list[int]] = {}
    for review in out:
        for obs in review.observations:
            if obs.code == "NO_PROCESS_DEFECT_IDENTIFIED":
                continue
            tally = counts.setdefault((review.arm_name, obs.code), [0, 0])
            tally[0] += 1
            tally[1] += review.agreed == "against the call"
    patterns = tuple(
        Pattern(arm, code, n, bad, CHANGES.get(code), "PROPOSED" if n >= N_SUPPORT and code in CHANGES else "WATCHING")
        for (arm, code), (n, bad) in sorted(counts.items())
    )
    proposed = sum(p.status == "PROPOSED" for p in patterns)
    watching = len(patterns) - proposed
    if proposed:
        said = (
            f"{proposed} {'lesson is' if proposed == 1 else 'lessons are'} proposed: a pattern matched {N_SUPPORT} or more settled "
            "calls. None has been tested: the forward test against a parent and a null lesson exists, and no lesson has reached it."
        )
    else:
        said = (
            f"No lesson has been proposed. A lesson needs {N_SUPPORT} settled calls that match one pattern, and then a forward test "
            "against its parent and a null lesson; that test exists and no lesson has reached it."
        )
    ledger = Ledger(
        reviewed=len(out),
        not_reviewed=len(settled) - len(out),
        proposed=proposed,
        tested=0,
        promoted=0,
        rejected=0,
        n_support=N_SUPPORT,
        statement=(
            f"{said} {watching} {'pattern is' if watching == 1 else 'patterns are'} being watched. {len(out)} settled "
            f"{'call has' if len(out) == 1 else 'calls have'} been reviewed. No agent has been changed by a review."
        ),
        patterns=patterns,
    )
    return Reviews(tuple(out), ledger)
