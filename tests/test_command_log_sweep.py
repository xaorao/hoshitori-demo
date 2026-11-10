"""Every small change of ``hoshitori.command_log`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

EQUIVALENT: dict[str, str] = {
    "CommandLog._scan|int+1|0|0": "the missing-file branch returns bytes that nothing reads: append opens the file before it scans, and read ignores the count",
    "CommandLog.append|int+1|0|0": "the file is opened for appending, so every write goes to the end whatever the seek position",
    "CommandLog.append|statement dropped|fcntl.flock(handle, fcntl.LOCK_EX)|0": "the step only makes the write durable or serialised against another process; a single-process test cannot tell the difference",
    "CommandLog.append|statement dropped|fcntl.flock(handle, fcntl.LOCK_UN)|0": "the step only makes the write durable or serialised against another process; a single-process test cannot tell the difference",
    "CommandLog.append|statement dropped|handle.flush()|0": "the step only makes the write durable or serialised against another process; a single-process test cannot tell the difference",
    "CommandLog.append|statement dropped|handle.seek(0, os.SEEK_END)|0": "the file is opened for appending, so every write goes to the end",
    "CommandLog.append|statement dropped|os.fsync(handle.fileno())|0": "the step only makes the write durable or serialised against another process; a single-process test cannot tell the difference",
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.command_log", "tests.command_log_checks")
    assert total > 40, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}"
