"""Event triggers that come from the asset registry: an instrument that appears, disappears or changes status on a venue.

``DISCOVERY_MANIFEST_SPEC`` section 3 names the registry diff as a trigger source and ``REGISTRY_NEW_INSTRUMENT`` as a rule.
Three rules are built here, each with a version, because a rule is code and its version is part of the protocol:

* ``REGISTRY_NEW_INSTRUMENT``: a venue's accepted list contains an instrument the previous accepted list did not.
* ``REGISTRY_INSTRUMENT_REMOVED``: an instrument the previous accepted list had is gone.
* ``REGISTRY_STATUS_CHANGED``: an instrument is still listed and its trading status moved (for example to ``HALTED`` or
  ``DELISTED``).

Rules that are read off the page are not built: the Binance announcement feed is not part of the documented API and its terms
are not checked, so ``LISTING_ANNOUNCED`` and ``DELISTING_ANNOUNCED`` wait for a verified source.

* **A baseline is not news.** The first accepted list of a venue and scope adds every instrument it holds. Those are the
  starting state, not events, and produce no trigger.
* **A rejected list says nothing.** It produces no instrument events, so it produces no trigger.
* **Knowledge time is first-seen time.** A trigger is first seen when the registry learned of the change; only changes inside
  the window ``(after, until]`` count, so a change after the discovery collection cannot enter a cycle that has already
  sealed its manifest.
* **A venue's own list is a primary record.** The change is the venue's own statement about its own market, so the trigger
  rests on a primary document and one venue is enough. Its origin family is the venue's operator: two venues of one operator
  are one source.
* **Impact is not decided here.** The order under the cap is the research-decision impact, which the ranking engine owns; the
  caller passes it in as a function of the change. ``equal_impact`` is the neutral choice, and the cap then falls back to the
  earliest first-seen time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from hoshitori.evidence import Family, FamilyKind
from hoshitori.ids import natural_id
from hoshitori.manifest import Trigger
from hoshitori.registry import Registry, canonical_symbol, instrument_from_record, operator_of
from hoshitori.timeline import to_ms
from hoshitori.timeutil import parse_rfc3339
from hoshitori.venue_data import Instrument, InstrumentStatus, token_symbol

NEW: Final = "REGISTRY_NEW_INSTRUMENT"
REMOVED: Final = "REGISTRY_INSTRUMENT_REMOVED"
STATUS: Final = "REGISTRY_STATUS_CHANGED"
RULE_VERSION: Final = "1"
MAX_INSTANT_MS: Final = 253_402_300_799_999


class TriggerError(ValueError):
    """A window that is not a window, or an impact that is not a number."""


@dataclass(frozen=True)
class Change:
    """One change to a venue's list, as the impact function sees it."""

    rule: str
    venue: str
    scope: str
    instrument: Instrument
    symbol: str
    known_at_ms: int
    previous_status: InstrumentStatus | None  # for a status change; ``None`` otherwise


Impact = Callable[[Change], Decimal]


def equal_impact(change: Change) -> Decimal:
    """Every change counts the same, so the cap keeps the earliest."""
    return Decimal(1)


def _instant(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_INSTANT_MS:
        raise TriggerError(f"{what} is a whole number of milliseconds up to the end of the year 9999")
    return value


def _symbol(instrument: Instrument) -> str:
    return canonical_symbol(token_symbol(instrument.base_raw, instrument.multiplier))


def _trigger(change: Change, impact: Impact) -> Trigger:
    weight = impact(change)
    if not isinstance(weight, Decimal) or not weight.is_finite():
        raise TriggerError("an impact is a finite decimal")
    ident = natural_id(
        "tg",
        {"rule": change.rule, "venue": change.venue, "instrument": change.instrument.instrument_id, "known_at_ms": change.known_at_ms},
    )
    family = Family(FamilyKind.EXCHANGE, operator_of(change.venue))
    return Trigger(ident, change.rule, RULE_VERSION, change.symbol, True, frozenset({family}), change.known_at_ms, weight)


def registry_changes(registry: Registry, *, after_ms: int, until_ms: int) -> tuple[Change, ...]:
    """The changes to the venues' lists that the registry's records hold, known in ``(after_ms, until_ms]``, earliest first."""
    start, end = _instant(after_ms, "after"), _instant(until_ms, "until")
    if start > end:
        raise TriggerError("the window starts no later than it ends")
    known: dict[str, Instrument] = {}
    baselined: set[tuple[str, str]] = set()
    found: list[Change] = []
    for record in registry.records():
        known_ms = to_ms(parse_rfc3339(record["known_at"]))
        events: list[dict[str, Any]] = record["events"]
        header = next((e for e in events if e["t"] == "snapshot"), None)
        accepted = header is not None and header["outcome"] == "accepted"
        key = None if header is None else (header["venue"], header["scope"])
        baseline = accepted and key not in baselined
        if baseline and key is not None:
            baselined.add(key)
        for event in events:
            kind = event["t"]
            if kind in ("added", "changed"):
                instrument = instrument_from_record(event["instrument"])
                before = known.get(instrument.instrument_id)
                known[instrument.instrument_id] = instrument
                if baseline or not start < known_ms <= end:
                    continue
                if kind == "added":
                    found.append(Change(NEW, instrument.venue, event["scope"], instrument, _symbol(instrument), known_ms, None))
                elif before is not None and before.status is not instrument.status:
                    found.append(Change(STATUS, instrument.venue, event["scope"], instrument, _symbol(instrument), known_ms, before.status))
            elif kind == "removed":
                gone = known.pop(event["instrument_id"])  # the registry only removes what an accepted list held
                if start < known_ms <= end:
                    found.append(Change(REMOVED, gone.venue, event["scope"], gone, _symbol(gone), known_ms, None))
    return tuple(found)


def registry_triggers(registry: Registry, *, after_ms: int, until_ms: int, impact: Impact) -> tuple[Trigger, ...]:
    """The triggers the registry's records hold for changes known in ``(after_ms, until_ms]``, earliest first."""
    changes = registry_changes(registry, after_ms=after_ms, until_ms=until_ms)
    found = [_trigger(change, impact) for change in changes]
    return tuple(sorted(found, key=lambda t: (t.first_seen_at_ms, t.trigger_id)))
