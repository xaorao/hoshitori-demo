"""The invented examples of a fresh install: made without a model, a market or RYO, labelled invented, and read by the site's own readers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from hoshitori import claims_file, lab, lab_view

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_claims_are_invented_and_read_by_the_page_reader(tmp_path: Path) -> None:
    mod = load("make_synthetic_claims")
    records = mod.read_all()
    data = (json.dumps(records, indent=1, ensure_ascii=False) + "\n").encode()
    assert len(claims_file.read_claims(data)) == len(mod.EXAMPLES) == 4
    assert {r["model"] for r in records} == {"synthetic example (no model)"}
    assert data == (json.dumps(mod.read_all(), indent=1, ensure_ascii=False) + "\n").encode(), "the same file every time"


def test_desk_runs_say_synthetic_and_commit_nothing(tmp_path: Path) -> None:
    out = tmp_path / "desk.json"
    assert load("make_synthetic_desk_runs").main([str(out)]) == 0
    text = out.read_text()
    assert '"label":"synthetic"' in text.replace(" ", "") and '"label":"recorded"' not in text.replace(" ", "")
    records = json.loads(text)["records"]
    assert {r["body"]["label"] for r in records if r["kind"] == "desk_run_state"} == {"synthetic"}
    assert not any(r["kind"] == "decision" and r["body"].get("forecast_id") for r in records), "nothing is committed"


def test_lab_log_has_only_invented_versions(tmp_path: Path) -> None:
    out = tmp_path / "lab.jsonl"
    assert load("make_synthetic_lab").main([str(out)]) == 0
    versions = lab.Lab(out).versions().values()
    assert len(versions) == 3 and all(v.by == "synthetic-example" for v in versions)
    assert all(v.mandate.statement.startswith("Invented example:") for v in versions)
    assert lab_view.build(lab.Lab(out)) is not None
    assert load("make_synthetic_lab").main([str(out)]) == 2, "an existing file is never overwritten"
