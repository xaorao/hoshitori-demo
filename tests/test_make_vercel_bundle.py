"""The Vercel bundle carries the site, the record under data/ and the security headers of the policy file, and refuses a wrong input."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from scripts import make_vercel_bundle as mod


def folders(tmp_path: Path) -> tuple[Path, Path]:
    dist, record = tmp_path / "dist", tmp_path / "record"
    dist.mkdir()
    record.mkdir()
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (dist / "app.js").write_text("1", encoding="utf-8")
    (record / "index.json").write_text("{}", encoding="utf-8")
    return dist, record


def test_the_bundle_holds_the_site_the_record_and_the_policy_headers(tmp_path: Path) -> None:
    dist, record = folders(tmp_path)
    out = tmp_path / "out"
    assert mod.assemble(dist, record, out) == 4
    assert (out / "index.html").is_file() and (out / "data" / "index.json").is_file()
    config = json.loads((out / "vercel.json").read_text(encoding="utf-8"))
    policy = json.loads(mod.HEADERS.read_text(encoding="utf-8"))
    (rule,) = config["headers"]
    assert rule["source"] == "/(.*)"
    assert {h["key"]: h["value"] for h in rule["headers"]} == policy
    assert "script-src 'self'" in policy["Content-Security-Policy"] and "unsafe-inline" not in policy["Content-Security-Policy"]
    assert config["buildCommand"] is None and config["outputDirectory"] == "."


def test_wrong_inputs_are_refused(tmp_path: Path) -> None:
    dist, record = folders(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(SystemExit, match="exists already"):
        mod.assemble(dist, record, out)
    out.rmdir()
    (dist / "data").mkdir()
    with pytest.raises(SystemExit, match="without data"):
        mod.assemble(dist, record, out)
    (dist / "data").rmdir()
    (record / "index.json").unlink()
    with pytest.raises(SystemExit, match="not an exported site"):
        mod.assemble(dist, record, out)
    (dist / "index.html").unlink()
    with pytest.raises(SystemExit, match="no built interface"):
        mod.assemble(dist, record, out)


def test_the_command_line_needs_three_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    assert mod.main(["a", "b"]) == 2
    assert "usage" in capsys.readouterr().err


def exported(tmp_path: Path, cycles: list[str], forged: str | None) -> tuple[Path, Path]:
    dist, record = folders(tmp_path)
    listed = []
    for cid in cycles:
        (record / "bundles" / cid).mkdir(parents=True)
        (record / "bundles" / cid / "a.json").write_text("{}", encoding="utf-8")
        listed.append({"cycle_id": cid, "path": f"bundles/{cid}", "files": ["a.json"], "directories": []})
    (record / "bundles.json").write_bytes(mod.canonical(listed))
    (record / "index.json").write_bytes(mod.canonical({"counts": {"bundles": len(cycles)}, "files": {"bundles.json": "old"}}))
    if forged is not None:
        (record / "forgery.json").write_text(json.dumps({"cycle_id": forged}), encoding="utf-8")
    return dist, record


def test_only_the_newest_bundles_and_the_forged_ones_cycle_are_hosted(tmp_path: Path) -> None:
    dist, record = exported(tmp_path, ["c1", "c2", "c3", "c4"], forged="c1")
    out = tmp_path / "out"
    mod.assemble(dist, record, out, keep_bundles=2)
    data = out / "data"
    assert sorted(p.name for p in (data / "bundles").iterdir()) == ["c1", "c3", "c4"]
    listed = json.loads((data / "bundles.json").read_text(encoding="utf-8"))
    assert [b["cycle_id"] for b in listed] == ["c1", "c3", "c4"]
    index = json.loads((data / "index.json").read_text(encoding="utf-8"))
    assert index["counts"]["bundles"] == 3
    assert index["files"]["bundles.json"] == hashlib.sha256((data / "bundles.json").read_bytes()).hexdigest()


def test_without_a_forged_copy_only_the_newest_remain_and_zero_is_refused(tmp_path: Path) -> None:
    dist, record = exported(tmp_path, ["c1", "c2", "c3"], forged=None)
    out = tmp_path / "out"
    mod.assemble(dist, record, out, keep_bundles=1)
    assert [p.name for p in (out / "data" / "bundles").iterdir()] == ["c3"]
    (tmp_path / "again").mkdir()
    dist2, record2 = exported(tmp_path / "again", ["c1"], forged=None)
    with pytest.raises(SystemExit, match="at least 1"):
        mod.assemble(dist2, record2, tmp_path / "out2", keep_bundles=0)


def test_the_option_is_read_from_the_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dist, record = exported(tmp_path, ["c1", "c2"], forged=None)
    out = tmp_path / "out"
    assert mod.main([str(dist), str(record), str(out), "--keep-bundles", "1"]) == 0
    assert [p.name for p in (out / "data" / "bundles").iterdir()] == ["c2"]
    assert mod.main([str(dist), str(record), str(tmp_path / "x"), "--keep-bundles"]) == 2
    assert "whole number" in capsys.readouterr().err
