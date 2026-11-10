"""Shared material for the RYO adapter tests: a synthetic catalogue written from the public guide, builders for envelopes, and a loader for
the private live recordings (skipped when they are not on this machine).

Synthetic means written by hand from the shapes in ``RYO_API_COMPATIBILITY.md`` and the builder guide; none of it is a recording. The live
recordings (the fixtures of the live service) are kept out of the public repository until the organisers say whether RYO
responses may be shown (contract section 16), so tests that need them skip, and say so.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

LIVE_DIR = Path(__file__).resolve().parents[2] / "research" / "architecture" / "fixtures" / "ryo_live"

SIX = ("market_overview", "scan_market", "analyze_token", "deep_analysis", "compare_tokens", "monitor_market_sentiment_shift")


def dumps(value: Any) -> bytes:
    """How RYO writes JSON, as read from the recordings: compact separators, non-ASCII kept."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


def synthetic_tools() -> list[dict[str, Any]]:
    def tool(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
        if required:
            schema["required"] = required
        return {"name": name, "description": description, "inputSchema": schema}

    return [
        tool(
            "market_overview",
            "Delivery: a market evidence pack. Use when: broad context is needed. Core inputs: none. Limits: research only.",
            {},
        ),
        tool(
            "scan_market",
            "Delivery: a ranked candidate set. Use when: a shortlist is needed. Core inputs: optional chain, theme, top_n. Limits: not a trade.",
            {
                "chain": {"type": "string", "description": "Chain slug."},
                "theme": {"type": "string", "description": "Theme."},
                "top_n": {"type": "number", "description": "How many."},
                "filter_direction": {"type": "string", "description": "positive, negative or all."},
            },
        ),
        tool(
            "analyze_token",
            "Delivery: a token pack. Use when: a quick read is needed. Core input: symbol. Limits: not advice.",
            {"symbol": {"type": "string", "description": "Symbol."}, "chain": {"type": "string", "description": "Chain slug."}},
            ["symbol"],
        ),
        tool(
            "deep_analysis",
            "Delivery: a deep pack. Use when: depth is needed. Core input: symbol. Limits: not wallet analysis.",
            {
                "symbol": {"type": "string", "description": "Symbol."},
                "chain": {"type": "string", "description": "Chain slug."},
                "include_perp": {"type": "boolean", "description": "Derivatives."},
            },
            ["symbol"],
        ),
        tool(
            "compare_tokens",
            "Delivery: a comparison. Use when: assets are compared. Core inputs: symbols; optional intent. Limits: disclosed.",
            {
                "symbols": {"type": "string", "description": "2-4 symbols."},
                "intent": {"type": "string", "description": "Horizon.", "enum": ["swing", "hold", "spot"]},
            },
            ["symbols"],
        ),
        tool(
            "monitor_market_sentiment_shift",
            "Delivery: a sentiment pack. Use when: a shift is checked. Core inputs: optional time_window. Limits: no causality.",
            {"time_window": {"type": "string", "enum": ["7d"], "default": "7d", "description": "Window."}},
        ),
    ]


def catalogue_body(mutate: Any = None) -> bytes:
    tools = copy.deepcopy(synthetic_tools())
    if mutate is not None:
        mutate(tools)  # a mutation edits the list in place; what it returns is ignored
    return dumps(tools)


def live_fixture(prefix: str) -> dict[str, Any]:
    """A private live recording whose file name starts with ``prefix``; the test is skipped if it is not here."""
    matches = sorted(LIVE_DIR.glob(prefix + "*.json")) if LIVE_DIR.is_dir() else []
    if not matches:
        pytest.skip(f"the private live recording {prefix!r} is not on this machine")
    data: dict[str, Any] = json.loads(matches[0].read_text())
    body = data["response"].get("body")
    raw = dumps(body)
    assert hashlib.sha256(raw).hexdigest() == data["response"]["body_sha256"], "the recording no longer reproduces its raw bytes"
    data["raw"] = raw
    return data
