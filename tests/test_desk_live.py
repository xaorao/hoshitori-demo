"""The Desk on live reads: wired through the gateway's runner, labelled live, refused on a replay, and bounded by its budget."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from hoshitori import collect, desk, desk_history, desk_live, desk_plan, desk_record
from hoshitori.clock import ManualClock
from hoshitori.gateway import ON_DEMAND
from hoshitori.governor import Priority
from hoshitori.lineage import EvidenceStore
from hoshitori.ryo_pin import PIN
from tests.collect_checks import NOW, World
from tests.desk_acquire_checks import script
from tests.desk_harness_checks import throwaway_ledger

TOOLS = frozenset(PIN)


def environment(root: Path, data_mode: str = "live") -> tuple[collect.Environment, World]:
    w = World(root, script)
    return collect.Environment(w.runner, w.captures, {}, w.registry, EvidenceStore(), w.clock, data_mode), w


def trigger() -> desk_plan.Trigger:
    return desk_plan.Trigger("trg-1", "EVENT", "SOL", "UP", 1, None)


def test_a_live_run_reads_ryo_through_the_runner_and_every_record_says_live(tmp_path: Path) -> None:
    env, w = environment(tmp_path / "w")
    ledger = throwaway_ledger(tmp_path / "ledger")
    out = desk_live.run_live(env, ledger, trigger(), "run-1", TOOLS, wait_s=0.05)
    assert out.state in ("ABSTAINED", "COMMITTED", "FAILED") and out.records
    asked = w.runner.calls
    assert asked and all(c.provider == "ryo" and c.namespace == ON_DEMAND and c.priority is Priority.P2 for c in asked)
    assert all(c.caller == "desk:run-1" for c in asked) and len(asked) <= 4
    states = [body for kind, body in out.records if kind == "desk_run_state"]
    assert states and all(b["label"] == "live" for b in states) and states[0]["state"] == "RUNNING"
    assert all(desk_record.READERS[kind](body) is not None for kind, body in out.records) if hasattr(desk_record, "READERS") else True


def test_the_live_run_carries_a_calibration_for_its_window(tmp_path: Path) -> None:
    """The time-anchored falsification tests may reduce exposure only with a measured false-alternative rate; the run measures it for the
    window it reads (480 minutes before the announcement and the trigger's horizon after it), once, and says where it came from."""
    from hoshitori import desk_history, falsify

    captured: list[desk.Ports] = []
    real = desk_live.desk.run

    def spy(ports: desk.Ports, *args: object, **kwargs: object) -> object:
        captured.append(ports)
        return real(ports, *args, **kwargs)  # type: ignore[arg-type]

    desk_live.desk.run = spy  # type: ignore[assignment]
    try:
        env, _ = environment(tmp_path / "w")
        desk_live.run_live(env, throwaway_ledger(tmp_path / "l"), trigger(), "run-cal", TOOLS, wait_s=0.05)
    finally:
        desk_live.desk.run = real  # type: ignore[assignment]
    from hoshitori.registry import Registry

    used = captured[0].acquire.args[0].registry  # type: ignore[attr-defined]
    assert isinstance(used, Registry), (
        "with no venue and no registry given, the run's evidence is kept against an empty registry, never none"
    )
    assert (desk_live.CALIBRATION_TRIALS, desk_live.CALIBRATION_SEED) == (500, 7), "the trials and the seed are part of what a digest names"
    cal = captured[0].calibration
    assert isinstance(cal, falsify.Calibration) and cal.trials == desk_live.CALIBRATION_TRIALS
    assert cal.allows("T_ONSET") and cal.allows("T_MARKET") and len(cal.sha256) == 64
    assert desk_live.calibration_for(trigger().hours) is desk_live.calibration_for(trigger().hours), "measured once for a window"
    again = desk_live.calibration_for(trigger().hours)
    assert again.sha256 == falsify_calibration_digest(desk_history.PRE_MINUTES, 60 * trigger().hours)


def falsify_calibration_digest(ta: int, h: int) -> str:
    from hoshitori import falsify_calibration

    return falsify_calibration.run(desk_live.CALIBRATION_TRIALS, desk_live.CALIBRATION_SEED, ta, h).sha256


def test_the_run_names_the_live_binding_not_the_recorded_one(tmp_path: Path) -> None:
    env, _ = environment(tmp_path / "w")
    out = desk_live.run_live(env, throwaway_ledger(tmp_path / "l"), trigger(), "run-2", TOOLS, wait_s=0.05)
    labels = {body["label"] for kind, body in out.records if kind == "desk_run_state"}
    assert labels == {"live"} and desk_live.LABEL == "live" and desk_live.SOURCE_PREFIX == "urn:hoshitori:desk-live:"


def test_a_replay_is_refused_as_a_live_environment(tmp_path: Path) -> None:
    env, _ = environment(tmp_path / "w", "recorded")
    with pytest.raises(desk_live.LiveError, match="never on a replay"):
        desk_live.run_live(env, throwaway_ledger(tmp_path / "l"), trigger(), "run-3", TOOLS)


def test_a_key_that_serves_none_of_the_tools_is_refused(tmp_path: Path) -> None:
    env, _ = environment(tmp_path / "w")
    with pytest.raises(desk_live.LiveError, match="none of the tools"):
        desk_live.run_live(env, throwaway_ledger(tmp_path / "l"), trigger(), "run-4", frozenset({"not_a_tool"}))


def test_the_menu_holds_ryo_checks_and_no_exchange_and_the_measured_costs() -> None:
    menu = desk_live.menu_for("SOL", TOOLS)
    kinds = {c.check_type for c in menu.checks}
    assert kinds and all(c.provider == "ryo" for c in menu.checks) and "INDEPENDENT_PRICE_CHECK" not in kinds
    assert (
        desk_live.MEASURED[("ryo", "analyze_token")] == (55.1,) and desk_live.DEFAULT_WAIT_S == 200.0 and desk_live.DEFAULT_SECONDS == 1800
    )
    assert desk_live.menu_for("SOL", frozenset({"analyze_token"})).checks != menu.checks


def test_the_call_budget_bounds_the_run(tmp_path: Path) -> None:
    env, w = environment(tmp_path / "w")
    desk_live.run_live(env, throwaway_ledger(tmp_path / "l"), trigger(), "run-5", TOOLS, wait_s=0.05, ryo_calls=1)
    assert len(w.runner.calls) <= 1 and isinstance(env.clock, ManualClock) and env.clock.now() >= NOW


def test_the_clock_of_a_run_has_whole_milliseconds_and_follows_the_clock_it_reads() -> None:
    from datetime import UTC, datetime

    class Fine:
        def now(self) -> datetime:
            return datetime(2026, 11, 4, 12, 0, 0, 123_987, tzinfo=UTC)

    cut = desk_live.WholeMilliseconds(Fine()).now()
    assert cut == datetime(2026, 11, 4, 12, 0, 0, 123_000, tzinfo=UTC) and cut.tzinfo is UTC
    whole = datetime(2026, 11, 4, 12, 0, 1, 0, tzinfo=UTC)
    assert desk_live.WholeMilliseconds(ManualClock(whole)).now() == whole


def test_a_run_on_a_clock_with_microseconds_makes_a_log(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    env, _ = environment(tmp_path / "w")

    class Fine:
        def now(self) -> datetime:
            return datetime(2026, 11, 4, 12, 0, 0, 555_555, tzinfo=UTC)

    live = collect.Environment(env.runner, env.captures, {}, env.registry, EvidenceStore(), Fine(), "live")
    out = desk_live.run_live(live, throwaway_ledger(tmp_path / "l"), trigger(), "run-6", TOOLS, wait_s=0.05)
    assert out.records and out.state in ("ABSTAINED", "COMMITTED", "FAILED")


def test_the_clock_cut_is_exact_for_different_microseconds() -> None:
    from datetime import UTC, datetime

    for micro, kept in ((500_000, 500_000), (999_999, 999_000), (1_000, 1_000), (999, 0), (0, 0)):

        class At:
            def __init__(self, m: int) -> None:
                self.m = m

            def now(self) -> datetime:
                return datetime(2026, 11, 4, 12, 0, 0, self.m, tzinfo=UTC)

        assert desk_live.WholeMilliseconds(At(micro)).now().microsecond == kept


def test_the_proposal_is_a_flat_position_and_the_log_is_in_the_ledger(tmp_path: Path) -> None:
    from hoshitori import scheduler

    env, _ = environment(tmp_path / "w")
    ledger = throwaway_ledger(tmp_path / "l")
    out = desk_live.run_live(env, ledger, trigger(), "run-7", TOOLS, wait_s=0.05)
    decisions = [body for kind, body in out.records if kind == "decision"]
    assert decisions and all(
        d["proposal"]["stance"] == "FLAT" and d["proposal"]["p"] == "0.5" and d["proposal"]["weight"] == "0" for d in decisions
    )
    assert all(d["proposal"]["cites"] == [] and d["model_outputs_digest"] == desk_live.NO_MODEL_DIGEST for d in decisions)
    kept = scheduler.read_ledger(ledger)
    assert len(kept) == len(out.records) and ledger.size == len(out.records)


def test_the_budget_the_run_states_is_the_one_it_was_given(tmp_path: Path) -> None:
    env, _ = environment(tmp_path / "w")
    out = desk_live.run_live(env, throwaway_ledger(tmp_path / "l"), trigger(), "run-8", TOOLS, wait_s=0.05)
    first = next(body for kind, body in out.records if kind == "desk_run_state")
    assert first["budget"] == {"calls": {"ryo": 4}, "seconds": 1800, "tokens": 1}
    out = desk_live.run_live(env, throwaway_ledger(tmp_path / "l2"), trigger(), "run-9", TOOLS, wait_s=0.05, ryo_calls=2, seconds=900)
    assert next(body for kind, body in out.records if kind == "desk_run_state")["budget"] == {
        "calls": {"ryo": 2},
        "seconds": 900,
        "tokens": 1,
    }


def test_each_check_costs_what_was_measured_for_its_tool() -> None:
    menu = desk_live.menu_for("SOL", TOOLS)
    seen = {c.operation: c.cost.seconds for c in menu.checks}
    expected = {
        "analyze_token": "55.100",
        "deep_analysis": "50.600",
        "monitor_market_sentiment_shift": "59.500",
        "market_overview": "60.000",
    }
    assert seen and all(seen[op] == expected[op] for op in seen if op in expected) and {op for op in seen if op in expected}
    assert set(desk_live.MEASURED) == {
        ("ryo", "analyze_token"),
        ("ryo", "deep_analysis"),
        ("ryo", "monitor_market_sentiment_shift"),
        ("ryo", "market_overview"),
    }


def test_the_small_ports_of_a_live_run() -> None:
    from decimal import Decimal

    request = None  # the flat proposal reads nothing of the request
    flat = desk_live.flat_proposal(request)  # type: ignore[arg-type]
    assert (flat.proposal.stance, flat.proposal.p, flat.proposal.weight, flat.cites, flat.model_outputs_digest, flat.tokens) == (
        "FLAT",
        Decimal("0.5"),
        Decimal(0),
        (),
        desk_live.NO_MODEL_DIGEST,
        0,
    )
    assert desk_live.grounded(flat, ()) is True and desk_live.grounded(flat, ("a",)) is True
    from hoshitori import desk
    from hoshitori.desk_gates import Proposal

    cites = desk.Proposed(Proposal("LONG", Decimal("0.6"), Decimal("0.01")), ("e1", "e2"), "d" * 64, 0)
    assert desk_live.grounded(cites, ("e1", "e2", "e3")) is True and desk_live.grounded(cites, ("e1",)) is False
    bare = desk.Proposed(Proposal("LONG", Decimal("0.6"), Decimal("0.01")), (), "d" * 64, 0)
    assert desk_live.grounded(bare, ("e1",)) is False, "a position that cites no evidence of the run is not grounded"
    assert desk_live.grounded(bare, ()) is False
    assert desk_live.not_stale(None) is False and desk_live.room_of(None) == Decimal("0.05")  # type: ignore[arg-type]


def listed_registry() -> object:
    from hoshitori.registry import Registry
    from hoshitori.venue_binance import BinanceSpot
    from tests.venue_support import recording

    rec = recording("binance_exchangeinfo_trading")
    adapter = BinanceSpot()
    registry = Registry()
    registry.admit(
        adapter.parse_instruments_body(rec.body, adapter.normalise("instruments", {})),
        scope="spot",
        known_at=rec.captured_at,
        capture_id="x",
    )
    from hoshitori.venue_coinbase import Coinbase

    cb = recording("coinbase_products")
    other = Coinbase()
    registry.admit(
        other.parse_instruments_body(cb.body, other.normalise("instruments", {})), scope="spot", known_at=cb.captured_at, capture_id="y"
    )
    return registry


def test_a_listed_symbol_resolves_to_one_dollar_quoted_spot_asset_and_the_rest_to_none() -> None:
    registry = listed_registry()
    asset = desk_live.asset_for(registry, "SOL")  # type: ignore[arg-type]
    assert asset is not None and asset.venue == "binance-spot" and asset.quote_class and asset.base_raw.upper() == "SOL"
    assert desk_live.asset_for(registry, "NOTACOIN") is None  # type: ignore[arg-type]


def test_the_menu_names_the_asset_it_was_given_and_a_placeholder_otherwise() -> None:
    registry = listed_registry()
    asset = desk_live.asset_for(registry, "SOL")  # type: ignore[arg-type]
    with_asset = desk_live.menu_for("SOL", TOOLS, asset)
    without = desk_live.menu_for("SOL", TOOLS)
    assert with_asset.asset_id == asset.asset_id and without.asset_id == "asset:live-sol"


def test_a_given_proposer_replaces_the_flat_one_and_its_tokens_are_the_budget(tmp_path: Path) -> None:
    from decimal import Decimal

    from hoshitori import desk
    from hoshitori.desk_gates import Proposal

    seen: list[desk.ProposeRequest] = []

    def proposer(request: desk.ProposeRequest) -> desk.Proposed:
        seen.append(request)
        return desk.Proposed(Proposal("LONG", Decimal("0.6"), Decimal("0.02")), (), desk_live.NO_MODEL_DIGEST, 7)

    env, _ = environment(tmp_path / "w")
    out = desk_live.run_live(env, throwaway_ledger(tmp_path / "l"), trigger(), "run-10", TOOLS, wait_s=0.05, proposer=proposer, tokens=500)
    decisions = [body for kind, body in out.records if kind == "decision"]
    first = next(body for kind, body in out.records if kind == "desk_run_state")
    assert first["budget"]["tokens"] == 500
    stance = decisions[0]["proposal"]["stance"] if decisions else None
    assert stance == ("LONG" if seen else "FLAT" if decisions else None)


def kraken_coinbase_registry() -> object:
    from hoshitori.registry import Registry
    from hoshitori.venue_coinbase import Coinbase
    from hoshitori.venue_kraken import Kraken
    from tests.venue_support import recording

    registry = Registry()
    for name, adapter in (("kraken_assetpairs_all", Kraken()), ("coinbase_products", Coinbase())):
        rec = recording(name)
        registry.admit(
            adapter.parse_instruments_body(rec.body, adapter.normalise("instruments", {})),
            scope="spot",
            known_at=rec.captured_at,
            capture_id=name,
        )
    return registry


def test_the_asset_is_taken_from_the_first_venue_that_lists_a_dollar_quote_for_it() -> None:
    bc = listed_registry()
    chosen = desk_live.asset_for(bc, "1INCH")  # type: ignore[arg-type]
    assert chosen is not None and (chosen.venue, chosen.quote_raw) == ("binance-spot", "USDT")
    kc = kraken_coinbase_registry()
    for symbol, want in (("ADA", ("kraken", "USDT")), ("1INCH", ("coinbase", "USD")), ("AIOZ", ("coinbase", "USDT"))):
        got = desk_live.asset_for(kc, symbol)  # type: ignore[arg-type]
        assert got is not None and (got.venue, got.quote_raw) == want, symbol
    assert desk_live.asset_for(kc, "00") is None  # type: ignore[arg-type]


def test_the_registry_asks_each_listed_spot_venue_once_and_nothing_when_there_is_none(tmp_path: Path) -> None:
    import dataclasses
    from types import SimpleNamespace

    from hoshitori import registry_job

    env, _ = environment(tmp_path / "w")
    none, failures = desk_live.build_registry(dataclasses.replace(env, adapters={}))
    assert failures == () and none is not env.registry
    seen: list[object] = []
    real = registry_job.refresh

    def fake(e: object, specs: object, **kw: object) -> object:
        seen.append((e, specs, kw))
        return SimpleNamespace(failures=("x",))

    registry_job.refresh = fake  # type: ignore[assignment]
    try:
        got, failed = desk_live.build_registry(
            dataclasses.replace(env, adapters={"kraken": object(), "binance-spot": object(), "other": object()}), 7.0
        )  # type: ignore[dict-item]
    finally:
        registry_job.refresh = real
    (e, specs, kw) = seen[0]
    assert len(seen) == 1 and e.registry is got and got is not env.registry  # type: ignore[attr-defined]
    assert specs == [registry_job.ListSpec("binance-spot", "spot"), registry_job.ListSpec("kraken", "spot")]
    assert kw == {"caller": "desk", "wait_s": 7.0} and failed == ("x",)


def test_a_run_reads_exchanges_only_when_asked_and_when_there_are_some(tmp_path: Path) -> None:
    import dataclasses
    from types import SimpleNamespace

    from hoshitori.registry import Registry

    calls: list[str] = []
    menus: list[tuple[object, object]] = []
    real = (desk_live.build_registry, desk_live.asset_for, desk_live.menu_for)
    asset = SimpleNamespace(asset_id="as-1", venue="binance-spot")
    adapter = SimpleNamespace(provider="binance-spot", operations=lambda: frozenset({"candles"}))

    def build(e: object) -> object:
        calls.append("build")
        return Registry(), ()

    def pick(registry: object, symbol: str) -> object:
        calls.append("pick:" + symbol)
        return asset

    def menu(symbol: str, tools: object, a: object = None, providers: object = None) -> object:
        menus.append((a, providers))
        return real[2](symbol, tools, None, None)  # type: ignore[arg-type]

    desk_live.build_registry, desk_live.asset_for, desk_live.menu_for = build, pick, menu  # type: ignore[assignment]
    try:
        env, _ = environment(tmp_path / "w")
        with_adapter = dataclasses.replace(env, adapters={"binance-spot": adapter})
        first = desk_live.run_live(with_adapter, throwaway_ledger(tmp_path / "l1"), trigger(), "run-11", TOOLS, wait_s=0.05)
        started = next(body for kind, body in first.records if kind == "desk_run_state")
        assert started["budget"]["calls"] == {"ryo": 4, "binance-spot": 4}
        assert calls == ["build", "pick:SOL"] and menus[-1][0] is asset and menus[-1][1] == {"binance-spot": frozenset({"candles"})}
        calls.clear()
        desk_live.run_live(with_adapter, throwaway_ledger(tmp_path / "l2"), trigger(), "run-12", TOOLS, wait_s=0.05, exchange=False)
        assert calls == [] and menus[-1] == (None, {})
        desk_live.run_live(env, throwaway_ledger(tmp_path / "l3"), trigger(), "run-13", TOOLS, wait_s=0.05)
        assert calls == [] and menus[-1] == (None, {})
        known = Registry()
        seen: list[object] = []

        def pick_known(registry: object, symbol: str) -> object:
            seen.append(registry)
            return asset

        desk_live.asset_for = pick_known  # type: ignore[assignment]
        desk_live.run_live(with_adapter, throwaway_ledger(tmp_path / "l4"), trigger(), "run-14", TOOLS, wait_s=0.05, registry=known)
        assert calls == [] and seen == [known], "a given registry is the one the token is resolved in, and no venue is listed again"
        assert menus[-1][0] is asset
        seen.clear()
        desk_live.run_live(env, throwaway_ledger(tmp_path / "l5"), trigger(), "run-15", TOOLS, wait_s=0.05, registry=known)
        assert seen == [] and menus[-1] == (None, {}), "with no exchange adapter a given registry resolves nothing"
    finally:
        desk_live.build_registry, desk_live.asset_for, desk_live.menu_for = real


def test_the_given_proposer_is_the_one_in_the_ports_and_the_version_names_the_kind_of_run(tmp_path: Path) -> None:
    import hashlib
    import json

    ports: list[desk.Ports] = []
    real = desk_live.desk.run

    def spy(p: desk.Ports, *args: object, **kw: object) -> object:
        ports.append(p)
        return real(p, *args, **kw)  # type: ignore[arg-type]

    def given(request: desk.ProposeRequest) -> None:
        return None

    desk_live.desk.run = spy  # type: ignore[assignment]
    try:
        env, _ = environment(tmp_path / "w")
        with_model = desk_live.run_live(env, throwaway_ledger(tmp_path / "l1"), trigger(), "run-14", TOOLS, wait_s=0.05, proposer=given)  # type: ignore[arg-type]
        flat = desk_live.run_live(env, throwaway_ledger(tmp_path / "l2"), trigger(), "run-15", TOOLS, wait_s=0.05)
    finally:
        desk_live.desk.run = real
    assert ports[0].propose is given and ports[1].propose is desk_live.flat_proposal
    model_text, flat_text = (json.dumps(o.records, default=str) for o in (with_model, flat))
    model_hash, flat_hash = (hashlib.sha256(b).hexdigest() for b in (b"desk-live-model", b"desk-live-flat"))
    assert model_hash in model_text and flat_hash not in model_text and flat_hash in flat_text and model_hash not in flat_text


SERVER_MS = 1793275503000


def desk_world(tmp_path: Path, with_adapter: bool = True) -> tuple[object, object]:
    from hoshitori import desk_acquire
    from hoshitori.lineage import EvidenceStore
    from tests.collect_checks import World

    w = World(tmp_path, None)  # type: ignore[arg-type]
    adapters = {"binance-spot": w.adapter} if with_adapter else {}
    env = desk_acquire.DeskEnv(w.runner, w.captures, adapters, w.registry, EvidenceStore(), w.clock, "live", 5.0)
    return w, env


def trigger_at(event_ms: int | None, hours: int = 1) -> object:
    from hoshitori.desk_plan import Trigger

    return Trigger("trg-h", "EVENT", "SOL", "UP", hours, event_ms)


def run_context(w: object) -> object:
    from hoshitori import desk_acquire

    return desk_acquire.RunContext("run-h", "x", "SOL", w.clock.now() + timedelta(minutes=20))  # type: ignore[attr-defined]


def test_the_live_history_is_read_from_the_exchange_for_the_token_and_the_market(tmp_path: Path) -> None:
    from hoshitori.gateway import ON_DEMAND
    from hoshitori.governor import Priority

    w, env = desk_world(tmp_path)
    asset = w.registry.lookup_asset(w.assets["SOL"], w.clock.now())  # type: ignore[attr-defined]
    port = desk_live.history_port(env, asset, trigger_at(SERVER_MS - 3 * 3_600_000))  # type: ignore[arg-type]
    built = port(run_context(w))
    assert isinstance(built, desk.History) and built.ta == desk_history.PRE_MINUTES and built.h == 60
    assert (
        len(built.token) == desk_history.PRE_MINUTES + 60
        and built.market is not None
        and len(built.market) == desk_history.PRE_MINUTES + 60
    )
    calls = w.runner.calls  # type: ignore[attr-defined]
    chunks = -(-(desk_history.PRE_MINUTES + 61) // w.adapter.config.max_window_bars)  # type: ignore[attr-defined]
    assert [c.operation for c in calls] == ["time"] + ["candles"] * (2 * chunks)
    assert all(c.priority is Priority.P2 and c.namespace == ON_DEMAND and c.caller == "desk:run-h" for c in calls)


def test_the_market_proxy_is_left_out_for_bitcoin_itself(tmp_path: Path) -> None:

    w, env = desk_world(tmp_path)
    asset = w.registry.lookup_asset(w.assets["BTC"], w.clock.now())  # type: ignore[attr-defined]
    built = desk_live.history_port(env, asset, trigger_at(SERVER_MS - 3 * 3_600_000))(run_context(w))  # type: ignore[arg-type]
    assert isinstance(built, desk.History) and built.market is None
    assert [c.operation for c in w.runner.calls] == ["time"] + ["candles"] * -(
        -(desk_history.PRE_MINUTES + 61) // w.adapter.config.max_window_bars
    )  # type: ignore[attr-defined]


def test_no_history_without_an_event_time_an_asset_an_adapter_or_a_finished_window(tmp_path: Path) -> None:

    w, env = desk_world(tmp_path)
    asset = w.registry.lookup_asset(w.assets["SOL"], w.clock.now())  # type: ignore[attr-defined]
    ctx = run_context(w)
    old = SERVER_MS - 3 * 3_600_000
    assert desk_live.history_port(env, asset, trigger_at(None))(ctx) is None  # type: ignore[arg-type]
    assert desk_live.history_port(env, None, trigger_at(old))(ctx) is None  # type: ignore[arg-type]
    assert w.runner.calls == []  # type: ignore[attr-defined]
    assert desk_live.history_port(env, asset, trigger_at(SERVER_MS - 10 * 60_000))(ctx) is None  # type: ignore[arg-type]
    _, bare = desk_world(tmp_path / "bare", with_adapter=False)
    assert desk_live.history_port(bare, asset, trigger_at(old))(ctx) is None  # type: ignore[arg-type]


def test_the_window_may_end_exactly_at_the_venue_s_last_closed_minute_and_a_longer_horizon_reads_a_longer_window(tmp_path: Path) -> None:
    w, env = desk_world(tmp_path / "a")
    asset = w.registry.lookup_asset(w.assets["SOL"], w.clock.now())  # type: ignore[attr-defined]
    edge = desk_live.history_port(env, asset, trigger_at(SERVER_MS - 60 * 60_000 - 3_000))(run_context(w))  # type: ignore[arg-type]
    assert isinstance(edge, desk.History) and len(edge.token) == desk_history.PRE_MINUTES + 60
    late = desk_live.history_port(env, asset, trigger_at(SERVER_MS - 60 * 60_000 + 57_000))(run_context(w))  # type: ignore[arg-type]
    assert late is None
    w2, env2 = desk_world(tmp_path / "b")
    asset2 = w2.registry.lookup_asset(w2.assets["SOL"], w2.clock.now())  # type: ignore[attr-defined]
    long = desk_live.history_port(env2, asset2, trigger_at(SERVER_MS - 4 * 3_600_000, 2))(run_context(w2))  # type: ignore[arg-type]
    assert isinstance(long, desk.History) and long.h == 120 and len(long.token) == desk_history.PRE_MINUTES + 120


def test_the_live_falsification_tests_are_calibrated_and_allowed_to_cut() -> None:
    """The time-anchored tests of a live run may lower exposure only with a recorded false-alternative rate of at most 10 percent: without
    this the falsification step would only be shown, never able to change a call."""
    from hoshitori import desk_live, falsify

    for hours in (1, 4, 24):
        calibration = desk_live.calibration_for(hours)
        assert calibration.trials == desk_live.CALIBRATION_TRIALS
        for test in ("T_ONSET", "T_MARKET"):
            assert calibration.rates[test] <= falsify.MAX_FALSE_ALTERNATIVE, (hours, test, calibration.rates[test])
            assert calibration.allows(test)
        assert not calibration.allows("T_PLACEBO_TIME"), "the placebo-time test is not the one that was calibrated"


def test_the_prior_proposal_is_the_hypotheses_own_prior_and_cites_what_the_run_read() -> None:
    hypothesis = desk_plan.hypotheses(trigger())[0]
    got = desk_live.prior_proposal(desk.ProposeRequest(hypothesis, {}, (), ("ev1", "ev2")))
    assert (got.proposal.stance, got.proposal.p, got.proposal.weight) == ("LONG", desk_plan.PRE_P_LONG, desk_plan.PRE_WEIGHT_LONG)
    assert got.cites == ("ev1", "ev2") and got.tokens == 0
    assert desk_live.grounded(got, ("ev1", "ev2")) and not desk_live.grounded(got, ("ev1",))


def test_an_on_demand_run_without_a_resolved_token_is_refused(tmp_path: Path) -> None:
    from hoshitori import ondemand

    env, _ = environment(tmp_path / "w")
    ledger = throwaway_ledger(tmp_path / "ledger")
    wiring = ondemand.Wiring(ledger, lambda digest, at: None, None, "USDT", "spot", "A", "p", "s")  # type: ignore[arg-type, return-value]
    with pytest.raises(desk_live.LiveError, match="resolves to one instrument"):
        desk_live.run_live(env, ledger, trigger(), "run-od", TOOLS, wait_s=0.05, on_demand=wiring)


def test_an_on_demand_run_hands_the_commit_port_the_asset_the_symbol_the_horizon_and_its_own_start(tmp_path: Path) -> None:
    import dataclasses
    from types import SimpleNamespace

    from hoshitori import ondemand
    from hoshitori.registry import Registry
    from hoshitori.timeline import to_ms

    seen: list[dict[str, object]] = []
    real = (desk_live.build_registry, desk_live.asset_for, ondemand.desk_commit_port)
    asset = SimpleNamespace(asset_id="as-1", venue="binance-spot")
    adapter = SimpleNamespace(provider="binance-spot", operations=lambda: frozenset({"candles"}))

    def fake_port(wiring: object, **kw: object) -> object:
        seen.append({"wiring": wiring, **kw})
        return lambda request: "fc-never"

    built = [Registry()]
    desk_live.build_registry = lambda e, *a: (built[0], ())  # type: ignore[assignment]
    desk_live.asset_for = lambda registry, symbol: asset  # type: ignore[assignment]
    ondemand.desk_commit_port = fake_port  # type: ignore[assignment]
    try:
        env, _ = environment(tmp_path / "w")
        env = dataclasses.replace(env, adapters={"binance-spot": adapter})
        wiring = ondemand.Wiring(throwaway_ledger(tmp_path / "od"), lambda digest, at: None, None, "USDT", "spot", "A", "p", "s")  # type: ignore[arg-type, return-value]
        for hours, run_id in ((1, "run-od-1"), (4, "run-od-4")):
            desk_live.run_live(
                env,
                throwaway_ledger(tmp_path / f"l{hours}"),
                desk_plan.Trigger("trg-1", "EVENT", "SOL", "UP", hours, None),
                run_id,
                TOOLS,
                wait_s=0.05,
                on_demand=wiring,
            )
    finally:
        desk_live.build_registry, desk_live.asset_for, ondemand.desk_commit_port = real  # type: ignore[assignment]
    assert [(s["asset_id"], s["symbol"], s["horizon_ms"]) for s in seen] == [("as-1", "SOL", 3_600_000), ("as-1", "SOL", 14_400_000)]
    assert all(s["started_ms"] == to_ms(env.clock.now()) for s in seen)
    assert all(s["wiring"].ledger is wiring.ledger and s["wiring"].registry is built[0] for s in seen)  # type: ignore[attr-defined]
    assert all(callable(s["now_ms"]) and s["now_ms"]() == to_ms(env.clock.now()) for s in seen)  # type: ignore[operator]
    assert all(isinstance(s["versions"], tuple) and len(s["versions"]) == 2 for s in seen)  # type: ignore[arg-type]
