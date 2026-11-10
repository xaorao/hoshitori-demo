"""Segment files: the append-only, human-readable source of truth of the ledger.

A segment is JSON Lines. Every line is the canonical form (see ``canonical``) of a small wrapper object:

* record line     ``{"leaf":"<64 hex>","rec":"<canonical record text>","seq":N}``
* manifest line   ``{"manifest":{"records":N,"sha256":"<64 hex>","segment":"<previous file name>"}}`` - the first
  line of every segment except the first; it fixes the hash of the closed segment before it (invariant L-2), which
  chains the files together.

The wrapper itself is not hashed into the tree; the *record text* is: ``leaf = SHA-256(0x00 || record bytes)`` and
the record text must already be canonical (invariant L-5). A scan classifies what it finds so the caller can tell
three situations apart that must be handled differently:

* a clean segment;
* a **torn tail**: bytes after the last newline, the signature of a write that was cut short;
* **damage**: an invalid line that is followed by more bytes, a bad manifest, a wrong sequence number or a wrong
  hash. A torn write can only affect the last line, because each line is fsynced before the next is written, so
  damage anywhere else is corruption and is never "repaired" by truncating.

A complete but invalid *final* line is reported separately (``LAST_LINE_INVALID``) so that the caller can name it. The
ledger treats it as corruption, as it does damage: a write cut short leaves its line without the newline, the last byte
written, so a complete line that fails its check was not cut short, and the ledger's index, a rebuildable cache, cannot
show that it was never acknowledged (LEDGER_SPEC section 7, invariant 8).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from hoshitori import canonical

SEGMENT_NAME: Final = re.compile(r"segment-(\d{12})\.jsonl")


class Problem(Enum):
    NONE = "none"
    TORN_TAIL = "torn tail: bytes after the last newline"
    LAST_LINE_INVALID = "the final line is complete but invalid"
    DAMAGE = "invalid data followed by more bytes, or a broken chain"


class SegmentError(ValueError):
    """An argument or a file name is not valid for this module."""


def segment_name(first_seq: int) -> str:
    if first_seq < 0:
        raise SegmentError("sequence numbers start at zero")
    return f"segment-{first_seq:012d}.jsonl"


def segment_first_seq(name: str) -> int:
    match = SEGMENT_NAME.fullmatch(name)
    if match is None:
        raise SegmentError(f"not a segment file name: {name!r}")
    return int(match.group(1))


def leaf_hash(record: bytes) -> bytes:
    return hashlib.sha256(b"\x00" + record).digest()


def encode_record_line(seq: int, record: bytes) -> bytes:
    """The exact bytes of a record line, including the newline. ``record`` must be canonical record text."""
    if canonical.canonicalize(record) != record:
        raise SegmentError("the record is not in canonical form")
    wrapper: dict[str, canonical.JsonValue] = {
        "leaf": leaf_hash(record).hex(),
        "rec": record.decode("utf-8"),
        "seq": seq,
    }
    return canonical.dumps(wrapper) + b"\n"


def encode_manifest_line(previous_name: str, previous_sha256: bytes, previous_records: int) -> bytes:
    segment_first_seq(previous_name)
    if len(previous_sha256) != 32 or previous_records < 1:
        raise SegmentError("a manifest line needs a 32-byte hash and a positive record count")
    body: dict[str, canonical.JsonValue] = {
        "records": previous_records,
        "segment": previous_name,
        "sha256": previous_sha256.hex(),
    }
    return canonical.dumps({"manifest": body}) + b"\n"


@dataclass(frozen=True)
class RecordLine:
    seq: int
    leaf: bytes
    record: bytes
    offset: int  # of the start of the line within the segment
    length: int  # including the newline


@dataclass
class Scan:
    """Result of scanning one segment's bytes."""

    records: list[RecordLine] = field(default_factory=list)
    manifest: dict[str, canonical.JsonValue] | None = None
    valid_end: int = 0  # offset just after the last valid line
    problem: Problem = Problem.NONE
    problem_offset: int = 0
    detail: str = ""


def _parse_line(
    line: bytes, expected_seq: int, first_line_of_continuation: bool
) -> tuple[RecordLine | dict[str, canonical.JsonValue], str]:
    """Return a parsed record, a manifest body, or raise ``SegmentError`` with the reason."""
    try:
        wrapper = canonical.parse_record(line)
    except canonical.CanonicalError as exc:
        raise SegmentError(f"not a canonical wrapper: {exc}") from exc
    if canonical.dumps(wrapper) != line:
        raise SegmentError("the wrapper is not in canonical form")
    if set(wrapper) == {"manifest"}:
        body = wrapper["manifest"]
        if not first_line_of_continuation or not isinstance(body, dict):
            raise SegmentError("unexpected manifest line")
        return body, "manifest"
    if set(wrapper) != {"leaf", "rec", "seq"}:
        raise SegmentError("unexpected members in a record line")
    seq, leaf_hex, text = wrapper["seq"], wrapper["leaf"], wrapper["rec"]
    if type(seq) is not int or seq != expected_seq:
        raise SegmentError(f"sequence number {seq!r}, expected {expected_seq}")
    if not isinstance(leaf_hex, str) or not isinstance(text, str):
        raise SegmentError("leaf and rec must be strings")
    record = text.encode("utf-8")
    try:
        if canonical.canonicalize(record) != record:
            raise SegmentError("the record text is not canonical")
    except canonical.CanonicalError as exc:
        raise SegmentError(f"the record text is not valid: {exc}") from exc
    if leaf_hex != leaf_hash(record).hex():
        raise SegmentError("leaf hash does not match the record")
    return RecordLine(expected_seq, bytes.fromhex(leaf_hex), record, 0, 0), "record"


def scan_segment(data: bytes, first_seq: int, previous: tuple[str, bytes, int] | None) -> Scan:
    """Scan a segment's bytes.

    ``first_seq`` is the sequence number of its first record (from the file name). ``previous`` is
    ``(file name, SHA-256 of its bytes, record count)`` of the closed segment before it, or ``None`` for the first
    segment. A continuation segment must start with a manifest line that matches ``previous`` exactly.
    """
    result = Scan()
    pos = 0
    expected = first_seq
    first_line = True
    while pos < len(data):
        newline = data.find(b"\n", pos)
        if newline < 0:
            result.problem, result.problem_offset = Problem.TORN_TAIL, pos
            result.detail = f"{len(data) - pos} bytes without a newline"
            return result
        line = data[pos:newline]
        is_last = newline + 1 == len(data)
        try:
            parsed, kind = _parse_line(line, expected, first_line and previous is not None)
        except SegmentError as exc:
            result.problem = Problem.LAST_LINE_INVALID if is_last else Problem.DAMAGE
            result.problem_offset, result.detail = pos, str(exc)
            return result
        if kind == "manifest":
            assert isinstance(parsed, dict) and previous is not None
            if parsed != {"records": previous[2], "segment": previous[0], "sha256": previous[1].hex()}:
                result.problem, result.problem_offset = Problem.DAMAGE, pos
                result.detail = "manifest does not match the previous segment"
                return result
            result.manifest = parsed
        else:
            assert isinstance(parsed, RecordLine)
            if first_line and previous is not None:
                result.problem, result.problem_offset = Problem.DAMAGE, pos
                result.detail = "a continuation segment must start with a manifest line"
                return result
            result.records.append(RecordLine(parsed.seq, parsed.leaf, parsed.record, pos, newline + 1 - pos))
            expected += 1
        first_line = False
        pos = newline + 1
        result.valid_end = pos
    if previous is not None and first_line:
        result.problem, result.problem_offset = Problem.DAMAGE, 0
        result.detail = "a continuation segment must start with a manifest line"
    return result
