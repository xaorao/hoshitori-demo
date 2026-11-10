"""Putting the league together from a settings file: every part wired to the outside world, and the commands that look at what
it wrote.

``CYCLE_RUNNER_SPEC`` section 4 is the rule. A settings file names the protocol, the frozen mappings, the trusted root, the
log's signing key, the venues (with an optional base URL, for tests and mirrors), the time-stamp authorities and the
instrument lists; relative paths are read against the file's own directory. Nothing secret is in the file: the log key is a
file of its own that only its owner may read. Everything is checked before anything is opened, and a settings file that
cannot run says so in one sentence.

* **The protocol names the root it runs under** (its version and hash), so a league cannot be started against another root
  than the one its protocol was pre-registered with.
* **The log key must be allowed by the root** for the size the ledger has now (the era check of contract section 8): a league
  whose checkpoints no verifier would accept does not start.
* **There are enough authorities** to reach C3 (at least the number of independent operators the root requires).
"""

from __future__ import annotations

import base64
import dataclasses
import json
import os
import random
import shutil
import stat
import tempfile
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Final, cast

from hoshitori import (
    arm_l1,
    arm_l1x,
    arm_l2,
    attest,
    barrier_rates,
    bundle,
    call_revisions,
    canonical,
    challenge_view,
    claim_checks,
    claims_file,
    collect,
    command_log,
    commitment_store,
    coverage_view,
    cycle,
    decision_alerts,
    desk_hook,
    desk_live,
    desk_model,
    desk_record,
    desk_view,
    drill,
    forecast,
    forgery,
    lab,
    lab_view,
    league,
    lifecycle,
    model_arms,
    model_challenge_file,
    model_client,
    model_gateway,
    model_settings,
    move_check,
    ondemand,
    practice,
    practice_view,
    protocol,
    quality,
    registry_job,
    rejudge,
    replay,
    rules_arm,
    ryo_collect,
    scheduler,
    settle_job,
    settlement_record,
    site_export,
    source_value,
    sweep_view,
    today_items,
    trusted_root,
    tsa_gateway,
    venues,
    view_models,
)
from hoshitori.breaker import BreakerConfig
from hoshitori.capture import CaptureStore
from hoshitori.checkpoint import CheckpointError, parse_text
from hoshitori.clock import Clock, SystemClock
from hoshitori.gateway import DispatchJournal, Gateway
from hoshitori.http_transport import TransportConfigError
from hoshitori.ledger import STATE_OK, Ledger, LedgerError, read_clock_events
from hoshitori.ledger_reader import LedgerReader
from hoshitori.lineage import EvidenceStore
from hoshitori.note import NoteError, Signer, split_note
from hoshitori.registry_job import ListSpec
from hoshitori.retry import RetryPolicy
from hoshitori.runner import Runner, ThreadExecutor, Transport
from hoshitori.ryo import PROVIDER as RYO_PROVIDER
from hoshitori.ryo import RyoConfig
from hoshitori.ryo_gateway import RATE_LIMIT as RYO_RATE_LIMIT
from hoshitori.ryo_gateway import RyoAttached, attach_ryo
from hoshitori.ryo_pin import PIN as RYO_TOOLS
from hoshitori.tsa import PinnedRoot, TokenPolicy

MEMBERS: Final = frozenset(
    {
        "directory", "protocol", "mappings", "barrier_mappings", "trusted_root", "log_key", "source", "venues", "authorities", "lists",
        "tick_every_s", "start_grace_ms", "finish_within_ms", "wait_s", "allow_insecure_loopback", "allow_plain_http_authorities",
        "practice", "models",
    }
)  # fmt: skip
OPTIONAL_MEMBERS: Final = frozenset({"ryo", "ondemand", "desk"})  # absent means off
ONDEMAND_MEMBERS: Final = frozenset({"log_key", "trusted_root"})
RYO_MEMBERS: Final = frozenset({"url", "key_env", "calls_per_hour", "scan_wait_s", "collect_wait_s"})
RYO_OPTIONAL: Final = frozenset({"calls_per_minute", "daily_hour"})  # calls a minute the key allows (absent: the measured five); the UTC
# hour of the one cycle a day that reads RYO (absent: every cycle reads it, within the hour's budget)
WORKERS: Final = 8
SEGMENT_RECORDS: Final = 5000
FORGED_BUNDLE: Final = "bundle"  # the folder, inside a forgery's directory, that holds the forged bundle
FORGED_ROOT: Final = "trusted_root.json"  # and the root it is checked against
STOP_WAIT_S: Final = 5
MAX_TEXT: Final = 1000


class DeploymentError(ValueError):
    """Settings that cannot run, or a command that cannot be carried out."""


@dataclass(frozen=True)
class RyoSettings:
    """RYO as a source of the league: where it is, which environment variable holds the key (never the key), and how long and how often."""

    url: str
    key_env: str
    calls_per_hour: int
    scan_wait_s: float
    collect_wait_s: float
    calls_per_minute: float = RYO_RATE_LIMIT  # RYO's stricter limit is six a minute for the fan-out; the league spends one under it
    daily_hour: int | None = None  # the UTC hour of the one cycle a day that reads RYO; absent, every cycle reads it


@dataclass(frozen=True)
class OnDemandSettings:
    """The on-demand ledger's own identity: its signing key and the trusted root that names that key and the log's origin. Neither is the
    league's: a checkpoint of one must never read as a checkpoint of the other."""

    log_key: Path
    trusted_root: Path


@dataclass(frozen=True)
class Settings:
    directory: Path
    protocol: Path
    mappings: dict[str, Path]
    barrier_mappings: dict[str, Path]  # the arms that also forecast contract B; may be empty
    trusted_root: Path
    log_key: Path
    source: str
    venues: dict[str, str | None]
    authorities: tuple[tsa_gateway.Authority, ...]
    lists: tuple[ListSpec, ...]
    tick_every_s: float
    start_grace_ms: int
    finish_within_ms: int
    wait_s: float
    allow_insecure_loopback: bool
    allow_plain_http_authorities: bool
    practice: bool  # the practice portfolio runs beside the settlement pass
    models: Path | None  # the file of the model arms' settings; none when the protocol has no model arm
    ryo: RyoSettings | None = None  # absent from the file, RYO is off and the league is exactly what it was without it
    ondemand: OnDemandSettings | None = None  # absent, no on-demand ledger is opened and none is settled
    desk: bool = (
        False  # true: the Desk runs on events of a committed cycle that then moved, under its daily cap (needs ondemand and models)
    )


def _object(value: object, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeploymentError(f"{what} is an object")
    return value


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
        raise DeploymentError(f"{what} is a non-empty string")
    return value


def _number(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise DeploymentError(f"{what} is a positive number")
    return float(value)


def _whole(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DeploymentError(f"{what} is a whole number")
    return value


def parse_settings(text: str, base: Path) -> Settings:
    """Settings from the JSON text of a file in directory ``base``; every member once, every value checked."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DeploymentError(f"the settings are not JSON: {exc.msg}") from exc
    top = _object(raw, "the settings")
    if set(top) - OPTIONAL_MEMBERS != MEMBERS:
        raise DeploymentError(f"the settings have the members {sorted(MEMBERS)}, not {sorted(top)}")

    def path(name: str) -> Path:
        return base / _text(top[name], name)

    mappings = _object(top["mappings"], "mappings")
    if not mappings:
        raise DeploymentError("there is at least one mapping")
    barrier = _object(top["barrier_mappings"], "barrier_mappings")
    venue_map = _object(top["venues"], "venues")
    if not venue_map:
        raise DeploymentError("there is at least one venue")
    listed = top["authorities"]
    if not isinstance(listed, list) or not listed:
        raise DeploymentError("there is at least one time-stamp authority")
    wanted = top["lists"]
    if not isinstance(wanted, list) or not wanted:
        raise DeploymentError("there is at least one instrument list")
    authorities = []
    for item in listed:
        entry = _object(item, "an authority")
        if set(entry) != {"name", "url"}:
            raise DeploymentError("an authority has a name and a url")
        try:
            authorities.append(
                tsa_gateway.Authority(_text(entry["name"], "an authority's name"), _text(entry["url"], "an authority's url"))
            )
        except tsa_gateway.TsaConfigError as exc:
            raise DeploymentError(str(exc)) from exc
    specs = []
    for item in wanted:
        entry = _object(item, "an instrument list")
        if not {"venue", "scope"} <= set(entry) <= {"venue", "scope", "kind"}:
            raise DeploymentError("an instrument list has a venue and a scope, and may have a kind")
        kind = entry.get("kind")
        specs.append(
            ListSpec(
                _text(entry["venue"], "a list's venue"),
                _text(entry["scope"], "a list's scope"),
                None if kind is None else _text(kind, "a list's kind"),
            )
        )
    flags = []
    for flag in ("allow_insecure_loopback", "allow_plain_http_authorities", "practice"):
        if not isinstance(top[flag], bool):
            raise DeploymentError(f"{flag} is true or false")
        flags.append(top[flag])
    models = top["models"]
    if models is not None:
        _text(models, "the model settings file")
    ryo = _parse_ryo(top["ryo"]) if "ryo" in top else None
    ondemand = None
    if "ondemand" in top:
        od = _object(top["ondemand"], "ondemand")
        if set(od) != ONDEMAND_MEMBERS:
            raise DeploymentError(f"ondemand has the members {sorted(ONDEMAND_MEMBERS)}, not {sorted(od)}")
        ondemand = OnDemandSettings(
            base / _text(od["log_key"], "the on-demand log key"), base / _text(od["trusted_root"], "the on-demand root")
        )
    desk = False
    if "desk" in top:
        if not isinstance(top["desk"], bool):
            raise DeploymentError("desk is true or false")
        desk = top["desk"]
        if desk and (ondemand is None or models is None):
            raise DeploymentError("the Desk commits through the on-demand ledger and asks the model: it needs ondemand and models")
    source = _text(top["source"], "the source")
    if not source.startswith("urn:"):
        raise DeploymentError("the source is a urn")
    overrides = {}
    for name, url in venue_map.items():
        overrides[name] = None if url is None else _text(url, f"the base url of {name}")
    return Settings(
        path("directory"),
        path("protocol"),
        {arm: base / _text(p, f"the mapping of arm {arm}") for arm, p in mappings.items()},
        {arm: base / _text(p, f"the barrier mapping of arm {arm}") for arm, p in barrier.items()},
        path("trusted_root"),
        path("log_key"),
        source,
        overrides,
        tuple(authorities),
        tuple(specs),
        _number(top["tick_every_s"], "the pause between ticks"),
        _whole(top["start_grace_ms"], "the start grace"),
        _whole(top["finish_within_ms"], "the time a cycle has to finish"),
        _number(top["wait_s"], "the wait"),
        flags[0],
        flags[1],
        flags[2],
        None if models is None else base / models,
        ryo,
        ondemand,
        desk,
    )


def _parse_ryo(value: object) -> RyoSettings:
    entry = _object(value, "the RYO settings")
    if not RYO_MEMBERS <= set(entry) <= RYO_MEMBERS | RYO_OPTIONAL:
        raise DeploymentError(f"the RYO settings have the members {sorted(RYO_MEMBERS)}, not {sorted(entry)}")
    per_minute = RYO_RATE_LIMIT if "calls_per_minute" not in entry else _number(entry["calls_per_minute"], "the RYO calls a minute")
    calls = _whole(entry["calls_per_hour"], "the RYO calls an hour")
    if calls < 1:
        raise DeploymentError("the RYO calls an hour is at least one")
    shortest = RyoConfig().min_attempt_s
    waits = []
    for name, what in (("scan_wait_s", "the wait for the RYO scan"), ("collect_wait_s", "the wait for the RYO evidence")):
        wait = _number(entry[name], what)
        if wait < shortest:  # a call that cannot be given one attempt before its deadline is refused at once, whatever RYO would have said
            raise DeploymentError(f"{what} is at least {shortest:g} seconds, the shortest attempt RYO is given")
        waits.append(wait)
    daily = None if "daily_hour" not in entry else _whole(entry["daily_hour"], "the daily RYO hour")
    if daily is not None and not 0 <= daily <= 23:
        raise DeploymentError("the daily RYO hour is a UTC hour, 0 to 23")
    return RyoSettings(
        _text(entry["url"], "the RYO url"),
        _text(entry["key_env"], "the name of the variable holding the RYO key"),
        calls,
        waits[0],
        waits[1],
        per_minute,
        daily,
    )


def load_settings(file: Path) -> Settings:
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeploymentError(f"the settings cannot be read: {exc.strerror}") from exc
    return parse_settings(text, file.parent)


# --- the signing key --------------------------------------------------------------------------------------------------------------------
def new_key(file: Path, origin: str) -> str:
    """Make a new log signing key in ``file`` (readable by its owner only) and return its public key as a trusted root lists it."""
    if file.exists():
        raise DeploymentError("a key file is never overwritten")
    seed = os.urandom(32)
    try:
        signer = Signer.from_seed(origin, seed)
    except NoteError as exc:
        raise DeploymentError(f"the log's name: {exc}") from exc
    descriptor = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(base64.b64encode(seed) + b"\n")
    return signer.verifier.vkey()


def read_key(file: Path) -> bytes:
    """The 32-byte seed in a key file, which no one but its owner may be able to read."""
    try:
        mode = stat.S_IMODE(file.stat().st_mode)
        text = file.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise DeploymentError(f"the log key cannot be read: {exc.strerror}") from exc
    except UnicodeDecodeError as exc:
        raise DeploymentError("the log key file is not base64") from exc
    if mode & 0o077:
        raise DeploymentError("the log key file can be read by others than its owner")
    try:
        seed = base64.b64decode(text, validate=True)
    except ValueError as exc:
        raise DeploymentError("the log key file is not base64") from exc
    if len(seed) != 32:
        raise DeploymentError("the log key is 32 bytes")
    return seed


# --- assembling a running league --------------------------------------------------------------------------------------------------------
type ModelConfigs = tuple[arm_l1.Config | None, arm_l2.Config | None, arm_l1x.Config | None]


@dataclass
class Running:
    settings: Settings
    services: league.Services
    config: scheduler.Config
    root: trusted_root.TrustedRoot
    ledger: Ledger
    captures: CaptureStore  # what the gateway stored: the raw answers of the exchanges, the authorities and the model
    model_provider: str | None  # the name the model's captures carry, when the league has model arms
    ryo: RyoAttached | None = None  # the RYO adapter and transport, when the settings configure RYO
    ondemand: Ledger | None = None  # the on-demand ledger, open under its own key and origin, when the settings name one

    # What the commands that only look need, under the same names as a ``Reading`` has them.
    @property
    def rules(self) -> protocol.Protocol:
        return self.services.protocol

    @property
    def mappings(self) -> Mapping[str, rules_arm.BaseRates]:
        return self.services.mappings

    @property
    def barrier_mappings(self) -> Mapping[str, barrier_rates.BarrierRates]:
        return self.services.barrier_mappings

    @property
    def clock(self) -> Clock:
        return self.services.env.clock

    @property
    def source_urn(self) -> str:
        return self.services.source_urn

    @property
    def model_configs(self) -> ModelConfigs | None:
        arms = self.services.models
        return None if arms is None else (arms.l1, arms.l2, arms.l1x)


@dataclass
class Reading:
    """What a command that only looks needs, and no more: the settings and the files they name, a read-only view of the ledger and of the
    capture store. No gateway, no thread, no network, no key and no lock, so it opens beside a running league and writes nothing."""

    settings: Settings
    rules: protocol.Protocol
    mappings: Mapping[str, rules_arm.BaseRates]
    barrier_mappings: Mapping[str, barrier_rates.BarrierRates]
    model_configs: ModelConfigs | None
    clock: Clock
    source_urn: str
    config: scheduler.Config
    root: trusted_root.TrustedRoot
    ledger: LedgerReader
    captures: CaptureStore  # opened read-only
    model_provider: str | None


type View = Running | Reading


def _arms_of(rules: protocol.Protocol) -> list[str]:
    """Every arm that makes directional forecasts: the rules arm and each model arm the protocol pins."""
    return [*(arm for arm, _ in rules.arms), *(arm for arm, _, _ in rules.model_arms)]


def _readable(source: str) -> None:
    """An optional source must be one a cycle reads (the collector's funding source, or RYO): any other is refused when the cycle
    collects, so a league that names one would abandon every cycle."""
    if source not in (collect.FUNDING_SOURCE, league.RYO_SOURCE):
        reads = f"{collect.FUNDING_SOURCE} and {league.RYO_SOURCE}"
        raise DeploymentError(f"the protocol's optional source {source} is not one the collector reads (it reads {reads})")


def _check(
    settings: Settings,
    rules: protocol.Protocol,
    root: trusted_root.TrustedRoot,
    mappings: dict[str, rules_arm.BaseRates],
    barrier_mappings: dict[str, barrier_rates.BarrierRates],
    models: model_settings.ModelSettings | None,
) -> None:
    if rules.root_version != root.version or rules.root_sha256 != root.sha256:
        raise DeploymentError("the protocol was pre-registered under another trusted root than the one given")
    if set(mappings) != {arm for arm, _ in rules.arms} or any(mappings[arm].sha256 != pinned for arm, pinned in rules.arms):
        raise DeploymentError("the mappings are the ones the protocol pins, for its arms and no others")
    if set(barrier_mappings) != {arm for arm, _ in rules.barrier_arms} or any(
        barrier_mappings[arm].sha256 != pinned for arm, pinned in rules.barrier_arms
    ):
        raise DeploymentError("the barrier mappings are the ones the protocol pins, for its barrier arms and no others")
    pinned = {name: version for name, version, _ in rules.model_arms}
    if (models is None) != (not pinned):
        raise DeploymentError("the model settings are given exactly when the protocol has model arms")
    if models is not None and models.versions() != pinned:
        raise DeploymentError("the model arms are the ones the protocol pins, at the versions it pins, and no others")
    known = {**venues.VENUES, **venues.SOURCES}
    for name in settings.venues:
        if name not in known:
            raise DeploymentError(f"{name} is not a venue (they are {sorted(known)})")
    for needed in (rules.primary_venue,):
        if needed not in settings.venues:
            raise DeploymentError(f"the protocol's primary oracle venue {needed} is not among the venues")
    for spec in settings.lists:
        if spec.venue not in settings.venues:
            raise DeploymentError(f"the instrument list of {spec.venue} names a venue that is not among the venues")
    if (settings.ryo is not None) != (league.RYO_SOURCE in rules.optional_sources):
        raise DeploymentError("the settings configure RYO exactly when the protocol lists it as an optional source")
    for source in rules.optional_sources:
        _readable(source)
        if source == league.RYO_SOURCE:
            continue
        if source not in settings.venues:
            raise DeploymentError(f"the protocol's optional source {source} is not among the venues")
    if collect.FUNDING_SOURCE in rules.optional_sources:
        funding_lists = [spec for spec in settings.lists if (spec.venue, spec.scope) == (collect.FUNDING_PERPETUAL_VENUE, "perpetual")]
        if not funding_lists:
            raise DeploymentError(
                f"funding is joined to a token through the perpetuals of {collect.FUNDING_PERPETUAL_VENUE}: their list is read too"
            )
        if any(spec.kind != "perpetual" for spec in funding_lists):  # the venue also lists dated futures; the registry refuses such a list
            raise DeploymentError(
                f'the list of the perpetuals of {collect.FUNDING_PERPETUAL_VENUE} names its kind, "perpetual": '
                "without it the list also holds "
                "dated futures and the registry refuses it"
            )
    if len(settings.authorities) < root.time_stamp.required_independent:
        raise DeploymentError(f"the root needs {root.time_stamp.required_independent} independent operators: there are too few authorities")
    if len({a.name for a in settings.authorities}) != len(settings.authorities):
        raise DeploymentError("authorities are named once each")
    if settings.start_grace_ms >= rules.schedule_every_ms:
        raise DeploymentError("the start grace is less than the interval between slots")


def _named_files(
    settings: Settings,
) -> tuple[
    trusted_root.TrustedRoot,
    protocol.Protocol,
    dict[str, rules_arm.BaseRates],
    dict[str, barrier_rates.BarrierRates],
    model_settings.ModelSettings | None,
]:
    """The trusted root, the protocol, the mappings and the model settings the settings name, read and checked against each other."""
    try:
        root = trusted_root.parse_root(settings.trusted_root.read_bytes())
        rules = protocol.load(settings.protocol.read_bytes())
        mappings = {arm: rules_arm.load(path.read_bytes()) for arm, path in settings.mappings.items()}
        barrier_mappings = {arm: barrier_rates.load(path.read_bytes()) for arm, path in settings.barrier_mappings.items()}
        models = None if settings.models is None else model_settings.load(settings.models)
    except (OSError, trusted_root.RootError, protocol.ProtocolError, rules_arm.RulesError, model_settings.ModelSettingsError) as exc:
        raise DeploymentError(f"a file named in the settings cannot be used: {exc}") from exc
    _check(settings, rules, root, mappings, barrier_mappings, models)
    return root, rules, mappings, barrier_mappings, models


def _practice_configs(rules: protocol.Protocol) -> tuple[practice.Config, ...]:
    """The practice books of a league: one for each arm that makes directional forecasts, and for each model arm a shadow book that follows
    the rules arm's long calls on the slots where the model arm chose to make no call (contract section 5: what passing cost or saved)."""
    terms = rules.practice_values()
    own = [practice.config_for(arm, terms) for arm in _arms_of(rules)]
    reference = rules.arms[0][0] if rules.arms else None
    shadows = [
        dataclasses.replace(practice.config_for(f"{arm}.shadow", terms), follows=reference, passed_by=arm)
        for arm, _, _ in rules.model_arms
        if reference is not None
    ]
    return (*own, *shadows)


def _scheduler_config(settings: Settings, rules: protocol.Protocol) -> scheduler.Config:
    return scheduler.Config(
        settings.start_grace_ms,
        settings.finish_within_ms,
        settings.lists,
        settings.directory / "commitments",
        settings.wait_s,
        _practice_configs(rules) if settings.practice else (),
    )


@contextmanager
def assemble_reading(settings: Settings, *, clock: Clock | None = None) -> Iterator[Reading]:
    """Open what a command that only looks needs, and close it when the block ends. Nothing is written, locked, signed or sent: the ledger
    and the capture store are opened read-only, so this runs beside a running league, and no log key and no model key is asked for. A
    league that has never run has no ledger, and none is made."""
    root, rules, mappings, barrier_mappings, models = _named_files(settings)
    try:
        ledger = LedgerReader.open(settings.directory / "ledger", root=root)
    except LedgerError as exc:
        raise DeploymentError(str(exc)) from exc
    captures = CaptureStore(settings.directory / "captures", read_only=True)
    try:
        yield Reading(
            settings,
            rules,
            mappings,
            barrier_mappings,
            None if models is None else (models.l1, models.l2, models.l1x),
            clock or SystemClock(),
            settings.source,
            _scheduler_config(settings, rules),
            root,
            ledger,
            captures,
            None if models is None else models.endpoint().provider,
        )
    finally:
        captures.close()


def _open_ondemand(settings: Settings, league_root: trusted_root.TrustedRoot, league_signer: Signer, clock: Clock) -> Ledger:
    """The on-demand ledger, open under its own key and its own origin. It is refused if either is the league's, or if its own trusted root
    does not allow its key: the two logs must be told apart by whoever checks a bundle of either."""
    od = settings.ondemand
    assert od is not None
    try:
        od_root = trusted_root.parse_root(od.trusted_root.read_bytes())
    except (OSError, trusted_root.RootError) as exc:
        raise DeploymentError(f"the on-demand root cannot be used: {exc}") from exc
    if od_root.origin == league_root.origin:
        raise DeploymentError("the on-demand ledger has its own origin, not the league's")
    od_signer = Signer.from_seed(od_root.origin, read_key(od.log_key))
    if od_signer.verifier.public_key == league_signer.verifier.public_key:
        raise DeploymentError("the on-demand ledger has its own signing key, not the league's")
    ledger = Ledger.open(
        settings.directory / "ondemand", signer=od_signer, origin=od_root.origin, clock=clock, segment_records=SEGMENT_RECORDS
    )
    if od_root.era_check(od_signer.verifier, ledger.size) != trusted_root.ERA_ACCEPT:
        ledger.close()
        raise DeploymentError("the on-demand log's signing key is not allowed by its trusted root for the size the ledger has now")
    return ledger


def _ondemand_pass(settings: Settings, services: league.Services, ledger: Ledger) -> Callable[[], settle_job.Passed]:
    """The settlement pass of the on-demand ledger, for the league's tick: records a process left queued are appended first, then every
    forecast that has matured is settled by the league's own code, once."""

    def run() -> settle_job.Passed:
        ledger.append_pending()
        waiting = settle_job.SettleServices(services.protocol, services.env, ondemand.SOURCE_PREFIX + "settlement")
        return ondemand.settle(waiting, ledger, wait_s=settings.wait_s)

    return run


def _desk_pass(
    settings: Settings, services: league.Services, ondemand_ledger: Ledger, desk_ledger: Ledger
) -> Callable[[list[dict[str, Any]], str, int], desk_hook.Passed]:
    """The Desk step of the league's tick (``desk_hook``): on the events of a committed cycle that then moved, under the daily cap, one
    live Desk run each with the first model arm as proposer, at most one RYO call, committing a long stance through the on-demand ledger."""
    models = services.models
    if models is None or models.l1 is None:
        raise DeploymentError("the Desk asks the single-model arm's model, and these settings configure none")
    l1 = models.l1
    proposer = desk_model.make_proposer(
        models.client, l1.model, max_tokens=l1.max_tokens, thinking=l1.thinking, response_format=l1.response_format
    )
    p = services.protocol
    wiring = ondemand.Wiring(
        ondemand_ledger,
        services.attestor,
        services.timing_rules,
        p.quote_class,
        p.instrument_kind,
        p.oracle_pair,
        p.primary_venue,
        p.second_venue,
        settings.directory / "ondemand_commitments",
    )

    def run(trigger: Any, run_id: str) -> Any:
        return desk_live.run_live(
            services.env,
            desk_ledger,
            trigger,
            run_id,
            frozenset(RYO_TOOLS),
            ryo_calls=desk_hook.RYO_CALLS,
            proposer=proposer,
            tokens=8000,
            on_demand=wiring,
            registry=services.env.registry,
        )

    def step(league_records: list[dict[str, Any]], cycle_id: str, now_ms: int) -> desk_hook.Passed:
        desk_ledger.append_pending()
        return desk_hook.run_pass(league_records, scheduler.read_ledger(desk_ledger), cycle_id, now_ms, run)

    return step


@contextmanager
def assemble(settings: Settings, *, clock: Clock | None = None) -> Iterator[Running]:
    """Open everything the settings name, run nothing, and close it all when the block ends."""
    clock = clock or SystemClock()
    root, rules, mappings, barrier_mappings, models = _named_files(settings)
    if models is not None and not os.environ.get(models.key_env):
        raise DeploymentError(f"the model key is not set: the environment variable {models.key_env} is empty")
    if settings.ryo is not None and not os.environ.get(settings.ryo.key_env):
        raise DeploymentError(f"the RYO key is not set: the environment variable {settings.ryo.key_env} is empty")
    signer = Signer.from_seed(root.origin, read_key(settings.log_key))
    ledger = Ledger.open(settings.directory / "ledger", signer=signer, origin=root.origin, clock=clock, segment_records=SEGMENT_RECORDS)
    captures = CaptureStore(settings.directory / "captures")
    pool = ThreadExecutor(WORKERS)
    runner: Runner | None = None
    loop: threading.Thread | None = None
    ondemand_ledger: Ledger | None = None
    desk_ledger: Ledger | None = None
    try:
        if root.era_check(signer.verifier, ledger.size) != trusted_root.ERA_ACCEPT:
            raise DeploymentError("the log's signing key is not allowed by the trusted root for the size the ledger has now")
        if settings.ondemand is not None:
            ondemand_ledger = _open_ondemand(settings, root, signer, clock)
            if settings.desk:  # the Desk's decision log, under the on-demand identity: never the league's key or origin
                desk_ledger = Ledger.open(
                    settings.directory / "desk", signer=ondemand_ledger.signer, origin=ondemand_ledger.origin, clock=clock,
                    segment_records=SEGMENT_RECORDS,
                )  # fmt: skip
        gateway = Gateway(
            captures,
            DispatchJournal(settings.directory / "dispatch.jsonl"),
            retry_policy=RetryPolicy(),
            rng=random.Random(),  # noqa: S311 - jitter for retries, not a secret
        )
        transports: dict[str, Transport] = {}
        adapters = {}
        ryo_attached: RyoAttached | None = None
        try:
            for name, base_url in settings.venues.items():
                attached = venues.attach(gateway, name, clock, base_url=base_url, allow_insecure_loopback=settings.allow_insecure_loopback)
                transports[attached.adapter.provider] = attached.transport
                adapters[name] = attached.adapter
            if models is not None:
                key_env = models.key_env
                attached_model = model_gateway.attach_model(
                    gateway,
                    models.endpoint(),
                    lambda: os.environ.get(key_env) or None,  # read at each request, so a rotated key is used and none is kept here
                    clock,
                    rate_limit=models.rate_per_min,
                    concurrency=models.concurrency,
                    attempt_cap_s=models.attempt_cap_s,
                    min_attempt_s=models.min_attempt_s,
                    allow_insecure_loopback=settings.allow_insecure_loopback,
                )
                transports[attached_model.adapter.provider] = attached_model.transport
            if settings.ryo is not None:
                ryo_key_env = settings.ryo.key_env
                ryo = attach_ryo(
                    gateway,
                    settings.ryo.url,
                    lambda: os.environ.get(ryo_key_env) or None,  # read at each request, so a rotated key is used and none is kept here
                    clock,
                    rate_limit=settings.ryo.calls_per_minute,
                    allow_insecure_loopback=settings.allow_insecure_loopback,
                )
                transports[ryo.adapter.provider] = ryo.transport
                ryo_attached = ryo
            for authority in settings.authorities:
                got = tsa_gateway.attach_tsa(
                    gateway,
                    authority,
                    clock,
                    breaker=BreakerConfig(),
                    allow_insecure_loopback=settings.allow_insecure_loopback,
                    allow_plain_http=settings.allow_plain_http_authorities,
                )
                transports[got.adapter.provider] = got.transport
        except TransportConfigError as exc:
            raise DeploymentError(str(exc)) from exc
        runner = Runner(gateway, transports, clock, pool)
        loop = threading.Thread(target=runner.run_forever, name="gateway-runner", daemon=True)
        loop.start()
        policy = TokenPolicy(tuple(PinnedRoot(pin.sha256, pin.operator) for pin in root.time_stamp.pinned))
        active = runner

        def attestor(digest: bytes, committed_at_ms: int) -> attest.Attestation:
            return attest.attest(
                active,
                captures,
                clock,
                list(settings.authorities),
                digest,
                committed_at_ms=committed_at_ms,
                policy=policy,
                rules=root.time_stamp,
            )

        registry = registry_job.load(scheduler.read_ledger(ledger))
        env = collect.Environment(runner, captures, adapters, registry, EvidenceStore(), clock)
        arms = None
        if models is not None:
            fetcher = model_client.GatewayFetcher(runner, captures, clock, models.endpoint().provider)
            arms = model_arms.ModelArms(model_client.ModelClient(fetcher), models.l1, models.l2, models.l1x, models.workers)
        wiring = None
        if settings.ryo is not None:
            wiring = league.RyoWiring(
                ryo_collect.RyoEnvironment(runner, captures, env.store, clock),
                scan_wait_s=settings.ryo.scan_wait_s,
                collect_wait_s=settings.ryo.collect_wait_s,
                calls_per_hour=settings.ryo.calls_per_hour,
                daily_hour=settings.ryo.daily_hour,
            )
        services = league.Services(
            rules,
            mappings,
            env,
            ledger,
            attestor,
            root.time_stamp,
            settings.source,
            barrier_mappings=barrier_mappings,
            models=arms,
            ryo=wiring,
            rejudge=rejudge.Rejudger(ledger, captures, policy=policy, rules=root.time_stamp, store=settings.directory / "commitments"),
        )
        config = _scheduler_config(settings, rules)
        if ondemand_ledger is not None:
            config = dataclasses.replace(config, on_demand=_ondemand_pass(settings, services, ondemand_ledger))
            if desk_ledger is not None:
                config = dataclasses.replace(config, desk=_desk_pass(settings, services, ondemand_ledger, desk_ledger))
        try:
            scheduler.check_config(services, config)
        except scheduler.SchedulerError as exc:
            raise DeploymentError(str(exc)) from exc
        yield Running(
            settings,
            services,
            config,
            root,
            ledger,
            captures,
            None if models is None else models.endpoint().provider,
            ryo_attached,
            ondemand_ledger,
        )
    finally:
        if runner is not None:
            runner.stop()
        if loop is not None:
            loop.join(timeout=STOP_WAIT_S)
        pool.shutdown()
        if desk_ledger is not None:
            desk_ledger.close()
        if ondemand_ledger is not None:
            ondemand_ledger.close()
        ledger.close()
        captures.close()


# --- looking at what was written --------------------------------------------------------------------------------------------------------
def tally(values: Iterable[str]) -> dict[str, int]:
    """How many times each value occurs, with the values in order."""
    found: dict[str, int] = {}
    for value in values:
        found[value] = found.get(value, 0) + 1
    return dict(sorted(found.items()))


def ledger_state(running: View) -> dict[str, str]:
    """The state the ledger is in (``OK``, or a failure state such as ``INTEGRITY_FAILURE``) and the ledger's reason, as every command that
    reads the ledger reports it (ADR-026: INTEGRITY_FAILURE is visible on every surface)."""
    return {"state": running.ledger.state, "detail": running.ledger.state_detail}


def _intact(running: View) -> bool:
    """Whether the ledger's records can be read as they were written. In any other state they are not reconstructible, and nothing is read
    from them (LEDGER_SPEC section 8: the product shows NOT RECONSTRUCTIBLE for affected records, never a pass)."""
    return running.ledger.state == STATE_OK


def _require_intact(running: View) -> None:
    if not _intact(running):
        raise DeploymentError(f"the log is in {running.ledger.state}: {running.ledger.state_detail}")


def status(running: View) -> dict[str, Any]:
    """The state of the ledger and counts of what it holds: cycles by state, slots by state, settlements, and what the heartbeat says.
    A ledger that is not intact has no counts: each is ``None``, never zero, and no slot is called missing for records that cannot be
    read."""
    if not _intact(running):
        unknown = ("records", "cycles", "slots", "forecasts", "failures", "settlements", "heartbeat")
        return {"ledger": ledger_state(running), **dict.fromkeys(unknown)}
    records = scheduler.read_ledger(running.ledger)
    cycles = cycle.rebuild(cast(list[dict[str, canonical.JsonValue]], [r for r in records if r.get("type") == cycle.TYPE]))
    slots = lifecycle.rebuild(cast(list[dict[str, canonical.JsonValue]], [r for r in records if r.get("type") == lifecycle.TYPE]))
    now = collect.now_ms(running.clock)
    schedule = running.rules.slots_through(now)
    beats = cycle.heartbeat(
        schedule,
        cycles.values(),
        now_ms=now,
        start_grace_ms=running.config.start_grace_ms,
        finish_within_ms=running.config.finish_within_ms,
    )
    count = lifecycle.denominator(slots.values())

    report = {
        "ledger": ledger_state(running),
        "records": len(records),
        "cycles": tally(cycle.current_state(h).value for h in cycles.values()),
        "slots": tally(lifecycle.current_state(h).value for h in slots.values()),
        "forecasts": count.forecasts,
        "failures": count.failures,
        "settlements": tally(r["body"]["state"] for r in records if r.get("type") == "settlement"),
        "heartbeat": [{"slot": b.planned_for_ms, "finding": b.finding.value, "cycle": b.cycle_id} for b in beats],
    }
    clock_events = read_clock_events(running.settings.directory / "ledger")
    if clock_events:  # only a league whose writer has refused a stepped clock reports on it
        report["clock"] = {"events": len(clock_events), "standing": clock_events[-1].get("kind") != "rebase", "last": clock_events[-1]}
    if running.settings.ryo is not None:  # only a league that reads RYO reports on it, so every other league's status is what it was
        report["ryo"] = {
            "calls_last_hour": running.captures.count_since(RYO_PROVIDER, running.clock.now() - timedelta(hours=1)),
            "calls_per_hour": running.settings.ryo.calls_per_hour,
        }
    return report


def replay_cycle(running: View, cycle_id: str) -> dict[str, Any]:
    """Replay a cycle from the ledger and the stored captures, and say in what, if anything, it differs from what was committed."""
    _require_intact(running)
    configs = running.model_configs
    answers = None
    if running.model_provider is not None:  # the league has model arms exactly when it has a provider for them
        answers = model_client.ReplayFetcher.from_store(running.captures, running.model_provider)
    inputs = replay.Inputs(
        running.rules,
        running.mappings,
        running.barrier_mappings,
        answers,
        None if configs is None else configs[0],
        None if configs is None else configs[1],
        None if configs is None else configs[2],
    )
    try:
        report = replay.replay_cycle(scheduler.read_ledger(running.ledger), cycle_id, inputs)
    except replay.ReplayError as exc:
        raise DeploymentError(str(exc)) from exc
    return {
        "cycle": report.cycle_id,
        "slots": len(report.slots),
        **{status.value.lower(): report.count(status) for status in replay.Status},
        "clean": report.clean,
        "exceptions": [
            {
                "forecast": s.forecast_id,
                "arm": s.arm_id,
                "symbol": s.symbol,
                "hours": s.hours,
                "contract": s.contract,
                "status": s.status.value,
                "detail": s.detail,
            }
            for s in report.slots
            if s.status is not replay.Status.MATCH
        ],
    }


def _committed(running: View, cycle_id: str) -> tuple[str, commitment_store.Saved, list[int]]:
    """What a cycle's bundle is made from: the commitment's id, what the authorities stamped (the checkpoint and the time-stamps) and the
    ledger positions of the forecasts it holds and the evidence they cite."""
    _require_intact(running)
    records = scheduler.read_ledger(running.ledger)
    cycles = cycle.rebuild(cast(list[dict[str, canonical.JsonValue]], [r for r in records if r.get("type") == cycle.TYPE]))
    history = cycles.get(cycle_id)
    if history is None or cycle.current_state(history) is not cycle.State.COMMITTED:
        raise DeploymentError("only a committed cycle can be exported")
    commitment_id = str(history[-1].step.commitment_id)
    if running.config.store is None:
        raise DeploymentError("this league keeps no commitments")
    try:
        saved = commitment_store.load(running.config.store, commitment_id)
    except commitment_store.StoreError as exc:
        raise DeploymentError(f"the cycle's commitment is not kept: {exc}") from exc
    mine = [r for r in records if r.get("type") == forecast.TYPE and r["body"]["cycle_id"] == cycle_id]
    wanted = {r["id"] for r in mine} | {ref["id"] for r in mine for ref in r["refs"]}
    source = running.source_urn
    found = {seq for ident in wanted if (seq := running.ledger.find(source, ident)) is not None}
    if len(found) != len(wanted):
        raise DeploymentError("a record the cycle cites is not in the ledger")
    return commitment_id, saved, sorted(found | _whole_batch(running, records, cycle_id, parse_text(split_note(saved.note)[0]).size))


def _whole_batch(running: View, records: list[dict[str, canonical.JsonValue]], cycle_id: str, stamped_size: int) -> set[int]:
    """The ledger positions of the cycle's whole batch: its committing step, which is the last record the time-stamps cover, and the
    ``batch_size`` records before it, so that a verifier can recompute the batch root and say the bundle is complete. A cycle written before
    the committing step named its size has no such set, and its bundle holds the forecasts and what they cite as before."""
    steps = [
        r
        for r in records
        if r.get("type") == cycle.TYPE
        and cast(dict[str, canonical.JsonValue], r["body"]).get("cycle_id") == cycle_id
        and cast(dict[str, canonical.JsonValue], r["body"]).get("state") == cycle.State.COMMITTING.value
    ]
    if len(steps) != 1:
        return set()
    size = cast(dict[str, canonical.JsonValue], steps[0]["body"]).get("batch_size")
    at = running.ledger.find(running.source_urn, str(steps[0]["id"]))
    if isinstance(size, bool) or not isinstance(size, int) or at is None or not 0 < size <= at < stamped_size:
        return set()
    return set(range(at - size, at + 1))


def _stored_checkpoints(directory: Path) -> list[tuple[int, bytes]]:
    """The signed checkpoints the ledger stored (``checkpoints.jsonl``), as (size, note), smallest first. A line that cannot be read is left
    out: the reader has already refused a ledger whose file is damaged, and the verifier checks every note it is given."""
    path = directory / "checkpoints.jsonl"
    found: dict[int, bytes] = {}
    try:
        lines = path.read_bytes().splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            note = base64.b64decode(str(canonical.parse_record(line)["note"]), validate=True)
            found[parse_text(split_note(note)[0]).size] = note
        except (canonical.CanonicalError, CheckpointError, NoteError, KeyError, ValueError):
            continue
    return sorted(found.items())


def _later_part(running: View, cycle_id: str, stamped_size: int) -> bundle.Later | None:
    """What the log appended after the checkpoint the authorities stamped, for the forecasts of a cycle: their later steps (committed,
    waiting for the origin, open, ended) and the settlements that end them, proven under the newest checkpoint the ledger has signed.
    None while the log has signed no checkpoint beyond the stamped one, or none of those records is under one."""
    larger = [(size, note) for size, note in _stored_checkpoints(running.settings.directory / "ledger") if size > stamped_size]
    if not larger:
        return None
    size, note = larger[-1]
    records = scheduler.read_ledger(running.ledger)
    mine = {r["id"] for r in records if r.get("type") == forecast.TYPE and r["body"]["cycle_id"] == cycle_id}
    steps = [r for r in records if r.get("type") in (lifecycle.TYPE, settlement_record.TYPE) and r["body"]["forecast_id"] in mine]
    positions = (running.ledger.find(running.source_urn, str(r["id"])) for r in steps)
    seqs = sorted(seq for seq in positions if seq is not None and stamped_size <= seq < size)
    return bundle.Later(note, size, tuple(seqs)) if seqs else None


def _later_part_size(directory: Path) -> int:
    """The size of the later checkpoint a bundle holds (the ``to_size`` of its ``consistency.json``), or 0 when it holds none or the file
    cannot be read; an unreadable file is then replaced by a fresh export."""
    try:
        document = canonical.parse_record((directory / bundle.CONSISTENCY).read_bytes())
    except (OSError, canonical.CanonicalError):
        return 0
    size = document.get("to_size")
    return size if isinstance(size, int) and not isinstance(size, bool) else 0


def _later_part_grew(running: View, cycle_id: str, directory: Path) -> bool:
    """Whether the log now has a later checkpoint beyond the one the bundle in ``directory`` holds, so that the bundle is to be made again:
    a committed cycle never changes, but the steps written after it, and the checkpoint that covers them, come later."""
    try:
        _, saved, _ = _committed(running, cycle_id)
        later = _later_part(running, cycle_id, parse_text(split_note(saved.note)[0]).size)
    except DeploymentError:
        return False
    return later is not None and later.size > _later_part_size(directory)


def export_cycle(running: View, cycle_id: str, out: Path) -> dict[str, Any]:
    """Write the bundle of a committed cycle: its forecasts and the evidence they cite, against the checkpoint its authorities stamped, and
    the later steps of its forecasts under the newest checkpoint the log has signed, with the proof that it extends the stamped one."""
    commitment_id, saved, seqs = _committed(running, cycle_id)
    note_text, _ = split_note(saved.note)
    stamped_size = parse_text(note_text).size
    later = _later_part(running, cycle_id, stamped_size)
    bundle.export_bundle(out, running.ledger, seqs, checkpoint_note=saved.note, size=stamped_size, tokens=saved.tokens, later=later)
    return {
        "cycle": cycle_id,
        "commitment": commitment_id,
        "records": len(seqs),
        "later_records": 0 if later is None else len(later.seqs),
        "directory": str(out),
    }


def forge_cycle(running: View, cycle_id: str, out: Path) -> dict[str, Any]:
    """Make the forged copy of a committed cycle's bundle (``hoshitori.forgery``) in ``out``: the bundle, the document that says what was
    changed, and the root it is checked against. The league's own key is not used (a checkpoint it signed over a changed log would be proof
    that the league's log equivocates); a stand-in key derived from the cycle's name signs the copy, and the root is the league's own with
    that key named as the log's. The ledger is only read, and nothing of the league is written."""
    _, saved, seqs = _committed(running, cycle_id)
    origin = running.root.origin
    signer = forgery.standin_signer(origin, cycle_id)
    if out.exists() and any(out.iterdir()):
        raise DeploymentError("the output directory must be new or empty")
    try:
        demonstration = forgery.demo_root(running.settings.trusted_root.read_bytes(), origin, signer.verifier.vkey())
    except (OSError, forgery.ForgeryError) as exc:
        raise DeploymentError(f"the forgery cannot be made: {exc}") from exc
    with tempfile.TemporaryDirectory(prefix="hoshitori-forge-") as work:
        try:
            made = forgery.forge(
                running.ledger,
                signer=signer,
                origin=origin,
                note=saved.note,
                tokens=saved.tokens,
                seqs=seqs,
                cycle_id=cycle_id,
                work=Path(work) / "work",
                out=out / FORGED_BUNDLE,
                segment_records=SEGMENT_RECORDS,
            )
        except forgery.ForgeryError as exc:
            raise DeploymentError(f"the forgery cannot be made: {exc}") from exc
    (out / forgery.DOCUMENT).write_text(forgery.document(made), encoding="utf-8")
    (out / FORGED_ROOT).write_bytes(demonstration)
    return {
        "cycle": cycle_id,
        "edit": {"record": made.edit.record_id, "field": made.edit.field, "was": made.edit.was, "now": made.edit.now},
        "standin_vkey": made.standin_vkey,
        "honest_root": made.honest_root,
        "forged_root": made.forged_root,
        "directory": str(out),
    }


def _laboratory(log: Path, candles: Sequence[Path], runs: Path | None) -> lab_view.Document:
    """The Laboratory's document from its command log and the stored candle and run records, each read strictly."""
    if not log.is_file():
        raise DeploymentError(f"the Laboratory log {log} does not exist")
    try:
        book = lab.Lab(log)
        records = [lab_view.read_candles(path.read_bytes()) for path in candles]
        stored = lab_view.read_runs(runs.read_bytes()) if runs is not None else None
        return lab_view.build(book, records, stored)
    except (OSError, command_log.LogError, lab.LabError, lab_view.LabViewError) as exc:
        raise DeploymentError(f"the Laboratory cannot be shown: {exc}") from exc


def _desk_ledger_runs(settings: Settings) -> list[desk_view.DeskRun] | None:
    """The real runs of a league's own Desk, read from its ledger (opened read-only and checked against the on-demand root, whose identity
    it is kept under); none when the league has no Desk, or a Desk that has not run yet. They come before any runs from a file."""
    if not settings.desk or settings.ondemand is None or not (settings.directory / "desk" / "segments").is_dir():
        return None
    try:
        od_root = trusted_root.parse_root(settings.ondemand.trusted_root.read_bytes())
        reader = LedgerReader.open(settings.directory / "desk", root=od_root)
        kept = [(r["type"], r["body"]) for r in scheduler.read_ledger(reader) if r["type"] in desk_record.TYPES]
        return desk_view.read_file(desk_view.file_bytes(kept))  # through the strict reader: every record checked against its schema
    except (OSError, trusted_root.RootError, LedgerError, desk_view.DeskViewError) as exc:
        raise DeploymentError(f"the Desk's ledger cannot be shown: {exc}") from exc


def export_site(
    running: View,
    out: Path,
    *,
    scored: bool = False,
    status: Path | None = None,
    drills: Path | None = None,
    claims: Path | None = None,
    model_challenges: Sequence[Path] = (),
    model_sources: Sequence[Path] = (),
    forged: Path | None = None,
    desk_runs: Path | None = None,
    sweeps: Path | None = None,
    coverage: Path | None = None,
    signals: Path | None = None,
    lab_log: Path | None = None,
    lab_candles: Sequence[Path] = (),
    lab_runs: Path | None = None,
) -> dict[str, Any]:
    """Write the static documents of the interface and, beside them, the bundle of every committed cycle whose commitment is kept.

    A bundle is written whole under a temporary name and moved into place, so a half-written bundle is never mistaken for one; a bundle
    that is already there is left as it is (a committed cycle never changes). ``model_challenges`` are recorded challenges of the model arm
    (``scripts/challenge_live.py``): each is read strictly and held to this ledger before anything is written, and none is published from a
    ledger that is not intact, whose records they are about. ``forged`` is a directory made by :func:`forge_cycle`: its bundle is copied
    under ``forged/`` (never beside the honest bundles) and the page is told what was changed."""
    table = None
    if status is not None:
        try:
            table = quality.read_status(status.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, quality.QualityError) as exc:
            raise DeploymentError(f"the status file cannot be shown: {exc}") from exc
    recorded = None
    if drills is not None:
        try:
            recorded = drill.read_drills(drills.read_bytes())
        except (OSError, drill.DrillError) as exc:
            raise DeploymentError(f"the drill file cannot be shown: {exc}") from exc
    read = None
    if claims is not None:
        try:
            read = claims_file.read_claims(claims.read_bytes())
        except (OSError, claims_file.ClaimsError) as exc:
            raise DeploymentError(f"the claims file cannot be shown: {exc}") from exc
    desk: list[desk_view.DeskRun] | None = _desk_ledger_runs(running.settings)
    if desk_runs is not None:
        try:
            desk = (desk or []) + desk_view.read_file(desk_runs.read_bytes())
        except (OSError, desk_view.DeskViewError) as exc:
            raise DeploymentError(f"the Desk runs file cannot be shown: {exc}") from exc
    swept: sweep_view.Sweeps | None = None
    if sweeps is not None:
        try:
            swept = sweep_view.read(sweeps.read_text(encoding="utf-8"), _source_root(sweeps))
        except (OSError, UnicodeDecodeError, sweep_view.SweepViewError) as exc:
            raise DeploymentError(f"the sweep page cannot be shown: {exc}") from exc
    covered: coverage_view.Coverage | None = None
    if coverage is not None:
        try:
            covered = coverage_view.read(
                (coverage / "RUBRIC_MATRIX.md").read_text(encoding="utf-8"), (coverage / "CONTRACT_MATRIX.md").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, coverage_view.CoverageError) as exc:
            raise DeploymentError(f"the coverage matrices cannot be shown: {exc}") from exc
    collected_signals: dict[str, Any] | None = None
    if signals is not None:
        try:
            collected_signals = json.loads(signals.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise DeploymentError(f"the signals file {signals} cannot be shown: {exc}") from exc
        if not isinstance(collected_signals, dict) or collected_signals.get("format") != 1:
            raise DeploymentError("the signals file is not a signals document of format 1")
    laboratory: lab_view.Document | None = None
    if lab_log is not None:
        laboratory = _laboratory(lab_log, lab_candles, lab_runs)
    elif lab_candles or lab_runs is not None:
        raise DeploymentError("stored candles and runs are shown beside a Laboratory log; give the log")
    tried: list[model_challenge_file.Challenge] = []
    for path in model_challenges:
        try:
            tried.append(model_challenge_file.read(path.read_bytes()))
        except (OSError, model_challenge_file.ChallengeFileError) as exc:
            raise DeploymentError(f"the model challenge file {path} cannot be shown: {exc}") from exc
    records = scheduler.read_ledger(running.ledger) if _intact(running) else []  # a ledger not intact: its state, and none of its records
    model_runs: list[source_value.ModelRuns] = []
    for path in model_sources:  # recorded runs of a model arm with and without a source (scripts/model_source_value.py)
        try:
            runs = source_value.read_model_runs(path.read_bytes())
            source_value.check_against(runs, records)
        except (OSError, source_value.RunsError) as exc:
            raise DeploymentError(f"the model source runs {path} cannot be shown: {exc}") from exc
        model_runs.append(runs)
    challenged_model: tuple[model_challenge_file.Row, ...] | None = None
    if model_challenges and _intact(running):
        try:
            challenged_model = model_challenge_file.for_site(tried, records)
        except model_challenge_file.ChallengeFileError as exc:
            raise DeploymentError(f"the model challenges cannot be shown: {exc}") from exc
    try:
        practiced = practice_view.build(records, practice.config_for(practice.ARM, running.rules.practice_values()).costs)
    except practice_view.PracticeViewError as exc:
        raise DeploymentError(f"the practice records cannot be shown: {exc}") from exc
    now = collect.now_ms(running.clock)
    shown = view_models.build(records, running.rules, now_ms=now, scored=scored)
    cycles = cycle.rebuild(cast(list[dict[str, canonical.JsonValue]], [r for r in records if r.get("type") == cycle.TYPE]))
    bundled: list[str] = []
    listed: list[site_export.Bundle] = []
    without: list[str] = []
    (out / "bundles").mkdir(parents=True, exist_ok=True)
    for cycle_id in (c for c, h in cycles.items() if cycle.current_state(h) is cycle.State.COMMITTED):
        target = out / "bundles" / cycle_id
        if target.exists() and not _later_part_grew(running, cycle_id, target):
            bundled.append(cycle_id)
            listed.append(_listing(cycle_id, target))
            continue
        partial = out / "bundles" / f".{cycle_id}.part"
        shutil.rmtree(partial, ignore_errors=True)
        try:
            export_cycle(running, cycle_id, partial)
        except (DeploymentError, bundle.BundleError):
            shutil.rmtree(partial, ignore_errors=True)
            if target.exists():  # the bundle already there stays: the refresh failed, the old one is still a whole bundle
                bundled.append(cycle_id)
                listed.append(_listing(cycle_id, target))
            else:
                without.append(cycle_id)
            continue
        if target.exists():  # the earlier bundle is moved aside and removed once the new one is in place
            replaced = out / "bundles" / f".{cycle_id}.old"
            shutil.rmtree(replaced, ignore_errors=True)
            os.replace(target, replaced)
            os.replace(partial, target)
            shutil.rmtree(replaced, ignore_errors=True)
        else:
            os.replace(partial, target)
        bundled.append(cycle_id)
        listed.append(_listing(cycle_id, target))
    challenged: list[challenge_view.Row] = []
    near: list[challenge_view.Boundary] = []
    for cycle_id in (b.cycle_id for b in listed):
        try:
            challenged += challenge_view.rows(records, cycle_id, running.rules, running.mappings)
            near += challenge_view.boundaries(records, cycle_id, running.rules, running.mappings)
        except challenge_view.ChallengeViewError:
            continue  # a cycle whose snapshot cannot be rebuilt is left out, as a cycle whose bundle cannot be made is
    checked_claims: tuple[claim_checks.Check, ...] | None = None
    if read is not None and _intact(running):  # no check of a claim against evidence that cannot be read
        try:
            checked_claims = claim_checks.checks_for(read, move_check.held_from(records))
        except move_check.MoveCheckError as exc:
            raise DeploymentError(f"the claims cannot be checked against the ledger: {exc}") from exc
    forged_copy: site_export.ForgedCopy | None = None
    if forged is not None:
        forged_copy = _forged_copy(forged, out, bundled)
    state = ledger_state(running)
    written = site_export.write(
        out,
        site_export.documents(
            shown,
            listed,
            running.settings.trusted_root.read_bytes(),
            table,
            recorded,
            read,
            tuple(challenged),
            tuple(near),
            checked_claims,
            challenged_model,
            forged_copy,
            desk,
            practiced,
            swept,
            covered,
            collected_signals,
            lab=laboratory,
            items=today_items.items(records, desk or []),
            alerts=decision_alerts.alerts(records, practiced or ()),
            revisions=call_revisions.revisions(records),
            sources=source_value.build(records, running.rules, running.mappings, model_runs) if _intact(running) else None,
            ledger=site_export.LedgerState(state["state"], state["detail"]),
        ),
    )
    return {
        "directory": str(out),
        "files": written.files,
        "removed": written.removed,
        "bundles": bundled,
        "without_bundle": without,
        "ledger": state,
    }


def _source_root(page: Path) -> Path | None:
    """The repository a sweep page belongs to, when its sources are beside it: the page is ``docs/SWEEP.md`` of a folder that holds
    ``src/hoshitori``; with no such folder nothing can be said of whether the swept files are still the ones there."""
    root = page.resolve().parent.parent
    return root if (root / "src" / "hoshitori").is_dir() else None


def _forged_copy(source: Path, out: Path, bundled: Sequence[str]) -> site_export.ForgedCopy:
    """The forged copy made by ``forge-demo``, copied whole under ``forged/`` (a copy already there is left as it is: the same forgery is
    the same bytes), and listed. It must name a cycle whose bundle the site holds."""
    try:
        summary = forgery.read_document((source / forgery.DOCUMENT).read_bytes())
    except (OSError, forgery.ForgeryError) as exc:
        raise DeploymentError(f"the forgery cannot be shown: {exc}") from exc
    if summary.cycle_id not in bundled:
        raise DeploymentError("the forgery was made from a cycle whose bundle this site does not hold")
    held = source / FORGED_BUNDLE
    if not held.is_dir():
        raise DeploymentError("the forgery directory holds no bundle")
    try:
        root_bytes = (source / FORGED_ROOT).read_bytes()
    except OSError as exc:
        raise DeploymentError(f"the forgery cannot be shown: {exc}") from exc
    try:
        parsed = trusted_root.parse_root(root_bytes)
    except trusted_root.RootError as exc:
        raise DeploymentError(f"the forgery's root is not a trusted root: {exc}") from exc
    if [era.key.vkey() for era in parsed.eras] != [summary.standin_vkey]:
        raise DeploymentError("the forgery's root does not name its stand-in key")
    target = out / site_export.FORGED / summary.cycle_id
    if not target.exists():
        partial = out / site_export.FORGED / f".{summary.cycle_id}.part"
        shutil.rmtree(partial, ignore_errors=True)
        partial.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(held, partial)
        os.replace(partial, target)
    listed = _listing(summary.cycle_id, target)
    return site_export.ForgedCopy(summary, listed.files, listed.directories, root_bytes)


def _listing(cycle_id: str, folder: Path) -> site_export.Bundle:
    """Every file and folder of a bundle, as paths below it, in a fixed order."""
    files = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
    folders = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_dir())
    return site_export.Bundle(cycle_id, tuple(files), tuple(folders))


def make_protocol(args: Any) -> dict[str, Any]:
    """Write the protocol file of the first league from the command's arguments; returns what was written and its hash."""
    try:
        root = trusted_root.parse_root(args.root.read_bytes())
        mappings = {}
        for item in args.mapping:
            arm, _, file = item.partition("=")
            if not arm or not file:
                raise DeploymentError("a mapping is ARM=FILE")
            mappings[arm] = rules_arm.load(Path(file).read_bytes()).sha256
        barrier = {}
        for item in args.barrier_mapping:
            arm, _, file = item.partition("=")
            if not arm or not file:
                raise DeploymentError("a barrier mapping is ARM=FILE")
            barrier[arm] = barrier_rates.load(Path(file).read_bytes()).sha256
        model_pins: list[tuple[str, str, tuple[tuple[int, int], ...]]] = []
        if args.models is not None:
            versions = model_settings.load(args.models).versions()
            cutoffs: dict[str, tuple[tuple[int, int], ...]] = {}
            for item in args.cutoff:
                arm, _, spec = item.partition("=")
                try:
                    cutoffs[arm] = tuple(
                        (int(hours), int(ms)) for hours, _, ms in (part.partition(":") for part in spec.split(",") if part)
                    )
                except ValueError:
                    raise DeploymentError("a cutoff is ARM=HOURS:MS,HOURS:MS") from None
                if not cutoffs[arm]:
                    raise DeploymentError("a cutoff is ARM=HOURS:MS,HOURS:MS")
            if set(cutoffs) != set(versions):
                raise DeploymentError("each model arm in the model settings has its cutoffs, and no other arm has")
            model_pins = [(arm, versions[arm], cutoffs[arm]) for arm in versions]  # the protocol puts them in order
        elif args.cutoff:
            raise DeploymentError("cutoffs are given with the model settings they belong to")
        for source in args.optional_source:
            _readable(source)
        rules = protocol.standard(
            name=args.name,
            start_ms=args.start_ms,
            every_ms=args.every_minutes * 60_000,
            root_version=root.version,
            root_sha256=root.sha256,
            mappings=mappings,
            barrier_mappings=barrier,
            model_arms=tuple(model_pins),
            watchlist=tuple(part.strip().upper() for part in args.watchlist.split(",") if part.strip()),
            primary_venue=args.primary,
            second_venue=args.second,
            optional_sources=tuple(args.optional_source),
        )
    except (OSError, trusted_root.RootError, rules_arm.RulesError, protocol.ProtocolError, model_settings.ModelSettingsError) as exc:
        raise DeploymentError(f"the protocol cannot be made: {exc}") from exc
    if args.out.exists():
        raise DeploymentError("a protocol file is never overwritten")
    args.out.write_bytes(protocol.dump(rules))
    return {"file": str(args.out), "sha256": rules.sha256, "slots_from_ms": rules.schedule_start_ms, "root_sha256": root.sha256}
