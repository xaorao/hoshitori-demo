"""The command log: an append-only, hash-chained file of the commands a state machine was given, replayed to rebuild it."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hoshitori import command_log as cl


def make(tmp_path: Path) -> cl.CommandLog:
    return cl.CommandLog(tmp_path / "log.jsonl")


def test_commands_are_kept_in_order_with_a_chain_of_hashes(tmp_path: Path) -> None:
    log = make(tmp_path)
    assert log.read() == []
    assert log.append({"op": "a", "n": 1}) == 0 and log.append({"op": "b", "n": 2}) == 1
    assert log.read() == [{"op": "a", "n": 1}, {"op": "b", "n": 2}]
    lines = [json.loads(line) for line in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert [x["seq"] for x in lines] == [0, 1] and lines[0]["prev"] == "0" * 64 and lines[1]["prev"] == lines[0]["hash"]
    want = hashlib.sha256(("0" * 64 + json.dumps({"op": "a", "n": 1}, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()
    assert lines[0]["hash"] == want
    assert make(tmp_path).append({"op": "c"}) == 2  # a second handle continues the chain


def test_an_edited_removed_or_reordered_line_is_found(tmp_path: Path) -> None:
    log = make(tmp_path)
    for n in range(4):
        log.append({"op": "x", "n": n})
    path = tmp_path / "log.jsonl"
    original = path.read_text()
    lines = original.splitlines()
    path.write_text(original.replace('"n":1', '"n":9'))
    with pytest.raises(cl.LogError, match="line 2: the hash does not match its command"):
        make(tmp_path).read()
    path.write_text("\n".join([lines[0], lines[2], lines[3]]) + "\n")
    with pytest.raises(cl.LogError, match="line 2: the chain is broken"):
        make(tmp_path).read()
    path.write_text("\n".join([lines[0], lines[2], lines[1], lines[3]]) + "\n")
    with pytest.raises(cl.LogError, match="line 2: the chain is broken"):
        make(tmp_path).read()
    path.write_text("\n".join(lines[:3]) + "\n")
    assert len(make(tmp_path).read()) == 3  # a shorter log is still a valid log; the file's own head is what a caller pins
    path.write_text(original)
    assert len(make(tmp_path).read()) == 4


def test_a_torn_last_line_is_ignored_and_cut_off_by_the_next_append(tmp_path: Path) -> None:
    log = make(tmp_path)
    log.append({"op": "a"})
    log.append({"op": "b"})
    path = tmp_path / "log.jsonl"
    path.write_text(path.read_text() + '{"seq":2,"prev":"abc","comm')
    assert make(tmp_path).read() == [{"op": "a"}, {"op": "b"}]
    assert make(tmp_path).append({"op": "c"}) == 2
    assert make(tmp_path).read() == [{"op": "a"}, {"op": "b"}, {"op": "c"}]
    assert path.read_text().count("\n") == 3 and path.read_text().endswith("\n")


def test_garbage_in_the_middle_is_refused_not_skipped(tmp_path: Path) -> None:
    log = make(tmp_path)
    log.append({"op": "a"})
    log.append({"op": "b"})
    path = tmp_path / "log.jsonl"
    lines = path.read_text().splitlines()
    path.write_text(lines[0] + "\nnot json\n" + lines[1] + "\n")
    with pytest.raises(cl.LogError, match="line 2: not a log line"):
        make(tmp_path).read()
    path.write_text(lines[0].replace('"seq":0', '"seq":5') + "\n")
    with pytest.raises(cl.LogError, match="line 1: the sequence number is 5, expected 0"):
        make(tmp_path).read()
    path.write_text(json.dumps({"seq": 0, "prev": "0" * 64}) + "\n")
    with pytest.raises(cl.LogError, match="line 1: not a log line"):
        make(tmp_path).read()


def test_a_command_is_a_json_object_and_the_head_is_the_last_hash(tmp_path: Path) -> None:
    log = make(tmp_path)
    with pytest.raises(cl.LogError, match="a command is a JSON object"):
        log.append([1, 2])  # type: ignore[arg-type]
    assert log.head() == "0" * 64
    log.append({"op": "a"})
    first = log.head()
    log.append({"op": "b"})
    assert (
        first != log.head()
        and len(log.head()) == 64
        and log.head() == json.loads((tmp_path / "log.jsonl").read_text().splitlines()[-1])["hash"]
    )


def test_the_log_makes_its_own_folder(tmp_path: Path) -> None:
    log = cl.CommandLog(tmp_path / "a" / "b" / "log.jsonl")
    assert log.append({"op": "x"}) == 0 and log.read() == [{"op": "x"}]
    assert cl.CommandLog(tmp_path / "a" / "b" / "log.jsonl").append({"op": "y"}) == 1  # a folder that exists is not an error
