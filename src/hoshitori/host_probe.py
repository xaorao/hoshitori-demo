"""The host's health probe: is the league on this host working, read from its files alone, without stopping or touching it.

    python -m hoshitori.host_probe SETTINGS [--backups DIR] [--max-wal-bytes N] [--min-free-bytes N] [--max-backup-age-s N]
                                   [--max-checkpoint-lag N] [--slots N] [--now RFC3339]

It prints one JSON line, ``{"ok": ..., "findings": [...]}``, and exits 0 when every finding is good, 1 when one is not (an alert), 2 for
arguments or settings that cannot be used. ``hoshitori-league status`` cannot do this job: it opens the ledger as its writer, so it waits
on (or fails at) the lock that a running league holds, and a failure it finds is written into the ledger's state. The probe opens no
database, takes no lock, writes nothing and uses no network. What it reads (OPERATIONS_ARCHITECTURE section 5; OPERATIONS_SLOS sections
2 and 3):

* ``ledger_state``: the ledger's ``STATE`` file (``OK`` when there is none);
* ``segments``: every complete line of the segments, chain and hashes checked as the ledger checks them; bytes after the last newline of
  the newest segment are a line being written, and a newest segment with no complete line is a rotation being made;
* ``heartbeat``: the cycle records against the protocol's schedule over the latest ``slots`` slots (``cycle.heartbeat``: a slot with no
  cycle after the start grace, a cycle not over in time, committed late, or abandoned);
* ``checkpoint_lag``: records appended since the last signed checkpoint (its size is read from its text, not verified: integrity is the
  verifier's job, this is a rate);
* ``wal_bytes``: the size of the index's write-ahead log (LEDGER_SPEC section 4: "WAL size is alarmed");
* ``free_bytes``: free space on the file system of the state directory;
* ``backup`` (with ``--backups``): the age of the newest backup and any unfinished one left behind.

Every limit is a prior, as every number of OPERATIONS_SLOS is, until a measured period replaces it.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from hoshitori import backup, canonical, cycle, deployment, protocol, segment
from hoshitori.checkpoint import CheckpointError, parse_text
from hoshitori.clock import SystemClock
from hoshitori.note import NoteError, split_note
from hoshitori.timeline import from_ms, to_ms
from hoshitori.timeutil import format_rfc3339_ms, parse_rfc3339

PRIOR_MAX_WAL_BYTES: Final = 64 * 1024 * 1024
PRIOR_MIN_FREE_BYTES: Final = 1024 * 1024 * 1024
PRIOR_MAX_BACKUP_AGE_S: Final = 2 * 3600  # twice the hourly backup of deploy/systemd/hoshitori-backup.timer
PRIOR_MAX_CHECKPOINT_LAG: Final = 1000
PRIOR_SLOTS: Final = 2
UNREAD: Final = "not read: the segments cannot be read"


class ProbeError(ValueError):
    """Limits that cannot be used."""


@dataclass(frozen=True)
class Limits:
    max_wal_bytes: int = PRIOR_MAX_WAL_BYTES
    min_free_bytes: int = PRIOR_MIN_FREE_BYTES
    max_backup_age_s: int = PRIOR_MAX_BACKUP_AGE_S
    max_checkpoint_lag: int = PRIOR_MAX_CHECKPOINT_LAG
    slots: int = PRIOR_SLOTS  # how many of the latest slots of the schedule the heartbeat looks at

    def __post_init__(self) -> None:
        values = (self.max_wal_bytes, self.min_free_bytes, self.max_backup_age_s, self.max_checkpoint_lag, self.slots)
        if any(type(value) is not int or value < 0 for value in values) or self.slots < 1:
            raise ProbeError("the limits are whole numbers of at least zero, and at least one slot is watched")


@dataclass(frozen=True)
class Finding:
    name: str
    ok: bool
    value: int | None
    detail: str


def _free(path: Path) -> int:
    return shutil.disk_usage(path).free


def _ledger_state(ledger_dir: Path) -> Finding:
    path = ledger_dir / "STATE"
    if not path.exists():
        return Finding("ledger_state", True, None, "OK")
    try:
        saved = json.loads(path.read_bytes())
        state, detail = str(saved["state"]), str(saved.get("detail", ""))
    except (ValueError, KeyError, TypeError):
        return Finding("ledger_state", False, None, "the STATE file cannot be read")
    if state == "OK":
        return Finding("ledger_state", True, None, "OK")
    return Finding("ledger_state", False, None, f"{state}: {detail}")


def _records(ledger_dir: Path) -> tuple[list[dict[str, Any]], str | None]:
    """Every record in the complete lines of the segments, in order, or the first problem found. Nothing is written."""
    folder = ledger_dir / "segments"
    if not folder.is_dir():
        return [], f"there is no ledger in {ledger_dir.parent}"
    names = sorted(name for name in os.listdir(folder) if not name.endswith(".tmp"))
    records: list[dict[str, Any]] = []
    previous: tuple[str, bytes, int] | None = None
    expected = 0
    for position, name in enumerate(names):
        try:
            first = segment.segment_first_seq(name)
        except segment.SegmentError:
            return records, f"unexpected file in the segment directory: {name}"
        if first != expected:
            return records, f"segment {name} starts at {first}, expected {expected}"
        data = (folder / name).read_bytes()
        if position == len(names) - 1:
            data = data[: data.rfind(b"\n") + 1]  # a line after the last newline is being written
            if position > 0 and not data:
                break  # a rotation being made: its manifest line is not complete yet
        scan = segment.scan_segment(data, first, previous)
        if scan.problem is not segment.Problem.NONE:
            return records, f"{name}: {scan.detail}"
        records.extend(canonical.parse_record(line.record) for line in scan.records)
        expected = first + len(scan.records)
        previous = (name, hashlib.sha256(data).digest(), len(scan.records))
    return records, None


def _heartbeat(settings: deployment.Settings, records: list[dict[str, Any]], now_ms: int, slots: int) -> Finding:
    try:
        rules = protocol.load(settings.protocol.read_bytes())
    except (OSError, protocol.ProtocolError) as exc:
        return Finding("heartbeat", False, None, f"not read: the protocol cannot be read: {exc}")
    try:
        histories = cycle.rebuild([r for r in records if r.get("type") == cycle.TYPE])
    except cycle.CycleError as exc:
        return Finding("heartbeat", False, None, f"not read: {exc}")
    schedule = rules.slots_between(now_ms - slots * rules.schedule_every_ms, now_ms)
    beats = cycle.heartbeat(
        schedule,
        histories.values(),
        now_ms=now_ms,
        start_grace_ms=settings.start_grace_ms,
        finish_within_ms=settings.finish_within_ms,
    )
    if not beats:
        return Finding("heartbeat", True, 0, f"the latest {len(schedule)} slots: nothing missing, unfinished, late or abandoned")
    said = "; ".join(f"{beat.finding.value} {format_rfc3339_ms(from_ms(beat.planned_for_ms))}" for beat in beats)
    return Finding("heartbeat", False, len(beats), said)


def _checkpoint_lag(ledger_dir: Path, records: int, limit: int) -> Finding:
    size = 0
    path = ledger_dir / "checkpoints.jsonl"
    lines = path.read_bytes().splitlines() if path.exists() else []
    if lines:
        try:
            note = base64.b64decode(str(canonical.parse_record(lines[-1])["note"]), validate=True)
            size = parse_text(split_note(note)[0]).size
        except (canonical.CanonicalError, CheckpointError, NoteError, KeyError, ValueError):
            return Finding("checkpoint_lag", False, None, "the last checkpoint cannot be read")
    lag = records - size
    return Finding("checkpoint_lag", lag <= limit, lag, f"{lag} records since the last checkpoint (at most {limit})")


def _backup(folder: Path, now: datetime, limit: int) -> Finding:
    if not folder.is_dir():
        return Finding("backup", False, None, f"no backup in {folder}")
    unfinished = sorted(p.name for p in folder.iterdir() if p.name.endswith(backup.PART))
    left = f"; unfinished: {', '.join(unfinished)}" if unfinished else ""
    kept = backup.listing(folder)
    if not kept:
        return Finding("backup", False, None, f"no backup in {folder}{left}")
    taken_at, name = kept[-1]
    age = int((now - parse_rfc3339(taken_at)).total_seconds())
    return Finding("backup", age <= limit and not unfinished, age, f"the newest backup, {name}, is {age} s old (at most {limit}){left}")


def health(
    settings_file: Path,
    *,
    now: datetime,
    limits: Limits | None = None,
    backups: Path | None = None,
    free: Callable[[Path], int] = _free,
) -> tuple[Finding, ...]:
    """The findings about the league of ``settings_file`` at ``now`` (taken to the millisecond, as every instant of a record is), in a
    fixed order."""
    limits = limits if limits is not None else Limits()
    now = now.replace(microsecond=now.microsecond - now.microsecond % 1000)
    settings = deployment.load_settings(settings_file)
    ledger_dir = settings.directory / "ledger"
    records, problem = _records(ledger_dir)
    found = [_ledger_state(ledger_dir)]
    if problem is None:
        found.append(Finding("segments", True, len(records), f"{len(records)} records"))
        found.append(_heartbeat(settings, records, to_ms(now), limits.slots))
        found.append(_checkpoint_lag(ledger_dir, len(records), limits.max_checkpoint_lag))
    else:
        found.append(Finding("segments", False, None, problem))
        found.append(Finding("heartbeat", False, None, UNREAD))
        found.append(Finding("checkpoint_lag", False, None, UNREAD))
    wal = ledger_dir / "index.db-wal"
    wal_bytes = wal.stat().st_size if wal.exists() else 0
    found.append(
        Finding(
            "wal_bytes",
            wal_bytes <= limits.max_wal_bytes,
            wal_bytes,
            f"{wal_bytes} bytes in the index's write-ahead log (at most {limits.max_wal_bytes})",
        )
    )
    space = free(settings.directory if settings.directory.is_dir() else settings_file.parent)
    found.append(Finding("free_bytes", space >= limits.min_free_bytes, space, f"{space} bytes free (at least {limits.min_free_bytes})"))
    if backups is not None:
        found.append(_backup(backups, now, limits.max_backup_age_s))
    return tuple(found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="host_probe", description="Is the league on this host working? Read from its files alone.")
    parser.add_argument("settings", type=Path)
    parser.add_argument("--backups", type=Path, default=None, help="the folder the backups are taken into")
    parser.add_argument("--max-wal-bytes", type=int, default=PRIOR_MAX_WAL_BYTES)
    parser.add_argument("--min-free-bytes", type=int, default=PRIOR_MIN_FREE_BYTES)
    parser.add_argument("--max-backup-age-s", type=int, default=PRIOR_MAX_BACKUP_AGE_S)
    parser.add_argument("--max-checkpoint-lag", type=int, default=PRIOR_MAX_CHECKPOINT_LAG)
    parser.add_argument("--slots", type=int, default=PRIOR_SLOTS, help="how many of the latest slots the heartbeat looks at")
    parser.add_argument("--now", default=None, help="the time to judge at (RFC 3339; default: this host's clock)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        limits = Limits(args.max_wal_bytes, args.min_free_bytes, args.max_backup_age_s, args.max_checkpoint_lag, args.slots)
        try:
            now = SystemClock().now() if args.now is None else parse_rfc3339(args.now)
        except ValueError as exc:
            raise ProbeError(f"--now is an RFC 3339 time: {exc}") from exc
        found = health(args.settings, now=now, limits=limits, backups=args.backups)
    except (ProbeError, deployment.DeploymentError) as exc:
        sys.stderr.write(f"host_probe: {exc}\n")
        return 2
    ok = all(f.ok for f in found)
    shown = {"ok": ok, "findings": [{"name": f.name, "ok": f.ok, "value": f.value, "detail": f.detail} for f in found]}
    sys.stdout.write(json.dumps(shown, sort_keys=True) + "\n")
    sys.stdout.flush()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
