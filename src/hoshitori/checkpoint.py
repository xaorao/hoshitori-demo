"""Transparency-log checkpoints (C2SP ``tlog-checkpoint`` v1.1.0) signed as notes.

The note text is ``origin\\nsize\\nbase64(root)\\n``. The size is an ASCII decimal without leading zeros; the root
is the canonical base64 of exactly 32 bytes. This product publishes no extension lines and a verifier rejects them:
extension lines are not covered by every signature type and are not auditable, so accepting them would only add
unsigned ambiguity.

A signed checkpoint is the only thing that authenticates a tree size and root (a proof does not), so
``verify_checkpoint`` is the gate every proof check starts from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from hoshitori.merkle import HASH_LENGTH
from hoshitori.note import NoteError, Signer, VerifierKey, b64decode, b64encode, sign_note, verify_note

MAX_TREE_SIZE: Final = 2**63 - 1


class CheckpointError(ValueError):
    """The checkpoint is malformed, for another log, or not signed by a trusted key."""


@dataclass(frozen=True)
class Checkpoint:
    origin: str
    size: int
    root: bytes

    def __post_init__(self) -> None:
        if not self.origin or any(ord(ch) < 0x20 or ch.isspace() for ch in self.origin):
            raise CheckpointError("the origin is non-empty and has no spaces or control characters")
        if type(self.size) is not int or not 0 <= self.size <= MAX_TREE_SIZE:
            raise CheckpointError("tree size out of range")
        if type(self.root) is not bytes or len(self.root) != HASH_LENGTH:
            raise CheckpointError(f"the root hash must be exactly {HASH_LENGTH} bytes")

    def text(self) -> bytes:
        return f"{self.origin}\n{self.size}\n{b64encode(self.root)}\n".encode()


def parse_text(text: bytes) -> Checkpoint:
    try:
        decoded = text.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CheckpointError("checkpoint text is not valid UTF-8") from exc
    if not decoded.endswith("\n"):
        raise CheckpointError("checkpoint text must end in a newline")
    lines = decoded[:-1].split("\n")
    if len(lines) != 3 or not all(lines):
        raise CheckpointError("a checkpoint is exactly three non-empty lines (extension lines are not accepted)")
    origin, size_text, root_text = lines
    if not size_text.isascii() or not size_text.isdigit() or (size_text != "0" and size_text.startswith("0")):
        raise CheckpointError("the tree size is an ASCII decimal without leading zeros")
    try:
        root = b64decode(root_text)
    except NoteError as exc:
        raise CheckpointError(str(exc)) from exc
    return Checkpoint(origin, int(size_text), root)


def sign_checkpoint(checkpoint: Checkpoint, signers: list[Signer]) -> bytes:
    return sign_note(checkpoint.text(), signers)


def verify_checkpoint(note: bytes, keys: list[VerifierKey], origin: str) -> Checkpoint:
    """Verify the note against trusted keys and require the expected origin; returns the checkpoint."""
    try:
        text, _ = verify_note(note, keys)
    except NoteError as exc:
        raise CheckpointError(str(exc)) from exc
    checkpoint = parse_text(text)
    if checkpoint.origin != origin:
        raise CheckpointError("checkpoint is for another log")
    if checkpoint.text() != text:
        raise CheckpointError("checkpoint text is not in canonical form")
    return checkpoint
