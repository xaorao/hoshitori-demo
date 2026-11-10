"""``scripts/ryo_budget.py`` prints the scan cutoff an allowance pays for, and refuses what no cutoff fits."""

from __future__ import annotations

import json
from typing import Any

from scripts.ryo_budget import main


def test_prints_the_largest_cutoff_and_its_cost(capsys: Any) -> None:
    assert main(["600", "--event-cap", "2", "--watchlist", "3"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"scan_cutoff": 3, "candidates_at_most": 8, "calls_per_reading": 19, "calls_a_month": 570, "allowance": 600}


def test_the_default_event_cap_and_watchlist(capsys: Any) -> None:
    assert main(["1000"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["scan_cutoff"], out["candidates_at_most"], out["calls_a_month"]) == (5, 15, 990)


def test_a_refusal_names_the_reason(capsys: Any) -> None:
    assert main(["200"]) == 2
    assert "alone use the whole allowance" in capsys.readouterr().err
    assert main(["100", "--event-cap", "0", "--watchlist", "0"]) == 2
