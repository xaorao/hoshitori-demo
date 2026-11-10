"""The Desk inside the league: when a cycle admits an event and the token then moved, the Desk investigates it, under a daily cap.

* **What starts a run.** In a committed cycle, an included candidate that came in on the ``EVENT`` route (a venue's own change to its list:
  a new instrument, one removed, a status changed), whose event was first seen at most 24 hours before the cycle's snapshot, and whose
  price then moved at least ``THRESHOLD`` times its expected move over the shortest window of 1, 4 or 24 hours that covers the event:
  ``|ret_h| / (atr14_share * sqrt(h))`` from the cycle's own features evidence (the quantity the rules agent reads). The direction is the
  move's. The Desk's hypothesis is that the move began after the event, which its falsification step tests against the event's time; a
  price move with no event gives that step nothing to test, so it starts no run. Fixed here, before any automatic run.
* **What else starts a run (ADR-031).** In a committed cycle, two agents on opposite sides of a coin flip on one up-or-down call of an
  included candidate: the lower chance at most 49 percent, the higher at least 51 percent, at least 6 points apart (each rounded to a whole
  percent, a half up, as the page shows it), a window of at most 24 hours, the same token and horizon. The Desk is
  asked to test the up reading. The trigger has no event time, so the Desk's onset and market tests are not testable for it (the existing
  no-event-time path) and its confidence ceiling is capped as for any run whose time-anchored tests could not be done. A move after an event
  outranks a disagreement; the cap and the once-per-trigger rule are shared.
* **The cap.** At most ``DAILY_RUNS`` Desk runs in a UTC day, counted from the Desk's own ledger (its records of runs that started), so a
  restart cannot reset it; a token is investigated at most once a day; the largest moves go first. Each run may ask RYO at most
  ``RYO_CALLS`` time and the model at most twice (one answer and, if it does not fit, one repair), within the owner's cap of three.
* **Once per event.** A trigger's id is the league's own trigger id of the event and the rule, and a run names its trigger; a trigger the
  Desk ledger already holds a run for is never run again, whatever restarts in between.

This module only decides what to run (``pick``); the run itself is ``desk_live.run_live`` with the caller's proposer and on-demand wiring,
so a long stance is committed, time-stamped and settled on the on-demand ledger, and a flat one or an abstention is recorded as the result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Final

from hoshitori import desk_plan
from hoshitori.ids import natural_id
from hoshitori.timeline import to_ms
from hoshitori.timeutil import parse_rfc3339

RULE: Final = "EVENT_THEN_MOVE_2_EXPECTED"
RULE_DISPUTE: Final = "AGENTS_DISAGREE_6_POINTS"
LOW_MAX: Final = 49  # percent: the lower chance is at most this and the higher at least HIGH_MIN: opposite sides of a coin flip
HIGH_MIN: Final = 51
GAP: Final = 6  # percentage points: and they are at least this far apart. The chances are first rounded to whole percents, a half up, as
# the page shows them, so the page's disagreement card and the Desk's trigger agree on every case.
THRESHOLD: Final = Decimal(2)
WINDOWS: Final = (1, 4, 24)  # the hours the features evidence measures a return over, shortest first
DAILY_RUNS: Final = 3
RYO_CALLS: Final = 1
HOUR_MS: Final = 3_600_000
MAX_HOURS: Final = desk_plan.MAX_HOURS  # a run speaks of a window of at most this many hours
DAY_MS: Final = 86_400_000
FEATURES_SOURCE: Final = "hoshitori.features"


class DeskHookError(ValueError):
    """Records the hook cannot read (a ledger defect)."""


@dataclass(frozen=True)
class Move:
    cycle_id: str
    asset_id: str
    symbol: str
    event_id: str  # the league's trigger id of the event
    event_ms: int  # when the event was first seen
    hours: int  # the shortest window of the features evidence that covers the event
    size: Decimal  # |ret_h| / (atr14_share * sqrt(h))
    direction: str  # UP or DOWN


@dataclass(frozen=True)
class Dispute:
    cycle_id: str
    asset_id: str
    symbol: str
    route: str  # the first included route the candidate came in on
    hours: int
    gap: Decimal  # the higher chance less the lower
    low_arm: str
    high_arm: str


@dataclass(frozen=True)
class Today:
    runs: int  # Desk runs started this UTC day
    symbols: frozenset[str]  # tokens already investigated this UTC day
    triggers: frozenset[str]  # every trigger the Desk ledger holds a run for, on any day


def moves(league: Sequence[Mapping[str, Any]], cycle_id: str) -> tuple[Move, ...]:
    """The event candidates of one committed cycle whose price moved enough since the event, from its snapshot's features evidence."""
    snaps = [r["body"] for r in league if r.get("type") == "snapshot" and r["body"]["cycle_id"] == cycle_id]
    manifests = [r["body"] for r in league if r.get("type") == "manifest" and r["body"]["cycle_id"] == cycle_id]
    if not snaps or not manifests:
        return ()
    at = _instant(snaps[0]["snapshot_at"])
    events: dict[str, tuple[str, str, int]] = {}
    for entry in manifests[0]["entries"]:
        for route in entry["routes"]:
            if route["route"] == "EVENT" and route["decision"] == "INCLUDED" and entry["inclusion"] == "INCLUDED":
                events[entry["candidate_id"]] = (entry["symbol"], route["trigger_id"], _instant(route["first_seen_at"]))
    evidence = {r["id"]: r["body"] for r in league if r.get("type") == "evidence"}
    out = []
    for cand in snaps[0]["candidates"]:
        if cand["candidate_id"] not in events:
            continue
        symbol, event_id, event_ms = events[cand["candidate_id"]]
        hours = next((h for h in WINDOWS if 0 <= at - event_ms <= h * HOUR_MS), None)
        if hours is None:
            continue  # an event seen after the snapshot, or more than a day before it, has no window that covers it
        for item in cand["items"]:
            body = evidence.get(item["evidence_id"])
            if body is None or body.get("source") != FEATURES_SOURCE:
                continue
            fields = body["fields"]
            try:
                ret, share = Decimal(fields[f"ret_{hours}h"]), Decimal(fields["atr14_share"])
            except (KeyError, InvalidOperation, TypeError):
                continue  # a candidate without both numbers has no measured move: it is never a trigger
            if share <= 0:
                continue
            size = abs(ret) / (share * Decimal(hours).sqrt())
            if size >= THRESHOLD:
                out.append(Move(cycle_id, cand["asset_id"], symbol, event_id, event_ms, hours, size, "UP" if ret > 0 else "DOWN"))
    return tuple(out)


def disputes(league: Sequence[Mapping[str, Any]], cycle_id: str) -> tuple[Dispute, ...]:
    """The calls of one committed cycle on which two agents stand on opposite sides of a coin flip, at least ``GAP`` apart.

    Up-or-down forecasts only (contract D); an included candidate only; the same token and horizon; the pair furthest apart is the dispute.
    """
    manifests = [r["body"] for r in league if r.get("type") == "manifest" and r["body"]["cycle_id"] == cycle_id]
    if not manifests:
        return ()
    routes: dict[str, str] = {}
    for entry in manifests[0]["entries"]:
        if entry["inclusion"] != "INCLUDED":
            continue
        included = [route["route"] for route in entry["routes"] if route["decision"] == "INCLUDED"]
        if included:
            routes[entry["symbol"]] = included[0]
    groups: dict[tuple[str, str, int], list[tuple[int, str]]] = {}
    for r in league:
        body = r.get("body")
        if r.get("type") != "forecast" or body is None or body["cycle_id"] != cycle_id or body["contract"] != "D":
            continue
        if body["symbol"] not in routes:
            continue
        try:
            chance = int((Decimal(body["p"]) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        except (KeyError, InvalidOperation, TypeError):
            continue  # a forecast without a readable chance takes no side
        groups.setdefault((body["asset_id"], body["symbol"], body["horizon_ms"]), []).append((chance, body["arm_id"]))
    out = []
    for (asset_id, symbol, horizon_ms), calls in groups.items():
        low, high = min(calls), max(calls)
        if (
            low[0] <= LOW_MAX
            and high[0] >= HIGH_MIN
            and high[0] - low[0] >= GAP
            and horizon_ms % HOUR_MS == 0
            and horizon_ms <= MAX_HOURS * HOUR_MS
        ):
            gap = Decimal(high[0] - low[0]) / 100
            out.append(Dispute(cycle_id, asset_id, symbol, routes[symbol], horizon_ms // HOUR_MS, gap, low[1], high[1]))
    return tuple(sorted(out, key=lambda d: (d.symbol, d.hours)))


def today(desk: Sequence[Mapping[str, Any]], now_ms: int) -> Today:
    """What the Desk ledger says about today: runs started in this UTC day, their tokens, and every trigger ever run."""
    start = now_ms - now_ms % DAY_MS
    runs = [r["body"] for r in desk if r.get("type") == "desk_run_state" and r["body"]["state"] == "RUNNING"]
    todays = [b for b in runs if start <= b["at_ms"] < start + DAY_MS]
    return Today(
        len({b["run_id"] for b in todays}),
        frozenset(b["symbol"] for b in todays),
        frozenset(b["trigger_id"] for b in runs if b["trigger_id"] is not None),
    )


def trigger_of(move: Move) -> desk_plan.Trigger:
    ident = natural_id("dt", {"event": move.event_id, "rule": RULE})
    return desk_plan.Trigger(ident, "EVENT", move.symbol, move.direction, move.hours, move.event_ms)


def trigger_of_dispute(dispute: Dispute) -> desk_plan.Trigger:
    ident = natural_id("dt", {"cycle": dispute.cycle_id, "symbol": dispute.symbol, "hours": dispute.hours, "rule": RULE_DISPUTE})
    return desk_plan.Trigger(ident, dispute.route, dispute.symbol, "UP", dispute.hours, None, "DISAGREEMENT")


def pick(found: Sequence[Move], seen: Today, disputed: Sequence[Dispute] = ()) -> tuple[desk_plan.Trigger, ...]:
    """The triggers to run now: the largest moves first, then the widest disagreements; one per token a day, none already run, within what
    is left of the day's cap. A move after an event outranks a disagreement, which has no event to test."""
    room = max(DAILY_RUNS - seen.runs, 0)
    ordered = [trigger_of(m) for m in sorted(found, key=lambda m: (-m.size, m.symbol))]
    ordered += [trigger_of_dispute(d) for d in sorted(disputed, key=lambda d: (-d.gap, d.symbol, d.hours))]
    out: list[desk_plan.Trigger] = []
    taken = set(seen.symbols)
    for trigger in ordered:
        if len(out) >= room:
            break
        if trigger.symbol in taken or trigger.trigger_id in seen.triggers:
            continue
        taken.add(trigger.symbol)
        out.append(trigger)
    return tuple(out)


def _instant(text: object) -> int:
    if isinstance(text, int) and not isinstance(text, bool):
        return text
    if not isinstance(text, str):
        raise DeskHookError("a time of the ledger is a time")
    return to_ms(parse_rfc3339(text))


@dataclass(frozen=True)
class Passed:
    triggers: tuple[str, ...]  # the triggers run in this pass, in the order they ran
    outcomes: tuple[tuple[str, str, str | None], ...]  # (trigger, the run's final state, its forecast id when it committed one)
    failed: tuple[tuple[str, str], ...] = ()  # (trigger, why): a run that raised; it is never retried as the same trigger in this pass


def run_pass(
    league: Sequence[Mapping[str, Any]],
    desk: Sequence[Mapping[str, Any]],
    cycle_id: str,
    now_ms: int,
    run: Any,
) -> Passed:
    """Run the Desk on the moved events of ``cycle_id`` that the cap allows. ``run(trigger, run_id)`` runs one (``desk_live.run_live`` with
    the deployment's proposer and on-demand wiring) and returns its outcome; one run that raises does not stop the next."""
    chosen = pick(moves(league, cycle_id), today(desk, now_ms), disputes(league, cycle_id))
    outcomes: list[tuple[str, str, str | None]] = []
    failed: list[tuple[str, str]] = []
    for trigger in chosen:
        assert trigger.trigger_id is not None
        run_id = natural_id("drun", {"trigger": trigger.trigger_id})
        try:
            out = run(trigger, run_id)
        except Exception as exc:  # a Desk run that fails must never stop the league's tick
            failed.append((trigger.trigger_id, f"{type(exc).__name__}: {exc}"[:200]))
            continue
        outcomes.append((trigger.trigger_id, str(out.state), out.forecast_id))
    return Passed(tuple(t.trigger_id or "" for t in chosen), tuple(outcomes), tuple(failed))
