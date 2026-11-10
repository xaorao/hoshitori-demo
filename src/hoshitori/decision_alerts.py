"""Alerts about changes that affect a decision, never about a price alone.

Four kinds, each read from a record, each a closed sentence:

* ``INVALIDATED``: a settled up-or-down call named a closing price that would prove it wrong (``close below 87.56``), and the price the
  window closed at met it. Only that form is checked: it is exact against the settlement's own closing price. A condition on a return
  (``ret_1h above 0``) does not say over which hour, and one on the entry and a range (``close below entry minus 1.5 ATR14``) needs a
  range the settlement does not hold; neither is guessed, so neither raises an alert.
* ``THESIS_CHANGED``: a premise of a committed call changed after it was made (a thesis revision ``MATERIAL_UPDATE``); the call stays as
  it was recorded.
* ``REVISED``: the same agent made a later call on the same token and horizon that points the other way while its earlier call is still
  open, and both calls stand at least five points from an even chance (``call_revisions``); both stay as recorded.
* ``RISK_STEP``: a practice book moved down its drawdown ladder (sizes halved, closed, stopped), a simulated portfolio's rule.
* ``SETTLED``: a call reached its end: settled with its outcome, or could not be settled, with the reason.

The list is ordered by how much it should change what a person does next, then newest first: a call proved wrong by its own terms, then a
changed premise, then an agent reversing an open call, then a risk rung, then an ordinary settlement. Nothing is ranked by price movement
and no alert claims skill. A person follows calls, tokens or agents in the page; the page filters this list, and nothing about who follows
what leaves the browser.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Final

from hoshitori import call_revisions, labels, practice_view

KINDS: Final = ("INVALIDATED", "THESIS_CHANGED", "REVISED", "RISK_STEP", "SETTLED")
_LEVEL: Final = re.compile(r"^(close|last_close) (below|above) (\d+(?:\.\d+)?)$")
MODE_WORDS: Final = {
    "ACTIVE": "went back to its planned sizes",
    "HALVED": "halved its sizes",
    "FLAT": "closed its positions",
    "HALTED": "stopped trading",
}
HOUR_MS: Final = 3_600_000


class AlertError(ValueError):
    """Records an alert cannot be made from (a ledger defect)."""


@dataclass(frozen=True)
class Alert:
    kind: str  # one of KINDS
    at: str  # when it happened, as the record says
    text: str  # the closed sentence
    forecast_id: str | None  # the call it is about; none for a practice book's rung
    symbol: str | None
    arm: str | None
    book: str | None  # the practice book, for a rung


def _hours(ms: int) -> str:
    hours = ms // HOUR_MS
    return "1 hour" if hours == 1 else f"{hours} hours"


def _who(call: Mapping[str, Any]) -> str:
    return f"{call['symbol']}, {_hours(int(call['horizon_ms']))}, {labels.arm_name(call['arm_id']).lower()}"


def _met(condition: str, closed: Decimal) -> tuple[str, Decimal] | None:
    """(the side, the level) when ``condition`` is a closing-price level that ``closed`` met; none otherwise or when it is another form."""
    m = _LEVEL.fullmatch(condition)
    if m is None:
        return None
    level = Decimal(m.group(3))
    side = m.group(2)
    return (side, level) if (closed < level if side == "below" else closed > level) else None


def alerts(records: Sequence[Mapping[str, Any]], books: Sequence[practice_view.PracticeBook] = ()) -> tuple[Alert, ...]:
    """Every decision alert the records hold, most consequential first, then newest first."""
    calls = {r["id"]: r["body"] for r in records if r.get("type") == "forecast"}
    seen = {r["id"]: r["body"]["first_seen_at"] for r in records if r.get("type") == "post_snapshot_event"}
    found: list[Alert] = []
    for record in records:
        kind = record.get("type")
        if kind == "settlement":
            body = record["body"]
            call = calls.get(body["forecast_id"])
            if call is None:
                raise AlertError(f"a settlement names a call the ledger does not hold: {body['forecast_id']}")
            at = record["created_at"]
            if body["state"] != "SETTLED":
                text = f"{_who(call)}: could not be settled ({str(body['state']).lower().replace('_', ' ')})."
                found.append(Alert("SETTLED", at, text, body["forecast_id"], call["symbol"], call["arm_id"], None))
                continue
            if call["contract"] == "D":
                rose = body["outcome"] == "1"
                found.append(
                    Alert(
                        "SETTLED",
                        at,
                        f"{_who(call)}: settled; the price {'rose' if rose else 'fell'}.",
                        body["forecast_id"],
                        call["symbol"],
                        call["arm_id"],
                        None,
                    )
                )
                try:
                    closed = Decimal(str(body["outcome_price"]))
                except (InvalidOperation, TypeError):
                    continue  # no closing price, nothing to check a level against
                for condition in call.get("invalidation") or ():
                    hit = _met(condition, closed)
                    if hit is not None:
                        side, level = hit
                        found.append(
                            Alert(
                                "INVALIDATED",
                                at,
                                f"{_who(call)}: its own condition for being wrong was met: the window closed at {_plain(closed)}, "
                                f"{side} {_plain(level)}.",
                                body["forecast_id"],
                                call["symbol"],
                                call["arm_id"],
                                None,
                            )
                        )
                        break
            else:
                found.append(
                    Alert(
                        "SETTLED",
                        at,
                        f"{_who(call)}: settled; {str(body['outcome']).lower()} first."
                        if body["outcome"] in ("TARGET", "STOP")
                        else f"{_who(call)}: settled; neither level was reached.",
                        body["forecast_id"],
                        call["symbol"],
                        call["arm_id"],
                        None,
                    )
                )
        elif kind == "thesis_revision" and record["body"]["kind"] == "MATERIAL_UPDATE":
            body = record["body"]
            call = calls.get(body["thesis_id"])
            if call is None or body["event_id"] not in seen:
                raise AlertError("a revision names a call or an event that the ledger does not hold")
            found.append(
                Alert(
                    "THESIS_CHANGED",
                    seen[body["event_id"]],
                    f"{_who(call)}: a premise changed after the call was made ({body['changed'][0]}); the call stays as it was recorded.",
                    body["thesis_id"],
                    call["symbol"],
                    call["arm_id"],
                    None,
                )
            )
    for v in call_revisions.revisions(records):
        if not v.decisive:
            continue  # a swing across an even chance is linked on the receipts; only a decisive reversal is worth an alert
        later = calls[v.later]
        found.append(
            Alert(
                "REVISED",
                v.later_origin,
                f"{_who(later)}: now puts a rise at {_percent(v.later_p)} while its call of {v.earlier_origin[11:16]} UTC "
                f"({_percent(v.earlier_p)}) is still open; both stay as recorded.",
                v.later,
                later["symbol"],
                later["arm_id"],
                None,
            )
        )
    for b in books:
        for step in b.steps:
            fall = (Decimal(step.drawdown) * 100).quantize(Decimal("0.1"))
            found.append(
                Alert(
                    "RISK_STEP",
                    step.at,
                    f"Practice, {b.arm_name.lower()} at {b.level.lower().replace('_', ' ')} costs: {MODE_WORDS[step.mode]} after a fall of "
                    f"{fall}% from its peak (a simulated portfolio's rule).",
                    None,
                    None,
                    b.arm,
                    b.book,
                )
            )
    order = {k: n for n, k in enumerate(KINDS)}
    return tuple(sorted(found, key=lambda a: (order[a.kind], _negated(a.at), a.forecast_id or a.book or "")))


def _percent(p: str) -> str:
    """A chance as a whole percentage, a half rounded up as the page rounds it (``0.535519`` as ``54%``, ``0.545`` as ``55%``)."""
    return f"{(Decimal(p) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)}%"


def _plain(value: Decimal) -> str:
    """A price as written without trailing zeros (``10.61700000`` as ``10.617``)."""
    return format(value.normalize(), "f")


def _negated(at: str) -> tuple[int, ...]:
    """A key that sorts the newest first: each character's code negated (the times are fixed-width RFC 3339 text)."""
    return tuple(-ord(ch) for ch in at)
