"""Backup of a league folder: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

import codecs
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

from hoshitori import backup
from tests.backup_checks import SCENARIOS, failures
from tests.mutation_sweep import survivors


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str, tmp_path: Path) -> None:
    assert SCENARIOS[name](backup, tmp_path) == []


def test_real_module_passes_every_check() -> None:
    assert failures(backup) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
# Where the reason rests on a fact about Python or SQLite, a test below states that fact and runs it.
_ENTRY_GUARD = "the entry guard cannot run inside a sweep (the mutant's module is never named __main__); test_the_module_runs_as_a_program runs the real one"
_READ_SIZE = (
    "CHUNK is only the size of one read: both loops that use it read to the end of the file, so any size copies and hashes the same bytes"
)
_UTF_8 = "the codec name is looked up after normalising it, and 'utf-8~' is the same codec as 'utf-8' (test_the_facts_the_harmless_changes_rest_on)"
EQUIVALENT: dict[str, str] = {
    "_Copy.put_index|int+1|1|0": (
        "pages=-2 instead of -1: any number of pages below one copies the whole database in one step, with one call of the progress "
        "callback (test_the_facts_the_harmless_changes_rest_on)"
    ),
    "_index|str+~|'mode=ro&immutable=1'|0": (
        "SQLite reads the value of a flag as a number and ignores what follows it, so 'immutable=1~' is 'immutable=1': the index is read "
        "from a backup nothing writes to, in the same way (test_the_facts_the_harmless_changes_rest_on)"
    ),
    "_relative|str+~|'/'|0": (
        "a text that starts with '/' has an empty first part, which the test for an empty, '.' or '..' part on the same line refuses; the "
        "separate test for a leading '/' is only a second way to the same refusal"
    ),
    "_sqlite_uri|str+~|'/'|0": (
        "quote() never escapes '~', so adding it to the characters kept as they are changes no URI (test_the_facts_the_harmless_changes_rest_on)"
    ),
    "listing|sorted removed|sorted(directory.iterdir())|0": (
        "every child is looked at on its own and the list is sorted again by time before it is returned, so the order of the children "
        "does not reach the result"
    ),
    "listing|statement dropped|continue|1": "the 'continue' is the last statement of the loop body, so leaving it out leaves the loop as it was",
    "take|sorted removed|sorted(saved.tokens.items())|0": (
        "only the order the tokens are copied in: the manifest lists its files sorted by path and the commitments are held as a set"
    ),
    "take|str+~|'utf-8'|0": _UTF_8,
    "verify|str+~|'utf-8'|0": _UTF_8,
    "|int+1|1|0": _READ_SIZE,
    "|int+1|20|0": _READ_SIZE,
    "|statement dropped|raise SystemExit(main())|0": _ENTRY_GUARD,
    "|str+~|'__main__'|0": _ENTRY_GUARD,
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.backup", "tests.backup_checks")
    assert total > 800, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


def test_the_facts_the_harmless_changes_rest_on(tmp_path: Path) -> None:
    assert codecs.lookup("utf-8~").name == codecs.lookup("utf-8").name
    assert quote("/a/~b c", safe="/~") == quote("/a/~b c", safe="/")

    source = sqlite3.connect(tmp_path / "source.db")
    source.execute("CREATE TABLE t(x)")
    source.executemany("INSERT INTO t VALUES(?)", [(bytes([number % 251]) * 500,) for number in range(300)])
    source.commit()
    copied: dict[int, tuple[int, tuple[int, ...]]] = {}
    for pages in (-1, -2):
        calls: list[int] = []

        def progress(_status: int, left: int, _total: int, calls: list[int] = calls) -> None:
            calls.append(left)

        copy = sqlite3.connect(tmp_path / f"copy{pages}.db")
        source.backup(copy, pages=pages, progress=progress)
        copied[pages] = (len(calls), copy.execute("SELECT count(*) FROM t").fetchone())
        copy.close()
    source.close()
    assert copied[-1] == copied[-2] == (1, (300,))

    locked = tmp_path / "locked.db"
    writer = sqlite3.connect(locked, isolation_level=None, timeout=0)
    writer.execute("CREATE TABLE t(x)")
    writer.execute("INSERT INTO t VALUES(1)")
    writer.execute("BEGIN EXCLUSIVE")
    writer.execute("INSERT INTO t VALUES(2)")
    read: dict[str, tuple[int, ...]] = {}
    try:
        for query in ("mode=ro&immutable=1", "mode=ro&immutable=1~"):
            reader = sqlite3.connect(backup._sqlite_uri(locked, query), uri=True, timeout=0)
            try:
                read[query] = reader.execute("SELECT count(*) FROM t").fetchone()
            finally:
                reader.close()
    finally:
        writer.execute("ROLLBACK")
        writer.close()
    assert read["mode=ro&immutable=1"] == read["mode=ro&immutable=1~"] == (1,)  # the file as it was before the writer began


def test_the_module_runs_as_a_program(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "hoshitori.backup", "verify", str(tmp_path / "nothing"), "--trusted-root", str(tmp_path / "missing.json")],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        check=False,
    )
    assert (done.returncode, done.stdout, done.stderr) == (
        2,
        "",
        "hoshitori-backup: the trusted root cannot be read: No such file or directory\n",
    )
