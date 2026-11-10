"""The record pack is deterministic and holds every file of the folder it packs, under its relative path."""

from __future__ import annotations

import tarfile
from pathlib import Path

from scripts.pack_record import credential_findings, pack


def test_the_same_folder_packs_to_the_same_bytes(tmp_path: Path) -> None:
    folder = tmp_path / "site"
    (folder / "bundles" / "c1").mkdir(parents=True)
    (folder / "index.json").write_text('{"format": 20}\n', encoding="utf-8")
    (folder / "bundles" / "c1" / "checkpoint").write_bytes(b"\x00\x01")
    a, b = tmp_path / "a.tar.gz", tmp_path / "b.tar.gz"
    assert pack(folder, a) == 2
    (folder / "index.json").touch()  # a new modification time changes nothing in the pack
    pack(folder, b)
    assert a.read_bytes() == b.read_bytes()
    with tarfile.open(a) as tar:
        assert tar.getnames() == ["bundles/c1/checkpoint", "index.json"]
        member = tar.getmember("index.json")
        assert member.mtime == 0 and member.uname == "" and member.mode == 0o644
        handle = tar.extractfile("bundles/c1/checkpoint")
        assert handle is not None and handle.read() == b"\x00\x01"


def test_no_forged_copy_without_a_cycle(tmp_path: Path) -> None:
    from scripts.pack_record import forge_newest

    settings = tmp_path / "settings.json"
    assert forge_newest(settings, tmp_path / "missing.json", tmp_path / "out") is None  # no cycles document
    (tmp_path / "cycles.json").write_text("[]", encoding="utf-8")
    assert forge_newest(settings, tmp_path / "cycles.json", tmp_path / "out") is None  # no cycle in it
    (tmp_path / "cycles.json").write_text("not json", encoding="utf-8")
    assert forge_newest(settings, tmp_path / "cycles.json", tmp_path / "out") is None


def test_the_newest_committed_cycle_is_forged_and_an_uncommitted_one_is_skipped(tmp_path: Path, monkeypatch: object) -> None:
    import json
    import subprocess

    from scripts import pack_record

    asked: list[list[str]] = []

    class Done:
        returncode = 0
        stderr = ""

    def fake_run(cmd: list[str], **_: object) -> Done:
        asked.append(cmd)
        return Done()

    monkeypatch.setattr(subprocess, "run", fake_run)  # type: ignore[attr-defined]
    rows = [
        {"cycle_id": "old", "committed_at": "2026-11-01T00:00:00.000Z", "state": "COMMITTED"},
        {"cycle_id": "new", "committed_at": "2026-11-02T00:00:00.000Z", "state": "COMMITTED"},
        {"cycle_id": "abandoned", "committed_at": None, "state": "ABANDONED"},
    ]
    (tmp_path / "cycles.json").write_text(json.dumps(rows), encoding="utf-8")
    assert pack_record.forge_newest(tmp_path / "s.json", tmp_path / "cycles.json", tmp_path / "out") == tmp_path / "out"
    assert asked[0][-2] == "new"


def test_a_credential_in_the_export_is_found_before_packing(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text('{"note": "nothing here"}\n', encoding="utf-8")
    assert credential_findings(tmp_path) == []
    (tmp_path / "b.json").write_text('{"k": "ryo_mcp_' + "a1" * 12 + '"}\n', encoding="utf-8")
    found = credential_findings(tmp_path)
    assert len(found) == 1 and found[0].startswith(str(tmp_path / "b.json")) and "ryo builder key" in found[0]
    assert "a1a1" not in found[0], "the finding must not print the match"
