"""The whale cohort is chosen by account value and a written seed, never by performance."""

from __future__ import annotations

import importlib.util
import json
from decimal import Decimal
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("make_whale_cohort", Path(__file__).resolve().parents[1] / "scripts" / "make_whale_cohort.py")
assert SPEC is not None and SPEC.loader is not None
cohort = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cohort)


def rows(n: int) -> list[dict[str, object]]:
    return [
        {
            "ethAddress": "0x" + f"{i:040x}",
            "accountValue": str(1_000_000 * (n - i)),
            "windowPerformances": [["allTime", {"pnl": str(i * 7 % 13)}]],
        }
        for i in range(n)
    ]


def test_the_top_by_value_and_a_seeded_sample_of_the_rest() -> None:
    data = rows(200)
    chosen = cohort.choose(data, 5, 5, Decimal(1_000_000), 7)
    assert chosen[:5] == ["0x" + f"{i:040x}" for i in range(5)] and len(chosen) == 10 and len(set(chosen)) == 10
    assert chosen == cohort.choose(data, 5, 5, Decimal(1_000_000), 7) and chosen != cohort.choose(data, 5, 5, Decimal(1_000_000), 8)


def test_performance_never_changes_the_choice() -> None:
    data = rows(60)
    other = [{**r, "windowPerformances": [["allTime", {"pnl": "-999999999"}]]} for r in data]
    assert cohort.choose(data, 5, 5, Decimal(0), 1) == cohort.choose(other, 5, 5, Decimal(0), 1)


def test_the_floor_bad_rows_and_a_small_pool() -> None:
    data = [
        *rows(8),
        {"ethAddress": "nope", "accountValue": "9"},
        {"ethAddress": "0x" + "1" * 40, "accountValue": "NaN"},
        {"accountValue": "1"},
    ]
    chosen = cohort.choose(data, 2, 10, Decimal(4_000_000), 1)
    assert len(chosen) <= 2 + 5 and all(a.startswith("0x") and len(a) == 42 for a in chosen)


def test_the_script_writes_a_settings_file_with_its_rule(tmp_path: Path) -> None:
    source = tmp_path / "lb.json"
    source.write_text(json.dumps({"leaderboardRows": rows(100)}))
    out = tmp_path / "cohort.json"
    assert cohort.main([str(out), "--top", "3", "--sample", "3", "--floor", "1", "--from", str(source)]) == 0
    doc = json.loads(out.read_text())
    assert (
        len(doc["wallets"]) == 6
        and doc["accounts"] == []
        and doc["cohort_rule"]["uses_performance"] is False
        and doc["cohort_rule"]["snapshot_rows"] == 100
    )
    assert cohort.main([str(out), "--top", "40", "--sample", "40", "--from", str(source)]) == 2
