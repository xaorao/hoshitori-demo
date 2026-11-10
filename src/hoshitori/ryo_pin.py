"""The structure of RYO's six tools as read from the authenticated catalogue: what the product was built against.

This is the pin of ``ryo_catalogue.schema_pin``: argument names, types, requirements and enums, without RYO's descriptions. A live catalogue
that differs from it in structure is drift (``ryo_catalogue.diff``); a live catalogue that only words things differently is not.
Regenerate with ``scripts/make_ryo_pin.py`` from a catalogue read with a builder key; a change here is a change of the product's contract.
"""

from __future__ import annotations

from typing import Any, Final

PIN: Final[dict[str, dict[str, Any]]] = {
    "analyze_token": {
        "additionalProperties": False,
        "properties": {"chain": {"type": "string"}, "symbol": {"type": "string"}},
        "required": ["symbol"],
        "type": "object",
    },
    "compare_tokens": {
        "additionalProperties": False,
        "properties": {"intent": {"enum": ["swing", "hold", "spot"], "type": "string"}, "symbols": {"type": "string"}},
        "required": ["symbols"],
        "type": "object",
    },
    "deep_analysis": {
        "additionalProperties": False,
        "properties": {"chain": {"type": "string"}, "include_perp": {"type": "boolean"}, "symbol": {"type": "string"}},
        "required": ["symbol"],
        "type": "object",
    },
    "market_overview": {"additionalProperties": False, "properties": {}, "type": "object"},
    "monitor_market_sentiment_shift": {
        "additionalProperties": False,
        "properties": {"time_window": {"default": "7d", "enum": ["7d"], "type": "string"}},
        "type": "object",
    },
    "scan_market": {
        "additionalProperties": False,
        "properties": {
            "chain": {"type": "string"},
            "filter_direction": {"type": "string"},
            "theme": {"type": "string"},
            "top_n": {"type": "number"},
        },
        "type": "object",
    },
}
