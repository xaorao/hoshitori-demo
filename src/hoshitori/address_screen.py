"""How the league reads the answer of the address-flag skill (``skills/address-flag-check``), and what evidence it makes of it.

The skill answers one question about one address: does the US Treasury's OFAC sanctions list name it, and has the USDT or USDC issuer frozen
it? It never says an address is safe; not being listed is "no flag found", with how many sources were read. This module reads the skill's
envelope as strictly as the writer wrote it, so an envelope that contradicts itself is refused and never half-read:

* the tool, the schema version and the members are exactly the skill's; the chain and the address are the ones that were asked about;
* the verdict follows from the counts (flagged exactly when some source lists the address; no flag found only when every source answered and
  none lists it; no verdict only when no source answered), the counts follow from the list of checks, and a status follows from both;
* a source is named once, and has a kind and a state from the closed lists.

Evidence is made of the sources that answered, one item each, in the order the skill gave them. A source that did not answer leaves no item:
an absence of evidence is never evidence of safety. The two issuer freezes read one chain, so they are one origin family (``CHAIN``); the
sanctions list is another (``PRIMARY_DOC``). The league keeps the skill at arm's length: it reads the envelope contract and imports
nothing of the skill, so it runs with or without the skill installed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from hoshitori.capture import RawCapture
from hoshitori.evidence import EvidenceError, EvidenceItem, Family, FamilyKind, FieldValue, Kind, Redistribution
from hoshitori.timeline import to_ms
from hoshitori.timeutil import TimeError, parse_rfc3339

TOOL: Final = "address_flag_check"
PROVIDER: Final = "address-flag"  # the gateway provider the screen is asked of
CHAINS: Final = ("ethereum", "arbitrum", "base", "bsc", "optimism", "polygon", "tron", "bitcoin")  # the chains the skill covers
SCHEMA_VERSION: Final = "1.0"
ADAPTER_VERSION: Final = "address-screen-1"
STATUSES: Final = ("ok", "partial", "unavailable")
DATA_MODES: Final = ("live", "mixed", "simulated", "unknown")
VERDICTS: Final = ("flagged", "no_flag_found", "incomplete")
KINDS: Final = ("sanctions_list", "issuer_freeze")
STATES: Final = ("listed", "not_listed", "unavailable")
READINGS: Final = ("FLAGGED", "NO_FLAG_FOUND", "INCOMPLETE", "NOT_SCREENED")
MEMBERS: Final = frozenset(
    {"schema_version", "tool", "status", "data_mode", "as_of", "request", "data", "summary", "availability", "warnings"}
)
DATA_MEMBERS: Final = frozenset({"address", "verdict", "counts", "checks", "not_covered", "chain_facts", "sent_to", "source_calls"})
COUNTS: Final = ("in_scope", "listed", "not_listed", "stale", "unavailable")
_SOURCE_ID: Final = re.compile(r"[a-z0-9_]{1,64}")
_CHAIN: Final = re.compile(r"[a-z0-9_]{1,32}")
_ADDRESS: Final = re.compile(r"[A-Za-z0-9]{8,128}")


class ScreenError(ValueError):
    """An answer that is not the skill's, or contradicts itself, or answers about another chain or address than the one asked about."""


@dataclass(frozen=True)
class Source:
    source_id: str
    kind: str
    state: str
    stale: bool


@dataclass(frozen=True)
class Screen:
    chain: str
    address: str  # as the skill normalised it (lower case for a hexadecimal address)
    status: str
    data_mode: str
    as_of: str
    verdict: str | None
    counts: dict[str, int]
    sources: tuple[Source, ...]

    @property
    def hazard(self) -> bool:
        """Some source lists the address. A listing counts whatever the other sources did, and whether the list was fresh."""
        return self.verdict == "flagged"


def reading(screen: Screen) -> str:
    """The one word a run records for a screen: FLAGGED, NO_FLAG_FOUND, INCOMPLETE (some source did not answer), NOT_SCREENED (none did)."""
    if screen.verdict == "flagged":
        return "FLAGGED"
    if screen.verdict == "no_flag_found":
        return "NO_FLAG_FOUND"
    if screen.verdict == "incomplete":
        return "INCOMPLETE"
    return "NOT_SCREENED"


def _fail(text: str) -> ScreenError:
    return ScreenError(text)


def _whole(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(f"the counts: {what} is a whole number, 0 or more")
    return value


def _same_address(asked: str, found: str) -> bool:
    return asked.lower() == found.lower() if asked.startswith("0x") else asked == found


def read_screen(envelope: object, *, chain: str, address: str) -> Screen:
    """The screen an envelope tells of, for the chain and the address that were asked about, or why it is refused."""
    if isinstance(envelope, (bytes, bytearray)):
        try:
            envelope = json.loads(bytes(envelope).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _fail("the answer is not readable JSON") from exc
    if not isinstance(envelope, Mapping):
        raise _fail("the answer is one JSON object")
    if _CHAIN.fullmatch(chain) is None or _ADDRESS.fullmatch(address) is None:
        raise _fail("the chain and the address asked about are plain words")
    if set(envelope) != MEMBERS:
        raise _fail(f"the members of the envelope are {sorted(MEMBERS)}")
    if envelope["tool"] != TOOL:
        raise _fail(f"the tool is {TOOL}")
    if envelope["schema_version"] != SCHEMA_VERSION:
        raise _fail(f"the schema version is {SCHEMA_VERSION}")
    status, data_mode, as_of = envelope["status"], envelope["data_mode"], envelope["as_of"]
    if status not in STATUSES:
        raise _fail(f"the status is one of {STATUSES}")
    if data_mode not in DATA_MODES:
        raise _fail(f"the data_mode is one of {DATA_MODES}")
    if not isinstance(as_of, str):
        raise _fail("as_of is a time")
    try:
        parse_rfc3339(as_of)
    except TimeError as exc:
        raise _fail("as_of is a time") from exc
    data: Any = envelope["data"]
    if not isinstance(data, Mapping) or set(data) != DATA_MEMBERS:
        raise _fail(f"the members of the data are {sorted(DATA_MEMBERS)}")
    named = data["address"]
    if not isinstance(named, Mapping) or named.get("chain") != chain:
        raise _fail("the answer is about another chain than the one asked about")
    normalized = named.get("normalized")
    if not isinstance(normalized, str) or not _same_address(address, normalized):
        raise _fail("the answer is about another address than the one asked about")
    verdict = data["verdict"]
    if verdict is not None and verdict not in VERDICTS:
        raise _fail(f"the verdict is one of {VERDICTS} or none")
    sources = _sources(data["checks"])
    counts = _counts(data["counts"], sources)
    _consistent(status, verdict, sources)
    return Screen(chain, normalized, status, data_mode, as_of, verdict, counts, sources)


def _sources(checks: object) -> tuple[Source, ...]:
    if not isinstance(checks, list) or not checks:
        raise _fail("the checks are a list of at least one source")
    out: list[Source] = []
    for check in checks:
        if not isinstance(check, Mapping):
            raise _fail("a check is an object")
        source_id = check.get("source_id")
        if not isinstance(source_id, str) or _SOURCE_ID.fullmatch(source_id) is None or any(s.source_id == source_id for s in out):
            raise _fail("a source is named once, in plain letters")
        kind, state, stale = check.get("kind"), check.get("state"), check.get("stale")
        if kind not in KINDS:
            raise _fail(f"the kind of a source is one of {KINDS}")
        if state not in STATES:
            raise _fail(f"the state of a source is one of {STATES}")
        if not isinstance(stale, bool):
            raise _fail("a source is stale or it is not: a boolean")
        out.append(Source(source_id, str(kind), str(state), stale))
    return tuple(out)


def _counts(raw: object, sources: tuple[Source, ...]) -> dict[str, int]:
    if not isinstance(raw, Mapping) or set(raw) != set(COUNTS):
        raise _fail(f"the counts are {COUNTS}")
    counts = {name: _whole(raw[name], name) for name in COUNTS}
    held = {
        "in_scope": len(sources),
        "listed": sum(1 for s in sources if s.state == "listed"),
        "not_listed": sum(1 for s in sources if s.state == "not_listed"),
        "stale": sum(1 for s in sources if s.stale),
        "unavailable": sum(1 for s in sources if s.state == "unavailable"),
    }
    if counts != held:
        raise _fail("the counts do not follow from the checks")
    return counts


def _consistent(status: str, verdict: str | None, sources: tuple[Source, ...]) -> None:
    """The skill's own decision rule, applied again to the sources it listed: a verdict or a status that they do not give is not believed.
    A source that lists the address makes it flagged, fresh or not; with none, the verdict needs every source to have answered and none
    to be stale, and the status is ok only then."""
    fresh = sum(1 for s in sources if s.state in ("listed", "not_listed") and not s.stale)
    if any(s.state == "listed" for s in sources):
        want = "flagged"
    elif fresh == 0:
        want = None
    elif fresh == len(sources):
        want = "no_flag_found"
    else:
        want = "incomplete"
    if verdict != want:
        raise _fail("the verdict does not follow from the checks")
    expected = "ok" if fresh == len(sources) else ("unavailable" if want is None else "partial")
    if status != expected:
        raise _fail("the status does not follow from the checks")


def screen_items(capture: RawCapture, screen: Screen) -> tuple[EvidenceItem, ...]:
    """One observation item for each source that answered, in the order the skill gave them; none for a source that did not."""
    received = to_ms(capture.received_at)
    event = to_ms(parse_rfc3339(screen.as_of))
    items: list[EvidenceItem] = []
    for source in screen.sources:
        if source.state == "unavailable":
            continue
        fields: dict[str, FieldValue] = {
            "state": source.state,
            "kind": source.kind,
            "stale": source.stale,
            "address": screen.address,
            "chain": screen.chain,
        }
        try:
            family = Family(FamilyKind.PRIMARY_DOC, "ofac") if source.kind == "sanctions_list" else Family(FamilyKind.CHAIN, screen.chain)
            items.append(
                EvidenceItem(
                    adapter_version=ADAPTER_VERSION,
                    path=f"address_flag/{source.source_id}",
                    kind=Kind.OBSERVATION,
                    source=capture.source,
                    origin=family,
                    received_at_ms=received,
                    known_at_ms=received,
                    fields=tuple(sorted(fields.items())),
                    capture_id=capture.capture_id,
                    raw_sha256=capture.body_sha256,
                    tool=TOOL,
                    request=(("address", screen.address), ("chain", screen.chain)),
                    status=screen.status,
                    data_mode=screen.data_mode,
                    event_time_ms=event,
                    redistribution=Redistribution.PRIVATE_ONLY,  # until the terms of every source it read are read
                )
            )
        except EvidenceError as exc:
            raise _fail(f"a reading cannot be recorded as evidence: {exc}") from exc
    return tuple(items)
