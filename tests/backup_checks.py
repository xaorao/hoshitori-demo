"""Scenarios for the backup of a league folder, runnable against the real module or a mutant of it (``failures(mod)``).

Each scenario builds its own league folder in a temporary directory: a settings file, the files it names (the protocol and the mapping are
fixture bytes, since a backup copies them and never reads them; the trusted root is a real one whose era key is the ledger's key), and a
state directory with a real ledger, a real capture store, a commitment laid out as ``commitment_store`` lays it out and a dispatch journal.
The expectations are worked out by hand from the writer's order in ``hoshitori.ledger`` and the copy order the backup promises (see the
module's docstring): checkpoints, journal, index, segments, captures, commitments, dispatch journal. They are never the module's output.

The writer runs in this process and writes between the steps of a backup through the hook, so every interleaving is exact and repeatable;
``tests/test_restore_drill.py`` runs the writer in another process and in a thread, for real.
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import threading
import types
import typing
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path, PosixPath
from typing import Any

from hoshitori import canonical, merkle, segment
from hoshitori.capture import CaptureStore, RawCapture
from hoshitori.checkpoint import Checkpoint, sign_checkpoint
from hoshitori.clock import ManualClock
from hoshitori.ledger import Ledger, read_log
from hoshitori.note import Signer
from hoshitori.tlog_proof import TlogProof, format_proof
from hoshitori.trusted_root import parse_root
from tests import root_support as R
from tests.deployment_checks import good_settings

START = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
NAME_AT_START = "backup-20261029T120000000Z"  # "backup-" and the instant in UTC to the millisecond, written by hand
SIGNER = R.log_signer(7)  # the key of the root's only era (``R.make_document``'s default)
OTHER_SIGNER = R.log_signer(8)
OTHER_ORIGIN = "hoshitori.example/other-log"
SOURCE = "urn:hoshitori:test"
FIXTURE = b'{"fixture":"not a protocol: a backup copies these bytes and never reads them"}\n'
TOKEN = b"fixture token bytes"
CAPTURE_BODY = b'{"serverTime":1000}'
SEGMENT_RECORDS = 3


def _root_document(signer: Signer = SIGNER, origin: str | None = None) -> bytes:
    document = R.make_document(1, eras=[{"vkey": signer.verifier.vkey(), "from_size": 0, "through_size": None}])
    if origin is not None:
        log = document["log"]
        assert isinstance(log, dict)
        log["origin"] = origin
    return canonical.dumps(document)  # type: ignore[arg-type]


ROOT_BYTES = _root_document()
ROOT = parse_root(ROOT_BYTES)
OTHER_KEY_ROOT_BYTES = _root_document(OTHER_SIGNER)
OTHER_KEY_ROOT = parse_root(OTHER_KEY_ROOT_BYTES)
OTHER_ORIGIN_ROOT_BYTES = _root_document(Signer.from_seed(OTHER_ORIGIN, bytes(32)), OTHER_ORIGIN)
OTHER_ORIGIN_ROOT = parse_root(OTHER_ORIGIN_ROOT_BYTES)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rec(i: int, extra: str = "") -> dict[str, canonical.JsonValue]:
    return {
        "spec": "hoshitori.record/1",
        "type": "evidence",
        "type_version": 1,
        "id": f"ev{i}",
        "source": SOURCE,
        "created_at": "2026-10-29T11:59:59.000Z",
        "body": {"n": i, "pad": extra},
    }


def capture(i: int, body: bytes = CAPTURE_BODY) -> RawCapture:
    return RawCapture(
        capture_id=f"cap-{i:032x}",
        source="binance-spot",
        request={"op": "time"},
        status=200,
        headers={"content-type": "application/json"},
        body=body,
        t_send=START,
        received_at=START + timedelta(milliseconds=5),
    )


def open_ledger(directory: Path, signer: Signer = SIGNER) -> Ledger:
    return Ledger.open(directory, signer=signer, origin=signer.name, clock=ManualClock(START), segment_records=SEGMENT_RECORDS)


class Folder:
    """A league folder with a ledger of seven records (checkpointed and attested at 7) and one acknowledged command not yet appended.

    ``files`` names more files to write beside the settings (with ``settings`` naming them, for the barrier mapping and the model
    settings); ``signer`` and ``root_bytes`` change the log key and the trusted root."""

    def __init__(
        self,
        base: Path,
        *,
        signer: Signer = SIGNER,
        root_bytes: bytes = ROOT_BYTES,
        extra: str = "",
        files: tuple[str, ...] = (),
        **settings: Any,
    ) -> None:
        self.base = base
        base.mkdir(parents=True)
        raw = {**good_settings(), "directory": "run", "log_key": "log.key", **settings}
        self.settings = base / "settings.json"
        self.settings.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        for name in ("protocol.json", "mapping.json", *files):
            (base / name).write_bytes(FIXTURE)
        (base / "root.json").write_bytes(root_bytes)
        self.extra_files = files
        self.state = base / "run"
        self.ledger_dir = self.state / "ledger"
        self.led = open_ledger(self.ledger_dir, signer)
        for i in range(7):
            self.led.submit(rec(i, extra))
        self.led.append_pending()
        self.cp7 = self.led.checkpoint()
        self.root7 = self.led.root()
        self.led.register_attested(7, self.root7, "fixture attestation")
        self.led.submit(rec(7, extra))  # acknowledged, not appended
        self.captures = CaptureStore(self.state / "captures")
        self.captures.append(capture(1))
        self.captures.close()
        kept = self.state / "commitments" / "c1"
        kept.mkdir(parents=True)
        (kept / "note").write_bytes(self.cp7)
        (kept / "alpha.tsr").write_bytes(TOKEN)
        staging = self.state / "commitments" / "c2.new"  # a save that had not been renamed into place
        staging.mkdir()
        (staging / "note").write_bytes(self.cp7)
        (self.state / "dispatch.jsonl").write_bytes(b'{"attempt":1}\n{"attempt":2}\n{"attem')

    def close(self) -> None:
        self.led.abandon()

    def files(self) -> list[str]:
        """The paths a backup of this folder holds when nothing is written during it, worked out from the layout above."""
        blob = sha(CAPTURE_BODY)
        return sorted(
            [
                *self.extra_files,
                "mapping.json",
                "protocol.json",
                "root.json",
                "settings.json",
                f"run/captures/blobs/{blob[:2]}/{blob}",
                "run/captures/captures.jsonl",
                "run/commitments/c1/alpha.tsr",
                "run/commitments/c1/note",
                "run/dispatch.jsonl",
                "run/ledger/attested.jsonl",
                "run/ledger/checkpoints.jsonl",
                "run/ledger/index.db",
                "run/ledger/journal.jsonl",
                "run/ledger/segments/segment-000000000000.jsonl",
                "run/ledger/segments/segment-000000000003.jsonl",
                "run/ledger/segments/segment-000000000006.jsonl",
            ]
        )


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def frozen(self, label: str, value: object, member: str) -> None:
        """A value made once and never changed: setting a member of it is refused (a frozen dataclass)."""
        try:
            setattr(value, member, getattr(value, member))
        except dataclasses.FrozenInstanceError:
            return
        self.bad.append(f"{label}: its {member} can be set after it is made")

    def refuses(self, label: str, call: Callable[[], object], says: str, *, prefix: bool = False) -> None:
        try:
            call()
        except self.mod.BackupError as exc:
            text = str(exc)
            if (prefix and not text.startswith(says)) or (not prefix and text != says):
                self.bad.append(f"{label}: refused with {text!r}, expected {'a message starting' if prefix else 'exactly'} {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def take(mod: types.ModuleType, folder: Folder, into: Path, *, at: datetime = START, hook: Callable[[str], None] | None = None) -> Any:
    return mod.take(folder.settings, into, clock=ManualClock(at), hook=hook)


def manifest_of(backup: Path) -> dict[str, Any]:
    return canonical.parse_record((backup / "BACKUP.json").read_bytes().rstrip(b"\n"))


def rewrite_manifest(backup: Path, change: Callable[[dict[str, Any]], None]) -> None:
    manifest = manifest_of(backup)
    change(manifest)
    (backup / "BACKUP.json").write_bytes(canonical.dumps(manifest) + b"\n")


def rehash(backup: Path, *paths: str) -> None:
    """Make the manifest agree with files the scenario changed, so a later check is reached."""

    def change(manifest: dict[str, Any]) -> None:
        for entry in manifest["files"]:
            if entry["path"] in paths:
                data = (backup / entry["path"]).read_bytes()
                entry["size"], entry["sha256"] = len(data), sha(data)

    rewrite_manifest(backup, change)


def drop_entry(backup: Path, path: str) -> None:
    rewrite_manifest(backup, lambda m: m.__setitem__("files", [e for e in m["files"] if e["path"] != path]))


def ledger_rows(directory: Path) -> list[tuple[Any, ...]]:
    db = sqlite3.connect(directory / "index.db")
    try:
        return db.execute("SELECT seq, source, rec_id, leaf, segment, off, len FROM ledger ORDER BY seq").fetchall()
    finally:
        db.close()


def record_ids(directory: Path) -> list[str]:
    return [str(canonical.parse_record(line.record)["id"]) for line in read_log(directory)]


def open_descriptors() -> set[int]:
    """The file descriptors this process has open. Listing ``/dev/fd`` opens a descriptor of its own, which would hide a leaked one with the
    same number, so the listing only gives the highest number and each number up to it is then tried once the listing is closed."""
    top = max(int(name) for name in os.listdir("/dev/fd"))
    found: set[int] = set()
    for number in range(top + 1):
        try:
            os.fstat(number)
        except OSError:
            continue
        found.add(number)
    return found


def says(text: str, phrase: str) -> bool:
    """Whether the phrase stands in the text as written, with nothing joined to its end (argparse wraps lines, so spaces are folded)."""
    return re.search(re.escape(phrase) + r"(\s|$)", " ".join(text.split())) is not None


class Flushes(io.StringIO):
    """Standard output that keeps what had been written each time it was flushed."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[str] = []

    def flush(self) -> None:
        self.seen.append(self.getvalue())
        super().flush()


class Reversed:
    """The os module as the module under test sees it, except that a folder's entries come in reverse name order: a file system may list
    them in any order, and what a backup says must not depend on it."""

    def __getattr__(self, name: str) -> Any:
        return getattr(os, name)

    @staticmethod
    def listdir(path: Any) -> list[str]:
        return sorted(os.listdir(path), reverse=True)

    @staticmethod
    def walk(top: Any, topdown: bool = True, onerror: Any = None, followlinks: bool = False) -> Iterator[tuple[str, list[str], list[str]]]:
        for folder, folders, names in os.walk(top, topdown, onerror, followlinks):
            folders.sort(reverse=True)
            yield folder, folders, sorted(names, reverse=True)


@contextlib.contextmanager
def replaced(mod: types.ModuleType, name: str, value: Any) -> Iterator[None]:
    """A name of the module under test bound to a stand-in for the length of a block."""
    real = getattr(mod, name)
    setattr(mod, name, value)
    try:
        yield
    finally:
        setattr(mod, name, real)


class Zoned:
    """A clock that tells the time as 21:00 in a zone nine hours ahead of UTC: the instant is START."""

    def now(self) -> datetime:
        return datetime(2026, 10, 29, 21, 0, tzinfo=timezone(timedelta(hours=9)))


def verify_in_time(mod: types.ModuleType, backup: Path, pipe: Path, seconds: float = 5.0) -> tuple[bool, object]:
    """``verify`` in a thread. A pipe opened for reading waits for a writer, so if the verify still runs after ``seconds`` the pipe is
    opened for writing, which lets it end. Whether it ended in time, and what it returned or raised."""
    result: list[object] = []

    def go() -> None:
        try:
            result.append(mod.verify(backup, ROOT))
        except BaseException as exc:
            result.append(exc)

    thread = threading.Thread(target=go, name="verify-a-pipe", daemon=True)
    thread.start()
    thread.join(seconds)
    in_time = not thread.is_alive()
    while thread.is_alive():
        with contextlib.suppress(OSError):
            os.close(os.open(pipe, os.O_WRONLY | os.O_NONBLOCK))
        thread.join(0.1)
    return in_time, result[0] if result else None


# --- a backup with nothing written during it ---------------------------------------------------------------------------------------------
def check_quiet_backup(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    folder = Folder(tmp / "league", files=("barrier.json", "models.json"), barrier_mappings={"L0": "barrier.json"}, models="models.json")
    source_bytes = {p: (folder.base / p).read_bytes() for p in folder.files()}
    into = tmp / "backups" / "daily"  # a folder whose parent does not exist yet
    before = open_descriptors()
    taken = take(mod, folder, into)
    # In production the league is another process. Here the test's own ledger holds the same index.db, and while any connection to a file
    # stays open in a process SQLite keeps the descriptor of a connection closed meanwhile (probed: it goes when the last one closes).
    # So the count is taken once the test has let go of its ledger; a descriptor the take really leaked is still open then.
    folder.close()
    c.eq("no file is left open by the take", sorted(open_descriptors() - before), [])
    backup = into / NAME_AT_START
    c.eq("the backup's folder", taken.path, backup)
    c.eq("a backup just taken was checked against no anchor", taken.anchors, 0)
    c.frozen("what a take returns", taken, "anchors")
    c.frozen("a manifest", taken.manifest, "records")
    c.frozen("a file entry", taken.manifest.files[0], "sha256")
    c.frozen("the facts of a backup", mod._Facts(merkle.MerkleTree(()), 0, 0, 0, ()), "captures")
    c.eq("no unfinished backup is left", sorted(p.name for p in into.iterdir()), [NAME_AT_START])
    m = taken.manifest
    c.eq(
        "what the manifest counts",
        (m.records, m.ledger_root, m.checkpoints, m.last_checkpoint, m.captures, m.commitments, m.origin, m.root_sha256, m.settings),
        (7, folder.root7.hex(), 1, 7, 1, ("c1",), R.LOG_ORIGIN, sha(ROOT_BYTES), "settings.json"),
    )
    c.eq("when", m.taken_at, "2026-10-29T12:00:00.000Z")
    c.eq("the files, in order", [f.path for f in m.files], folder.files())
    for entry in m.files:
        data = (backup / entry.path).read_bytes()
        c.eq(f"{entry.path}: size and hash as listed", (entry.size, entry.sha256), (len(data), sha(data)))
        c.eq(f"{entry.path}: readable by its owner only", oct((backup / entry.path).stat().st_mode & 0o777), oct(0o600))
    for path in folder.files():
        if path in ("run/ledger/index.db", "run/dispatch.jsonl"):
            continue
        c.eq(f"{path} is copied byte for byte", (backup / path).read_bytes(), source_bytes[path])
    c.eq("the dispatch journal up to its last newline", (backup / "run/dispatch.jsonl").read_bytes(), b'{"attempt":1}\n{"attempt":2}\n')
    db = sqlite3.connect(f"file:{backup / 'run/ledger/index.db'}?mode=ro&immutable=1", uri=True)
    try:
        c.eq("and whole", db.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
        c.eq(
            "with the pending command",
            db.execute("SELECT rec_id, state FROM outbox WHERE state='pending'").fetchall(),
            [("ev7", "pending")],
        )
    finally:
        db.close()
    # SQLite file format, section 1.3.3: the bytes at offsets 18 and 19 are 1 for rollback journalling and 2 for WAL
    c.eq("the copied index is one file, in rollback mode", (backup / "run/ledger/index.db").read_bytes()[18:20], b"\x01\x01")
    c.eq("no side file of the index", sorted(p.name for p in (backup / "run/ledger").iterdir() if p.name.startswith("index")), ["index.db"])
    c.eq("the manifest file ends in one newline", (backup / "BACKUP.json").read_bytes().endswith(b"}\n"), True)
    c.eq("and is readable by its owner only", oct((backup / "BACKUP.json").stat().st_mode & 0o777), oct(0o600))
    before = open_descriptors()
    again = mod.verify(backup, ROOT, [folder.cp7])
    c.eq("no file is left open by the verify", sorted(open_descriptors() - before), [])
    c.eq("it verifies, with an anchor", (again.manifest.records, again.anchors), (7, 1))
    c.eq("and without one", mod.verify(backup, ROOT).anchors, 0)
    folder.close()

    target = tmp / "new" / "deep" / "restored"  # a folder whose parents do not exist yet
    before = open_descriptors()
    done = mod.restore(
        backup, target, root=ROOT, seed=(7).to_bytes(32, "big"), anchors=[folder.cp7], unanchored=False, clock=ManualClock(START)
    )
    c.eq("no file is left open by the restore", sorted(open_descriptors() - before), [])
    c.frozen("what a restore returns", done, "records")
    c.eq(
        "what the restore reports",
        (done.path, done.records, done.root, done.checkpoints, done.anchors, done.log_key),
        (target, 7, folder.root7.hex(), 1, 1, target / "log.key"),
    )
    c.eq(
        "and what the recovery did",
        (done.indexed_missing_rows, done.replayed_intents, done.pending, done.captures, done.commitments),
        (0, 0, 1, 1, 1),
    )
    c.eq("nothing else beside the target", sorted(p.name for p in target.parent.iterdir()), ["restored"])
    for path in folder.files():
        if path != "run/ledger/index.db":
            c.eq(f"{path} restored as backed up", (target / path).read_bytes(), (backup / path).read_bytes())
            c.eq(f"{path}: restored readable by its owner only", oct((target / path).stat().st_mode & 0o777), oct(0o600))
    c.eq("the log key is not restored", (target / "log.key").exists(), False)
    led = open_ledger(target / "run" / "ledger")
    try:
        c.eq("the restored ledger opens", (led.state, led.size, led.root()), ("OK", 7, folder.root7))
        led.append_pending()
        led.submit(rec(8))
        led.append_pending()
        note = led.checkpoint()
        c.eq("the pending command and a new one continue the log", record_ids(target / "run" / "ledger"), [f"ev{i}" for i in range(9)])
        c.eq(
            "the chain of segments goes on",
            [p.name for p in sorted((target / "run/ledger/segments").iterdir())][-1],
            segment.segment_name(6),
        )
        c.eq(
            "the new head is consistent with the backed-up checkpoint",
            merkle.verify_consistency(7, 9, led.consistency_proof(7), folder.root7, led.root()),
            True,
        )
        c.eq("and signed", note.startswith(f"{R.LOG_ORIGIN}\n9\n".encode()), True)
    finally:
        led.abandon()
    store = CaptureStore(target / "run" / "captures")
    try:
        c.eq("the capture is back", (len(store), store.get(f"cap-{1:032x}").body), (1, CAPTURE_BODY))
    finally:
        store.close()
    return c.bad


# --- writes between the steps ------------------------------------------------------------------------------------------------------------
# What the backup holds when the writer, at the named point, appends the command it had queued (ev7) and two more (ev8, ev9; ev9 rotates
# into segment 9), checkpoints at 10, queues ev10 and stores a second capture. Worked out from the copy order, see the module docstring:
# (records, checkpoints, last checkpoint, captures) in the backup, then (index rows the recovery adds, journal intents it replays, commands
# still queued) when the restore opens it, then the records once the queue is appended.
INTERLEAVINGS: dict[str, tuple[tuple[int, int, int, int], tuple[int, int, int], int]] = {
    "config": ((10, 2, 10, 2), (0, 0, 1), 11),  # everything is copied after the writes
    "checkpoints": ((10, 1, 7, 2), (0, 0, 1), 11),  # the checkpoint of 10 is newer than the copy of the checkpoints
    "journal": ((10, 1, 7, 2), (0, 0, 1), 11),  # ev10 is not in the journal copy but is queued in the index copy
    "index": ((10, 1, 7, 2), (3, 0, 0), 10),  # rows 7 to 9 are in the segments only; ev10 is in neither copy (acknowledged after the start)
    "listed_segments": ((9, 1, 7, 2), (2, 0, 0), 9),  # the listing has segments 0, 3 and 6; segment 6 is read with 7 and 8; 9 is not listed
    "segments": ((7, 1, 7, 2), (0, 0, 1), 8),  # the ledger is copied before the writes; the capture after them
    "captures": ((7, 1, 7, 1), (0, 0, 1), 8),  # the second capture is newer than the copy of the captures
}


def check_writes_between_steps(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    for point, (held, recovered, appended) in INTERLEAVINGS.items():
        folder = Folder(tmp / point / "league")
        seen: list[str] = []

        def hook(name: str, point: str = point, folder: Folder = folder, seen: list[str] = seen) -> None:
            seen.append(name)
            if name == point:
                folder.led.submit(rec(8))
                folder.led.submit(rec(9))
                folder.led.append_pending()
                folder.led.checkpoint()
                folder.led.submit(rec(10))
                store = CaptureStore(folder.state / "captures")
                store.append(capture(2, b'{"serverTime":2000}'))
                store.close()

        taken = take(mod, folder, tmp / point / "backups", hook=hook)
        folder.close()
        c.eq(
            f"{point}: the hook points, in order",
            seen,
            [
                "config",
                "checkpoints",
                "journal",
                "index_step",
                "index",
                "listed_segments",
                "segments",
                "captures",
                "commitments",
                "dispatch",
            ],
        )
        m = taken.manifest
        c.eq(f"{point}: what the backup holds", (m.records, m.checkpoints, m.last_checkpoint, m.captures), held)
        target = tmp / point / "restored"
        done = mod.restore(
            taken.path, target, root=ROOT, seed=(7).to_bytes(32, "big"), anchors=[folder.cp7], unanchored=False, clock=ManualClock(START)
        )
        c.eq(f"{point}: what the recovery did", (done.indexed_missing_rows, done.replayed_intents, done.pending), recovered)
        led = open_ledger(target / "run" / "ledger")
        try:
            c.eq(f"{point}: the records that existed when the backup began", led.root(7), folder.root7)
            led.append_pending()
            ids = record_ids(target / "run" / "ledger")
            c.eq(f"{point}: records once the queue is appended", len(ids), appended)
            c.eq(
                f"{point}: every command acknowledged before the backup began",
                [i for i in ids if i in {f"ev{n}" for n in range(8)}],
                [f"ev{n}" for n in range(8)],
            )
            c.eq(f"{point}: no duplicate", len(ids), len(set(ids)))
        finally:
            led.abandon()
        rebuilt = tmp / point / "rebuilt"
        shutil.copytree(target / "run" / "ledger", rebuilt, ignore=shutil.ignore_patterns("index.db*", "LOCK"))
        again = open_ledger(rebuilt)
        again.abandon()
        c.eq(
            f"{point}: the restored index equals one rebuilt from the segments",
            ledger_rows(target / "run" / "ledger"),
            ledger_rows(rebuilt),
        )
    return c.bad


# --- refusals of a damaged or foreign backup ---------------------------------------------------------------------------------------------
def _fresh(tmp: Path, name: str, mod: types.ModuleType, **kw: Any) -> tuple[Folder, Path]:
    folder = Folder(tmp / name / "league", **kw)
    taken = take(mod, folder, tmp / name / "backups")
    folder.close()
    return folder, taken.path


def check_torn_and_missing(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    folder, backup = _fresh(tmp, "torn", mod)
    seg6 = "run/ledger/segments/segment-000000000006.jsonl"
    whole = (folder.base / seg6).read_bytes()
    (backup / seg6).write_bytes(whole[:-5])
    c.refuses(
        "a segment cut short",
        lambda: mod.verify(backup, ROOT),
        f"{seg6} is not the file the manifest lists: {len(whole) - 5} bytes with SHA-256 {sha(whole[:-5])}, "
        f"the manifest says {len(whole)} bytes with {sha(whole)}",
    )
    folder, backup = _fresh(tmp, "torn-checkpoints", mod)
    cps = (folder.base / "run/ledger/checkpoints.jsonl").read_bytes()
    (backup / "run/ledger/checkpoints.jsonl").write_bytes(cps[: len(cps) // 2])
    c.refuses(
        "the checkpoints cut short",
        lambda: mod.verify(backup, ROOT),
        f"run/ledger/checkpoints.jsonl is not the file the manifest lists: {len(cps) // 2} bytes with SHA-256 {sha(cps[: len(cps) // 2])}, "
        f"the manifest says {len(cps)} bytes with {sha(cps)}",
    )
    folder, backup = _fresh(tmp, "missing", mod)
    seg3 = "run/ledger/segments/segment-000000000003.jsonl"
    (backup / seg3).unlink()
    c.refuses("a segment missing", lambda: mod.verify(backup, ROOT), f"the backup lacks {seg3}, which its manifest lists")
    drop_entry(backup, seg3)
    c.refuses(
        "a segment missing from the files and the manifest",
        lambda: mod.verify(backup, ROOT),
        "the backup's segments are not a clean log: segment segment-000000000006.jsonl starts at 6, expected 3",
    )
    folder, backup = _fresh(tmp, "missing-last", mod)
    (backup / seg6).unlink()
    drop_entry(backup, seg6)
    c.refuses(
        "the last segment missing from the files and the manifest",
        lambda: mod.verify(backup, ROOT),
        "the backup holds 6 records and its manifest says 7",
    )
    rewrite_manifest(backup, lambda m: m["ledger"].__setitem__("records", 6))
    c.refuses(
        "and a manifest that agrees",
        lambda: mod.verify(backup, ROOT),
        "the backup's records do not have the root its manifest names",
    )

    def shorter_root(m: dict[str, Any]) -> None:
        m["ledger"]["root"] = merkle.MerkleTree(line.leaf for line in read_log(folder.ledger_dir)[:6]).root().hex()

    rewrite_manifest(backup, shorter_root)
    c.refuses("and a root that agrees", lambda: mod.verify(backup, ROOT), "the checkpoint of size 7 in the backup exceeds its 6 records")
    return c.bad


def check_another_log(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    folder, backup = _fresh(tmp, "a", mod)
    c.refuses(
        "a trusted root with another key",
        lambda: mod.verify(backup, OTHER_KEY_ROOT),
        f"the backup was taken under another trusted root (SHA-256 {sha(ROOT_BYTES)}), not the one given ({sha(OTHER_KEY_ROOT_BYTES)})",
    )
    c.refuses(
        "a trusted root of another log",
        lambda: mod.verify(backup, OTHER_ORIGIN_ROOT),
        f"the backup is of the log {R.LOG_ORIGIN}, not of {OTHER_ORIGIN}",
    )
    _, other_backup = _fresh(tmp, "d", mod, extra="another history")
    c.refuses(
        "the same key and root, another history, caught by an anchor",
        lambda: mod.verify(other_backup, ROOT, [folder.cp7]),
        "the backup does not match anchor 1 of size 7: it holds another history",
    )
    newer = open_ledger(folder.ledger_dir)
    newer.append_pending()
    newer.submit(rec(8))
    newer.submit(rec(9))
    newer.append_pending()
    cp10 = newer.checkpoint()
    newer.abandon()
    c.refuses(
        "an older backup against a newer anchor",
        lambda: mod.verify(backup, ROOT, [folder.cp7, cp10]),
        "the backup holds 7 records, fewer than anchor 2 of size 10: it is older than the anchor",
    )
    forged = sign_checkpoint(Checkpoint(R.LOG_ORIGIN, 7, folder.root7), [OTHER_SIGNER])
    c.refuses(
        "an anchor signed by another key",
        lambda: mod.verify(backup, ROOT, [forged]),
        "anchor 1 is not a checkpoint signed under the trusted root: ",
        prefix=True,
    )
    (backup / "root.json").write_bytes(OTHER_KEY_ROOT_BYTES)
    rehash(backup, "root.json")
    c.refuses(
        "a backup whose own root was swapped",
        lambda: mod.verify(backup, ROOT),
        "the trusted root in the backup is not the one its manifest names",
    )
    return c.bad


def check_manifest_and_files(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    _, backup = _fresh(tmp, "extra", mod)
    (backup / "extra.txt").write_bytes(b"x")
    c.refuses("an unlisted file", lambda: mod.verify(backup, ROOT), "the backup holds extra.txt, which its manifest does not list")
    folder, backup = _fresh(tmp, "link", mod)
    (backup / "protocol.json").unlink()
    (backup / "protocol.json").symlink_to(folder.base / "protocol.json")
    c.refuses("a link in place of a file", lambda: mod.verify(backup, ROOT), "protocol.json in the backup is not a regular file")
    _, backup = _fresh(tmp, "escape", mod)
    rewrite_manifest(backup, lambda m: m["files"].append({"path": "../escape", "sha256": sha(b""), "size": 0}))
    c.refuses("a path outside", lambda: mod.verify(backup, ROOT), "the manifest names a path outside the backup: '../escape'")
    _, backup = _fresh(tmp, "twice", mod)
    rewrite_manifest(backup, lambda m: m["files"].append(dict(m["files"][0])))
    c.refuses("a path twice", lambda: mod.verify(backup, ROOT), "the manifest names mapping.json twice")
    _, backup = _fresh(tmp, "count", mod)
    rewrite_manifest(backup, lambda m: m["ledger"].__setitem__("records", 8))
    c.refuses("a wrong count", lambda: mod.verify(backup, ROOT), "the backup holds 7 records and its manifest says 8")
    _, backup = _fresh(tmp, "format", mod)
    rewrite_manifest(backup, lambda m: m.__setitem__("format", "hoshitori-backup/0"))
    c.refuses(
        "another format", lambda: mod.verify(backup, ROOT), "BACKUP.json is not a backup manifest: the format is not hoshitori-backup/1"
    )
    _, backup = _fresh(tmp, "members", mod)
    rewrite_manifest(backup, lambda m: m.pop("captures"))
    c.refuses(
        "a member missing",
        lambda: mod.verify(backup, ROOT),
        "BACKUP.json is not a backup manifest: its members are "
        "['captures', 'commitments', 'files', 'format', 'ledger', 'origin', 'root_sha256', 'settings', 'taken_at']",
    )
    _, backup = _fresh(tmp, "not-canonical", mod)
    (backup / "BACKUP.json").write_bytes(json.dumps(manifest_of(backup), indent=1).encode() + b"\n")
    c.refuses("a manifest not written by a backup", lambda: mod.verify(backup, ROOT), "BACKUP.json is not a backup manifest: ", prefix=True)
    _, backup = _fresh(tmp, "not-an-object", mod)
    (backup / "BACKUP.json").write_bytes(b"[]\n")
    c.refuses(
        "a manifest that is not an object",
        lambda: mod.verify(backup, ROOT),
        "BACKUP.json is not a backup manifest: it is not canonical JSON: a record must be a JSON object",
    )
    _, backup = _fresh(tmp, "gone", mod)
    (backup / "BACKUP.json").unlink()
    c.refuses("no manifest", lambda: mod.verify(backup, ROOT), f"{backup} holds no backup: BACKUP.json is missing")
    _, backup = _fresh(tmp, "no-settings", mod)
    rewrite_manifest(backup, lambda m: m.__setitem__("settings", "other.json"))
    c.refuses(
        "settings not in the backup",
        lambda: mod.verify(backup, ROOT),
        "the backup lacks other.json, which its manifest names as the settings",
    )
    _, backup = _fresh(tmp, "no-protocol", mod)
    (backup / "protocol.json").unlink()
    drop_entry(backup, "protocol.json")
    c.refuses(
        "a file the settings name is not in it", lambda: mod.verify(backup, ROOT), "the backup lacks protocol.json, which its settings name"
    )
    return c.bad


def check_ledger_files(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    folder, backup = _fresh(tmp, "row", mod)
    index = backup / "run/ledger/index.db"
    db = sqlite3.connect(index, isolation_level=None)
    db.execute("UPDATE ledger SET len = len + 1 WHERE seq = 2")
    db.close()
    rehash(backup, "run/ledger/index.db")
    c.refuses("an index row that disagrees", lambda: mod.verify(backup, ROOT), "row 2 of the backup's index disagrees with its segments")
    _, backup = _fresh(tmp, "beyond", mod)
    index = backup / "run/ledger/index.db"
    db = sqlite3.connect(index, isolation_level=None)
    db.execute(
        "INSERT INTO ledger(seq, source, rec_id, leaf, segment, off, len) VALUES(7, 's', 'x', x'00', 'segment-000000000006.jsonl', 0, 1)"
    )
    db.close()
    rehash(backup, "run/ledger/index.db")
    c.refuses(
        "an index row beyond the records",
        lambda: mod.verify(backup, ROOT),
        "the backup's index has a row for record 7, beyond its 7 records",
    )
    _, backup = _fresh(tmp, "garbage-index", mod)
    (backup / "run/ledger/index.db").write_bytes(b"not a database, " * 64)
    rehash(backup, "run/ledger/index.db")
    c.refuses("an index that is not a database", lambda: mod.verify(backup, ROOT), "the backup's index cannot be read: ", prefix=True)

    def with_line(name: str, note: bytes, size: int) -> Path:
        _, b = _fresh(tmp, name, mod)
        path = b / "run/ledger/checkpoints.jsonl"
        path.write_bytes(path.read_bytes() + canonical.dumps({"note": base64.b64encode(note).decode(), "size": size}) + b"\n")
        rehash(b, "run/ledger/checkpoints.jsonl")
        return b

    b = with_line("foreign-cp", sign_checkpoint(Checkpoint(R.LOG_ORIGIN, 7, folder.root7), [OTHER_SIGNER]), 7)
    c.refuses(
        "a checkpoint line signed by another key",
        lambda: mod.verify(b, ROOT),
        "line 2 of checkpoints.jsonl in the backup is not a checkpoint signed under the trusted root: ",
        prefix=True,
    )
    b = with_line("smaller-cp", sign_checkpoint(Checkpoint(R.LOG_ORIGIN, 5, b"\x07" * 32), [SIGNER]), 5)
    c.refuses(
        "a smaller checkpoint after a larger",
        lambda: mod.verify(b, ROOT),
        "checkpoint sizes decrease at line 2 of checkpoints.jsonl in the backup",
    )
    b = with_line("beyond-cp", sign_checkpoint(Checkpoint(R.LOG_ORIGIN, 9, b"\x07" * 32), [SIGNER]), 9)
    c.refuses(
        "a checkpoint beyond the records", lambda: mod.verify(b, ROOT), "the checkpoint of size 9 in the backup exceeds its 7 records"
    )
    b = with_line("wrong-cp", sign_checkpoint(Checkpoint(R.LOG_ORIGIN, 7, b"\x07" * 32), [SIGNER]), 7)
    c.refuses(
        "a checkpoint of another root", lambda: mod.verify(b, ROOT), "the checkpoint of size 7 in the backup does not match its records"
    )
    b = with_line("again-cp", folder.cp7, 7)
    c.refuses(
        "a checkpoint the manifest does not count", lambda: mod.verify(b, ROOT), "the backup holds 2 checkpoints and its manifest says 1"
    )
    _, b = _fresh(tmp, "attested", mod)
    path = b / "run/ledger/attested.jsonl"
    entry = canonical.parse_record(path.read_bytes().rstrip(b"\n"))
    path.write_bytes(canonical.dumps({**entry, "root": "07" * 32}) + b"\n")
    rehash(b, "run/ledger/attested.jsonl")
    c.refuses(
        "an attestation not bound to a signed checkpoint",
        lambda: mod.verify(b, ROOT),
        "line 1 of attested.jsonl in the backup is not bound to a checkpoint signed under the trusted root",
    )
    _, b = _fresh(tmp, "state", mod)
    (b / "run/ledger/STATE").write_bytes(json.dumps({"state": "INTEGRITY_FAILURE", "detail": "x"}).encode())
    rewrite_manifest(
        b,
        lambda m: m["files"].append(
            {
                "path": "run/ledger/STATE",
                "sha256": sha((b / "run/ledger/STATE").read_bytes()),
                "size": len((b / "run/ledger/STATE").read_bytes()),
            }
        ),
    )
    rewrite_manifest(b, lambda m: m["files"].sort(key=lambda e: e["path"]))
    c.refuses("a backup whose ledger state is a failure", lambda: mod.verify(b, ROOT), "the backup's ledger state is INTEGRITY_FAILURE")

    def stray_character(name: str, rel: str) -> Path:
        """A copy of the backup whose one line of ``rel`` has a note with a character outside Base64 (a decoder that skips such
        characters would read the note as it was; the ledger's own reading refuses it)."""
        _, b = _fresh(tmp, name, mod)
        path = b / rel
        line = canonical.parse_record(path.read_bytes().rstrip(b"\n"))
        note = str(line["note"])
        path.write_bytes(canonical.dumps({**line, "note": note[:8] + "!" + note[8:]}) + b"\n")
        rehash(b, rel)
        return b

    b = stray_character("cp-not-base64", "run/ledger/checkpoints.jsonl")
    c.refuses(
        "a checkpoint whose note has a character outside Base64",
        lambda: mod.verify(b, ROOT),
        "line 1 of checkpoints.jsonl in the backup is not a checkpoint signed under the trusted root: ",
        prefix=True,
    )
    b = stray_character("attested-not-base64", "run/ledger/attested.jsonl")
    c.refuses(
        "an attestation whose note has a character outside Base64",
        lambda: mod.verify(b, ROOT),
        "line 1 of attested.jsonl in the backup is not bound to a checkpoint signed under the trusted root",
    )
    _, b = _fresh(tmp, "last-checkpoint", mod)
    rewrite_manifest(b, lambda m: m["ledger"].__setitem__("last_checkpoint", 6))
    c.refuses(
        "a manifest that misstates the last checkpoint",
        lambda: mod.verify(b, ROOT),
        "the backup's last checkpoint is of size 7 and its manifest says 6",
    )
    _, b = _fresh(tmp, "state-outside", mod)
    raw = json.loads((b / "settings.json").read_text(encoding="utf-8"))
    (b / "settings.json").write_text(json.dumps({**raw, "directory": "../run"}, indent=2) + "\n", encoding="utf-8")
    rehash(b, "settings.json")
    c.refuses(
        "a backup whose own settings put the state outside it",
        lambda: mod.verify(b, ROOT),
        "the state directory is outside the folder of the settings: a backup keeps the files of that folder",
    )
    _, b = _fresh(tmp, "garbage-index-kept", mod)
    (b / "run/ledger/index.db").write_bytes(b"not a database, " * 64)
    rehash(b, "run/ledger/index.db")
    before = open_descriptors()
    try:
        mod.verify(b, ROOT)
        c.bad.append("an index that is not a database: was accepted")
    except mod.BackupError:
        c.eq("while its refusal is handled, no file of the backup is left open", sorted(open_descriptors() - before), [])
    return c.bad


def check_captures_and_commitments(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    blob = sha(CAPTURE_BODY)
    blob_path = f"run/captures/blobs/{blob[:2]}/{blob}"
    _, b = _fresh(tmp, "no-blob", mod)
    (b / blob_path).unlink()
    drop_entry(b, blob_path)
    c.refuses(
        "a capture whose body is missing",
        lambda: mod.verify(b, ROOT),
        f"the backup's capture log names the body {blob}, which the backup lacks",
    )
    _, b = _fresh(tmp, "bad-blob", mod)
    (b / blob_path).write_bytes(b'{"serverTime":1001}')
    rehash(b, blob_path)
    c.refuses("a body that is not its hash", lambda: mod.verify(b, ROOT), f"the body {blob} in the backup does not have that SHA-256")
    _, b = _fresh(tmp, "capture-count", mod)
    rewrite_manifest(b, lambda m: m.__setitem__("captures", 2))
    c.refuses("a capture count that disagrees", lambda: mod.verify(b, ROOT), "the backup holds 1 captures and its manifest says 2")
    folder, b = _fresh(tmp, "bad-commitment", mod)
    (b / "run/commitments/c1/note").write_bytes(sign_checkpoint(Checkpoint(R.LOG_ORIGIN, 7, folder.root7), [OTHER_SIGNER]))
    rehash(b, "run/commitments/c1/note")
    c.refuses(
        "a commitment of another key", lambda: mod.verify(b, ROOT), "the commitment c1 in the backup is not a checkpoint of its records"
    )
    _, b = _fresh(tmp, "other-root-commitment", mod)
    (b / "run/commitments/c1/note").write_bytes(sign_checkpoint(Checkpoint(R.LOG_ORIGIN, 7, b"\x07" * 32), [SIGNER]))
    rehash(b, "run/commitments/c1/note")
    c.refuses(
        "a commitment signed by the log key, of the right size and another root",
        lambda: mod.verify(b, ROOT),
        "the commitment c1 in the backup is not a checkpoint of its records",
    )
    _, b = _fresh(tmp, "commitment-list", mod)
    rewrite_manifest(b, lambda m: m.__setitem__("commitments", ["c1", "c9"]))
    c.refuses("a commitment the backup lacks", lambda: mod.verify(b, ROOT), "the backup lacks the commitment c9, which its manifest lists")
    two = Folder(tmp / "two" / "league")
    store = CaptureStore(two.state / "captures")
    store.append(capture(2, b'{"serverTime":2000}'))
    store.close()
    two.close()
    backup = take(mod, two, tmp / "two" / "backups").path
    log = "run/captures/captures.jsonl"

    def broken_chain(line: int, name: str) -> Path:
        copy = _copy_of(backup, tmp, name)
        lines = (copy / log).read_bytes().splitlines()
        lines[line] = canonical.dumps({**canonical.parse_record(lines[line]), "prev": "0" * 63 + "1"})
        (copy / log).write_bytes(b"".join(x + b"\n" for x in lines))
        rehash(copy, log)
        return copy

    first = broken_chain(0, "first-line")
    c.refuses(
        "a capture log whose first line does not follow the start of the chain",
        lambda: _restore(mod, first, tmp / "r-first", anchors=[two.cp7]),
        "the restored capture store does not open: ",
        prefix=True,
    )
    last = broken_chain(1, "last-line")
    c.refuses(
        "a capture log whose last line does not follow the line before it",
        lambda: _restore(mod, last, tmp / "r-last", anchors=[two.cp7]),
        "the restored capture store holds 1 captures, the backup 2",
    )
    return c.bad


# --- refusals of take and restore --------------------------------------------------------------------------------------------------------
def check_take_refusals(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    folder = Folder(tmp / "failed" / "league")
    folder.close()
    (folder.ledger_dir / "STATE").write_text(json.dumps({"state": "INTEGRITY_FAILURE", "detail": "segment x: damaged"}))
    c.refuses(
        "a ledger in failure",
        lambda: take(mod, folder, tmp / "failed" / "backups"),
        "the ledger is in INTEGRITY_FAILURE: segment x: damaged; a backup is taken only of a ledger that is OK",
    )
    c.eq("and nothing was made", (tmp / "failed" / "backups").exists(), False)
    for name, change, what in (
        ("outside-protocol", {"protocol": "../protocol.json"}, "the protocol"),
        ("outside-mapping", {"mappings": {"L0": "../mapping.json"}}, "the mapping of arm L0"),
        ("outside-root", {"trusted_root": "/etc/root.json"}, "the trusted root"),
        ("outside-state", {"directory": "../run"}, "the state directory"),
        ("outside-models", {"models": "../models.json"}, "the model settings file"),
        ("state-is-the-folder", {"directory": "."}, "the state directory"),
    ):
        outside = Folder(tmp / name / "league", **change)
        outside.close()
        c.refuses(
            f"{what} outside the folder",
            lambda outside=outside, name=name: take(mod, outside, tmp / name / "backups"),
            f"{what} is outside the folder of the settings: a backup keeps the files of that folder",
        )
    empty = tmp / "empty"
    empty.mkdir()
    (empty / "settings.json").write_text(json.dumps({**good_settings(), "directory": "run"}))
    for name in ("protocol.json", "mapping.json"):
        (empty / name).write_bytes(FIXTURE)
    (empty / "root.json").write_bytes(ROOT_BYTES)
    c.refuses(
        "no ledger yet",
        lambda: mod.take(empty / "settings.json", tmp / "empty-backups", clock=ManualClock(START)),
        f"there is no ledger in {empty / 'run'}: nothing to back up",
    )
    folder = Folder(tmp / "twice" / "league")
    into = tmp / "twice" / "backups"
    take(mod, folder, into)
    c.refuses(
        "a second backup in the same millisecond",
        lambda: take(mod, folder, into),
        f"{into / NAME_AT_START} exists: a backup is never written over another",
    )
    (into / (NAME_AT_START.replace("000Z", "001Z") + ".part")).mkdir()
    c.refuses(
        "an unfinished backup in the way",
        lambda: take(mod, folder, into, at=START + timedelta(milliseconds=1)),
        f"an unfinished backup is in the way: {into / (NAME_AT_START.replace('000Z', '001Z') + '.part')}",
    )
    folder.close()
    # two files the settings name are missing: the one named first, by arm, is the one reported, whatever order the settings list them in
    for name, change, first in (
        ("two-mappings", {"mappings": {"L1": "m1.json", "L0": "m0.json"}}, "m0.json"),
        ("two-barriers", {"barrier_mappings": {"L1": "b1.json", "L0": "b0.json"}}, "b0.json"),
    ):
        missing = Folder(tmp / name / "league", **change)
        missing.close()
        c.refuses(
            f"{name}: the first missing file by arm",
            lambda missing=missing, name=name: take(mod, missing, tmp / name / "backups"),
            f"{first} cannot be read: No such file or directory",
        )
    for name, damage, says in (
        ("unreadable-index", lambda p: os.chmod(p, 0), "the index cannot be backed up: unable to open database file"),
        ("garbage-index", lambda p: p.write_bytes(b"not a database, " * 64), "the index cannot be backed up: file is not a database"),
    ):
        if name == "unreadable-index" and os.geteuid() == 0:
            continue  # the super-user reads a file of mode 000, so the index would open
        broken = Folder(tmp / name / "league")
        broken.close()
        damage(broken.ledger_dir / "index.db")
        before = open_descriptors()
        try:
            take(mod, broken, tmp / name / "backups")
            c.bad.append(f"{name}: was accepted")
        except mod.BackupError as exc:
            c.eq(f"{name}: refused", str(exc), says)
            c.eq(
                f"{name}: while its refusal is handled, no file of the index or its copy is left open",
                sorted(open_descriptors() - before),
                [],
            )
        except Exception as exc:
            c.bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
        finally:
            os.chmod(broken.ledger_dir / "index.db", 0o600)
    stray = Folder(tmp / "stray" / "league")
    stray.close()
    part = tmp / "stray" / "backups" / (NAME_AT_START + ".part")

    def write_into_the_backup(point: str) -> None:
        if point == "dispatch":
            (part / "stray.txt").write_bytes(b"written by someone else while the backup was made")

    c.refuses(
        "a file that appears in the backup while it is made: the backup checks what it wrote before it takes its name",
        lambda: take(mod, stray, tmp / "stray" / "backups", hook=write_into_the_backup),
        "the backup just taken does not verify: the backup holds stray.txt, which its manifest does not list",
    )
    c.eq("and it never takes its name", sorted(p.name for p in (tmp / "stray" / "backups").iterdir()), [NAME_AT_START + ".part"])
    return c.bad


def check_restore_refusals(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    folder, backup = _fresh(tmp, "r", mod)
    seed = (7).to_bytes(32, "big")
    exists = tmp / "exists"
    exists.mkdir()

    def restore(target: Path, **kw: Any) -> Any:
        args: dict[str, Any] = {"root": ROOT, "seed": seed, "anchors": [folder.cp7], "unanchored": False, "clock": ManualClock(START)}
        args.update(kw)
        return mod.restore(backup, target, **args)

    c.refuses(
        "a target that exists", lambda: restore(exists), f"the restore target {exists} exists: a restore writes only into a new folder"
    )
    c.refuses(
        "no anchor and not said",
        lambda: restore(tmp / "t1", anchors=[]),
        "nothing outside the backup to check it against: give an anchor, a signed checkpoint kept away from this host, or say unanchored",
    )
    c.eq("nothing was made", (tmp / "t1").exists(), False)
    done = restore(tmp / "t2", anchors=[], unanchored=True)
    c.eq("unanchored when said", (done.records, done.anchors), (7, 0))
    c.refuses(
        "another log key",
        lambda: restore(tmp / "t3", seed=(8).to_bytes(32, "big")),
        "the log key given is not allowed by the trusted root for a log of 7 records",
    )
    part = tmp / "t4.part"
    part.mkdir()
    c.refuses("an unfinished restore in the way", lambda: restore(tmp / "t4"), f"an unfinished restore is in the way: {part}")
    (tmp / "t6").symlink_to(tmp / "nowhere")
    c.refuses(
        "a target that is a dangling link",
        lambda: restore(tmp / "t6"),
        f"the restore target {tmp / 't6'} exists: a restore writes only into a new folder",
    )
    (tmp / "t7.part").symlink_to(tmp / "nowhere")
    c.refuses(
        "an unfinished restore that is a dangling link",
        lambda: restore(tmp / "t7"),
        f"an unfinished restore is in the way: {tmp / 't7.part'}",
    )
    seg6 = backup / "run/ledger/segments/segment-000000000006.jsonl"
    seg6.write_bytes(seg6.read_bytes()[:-5])
    c.refuses(
        "a damaged backup",
        lambda: restore(tmp / "t5"),
        "run/ledger/segments/segment-000000000006.jsonl is not the file the manifest lists: ",
        prefix=True,
    )
    c.eq("nothing was made for it", ((tmp / "t5").exists(), (tmp / "t5.part").exists()), (False, False))
    return c.bad


def check_drill_and_prune(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    folder = Folder(tmp / "league")
    into = tmp / "backups"
    names = []
    for second in range(3):
        names.append(take(mod, folder, into, at=START + timedelta(seconds=second)).path.name)
    folder.close()
    c.eq(
        "three backups, named by their instant",
        names,
        ["backup-20261029T120000000Z", "backup-20261029T120001000Z", "backup-20261029T120002000Z"],
    )
    scratch = tmp / "scratch"
    scratch.mkdir()
    done = mod.drill(
        into / names[-1], scratch, root=ROOT, seed=(7).to_bytes(32, "big"), anchors=[folder.cp7], unanchored=False, clock=ManualClock(START)
    )
    c.eq("the drill restores", (done["records"], done["anchors"], done["indexed_missing_rows"]), (7, 1, 0))
    c.eq("and says how long it took", isinstance(done["seconds"], float) and 0 <= done["seconds"] < 60, True)
    c.eq("and leaves nothing behind", list(scratch.iterdir()), [])
    (into / "not-a-backup").mkdir()
    (into / "not-a-backup" / "BACKUP.json").write_text('{"format":"something else"}\n')
    (into / "backup-x.part").mkdir()
    shutil.copytree(into / names[0], into / "backup-y.part")  # an unfinished backup whose manifest was written
    (into / "a-file").write_bytes(b"x")
    (into / "link-to-oldest").symlink_to(into / names[0])
    removed = mod.prune(into, 2)
    c.eq("prune removes the oldest beyond the number kept", removed, [names[0]])
    c.eq(
        "and nothing that is not a backup",
        sorted(p.name for p in into.iterdir()),
        sorted([names[1], names[2], "a-file", "backup-x.part", "backup-y.part", "link-to-oldest", "not-a-backup"]),
    )
    c.eq("again, nothing more", mod.prune(into, 2), [])
    c.eq("the listing, oldest first", mod.listing(into), [("2026-10-29T12:00:01.000Z", names[1]), ("2026-10-29T12:00:02.000Z", names[2])])
    c.eq("keeping more than there are removes nothing", (mod.prune(into, 5), mod.prune(into, 3)), ([], []))
    c.eq("and leaves both", sorted(p.name for p in into.iterdir() if p.name.startswith("backup-2")), [names[1], names[2]])
    c.eq("keeping one removes the older", mod.prune(into, 1), [names[1]])
    c.refuses("keeping none", lambda: mod.prune(into, 0), "prune keeps at least one backup")
    c.refuses("keeping a truth value", lambda: mod.prune(into, True), "prune keeps at least one backup")
    return c.bad


def check_command(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    folder = Folder(tmp / "league")
    folder.close()
    root_file = folder.base / "root.json"
    anchor = tmp / "anchor.note"
    anchor.write_bytes(folder.cp7)
    key = tmp / "log.key"
    descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(base64.b64encode((7).to_bytes(32, "big")) + b"\n")

    def run(*argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mod.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    code, out, err = run("take", str(folder.settings), str(tmp / "backups"))
    c.eq("take: exit and no error", (code, err), (0, ""))
    shown = json.loads(out) if code == 0 else {}
    backup = Path(shown.get("path", "")) if code == 0 else tmp
    c.eq("take: one line of JSON", (out.count("\n"), shown.get("records"), shown.get("checkpoints")), (1, 7, 1))
    code, out, err = run("verify", str(backup), "--trusted-root", str(root_file), "--anchor", str(anchor))
    c.eq("verify: exit", (code, err), (0, ""))
    said = {
        "path": str(backup),
        "records": 7,
        "root": folder.root7.hex(),
        "checkpoints": 1,
        "last_checkpoint": 7,
        "captures": 1,
        "commitments": ["c1"],
        "anchors": 1,
    }
    c.eq("verify: what it says, as one sorted line", out, json.dumps(said, sort_keys=True) + "\n")
    proof = tmp / "anchor.tlog-proof"
    proof.write_bytes(format_proof(TlogProof(0, (), folder.cp7, None)))
    code, out, err = run("verify", str(backup), "--trusted-root", str(root_file), "--anchor", str(proof))
    c.eq("an anchor given as a bundle's tlog-proof", (code, json.loads(out).get("anchors") if code == 0 else err), (0, 1))
    bad_proof = tmp / "bad.tlog-proof"
    bad_proof.write_bytes(b"c2sp.org/tlog-proof@v1\nindex x\n\n")
    code, _, err = run("verify", str(backup), "--trusted-root", str(root_file), "--anchor", str(bad_proof))
    c.eq(
        "a tlog-proof that cannot be read: exit 2",
        (code, err.startswith(f"hoshitori-backup: the anchor {bad_proof} is not a tlog-proof: ")),
        (2, True),
    )
    code, _, err = run("verify", str(backup), "--trusted-root", str(root_file), "--anchor", str(tmp / "no.note"))
    c.eq(
        "an anchor that cannot be read: exit 2",
        (code, err),
        (2, f"hoshitori-backup: the anchor {tmp / 'no.note'} cannot be read: No such file or directory\n"),
    )
    scratch = tmp / "scratch"
    scratch.mkdir()
    code, out, err = run(
        "drill", str(backup), "--trusted-root", str(root_file), "--log-key", str(key), "--anchor", str(anchor), "--scratch", str(scratch)
    )
    drilled = json.loads(out) if code == 0 else {}
    c.eq("drill: exit", (code, err), (0, ""))
    c.eq(
        "drill: what it says",
        sorted(drilled),
        sorted(
            [
                "records",
                "root",
                "checkpoints",
                "anchors",
                "indexed_missing_rows",
                "replayed_intents",
                "pending",
                "captures",
                "commitments",
                "seconds",
            ]
        ),
    )
    c.eq("drill: nothing left in the scratch folder", list(scratch.iterdir()), [])
    base = ("--trusted-root", str(root_file), "--log-key", str(key), "--anchor", str(anchor), "--scratch", str(scratch))
    code, out, err = run("drill", "--newest-in", str(tmp / "backups"), *base)
    c.eq("drill of the newest backup in a folder", (code, err, json.loads(out).get("records") if code == 0 else None), (0, "", 7))
    (tmp / "no-backups").mkdir()
    code, out, err = run("drill", "--newest-in", str(tmp / "no-backups"), *base)
    c.eq("a folder with no backup: refused", (code, err), (1, f"hoshitori-backup: there is no backup in {tmp / 'no-backups'}\n"))
    code, out, err = run("drill", "--newest-in", str(tmp / "absent"), *base)
    c.eq("a folder that is not there: refused", (code, err), (1, f"hoshitori-backup: there is no backup in {tmp / 'absent'}\n"))
    code, _, _ = run("drill", str(backup), "--newest-in", str(tmp / "backups"), *base)
    c.eq("a backup and a folder: usage", code, 2)
    code, _, _ = run("drill", *base)
    c.eq("neither: usage", code, 2)
    (tmp / "a-file").write_bytes(b"x")
    code, _, err = run("take", str(folder.settings), str(tmp / "a-file"))
    c.eq("an unexpected failure: exit 1 and its type", (code, err.startswith("hoshitori-backup: FileExistsError: ")), (1, True))
    code, out, _ = run("--help")
    c.eq("help: exit 0", (code, out.startswith("usage: hoshitori-backup")), (0, True))
    code, out, err = run(
        "restore", str(backup), str(tmp / "restored"), "--trusted-root", str(root_file), "--log-key", str(key), "--anchor", str(anchor)
    )
    c.eq("restore: exit", (code, err), (0, ""))
    c.eq("restore: records", json.loads(out).get("records") if code == 0 else None, 7)
    code, out, err = run(
        "restore", str(backup), str(tmp / "restored"), "--trusted-root", str(root_file), "--log-key", str(key), "--anchor", str(anchor)
    )
    c.eq(
        "a refusal: exit 1, the sentence on standard error",
        (code, out, err),
        (1, "", f"hoshitori-backup: the restore target {tmp / 'restored'} exists: a restore writes only into a new folder\n"),
    )
    code, out, err = run("restore", str(backup), str(tmp / "r2"), "--trusted-root", str(root_file), "--log-key", str(key))
    c.eq("restore with no anchor: refused", code, 1)
    code, out, err = run("restore", str(backup), str(tmp / "r3"), "--trusted-root", str(root_file), "--log-key", str(key), "--unanchored")
    c.eq("restore unanchored: says so", (code, json.loads(out).get("anchors") if code == 0 else None), (0, 0))
    code, _, err = run(
        "restore",
        str(backup),
        str(tmp / "r4"),
        "--trusted-root",
        str(root_file),
        "--log-key",
        str(key),
        "--unanchored",
        "--anchor",
        str(anchor),
    )
    c.eq("an anchor and unanchored together: usage", code, 2)
    os.chmod(key, 0o644)
    code, _, err = run("restore", str(backup), str(tmp / "r5"), "--trusted-root", str(root_file), "--log-key", str(key), "--unanchored")
    c.eq(
        "a key others can read: refused before anything",
        (code, err),
        (2, "hoshitori-backup: the log key file can be read by others than its owner\n"),
    )
    c.eq("and nothing was made", (tmp / "r5").exists(), False)
    code, out, err = run("drill", str(backup), "--trusted-root", str(root_file), "--log-key", str(tmp / "missing.key"), "--unanchored")
    c.eq("a key that cannot be read: exit 2", code, 2)
    code, out, _ = run("prune", str(tmp / "backups"), "--keep", "1")
    c.eq("prune: nothing to remove", (code, json.loads(out) if code == 0 else None), (0, {"removed": []}))
    code, _, _ = run()
    c.eq("no command: usage", code, 2)
    code, _, err = run("take", str(tmp / "missing.json"), str(tmp / "b"))
    c.eq("settings that cannot be read: exit 2", (code, err.startswith("hoshitori-backup: the settings cannot be read")), (2, True))
    code, _, err = run("verify", str(tmp / "nothing"), "--trusted-root", str(root_file))
    c.eq("verify of no backup: exit 1", (code, err), (1, f"hoshitori-backup: {tmp / 'nothing'} holds no backup: BACKUP.json is missing\n"))
    code, _, err = run("verify", str(backup), "--trusted-root", str(tmp / "missing-root.json"))
    c.eq(
        "a trusted root that cannot be read: exit 2", (code, err.startswith("hoshitori-backup: the trusted root cannot be read")), (2, True)
    )
    return c.bad


def _copy_of(backup: Path, tmp: Path, name: str) -> Path:
    """A copy of a backup to damage in one way."""
    target = tmp / "copies" / name
    shutil.copytree(backup, target)
    return target


def _restore(mod: types.ModuleType, backup: Path, target: Path, *, seed: int = 7, anchors: list[bytes] | None = None) -> Any:
    return mod.restore(
        backup,
        target,
        root=ROOT,
        seed=seed.to_bytes(32, "big"),
        anchors=anchors or [],
        unanchored=anchors is None,
        clock=ManualClock(START),
    )


def check_a_league_that_never_ran(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    base = tmp / "league"
    base.mkdir()
    (base / "settings.json").write_text(json.dumps({**good_settings(), "directory": "run"}))
    for name in ("protocol.json", "mapping.json"):
        (base / name).write_bytes(FIXTURE)
    (base / "root.json").write_bytes(ROOT_BYTES)
    open_ledger(base / "run" / "ledger").close()
    taken = mod.take(base / "settings.json", tmp / "backups", clock=ManualClock(START))
    m = taken.manifest
    c.eq(
        "what it holds",
        (m.records, m.ledger_root, m.checkpoints, m.last_checkpoint, m.captures, m.commitments),
        (0, sha(b""), 0, 0, 0, ()),
    )
    c.eq(
        "its files",
        [f.path for f in m.files],
        [
            "mapping.json",
            "protocol.json",
            "root.json",
            "run/ledger/index.db",
            "run/ledger/segments/segment-000000000000.jsonl",
            "settings.json",
        ],
    )
    c.eq("an empty first segment", (taken.path / "run/ledger/segments/segment-000000000000.jsonl").read_bytes(), b"")
    done = _restore(mod, taken.path, tmp / "restored")
    c.eq(
        "restored, unanchored",
        (done.records, done.root, done.checkpoints, done.anchors, done.indexed_missing_rows, done.replayed_intents, done.pending),
        (0, sha(b""), 0, 0, 0, 0, 0),
    )
    c.eq("with no capture and no commitment", (done.captures, done.commitments), (0, 0))
    return c.bad


def check_writes_in_progress(mod: types.ModuleType, tmp: Path) -> list[str]:
    """Lines being written when the backup reads a file are left out; a rotation cut short and temporary files are not copied."""
    c = Case(mod)
    folder = Folder(tmp / "torn" / "league")
    folder.close()
    whole: dict[str, bytes] = {}
    for rel in (
        "run/ledger/segments/segment-000000000006.jsonl",
        "run/ledger/checkpoints.jsonl",
        "run/ledger/attested.jsonl",
        "run/ledger/journal.jsonl",
        "run/captures/captures.jsonl",
    ):
        whole[rel] = (folder.base / rel).read_bytes()
        (folder.base / rel).write_bytes(whole[rel] + b'{"half":"a line being writ')
    (folder.ledger_dir / "segments" / "segment-000000000009.jsonl.tmp").write_bytes(b"x")
    taken = take(mod, folder, tmp / "torn" / "backups")
    for rel, data in whole.items():
        c.eq(f"{rel}: the complete lines only", (taken.path / rel).read_bytes(), data)
    c.eq("the temporary file is left out", [f.path for f in taken.manifest.files], folder.files())
    done = _restore(mod, taken.path, tmp / "torn" / "restored", anchors=[folder.cp7])
    c.eq("restored whole", (done.records, done.root, done.pending), (7, folder.root7.hex(), 1))
    for label, content in (("empty", b""), ("half-manifest", b'{"manifest":{"rec')):
        rotated = Folder(tmp / label / "league")
        rotated.led.append_pending()
        rotated.led.submit(rec(8))
        rotated.led.append_pending()  # segment 6 now holds 6, 7 and 8: the next append rotates
        nine = rotated.led.root()
        rotated.close()
        (rotated.ledger_dir / "segments" / "segment-000000000009.jsonl").write_bytes(content)
        got = take(mod, rotated, tmp / label / "backups")
        c.eq(f"a rotation cut short ({label}) is not copied", [f.path for f in got.manifest.files], rotated.files())
        c.eq(f"({label}) and the records before it are", (got.manifest.records, got.manifest.ledger_root), (9, nine.hex()))
    return c.bad


def check_source_problems(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)

    def folder(name: str, **kw: Any) -> Folder:
        made = Folder(tmp / name / "league", **kw)
        made.close()
        return made

    def refused(label: str, made: Folder, says: str) -> None:
        c.refuses(label, lambda: take(mod, made, made.base.parent / "backups"), says)

    f = folder("garbage")
    log = f.state / "captures" / "captures.jsonl"
    log.write_bytes(log.read_bytes() + b'{"not":"a capture"}\n')
    refused("a capture line without a body hash", f, "line 2 of the capture log cannot be read")
    f = folder("not-hex")
    log = f.state / "captures" / "captures.jsonl"
    log.write_bytes(log.read_bytes() + canonical.dumps({"body_sha256": "xyz"}) + b"\n")
    refused("a capture line whose body hash is not one", f, "line 2 of the capture log cannot be read")
    f = folder("no-blob")
    blob = sha(CAPTURE_BODY)
    (f.state / "captures" / "blobs" / blob[:2] / blob).unlink()
    refused("a body missing from the store", f, f"the body {blob} of a stored capture is missing")
    f = folder("bad-commitment")
    broken = f.state / "commitments" / "c3"
    broken.mkdir()
    (broken / "note").write_bytes(f.cp7)
    (broken / "x.txt").write_bytes(b"x")
    refused("a commitment that cannot be read", f, "the commitment c3 cannot be read: x.txt is not a file of a commitment")
    f = folder("stray")
    (f.ledger_dir / "segments" / "notes.txt").write_bytes(b"x")
    refused("a stray file among the segments", f, "unexpected file in the segment directory: notes.txt")
    f = folder("fenced")
    (f.ledger_dir / "STATE").write_text(json.dumps({"state": "FENCED"}))
    refused("a fenced ledger whose state says no more", f, "the ledger is in FENCED: ; a backup is taken only of a ledger that is OK")
    f = folder("gone")
    (f.base / "protocol.json").unlink()
    refused("a named file that cannot be read", f, "protocol.json cannot be read: No such file or directory")
    f = folder("bad-root", root_bytes=b"{}")
    c.refuses(
        "a trusted root that cannot be read",
        lambda: take(mod, f, f.base.parent / "backups"),
        "the trusted root cannot be read: ",
        prefix=True,
    )
    during = Folder(tmp / "during" / "league")

    def fail(name: str) -> None:
        if name == "index":
            (during.ledger_dir / "STATE").write_text(json.dumps({"state": "INTEGRITY_FAILURE", "detail": "found while backing up"}))

    c.refuses(
        "a failure that appears during the backup",
        lambda: take(mod, during, tmp / "during" / "backups", hook=fail),
        "the backup just taken does not verify: the backup's ledger state is INTEGRITY_FAILURE",
    )
    c.eq(
        "which is left unfinished, for inspection",
        sorted(p.name for p in (tmp / "during" / "backups").iterdir()),
        [NAME_AT_START + ".part"],
    )
    during.close()
    return c.bad


def check_manifest_shape(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    _, backup = _fresh(tmp, "base", mod)

    def manifest_change(change: Callable[[dict[str, Any]], object]) -> Callable[[Path], None]:
        return lambda b: rewrite_manifest(b, lambda m: change(m) and None)

    def no_newline(b: Path) -> None:
        (b / "BACKUP.json").write_bytes((b / "BACKUP.json").read_bytes().rstrip(b"\n"))

    shape = "BACKUP.json is not a backup manifest: "
    cases: list[tuple[str, Callable[[Path], None], str]] = [
        ("no newline", no_newline, shape + "it does not end in a newline"),
        (
            "ledger members",
            manifest_change(lambda m: m["ledger"].pop("root")),
            shape + "its ledger has the members ['checkpoints', 'last_checkpoint', 'records', 'root']",
        ),
        (
            "commitments not ids",
            manifest_change(lambda m: m.__setitem__("commitments", ["C1"])),
            shape + "the commitments are a list of commitment ids",
        ),
        (
            "commitments not a list",
            manifest_change(lambda m: m.__setitem__("commitments", "c1")),
            shape + "the commitments are a list of commitment ids",
        ),
        ("files not a list", manifest_change(lambda m: m.__setitem__("files", {})), shape + "the files are a list"),
        (
            "a file without its hash",
            manifest_change(lambda m: m["files"][0].pop("sha256")),
            shape + "a file is listed with its path, size and SHA-256",
        ),
        ("a negative size", manifest_change(lambda m: m["files"][0].__setitem__("size", -1)), shape + "a count is a whole number"),
        (
            "a size that is a truth value",
            manifest_change(lambda m: m["files"][0].__setitem__("size", True)),
            shape + "a count is a whole number",
        ),
        (
            "a hash in capitals",
            manifest_change(lambda m: m["files"][0].__setitem__("sha256", m["files"][0]["sha256"].upper())),
            shape + "a hash is 64 lower-case hex digits",
        ),
        ("records as text", manifest_change(lambda m: m["ledger"].__setitem__("records", "7")), shape + "a count is a whole number"),
        (
            "a root hash too short",
            manifest_change(lambda m: m.__setitem__("root_sha256", "ab")),
            shape + "a hash is 64 lower-case hex digits",
        ),
        ("no time", manifest_change(lambda m: m.__setitem__("taken_at", "")), shape + "the time, the settings and the origin are named"),
        (
            "an origin that is not text",
            manifest_change(lambda m: m.__setitem__("origin", 5)),
            shape + "the time, the settings and the origin are named",
        ),
        (
            "settings outside",
            manifest_change(lambda m: m.__setitem__("settings", "../settings.json")),
            shape + "the time, the settings and the origin are named",
        ),
    ]
    for text in ("/abs", "a//b", "./a", "a\\b", "", "a/../b", "a/", "x\x00y", 5):
        cases.append(
            (
                f"the path {text!r}",
                manifest_change(lambda m, text=text: m["files"].append({"path": text, "sha256": sha(b""), "size": 0})),
                f"the manifest names a path outside the backup: {text!r}",
            )
        )
    for number, (label, damage, says) in enumerate(cases):
        copy = _copy_of(backup, tmp, str(number))
        damage(copy)
        c.refuses(label, lambda copy=copy: mod.verify(copy, ROOT), says)
    return c.bad


def check_special_files(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    _, backup = _fresh(tmp, "base", mod)
    alpha = "run/commitments/c1/alpha.tsr"
    b = _copy_of(backup, tmp, "fifo-listed")
    (b / "protocol.json").unlink()
    os.mkfifo(b / "protocol.json")
    before = open_descriptors()
    in_time, said = verify_in_time(mod, b, b / "protocol.json")
    c.eq("a listed file that is a pipe: the verify does not wait for a writer", in_time, True)
    c.eq(
        "and refuses it",
        str(said) if isinstance(said, mod.BackupError) else said,
        "protocol.json in the backup is not a regular file",
    )
    c.eq("and leaves no file open", sorted(open_descriptors() - before), [])
    b2 = _copy_of(backup, tmp, "fifo-unlisted")
    os.mkfifo(b2 / "zz.fifo")
    c.refuses("an unlisted pipe", lambda: mod.verify(b2, ROOT), "zz.fifo in the backup is not a regular file")
    b3 = _copy_of(backup, tmp, "parent-file")
    shutil.rmtree(b3 / "run/commitments/c1")
    (b3 / "run/commitments/c1").write_bytes(b"x")
    c.refuses("a file where a folder should be", lambda: mod.verify(b3, ROOT), f"{alpha} in the backup is not a regular file")
    b4 = _copy_of(backup, tmp, "parent-link")
    shutil.move(b4 / "run/commitments", tmp / "elsewhere")
    (b4 / "run/commitments").symlink_to(tmp / "elsewhere")
    c.refuses("a link where a folder should be", lambda: mod.verify(b4, ROOT), f"{alpha} in the backup is not a regular file")
    b5 = _copy_of(backup, tmp, "parent-missing")
    shutil.rmtree(b5 / "run/commitments")
    c.refuses("a folder missing", lambda: mod.verify(b5, ROOT), f"the backup lacks {alpha}, which its manifest lists")
    b7 = _copy_of(backup, tmp, "state-missing")
    shutil.rmtree(b7 / "run")
    blob = sha(CAPTURE_BODY)
    c.refuses(
        "the whole state folder missing: the first file listed in it is named",
        lambda: mod.verify(b7, ROOT),
        f"the backup lacks run/captures/blobs/{blob[:2]}/{blob}, which its manifest lists",
    )
    b6 = _copy_of(backup, tmp, "manifest-link")
    os.replace(b6 / "BACKUP.json", tmp / "manifest.json")
    (b6 / "BACKUP.json").symlink_to(tmp / "manifest.json")
    c.refuses("a manifest that is a link", lambda: mod.verify(b6, ROOT), "BACKUP.json in the backup is not a regular file")
    return c.bad


def check_more_ledger_files(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    folder, backup = _fresh(tmp, "base", mod)
    b = _copy_of(backup, tmp, "damaged-index")
    index = b / "run/ledger/index.db"
    db = sqlite3.connect(index, isolation_level=None)
    (other,) = db.execute("SELECT rootpage FROM sqlite_master WHERE name = 'sqlite_autoindex_outbox_2'").fetchone()
    db.execute("PRAGMA writable_schema=ON")  # point the ledger's unique index at the outbox's: two valid b-trees, the wrong entries
    db.execute("UPDATE sqlite_master SET rootpage = ? WHERE name = 'sqlite_autoindex_ledger_1'", (other,))
    db.close()
    rehash(b, "run/ledger/index.db")
    c.refuses(
        "an index whose entries are not its table's",
        lambda: mod.verify(b, ROOT),
        "the backup's index fails its integrity check: ",
        prefix=True,
    )
    b = _copy_of(backup, tmp, "no-index")
    (b / "run/ledger/index.db").unlink()
    drop_entry(b, "run/ledger/index.db")
    c.eq("a backup without its index verifies", mod.verify(b, ROOT).manifest.records, 7)
    done = _restore(mod, b, tmp / "restored-no-index", anchors=[folder.cp7])
    c.eq(
        "and its restore rebuilds the index and takes the queued command back from the journal",
        (done.records, done.indexed_missing_rows, done.replayed_intents, done.pending),
        (7, 7, 1, 1),
    )
    attested = "run/ledger/attested.jsonl"

    def edit_attestation(b: Path, change: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        path = b / attested
        path.write_bytes(canonical.dumps(change(canonical.parse_record(path.read_bytes().rstrip(b"\n")))) + b"\n")
        rehash(b, attested)

    b = _copy_of(backup, tmp, "legacy")
    edit_attestation(b, lambda e: {k: v for k, v in e.items() if k != "note"})
    c.eq("an attestation written before entries carried a note, bound to a signed checkpoint", mod.verify(b, ROOT).manifest.records, 7)
    b = _copy_of(backup, tmp, "legacy-unbound")
    edit_attestation(b, lambda e: {**{k: v for k, v in e.items() if k != "note"}, "root": "07" * 32})
    c.refuses(
        "an attestation with no note and no checkpoint of its own",
        lambda: mod.verify(b, ROOT),
        "line 1 of attested.jsonl in the backup is not bound to a checkpoint signed under the trusted root",
    )
    for label, size in (("as text", "7"), ("as a truth value", True)):
        for note in (True, False):
            b = _copy_of(backup, tmp, f"size-{label}-{note}".replace(" ", "-"))
            edit_attestation(b, lambda e, size=size, note=note: {**{k: v for k, v in e.items() if note or k != "note"}, "size": size})
            c.refuses(
                f"an attestation whose size is {label}, {'with' if note else 'without'} its note",
                lambda b=b: mod.verify(b, ROOT),
                "line 1 of attested.jsonl in the backup is not bound to a checkpoint signed under the trusted root",
            )
    other = Folder(tmp / "other" / "league", extra="another history")
    other.close()
    b = _copy_of(backup, tmp, "foreign-attestation")
    edit_attestation(b, lambda e: {**e, "note": base64.b64encode(other.cp7).decode(), "root": other.root7.hex()})
    c.refuses(
        "an attestation of another history signed by the same key",
        lambda: mod.verify(b, ROOT),
        "the attested checkpoint of size 7 in the backup is not one of its records' heads",
    )
    b = _copy_of(backup, tmp, "bad-state")
    (b / "run/ledger/STATE").write_bytes(b"not json")
    rewrite_manifest(b, lambda m: m["files"].append({"path": "run/ledger/STATE", "sha256": sha(b"not json"), "size": 8}))
    c.refuses("a ledger state that cannot be read", lambda: mod.verify(b, ROOT), "the backup's ledger state cannot be read")
    b = _copy_of(backup, tmp, "no-segments")
    shutil.rmtree(b / "run/ledger/segments")
    for first in (0, 3, 6):
        drop_entry(b, f"run/ledger/segments/{segment.segment_name(first)}")
    c.refuses("no segments", lambda: mod.verify(b, ROOT), "the backup holds no segments")
    for rel in ("run/ledger/checkpoints.jsonl", attested, "run/ledger/journal.jsonl", "run/captures/captures.jsonl", "run/dispatch.jsonl"):
        b = _copy_of(backup, tmp, "half-" + rel.replace("/", "-"))
        (b / rel).write_bytes((b / rel).read_bytes() + b'{"half')
        rehash(b, rel)
        c.refuses(f"{rel} ending inside a line", lambda b=b: mod.verify(b, ROOT), f"{rel} in the backup ends inside a line")
    b = _copy_of(backup, tmp, "bad-settings")
    (b / "settings.json").write_text("{}")
    rehash(b, "settings.json")
    c.refuses("settings that cannot be used", lambda: mod.verify(b, ROOT), "the backup's settings cannot be used: ", prefix=True)
    b = _copy_of(backup, tmp, "unlisted-commitment")
    rewrite_manifest(b, lambda m: m.__setitem__("commitments", []))
    c.refuses(
        "a commitment the manifest does not list",
        lambda: mod.verify(b, ROOT),
        "the backup holds the commitment c1, which its manifest does not list",
    )
    b = _copy_of(backup, tmp, "commitment-without-note")
    (b / "run/commitments/c1/note").unlink()
    drop_entry(b, "run/commitments/c1/note")
    c.refuses(
        "a commitment without its note", lambda: mod.verify(b, ROOT), "the commitment c1 in the backup is not a checkpoint of its records"
    )
    return c.bad


def check_eras(mod: types.ModuleType, tmp: Path) -> list[str]:
    """Checkpoints are checked under the key the root allows for their size; a key rotation the ledger cannot open is refused."""
    c = Case(mod)
    first, second = R.log_signer(7), R.log_signer(9)

    def root_of(through: int) -> bytes:
        eras = [
            {"vkey": first.verifier.vkey(), "from_size": 0, "through_size": through},
            {"vkey": second.verifier.vkey(), "from_size": through + 1, "through_size": None},
        ]
        return canonical.dumps(R.make_document(1, eras=eras))  # type: ignore[arg-type]

    closed = Folder(tmp / "closed" / "league", root_bytes=root_of(5))
    c.refuses(
        "a checkpoint signed past its key's era",
        lambda: take(mod, closed, tmp / "closed" / "backups"),
        "the backup just taken does not verify: line 1 of checkpoints.jsonl in the backup is not a checkpoint signed under the trusted root: ",
        prefix=True,
    )
    closed.close()
    rotated_bytes = root_of(8)
    rotated_root = parse_root(rotated_bytes)
    rotated = Folder(tmp / "rotated" / "league", root_bytes=rotated_bytes)
    rotated.led.append_pending()
    rotated.led.submit(rec(8))
    rotated.led.append_pending()
    rotated.close()
    taken = take(mod, rotated, tmp / "rotated" / "backups")
    c.eq("nine records, one checkpoint signed by the first key", (taken.manifest.records, taken.manifest.checkpoints), (9, 1))

    def restore_with(seed: int, name: str) -> Any:
        return mod.restore(
            taken.path,
            tmp / "rotated" / name,
            root=rotated_root,
            seed=seed.to_bytes(32, "big"),
            anchors=[rotated.cp7],
            unanchored=False,
            clock=ManualClock(START),
        )

    c.refuses(
        "the first key past its era",
        lambda: restore_with(7, "r1"),
        "the log key given is not allowed by the trusted root for a log of 9 records",
    )
    c.refuses(
        "the second key: the ledger opened with it refuses the first era's checkpoint",
        lambda: restore_with(9, "r2"),
        "the restored ledger opened in INTEGRITY_FAILURE: checkpoints.jsonl line 1 is not a valid signed checkpoint: ",
        prefix=True,
    )
    return c.bad


# --- what a reader of the files cannot see, and the text of the command ------------------------------------------------------------------
class Syncs:
    """The os module as the module under test sees it, except that each fsync is noted: the size of a file at the moment it is made durable
    (bytes still in a writer's buffer are not made durable by it), or that a folder was. Power loss cannot be had in a test, so the calls are
    counted where the code makes them."""

    def __init__(self) -> None:
        self.files: list[int] = []
        self.folders = 0
        self.order: list[Path] = []  # the folders, in the order they were made durable
        self._opened: dict[int, Path] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(os, name)

    def open(self, path: Any, flags: int, mode: int = 0o777) -> int:
        descriptor = os.open(path, flags, mode)
        self._opened[descriptor] = Path(path)
        return descriptor

    def fsync(self, descriptor: int) -> None:
        info = os.fstat(descriptor)
        if stat.S_ISDIR(info.st_mode):
            self.folders += 1
            self.order.append(self._opened[descriptor])
        else:
            self.files.append(info.st_size)
        os.fsync(descriptor)

    def children_after_parents(self) -> list[str]:
        """The folders made durable before a folder inside them: a parent's entry for a new folder must not be durable before the folder's
        own entries are."""
        late = []
        for position, outer in enumerate(self.order):
            for inner in self.order[position + 1 :]:
                if inner != outer and outer in inner.parents:
                    late.append(f"{outer.name} before {inner.name}")
        return late


def run_main(mod: types.ModuleType, *argv: str, stdout: io.StringIO | None = None) -> tuple[int, str, str]:
    """The command with its output caught: exit code, standard output, standard error."""
    out, err = stdout if stdout is not None else io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = mod.main(list(argv))
    return code, out.getvalue(), err.getvalue()


@contextlib.contextmanager
def wide_terminal() -> Iterator[None]:
    """A terminal wide enough that argparse wraps no line of its help (it would break a phrase at a hyphen)."""
    saved = os.environ.get("COLUMNS")
    os.environ["COLUMNS"] = "400"
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = saved


def _bare_league(base: Path) -> Path:
    """A league folder with its settings and the files they name, and no ledger yet; the settings file."""
    base.mkdir(parents=True)
    (base / "settings.json").write_text(json.dumps({**good_settings(), "directory": "run"}))
    for name in ("protocol.json", "mapping.json"):
        (base / name).write_bytes(FIXTURE)
    (base / "root.json").write_bytes(ROOT_BYTES)
    return base / "settings.json"


def check_durability(mod: types.ModuleType, tmp: Path) -> list[str]:
    """Everything a backup or a restore writes is made durable: each file once, with all its bytes already out of the writer's buffer, and
    each folder once, the folder that holds the new one as well."""
    c = Case(mod)
    folder = Folder(tmp / "league")
    folder.close()
    seen = Syncs()
    with replaced(mod, "os", seen):
        taken = take(mod, folder, tmp / "backups")
    on_disk = sorted(p.stat().st_size for p in taken.path.rglob("*") if p.is_file())
    c.eq("take: each file once, whole when it is synced", sorted(seen.files), on_disk)
    # the backup, run, captures, blobs, a blob's folder, commitments, c1, ledger, segments: nine; and the folder that holds the backups
    c.eq("take: each folder once", seen.folders, 10)
    c.eq("take: a folder after the folders inside it", seen.children_after_parents(), [])
    seen = Syncs()
    with replaced(mod, "os", seen):
        _restore(mod, taken.path, tmp / "new" / "restored", anchors=[folder.cp7])
    c.eq("restore: each file once, whole when it is synced", sorted(seen.files), sorted(f.size for f in taken.manifest.files))
    # the same nine, with the folder that holds the restored one (here ``new``)
    c.eq("restore: each folder once", seen.folders, 10)
    c.eq("restore: a folder after the folders inside it", seen.children_after_parents(), [])
    return c.bad


def check_order_of_names(mod: types.ModuleType, tmp: Path) -> list[str]:
    """What a backup reports does not depend on the order a file system lists names in: the problem reported is the first by name, and the
    commitments are named in name order."""
    c = Case(mod)
    _, backup = _fresh(tmp, "two-extra", mod)
    (backup / "z.txt").write_bytes(b"z")
    (backup / "a.txt").write_bytes(b"a")
    with replaced(mod, "os", Reversed()):
        c.refuses(
            "two files the manifest does not list",
            lambda: mod.verify(backup, ROOT),
            "the backup holds a.txt, which its manifest does not list",
        )
    broken = Folder(tmp / "broken" / "league")
    broken.close()
    for name in ("c3", "c4"):
        kept = broken.state / "commitments" / name
        kept.mkdir()
        (kept / "note").write_bytes(broken.cp7)
        (kept / "x.txt").write_bytes(b"x")
    with replaced(mod, "os", Reversed()):
        c.refuses(
            "two commitments that cannot be read",
            lambda: take(mod, broken, tmp / "broken" / "backups"),
            "the commitment c3 cannot be read: x.txt is not a file of a commitment",
        )
    many = Folder(tmp / "many" / "league")
    many.close()
    for number in range(2, 13):
        kept = many.state / "commitments" / f"c{number}"
        kept.mkdir()
        (kept / "note").write_bytes(many.cp7)
    taken = take(mod, many, tmp / "many" / "backups")
    c.eq(
        "the commitments, in name order",
        taken.manifest.commitments,
        ("c1", "c10", "c11", "c12", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9"),
    )
    into = tmp / "listing" / "backups"
    league = Folder(tmp / "listing" / "league")
    made = [take(mod, league, into, at=START + timedelta(seconds=second)).path for second in range(3)]
    league.close()
    made[2].rename(into / "a-newest")  # sorted by name it would come first; it is the newest
    c.eq(
        "the listing is by the time taken, not by name",
        mod.listing(into),
        [
            ("2026-10-29T12:00:00.000Z", made[0].name),
            ("2026-10-29T12:00:01.000Z", made[1].name),
            ("2026-10-29T12:00:02.000Z", "a-newest"),
        ],
    )
    c.eq("and prune removes the oldest by it", mod.prune(into, 1), sorted([made[0].name, made[1].name]))
    return c.bad


def check_corners(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    zoned = Folder(tmp / "zoned" / "league")
    taken = mod.take(zoned.settings, tmp / "zoned" / "backups", clock=Zoned())
    zoned.close()
    c.eq("a clock that tells the time in another zone: the folder is named by the instant in UTC", taken.path.name, NAME_AT_START)
    settings = _bare_league(tmp / "short" / "league")
    ledger_dir = tmp / "short" / "league" / "run" / "ledger"
    led = open_ledger(ledger_dir)
    for number in range(3):  # exactly one full segment: the next append would start segment 3
        led.submit(rec(number))
    led.append_pending()
    led.checkpoint()
    head = led.root()
    led.abandon()
    (ledger_dir / "segments" / "segment-000000000003.jsonl").write_bytes(b"")
    got = mod.take(settings, tmp / "short" / "backups", clock=ManualClock(START))
    c.eq(
        "a rotation cut short just after a full segment is not copied",
        ([f.path for f in got.manifest.files if "/segments/" in f.path], got.manifest.records, got.manifest.ledger_root),
        (["run/ledger/segments/segment-000000000000.jsonl"], 3, head.hex()),
    )
    unsigned = Folder(tmp / "unsigned" / "league")
    unsigned.led.append_pending()  # the eighth record is appended; the last checkpoint is still of seven
    made = take(mod, unsigned, tmp / "unsigned" / "backups")
    unsigned.close()
    done = _restore(mod, made.path, tmp / "unsigned" / "restored", anchors=[unsigned.cp7])
    c.eq(
        "a restore signs nothing: its checkpoints are the backup's",
        ((tmp / "unsigned" / "restored" / "run/ledger/checkpoints.jsonl").read_bytes(), done.records),
        ((made.path / "run/ledger/checkpoints.jsonl").read_bytes(), 8),
    )
    for name in (
        "take",
        "verify",
        "restore",
        "drill",
        "listing",
        "prune",
        "main",
    ):  # the annotations are strings: a missing name shows only here
        try:
            typing.get_type_hints(getattr(mod, name))
        except NameError as exc:
            c.eq(f"the annotations of {name} name only what the module defines", str(exc), "")
    return c.bad


def check_league_left_alone(mod: types.ModuleType, tmp: Path) -> list[str]:
    """A backup reads the league's index and changes nothing of it. A copy made while the league ran (what a crash leaves) has frames in its
    write-ahead log: a reader that could write would fold them into the index file and delete the log when it closed."""
    c = Case(mod)
    live = Folder(tmp / "live" / "league")
    crash = tmp / "crash"
    shutil.copytree(live.base, crash)
    live.close()
    ledger_dir = crash / "run" / "ledger"

    def index_files() -> dict[str, bytes | None]:
        return {n: (ledger_dir / n).read_bytes() if (ledger_dir / n).exists() else None for n in ("index.db", "index.db-wal")}

    before = index_files()
    c.eq("the copy of a running league has frames in its log", bool(before["index.db-wal"]), True)
    mod.take(crash / "settings.json", tmp / "crash-backups", clock=ManualClock(START))
    c.eq("the league's index file and log are as they were", index_files(), before)
    return c.bad


def check_drill_inside(mod: types.ModuleType, tmp: Path) -> list[str]:
    """Where a drill works, and that a folder it cannot remove does not hide what it found."""
    c = Case(mod)
    folder = Folder(tmp / "league")
    taken = take(mod, folder, tmp / "backups")
    folder.close()
    scratch = tmp / "scratch"
    scratch.mkdir()
    real = mod.restore
    targets: list[Path] = []

    def spy(backup: Path, target: Path, **kw: Any) -> Any:
        targets.append(target)
        if os.geteuid() != 0:  # a folder that cannot be changed cannot lose its entries; root is not stopped by it
            os.chmod(scratch, 0o500)
        return real(backup, target, **kw)

    try:
        with replaced(mod, "restore", spy):
            done = mod.drill(
                taken.path,
                scratch,
                root=ROOT,
                seed=(7).to_bytes(32, "big"),
                anchors=[folder.cp7],
                unanchored=False,
                clock=ManualClock(START),
            )
    finally:
        os.chmod(scratch, 0o700)
    c.eq("a folder the drill cannot remove does not hide the result", done["records"], 7)
    c.eq("it restores into a folder named league", [t.name for t in targets], ["league"])
    c.eq("inside a folder of its own", [t.parent.parent for t in targets], [scratch])
    c.eq("named for the drill", [re.fullmatch(r"hoshitori-drill-[a-z0-9_]{8}", t.parent.name) is not None for t in targets], [True])
    return c.bad


def check_command_text(mod: types.ModuleType, tmp: Path) -> list[str]:
    """The command's help is what it says to someone at a terminal; its required options are refused when left out; and its result is
    flushed at once."""
    c = Case(mod)
    folder = Folder(tmp / "league")
    folder.close()
    backup = take(mod, folder, tmp / "backups").path
    root_file = folder.base / "root.json"
    key = tmp / "log.key"
    descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(base64.b64encode((7).to_bytes(32, "big")) + b"\n")
    anchor_help = "a signed checkpoint or a tlog-proof kept off the host"
    log_key_help = "the log key file, readable by its owner only; it is never copied"
    with wide_terminal():
        helps = {name: run_main(mod, *name.split(), "--help")[1] for name in ("", "take", "verify", "restore", "drill", "prune")}
    wanted = {
        "": [
            "usage: hoshitori-backup [-h] {take,verify,restore,drill,prune} ...",
            "Back up, verify and restore a league folder.",
            "back up the league folder of a settings file while it runs",
            "check a backup with the trusted root and anchors",
            "restore a backup into a new folder",
            "restore a backup into a scratch folder, then remove it",
            "remove the oldest backups beyond a number kept",
        ],
        "take": ["the folder that holds the backups; each backup is a new folder in it"],
        "verify": [anchor_help],
        "restore": [anchor_help, log_key_help, "restore with nothing outside the backup to check it against"],
        "drill": [
            anchor_help,
            log_key_help,
            "restore with nothing outside the backup to check it against",
            "--newest-in DIR",
            "drill the newest backup in this folder",
            "where the drill restores (default: the system's temporary folder)",
        ],
        "prune": [],
    }
    for name, phrases in wanted.items():
        for phrase in phrases:
            c.eq(f"help of {name or 'the command'} says {phrase!r}", says(helps[name], phrase), True)
    root_arg = ("--trusted-root", str(root_file))
    key_arg = ("--log-key", str(key))
    for label, argv in (
        ("verify without a trusted root", ("verify", str(backup))),
        ("prune without a number to keep", ("prune", str(tmp / "backups"))),
        ("restore without a trusted root", ("restore", str(backup), str(tmp / "r1"), *key_arg, "--unanchored")),
        ("restore without a log key", ("restore", str(backup), str(tmp / "r2"), *root_arg, "--unanchored")),
        ("drill without a trusted root", ("drill", str(backup), *key_arg, "--unanchored")),
        ("drill without a log key", ("drill", str(backup), *root_arg, "--unanchored")),
    ):
        code, out, _ = run_main(mod, *argv)
        c.eq(f"{label}: usage, exit 2", (code, out), (2, ""))
    code, out, err = run_main(mod, "drill", str(backup), *root_arg, *key_arg, "--unanchored", "--scratch", str(tmp / "absent"))
    c.eq(
        "a scratch folder that is not there is the one used, and refused",
        (code, out, err.startswith("hoshitori-backup: FileNotFoundError: ")),
        (1, "", True),
    )
    bad_root = tmp / "bad-root.json"
    bad_root.write_bytes(b"{}")
    code, _, err = run_main(mod, "verify", str(backup), "--trusted-root", str(bad_root))
    c.eq(
        "a file that is not a trusted root: exit 2",
        (code, err.startswith("hoshitori-backup: the trusted root cannot be read: MALFORMED")),
        (2, True),
    )
    nothing = tmp / "no-backups"
    nothing.mkdir()
    shown = Flushes()
    code, _, _ = run_main(mod, "prune", str(nothing), "--keep", "1", stdout=shown)
    c.eq("a result is flushed as soon as it is written", (code, shown.seen), (0, ['{"removed": []}\n']))
    return c.bad


def racing(watch: Path, rival: Path) -> type[PosixPath]:
    """A kind of path for the instant between a module's look at its working folder and its claim of it: when the folder ``watch`` is made
    (the module makes it with ``parents=True, exist_ok=True`` just before the claim), another process makes the folder ``rival`` at once."""

    class Racing(PosixPath):
        def mkdir(self, mode: int = 0o777, parents: bool = False, exist_ok: bool = False) -> None:
            super().mkdir(mode, parents, exist_ok)
            if str(self) == str(watch) and not rival.exists():
                os.mkdir(rival)

    return Racing


class WithoutNonblock:
    """The os module as the module under test sees it on a platform that has no ``O_NONBLOCK``."""

    def __getattr__(self, name: str) -> Any:
        if name == "O_NONBLOCK":
            raise AttributeError(name)
        return getattr(os, name)


def check_a_rival_claims_the_folder(mod: types.ModuleType, tmp: Path) -> list[str]:
    """A backup (or a restore) is written into a folder only this call made: a folder another process made in the instant between the
    module's look and its claim is refused, and what that process put there is left as it was. (Two backups taken in one millisecond are
    named alike; the claim by ``mkdir`` is what keeps them apart.)"""
    c = Case(mod)

    def claimed(label: str, call: Callable[[], object], rival: Path) -> None:
        try:
            call()
        except FileExistsError:
            c.eq(f"{label}: the other process's folder is left as it was", sorted(rival.iterdir()), [])
        except Exception as exc:
            c.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
        else:
            c.bad.append(f"{label}: was accepted")

    folder = Folder(tmp / "league")
    folder.close()
    backups = tmp / "backups"
    rival = backups / (NAME_AT_START + ".part")
    claimed("a backup", lambda: take(mod, folder, racing(backups, rival)(backups)), rival)
    good = take(mod, folder, tmp / "good").path
    target = tmp / "new" / "restored"
    rival = target.with_name("restored.part")
    claimed(
        "a restore",
        lambda: _restore(mod, good, racing(target.parent, rival)(target), anchors=[folder.cp7]),
        rival,
    )
    return c.bad


def check_no_nonblock_flag(mod: types.ModuleType, tmp: Path) -> list[str]:
    """A platform with no ``O_NONBLOCK`` still reads the files of a backup: the missing flag adds nothing to how they are opened."""
    c = Case(mod)
    _, backup = _fresh(tmp, "portable", mod)
    records: object
    try:
        with replaced(mod, "os", WithoutNonblock()):
            records = mod.verify(backup, ROOT).manifest.records
    except Exception as exc:
        records = f"raised {type(exc).__name__}: {exc}"
    c.eq("a backup is still read and verified", records, 7)
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType, Path], list[str]]] = {
    "rival_claims_the_folder": check_a_rival_claims_the_folder,
    "no_nonblock_flag": check_no_nonblock_flag,
    "durability": check_durability,
    "order_of_names": check_order_of_names,
    "corners": check_corners,
    "league_left_alone": check_league_left_alone,
    "drill_inside": check_drill_inside,
    "command_text": check_command_text,
    "quiet_backup": check_quiet_backup,
    "writes_between_steps": check_writes_between_steps,
    "a_league_that_never_ran": check_a_league_that_never_ran,
    "writes_in_progress": check_writes_in_progress,
    "source_problems": check_source_problems,
    "torn_and_missing": check_torn_and_missing,
    "another_log": check_another_log,
    "manifest_and_files": check_manifest_and_files,
    "manifest_shape": check_manifest_shape,
    "special_files": check_special_files,
    "ledger_files": check_ledger_files,
    "more_ledger_files": check_more_ledger_files,
    "captures_and_commitments": check_captures_and_commitments,
    "eras": check_eras,
    "take_refusals": check_take_refusals,
    "restore_refusals": check_restore_refusals,
    "drill_and_prune": check_drill_and_prune,
    "command": check_command,
}


def failures(mod: types.ModuleType, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        tmp = Path(tempfile.mkdtemp(prefix=f"hoshitori-backup-{name}-"))
        try:
            bad += [f"{name}: {message}" for message in scenario(mod, tmp)]
        except Exception as exc:  # a crash in the scenario is a failure of the module under test
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return bad
