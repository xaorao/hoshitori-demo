"""Synthetic RYO responses: valid ``result`` envelopes for the six tools, built by hand from the documented shapes.

The shapes (members and types) follow ``RYO_API_COMPATIBILITY`` section 6 and what the live recordings showed for four of the tools; the
values are invented. ``market_overview`` and ``compare_tokens`` returned 503 in the first live pass, so their shapes rest on third-party
recordings and these builders are labelled synthetic wherever they are used.
"""

from __future__ import annotations

import copy
import json
from typing import Any

AS_OF = "2026-10-29T12:00:00Z"


def _asset(symbol: str = "SOL", name: str = "Solana") -> dict[str, Any]:
    return {"symbol": symbol, "name": name, "chain": None, "contract": None, "rank": 7}


def analyze_token_data() -> dict[str, Any]:
    return {
        "asset": _asset(),
        "market": {"price_usd": 100.5, "market_cap_usd": 5.9e10, "fully_diluted_value_usd": 6.4e10, "volume_24h_usd": 2.9e9},
        "performance": {"change_1h_pct": 0.1, "change_24h_pct": -1.02, "change_7d_pct": -3.79, "change_30d_pct": -16.69},
        "technical_analysis": {"trend": "down", "rsi_14": 39.8, "momentum_30d_pct": -16.69, "atr_14_pct": 3.79},
        "intelligence": {"narrative": "A large-cap platform token.", "catalysts": ["Deep liquidity"], "risks": ["Sustained downtrend"]},
        "verdict": "avoid",
    }


def deep_analysis_data() -> dict[str, Any]:
    return {
        "asset": _asset(),
        "market": {
            "mcap_usd": 5.9e10,
            "fdv_usd": 6.4e10,
            "volume_24h_usd": 2.9e9,
            "dominance_pct": 1.7,
            "circulating_supply": 5.8e8,
            "max_supply": None,
            "price_usd": 100.5,
        },
        "performance": {"h1": 0.1, "h24": -1.02, "d7": -3.79, "d30": -16.69},
        "technical_analysis": {"trend": "down", "rsi_14": 39.8, "momentum_30d_pct": -16.69, "atr_14_pct": 3.79},
        "confluence": {
            "state": "bearish",
            "score": 0.25,
            "gates": [{"name": "trend", "passed": False, "detail": {"trend": "down", "rsi_14": 39.8}}],
            "status": "ok",
        },
        "verdict": {
            "call": "avoid",
            "headline": "SOL: avoid",
            "bottom_line": "Weak trend.",
            "key_driver": "trend",
            "what_changes_it": "A reversal.",
            "confluence_score": 0.25,
        },
        "token_profile": {
            "ok": True,
            "data": {
                "type": "profile",
                "skill_id": "token_profile",
                "timestamp": AS_OF,
                "data": {
                    "status": "ok",
                    "confidence": "medium",
                    "missing_or_stale_inputs": ["holders"],
                    "decision_report": {"title": "SOL", "brand_line": "x", "conclusion": "c", "analysis": "a"},
                    "action_guidance": {"bias": "neutral", "reference_action": "wait"},
                },
            },
            "execution": {"status": "ok", "elapsed_ms": 1200},
        },
        "derivatives": {
            "symbol": "SOL",
            "funding_rate_bps": 0.8,
            "funding_trend": "flat",
            "open_interest_change_24h_pct": -2.1,
            "long_short_ratio": None,
            "liquidation_pressure": "low",
            "squeeze_risk": "low",
            "veto": False,
            "veto_reason": None,
        },
        "trade_plan": {
            "entry": 100.5,
            "stop": 95.0,
            "targets": [110.0, 118.0],
            "atr_14_usd": 3.8,
            "atr_14_pct": 3.79,
            "atr_multiplier": 1.5,
            "risk_reward": [1.7, 3.2],
            "method": "atr",
            "preview_only": True,
        },
        "intelligence": {"narrative": "A large-cap platform token.", "catalysts": [], "risks": ["Sustained downtrend"]},
    }


def scan_market_data() -> dict[str, Any]:
    candidate = {
        "rank": 1,
        "symbol": "AAA",
        "name": "Alpha",
        "chain": None,
        "contract": "0xabc",
        "price_usd": 1.25,
        "change_24h_pct": 12.5,
        "market_cap_usd": 3.0e8,
        "volume_24h_usd": 5.0e7,
        "turnover_ratio": 0.17,
        "momentum_score": 71.2,
        "established_asset": False,
        "reason": "Strong momentum.",
    }
    return {
        "filters": {"chain": None, "theme": None, "limit": 5},
        "selection_method": "momentum",
        "candidate_count": 1,
        "candidates": [candidate],
        "excluded_candidate_count": 3,
    }


def market_overview_data() -> dict[str, Any]:
    mover = {"symbol": "AAA", "name": "Alpha", "chain": None, "price_usd": 1.25, "change_24h_pct": 12.5}
    return {
        "regime": "risk_off",
        "sentiment": {"fear_greed_index": 31.0, "label": "Fear"},
        "market": {
            "total_market_cap_usd": 2.1e12,
            "total_volume_24h_usd": 8.0e10,
            "market_cap_change_24h_pct": -1.1,
            "btc_dominance_pct": 56.2,
            "eth_dominance_pct": 12.9,
            "breadth": 0.38,
            "breadth_details": {"universe_size": 100, "advancing": 38, "declining": 60, "unchanged": 2, "method": "listing snapshot"},
        },
        "top_movers": {"gainers": [mover], "losers": [dict(mover, symbol="BBB", name="Beta", change_24h_pct=-9.0)]},
    }


def compare_tokens_data() -> dict[str, Any]:
    def token(symbol: str, name: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "name": name,
            "chain": None,
            "rank": 7,
            "price_usd": 100.5,
            "changes": {"h1": 0.1, "h24": -1.0, "d7": -3.8, "d30": -16.7},
            "metrics": {"market_cap_usd": 5.9e10, "volume_24h_usd": 2.9e9, "rsi_14": 39.8, "atr_14_pct": 3.79, "trend": "down"},
            "verdict": "avoid",
            "token_profile": None,
        }

    return {
        "intent": "swing",
        "tokens": [token("SOL", "Solana"), token("AVAX", "Avalanche")],
        "factors": [{"key": "momentum", "label": "Momentum", "winner": "AVAX", "scores": {"SOL": 0.4, "AVAX": 0.6}}],
        "winner": "AVAX",
        "conclusion": {
            "pick": "AVAX",
            "headline": "AVAX edges SOL.",
            "rationale": "Momentum.",
            "runner_up_case": "SOL if volume returns.",
            "confidence": "low",
            "method": "deterministic",
        },
        "coverage_by_token": {
            "SOL": {"market_data": "available", "token_profile": "unavailable"},
            "AVAX": {"market_data": "available", "token_profile": "unavailable"},
        },
    }


def monitor_data() -> dict[str, Any]:
    return {
        "observation_as_of": AS_OF,
        "status": "ok",
        "source_attribution": "a market data provider",
        "summary": "Sentiment fell.",
        "evidence": {
            "fear_greed": {
                "data_date": "2026-10-29",
                "value": 31,
                "label": "Fear",
                "baseline_7d_date": "2026-10-22",
                "baseline_7d_value": 45,
                "change_7d_points": -14,
                "change_direction": "down",
                "material_shift": True,
            },
            "funding": {
                "data_date": "2026-10-29",
                "latest_bps": 0.4,
                "current_7d_window": {"start_date": "2026-10-23", "end_date": "2026-10-29", "mean_bps": 0.5},
                "previous_7d_window": {"start_date": "2026-10-16", "end_date": "2026-10-22", "mean_bps": 0.9},
                "wow_change_bps": -0.4,
                "current_7d_percentile_90d": 20,
                "crowding_state": "cooling",
                "wow_change_pct": -44.4,
            },
            "liquidation": {
                "data_date": "2026-10-29",
                "total_usd": 2.5e8,
                "dominant_liquidated_side": "long",
                "percentile_90d": 70.0,
                "pressure_state": "elevated",
                "coverage_scope": "BTC",
                "long_share_pct": 68.0,
            },
            "altseason": {"data_date": "2026-10-29", "index": 22, "phase": "bitcoin_season"},
            "sentiment_regime": "fearful",
        },
        "source": {"type": "skill", "skill_id": "sentiment", "timestamp": AS_OF, "attribution": "a provider"},
        "diagnostics": {"execution": {"status": "ok", "elapsed_ms": 900}},
    }


DATA = {
    "market_overview": market_overview_data,
    "scan_market": scan_market_data,
    "analyze_token": analyze_token_data,
    "deep_analysis": deep_analysis_data,
    "compare_tokens": compare_tokens_data,
    "monitor_market_sentiment_shift": monitor_data,
}

AVAILABILITY = {
    "market_overview": {
        "market_regime": "available",
        "market_totals": "available",
        "market_sentiment": "available",
        "market_breadth": "available",
        "top_movers": "available",
    },
    "scan_market": {"ranked_candidates": "available", "theme_filter": "not_requested"},
    "analyze_token": {"market_data": "available", "technical_analysis": "available", "market_intelligence": "available"},
    "deep_analysis": {
        "market_data": "available",
        "technical_analysis": "available",
        "token_profile": "available",
        "derivatives": "available",
        "trade_plan": "available",
    },
    "compare_tokens": {
        "token_market_data": "available",
        "comparison_factors": "available",
        "token_profiles": "unavailable",
        "conclusion": "available",
    },
    "monitor_market_sentiment_shift": {"research": "available", "evidence": "available", "diagnostics": "available"},
}

REQUESTS = {
    "market_overview": {},
    "scan_market": {"top_n": 5},
    "analyze_token": {"symbol": "SOL"},
    "deep_analysis": {"symbol": "SOL", "include_perp": True},
    "compare_tokens": {"symbols": "SOL, AVAX", "intent": "swing"},
    "monitor_market_sentiment_shift": {"time_window": "7d"},
}


def result(tool: str, /, **overrides: Any) -> dict[str, Any]:
    """A valid ``result`` envelope; ``overrides`` replace top-level members."""
    out: dict[str, Any] = {
        "schema_version": "1.0",
        "tool": tool,
        "status": "ok",
        "data_mode": "live",
        "as_of": AS_OF,
        "request": copy.deepcopy(REQUESTS[tool]),
        "data": DATA[tool](),
        "summary": {"headline": f"{tool} headline.", "key_points": ["A point."]},
        "availability": dict(AVAILABILITY[tool]),
        "warnings": [],
    }
    out.update(overrides)
    return out


def rest_body(tool: str, res: dict[str, Any] | None = None, latency_ms: int | None = 642) -> bytes:
    body: dict[str, Any] = {"tool": tool, "result": res if res is not None else result(tool)}
    if latency_ms is not None:
        body["latency_ms"] = latency_ms
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()


def mcp_text_body(tool: str, res: dict[str, Any] | None = None, request_id: int = 3) -> bytes:
    text = json.dumps(res if res is not None else result(tool), separators=(",", ":"), ensure_ascii=False)
    body = {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}], "isError": False}}
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()


def mcp_error_body(text: str, request_id: int = 3) -> bytes:
    body = {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}], "isError": True}}
    return json.dumps(body, separators=(",", ":")).encode()


def error_body(code: str, message: str, trace_id: str | None = "431518c21f1b42a0b810d5bb0a22bdbe", details: Any = None) -> bytes:
    return json.dumps({"code": code, "message": message, "details": details, "trace_id": trace_id}, separators=(",", ":")).encode()
