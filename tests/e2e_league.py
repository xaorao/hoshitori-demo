"""A whole league on loopback: real gateway, runner and HTTP transports; two fake exchanges; three fake time-stamp authorities; a real ledger;
the real registry, cycle, commitment, scheduler, settlement and offline verifiers. Only the outside world is a fake, and only through its
sockets: nothing inside the product is replaced.

The clock is the real one plus an offset that only grows, so the test can let an hour pass between two ticks without waiting for it.

For the optional funding source the fake also speaks the two Binance USD-M routes it needs: the exchange list (the registry's perpetuals)
and ``premiumIndex`` (the funding reading). Their bodies have the recorded shapes, kept in the test fixture ``e2e_binance_usdm.json``.
"""

from __future__ import annotations

import functools
import json
import random
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final
from urllib.parse import parse_qs, urlsplit

from hoshitori import attest, canonical, protocol, rules_arm, ryo_collect, ryo_gateway, trusted_root, tsa_gateway, venues
from hoshitori import league as league_module
from hoshitori.breaker import BreakerConfig
from hoshitori.capture import CaptureStore
from hoshitori.collect import Environment
from hoshitori.gateway import DispatchJournal, Gateway
from hoshitori.league import Services
from hoshitori.ledger import Ledger
from hoshitori.lineage import EvidenceStore
from hoshitori.registry import Registry
from hoshitori.registry_job import ListSpec
from hoshitori.retry import RetryPolicy
from hoshitori.runner import Runner, ThreadExecutor
from hoshitori.scheduler import Config
from hoshitori.timeline import to_ms
from hoshitori.timeutil import format_rfc3339_ms
from hoshitori.trusted_root import parse_root
from hoshitori.tsa import PinnedRoot, TokenPolicy
from tests import root_support as R
from tests import ryo_data, tsa_pki
from tests.fake_tsa import FakeTsa
from tests.profiles_binance import spot_symbol
from tests.profiles_okx import spot_row
from tests.protocol_checks import make
from tests.rules_arm_checks import graded
from tests.ryo_fake_server import KEY as RYO_KEY
from tests.ryo_fake_server import Behaviour, FakeRyo, Seen

MINUTE = 60_000
HOUR = 3_600_000
SYMBOLS = ("BTC", "ETH", "SOL")
USDM = Path(__file__).with_name("e2e_binance_usdm.json")  # test fixture: recorded Binance USD-M bodies, described in its first line
FUNDING_EVERY_MS = 8 * HOUR  # the fake's next settlement is the next eight-hour mark, as the recorded nextFundingTime is
# What the fake states for the perpetuals of a token (test values, not readings): the last settled funding rate, and the premium of the
# mark price over the index price, which is the token's price of the minute (``price``).
FUNDING = {"BTC": ("0.00010000", Decimal("1.25")), "ETH": ("-0.00002500", Decimal("-0.75"))}


@functools.cache
def usdm() -> dict[str, Any]:
    """The recorded parts of the Binance USD-M answers the fake serves: the exchange list, a premiumIndex answer and its refusal."""
    loaded: dict[str, Any] = json.loads(USDM.read_text(encoding="utf-8"))
    return loaded


class OffsetClock:
    """The real time plus an offset that can only be made larger."""

    def __init__(self) -> None:
        self._offset = timedelta(0)
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            moment = datetime.now(UTC) + self._offset
        return moment.replace(microsecond=moment.microsecond // 1000 * 1000)

    def jump(self, seconds: float) -> None:
        with self._lock:
            self._offset += timedelta(seconds=seconds)


def price(base: str, minute_ms: int, anchor: int, unit: int = MINUTE) -> Decimal:
    """BTC rises half a unit a minute, ETH falls half a unit a minute, SOL stays: the same on both exchanges. A daily bar counts its slope per
    day (``unit``), so that a month back is still a price."""
    slope = {"BTC": Decimal("0.5"), "ETH": Decimal("-0.5"), "SOL": Decimal(0)}[base]
    return Decimal(10_000) + slope * Decimal((minute_ms - anchor) // unit)


class FakeExchange(FakeRyo):
    """One loopback server that speaks the public routes of Binance spot and of OKX, with the bars of ``price``, and the two Binance USD-M
    routes of the funding source: the exchange list and ``premiumIndex``."""

    def __init__(self, clock: OffsetClock) -> None:
        super().__init__()
        self.clock = clock
        self.anchor = to_ms(clock.now()) // MINUTE * MINUTE
        self.requests: list[tuple[str, str]] = []
        self.premium: dict[str, dict[str, Any]] = {}  # the last premiumIndex body served, by symbol
        self.delisted: set[str] = set()  # bases that Binance spot no longer lists (its candles are still served)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def default(self, seen: Seen) -> Behaviour:
        parts = urlsplit(seen.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.requests.append((parts.path, parts.query))
        now_ms = to_ms(self.clock.now().replace(microsecond=0))
        if parts.path == "/api/v3/time":
            return self.json({"serverTime": now_ms})
        if parts.path == "/api/v5/public/time":
            return self.json({"code": "0", "msg": "", "data": [{"ts": str(now_ms)}]})
        if parts.path == "/api/v3/exchangeInfo":
            return self.json({"symbols": [spot_symbol(f"{n}USDT", n, "USDT") for n in SYMBOLS if n not in self.delisted]})
        if parts.path == "/api/v5/public/instruments":
            return self.json({"code": "0", "msg": "", "data": [spot_row(f"{n}-USDT", n, "USDT") for n in SYMBOLS]})
        if parts.path == "/api/v3/klines":
            step = {"1m": MINUTE, "1h": HOUR, "1d": 24 * HOUR}[query["interval"]]
            base = query["symbol"].removesuffix("USDT")
            first = -(-int(query["startTime"]) // step) * step
            rows = []
            for opened in range(first, int(query["endTime"]) + 1, step):
                p = price(base, opened, self.anchor, step if step == 24 * HOUR else MINUTE)
                rows.append([opened, str(p), str(p), str(p), str(p), "12.5", opened + step - 1, "987.6", 120, "6.1", "482.0", "0"])
            return self.json(rows)
        if parts.path == "/api/v5/market/history-candles":
            step = {"1m": MINUTE, "1H": HOUR}[query["bar"]]
            base = query["instId"].split("-")[0]
            low, high = int(query["before"]), int(query["after"])
            first = (low // step + 1) * step
            rows = []
            for opened in range(first, high, step):
                p = price(base, opened, self.anchor)
                rows.append([str(opened), str(p), str(p), str(p), str(p), "2.5", "250", "260", "1"])
            return self.json({"code": "0", "msg": "", "data": list(reversed(rows))})
        if parts.path == "/fapi/v1/exchangeInfo":  # the recorded list, with the fake's clock
            return self.json({**usdm()["exchange_info"], "serverTime": now_ms})
        if parts.path == "/fapi/v1/premiumIndex" and "symbol" in query:  # without a symbol the venue lists every instrument: not recorded
            return self.premium_index(query["symbol"], now_ms)
        return Behaviour(404, b'{"message":"no such route"}', {"content-type": "application/json"})

    def premium_index(self, symbol: str, now_ms: int) -> Behaviour:
        """``premiumIndex`` for one perpetual of the fake's USD-M list: the recorded members in the recorded order, with the fake's values
        (``interestRate`` stays as recorded); any other symbol gets the recorded refusal."""
        row = next((r for r in usdm()["exchange_info"]["symbols"] if r["symbol"] == symbol and r["contractType"] == "PERPETUAL"), None)
        if row is None:
            refused = usdm()["unknown_symbol"]
            words = json.dumps(refused["body"], separators=(",", ":")).encode()
            return Behaviour(refused["status"], words, {"content-type": "application/json"})
        rate, premium = FUNDING[row["baseAsset"]]
        index = price(row["baseAsset"], now_ms, self.anchor)
        body = {
            **usdm()["premium_index"],
            "symbol": symbol,
            "markPrice": f"{index + premium:.8f}",
            "indexPrice": f"{index:.8f}",
            "estimatedSettlePrice": f"{index:.8f}",
            "lastFundingRate": rate,
            "nextFundingTime": (now_ms // FUNDING_EVERY_MS + 1) * FUNDING_EVERY_MS,
            "time": now_ms,
        }
        self.premium[symbol] = body
        return self.json(body)

    @staticmethod
    def json(value: Any) -> Behaviour:
        return Behaviour(200, json.dumps(value, separators=(",", ":")).encode(), {"content-type": "application/json"})


@dataclass
class League:
    clock: OffsetClock
    exchange: FakeExchange
    services: Services
    config: Config
    ledger: Ledger
    captures: CaptureStore
    root_document: dict[str, object]
    root: trusted_root.TrustedRoot
    store_dir: Path
    sites: list[Any]


@contextmanager
def league(
    directory: Path,
    *,
    grace_ms: int = 10 * MINUTE,
    barrier: Any = None,
    watchlist: tuple[str, ...] = ("BTC", "ETH"),
    perpetuals: bool = False,
    optional_sources: tuple[str, ...] = (),
    at_minute: int | None = None,
    ryo: bool = False,
) -> Iterator[League]:
    """A league on loopback. ``perpetuals`` also attaches the USD-M venue and the funding source to the fake exchange and reads the USD-M
    perpetual list before each cycle, as a deployment that names the funding source must; ``optional_sources`` go into the protocol.

    What the rules arm says depends on where in the hour the league starts (its first slot is the minute before the start, so a start
    at hh:01 puts the slot on the hour and moves the 1-hour BTC call to the top state, where its score mirrors ETH's).
    ``at_minute`` moves the clock forward, before anything reads it, to the next time in that minute of an hour, for a check that needs
    one answer whenever it runs. ``ryo`` reads RYO from a loopback server that answers about the token it is asked for, as of the league's
    clock, with a scan that ranks the watchlist (its own tests are in ``ryo_fake_server``); RYO is then an optional source of the protocol."""
    clock = OffsetClock()
    if at_minute is not None:
        if not 0 <= at_minute < 60:
            raise ValueError(f"a minute of the hour is 0 to 59, not {at_minute}")
        clock.jump((at_minute * 60 - to_ms(clock.now()) // 1000 % 3600) % 3600)
    exchange = FakeExchange(clock)
    exchange.start()
    captures = CaptureStore(directory / "captures")
    gateway = Gateway(
        captures,
        DispatchJournal(directory / "dispatch.jsonl"),
        retry_policy=RetryPolicy(base_s=0.05, cap_s=0.2, rate_limit_default_wait_s=0.1),
        rng=random.Random(1),
    )
    transports = {}
    adapters = {}
    for venue in ("binance-spot", "okx", *(("binance-usdm", "binance-funding") if perpetuals else ())):
        got = venues.attach(gateway, venue, clock, base_url=exchange.url, allow_insecure_loopback=True)
        transports[got.adapter.provider] = got.transport
        adapters[venue] = got.adapter
    sites = []
    authorities = []
    for name in ("alpha", "beta", "gamma"):
        chain = tsa_pki.Chain.build(name)
        server = FakeTsa(chain, clock.now)
        server.start()
        authority = tsa_gateway.Authority(name, server.url)
        got = tsa_gateway.attach_tsa(
            gateway, authority, clock, breaker=BreakerConfig(consecutive_failures=50), allow_insecure_loopback=True
        )
        transports[got.adapter.provider] = got.transport
        sites.append((name, chain, server))
        authorities.append(authority)
    fake_ryo = None
    if ryo:
        quote = lambda base: price(base, to_ms(clock.now()) // MINUTE * MINUTE, exchange.anchor)  # noqa: E731
        fake_ryo = FakeRyo(script=_ryo_script(clock, watchlist, quote)).start()
        attached = ryo_gateway.attach_ryo(
            gateway, fake_ryo.url, lambda: RYO_KEY, clock, rate_limit=600.0, allow_insecure_loopback=True
        )  # a loopback server has no fan-out limit to keep to
        transports[attached.adapter.provider] = attached.transport
        optional_sources = (*optional_sources, "ryo")
    pool = ThreadExecutor(8)
    runner = Runner(gateway, transports, clock, pool)
    loop = threading.Thread(target=runner.run_forever, daemon=True)
    loop.start()
    log_signer = R.log_signer(7)
    document = R.make_document(
        1,
        root_keys=[R.new_signer(1)],
        eras=[{"vkey": log_signer.verifier.vkey(), "from_size": 0, "through_size": None}],
        pinned=[(chain.root, f"operator-{name}") for name, chain, _ in sites],
    )
    root = parse_root(canonical.dumps(document))  # type: ignore[arg-type]
    ledger = Ledger.open(directory / "ledger", signer=log_signer, origin=R.LOG_ORIGIN, clock=clock, segment_records=500)
    policy = TokenPolicy(tuple(PinnedRoot(chain.root.fingerprint, f"operator-{name}") for name, chain, _ in sites))
    mapping = graded(rules_arm)

    def attestor(digest: bytes, committed_at_ms: int) -> attest.Attestation:
        return attest.attest(
            runner, captures, clock, authorities, digest, committed_at_ms=committed_at_ms, policy=policy, rules=root.time_stamp
        )

    start = (to_ms(clock.now()) // MINUTE - 1) * MINUTE  # a minute ago: the first slot has come and is inside the grace
    rules = make(
        protocol,
        name="e2e",
        primary_venue="binance-spot",
        second_venue="okx",
        schedule_start_ms=start,
        watchlist=watchlist,
        arms=(("L0", mapping.sha256),),
        clock_offset_limit_ms=5_000,
        wait_s=20,
        root_version=1,
        root_sha256=root.sha256,
        snapshot_freshness_ms=600_000,
        barrier_arms=() if barrier is None else (("L0", barrier.sha256),),
        optional_sources=optional_sources,
    )
    env = Environment(runner, captures, adapters, Registry(), EvidenceStore(), clock)
    services = Services(
        rules,
        {"L0": mapping},
        env,
        ledger,
        attestor,
        root.time_stamp,
        "urn:hoshitori:league:e2e",
        barrier_mappings={} if barrier is None else {"L0": barrier},
        ryo=None if fake_ryo is None else league_module.RyoWiring(ryo_collect.RyoEnvironment(runner, captures, env.store, clock)),
    )
    store_dir = directory / "commitments"
    lists = (ListSpec("binance-spot", "spot"), ListSpec("okx", "spot", "spot"))
    config = Config(
        start_grace_ms=grace_ms,
        finish_within_ms=20 * MINUTE,
        lists=(*lists, ListSpec("binance-usdm", "perpetual", "perpetual")) if perpetuals else lists,
        store=store_dir,
        wait_s=20,
    )
    try:
        yield League(clock, exchange, services, config, ledger, captures, document, root, store_dir, sites)
    finally:
        runner.stop()
        loop.join(timeout=3)
        pool.shutdown()
        for _, _, server in sites:
            server.stop()
        exchange.stop()
        if fake_ryo is not None:
            fake_ryo.stop()
        ledger.close()
        captures.close()


RYO_PLAN_OFFSET: Final = Decimal(100)  # BTC rises half a unit a minute and ETH falls: each reaches one level in about 200 minutes


def _ryo_script(clock: OffsetClock, watchlist: tuple[str, ...], quote: Callable[[str], Decimal]) -> Callable[[Seen], Behaviour | None]:
    """RYO's tools as the loopback league reads them: the recorded shapes of ``ryo_data``, about the token asked for, as of now; the
    deep analysis's preview plan is placed around the exchange's price (``RYO_PLAN_OFFSET`` below and above), so that it can settle."""
    prefix = "/api/mcp/tools/"

    def answer(seen: Seen) -> Behaviour | None:
        if seen.method != "POST" or not seen.path.startswith(prefix) or not seen.path.endswith("/call"):
            return None  # health, whoami and the catalogue are the fake server's own
        name = seen.path[len(prefix) : -len("/call")]
        args = json.loads(seen.body or b"{}")
        if name == "scan_market":
            data = ryo_data.scan_market_data()
            base = data["candidates"][0]
            data["candidates"] = [{**base, "rank": n + 1, "symbol": symbol} for n, symbol in enumerate(watchlist)]
            data["candidate_count"] = len(watchlist)
        else:
            data = ryo_data.DATA[name]()
            if "symbol" in args:
                data["asset"]["symbol"] = args["symbol"]
            if name == "deep_analysis":
                entry = quote(str(args["symbol"]))
                data["trade_plan"] = {
                    **data["trade_plan"],
                    "entry": float(entry),
                    "stop": float(entry - RYO_PLAN_OFFSET),
                    "targets": [float(entry + RYO_PLAN_OFFSET), float(entry + 2 * RYO_PLAN_OFFSET)],
                }
        body = ryo_data.rest_body(name, ryo_data.result(name, data=data, as_of=format_rfc3339_ms(clock.now()), request=args))
        return Behaviour(200, body, {"content-type": "application/json"})

    return answer


def scratch() -> Callable[[], Any]:
    return tempfile.TemporaryDirectory


@dataclass
class Outside:
    """The fake outside world alone: the exchange and the authorities, with the trusted-root pieces that belong to them."""

    exchange: FakeExchange
    sites: list[tuple[str, tsa_pki.Chain, FakeTsa]]

    @property
    def authorities(self) -> list[dict[str, str]]:
        return [{"name": name, "url": server.url} for name, _, server in self.sites]

    def pinned(self) -> list[tuple[Any, str]]:
        return [(chain.root, f"operator-{name}") for name, chain, _ in self.sites]


@contextmanager
def outside(clock: Any) -> Iterator[Outside]:
    exchange = FakeExchange(clock)
    exchange.start()
    sites = []
    try:
        for name in ("alpha", "beta", "gamma"):
            chain = tsa_pki.Chain.build(name)
            server = FakeTsa(chain, clock.now)
            server.start()
            sites.append((name, chain, server))
        yield Outside(exchange, sites)
    finally:
        for _, _, server in sites:
            server.stop()
        exchange.stop()
