"""The verification bundle: a directory anyone can check offline, and the writer that produces it.

Layout::

    items/<stem>.canonical.json   the record exactly as hashed (canonical form, with the writer's committed_at)
    items/<stem>.tlog-proof       C2SP tlog-proof: index, inclusion path and the signed checkpoint, verbatim
    tsa/<name>.tsr                RFC 3161 time-stamp tokens over SHA-256 of the checkpoint note text
    raw/<sha256>                  raw payloads cited by records (``body.raw_sha256``) where they may be redistributed
    publication.json              optional claims of public references (never authenticated offline)
    consistency.json              with a later part: the proof that the later checkpoint extends the stamped one

The items of a bundle are proven against the checkpoint the tokens stamp. A bundle may also hold records the log appended after
that checkpoint (the later steps of its forecasts), each proven against one later checkpoint, with ``consistency.json`` tying the
two together; it never holds a third. A bundle carries no trust of its
own: the verifier takes the log key, the pinned time-stamp roots and the operator registry from a trusted root supplied
from outside (``hoshitori.trusted_root``), and reads the committed time and any declared window from the proven
record, never from a file beside it.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from hoshitori import canonical
from hoshitori.ledger import LedgerView
from hoshitori.tlog_proof import TlogProof, format_proof

ITEMS: Final = "items"
TSA: Final = "tsa"
RAW: Final = "raw"
PUBLICATION: Final = "publication.json"
CONSISTENCY: Final = "consistency.json"
CONSISTENCY_FORMAT: Final = 1
CANONICAL_SUFFIX: Final = ".canonical.json"
PROOF_SUFFIX: Final = ".tlog-proof"
TOKEN_SUFFIX: Final = ".tsr"  # noqa: S105  (a file extension, not a secret)
STEM: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")  # matched with fullmatch: `$` would also accept a trailing newline
NAME: Final = STEM


class BundleError(ValueError):
    """A bundle cannot be written as asked."""


def item_stem(seq: int) -> str:
    return f"item-{seq:08d}"


@dataclass(frozen=True)
class Later:
    """The part of a bundle after the stamped checkpoint: a later signed note, its size, and the records proven under it."""

    note: bytes
    size: int
    seqs: tuple[int, ...]


def consistency_document(first: int, second: int, path: Sequence[bytes]) -> bytes:
    """The bytes of ``consistency.json``: the path that shows the tree of size ``second`` extends the tree of size ``first``."""
    document: dict[str, canonical.JsonValue] = {
        "format": CONSISTENCY_FORMAT,
        "from_size": first,
        "to_size": second,
        "hashes": [h.hex() for h in path],
    }
    return canonical.dumps(document)


def export_bundle(
    directory: Path,
    ledger: LedgerView,
    seqs: Sequence[int],
    *,
    checkpoint_note: bytes | None = None,
    size: int | None = None,
    tokens: Mapping[str, bytes] | None = None,
    raw: Mapping[str, bytes] | None = None,
    publication: Mapping[str, canonical.JsonValue] | None = None,
    later: Later | None = None,
) -> None:
    """Write a bundle proving the records at ``seqs`` against one signed checkpoint of ``ledger``.

    ``checkpoint_note`` must be the signed note whose text the tokens stamp; by default the ledger signs a fresh checkpoint of
    its current size. ``directory`` must be new or empty.
    """
    if not seqs:
        raise BundleError("a bundle proves at least one record")
    if directory.exists() and any(directory.iterdir()):
        raise BundleError("the bundle directory must be empty")
    note = checkpoint_note if checkpoint_note is not None else ledger.checkpoint()
    tree_size = ledger.size if size is None else size
    (directory / ITEMS).mkdir(parents=True, exist_ok=True)
    for seq in seqs:
        stem = item_stem(seq)
        record = ledger.read_record(seq)
        path = ledger.inclusion_proof(seq, tree_size)
        (directory / ITEMS / f"{stem}{CANONICAL_SUFFIX}").write_bytes(record)
        (directory / ITEMS / f"{stem}{PROOF_SUFFIX}").write_bytes(format_proof(TlogProof(seq, tuple(path), note)))
    if later is not None:
        if not later.seqs or later.size <= tree_size:
            raise BundleError("a later part holds at least one record and a checkpoint larger than the stamped one")
        for seq in later.seqs:
            if seq < tree_size or seq >= later.size:
                raise BundleError("a record of the later part lies after the stamped checkpoint and inside the later one")
            stem = item_stem(seq)
            record = ledger.read_record(seq)
            (directory / ITEMS / f"{stem}{CANONICAL_SUFFIX}").write_bytes(record)
            proof = TlogProof(seq, tuple(ledger.inclusion_proof(seq, later.size)), later.note)
            (directory / ITEMS / f"{stem}{PROOF_SUFFIX}").write_bytes(format_proof(proof))
        (directory / CONSISTENCY).write_bytes(consistency_document(tree_size, later.size, ledger.consistency_proof(tree_size, later.size)))
    if tokens:
        (directory / TSA).mkdir(exist_ok=True)
        for name, blob in tokens.items():
            if not NAME.fullmatch(name):
                raise BundleError(f"bad token name {name!r}")
            (directory / TSA / f"{name}{TOKEN_SUFFIX}").write_bytes(blob)
    if raw:
        (directory / RAW).mkdir(exist_ok=True)
        for digest, blob in raw.items():
            if hashlib.sha256(blob).hexdigest() != digest:
                raise BundleError(f"raw payload does not hash to its name {digest}")
            (directory / RAW / digest).write_bytes(blob)
    if publication is not None:
        (directory / PUBLICATION).write_text(json.dumps(publication, sort_keys=True, indent=1) + "\n", encoding="utf-8")
