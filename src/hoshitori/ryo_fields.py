"""What each RYO tool's ``data`` may contain, and which of it is observation: the evidence-class table (contract section 6).

The independent arms of the league may read RYO's observations and never its interpretations. That rule is only as good as the table that
says which field is which, so the table is code: one specification per tool, walked against every response.

* ``OBSERVATION``     a measurement or an identity (a price, a change, an RSI, a funding rate, a Fear and Greed value);
* ``INTERPRETATION``  something RYO concluded (a verdict, a label chosen by a rule, a score, a plan, prose);
* ``META``            about the response, not the market (a method name, a count of what was filtered out, timings).

When it is unclear the field is an interpretation: wrongly withholding an observation costs the arms a field, wrongly passing an
interpretation spoils the comparison. A member the specification does not name is never read (``unmapped``). A member that is named but
has the wrong type is ``CONTRACT_DRIFT``: the tool has changed shape and is unavailable until a person has looked. A member that is
``null`` is unknown, and unknown is never zero.

Numbers are returned as the decimal string written in the raw bytes (``jsonnum``), text as NFC, and nothing is converted through a float.
The table is generated from these specifications (``classification_table``) and hashed (``table_sha256``) so it can be pinned in the
protocol before a scored period. It rests on the shapes recorded live for all six tools, in two passes of the same plan
(``RYO_API_COMPATIBILITY``): every real answer walks with nothing missing and nothing unknown. It is a reading of those answers, not a
published specification.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from hoshitori import canonical, jsonnum
from hoshitori.connector import ConnectorError, ErrorClass

MAX_ITEMS: Final = 500


class FieldClass(StrEnum):
    OBSERVATION = "OBSERVATION"
    INTERPRETATION = "INTERPRETATION"
    META = "META"


OBS, INT, META = FieldClass.OBSERVATION, FieldClass.INTERPRETATION, FieldClass.META


@dataclass(frozen=True)
class Leaf:
    kind: str  # text, number, bool
    klass: FieldClass
    nullable: bool = True


@dataclass(frozen=True)
class Obj:
    members: Mapping[str, Spec]
    required: frozenset[str] = frozenset()
    optional: bool = False  # a section the answer leaves null when it was not asked for (``include_perp: false``)


@dataclass(frozen=True)
class Seq:
    item: Spec


@dataclass(frozen=True)
class Keyed:
    """An object whose member names are data (a symbol), all with the same specification."""

    value: Spec


@dataclass(frozen=True)
class Opaque:
    """A value of variable structure that is not exposed at all; its class applies to the whole of it."""

    klass: FieldClass


Spec = Leaf | Obj | Seq | Keyed | Opaque


def _num(klass: FieldClass = OBS) -> Leaf:
    return Leaf("number", klass)


def _text(klass: FieldClass = OBS, *, nullable: bool = True) -> Leaf:
    return Leaf("text", klass, nullable)


def _bool(klass: FieldClass = INT) -> Leaf:
    return Leaf("bool", klass)


def _obj(members: Mapping[str, Spec], *required: str) -> Obj:
    return Obj(members, frozenset(required))


def _section(members: Mapping[str, Spec], *required: str) -> Obj:
    """An object that the answer may leave null when it was not asked for; every field of it is then absent, never zero."""
    return Obj(members, frozenset(required), True)


_ASSET = _obj({"symbol": _text(OBS, nullable=False), "name": _text(), "chain": _text(), "contract": _text(), "rank": _num()}, "symbol")
_TECH = _obj({"trend": _text(INT), "rsi_14": _num(), "momentum_30d_pct": _num(), "atr_14_pct": _num()})
_INTELLIGENCE = _obj({"narrative": _text(INT), "catalysts": Seq(_text(INT, nullable=False)), "risks": Seq(_text(INT, nullable=False))})
_MOVER = _obj({"symbol": _text(), "name": _text(), "chain": _text(), "price_usd": _num(), "change_24h_pct": _num()})

SPECS: Final[dict[str, Obj]] = {
    "market_overview": _obj(
        {
            "regime": _text(INT),
            "sentiment": _obj({"fear_greed_index": _num(), "label": _text()}),
            "market": _obj(
                {
                    "total_market_cap_usd": _num(),
                    "total_volume_24h_usd": _num(),
                    "market_cap_change_24h_pct": _num(),
                    "btc_dominance_pct": _num(),
                    "eth_dominance_pct": _num(),
                    "breadth": _num(),
                    "breadth_details": _obj(
                        {"universe_size": _num(), "advancing": _num(), "declining": _num(), "unchanged": _num(), "method": _text(META)}
                    ),
                }
            ),
            "top_movers": _obj({"gainers": Seq(_MOVER), "losers": Seq(_MOVER)}),
        }
    ),
    "scan_market": _obj(
        {
            "filters": _obj({"chain": _text(META), "theme": _text(META), "limit": _num(META)}),
            "selection_method": _text(META),
            "candidate_count": _num(META),
            "excluded_candidate_count": _num(META),
            "candidates": Seq(
                _obj(
                    {
                        "rank": _num(INT),
                        "symbol": _text(OBS, nullable=False),
                        "name": _text(),
                        "chain": _text(),
                        "contract": _text(),
                        "price_usd": _num(),
                        "change_24h_pct": _num(),
                        "market_cap_usd": _num(),
                        "volume_24h_usd": _num(),
                        "turnover_ratio": _num(),
                        "momentum_score": _num(INT),
                        "established_asset": _bool(INT),
                        "reason": _text(INT),
                    },
                    "symbol",
                )
            ),
        },
        "candidates",
        "candidate_count",
    ),
    "analyze_token": _obj(
        {
            "asset": _ASSET,
            "market": _obj({"price_usd": _num(), "market_cap_usd": _num(), "fully_diluted_value_usd": _num(), "volume_24h_usd": _num()}),
            "performance": _obj({"change_1h_pct": _num(), "change_24h_pct": _num(), "change_7d_pct": _num(), "change_30d_pct": _num()}),
            "technical_analysis": _TECH,
            "intelligence": _INTELLIGENCE,
            "verdict": _text(INT),
        },
        "asset",
    ),
    "deep_analysis": _obj(
        {
            "asset": _ASSET,
            "market": _obj(
                {
                    "mcap_usd": _num(),
                    "fdv_usd": _num(),
                    "volume_24h_usd": _num(),
                    "dominance_pct": _num(),
                    "circulating_supply": _num(),
                    "max_supply": _num(),
                    "price_usd": _num(),
                }
            ),
            "performance": _obj({"h1": _num(), "h24": _num(), "d7": _num(), "d30": _num()}),
            "technical_analysis": _TECH,
            "confluence": _obj(
                {
                    "state": _text(INT),
                    "score": _num(INT),
                    "gates": Seq(_obj({"name": _text(INT), "passed": _bool(INT), "detail": Opaque(INT)})),
                    "status": _text(META),
                }
            ),
            "verdict": _obj(
                {
                    "call": _text(INT),
                    "headline": _text(INT),
                    "bottom_line": _text(INT),
                    "key_driver": _text(INT),
                    "what_changes_it": _text(INT),
                    "confluence_score": _num(INT),
                }
            ),
            "token_profile": Opaque(INT),
            "derivatives": _section(
                {
                    "symbol": _text(META),
                    "funding_rate_bps": _num(),
                    "funding_trend": _text(INT),
                    "open_interest_change_24h_pct": _num(),
                    "long_short_ratio": _num(),
                    "liquidation_pressure": _text(INT),
                    "squeeze_risk": _text(INT),
                    "veto": _bool(INT),
                    "veto_reason": _text(INT),
                }
            ),
            "trade_plan": Opaque(INT),
            "intelligence": _INTELLIGENCE,
        },
        "asset",
    ),
    "compare_tokens": _obj(
        {
            "intent": _text(META),
            "tokens": Seq(
                _obj(
                    {
                        "symbol": _text(OBS, nullable=False),
                        "name": _text(),
                        "chain": _text(),
                        "rank": _num(),
                        "price_usd": _num(),
                        "changes": _obj({"h1": _num(), "h24": _num(), "d7": _num(), "d30": _num()}),
                        "metrics": _obj(
                            {
                                "market_cap_usd": _num(),
                                "volume_24h_usd": _num(),
                                "rsi_14": _num(),
                                "atr_14_pct": _num(),
                                "trend": _text(INT),
                            }
                        ),
                        "verdict": _text(INT),
                        "token_profile": Opaque(INT),
                    },
                    "symbol",
                )
            ),
            "factors": Seq(_obj({"key": _text(META), "label": _text(META), "winner": _text(INT), "scores": Keyed(_num(INT))})),
            "winner": _text(INT),
            "conclusion": _obj(
                {
                    "pick": _text(INT),
                    "headline": _text(INT),
                    "rationale": _text(INT),
                    "runner_up_case": _text(INT),
                    "confidence": _text(INT),
                    "method": _text(INT),
                }
            ),
            "coverage_by_token": Keyed(_obj({"market_data": _text(META), "token_profile": _text(META)})),
        },
        "tokens",
    ),
    "monitor_market_sentiment_shift": _obj(
        {
            "observation_as_of": _text(META),
            "status": _text(META),
            "source_attribution": _text(META),
            "summary": _text(INT),
            "evidence": _obj(
                {
                    "fear_greed": _obj(
                        {
                            "data_date": _text(META),
                            "value": _num(),
                            "label": _text(),
                            "baseline_7d_date": _text(META),
                            "baseline_7d_value": _num(),
                            "change_7d_points": _num(),
                            "change_direction": _text(INT),
                            "material_shift": _bool(INT),
                        }
                    ),
                    "funding": _obj(
                        {
                            "data_date": _text(META),
                            "latest_bps": _num(),
                            "current_7d_window": _obj({"start_date": _text(META), "end_date": _text(META), "mean_bps": _num()}),
                            "previous_7d_window": _obj({"start_date": _text(META), "end_date": _text(META), "mean_bps": _num()}),
                            "wow_change_bps": _num(),
                            "current_7d_percentile_90d": _num(),
                            "crowding_state": _text(INT),
                            "wow_change_pct": _num(),
                        }
                    ),
                    "liquidation": _obj(
                        {
                            "data_date": _text(META),
                            "total_usd": _num(),
                            "dominant_liquidated_side": _text(),
                            "percentile_90d": _num(),
                            "pressure_state": _text(INT),
                            "coverage_scope": _text(META),
                            "long_share_pct": _num(),
                        }
                    ),
                    "altseason": _obj({"data_date": _text(META), "index": _num(), "phase": _text(INT)}),
                    "sentiment_regime": _text(INT),
                }
            ),
            "source": _obj({"type": _text(META), "skill_id": _text(META), "timestamp": _text(META), "attribution": _text(META)}),
            "diagnostics": _obj({"execution": _obj({"status": _text(META), "elapsed_ms": _num(META)})}),
        }
    ),
}

# the envelope members that are not ``data``
ENVELOPE_CLASSES: Final[dict[str, FieldClass]] = {
    "summary.headline": INT,
    "summary.key_points[]": INT,
    "availability{}": META,
    "warnings[]": META,
}


@dataclass(frozen=True)
class FieldValue:
    path: str
    klass: FieldClass
    kind: str
    value: str | bool | None  # numbers as plain decimal text, text as NFC, None when the response said null


@dataclass
class Walked:
    fields: list[FieldValue] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # named by the specification, absent from the response
    unmapped: list[str] = field(default_factory=list)  # present in the response, named by nothing: never read
    opaque: list[str] = field(default_factory=list)  # present, not exposed by design

    def observations(self) -> list[FieldValue]:
        """What an independent arm may see."""
        return [f for f in self.fields if f.klass is OBS]


def _drift(path: str, message: str) -> ConnectorError:
    return ConnectorError(ErrorClass.CONTRACT_DRIFT, f"RYO's data.{path} {message}" if path else f"RYO's data {message}")


def _describe(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, jsonnum.Num):
        return "a number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, dict):
        return "an object"
    if isinstance(value, list):
        return "a list"
    return type(value).__name__


def _leaf(path: str, spec: Leaf, value: Any, out: Walked) -> None:
    if value is None:
        if not spec.nullable:
            raise _drift(path, "is null but must have a value")
        out.fields.append(FieldValue(path, spec.klass, spec.kind, None))
        return
    if spec.kind == "number" and isinstance(value, jsonnum.Num):
        try:
            out.fields.append(FieldValue(path, spec.klass, spec.kind, value.plain()))
        except jsonnum.JsonError as exc:
            raise ConnectorError(
                ErrorClass.MALFORMED, f"RYO's data.{path} holds a number that cannot be read exactly ({exc.code})"
            ) from exc
    elif spec.kind == "text" and isinstance(value, str):
        out.fields.append(FieldValue(path, spec.klass, spec.kind, unicodedata.normalize("NFC", value)))
    elif spec.kind == "bool" and isinstance(value, bool):
        out.fields.append(FieldValue(path, spec.klass, spec.kind, value))
    else:
        raise _drift(path, f"is {_describe(value)} but must be {'a ' + spec.kind if spec.kind != 'text' else 'text'}")


def _absent(path: str, spec: Spec, out: Walked) -> None:
    """Every field of a section that was left out, as absent."""
    if isinstance(spec, Obj):
        for name in sorted(spec.members):
            _absent(f"{path}.{name}", spec.members[name], out)
    else:
        out.missing.append(path)


def _walk(path: str, spec: Spec, value: Any, out: Walked) -> None:
    if isinstance(spec, Leaf):
        _leaf(path, spec, value, out)
    elif isinstance(spec, Opaque):
        out.opaque.append(path)
    elif isinstance(spec, Obj):
        if value is None and spec.optional:
            _absent(path, spec, out)
            return
        if not isinstance(value, dict):
            raise _drift(path, f"is {_describe(value)} but must be an object")
        for name in sorted(spec.members):
            child = f"{path}.{name}" if path else name
            if name not in value:
                if name in spec.required:
                    raise _drift(child, "is missing")
                out.missing.append(child)
                continue
            _walk(child, spec.members[name], value[name], out)
        for name in sorted(set(value) - set(spec.members)):
            out.unmapped.append(f"{path}.{name}" if path else name)
    elif isinstance(spec, Seq):
        if not isinstance(value, list):
            raise _drift(path, f"is {_describe(value)} but must be a list")
        if len(value) > MAX_ITEMS:
            raise ConnectorError(ErrorClass.MALFORMED, f"RYO's data.{path} has {len(value)} items, more than {MAX_ITEMS}")
        for index, item in enumerate(value):
            _walk(f"{path}[{index}]", spec.item, item, out)
    else:  # Keyed
        if not isinstance(value, dict):
            raise _drift(path, f"is {_describe(value)} but must be an object")
        if len(value) > MAX_ITEMS:
            raise ConnectorError(ErrorClass.MALFORMED, f"RYO's data.{path} has {len(value)} members, more than {MAX_ITEMS}")
        for key in sorted(value):
            _walk(f"{path}{{{unicodedata.normalize('NFC', key)}}}", spec.value, value[key], out)


def walk(tool: str, data: Mapping[str, Any]) -> Walked:
    """Read ``data`` of a ``tool`` answer against its specification. Raises ``CONTRACT_DRIFT`` or ``MALFORMED``."""
    spec = SPECS.get(tool)
    if spec is None:
        raise ConnectorError(ErrorClass.CONTRACT_DRIFT, f"there is no field specification for the tool {tool!r}")
    out = Walked()
    _walk("", spec, data, out)
    return out


# --- the table ---------------------------------------------------------------------------------------------------------------------------
def _rows(path: str, spec: Spec, rows: list[tuple[str, str, str]]) -> None:
    if isinstance(spec, Leaf):
        rows.append((path, spec.klass.value, spec.kind))
    elif isinstance(spec, Opaque):
        rows.append((path, spec.klass.value, "opaque"))
    elif isinstance(spec, Obj):
        for name in sorted(spec.members):
            _rows(f"{path}.{name}" if path else name, spec.members[name], rows)
    elif isinstance(spec, Seq):
        _rows(f"{path}[]", spec.item, rows)
    else:
        _rows(f"{path}{{}}", spec.value, rows)


def classification_table(tool: str) -> list[tuple[str, str, str]]:
    """Every path pattern of a tool's ``data`` with its class and kind, in path order."""
    spec = SPECS[tool]
    rows: list[tuple[str, str, str]] = []
    _rows("", spec, rows)
    return rows


def table_sha256() -> str:
    """The hash of the whole table (all tools, and the envelope members), for the protocol to pin."""
    listing: dict[str, canonical.JsonValue] = {tool: [list(row) for row in classification_table(tool)] for tool in sorted(SPECS)}
    listing["envelope"] = [[path, klass.value] for path, klass in sorted(ENVELOPE_CLASSES.items())]
    return hashlib.sha256(canonical.dumps(listing)).hexdigest()
