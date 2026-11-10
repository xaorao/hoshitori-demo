"""The cycle's candidate manifest: which assets the cycle looks at, and why, as a pure function of what was stored.

Contract sections 6 and 25 and ``DISCOVERY_MANIFEST_SPEC`` are the rules. The manifest is built after the discovery
collection and before any evidence is collected for a candidate, from three things only: the stored RYO scan answer, the
stored log of event triggers and the watchlist, together with the rules of the protocol. Nothing is read from a clock or a
network; the same inputs and the same rules give the same manifest and the same hash, so a replay from the stored captures
can be compared with what was sealed.

* **Three routes.** ``RYO_SCAN`` takes the first N rows of the scan by RYO's own rank; ``EVENT`` takes qualified triggers (a
  primary document or at least two origin families, naming an asset that can be settled), at most E of them; ``WATCHLIST``
  takes a list whose hash the protocol fixed.
* **One candidate per asset.** An asset reached by two routes is one entry carrying both. Every proposal is in the manifest
  with its outcome: excluded ones are listed with a reason from a closed list, never silently dropped, and no free text
  exists.
* **The union is what every arm gets.** The entries with ``INCLUDED`` are the eligible union; nothing outside it is scored.
* **A route can fail.** If the scan or the trigger log could not be read, the route is ``FAILED_EVIDENCE`` and contributes
  nothing, and the other routes carry on.
* **Choices made here.** The rank cutoff is applied to the scan as RYO ranked it, before any check of whether an asset can be
  settled, so a candidate that cannot be settled does not let a lower-ranked one in (the cutoff does not depend on which
  assets happen to have an oracle). Duplicates are removed first: of two rows for one asset the better-ranked stays; of two
  triggers with one rule and one asset the earlier stays.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

from hoshitori import canonical, envelope
from hoshitori.evidence import Family
from hoshitori.ids import natural_id
from hoshitori.timeline import from_ms
from hoshitori.timeutil import format_rfc3339_ms

TYPE: Final = "manifest"
TYPE_VERSION: Final = 1
DEFAULT_SCAN_N: Final = 10
DEFAULT_EVENT_CAP: Final = 5
MAX_ROWS: Final = 1000
MAX_INSTANT_MS: Final = 253_402_300_799_999


class ManifestError(ValueError):
    """Inputs or rules from which no manifest can be built."""


class Route(StrEnum):
    RYO_SCAN = "RYO_SCAN"
    EVENT = "EVENT"
    WATCHLIST = "WATCHLIST"


class Exclusion(StrEnum):
    BELOW_RANK_CUTOFF = "below_rank_cutoff"
    DUPLICATE = "duplicate"
    CAPPED = "capped"
    INELIGIBLE_ASSET = "ineligible_asset"
    NO_ORACLE = "no_oracle"
    TRIGGER_NOT_QUALIFIED = "trigger_not_qualified"


@dataclass(frozen=True)
class Resolved:
    """What the asset registry says about a symbol: its asset, whether the mapping is unambiguous, and whether it can be settled."""

    asset_id: str | None
    unambiguous: bool
    has_oracle: bool


Resolver = Callable[[str], Resolved]


@dataclass(frozen=True)
class ScanRow:
    rank: int  # RYO's own rank in the answer, 1 for the best
    symbol: str


@dataclass(frozen=True)
class Trigger:
    trigger_id: str
    rule: str
    rule_version: str
    symbol: str
    primary: bool  # rests on a primary document (an exchange's own notice, a status incident, a project's own post)
    families: frozenset[Family]
    first_seen_at_ms: int
    impact: Decimal  # research-decision impact, for ordering under the cap

    def qualified(self) -> bool:
        return self.primary or len({f for f in self.families if f.is_origin}) >= 2


@dataclass(frozen=True)
class Capture:
    capture_id: str
    sha256: str


@dataclass(frozen=True)
class Rules:
    scan_rule_version: str
    scan_rule_hash: str
    scan_n: int
    event_rule_version: str
    event_rule_hash: str
    event_cap: int
    watchlist_config_hash: str


@dataclass(frozen=True)
class Failed:
    """A route whose input could not be read: it contributes nothing and the manifest says why."""

    reason: str


@dataclass(frozen=True)
class Inputs:
    cycle_id: str
    protocol_hash: str
    discovery_at_ms: int
    sealed_at_ms: int
    scan: tuple[ScanRow, ...] | Failed
    scan_capture: Capture | None
    triggers: tuple[Trigger, ...] | Failed
    trigger_capture: Capture | None
    watchlist: tuple[str, ...]


@dataclass(frozen=True)
class RouteEntry:
    route: Route
    included: bool
    reason: Exclusion | None
    rank: int | None
    trigger_id: str | None
    first_seen_at_ms: int | None
    dedupe_key: str


@dataclass(frozen=True)
class Entry:
    candidate_id: str
    asset_id: str | None
    symbol: str
    routes: tuple[RouteEntry, ...]

    @property
    def included(self) -> bool:
        return any(r.included for r in self.routes)

    @property
    def tags(self) -> tuple[Route, ...]:
        """The routes that put this candidate in the cycle."""
        return tuple(r.route for r in self.routes if r.included)

    @property
    def reason(self) -> Exclusion | None:
        """Why a candidate that no route included was left out: the reason of the first route in the order of the routes."""
        return None if self.included else self.routes[0].reason


@dataclass(frozen=True)
class Manifest:
    inputs: Inputs
    rules: Rules
    entries: tuple[Entry, ...]

    def union(self) -> tuple[Entry, ...]:
        """The eligible union: every arm of the cycle receives exactly these candidates."""
        return tuple(entry for entry in self.entries if entry.included)

    def route_status(self) -> dict[str, str]:
        return {
            Route.RYO_SCAN.value: "FAILED_EVIDENCE" if isinstance(self.inputs.scan, Failed) else "OK",
            Route.EVENT.value: "FAILED_EVIDENCE" if isinstance(self.inputs.triggers, Failed) else "OK",
            Route.WATCHLIST.value: "OK",
        }

    def body(self) -> dict[str, canonical.JsonValue]:
        def stamp(ms: int | None) -> canonical.JsonValue:
            return None if ms is None else format_rfc3339_ms(from_ms(ms))

        used: list[canonical.JsonValue] = []
        for kind, capture in (("ryo_scan_response", self.inputs.scan_capture), ("event_trigger_log", self.inputs.trigger_capture)):
            if capture is not None:
                used.append({"kind": kind, "capture_id": capture.capture_id, "sha256": capture.sha256})
        failures: dict[str, canonical.JsonValue] = {}
        if isinstance(self.inputs.scan, Failed):
            failures[Route.RYO_SCAN.value] = self.inputs.scan.reason
        if isinstance(self.inputs.triggers, Failed):
            failures[Route.EVENT.value] = self.inputs.triggers.reason
        entries: list[canonical.JsonValue] = []
        for entry in self.entries:
            routes: list[canonical.JsonValue] = []
            for r in entry.routes:
                routes.append(
                    {
                        "route": r.route.value,
                        "decision": "INCLUDED" if r.included else "EXCLUDED",
                        "reason": None if r.reason is None else r.reason.value,
                        "rank": r.rank,
                        "trigger_id": r.trigger_id,
                        "first_seen_at": stamp(r.first_seen_at_ms),
                        "dedupe_key": r.dedupe_key,
                    }
                )
            entries.append(
                {
                    "candidate_id": entry.candidate_id,
                    "asset_id": entry.asset_id,
                    "symbol": entry.symbol,
                    "inclusion": "INCLUDED" if entry.included else "EXCLUDED",
                    "exclusion": None if entry.reason is None else entry.reason.value,
                    "routes": routes,
                }
            )
        rules = self.rules
        return {
            "cycle_id": self.inputs.cycle_id,
            "protocol_hash": self.inputs.protocol_hash,
            "discovery_at": format_rfc3339_ms(from_ms(self.inputs.discovery_at_ms)),
            "sealed_at": format_rfc3339_ms(from_ms(self.inputs.sealed_at_ms)),
            "inputs": used,
            "rules": {
                "ryo_scan": {"rule_version": rules.scan_rule_version, "rule_hash": rules.scan_rule_hash, "n": rules.scan_n},
                "event": {"rule_version": rules.event_rule_version, "rule_hash": rules.event_rule_hash, "cap": rules.event_cap},
                "watchlist": {"config_hash": rules.watchlist_config_hash},
            },
            "routes": dict(self.route_status()),
            "failures": failures,
            "entries": entries,
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical.dumps(self.body())).hexdigest()


# --- building ---------------------------------------------------------------------------------------------------------------------------
def _text(value: object, what: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise ManifestError(f"{what} is a non-empty string of at most 200 characters")
    try:
        canonical.dumps({"v": value})
    except canonical.CanonicalError as exc:
        raise ManifestError(f"{what} is not valid canonical text: {exc}") from exc


def _hash(value: object, what: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ManifestError(f"{what} is 64 lower-case hexadecimal characters")


def _instant(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_INSTANT_MS:
        raise ManifestError(f"{what} is a whole number of milliseconds up to the end of the year 9999")
    return value


def _count(value: object, what: str, low: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= MAX_ROWS:
        raise ManifestError(f"{what} is a whole number from {low} to {MAX_ROWS}")


def _check_inputs(inputs: Inputs, rules: Rules) -> None:
    _text(inputs.cycle_id, "the cycle id")
    _hash(inputs.protocol_hash, "the protocol hash")
    discovery, sealed = _instant(inputs.discovery_at_ms, "discovery_at"), _instant(inputs.sealed_at_ms, "sealed_at")
    if discovery > sealed:
        raise ManifestError("the manifest is sealed no earlier than the discovery collection")
    _text(rules.scan_rule_version, "the scan rule version")
    _hash(rules.scan_rule_hash, "the scan rule hash")
    _count(rules.scan_n, "the scan cutoff", 1)
    _text(rules.event_rule_version, "the event rule version")
    _hash(rules.event_rule_hash, "the event rule hash")
    _count(rules.event_cap, "the event cap", 0)
    _hash(rules.watchlist_config_hash, "the watchlist configuration hash")
    for capture, rows, what in ((inputs.scan_capture, inputs.scan, "scan"), (inputs.trigger_capture, inputs.triggers, "trigger log")):
        if capture is not None:
            _text(capture.capture_id, f"the {what} capture id")
            _hash(capture.sha256, f"the {what} capture hash")
        if not isinstance(rows, Failed) and capture is None:
            raise ManifestError(f"a {what} that was read comes with the capture it was read from")
        if isinstance(rows, Failed):
            _text(rows.reason, f"the {what} failure reason")
    if not isinstance(inputs.scan, Failed):
        if len(inputs.scan) > MAX_ROWS:
            raise ManifestError(f"a scan has at most {MAX_ROWS} rows")
        ranks = [row.rank for row in inputs.scan]
        if any(isinstance(r, bool) or not isinstance(r, int) or r < 1 for r in ranks) or len(set(ranks)) != len(ranks):
            raise ManifestError("the rows of a scan have distinct ranks from 1")
        for row in inputs.scan:
            _text(row.symbol, "a scan symbol")
    if not isinstance(inputs.triggers, Failed):
        if len(inputs.triggers) > MAX_ROWS:
            raise ManifestError(f"a trigger log has at most {MAX_ROWS} triggers")
        if len({t.trigger_id for t in inputs.triggers}) != len(inputs.triggers):
            raise ManifestError("trigger ids are distinct")
        for trigger in inputs.triggers:
            for value, what in (
                (trigger.trigger_id, "a trigger id"),
                (trigger.rule, "a trigger rule"),
                (trigger.rule_version, "a rule version"),
                (trigger.symbol, "a trigger symbol"),
            ):
                _text(value, what)
            _instant(trigger.first_seen_at_ms, "first_seen_at")
            if not isinstance(trigger.impact, Decimal) or not trigger.impact.is_finite():
                raise ManifestError("a trigger's impact is a finite decimal")
            if not isinstance(trigger.primary, bool):
                raise ManifestError("a trigger is primary or it is not")
    if len(inputs.watchlist) > MAX_ROWS:
        raise ManifestError(f"a watchlist has at most {MAX_ROWS} symbols")
    for symbol in inputs.watchlist:
        _text(symbol, "a watchlist symbol")


@dataclass(frozen=True)
class _Proposal:
    route: Route
    symbol: str
    key: str
    asset_id: str | None
    eligibility: Exclusion | None
    rank: int | None
    trigger: Trigger | None
    dedupe_key: str


def _proposal(route: Route, symbol: str, resolve: Resolver, rule: str, rank: int | None, trigger: Trigger | None) -> _Proposal:
    resolved = resolve(symbol)
    why: Exclusion | None = None
    if resolved.asset_id is None or not resolved.unambiguous:
        key, why = f"unresolved:{symbol.upper()}", Exclusion.INELIGIBLE_ASSET
    else:
        key = resolved.asset_id
        if not resolved.has_oracle:
            why = Exclusion.NO_ORACLE
    return _Proposal(route, symbol, key, resolved.asset_id, why, rank, trigger, f"{rule}:{key}")


def _entry(proposal: _Proposal, reason: Exclusion | None) -> RouteEntry:
    trigger = proposal.trigger
    return RouteEntry(
        proposal.route,
        reason is None,
        reason,
        proposal.rank,
        None if trigger is None else trigger.trigger_id,
        None if trigger is None else trigger.first_seen_at_ms,
        proposal.dedupe_key,
    )


def _scan_route(rows: Sequence[ScanRow], rules: Rules, resolve: Resolver) -> list[tuple[_Proposal, Exclusion | None]]:
    seen: set[str] = set()
    out: list[tuple[_Proposal, Exclusion | None]] = []
    for row in sorted(rows, key=lambda r: r.rank):
        proposal = _proposal(Route.RYO_SCAN, row.symbol, resolve, "ryo_scan", row.rank, None)
        if proposal.key in seen:
            out.append((proposal, Exclusion.DUPLICATE))
        elif row.rank > rules.scan_n:
            seen.add(proposal.key)
            out.append((proposal, Exclusion.BELOW_RANK_CUTOFF))
        else:
            seen.add(proposal.key)
            out.append((proposal, proposal.eligibility))
    return out


def _event_route(triggers: Sequence[Trigger], rules: Rules, resolve: Resolver) -> list[tuple[_Proposal, Exclusion | None]]:
    out: list[tuple[_Proposal, Exclusion | None]] = []
    standing: list[tuple[Trigger, _Proposal]] = []
    for trigger in sorted(triggers, key=lambda t: (t.first_seen_at_ms, t.trigger_id)):
        proposal = _proposal(Route.EVENT, trigger.symbol, resolve, trigger.rule, None, trigger)
        if not trigger.qualified():
            out.append((proposal, Exclusion.TRIGGER_NOT_QUALIFIED))
        elif proposal.eligibility is not None:
            out.append((proposal, proposal.eligibility))
        elif any(other.dedupe_key == proposal.dedupe_key for _, other in standing):
            out.append((proposal, Exclusion.DUPLICATE))
        else:
            standing.append((trigger, proposal))
    ranked = sorted(standing, key=lambda pair: (-pair[0].impact, pair[0].first_seen_at_ms, pair[0].trigger_id))
    for position, (_, proposal) in enumerate(ranked):
        out.append((proposal, None if position < rules.event_cap else Exclusion.CAPPED))
    return out


def _watch_route(symbols: Sequence[str], resolve: Resolver) -> list[tuple[_Proposal, Exclusion | None]]:
    seen: set[str] = set()
    out: list[tuple[_Proposal, Exclusion | None]] = []
    for symbol in symbols:
        proposal = _proposal(Route.WATCHLIST, symbol, resolve, "watchlist", None, None)
        if proposal.key in seen:
            out.append((proposal, Exclusion.DUPLICATE))
        else:
            seen.add(proposal.key)
            out.append((proposal, proposal.eligibility))
    return out


def build(inputs: Inputs, rules: Rules, resolve: Resolver) -> Manifest:
    """The manifest of a cycle: a pure function of the stored inputs, the protocol's rules and the registry's answers."""
    _check_inputs(inputs, rules)
    decided: list[tuple[_Proposal, Exclusion | None]] = []
    if not isinstance(inputs.scan, Failed):
        decided += _scan_route(inputs.scan, rules, resolve)
    if not isinstance(inputs.triggers, Failed):
        decided += _event_route(inputs.triggers, rules, resolve)
    decided += _watch_route(inputs.watchlist, resolve)
    groups: dict[str, list[tuple[_Proposal, Exclusion | None]]] = {}
    for proposal, reason in decided:
        groups.setdefault(proposal.key, []).append((proposal, reason))
    entries: list[Entry] = []
    for key, members in groups.items():
        first = members[0][0]
        entries.append(
            Entry(
                natural_id("cd", {"cycle": inputs.cycle_id, "key": key}),
                None if key.startswith("unresolved:") else key,
                first.symbol,
                tuple(_entry(proposal, reason) for proposal, reason in members),
            )
        )
    return Manifest(inputs, rules, tuple(sorted(entries, key=lambda e: e.candidate_id)))


# --- the record -------------------------------------------------------------------------------------------------------------------------
def to_record(manifest: Manifest, source_urn: str, created_at_ms: int) -> dict[str, canonical.JsonValue]:
    """The record a producer submits to the ledger; it cannot be made before the manifest was sealed."""
    if _instant(created_at_ms, "the creation time") < manifest.inputs.sealed_at_ms:
        raise ManifestError("a manifest is recorded at or after the time it was sealed")
    record: dict[str, canonical.JsonValue] = {
        "spec": envelope.SPEC,
        "type": TYPE,
        "type_version": TYPE_VERSION,
        "id": natural_id("mf", {"cycle": manifest.inputs.cycle_id}),
        "source": source_urn,
        "created_at": format_rfc3339_ms(from_ms(created_at_ms)),
        "body": manifest.body(),
        "refs": [],
        "privacy": "public",
    }
    try:
        envelope.validate_producer_record(record)
    except envelope.EnvelopeError as exc:
        raise ManifestError(f"the record does not satisfy the envelope: {exc}") from exc
    return record


def matches(record: dict[str, canonical.JsonValue], rebuilt: Manifest) -> bool:
    """Whether a sealed manifest record is exactly what its stored inputs and rules give: the replay test, and the tamper test."""
    return record.get("type") == TYPE and canonical.dumps({"b": record.get("body")}) == canonical.dumps({"b": rebuilt.body()})
