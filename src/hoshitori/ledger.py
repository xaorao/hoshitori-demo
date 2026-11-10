"""The ledger: one writer, append-only segments, a rebuildable SQLite index, signed checkpoints.

Storage (all under one directory):

* ``segments/segment-<first seq>.jsonl``: the source of truth (``segment`` module), fsynced line by line.
* ``index.db``: SQLite in WAL mode. ``ledger`` is a *derived* table (a rebuild from the segments must be byte
  identical); ``outbox``, ``attested`` and ``meta`` are operational.
* ``journal.jsonl``: the intent journal. A producer's command is acknowledged only after its intent is appended
  here and fsynced, so a restored or lost SQLite cannot silently drop an acknowledged command (ADR-027). The journal
  does not cover its own loss: then only a receipt that its producer presents to ``reconcile_receipts`` shows the loss.
* ``checkpoints.jsonl``: signed C2SP checkpoints, append-only.
* ``attested.jsonl``: the checkpoints that ``register_attested`` was told an outside party stamped, each with the
  signed note of that checkpoint, append-only.
* ``STATE``: ``OK`` or ``INTEGRITY_FAILURE``. In the failure state the log refuses writes; it is never resumed over
  a prefix that cannot be proven (ADR-026).
* ``LOCK``: an exclusive file lock; a second writer cannot open the same directory.

Recovery runs on every open, before any append. The segments decide what exists:

1. A torn tail (bytes after the last newline: a write cut short leaves its line without the newline, the last byte
   written) is truncated. Anything else that is wrong in the segments, a complete but invalid final line included, is
   corruption: the log enters ``INTEGRITY_FAILURE`` and nothing is truncated. The index is never
   asked whether a line may go (LEDGER_SPEC section 7, invariant 8).
2. The tree is recomputed from the segments. Every signed checkpoint the log holds, and every externally attested
   checkpoint, must match the recomputed root for its size; otherwise ``INTEGRITY_FAILURE``. An attestation is
   believed only as far as the signed note it carries verifies under the log key; one that does not is
   ``INTEGRITY_FAILURE`` too.
3. The ``ledger`` table is brought into line with the segments: missing rows are added, rows beyond the end are
   removed (their outbox rows reopen), and a row that disagrees with the segment is corruption.
4. Journal intents found in neither the ledger nor the outbox are replayed into the outbox.

``committed_at`` is added by the writer, inside the record that is hashed, so the time a verifier reads is part of
the proven bytes. It never decreases in ledger order; a clock that has moved back more than the tolerance stops
the writer.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, Protocol

from hoshitori import canonical, envelope, segment
from hoshitori.cell import PointerStore
from hoshitori.checkpoint import Checkpoint, CheckpointError, sign_checkpoint, verify_checkpoint
from hoshitori.clock import Clock
from hoshitori.merkle import MerkleTree
from hoshitori.note import Signer
from hoshitori.timeutil import format_rfc3339_ms, parse_rfc3339

STATE_OK: Final = "OK"
STATE_INTEGRITY_FAILURE: Final = "INTEGRITY_FAILURE"
STATE_FENCED: Final = "FENCED"


def _saved_state(path: Path) -> tuple[str, str]:
    """The state a log was left in. A file that cannot be read, or says something that is not a state, is not trusted and not a crash: the
    log opens in INTEGRITY_FAILURE with the reason, so every surface shows it and nothing is written."""
    try:
        saved = json.loads(path.read_bytes().decode("utf-8"))
        state, detail = saved["state"], saved.get("detail", "")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return STATE_INTEGRITY_FAILURE, "the STATE file cannot be read; the log is not trusted until it is restored from a replica"
    if state not in (STATE_OK, STATE_INTEGRITY_FAILURE, STATE_FENCED) or not isinstance(detail, str):
        return (
            STATE_INTEGRITY_FAILURE,
            "the STATE file names a state this version does not know; the log is not trusted until it is restored",
        )
    return state, detail


EPOCH_TYPE: Final = "epoch"
CLOCK_TOLERANCE: Final = timedelta(seconds=1)
CLOCK_STEP_TOLERANCE: Final = timedelta(seconds=2)  # a change of the wall clock that elapsed time does not explain, beyond this, is a step
CLOCK_EVENTS: Final = "clock_events.jsonl"
CLOCK_ANOMALY_SOURCE: Final = "urn:hoshitori:ledger"
DEFAULT_SEGMENT_RECORDS: Final = 10_000
LEDGER_SOURCE: Final = "urn:hoshitori:ledger"
FORMAT: Final = (
    "hoshitori-ledger/1"  # the version of the on-disk form of a ledger directory; a change of that form changes this and says how
)
# an older directory is read

CrashHook = Callable[[str], None]


def _write_all(fd: int, data: bytes) -> None:
    """Write every byte: ``os.write`` may take fewer than it is given (a nearly full disk, a signal), and the rest must follow."""
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _append_durably(path: Path, data: bytes) -> None:
    """Append to a file and make it durable: the bytes by ``fsync``, and, when this append created the file, its entry in the directory
    (without that, a power loss could leave an acknowledged command with no file)."""
    created = not path.exists()
    with open(path, "ab") as handle:
        _write_all(handle.fileno(), data)
        os.fsync(handle.fileno())
    if created:
        _fsync_dir(path.parent)


class LedgerError(Exception):
    """Base class for ledger errors."""


class IntegrityFailure(LedgerError):
    """The log is in INTEGRITY_FAILURE and refuses writes."""


class ConflictError(LedgerError):
    """A different record already exists under the same (source, id)."""


class ClockError(LedgerError):
    """The clock moved back further than the tolerance."""


class ClockStepError(ClockError):
    """The host clock changed by more than the time that passed (a step), so no record is committed with its reading."""


class LockError(LedgerError):
    """Another writer holds the ledger directory."""


class Fenced(LedgerError):
    """This writer lost the compare-and-swap cell (a newer epoch exists, or the pointer moved): it must stop."""


@dataclass(frozen=True)
class Receipt:
    """What a producer holds after submitting: proof the command was acknowledged."""

    receipt_id: str
    source: str
    record_id: str
    body_sha256: str


@dataclass
class RecoveryReport:
    records: int = 0
    truncated_bytes: int = 0
    removed_index_rows: int = 0
    reopened_outbox: int = 0
    indexed_missing_rows: int = 0
    replayed_intents: int = 0
    state: str = STATE_OK
    detail: str = ""
    notes: list[str] = field(default_factory=list)


def check_format(directory: Path) -> None:
    """Refuse a ledger directory whose ``FORMAT`` file names a form this code does not read. No file: version 1, as before the file."""
    path = directory / "FORMAT"
    if not path.exists():
        return
    found = path.read_text(encoding="utf-8").rstrip("\n")
    if found != FORMAT:
        raise LedgerError(f"the ledger directory is of format {found!r}; this code reads {FORMAT!r}")


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


atomic_write = _atomic_write  # the same write, under a name that other modules may use: whole file or nothing, and synced


def receipt_id(source: str, record_id: str, body_sha256: str) -> str:
    digest = hashlib.sha256(f"receipt\n{source}\n{record_id}\n{body_sha256}".encode()).digest()
    return "rc" + base64.b32encode(digest).decode().lower().rstrip("=")[:26]


def strip_committed_at(record: bytes) -> bytes:
    """The producer's bytes: a stored record without the member the writer added."""
    parsed = canonical.parse_record(record)
    parsed.pop("committed_at", None)
    return canonical.dumps(parsed)


class LedgerView(Protocol):
    """What anything that only reads a ledger may call: the writer (:class:`Ledger`) and the read-only reader
    (``ledger_reader.LedgerReader``, which has no lock, no key and no way to write) both answer it. ``checkpoint`` is here because a bundle
    without a given checkpoint asks for one; a reader refuses, and says why."""

    @property
    def state(self) -> str: ...

    @property
    def state_detail(self) -> str: ...

    @property
    def size(self) -> int: ...

    def root(self, size: int | None = None) -> bytes: ...

    def read_record(self, seq: int) -> bytes: ...

    def find(self, source: str, record_id: str) -> int | None: ...

    def inclusion_proof(self, seq: int, size: int | None = None) -> list[bytes]: ...

    def consistency_proof(self, first: int, second: int | None = None) -> list[bytes]: ...

    def leaf_hash_at(self, seq: int) -> bytes: ...

    def checkpoint(self) -> bytes: ...


def read_clock_events(directory: Path) -> list[dict[str, object]]:
    """The clock events of the ledger in ``directory``, oldest first; none when the writer never refused a clock."""
    try:
        text = (directory / CLOCK_EVENTS).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    events: list[dict[str, object]] = []
    for line in text.splitlines():
        try:
            loaded = json.loads(line)
        except ValueError:
            continue  # a torn last line from a crash is not an event
        if isinstance(loaded, dict):
            events.append(loaded)
    return events


class Ledger:
    """A single-writer ledger. Open it with :meth:`open`; close it with :meth:`close`."""

    def __init__(
        self,
        directory: Path,
        signer: Signer,
        origin: str,
        clock: Clock,
        segment_records: int,
        crash_hook: CrashHook | None,
    ) -> None:
        self.directory = directory
        self.signer = signer
        self.origin = origin
        self.clock = clock
        self.segment_records = segment_records
        self._crash = crash_hook
        self._mutex = threading.RLock()
        self._tree = MerkleTree()
        self._closed_segments: list[tuple[str, int, bytes]] = []  # name, record count, sha256 of its bytes
        self._current_name = ""
        self._current_records = 0
        self._current_fd = -1
        self._last_committed_at = ""
        self._clock_ref: tuple[datetime, float] | None = None  # the wall and monotonic readings at the last commit
        self._standing_step = False  # a refused step is in force, and has been written to the event file
        self._lock_fd = -1
        self.state = STATE_OK
        self.state_detail = ""
        self.epoch = 0
        self.report = RecoveryReport()
        self._db: sqlite3.Connection

    # ------------------------------------------------------------------ opening
    @classmethod
    def open(
        cls,
        directory: str | Path,
        *,
        signer: Signer,
        origin: str,
        clock: Clock,
        segment_records: int = DEFAULT_SEGMENT_RECORDS,
        crash_hook: CrashHook | None = None,
    ) -> Ledger:
        if segment_records < 1:
            raise LedgerError("segment_records must be positive")
        ledger = cls(Path(directory), signer, origin, clock, segment_records, crash_hook)
        ledger._open()
        return ledger

    def _hook(self, point: str) -> None:
        if self._crash is not None:
            self._crash(point)

    def _open(self) -> None:
        d = self.directory
        (d / "segments").mkdir(parents=True, exist_ok=True)
        check_format(d)  # before anything is locked or written
        self._lock_fd = os.open(d / "LOCK", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._lock_fd)
            raise LockError(f"another writer holds {d}") from exc
        self._db = sqlite3.connect(d / "index.db", isolation_level=None, timeout=30, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._create_schema()
        if not (d / "FORMAT").exists():  # a new ledger, or one written before the file existed (which is version 1 by definition)
            _atomic_write(d / "FORMAT", (FORMAT + "\n").encode("utf-8"))
        state_file = d / "STATE"
        if state_file.exists():
            self.state, self.state_detail = _saved_state(state_file)
        self._recover()

    def _create_schema(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox(
                receipt TEXT PRIMARY KEY, source TEXT NOT NULL, rec_id TEXT NOT NULL, body BLOB NOT NULL,
                state TEXT NOT NULL, ord INTEGER NOT NULL, UNIQUE(source, rec_id));
            CREATE TABLE IF NOT EXISTS ledger(
                seq INTEGER PRIMARY KEY, source TEXT NOT NULL, rec_id TEXT NOT NULL, leaf BLOB NOT NULL,
                segment TEXT NOT NULL, off INTEGER NOT NULL, len INTEGER NOT NULL, UNIQUE(source, rec_id));
            CREATE TABLE IF NOT EXISTS checkpoints(size INTEGER PRIMARY KEY, root BLOB NOT NULL, note BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS attested(
                size INTEGER NOT NULL, root BLOB NOT NULL, evidence TEXT NOT NULL, PRIMARY KEY(size, root));
            """
        )

    # --------------------------------------------------------------- state
    def _set_state(self, state: str, detail: str) -> None:
        self.state, self.state_detail = state, detail
        _atomic_write(self.directory / "STATE", json.dumps({"state": state, "detail": detail}).encode())

    def _fail(self, detail: str) -> None:
        self._set_state(STATE_INTEGRITY_FAILURE, detail)
        self.report.state, self.report.detail = STATE_INTEGRITY_FAILURE, detail

    def _require_ok(self) -> None:
        if self.state != STATE_OK:
            raise IntegrityFailure(f"the log is in {self.state}: {self.state_detail}")

    # ------------------------------------------------------------- recovery
    def _segment_files(self) -> list[Path]:
        names = sorted(p.name for p in (self.directory / "segments").iterdir() if not p.name.endswith(".tmp"))
        files = []
        for name in names:
            try:
                segment.segment_first_seq(name)
            except segment.SegmentError:
                raise IntegrityFailure(f"unexpected file in the segment directory: {name}") from None
            files.append(self.directory / "segments" / name)
        return files

    def _recover(self) -> None:
        report = self.report
        if self.state != STATE_OK:
            # A persisted failure stays until a person restores the log; nothing is repaired or truncated.
            report.state, report.detail = self.state, self.state_detail
            self._load_degraded_view()
            return
        try:
            lines = self._scan_segments(report)
        except IntegrityFailure as exc:
            self._fail(str(exc))
            self._load_degraded_view()
            return
        self._tree = MerkleTree(r.leaf for r in lines)
        report.records = self._tree.size
        try:
            self._check_checkpoints()
            self._check_attested()
        except IntegrityFailure as exc:
            self._fail(str(exc))
            return
        self._reconcile_index(lines, report)
        if self.state == STATE_OK:
            self._replay_journal(report)
            self._last_committed_at = self._load_last_committed_at()
            self.epoch = self._load_epoch()

    def _load_degraded_view(self) -> None:
        self._tree = MerkleTree()

    def _drop_unfinished_rotation(self) -> None:
        """A crash during rotation can leave a new, empty or half-written segment: remove it, the old one is current."""
        while True:
            files = self._segment_files()
            if len(files) < 2:
                return
            last = files[-1]
            data = last.read_bytes()
            if len(data) == 0 or b"\n" not in data:
                last.unlink()
                _fsync_dir(self.directory / "segments")
                self.report.notes.append(f"removed {last.name}: a rotation was interrupted before its manifest line was complete")
            else:
                return

    def _scan_segments(self, report: RecoveryReport) -> list[segment.RecordLine]:
        self._drop_unfinished_rotation()
        files = self._segment_files()
        all_lines: list[segment.RecordLine] = []
        previous: tuple[str, bytes, int] | None = None
        expected_first = 0
        self._closed_segments = []
        for index, path in enumerate(files):
            first_seq = segment.segment_first_seq(path.name)
            if first_seq != expected_first:
                raise IntegrityFailure(f"segment {path.name} starts at {first_seq}, expected {expected_first}")
            data = path.read_bytes()
            scan = segment.scan_segment(data, first_seq, previous)
            is_last = index == len(files) - 1
            if scan.problem is not segment.Problem.NONE:
                if not is_last:
                    raise IntegrityFailure(f"closed segment {path.name}: {scan.detail} (offset {scan.problem_offset})")
                self._repair_last_segment(path, data, scan, first_seq, report)
                data = path.read_bytes()
                scan = segment.scan_segment(data, first_seq, previous)
                if scan.problem is not segment.Problem.NONE:  # defensive: repair must leave a clean segment
                    raise IntegrityFailure(f"segment {path.name} is still invalid after repair: {scan.detail}")
            all_lines.extend(scan.records)
            expected_first = first_seq + len(scan.records)
            if is_last:
                self._current_name = path.name
                self._current_records = len(scan.records)
            else:
                digest = hashlib.sha256(data).digest()
                self._closed_segments.append((path.name, len(scan.records), digest))
                previous = (path.name, digest, len(scan.records))
        if not files:
            self._current_name = segment.segment_name(0)
            self._current_records = 0
            (self.directory / "segments" / self._current_name).touch()
            _fsync_dir(self.directory / "segments")
        self._current_fd = os.open(self.directory / "segments" / self._current_name, os.O_WRONLY | os.O_APPEND)
        return all_lines

    def _repair_last_segment(self, path: Path, data: bytes, scan: segment.Scan, first_seq: int, report: RecoveryReport) -> None:
        """Only a torn tail may be removed: the segment alone decides, the index is never asked (LEDGER_SPEC section 7, invariant 8).

        A writer that stops in the middle of a line leaves it without its newline, the last byte written, so bytes after the last newline
        are a write cut short. A complete final line that fails its check is corruption, whether or not the index has a row for
        it: the index is a rebuildable cache, so a missing row does not show that the line was never acknowledged (finding R-INDEX). The
        index is read only to say in the refusal that the line had been indexed.
        """
        if scan.problem is segment.Problem.DAMAGE:
            raise IntegrityFailure(f"segment {path.name}: {scan.detail} at offset {scan.problem_offset}; not repaired")
        seq_of_bad = first_seq + len(scan.records)
        if scan.problem is segment.Problem.LAST_LINE_INVALID:
            row = self._db.execute("SELECT 1 FROM ledger WHERE seq=?", (seq_of_bad,)).fetchone()
            if row is not None:
                raise IntegrityFailure(
                    f"segment {path.name}: line {seq_of_bad} is damaged although it was indexed ({scan.detail}); not repaired"
                )
            raise IntegrityFailure(f"segment {path.name}: line {seq_of_bad} is complete but invalid ({scan.detail}); not repaired")
        cut = scan.valid_end
        report.truncated_bytes += len(data) - cut
        with open(path, "r+b") as handle:
            handle.truncate(cut)
            handle.flush()
            os.fsync(handle.fileno())
        report.notes.append(f"truncated {len(data) - cut} bytes of an unacknowledged tail of {path.name}")

    def _check_checkpoints(self) -> None:
        self._db.execute("DELETE FROM checkpoints")
        path = self.directory / "checkpoints.jsonl"
        if not path.exists():
            return
        last_size = 0
        for number, line in enumerate(path.read_bytes().splitlines(), start=1):
            try:
                wrapper = canonical.parse_record(line)
                note = base64.b64decode(str(wrapper["note"]), validate=True)
                checkpoint = verify_checkpoint(note, [self.signer.verifier], self.origin)
            except (canonical.CanonicalError, CheckpointError, KeyError, ValueError) as exc:
                raise IntegrityFailure(f"checkpoints.jsonl line {number} is not a valid signed checkpoint: {exc}") from exc
            if checkpoint.size < last_size:
                raise IntegrityFailure(f"checkpoint sizes decrease at line {number}")
            if checkpoint.size > self._tree.size:
                raise IntegrityFailure(
                    f"checkpoint of size {checkpoint.size} exceeds the {self._tree.size} records found: the log was rolled back"
                )
            if self._tree.root(checkpoint.size) != checkpoint.root:
                raise IntegrityFailure(f"checkpoint of size {checkpoint.size} does not match the records: the prefix was rewritten")
            last_size = checkpoint.size
            self._db.execute("INSERT OR REPLACE INTO checkpoints(size, root, note) VALUES(?,?,?)", (checkpoint.size, checkpoint.root, note))

    def _load_attested(self) -> None:
        """The attestation file is the record; the table is rebuilt from it (a lost index must not forget a commitment).

        Each entry is believed only as the checkpoint note it carries (``_attestations``); an entry that does not verify stops recovery.
        """
        for size, root, evidence in _attestations(self.directory / "attested.jsonl", self.signer, self.origin):
            self._db.execute("INSERT OR IGNORE INTO attested(size, root, evidence) VALUES(?,?,?)", (size, root, evidence))

    def _check_attested(self) -> None:
        self._load_attested()
        for size, root, evidence in self._db.execute("SELECT size, root, evidence FROM attested ORDER BY size").fetchall():
            if size > self._tree.size:
                raise IntegrityFailure(f"attested checkpoint of size {size} ({evidence}) exceeds the records found")
            if self._tree.root(size) != bytes(root):
                raise IntegrityFailure(f"attested checkpoint of size {size} ({evidence}) does not match the records")

    def _reconcile_index(self, lines: list[segment.RecordLine], report: RecoveryReport) -> None:
        segment_of = self._segment_offsets(lines)
        by_seq = {row[0]: row for row in self._db.execute("SELECT seq, source, rec_id, leaf FROM ledger").fetchall()}
        self._db.execute("BEGIN IMMEDIATE")
        try:
            for seq in sorted(s for s in by_seq if s >= len(lines)):
                _, source, rec_id, _leaf = by_seq[seq]
                self._db.execute("DELETE FROM ledger WHERE seq=?", (seq,))
                reopened = self._db.execute(
                    "UPDATE outbox SET state='pending' WHERE source=? AND rec_id=? AND state='done'", (source, rec_id)
                ).rowcount
                report.removed_index_rows += 1
                report.reopened_outbox += reopened
            for line in lines:
                parsed = canonical.parse_record(line.record)
                source, rec_id = str(parsed["source"]), str(parsed["id"])
                name, offset = segment_of[line.seq]
                row = by_seq.get(line.seq)
                if row is not None:
                    if bytes(row[3]) != line.leaf or row[1] != source or row[2] != rec_id:
                        raise IntegrityFailure(f"index row {line.seq} disagrees with the segment: the segment was rewritten")
                else:
                    self._db.execute(
                        "INSERT INTO ledger(seq, source, rec_id, leaf, segment, off, len) VALUES(?,?,?,?,?,?,?)",
                        (line.seq, source, rec_id, line.leaf, name, offset, line.length),
                    )
                    report.indexed_missing_rows += 1
                self._db.execute("UPDATE outbox SET state='done' WHERE source=? AND rec_id=?", (source, rec_id))
            self._db.execute("COMMIT")
        except IntegrityFailure as exc:
            self._db.execute("ROLLBACK")
            self._fail(str(exc))
        except Exception:
            self._db.execute("ROLLBACK")
            raise

    def _segment_offsets(self, lines: list[segment.RecordLine]) -> dict[int, tuple[str, int]]:
        """Segment file and offset of every record, derived from the sequence ranges of the segments."""
        result: dict[int, tuple[str, int]] = {}
        names = [p.name for p in self._segment_files()]
        firsts = [segment.segment_first_seq(n) for n in names]
        for line in lines:
            idx = max(i for i, first in enumerate(firsts) if first <= line.seq)
            result[line.seq] = (names[idx], line.offset)
        return result

    def _replay_journal(self, report: RecoveryReport) -> None:
        path = self.directory / "journal.jsonl"
        if not path.exists():
            return
        for line in path.read_bytes().splitlines():
            try:
                wrapper = canonical.parse_record(line)
                # a line is one command, or a group acknowledged together (``submit_many``): a torn line is none of them
                intents = wrapper.get("group", [wrapper])
                if not isinstance(intents, list):
                    continue
                parsed = []
                for intent in intents:
                    assert isinstance(intent, dict)
                    body = str(intent["rec"]).encode("utf-8")
                    parsed.append((str(intent["receipt"]), body, canonical.parse_record(body)))
            except (canonical.CanonicalError, KeyError, AssertionError):
                continue  # a torn final journal line is an unacknowledged command
            for rid, body, record in parsed:
                source, rec_id = str(record["source"]), str(record["id"])
                in_ledger = self._db.execute("SELECT 1 FROM ledger WHERE source=? AND rec_id=?", (source, rec_id)).fetchone()
                in_outbox = self._db.execute("SELECT 1 FROM outbox WHERE source=? AND rec_id=?", (source, rec_id)).fetchone()
                if in_ledger is None and in_outbox is None:
                    self._insert_outbox(rid, source, rec_id, body)
                    report.replayed_intents += 1

    def epoch_records(self) -> list[dict[str, canonical.JsonValue]]:
        """The ``epoch`` records in the log, in order: the promotions the log has recorded."""
        rows = self._db.execute(
            "SELECT segment, off, len FROM ledger WHERE source=? AND rec_id LIKE 'epoch-%' ORDER BY seq", (LEDGER_SOURCE,)
        ).fetchall()
        result = []
        for name, offset, length in rows:
            record = canonical.parse_record(self._read_line_record(name, offset, length))
            if record.get("type") == EPOCH_TYPE:
                result.append(record)
        return result

    def _load_epoch(self) -> int:
        epochs = [r["body"]["epoch"] for r in self.epoch_records() if isinstance(r["body"], dict)]
        return max((e for e in epochs if type(e) is int), default=0)

    def _load_last_committed_at(self) -> str:
        if self._tree.size == 0:
            return ""
        row = self._db.execute("SELECT segment, off, len FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
        data = (self.directory / "segments" / row[0]).read_bytes()[row[1] : row[1] + row[2]]
        wrapper = canonical.parse_record(data.rstrip(b"\n"))
        record = canonical.parse_record(str(wrapper["rec"]).encode("utf-8"))
        return str(record.get("committed_at", ""))

    # -------------------------------------------------------------- producers
    def _insert_outbox(self, rid: str, source: str, rec_id: str, body: bytes) -> None:
        order = self._db.execute("SELECT COALESCE(MAX(ord), 0) + 1 FROM outbox").fetchone()[0]
        self._db.execute(
            "INSERT OR IGNORE INTO outbox(receipt, source, rec_id, body, state, ord) VALUES(?,?,?,?, 'pending', ?)",
            (rid, source, rec_id, body, order),
        )

    def submit(self, record: dict[str, canonical.JsonValue]) -> Receipt:
        """Acknowledge a command: journal its intent (fsynced), queue it in the outbox, return a receipt.

        Submitting the same record again returns the same receipt; a different record under the same
        ``(source, id)`` is a conflict and is never queued.
        """
        with self._mutex:
            self._require_ok()
            envelope.validate_producer_record(record)
            body = canonical.dumps(record)
            source, rec_id = str(record["source"]), str(record["id"])
            body_sha = hashlib.sha256(body).hexdigest()
            rid = receipt_id(source, rec_id, body_sha)
            existing = self._db.execute("SELECT segment, off, len FROM ledger WHERE source=? AND rec_id=?", (source, rec_id)).fetchone()
            if existing is not None:
                if strip_committed_at(self._read_line_record(existing[0], existing[1], existing[2])) != body:
                    raise ConflictError(f"{source} {rec_id} already exists with different content")
                return Receipt(rid, source, rec_id, body_sha)
            queued = self._db.execute("SELECT body FROM outbox WHERE source=? AND rec_id=?", (source, rec_id)).fetchone()
            if queued is not None:
                if bytes(queued[0]) != body:
                    raise ConflictError(f"{source} {rec_id} is already queued with different content")
                return Receipt(rid, source, rec_id, body_sha)
            intent = canonical.dumps({"receipt": rid, "rec": body.decode("utf-8")}) + b"\n"
            _append_durably(self.directory / "journal.jsonl", intent)
            self._hook("after_journal_before_outbox")
            self._insert_outbox(rid, source, rec_id, body)
            return Receipt(rid, source, rec_id, body_sha)

    def submit_many(self, records: Sequence[dict[str, canonical.JsonValue]]) -> list[Receipt]:
        """Acknowledge a group of commands together: every record is checked first, the new ones are journaled in one fsynced line, and then
        queued in order. A crash leaves all of the group's new commands or none of them, and a refusal of any one leaves none queued. A
        command already in the log or queued with the same content is not journaled again (as with ``submit``); a record twice in one
        group is refused."""
        with self._mutex:
            self._require_ok()
            seen: set[tuple[str, str]] = set()
            receipts: list[Receipt] = []
            fresh: list[tuple[str, str, str, bytes]] = []
            for record in records:
                envelope.validate_producer_record(record)
                body = canonical.dumps(record)
                source, rec_id = str(record["source"]), str(record["id"])
                if (source, rec_id) in seen:
                    raise ConflictError(f"{source} {rec_id} is in the group twice")
                seen.add((source, rec_id))
                body_sha = hashlib.sha256(body).hexdigest()
                rid = receipt_id(source, rec_id, body_sha)
                receipts.append(Receipt(rid, source, rec_id, body_sha))
                existing = self._db.execute("SELECT segment, off, len FROM ledger WHERE source=? AND rec_id=?", (source, rec_id)).fetchone()
                if existing is not None:
                    if strip_committed_at(self._read_line_record(existing[0], existing[1], existing[2])) != body:
                        raise ConflictError(f"{source} {rec_id} already exists with different content")
                    continue
                queued = self._db.execute("SELECT body FROM outbox WHERE source=? AND rec_id=?", (source, rec_id)).fetchone()
                if queued is not None:
                    if bytes(queued[0]) != body:
                        raise ConflictError(f"{source} {rec_id} is already queued with different content")
                    continue
                fresh.append((rid, source, rec_id, body))
            if fresh:
                line = canonical.dumps({"group": [{"receipt": rid, "rec": body.decode("utf-8")} for rid, _, _, body in fresh]}) + b"\n"
                _append_durably(self.directory / "journal.jsonl", line)
                self._hook("after_journal_before_outbox")
                for rid, source, rec_id, body in fresh:
                    self._insert_outbox(rid, source, rec_id, body)
            return receipts

    def _read_line_record(self, name: str, offset: int, length: int) -> bytes:
        with open(self.directory / "segments" / name, "rb") as handle:
            handle.seek(offset)
            line = handle.read(length)
        wrapper = canonical.parse_record(line.rstrip(b"\n"))
        return str(wrapper["rec"]).encode("utf-8")

    # ------------------------------------------------------------------ writer
    def append_pending(self) -> int:
        """Append every queued record to the segment, in submission order; returns the number appended."""
        with self._mutex:
            self._require_ok()
            appended = 0
            rows = self._db.execute("SELECT receipt, source, rec_id, body FROM outbox WHERE state='pending' ORDER BY ord").fetchall()
            for rid, source, rec_id, body in rows:
                if self._db.execute("SELECT 1 FROM ledger WHERE source=? AND rec_id=?", (source, rec_id)).fetchone():
                    self._db.execute("UPDATE outbox SET state='done' WHERE receipt=?", (rid,))
                    continue
                self._append_one(rid, source, rec_id, bytes(body))
                appended += 1
            return appended

    def _read_monotonic(self) -> float | None:
        reader = getattr(self.clock, "monotonic", None)
        return float(reader()) if callable(reader) else None

    def _check_step(self, now: datetime) -> None:
        """Refuse a wall-clock reading that elapsed time does not explain, and keep the refusal once per standing step."""
        mono = self._read_monotonic()
        if mono is None or self._clock_ref is None:
            if mono is not None:
                self._clock_ref = (now, mono)
            return
        ref_wall, ref_mono = self._clock_ref
        elapsed = mono - ref_mono
        unexplained = (now - ref_wall).total_seconds() - elapsed
        if abs(unexplained) <= CLOCK_STEP_TOLERANCE.total_seconds():
            self._clock_ref = (now, mono)
            self._standing_step = False
            return
        kind = "forward_step" if unexplained > 0 else "backward_step"
        if not self._standing_step:
            self._standing_step = True
            self._write_clock_event(
                {
                    "kind": kind,
                    "at": format_rfc3339_ms(now),
                    "reference": format_rfc3339_ms(ref_wall),
                    "elapsed_seconds": f"{elapsed:.3f}",
                    "unexplained_seconds": f"{unexplained:.3f}",
                }
            )
        raise ClockStepError(
            f"the host clock changed by {unexplained:+.0f} s that elapsed time does not explain; nothing is committed with this reading "
            "(correct the clock, or confirm the new time with rebase_clock)"
        )

    def _write_clock_event(self, event: dict[str, object]) -> None:
        path = self.directory / CLOCK_EVENTS
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps(event, sort_keys=True) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_dir(self.directory)

    def clock_events(self) -> list[dict[str, object]]:
        """Every refusal of a stepped clock and every confirmed rebase this ledger has seen, oldest first."""
        return read_clock_events(self.directory)

    def rebase_clock(self, reason: str) -> None:
        """Accept the host clock's present reading as the true time after a step, and put that decision in the ledger.

        This is the explicit operator step: the reason says what was checked. It records a ``rebase`` event, takes the present reading
        as the new baseline and appends a ``clock_anomaly`` record that carries the refusals since the last rebase.
        """
        with self._mutex:
            self._require_ok()
            if not isinstance(reason, str) or not reason.strip() or reason != reason.strip():
                raise LedgerError("a rebase needs a stated reason")
            mono = self._read_monotonic()
            now = self.clock.now()
            since = self.clock_events()
            last_rebase = max((i for i, e in enumerate(since) if e.get("kind") == "rebase"), default=-1)
            standing = since[last_rebase + 1 :]
            if not standing:
                raise LedgerError("there is no refused clock step to confirm")
            self._write_clock_event({"kind": "rebase", "at": format_rfc3339_ms(now), "reason": reason})
            if mono is not None:
                self._clock_ref = (now, mono)
            self._standing_step = False
            self.submit(
                {
                    "spec": "hoshitori.record/1",
                    "type": "clock_anomaly",
                    "type_version": 1,
                    "id": f"clock-anomaly-{len(since)}",
                    "source": CLOCK_ANOMALY_SOURCE,
                    "created_at": format_rfc3339_ms(now),
                    "body": {"reason": reason, "refusals": [dict(e) for e in standing]},  # type: ignore[arg-type]
                }
            )
            self.append_pending()

    def _next_committed_at(self) -> str:
        now = self.clock.now()
        self._check_step(now)
        if self._last_committed_at:
            last = parse_rfc3339(self._last_committed_at)
            if now < last - CLOCK_TOLERANCE:
                raise ClockError(f"the clock moved back from {self._last_committed_at}")
            now = max(now, last)
        return format_rfc3339_ms(now)

    def _rotate_if_full(self) -> None:
        if self._current_records < self.segment_records:
            return
        os.fsync(self._current_fd)
        os.close(self._current_fd)
        path = self.directory / "segments" / self._current_name
        digest = hashlib.sha256(path.read_bytes()).digest()
        self._closed_segments.append((self._current_name, self._current_records, digest))
        first_seq = segment.segment_first_seq(self._current_name) + self._current_records
        new_name = segment.segment_name(first_seq)
        new_path = self.directory / "segments" / new_name
        manifest = segment.encode_manifest_line(self._current_name, digest, self._current_records)
        fd = os.open(new_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.write(fd, manifest)
        os.fsync(fd)
        _fsync_dir(self.directory / "segments")
        self._hook("after_rotation")
        self._current_name, self._current_records, self._current_fd = new_name, 0, fd

    def _append_one(self, rid: str, source: str, rec_id: str, body: bytes) -> None:
        committed_at = self._next_committed_at()
        record = canonical.parse_record(body)
        record["committed_at"] = committed_at
        text = canonical.dumps(record)
        seq = self._tree.size
        self._rotate_if_full()
        line = segment.encode_record_line(seq, text)
        offset = os.lseek(self._current_fd, 0, os.SEEK_END)
        self._hook("before_append")
        half = len(line) // 2
        try:
            _write_all(self._current_fd, line[:half])
            self._hook("mid_line")
            _write_all(self._current_fd, line[half:])
        except OSError:  # a full disk or a failed write: the segment goes back to where this append began, never a damaged line
            os.ftruncate(self._current_fd, offset)
            raise
        self._hook("after_write_before_fsync")
        os.fsync(self._current_fd)
        self._hook("after_fsync_before_index")
        leaf = segment.leaf_hash(text)
        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                "INSERT INTO ledger(seq, source, rec_id, leaf, segment, off, len) VALUES(?,?,?,?,?,?,?)",
                (seq, source, rec_id, leaf, self._current_name, offset, len(line)),
            )
            self._db.execute("UPDATE outbox SET state='done' WHERE receipt=?", (rid,))
            self._db.execute("COMMIT")
        except Exception:
            self._db.execute("ROLLBACK")
            raise
        self._hook("after_index_commit")
        self._tree.append_leaf_hash(leaf)
        self._current_records += 1
        self._last_committed_at = committed_at

    # ------------------------------------------------------------- checkpoints
    @property
    def size(self) -> int:
        return self._tree.size

    def root(self, size: int | None = None) -> bytes:
        return self._tree.root(size)

    def checkpoint(self) -> bytes:
        """Sign and store a checkpoint of the current head; idempotent for an unchanged head."""
        with self._mutex:
            self._require_ok()
            if self._tree.size == 0:
                raise LedgerError("there is nothing to checkpoint in an empty log")
            size, root = self._tree.size, self._tree.root()
            latest = self._db.execute("SELECT size, root, note FROM checkpoints ORDER BY size DESC LIMIT 1").fetchone()
            if latest is not None:
                if latest[0] == size:
                    if bytes(latest[1]) != root:
                        raise IntegrityFailure("a different root already exists for this size")
                    return bytes(latest[2])
                if latest[0] > size:
                    raise IntegrityFailure("the log is smaller than a checkpoint already signed")
            note = sign_checkpoint(Checkpoint(self.origin, size, root), [self.signer])
            line = canonical.dumps({"note": base64.b64encode(note).decode("ascii"), "size": size}) + b"\n"
            self._hook("before_checkpoint_store")
            _append_durably(self.directory / "checkpoints.jsonl", line)
            self._db.execute("INSERT OR REPLACE INTO checkpoints(size, root, note) VALUES(?,?,?)", (size, root, note))
            return note

    def publish_checkpoint(self, cell: PointerStore) -> bytes:
        """Sign a checkpoint and release it through the cell. If the cell refuses, this writer is fenced off for good."""
        with self._mutex:
            self._require_ok()
            note = self.checkpoint()
            checkpoint = verify_checkpoint(note, [self.signer.verifier], self.origin)
            new_pointer = (checkpoint.size, checkpoint.root)
            state = cell.read()
            if state.pointer == new_pointer and state.epoch == self.epoch:
                return note  # already published
            if state.epoch != self.epoch or not cell.publish(self.epoch, state.pointer, new_pointer):
                detail = f"the cell is at epoch {state.epoch} and this writer is at epoch {self.epoch}, or its pointer moved"
                self._set_state(STATE_FENCED, detail)
                raise Fenced(detail)
            return note

    def register_attested(self, size: int, root: bytes, evidence: str) -> None:
        """Record an externally attested (size, root); recovery then refuses any log whose prefix differs.

        What an outside party stamps is the text of a checkpoint this log signed (LEDGER_SPEC section 5), so only such a checkpoint can be
        attested: the entry written to ``attested.jsonl`` carries its signed note, and recovery and a restore believe the entry only as far
        as that note verifies under the log key (finding R-ATTEST). The tokens are not in the entry: the ledger holds neither them nor the
        authorities' pinned roots.
        """
        with self._mutex:
            if size < 1 or len(root) != 32:
                raise LedgerError("an attested checkpoint has a positive size and a 32-byte root")
            if size > self._tree.size or self._tree.root(size) != root:
                self._fail(f"attested checkpoint of size {size} ({evidence}) does not match the records")
                raise IntegrityFailure(self.state_detail)
            signed = self._db.execute("SELECT note FROM checkpoints WHERE size=? AND root=?", (size, root)).fetchone()
            if signed is None:
                raise LedgerError(
                    f"no checkpoint of size {size} with this root was signed by this log: only a signed checkpoint can be attested"
                )
            note = base64.b64encode(bytes(signed[0])).decode("ascii")
            line = canonical.dumps({"evidence": evidence, "note": note, "root": root.hex(), "size": size}) + b"\n"
            _append_durably(self.directory / "attested.jsonl", line)
            self._db.execute("INSERT OR IGNORE INTO attested(size, root, evidence) VALUES(?,?,?)", (size, root, evidence))

    def reconcile_receipts(self, receipts: Iterable[Receipt]) -> list[Receipt]:
        """Account for acknowledged commands that are nowhere: each becomes a ``failed_lost`` record (ADR-027).

        A receipt whose ``(source, id)`` is in the ledger, or still queued in the outbox, is fine. One that is in
        neither (the journal and the outbox were both lost, for example a replaced host) is recorded as lost, with
        its reason, so that loss is counted. Only the receipts passed here are accounted for: with the journal and the
        outbox both lost, a command whose producer does not keep its receipt and call this is absent with no record
        (ADR-027: the producer keeps its receipts outside the SQLite that failed). Returns the receipts found lost.
        """
        lost: list[Receipt] = []
        with self._mutex:
            self._require_ok()
            for receipt in receipts:
                in_ledger = self._db.execute(
                    "SELECT 1 FROM ledger WHERE source=? AND rec_id=?", (receipt.source, receipt.record_id)
                ).fetchone()
                in_outbox = self._db.execute(
                    "SELECT 1 FROM outbox WHERE source=? AND rec_id=?", (receipt.source, receipt.record_id)
                ).fetchone()
                if in_ledger is not None or in_outbox is not None:
                    continue
                digest = hashlib.sha256(f"failed_lost\n{receipt.receipt_id}".encode()).digest()
                lost_id = "fl" + base64.b32encode(digest).decode().lower().rstrip("=")[:26]
                if self._db.execute("SELECT 1 FROM ledger WHERE source=? AND rec_id=?", (LEDGER_SOURCE, lost_id)).fetchone():
                    continue  # already accounted for by an earlier reconciliation
                lost.append(receipt)
                self.submit(
                    {
                        "spec": envelope.SPEC,
                        "type": "failed_lost",
                        "type_version": 1,
                        "id": lost_id,
                        "source": LEDGER_SOURCE,
                        "created_at": format_rfc3339_ms(self.clock.now()),
                        "body": {
                            "receipt": receipt.receipt_id,
                            "source": receipt.source,
                            "record_id": receipt.record_id,
                            "body_sha256": receipt.body_sha256,
                            "reason": "acknowledged command found in neither the ledger, the outbox nor the journal",
                        },
                    }
                )
        if lost:
            self.append_pending()
        return lost

    # ---------------------------------------------------------------- reading
    def read_record(self, seq: int) -> bytes:
        """The canonical record bytes at ``seq`` (including ``committed_at``), verified against its leaf hash."""
        row = self._db.execute("SELECT segment, off, len, leaf FROM ledger WHERE seq=?", (seq,)).fetchone()
        if row is None:
            raise LedgerError(f"no record {seq}")
        record = self._read_line_record(row[0], row[1], row[2])
        if segment.leaf_hash(record) != bytes(row[3]):
            raise IntegrityFailure(f"record {seq} no longer matches its leaf hash")
        return record

    def find(self, source: str, record_id: str) -> int | None:
        row = self._db.execute("SELECT seq FROM ledger WHERE source=? AND rec_id=?", (source, record_id)).fetchone()
        return None if row is None else int(row[0])

    def inclusion_proof(self, seq: int, size: int | None = None) -> list[bytes]:
        return self._tree.inclusion_proof(seq, size)

    def consistency_proof(self, first: int, second: int | None = None) -> list[bytes]:
        return self._tree.consistency_proof(first, second)

    def leaf_hash_at(self, seq: int) -> bytes:
        return self._tree.leaf_hash_at(seq)

    def pending(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM outbox WHERE state='pending'").fetchone()[0])

    # ---------------------------------------------------------------- closing
    def abandon(self) -> None:
        """Release every resource without any shutdown work. For tests that simulate a crash inside one process."""
        if self._current_fd >= 0:
            os.close(self._current_fd)
            self._current_fd = -1
        self._db.close()
        if self._lock_fd >= 0:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = -1

    def close(self, *, checkpoint: bool = True) -> None:
        with self._mutex:
            if checkpoint and self.state == STATE_OK and self._tree.size > 0:
                self.checkpoint()
            if self._current_fd >= 0:
                os.close(self._current_fd)
                self._current_fd = -1
            self._db.close()
            if self._lock_fd >= 0:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
                self._lock_fd = -1


def read_log(directory: str | Path) -> list[segment.RecordLine]:
    """Read and fully validate a ledger directory's segments without opening a ledger (no repair, no lock).

    Raises ``IntegrityFailure`` for anything but a perfectly clean log. Used to judge a replica.
    """
    files = sorted(p for p in (Path(directory) / "segments").iterdir() if not p.name.endswith(".tmp"))
    lines: list[segment.RecordLine] = []
    previous: tuple[str, bytes, int] | None = None
    expected = 0
    for path in files:
        try:
            first = segment.segment_first_seq(path.name)
        except segment.SegmentError as exc:
            raise IntegrityFailure(str(exc)) from exc
        if first != expected:
            raise IntegrityFailure(f"segment {path.name} starts at {first}, expected {expected}")
        data = path.read_bytes()
        scan = segment.scan_segment(data, first, previous)
        if scan.problem is not segment.Problem.NONE:
            raise IntegrityFailure(f"{path.name}: {scan.detail}")
        lines.extend(scan.records)
        expected = first + len(scan.records)
        previous = (path.name, hashlib.sha256(data).digest(), len(scan.records))
    return lines


def _signed_checkpoints(directory: Path, signer: Signer, origin: str) -> set[tuple[int, bytes]]:
    """The (size, root) of every checkpoint in ``checkpoints.jsonl`` whose note verifies under the log key for ``origin``; a line that does
    not verify vouches for nothing (recovery reports such a line on its own)."""
    path = directory / "checkpoints.jsonl"
    found: set[tuple[int, bytes]] = set()
    if not path.exists():
        return found
    for line in path.read_bytes().splitlines():
        try:
            wrapper = canonical.parse_record(line)
            checkpoint = verify_checkpoint(base64.b64decode(str(wrapper["note"]), validate=True), [signer.verifier], origin)
        except (canonical.CanonicalError, CheckpointError, KeyError, ValueError):
            continue
        found.add((checkpoint.size, checkpoint.root))
    return found


def _attestations(path: Path, signer: Signer, origin: str) -> list[tuple[int, bytes, str]]:
    """The (size, root, evidence) of every entry of ``attested.jsonl``, each proven by a checkpoint this log signed.

    An entry counts only if the signed checkpoint note it carries verifies under the log key for ``origin`` and names the entry's size and
    root: the stamped text of a checkpoint this log signed (LEDGER_SPEC section 7, invariant 9; ADR-026). An entry with no note was written
    before entries carried one; it counts only if ``checkpoints.jsonl`` beside it holds a checkpoint with its size and root whose note
    verifies under the log key, which is the same proof (only the log key signs one), so a log written before the change still opens, and
    one whose signed checkpoint is gone fails closed. Any other complete line raises ``IntegrityFailure``: the recovery would otherwise
    compare the records with an unauthenticated claim (finding R-ATTEST). Bytes after the last newline were being written when the writer
    stopped, before ``register_attested`` returned, so they were never acknowledged and are skipped.

    What this proves is that the log key signed the checkpoint, not that an outside party stamped it, and a deleted entry is not noticed:
    a party that holds the log key, or that removes the file, is stopped only by an attestation kept away from this host (ADR-026).
    """
    if not path.exists():
        return []
    *complete, _torn = path.read_bytes().split(b"\n")
    found: list[tuple[int, bytes, str]] = []
    signed: set[tuple[int, bytes]] | None = None  # read once, and only for an entry that carries no note
    for number, line in enumerate(complete, start=1):
        entry: tuple[int, bytes, str] | None = None
        try:
            wrapper = canonical.parse_record(line)
            size, root = wrapper["size"], bytes.fromhex(str(wrapper["root"]))
            if "note" not in wrapper:
                if signed is None:
                    signed = _signed_checkpoints(path.parent, signer, origin)
                if type(size) is int and (size, root) in signed:
                    entry = (size, root, str(wrapper["evidence"]))
            else:
                checkpoint = verify_checkpoint(base64.b64decode(str(wrapper["note"]), validate=True), [signer.verifier], origin)
                if type(size) is int and (checkpoint.size, checkpoint.root) == (size, root):
                    entry = (checkpoint.size, checkpoint.root, str(wrapper["evidence"]))
        except (canonical.CanonicalError, CheckpointError, KeyError, ValueError):
            entry = None
        if entry is None:
            raise IntegrityFailure(f"attested.jsonl line {number} is not bound to a checkpoint signed by this log")
        found.append(entry)
    return found


def _commitments(directory: Path, signer: Signer, origin: str) -> list[tuple[int, bytes, str]]:
    """Every (size, root, source) the log has committed to: its own signed checkpoints and its attestations.

    An attestation that is not bound to a checkpoint the log signed raises ``IntegrityFailure`` (``_attestations``), so a replica is never
    judged against one.
    """
    found: list[tuple[int, bytes, str]] = []
    cp_path = directory / "checkpoints.jsonl"
    if cp_path.exists():
        for line in cp_path.read_bytes().splitlines():
            try:
                wrapper = canonical.parse_record(line)
                checkpoint = verify_checkpoint(base64.b64decode(str(wrapper["note"]), validate=True), [signer.verifier], origin)
            except (canonical.CanonicalError, CheckpointError, KeyError, ValueError):
                continue
            found.append((checkpoint.size, checkpoint.root, "own checkpoint"))
    for size, root, evidence in _attestations(directory / "attested.jsonl", signer, origin):
        found.append((size, root, f"attested: {evidence}"))
    return found


def restore_from_replica(directory: str | Path, replica_directory: str | Path, *, signer: Signer, origin: str) -> int:
    """Replace a failed log's segments with a replica's, after proving the replica matches every commitment.

    The replica is accepted only if it is a perfectly clean log and, for every checkpoint the local log signed and
    every externally attested checkpoint it recorded, it is at least that long and has the same root. An attestation
    that is not bound to a checkpoint the log signed refuses the restore (``_attestations``). With no
    commitment to check against, nothing is restored: a replica is not trusted on its own word. The local segments
    are moved to ``quarantine/`` (never deleted), the index is dropped (it is rebuilt), the failure flag is cleared
    and the intent journal is kept, so acknowledged commands that the replica lacks are replayed on the next open.
    Returns the number of records in the restored log. The ledger must not be open.
    """
    d, replica = Path(directory), Path(replica_directory)
    lock_fd = os.open(d / "LOCK", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise LockError(f"another writer holds {d}") from exc
        lines = read_log(replica)
        tree = MerkleTree(r.leaf for r in lines)
        commitments = _commitments(d, signer, origin)
        if not commitments:
            raise LedgerError("nothing to check the replica against: no signed checkpoint and no attestation")
        for size, root, source in commitments:
            if size > tree.size:
                raise IntegrityFailure(f"the replica ({tree.size} records) is shorter than {source} of size {size}")
            if tree.root(size) != root:
                raise IntegrityFailure(f"the replica does not match {source} of size {size}")
        quarantine = d / "quarantine"
        quarantine.mkdir(exist_ok=True)
        number = len(list(quarantine.iterdir()))
        os.replace(d / "segments", quarantine / f"segments-{number}")
        for name in ("index.db", "index.db-wal", "index.db-shm"):
            if (d / name).exists():
                os.replace(d / name, quarantine / f"{name}-{number}")
        (d / "segments").mkdir()
        for path in sorted((replica / "segments").iterdir()):
            shutil.copyfile(path, d / "segments" / path.name)
            with open(d / "segments" / path.name, "rb") as handle:
                os.fsync(handle.fileno())
        _fsync_dir(d / "segments")
        (d / "STATE").unlink(missing_ok=True)
        _fsync_dir(d)
        return tree.size
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
