"""Signed notes (C2SP ``signed-note`` v1.1.0) with Ed25519 signatures.

A note is text ending in a newline, a blank line, then one or more signature lines
``<em dash> <key name> <base64(key id || signature)>``. The key id is the first four bytes of
``SHA-256(key name || 0x0A || 0x01 || public key)``. Verification follows the specification's rules:

* signatures from unknown keys (a different name or id) are ignored;
* a signature from a known key that fails to verify rejects the whole note;
* a note with no verified signature from a known key is rejected;
* base64 must be canonical (decoded and re-encoded bytes must match);
* the text is valid UTF-8 with no control character other than newline.

The same ``(name, key id)`` appearing twice is rejected (the checkpoint specification forbids it).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass
from typing import Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

ED25519: Final = 0x01
EM_DASH: Final = "—"
MAX_SIGNATURES: Final = 32  # the specification requires accepting at least 16


class NoteError(ValueError):
    """The note, a key or a signature is malformed or fails verification."""


def b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64decode(text: str) -> bytes:
    """Standard base64 with padding; non-canonical encodings are rejected."""
    try:
        data = base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise NoteError("invalid base64") from exc
    if b64encode(data) != text:
        raise NoteError("non-canonical base64")
    return data


def check_key_name(name: str) -> str:
    if not name or "+" in name or any(ch.isspace() for ch in name) or any(ord(ch) < 0x20 for ch in name):
        raise NoteError("key names are non-empty and contain no spaces, control characters or '+'")
    return name


def key_id(name: str, public_key: bytes, signature_type: int = ED25519) -> bytes:
    """SHA-256(name || 0x0A || type || public key)[:4]."""
    return hashlib.sha256(name.encode("utf-8") + b"\n" + bytes([signature_type]) + public_key).digest()[:4]


@dataclass(frozen=True)
class VerifierKey:
    """A trusted public key: what a verifier is configured with."""

    name: str
    public_key: bytes

    def __post_init__(self) -> None:
        check_key_name(self.name)
        if len(self.public_key) != 32:
            raise NoteError("an Ed25519 public key is 32 bytes")

    @property
    def key_id(self) -> bytes:
        return key_id(self.name, self.public_key)

    def vkey(self) -> str:
        """``name+hex(key id)+base64(type || public key)``."""
        return f"{self.name}+{self.key_id.hex()}+{b64encode(bytes([ED25519]) + self.public_key)}"

    @staticmethod
    def from_vkey(text: str) -> VerifierKey:
        # The key name has no '+', the key id is 8 hex digits, and the base64 alphabet itself contains '+', so only
        # the first two '+' separate the parts.
        name, sep1, rest = text.partition("+")
        hex_id, sep2, material_b64 = rest.partition("+")
        if not (sep1 and sep2):
            raise NoteError("a verifier key has three '+'-separated parts")
        material = b64decode(material_b64)
        if len(material) != 33 or material[0] != ED25519:
            raise NoteError("only Ed25519 verifier keys are supported")
        key = VerifierKey(name, material[1:])
        if hex_id != key.key_id.hex():
            raise NoteError("key id does not match the key")
        return key


class Signer:
    """An Ed25519 note signer."""

    def __init__(self, name: str, private_key: Ed25519PrivateKey) -> None:
        self.name = check_key_name(name)
        self._private = private_key
        public = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.verifier = VerifierKey(name, public)

    @staticmethod
    def from_seed(name: str, seed: bytes) -> Signer:
        if len(seed) != 32:
            raise NoteError("an Ed25519 seed is 32 bytes")
        return Signer(name, Ed25519PrivateKey.from_private_bytes(seed))

    def sign(self, text: bytes) -> bytes:
        return self._private.sign(text)


def check_text(text: bytes) -> str:
    """The note text: valid UTF-8, ending in a newline, no control characters except newline."""
    try:
        decoded = text.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise NoteError("note text is not valid UTF-8") from exc
    if not decoded.endswith("\n"):
        raise NoteError("note text must end in a newline")
    if any(ord(ch) < 0x20 and ch != "\n" for ch in decoded):
        raise NoteError("note text contains a control character")
    return decoded


def sign_note(text: bytes, signers: list[Signer]) -> bytes:
    check_text(text)
    if not signers:
        raise NoteError("at least one signer is required")
    lines = []
    for signer in signers:
        blob = signer.verifier.key_id + signer.sign(text)
        lines.append(f"{EM_DASH} {signer.name} {b64encode(blob)}\n")
    return text + b"\n" + "".join(lines).encode("utf-8")


@dataclass(frozen=True)
class NoteSignature:
    name: str
    key_id: bytes
    signature: bytes


def split_note(note: bytes) -> tuple[bytes, list[NoteSignature]]:
    """Separate the text from the signature lines (at the last empty line) and parse the signature lines."""
    try:
        decoded = note.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise NoteError("note is not valid UTF-8") from exc
    if any(ord(ch) < 0x20 and ch != "\n" for ch in decoded):
        raise NoteError("note contains a control character")
    cut = decoded.rfind("\n\n")
    if cut < 0 or not decoded.endswith("\n"):
        raise NoteError("a note is text, a blank line and signature lines, each ending in a newline")
    text, tail = decoded[: cut + 1], decoded[cut + 2 :]
    if not text.strip("\n"):
        raise NoteError("empty note text")
    signatures: list[NoteSignature] = []
    seen: set[tuple[str, bytes]] = set()
    for line in tail.split("\n")[:-1]:
        prefix = EM_DASH + " "
        if not line.startswith(prefix):
            raise NoteError("a signature line starts with an em dash and a space")
        name, sep, encoded = line[len(prefix) :].rpartition(" ")
        if not sep:
            raise NoteError("a signature line has a key name and a signature")
        check_key_name(name)
        blob = b64decode(encoded)
        if len(blob) < 5:
            raise NoteError("signature too short")
        item = NoteSignature(name, blob[:4], blob[4:])
        if (item.name, item.key_id) in seen:
            raise NoteError("duplicate signature for one key name and key id")
        seen.add((item.name, item.key_id))
        signatures.append(item)
    if not signatures:
        raise NoteError("no signature lines")
    if len(signatures) > MAX_SIGNATURES:
        raise NoteError("too many signatures")
    return text.encode("utf-8"), signatures


def verify_note(note: bytes, keys: list[VerifierKey]) -> tuple[bytes, list[str]]:
    """Verify a note against trusted keys; returns the text and the names of the keys whose signatures verified."""
    text, signatures = split_note(note)
    trusted = {(key.name, key.key_id): key for key in keys}
    verified: list[str] = []
    for item in signatures:
        key = trusted.get((item.name, item.key_id))
        if key is None:
            continue  # unknown key: ignored, even if the name or the id alone matches a known key
        try:
            Ed25519PublicKey.from_public_bytes(key.public_key).verify(item.signature, text)
        except InvalidSignature as exc:
            raise NoteError(f"signature of {item.name} does not verify") from exc
        verified.append(item.name)
    if not verified:
        raise NoteError("no signature from a trusted key")
    return text, verified
