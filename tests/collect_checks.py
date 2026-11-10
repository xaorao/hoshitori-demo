"""Checks of the collector (``hoshitori.collect``), runnable against a mutant.

The gateway is replaced by a runner that answers from a script and writes real captures into a real capture store; the adapter is the real
Binance spot adapter and the registry is the real registry, so what is checked is what the collector asks, what it does with the answers
and what it returns. The gateway itself and the sockets are checked elsewhere (``test_venue_stack``).
"""

from __future__ import annotations

import json
import tempfile
import types
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

from hoshitori import evidence, snapshot
from hoshitori.capture import CaptureStore, RawCapture
from hoshitori.clock import ManualClock
from hoshitori.connector import ConnectorError, ErrorClass
from hoshitori.gateway import Call, CallResult, Outcome
from hoshitori.governor import Priority
from hoshitori.lineage import EvidenceStore
from hoshitori.registry import Registry, Want
from hoshitori.timeline import to_ms
from hoshitori.timeutil import parse_rfc3339
from hoshitori.venue_binance import BinanceSpot, BinanceUsdm
from hoshitori.venue_funding import BinanceFunding
from hoshitori.venue_kraken import Kraken
from tests.features_checks import text
from tests.registry_checks import FUTURE, PERP, inst, lst, t

NOW = datetime(2026, 10, 29, 12, 5, 10, tzinfo=UTC)
SERVER = b'{"serverTime":1793275503000}'  # 12:05:03


def klines(args: Any) -> bytes:
    """Binance klines for every slot of the requested window, all closed: a gentle zigzag around 1000, valid bars."""
    start, end, step = parse_rfc3339(str(args["start"])), parse_rfc3339(str(args["end"])), int(args["interval_s"])
    rows = []
    previous = 1000
    for n in range(int((end - start).total_seconds()) // step):
        opened = int(start.timestamp() * 1000) + n * step * 1000
        close = 1000 + (n * 7) % 23
        high, low = max(previous, close) + 1, min(previous, close) - 1
        rows.append(
            f'[{opened},"{previous}.00","{high}.00","{low}.00","{close}.00","12.5",{opened + step * 1000 - 1},"987.6",120,"6.1","482.0","0"]'
        )
        previous = close
    return ("[" + ",".join(rows) + "]").encode()


def one(mod: types.ModuleType, env: Any, candidates: Any, *, bars: int = 5, interval_s: int = 60, **options: Any) -> Any:
    """A collection of a single window, the shape most checks want."""
    return mod.collect(env, candidates, windows=(mod.Window(interval_s, bars),), **options)


SRC = "urn:hoshitori:collector:test"


@dataclass
class Reply:
    body: bytes = b""
    outcome: Outcome = Outcome.OK
    reason: str = ""
    error: ConnectorError | None = None
    hang: bool = False
    capture: bool | None = None  # write a capture even when the outcome is not OK (or none when it is)


class FakeRunner:
    """Answers each call from a script and keeps what it was asked."""

    def __init__(self, captures: CaptureStore, clock: ManualClock, script: Callable[[Call], Reply]) -> None:
        self.captures, self.clock, self.script = captures, clock, script
        self.calls: list[Call] = []
        self._n = 0

    def submit(self, call: Call) -> Future[CallResult]:
        self.calls.append(call)
        future: Future[CallResult] = Future()
        reply = self.script(call)
        if reply.hang:
            return future
        self._n += 1
        capture_id = None
        if reply.capture if reply.capture is not None else reply.outcome is Outcome.OK:
            capture_id = f"cap-{self._n:032x}"
            now = self.clock.now()
            request = {"operation": call.operation, "arguments": dict(call.args), "attempt": 1}
            self.captures.append(RawCapture(capture_id, call.provider, request, 200, {}, reply.body, now, now))
        future.set_result(CallResult(self._n, reply.outcome, capture_id, 1, reply.error, reply.reason, 0.0, self.clock.now()))
        return future


class World:
    def __init__(self, root: Path, script: Callable[[Call], Reply] | None = None) -> None:
        self.clock = ManualClock(NOW)
        self.captures = CaptureStore(root / "captures")
        self.registry = Registry()
        self.store = EvidenceStore()
        self.adapter = BinanceSpot()
        names = ("BTC", "ETH", "SOL")
        self.registry.admit(
            lst("binance-spot", *[inst("binance-spot", f"{n}USDT", n) for n in names]), scope="spot", known_at=t(0), capture_id="cap-b"
        )
        self.registry.admit(
            lst("okx-spot", *[inst("okx-spot", f"{n}-USDT", n) for n in names]), scope="spot", known_at=t(1), capture_id="cap-o"
        )
        self.registry.admit(
            lst("binance-usdm", *[inst("binance-usdm", f"{n}USDT", n, kind=PERP) for n in names]),
            scope="perpetual",
            known_at=t(2),
            capture_id="cap-u",
        )
        self.assets = {n: self.asset(n) for n in names}
        self.perp = {n: self.asset(n, "binance-usdm", PERP) for n in names}

        def default(call: Call) -> Reply:
            return Reply(SERVER if call.operation == "time" else klines(call.args))

        self.runner = FakeRunner(self.captures, self.clock, script or default)

    def asset(self, name: str, venue: str = "binance-spot", kind: Any = None) -> str:
        iid = (inst(venue, f"{name}USDT", name) if kind is None else inst(venue, f"{name}USDT", name, kind=kind)).instrument_id
        found = self.registry.join(iid, Want()).asset
        assert found is not None
        return found.asset_id

    def candidates(self, mod: types.ModuleType, *names: str) -> list[Any]:
        return [snapshot.Candidate(f"cd-{n.lower()}", self.assets[n]) for n in names]

    def env(self, mod: types.ModuleType, **over: Any) -> Any:
        values: dict[str, Any] = {
            "runner": self.runner,
            "captures": self.captures,
            "adapters": {"binance-spot": self.adapter},
            "registry": self.registry,
            "store": self.store,
            "clock": self.clock,
        }
        values.update(over)
        return mod.Environment(**values)

    def run(self, mod: types.ModuleType, names: tuple[str, ...] = ("BTC", "ETH"), **over: Any) -> Any:
        options: dict[str, Any] = {"source_urn": SRC, "wait_s": 0.05}
        envopts = {k: over.pop(k) for k in ("adapters",) if k in over}
        shape = {k: over.pop(k) for k in ("bars", "interval_s") if k in over}
        if "windows" not in over:
            options["windows"] = (mod.Window(shape.get("interval_s", 60), shape.get("bars", 5)),)
        options.update(over)
        return mod.collect(self.env(mod, **envopts), self.candidates(mod, *names), **options)


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str) -> None:
        try:
            call()
        except self.mod.CollectError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def failing(operation: str, reply: Reply) -> Callable[[Call], Reply]:
    return lambda call: reply if call.operation == operation else Reply(SERVER if call.operation == "time" else klines(call.args))


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("version and limit", (mod.ADAPTER_VERSION, mod.MAX_DETAIL), ("collect-1", 200))
    c.eq(
        "the optional source it reads",
        (mod.FUNDING_SOURCE, mod.FUNDING_PERPETUAL_VENUE, mod.FUNDING_ADAPTER_VERSION),
        ("binance-funding", "binance-usdm", "collect-funding-1"),
    )
    c.eq(
        "reasons",
        [r.value for r in mod.Reason],
        [
            "NO_ASSET",
            "NO_ADAPTER",
            "NO_SERVER_TIME",
            "BAD_REQUEST",
            "CALL_FAILED",
            "UNREADABLE",
            "TIMED_OUT",
            "NO_PERPETUAL",
            "PERPETUAL_LIST_UNUSABLE",
        ],
    )
    c.eq("the snapshot's closed list of reasons is the collector's", snapshot.UNAVAILABLE_REASONS, tuple(r.value for r in mod.Reason))
    for cls in (mod.Failure, mod.Window, mod.Offset, mod.Collection, mod.Environment, mod.Problem):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    c.eq("an offset is the venue's clock less ours", mod.Offset("v", 5_000, 3_500).offset_ms, 1_500)
    c.eq("a collection gives its ids as a mapping", mod.Collection((("a", ("x",)),), (), (), ()).ids(), {"a": ("x",)})
    return c.bad


def check_happy_path(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root)
    got = w.run(mod, earmark=3, caller="league")
    c.eq("no failures", got.failures, ())
    c.eq("both candidates have one item", [(cid, len(ids)) for cid, ids in got.by_candidate], [("cd-btc", 1), ("cd-eth", 1)])
    c.eq("one record per item, in candidate order", [r["id"] for r in got.records], [i for _, ids in got.by_candidate for i in ids])
    item = w.store.get(got.by_candidate[0][1][0])
    c.eq(
        "the item is the venue's series",
        (item.kind, item.source, item.origin, item.path, item.tool),
        (evidence.Kind.OBSERVATION, "binance-spot", evidence.Family(evidence.FamilyKind.EXCHANGE, "binance-spot"), "series", None),
    )
    c.eq("the item says it was read live", item.data_mode, "live")
    iid = inst("binance-spot", "BTCUSDT", "BTC").instrument_id
    c.eq("it is about the candidate's asset and instrument, resolved", item.asset_refs, ((w.assets["BTC"], iid, "RESOLVED"),))
    c.eq("with the adapter version", item.adapter_version, "collect-1")
    fields = dict(item.fields)
    c.eq(
        "five closed bars, none missing",
        (fields["count"], fields["missing"], fields["pending"], fields["excluded_unfinished"]),
        ("5", "0", "0", "0"),
    )
    c.eq("the event time is the venue's clock", item.event_time_ms, to_ms(datetime(2026, 10, 29, 12, 5, 3, tzinfo=UTC)))
    c.eq(
        "the request is the window asked",
        dict(item.request),
        {"end": "2026-10-29T12:05:00.000Z", "instrument": "BTCUSDT", "interval_s": 60, "start": "2026-10-29T12:00:00.000Z"},
    )
    c.eq("received now", item.received_at_ms, to_ms(NOW))
    c.eq("the record is the item's", got.records[0]["body"], item.to_record(SRC, item.known_at_ms)["body"])
    c.eq(
        "the store holds the digest of that record",
        w.store.digest(item.evidence_id),
        evidence.record_digest(item.to_record(SRC, item.known_at_ms)),
    )
    c.eq(
        "the venue's clock against ours", [(o.venue, o.offset_ms) for o in got.offsets], [("binance-spot", 1_793_275_503_000 - to_ms(NOW))]
    )
    # what was asked
    calls = w.runner.calls
    c.eq("one clock call, then one candle call per candidate", [(x.operation, dict(x.args)) for x in calls], [
        ("time", {}),
        ("candles", {"end": "2026-10-29T12:05:00.000Z", "instrument": "BTCUSDT", "interval_s": 60, "start": "2026-10-29T12:00:00.000Z"}),
        ("candles", {"end": "2026-10-29T12:05:00.000Z", "instrument": "ETHUSDT", "interval_s": 60, "start": "2026-10-29T12:00:00.000Z"}),
    ])  # fmt: skip
    c.eq(
        "every call is the venue's, highest priority, with the caller and the window",
        {(x.provider, x.priority, x.caller, x.window, x.namespace) for x in calls},
        {(w.adapter.provider, Priority.P0, "league", 3, "LEAGUE")},
    )
    c.eq("with a deadline of the wait", {x.deadline for x in calls}, {NOW + timedelta(seconds=0.05)})
    # the snapshot can be frozen from it
    snap = snapshot.freeze(
        w.store,
        cycle_id="cy-1",
        manifest_hash="9" * 64,
        snapshot_at_ms=to_ms(NOW) + 1_000,
        candidates=w.candidates(mod, "BTC", "ETH"),
        shared=[],
        by_candidate=got.ids(),
        required=["binance-spot"],
    )
    c.eq(
        "a snapshot freezes it, fresh and complete",
        [(s.missing, s.rejected, len(s.item_ids)) for s in snap.slots],
        [((), (), 1), ((), (), 1)],
    )
    return c.bad


def funding_body(args: Any, **over: Any) -> bytes:
    """Binance premiumIndex for the instrument asked, read at 12:05:03 by the venue's clock, with the next settlement three hours on."""
    body = {
        "symbol": args["instrument"],
        "markPrice": "1001.50",
        "indexPrice": "1000.25",
        "estimatedSettlePrice": "1001.00",
        "lastFundingRate": "0.00010000",
        "interestRate": "0.00010000",
        "nextFundingTime": 1793275503000 + 3 * 3600 * 1000,
        "time": 1793275503000,
        **over,
    }
    return json.dumps(body).encode()


def with_funding(reply: Callable[[Call], Reply] | None = None) -> Callable[[Call], Reply]:
    def script(call: Call) -> Reply:
        if call.operation == "funding":
            return Reply(funding_body(call.args))
        if reply is not None:
            return reply(call)
        return Reply(SERVER if call.operation == "time" else klines(call.args))

    return script


def funding_adapters() -> dict[str, Any]:
    return {"binance-spot": BinanceSpot(), "binance-funding": BinanceFunding()}


def check_funding(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    R = mod.Reason
    names = ("BTC", "ETH")
    w = World(root / "ok", with_funding())
    got = w.run(mod, names, adapters=funding_adapters(), optional_sources=("binance-funding",), earmark=3, caller="league")
    c.eq("no failures", got.failures, ())
    c.eq(
        "each candidate has its series and its funding", [(cid, len(ids)) for cid, ids in got.by_candidate], [("cd-btc", 2), ("cd-eth", 2)]
    )
    series, funding = (w.store.get(i) for i in got.by_candidate[0][1])
    c.eq("the series comes first and the funding after it", (series.path, funding.path), ("series", "funding"))
    c.eq(
        "the funding is the venue's own reading",
        (funding.kind, funding.source, funding.origin, funding.data_mode, funding.adapter_version),
        (
            evidence.Kind.OBSERVATION,
            "binance-funding",
            evidence.Family(evidence.FamilyKind.EXCHANGE, "binance-funding"),
            "live",
            "collect-funding-1",
        ),
    )
    c.eq(
        "its fields",
        dict(funding.fields),
        {
            "index_price": "1000.25",
            "last_funding_rate": "0.00010000",
            "mark_price": "1001.50",
            "next_funding_time": "2026-10-29T15:05:03.000Z",
            "server_time": "2026-10-29T12:05:03.000Z",
        },
    )
    c.eq(
        "it is about the candidate's asset, measured on its perpetual, resolved",
        funding.asset_refs,
        ((w.assets["BTC"], inst("binance-usdm", "BTCUSDT", "BTC", kind=PERP).instrument_id, "RESOLVED"),),
    )
    c.eq(
        "one record per item, series then funding, candidate by candidate",
        [r["id"] for r in got.records],
        [i for _, ids in got.by_candidate for i in ids],
    )
    c.eq(
        "the store holds the digest of the funding record",
        w.store.digest(funding.evidence_id),
        evidence.record_digest(funding.to_record(SRC, funding.known_at_ms)),
    )
    funding_calls = [x for x in w.runner.calls if x.operation == "funding"]
    c.eq(
        "one funding call per candidate, with the instrument",
        [dict(x.args) for x in funding_calls],
        [{"instrument": "BTCUSDT"}, {"instrument": "ETHUSDT"}],
    )
    c.eq(
        "through the gateway as the funding provider, highest priority, with the caller and the window",
        {(x.provider, x.priority, x.caller, x.window, x.namespace) for x in funding_calls},
        {("binance-funding", Priority.P0, "league", 3, "LEAGUE")},
    )
    c.eq("with a deadline of the wait", {x.deadline for x in funding_calls}, {NOW + timedelta(seconds=0.05)})
    snap = snapshot.freeze(
        w.store,
        cycle_id="cy-1",
        manifest_hash="9" * 64,
        snapshot_at_ms=to_ms(NOW) + 1_000,
        candidates=w.candidates(mod, *names),
        shared=[],
        by_candidate=got.ids(),
        required=["binance-spot"],
    )
    c.eq(
        "a snapshot freezes it, fresh, with the funding admitted",
        [(s.missing, s.rejected, len(s.item_ids)) for s in snap.slots],
        [((), (), 2), ((), (), 2)],
    )
    # not asked for: not read
    w = World(root / "off", with_funding())
    got = w.run(mod, names, adapters=funding_adapters())
    c.eq(
        "without the option nothing about funding is asked",
        ([x.operation for x in w.runner.calls if x.operation == "funding"], [len(i) for _, i in got.by_candidate]),
        ([], [1, 1]),
    )
    c.refuses(
        "a source it cannot read",
        lambda: w.run(mod, names, adapters=funding_adapters(), optional_sources=("binance-funding", "somewhere")),
        "an optional source this collector cannot read: 'somewhere'",
    )
    # the adapter is not attached
    w = World(root / "noadapter", with_funding())
    got = w.run(mod, names, optional_sources=("binance-funding",))
    c.eq(
        "no adapter for funding: a failure of each candidate's optional source, the series stay",
        ([(f.candidate_id, f.reason, f.detail) for f in got.failures], [len(i) for _, i in got.by_candidate]),
        ([("cd-btc", R.NO_ADAPTER, "no adapter for binance-funding"), ("cd-eth", R.NO_ADAPTER, "no adapter for binance-funding")], [1, 1]),
    )
    # a token with no perpetual, and one whose perpetual is another size
    w = World(root / "noperp", with_funding())
    w.registry.admit(
        lst("binance-spot", *[inst("binance-spot", f"{n}USDT", n) for n in ("BTC", "ETH", "SOL", "PEPE", "DOGE")]),
        scope="spot",
        known_at=t(3),
        capture_id="cap-b2",
    )
    w.registry.admit(
        lst(
            "binance-usdm",
            *[inst("binance-usdm", f"{n}USDT", n, kind=PERP) for n in ("BTC", "ETH", "SOL")],
            inst("binance-usdm", "DOGEUSDT", "DOGE", kind=PERP, mult="1000"),
        ),
        scope="perpetual",
        known_at=t(4),
        capture_id="cap-u2",
    )
    w.registry.admit(
        lst("okx-spot", *[inst("okx-spot", f"{n}-USDT", n) for n in ("BTC", "ETH", "SOL", "PEPE", "DOGE")]),
        scope="spot",
        known_at=t(5),
        capture_id="cap-o2",
    )
    w.assets["PEPE"], w.assets["DOGE"] = w.asset("PEPE"), w.asset("DOGE")
    got = w.run(mod, ("BTC", "PEPE", "DOGE"), adapters=funding_adapters(), optional_sources=("binance-funding",))
    by_reason = {f.candidate_id: (f.reason, f.detail) for f in got.failures}
    c.eq("a token the venue does not list as a perpetual", by_reason["cd-pepe"][0], R.NO_PERPETUAL)
    c.eq("and the registry's words", by_reason["cd-pepe"][1], "the registry lists no perpetual of the token on binance-usdm")
    c.eq("a perpetual of another size is not joined", by_reason["cd-doge"][0], R.NO_PERPETUAL)
    c.eq("and says which field", by_reason["cd-doge"][1].startswith("DOGEUSDT: "), True)
    c.eq(
        "the series are all kept and only the token with a perpetual has funding",
        [(cid, len(i)) for cid, i in got.by_candidate],
        [("cd-btc", 2), ("cd-doge", 1), ("cd-pepe", 1)],
    )
    c.eq(
        "no funding was asked for what was not joined",
        sorted(dict(x.args)["instrument"] for x in w.runner.calls if x.operation == "funding"),
        ["BTCUSDT"],
    )
    # an asset the registry does not resolve, and an instrument the venue's adapter refuses to ask for
    w = World(root / "stranger", with_funding())
    stranger = snapshot.Candidate("cd-zzz", "asnotanasset")
    got = mod.collect(
        w.env(mod, adapters=funding_adapters()),
        [*w.candidates(mod, "BTC"), stranger],
        source_urn=SRC,
        wait_s=0.05,
        windows=(mod.Window(60, 5),),
        optional_sources=("binance-funding",),
    )
    c.eq(
        "an unresolved asset is reported once, as the series failure, and does not stop the others",
        ([(f.candidate_id, f.reason) for f in got.failures], [(cid, len(i)) for cid, i in got.by_candidate]),
        ([("cd-zzz", R.NO_ASSET)], [("cd-btc", 2)]),
    )

    class Refusing(BinanceFunding):
        def normalise(self, operation: str, args: Any) -> dict[str, Any]:
            raise ConnectorError(ErrorClass.INVALID_ARGS, "not an instrument this venue lists")

    w = World(root / "refused", with_funding())
    got = w.run(mod, names, adapters={"binance-spot": BinanceSpot(), "binance-funding": Refusing()}, optional_sources=("binance-funding",))
    c.eq(
        "an instrument the adapter refuses is a failure of the optional source and no call is made for it",
        ([(f.candidate_id, f.reason, f.detail) for f in got.failures], [x.operation for x in w.runner.calls if x.operation == "funding"]),
        (
            [
                ("cd-btc", R.BAD_REQUEST, "not an instrument this venue lists"),
                ("cd-eth", R.BAD_REQUEST, "not an instrument this venue lists"),
            ],
            [],
        ),
    )
    # the call fails, or the answer cannot be read
    for label, reply, reason, words in (
        (
            "a failed call",
            Reply(outcome=Outcome.FAILED, reason="EXHAUSTED", error=ConnectorError(ErrorClass.TIMEOUT, "no answer")),
            R.CALL_FAILED,
            "FAILED: no answer",
        ),
        ("a body that is not funding", Reply(b"[]"), R.UNREADABLE, None),
        ("an answer that never comes", Reply(hang=True), R.TIMED_OUT, "no result before the wait ended"),
    ):
        base = with_funding()
        w = World(root / label.replace(" ", "_"), lambda call, base=base, reply=reply: reply if call.operation == "funding" else base(call))
        got = w.run(mod, ("BTC",), adapters=funding_adapters(), optional_sources=("binance-funding",))
        c.eq(
            f"{label}: a failure of the optional source and the series stays",
            ([f.reason for f in got.failures], [len(i) for _, i in got.by_candidate]),
            ([reason], [1]),
        )
        if words is not None:
            c.eq(f"{label}: with its words", got.failures[0].detail, words)
        c.eq(f"{label}: and the funding source named", got.failures[0].source, "binance-funding")
    w = World(root / "mismatch", with_funding(lambda call: Reply(SERVER if call.operation == "time" else klines(call.args))))
    w.runner.script = lambda call: (
        Reply(funding_body({"instrument": "ETHUSDT"}))
        if call.operation == "funding"
        else Reply(SERVER if call.operation == "time" else klines(call.args))
    )
    got = w.run(mod, ("BTC",), adapters=funding_adapters(), optional_sources=("binance-funding",))
    c.eq("an answer about another instrument is unreadable", [f.reason for f in got.failures], [R.UNREADABLE])
    # the series fail but the funding came
    w = World(root / "noseries", with_funding(failing("candles", Reply(outcome=Outcome.FAILED, reason="x"))))
    got = w.run(mod, ("BTC",), adapters=funding_adapters(), optional_sources=("binance-funding",))
    c.eq(
        "a candidate whose series failed keeps the funding it got",
        ([f.reason for f in got.failures], [(cid, len(i)) for cid, i in got.by_candidate], len(got.records)),
        ([R.CALL_FAILED], [("cd-btc", 1)], 1),
    )
    return c.bad


def check_funding_perpetual(mod: types.ModuleType, root: Path) -> list[str]:
    """The perpetual whose funding is read is found through the registry by the candidate's token, never by a name made from the primary
    venue's spelling of it: Kraken calls bitcoin ``XXBT`` and the registry knows that it is the token of ``BTCUSDT`` on binance-usdm."""
    c = Case(mod)
    perp_btc = inst("binance-usdm", "BTCUSDT", "BTC", kind=PERP).instrument_id

    def run(w: World, *pairs: tuple[str, str]) -> Any:
        joined = {cid: w.registry.join(inst("kraken", raw, base).instrument_id, Want()).asset for cid, raw, base in pairs}
        return mod.collect(
            w.env(mod, adapters={"kraken": Kraken(), "binance-funding": BinanceFunding()}),
            [snapshot.Candidate(cid, asset.asset_id) for cid, asset in joined.items()],
            source_urn=SRC,
            wait_s=0.05,
            windows=(mod.Window(60, 5),),
            optional_sources=("binance-funding",),
        ), joined

    def funding_asked(w: World) -> list[str]:
        return sorted(dict(x.args)["instrument"] for x in w.runner.calls if x.operation == "funding")

    def no_perpetual(got: Any) -> dict[str, str]:
        return {f.candidate_id: f.detail for f in got.failures if f.reason is mod.Reason.NO_PERPETUAL}

    w = World(root / "kraken", with_funding())
    w.registry.admit(  # a second venue that lists ADA, so that the registry resolves it
        lst("binance-spot", *[inst("binance-spot", f"{n}USDT", n) for n in ("BTC", "ETH", "SOL", "ADA")]),
        scope="spot",
        known_at=t(3),
        capture_id="cap-b2",
    )
    w.registry.admit(
        lst("kraken", inst("kraken", "XBTUSDT", "XXBT"), inst("kraken", "SOLUSDT", "SOL"), inst("kraken", "ADAUSDT", "ADA")),
        scope="spot",
        known_at=t(4),
        capture_id="cap-k",
    )
    got, joined = run(w, ("cd-btc", "XBTUSDT", "XXBT"), ("cd-sol", "SOLUSDT", "SOL"), ("cd-ada", "ADAUSDT", "ADA"))
    c.eq("a token the primary venue spells its own way still has its perpetual's funding read", funding_asked(w), ["BTCUSDT", "SOLUSDT"])
    funded = [w.store.get(i) for i in got.ids().get("cd-btc", ()) if w.store.get(i).path == "funding"]
    c.eq(
        "and the item is about the candidate's asset and that perpetual",
        [x.asset_refs for x in funded],
        [((joined["cd-btc"].asset_id, perp_btc, "RESOLVED"),)],
    )
    c.eq(
        "a token with no perpetual on the funding venue says so",
        no_perpetual(got),
        {"cd-ada": "the registry lists no perpetual of the token on binance-usdm"},
    )
    # a USDT perpetual of the token on another venue, and a USDC perpetual and one of another size on the venue: none is the one wanted
    w = World(root / "others", with_funding())
    w.registry.admit(
        lst("binance-spot", *[inst("binance-spot", f"{n}USDT", n) for n in ("BTC", "ETH", "SOL", "ADA")]),
        scope="spot",
        known_at=t(3),
        capture_id="cap-b2",
    )
    w.registry.admit(lst("kraken", inst("kraken", "ADAUSDT", "ADA")), scope="spot", known_at=t(4), capture_id="cap-k")
    w.registry.admit(
        lst(
            "binance-usdm",
            *[inst("binance-usdm", f"{n}USDT", n, kind=PERP) for n in ("BTC", "ETH", "SOL")],
            inst("binance-usdm", "ADAUSDC", "ADA", "USDC", kind=PERP),
            inst("binance-usdm", "1000ADAUSDT", "1000ADA", kind=PERP, mult="1000"),
        ),
        scope="perpetual",
        known_at=t(5),
        capture_id="cap-u2",
    )
    w.registry.admit(lst("okx", inst("okx", "ADA-USDT-SWAP", "ADA", kind=PERP)), scope="perpetual", known_at=t(6), capture_id="cap-x")
    got, _ = run(w, ("cd-ada", "ADAUSDT", "ADA"))
    c.eq("none of them is read", funding_asked(w), [])
    c.eq(
        "and the refusal names the first perpetual on the venue with the field the registry would not join",
        no_perpetual(got),
        {"cd-ada": "1000ADAUSDT: the evidence has multiplier '1', the instrument '1000'"},
    )
    return c.bad


def expected_features(minute_n: int, hour_n: int) -> dict[str, str]:
    """The features of the klines() zigzag, by the fraction path (a different path from the module's decimals)."""

    def closes(n: int) -> list[Fraction]:
        return [Fraction(1000 + (k * 7) % 23) for k in range(n)]

    m, h = closes(minute_n), closes(hour_n)

    def true_range(c: list[Fraction], i: int) -> Fraction:
        high, low = max(c[i - 1], c[i]) + 1, min(c[i - 1], c[i]) - 1
        return max(high - low, abs(high - c[i - 1]), abs(low - c[i - 1]))

    return {
        "atr14": text(sum(true_range(h, i) for i in range(hour_n - 14, hour_n)) / 14),
        "atr14_share": text(sum(true_range(h, i) for i in range(hour_n - 14, hour_n)) / 14 / h[-1]),
        "close": text(m[-1]),
        "ret_1h": text(m[-1] / m[-61] - 1),
        "ret_24h": text(h[-1] / h[-25] - 1),
        "ret_4h": text(h[-1] / h[-5] - 1),
    }


def check_funding_list_unusable(mod: types.ModuleType, root: Path) -> list[str]:
    """A list of perpetuals the registry never accepted is said so, with the registry's own reason; it is not a token without a perpetual."""
    c = Case(mod)
    R = mod.Reason
    names = ("BTC", "ETH")

    def build(tag: str, *usdm: tuple[Any, str, int]) -> Any:
        w = World(root / tag, with_funding())
        w.registry = Registry()
        w.registry.admit(
            lst("binance-spot", *[inst("binance-spot", f"{n}USDT", n) for n in ("BTC", "ETH", "SOL")]),
            scope="spot",
            known_at=t(0),
            capture_id="cap-b",
        )
        w.registry.admit(
            lst("okx-spot", *[inst("okx-spot", f"{n}-USDT", n) for n in ("BTC", "ETH", "SOL")]),
            scope="spot",
            known_at=t(1),
            capture_id="cap-o",
        )
        for listing, scope, minute in usdm:
            w.registry.admit(listing, scope=scope, known_at=t(minute), capture_id=f"cap-u{minute}")
        w.assets = {n: w.asset(n) for n in ("BTC", "ETH", "SOL")}
        return w

    def failures_of(w: Any) -> list[tuple[str, Any, str]]:
        got = w.run(mod, names, adapters=funding_adapters(), optional_sources=("binance-funding",))
        return [(f.candidate_id, f.reason, f.detail) for f in got.failures]

    perps = lst("binance-usdm", *[inst("binance-usdm", f"{n}USDT", n, kind=PERP) for n in ("BTC", "ETH", "SOL")])
    dated = inst("binance-usdm", "BTCUSDT_261225", "BTC", kind=FUTURE)
    mixed = lst("binance-usdm", *perps.instruments, dated)
    # no list of perpetuals was ever read
    c.eq(
        "no list read",
        failures_of(build("none")),
        [
            (f"cd-{n.lower()}", R.PERPETUAL_LIST_UNUSABLE, "the registry has not yet accepted a list of perpetuals of binance-usdm")
            for n in names
        ],
    )
    # a list that also holds a dated future is refused, and the refusal's reason is kept
    c.eq(
        "a mixed list is refused as a scope mismatch",
        failures_of(build("mixed", (mixed, "perpetual", 2))),
        [
            (f"cd-{n.lower()}", R.PERPETUAL_LIST_UNUSABLE, "the registry refused the list of perpetuals of binance-usdm: SCOPE_MISMATCH")
            for n in names
        ],
    )
    # an empty list is refused too, with its own reason
    c.eq(
        "an empty list is refused as empty",
        failures_of(build("empty", (lst("binance-usdm"), "perpetual", 2))),
        [
            (f"cd-{n.lower()}", R.PERPETUAL_LIST_UNUSABLE, "the registry refused the list of perpetuals of binance-usdm: EMPTY")
            for n in names
        ],
    )
    # a good list: the funding is read and nothing fails
    c.eq("a good list of perpetuals", failures_of(build("good", (perps, "perpetual", 2))), [])
    # a good list, then a refused one: the good list stays, so a token without a perpetual is said to have none
    after = build("later", (perps, "perpetual", 2), (mixed, "perpetual", 3))
    c.eq("a refusal after a good list does not hide the good list", failures_of(after), [])
    only_spot = lst("binance-usdm", *[inst("binance-usdm", f"{n}USDT", n, kind=PERP) for n in ("SOL",)])
    sol = build("tokenless", (only_spot, "perpetual", 2))
    got = sol.run(mod, names, adapters=funding_adapters(), optional_sources=("binance-funding",))
    c.eq(
        "a good list without the token's perpetual is a token without a perpetual",
        sorted((f.candidate_id, f.reason) for f in got.failures),
        [("cd-btc", R.NO_PERPETUAL), ("cd-eth", R.NO_PERPETUAL)],
    )
    return c.bad


def check_environment_cache(mod: types.ModuleType, root: Path) -> list[str]:
    """The day's clusters are a cache each environment has of its own, and an environment equals another that differs only in it."""
    c = Case(mod)
    w = World(root)
    a, b = w.env(mod), w.env(mod)
    c.eq("a cache of one's own", a.day_clusters is b.day_clusters, False)
    a.day_clusters[(1, (("v", "i", "s"),))] = (("X", "Y"),)
    c.eq("filling it leaves the other empty", b.day_clusters, {})
    c.eq("environments that differ only in the cache are equal", a == b, True)
    c.eq("the data mode is live unless said", a.data_mode, "live")
    return c.bad


def check_defaults_and_order(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root)
    got = mod.collect(w.env(mod), list(reversed(w.candidates(mod, "SOL", "BTC", "ETH"))), source_urn=SRC)
    asked = [(x.args["instrument"], x.args["interval_s"]) for x in w.runner.calls if x.operation == "candles"]
    c.eq(
        "candidates in id order, the one-minute window before the one-hour window",
        asked,
        [(f"{n}USDT", i) for n in ("BTC", "ETH", "SOL") for i in (60, 3600)],
    )
    c.eq("the clock is asked first", w.runner.calls[0].operation, "time")
    c.eq(
        "by default 120 one-minute bars ending on the grid",
        dict(w.runner.calls[1].args),
        {"end": "2026-10-29T12:05:00.000Z", "instrument": "BTCUSDT", "interval_s": 60, "start": "2026-10-29T10:05:00.000Z"},
    )
    c.eq(
        "and 40 one-hour bars ending on the hour",
        dict(w.runner.calls[2].args),
        {"end": "2026-10-29T12:00:00.000Z", "instrument": "BTCUSDT", "interval_s": 3600, "start": "2026-10-27T20:00:00.000Z"},
    )
    c.eq("the default caller and no earmark", {(x.caller, x.window) for x in w.runner.calls}, {("cycle", None)})
    c.eq("and a default wait of 30 seconds", {x.deadline for x in w.runner.calls}, {NOW + timedelta(seconds=30)})
    c.eq("no failures", got.failures, ())
    c.eq(
        "three candidates, each with two series and the features",
        [(cid, len(ids)) for cid, ids in got.by_candidate],
        [("cd-btc", 3), ("cd-eth", 3), ("cd-sol", 3)],
    )
    ids = got.by_candidate[0][1]
    minute, hourly, feats = (w.store.get(i) for i in ids)
    c.eq(
        "in that order: minute, hour, features",
        (dict(minute.request)["interval_s"], dict(hourly.request)["interval_s"], feats.path),
        (60, 3600, "features"),
    )
    c.eq(
        "the features item is derived, from the two series",
        (feats.kind, sorted(p for p, _ in feats.derived_from)),
        (evidence.Kind.DERIVED, sorted([minute.evidence_id, hourly.evidence_id])),
    )
    c.eq(
        "with their digests",
        dict(feats.derived_from),
        {minute.evidence_id: w.store.digest(minute.evidence_id), hourly.evidence_id: w.store.digest(hourly.evidence_id)},
    )
    c.eq("and the values the fraction path gives", dict(feats.fields), expected_features(120, 40))
    c.eq(
        "every item of a live read says so, the features too",
        (minute.data_mode, hourly.data_mode, feats.data_mode),
        ("live", "live", "live"),
    )
    replay = World(root / "replay")
    replayed = mod.collect(replay.env(mod, data_mode="recorded"), replay.candidates(mod, "BTC"), source_urn=SRC)
    c.eq(
        "a replay says it is one, in every item",
        [replay.store.get(i).data_mode for i in replayed.by_candidate[0][1]],
        ["recorded", "recorded", "recorded"],
    )
    c.eq("known when built, about the same asset", (feats.known_at_ms, feats.asset_refs), (to_ms(NOW), minute.asset_refs))
    c.eq(
        "nine records, series first and the features after them, candidate by candidate",
        [r["body"]["path"] for r in got.records],
        ["series", "series", "features"] * 3,
    )  # type: ignore[index]
    c.eq(
        "the features item is in the store with the digest of its record",
        w.store.digest(feats.evidence_id),
        evidence.record_digest(got.records[2]),
    )
    c.eq(
        "the default shape freezes into a complete snapshot of the features",
        [
            x.missing
            for x in snapshot.freeze(
                w.store,
                cycle_id="cy-1",
                manifest_hash="9" * 64,
                snapshot_at_ms=to_ms(NOW) + 1_000,
                candidates=w.candidates(mod, "BTC", "ETH", "SOL"),
                shared=[],
                by_candidate=got.ids(),
                required=["hoshitori.features"],
            ).slots
        ],
        [(), (), ()],
    )
    # a real clock has microseconds: the features item is still built, known at the millisecond below
    wm = World(root / "micro")
    wm.clock.set(datetime(2026, 10, 29, 12, 5, 10, 987_654, tzinfo=UTC))
    micro = wm.run(mod, ("BTC",), windows=mod.DEFAULT_WINDOWS)
    c.eq(
        "a clock with microseconds: the item is known at the millisecond below",
        wm.store.get(micro.by_candidate[0][1][2]).known_at_ms,
        to_ms(NOW) + 987,
    )
    w2 = World(root / "second")
    w2.clock.set(datetime(2026, 10, 29, 12, 5, 10, tzinfo=UTC))
    w2.run(mod, ("BTC",), interval_s=60, bars=3)
    c.eq("a window of three bars", dict(w2.runner.calls[1].args)["start"], "2026-10-29T12:02:00.000Z")
    # features need both the one-minute and the one-hour series
    w3 = World(root / "third")
    got = w3.run(mod, ("BTC",), windows=(mod.Window(60, 5), mod.Window(300, 3)))
    c.eq("two windows without an hour: two series and no features", [(cid, len(ids)) for cid, ids in got.by_candidate], [("cd-btc", 2)])
    c.eq("and no features record", [r["body"]["path"] for r in got.records], ["series", "series"])  # type: ignore[index]
    w4 = World(root / "fourth")
    got = w4.run(mod, ("BTC",), windows=(mod.Window(3600, 30), mod.Window(60, 70)))
    c.eq(
        "the windows in the order given, features last",
        [dict(w4.store.get(i).request)["interval_s"] if w4.store.get(i).path == "series" else "features" for i in got.by_candidate[0][1]],
        [3600, 60, "features"],
    )
    # a window that fails leaves the rest
    w5 = World(
        root / "fifth",
        lambda call: (
            Reply(outcome=Outcome.FAILED, reason="x")
            if call.args.get("interval_s") == 3600
            else Reply(SERVER if call.operation == "time" else klines(call.args))
        ),
    )
    got = mod.collect(w5.env(mod), w5.candidates(mod, "BTC"), source_urn=SRC, wait_s=0.05)
    c.eq(
        "the hourly window failing: the minute series stays, no features, the failure names its window",
        ([(cid, len(ids)) for cid, ids in got.by_candidate], [(f.candidate_id, f.reason, f.interval_s) for f in got.failures]),
        ([("cd-btc", 1)], [("cd-btc", mod.Reason.CALL_FAILED, 3600)]),
    )
    c.eq("and names the source that failed", [f.source for f in got.failures], ["binance-spot"])
    return c.bad


def check_failures(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    R = mod.Reason
    # an asset the registry does not resolve
    w = World(root / "a")
    stranger = snapshot.Candidate("cd-zzz", "asnotanasset")
    got = one(mod, w.env(mod), [*w.candidates(mod, "BTC"), stranger], source_urn=SRC, wait_s=0.05)
    c.eq(
        "an unresolved asset is a failure of that candidate only",
        ([f.candidate_id for f in got.failures], [f.reason for f in got.failures], [cid for cid, _ in got.by_candidate]),
        (["cd-zzz"], [R.NO_ASSET], ["cd-btc"]),
    )
    c.eq("with its words", got.failures[0].detail, "the registry does not resolve the candidate's asset")
    c.eq("and the registry as the source that could not say", got.failures[0].source, "registry")
    # no adapter for the venue
    w = World(root / "b")
    got = (
        w.run(mod, adapters={"adapters": {}})
        if False
        else one(mod, w.env(mod, adapters={}), w.candidates(mod, "BTC"), source_urn=SRC, wait_s=0.05)
    )
    c.eq(
        "a venue without an adapter",
        [(f.candidate_id, f.reason, f.detail) for f in got.failures],
        [("cd-btc", R.NO_ADAPTER, "no adapter for binance-spot")],
    )
    c.eq("and no call at all", w.runner.calls, [])
    c.eq("the failure names the venue that has no adapter", [f.source for f in got.failures], ["binance-spot"])
    # the venue's clock fails
    for label, reply, detail in (
        (
            "a failed clock call",
            Reply(outcome=Outcome.FAILED, reason="EXHAUSTED", error=ConnectorError(ErrorClass.TIMEOUT, "no answer")),
            "FAILED: no answer",
        ),
        ("a failed clock call without an error", Reply(outcome=Outcome.BREAKER_OPEN, reason="breaker open"), "BREAKER_OPEN: breaker open"),
        ("a clock that cannot be read", Reply(b"not json"), None),
    ):
        w = World(root / label.replace(" ", "_"), failing("time", reply))
        got = w.run(mod)
        c.eq(
            f"{label}: both candidates fail for the clock",
            [(f.candidate_id, f.reason) for f in got.failures],
            [("cd-btc", R.NO_SERVER_TIME), ("cd-eth", R.NO_SERVER_TIME)],
        )
        if detail is not None:
            c.eq(f"{label}: with the reason", {f.detail for f in got.failures}, {detail})
        c.eq(f"{label}: and the venue whose clock it was", {f.source for f in got.failures}, {"binance-spot"})
        c.eq(f"{label}: no candle call is made", [x.operation for x in w.runner.calls], ["time"])
        c.eq(f"{label}: nothing found and no offset", (got.by_candidate, got.records, got.offsets), ((), (), ()))
    w = World(root / "hang_time", failing("time", Reply(hang=True)))
    got = w.run(mod)
    c.eq(
        "a clock that never answers", {(f.reason, f.detail) for f in got.failures}, {(R.NO_SERVER_TIME, "no result before the wait ended")}
    )

    # a candle call fails for one candidate
    def one_bad(call: Call) -> Reply:
        if call.operation == "time":
            return Reply(SERVER)
        if call.args["instrument"] == "ETHUSDT":
            return Reply(
                outcome=Outcome.FAILED, reason="NOT_RETRYABLE", error=ConnectorError(ErrorClass.MALFORMED, "bad  thing\n   happened")
            )
        return Reply(klines(call.args))

    w = World(root / "one_bad", one_bad)
    got = w.run(mod)
    c.eq(
        "one failing candle call",
        ([cid for cid, _ in got.by_candidate], [(f.candidate_id, f.reason, f.detail) for f in got.failures]),
        (["cd-btc"], [("cd-eth", R.CALL_FAILED, "FAILED: bad thing happened")]),
    )
    c.eq("a failed candidate leaves no item and no record", (len(got.records), len(w.store)), (1, 1))
    for label, reply, detail in (
        ("a refused call", Reply(outcome=Outcome.REFUSED, reason="bad arguments"), "REFUSED: bad arguments"),
        ("a breaker that is open", Reply(outcome=Outcome.BREAKER_OPEN, reason="breaker open"), "BREAKER_OPEN: breaker open"),
        ("a deadline that cannot be met", Reply(outcome=Outcome.DEADLINE_UNREACHABLE, reason="DEADLINE"), "DEADLINE_UNREACHABLE: DEADLINE"),
    ):
        w = World(root / label.replace(" ", "_"), failing("candles", reply))
        got = w.run(mod, ("BTC",))
        c.eq(label, [(f.reason, f.detail) for f in got.failures], [(R.CALL_FAILED, detail)])
    w = World(root / "hang_candles", failing("candles", Reply(hang=True)))
    got = w.run(mod, ("BTC",))
    c.eq(
        "a candle call that never answers", [(f.reason, f.detail) for f in got.failures], [(R.TIMED_OUT, "no result before the wait ended")]
    )
    c.eq("and the clock is still reported", len(got.offsets), 1)
    w = World(root / "garbage", failing("candles", Reply(b"[[1,2,3]]")))
    got = w.run(mod, ("BTC",))
    c.eq("an answer that is not a series", [f.reason for f in got.failures], [R.UNREADABLE])
    c.eq("with the adapter's words, cut short", len(got.failures[0].detail) <= 200 and got.failures[0].detail != "", True)
    w = World(
        root / "long", failing("candles", Reply(outcome=Outcome.FAILED, reason="x", error=ConnectorError(ErrorClass.MALFORMED, "w" * 500)))
    )
    got = w.run(mod, ("BTC",))
    c.eq("a long reason is cut at 200 characters", len(got.failures[0].detail), 200)
    w = World(root / "bad_window")
    got = w.run(mod, ("BTC",), bars=100_000)
    c.eq("a window the venue cannot serve", [(f.candidate_id, f.reason) for f in got.failures], [("cd-btc", R.BAD_REQUEST)])
    c.eq("is not even asked", [x.operation for x in w.runner.calls], ["time"])
    # a call that fails after the clock, and a candidate that failed before any call: still in candidate order
    w = World(root / "mixed", failing("candles", Reply(outcome=Outcome.FAILED, reason="x")))
    mixed = one(
        mod,
        w.env(mod),
        [snapshot.Candidate("cd-b", "asnotanasset"), snapshot.Candidate("cd-a", w.assets["BTC"])],
        source_urn=SRC,
        wait_s=0.05,
    )
    c.eq(
        "failures of different kinds are in candidate order",
        [(f.candidate_id, f.reason) for f in mixed.failures],
        [("cd-a", R.CALL_FAILED), ("cd-b", R.NO_ASSET)],
    )
    # a failed call that still left a capture is a failure; a good outcome with no capture is one too
    for label, reply, detail in (
        ("a failed call that left a capture", Reply(b"{}", outcome=Outcome.FAILED, reason="EXHAUSTED", capture=True), "FAILED: EXHAUSTED"),
        ("a good outcome that left no capture", Reply(outcome=Outcome.OK, capture=False), "OK:"),
    ):
        w = World(root / label.replace(" ", "_"), failing("candles", reply))
        c.eq(label, [(f.reason, f.detail) for f in w.run(mod, ("BTC",)).failures], [(R.CALL_FAILED, detail)])
    w = World(root / "capture_on_clock", failing("time", Reply(SERVER, outcome=Outcome.FAILED, reason="EXHAUSTED", capture=True)))
    c.eq(
        "a clock call that failed but left a capture",
        [(f.reason, f.detail) for f in w.run(mod, ("BTC",)).failures],
        [(R.NO_SERVER_TIME, "FAILED: EXHAUSTED")],
    )
    w = World(root / "interval_one")
    c.eq(
        "an interval of one second is the venue's to refuse",
        [f.reason for f in w.run(mod, ("BTC",), interval_s=1).failures],
        [R.BAD_REQUEST],
    )
    # two venues: each is asked its time once, in the order the candidates come
    w = World(root / "two_venues")
    both = [
        snapshot.Candidate("cd-a", w.perp["BTC"]),
        snapshot.Candidate("cd-b", w.assets["ETH"]),
        snapshot.Candidate("cd-c", w.perp["SOL"]),
    ]
    got = one(mod, w.env(mod, adapters={"binance-spot": w.adapter, "binance-usdm": BinanceUsdm()}), both, source_urn=SRC, wait_s=0.05)
    c.eq("two venues, no failures", (got.failures, [cid for cid, _ in got.by_candidate]), ((), ["cd-a", "cd-b", "cd-c"]))
    c.eq(
        "each venue's clock once, first the venue of the first candidate",
        [(x.provider, x.operation) for x in w.runner.calls[:2]],
        [("binance-usdm", "time"), ("binance-spot", "time")],
    )
    c.eq("one offset per venue", [o.venue for o in got.offsets], ["binance-usdm", "binance-spot"])
    c.eq(
        "failures come out in candidate order",
        [
            f.candidate_id
            for f in mod.collect(
                World(root / "order").env(mod, adapters={}),
                [snapshot.Candidate("cd-b", "x"), snapshot.Candidate("cd-a", "y")],
                source_urn=SRC,
            ).failures
        ],
        ["cd-a", "cd-b"],
    )
    return c.bad


def check_validation(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root / "v")
    run = lambda **o: w.run(mod, ("BTC",), **o)  # noqa: E731
    c.refuses("no bars", lambda: run(bars=0), "a collection reads at least one bar")
    c.refuses("a boolean number of bars", lambda: run(bars=True), "a collection reads at least one bar")
    c.refuses("a fractional number of bars", lambda: run(bars=2.5), "a collection reads at least one bar")
    c.refuses("an interval of zero", lambda: run(interval_s=0), "an interval is a whole number of seconds")
    c.refuses("a boolean interval", lambda: run(interval_s=True), "an interval is a whole number of seconds")
    c.refuses("a wait of zero", lambda: run(wait_s=0), "the wait is a positive number of seconds")
    c.refuses("a negative wait", lambda: run(wait_s=-1), "the wait is a positive number of seconds")
    c.refuses("a boolean wait", lambda: run(wait_s=True), "the wait is a positive number of seconds")
    c.refuses("a text wait", lambda: run(wait_s="5"), "the wait is a positive number of seconds")
    c.refuses("no windows", lambda: run(windows=()), "a collection has at least one window and no interval twice")
    c.refuses(
        "one interval twice",
        lambda: run(windows=(mod.Window(60, 5), mod.Window(60, 6))),
        "a collection has at least one window and no interval twice",
    )
    c.refuses("a bad second window", lambda: run(windows=(mod.Window(60, 5), mod.Window(3600, 0))), "a collection reads at least one bar")
    c.refuses("a negative number of bars", lambda: run(bars=-1), "a collection reads at least one bar")
    c.refuses("a negative interval", lambda: run(interval_s=-60), "an interval is a whole number of seconds")
    c.eq("one bar is fine", len(run(bars=1).by_candidate), 1)
    c.eq("a whole-second wait is fine", len(run(wait_s=1).by_candidate), 1)
    c.eq("nothing to collect", (w.run(mod, ()).by_candidate, w.run(mod, ()).failures), ((), ()))
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_happy_path,
        check_environment_cache,
        check_defaults_and_order,
        check_failures,
        check_funding,
        check_funding_perpetual,
        check_funding_list_unusable,
        check_validation,
    ):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                found = check(mod) if check is check_constants else check(mod, Path(tmp))  # type: ignore[call-arg]
            bad += [f"{check.__name__}: {message}" for message in found]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
