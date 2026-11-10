"""The venue survey: from the host that would run the league, which pairs of oracle venues can settle every token of the watchlist?

    python -m hoshitori.venue_survey --watchlist BTC,ETH,SOL [--venues binance-spot,okx,kraken,coinbase,hyperliquid]
                                     [--minutes 60] [--wait 30] [--label TEXT] [--base-url VENUE=URL ...] [--allow-loopback]

A host, a region or an oracle pair is chosen only after this has been run from that host (CF-60: a venue can be blocked on one network and
open on another; the venue probe from the target host decides the region). Public reads only, no key. For
each venue it reads the venue's spot instrument list through the gateway into a registry (``registry_job.refresh``); for each ordered
pair of venues and each token it asks the oracle rule of the standard protocol (spot, quoted in USDT: ``protocol.standard``,
``oracle.pick``) which instrument each venue would settle against, so no instrument id is ever made up from a symbol; then it measures,
once per venue, the one-minute bar coverage of exactly those instruments over the last ``minutes`` closed minutes (``venue_probe``). A
pair can settle when every token has its two instruments and both can settle. The protocol the rule reads is built in memory for each
pair with no pins (the rule reads only the venues, the kind and the quote class); it is never written or shown.

It prints one JSON line and exits 0 when at least one pair can settle every token, 1 when none can, 2 for a request that cannot be made.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from hoshitori import oracle, protocol, registry_job
from hoshitori.breaker import BreakerConfig
from hoshitori.capture import CaptureStore
from hoshitori.clock import Clock, SystemClock
from hoshitori.collect import Environment
from hoshitori.gateway import DispatchJournal, Gateway
from hoshitori.history import Source
from hoshitori.http_transport import TransportConfigError
from hoshitori.lineage import EvidenceStore
from hoshitori.registry import MappingStatus, Registry
from hoshitori.retry import RetryPolicy
from hoshitori.runner import Runner, ThreadExecutor, Transport
from hoshitori.venue_base import VenueConfig
from hoshitori.venue_probe import MAX_MINUTES, VenueFinding, probe
from hoshitori.venue_probe_job import as_json
from hoshitori.venues import attach

SPOT_VENUES: Final = ("binance-spot", "coinbase", "hyperliquid", "kraken", "okx")  # the venues that list spot instruments
DEFAULT_MINUTES: Final = 60
DEFAULT_WAIT_S: Final = 30.0
WORKERS: Final = 4
STOP_WAIT_S: Final = 5
NO_PIN: Final = "0" * 64  # the oracle rule reads no pin of the protocol it is given


class SurveyError(ValueError):
    """A survey that cannot be asked for."""


@dataclass(frozen=True)
class ListRead:
    venue: str
    read: bool
    detail: str  # "accepted", "rejected: REASON", or "REASON: what the gateway said"


@dataclass(frozen=True)
class TokenPick:
    symbol: str
    primary: str | None  # the instrument id on each venue, as the venue spells it
    second: str | None
    can_settle: bool
    why: str | None  # why not, when it cannot


@dataclass(frozen=True)
class PairResult:
    primary: str
    second: str
    tokens: tuple[TokenPick, ...]

    @property
    def can_settle(self) -> bool:
        return all(token.can_settle for token in self.tokens)


@dataclass(frozen=True)
class Survey:
    lists: tuple[ListRead, ...]
    probes: tuple[VenueFinding, ...]
    pairs: tuple[PairResult, ...]

    @property
    def usable(self) -> tuple[str, ...]:
        return tuple(f"{pair.primary},{pair.second}" for pair in self.pairs if pair.can_settle)


def _check(venues: Sequence[str], watchlist: Sequence[str], minutes: int, wait_s: float, base_urls: Mapping[str, str]) -> None:
    if not watchlist or len(set(watchlist)) != len(watchlist) or any(not s or s != s.upper() for s in watchlist):
        raise SurveyError("the watchlist names at least one symbol, each once, in capitals")
    if len(venues) < 2 or len(set(venues)) != len(venues):
        raise SurveyError("a pair needs two venues, each named once")
    for venue in venues:
        if venue not in SPOT_VENUES:
            raise SurveyError(f"{venue} is not a spot venue the survey reads (they are {list(SPOT_VENUES)})")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= MAX_MINUTES:
        raise SurveyError(f"a probe looks at from 1 to {MAX_MINUTES} minutes")
    if isinstance(wait_s, bool) or not isinstance(wait_s, int | float) or wait_s <= 0:
        raise SurveyError("the wait is a positive number of seconds")
    for venue in base_urls:
        if venue not in venues:
            raise SurveyError(f"a base URL is given for {venue}, which is not surveyed")


def _rules(watchlist: Sequence[str], primary: str, second: str) -> protocol.Protocol:
    return protocol.standard(
        name="venue-survey",
        start_ms=0,
        every_ms=3_600_000,
        root_version=1,
        root_sha256=NO_PIN,
        mappings={"L0": NO_PIN},
        watchlist=tuple(watchlist),
        primary_venue=primary,
        second_venue=second,
    )


def _lists(refreshed: registry_job.Refresh, venues: Sequence[str]) -> tuple[ListRead, ...]:
    said: dict[str, ListRead] = {}
    for record in refreshed.records:
        header = next(event for event in record["events"] if event["t"] == "snapshot")
        venue, outcome, reason = str(header["venue"]), str(header["outcome"]), header["reason"]
        said[venue] = ListRead(venue, outcome == "accepted", "accepted" if outcome == "accepted" else f"{outcome}: {reason}")
    for failure in refreshed.failures:
        said[failure.venue] = ListRead(failure.venue, False, f"{failure.reason}: {failure.detail}")
    return tuple(said[venue] for venue in venues if venue in said)  # the caller passes the venues in name order


def _coverage_problem(finding: VenueFinding, venue: str, instrument: str) -> str | None:
    """Why the instrument cannot settle on this venue, from the probe's finding, or None when it can."""
    if not finding.reachable:
        return f"{venue} does not answer: {finding.problem}"
    one = next(f for f in finding.instruments if f.instrument == instrument)
    if one.coverage is None:
        return f"{venue} {instrument}: {one.problem}"
    if not one.coverage.can_settle:
        return f"{venue} {instrument}: {one.coverage.present} of {one.coverage.closed_slots} closed minutes have a bar"
    return None


def survey(env: Environment, venues: Sequence[str], watchlist: Sequence[str], *, minutes: int, wait_s: float) -> Survey:
    """The survey through ``env``, whose adapters are the venues named, keyed by name, and whose registry is empty."""
    ordered = sorted(venues)
    specs = [registry_job.ListSpec(v, "spot", "spot" if "spot" in env.adapters[v].instrument_kinds else None) for v in ordered]
    lists = _lists(registry_job.refresh(env, specs, caller="venue-survey", wait_s=wait_s), ordered)
    read = {item.venue for item in lists if item.read}
    at = env.clock.now()
    chosen: dict[tuple[str, str, str], tuple[str, str] | str] = {}
    wanted: dict[str, set[str]] = {v: set() for v in ordered}
    for primary, second in itertools.permutations(ordered, 2):
        rules = _rules(watchlist, primary, second)
        for symbol in sorted(watchlist):
            missing = [v for v in (primary, second) if v not in read]
            if missing:
                chosen[primary, second, symbol] = f"{missing[0]}'s instrument list was not read"
                continue
            resolved = env.registry.resolve_symbol(symbol, as_of=at)
            if resolved.status is not MappingStatus.RESOLVED:
                chosen[primary, second, symbol] = f"the registry does not resolve {symbol} to one token ({resolved.status.value})"
                continue
            pick = oracle.pick(env.registry, rules, resolved.token_id, at)
            if pick is None:
                chosen[primary, second, symbol] = "the oracle rule finds no one trading spot instrument quoted in USDT on each venue"
                continue
            chosen[primary, second, symbol] = (pick.primary.raw_id, pick.second.raw_id)
            wanted[primary].add(pick.primary.raw_id)
            wanted[second].add(pick.second.raw_id)
    probes = {
        venue: probe(Source(env.runner, env.captures, env.adapters[venue], env.clock), sorted(ids), minutes=minutes, wait_s=wait_s)
        for venue, ids in wanted.items()
        if ids
    }
    pairs: list[PairResult] = []
    for primary, second in itertools.permutations(ordered, 2):
        tokens: list[TokenPick] = []
        for symbol in sorted(watchlist):
            choice = chosen[primary, second, symbol]
            if isinstance(choice, str):
                tokens.append(TokenPick(symbol, None, None, False, choice))
                continue
            why = _coverage_problem(probes[primary], primary, choice[0]) or _coverage_problem(probes[second], second, choice[1])
            tokens.append(TokenPick(symbol, choice[0], choice[1], why is None, why))
        pairs.append(PairResult(primary, second, tuple(tokens)))
    return Survey(lists, tuple(probes[v] for v in ordered if v in probes), tuple(pairs))


def run(
    venues: Sequence[str],
    watchlist: Sequence[str],
    *,
    minutes: int,
    wait_s: float,
    base_urls: Mapping[str, str],
    allow_loopback: bool,
    clock: Clock,
) -> Survey:
    """Wire the venues to one gateway on this host, survey them, and stop everything."""
    _check(venues, watchlist, minutes, wait_s, base_urls)
    with tempfile.TemporaryDirectory(prefix="hoshitori-venue-survey-") as tmp:
        work = Path(tmp)
        store = CaptureStore(work / "captures")
        gateway = Gateway(store, DispatchJournal(work / "dispatches.jsonl"), retry_policy=RetryPolicy(), rng=random.Random())  # noqa: S311
        transports: dict[str, Transport] = {}
        adapters = {}
        try:
            for venue in venues:
                attached = attach(
                    gateway,
                    venue,
                    clock,
                    base_url=base_urls.get(venue),
                    config=VenueConfig(),
                    breaker=BreakerConfig(),
                    allow_insecure_loopback=allow_loopback,
                )
                transports[attached.adapter.provider] = attached.transport
                adapters[venue] = attached.adapter
        except TransportConfigError as exc:
            store.close()
            raise SurveyError(str(exc)) from exc
        pool = ThreadExecutor(WORKERS)
        runner = Runner(gateway, transports, clock, pool)
        loop = threading.Thread(target=runner.run_forever, name="venue-survey-runner", daemon=True)
        loop.start()
        try:
            env = Environment(runner, store, adapters, Registry(), EvidenceStore(), clock)
            return survey(env, venues, watchlist, minutes=minutes, wait_s=wait_s)
        finally:
            runner.stop()
            loop.join(timeout=STOP_WAIT_S)
            pool.shutdown()
            store.close()


def as_document(found: Survey, label: str | None) -> dict[str, Any]:
    return {
        "label": label,
        "lists": [{"venue": item.venue, "read": item.read, "detail": item.detail} for item in found.lists],
        "venues": [as_json(finding) for finding in found.probes],
        "pairs": [
            {
                "primary": pair.primary,
                "second": pair.second,
                "can_settle": pair.can_settle,
                "tokens": [
                    {"symbol": t.symbol, "primary": t.primary, "second": t.second, "can_settle": t.can_settle, "why": t.why}
                    for t in pair.tokens
                ],
            }
            for pair in found.pairs
        ],
        "usable": list(found.usable),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="venue_survey", description="Which pairs of oracle venues can this host settle against?")
    parser.add_argument("--watchlist", required=True, help="comma-separated symbols, as the protocol's watchlist")
    parser.add_argument("--venues", default=",".join(SPOT_VENUES), help="comma-separated spot venues (default: all of them)")
    parser.add_argument("--minutes", type=int, default=DEFAULT_MINUTES)
    parser.add_argument("--wait", type=float, default=DEFAULT_WAIT_S)
    parser.add_argument("--label", default=None, help="what this host is (provider, region, size), kept in the output")
    parser.add_argument("--base-url", action="append", default=[], metavar="VENUE=URL", help="another base URL for a venue (tests)")
    parser.add_argument("--allow-loopback", action="store_true", help="allow http loopback base URLs (tests only)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        urls: dict[str, str] = {}
        for item in args.base_url:
            venue, sep, url = item.partition("=")
            if not sep or not venue or not url:
                raise SurveyError("a base URL is VENUE=URL")
            urls[venue] = url
        found = run(
            [v for v in args.venues.split(",") if v],
            [s for s in args.watchlist.split(",") if s],
            minutes=args.minutes,
            wait_s=args.wait,
            base_urls=urls,
            allow_loopback=args.allow_loopback,
            clock=SystemClock(),
        )
    except SurveyError as exc:
        sys.stderr.write(f"venue_survey: {exc}\n")
        return 2
    sys.stdout.write(json.dumps(as_document(found, args.label), sort_keys=True) + "\n")
    sys.stdout.flush()
    return 0 if found.usable else 1


if __name__ == "__main__":
    raise SystemExit(main())
