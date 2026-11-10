"""How RYO's own verdicts settled, from the outcomes of a published record.

Each outcome is one verdict of RYO's ``deep_analysis`` on a token, its word read as a reading (``UP``, ``DOWN``, ``NOT_LONG`` or not a
direction), and what the 24 hours after it did. The rules:

* A reading is **right** when it said up and the window rose, or said down or against buying and the window fell. A tie, a window that was
  not settled and a reading that is not a direction are **not scored**; they are counted with their reason and are never a miss.
* The **hit rate** of a reading is hits over scored reads, with a 95 percent Wilson interval. It is compared with the **market's own rate**
  for that side, from all scored windows of the record: the share that rose for ``UP``, the share that fell for the other two.
* Before ``min_scored`` scored reads of a reading there is no comparison (``insufficient``); after it, ``above_market`` or ``below_market``
  when the whole interval lies on that side of the market's rate, and ``not_distinguishable`` otherwise.
* The recorded ``hit`` is checked against the rule; a record whose hit disagrees is reported, and its own hit is not used.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections import Counter
from typing import Any

from .errors import InputError
from .schema import (
    DEFAULT_MIN_SCORED,
    DIRECTIONS,
    EXAMPLE_CALL,
    INPUT_SCHEMA,
    MAX_OUTCOMES,
    PLANS,
    READINGS,
    RULES_TOOL_NAME,
    SCHEMA_VERSION,
    SCORED_READINGS,
    TOOL_NAME,
)

Z95 = 1.959963984540054
_SYMBOL = re.compile(r"[A-Z0-9]{1,20}")
RIGHT_DIRECTION = {"UP": "ROSE", "DOWN": "FELL", "NOT_LONG": "FELL"}


def _fail(field: str, message: str) -> InputError:
    return InputError(field, message, EXAMPLE_CALL)


def wilson(hits: int, n: int) -> tuple[float, float]:
    """The 95 percent Wilson score interval of ``hits`` in ``n``; (0, 1) for no reads."""
    if n <= 0:
        return 0.0, 1.0
    p = hits / n
    denom = 1 + Z95**2 / n
    centre = (p + Z95**2 / (2 * n)) / denom
    half = Z95 * math.sqrt(p * (1 - p) / n + Z95**2 / (4 * n * n)) / denom
    # At zero hits the lower end is exactly 0 and at all hits the upper end is exactly 1; the float sum can leave 1e-17 on the wrong side,
    # which would make a zero-hit read "above" a market rate of 0 (or an all-hit read "below" a rate of 1).
    low = 0.0 if hits <= 0 else max(0.0, centre - half)
    high = 1.0 if hits >= n else min(1.0, centre + half)
    return low, high


def scored_direction(reading: str, direction: object) -> str | None:
    """Why this read is not scored, as a closed word, or none when it is."""
    if reading not in SCORED_READINGS:
        return "not_a_direction"
    if direction is None:
        return "not_settled"
    if direction == "TIE":
        return "tie"
    return None


def hit_of(reading: str, direction: object) -> bool | None:
    return None if scored_direction(reading, direction) is not None else RIGHT_DIRECTION[reading] == direction


def _outcomes(record: object) -> list[Any]:
    outcomes = record.get("outcomes") if isinstance(record, dict) else record
    if not isinstance(outcomes, list):
        raise _fail("record", "must be an object with an outcomes list, or a list of outcomes")
    if len(outcomes) > MAX_OUTCOMES:
        raise _fail("record", f"has more than {MAX_OUTCOMES} outcomes")
    return outcomes


def _validate(args: object) -> tuple[list[Any], str | None, int]:
    if not isinstance(args, dict):
        raise _fail("arguments", "must be an object")
    unknown = sorted(set(args) - set(INPUT_SCHEMA["properties"]))
    if unknown:
        raise _fail(unknown[0], "is not an argument of this tool")
    if "record" not in args:
        raise _fail("record", "is required")
    outcomes = _outcomes(args["record"])
    symbol = args.get("symbol")
    if symbol is not None and (not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None):
        raise _fail("symbol", "must be 1 to 20 capital letters or digits")
    minimum = args.get("min_scored", DEFAULT_MIN_SCORED)
    if isinstance(minimum, bool) or not isinstance(minimum, int) or not 1 <= minimum <= 1000:
        raise _fail("min_scored", "must be a whole number from 1 to 1000")
    return outcomes, symbol, minimum


def run(args: object, *, now: dt.datetime | None = None, **_: Any) -> dict[str, Any]:
    outcomes, symbol, minimum = _validate(args)
    now = now or dt.datetime.now(dt.UTC)
    warnings: list[str] = []
    reads: list[dict[str, Any]] = []
    malformed = 0
    for item in outcomes:
        if (
            not isinstance(item, dict)
            or item.get("reading") not in READINGS
            or not isinstance(item.get("symbol"), str)
            or item.get("direction") not in (*DIRECTIONS, None)
        ):
            malformed += 1
            continue
        if symbol is None or item["symbol"] == symbol:
            reads.append({**item, "direction": item.get("direction")})  # no direction key reads as not settled, as None does
    if malformed:
        warnings.append(
            f"{malformed} outcome(s) could not be read (a missing symbol, an unknown reading or an unknown direction) and are left out"
        )

    scored = [r for r in reads if scored_direction(r["reading"], r["direction"]) is None]
    unscored = Counter(
        scored_direction(r["reading"], r["direction"]) for r in reads if scored_direction(r["reading"], r["direction"]) is not None
    )
    windows = [r["direction"] for r in scored]  # the market's own windows: every scored read's window
    market = {"ROSE": windows.count("ROSE") / len(windows), "FELL": windows.count("FELL") / len(windows)} if windows else None

    mismatched = 0
    by_reading: dict[str, dict[str, Any]] = {}
    for kind in SCORED_READINGS:
        mine = [r for r in scored if r["reading"] == kind]
        hits = sum(1 for r in mine if hit_of(kind, r["direction"]))
        mismatched += sum(1 for r in mine if isinstance(r.get("hit"), bool) and r["hit"] is not hit_of(kind, r["direction"]))
        n = len(mine)
        low, high = wilson(hits, n)
        base = None if market is None else market[RIGHT_DIRECTION[kind]]
        if n < minimum or base is None:
            verdict = "insufficient"
        elif low > base:
            verdict = "above_market"
        elif high < base:
            verdict = "below_market"
        else:
            verdict = "not_distinguishable"
        by_reading[kind] = {
            "reads": sum(1 for r in reads if r["reading"] == kind),
            "scored": n,
            "hits": hits,
            "hit_rate": None if n == 0 else round(hits / n, 4),
            "interval_95": None if n == 0 else [round(low, 4), round(high, 4)],
            "market_rate": None if base is None else round(base, 4),
            "verdict": verdict,
        }
    if market is not None and len(scored) >= 2 and (market["ROSE"] in (0.0, 1.0)):
        warnings.append(
            "every scored window in this record went the same way, and the market's rate is taken from these windows; no reading can be "
            "told from luck here, so none is called above or below the market"
        )
    if mismatched:
        warnings.append(f"{mismatched} recorded hit(s) disagree with the rule; the rule's hit is used, and the record should be looked at")
    plans = Counter(r.get("plan") if r.get("plan") in PLANS else "OTHER" for r in reads)
    if plans.get("OTHER"):
        warnings.append(f"{plans['OTHER']} plan word(s) are not in the closed list and are counted as OTHER")

    data: dict[str, Any] = {
        "reads": len(reads),
        "scored": len(scored),
        "unscored": {why: unscored.get(why, 0) for why in ("not_a_direction", "not_settled", "tie")},
        "by_reading": by_reading,
        "market_rate_rose": None if market is None else round(market["ROSE"], 4),
        "plans": {plan: plans.get(plan, 0) for plan in (*PLANS, "OTHER")},
        "min_scored": minimum,
        "symbol": symbol,
    }
    status = "unavailable" if not reads else ("partial" if warnings else "ok")
    points = [
        f"{len(reads)} verdicts read, {len(scored)} scored; unscored: " + ", ".join(f"{k} {v}" for k, v in data["unscored"].items()) + "."
    ]
    for kind, line in by_reading.items():
        if line["scored"]:
            points.append(f"{kind}: {line['hits']} of {line['scored']} right ({line['verdict'].replace('_', ' ')}).")
    if not reads:
        headline = "No RYO verdict is in this record, so there is nothing to score. That is not a finding about RYO."
    elif not scored:
        headline = f"{len(reads)} RYO verdicts were read, none could be scored yet. Nothing is counted as a miss."
    else:
        headline = (
            f"RYO's verdicts: {len(scored)} of {len(reads)} read could be scored; "
            + "; ".join(f"{k} {v['hits']}/{v['scored']} ({v['verdict'].replace('_', ' ')})" for k, v in by_reading.items() if v["scored"])
            + "."
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": status,
        "data_mode": "unknown",
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "request": {"symbol": symbol, "min_scored": minimum},
        "data": data,
        "summary": {"headline": headline, "key_points": points},
        "availability": {"outcomes": "available" if reads else "unavailable"},
        "warnings": warnings,
    }


def run_rules(args: object, *, now: dt.datetime | None = None, **_: Any) -> dict[str, Any]:
    """Describe the rules; scores nothing."""
    if not isinstance(args, dict) or args:
        raise InputError("arguments", "this tool takes none", {})
    now = now or dt.datetime.now(dt.UTC)
    rules = [
        "a reading is right when it said up and the window rose, or said down or against buying and the window fell",
        "a tie, a window not yet settled and a reading that is not a direction are not scored; "
        "they are counted with a reason and are never a miss",
        "the hit rate is hits over scored reads, with a 95 percent Wilson interval",
        "it is compared with the market's own rate for that side, taken from every scored window in the record: "
        "the share that rose for up, the share that fell for down and against buying",
        f"before min_scored (default {DEFAULT_MIN_SCORED}) scored reads of a reading there is no comparison: insufficient",
        "above_market or below_market only when the whole interval is on that side of the market's rate; otherwise not_distinguishable",
        "the recorded hit is checked against the rule; a disagreement is reported and the rule's hit is used",
        "the plan counts say which of RYO's own levels came first (target, stop, timeout, ambiguous) or why there is no answer",
        f"limits: at most {MAX_OUTCOMES} outcomes; nothing is sent anywhere; the rates describe the record, not the future",
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": RULES_TOOL_NAME,
        "status": "ok",
        "data_mode": "unknown",
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "request": {},
        "data": {"rules": rules},
        "summary": {"headline": "ryo_verdict_record never counts an unscored read as a miss.", "key_points": rules[:3]},
        "availability": {"rules": "available"},
        "warnings": [],
    }
