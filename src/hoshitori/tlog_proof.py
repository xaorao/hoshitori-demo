"""C2SP ``tlog-proof`` v1: an offline-verifiable inclusion proof in one text file.

::

    c2sp.org/tlog-proof@v1
    extra <base64>            (optional, not authenticated)
    index <decimal>
    <base64 sibling hash>     (zero or more lines, leaf side first)

    <the checkpoint note, verbatim>

The decoder is strict where the specification says MUST (canonical base64, an index without leading zeros) and where a
lenient reader would hide a defect: every hash is exactly 32 bytes, the path is bounded, lines end in a bare newline.
The note is returned as the verbatim bytes; its signatures are checked by ``checkpoint.verify_checkpoint``, never here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from hoshitori.checkpoint import MAX_TREE_SIZE
from hoshitori.merkle import HASH_LENGTH
from hoshitori.note import NoteError, b64decode, b64encode

HEADER: Final = "c2sp.org/tlog-proof@v1"
MAX_PATH: Final = 64  # a tree of 2**63 leaves needs at most 63 siblings
MAX_PROOF_BYTES: Final = 65_536


class ProofError(ValueError):
    """The text is not a well-formed tlog-proof."""


@dataclass(frozen=True)
class TlogProof:
    index: int
    path: tuple[bytes, ...]
    note: bytes
    extra: bytes | None = None


def _decimal(text: str, what: str) -> int:
    if not text.isascii() or not text.isdigit() or (len(text) > 1 and text.startswith("0")):
        raise ProofError(f"{what} must be an ASCII decimal without leading zeros")
    value = int(text)
    if value > MAX_TREE_SIZE:
        raise ProofError(f"{what} out of range")
    return value


def _hash(text: str) -> bytes:
    try:
        raw = b64decode(text)
    except NoteError as exc:
        raise ProofError(f"bad base64: {exc}") from exc
    if len(raw) != HASH_LENGTH:
        raise ProofError(f"a path hash is exactly {HASH_LENGTH} bytes")
    return raw


def parse_proof(data: bytes) -> TlogProof:
    if len(data) > MAX_PROOF_BYTES:
        raise ProofError("proof too large")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProofError("proof is not valid UTF-8") from exc
    if "\r" in text:
        raise ProofError("lines end in a bare newline")
    lines = text.split("\n")
    if not lines or lines[0] != HEADER:
        raise ProofError(f"the first line is {HEADER!r}")
    position = 1
    extra: bytes | None = None
    if position < len(lines) and lines[position].startswith("extra "):
        try:
            extra = b64decode(lines[position][len("extra ") :])
        except NoteError as exc:
            raise ProofError(f"extra data: {exc}") from exc
        position += 1
    if position >= len(lines) or not lines[position].startswith("index "):
        raise ProofError("the index line is missing")
    index = _decimal(lines[position][len("index ") :], "index")
    position += 1
    path: list[bytes] = []
    while position < len(lines) and lines[position] != "":
        path.append(_hash(lines[position]))
        if len(path) > MAX_PATH:
            raise ProofError("inclusion path too long")
        position += 1
    if position >= len(lines):
        raise ProofError("the blank line before the checkpoint is missing")
    note = "\n".join(lines[position + 1 :]).encode("utf-8")
    if not note:
        raise ProofError("the checkpoint is missing")
    return TlogProof(index, tuple(path), note, extra)


def format_proof(proof: TlogProof) -> bytes:
    lines = [HEADER]
    if proof.extra is not None:
        lines.append("extra " + b64encode(proof.extra))
    lines.append(f"index {proof.index}")
    lines.extend(b64encode(item) for item in proof.path)
    return ("\n".join(lines) + "\n\n").encode("utf-8") + proof.note
