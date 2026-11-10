"""The skill: read the venues, run the model, build the envelope.

``data_mode`` is earned: ``live`` when at least one answer was fetched in this call, ``unknown`` when none was. ``status`` is ``ok`` when
the cost of the exit can be stated (pool depth or a quote, and volume for the time), ``partial`` when only part can (volume alone answers
the time to exit; depth alone answers the cost), and ``unavailable`` when nothing could be read. A figure that could not be read is
``null`` and named under ``gaps``; it is never zero. A quote outranks the model; the model is kept beside it so its error is visible.

The verdict bands and the confidence rule follow Hanko's ``assess`` (MIT; see ``model.py``): a cost at or under the ceiling is ``ok``, up to
twice the ceiling ``tight``, above that ``illiquid``, and ``unknown`` when no cost can be stated. Confidence is ``high`` only for a quote of
this exact size; a modelled figure is at most ``moderate``, and ``low`` past the model's validity limit.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

from . import model
from .errors import REASON_TEXT, InputError, SourceError
from .registry import SUPPORTED, TOKENS
from .schema import SCHEMA_VERSION, SOURCES_TOOL_NAME, TOOL_NAME, parse_request
from .transport import Caller, Transport
from .trust import default_transport
from .venues import PoolDepth, VenueQuote, fetch_depth, fetch_quote

BUDGET_SECONDS = 30.0
VERDICT_WORDS = {
    "ok": "the exit fits inside the ceiling",
    "tight": "the exit is above the ceiling",
    "illiquid": "the exit is severe at this size",
    "unknown": "no exit cost can be stated",
}


def _money(value: float) -> str:
    return f"${value:,.0f}" if value >= 100 else f"${value:,.2f}"


def _pct(share: float) -> str:
    percent = share * 100
    if percent < 0.01:
        return "under 0.01%"
    return f"{percent:.2f}%" if percent < 10 else f"{percent:.1f}%"


def _hours(hours: float) -> str:
    if hours < 1:
        return f"{round(hours * 60)} minutes"
    if hours < 48:
        return f"{hours:.1f} hours"
    return f"{hours / 24:.1f} days"


def _grade(cost: float, ceiling: float) -> str:
    if cost <= ceiling:
        return "ok"
    return "tight" if cost <= ceiling * 2 else "illiquid"


def _envelope(
    tool: str,
    now: dt.datetime,
    request: dict[str, Any],
    status: str,
    data_mode: str,
    data: dict[str, Any],
    headline: str,
    key_points: list[str],
    availability: dict[str, str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": tool,
        "status": status,
        "data_mode": data_mode,
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "request": request,
        "data": data,
        "summary": {"headline": headline, "key_points": key_points},
        "availability": availability,
        "warnings": warnings,
    }


def run(
    args: object,
    *,
    now: dt.datetime | None = None,
    transport: Transport | None = None,
    budget_s: float = BUDGET_SECONDS,
    caller: Caller | None = None,
) -> dict[str, Any]:
    """One call. Raises ``InputError`` for a malformed request; every source failure is reported inside the envelope."""
    req = parse_request(args)
    now = now or dt.datetime.now(dt.UTC)
    caller = caller or Caller(transport or default_transport(), now=now, deadline=time.monotonic() + budget_s)
    token = req.token
    warnings: list[str] = []
    gaps: list[str] = []
    notes: list[str] = []
    failures: list[dict[str, str]] = []

    depth: PoolDepth | None = None
    try:
        depth = fetch_depth(token, caller)
    except SourceError as exc:
        failures.append({"source": "dexscreener", "reason": exc.reason, "text": REASON_TEXT[exc.reason]})
    if depth is not None and not depth.pools:
        failures.append({"source": "dexscreener", "reason": "no_pool", "text": "no pool passed the filters for this token's contract"})
        gaps.append("no pool passed the filters, so there is no depth to model")

    quote: VenueQuote | None = None
    solana = TOKENS[token]["chain"] == "solana"
    price = depth.price_usd if depth is not None else None
    if req.size_usd is not None and solana and price:
        try:
            quote = fetch_quote(token, req.size_usd, price, caller)
        except SourceError as exc:
            failures.append({"source": "jupiter", "reason": exc.reason, "text": REASON_TEXT[exc.reason]})
    elif req.size_usd is not None and not solana:
        notes.append(
            "no keyless executable-quote venue exists for this token on its home chain, so the cost is modelled from on-chain depth"
        )
    elif req.size_usd is not None and solana and not price:
        notes.append("no price was read, so no quote was sized")

    liquidity = depth.total_liquidity_usd if depth is not None else None
    volume = depth.total_volume_24h_usd if depth is not None else None
    if depth is not None and depth.pools:
        notes.append(
            f"depth is the sum of {len(depth.pools)} on-chain pools for the canonical contract; "
            "centralised-exchange depth and volume are not included"
        )
    if liquidity is None:
        gaps.append("pool liquidity unavailable; the exit cost cannot be modelled and is reported as null, not zero")
    if volume is None:
        gaps.append("24 hour volume unavailable; the time to exit cannot be estimated")

    size = req.size_usd
    estimate = model.estimate(size, liquidity) if size and liquidity else None
    max_sizes: dict[str, float | None] = {"1pct": None, "3pct": None, "at_ceiling": None}
    ladder: list[dict[str, Any]] = []
    if liquidity:
        max_sizes = {
            "1pct": round(model.size_for_slippage(0.01, liquidity), 2),
            "3pct": round(model.size_for_slippage(0.03, liquidity), 2),
            "at_ceiling": round(model.size_for_slippage(req.max_slippage, liquidity), 2),
        }
        ladder = [e.as_dict() for e in model.curve(liquidity, model.default_ladder(liquidity))]
    hours = model.hours_to_exit(size, volume, req.participation) if size and volume else None
    capacity = model.size_for_hours(1.0, volume, req.participation) if volume else None

    verdict, basis, confidence, model_check = "unknown", None, "none", None
    if quote is not None:
        verdict, basis, confidence = _grade(quote.effective_cost, req.max_slippage), "quoted", "high"
        notes.append(
            "confidence is high only because an executable quote for this exact size was observed; it holds for this moment and venue"
        )
        if estimate is not None and quote.effective_cost > 0:
            model_check = {
                "modelled_cost_pct": round(estimate.slippage * 100, 4),
                "quoted_cost_pct": round(quote.effective_cost * 100, 4),
                "model_over_quote": round(estimate.slippage / quote.effective_cost, 3),
                "note": "the model is kept beside the quote so its error is visible; the verdict rests on the quote",
            }
    elif estimate is not None:
        verdict, basis = _grade(estimate.slippage, req.max_slippage), "modelled"
        confidence = "moderate" if estimate.within_model else "low"
        notes.append("confidence never exceeds moderate for a modelled figure: no venue quote was observed for this size")
        if not estimate.within_model:
            warnings.append(
                f"this exit is {estimate.pool_fraction * 100:.1f}% of the pool, past the {model.VALIDITY_LIMIT * 100:.0f}% point where a "
                "constant-product curve stops describing a real venue; treat the modelled cost as a floor"
            )
    elif size is None and liquidity:
        notes.append("no size was given, so the ceilings and the cost curve are answered without a verdict")

    if volume and liquidity and volume < liquidity * 0.02:
        warnings.append(
            "24 hour volume is under 2% of pool liquidity: the pool may be deep but inactive, "
            "and a patient exit may take far longer than modelled"
        )
    for failure in failures:
        warnings.append(f"{failure['source']}: {failure['text']}")

    have_cost = quote is not None or estimate is not None or (size is None and liquidity is not None)
    mode = "live" if depth is not None or quote is not None else "unknown"
    if liquidity is None and quote is None:
        status = "unavailable"
    else:
        status = "ok" if have_cost and (volume is not None or quote is not None) else "partial"
    inputs = [
        {
            "name": "liquidity_usd",
            "value": None if liquidity is None else round(liquidity, 2),
            "source": None if depth is None else depth.source,
        },
        {
            "name": "volume_24h_usd",
            "value": None if volume is None else round(volume, 2),
            "source": None if depth is None else depth.source,
        },
        {"name": "price_usd", "value": price, "source": None if depth is None else depth.source},
    ]
    data: dict[str, Any] = {
        "token": token,
        "chain": TOKENS[token]["chain"],
        "contract": TOKENS[token]["address"],
        "measured_as": TOKENS[token]["via"],
        "verdict": verdict,
        "basis": basis,
        "confidence": confidence,
        "requested_size_usd": size,
        "max_slippage_pct": round(req.max_slippage * 100, 4),
        "participation_pct": round(req.participation * 100, 4),
        "estimate": None if estimate is None else estimate.as_dict(),
        "quote": None if quote is None else quote.as_dict(),
        "model_check": model_check,
        "max_size_usd": max_sizes,
        "hours_to_exit": None if hours is None else round(hours, 2),
        "hourly_capacity_usd": None if capacity is None else round(capacity, 2),
        "curve": ladder,
        "pool_depth": None if depth is None else depth.as_dict(),
        "model": {"id": model.MODEL_ID, "assumptions": list(model.ASSUMPTIONS), "validity_limit_pct": model.VALIDITY_LIMIT * 100},
        "inputs": inputs,
        "gaps": gaps,
        "notes": notes,
        "source_calls": [c.as_dict() for c in caller.records],
    }

    points: list[str] = []
    if quote is not None:
        points.append(f"Jupiter quoted {_money(quote.out_usd)} back for the sale: a cost of {_pct(quote.effective_cost)}, fees included.")
    if estimate is not None:
        points.append(
            f"Modelled from pool depth: {_pct(estimate.slippage)} ({_money(estimate.cost_usd)}); "
            f"the exit is {_pct(estimate.pool_fraction)} of the pool."
        )
    if max_sizes["3pct"] is not None:
        points.append(
            f"Largest exit at 1%: {_money(max_sizes['1pct'] or 0)}; at 3%: {_money(max_sizes['3pct'])}; "
            f"at your ceiling: {_money(max_sizes['at_ceiling'] or 0)}."
        )
    if hours is not None:
        points.append(f"A patient exit at {req.participation * 100:g}% of daily volume takes {_hours(hours)}.")
    elif capacity is not None:
        points.append(f"At {req.participation * 100:g}% of daily volume the market absorbs about {_money(capacity)} an hour.")
    points.extend(gaps)
    if verdict == "unknown" and size is not None and status == "unavailable":
        headline = f"{token}: no exit cost could be stated; no source answered. This is not a liquidity rating."
    elif size is not None and (quote is not None or estimate is not None):
        cost = quote.effective_cost if quote is not None else estimate.slippage if estimate is not None else 0.0
        headline = f"{token}: exiting {_money(size)} costs {_pct(cost)} ({basis}); {VERDICT_WORDS[verdict]}."
    elif liquidity is not None:
        headline = f"{token}: a ${max_sizes['3pct']:,.0f} exit stays within 3% (modelled); no size was given, so there is no verdict."
    else:
        headline = f"{token}: no exit cost could be stated from what was read."
    availability = {
        "pool_depth": "available" if liquidity is not None else "unavailable",
        "volume": "available" if volume is not None else "unavailable",
        "executable_quote": "available" if quote is not None else ("unavailable" if (size is not None and solana) else "not_requested"),
    }
    request = {
        "token": token,
        "size_usd": size,
        "max_slippage_pct": data["max_slippage_pct"],
        "participation_pct": data["participation_pct"],
    }
    return _envelope(TOOL_NAME, now, request, status, mode, data, headline, points, availability, warnings)


def run_sources(args: object, *, now: dt.datetime | None = None, **_: Any) -> dict[str, Any]:
    """Describe the coverage; reads nothing from the network."""
    if not isinstance(args, dict) or args:
        raise InputError("arguments", "this tool takes none", {})
    now = now or dt.datetime.now(dt.UTC)
    tokens = [
        {"token": t, "chain": TOKENS[t]["chain"], "contract": TOKENS[t]["address"], "measured_as": TOKENS[t]["via"]} for t in SUPPORTED
    ]
    sources = [
        {"name": "dexscreener", "gives": "pool liquidity, 24 hour volume and price per pool", "keyless": True},
        {"name": "jupiter", "gives": "an executable quote for the exact size (Solana tokens only)", "keyless": True},
    ]
    data = {"tokens": tokens, "sources": sources}
    headline = f"exit_liquidity_check covers {len(tokens)} tokens by canonical contract, from 2 keyless sources."
    return _envelope(SOURCES_TOOL_NAME, now, {}, "ok", "unknown", data, headline, [], {"coverage": "available"}, [])
