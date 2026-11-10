"""The model agent asked again with and without a source (``scripts/model_source_value.py``), on a loopback league with a loopback model:
the script refuses a budget its worst case does not fit, asks twice per settled call, records what was answered, and the export shows the
lines; a recorded file the ledger does not bear out is refused."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from scripts import model_source_value as script

from hoshitori import deployment, scheduler, source_value
from tests import model_world
from tests.deployment_checks import MODEL_KEY_ENV, models_document, world_files
from tests.e2e_league import OffsetClock, outside
from tests.smart_model import SmartModel


def test_asked_recorded_shown_and_held_to_the_ledger(tmp_path: Path) -> None:
    clock = OffsetClock()
    previous = os.environ.get(MODEL_KEY_ENV)
    model = SmartModel().start()
    os.environ[MODEL_KEY_ENV] = model_world.KEY
    try:
        with outside(clock) as world:
            (tmp_path / "a").mkdir()
            path = world_files(tmp_path / "a", world, models_doc=models_document(model.url))
            settings = deployment.load_settings(path)
            with deployment.assemble(settings, clock=clock) as running:
                first = scheduler.tick(running.services, running.config)
                assert first.ran is not None and first.ran.state.value == "COMMITTED"
                clock.jump(2 * 3600)
                scheduler.tick(running.services, running.config)
            out = tmp_path / "runs.json"
            with pytest.raises(script.Refused, match="more than the budget"):
                script.run(path, "hoshitori.features", budget=3, out=out, max_slots=1, clock=clock)
            asked_before = len(model.requests)
            report = script.run(path, "hoshitori.features", budget=40, out=out, max_slots=10, hours={1}, clock=clock)
            runs = source_value.read_model_runs(out.read_bytes())
            assert report["slots"] == len(runs.pairs) > 0
            assert len(model.requests) - asked_before == report["model_calls"] <= 4 * report["slots"]
            assert {p.hours for p in runs.pairs} == {1}
            assert len({p.seed for p in runs.pairs}) == len(runs.pairs)  # each slot its own seed, shared by its two runs
            with deployment.assemble_reading(settings) as reading:
                site = tmp_path / "site"
                deployment.export_site(reading, site, model_sources=[out])
            lines = json.loads((site / "sources.json").read_text())["lines"]
            mine = [line for line in lines if line["arm"] == "L1"]
            assert [(line["source"], line["hours"]) for line in mine] == [("hoshitori.features", 1)]
            assert mine[0]["shared"] + mine[0]["required"] + mine[0]["not_evaluated"] <= len(runs.pairs)
            doc = json.loads(out.read_text())
            doc["pairs"][0]["outcome"] = 1 - doc["pairs"][0]["outcome"]
            forged = tmp_path / "forged.json"
            forged.write_text(json.dumps(doc))
            with deployment.assemble_reading(settings) as reading, pytest.raises(deployment.DeploymentError, match="not the ledger's"):
                deployment.export_site(reading, tmp_path / "site2", model_sources=[forged])
    finally:
        model.stop()
        if previous is None:
            os.environ.pop(MODEL_KEY_ENV, None)
        else:
            os.environ[MODEL_KEY_ENV] = previous
