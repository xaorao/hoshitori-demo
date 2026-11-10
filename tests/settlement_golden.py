"""The golden settlement cases (``tests/golden/settlement/*.json``) as input to an implementation of ``hoshitori.settlement``."""

from __future__ import annotations

import json
import types
from decimal import Decimal
from pathlib import Path
from typing import Any

from hoshitori.timeline import TimelineError

GOLDEN = Path(__file__).parent / "golden" / "settlement"


def load_cases() -> list[dict[str, Any]]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(GOLDEN.glob("*.json"))]


def _oracle(mod: types.ModuleType, data: dict[str, Any]) -> Any:
    bars = {int(row[0]): mod.Bar(int(row[0]), Decimal(row[1]), Decimal(row[2]), Decimal(row[3]), Decimal(row[4])) for row in data["bars"]}
    tick = None if data["tick"] is None else Decimal(data["tick"])
    return mod.Oracle(
        data["venue"], data["instrument"], data["quote_class"], Decimal(data["multiplier"]), tick, int(data["server_time_ms"]), bars
    )


def _answer(result: Any) -> dict[str, Any]:
    return {
        "contract": result.contract,
        "state": result.state.value,
        "outcome": result.outcome,
        "reason": result.reason,
        "p0": result.p0,
        "outcome_price": result.outcome_price,
        "touch_open_ms": result.touch_open_ms,
        "flags": sorted(result.flags),
    }


def run_case(mod: types.ModuleType, case: dict[str, Any]) -> dict[str, Any]:
    """What the implementation answers for a case, in the form the files use."""
    kind = case["kind"]
    try:
        if kind == "void":
            return _answer(mod.void(case["forecast_id"], case["contract"], case["pair_id"], case["expected"]["reason"]))
        pair = mod.OraclePair(case["pair"]["pair_id"], _oracle(mod, case["pair"]["primary"]), _oracle(mod, case["pair"]["second"]))
        f = case["forecast"]
        basis = None if case["basis"] is None else Decimal(case["basis"])
        if kind == "directional":
            forecast = mod.Directional(f["forecast_id"], int(f["origin_ms"]), int(f["horizon_ms"]), f["quote_class"])
            return _answer(mod.settle_directional(forecast, pair, retries_exhausted=case["retries_exhausted"], basis=basis))
        barrier = mod.Barrier(
            f["forecast_id"],
            int(f["origin_ms"]),
            int(f["horizon_ms"]),
            f["quote_class"],
            mod.Side(f["side"]),
            Decimal(f["target_offset"]),
            Decimal(f["stop_offset"]),
        )
        finer = {int(k): [mod.Fine(Decimal(high), Decimal(low)) for high, low in v] for k, v in case["finer"].items()}
        return _answer(mod.settle_barrier(barrier, pair, retries_exhausted=case["retries_exhausted"], finer=finer, basis=basis))
    except (mod.SettlementError, TimelineError):
        return {"error": "invalid"}


def golden_failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for case in load_cases():
        got = run_case(mod, case)
        if got != case["expected"]:
            bad.append(f"{case['id']} (contract {case['section']}): got {got}, expected {case['expected']}")
    return bad
