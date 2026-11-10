"""A forged copy of a cycle's bundle, made to show what the time-stamps are for.

Record integrity (a signed checkpoint, inclusion proofs) proves that the records of a bundle are the ones a log signed. It cannot prove
that the log is the one that existed when the cycle was committed: someone who holds the log's signing key can change a record, rebuild
every hash and sign the new checkpoint, and the result is internally consistent. The one thing such a person cannot rebuild is the
independent time-stamps, which were made over the original checkpoint's digest by authorities they do not control. This module makes that
forgery from a league's own ledger so that a person can see the verifier refuse it and read why.

The league's real key is never used. A checkpoint it signed over a changed log would be, for anyone who kept both, proof that the log
equivocates, and published evidence against our own log is not a demonstration. The forger is a stand-in whose key is derived from the
cycle's name alone (it is no secret and is used for nothing else), and the copy is checked against a root that is the real root with the
log's key replaced by the stand-in's (``demo_root``); every other part of that root, the time-stamp authorities included, is the real one.
So the one thing the demonstration shows is the one that matters: given the log's key, the time-stamps still fail.

``forge`` replays the ledger up to the stamped checkpoint into a new, separate ledger, changing the chance of the cycle's first up-or-down
forecast (one number, in the one field), signs the new checkpoint with the stand-in key and writes a bundle that carries the original
time-stamps. The replay is held to the original before the change (the root of everything before the changed record must be the original's),
so what the copy differs in is the one record and what follows from it. Nothing of the league is written: the work and the output are new
directories, and the original ledger is only read.

The result is a demonstration. It is published labelled as a forgery, never beside the honest bundles, and it says what was changed.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

from hoshitori import bundle, canonical, envelope
from hoshitori.checkpoint import CheckpointError, parse_text
from hoshitori.clock import ManualClock
from hoshitori.league import batch_root
from hoshitori.ledger import Ledger, LedgerError, LedgerView, strip_committed_at
from hoshitori.note import NoteError, Signer, VerifierKey, split_note
from hoshitori.timeutil import parse_rfc3339
from hoshitori.trusted_root import RootError, parse_root

FIELD: Final = "p"
BOLD: Final = "0.990000"
TIMID: Final = "0.010000"
CONFIDENT: Final = Decimal("0.95")  # a call this sure or surer is turned the other way, so the change is never a slight one
DOCUMENT: Final = "forgery.json"
FORMAT: Final = 1
DOCUMENT_REFUSAL: Final = "the forgery document is not one this version wrote"
STANDIN_SEED: Final = b"hoshitori forgery demonstration key: "
SEGMENT_RECORDS: Final = 5000
_DIGEST: Final = re.compile(r"[0-9a-f]{64}")


class ForgeryError(ValueError):
    """A forgery that cannot be made, or a document of one that cannot be read."""


@dataclass(frozen=True)
class Edit:
    record_id: str  # the forecast whose chance was changed
    field: str
    was: str
    now: str


@dataclass(frozen=True)
class Summary:
    """What a forgery document says: enough to show what was changed and to tell the two checkpoints apart."""

    cycle_id: str
    edit: Edit
    standin_vkey: str  # the key that signed the forged checkpoint, as a trusted root lists it
    size: int
    honest_root: str
    forged_root: str


@dataclass(frozen=True)
class Forged:
    cycle_id: str
    edit: Edit
    standin_vkey: str
    size: int  # of the stamped checkpoint, which the forged one matches
    honest_root: str  # the root of the log at that size, as stamped
    forged_root: str
    seqs: tuple[int, ...]  # the records the bundle holds

    def summary(self) -> Summary:
        return Summary(self.cycle_id, self.edit, self.standin_vkey, self.size, self.honest_root, self.forged_root)


def standin_signer(origin: str, cycle_id: str) -> Signer:
    """The forger of a cycle's demonstration: a key anyone can derive, named as the log is, used for nothing else."""
    return Signer.from_seed(origin, hashlib.sha256(STANDIN_SEED + cycle_id.encode()).digest())


def demo_root(real: bytes, origin: str, vkey: str) -> bytes:
    """The real trusted-root document with the log's key replaced by ``vkey`` for every size, and nothing else changed."""
    try:
        document = json.loads(real.decode("utf-8"))
        log = document["log"]
        if not isinstance(document, dict) or not isinstance(log, dict):
            raise ValueError("shape")
    except (UnicodeDecodeError, ValueError, KeyError, TypeError):
        raise ForgeryError("the real root cannot be read") from None
    if log.get("origin") != origin:
        raise ForgeryError("the root is not for this log")
    try:
        VerifierKey.from_vkey(vkey)
    except NoteError:
        raise ForgeryError("the stand-in key is not a key") from None
    made = canonical.dumps({**document, "log": {**log, "eras": [{"vkey": vkey, "from_size": 0, "through_size": None}]}})
    try:
        parse_root(made)
    except RootError as exc:
        raise ForgeryError(f"the demonstration root is not a root: {exc}") from exc
    return made


def new_value(was: Decimal) -> str:
    """The chance the forger writes: a bold lean up, or, where the call was already that confident, the opposite."""
    return TIMID if was >= CONFIDENT else BOLD


def _target(source: LedgerView, seqs: Sequence[int]) -> tuple[int, Edit]:
    for seq in sorted(seqs):
        record = canonical.parse_record(source.read_record(seq))
        body = record.get("body")
        chance = body.get(FIELD) if isinstance(body, dict) else None
        if record.get("type") == "forecast" and isinstance(chance, str):
            try:
                was = Decimal(chance)
            except InvalidOperation:
                continue
            if was.is_finite() and 0 < was < 1:  # a chance: nothing else is changed to look like one
                return seq, Edit(str(record["id"]), FIELD, chance, new_value(was))
    raise ForgeryError("the cycle has no up-or-down forecast to change")


def _empty(directory: Path, what: str) -> None:
    if directory.exists() and any(directory.iterdir()):
        raise ForgeryError(f"the {what} directory must be new or empty")


def _renamed_batch(record: dict[str, canonical.JsonValue], replayed: list[dict[str, canonical.JsonValue]]) -> None:
    """A cycle's committing step names the root of the batch before it. A forger who holds the log's key and changed a record of the batch
    would also rewrite that step, or the copy would be refused by the bundle's own batch check and the demonstration would not show what the
    time-stamps are for; so the step is rewritten to name the root of the batch as the forger changed it. A step that names no size (a cycle
    written before it did) is left as it is."""
    body = record.get("body")
    if record.get("type") != "cycle_state" or not isinstance(body, dict) or body.get("state") != "COMMITTING":
        return
    size = body.get("batch_size")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= len(replayed):
        return
    body["batch_root"] = batch_root(replayed[-size:])


def forge(
    source: LedgerView,
    *,
    signer: Signer,
    origin: str,
    note: bytes,
    tokens: Mapping[str, bytes],
    seqs: Sequence[int],
    cycle_id: str,
    work: Path,
    out: Path,
    segment_records: int = SEGMENT_RECORDS,
) -> Forged:
    """The forged copy of the bundle of ``seqs`` against ``note``, the checkpoint the ``tokens`` stamp, written to ``out``."""
    if not seqs:
        raise ForgeryError("a forgery needs the records of a bundle")
    if not tokens:
        raise ForgeryError("a forgery keeps the original time-stamps, and there are none")
    _empty(work, "work")
    _empty(out, "output")
    try:
        stamped = parse_text(split_note(note)[0])
    except (CheckpointError, NoteError) as exc:
        raise ForgeryError("the stamped checkpoint cannot be read") from exc
    if stamped.origin != origin or stamped.size > source.size or source.root(stamped.size) != stamped.root:
        raise ForgeryError("the stamped checkpoint is not of this ledger")
    for seq in seqs:
        if not 0 <= seq < stamped.size:
            raise ForgeryError(f"record {seq} is not in the ledger up to the stamped checkpoint")
    target, edit = _target(source, seqs)
    first = parse_rfc3339(str(canonical.parse_record(source.read_record(0))["committed_at"]))
    clock = ManualClock(first)
    replay = Ledger.open(work / "ledger", signer=signer, origin=origin, clock=clock, segment_records=segment_records)
    try:
        replayed: list[dict[str, canonical.JsonValue]] = []
        for seq in range(stamped.size):
            raw = source.read_record(seq)
            clock.set(parse_rfc3339(str(canonical.parse_record(raw)["committed_at"])))
            record = canonical.parse_record(strip_committed_at(raw))
            if seq == target:
                body = record["body"]
                assert isinstance(body, dict)
                body[FIELD] = edit.now
            _renamed_batch(record, replayed)
            replayed.append(record)
            try:
                replay.submit(record)
                replay.append_pending()
            except (LedgerError, envelope.EnvelopeError) as exc:
                raise ForgeryError(f"record {seq} cannot be replayed: {exc}") from exc
        if replay.size != stamped.size or (target > 0 and replay.root(target) != source.root(target)):
            raise ForgeryError("the ledger could not be replayed as it was before the change")
        forged_note = replay.checkpoint()
        forged_root = parse_text(split_note(forged_note)[0]).root
        try:
            bundle.export_bundle(out, replay, list(seqs), checkpoint_note=forged_note, size=stamped.size, tokens=tokens)
        except bundle.BundleError as exc:
            raise ForgeryError(f"the forged bundle cannot be written: {exc}") from exc
    finally:
        replay.close()
    return Forged(cycle_id, edit, signer.verifier.vkey(), stamped.size, stamped.root.hex(), forged_root.hex(), tuple(sorted(seqs)))


def document(forged: Forged | Summary) -> str:
    """The forgery document: what was changed, on which cycle, and the two roots; fixed order, ending in a newline."""
    shown = forged.summary() if isinstance(forged, Forged) else forged
    value = {
        "format": FORMAT,
        "cycle_id": shown.cycle_id,
        "edit": {"record": shown.edit.record_id, "field": shown.edit.field, "was": shown.edit.was, "now": shown.edit.now},
        "standin_vkey": shown.standin_vkey,
        "size": shown.size,
        "honest_root": shown.honest_root,
        "forged_root": shown.forged_root,
    }
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _text(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ForgeryError(DOCUMENT_REFUSAL)
    return value


def read_document(data: bytes) -> Summary:
    """The forgery document as written by :func:`document`, or a refusal: any other field, format or shape is not read."""
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ForgeryError(DOCUMENT_REFUSAL) from None
    keys = {"format", "cycle_id", "edit", "standin_vkey", "size", "honest_root", "forged_root"}
    if not isinstance(value, dict) or set(value) != keys or value["format"] != FORMAT or type(value["format"]) is not int:
        raise ForgeryError(DOCUMENT_REFUSAL)
    edit = value["edit"]
    if not isinstance(edit, dict) or set(edit) != {"record", "field", "was", "now"}:
        raise ForgeryError(DOCUMENT_REFUSAL)
    size = value["size"]
    roots = (value["honest_root"], value["forged_root"])
    if (
        type(size) is not int
        or size < 1
        or any(not isinstance(r, str) or _DIGEST.fullmatch(r) is None for r in roots)
        or roots[0] == roots[1]
    ):
        raise ForgeryError(DOCUMENT_REFUSAL)
    changed = Edit(_text(edit["record"]), _text(edit["field"]), _text(edit["was"]), _text(edit["now"]))
    if changed.was == changed.now:
        raise ForgeryError(DOCUMENT_REFUSAL)
    vkey = _text(value["standin_vkey"])
    try:
        VerifierKey.from_vkey(vkey)
    except NoteError:
        raise ForgeryError(DOCUMENT_REFUSAL) from None
    return Summary(_text(value["cycle_id"]), changed, vkey, size, roots[0], roots[1])
