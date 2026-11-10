"""What a receipt links to: the receipt is the hub, and every lens of the product on the same call is reached from it.

For one call, read from what the site already holds and nothing else:

* **The same opportunity, other agents.** Every other call of the same cycle on the same token, window and question: the agents and the
  simple baseline were asked the same thing from the same frozen evidence, so these are the calls to set beside it. In the order of the
  agents' fixed order, never ranked.
* **What following it did.** The practice trades whose sale follows this call, in each book (a book is one agent at one cost level): what
  a simulated portfolio would have done, never an order sent anywhere.
* **The Desk on this token.** The Desk's runs about the same token, newest last, at most ``MAX_DESK``: an investigation of the token, not
  of this call, and labelled with how it was made (live, recorded, invented).

Nothing here changes a call, a score or a settlement; a link that cannot be made is left out, never invented.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from hoshitori import call_revisions, desk_view, labels, practice_view, view_models

MAX_DESK: Final = 3
CENT: Final = Decimal("0.01")


@dataclass(frozen=True)
class SameCall:
    forecast_id: str
    arm: str
    arm_name: str
    chance: str | None
    state: str
    result: str | None


@dataclass(frozen=True)
class Followed:
    book: str
    arm_name: str
    level: str
    entry_price: str
    exit_price: str
    spent: str  # what the purchase cost before fees: quantity times the entry price; small when the token was already held near its cap
    net: str
    exit_kind: str | None
    closed_at: str


@dataclass(frozen=True)
class DeskLink:
    run_id: str
    label: str
    state: str
    forecast_id: str | None


@dataclass(frozen=True)
class Links:
    others: tuple[SameCall, ...]
    followed: tuple[Followed, ...]
    desk: tuple[DeskLink, ...]
    revisions: tuple[call_revisions.Revision, ...] = ()  # the agent's reversals that this call made, or that a later call made of it


def _order(arm: str) -> tuple[int, str]:
    known = tuple(labels.ARM_NAMES)
    return (known.index(arm) if arm in known else len(known), arm)


def _spent(quantity: str, price: str) -> str:
    return str((Decimal(quantity) * Decimal(price)).quantize(CENT))


def links(
    row: view_models.Row,
    rows: Sequence[view_models.Row],
    books: Sequence[practice_view.PracticeBook] = (),
    desk_runs: Sequence[desk_view.DeskRun] = (),
    revisions: Sequence[call_revisions.Revision] = (),
) -> Links:
    """The links of the call ``row``: its peers among ``rows``, the practice trades that followed it, the Desk's runs on its token."""
    others = sorted(
        (
            r
            for r in rows
            if r.cycle_id == row.cycle_id
            and r.symbol == row.symbol
            and r.hours == row.hours
            and r.contract == row.contract
            and r.forecast_id != row.forecast_id
        ),
        key=lambda r: _order(r.arm),
    )
    followed = tuple(
        Followed(
            b.book, b.arm_name, b.level, t.entry_price, t.exit_price, _spent(t.quantity, t.entry_price), t.net, t.exit_kind, t.closed_at
        )
        for b in books
        for t in b.trades
        if t.forecast_id == row.forecast_id
    )
    desk = [DeskLink(d.run_id, d.label, d.state, d.forecast_id) for d in desk_runs if d.symbol == row.symbol]
    return Links(
        tuple(SameCall(r.forecast_id, r.arm, r.arm_name, r.chance, r.state, r.result) for r in others),
        followed,
        tuple(desk[-MAX_DESK:]),
        tuple(v for v in revisions if row.forecast_id in (v.earlier, v.later)),
    )
