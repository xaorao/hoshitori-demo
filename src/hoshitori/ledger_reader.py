"""A read-only view of a ledger directory, for everything that looks at a ledger and does not write to it.

``hoshitori-league status``, ``site``, ``export`` and ``replay`` open this instead of the writer. The writer takes the directory's
exclusive lock (so it cannot run beside a running league), recovers (and so may truncate a torn tail), saves a failure it finds into
``STATE`` and signs a checkpoint when it closes. A reader is none of these by construction: the class has no method that writes, takes no
lock, opens no database, and needs no key. The only thing it needs to check the signed checkpoints is the trusted root, whose era keys are
public.

A league may be running while this reads, so what it reads is a consistent prefix of the log:

* ``checkpoints.jsonl`` is read before the segments. A checkpoint is written after the records it covers are durable, so every checkpoint
  read is covered by the segments read after it. (Read the other way round, a record and a checkpoint made in between would look like a
  log rolled back.)
* bytes after the last newline of the newest segment are a line the writer is part-way through: not a record, and not a failure;
* a newest segment whose first line is not whole is a rotation just begun: left out, and not a failure.

Anything else that is not clean is reported in the writer's own words as ``INTEGRITY_FAILURE``, and the reader then holds no record, as
the writer's degraded view does. A ``STATE`` the writer saved is believed.

What it does not check: the attestation file and the SQLite index. Neither is needed to read records, the index is a cache the segments
can rebuild, and the attestation file is read by the verifier from a bundle. Without a trusted root the signed checkpoints are not read at
all, and the reader then says only that the segments are a clean chain.

``FORMAT`` is the version of the on-disk form of a ledger directory. A directory with no ``FORMAT`` file was written before the file
existed and is version 1; any other value is refused.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from hoshitori import canonical, segment, trusted_root
from hoshitori.checkpoint import Checkpoint, CheckpointError, parse_text, verify_checkpoint
from hoshitori.ledger import STATE_INTEGRITY_FAILURE, STATE_OK, IntegrityFailure, LedgerError, check_format
from hoshitori.merkle import MerkleTree
from hoshitori.note import NoteError, split_note


def signed_checkpoint(note: bytes, root: trusted_root.TrustedRoot) -> Checkpoint:
    """The checkpoint of a note signed by a key the root allows for the note's size, for the root's log."""
    text, _ = split_note(note)
    size = parse_text(text).size
    keys = [era.key for era in root.eras if root.era_check(era.key, size) == trusted_root.ERA_ACCEPT]
    return verify_checkpoint(note, keys, root.origin)


class LedgerReader:
    """The records, proofs and state of a ledger directory, read and never written. Open it with :meth:`open`."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.state = STATE_OK
        self.state_detail = ""
        self._lines: list[segment.RecordLine] = []
        self._tree = MerkleTree()
        self._keys: dict[tuple[str, str], int] | None = None

    @classmethod
    def open(cls, directory: str | Path, *, root: trusted_root.TrustedRoot | None) -> LedgerReader:
        """Read ``directory``. With a trusted root its signed checkpoints are checked too; with ``None`` they are not read."""
        reader = cls(Path(directory))
        if not (reader.directory / "segments").is_dir():
            raise LedgerError(f"there is no ledger in {reader.directory}")
        check_format(reader.directory)
        saved = reader._saved_state()
        if saved is not None:
            reader.state, reader.state_detail = saved
            return reader
        checkpoints = reader._complete_lines(reader.directory / "checkpoints.jsonl")  # before the segments: see the module docstring
        try:
            reader._lines = reader._scan_segments()
            reader._tree = MerkleTree(line.leaf for line in reader._lines)
            if root is not None:
                reader._check_checkpoints(checkpoints, root)
        except IntegrityFailure as exc:
            reader._lines, reader._tree, reader._keys = [], MerkleTree(), None
            reader.state, reader.state_detail = STATE_INTEGRITY_FAILURE, str(exc)
        return reader

    # ------------------------------------------------------------------ reading the files
    def _saved_state(self) -> tuple[str, str] | None:
        """The failure the writer saved, if it saved one."""
        path = self.directory / "STATE"
        if not path.exists():
            return None
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            state, detail = str(saved["state"]), str(saved.get("detail", ""))
        except (ValueError, KeyError, TypeError, AttributeError):
            return STATE_INTEGRITY_FAILURE, "the STATE file cannot be read"
        return None if state == STATE_OK else (state, detail)

    @staticmethod
    def _complete_lines(path: Path) -> list[bytes]:
        """The lines of an append-only file that end in a newline; what follows the last newline is a line being written."""
        if not path.exists():
            return []
        data = path.read_bytes()
        return data[: data.rfind(b"\n") + 1].splitlines()

    def _segment_files(self) -> list[Path]:
        names = sorted(p.name for p in (self.directory / "segments").iterdir() if not p.name.endswith(".tmp"))
        for name in names:
            try:
                segment.segment_first_seq(name)
            except segment.SegmentError:
                raise IntegrityFailure(f"unexpected file in the segment directory: {name}") from None
        return [self.directory / "segments" / name for name in names]

    def _scan_segments(self) -> list[segment.RecordLine]:
        files = self._segment_files()
        lines: list[segment.RecordLine] = []
        previous: tuple[str, bytes, int] | None = None
        expected = 0
        for index, path in enumerate(files):
            first = segment.segment_first_seq(path.name)
            if first != expected:
                raise IntegrityFailure(f"segment {path.name} starts at {first}, expected {expected}")
            data = path.read_bytes()
            last = index == len(files) - 1
            if last:
                data = data[: data.rfind(b"\n") + 1]  # a line being written is not a record yet
                if index > 0 and not data:
                    break  # a rotation just begun: its first line is not whole
            scan = segment.scan_segment(data, first, previous)
            if scan.problem is not segment.Problem.NONE:
                raise IntegrityFailure(self._problem(path.name, first, scan, last))
            lines.extend(scan.records)
            expected = first + len(scan.records)
            previous = (path.name, hashlib.sha256(data).digest(), len(scan.records))
        return lines

    @staticmethod
    def _problem(name: str, first: int, scan: segment.Scan, last: bool) -> str:
        if not last:
            return f"closed segment {name}: {scan.detail} (offset {scan.problem_offset})"
        if scan.problem is segment.Problem.LAST_LINE_INVALID:
            return f"segment {name}: line {first + len(scan.records)} is complete but invalid ({scan.detail})"
        return f"segment {name}: {scan.detail} at offset {scan.problem_offset}"

    def _check_checkpoints(self, lines: list[bytes], root: trusted_root.TrustedRoot) -> None:
        last_size = 0
        for number, line in enumerate(lines, start=1):
            try:
                wrapper = canonical.parse_record(line)
                checkpoint = signed_checkpoint(base64.b64decode(str(wrapper["note"]), validate=True), root)
            except (canonical.CanonicalError, CheckpointError, NoteError, KeyError, ValueError) as exc:
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

    # ------------------------------------------------------------------ what a ledger answers
    @property
    def size(self) -> int:
        return self._tree.size

    def root(self, size: int | None = None) -> bytes:
        return self._tree.root(size)

    def read_record(self, seq: int) -> bytes:
        """The canonical record bytes at ``seq`` (including ``committed_at``); the scan has already checked each against its leaf hash."""
        if not 0 <= seq < len(self._lines):
            raise LedgerError(f"no record {seq}")
        return self._lines[seq].record

    def find(self, source: str, record_id: str) -> int | None:
        if self._keys is None:
            self._keys = {}
            for line in self._lines:
                record = canonical.parse_record(line.record)
                self._keys[(str(record["source"]), str(record["id"]))] = line.seq
        return self._keys.get((source, record_id))

    def inclusion_proof(self, seq: int, size: int | None = None) -> list[bytes]:
        return self._tree.inclusion_proof(seq, size)

    def consistency_proof(self, first: int, second: int | None = None) -> list[bytes]:
        return self._tree.consistency_proof(first, second)

    def leaf_hash_at(self, seq: int) -> bytes:
        return self._tree.leaf_hash_at(seq)

    def checkpoint(self) -> bytes:
        """A reader signs nothing: a caller that wants a checkpoint (a bundle without one given) must be given one."""
        raise LedgerError("a read-only ledger signs no checkpoint: give the bundle the checkpoint note to use")
