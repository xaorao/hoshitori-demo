"""Backups of a league folder, taken while the league runs, verified with public material only, and restored into a new folder.

    python -m hoshitori.backup take SETTINGS INTO
    python -m hoshitori.backup verify BACKUP --trusted-root FILE [--anchor FILE ...]
    python -m hoshitori.backup restore BACKUP TARGET --trusted-root FILE --log-key FILE (--anchor FILE ... | --unanchored)
    python -m hoshitori.backup drill BACKUP --trusted-root FILE --log-key FILE (--anchor FILE ... | --unanchored) [--scratch DIR]
    python -m hoshitori.backup prune INTO --keep N

A league folder is the folder of its settings file: the settings, the protocol, the mappings, the trusted root, the model settings, and
the state directory (``ledger/``, ``captures/``, ``commitments/``, ``dispatch.jsonl``). The log key is never copied: it is a secret held
apart (OPERATIONS_ARCHITECTURE section 2, "Keys"), and a restore asks for it only to open what it restored.

Taking a backup never takes the writer's lock and never stops the league. The copy order makes the copy a consistent prefix of the log;
the writer's own order is in ``hoshitori.ledger``:

1. the settings and the files they name, whole (they do not change while a league runs);
2. ``checkpoints.jsonl`` and ``attested.jsonl`` up to their last newline: a checkpoint is written after the records it covers are
   fsynced, and the segments only grow, so the segments copied later hold every record a copied checkpoint covers;
3. ``journal.jsonl`` up to its last newline;
4. ``index.db`` through SQLite's online backup API in one step (``pages=-1``): a snapshot, never a file copy of a live WAL database,
   and one step because an incremental backup restarts whenever another process writes and "may never run to completion"
   (SQLite, "Using the SQLite Online Backup API", section 3.1). The copy is set to rollback mode, so it is one self-contained file. An
   index row is committed only after its line is fsynced, so no row of the copy is beyond the segments copied after it;
5. the segments, the closed ones whole and the newest up to its last newline; a segment made after they were listed waits for the next
   backup, and a newest segment with no complete line is a rotation cut short, which the ledger itself removes;
6. the captures, ``captures.jsonl`` up to its last newline and then every body it names (a body is stored before its line, and a capture
   before any record cites it);
7. every commitment saved under its final name (``commitment_store`` renames a save into place whole);
8. ``dispatch.jsonl`` up to its last newline.

The backup is written under ``<name>.part``, its manifest last, then verified, synced and renamed: a backup that has its name is whole.
``verify`` needs only the trusted root and, to tell an old or substituted backup from the latest, an *anchor*: a signed checkpoint kept
away from the host (the checkpoint of a published bundle, or a commitment's note copied off the host). ``restore`` writes only into a new
folder and opens what it wrote with the ledger's and the capture store's own recovery; ``verify`` has already required every row of the
backed-up index to be the row a rebuild from the segments writes. Exit status of the command: 0 done, 1 refused (the sentence is on
standard error), 2 for arguments that cannot be used.
"""

from __future__ import annotations

import argparse
import base64
import bisect
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final
from urllib.parse import quote

from hoshitori import canonical, commitment_store, deployment, segment, trusted_root
from hoshitori.capture import CaptureStore, CaptureStoreError
from hoshitori.checkpoint import Checkpoint, CheckpointError, parse_text, verify_checkpoint
from hoshitori.clock import Clock, SystemClock
from hoshitori.ledger import STATE_OK, IntegrityFailure, Ledger, read_log
from hoshitori.merkle import MerkleTree
from hoshitori.note import NoteError, Signer, split_note
from hoshitori.timeutil import format_rfc3339_ms
from hoshitori.tlog_proof import HEADER, ProofError, parse_proof

FORMAT: Final = "hoshitori-backup/1"
MANIFEST: Final = "BACKUP.json"
PART: Final = ".part"
PREFIX: Final = "backup-"
BUSY_S: Final = 30.0
CHUNK: Final = 1 << 20
MEMBERS: Final = frozenset({"format", "taken_at", "settings", "origin", "root_sha256", "ledger", "captures", "commitments", "files"})
LEDGER_MEMBERS: Final = frozenset({"records", "root", "checkpoints", "last_checkpoint"})
FILE_MEMBERS: Final = frozenset({"path", "sha256", "size"})
HEX64: Final = re.compile(r"[0-9a-f]{64}")
CHECKPOINTS: Final = "checkpoints.jsonl"
ATTESTED: Final = "attested.jsonl"
JOURNAL: Final = "journal.jsonl"
APPENDED: Final = (CHECKPOINTS, ATTESTED, JOURNAL)  # the ledger's append-only files beside its segments
COMMITMENT_ID: Final = re.compile(r"[a-z0-9]{1,64}")  # the names ``commitment_store`` gives the folders of saved commitments
NO_ANCHOR: Final = (
    "nothing outside the backup to check it against: give an anchor, a signed checkpoint kept away from this host, or say unanchored"
)

Hook = Callable[[str], None]


class BackupError(ValueError):
    """A backup that cannot be taken, verified or restored, said in one sentence."""


class _Unusable(Exception):
    """An argument of the command that cannot be used (exit status 2)."""


@dataclass(frozen=True)
class FileEntry:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Manifest:
    taken_at: str
    settings: str
    origin: str
    root_sha256: str
    records: int
    ledger_root: str
    checkpoints: int
    last_checkpoint: int
    captures: int
    commitments: tuple[str, ...]
    files: tuple[FileEntry, ...]


@dataclass(frozen=True)
class Verified:
    path: Path
    manifest: Manifest
    anchors: int  # how many anchors the backup was checked against


@dataclass(frozen=True)
class Restored:
    path: Path
    records: int
    root: str
    checkpoints: int
    anchors: int
    indexed_missing_rows: int  # rows the recovery added to the restored index, for records copied after it
    replayed_intents: int  # acknowledged commands the recovery took back from the journal
    pending: int  # acknowledged commands not yet appended: the league appends them when it runs
    captures: int
    commitments: int
    log_key: Path  # where the settings expect the log key, which a restore never writes


@dataclass(frozen=True)
class _Facts:
    tree: MerkleTree
    checkpoints: int
    last_checkpoint: int
    captures: int
    commitments: tuple[str, ...]


# --- paths -------------------------------------------------------------------------------------------------------------------------------
def _relative(text: object) -> PurePosixPath | None:
    """A path inside a folder, written plainly (no absolute path, no empty, ``.`` or ``..`` part), or None."""
    if not isinstance(text, str) or not text or "\\" in text or "\x00" in text:
        return None
    if text.startswith("/") or any(part in ("", ".", "..") for part in text.split("/")):
        return None
    return PurePosixPath(text)


def _inside(base: Path, path: Path, what: str) -> PurePosixPath:
    try:
        rel = path.resolve().relative_to(base.resolve())
    except ValueError:
        raise BackupError(f"{what} is outside the folder of the settings: a backup keeps the files of that folder") from None
    if not rel.parts:
        raise BackupError(f"{what} is outside the folder of the settings: a backup keeps the files of that folder")
    return PurePosixPath(rel.as_posix())


def _named(settings: deployment.Settings, base: Path) -> list[PurePosixPath]:
    """The files the settings name, as paths inside their folder: the protocol, the mappings, the trusted root, the model settings."""
    items = [("the protocol", settings.protocol)]
    items += [(f"the mapping of arm {arm}", path) for arm, path in sorted(settings.mappings.items())]
    items += [(f"the barrier mapping of arm {arm}", path) for arm, path in sorted(settings.barrier_mappings.items())]
    items.append(("the trusted root", settings.trusted_root))
    if settings.models is not None:
        items.append(("the model settings file", settings.models))
    return [_inside(base, path, what) for what, path in items]


def _cut(data: bytes) -> bytes:
    """An append-only file up to its last newline: a line after it was being written."""
    return data[: data.rfind(b"\n") + 1]


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_tree(root: Path) -> None:
    for folder, _, _ in os.walk(root, topdown=False):
        _fsync_dir(Path(folder))


def _open_listed(backup: Path, rel: PurePosixPath) -> int:
    """A descriptor of the regular file at ``rel`` in the backup, reached through real folders only (a link is never followed)."""
    here = backup
    for part in rel.parts[:-1]:
        here = here / part
        try:
            info = os.lstat(here)
        except FileNotFoundError:
            raise BackupError(f"the backup lacks {rel}, which its manifest lists") from None
        if not stat.S_ISDIR(info.st_mode):
            raise BackupError(f"{rel} in the backup is not a regular file")
    try:
        descriptor = os.open(here / rel.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        raise BackupError(f"the backup lacks {rel}, which its manifest lists") from None
    except OSError:
        raise BackupError(f"{rel} in the backup is not a regular file") from None
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise BackupError(f"{rel} in the backup is not a regular file")
    return descriptor


def _digest(backup: Path, rel: PurePosixPath) -> tuple[int, str]:
    descriptor = _open_listed(backup, rel)
    hasher, size = hashlib.sha256(), 0
    try:
        while chunk := os.read(descriptor, CHUNK):
            hasher.update(chunk)
            size += len(chunk)
    finally:
        os.close(descriptor)
    return size, hasher.hexdigest()


def _sqlite_uri(path: Path, query: str) -> str:
    return "file:" + quote(str(path.resolve()), safe="/") + "?" + query


# --- writing a backup --------------------------------------------------------------------------------------------------------------------
class _Copy:
    """The files written into a backup (or a restore) being made, each synced, with its size and hash."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.entries: dict[str, FileEntry] = {}

    def _target(self, rel: PurePosixPath) -> Path:
        target = self.root.joinpath(*rel.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def put(self, rel: PurePosixPath, data: bytes) -> None:
        if rel.as_posix() in self.entries:
            return
        descriptor = os.open(self._target(rel), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        self.entries[rel.as_posix()] = FileEntry(rel.as_posix(), len(data), hashlib.sha256(data).hexdigest())

    def put_prefix(self, source: Path, rel: PurePosixPath) -> bytes | None:
        data = _read(source)
        if data is None:
            return None
        self.put(rel, _cut(data))
        return _cut(data)

    def put_index(self, source: Path, rel: PurePosixPath, step: Hook) -> None:
        target = self._target(rel)
        os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))  # owner only, as every file of a backup; SQLite fills it
        try:
            reader = sqlite3.connect(_sqlite_uri(source, "mode=ro"), uri=True, timeout=BUSY_S)
        except sqlite3.Error as exc:
            raise BackupError(f"the index cannot be backed up: {exc}") from exc
        try:
            copy = sqlite3.connect(target)
            try:
                # The whole database in one step, a snapshot that a writer in another process cannot restart: the progress callback is
                # called once per step, so the hook sees "index_step" exactly once.
                reader.backup(copy, pages=-1, progress=lambda _status, _left, _total: step("index_step"))
                copy.execute("PRAGMA journal_mode=DELETE")  # one self-contained file, read later with immutable=1
            finally:
                copy.close()
        except sqlite3.Error as exc:
            raise BackupError(f"the index cannot be backed up: {exc}") from exc
        finally:
            reader.close()
        data = target.read_bytes()
        with open(target, "rb") as handle:
            os.fsync(handle.fileno())
        self.entries[rel.as_posix()] = FileEntry(rel.as_posix(), len(data), hashlib.sha256(data).hexdigest())


def _stamp(moment: datetime) -> str:
    moment = moment.astimezone(UTC)
    return moment.strftime("%Y%m%dT%H%M%S") + f"{moment.microsecond // 1000:03d}Z"


def _require_ok(state_file: Path) -> None:
    data = _read(state_file)
    if data is None:
        return
    saved = json.loads(data)
    if saved["state"] != STATE_OK:
        raise BackupError(f"the ledger is in {saved['state']}: {saved.get('detail', '')}; a backup is taken only of a ledger that is OK")


def _segment_names(directory: Path) -> list[str]:
    names = sorted(name for name in os.listdir(directory) if not name.endswith(".tmp"))
    for name in names:
        try:
            segment.segment_first_seq(name)
        except segment.SegmentError:
            raise BackupError(f"unexpected file in the segment directory: {name}") from None
    return names


def _capture_bodies(log: bytes) -> list[str]:
    """The body hashes the lines of a capture log name, in order, once each."""
    found: list[str] = []
    for number, line in enumerate(log.splitlines(), start=1):
        try:
            body = canonical.parse_record(line)["body_sha256"]
        except (canonical.CanonicalError, KeyError):
            raise BackupError(f"line {number} of the capture log cannot be read") from None
        if not isinstance(body, str) or HEX64.fullmatch(body) is None:
            raise BackupError(f"line {number} of the capture log cannot be read")
        if body not in found:
            found.append(body)
    return found


def take(settings_file: Path, into: Path, *, clock: Clock, hook: Hook | None = None) -> Verified:
    """Back up the league folder of ``settings_file`` into a new folder under ``into``, while the league runs; returns it verified."""
    settings = deployment.load_settings(settings_file)
    base = settings_file.parent
    named = _named(settings, base)
    state = _inside(base, settings.directory, "the state directory")
    ledger_dir = settings.directory / "ledger"
    if not (ledger_dir / "segments").is_dir():
        raise BackupError(f"there is no ledger in {settings.directory}: nothing to back up")
    _require_ok(ledger_dir / "STATE")
    try:
        root = trusted_root.parse_root(settings.trusted_root.read_bytes())
    except (OSError, trusted_root.RootError) as exc:
        raise BackupError(f"the trusted root cannot be read: {exc}") from exc
    now = clock.now()
    final = into / (PREFIX + _stamp(now))
    part = into / (final.name + PART)
    if final.exists():
        raise BackupError(f"{final} exists: a backup is never written over another")
    if part.exists():
        raise BackupError(f"an unfinished backup is in the way: {part}")
    into.mkdir(parents=True, exist_ok=True)
    part.mkdir()
    step = hook if hook is not None else (lambda _point: None)
    copy = _Copy(part)
    copy.put(PurePosixPath(settings_file.name), settings_file.read_bytes())
    for rel in named:
        try:
            data = (base / rel).read_bytes()
        except OSError as exc:
            raise BackupError(f"{rel} cannot be read: {exc.strerror}") from exc
        copy.put(rel, data)
    step("config")
    held = state / "ledger"
    copy.put_prefix(ledger_dir / CHECKPOINTS, held / CHECKPOINTS)
    copy.put_prefix(ledger_dir / ATTESTED, held / ATTESTED)
    step("checkpoints")
    copy.put_prefix(ledger_dir / JOURNAL, held / JOURNAL)
    step("journal")
    if (ledger_dir / "index.db").exists():
        copy.put_index(ledger_dir / "index.db", held / "index.db", step)
    step("index")
    names = _segment_names(ledger_dir / "segments")
    step("listed_segments")
    for position, name in enumerate(names):
        data = (ledger_dir / "segments" / name).read_bytes()
        if position == len(names) - 1:
            data = _cut(data)
            if position > 0 and not data:
                continue  # a rotation cut short before its manifest line was whole: the ledger removes it on its next open
        copy.put(held / "segments" / name, data)
    state_data = _read(ledger_dir / "STATE")
    if state_data is not None:
        copy.put(held / "STATE", state_data)
    step("segments")
    log = copy.put_prefix(settings.directory / "captures" / "captures.jsonl", state / "captures" / "captures.jsonl")
    for body in _capture_bodies(log or b""):
        blob = _read(settings.directory / "captures" / "blobs" / body[:2] / body)
        if blob is None:
            raise BackupError(f"the body {body} of a stored capture is missing")
        copy.put(state / "captures" / "blobs" / body[:2] / body, blob)
    step("captures")
    kept = settings.directory / "commitments"
    for name in sorted(os.listdir(kept)) if kept.is_dir() else []:
        if COMMITMENT_ID.fullmatch(name) is None:
            continue  # a save not yet renamed into place
        try:
            saved = commitment_store.load(kept, name)
        except commitment_store.StoreError as exc:
            raise BackupError(f"the commitment {name} cannot be read: {exc}") from exc
        copy.put(state / "commitments" / name / commitment_store.NOTE, saved.note)
        for authority, token in sorted(saved.tokens.items()):
            copy.put(state / "commitments" / name / (authority + commitment_store.SUFFIX), token)
    step("commitments")
    copy.put_prefix(settings.directory / "dispatch.jsonl", state / "dispatch.jsonl")
    step("dispatch")
    try:
        parsed = deployment.parse_settings(settings_file.read_text(encoding="utf-8"), part)
        facts = _facts(part, parsed, root, set(copy.entries), None)
        manifest = Manifest(
            format_rfc3339_ms(now),
            settings_file.name,
            root.origin,
            root.sha256,
            facts.tree.size,
            facts.tree.root().hex(),
            facts.checkpoints,
            facts.last_checkpoint,
            facts.captures,
            facts.commitments,
            tuple(copy.entries[path] for path in sorted(copy.entries)),
        )
        _write_manifest(part, manifest)
        verify(part, root)
    except BackupError as exc:
        raise BackupError(f"the backup just taken does not verify: {exc}") from exc
    _sync_tree(part)
    os.replace(part, final)
    _fsync_dir(into)
    return Verified(final, manifest, 0)


def _write_manifest(folder: Path, manifest: Manifest) -> None:
    body: dict[str, canonical.JsonValue] = {
        "format": FORMAT,
        "taken_at": manifest.taken_at,
        "settings": manifest.settings,
        "origin": manifest.origin,
        "root_sha256": manifest.root_sha256,
        "ledger": {
            "records": manifest.records,
            "root": manifest.ledger_root,
            "checkpoints": manifest.checkpoints,
            "last_checkpoint": manifest.last_checkpoint,
        },
        "captures": manifest.captures,
        "commitments": list(manifest.commitments),
        "files": [{"path": f.path, "sha256": f.sha256, "size": f.size} for f in manifest.files],
    }
    descriptor = os.open(folder / MANIFEST, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(canonical.dumps(body) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


# --- reading and verifying a backup ------------------------------------------------------------------------------------------------------
def _not_manifest(reason: str) -> BackupError:
    return BackupError(f"{MANIFEST} is not a backup manifest: {reason}")


def _count(value: object) -> int:
    if type(value) is not int or value < 0:
        raise _not_manifest("a count is a whole number")
    return value


def _hex(value: object) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise _not_manifest("a hash is 64 lower-case hex digits")
    return value


def _read_manifest(backup: Path) -> Manifest:
    if not os.path.lexists(backup / MANIFEST):
        raise BackupError(f"{backup} holds no backup: {MANIFEST} is missing")
    with os.fdopen(_open_listed(backup, PurePosixPath(MANIFEST)), "rb") as handle:
        data = handle.read()
    if not data.endswith(b"\n"):
        raise _not_manifest("it does not end in a newline")
    try:
        raw = canonical.parse_record(data[:-1])
    except canonical.CanonicalError as exc:
        raise _not_manifest(f"it is not canonical JSON: {exc}") from exc
    if canonical.dumps(raw) != data[:-1]:
        raise _not_manifest("it is not in canonical form")
    if set(raw) != MEMBERS:
        raise _not_manifest(f"its members are {sorted(MEMBERS)}")
    if raw["format"] != FORMAT:
        raise _not_manifest(f"the format is not {FORMAT}")
    ledger = raw["ledger"]
    if not isinstance(ledger, dict) or set(ledger) != LEDGER_MEMBERS:
        raise _not_manifest(f"its ledger has the members {sorted(LEDGER_MEMBERS)}")
    commitments, files = raw["commitments"], raw["files"]
    if not isinstance(commitments, list) or any(not isinstance(c, str) or COMMITMENT_ID.fullmatch(c) is None for c in commitments):
        raise _not_manifest("the commitments are a list of commitment ids")
    if not isinstance(files, list):
        raise _not_manifest("the files are a list")
    entries: list[FileEntry] = []
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or set(item) != FILE_MEMBERS:
            raise _not_manifest("a file is listed with its path, size and SHA-256")
        rel = _relative(item["path"])
        if rel is None:
            raise BackupError(f"the manifest names a path outside the backup: {item['path']!r}")
        if rel.as_posix() in seen:
            raise BackupError(f"the manifest names {rel} twice")
        seen.add(rel.as_posix())
        entries.append(FileEntry(rel.as_posix(), _count(item["size"]), _hex(item["sha256"])))
    texts = [raw["taken_at"], raw["settings"], raw["origin"]]
    if any(not isinstance(text, str) or not text for text in texts) or _relative(raw["settings"]) is None:
        raise _not_manifest("the time, the settings and the origin are named")
    return Manifest(
        str(raw["taken_at"]),
        str(raw["settings"]),
        str(raw["origin"]),
        _hex(raw["root_sha256"]),
        _count(ledger["records"]),
        _hex(ledger["root"]),
        _count(ledger["checkpoints"]),
        _count(ledger["last_checkpoint"]),
        _count(raw["captures"]),
        tuple(str(c) for c in commitments),
        tuple(entries),
    )


def _check_files(backup: Path, manifest: Manifest) -> None:
    for entry in manifest.files:
        size, digest = _digest(backup, PurePosixPath(entry.path))
        if (size, digest) != (entry.size, entry.sha256):
            raise BackupError(
                f"{entry.path} is not the file the manifest lists: {size} bytes with SHA-256 {digest}, "
                f"the manifest says {entry.size} bytes with {entry.sha256}"
            )
    listed = {entry.path for entry in manifest.files} | {MANIFEST}
    for folder, folders, names in os.walk(backup):
        for name in sorted(folders + names):
            path = Path(folder) / name
            rel = path.relative_to(backup).as_posix()
            mode = os.lstat(path).st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise BackupError(f"{rel} in the backup is not a regular file")
            if rel not in listed:
                raise BackupError(f"the backup holds {rel}, which its manifest does not list")


def _checkpoint(note: bytes, root: trusted_root.TrustedRoot) -> Checkpoint:
    """The checkpoint of a note signed by a key the root allows for the note's size, for the root's log."""
    text, _ = split_note(note)
    size = parse_text(text).size
    keys = [era.key for era in root.eras if root.era_check(era.key, size) == trusted_root.ERA_ACCEPT]
    return verify_checkpoint(note, keys, root.origin)


SIGNED_ERRORS: Final = (canonical.CanonicalError, CheckpointError, NoteError, KeyError, ValueError)


def _checkpoints(path: Path, root: trusted_root.TrustedRoot, tree: MerkleTree) -> list[Checkpoint]:
    data = _read(path) or b""
    found: list[Checkpoint] = []
    for number, line in enumerate(data.splitlines(), start=1):
        try:
            wrapper = canonical.parse_record(line)
            checkpoint = _checkpoint(base64.b64decode(str(wrapper["note"]), validate=True), root)
        except SIGNED_ERRORS as exc:
            raise BackupError(
                f"line {number} of checkpoints.jsonl in the backup is not a checkpoint signed under the trusted root: {exc}"
            ) from exc
        if found and checkpoint.size < found[-1].size:
            raise BackupError(f"checkpoint sizes decrease at line {number} of checkpoints.jsonl in the backup")
        if checkpoint.size > tree.size:
            raise BackupError(f"the checkpoint of size {checkpoint.size} in the backup exceeds its {tree.size} records")
        if tree.root(checkpoint.size) != checkpoint.root:
            raise BackupError(f"the checkpoint of size {checkpoint.size} in the backup does not match its records")
        found.append(checkpoint)
    return found


def _bound(line: bytes, root: trusted_root.TrustedRoot, signed: set[tuple[int, bytes]]) -> tuple[int, bytes] | None:
    """The (size, root) of an attestation line when it is bound to a checkpoint signed under ``root``, else None."""
    try:
        wrapper = canonical.parse_record(line)
        size, digest = wrapper["size"], bytes.fromhex(str(wrapper["root"]))
        if type(size) is not int:
            return None
        if "note" not in wrapper:
            return (size, digest) if (size, digest) in signed else None
        checkpoint = _checkpoint(base64.b64decode(str(wrapper["note"]), validate=True), root)
    except SIGNED_ERRORS:
        return None
    return (size, digest) if (checkpoint.size, checkpoint.root) == (size, digest) else None


def _attested(path: Path, root: trusted_root.TrustedRoot, tree: MerkleTree, signed: set[tuple[int, bytes]]) -> None:
    """Each attestation must carry a checkpoint signed under the root (or, written before entries carried one, name a signed checkpoint
    of the log), as the ledger's own recovery requires (``ledger._attestations``), and fall inside the records."""
    data = _read(path) or b""
    for number, line in enumerate(data.splitlines(), start=1):
        entry = _bound(line, root, signed)
        if entry is None:
            raise BackupError(f"line {number} of attested.jsonl in the backup is not bound to a checkpoint signed under the trusted root")
        size, digest = entry
        if size > tree.size or tree.root(size) != digest:
            raise BackupError(f"the attested checkpoint of size {size} in the backup is not one of its records' heads")


def _index(path: Path, lines: Sequence[segment.RecordLine], names: Sequence[str]) -> None:
    if not path.exists():
        return
    try:
        db = sqlite3.connect(_sqlite_uri(path, "mode=ro&immutable=1"), uri=True)
        try:
            result = db.execute("PRAGMA integrity_check").fetchall()
            if result != [("ok",)]:
                raise BackupError(f"the backup's index fails its integrity check: {result[0][0]}")
            rows = db.execute("SELECT seq, source, rec_id, leaf, segment, off, len FROM ledger ORDER BY seq").fetchall()
        finally:
            db.close()
    except sqlite3.Error as exc:
        raise BackupError(f"the backup's index cannot be read: {exc}") from exc
    firsts = [segment.segment_first_seq(name) for name in names]
    for seq, source, rec_id, leaf, name, offset, length in rows:
        if seq >= len(lines):
            raise BackupError(f"the backup's index has a row for record {seq}, beyond its {len(lines)} records")
        line = lines[seq]
        record = canonical.parse_record(line.record)
        want = (record["source"], record["id"], line.leaf, names[bisect.bisect_right(firsts, seq) - 1], line.offset, line.length)
        if (source, rec_id, bytes(leaf), name, offset, length) != want:
            raise BackupError(f"row {seq} of the backup's index disagrees with its segments")


def _facts(
    backup: Path, settings: deployment.Settings, root: trusted_root.TrustedRoot, listed: set[str], expect: Manifest | None
) -> _Facts:
    """What a backup holds, every part checked against the others and, when ``expect`` is given, against the manifest."""
    state = _inside(backup, settings.directory, "the state directory")
    ledger_dir = settings.directory / "ledger"
    for rel in (*(state / "ledger" / name for name in APPENDED), state / "captures" / "captures.jsonl", state / "dispatch.jsonl"):
        data = _read(backup.joinpath(*rel.parts))
        if data and not data.endswith(b"\n"):
            raise BackupError(f"{rel} in the backup ends inside a line")
    if not (ledger_dir / "segments").is_dir():
        raise BackupError("the backup holds no segments")
    try:
        lines = read_log(ledger_dir)
    except IntegrityFailure as exc:
        raise BackupError(f"the backup's segments are not a clean log: {exc}") from exc
    tree = MerkleTree(line.leaf for line in lines)
    if expect is not None and tree.size != expect.records:
        raise BackupError(f"the backup holds {tree.size} records and its manifest says {expect.records}")
    if expect is not None and tree.root().hex() != expect.ledger_root:
        raise BackupError("the backup's records do not have the root its manifest names")
    signed = _checkpoints(ledger_dir / CHECKPOINTS, root, tree)
    last = signed[-1].size if signed else 0
    if expect is not None and len(signed) != expect.checkpoints:
        raise BackupError(f"the backup holds {len(signed)} checkpoints and its manifest says {expect.checkpoints}")
    if expect is not None and last != expect.last_checkpoint:
        raise BackupError(f"the backup's last checkpoint is of size {last} and its manifest says {expect.last_checkpoint}")
    _attested(ledger_dir / ATTESTED, root, tree, {(c.size, c.root) for c in signed})
    _index(ledger_dir / "index.db", lines, sorted(os.listdir(ledger_dir / "segments")))
    state_data = _read(ledger_dir / "STATE")
    if state_data is not None:
        try:
            ledger_state = json.loads(state_data)["state"]
        except (ValueError, KeyError, TypeError):
            raise BackupError("the backup's ledger state cannot be read") from None
        if ledger_state != STATE_OK:
            raise BackupError(f"the backup's ledger state is {ledger_state}")
    captures = 0
    log = _read(settings.directory / "captures" / "captures.jsonl")
    if log is not None:
        captures = len(log.splitlines())
        for body in _capture_bodies(log):
            blob = (state / "captures" / "blobs" / body[:2] / body).as_posix()
            if blob not in listed:
                raise BackupError(f"the backup's capture log names the body {body}, which the backup lacks")
            if hashlib.sha256((backup / blob).read_bytes()).hexdigest() != body:
                raise BackupError(f"the body {body} in the backup does not have that SHA-256")
    if expect is not None and captures != expect.captures:
        raise BackupError(f"the backup holds {captures} captures and its manifest says {expect.captures}")
    prefix = (state / "commitments").as_posix() + "/"
    held = sorted({path[len(prefix) :].split("/")[0] for path in listed if path.startswith(prefix)})
    for name in expect.commitments if expect is not None else ():
        if name not in held:
            raise BackupError(f"the backup lacks the commitment {name}, which its manifest lists")
    for name in held:
        if expect is not None and name not in expect.commitments:
            raise BackupError(f"the backup holds the commitment {name}, which its manifest does not list")
        try:
            checkpoint = _checkpoint((settings.directory / "commitments" / name / commitment_store.NOTE).read_bytes(), root)
            matches = checkpoint.size <= tree.size and tree.root(checkpoint.size) == checkpoint.root
        except (OSError, *SIGNED_ERRORS):
            matches = False
        if not matches:
            raise BackupError(f"the commitment {name} in the backup is not a checkpoint of its records")
    return _Facts(tree, len(signed), last, captures, tuple(held))


def verify(backup: Path, root: trusted_root.TrustedRoot, anchors: Sequence[bytes] = ()) -> Verified:
    """Check a backup with public material only: its files against its manifest, its records against its own checkpoints, attestations,
    index, captures and commitments, all under ``root``, and against each anchor (a signed checkpoint kept away from the host)."""
    manifest = _read_manifest(backup)
    _check_files(backup, manifest)
    listed = {entry.path for entry in manifest.files}
    if manifest.settings not in listed:
        raise BackupError(f"the backup lacks {manifest.settings}, which its manifest names as the settings")
    try:
        settings = deployment.parse_settings((backup / manifest.settings).read_text(encoding="utf-8"), backup)
    except (deployment.DeploymentError, UnicodeDecodeError) as exc:
        raise BackupError(f"the backup's settings cannot be used: {exc}") from exc
    for rel in _named(settings, backup):
        if rel.as_posix() not in listed:
            raise BackupError(f"the backup lacks {rel}, which its settings name")
    if manifest.origin != root.origin:
        raise BackupError(f"the backup is of the log {manifest.origin}, not of {root.origin}")
    if manifest.root_sha256 != root.sha256:
        raise BackupError(
            f"the backup was taken under another trusted root (SHA-256 {manifest.root_sha256}), not the one given ({root.sha256})"
        )
    if hashlib.sha256(settings.trusted_root.read_bytes()).hexdigest() != manifest.root_sha256:
        raise BackupError("the trusted root in the backup is not the one its manifest names")
    facts = _facts(backup, settings, root, listed, manifest)
    for number, note in enumerate(anchors, start=1):
        try:
            anchor = _checkpoint(note, root)
        except SIGNED_ERRORS as exc:
            raise BackupError(f"anchor {number} is not a checkpoint signed under the trusted root: {exc}") from exc
        if anchor.size > facts.tree.size:
            raise BackupError(
                f"the backup holds {facts.tree.size} records, fewer than anchor {number} of size {anchor.size}: it is older than the anchor"
            )
        if facts.tree.root(anchor.size) != anchor.root:
            raise BackupError(f"the backup does not match anchor {number} of size {anchor.size}: it holds another history")
    return Verified(backup, manifest, len(anchors))


# --- restoring ---------------------------------------------------------------------------------------------------------------------------
def restore(
    backup: Path,
    target: Path,
    *,
    root: trusted_root.TrustedRoot,
    seed: bytes,
    anchors: Sequence[bytes],
    unanchored: bool,
    clock: Clock,
) -> Restored:
    """Verify ``backup``, write it into the new folder ``target``, and open it with the ledger's and the capture store's own recovery.
    ``seed`` is the log key, checked against ``root``; it is never written. The index needs no rebuild here: ``verify`` has required
    every row of the backed-up index to be the row a rebuild from the segments writes, and the recovery adds the rows of records copied
    after the index, as a rebuild does."""
    if not anchors and not unanchored:
        raise BackupError(NO_ANCHOR)
    verified = verify(backup, root, anchors)
    manifest = verified.manifest
    signer = Signer.from_seed(root.origin, seed)
    if root.era_check(signer.verifier, manifest.records) != trusted_root.ERA_ACCEPT:
        raise BackupError(f"the log key given is not allowed by the trusted root for a log of {manifest.records} records")
    if target.exists() or target.is_symlink():
        raise BackupError(f"the restore target {target} exists: a restore writes only into a new folder")
    part = target.with_name(target.name + PART)
    if part.exists() or part.is_symlink():
        raise BackupError(f"an unfinished restore is in the way: {part}")
    target.parent.mkdir(parents=True, exist_ok=True)
    part.mkdir()
    for entry in manifest.files:
        rel = PurePosixPath(entry.path)
        out = part.joinpath(*rel.parts)
        out.parent.mkdir(parents=True, exist_ok=True)
        with (
            os.fdopen(_open_listed(backup, rel), "rb") as source,
            os.fdopen(os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as sink,
        ):
            shutil.copyfileobj(source, sink, CHUNK)
            sink.flush()
            os.fsync(sink.fileno())
    _sync_tree(part)
    os.replace(part, target)
    _fsync_dir(target.parent)
    settings = deployment.load_settings(target / manifest.settings)
    led = Ledger.open(
        settings.directory / "ledger", signer=signer, origin=root.origin, clock=clock, segment_records=deployment.SEGMENT_RECORDS
    )
    try:
        if led.state != STATE_OK:
            raise BackupError(f"the restored ledger opened in {led.state}: {led.state_detail}")
        records, head, report, pending = led.size, led.root().hex(), led.report, led.pending()
    finally:
        led.close(checkpoint=False)
    try:
        store = CaptureStore(settings.directory / "captures")
    except CaptureStoreError as exc:
        raise BackupError(f"the restored capture store does not open: {exc}") from exc
    try:
        captures = len(store)
    finally:
        store.close()
    if captures != manifest.captures:
        raise BackupError(f"the restored capture store holds {captures} captures, the backup {manifest.captures}")
    return Restored(
        target,
        records,
        head,
        manifest.checkpoints,
        verified.anchors,
        report.indexed_missing_rows,
        report.replayed_intents,
        pending,
        captures,
        len(manifest.commitments),
        settings.log_key,
    )


def _restored_json(done: Restored) -> dict[str, Any]:
    return {
        "path": str(done.path),
        "records": done.records,
        "root": done.root,
        "checkpoints": done.checkpoints,
        "anchors": done.anchors,
        "indexed_missing_rows": done.indexed_missing_rows,
        "replayed_intents": done.replayed_intents,
        "pending": done.pending,
        "captures": done.captures,
        "commitments": done.commitments,
        "log_key": str(done.log_key),
    }


def drill(
    backup: Path,
    scratch: Path,
    *,
    root: trusted_root.TrustedRoot,
    seed: bytes,
    anchors: Sequence[bytes],
    unanchored: bool,
    clock: Clock,
) -> dict[str, Any]:
    """The restore drill (OPERATIONS_ARCHITECTURE section 2): restore ``backup`` into a new folder under ``scratch``, report what the
    restore found and how long it took, then remove that folder."""
    started = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix="hoshitori-drill-", dir=scratch))
    try:
        done = restore(backup, work / "league", root=root, seed=seed, anchors=anchors, unanchored=unanchored, clock=clock)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    found = _restored_json(done)
    del found["path"], found["log_key"]
    return {**found, "seconds": time.monotonic() - started}


def listing(directory: Path) -> list[tuple[str, str]]:
    """The backups in ``directory``, oldest first, as (time taken, folder name): only folders whose manifest is a backup's count, never
    an unfinished ``.part`` folder or a link."""
    found: list[tuple[str, str]] = []
    for child in sorted(directory.iterdir()):
        if child.is_symlink() or not child.is_dir() or child.name.endswith(PART):
            continue
        try:
            found.append((_read_manifest(child).taken_at, child.name))
        except BackupError:
            continue
    return sorted(found)


def prune(directory: Path, keep: int) -> list[str]:
    """Remove the oldest backups in ``directory`` beyond the ``keep`` newest (see ``listing``); returns the names removed."""
    if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
        raise BackupError("prune keeps at least one backup")
    found = listing(directory)
    doomed = sorted(name for _, name in found[: max(0, len(found) - keep)])
    for name in doomed:
        shutil.rmtree(directory / name)
    return doomed


# --- the command -------------------------------------------------------------------------------------------------------------------------
def _verified_json(done: Verified) -> dict[str, Any]:
    m = done.manifest
    return {
        "path": str(done.path),
        "records": m.records,
        "root": m.ledger_root,
        "checkpoints": m.checkpoints,
        "last_checkpoint": m.last_checkpoint,
        "captures": m.captures,
        "commitments": list(m.commitments),
        "anchors": done.anchors,
    }


def _root_of(file: Path) -> trusted_root.TrustedRoot:
    try:
        return trusted_root.parse_root(file.read_bytes())
    except OSError as exc:
        raise _Unusable(f"the trusted root cannot be read: {exc.strerror}") from exc
    except trusted_root.RootError as exc:
        raise _Unusable(f"the trusted root cannot be read: {exc}") from exc


def _anchor_of(file: Path) -> bytes:
    """A signed checkpoint note, or the checkpoint inside a C2SP tlog-proof (the ``.tlog-proof`` files of a published bundle)."""
    try:
        data = file.read_bytes()
    except OSError as exc:
        raise _Unusable(f"the anchor {file} cannot be read: {exc.strerror}") from exc
    if data.startswith(HEADER.encode() + b"\n"):
        try:
            return parse_proof(data).note
        except ProofError as exc:
            raise _Unusable(f"the anchor {file} is not a tlog-proof: {exc}") from exc
    return data


def _newest(folder: Path) -> Path:
    kept = listing(folder) if folder.is_dir() else []
    if not kept:
        raise BackupError(f"there is no backup in {folder}")
    return folder / kept[-1][1]


def _emit(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, sort_keys=True) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hoshitori-backup", description="Back up, verify and restore a league folder.")
    commands = parser.add_subparsers(dest="command", required=True)
    take_cmd = commands.add_parser("take", help="back up the league folder of a settings file while it runs")
    take_cmd.add_argument("settings", type=Path)
    take_cmd.add_argument("into", type=Path, help="the folder that holds the backups; each backup is a new folder in it")
    verify_cmd = commands.add_parser("verify", help="check a backup with the trusted root and anchors")
    verify_cmd.add_argument("backup", type=Path)
    verify_cmd.add_argument("--trusted-root", type=Path, required=True)
    verify_cmd.add_argument(
        "--anchor", type=Path, action="append", default=[], help="a signed checkpoint or a tlog-proof kept off the host"
    )
    for name, what in (
        ("restore", "restore a backup into a new folder"),
        ("drill", "restore a backup into a scratch folder, then remove it"),
    ):
        sub = commands.add_parser(name, help=what)
        if name == "restore":
            sub.add_argument("backup", type=Path)
            sub.add_argument("target", type=Path)
        else:
            chosen = sub.add_mutually_exclusive_group(required=True)
            chosen.add_argument("backup", type=Path, nargs="?", default=None)
            chosen.add_argument("--newest-in", type=Path, default=None, metavar="DIR", help="drill the newest backup in this folder")
            sub.add_argument("--scratch", type=Path, default=None, help="where the drill restores (default: the system's temporary folder)")
        sub.add_argument("--trusted-root", type=Path, required=True)
        sub.add_argument("--log-key", type=Path, required=True, help="the log key file, readable by its owner only; it is never copied")
        which = sub.add_mutually_exclusive_group()
        which.add_argument("--anchor", type=Path, action="append", default=[], help="a signed checkpoint or a tlog-proof kept off the host")
        which.add_argument("--unanchored", action="store_true", help="restore with nothing outside the backup to check it against")
    prune_cmd = commands.add_parser("prune", help="remove the oldest backups beyond a number kept")
    prune_cmd.add_argument("into", type=Path)
    prune_cmd.add_argument("--keep", type=int, required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    clock = SystemClock()
    try:
        if args.command == "take":
            _emit(_verified_json(take(args.settings, args.into, clock=clock)))
        elif args.command == "verify":
            root = _root_of(args.trusted_root)
            anchors = [_anchor_of(file) for file in args.anchor]
            _emit(_verified_json(verify(args.backup, root, anchors)))
        elif args.command == "prune":
            _emit({"removed": prune(args.into, args.keep)})
        else:
            root = _root_of(args.trusted_root)
            anchors = [_anchor_of(file) for file in args.anchor]
            seed = deployment.read_key(args.log_key)
            if args.command == "restore":
                done = restore(args.backup, args.target, root=root, seed=seed, anchors=anchors, unanchored=args.unanchored, clock=clock)
                _emit(_restored_json(done))
            else:
                scratch = args.scratch if args.scratch is not None else Path(tempfile.gettempdir())
                chosen_backup = args.backup if args.newest_in is None else _newest(args.newest_in)
                _emit(drill(chosen_backup, scratch, root=root, seed=seed, anchors=anchors, unanchored=args.unanchored, clock=clock))
    except (_Unusable, deployment.DeploymentError) as exc:
        sys.stderr.write(f"hoshitori-backup: {exc}\n")
        return 2
    except BackupError as exc:
        sys.stderr.write(f"hoshitori-backup: {exc}\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"hoshitori-backup: {type(exc).__name__}: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
