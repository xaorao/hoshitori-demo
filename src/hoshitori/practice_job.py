"""One pass of the practice portfolio: read the bars the plan needs through the gateway, run the plan, and give back the records to append.

``practice`` decides; this reads. The bars are the closed one-minute candles of each traded token's primary oracle venue, asked once for
each instrument in windows of consecutive minutes (``settle_job.read_bars``, the same reader as the settlement pass). A bar counts only if
it had closed by the venue's own clock; a venue that gave no time yields no bars, and the plan then waits: nothing is filled in.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hoshitori import clusters, oracle, practice, settle_job
from hoshitori.canonical import JsonValue
from hoshitori.settle_job import SettleServices
from hoshitori.settlement import Bar
from hoshitori.timeline import MINUTE_MS, from_ms


@dataclass(frozen=True)
class Passed:
    arm: str
    records: tuple[dict[str, JsonValue], ...]  # new records, in order, for the ledger
    plan: practice.Plan
    unread: tuple[str, ...]  # tokens with no oracle pair or no bar at all, so nothing could be decided for them


def last_closed_minute(now_ms: int) -> int:
    """The open of the latest one-minute bar that has certainly closed."""
    return now_ms // MINUTE_MS * MINUTE_MS - MINUTE_MS


def practice_pass(
    sv: SettleServices, cfgs: Sequence[practice.Config], records: Sequence[Mapping[str, Any]], now_ms: int, *, wait_s: float = 30.0
) -> tuple[Passed, ...]:
    """Read what every arm's plan needs once, then plan each arm apart (their books never touch)."""
    wanted: dict[tuple[str, str], list[int]] = {}
    symbol_of: dict[tuple[str, str], str] = {}
    unread: list[str] = []
    for demand in (d for cfg in cfgs for d in practice.demands(records, cfg)):
        pair = oracle.pick_for_asset(sv.env.registry, sv.protocol, demand.asset_id, from_ms(now_ms), trading=False)
        if pair is None:
            unread.append(demand.symbol)
            continue
        key = (pair.primary.venue, pair.primary.raw_id)
        symbol_of[key] = demand.symbol
        stop = min(demand.end_ms, last_closed_minute(now_ms) + MINUTE_MS)
        wanted.setdefault(key, []).extend(range(demand.start_ms, stop, MINUTE_MS))
    read = settle_job.read_bars(sv.env, wanted, wait_s, None, "practice")
    bars: dict[str, dict[int, Bar]] = {}
    for (venue, raw, minute), bar in read.bars.items():
        if bar is not None:
            bars.setdefault(symbol_of[(venue, raw)], {})[minute] = bar
    unread += [symbol for symbol in dict.fromkeys(symbol_of.values()) if symbol not in bars]
    nothing = tuple(dict.fromkeys(unread))
    traded = list(dict.fromkeys(symbol_of.values()))
    day = now_ms // clusters.DAY_MS
    cache_key = (day, tuple(sorted((venue, raw, symbol) for (venue, raw), symbol in symbol_of.items())))
    if cache_key not in sv.env.day_clusters:  # the daily closes change once a day: read them once a day for these tokens, not every pass
        daily = settle_job.read_daily_closes(sv.env, symbol_of, now_ms, clusters.WINDOW_DAYS + 1, wait_s, "practice")
        sv.env.day_clusters.clear()  # yesterday's are of no use
        sv.env.day_clusters[cache_key] = clusters.clusters(
            daily, traded
        )  # the tokens that move together: the cluster cap holds their gross exposure
    together = sv.env.day_clusters[cache_key]
    plans = [(cfg.arm, practice.plan(records, cfg, bars, now_ms, sv.source_urn, together)) for cfg in cfgs]
    return tuple(Passed(arm, got.records, got, nothing) for arm, got in plans)
