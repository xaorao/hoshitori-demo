"""Scenarios for the host's health probe, runnable against the real module or a mutant of it (``failures(mod)``).

The league folder is real: a protocol file, a ledger written through ``hoshitori.ledger`` holding cycle records made with
``hoshitori.cycle`` (the schedule is hourly from 12:00; slot ``k`` is at 12:00 plus ``k`` hours), a trusted root and a backup taken with
``hoshitori.backup``. The probe must find what is wrong without writing anything, while the writer holds the ledger. Every expected value
is worked out by hand from the records written and the heartbeat rule (a slot is MISSING after the start grace of five minutes, a cycle
UNFINISHED after twenty, LATE if committed after twenty).
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import re
import shutil
import tempfile
import types
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hoshitori import backup, canonical, cycle, protocol, segment
from hoshitori.clock import ManualClock
from hoshitori.ledger import Ledger
from hoshitori.timeline import to_ms
from tests import root_support as R
from tests.backup_checks import FIXTURE, ROOT_BYTES, SIGNER
from tests.deployment_checks import good_settings
from tests.protocol_checks import make

START = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
RULES = make(protocol, schedule_start_ms=to_ms(START))
SOURCE = "urn:hoshitori:league:test"
GIB = 1024**3


def slot(k: int) -> int:
    return to_ms(START) + k * 3_600_000


def at(k: int, minutes: float) -> datetime:
    return START + timedelta(hours=k, minutes=minutes)


def planned(k: int) -> cycle.History:
    return cycle.begin(RULES.sha256, slot(k), slot(k) + 1000)


def committed(k: int, after_minutes: int) -> cycle.History:
    t0 = slot(k)
    S = cycle.State
    steps = [
        cycle.Step(S.DISCOVERING, t0 + 2000, discovery_at_ms=t0 + 2000),
        cycle.Step(S.SEALED, t0 + 3000, sealed_at_ms=t0 + 3000, manifest_hash="a" * 64),
        cycle.Step(S.COLLECTING, t0 + 4000),
        cycle.Step(S.SNAPSHOT_FROZEN, t0 + 5000, snapshot_at_ms=t0 + 5000, snapshot_hash="b" * 64),
        cycle.Step(S.RUNNING, t0 + 6000),
        cycle.Step(S.COMMITTING, t0 + 7000, batch_root="c" * 64),
        cycle.Step(S.COMMITTED, t0 + after_minutes * 60_000, commitment_id=f"cm{k}", committed_at_ms=t0 + after_minutes * 60_000),
    ]
    history = planned(k)
    for step in steps:
        history = cycle.advance(history, step)
    return history


def abandoned(k: int) -> cycle.History:
    return cycle.advance(planned(k), cycle.abandon_step("PROCESS_LOST", slot(k) + 60_000))


def records_of(history: cycle.History) -> list[dict[str, canonical.JsonValue]]:
    return [cycle.to_record(t, SOURCE, t.step.at_ms) for t in history]


class League:
    """A league folder whose ledger holds the cycle records given, a checkpoint after ``checkpoint_after`` of them, and the rest."""

    def __init__(self, base: Path, histories: list[cycle.History], *, checkpoint_after: int | None = None) -> None:
        self.base = base
        base.mkdir(parents=True)
        self.settings = base / "settings.json"
        self.settings.write_text(json.dumps({**good_settings(), "directory": "run"}))
        (base / "protocol.json").write_bytes(protocol.dump(RULES))
        (base / "mapping.json").write_bytes(FIXTURE)
        (base / "root.json").write_bytes(ROOT_BYTES)
        self.ledger_dir = base / "run" / "ledger"
        self.led = Ledger.open(self.ledger_dir, signer=SIGNER, origin=R.LOG_ORIGIN, clock=ManualClock(START), segment_records=3)
        records = [r for h in histories for r in records_of(h)]
        cut = len(records) if checkpoint_after is None else checkpoint_after
        for record in records[:cut]:
            self.led.submit(record)
        self.led.append_pending()
        if cut:
            self.led.checkpoint()
        for record in records[cut:]:
            self.led.submit(record)
        self.led.append_pending()
        self.records = len(records)

    def close(self) -> None:
        self.led.abandon()

    def wal(self) -> int:
        path = self.ledger_dir / "index.db-wal"
        return path.stat().st_size if path.exists() else 0


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


def tree(path: Path) -> dict[str, tuple[int, int]]:
    """Every file below ``path`` with its size and modification time, to show that nothing was written."""
    return {str(p.relative_to(path)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(path.rglob("*")) if p.is_file()}


def found(mod: types.ModuleType, league: League, now: datetime, **kw: Any) -> list[tuple[str, bool, int | None, str]]:
    kw.setdefault("free", lambda _path: 2 * GIB)
    return [(f.name, f.ok, f.value, f.detail) for f in mod.health(league.settings, now=now, **kw)]


def by_name(rows: list[tuple[str, bool, int | None, str]], name: str) -> tuple[str, bool, int | None, str] | None:
    return next((row for row in rows if row[0] == name), None)


# --- a healthy league ---------------------------------------------------------------------------------------------------------------------
def check_healthy(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    league = League(tmp / "league", [committed(4, 2), planned(5)], checkpoint_after=8)
    backups = tmp / "backups"
    taken = backup.take(league.settings, backups, clock=ManualClock(at(5, -20)))
    before = tree(tmp)
    rows = found(mod, league, at(5, 10), backups=backups)
    c.eq("nothing was written, with the writer holding the ledger", tree(tmp), before)
    c.eq(
        "the findings",
        rows,
        [
            ("ledger_state", True, None, "OK"),
            ("segments", True, 9, "9 records"),
            ("heartbeat", True, 0, "the latest 2 slots: nothing missing, unfinished, late or abandoned"),
            ("checkpoint_lag", True, 1, "1 records since the last checkpoint (at most 1000)"),
            ("wal_bytes", True, league.wal(), f"{league.wal()} bytes in the index's write-ahead log (at most {64 * 1024 * 1024})"),
            ("free_bytes", True, 2 * GIB, f"{2 * GIB} bytes free (at least {GIB})"),
            ("backup", True, 1800, f"the newest backup, {taken.path.name}, is 1800 s old (at most 7200)"),
        ],
    )
    c.eq("the write-ahead log is there while the writer runs", league.wal() > 0, True)
    c.eq("no backup finding when none is watched", by_name(found(mod, league, at(5, 10)), "backup"), None)
    c.eq("the default free space is the file system's", type(mod.health(league.settings, now=at(5, 10))[5].value), int)
    asked: list[Path] = []
    mod.health(league.settings, now=at(5, 10), free=lambda path: asked.append(path) or 2 * GIB)
    c.eq("the free space asked of the state directory", asked, [league.base / "run"])
    c.frozen("a finding", mod.health(league.settings, now=at(5, 10), free=lambda _path: GIB)[0], "ok")
    c.eq(
        "a time finer than a millisecond is taken to the millisecond",
        found(mod, league, at(5, 10) + timedelta(microseconds=999), backups=backups),
        rows,
    )
    every = League(tmp / "every", [committed(k, 3) for k in range(6)])
    c.eq(
        "a window wider than the schedule so far",
        by_name(found(mod, every, at(5, 30), limits=mod.Limits(slots=10)), "heartbeat"),
        ("heartbeat", True, 0, "the latest 6 slots: nothing missing, unfinished, late or abandoned"),
    )
    c.eq(
        "no lag right after a checkpoint",
        by_name(found(mod, every, at(5, 30)), "checkpoint_lag"),
        ("checkpoint_lag", True, 0, "0 records since the last checkpoint (at most 1000)"),
    )
    every.close()
    league.close()
    return c.bad


# --- the heartbeat ------------------------------------------------------------------------------------------------------------------------
def check_heartbeat(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)

    def beat(name: str, histories: list[cycle.History], now: datetime, **kw: Any) -> Any:
        league = League(tmp / name, histories)
        try:
            return by_name(found(mod, league, now, **kw), "heartbeat")
        finally:
            league.close()

    c.eq(
        "a cycle not over after twenty minutes",
        beat("unfinished", [committed(4, 2), planned(5)], at(5, 25)),
        ("heartbeat", False, 1, "UNFINISHED 2026-10-29T17:00:00.000Z"),
    )
    c.eq(
        "and at exactly twenty, not yet",
        beat("twenty", [committed(4, 2), planned(5)], at(5, 20)),
        ("heartbeat", True, 0, "the latest 2 slots: nothing missing, unfinished, late or abandoned"),
    )
    c.eq(
        "a late commit and an abandoned cycle",
        beat("late", [committed(4, 25), abandoned(5)], at(5, 10)),
        ("heartbeat", False, 2, "LATE 2026-10-29T16:00:00.000Z; ABANDONED 2026-10-29T17:00:00.000Z"),
    )
    c.eq(
        "a slot with no cycle after the grace",
        beat("missing", [committed(4, 2)], at(5, 6)),
        ("heartbeat", False, 1, "MISSING 2026-10-29T17:00:00.000Z"),
    )
    c.eq(
        "and within it, not yet",
        beat("grace", [committed(4, 2)], at(5, 4)),
        ("heartbeat", True, 0, "the latest 2 slots: nothing missing, unfinished, late or abandoned"),
    )
    c.eq(
        "a wider window sees the older misses",
        beat("wider", [committed(4, 2), planned(5)], at(5, 10), limits=mod.Limits(slots=6)),
        (
            "heartbeat",
            False,
            4,
            "MISSING 2026-10-29T12:00:00.000Z; MISSING 2026-10-29T13:00:00.000Z; MISSING 2026-10-29T14:00:00.000Z; MISSING 2026-10-29T15:00:00.000Z",
        ),
    )
    c.eq(
        "one slot only",
        beat("one", [planned(5)], at(5, 10), limits=mod.Limits(slots=1)),
        ("heartbeat", True, 0, "the latest 1 slots: nothing missing, unfinished, late or abandoned"),
    )
    c.eq(
        "before the first slot",
        beat("early", [], at(0, -30)),
        ("heartbeat", True, 0, "the latest 0 slots: nothing missing, unfinished, late or abandoned"),
    )
    gap = League(tmp / "gap", [])
    gap.led.submit(records_of(committed(4, 2))[1])  # the second step of a cycle without its first
    gap.led.append_pending()
    c.eq(
        "cycle records that cannot be rebuilt",
        by_name(found(mod, gap, at(5, 10)), "heartbeat"),
        ("heartbeat", False, None, "not read: " + _rebuild_error([records_of(committed(4, 2))[1]])),
    )
    gap.close()
    league = League(tmp / "no-protocol", [planned(5)])
    (league.base / "protocol.json").write_bytes(b"not a protocol")
    row = by_name(found(mod, league, at(5, 10)), "heartbeat")
    c.eq(
        "a protocol that cannot be read",
        (row[:3], row[3].startswith("not read: the protocol cannot be read: ")) if row else None,
        (("heartbeat", False, None), True),
    )
    league.close()
    return c.bad


def _rebuild_error(records: list[dict[str, canonical.JsonValue]]) -> str:
    """What the cycle module says of records it cannot rebuild (the probe passes the sentence on)."""
    try:
        cycle.rebuild(records)
    except cycle.CycleError as exc:
        return str(exc)
    raise AssertionError("the records were expected not to rebuild")


# --- the ledger ---------------------------------------------------------------------------------------------------------------------------
def check_ledger(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    league = League(tmp / "failed", [planned(5)])
    league.close()
    (league.ledger_dir / "STATE").write_text(json.dumps({"state": "INTEGRITY_FAILURE", "detail": "segment x: damaged"}))
    c.eq(
        "a ledger in failure",
        by_name(found(mod, league, at(5, 10)), "ledger_state"),
        ("ledger_state", False, None, "INTEGRITY_FAILURE: segment x: damaged"),
    )
    (league.ledger_dir / "STATE").write_bytes(b"not json")
    c.eq(
        "a state that cannot be read",
        by_name(found(mod, league, at(5, 10)), "ledger_state"),
        ("ledger_state", False, None, "the STATE file cannot be read"),
    )
    (league.ledger_dir / "STATE").write_text(json.dumps({"state": "OK", "detail": ""}))
    c.eq("a state file that says OK", by_name(found(mod, league, at(5, 10)), "ledger_state"), ("ledger_state", True, None, "OK"))
    (league.ledger_dir / "STATE").write_text(json.dumps({"state": "FENCED"}))
    c.eq("a state with no detail", by_name(found(mod, league, at(5, 10)), "ledger_state"), ("ledger_state", False, None, "FENCED: "))

    league = League(tmp / "damaged", [committed(4, 2), planned(5)], checkpoint_after=8)
    league.close()
    first = league.ledger_dir / "segments" / segment.segment_name(0)
    data = bytearray(first.read_bytes())
    data[40] ^= 1
    first.write_bytes(bytes(data))
    detail = segment.scan_segment(bytes(data), 0, None).detail
    rows = found(mod, league, at(5, 10))
    c.eq("a damaged closed segment", by_name(rows, "segments"), ("segments", False, None, f"{first.name}: {detail}"))
    c.eq("and so no heartbeat", by_name(rows, "heartbeat"), ("heartbeat", False, None, "not read: the segments cannot be read"))
    c.eq("and no lag", by_name(rows, "checkpoint_lag"), ("checkpoint_lag", False, None, "not read: the segments cannot be read"))

    league = League(tmp / "writing", [committed(4, 2), planned(5)], checkpoint_after=8)
    league.close()
    names = sorted(p.name for p in (league.ledger_dir / "segments").iterdir())
    newest = league.ledger_dir / "segments" / names[-1]
    whole = newest.read_bytes()
    newest.write_bytes(whole + b'{"leaf":"half a line')
    c.eq("a line being written is not read", by_name(found(mod, league, at(5, 10)), "segments"), ("segments", True, 9, "9 records"))
    newest.write_bytes(whole)  # a writer rotates only after its last line is whole
    (league.ledger_dir / "segments" / segment.segment_name(9)).write_bytes(b"")
    (league.ledger_dir / "segments" / "segment-000000000012.jsonl.tmp").write_bytes(b"x")
    c.eq(
        "nor a rotation being made, nor a temporary file",
        by_name(found(mod, league, at(5, 10)), "segments"),
        ("segments", True, 9, "9 records"),
    )
    (league.ledger_dir / "segments" / "notes.txt").write_bytes(b"x")
    c.eq(
        "a stray file",
        by_name(found(mod, league, at(5, 10)), "segments"),
        ("segments", False, None, "unexpected file in the segment directory: notes.txt"),
    )
    (league.ledger_dir / "segments" / "notes.txt").unlink()
    (league.ledger_dir / "segments" / segment.segment_name(3)).unlink()
    c.eq(
        "a segment missing",
        by_name(found(mod, league, at(5, 10)), "segments"),
        ("segments", False, None, "segment segment-000000000006.jsonl starts at 6, expected 3"),
    )

    league = League(tmp / "first-rotation", [planned(3), planned(4), planned(5)])  # three records: the first segment is full
    league.close()
    (league.ledger_dir / "segments" / segment.segment_name(3)).write_bytes(b"")
    c.eq(
        "the first rotation being made",
        by_name(found(mod, league, at(5, 10)), "segments"),
        ("segments", True, 3, "3 records"),
    )

    empty = tmp / "empty"
    empty.mkdir()
    (empty / "settings.json").write_text(json.dumps({**good_settings(), "directory": "run"}))
    (empty / "protocol.json").write_bytes(protocol.dump(RULES))
    rows = [(f.name, f.ok, f.value, f.detail) for f in mod.health(empty / "settings.json", now=at(5, 10), free=lambda _p: 2 * GIB)]
    c.eq("no ledger", by_name(rows, "segments"), ("segments", False, None, f"there is no ledger in {empty / 'run'}"))
    asked: list[Path] = []
    mod.health(empty / "settings.json", now=at(5, 10), free=lambda path: asked.append(path) or 2 * GIB)
    c.eq("with no state directory yet, the free space of the settings' folder", asked, [empty])
    c.eq(
        "no write-ahead log",
        by_name(rows, "wal_bytes"),
        ("wal_bytes", True, 0, f"0 bytes in the index's write-ahead log (at most {64 * 1024 * 1024})"),
    )

    league = League(tmp / "bad-checkpoint", [planned(5)])
    league.close()
    signed = canonical.parse_record((league.ledger_dir / "checkpoints.jsonl").read_bytes().rstrip(b"\n"))
    note = str(signed["note"])
    (league.ledger_dir / "checkpoints.jsonl").write_bytes(canonical.dumps({**signed, "note": note[:8] + "!" + note[8:]}) + b"\n")
    c.eq(
        "a checkpoint whose note has a character outside Base64, which the ledger refuses too",
        by_name(found(mod, league, at(5, 10)), "checkpoint_lag"),
        ("checkpoint_lag", False, None, "the last checkpoint cannot be read"),
    )
    (league.ledger_dir / "checkpoints.jsonl").write_bytes(b'{"note":"not base64!","size":1}\n')
    c.eq(
        "a last checkpoint that cannot be read",
        by_name(found(mod, league, at(5, 10)), "checkpoint_lag"),
        ("checkpoint_lag", False, None, "the last checkpoint cannot be read"),
    )
    (league.ledger_dir / "checkpoints.jsonl").unlink()
    c.eq(
        "no checkpoint yet: every record is behind",
        by_name(found(mod, league, at(5, 10)), "checkpoint_lag"),
        ("checkpoint_lag", True, 1, "1 records since the last checkpoint (at most 1000)"),
    )
    return c.bad


# --- limits and backups -------------------------------------------------------------------------------------------------------------------
def check_limits_and_backups(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    league = League(tmp / "league", [committed(4, 2), planned(5)], checkpoint_after=8)
    backups = tmp / "backups"
    taken = backup.take(league.settings, backups, clock=ManualClock(at(5, -20)))
    tight = mod.Limits(max_wal_bytes=league.wal() - 1, min_free_bytes=2 * GIB + 1, max_backup_age_s=1799, max_checkpoint_lag=0)
    rows = found(mod, league, at(5, 10), backups=backups, limits=tight)
    c.eq(
        "the lag over its limit",
        by_name(rows, "checkpoint_lag"),
        ("checkpoint_lag", False, 1, "1 records since the last checkpoint (at most 0)"),
    )
    c.eq(
        "the log over its limit",
        by_name(rows, "wal_bytes"),
        ("wal_bytes", False, league.wal(), f"{league.wal()} bytes in the index's write-ahead log (at most {league.wal() - 1})"),
    )
    c.eq(
        "the disk under its floor",
        by_name(rows, "free_bytes"),
        ("free_bytes", False, 2 * GIB, f"{2 * GIB} bytes free (at least {2 * GIB + 1})"),
    )
    c.eq(
        "the backup too old",
        by_name(rows, "backup"),
        ("backup", False, 1800, f"the newest backup, {taken.path.name}, is 1800 s old (at most 1799)"),
    )
    exact = mod.Limits(max_wal_bytes=league.wal(), min_free_bytes=2 * GIB, max_backup_age_s=1800, max_checkpoint_lag=1)
    rows = found(mod, league, at(5, 10), backups=backups, limits=exact)
    c.eq("every limit met exactly", [row[1] for row in rows], [True] * 7)
    newer = backup.take(league.settings, backups, clock=ManualClock(at(5, 5)))
    c.eq(
        "the newest backup is the one looked at",
        by_name(found(mod, league, at(5, 10), backups=backups), "backup"),
        ("backup", True, 300, f"the newest backup, {newer.path.name}, is 300 s old (at most 7200)"),
    )
    (backups / "backup-20261029T170900000Z.part").mkdir()
    c.eq(
        "an unfinished backup left behind",
        by_name(found(mod, league, at(5, 10), backups=backups), "backup"),
        (
            "backup",
            False,
            300,
            f"the newest backup, {newer.path.name}, is 300 s old (at most 7200); unfinished: backup-20261029T170900000Z.part",
        ),
    )
    nothing = tmp / "nothing"
    nothing.mkdir()
    c.eq("no backup", by_name(found(mod, league, at(5, 10), backups=nothing), "backup"), ("backup", False, None, f"no backup in {nothing}"))
    (nothing / "backup-x.part").mkdir()
    c.eq(
        "only an unfinished one",
        by_name(found(mod, league, at(5, 10), backups=nothing), "backup"),
        ("backup", False, None, f"no backup in {nothing}; unfinished: backup-x.part"),
    )
    c.eq(
        "a folder that is not there",
        by_name(found(mod, league, at(5, 10), backups=tmp / "absent"), "backup"),
        ("backup", False, None, f"no backup in {tmp / 'absent'}"),
    )
    for label, kw in (
        ("a negative limit", {"max_wal_bytes": -1}),
        ("no slot watched", {"slots": 0}),
        ("a limit that is a truth value", {"max_checkpoint_lag": True}),
    ):
        try:
            mod.Limits(**kw)
            c.bad.append(f"{label}: was accepted")
        except mod.ProbeError as exc:
            c.eq(label, str(exc), "the limits are whole numbers of at least zero, and at least one slot is watched")
    c.eq(
        "the priors",
        (
            mod.Limits().max_wal_bytes,
            mod.Limits().min_free_bytes,
            mod.Limits().max_backup_age_s,
            mod.Limits().max_checkpoint_lag,
            mod.Limits().slots,
        ),
        (64 * 1024 * 1024, GIB, 7200, 1000, 2),
    )
    c.frozen("the limits", mod.Limits(), "slots")
    league.close()
    return c.bad


# --- the command --------------------------------------------------------------------------------------------------------------------------
def check_command(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    league = League(tmp / "league", [committed(4, 2), planned(5)], checkpoint_after=8)
    backups = tmp / "backups"
    backup.take(league.settings, backups, clock=ManualClock(at(5, -20)))

    def run(*argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mod.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    common = ("--min-free-bytes", "0", "--now", "2026-10-29T17:10:00.000Z")
    code, out, err = run(str(league.settings), "--backups", str(backups), *common)
    shown = json.loads(out) if out else {}
    c.eq("healthy: exit 0", (code, err), (0, ""))
    c.eq("one sorted line", out, json.dumps(shown, sort_keys=True) + "\n")
    c.eq(
        "ok, and the findings in order",
        (shown.get("ok"), [f["name"] for f in shown.get("findings", [])]),
        (True, ["ledger_state", "segments", "heartbeat", "checkpoint_lag", "wal_bytes", "free_bytes", "backup"]),
    )
    c.eq("a finding as written", shown.get("findings", [{}])[1], {"name": "segments", "ok": True, "value": 9, "detail": "9 records"})
    code, out, _ = run(str(league.settings), "--max-checkpoint-lag", "0", *common)
    c.eq("one finding over its limit: exit 1", (code, json.loads(out).get("ok") if out else None), (1, False))
    code, out, _ = run(
        str(league.settings), "--slots", "3", "--max-wal-bytes", "1", "--max-backup-age-s", "5", "--backups", str(backups), *common
    )
    c.eq(
        "every limit from the command line",
        [f["ok"] for f in json.loads(out)["findings"]] if out else None,
        [True, True, False, True, False, True, False],
    )
    code, _, err = run(str(league.settings), "--slots", "0")
    c.eq(
        "a limit that cannot be used: exit 2",
        (code, err),
        (2, "host_probe: the limits are whole numbers of at least zero, and at least one slot is watched\n"),
    )
    code, _, err = run(str(tmp / "missing.json"))
    c.eq("settings that cannot be read: exit 2", (code, err.startswith("host_probe: the settings cannot be read")), (2, True))
    code, _, err = run(str(league.settings), "--now", "yesterday")
    c.eq("a time that cannot be read: exit 2", (code, err.startswith("host_probe: --now is an RFC 3339 time: ")), (2, True))
    code, _, _ = run()
    c.eq("no settings: usage", code, 2)
    code, out, _ = run("--help")
    c.eq("help: exit 0", (code, out.startswith("usage: host_probe")), (0, True))
    c.eq("the usage names the program", out.startswith("usage: host_probe "), True)
    for phrase in (
        "Is the league on this host working? Read from its files alone.",
        "the folder the backups are taken into",
        "how many of the latest slots the heartbeat looks at",
        "the time to judge at (RFC 3339; default: this host's clock)",
    ):
        c.eq(f"the help says {phrase!r}", says(out, phrase), True)
    recorder = Flushes()
    with contextlib.redirect_stdout(recorder), contextlib.redirect_stderr(io.StringIO()):
        mod.main([str(league.settings), *common])
    c.eq(
        "the line is flushed once it is whole, so a pipe or the journal of a timer sees it at once",
        (recorder.seen[-1:], recorder.getvalue().endswith("}\n")),
        ([recorder.getvalue()], True),
    )
    code, out, _ = run(str(league.settings), "--min-free-bytes", "0")
    shown = json.loads(out) if out else {}
    c.eq(
        "the system clock by default: six findings, and the exit status says whether all are good",
        (len(shown.get("findings", [])), code),
        (6, 0 if shown.get("ok") else 1),
    )
    league.close()
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType, Path], list[str]]] = {
    "healthy": check_healthy,
    "heartbeat": check_heartbeat,
    "ledger": check_ledger,
    "limits_and_backups": check_limits_and_backups,
    "command": check_command,
}


def failures(mod: types.ModuleType, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        tmp = Path(tempfile.mkdtemp(prefix=f"hoshitori-probe-{name}-"))
        try:
            bad += [f"{name}: {message}" for message in scenario(mod, tmp)]
        except Exception as exc:  # a crash in the scenario is a failure of the module under test
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return bad
