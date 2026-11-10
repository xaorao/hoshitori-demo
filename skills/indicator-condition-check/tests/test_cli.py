"""The command line, offline: a careless call is refused before any venue is contacted, names the field, and shows a working call."""

from __future__ import annotations

import json
import subprocess
import sys

from indicator_condition_check.schema import EXAMPLE_CALL


def cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "indicator_condition_check", *args], capture_output=True, text=True, timeout=60)


def test_a_condition_that_is_not_json_is_refused_with_a_working_example() -> None:
    p = cli("check", "--symbol", "SOL", "--condition", "notjson")
    assert p.returncode == 2 and p.stdout == ""
    error = json.loads(p.stderr)
    assert error["code"] == "INVALID_JSON"
    assert error["message"].startswith("--condition is not valid JSON")
    assert error["details"] == {"field": "condition", "example": EXAMPLE_CALL}


def test_an_empty_condition_and_a_bad_symbol_are_refused_naming_the_field_with_an_example() -> None:
    for args, field in (
        (("--symbol", "SOL", "--condition", "{}"), "condition"),
        (("--symbol", "!!!", "--condition", '{"left":{"indicator":"rsi"},"op":"<","right":{"value":30}}'), "symbol"),
    ):
        p = cli("check", *args)
        error = json.loads(p.stderr)
        assert p.returncode == 2 and p.stdout == ""
        assert error["code"] == "INVALID_ARGUMENTS" and error["details"]["field"] == field and error["details"]["example"]


def test_a_rule_file_that_cannot_be_read_is_refused_with_a_working_example_not_a_traceback(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The README's own example is ``--condition @rule.json``: where that file is missing, a folder or not text, the refusal is the
    same shape as for a condition that is not JSON, and no Python traceback reaches a person who has just installed the skill."""
    (tmp_path / "folder").mkdir()
    (tmp_path / "binary.json").write_bytes(b"\xff\xfe\x00\x01 not text")
    for name in ("missing.json", "folder", "binary.json"):
        p = cli("check", "--symbol", "SOL", "--condition", f"@{tmp_path / name}")
        assert p.returncode == 2 and p.stdout == "", (name, p.returncode, p.stderr[-200:])
        assert "Traceback" not in p.stderr, name
        error = json.loads(p.stderr)
        assert error["code"] == "UNREADABLE_FILE", name
        assert error["message"].startswith(f"--condition names the file {tmp_path / name}, which cannot be read"), error["message"]
        assert error["details"] == {"field": "condition", "example": EXAMPLE_CALL}


def test_a_rule_file_that_is_there_is_read(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A file holding something that is not JSON is refused as a condition that is not JSON, naming the same field."""
    rule = tmp_path / "rule.json"
    rule.write_text("not json at all", encoding="utf-8")
    p = cli("check", "--symbol", "SOL", "--condition", f"@{rule}")
    assert p.returncode == 2 and json.loads(p.stderr)["code"] == "INVALID_JSON"
