"""The Desk on live reads: the same run as the recorded one, with its checks sent through the real gateway and its decision log written to a
ledger of its own.

``desk_harness`` replays recordings and says so in every record. This binds the same run to what the league's gateway reaches now: RYO's
tools through the RYO adapter (slow: the measured answers take 40 to 160 seconds) and, when the symbol resolves to one asset, the exchanges'
own candles and funding, all under the Desk's on-demand namespace and class, with the wait for one answer set from those measurements and a
budget of calls that bounds the run. Every item and every record says ``live``.

What this does not do, said once: it asks no model (the proposal is a flat position, which claims no skill); it reads the
announcement's history only when every bar of its window has closed (otherwise the falsification tests that need one are listed as
unresolved, not passed); and it commits no forecast (the commitment names none). A live run is never time-stamped by an authority here
and never settled. The ledger it is given is the Desk's own, never the league's.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import lru_cache, partial
from pathlib import Path
from typing import Final, cast

from hoshitori import (
    canonical,
    desk,
    desk_acquire,
    desk_gates,
    desk_history,
    desk_menu,
    desk_plan,
    desk_record,
    falsify,
    falsify_calibration,
    ondemand,
    registry_job,
)
from hoshitori.clock import Clock
from hoshitori.collect import UNREADABLE, Environment, grid_floor
from hoshitori.connector import ConnectorError
from hoshitori.ledger import Ledger
from hoshitori.lineage import EvidenceStore
from hoshitori.registry import Asset, Registry, Want
from hoshitori.ryo_pin import PIN
from hoshitori.timeline import to_ms
from hoshitori.timeutil import format_rfc3339_ms
from hoshitori.venue_data import Candle, CandleSeries, VenueDataError, build_series

LABEL: Final = "live"
ASSET_PREFIX: Final = "asset:live-"
SOURCE_PREFIX: Final = "urn:hoshitori:desk-live:"
DEFAULT_WAIT_S: Final = 200.0  # a little above the slowest answer measured from this machine (161 seconds)
DEFAULT_SECONDS: Final = 1800
EXCHANGE_CALLS: Final = 4  # calls a run may make of one exchange
SPOT_VENUES: Final = ("binance-spot", "kraken", "coinbase")  # the venues a run lists, in the order an asset is taken from them
QUOTES: Final = ("USDT", "USD")
# What the measured answers of this key took, in seconds, for the cost the menu shows and the deadline check uses: from the first
# authenticated pass (`fixtures/ryo_live`), kept as plain numbers so a change shows in a diff.
MEASURED: Final = {
    ("ryo", "analyze_token"): (55.1,),
    ("ryo", "deep_analysis"): (50.6,),
    ("ryo", "monitor_market_sentiment_shift"): (59.5,),
    ("ryo", "market_overview"): (60.0,),
}
NO_MODEL_DIGEST: Final = hashlib.sha256(b"").hexdigest()


class WholeMilliseconds:
    """A clock that reads another and cuts the microseconds off: the log keeps whole milliseconds (a real clock has more)."""

    def __init__(self, inner: Clock) -> None:
        self._inner = inner

    def now(self) -> datetime:
        moment = self._inner.now()
        return moment.replace(microsecond=moment.microsecond // 1000 * 1000)


HOUR_MS: Final = 3_600_000


class LiveError(ValueError):
    """A live run that will not be made."""


def build_registry(env: Environment, wait_s: float = 60.0) -> tuple[Registry, tuple[registry_job.ListFailure, ...]]:
    """The asset registry of a live run: each venue of the environment that lists spot instruments is asked for its list, once. A venue that
    cannot answer is listed with its reason and the registry holds what the others gave; nothing is invented for it."""
    registry = Registry()
    specs = [registry_job.ListSpec(venue, "spot") for venue in SPOT_VENUES if venue in env.adapters]
    if not specs:
        return registry, ()
    done = registry_job.refresh(dataclasses.replace(env, registry=registry), specs, caller="desk", wait_s=wait_s)
    return registry, done.failures


def asset_for(registry: Registry, symbol: str) -> Asset | None:
    """The asset a live run checks: the symbol's spot instrument on the first venue that lists it with a dollar quote, when the registry
    resolves the symbol to one token. A symbol that is ambiguous or unlisted has no asset, and the run goes without exchange checks."""
    resolution = registry.resolve_symbol(symbol)
    if not resolution.joinable:
        return None
    for venue in SPOT_VENUES:
        for iid in resolution.instrument_ids:
            found = registry.instrument_at(iid)
            if found is None or found.venue != venue or found.quote_raw.upper() not in QUOTES:
                continue
            joined = registry.join(iid, Want())
            if joined.ok and joined.asset is not None:
                return joined.asset
    return None


def menu_for(
    symbol: str, tools: frozenset[str], asset: Asset | None = None, providers: dict[str, frozenset[str]] | None = None
) -> desk_menu.Menu:
    """The menu of a live run: the RYO checks the key serves for this token and, when the symbol resolves to an asset, the exchange checks
    of the providers the run can ask; without an asset, the market-context checks and RYO's token reads only."""
    return desk_menu.build_menu(
        asset.asset_id if asset is not None else ASSET_PREFIX + symbol.lower(),
        symbol,
        providers or {},
        tools & frozenset(PIN),
        desk_menu.CostTable.from_latencies(MEASURED),
    )


def history_port(
    desk_env: desk_acquire.DeskEnv, asset: Asset | None, trigger: desk_plan.Trigger
) -> Callable[[desk_acquire.RunContext], desk.History | None]:
    """The history of an announcement read from the asset's own venue: one-minute bars of the token and, for any token but bitcoin, of
    bitcoin as the market proxy. It is made only when the trigger has an announcement time, the asset has a venue this run can ask and every
    bar of the window (480 minutes before the announcement to one horizon after it) has closed and arrived; otherwise the tests say they
    could not be run. A window longer than one call serves is read in several; these calls are outside the run's acquisition budget."""

    def history(ctx: desk_acquire.RunContext) -> desk.History | None:
        if asset is None or trigger.event_time_ms is None:
            return None
        adapter = desk_env.adapters.get(asset.venue)
        if adapter is None:
            return None
        minute = datetime.fromtimestamp(trigger.event_time_ms // 60_000 * 60, tz=UTC)
        start = minute - timedelta(minutes=desk_history.PRE_MINUTES + 1)
        end = minute + timedelta(minutes=60 * trigger.hours)
        calls = desk_acquire._Calls(desk_env, ctx)
        try:
            server = cast(datetime, adapter.parse(calls.ask(adapter.provider, "time", {})))
            if grid_floor(server, 60) < end:
                return None

            def read(of: Asset) -> CandleSeries | None:
                found = desk_acquire._instrument_on(desk_env.registry, of, asset.venue, desk_env.clock.now())
                if found is None:
                    return None
                step = timedelta(minutes=adapter.config.max_window_bars)
                bars: list[Candle] = []
                cursor = start
                while cursor < end:
                    stop = min(cursor + step, end)
                    window = {"instrument": found[1], "interval_s": 60, "start": format_rfc3339_ms(cursor), "end": format_rfc3339_ms(stop)}
                    got = cast(
                        CandleSeries, adapter.parse(calls.ask(adapter.provider, "candles", adapter.normalise("candles", window)), server)
                    )
                    bars.extend(got.candles)
                    cursor = stop
                return build_series(asset.venue, found[1], 60, bars, server, start, end)

            token = read(asset)
            proxy = asset_for(desk_env.registry, "BTC")
            market = read(proxy) if proxy is not None and proxy.asset_id != asset.asset_id and proxy.venue == asset.venue else None
        except (desk_acquire._Stop, ConnectorError, VenueDataError, *UNREADABLE):
            return None
        return desk_history.build(token, market, trigger.event_time_ms, trigger.hours)

    return history


def flat_proposal(request: desk.ProposeRequest) -> desk.Proposed:
    """No skill is claimed: a flat position whatever the evidence says."""
    return desk.Proposed(desk_gates.Proposal("FLAT", Decimal("0.5"), Decimal(0)), (), NO_MODEL_DIGEST, 0)


def prior_proposal(request: desk.ProposeRequest) -> desk.Proposed:
    """No model and no skill beyond the checks: the Desk's own written prior for the hypothesis (a long position at the prior probability
    and size), citing the evidence the run read, for the gates to cut or keep. It exists so that a run can reach a directional forecast
    without a model's opinion; the run says which proposer it used."""
    prior = request.hypothesis.pre_target
    return desk.Proposed(desk_gates.Proposal(prior.stance, prior.p, prior.weight), request.evidence_ids, NO_MODEL_DIGEST, 0)


def grounded(proposed: desk.Proposed, evidence_ids: tuple[str, ...]) -> bool:
    """A proposal is grounded when everything it cites is evidence of this run, and a position (anything but a flat stance) cites at
    least one item: a decision that names no evidence stands on nothing."""
    if proposed.proposal.stance != "FLAT" and not proposed.cites:
        return False
    return set(proposed.cites) <= set(evidence_ids)


def not_stale(context: desk_acquire.RunContext) -> bool:
    """A live read is as old as the call that made it, and no call is started that cannot finish before the deadline."""
    return False


def room_of(context: desk_acquire.RunContext) -> Decimal:
    """The room the risk kernel leaves for one position of a run that commits nothing: the per-token limit."""
    return Decimal("0.05")


CALIBRATION_TRIALS: Final = 500  # planted series of each scenario, a second or two of arithmetic, measured once for a window
CALIBRATION_SEED: Final = 7


@lru_cache(maxsize=8)
def calibration_for(hours: int) -> falsify.Calibration:
    """The false-alternative rates of the time-anchored falsification tests for the window a run reads: ``desk_history.PRE_MINUTES``
    before the announcement and ``hours`` after it. They are measured on seeded planted series, so the same window always gives the same
    rates and the same digest, which the run's falsification records name as the source of the permission to lower exposure."""
    return falsify_calibration.run(CALIBRATION_TRIALS, CALIBRATION_SEED, desk_history.PRE_MINUTES, 60 * hours).calibration()


def run_live(
    env: Environment,
    ledger: Ledger,
    trigger: desk_plan.Trigger,
    run_id: str,
    tools: frozenset[str],
    *,
    wait_s: float = DEFAULT_WAIT_S,
    seconds: int = DEFAULT_SECONDS,
    ryo_calls: int = 4,
    code_path: Path | None = None,
    exchange: bool = True,
    proposer: Callable[[desk.ProposeRequest], desk.Proposed | desk.ModelFailure] | None = None,
    tokens: int = 1,
    on_demand: ondemand.Wiring | None = None,
    registry: Registry | None = None,
) -> desk.RunOutcome:
    """One Desk run on live reads. Its records go to ``ledger``, which must be the Desk's own. With ``registry`` (the league's own, when the
    Desk runs inside the league) the token is resolved there and no venue is asked for its list again; without it the run lists the spot
    venues itself."""
    if env.data_mode != "live":
        raise LiveError("a live run is made on a live environment, never on a replay")
    if not tools & frozenset(PIN):
        raise LiveError("the key serves none of the tools the Desk reads")
    clock = WholeMilliseconds(env.clock)
    source = SOURCE_PREFIX + run_id
    known, asset = registry, None
    registry = known if known is not None else Registry()
    if exchange and env.adapters:
        if known is None:
            registry, _unlisted = build_registry(env)
        asset = asset_for(registry, trigger.symbol)
    desk_env = desk_acquire.DeskEnv(
        env.runner, env.captures, dict(env.adapters) if asset is not None else {}, registry, EvidenceStore(), clock, LABEL, wait_s
    )

    def sink(kind: str, body: dict[str, canonical.JsonValue]) -> None:
        ledger.submit(desk_record.to_record(kind, body, source, to_ms(clock.now())))

    here = code_path or Path(desk.__file__)
    versions = desk.Versions(
        hashlib.sha256(b"desk-live-model" if proposer else b"desk-live-flat").hexdigest(),
        hashlib.sha256(here.read_bytes()).hexdigest(),
        hashlib.sha256(b"no-protocol").hexdigest(),
    )
    commit: Callable[[desk.CommitRequest], str] = lambda request: f"live-{request.run_id}"  # noqa: E731 - the default names no forecast
    if on_demand is not None:
        if asset is None:
            raise LiveError("an on-demand forecast needs a token the registry resolves to one instrument")
        commit = ondemand.desk_commit_port(
            dataclasses.replace(on_demand, registry=registry),
            asset_id=asset.asset_id,
            symbol=trigger.symbol,
            horizon_ms=trigger.hours * HOUR_MS,
            started_ms=to_ms(clock.now()),
            store=desk_env.store,
            versions=(versions.agent_version_hash, versions.protocol_hash),
            now_ms=lambda: to_ms(clock.now()),
        )
    ports = desk.Ports(
        acquire=partial(desk_acquire.acquire, desk_env),
        store=desk_env.store,
        history=history_port(desk_env, asset, trigger),
        propose=proposer or flat_proposal,
        grounded=grounded,
        stale=not_stale,
        room=room_of,
        commit=commit,
        sink=sink,
        calibration=calibration_for(trigger.hours),
        clock=clock,
    )
    budget = desk.Budget({"ryo": ryo_calls, **{a.provider: EXCHANGE_CALLS for a in desk_env.adapters.values()}}, seconds, tokens)
    menu = menu_for(trigger.symbol, tools, asset, desk_acquire.providers_of(desk_env))
    outcome = desk.run(ports, trigger, menu, budget, run_id, desk.Binding(LABEL), versions)
    ledger.append_pending()
    return outcome
