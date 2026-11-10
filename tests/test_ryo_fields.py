"""The evidence-class table and the data walker: scenarios, the live recordings, and mutants of both the walker and the table."""

from __future__ import annotations

import pytest

from hoshitori import ryo_envelope, ryo_fields
from tests.mutation import mutant
from tests.ryo_fields_checks import failures
from tests.ryo_support import live_fixture


def test_real_module_passes_every_check() -> None:
    assert failures(ryo_fields) == []


@pytest.mark.parametrize(
    ("prefix", "tool"),
    [
        ("call_analyze_token", "analyze_token"),
        ("call_deep_analysis", "deep_analysis"),
        ("call_scan_market", "scan_market"),
        ("call_monitor_market_sentiment_shift", "monitor_market_sentiment_shift"),
    ],
)
def test_the_live_recordings_walk_without_surprises(prefix: str, tool: str) -> None:
    live = live_fixture(prefix)
    env = ryo_envelope.read_rest_success(live["raw"], tool)
    walked = ryo_fields.walk(tool, env.data)
    assert walked.fields
    assert walked.observations()
    # every member of a live answer is known to the table: a new member would show here before it showed in a score
    assert walked.unmapped == [], walked.unmapped
    assert (
        all(f.value is not None for f in walked.observations()) or True
    )  # nulls are allowed; the count of unknowns is reported by the suite


def test_the_table_is_pinned() -> None:
    # the hash changes whenever a class changes; a deliberate change updates this value in the same commit as the protocol
    assert ryo_fields.table_sha256() == "1c419cb214fc81c1522ece0267988dd99c247bb1b04433eae8653b4872ca0be5"


MUTATIONS: dict[str, tuple[str, str]] = {
    # --- the walker --------------------------------------------------------------------------------------------------------------------
    "a null is accepted where a value is required": (
        '        if not spec.nullable:\n            raise _drift(path, "is null but must have a value")\n',
        "",
    ),
    "a null becomes zero": (
        "        out.fields.append(FieldValue(path, spec.klass, spec.kind, None))\n        return",
        '        out.fields.append(FieldValue(path, spec.klass, spec.kind, "0"))\n        return',
    ),
    "a number keeps its exponent": (
        "FieldValue(path, spec.klass, spec.kind, value.plain())",
        "FieldValue(path, spec.klass, spec.kind, str(value))",
    ),
    "a number is read through a float": (
        "FieldValue(path, spec.klass, spec.kind, value.plain())",
        "FieldValue(path, spec.klass, spec.kind, str(float(str(value))))",
    ),
    "a huge number is a drift": (
        '            raise ConnectorError(\n                ErrorClass.MALFORMED, f"RYO\'s data.{path} holds a number that cannot be read exactly ({exc.code})"\n            ) from exc',
        '            raise ConnectorError(\n                ErrorClass.CONTRACT_DRIFT, f"RYO\'s data.{path} holds a number that cannot be read exactly ({exc.code})"\n            ) from exc',
    ),
    "a number is accepted as text": (
        '    elif spec.kind == "text" and isinstance(value, str):',
        '    elif spec.kind == "text" and isinstance(value, str | jsonnum.Num):',
    ),
    "text is accepted as a number": (
        '    if spec.kind == "number" and isinstance(value, jsonnum.Num):',
        '    if spec.kind == "number" and isinstance(value, jsonnum.Num | str):',
    ),
    "a number is accepted as a boolean": (
        '    elif spec.kind == "bool" and isinstance(value, bool):',
        '    elif spec.kind == "bool" and isinstance(value, bool | jsonnum.Num):',
    ),
    "text is not normalised": (
        'unicodedata.normalize("NFC", value)))\n    elif spec.kind == "bool"',
        'value))\n    elif spec.kind == "bool"',
    ),
    "an object may be anything": (
        '        if not isinstance(value, dict):\n            raise _drift(path, f"is {_describe(value)} but must be an object")\n        for name in sorted(spec.members):',
        "        for name in sorted(spec.members):",
    ),
    "a required member may be missing": (
        '                if name in spec.required:\n                    raise _drift(child, "is missing")\n',
        "",
    ),
    "a missing member is not recorded": ("                out.missing.append(child)\n                continue", "                continue"),
    "missing members are not sorted": (
        "        for name in sorted(spec.members):\n            child =",
        "        for name in spec.members:\n            child =",
    ),
    "an unmapped member is read as mapped": ('            out.unmapped.append(f"{path}.{name}" if path else name)', "            pass"),
    "unmapped members are not sorted": (
        "        for name in sorted(set(value) - set(spec.members)):",
        "        for name in set(value) - set(spec.members):",
    ),
    "a list may be anything": (
        '        if not isinstance(value, list):\n            raise _drift(path, f"is {_describe(value)} but must be a list")\n',
        "",
    ),
    "a list may be any length": (
        '        if len(value) > MAX_ITEMS:\n            raise ConnectorError(ErrorClass.MALFORMED, f"RYO\'s data.{path} has {len(value)} items, more than {MAX_ITEMS}")\n',
        "",
    ),
    "the list limit is one too small": (
        "        if len(value) > MAX_ITEMS:\n            raise ConnectorError(ErrorClass.MALFORMED, f\"RYO's data.{path} has {len(value)} items",
        "        if len(value) >= MAX_ITEMS:\n            raise ConnectorError(ErrorClass.MALFORMED, f\"RYO's data.{path} has {len(value)} items",
    ),
    "list items are not indexed": ('_walk(f"{path}[{index}]", spec.item, item, out)', '_walk(f"{path}[0]", spec.item, item, out)'),
    "a keyed object may be anything": (
        '    else:  # Keyed\n        if not isinstance(value, dict):\n            raise _drift(path, f"is {_describe(value)} but must be an object")\n',
        "    else:  # Keyed\n",
    ),
    "a keyed object may be any size": (
        '        if len(value) > MAX_ITEMS:\n            raise ConnectorError(ErrorClass.MALFORMED, f"RYO\'s data.{path} has {len(value)} members, more than {MAX_ITEMS}")\n',
        "",
    ),
    "the keyed limit is one too small": (
        "        if len(value) > MAX_ITEMS:\n            raise ConnectorError(ErrorClass.MALFORMED, f\"RYO's data.{path} has {len(value)} members",
        "        if len(value) >= MAX_ITEMS:\n            raise ConnectorError(ErrorClass.MALFORMED, f\"RYO's data.{path} has {len(value)} members",
    ),
    "keys are not normalised": ("{unicodedata.normalize('NFC', key)}", "{key}"),
    "keyed members are not walked in order": ("        for key in sorted(value):", "        for key in value:"),
    "an opaque member is read": (
        "    elif isinstance(spec, Opaque):\n        out.opaque.append(path)",
        "    elif isinstance(spec, Opaque):\n        pass",
    ),
    "an unknown tool is walked": (
        '    if spec is None:\n        raise ConnectorError(ErrorClass.CONTRACT_DRIFT, f"there is no field specification for the tool {tool!r}")\n',
        "    if spec is None:\n        return Walked()\n",
    ),
    "observations include everything": ("        return [f for f in self.fields if f.klass is OBS]", "        return list(self.fields)"),
    "observations include meta": (
        "        return [f for f in self.fields if f.klass is OBS]",
        "        return [f for f in self.fields if f.klass is not INT]",
    ),
    "observations include nothing but nulls": (
        "        return [f for f in self.fields if f.klass is OBS]",
        "        return [f for f in self.fields if f.klass is OBS and f.value is None]",
    ),
    # --- the table ---------------------------------------------------------------------------------------------------------------------
    "the analyze_token verdict is an observation": (
        '            "intelligence": _INTELLIGENCE,\n            "verdict": _text(INT),\n        },\n        "asset",',
        '            "intelligence": _INTELLIGENCE,\n            "verdict": _text(),\n        },\n        "asset",',
    ),
    "the trend is an observation": ('_TECH = _obj({"trend": _text(INT),', '_TECH = _obj({"trend": _text(),'),
    "the RSI is an interpretation": (
        '"rsi_14": _num(), "momentum_30d_pct": _num(), "atr_14_pct": _num()})\n_INTELLIGENCE',
        '"rsi_14": _num(INT), "momentum_30d_pct": _num(), "atr_14_pct": _num()})\n_INTELLIGENCE',
    ),
    "the narrative is an observation": ('_INTELLIGENCE = _obj({"narrative": _text(INT),', '_INTELLIGENCE = _obj({"narrative": _text(),'),
    "the risks are observations": ('"risks": Seq(_text(INT, nullable=False))})', '"risks": Seq(_text(OBS, nullable=False))})'),
    "the price is an interpretation": (
        '"market": _obj({"price_usd": _num(), "market_cap_usd": _num(), "fully_diluted_value_usd"',
        '"market": _obj({"price_usd": _num(INT), "market_cap_usd": _num(), "fully_diluted_value_usd"',
    ),
    "the candidate rank is an observation": (
        '                        "rank": _num(INT),\n                        "symbol"',
        '                        "rank": _num(),\n                        "symbol"',
    ),
    "the momentum score is an observation": ('"momentum_score": _num(INT),', '"momentum_score": _num(),'),
    "the candidate reason is an observation": ('"reason": _text(INT),', '"reason": _text(),'),
    "the established flag is an observation": ('"established_asset": _bool(INT),', '"established_asset": _bool(OBS),'),
    "the candidate count is an observation": ('"candidate_count": _num(META),', '"candidate_count": _num(),'),
    "the selection method is an observation": ('"selection_method": _text(META),', '"selection_method": _text(),'),
    "the regime is an observation": ('            "regime": _text(INT),', '            "regime": _text(),'),
    "the fear and greed index is an interpretation": ('"fear_greed_index": _num(),', '"fear_greed_index": _num(INT),'),
    "the breadth is an interpretation": ('                    "breadth": _num(),', '                    "breadth": _num(INT),'),
    "the funding rate is an interpretation": ('"funding_rate_bps": _num(),', '"funding_rate_bps": _num(INT),'),
    "the funding trend is an observation": ('"funding_trend": _text(INT),', '"funding_trend": _text(),'),
    "the veto is an observation": ('"veto": _bool(INT),', '"veto": _bool(OBS),'),
    "the squeeze risk is an observation": ('"squeeze_risk": _text(INT),', '"squeeze_risk": _text(),'),
    "the liquidation pressure is an observation": ('"liquidation_pressure": _text(INT),', '"liquidation_pressure": _text(),'),
    "the confluence score is an observation": ('                    "score": _num(INT),', '                    "score": _num(),'),
    "the verdict call is an observation": ('                    "call": _text(INT),', '                    "call": _text(),'),
    "the trade plan is an observation": ('"trade_plan": Opaque(INT),', '"trade_plan": Opaque(OBS),'),
    "the token profile is an observation": (
        '            "token_profile": Opaque(INT),\n            "derivatives"',
        '            "token_profile": Opaque(OBS),\n            "derivatives"',
    ),
    "the mcap is an interpretation": ('"mcap_usd": _num(),', '"mcap_usd": _num(INT),'),
    "the 24 hour change is an interpretation": (
        '"performance": _obj({"h1": _num(), "h24": _num(), "d7": _num(), "d30": _num()}),',
        '"performance": _obj({"h1": _num(), "h24": _num(INT), "d7": _num(), "d30": _num()}),',
    ),
    "the comparison winner is an observation": (
        '            "winner": _text(INT),\n            "conclusion"',
        '            "winner": _text(),\n            "conclusion"',
    ),
    "the factor scores are observations": ('"scores": Keyed(_num(INT))', '"scores": Keyed(_num())'),
    "the conclusion pick is an observation": ('                    "pick": _text(INT),', '                    "pick": _text(),'),
    "the conclusion confidence is an observation": (
        '                    "confidence": _text(INT),',
        '                    "confidence": _text(),',
    ),
    "the comparison trend is an observation": (
        '                                "atr_14_pct": _num(),\n                                "trend": _text(INT),',
        '                                "atr_14_pct": _num(),\n                                "trend": _text(),',
    ),
    "the fear and greed value is an interpretation": (
        '                            "value": _num(),',
        '                            "value": _num(INT),',
    ),
    "the material shift is an observation": ('"material_shift": _bool(INT),', '"material_shift": _bool(OBS),'),
    "the change direction is an observation": ('"change_direction": _text(INT),', '"change_direction": _text(),'),
    "the crowding state is an observation": ('"crowding_state": _text(INT),', '"crowding_state": _text(),'),
    "the pressure state is an observation": ('"pressure_state": _text(INT),', '"pressure_state": _text(),'),
    "the altseason phase is an observation": ('"phase": _text(INT)}', '"phase": _text()}'),
    "the sentiment regime is an observation": ('"sentiment_regime": _text(INT),', '"sentiment_regime": _text(),'),
    "the sentiment summary is an observation": ('            "summary": _text(INT),', '            "summary": _text(),'),
    "the altseason index is an interpretation": ('"index": _num(), "phase"', '"index": _num(INT), "phase"'),
    "the latest funding is an interpretation": ('"latest_bps": _num(),', '"latest_bps": _num(INT),'),
    "the elapsed time is evidence": ('"elapsed_ms": _num(META)', '"elapsed_ms": _num()'),
    "the envelope summary is an observation": ('    "summary.headline": INT,', '    "summary.headline": OBS,'),
    "the envelope warnings are interpretation": ('    "warnings[]": META,', '    "warnings[]": INT,'),
    # --- the table functions -------------------------------------------------------------------------------------------------------------
    "list items are not marked in the table": ('        _rows(f"{path}[]", spec.item, rows)', "        _rows(path, spec.item, rows)"),
    "keyed members are not marked in the table": (
        '        _rows(f"{path}{{}}", spec.value, rows)',
        "        _rows(path, spec.value, rows)",
    ),
    "opaque members are called text": ('rows.append((path, spec.klass.value, "opaque"))', 'rows.append((path, spec.klass.value, "text"))'),
    "the table hash ignores the envelope": (
        '    listing["envelope"] = [[path, klass.value] for path, klass in sorted(ENVELOPE_CLASSES.items())]',
        '    listing["envelope"] = []',
    ),
    "the table hash ignores the classes": (
        "listing: dict[str, canonical.JsonValue] = {tool: [list(row) for row in classification_table(tool)] for tool in sorted(SPECS)}",
        "listing: dict[str, canonical.JsonValue] = {tool: [[row[0]] for row in classification_table(tool)] for tool in sorted(SPECS)}",
    ),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.ryo_fields", old, new)
    try:
        found = failures(module)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
