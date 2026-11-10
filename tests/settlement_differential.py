"""Random settlement cases, answered by the Python module and by the Node one (``settlement-js``), which must agree on every one.

The Node module was written from the contract text and shares no code with the Python one. Agreement on thousands of generated cases (and on the hand-written
golden set) is evidence that both read the contract the same way. It is not independent in the strong sense: one author wrote both.
"""

from __future__ import annotations

import json
import random
import shutil
import subprocess
import types
from decimal import Decimal
from pathlib import Path
from typing import Any

from tests.settlement_golden import run_case

M, H = 60_000, 3_600_000
NODE_ENTRY = Path(__file__).resolve().parent.parent / "settlement-js" / "bin" / "answer.mjs"
BASE = 1_793_311_320_000


def node_available() -> bool:
    return shutil.which("node") is not None and NODE_ENTRY.exists()


def _price(rng: random.Random, around: Decimal, spread: float, places: int) -> Decimal:
    move = Decimal(str(round(rng.uniform(-spread, spread), 6)))
    return max(Decimal(1).scaleb(-places), (around * (1 + move)).quantize(Decimal(1).scaleb(-places)))


def _bars(rng: random.Random, opens: list[int], start: Decimal, places: int, wild: bool) -> list[list[Any]]:
    rows: list[list[Any]] = []
    price = start
    for open_ms in opens:
        spread = 0.02 if wild else 0.002
        o, c = price, _price(rng, price, spread, places)
        high = max(o, c, _price(rng, max(o, c), spread / 2, places))
        low = min(o, c, _price(rng, min(o, c), spread / 2, places))
        rows.append([open_ms, f"{o:f}", f"{high:f}", f"{low:f}", f"{c:f}"])
        price = c
    return rows


def generate(seed: int, count: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    cases: list[dict[str, Any]] = []
    for n in range(count):
        kind = rng.choice(["directional", "barrier"])
        horizon = rng.choice([1, 2, 4, 8, 24]) * H
        origin = BASE + rng.randrange(0, 5000) * M
        places = rng.choice([0, 1, 2, 4, 8])
        tick = None if rng.random() < 0.3 else f"{Decimal(1).scaleb(-rng.randrange(0, places + 1)) * rng.choice([1, 1, 5]):f}"
        multiplier = rng.choice(["1", "1", "1", "1000", "0.001"])
        start = Decimal(rng.choice([1, 10, 100, 1000, 79000, 100000])) * Decimal(rng.choice([1, 1, 1, "1.37"]))
        needed = (
            {origin - M, origin + horizon - M} if kind == "directional" else {origin - M, *(origin + k * M for k in range(horizon // M))}
        )
        opens = sorted(needed)
        primary_bars = _bars(rng, opens, start, places, wild=rng.random() < 0.3)
        scale = Decimal(multiplier)
        second_bars = [[row[0], *[f"{Decimal(v) * scale / 1:f}" for v in row[1:]]] for row in primary_bars]
        if rng.random() < 0.15:  # the second oracle drifts, sometimes past the limit
            drift = Decimal(rng.choice(["1.001", "1.005", "1.0051", "0.99", "1.2"]))
            second_bars = [[row[0], *[f"{Decimal(v) * drift:f}" for v in row[1:]]] for row in second_bars]
        for bars in (primary_bars, second_bars):
            if rng.random() < 0.15:  # a bar goes missing
                del bars[rng.randrange(len(bars))]
        end = origin + horizon
        late = rng.random() < 0.15
        server = rng.choice([origin - M, origin, end - M, end - 1]) if late else end + rng.choice([0, M, H])

        def oracle(bars: list[list[Any]], mult: str, server_time: int = server, tick: str | None = tick) -> dict[str, Any]:
            return {
                "venue": "v",
                "instrument": "i",
                "quote_class": "USDT",
                "multiplier": mult,
                "tick": tick,
                "server_time_ms": server_time,
                "bars": bars,
            }

        second_server = server if rng.random() < 0.8 else rng.choice([end - M, end + H])
        pair = {"pair_id": f"P{n}", "primary": oracle(primary_bars, "1"), "second": oracle(second_bars, multiplier, second_server)}
        if multiplier != "1":
            pair["primary"]["multiplier"] = "1"
            pair["second"]["multiplier"] = multiplier
        case: dict[str, Any] = {"id": f"R{seed}-{n}", "kind": kind, "pair": pair, "retries_exhausted": rng.random() < 0.4, "finer": {}}
        case["basis"] = rng.choice([None, None, "0", "0.003", "0.0031", "-0.01", "0.5"])
        forecast: dict[str, Any] = {"forecast_id": f"F{n}", "origin_ms": origin, "horizon_ms": horizon, "quote_class": "USDT"}
        if kind == "barrier":

            def offset(start: Decimal = start) -> str:
                scale = Decimal(rng.choice(["0.2", "0.5", "1", "2", "5", "10", "50"])) if rng.random() < 0.9 else Decimal(0)
                return f"{scale * start / Decimal(1000):f}"

            forecast |= {"side": rng.choice(["LONG", "SHORT"]), "target_offset": offset(), "stop_offset": offset()}
            for row in primary_bars:
                if rng.random() < 0.5:
                    low, high = Decimal(row[3]), Decimal(row[2])
                    pieces = []
                    for _ in range(rng.randrange(0, 4)):
                        a, b = sorted(low + (high - low) * Decimal(str(round(rng.random(), 4))) for _ in range(2))
                        pieces.append([f"{rng.choice([b, high]):f}", f"{rng.choice([a, low]):f}"])
                    case["finer"][str(row[0])] = pieces
        if rng.random() < 0.04:  # a forecast no rule can accept
            broken = rng.choice(["origin", "horizon", "short", "quote"])
            if broken == "origin":
                forecast["origin_ms"] = origin + rng.choice([1, 1000, 59_999])
            elif broken == "horizon":
                forecast["horizon_ms"] = horizon + rng.choice([1, 1000])
            elif broken == "short":
                forecast["horizon_ms"] = 59 * M
            else:
                forecast["quote_class"] = "USD"
        case["forecast"] = forecast
        case["expected"] = {}
        cases.append(case)
    return cases


def node_answers(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lines = "\n".join(json.dumps(case) for case in cases) + "\n"
    out = subprocess.run(
        [str(shutil.which("node")), str(NODE_ENTRY), "-"], input=lines, capture_output=True, text=True, check=True, timeout=300
    )
    return {row["id"]: row["answer"] for row in map(json.loads, out.stdout.splitlines())}


def disagreements(mod: types.ModuleType, seed: int, count: int) -> list[str]:
    """Cases on which the module and the Node implementation answer differently (an empty list when they agree)."""
    cases = generate(seed, count)
    theirs = node_answers(cases)
    bad = []
    for case in cases:
        mine = run_case(mod, case)
        if mine != theirs[case["id"]]:
            bad.append(f"{case['id']}: python {mine}, node {theirs[case['id']]}")
    return bad
