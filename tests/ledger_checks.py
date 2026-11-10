"""Scenarios for the ledger, runnable against the real module or against a mutant of it.

Each scenario builds its own ledger in a temporary directory, does something to it (a normal run, a torn write, a
flipped byte, a lost index, a simulated crash) and reports which expectations failed. ``failures(mod)`` runs them
all; a mutant of ``hoshitori.ledger`` must fail at least one.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import shutil
import sqlite3
import tempfile
import types
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hoshitori import canonical, merkle, segment
from hoshitori.clock import ManualClock
from hoshitori.note import Signer

ORIGIN = "example.org/hoshitori-test-ledger"
SIGNER = Signer.from_seed(ORIGIN, bytes(range(32)))
START = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)

# The exact refusals for a corrupt final line and for an attestation. A complete final line that fails its check is corruption, whatever the
# index says (LEDGER_SPEC section 7 invariant 8); an attestation counts only as the signed checkpoint it was made over
# (LEDGER_SPEC section 7 invariant 9, ADR-026).
COMPLETE_BUT_INVALID = "segment {name}: line {seq} is complete but invalid ({detail}); not repaired"
INDEXED_BUT_INVALID = "segment {name}: line {seq} is damaged although it was indexed ({detail}); not repaired"
NOT_SIGNED = "no checkpoint of size {size} with this root was signed by this log: only a signed checkpoint can be attested"
UNBOUND = "attested.jsonl line {number} is not bound to a checkpoint signed by this log"


class SimulatedCrash(Exception):
    """Raised by a crash hook to stop the writer at an exact point; the object is then abandoned."""


def rec(i: int, source: str = "urn:hoshitori:test", extra: str = "") -> dict[str, canonical.JsonValue]:
    return {
        "spec": "hoshitori.record/1",
        "type": "evidence",
        "type_version": 1,
        "id": f"ev{i}",
        "source": source,
        "created_at": "2026-10-29T11:59:59.000Z",
        "body": {"n": i, "pad": extra},
    }


def open_ledger(mod: types.ModuleType, directory: Path, clock: ManualClock | None = None, records: int = 3, hook=None):  # type: ignore[no-untyped-def]
    return mod.Ledger.open(
        directory, signer=SIGNER, origin=ORIGIN, clock=clock or ManualClock(START), segment_records=records, crash_hook=hook
    )


def fill(led, n: int, start: int = 0) -> list:  # type: ignore[no-untyped-def]
    receipts = [led.submit(rec(i)) for i in range(start, start + n)]
    led.append_pending()
    return receipts


def segments(d: Path) -> list[Path]:
    return sorted((d / "segments").iterdir())


def last_line_detail(seg: Path) -> str:
    """What the segment module says about the damaged final line of ``seg`` (the first and only segment of a log)."""
    return segment.scan_segment(seg.read_bytes(), segment.segment_first_seq(seg.name), None).detail


def rewrite_attested(d: Path, change: Callable[[dict[str, canonical.JsonValue]], dict[str, canonical.JsonValue]]) -> None:
    """Edit every complete entry of a log's ``attested.jsonl``; a torn final line is kept as it is."""
    path = d / "attested.jsonl"
    *complete, torn = path.read_bytes().split(b"\n")
    path.write_bytes(b"".join(canonical.dumps(change(canonical.parse_record(line))) + b"\n" for line in complete) + torn)


def remove(d: Path, *names: str) -> None:
    for name in names:
        (d / name).unlink(missing_ok=True)


INDEX_FILES = ("index.db", "index.db-wal", "index.db-shm")


def all_lines(d: Path) -> list[bytes]:
    return [line for p in segments(d) for line in p.read_bytes().splitlines()]


def record_ids(d: Path) -> list[str]:
    ids = []
    for line in all_lines(d):
        wrapper = canonical.parse_record(line)
        if "rec" in wrapper:
            ids.append(str(canonical.parse_record(str(wrapper["rec"]).encode())["id"]))
    return ids


def ledger_dump(d: Path) -> list[tuple]:  # type: ignore[type-arg]
    db = sqlite3.connect(d / "index.db")
    try:
        return db.execute("SELECT seq, source, rec_id, leaf, segment, off, len FROM ledger ORDER BY seq").fetchall()
    finally:
        db.close()


def invariants(d: Path, led) -> list[str]:  # type: ignore[no-untyped-def]
    """The recovery invariant: no duplicate line, gapless sequence, valid leaf hashes, index equal to a rebuild."""
    bad: list[str] = []
    ids = record_ids(d)
    if len(ids) != len(set(ids)):
        bad.append("duplicate record in the segments")
    seq = 0
    previous = None
    for p in segments(d):
        first = segment.segment_first_seq(p.name)
        if first != seq:
            bad.append("segment names do not continue the sequence")
        scan = segment.scan_segment(p.read_bytes(), first, previous)
        if scan.problem is not segment.Problem.NONE:
            bad.append(f"{p.name}: {scan.detail}")
        seq += len(scan.records)
        previous = (p.name, hashlib.sha256(p.read_bytes()).digest(), len(scan.records))
    if led.size != seq:
        bad.append(f"tree size {led.size} differs from the records in the segments {seq}")
    rows = ledger_dump(d)
    if [r[0] for r in rows] != list(range(seq)):
        bad.append("index is not gapless")
    return bad


# ---------------------------------------------------------------------------------------------------------------------
def basic_api(mod, tmp: Path) -> list[str]:  # type: ignore[no-untyped-def]
    bad: list[str] = []
    clock = ManualClock(START)
    led = open_ledger(mod, tmp, clock)
    receipts = [led.submit(rec(i)) for i in range(7)]
    if led.submit(rec(3)).receipt_id != receipts[3].receipt_id:
        bad.append("submitting the same record twice gave a different receipt")
    try:
        led.submit(rec(3, extra="different"))
        bad.append("a conflicting record under one (source, id) was accepted")
    except mod.ConflictError:
        pass
    different_source = led.submit(rec(3, source="urn:hoshitori:other"))
    if led.append_pending() != 8:
        bad.append("append_pending did not append every queued record")
    try:
        led.submit(rec(3, extra="after the append"))
        bad.append("a conflicting record for an already committed (source, id) was accepted")
    except mod.ConflictError:
        pass
    if led.submit(rec(3)).receipt_id != receipts[3].receipt_id:
        bad.append("re-submitting a committed record did not return its receipt")
    if different_source.receipt_id == receipts[3].receipt_id:
        bad.append("(source, id) is not the dedupe key")
    for field_name in ("committed_at",):
        try:
            led.submit({**rec(99), field_name: "2026-10-29T12:00:00.000Z"})
            bad.append("a producer-supplied committed_at was accepted")
        except mod.envelope.EnvelopeError:
            pass
    if led.append_pending() != 0:
        bad.append("append_pending appended twice")
    if led.size != 8:
        bad.append(f"wrong size {led.size}")
    try:
        first = canonical.parse_record(led.read_record(0))
        if first.get("committed_at") != "2026-10-29T12:00:00.000Z":
            bad.append("committed_at not set from the clock")
    except Exception as exc:
        bad.append(f"read_record: {type(exc).__name__}")
    note = led.checkpoint()
    if led.checkpoint() != note:
        bad.append("checkpoint is not idempotent for an unchanged head")
    cp = mod.verify_checkpoint(note, [SIGNER.verifier], ORIGIN)
    if cp.size != 8 or cp.root != led.root():
        bad.append("checkpoint does not describe the head")
    for index in range(8):
        if not merkle.verify_inclusion(led.leaf_hash_at(index), index, 8, led.inclusion_proof(index), cp.root):
            bad.append(f"inclusion proof {index} does not verify")
    led.submit(rec(20))
    led.append_pending()
    note2 = led.checkpoint()
    cp2 = mod.verify_checkpoint(note2, [SIGNER.verifier], ORIGIN)
    if not merkle.verify_consistency(8, 9, led.consistency_proof(8), cp.root, cp2.root):
        bad.append("consistency proof between checkpoints does not verify")
    # time: monotone, tolerant of a small step back, stopped by a larger one
    clock.advance(timedelta(seconds=5))
    led.submit(rec(21))
    led.append_pending()
    clock.advance(timedelta(milliseconds=-500))
    led.submit(rec(22))
    led.append_pending()
    times = [canonical.parse_record(led.read_record(i))["committed_at"] for i in range(led.size)]
    if times != sorted(times):
        bad.append("committed_at decreased in ledger order")
    clock.advance(timedelta(seconds=-10))
    led.submit(rec(23))
    try:
        led.append_pending()
        bad.append("a clock that moved back by 10 s did not stop the writer")
    except mod.ClockError:
        pass
    try:
        open_ledger(mod, tmp)
        bad.append("a second writer opened the same directory")
    except mod.LockError:
        pass
    led.close()
    return bad


def rotation_and_reopen(mod, tmp: Path) -> list[str]:  # type: ignore[no-untyped-def]
    bad: list[str] = []
    led = open_ledger(mod, tmp, records=3)
    fill(led, 10)
    names = [p.name for p in segments(tmp)]
    if names != [segment.segment_name(n) for n in (0, 3, 6, 9)]:
        bad.append(f"unexpected segments {names}")
    root = led.root()
    led.close()
    again = open_ledger(mod, tmp, records=3)
    if again.size != 10 or again.root() != root or again.state != "OK":
        bad.append("reopen changed the log")
    fill(again, 2, start=10)
    bad += invariants(tmp, again)
    again.close()
    # a closed segment altered after the fact breaks the chain
    first = segments(tmp)[0]
    data = bytearray(first.read_bytes())
    data[30] ^= 1
    first.write_bytes(bytes(data))
    broken = open_ledger(mod, tmp, records=3)
    if broken.state != "INTEGRITY_FAILURE":
        bad.append("a damaged closed segment was not detected")
    broken.abandon()
    return bad


def torn_and_damaged_tails(mod, tmp: Path) -> list[str]:  # type: ignore[no-untyped-def]
    bad: list[str] = []
    base = tmp / "torn"
    led = open_ledger(mod, base, records=100)
    fill(led, 4)
    led.abandon()
    seg = segments(base)[-1]
    line = segment.encode_record_line(4, canonical.dumps({**rec(4), "committed_at": "2026-10-29T12:00:00.000Z"}))
    seg.write_bytes(seg.read_bytes() + line[: len(line) // 2])
    after = open_ledger(mod, base, records=100)
    if after.state != "OK" or after.report.truncated_bytes != len(line) // 2:
        bad.append(f"torn tail not truncated cleanly: {after.state} {after.report.truncated_bytes}")
    after.submit(rec(5))
    after.append_pending()
    bad += invariants(base, after)
    after.abandon()

    # a complete but invalid final line is corruption even when the index has no row for it: a write cut short leaves no newline, and
    # the index is a rebuildable cache that cannot say the line was never acknowledged (finding R-INDEX)
    base2 = tmp / "unindexed-last"
    led = open_ledger(mod, base2, records=100)
    fill(led, 4)
    led.abandon()
    seg = segments(base2)[-1]
    bogus = line.replace(b"ev4", b"ev9")  # valid JSON, wrong leaf hash
    seg.write_bytes(seg.read_bytes() + bogus)
    damaged = seg.read_bytes()
    wanted = COMPLETE_BUT_INVALID.format(name=seg.name, seq=4, detail=last_line_detail(seg))
    after = open_ledger(mod, base2, records=100)
    if (after.state, after.state_detail) != ("INTEGRITY_FAILURE", wanted):
        bad.append(f"an unindexed complete but invalid final line: {after.state} {after.state_detail!r}, expected {wanted!r}")
    if seg.read_bytes() != damaged:
        bad.append("an unindexed complete but invalid final line was truncated")
    after.abandon()

    # the same damage on a line that was indexed (acknowledged) is corruption, not a torn write
    base3 = tmp / "indexed-last"
    led = open_ledger(mod, base3, records=100)
    fill(led, 4)
    led.abandon()
    seg = segments(base3)[-1]
    raw = bytearray(seg.read_bytes())
    raw[-12] ^= 1
    seg.write_bytes(bytes(raw))
    wanted = INDEXED_BUT_INVALID.format(name=seg.name, seq=3, detail=last_line_detail(seg))
    after = open_ledger(mod, base3, records=100)
    if after.state != "INTEGRITY_FAILURE":
        bad.append("damage to an acknowledged final line was treated as a torn write")
    if after.state_detail != wanted:
        bad.append(f"damage to an indexed final line is described as {after.state_detail!r}, expected {wanted!r}")
    if seg.read_bytes() != bytes(raw):
        bad.append("the damaged segment was modified")
    after.abandon()

    # R-INDEX: an acknowledged final line flipped by one bit, with the index lost and no checkpoint over it, is still not truncated
    base4 = tmp / "indexed-last-index-lost"
    led = open_ledger(mod, base4, records=100)
    acknowledged = fill(led, 4)
    led.abandon()
    remove(base4, *INDEX_FILES)
    seg = segments(base4)[-1]
    raw = bytearray(seg.read_bytes())
    raw[-12] ^= 1
    seg.write_bytes(bytes(raw))
    wanted = COMPLETE_BUT_INVALID.format(name=seg.name, seq=3, detail=last_line_detail(seg))
    after = open_ledger(mod, base4, records=100)
    if (after.state, after.state_detail) != ("INTEGRITY_FAILURE", wanted):
        bad.append(f"a damaged acknowledged final line with the index lost: {after.state} {after.state_detail!r}, expected {wanted!r}")
    if seg.read_bytes() != bytes(raw) or len(acknowledged) != 4:
        bad.append("a damaged acknowledged final line was truncated because the index had been lost")
    after.abandon()
    return bad


def mid_file_corruption(mod, tmp: Path) -> list[str]:  # type: ignore[no-untyped-def]
    """a flipped byte in a middle line is corruption; nothing after it may be truncated or lost."""
    bad: list[str] = []
    led = open_ledger(mod, tmp, records=100)
    fill(led, 10)
    led.abandon()
    seg = segments(tmp)[0]
    lines = seg.read_bytes().split(b"\n")
    cut = sum(len(x) + 1 for x in lines[:3]) + 20
    raw = bytearray(seg.read_bytes())
    raw[cut] ^= 1
    seg.write_bytes(bytes(raw))
    db = sqlite3.connect(tmp / "index.db", isolation_level=None)
    db.execute("DELETE FROM outbox")  # completed outbox rows pruned, as production retention would do
    db.close()
    after = open_ledger(mod, tmp, records=100)
    if after.state != "INTEGRITY_FAILURE":
        bad.append("mid-file corruption was not reported")
    if seg.read_bytes() != bytes(raw):
        bad.append("mid-file corruption was 'repaired' by truncating valid later lines")
    try:
        after.submit(rec(50))
        bad.append("a log in INTEGRITY_FAILURE accepted a submission")
    except mod.IntegrityFailure:
        pass
    after.abandon()
    reopened = open_ledger(mod, tmp, records=100)
    if reopened.state != "INTEGRITY_FAILURE":
        bad.append("INTEGRITY_FAILURE did not persist across a restart")
    if seg.read_bytes() != bytes(raw):
        bad.append("a restart modified a log in INTEGRITY_FAILURE")
    for call in (reopened.append_pending, reopened.checkpoint):
        try:
            call()
            bad.append(f"{call.__name__} worked in INTEGRITY_FAILURE")
        except mod.IntegrityFailure:
            pass
    reopened.abandon()
    return bad


def prefix_protection(mod, tmp: Path) -> list[str]:  # type: ignore[no-untyped-def]
    bad: list[str] = []
    # rolled back below a signed checkpoint
    a = tmp / "rollback"
    led = open_ledger(mod, a, records=100)
    fill(led, 6)
    led.checkpoint()
    fill(led, 2, start=6)
    led.abandon()
    seg = segments(a)[0]
    lines = seg.read_bytes().split(b"\n")
    seg.write_bytes(b"\n".join(lines[:4]) + b"\n")
    after = open_ledger(mod, a, records=100)
    if after.state != "INTEGRITY_FAILURE":
        bad.append("a log rolled back below its checkpoint resumed")
    after.abandon()

    # rewritten prefix with valid lines (a perfect forgery of one record) keeps the size but changes the root
    b = tmp / "rewrite"
    led = open_ledger(mod, b, records=100)
    fill(led, 6)
    led.checkpoint()
    led.abandon()
    for name in ("index.db", "index.db-wal", "index.db-shm"):  # without the index only the signed checkpoint can object
        (b / name).unlink(missing_ok=True)
    seg = segments(b)[0]
    lines = seg.read_bytes().split(b"\n")
    forged_record = canonical.dumps({**rec(2, extra="forged"), "committed_at": "2026-10-29T12:00:00.000Z"})
    lines[2] = segment.encode_record_line(2, forged_record).rstrip(b"\n")
    seg.write_bytes(b"\n".join(lines))
    after = open_ledger(mod, b, records=100)
    if after.state != "INTEGRITY_FAILURE":
        bad.append("a rewritten prefix was accepted although a signed checkpoint covers it")
    after.abandon()

    # an externally attested checkpoint survives the loss of the index (and of the signed checkpoints, so that only it can object)
    c = tmp / "attested"
    led = open_ledger(mod, c, records=100)
    fill(led, 5)
    led.checkpoint()
    led.register_attested(5, led.root(), "test witness")
    fill(led, 2, start=5)
    led.abandon()
    remove(c, *INDEX_FILES, "checkpoints.jsonl")
    seg = segments(c)[0]
    lines = seg.read_bytes().split(b"\n")
    lines[1] = segment.encode_record_line(
        1, canonical.dumps({**rec(1, extra="forged"), "committed_at": "2026-10-29T12:00:00.000Z"})
    ).rstrip(b"\n")
    seg.write_bytes(b"\n".join(lines))
    after = open_ledger(mod, c, records=100)
    if after.state != "INTEGRITY_FAILURE":
        bad.append("an attestation was forgotten with the index, so a rewritten prefix was accepted")
    after.abandon()

    # an attested checkpoint larger than what is left (the log lost its tail) fails closed
    e = tmp / "attested-rolled-back"
    led = open_ledger(mod, e, records=100)
    fill(led, 5)
    led.checkpoint()
    led.register_attested(5, led.root(), "test witness")
    led.abandon()
    remove(e, *INDEX_FILES, "checkpoints.jsonl")
    seg = segments(e)[0]
    seg.write_bytes(b"\n".join(seg.read_bytes().split(b"\n")[:3]) + b"\n")
    after = open_ledger(mod, e, records=100)
    if after.state != "INTEGRITY_FAILURE":
        bad.append("a log shorter than an attested checkpoint resumed")
    after.abandon()

    # registering an attestation that does not match fails closed
    d = tmp / "bad-attestation"
    led = open_ledger(mod, d, records=100)
    fill(led, 3)
    try:
        led.register_attested(3, b"\x01" * 32, "wrong")
        bad.append("a mismatching attestation was accepted")
    except mod.IntegrityFailure:
        pass
    if led.state != "INTEGRITY_FAILURE":
        bad.append("a mismatching attestation did not stop the log")
    led.abandon()
    again = open_ledger(mod, d, records=100)
    if again.state != "INTEGRITY_FAILURE":
        bad.append("a failure that left no trace in the segments was forgotten on restart")
    again.abandon()
    return bad


def forge_line_one(d: Path) -> bytes:
    """Rewrite record 1 of a log's only segment with valid lines (a perfect forgery); returns the root of the forged history."""
    seg = segments(d)[0]
    lines = seg.read_bytes().split(b"\n")
    forged = canonical.dumps({**rec(1, extra="forged"), "committed_at": "2026-10-29T12:00:00.000Z"})
    lines[1] = segment.encode_record_line(1, forged).rstrip(b"\n")
    seg.write_bytes(b"\n".join(lines))
    return merkle.MerkleTree(r.leaf for r in segment.scan_segment(seg.read_bytes(), 0, None).records).root()


def attestation_binding(mod, tmp: Path) -> list[str]:  # type: ignore[no-untyped-def]
    """Finding R-ATTEST: an attestation is believed only as the checkpoint this log signed and an outside party stamped.

    LEDGER_SPEC section 7 invariant 9 and ADR-026 compare the segment with the highest externally attested checkpoint; the note text is
    what the authorities stamp (LEDGER_SPEC section 5). So an entry of ``attested.jsonl`` carries that signed note, and recovery and a
    restore refuse an entry whose note does not verify under the log key or names another size or root. Before this, the file was plain
    JSON: rewritten together with the segments (signed checkpoints and index removed), it vouched for the rewritten history.
    """
    bad: list[str] = []

    # only a checkpoint this log signed can be attested
    a = tmp / "never-signed"
    led = open_ledger(mod, a, records=100)
    fill(led, 3)
    try:
        led.register_attested(3, led.root(), "test witness")
        bad.append("an attestation of a checkpoint the log never signed was accepted")
    except mod.IntegrityFailure as exc:
        bad.append(f"an attestation of a checkpoint the log never signed stopped the log: {exc}")
    except mod.LedgerError as exc:
        if str(exc) != NOT_SIGNED.format(size=3):
            bad.append(f"an attestation of a checkpoint the log never signed is refused as {str(exc)!r}")
    if led.state != "OK" or (a / "attested.jsonl").exists():
        bad.append("a refused attestation changed the log")
    led.abandon()

    # the entry carries the signed note it was made over, and that is enough for a restore with no other commitment
    b = tmp / "bound"
    led = open_ledger(mod, b, records=100)
    fill(led, 4)
    note = led.checkpoint()
    root = led.root()
    led.register_attested(4, root, "test witness")
    led.abandon()
    entries = [canonical.parse_record(x) for x in (b / "attested.jsonl").read_bytes().splitlines()]
    wanted_entry = {"evidence": "test witness", "note": base64.b64encode(note).decode("ascii"), "root": root.hex(), "size": 4}
    if entries != [wanted_entry]:
        bad.append(f"the attested entries are {entries}, expected the entry with its signed note {wanted_entry}")
    again = open_ledger(mod, b, records=100)
    if again.state != "OK":
        bad.append(f"a log with a bound attestation did not open: {again.state_detail}")
    again.abandon()
    shutil.copytree(b, tmp / "bound-replica", ignore=shutil.ignore_patterns("LOCK"))
    remove(b, "checkpoints.jsonl")
    try:
        if mod.restore_from_replica(b, tmp / "bound-replica", signer=SIGNER, origin=ORIGIN) != 4:
            bad.append("a restore against a bound attestation restored the wrong number of records")
    except Exception as exc:
        bad.append(f"a restore against a bound attestation alone was refused: {type(exc).__name__}: {exc}")

    # A6 R-ATTEST: the segments and attested.jsonl rewritten together, the signed checkpoints and the index removed
    c = tmp / "rewritten"
    led = open_ledger(mod, c, records=100)
    fill(led, 5)
    led.checkpoint()
    led.register_attested(5, led.root(), "test witness")
    led.abandon()
    remove(c, *INDEX_FILES, "checkpoints.jsonl")
    forged_root = forge_line_one(c)
    shutil.copytree(c, tmp / "rewritten-replica", ignore=shutil.ignore_patterns("LOCK"))  # the forged history, offered as a replica
    rewrite_attested(c, lambda entry: {**entry, "root": forged_root.hex()})
    after = open_ledger(mod, c, records=100)
    wanted = UNBOUND.format(number=1)
    if (after.state, after.state_detail) != ("INTEGRITY_FAILURE", wanted):
        bad.append(f"a rewritten attestation over a rewritten prefix: {after.state} {after.state_detail!r}, expected {wanted!r}")
    after.abandon()
    try:
        mod.restore_from_replica(c, tmp / "rewritten-replica", signer=SIGNER, origin=ORIGIN)
        bad.append("a replica was restored against an attestation that no signed checkpoint vouches for")
    except mod.IntegrityFailure as exc:
        if str(exc) != wanted:
            bad.append(f"a restore against an unbound attestation is refused as {str(exc)!r}, expected {wanted!r}")

    # an entry without its note is refused even when it names the true root; a torn final line was never acknowledged and is skipped
    d = tmp / "without-note"
    led = open_ledger(mod, d, records=100)
    fill(led, 3)
    led.checkpoint()
    led.register_attested(3, led.root(), "test witness")
    led.abandon()
    with open(d / "attested.jsonl", "ab") as handle:
        handle.write(b'{"evidence":"half a line')
    again = open_ledger(mod, d, records=100)
    if again.state != "OK":
        bad.append(f"a torn final line of attested.jsonl stopped the log: {again.state_detail}")
    again.abandon()
    # an entry as the log wrote it before entries carried their note: it is bound through the signed checkpoint the log keeps beside it
    # (the same proof as a note: only the log key signs one), so a log written before the change still opens; with that checkpoint gone
    # nothing proves the entry, and the log fails closed
    rewrite_attested(d, lambda entry: {k: v for k, v in entry.items() if k != "note"})
    older = open_ledger(mod, d, records=100)
    if older.state != "OK":
        bad.append(
            f"an entry written before entries carried their note, with its signed checkpoint kept: {older.state} {older.state_detail!r}"
        )
    older.abandon()
    remove(d, "checkpoints.jsonl")
    after = open_ledger(mod, d, records=100)
    if (after.state, after.state_detail) != ("INTEGRITY_FAILURE", wanted):
        bad.append(
            f"an attestation without its signed note or a signed checkpoint: {after.state} {after.state_detail!r}, expected {wanted!r}"
        )
    after.abandon()
    # and a note-less entry naming a root no signed checkpoint has is refused, the checkpoints kept
    e = tmp / "without-note-forged"
    led = open_ledger(mod, e, records=100)
    fill(led, 3)
    led.checkpoint()
    led.register_attested(3, led.root(), "test witness")
    led.abandon()
    rewrite_attested(e, lambda entry: {"evidence": entry["evidence"], "root": "00" * 32, "size": entry["size"]})
    forged = open_ledger(mod, e, records=100)
    if (forged.state, forged.state_detail) != ("INTEGRITY_FAILURE", wanted):
        bad.append(f"a note-less entry no signed checkpoint vouches for: {forged.state} {forged.state_detail!r}, expected {wanted!r}")
    forged.abandon()
    return bad


def index_and_journal_recovery(mod, tmp: Path) -> list[str]:  # type: ignore[no-untyped-def]
    bad: list[str] = []
    a = tmp / "lost-index"
    led = open_ledger(mod, a, records=4)
    fill(led, 9)
    led.checkpoint()
    before = ledger_dump(a)
    led.abandon()
    for name in ("index.db", "index.db-wal", "index.db-shm"):
        (a / name).unlink(missing_ok=True)
    after = open_ledger(mod, a, records=4)
    if after.state != "OK" or ledger_dump(a) != before:
        bad.append("rebuilding the index from the segments did not give identical rows")
    fill(after, 1, start=9)
    bad += invariants(a, after)
    after.abandon()

    b = tmp / "tail-cut"
    led = open_ledger(mod, b, records=100)
    fill(led, 5)
    led.abandon()
    seg = segments(b)[0]
    lines = seg.read_bytes().split(b"\n")
    seg.write_bytes(b"\n".join(lines[:3]) + b"\n")  # committed rows lost from the segment, still in the index
    after = open_ledger(mod, b, records=100)
    if after.state != "OK" or after.size != 3 or after.report.removed_index_rows != 2:
        bad.append(f"index rows beyond the segment were not removed: {after.state} {after.size} {after.report}")
    after.append_pending()
    if sorted(record_ids(b)) != [f"ev{i}" for i in range(5)] or after.size != 5:
        bad.append("records lost from an unattested tail were not re-appended from the outbox")
    bad += invariants(b, after)
    after.abandon()

    c = tmp / "journal"
    led = open_ledger(mod, c, records=100)
    receipts = [led.submit(rec(i)) for i in range(3)]
    led.abandon()
    db = sqlite3.connect(c / "index.db", isolation_level=None)
    db.execute("DELETE FROM outbox")  # SQLite restored from an older backup
    db.close()
    after = open_ledger(mod, c, records=100)
    if after.report.replayed_intents != 3:
        bad.append(f"journal intents were not replayed: {after.report.replayed_intents}")
    after.append_pending()
    if sorted(record_ids(c)) != ["ev0", "ev1", "ev2"] or len(receipts) != 3:
        bad.append("acknowledged commands were lost after the outbox was restored from an older backup")
    after.abandon()
    return bad


def submit_many_is_all_or_nothing(mod, tmp: Path) -> list[str]:  # type: ignore[no-untyped-def]
    """``submit_many`` acknowledges a group of commands together: one journal write, so that a crash leaves all of them or none, and a
    refusal of any one leaves none queued (a step of a cycle that is half acknowledged is what a restart could not finish)."""
    bad: list[str] = []
    a = tmp / "group"
    led = open_ledger(mod, a, records=100)
    records = [rec(i) for i in range(5)]
    receipts = led.submit_many(records)
    single = open_ledger(mod, tmp / "single", records=100)
    if [r.receipt_id for r in receipts] != [single.submit(r).receipt_id for r in records]:
        bad.append("a group's receipts are not the receipts the commands would have had one by one")
    single.abandon()
    if led.pending() != 5 or led.submit_many([]) != []:
        bad.append("a group is not queued whole, or an empty group is not an empty answer")
    journal = a / "journal.jsonl"
    size = journal.stat().st_size
    if len(journal.read_bytes().splitlines()) != 1:
        bad.append("a group is not one journal line")
    again = led.submit_many(records)
    if again != receipts or journal.stat().st_size != size:
        bad.append("the same group again did not give the same receipts and write nothing")
    mixed = led.submit_many([records[0], rec(5)])
    if len(mixed) != 2 or led.pending() != 6 or len(journal.read_bytes().splitlines()) != 2:
        bad.append("a group of one known and one new command was not journaled as the one new command")
    led.append_pending()
    if record_ids(a) != [f"ev{i}" for i in (0, 1, 2, 3, 4, 5)]:
        bad.append(f"a group is not appended in the order given: {record_ids(a)}")
    # a refusal of any one leaves none queued, and nothing journaled
    before = journal.read_bytes()
    clash = {**rec(0), "body": {"n": 0, "pad": "changed"}}
    invalid = {"spec": "not a record"}
    for label, group in (
        ("a command that conflicts with a record in the log", [rec(6), rec(7), clash]),
        ("a command that is not a record", [rec(6), invalid]),  # type: ignore[list-item]
        ("a command twice", [rec(6), rec(6)]),
    ):
        try:
            led.submit_many(group)
            bad.append(f"{label}: was accepted")
        except (mod.LedgerError, ValueError):
            pass
        if led.pending() != 0 or journal.read_bytes() != before:
            bad.append(f"{label}: left something queued or journaled")
    led.abandon()
    # a crash after the journal write loses none of the group; a torn journal line loses all of it, and nothing else
    b = tmp / "crash"
    hit = {"n": 0}

    def hook(name: str) -> None:
        if name == "after_journal_before_outbox":
            hit["n"] += 1
            if hit["n"] == 2:
                raise SimulatedCrash(name)

    led = open_ledger(mod, b, records=100, hook=hook)
    led.submit(rec(0))
    led.append_pending()
    with contextlib.suppress(SimulatedCrash):
        led.submit_many([rec(i) for i in range(1, 5)])
    led.abandon()
    after = open_ledger(mod, b, records=100)
    if after.state != "OK" or after.pending() != 4:
        bad.append(f"after a crash following the journal write, {after.pending()} of the group's 4 were queued")
    after.append_pending()
    if record_ids(b) != [f"ev{i}" for i in range(5)]:
        bad.append(f"the group was not appended whole after the crash: {record_ids(b)}")
    after.abandon()
    c = tmp / "torn"
    led = open_ledger(mod, c, records=100)
    led.submit(rec(0))
    led.append_pending()
    led.submit_many([rec(i) for i in range(1, 5)])
    led.abandon()
    journal = c / "journal.jsonl"
    journal.write_bytes(journal.read_bytes()[:-20])  # the group's line is cut short
    db = sqlite3.connect(c / "index.db", isolation_level=None)
    db.execute("DELETE FROM outbox")
    db.close()
    after = open_ledger(mod, c, records=100)
    if after.report.replayed_intents != 0 or after.pending() != 0:
        bad.append("a torn group was partly replayed")
    after.append_pending()
    if record_ids(c) != ["ev0"]:
        bad.append("a torn group left some of its records in the log")
    after.abandon()
    return bad


def index_disagreement(mod, tmp: Path) -> list[str]:  # type: ignore[no-untyped-def]
    """A segment rewritten under an intact index (no checkpoint covers it) is corruption, not something to reindex."""
    bad: list[str] = []
    led = open_ledger(mod, tmp, records=100)
    fill(led, 5)
    led.abandon()
    seg = segments(tmp)[0]
    lines = seg.read_bytes().split(b"\n")
    forged = canonical.dumps({**rec(1, extra="forged"), "committed_at": "2026-10-29T12:00:00.000Z"})
    lines[1] = segment.encode_record_line(1, forged).rstrip(b"\n")
    seg.write_bytes(b"\n".join(lines))
    after = open_ledger(mod, tmp, records=100)
    if after.state != "INTEGRITY_FAILURE":
        bad.append("a rewritten segment under an intact index was reindexed")
    after.abandon()
    return bad


def lost_commands(mod, tmp: Path) -> list[str]:  # type: ignore[no-untyped-def]
    """An acknowledged command that is in neither the ledger, the outbox nor the journal becomes a FAILED_LOST record."""
    bad: list[str] = []
    led = open_ledger(mod, tmp, records=100)
    kept = fill(led, 2)  # committed
    lost = [led.submit(rec(i)) for i in range(2, 5)]  # acknowledged, never appended
    led.abandon()
    (tmp / "journal.jsonl").unlink()
    for name in ("index.db", "index.db-wal", "index.db-shm"):
        (tmp / name).unlink(missing_ok=True)
    after = open_ledger(mod, tmp, records=100)
    found = after.reconcile_receipts([*kept, *lost])
    if sorted(r.record_id for r in found) != ["ev2", "ev3", "ev4"]:
        bad.append(f"wrong receipts reported lost: {[r.record_id for r in found]}")
    types = []
    for seq in range(after.size):
        record = canonical.parse_record(after.read_record(seq))
        types.append(str(record["type"]))
        if record["type"] == "failed_lost" and "reason" not in record["body"]:  # type: ignore[operator]
            bad.append("a failed_lost record carries no reason")
    if types.count("failed_lost") != 3 or types.count("evidence") != 2:
        bad.append(f"unexpected records after reconciliation: {types}")
    if after.reconcile_receipts([*kept, *lost]):
        bad.append("reconciling twice reported the same loss again as new")
    bad += invariants(tmp, after)
    after.abandon()
    return bad


def replica_restore(mod, tmp: Path) -> list[str]:  # type: ignore[no-untyped-def]
    bad: list[str] = []
    primary, replica = tmp / "primary", tmp / "replica"
    led = open_ledger(mod, primary, records=4)
    fill(led, 8)
    led.checkpoint()
    led.abandon()
    shutil.copytree(primary, replica, ignore=shutil.ignore_patterns("LOCK", "quarantine"))
    # damage the primary in the middle of a segment
    seg = segments(primary)[0]
    raw = bytearray(seg.read_bytes())
    raw[40] ^= 1
    seg.write_bytes(bytes(raw))
    broken = open_ledger(mod, primary, records=4)
    if broken.state != "INTEGRITY_FAILURE":
        bad.append("setup: the damaged primary was not detected")
    broken.abandon()

    forged = tmp / "forged"  # a clean log of the same length with different content: another history
    other = open_ledger(mod, forged, records=4)
    for i in range(8):
        other.submit(rec(i, extra="another history"))
    other.append_pending()
    other.abandon()
    try:
        mod.restore_from_replica(primary, forged, signer=SIGNER, origin=ORIGIN)
        bad.append("a replica that contradicts a signed checkpoint was accepted")
    except mod.IntegrityFailure:
        pass

    short = tmp / "short"
    shutil.copytree(replica, short)
    for p in segments(short)[1:]:
        p.unlink()
    try:
        mod.restore_from_replica(primary, short, signer=SIGNER, origin=ORIGIN)
        bad.append("a replica shorter than a signed checkpoint was accepted")
    except (mod.IntegrityFailure, mod.LedgerError):
        pass

    count = mod.restore_from_replica(primary, replica, signer=SIGNER, origin=ORIGIN)
    if count != 8:
        bad.append(f"restored {count} records, expected 8")
    after = open_ledger(mod, primary, records=4)
    if after.state != "OK" or after.size != 8:
        bad.append(f"the restored log did not open cleanly: {after.state} {after.size}")
    kept = list((primary / "quarantine").rglob("segment-*.jsonl"))
    if not kept:
        bad.append("the damaged segments were not kept in quarantine")
    bad += invariants(primary, after)
    fill(after, 1, start=8)
    after.abandon()

    bare = tmp / "bare"
    led = open_ledger(mod, bare, records=4)
    fill(led, 4)  # never checkpointed, never attested
    led.abandon()
    shutil.copytree(bare, tmp / "bare-replica", ignore=shutil.ignore_patterns("LOCK"))
    try:
        mod.restore_from_replica(bare, tmp / "bare-replica", signer=SIGNER, origin=ORIGIN)
        bad.append("a replica was trusted with nothing to check it against")
    except mod.LedgerError:
        pass
    return bad


def checkpoint_guards(mod, tmp: Path) -> list[str]:  # type: ignore[no-untyped-def]
    """The writer never signs a smaller size than one already signed, nor another root for the same size."""
    bad: list[str] = []
    led = open_ledger(mod, tmp, records=100)
    fill(led, 4)
    led.checkpoint()
    db = sqlite3.connect(tmp / "index.db", isolation_level=None)
    db.execute("UPDATE checkpoints SET root=? WHERE size=4", (b"\x07" * 32,))
    try:
        led.checkpoint()
        bad.append("a different root for an already signed size was accepted")
    except mod.IntegrityFailure:
        pass
    db.execute("UPDATE checkpoints SET size=9 WHERE size=4")
    try:
        led.checkpoint()
        bad.append("a checkpoint smaller than one already signed was accepted")
    except mod.IntegrityFailure:
        pass
    db.close()
    led.abandon()
    return bad


def interrupted_rotation(mod, tmp: Path) -> list[str]:  # type: ignore[no-untyped-def]
    bad: list[str] = []
    for label, content in (("empty", b""), ("half manifest", b'{"manifest":{"records":3,"segm')):
        d = tmp / label.replace(" ", "-")
        led = open_ledger(mod, d, records=3)
        fill(led, 3)
        led.abandon()
        (d / "segments" / segment.segment_name(3)).write_bytes(content)
        after = open_ledger(mod, d, records=3)
        if after.state != "OK" or after.size != 3:
            bad.append(f"interrupted rotation ({label}) was not recovered: {after.state}")
        fill(after, 4, start=3)
        bad += invariants(d, after)
        after.abandon()
    return bad


def format_version(mod, tmp: Path) -> list[str]:  # type: ignore[no-untyped-def]
    """The on-disk form of a ledger directory carries a version, ``FORMAT``. A new ledger writes it; a ledger written before the file
    existed is version 1 and gets the file when the writer next opens it; any other version is refused before anything is touched."""
    bad: list[str] = []
    d = tmp / "ledger"
    led = open_ledger(mod, d)
    fill(led, 2)
    led.abandon()
    written = (d / "FORMAT").read_bytes() if (d / "FORMAT").exists() else None
    if written != b"hoshitori-ledger/1\n":
        bad.append(f"a new ledger writes FORMAT as {written!r}, expected hoshitori-ledger/1 and a newline")
    if getattr(mod, "FORMAT", None) != "hoshitori-ledger/1":
        bad.append(f"the module's format is {getattr(mod, 'FORMAT', None)!r}")
    (d / "FORMAT").unlink(missing_ok=True)  # a ledger written before the file existed
    old = open_ledger(mod, d)
    if (old.state, old.size) != ("OK", 2) or not (d / "FORMAT").exists() or (d / "FORMAT").read_bytes() != b"hoshitori-ledger/1\n":
        bad.append("a ledger with no FORMAT file is not opened as version 1 and given the file")
    old.abandon()
    (d / "FORMAT").write_bytes(b"hoshitori-ledger/9\n")
    before = {p.name: p.read_bytes() for p in sorted(d.iterdir()) if p.is_file()}
    try:
        accepted = open_ledger(mod, d)
    except mod.LedgerError as exc:
        if str(exc) != "the ledger directory is of format 'hoshitori-ledger/9'; this code reads 'hoshitori-ledger/1'":
            bad.append(f"another format was refused with {str(exc)!r}")
    except Exception as exc:
        bad.append(f"another format raised {type(exc).__name__}: {exc}")
    else:
        bad.append("another format was accepted")
        accepted.abandon()
    if {p.name: p.read_bytes() for p in sorted(d.iterdir()) if p.is_file()} != before:
        bad.append("a refused format still changed the directory")
    (d / "FORMAT").write_bytes(b"hoshitori-ledger/1")  # no newline: still ours
    again = open_ledger(mod, d)
    if again.size != 2:
        bad.append("a FORMAT without its newline is not read as ours")
    again.abandon()
    return bad


def crash_sweep(mod, tmp: Path) -> list[str]:  # type: ignore[no-untyped-def]
    """Stop the writer at every hook point after N hits, reopen, finish the work, and check the recovery invariant."""
    bad: list[str] = []
    points = (
        "after_journal_before_outbox",
        "before_append",
        "mid_line",
        "after_write_before_fsync",
        "after_fsync_before_index",
        "after_index_commit",
        "after_rotation",
        "before_checkpoint_store",
    )
    for point in points:
        for nth in (1, 2, 4):
            d = tmp / f"{point}-{nth}"
            hits = {"n": 0}

            def hook(name: str, point: str = point, nth: int = nth, hits: dict[str, int] = hits) -> None:
                if name == point:
                    hits["n"] += 1
                    if hits["n"] == nth:
                        raise SimulatedCrash(name)

            led = open_ledger(mod, d, records=3, hook=hook)
            acknowledged: list = []
            try:
                for i in range(8):
                    acknowledged.append(led.submit(rec(i)))
                led.append_pending()
                led.checkpoint()
                for i in range(8, 10):
                    acknowledged.append(led.submit(rec(i)))
                led.append_pending()
                led.checkpoint()
            except SimulatedCrash:
                pass
            led.abandon()
            after = open_ledger(mod, d, records=3)
            if after.state != "OK":
                bad.append(f"{point}#{nth}: recovery ended in {after.state}: {after.state_detail}")
                after.abandon()
                continue
            after.append_pending()
            ids = record_ids(d)
            missing = [r.record_id for r in acknowledged if r.record_id not in ids]
            if missing:
                bad.append(f"{point}#{nth}: acknowledged records lost: {missing}")
            bad += [f"{point}#{nth}: {x}" for x in invariants(d, after)]
            after.abandon()
    return bad


def clock_step_is_refused_and_recorded(mod, tmp: Path) -> list[str]:  # type: ignore[no-untyped-def]
    """A host clock that jumps forward is refused before anything is committed with the wrong time, the refusal is kept in
    the writer's own event file, and the writer goes on as soon as the clock is right again, or when an operator says the new time is the
    true one, which is itself a ledger record. A step is a change of the wall clock that elapsed time does not explain."""
    bad: list[str] = []
    clock = ManualClock(START)
    led = open_ledger(mod, tmp / "step", clock, records=100)
    led.submit(rec(1))
    led.append_pending()
    clock.advance(timedelta(seconds=30))  # time passing is not a step
    led.submit(rec(2))
    led.append_pending()
    if led.size != 2:
        bad.append("ordinary time passing was refused")
    clock.step(timedelta(hours=1))  # a wrong time source puts the host an hour ahead
    led.submit(rec(3))
    try:
        led.append_pending()
        bad.append("a forward step of an hour was accepted")
    except mod.ClockStepError:
        pass
    if led.size != 2:
        bad.append("a record was committed with the stepped time")
    events = led.clock_events()
    if [e["kind"] for e in events] != ["forward_step"] or not 3500 < float(str(events[0]["unexplained_seconds"])) < 3700:
        bad.append(f"the refusal was not recorded as one forward step of about an hour: {events!r}")
    with contextlib.suppress(mod.ClockStepError):
        led.append_pending()
    if len(led.clock_events()) != 1:
        bad.append("the same standing refusal was recorded again on every retry")
    clock.step(timedelta(hours=-1))  # the time source is corrected
    led.append_pending()
    if led.size != 3:
        bad.append("the writer did not go on when the clock was right again")
    clock.step(timedelta(hours=2))  # a step that does not come back: the host really is on a new time
    led.submit(rec(4))
    try:
        led.append_pending()
        bad.append("a second step was accepted")
    except mod.ClockStepError:
        pass
    try:
        led.rebase_clock("")
        bad.append("a rebase without a stated reason was accepted")
    except mod.LedgerError:
        pass
    led.rebase_clock("operator confirmed the host clock against two independent references")
    last = canonical.parse_record(led.read_record(led.size - 1))
    kinds = [canonical.parse_record(led.read_record(i))["type"] for i in range(led.size)]
    if "clock_anomaly" not in kinds:
        bad.append("a rebase left no clock_anomaly record in the ledger")
    elif last["type"] != "evidence" and last["type"] != "clock_anomaly":
        bad.append("the queued command and the anomaly record are not both in the ledger after a rebase")
    if led.pending() != 0:
        bad.append("the queued commands were not appended after a rebase")
    if [e["kind"] for e in led.clock_events()] != ["forward_step", "forward_step", "rebase"]:
        bad.append(f"the event file does not hold the two refusals and the rebase: {[e['kind'] for e in led.clock_events()]}")
    led.close()
    again = open_ledger(mod, tmp / "step", clock, records=100)  # the events survive a restart
    if [e["kind"] for e in again.clock_events()] != ["forward_step", "forward_step", "rebase"]:
        bad.append("the clock events did not survive a restart")
    again.close()
    return bad


def clock_back_tolerance(mod, tmp: Path) -> list[str]:  # type: ignore[no-untyped-def]
    """A wall clock that is a little behind the last committed time is tolerated up to one second (the time is held at the last one), and
    one that is further behind stops the writer, even where it is too small a change for the step check (two seconds) to refuse: the two
    limits are separate rules and each must hold on its own."""
    bad: list[str] = []
    clock = ManualClock(START)
    led = open_ledger(mod, tmp / "back", clock, records=100)
    clock.advance(timedelta(seconds=5))
    led.submit(rec(1))
    led.append_pending()
    clock.step(timedelta(seconds=-0.9))  # inside the one second the ledger tolerates
    led.submit(rec(2))
    led.append_pending()
    times = [canonical.parse_record(led.read_record(i))["committed_at"] for i in range(led.size)]
    if led.size != 2 or times != sorted(times):
        bad.append(f"a clock 0.9 s behind was not tolerated with the time held: {times}")
    clock.step(timedelta(seconds=-0.5))  # now 1.4 s behind the last record: past the ledger's limit, inside the step check's
    led.submit(rec(3))
    try:
        led.append_pending()
        bad.append("a clock 1.4 s behind the last committed time did not stop the writer")
    except mod.ClockStepError:
        bad.append("a clock 1.4 s behind was refused as a step, which is for changes beyond two seconds")
    except mod.ClockError:
        pass
    if led.size != 2:
        bad.append("a record was committed with a time behind the last")
    led.abandon()
    return bad


def group_conflicts(mod, tmp: Path) -> list[str]:  # type: ignore[no-untyped-def]
    """A command that conflicts with a known one is refused alone and in a group, whether the known one is in the log or only queued, and
    a command identical to a known one is acknowledged without being queued twice."""
    bad: list[str] = []
    led = open_ledger(mod, tmp / "conf", records=100)
    led.submit(rec(0))
    led.append_pending()
    led.submit(rec(1))  # queued, not appended
    clash = lambda i: {**rec(i), "body": {"n": i, "pad": "changed"}}  # noqa: E731
    for label, call in (
        ("a single command that conflicts with a record in the log", lambda: led.submit(clash(0))),
        ("a single command that conflicts with a queued one", lambda: led.submit(clash(1))),
        ("a group with a command that conflicts with a record in the log", lambda: led.submit_many([rec(2), clash(0)])),
        ("a group with a command that conflicts with a queued one", lambda: led.submit_many([rec(2), clash(1)])),
    ):
        try:
            call()
            bad.append(f"{label}: was accepted")
        except mod.ConflictError:
            pass
        if led.pending() != 1:
            bad.append(f"{label}: changed what is queued ({led.pending()})")
    same = led.submit_many([rec(0), rec(1)])
    if len(same) != 2 or led.pending() != 1:
        bad.append("a group of commands already known was not acknowledged as they were")
    led.abandon()
    return bad


def damaged_state_file(mod, tmp: Path) -> list[str]:  # type: ignore[no-untyped-def]
    """A STATE file that cannot be read is not a crash and not an OK log: the log opens in INTEGRITY_FAILURE, says why, and writes nothing."""
    bad: list[str] = []
    led = open_ledger(mod, tmp / "d", records=100)
    fill(led, 2)
    led.close()
    for name, content in (
        ("not json", b"{{{"),
        ("an empty file", b""),
        ("a list", b"[]"),
        ("no state member", b'{"detail": "x"}'),
        ("a state that is not text", b'{"state": 3}'),
        ("a state that is not one of the three", b'{"state": "FINE"}'),
        ("not utf-8", b"\xff\xfe"),
    ):
        (tmp / "d" / "STATE").write_bytes(content)
        again = open_ledger(mod, tmp / "d", records=100)
        if again.state != "INTEGRITY_FAILURE" or "STATE" not in again.state_detail:
            bad.append(f"{name}: the log opened in {again.state!r} ({again.state_detail!r})")
        try:
            again.submit(rec(99))
            bad.append(f"{name}: a log in that state took a record")
        except mod.IntegrityFailure:
            pass
        again.abandon()
    (tmp / "d" / "STATE").write_bytes(b'{"state": "OK", "detail": ""}')
    ok = open_ledger(mod, tmp / "d", records=100)
    if ok.state != "OK" or ok.size != 2:
        bad.append(f"a readable STATE file of an OK log was not accepted, or the records were changed ({ok.state}, {ok.size})")
    ok.close()
    return bad


SCENARIOS: dict[str, Callable[[types.ModuleType, Path], list[str]]] = {
    "damaged_state_file": damaged_state_file,
    "basic_api": basic_api,
    "rotation_and_reopen": rotation_and_reopen,
    "torn_and_damaged_tails": torn_and_damaged_tails,
    "mid_file_corruption": mid_file_corruption,
    "prefix_protection": prefix_protection,
    "attestation_binding": attestation_binding,
    "index_and_journal_recovery": index_and_journal_recovery,
    "submit_many_is_all_or_nothing": submit_many_is_all_or_nothing,
    "clock_step_is_refused_and_recorded": clock_step_is_refused_and_recorded,
    "clock_back_tolerance": clock_back_tolerance,
    "group_conflicts": group_conflicts,
    "index_disagreement": index_disagreement,
    "lost_commands": lost_commands,
    "replica_restore": replica_restore,
    "checkpoint_guards": checkpoint_guards,
    "interrupted_rotation": interrupted_rotation,
    "format_version": format_version,
    "crash_sweep": crash_sweep,
}


def failures(mod: types.ModuleType, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        tmp = Path(tempfile.mkdtemp(prefix=f"hoshitori-{name}-"))
        try:
            bad += [f"{name}: {message}" for message in scenario(mod, tmp)]
        except Exception as exc:  # a crash in the scenario is a failure of the module under test
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return bad


def snapshot_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, default=str)
