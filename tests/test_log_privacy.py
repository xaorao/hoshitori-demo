"""What the league's commands print must not hold the log signing key.

An operator runs the league under a service manager that keeps everything it prints in a journal, and sends that journal to others when
something is wrong. The commands print JSON results and one-line errors; none may carry the key (the security model, section 2: a key in a
log is an incident). Each command is run against a loopback league, and its output, including the output of the failures an operator can
cause (a wrong key file, a missing file, a key that others can read), is searched for the key.
"""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path

from hoshitori import league_cli
from tests.deployment_checks import world_files
from tests.e2e_league import OffsetClock, outside


def run(*argv: str) -> str:
    """Everything a command prints, standard output and error together."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), contextlib.suppress(SystemExit):
        league_cli.main(list(argv))
    return out.getvalue() + err.getvalue()


def test_no_command_prints_the_log_key(tmp_path: Path) -> None:
    with outside(OffsetClock()) as world:
        directory = tmp_path / "league"
        directory.mkdir()
        settings = world_files(directory, world)
        key_text = (directory / "log.key").read_text(encoding="utf-8").strip()
        assert len(key_text) >= 32, "the key file is not what this test thinks it is"
        printed = [
            run("status", str(settings)),
            run("run", str(settings), "--once"),
            run("status", str(settings)),
            run("site", str(settings), str(tmp_path / "site")),
        ]
        wrong = tmp_path / "wrong.key"
        wrong.write_text(key_text + "\n", encoding="utf-8")
        os.chmod(wrong, 0o644)  # readable by others: refused, and the refusal must not print what it refused to read
        other = tmp_path / "other"
        other.mkdir()
        bad_settings = world_files(other, world)
        (other / "log.key").write_bytes(wrong.read_bytes())
        os.chmod(other / "log.key", 0o644)
        printed.append(run("status", str(bad_settings)))
        printed.append(run("run", str(bad_settings), "--once"))
        printed.append(run("status", str(tmp_path / "nowhere.json")))
    assert sum(len(p) for p in printed) > 0, "the commands printed nothing: the search looked at too little"
    assert any('"' in p for p in printed), "no command printed a result"
    assert any("can be read by others" in p for p in printed), "the refusal of a readable key was not exercised"
    for text in printed:
        assert key_text not in text
        assert key_text[:16] not in text
