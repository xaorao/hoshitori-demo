"""The recipe of the empty site: a league that has run no cycle exports a site that says so."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_empty_site.py"


def load() -> object:
    spec = importlib.util.spec_from_file_location("make_empty_site", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_site_of_a_league_with_no_cycle(tmp_path: Path) -> None:
    mod = load()
    out = tmp_path / "site"
    assert mod.main([str(out)]) == 0  # type: ignore[attr-defined]
    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert index["files"]
    assert not list((out / "receipts").glob("*.json")) if (out / "receipts").exists() else True
    assert not list((out / "bundles").iterdir()) if (out / "bundles").exists() else True


def test_an_existing_folder_is_refused_and_usage_is_checked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = load()
    assert mod.main([str(tmp_path)]) == 1  # type: ignore[attr-defined]
    assert mod.main([]) == 2  # type: ignore[attr-defined]
    assert "usage" in capsys.readouterr().err


def test_site_options_are_passed_on(tmp_path: Path) -> None:
    mod = load()
    status = Path(__file__).resolve().parents[1] / "docs" / "STATUS.md"
    out = tmp_path / "site"
    assert mod.main([str(out), "--status", str(status)]) == 0  # type: ignore[attr-defined]
    assert (out / "quality.json").is_file()


def test_recorded_challenges_are_added_with_their_digest(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("add_recorded_challenges", SCRIPT.with_name("add_recorded_challenges.py"))
    assert spec is not None and spec.loader is not None
    adder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adder)
    site = tmp_path / "site"
    assert load().main([str(site)]) == 0  # type: ignore[attr-defined]
    assert adder.add(site) == 3
    body = (site / "model_challenges.json").read_bytes()
    index = json.loads((site / "index.json").read_text(encoding="utf-8"))
    assert index["files"]["model_challenges.json"] == hashlib.sha256(body).hexdigest()
    assert len(json.loads(body)) == 3
    assert adder.main([]) == 2
