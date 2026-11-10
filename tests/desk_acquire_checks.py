"""Checks of how the Desk runs one menu check (``hoshitori.desk_acquire``), runnable against a mutant.

The gateway is replaced by the scripted runner of the collector's checks, writing real captures into a real store; the adapters and the
registry are the real ones, and RYO's answers are the synthetic ones of ``ryo_data``. What is checked is what the Desk asks (class,
namespace, caller, operations), what it makes of the answers (evidence in a store of its own) and how it fails (a closed class, no evidence).
"""

from __future__ import annotations

import tempfile
import types
from collections.abc import Callable
from concurrent.futures import Future
from datetime import timedelta
from pathlib import Path
from typing import Any

from hoshitori import desk_menu
from hoshitori.capture import RawCapture
from hoshitori.connector import ConnectorError, ErrorClass
from hoshitori.gateway import ON_DEMAND, Call, CallResult, Outcome
from hoshitori.governor import Priority
from hoshitori.lineage import EvidenceStore
from hoshitori.registry import Registry
from hoshitori.venue_binance import BinanceSpot
from hoshitori.venue_funding import BinanceFunding
from hoshitori.venue_kraken import Kraken
from tests import ryo_data as D
from tests.collect_checks import NOW, FakeRunner, Reply, World, funding_body, klines
from tests.collect_checks import SERVER as SERVER_BODY
from tests.ryo_collect_checks import reply_for as ryo_reply

RUN = "run-1"


class ModeRunner(FakeRunner):
    """The scripted runner, whose captures say they are replays: the recorded transport marks the request ``mode``."""

    def submit(self, call: Call) -> Future[CallResult]:
        self.calls.append(call)
        future: Future[CallResult] = Future()
        reply = self.script(call)
        self._n += 1
        capture_id = f"cap-{self._n:032x}"
        now = self.clock.now()
        request = {"operation": call.operation, "arguments": dict(call.args), "attempt": 1, "mode": "recorded"}
        self.captures.append(RawCapture(capture_id, call.provider, request, 200, {}, reply.body, now, now))
        future.set_result(CallResult(self._n, Outcome.OK, capture_id, 1, None, "", 0.0, self.clock.now()))
        return future


def script(call: Call) -> Reply:
    if call.provider == "ryo":
        return ryo_reply(call)
    if call.operation == "time":
        return Reply(SERVER_BODY)
    if call.operation == "funding":
        return Reply(funding_body(call.args))
    return Reply(klines(call.args))


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def adapters() -> dict[str, Any]:
    return {"binance-spot": BinanceSpot(), "binance-funding": BinanceFunding(), "kraken": Kraken()}


def desk_env(mod: types.ModuleType, w: World, **over: Any) -> Any:
    values: dict[str, Any] = {
        "runner": w.runner,
        "captures": w.captures,
        "adapters": adapters(),
        "registry": w.registry,
        "store": EvidenceStore(),
        "clock": w.clock,
        "wait_s": 0.05,
    }
    values.update(over)
    return mod.DeskEnv(**values)


def run_context(mod: types.ModuleType, w: World, name: str = "BTC", **over: Any) -> Any:
    values: dict[str, Any] = {"run_id": RUN, "asset_id": w.assets[name], "symbol": name, "deadline": NOW + timedelta(seconds=600)}
    values.update(over)
    return mod.RunContext(**values)


def menu_check(env: Any, w: World, check_id: str, name: str = "BTC", costs: desk_menu.CostTable | None = None, ryo: bool = True) -> Any:
    providers = {a.provider: frozenset(a.operations() & {"candles", "funding"}) for a in env.adapters.values()}
    built = desk_menu.build_menu(
        w.assets[name],
        name,
        providers,
        frozenset(desk_menu.PIN) if ryo else frozenset(),
        costs or desk_menu.CostTable.from_latencies({}),
    )
    return next(c for c in built.checks if c.check_id == check_id)


PRICE = "INDEPENDENT_PRICE_CHECK:binance-spot:candles"
FUNDING = "DERIVATIVES_CONTEXT:binance-funding:funding"
TOKEN = "RYO_TOKEN_READ:ryo:analyze_token"
DEEP = "RYO_DEEP_ANALYSIS:ryo:deep_analysis"
OVERVIEW = "MARKET_CONTEXT_OVERVIEW:ryo:market_overview"


def with_world(body: Callable[[Path], list[str]]) -> list[str]:
    with tempfile.TemporaryDirectory() as raw:
        return body(Path(raw))


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("outcomes", mod.OUTCOMES, ("OK", "FAILED", "NOT_STARTED"))
    c.eq(
        "error classes",
        mod.ERROR_CLASSES,
        (
            "DEADLINE_UNREACHABLE",
            "NO_ADAPTER",
            "NO_ASSET",
            "NO_INSTRUMENT",
            "BAD_REQUEST",
            "CALL_FAILED",
            "TIMED_OUT",
            "UNREADABLE",
            "NOT_LIVE",
            "BINDING_MISMATCH",
            "UNKNOWN_CHECK_TYPE",
        ),
    )
    c.eq("bars", mod.BARS, 60)
    c.eq("source", mod.SOURCE_PREFIX, "urn:hoshitori:desk:")
    kinds = (mod.DeskEnv, mod.RunContext, mod.Acquired)
    c.eq("frozen", [k.__dataclass_params__.frozen for k in kinds], [True] * 3)
    return c.bad


def check_providers(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root, script)
    env = desk_env(mod, w)
    c.eq(
        "providers and the operations the menu uses",
        mod.providers_of(env),
        {"binance-spot": frozenset({"candles"}), "binance-funding": frozenset({"funding"}), "kraken": frozenset({"candles"})},
    )
    return c.bad


def check_price(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root, script)
    env = desk_env(mod, w)
    got = mod.acquire(env, menu_check(env, w, PRICE), run_context(mod, w))
    c.eq("ok and two calls", (got.outcome, got.error_class, got.spent), ("OK", None, 2))
    c.eq("two captures, one item", (len(got.capture_ids), len(got.evidence_ids)), (2, 1))
    c.eq(
        "a price check hands back the series it read",
        (type(got.series).__name__, len(got.series.candles), got.series.interval_s),
        ("CandleSeries", 60, 60),
    )
    c.eq(
        "every call is on-demand, class P2, called by the desk run",
        [(x.priority, x.namespace, x.caller, x.provider, x.operation) for x in w.runner.calls],
        [
            (Priority.P2, ON_DEMAND, f"desk:{RUN}", "binance-spot", "time"),
            (Priority.P2, ON_DEMAND, f"desk:{RUN}", "binance-spot", "candles"),
        ],
    )
    c.eq("no earmark", {x.window for x in w.runner.calls}, {None})
    candles = w.runner.calls[1].args
    c.eq(
        "sixty one-minute bars ending at the last full minute of the venue's clock",
        (candles["interval_s"], candles["start"], candles["end"]),
        (60, "2026-10-29T11:05:00.000Z", "2026-10-29T12:05:00.000Z"),
    )
    item = env.store.get(got.evidence_ids[0])
    c.eq(
        "a live observation of the asset",
        (item.data_mode, item.asset_refs[0][0], item.asset_refs[0][2]),
        ("live", w.assets["BTC"], "RESOLVED"),
    )
    c.eq(
        "the item is in the desk's own store and the league's is untouched",
        (got.evidence_ids[0] in env.store, len(w.store), len(env.store)),
        (True, 0, 1),
    )
    c.eq("the item names the capture it was read from", item.capture_id, got.capture_ids[1])
    recorded = desk_env(mod, w, data_mode="recorded")
    again = mod.acquire(recorded, menu_check(recorded, w, PRICE), run_context(mod, w))
    c.eq("a recorded binding labels the item recorded", recorded.store.get(again.evidence_ids[0]).data_mode, "recorded")
    return c.bad


def check_funding(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root, script)
    env = desk_env(mod, w)
    got = mod.acquire(env, menu_check(env, w, FUNDING), run_context(mod, w))
    c.eq("ok and one call", (got.outcome, got.error_class, got.spent), ("OK", None, 1))
    c.eq(
        "the operation",
        [(x.provider, x.operation, x.priority, x.namespace) for x in w.runner.calls],
        [("binance-funding", "funding", Priority.P2, ON_DEMAND)],
    )
    item = env.store.get(got.evidence_ids[0])
    c.eq(
        "the asset reference is the perpetual the registry joins",
        (item.asset_refs[0][0], item.asset_refs[0][2]),
        (w.assets["BTC"], "RESOLVED"),
    )
    c.eq("live", item.data_mode, "live")
    return c.bad


def check_ryo(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root, script)
    env = desk_env(mod, w)
    got = mod.acquire(env, menu_check(env, w, TOKEN), run_context(mod, w, "BTC"))
    c.eq("ok and one call", (got.outcome, got.error_class, got.spent), ("OK", None, 1))
    c.eq("observation and interpretation are kept", len(got.evidence_ids), 2)
    c.eq("a RYO read has no series", got.series, None)
    call = w.runner.calls[0]
    c.eq(
        "the call",
        (call.provider, call.operation, dict(call.args), call.priority, call.namespace, call.caller),
        ("ryo", "analyze_token", {"symbol": "BTC"}, Priority.P2, ON_DEMAND, f"desk:{RUN}"),
    )
    first, second = (env.store.get(i) for i in got.evidence_ids)
    c.eq(
        "the observation first, both live, both referring to the asset",
        (first.kind.value, second.kind.value, first.data_mode, second.data_mode),
        ("OBSERVATION", "INTERPRETATION", "live", "live"),
    )
    c.eq("asset reference", first.asset_refs, ((w.assets["BTC"], "ryo:BTC", "RESOLVED"),))
    deep = mod.acquire(env, menu_check(env, w, DEEP), run_context(mod, w, "BTC"))
    c.eq(
        "deep analysis asks with the perpetual flag as a boolean",
        (deep.outcome, dict(w.runner.calls[-1].args)),
        ("OK", {"include_perp": True, "symbol": "BTC"}),
    )
    c.eq("deep analysis refers to the asset", env.store.get(deep.evidence_ids[0]).asset_refs, ((w.assets["BTC"], "ryo:BTC", "RESOLVED"),))
    shift = mod.acquire(env, menu_check(env, w, "MARKET_SENTIMENT_SHIFT:ryo:monitor_market_sentiment_shift"), run_context(mod, w, "BTC"))
    c.eq(
        "the sentiment read",
        (shift.outcome, w.runner.calls[-1].operation, env.store.get(shift.evidence_ids[0]).asset_refs),
        ("OK", "monitor_market_sentiment_shift", ()),
    )
    overview = mod.acquire(env, menu_check(env, w, OVERVIEW), run_context(mod, w, "BTC"))
    c.eq(
        "a market read asks for nothing and has no asset reference",
        (overview.outcome, dict(w.runner.calls[-1].args), env.store.get(overview.evidence_ids[0]).asset_refs),
        ("OK", {}, ()),
    )
    # a replay under a live binding, and a live read under a recorded binding, are both refused
    replay = World(root / "replay", script)
    replay.runner = ModeRunner(replay.captures, replay.clock, script)
    env_live = desk_env(mod, replay, runner=replay.runner)
    bad = mod.acquire(env_live, menu_check(env_live, replay, TOKEN), run_context(mod, replay))
    c.eq("a replay is never a live read", (bad.outcome, bad.error_class, bad.evidence_ids), ("FAILED", "BINDING_MISMATCH", ()))
    env_rec = desk_env(mod, replay, runner=replay.runner, data_mode="recorded")
    ok = mod.acquire(env_rec, menu_check(env_rec, replay, TOKEN), run_context(mod, replay))
    c.eq(
        "a replay under a recorded binding says recorded",
        (ok.outcome, [env_rec.store.get(i).data_mode for i in ok.evidence_ids]),
        ("OK", ["recorded", "recorded"]),
    )
    live_rec = desk_env(mod, w, data_mode="recorded")
    mismatch = mod.acquire(live_rec, menu_check(live_rec, w, TOKEN), run_context(mod, w))
    c.eq("a live read under a recorded binding is refused", (mismatch.outcome, mismatch.error_class), ("FAILED", "BINDING_MISMATCH"))
    return c.bad


FLAG_FIXTURES = Path(__file__).parent / "fixtures" / "address_flag"
DEAD = "0x000000000000000000000000000000000000dEaD"
ENTITY = "0xE950DC316b836e4EeFb8308bf32Bf7C72a1358FF"
SCREEN = "ADDRESS_FLAG_CHECK:address-flag:check"


def flag_body(name: str) -> bytes:
    return (FLAG_FIXTURES / f"{name}.json").read_bytes()


def screen_script(answers: dict[str, bytes]) -> Callable[[Call], Reply]:
    """The scripted world with the screen added: it answers about the address it was asked about, with the recorded envelope."""

    def reply(call: Call) -> Reply:
        if call.provider == "address-flag":
            return Reply(answers[str(call.args["address"]).lower()])
        return script(call)

    return reply


def screen_check(env: Any, w: World, address: str = DEAD, chain: str = "ethereum") -> Any:
    providers = {**{a.provider: frozenset(a.operations() & {"candles", "funding"}) for a in env.adapters.values()}, **env_screen(env)}
    built = desk_menu.build_menu(
        w.assets["BTC"], "BTC", providers, frozenset(), desk_menu.CostTable.from_latencies({}), contract=(chain, address)
    )
    return next(c for c in built.checks if c.check_id == SCREEN)


def env_screen(env: Any) -> dict[str, frozenset[str]]:
    return {name: ops for name, ops in env_providers(env).items() if name == "address-flag"}


def env_providers(env: Any) -> dict[str, frozenset[str]]:
    return {"address-flag": frozenset({"check"})} if env.screens else {}


def check_screen(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    answers = {DEAD.lower(): flag_body("no_flag_found"), ENTITY.lower(): flag_body("flagged")}
    w = World(root, screen_script(answers))
    env = desk_env(mod, w, screens=True)
    c.eq("a Desk with the screen lists its provider for the menu", mod.providers_of(env).get("address-flag"), frozenset({"check"}))
    c.eq("and one without lists none", "address-flag" in mod.providers_of(desk_env(mod, w)), False)
    got = mod.acquire(env, screen_check(env, w), run_context(mod, w, "BTC"))
    c.eq(
        "a clean screen: ok, one call, no failure", (got.outcome, got.error_class, got.spent, got.reading), ("OK", None, 1, "NO_FLAG_FOUND")
    )
    call = w.runner.calls[0]
    c.eq(
        "the call: the screen's provider, on demand, by the run",
        (call.provider, call.operation, dict(call.args), call.priority, call.namespace, call.caller),
        ("address-flag", "check", {"address": DEAD, "chain": "ethereum"}, Priority.P2, ON_DEMAND, f"desk:{RUN}"),
    )
    items = [env.store.get(i) for i in got.evidence_ids]
    c.eq(
        "three readings, each of a source that answered, kept in the Desk's own store",
        [(i.path, i.origin.text, i.data_mode) for i in items],
        [
            ("address_flag/ofac_sdn", "PRIMARY_DOC:ofac", "unknown"),
            ("address_flag/usdt_freeze_ethereum", "CHAIN:ethereum", "unknown"),
            ("address_flag/usdc_freeze_ethereum", "CHAIN:ethereum", "unknown"),
        ],
    )
    c.eq("each reading names the capture the call made", {i.capture_id for i in items}, {got.capture_ids[0]})
    flagged = mod.acquire(env, screen_check(env, w, ENTITY), run_context(mod, w, "BTC"))
    c.eq(
        "a listed address is read as flagged, with its evidence",
        (flagged.outcome, flagged.reading, len(flagged.evidence_ids)),
        ("OK", "FLAGGED", 3),
    )
    # no source answered: that is a failed call, and leaves no evidence
    down = World(root / "down", screen_script({DEAD.lower(): flag_body("unavailable")}))
    env_down = desk_env(mod, down, screens=True)
    nothing = mod.acquire(env_down, screen_check(env_down, down), run_context(mod, down, "BTC"))
    c.eq(
        "no source answered: a failed check with no evidence and no reading",
        (nothing.outcome, nothing.error_class, nothing.evidence_ids, nothing.reading),
        ("FAILED", "CALL_FAILED", (), None),
    )
    # some sources answered
    import json as _json

    partial = _json.loads(flag_body("no_flag_found"))
    partial["status"] = "partial"
    partial["data"]["verdict"] = "incomplete"
    partial["data"]["checks"][1].update({"state": "unavailable", "reason": "timeout", "raw_value": None, "block": None})
    partial["data"]["counts"] = {"in_scope": 3, "listed": 0, "not_listed": 2, "stale": 0, "unavailable": 1}
    some = World(root / "some", screen_script({DEAD.lower(): _json.dumps(partial).encode()}))
    env_some = desk_env(mod, some, screens=True)
    half = mod.acquire(env_some, screen_check(env_some, some), run_context(mod, some, "BTC"))
    c.eq(
        "some sources answered: incomplete, with the evidence of those that did",
        (half.outcome, half.reading, len(half.evidence_ids)),
        ("OK", "INCOMPLETE", 2),
    )
    # an answer that is not the screen of what was asked
    wrong = World(root / "wrong", screen_script({DEAD.lower(): flag_body("flagged")}))
    env_wrong = desk_env(mod, wrong, screens=True)
    bad = mod.acquire(env_wrong, screen_check(env_wrong, wrong), run_context(mod, wrong, "BTC"))
    c.eq(
        "an answer about another address is unreadable and leaves nothing",
        (bad.outcome, bad.error_class, bad.evidence_ids),
        ("FAILED", "UNREADABLE", ()),
    )
    junk = World(root / "junk", screen_script({DEAD.lower(): b"not json"}))
    env_junk = desk_env(mod, junk, screens=True)
    bad = mod.acquire(env_junk, screen_check(env_junk, junk), run_context(mod, junk, "BTC"))
    c.eq("and so is one that is not JSON", (bad.outcome, bad.error_class), ("FAILED", "UNREADABLE"))
    # a Desk with no screen is asked for one
    none = desk_env(mod, w)
    asked = mod.acquire(none, screen_check(env, w), run_context(mod, w, "BTC"))
    c.eq(
        "a screen asked of a Desk that has none is refused before any call, and says why",
        (asked.outcome, asked.error_class, asked.spent, asked.detail),
        ("FAILED", "NO_ADAPTER", 0, "this Desk has no address screen"),
    )
    # a replay is never a live screen
    replay = World(root / "replay", screen_script(answers))
    replay.runner = ModeRunner(replay.captures, replay.clock, screen_script(answers))
    env_live = desk_env(mod, replay, runner=replay.runner, screens=True)
    mismatch = mod.acquire(env_live, screen_check(env_live, replay), run_context(mod, replay))
    c.eq(
        "a replay is never a live screen",
        (mismatch.outcome, mismatch.error_class, mismatch.evidence_ids),
        ("FAILED", "BINDING_MISMATCH", ()),
    )
    env_recorded = desk_env(mod, replay, runner=replay.runner, data_mode="recorded", screens=True)
    kept = mod.acquire(env_recorded, screen_check(env_recorded, replay), run_context(mod, replay))
    c.eq("a replay under a recorded binding is a screen", (kept.outcome, kept.reading, len(kept.evidence_ids)), ("OK", "NO_FLAG_FOUND", 3))
    live_recorded = desk_env(mod, w, data_mode="recorded", screens=True)
    refused = mod.acquire(live_recorded, screen_check(live_recorded, w), run_context(mod, w))
    c.eq("and a live screen under a recorded binding is refused", (refused.outcome, refused.error_class), ("FAILED", "BINDING_MISMATCH"))
    return c.bad


class RefusesRequests:
    """An adapter whose requests are refused as invalid: the Desk must say so."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.provider = inner.provider

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def normalise(self, operation: str, args: Any) -> Any:
        if operation in ("candles", "funding"):
            raise ConnectorError(ErrorClass.INVALID_ARGS, "the instrument is not valid on this venue")
        return self._inner.normalise(operation, args)


def spot_only_world(root: Path, reply: Callable[[Call], Reply] = script) -> World:
    """The same world, whose registry lists the spot instruments only (so the token has no perpetual)."""
    from tests.registry_checks import inst, lst, t

    w = World(root, reply)
    w.registry = Registry()
    names = ("BTC", "ETH", "SOL")
    w.registry.admit(
        lst("binance-spot", *[inst("binance-spot", f"{n}USDT", n) for n in names]), scope="spot", known_at=t(0), capture_id="cap-b"
    )
    w.registry.admit(lst("okx-spot", *[inst("okx-spot", f"{n}-USDT", n) for n in names]), scope="spot", known_at=t(1), capture_id="cap-o")
    w.assets = {n: w.asset(n) for n in names}
    return w


def failing(reply: Reply) -> Callable[[Call], Reply]:
    return lambda call: reply


def check_failures(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    # a call that fails: one call spent, nothing recorded
    w = World(root / "f1", failing(Reply(outcome=Outcome.FAILED, reason="EXHAUSTED", error=ConnectorError(ErrorClass.UPSTREAM, "down"))))
    env = desk_env(mod, w)
    got = mod.acquire(env, menu_check(env, w, PRICE), run_context(mod, w))
    c.eq(
        "a failed call", (got.outcome, got.error_class, got.spent, got.evidence_ids, got.capture_ids), ("FAILED", "CALL_FAILED", 1, (), ())
    )
    c.eq("and no series", got.series, None)
    c.eq("with its detail", "down" in got.detail, True)
    # a call that does not come back
    w = World(root / "f2", failing(Reply(hang=True)))
    env = desk_env(mod, w)
    got = mod.acquire(env, menu_check(env, w, PRICE), run_context(mod, w))
    c.eq("a call with no result", (got.outcome, got.error_class, got.spent), ("FAILED", "TIMED_OUT", 1))
    # a body that cannot be read
    w = World(root / "f3", lambda call: Reply(SERVER_BODY) if call.operation == "time" else Reply(b"not json"))
    env = desk_env(mod, w)
    got = mod.acquire(env, menu_check(env, w, PRICE), run_context(mod, w))
    c.eq("an unreadable body", (got.outcome, got.error_class, got.spent, got.evidence_ids), ("FAILED", "UNREADABLE", 2, ()))
    c.eq("the captures are kept for the log", len(got.capture_ids), 2)
    w = World(root / "f4", script)
    env = desk_env(mod, w)
    ryo_bad = World(root / "f5", lambda call: Reply(b"{}"))
    renv = desk_env(mod, ryo_bad)
    got_ryo = mod.acquire(renv, menu_check(renv, ryo_bad, TOKEN), run_context(mod, ryo_bad))
    c.eq("a RYO body that is not an answer", (got_ryo.outcome, got_ryo.error_class, got_ryo.spent), ("FAILED", "UNREADABLE", 1))
    c.eq("with the connector's own message, without its class prefix", got_ryo.detail, "RYO's response has no result object")
    # no adapter, no asset, no instrument
    env = desk_env(mod, w)
    check = menu_check(env, w, PRICE)
    none = desk_env(mod, w, adapters={})
    got = mod.acquire(none, check, run_context(mod, w))
    c.eq("no adapter for the provider", (got.outcome, got.error_class, got.spent), ("FAILED", "NO_ADAPTER", 0))
    got = mod.acquire(env, check, run_context(mod, w, asset_id="asset:nope"))
    c.eq("an asset the registry does not know", (got.outcome, got.error_class, got.spent), ("FAILED", "NO_ASSET", 0))
    kraken = menu_check(env, w, "INDEPENDENT_PRICE_CHECK:kraken:candles")
    got = mod.acquire(env, kraken, run_context(mod, w))
    c.eq("no instrument of the token on that venue", (got.outcome, got.error_class, got.spent), ("FAILED", "NO_INSTRUMENT", 0))
    empty = desk_env(mod, w, registry=Registry())
    got = mod.acquire(empty, check, run_context(mod, w))
    c.eq("an empty registry", (got.outcome, got.error_class), ("FAILED", "NO_ASSET"))
    odd = desk_menu.MenuCheck(
        "CHECK_WALLET_IDENTITY:x:y", "CHECK_WALLET_IDENTITY", "OBSERVATION", "x", "y", (), (), desk_menu.Cost(1, None, None, 0)
    )
    got = mod.acquire(env, odd, run_context(mod, w))
    c.eq("a check type nothing serves", (got.outcome, got.error_class, got.spent), ("FAILED", "UNKNOWN_CHECK_TYPE", 0))
    c.eq("and no call was made for any of these", len(w.runner.calls), 0)
    no_perp = spot_only_world(root / "f6")
    nenv = desk_env(mod, no_perp)
    got = mod.acquire(nenv, menu_check(nenv, no_perp, FUNDING), run_context(mod, no_perp))
    c.eq("a token with no perpetual", (got.outcome, got.error_class, got.spent), ("FAILED", "NO_INSTRUMENT", 0))
    c.eq("with the registry's reason", "perpetual" in got.detail, True)
    got = mod.acquire(empty, menu_check(env, w, FUNDING), run_context(mod, w))
    c.eq("funding of an asset the registry does not know", (got.outcome, got.error_class), ("FAILED", "NO_ASSET"))
    w7 = World(root / "f7", lambda call: Reply(b"not json") if call.operation == "funding" else script(call))
    e7 = desk_env(mod, w7)
    got = mod.acquire(e7, menu_check(e7, w7, FUNDING), run_context(mod, w7))
    c.eq("an unreadable funding body", (got.outcome, got.error_class, got.spent, got.evidence_ids), ("FAILED", "UNREADABLE", 1, ()))
    w8 = World(root / "f8", script)
    e8 = desk_env(mod, w8, adapters={"binance-spot": RefusesRequests(BinanceSpot()), "binance-funding": RefusesRequests(BinanceFunding())})
    for check_id in (PRICE, FUNDING):
        got = mod.acquire(e8, menu_check(e8, w8, check_id), run_context(mod, w8))
        c.eq(f"an invalid request: {check_id}", (got.outcome, got.error_class, "not valid" in got.detail), ("FAILED", "BAD_REQUEST", True))
    c.eq("only the clock call of the price check was spent", len(w8.runner.calls), 1)
    w9 = World(root / "f9", lambda call: Reply(D.rest_body(call.operation, D.result(call.operation, data_mode="simulated"))))
    e9 = desk_env(mod, w9)
    got = mod.acquire(e9, menu_check(e9, w9, TOKEN), run_context(mod, w9))
    c.eq("a RYO answer that says it is not live", (got.outcome, got.error_class, got.evidence_ids), ("FAILED", "NOT_LIVE", ()))
    c.eq("the detail names the data mode", "data mode" in got.detail, True)
    return c.bad


def check_deadline(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root, script)
    env = desk_env(mod, w)
    slow = desk_menu.CostTable.from_latencies({("binance-spot", "candles"): [30.0], ("ryo", "analyze_token"): [60.0]})
    check = menu_check(env, w, PRICE, costs=slow)
    got = mod.acquire(env, check, run_context(mod, w, deadline=NOW + timedelta(seconds=20)))
    c.eq(
        "a call that cannot finish is not started",
        (got.outcome, got.error_class, got.spent, len(w.runner.calls)),
        ("NOT_STARTED", "DEADLINE_UNREACHABLE", 0, 0),
    )
    c.eq("and says why", got.detail, "the call cannot finish before the deadline")
    got = mod.acquire(env, check, run_context(mod, w, deadline=NOW + timedelta(seconds=30)))
    c.eq("a call that can just finish is started", (got.outcome, len(w.runner.calls) > 0), ("OK", True))
    w2 = World(root / "b", script)
    env2 = desk_env(mod, w2)
    unknown = menu_check(env2, w2, PRICE)
    got = mod.acquire(env2, unknown, run_context(mod, w2, deadline=NOW + timedelta(seconds=1)))
    c.eq("an unknown cost does not stop a call", got.outcome, "OK")
    half = mod.acquire(env2, unknown, run_context(mod, w2, deadline=NOW + timedelta(milliseconds=500)))
    c.eq("not even with less than a second left", half.outcome, "OK")
    got = mod.acquire(env2, unknown, run_context(mod, w2, deadline=NOW))
    c.eq("no time left stops it", (got.outcome, got.error_class), ("NOT_STARTED", "DEADLINE_UNREACHABLE"))
    got = mod.acquire(env2, unknown, run_context(mod, w2, deadline=NOW - timedelta(seconds=5)))
    c.eq("a deadline in the past stops it", got.outcome, "NOT_STARTED")
    ryo = menu_check(env2, w2, TOKEN, costs=slow)
    got = mod.acquire(env2, ryo, run_context(mod, w2, deadline=NOW + timedelta(seconds=59)))
    c.eq("the same for a RYO read", (got.outcome, got.error_class), ("NOT_STARTED", "DEADLINE_UNREACHABLE"))
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    try:
        bad += [f"check_constants: {m}" for m in check_constants(mod)]
    except TimeoutError:
        raise
    except Exception as exc:
        bad.append(f"check_constants: raised {type(exc).__name__}: {exc}")
    for check in (check_providers, check_price, check_funding, check_ryo, check_screen, check_failures, check_deadline):
        with tempfile.TemporaryDirectory() as raw:
            try:
                bad += [f"{check.__name__}: {m}" for m in check(mod, Path(raw))]
            except TimeoutError:
                raise
            except Exception as exc:
                bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
