#!/usr/bin/env python3
"""A two-minute smoke run: the fast tests of the core, without the mutation sweeps.

    python scripts/smoke.py

It runs the tests of the canonical record form, forecasts and scoring, settlement, the verifier, the ledger, the risk kernel, the RYO
readers and budget, the grounding gate, the Desk's plan and the skills' catalogue style. It is a quick check that a fresh clone works,
not the suite: the full default set is `pytest -k "not every_change"` (about 20 minutes) and the sweeps are `pytest` (more than an hour).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "test_canonical.py",
    "test_forecast.py",
    "test_scoring.py",
    "test_settlement.py",
    "test_verify.py",
    "test_ledger.py",
    "test_risk_kernel.py",
    "test_ryo_budget.py",
    "test_ryo_read.py",
    "test_clusters.py",
    "test_desk_plan.py",
    "test_grounding.py",
    "test_skill_catalogue_style.py",
)


def main() -> int:
    missing = [f for f in FILES if not (ROOT / "tests" / f).is_file()]
    if missing:
        sys.stderr.write(f"smoke: not found: {', '.join(missing)}\n")
        return 2
    cmd = [sys.executable, "-m", "pytest", "-q", "-k", "not every_change", *(f"tests/{f}" for f in FILES)]
    return subprocess.run(cmd, cwd=ROOT, check=False).returncode  # noqa: S603 - our own command


if __name__ == "__main__":
    raise SystemExit(main())
