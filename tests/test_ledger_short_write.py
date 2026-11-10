"""A short write or a failed write while a record is appended never leaves a damaged line in the log: the line is written in full, or the
segment is cut back to where the append began and the error is raised (a full disk must not turn an acknowledged log into one that fails
its check on the next start)."""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Any

import pytest

from hoshitori import ledger
from tests.ledger_checks import all_lines, fill, open_ledger, rec, record_ids, segments


def test_a_write_that_takes_a_few_bytes_at_a_time_still_lands_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    led = open_ledger(ledger, tmp_path / "a", records=50)
    real = os.write

    def few(fd: int, data: Any) -> int:
        return real(fd, bytes(data)[:7]) if fd == led._current_fd else real(fd, data)

    monkeypatch.setattr(ledger.os, "write", few)
    fill(led, 3)
    monkeypatch.undo()
    assert record_ids(tmp_path / "a") == ["ev0", "ev1", "ev2"]
    led.close()
    again = open_ledger(ledger, tmp_path / "a", records=50)  # a restart finds nothing to repair
    fill(again, 1, start=3)
    assert record_ids(tmp_path / "a") == ["ev0", "ev1", "ev2", "ev3"]


def test_a_disk_that_fills_part_way_leaves_the_segment_as_it_was(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    led = open_ledger(ledger, tmp_path / "b", records=50)
    fill(led, 2)
    (segment,) = segments(tmp_path / "b")
    before = segment.stat().st_size
    real = os.write
    calls = {"n": 0}

    def fills_up(fd: int, data: Any) -> int:
        if fd != led._current_fd:
            return real(fd, data)
        calls["n"] += 1
        if calls["n"] == 1:
            return real(fd, bytes(data)[: max(1, len(data) // 2)])
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(ledger.os, "write", fills_up)
    led.submit(rec(2))
    with pytest.raises(OSError, match="No space"):
        led.append_pending()
    monkeypatch.undo()
    assert segment.stat().st_size == before, "a half-written line was left in the segment"
    assert record_ids(tmp_path / "b") == ["ev0", "ev1"]
    assert all(line.endswith(b"}") or line for line in all_lines(tmp_path / "b"))
    led.append_pending()  # the record is still pending: with room again it is appended whole
    assert record_ids(tmp_path / "b") == ["ev0", "ev1", "ev2"]
    led.close()
    again = open_ledger(ledger, tmp_path / "b", records=50)
    fill(again, 1, start=3)
    assert record_ids(tmp_path / "b") == ["ev0", "ev1", "ev2", "ev3"]


def test_the_first_journal_entry_makes_its_file_durable_in_its_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file created by the first append is not durable until the directory that names it is flushed: after a power loss the whole file
    could be missing though the command was acknowledged. Only the creating append pays for it."""
    seen: list[Path] = []
    real = ledger._fsync_dir
    monkeypatch.setattr(ledger, "_fsync_dir", lambda path: (seen.append(path), real(path))[1])
    led = open_ledger(ledger, tmp_path / "c", records=50)
    seen.clear()
    led.submit(rec(0))
    assert (tmp_path / "c") in seen, "the directory of the new journal file was not flushed"
    seen.clear()
    led.submit(rec(1))
    assert (tmp_path / "c") not in seen, "a later append flushed the directory again"
