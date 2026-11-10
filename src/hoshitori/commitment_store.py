"""Keeping what a commitment needs to be shown later: the signed checkpoint text and the time-stamp answers, each in its own
file.

``CYCLE_RUNNER_SPEC`` section 2 (step 8) is the rule. The ledger holds the records; a verification bundle also needs the
checkpoint note that was stamped and the authorities' answers (``Attestation.tokens()``), which the ledger does not hold.
They are written once, atomically, in a directory named by the commitment id, and read back whole or not at all: a half-
written commitment is never offered to a bundle.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from hoshitori.commit import Commitment
from hoshitori.ledger import atomic_write

NOTE: Final = "note"
SUFFIX: Final = ".tsr"
MAX_BYTES: Final = 1_048_576
_NAME: Final = re.compile(r"[a-z0-9_-]{1,32}")
_ID: Final = re.compile(r"[a-z0-9]{1,64}")


class StoreError(ValueError):
    """A commitment that cannot be saved or loaded as asked."""


@dataclass(frozen=True)
class Saved:
    note: bytes
    tokens: dict[str, bytes]


def save(directory: Path, commitment: Commitment) -> Path:
    """Write the note and the tokens of a commitment; again, the same changes nothing; other contents under the same id are refused."""
    tokens = commitment.attestation.tokens()
    if any(_NAME.fullmatch(name) is None for name in tokens):
        raise StoreError("an authority's name is 1 to 32 characters of a-z, 0-9, - and _")
    if len(commitment.note) > MAX_BYTES or any(len(t) > MAX_BYTES for t in tokens.values()):
        raise StoreError(f"a note or a token is at most {MAX_BYTES} bytes")
    target = directory / commitment.commitment_id
    if target.exists():
        if load(directory, commitment.commitment_id) != Saved(commitment.note, tokens):
            raise StoreError("this commitment is already saved with other contents")
        return target
    staging = directory / (commitment.commitment_id + ".new")
    staging.mkdir(parents=True, exist_ok=True)
    for stale in staging.iterdir():
        stale.unlink()
    atomic_write(staging / NOTE, commitment.note)
    for name, token in tokens.items():
        atomic_write(staging / (name + SUFFIX), token)
    os.replace(staging, target)
    return target


def load(directory: Path, commitment_id: str) -> Saved:
    """The saved note and tokens of a commitment, or an error if there are none or they are not whole."""
    if _ID.fullmatch(commitment_id) is None:
        raise StoreError("a commitment id is lower-case letters and digits")
    target = directory / commitment_id
    if not target.is_dir() or not (target / NOTE).is_file():
        raise StoreError("no such commitment is saved")
    tokens: dict[str, bytes] = {}
    for path in target.iterdir():
        if path.name == NOTE:
            continue
        name = path.name.removesuffix(SUFFIX)
        if not path.name.endswith(SUFFIX) or _NAME.fullmatch(name) is None:
            raise StoreError(f"{path.name} is not a file of a commitment")
        tokens[name] = path.read_bytes()
    return Saved((target / NOTE).read_bytes(), tokens)
