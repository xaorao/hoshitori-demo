"""The shipped schema pin: it loads, it matches the synthetic and the live catalogues in structure, and its generator reproduces it."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from hoshitori import ryo_catalogue
from hoshitori.ryo_pin import PIN
from tests.ryo_support import SIX, catalogue_body, live_fixture

ROOT = Path(__file__).resolve().parents[1]


def test_the_pin_loads_and_names_the_six_tools() -> None:
    pinned = ryo_catalogue.load_pin(PIN)
    assert pinned.names() == frozenset(SIX)
    assert all(spec.description == "" for spec in pinned.tools.values())


def test_the_synthetic_catalogue_has_the_structure_of_the_pin() -> None:
    assert ryo_catalogue.diff(ryo_catalogue.load_pin(PIN), ryo_catalogue.parse_catalogue(catalogue_body())) == []


def test_the_live_catalogue_has_the_structure_of_the_pin() -> None:
    live = ryo_catalogue.parse_catalogue(live_fixture("tools.")["raw"])
    assert ryo_catalogue.diff(ryo_catalogue.load_pin(PIN), live) == []


def test_the_generator_reproduces_the_shipped_pin(tmp_path: Path) -> None:
    live = live_fixture("tools.")
    source = tmp_path / "catalogue.json"
    source.write_bytes(live["raw"])
    target = tmp_path / "ryo_pin.py"
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_ryo_pin.py"), str(source), str(target)], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    subprocess.run([sys.executable, "-m", "ruff", "format", "--line-length", "140", str(target)], check=True, capture_output=True)
    spec = importlib.util.spec_from_file_location("generated_pin", target)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.PIN == PIN


def test_the_generator_refuses_a_catalogue_it_cannot_check(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_bytes(b'[{"name":"a","description":"d","inputSchema":{"type":"object","properties":{},"additionalProperties":true}}]')
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_ryo_pin.py"), str(bad), str(tmp_path / "o.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode != 0
    assert not (tmp_path / "o.py").exists()


def test_the_generator_prints_its_usage_without_arguments() -> None:
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "make_ryo_pin.py")], capture_output=True, text=True, check=False)
    assert done.returncode == 2
    assert "Usage" in done.stdout


def test_the_catalogue_and_key_status_read_live_on_9_november_fit_the_pin_and_the_reader() -> None:
    """Read again on 9 Nov 2026 with the owner's key (catalogue and key status spend no tool call): the catalogue is the pinned one byte for
    byte, and the key status, a monthly limit of 200 with the key not allowed, is read as that and stops a cycle before any call."""
    import json

    import pytest

    from hoshitori import ryo_envelope

    folder = ROOT.parent / "research" / "architecture" / "fixtures" / "ryo_live_2026-11-09"
    if not folder.is_dir():
        pytest.skip("the 9 November capture is not on this machine")
    tools = (folder / "tools.json").read_bytes()
    assert ryo_catalogue.diff(ryo_catalogue.load_pin(PIN), ryo_catalogue.parse_catalogue(tools)) == []
    assert json.loads(tools) == json.loads(live_fixture("tools.")["raw"]), "unchanged since the pinned capture"
    who = ryo_envelope.read_whoami((folder / "whoami.json").read_bytes())
    assert (who.call_allowed, who.call_remaining) == (False, 200)
    assert sorted(who.tools) == sorted(name for name in PIN)
