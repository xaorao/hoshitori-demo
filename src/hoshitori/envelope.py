"""The record envelope: the one shape every ledger record has (``DOMAIN_MODEL`` section 4).

The ledger interprets nothing inside ``body``; it validates the envelope so that every record can be identified,
deduplicated by ``(source, id)`` and dated, and so that a producer cannot supply the one field only the ledger
writer may set (``committed_at``).
"""

from __future__ import annotations

import hashlib
import re
from typing import Final

from hoshitori import canonical
from hoshitori.timeutil import TimeError, parse_rfc3339

SPEC: Final = "hoshitori.record/1"
PRODUCER_MEMBERS: Final = frozenset({"spec", "type", "type_version", "id", "source", "created_at", "body", "refs", "privacy"})
REQUIRED_MEMBERS: Final = frozenset({"spec", "type", "type_version", "id", "source", "created_at", "body"})
PRIVACY_VALUES: Final = frozenset({"public", "private_digest_only"})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}")
_TYPE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


class EnvelopeError(ValueError):
    """A record does not satisfy the envelope rules."""


def validate_producer_record(record: dict[str, canonical.JsonValue]) -> None:
    """Check a record as a producer submits it (without ``committed_at``)."""
    if "committed_at" in record:
        raise EnvelopeError("committed_at is set by the ledger writer; a producer must not supply it")
    unknown = set(record) - PRODUCER_MEMBERS
    if unknown:
        raise EnvelopeError(f"unknown envelope members: {sorted(unknown)}")
    missing = REQUIRED_MEMBERS - set(record)
    if missing:
        raise EnvelopeError(f"missing envelope members: {sorted(missing)}")
    if record["spec"] != SPEC:
        raise EnvelopeError(f"spec must be {SPEC!r}")
    kind = record["type"]
    if not isinstance(kind, str) or not _TYPE.fullmatch(kind):
        raise EnvelopeError("type must be a lower-case identifier")
    version = record["type_version"]
    if type(version) is not int or version < 1:
        raise EnvelopeError("type_version must be a positive integer")
    ident = record["id"]
    if not isinstance(ident, str) or not _ID.fullmatch(ident):
        raise EnvelopeError("id must be 1 to 128 characters of letters, digits and . _ : -")
    source = record["source"]
    if not isinstance(source, str) or not source.startswith("urn:") or len(source) > 256 or not source.isascii():
        raise EnvelopeError("source must be an ASCII urn")
    created = record["created_at"]
    if not isinstance(created, str):
        raise EnvelopeError("created_at must be an RFC 3339 string")
    try:
        parse_rfc3339(created)
    except TimeError as exc:
        raise EnvelopeError(f"created_at: {exc}") from exc
    if not isinstance(record["body"], dict):
        raise EnvelopeError("body must be an object")
    refs = record.get("refs", [])
    if not isinstance(refs, list):
        raise EnvelopeError("refs must be a list")
    for ref in refs:
        if not (
            isinstance(ref, dict)
            and set(ref) == {"id", "sha256"}
            and isinstance(ref["id"], str)
            and isinstance(ref["sha256"], str)
            and _SHA256_HEX.fullmatch(ref["sha256"])
        ):
            raise EnvelopeError("each ref is {id, sha256 (64 lower-case hex)}")
    privacy = record.get("privacy", "public")
    if not isinstance(privacy, str) or privacy not in PRIVACY_VALUES:
        raise EnvelopeError("privacy must be 'public' or 'private_digest_only'")


def validate_committed_record(record: dict[str, canonical.JsonValue]) -> None:
    """Check a record as the ledger holds it: the producer's envelope plus the writer's ``committed_at``."""
    committed = record.get("committed_at")
    if not isinstance(committed, str):
        raise EnvelopeError("committed_at must be an RFC 3339 string")
    try:
        parse_rfc3339(committed)
    except TimeError as exc:
        raise EnvelopeError(f"committed_at: {exc}") from exc
    validate_producer_record({key: value for key, value in record.items() if key != "committed_at"})


def body_digest(record_bytes: bytes) -> bytes:
    """SHA-256 of a record's canonical bytes; what a reference carries."""
    return hashlib.sha256(record_bytes).digest()
