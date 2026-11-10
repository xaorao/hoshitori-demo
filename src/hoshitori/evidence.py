"""The evidence item: one typed reading of what a source said, with the four times it has, what it does not know, and where it came from.

``DATA_PROVENANCE`` sections 1 to 3 and 6 and contract sections 18, 21 and 40 are the rules. An item is never a replacement for the bytes it
was read from (the raw capture stays), and it is never edited: a revision is a new item that names the one it replaces.

* **Four times, never one.** ``event_time`` (the source's word, untrusted), ``first_seen_at`` (a provider's word), ``received_at`` (our
  gateway finished receiving the bytes) and ``known_at`` (when the system could use it). A fact is never known before it was received, so
  ``known_at`` is the receive time or later; a derived item is known when it was built.
* **Unknown is not zero.** A measurement that is null, absent, unavailable, malformed or a zero that a degraded answer may have printed in
  place of "no value" is not in ``fields``; it is in ``unknown_fields`` with its reason, and nothing downstream may read it as a number.
* **Origins, not calls.** Every item names its ``origin_family`` (RYO as one family whatever the tool; an exchange; a chain; a primary
  document; a news outlet; a social author; or DERIVED, which is never an origin and takes the families of its parents).
* **Lineage is declared.** A derived item names its parents by id and the digest of their records, and the transform and its version.
* **Identity.** ``ev`` over the capture, the adapter version and the item's path in the capture; a derived item over its transform, the
  version, its parents and its path. Two fetches of identical bytes are two captures, so two items; the content hash is an attribute.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, cast

from hoshitori import canonical, envelope
from hoshitori.ids import natural_id
from hoshitori.timeline import from_ms, to_ms
from hoshitori.timeutil import TimeError, format_rfc3339_ms, parse_rfc3339

TYPE: Final = "evidence"
TYPE_VERSION: Final = 1
MAX_FIELDS: Final = 5000
MAX_TEXT: Final = 2000
MAX_VALUE: Final = 20_000  # a field value or a warning: prose from a vendor can be longer than a label (the longest recorded is 2,162)
MAX_PATH: Final = 300
MAX_SMALL: Final = 64  # warnings, availability sections, parents and asset references
MAX_INSTANT_MS: Final = 253_402_300_799_999

_SHA256 = re.compile(r"[0-9a-f]{64}")
_NAME = re.compile(r"[A-Za-z0-9@][A-Za-z0-9._@-]{0,63}")
_SOURCE = re.compile(r"[a-z0-9_.:-]{1,64}")
_VERSION = re.compile(r"[A-Za-z0-9._+-]{1,64}")


class EvidenceError(ValueError):
    """An item that cannot exist, or a record that is not one."""


class Kind(StrEnum):
    OBSERVATION = "OBSERVATION"  # a measured value from a source
    DERIVED = "DERIVED"  # computed by us from parents
    INTERPRETATION = "INTERPRETATION"  # a vendor's or a model's judgement: never given to an independent arm


class Unknown(StrEnum):
    NULL = "NULL"  # the source said null
    ABSENT = "ABSENT"  # the contract names the field and the answer has none
    UNAVAILABLE_BY_AVAILABILITY = "UNAVAILABLE_BY_AVAILABILITY"  # the answer says its section was not available
    MALFORMED = "MALFORMED"  # a value the field cannot have
    DEGRADED_ZERO = "DEGRADED_ZERO"  # a zero in an answer that was not fully available: not a measurement


class Redistribution(StrEnum):
    PUBLIC_OK = "PUBLIC_OK"
    PRIVATE_ONLY = "PRIVATE_ONLY"
    DERIVED_ONLY = "DERIVED_ONLY"


class FamilyKind(StrEnum):
    RYO = "RYO"
    EXCHANGE = "EXCHANGE"
    CHAIN = "CHAIN"
    PRIMARY_DOC = "PRIMARY_DOC"
    NEWS = "NEWS"
    SOCIAL = "SOCIAL"
    DERIVED = "DERIVED"


_NAMELESS: Final = (FamilyKind.RYO, FamilyKind.DERIVED)


@dataclass(frozen=True, order=True)
class Family:
    """Where a piece of information came from, for counting independent origins."""

    kind: FamilyKind
    name: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, FamilyKind):
            raise EvidenceError("a family has a kind")
        if self.kind in _NAMELESS:
            if self.name:
                raise EvidenceError(f"the family {self.kind.value} has no name")
        elif not isinstance(self.name, str) or _NAME.fullmatch(self.name) is None:
            raise EvidenceError(f"the family {self.kind.value} has a name of 1 to 64 letters, digits and . _ @ -")

    @property
    def text(self) -> str:
        return self.kind.value if self.kind in _NAMELESS else f"{self.kind.value}:{self.name}"

    @property
    def is_origin(self) -> bool:
        return self.kind is not FamilyKind.DERIVED


def parse_family(text: object) -> Family:
    if not isinstance(text, str):
        raise EvidenceError("a family is written as text")
    kind, _, name = text.partition(":")
    if kind not in FamilyKind.__members__:
        raise EvidenceError(f"{kind!r} is not an origin family")
    if (FamilyKind(kind) in _NAMELESS) != (not name) or (":" in name):
        raise EvidenceError(f"the family {text!r} is not written the way its kind is written")
    return Family(FamilyKind(kind), name)


RYO: Final = Family(FamilyKind.RYO)
DERIVED: Final = Family(FamilyKind.DERIVED)


def _check_text(value: object, what: str, limit: int = MAX_TEXT) -> None:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise EvidenceError(f"{what} is a non-empty string of at most {limit} characters")
    try:
        canonical.dumps({"v": value})
    except canonical.CanonicalError as exc:
        raise EvidenceError(f"{what} is not valid canonical text: {exc}") from exc


def _check_hash(value: object, what: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise EvidenceError(f"{what} is 64 lower-case hexadecimal characters")


def _check_ms(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_INSTANT_MS:
        raise EvidenceError(f"{what} is a whole number of milliseconds up to the end of the year 9999")
    return value


def record_digest(record: dict[str, canonical.JsonValue]) -> str:
    """The digest a reference to a record carries: SHA-256 of its canonical bytes, as hex."""
    return envelope.body_digest(canonical.dumps(record)).hex()


type FieldValue = str | bool


@dataclass(frozen=True)
class EvidenceItem:
    """One reading. ``fields`` holds what is known, ``unknown_fields`` what is not and why; their paths never overlap."""

    adapter_version: str
    path: str
    kind: Kind
    source: str
    origin: Family
    received_at_ms: int
    known_at_ms: int
    fields: tuple[tuple[str, FieldValue], ...]
    unknown_fields: tuple[tuple[str, Unknown], ...] = ()
    capture_id: str | None = None
    raw_sha256: str | None = None
    tool: str | None = None
    request: tuple[tuple[str, canonical.JsonValue], ...] = ()
    status: str | None = None
    data_mode: str | None = None
    availability: tuple[tuple[str, str], ...] = ()
    warnings: tuple[str, ...] = ()
    event_time_ms: int | None = None
    first_seen_at_ms: int | None = None
    derived_from: tuple[tuple[str, str], ...] = ()  # (evidence id, digest of that item's record)
    transform: tuple[str, str] | None = None  # (name, version)
    supersedes: str | None = None
    asset_refs: tuple[tuple[str, str, str], ...] = ()  # (asset id, instrument id, status)
    redistribution: Redistribution = Redistribution.PRIVATE_ONLY

    def __post_init__(self) -> None:
        if not isinstance(self.adapter_version, str) or _VERSION.fullmatch(self.adapter_version) is None:
            raise EvidenceError("an adapter version is 1 to 64 letters, digits and . _ + -")
        _check_text(self.path, "an item path", MAX_PATH)
        if not isinstance(self.kind, Kind) or not isinstance(self.origin, Family) or not isinstance(self.redistribution, Redistribution):
            raise EvidenceError("kind, origin and redistribution have their own types")
        if not isinstance(self.source, str) or _SOURCE.fullmatch(self.source) is None:
            raise EvidenceError("a source is 1 to 64 characters of a-z, 0-9 and . _ : -")
        self._check_times()
        self._check_content()
        self._check_origin()

    def _check_times(self) -> None:
        received, known = _check_ms(self.received_at_ms, "received_at"), _check_ms(self.known_at_ms, "known_at")
        if known < received:
            raise EvidenceError("an item is not known before it was received")
        for value, what in ((self.event_time_ms, "event_time"), (self.first_seen_at_ms, "first_seen_at")):
            if value is not None:
                _check_ms(value, what)
        if self.first_seen_at_ms is not None and self.first_seen_at_ms > received:
            raise EvidenceError("a provider cannot have first shown an item after we received it")

    def _check_content(self) -> None:
        if len(self.fields) + len(self.unknown_fields) > MAX_FIELDS:
            raise EvidenceError(f"an item has at most {MAX_FIELDS} fields")
        known: set[str] = set()
        for path, value in self.fields:
            _check_text(path, "a field path", MAX_PATH)
            if not isinstance(value, bool) and not isinstance(value, str):
                raise EvidenceError("a field is text, a plain decimal in text, or true or false: never a number")
            if isinstance(value, str):
                _check_text(value, "a field value", MAX_VALUE)
            known.add(path)
        if [p for p, _ in self.fields] != sorted(known):
            raise EvidenceError("fields are in path order, each path once")
        missing: set[str] = set()
        for path, reason in self.unknown_fields:
            _check_text(path, "an unknown field path", MAX_PATH)
            if not isinstance(reason, Unknown):
                raise EvidenceError("an unknown field has a reason")
            missing.add(path)
        if [p for p, _ in self.unknown_fields] != sorted(missing):
            raise EvidenceError("unknown fields are in path order, each path once")
        if known & missing:
            raise EvidenceError("a field is either known or unknown, never both")
        if self.tool is not None:
            _check_text(self.tool, "a tool", 64)
        if self.status is not None:
            _check_text(self.status, "a status", 64)
        if self.data_mode is not None:
            _check_text(self.data_mode, "a data mode", 64)
        if len(self.availability) > MAX_SMALL or len(self.warnings) > MAX_SMALL or len(self.asset_refs) > MAX_SMALL:
            raise EvidenceError(f"availability, warnings and asset references are at most {MAX_SMALL} each")
        keys = [key for key, _ in self.availability]
        if keys != sorted(set(keys)):
            raise EvidenceError("availability is in section order, each section once")
        for key, word in self.availability:
            _check_text(key, "an availability section", 64)
            _check_text(word, "an availability word", 64)
        for warning in self.warnings:
            _check_text(warning, "a warning", MAX_VALUE)
        keys = [key for key, _ in self.request]
        if keys != sorted(set(keys)):
            raise EvidenceError("the request is in key order, each key once")
        for key, _ in self.request:
            _check_text(key, "a request key", 64)
        try:
            canonical.dumps({"request": dict(self.request)})
        except canonical.CanonicalError as exc:
            raise EvidenceError(f"the request is not valid canonical data: {exc}") from exc
        for asset in self.asset_refs:
            if len(asset) != 3:
                raise EvidenceError("an asset reference is (asset id, instrument id, status)")
            for part in asset:
                _check_text(part, "an asset reference part", 128)
        if sorted(self.asset_refs) != list(self.asset_refs):
            raise EvidenceError("asset references are in order")

    def _check_origin(self) -> None:
        if self.kind is Kind.DERIVED:
            if (
                self.origin != DERIVED
                or not self.derived_from
                or self.transform is None
                or self.capture_id is not None
                or self.raw_sha256 is not None
            ):
                raise EvidenceError("a derived item has the DERIVED family, parents, a transform and no capture")
        elif (
            self.origin == DERIVED or self.derived_from or self.transform is not None or self.capture_id is None or self.raw_sha256 is None
        ):
            raise EvidenceError("an item that is not derived has an origin family, a capture and its hash, and no parents or transform")
        if self.capture_id is not None:
            _check_text(self.capture_id, "a capture id", 128)
            _check_hash(self.raw_sha256, "the raw hash")
        if len(self.derived_from) > MAX_SMALL or len({ident for ident, _ in self.derived_from}) != len(self.derived_from):
            raise EvidenceError(f"parents are at most {MAX_SMALL} distinct ids")
        if [i for i, _ in self.derived_from] != sorted(i for i, _ in self.derived_from):
            raise EvidenceError("parents are in id order")
        for ident, digest in self.derived_from:
            _check_text(ident, "a parent id", 128)
            _check_hash(digest, "a parent digest")
        if self.transform is not None:
            _check_text(self.transform[0], "a transform name", 64)
            if _VERSION.fullmatch(self.transform[1]) is None:
                raise EvidenceError("a transform version is 1 to 64 letters, digits and . _ + -")
        if self.supersedes is not None:
            _check_text(self.supersedes, "the superseded id", 128)

    @property
    def evidence_id(self) -> str:
        if self.kind is Kind.DERIVED:
            name, version = cast(tuple[str, str], self.transform)
            key: dict[str, canonical.JsonValue] = {
                "transform": name,
                "version": version,
                "parents": [ident for ident, _ in self.derived_from],
                "path": self.path,
            }
        else:
            key = {"capture": cast(str, self.capture_id), "adapter": self.adapter_version, "path": self.path}
        return natural_id("ev", key)

    @property
    def key(self) -> tuple[str, str | None, str, str]:
        """What the item measures, apart from when: a later item with the same key is a newer reading or a revision of it."""
        request = canonical.dumps({"request": dict(self.request)}).decode()
        return (self.source, self.tool, request, self.path)

    def field(self, path: str) -> FieldValue | None:
        return next((value for p, value in self.fields if p == path), None)

    def to_body(self) -> dict[str, canonical.JsonValue]:
        def stamp(ms: int | None) -> canonical.JsonValue:
            return None if ms is None else format_rfc3339_ms(from_ms(ms))

        return {
            "kind": self.kind.value,
            "source": self.source,
            "tool": self.tool,
            "capture_id": self.capture_id,
            "raw_sha256": self.raw_sha256,
            "adapter_version": self.adapter_version,
            "path": self.path,
            "request": dict(self.request),
            "status": self.status,
            "data_mode": self.data_mode,
            "availability": dict(self.availability),
            "warnings": list(self.warnings),
            "fields": dict(self.fields),
            "unknown_fields": [{"path": p, "reason": r.value} for p, r in self.unknown_fields],
            "event_time": stamp(self.event_time_ms),
            "first_seen_at": stamp(self.first_seen_at_ms),
            "received_at": stamp(self.received_at_ms),
            "known_at": stamp(self.known_at_ms),
            "origin_family": self.origin.text,
            "derived_from": [{"evidence_id": i, "sha256": d} for i, d in self.derived_from],
            "transform": None if self.transform is None else {"name": self.transform[0], "version": self.transform[1]},
            "supersedes": self.supersedes,
            "asset_refs": [{"asset_id": a, "instrument_id": i, "status": s} for a, i, s in self.asset_refs],
            "redistribution": self.redistribution.value,
        }

    def to_record(self, source_urn: str, created_at_ms: int) -> dict[str, canonical.JsonValue]:
        """The record a producer submits to the ledger. It is created no earlier than the item is known."""
        if _check_ms(created_at_ms, "the creation time") < self.known_at_ms:
            raise EvidenceError("an item is recorded at or after the time it is known")
        record: dict[str, canonical.JsonValue] = {
            "spec": envelope.SPEC,
            "type": TYPE,
            "type_version": TYPE_VERSION,
            "id": self.evidence_id,
            "source": source_urn,
            "created_at": format_rfc3339_ms(from_ms(created_at_ms)),
            "body": self.to_body(),
            "refs": [{"id": i, "sha256": d} for i, d in self.derived_from],
            "privacy": "private_digest_only" if self.redistribution is Redistribution.PRIVATE_ONLY else "public",
        }
        try:
            envelope.validate_producer_record(record)
        except envelope.EnvelopeError as exc:
            raise EvidenceError(f"the record does not satisfy the envelope: {exc}") from exc
        return record


# --- reading a record back --------------------------------------------------------------------------------------------------------------
_MEMBERS: Final = frozenset(
    {
        "kind", "source", "tool", "capture_id", "raw_sha256", "adapter_version", "path", "request", "status", "data_mode", "availability",
        "warnings", "fields", "unknown_fields", "event_time", "first_seen_at", "received_at", "known_at", "origin_family", "derived_from",
        "transform", "supersedes", "asset_refs", "redistribution",
    }
)  # fmt: skip


def _instant(value: canonical.JsonValue, what: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise EvidenceError(f"{what} is an RFC 3339 string")
    try:
        milliseconds = to_ms(parse_rfc3339(value))
    except (TimeError, ValueError) as exc:
        raise EvidenceError(f"{what}: {exc}") from exc
    if format_rfc3339_ms(from_ms(milliseconds)) != value:
        raise EvidenceError(f"{what} is written as UTC with milliseconds")
    return milliseconds


def _object(value: canonical.JsonValue, what: str) -> dict[str, canonical.JsonValue]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{what} is an object")
    return value


def _list(value: canonical.JsonValue, what: str) -> list[canonical.JsonValue]:
    if not isinstance(value, list):
        raise EvidenceError(f"{what} is a list")
    return value


def _optional_text(value: canonical.JsonValue, what: str) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise EvidenceError(f"{what} is text or null")


def _enum[E: StrEnum](cls: type[E], value: canonical.JsonValue, what: str) -> E:
    if not isinstance(value, str) or value not in cls.__members__:
        raise EvidenceError(f"{what} is one of {sorted(cls.__members__)}")
    return cls(value)


def _text(value: canonical.JsonValue, what: str) -> str:
    if not isinstance(value, str):
        raise EvidenceError(f"{what} is text")
    return value


def from_record(record: dict[str, canonical.JsonValue]) -> EvidenceItem:
    """Read an evidence record exactly as strictly as it was written."""
    try:
        envelope.validate_producer_record({k: v for k, v in record.items() if k != "committed_at"})
    except envelope.EnvelopeError as exc:
        raise EvidenceError(f"the envelope: {exc}") from exc
    if record["type"] != TYPE or record["type_version"] != TYPE_VERSION:
        raise EvidenceError("not an evidence record of a version this reader knows")
    body = cast(dict[str, canonical.JsonValue], record["body"])  # the envelope check guarantees an object
    if set(body) != _MEMBERS:
        raise EvidenceError(f"the body has the members {sorted(set(body) ^ _MEMBERS)} wrong")
    fields = _object(body["fields"], "fields")
    parents = []
    for entry in _list(body["derived_from"], "derived_from"):
        parent = _object(entry, "a parent")
        if set(parent) != {"evidence_id", "sha256"}:
            raise EvidenceError("a parent is {evidence_id, sha256}")
        parents.append((_text(parent["evidence_id"], "a parent id"), _text(parent["sha256"], "a parent digest")))
    unknown = []
    for entry in _list(body["unknown_fields"], "unknown_fields"):
        gap = _object(entry, "an unknown field")
        if set(gap) != {"path", "reason"}:
            raise EvidenceError("an unknown field is {path, reason}")
        unknown.append((_text(gap["path"], "an unknown field path"), _enum(Unknown, gap["reason"], "an unknown field's reason")))
    assets = []
    for entry in _list(body["asset_refs"], "asset_refs"):
        asset = _object(entry, "an asset reference")
        if set(asset) != {"asset_id", "instrument_id", "status"}:
            raise EvidenceError("an asset reference is {asset_id, instrument_id, status}")
        assets.append(
            (_text(asset["asset_id"], "asset_id"), _text(asset["instrument_id"], "instrument_id"), _text(asset["status"], "status"))
        )
    transform_value = body["transform"]
    transform: tuple[str, str] | None = None
    if transform_value is not None:
        spec = _object(transform_value, "the transform")
        if set(spec) != {"name", "version"}:
            raise EvidenceError("the transform is {name, version}")
        transform = (_text(spec["name"], "the transform name"), _text(spec["version"], "the transform version"))
    values = cast(list[tuple[str, FieldValue]], list(fields.items()))  # the item below refuses a value that is not text or a boolean
    availability = _object(body["availability"], "availability")
    request = _object(body["request"], "the request")
    item = EvidenceItem(
        adapter_version=_text(body["adapter_version"], "the adapter version"),
        path=_text(body["path"], "the item path"),
        kind=_enum(Kind, body["kind"], "kind"),
        source=_text(body["source"], "the source"),
        origin=parse_family(body["origin_family"]),
        received_at_ms=cast(int, _instant(body["received_at"], "received_at")),
        known_at_ms=cast(int, _instant(body["known_at"], "known_at")),
        fields=tuple(sorted(values)),
        unknown_fields=tuple(unknown),
        capture_id=_optional_text(body["capture_id"], "the capture id"),
        raw_sha256=_optional_text(body["raw_sha256"], "the raw hash"),
        tool=_optional_text(body["tool"], "the tool"),
        request=tuple(sorted(request.items())),
        status=_optional_text(body["status"], "the status"),
        data_mode=_optional_text(body["data_mode"], "the data mode"),
        availability=tuple(sorted((k, _text(v, "an availability word")) for k, v in availability.items())),
        warnings=tuple(_text(w, "a warning") for w in _list(body["warnings"], "warnings")),
        event_time_ms=_instant(body["event_time"], "event_time", optional=True),
        first_seen_at_ms=_instant(body["first_seen_at"], "first_seen_at", optional=True),
        derived_from=tuple(parents),
        transform=transform,
        supersedes=_optional_text(body["supersedes"], "supersedes"),
        asset_refs=tuple(assets),
        redistribution=_enum(Redistribution, body["redistribution"], "redistribution"),
    )
    if record["id"] != item.evidence_id:
        raise EvidenceError("the id is not the id of this item")
    return item
