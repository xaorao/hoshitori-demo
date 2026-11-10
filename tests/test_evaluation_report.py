"""The evaluation report script: it writes both reports, every line says simulated, and the controls fail where they must."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "evaluation_report.py"


def load():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("evaluation_report", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_report_labels_everything_simulated_and_shows_the_controls_failing() -> None:
    text = load().lines()
    assert sum(1 for line in text if line.startswith("SIMULATED:")) == 4  # one label for each of the four reports
    assert any(line.startswith("Echo of existing evidence: FAIL") for line in text), "the gullible answerer must fail the echo"
    assert any(line.startswith("G1, raise the exposure: the attacker succeeded in 55 of 55") for line in text), (
        "the control must be exploited"
    )
    assert any(line.startswith("G1, raise the exposure: the attacker succeeded in 0 of 55") for line in text)


def test_the_script_writes_a_file(tmp_path: Path) -> None:
    import sys

    module = load()
    out = tmp_path / "report.txt"
    saved = sys.argv
    sys.argv = ["evaluation_report.py", str(out)]
    try:
        assert module.main() == 0
    finally:
        sys.argv = saved
    assert out.read_text(encoding="utf-8").startswith("Belief revision")
