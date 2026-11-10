"""Shared helpers for experiment E2c (bundle builder). The two verifiers do NOT import this file: each is written separately."""
from __future__ import annotations

import base64
import hashlib
import sys
from decimal import Decimal
from pathlib import Path

EXP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXP / "E1"))
sys.path.insert(0, str(EXP / "E2a"))
import canon  # noqa: E402  (record profile canonicaliser, E1)
import merkle as M  # noqa: E402  (RFC 9162 tree, E2a)


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def key_id(name: str, pub: bytes) -> bytes:
    return hashlib.sha256(name.encode() + b"\n" + b"\x01" + pub).digest()[:4]


def vkey(name: str, pub: bytes) -> str:
    return f"{name}+{key_id(name, pub).hex()}+{b64(b'\x01' + pub)}"


def decimal_plain(token: str) -> str:
    """The decimal string spelled in a raw JSON number token, without a binary float in between (API compatibility section 6)."""
    d = Decimal(token)
    s = format(d, "f")
    return s


def note_text(origin: str, size: int, root: bytes) -> bytes:
    return f"{origin}\n{size}\n{b64(root)}\n".encode()


def signed_note(text: bytes, name: str, pub: bytes, sig: bytes) -> bytes:
    return text + b"\n" + "— ".encode() + name.encode() + b" " + b64(key_id(name, pub) + sig).encode() + b"\n"


def tlog_proof(index: int, path: list[bytes], checkpoint_note: bytes, extra: bytes | None = None) -> bytes:
    lines = ["c2sp.org/tlog-proof@v1"]
    if extra is not None:
        lines.append("extra " + b64(extra))
    lines.append(f"index {index}")
    lines += [b64(p) for p in path]
    return ("\n".join(lines) + "\n\n").encode() + checkpoint_note
