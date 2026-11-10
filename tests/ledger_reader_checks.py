"""Scenarios for the read-only ledger reader (``hoshitori.ledger_reader``), runnable against the real module or a mutant of it.

The reader is what ``status``, ``site``, ``export`` and ``replay`` open instead of the writer, so that they run beside a running league and
write nothing. What it must do, worked out by hand:

* give what the writer holds: the writer is the other implementation here, so size, root, records, proofs and lookups are compared with its
  own answers for a ledger it made (nine records in three segments of three, the last checkpoint at nine, a tenth command acknowledged and
  not yet appended: the reader does not see it);
* write nothing: a snapshot of every file (name, size, modification time, SHA-256) before and after is the same, and no file appears;
* see a consistent prefix of a log a writer is writing: bytes after the last newline are a line being written, and a newest segment whose
  first line is not whole is a rotation just begun; neither is a record, and neither is a failure;
* report a log that is not clean with the writer's own words, and then hold no record, as the writer's degraded view does;
* check the signed checkpoints under the trusted root when it is given (the reader has no key; the root's era keys are enough), and say
  so when it is not given: then the checkpoints are not read.

Not covered by the reader, and said so in its docstring: the attestation file and the index. Neither is needed to read records.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import tempfile
import types
from collections.abc import Callable
from pathlib import Path

from hoshitori import canonical
from hoshitori.checkpoint import Checkpoint, sign_checkpoint
from hoshitori.ledger import Ledger, LedgerError
from tests.backup_checks import OTHER_SIGNER, ROOT, SIGNER, SOURCE, open_ledger, rec

SEGMENT_0 = "segment-000000000000.jsonl"
SEGMENT_3 = "segment-000000000003.jsonl"
SEGMENT_6 = "segment-000000000006.jsonl"
EMPTY_ROOT = hashlib.sha256(b"").digest()


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def opens_with(self, label: str, directory: Path, state: str, detail: str, *, root: object = ROOT, prefix: bool = False) -> object:
        """Open the reader; it must hold the state ``state`` with the detail ``detail`` (a start, with ``prefix``); the reader is returned."""
        try:
            reader = self.mod.LedgerReader.open(directory, root=root)
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return None
        said = reader.state_detail
        if reader.state != state or (not said.startswith(detail) if prefix else said != detail):
            self.bad.append(f"{label}: the reader is in {reader.state!r} saying {said!r}, expected {state!r} saying {detail!r}")
        return reader

    def refuses(self, label: str, directory: Path, says: str, **kw: object) -> None:
        try:
            self.mod.LedgerReader.open(directory, **kw)
        except LedgerError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def make(tmp: Path, name: str = "ledger") -> tuple[Ledger, Path]:
    """A ledger of nine records in three segments, a checkpoint at nine, and a tenth command acknowledged but not appended. The writer is
    left open: it holds the lock and the index, as a running league does."""
    directory = tmp / name
    led = open_ledger(directory)
    for i in range(9):
        led.submit(rec(i))
    led.append_pending()
    led.checkpoint()
    led.submit(rec(9))
    return led, directory


def snapshot(directory: Path) -> dict[str, tuple[int, int, str]]:
    """Every file under the directory: size, modification time and SHA-256."""
    found: dict[str, tuple[int, int, str]] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            info = path.stat()
            found[path.relative_to(directory).as_posix()] = (info.st_size, info.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
    return found


def note_line(size: int, root: bytes, signer: object = SIGNER) -> bytes:
    note = sign_checkpoint(Checkpoint(SIGNER.name, size, root), [signer])  # type: ignore[list-item]
    return canonical.dumps({"note": base64.b64encode(note).decode("ascii")}) + b"\n"


def check_the_writers_ledger(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    led, directory = make(tmp)
    reader = c.opens_with("a ledger the writer holds open", directory, "OK", "")
    if reader is None:
        led.abandon()
        return c.bad
    c.eq("the size: nine records, not the tenth command", reader.size, 9)
    c.eq("the root is the writer's", reader.root(), led.root())
    c.eq("and of a smaller size", reader.root(4), led.root(4))
    for seq in range(9):
        c.eq(f"record {seq}", reader.read_record(seq), led.read_record(seq))
    c.eq("and no record nine", _raises(lambda: reader.read_record(9)), "LedgerError: no record 9")
    c.eq("a lookup the writer answers", [reader.find(SOURCE, f"ev{i}") for i in (0, 4, 8)], [0, 4, 8])
    c.eq("the acknowledged command is not yet a record", reader.find(SOURCE, "ev9"), None)
    c.eq("nor is a record of another source", reader.find("urn:other", "ev0"), None)
    c.eq("an inclusion proof", reader.inclusion_proof(4, 9), led.inclusion_proof(4, 9))
    c.eq("an inclusion proof against a smaller tree", reader.inclusion_proof(2, 5), led.inclusion_proof(2, 5))
    c.eq("a consistency proof", reader.consistency_proof(3, 9), led.consistency_proof(3, 9))
    c.eq("a leaf hash", reader.leaf_hash_at(5), led.leaf_hash_at(5))
    c.eq(
        "a reader signs no checkpoint",
        _raises(reader.checkpoint),
        "LedgerError: a read-only ledger signs no checkpoint: give the bundle the checkpoint note to use",
    )
    led.abandon()
    return c.bad


def _raises(call: Callable[[], object]) -> str:
    try:
        call()
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return "nothing"


def check_it_writes_nothing(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    led, directory = make(tmp)
    before = snapshot(directory)
    reader = mod.LedgerReader.open(directory, root=ROOT)
    reader.root()
    reader.find(SOURCE, "ev3")
    for seq in range(reader.size):
        reader.read_record(seq)
        reader.inclusion_proof(seq)
    reader.consistency_proof(3, 9)
    reader.leaf_hash_at(0)
    after = snapshot(directory)
    c.eq("the files are the same, byte for byte, in size, time and number", after == before, True)
    c.eq("no lock file was made or taken: the writer can still append", _append(led), None)
    led.abandon()
    # a ledger that is not clean is read, and still nothing is written
    broken = tmp / "broken"
    shutil.copytree(directory, broken)
    (broken / "segments" / SEGMENT_6).write_bytes(b"not a segment")
    before = snapshot(broken)
    again = mod.LedgerReader.open(broken, root=ROOT)
    c.eq("a failure found while reading is not saved into STATE", (snapshot(broken) == before, again.state), (True, "INTEGRITY_FAILURE"))
    return c.bad


def _append(led: Ledger) -> str | None:
    try:
        led.append_pending()
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def check_a_log_being_written(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    led, directory = make(tmp)
    led.abandon()
    segments = directory / "segments"
    with open(segments / SEGMENT_6, "ab") as handle:
        handle.write(b'{"leaf":"00","rec":"a line being writ')  # no newline: the writer is part-way through it
    reader = c.opens_with("a line being written", directory, "OK", "")
    c.eq("it is not a record", None if reader is None else reader.size, 9)
    (segments / SEGMENT_6).write_bytes((segments / SEGMENT_6).read_bytes().split(b'{"leaf":"00"')[0])
    for label, content in (("empty", b""), ("half a manifest", b'{"manifest":{"rec')):
        (segments / "segment-000000000009.jsonl").write_bytes(content)
        reader = c.opens_with(f"a rotation just begun ({label})", directory, "OK", "")
        c.eq(f"({label}) its records are the nine", None if reader is None else (reader.size, reader.root()), (9, led.root()))
    (segments / "segment-000000000009.jsonl").unlink()
    short = open_ledger(tmp / "short")
    for i in range(3):
        short.submit(rec(i))
    short.append_pending()  # exactly one full segment
    short.abandon()
    (tmp / "short" / "segments" / "segment-000000000003.jsonl").write_bytes(b"")
    reader = c.opens_with("a rotation begun after the first segment", tmp / "short", "OK", "")
    c.eq("its records are the three", None if reader is None else reader.size, 3)
    with open(directory / "checkpoints.jsonl", "ab") as handle:
        handle.write(b'{"note":"a checkpoint being writ')  # the writer is part-way through the next checkpoint
    reader = c.opens_with("a checkpoint line being written", directory, "OK", "")
    c.eq("it is not read, and the records are still nine", None if reader is None else reader.size, 9)
    return c.bad


def check_a_log_that_is_not_clean(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    led, clean = make(tmp, "clean")
    led.abandon()

    def damaged(name: str, change: Callable[[Path], None]) -> Path:
        copy = tmp / name
        shutil.copytree(clean, copy)
        change(copy / "segments")
        return copy

    def edit(segment: str, line: int, old: bytes, new: bytes) -> Callable[[Path], None]:
        def go(segments: Path) -> None:
            lines = (segments / segment).read_bytes().splitlines(keepends=True)
            assert old in lines[line]
            lines[line] = lines[line].replace(old, new)
            (segments / segment).write_bytes(b"".join(lines))

        return go

    closed = damaged("closed", edit(SEGMENT_0, 0, b'"seq":0}', b'"seq":5}'))
    reader = c.opens_with(
        "a closed segment with a wrong sequence number",
        closed,
        "INTEGRITY_FAILURE",
        f"closed segment {SEGMENT_0}: sequence number 5, expected 0 (offset 0)",
    )
    if reader is not None:
        c.eq("it then holds no record", (reader.size, reader.root(), reader.find(SOURCE, "ev0")), (0, EMPTY_ROOT, None))
        c.eq("and no record can be read", _raises(lambda: reader.read_record(0)), "LedgerError: no record 0")
    last = damaged("last", edit(SEGMENT_6, 3, b'"seq":8}', b'"seq":9}'))
    c.opens_with(
        "a complete final line that does not check",
        last,
        "INTEGRITY_FAILURE",
        f"segment {SEGMENT_6}: line 8 is complete but invalid (sequence number 9, expected 8)",
    )
    middle = damaged("middle", edit(SEGMENT_6, 2, b'"seq":7}', b'"seq":9}'))
    lines = (clean / "segments" / SEGMENT_6).read_bytes().splitlines(keepends=True)
    c.opens_with(
        "a damaged line with more lines after it in the newest segment",
        middle,
        "INTEGRITY_FAILURE",
        f"segment {SEGMENT_6}: sequence number 9, expected 7 at offset {len(lines[0]) + len(lines[1])}",
    )
    gap = damaged("gap", lambda segments: (segments / SEGMENT_3).unlink())
    c.opens_with("a segment missing between two", gap, "INTEGRITY_FAILURE", f"segment {SEGMENT_6} starts at 6, expected 3")
    foreign = damaged("foreign", lambda segments: (segments / "notes.txt").write_bytes(b"x"))
    c.opens_with("a file that is not a segment", foreign, "INTEGRITY_FAILURE", "unexpected file in the segment directory: notes.txt")
    temporary = damaged("temporary", lambda segments: (segments / "segment-000000000009.jsonl.tmp").write_bytes(b"x"))
    c.opens_with("a temporary file is not a segment", temporary, "OK", "")
    return c.bad


def check_the_state_file(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    led, directory = make(tmp)
    led.abandon()
    (directory / "STATE").write_text(json.dumps({"state": "INTEGRITY_FAILURE", "detail": "segment x: damaged"}))
    reader = c.opens_with("a state the writer saved", directory, "INTEGRITY_FAILURE", "segment x: damaged")
    c.eq(
        "then it holds nothing, as the writer's degraded view does",
        None if reader is None else (reader.size, reader.find(SOURCE, "ev0")),
        (0, None),
    )
    (directory / "STATE").write_text("not json")
    c.opens_with("a STATE file that cannot be read", directory, "INTEGRITY_FAILURE", "the STATE file cannot be read")
    (directory / "STATE").write_text(json.dumps({"state": "OK"}))
    c.opens_with("a saved OK with no detail", directory, "OK", "")
    return c.bad


def check_the_format(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    led, directory = make(tmp)
    led.abandon()
    c.opens_with("no FORMAT file: a ledger written before the file existed is version 1", directory, "OK", "")
    for text in (b"hoshitori-ledger/1\n", b"hoshitori-ledger/1"):
        (directory / "FORMAT").write_bytes(text)
        c.opens_with(f"FORMAT {text!r}", directory, "OK", "")
    (directory / "FORMAT").write_bytes(b"hoshitori-ledger/9\n")
    c.refuses(
        "another format",
        directory,
        "the ledger directory is of format 'hoshitori-ledger/9'; this code reads 'hoshitori-ledger/1'",
        root=ROOT,
    )
    (directory / "FORMAT").write_bytes(b"hoshitori-ledger/1~")
    c.refuses(
        "a format that only ends like ours",
        directory,
        "the ledger directory is of format 'hoshitori-ledger/1~'; this code reads 'hoshitori-ledger/1'",
        root=ROOT,
    )
    (directory / "FORMAT").write_bytes(b"")
    c.refuses("an empty FORMAT file", directory, "the ledger directory is of format ''; this code reads 'hoshitori-ledger/1'", root=ROOT)
    return c.bad


def check_no_ledger_and_an_empty_one(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    c.refuses("a folder with no ledger", tmp / "absent", f"there is no ledger in {tmp / 'absent'}", root=ROOT)
    (tmp / "empty" / "segments").mkdir(parents=True)
    reader = c.opens_with("a ledger that has no segment yet", tmp / "empty", "OK", "")
    c.eq("it holds nothing", None if reader is None else (reader.size, reader.root()), (0, EMPTY_ROOT))
    (tmp / "empty" / "segments" / SEGMENT_0).write_bytes(b"")
    reader = c.opens_with("a ledger with an empty first segment", tmp / "empty", "OK", "")
    c.eq("it holds nothing either", None if reader is None else (reader.size, reader.root()), (0, EMPTY_ROOT))
    return c.bad


def check_the_signed_checkpoints(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    led, clean = make(tmp, "clean")
    root3, root6, root9 = led.root(3), led.root(6), led.root(9)
    led.abandon()

    def with_checkpoints(name: str, *lines: bytes) -> Path:
        copy = tmp / name
        shutil.copytree(clean, copy)
        (copy / "checkpoints.jsonl").write_bytes(b"".join(lines))
        return copy

    c.opens_with("the checkpoint the writer signed", clean, "OK", "")
    c.opens_with("two valid checkpoints, the size growing", with_checkpoints("two", note_line(3, root3), note_line(6, root6)), "OK", "")
    c.opens_with("two checkpoints of the same size", with_checkpoints("same", note_line(3, root3), note_line(3, root3)), "OK", "")
    c.opens_with("a checkpoint of the empty log", with_checkpoints("zero", note_line(0, EMPTY_ROOT)), "OK", "")
    stray = (
        canonical.dumps({"note": "!" + base64.b64encode(sign_checkpoint(Checkpoint(SIGNER.name, 3, root3), [SIGNER])).decode("ascii")})
        + b"\n"
    )
    c.opens_with(
        "a note with a character that is not base64",
        with_checkpoints("stray", stray),
        "INTEGRITY_FAILURE",
        "checkpoints.jsonl line 1 is not a valid signed checkpoint: ",
        prefix=True,
    )
    c.opens_with(
        "sizes that decrease",
        with_checkpoints("down", note_line(6, root6), note_line(3, root3)),
        "INTEGRITY_FAILURE",
        "checkpoint sizes decrease at line 2",
    )
    c.opens_with(
        "a checkpoint of a root the records do not have",
        with_checkpoints("root", note_line(3, b"\x01" * 32)),
        "INTEGRITY_FAILURE",
        "checkpoint of size 3 does not match the records: the prefix was rewritten",
    )
    c.opens_with(
        "a checkpoint signed by another key",
        with_checkpoints("key", note_line(3, root3, OTHER_SIGNER)),
        "INTEGRITY_FAILURE",
        "checkpoints.jsonl line 1 is not a valid signed checkpoint: ",
        prefix=True,
    )
    c.opens_with(
        "a line that is not a checkpoint",
        with_checkpoints("junk", b"not json\n"),
        "INTEGRITY_FAILURE",
        "checkpoints.jsonl line 1 is not a valid signed checkpoint: ",
        prefix=True,
    )
    rolled = tmp / "rolled"
    shutil.copytree(clean, rolled)
    (rolled / "segments" / SEGMENT_6).unlink()
    failed = c.opens_with(
        "records lost behind a signed checkpoint",
        rolled,
        "INTEGRITY_FAILURE",
        "checkpoint of size 9 exceeds the 6 records found: the log was rolled back",
    )
    c.eq(
        "a failure found after the segments were read leaves nothing to read",
        None if failed is None else (failed.size, failed.root(), failed.find(SOURCE, "ev0")),
        (0, EMPTY_ROOT, None),
    )
    unchecked = c.opens_with("the same log read with no trusted root: the checkpoints are not read", rolled, "OK", "", root=None)
    c.eq("and it holds the six records", None if unchecked is None else (unchecked.size, unchecked.root()), (6, led.root(6)))
    c.eq("the root a checkpoint carries is the tree's own", root9 == led.root(9), True)
    return c.bad


def check_a_writer_that_writes_while_it_reads(mod: types.ModuleType, tmp: Path) -> list[str]:
    """A checkpoint read is covered by the records read after it, because the checkpoints are read first: if the writer appends three
    records and signs a checkpoint of twelve between the two reads, the reader holds twelve records and a checkpoint of nine (not a log
    rolled back). Read the other way round it would hold nine records and a checkpoint of twelve."""
    c = Case(mod)
    led, directory = make(tmp)
    led.append_pending()  # the tenth command: ten records, and a checkpoint of nine
    for i in range(10, 12):
        led.submit(rec(i))
    fired: list[int] = []

    def writes_once(real: Callable[..., object]) -> Callable[..., object]:
        def wrapped(*args: object, **kw: object) -> object:
            result = real(*args, **kw)
            if not fired:
                fired.append(1)
                led.append_pending()
                led.checkpoint()
            return result

        return wrapped

    reader_class = mod.LedgerReader
    real_lines, real_scan = reader_class.__dict__["_complete_lines"], reader_class._scan_segments
    reader_class._complete_lines = staticmethod(writes_once(real_lines.__func__))
    reader_class._scan_segments = writes_once(real_scan)  # type: ignore[method-assign]
    try:
        reader = mod.LedgerReader.open(directory, root=ROOT)
    finally:
        reader_class._complete_lines = real_lines
        reader_class._scan_segments = real_scan  # type: ignore[method-assign]
    c.eq("the writer wrote between the two reads", fired, [1])
    c.eq("the reader is not in failure", (reader.state, reader.state_detail), ("OK", ""))
    c.eq("it holds the twelve records, covered by the checkpoint of nine it read first", reader.size, 12)
    led.abandon()
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType, Path], list[str]]] = {
    "the_writers_ledger": check_the_writers_ledger,
    "it_writes_nothing": check_it_writes_nothing,
    "a_log_being_written": check_a_log_being_written,
    "a_log_that_is_not_clean": check_a_log_that_is_not_clean,
    "the_state_file": check_the_state_file,
    "the_format": check_the_format,
    "no_ledger_and_an_empty_one": check_no_ledger_and_an_empty_one,
    "the_signed_checkpoints": check_the_signed_checkpoints,
    "a_writer_that_writes_while_it_reads": check_a_writer_that_writes_while_it_reads,
}


def failures(mod: types.ModuleType, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        tmp = Path(tempfile.mkdtemp(prefix=f"hoshitori-ledger-reader-{name}-"))
        try:
            bad += [f"{name}: {message}" for message in scenario(mod, tmp)]
        except Exception as exc:  # a crash in the scenario is a failure of the module under test
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return bad
