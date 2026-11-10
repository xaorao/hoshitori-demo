"""Checks of the RYO catalogue reader, drift detector and argument checker, runnable against the real module or a mutant."""

from __future__ import annotations

import copy
import types
from typing import Any

from tests.ryo_support import SIX, catalogue_body, dumps, synthetic_tools


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    E = mod.ErrorClass

    def parses(label: str, raw: bytes) -> Any:
        try:
            return mod.parse_catalogue(raw)
        except Exception as exc:
            bad.append(f"{label}: refused ({exc})")
            return None

    def refuses(label: str, raw: bytes, error_class: Any) -> None:
        try:
            mod.parse_catalogue(raw)
            bad.append(f"{label}: accepted")
        except mod.ConnectorError as exc:
            if exc.error_class is not error_class:
                bad.append(f"{label}: classed {exc.error_class.value}, expected {error_class.value}")
        except Exception as exc:
            bad.append(f"{label}: raised {type(exc).__name__}: {exc}")

    def changed(label: str, mutate: Any, error_class: Any) -> None:
        refuses(label, catalogue_body(mutate), error_class)

    # --- the good catalogue ---------------------------------------------------------------------------------------------------------
    cat = parses("synthetic catalogue", catalogue_body())
    if cat is None:
        return bad
    if sorted(cat.tools) != sorted(SIX) or cat.names() != frozenset(SIX):
        bad.append(f"tools read: {sorted(cat.tools)}")
    analyze = cat.tools["analyze_token"]
    if analyze.required() != ("symbol",) or sorted(analyze.properties()) != ["chain", "symbol"]:
        bad.append("analyze_token's schema was not read as written")
    if cat.tools["market_overview"].required() != () or cat.tools["market_overview"].properties() != {}:
        bad.append("market_overview should take nothing")
    if len(cat.sha256) != 64:
        bad.append("the catalogue hash is not a SHA-256")
    again = parses("again", catalogue_body())
    if again is None or again.sha256 != cat.sha256:
        bad.append("the hash of the same catalogue differs")
    reordered = parses("reordered", dumps(list(reversed(synthetic_tools()))))
    if reordered is None or reordered.sha256 != cat.sha256:
        bad.append("the hash depends on the order the tools are listed in")
    renamed = parses("description edited", catalogue_body(lambda t: t[0].update(description=t[0]["description"] + " More.")))
    if renamed is None or renamed.sha256 == cat.sha256:
        bad.append("the hash ignores the description")
    schemed = parses("schema edited", catalogue_body(lambda t: t[2]["inputSchema"]["properties"].update(extra={"type": "string"})))
    if schemed is None or schemed.sha256 == cat.sha256:
        bad.append("the hash ignores the schema")
    parses(
        "numbers inside a schema are kept as text", catalogue_body(lambda t: t[1]["inputSchema"]["properties"]["top_n"].update(default=5.0))
    )

    # --- an unreadable or misshapen catalogue is MALFORMED -------------------------------------------------------------------------------
    refuses("not JSON", b"<html>bad gateway</html>", E.MALFORMED)
    refuses("an object", b'{"tools": []}', E.MALFORMED)
    refuses("empty list", b"[]", E.MALFORMED)
    refuses("duplicate key", b'[{"name":"a","name":"b","description":"d","inputSchema":{}}]', E.MALFORMED)
    refuses(
        "too many tools",
        dumps(
            [
                {"name": f"t{i}", "description": "d", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}
                for i in range(65)
            ]
        ),
        E.MALFORMED,
    )
    exactly = parses(
        "64 tools",
        dumps(
            [
                {"name": f"t{i}", "description": "d", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}
                for i in range(64)
            ]
        ),
    )
    if exactly is None or len(exactly.tools) != 64:
        bad.append("64 tools are allowed")
    refuses("an entry that is not an object", dumps(["analyze_token"]), E.MALFORMED)
    changed("a missing description", lambda t: t[0].pop("description"), E.MALFORMED)
    changed("a missing name", lambda t: t[0].pop("name"), E.MALFORMED)
    changed("a missing schema", lambda t: t[0].pop("inputSchema"), E.MALFORMED)
    changed("an extra entry key", lambda t: t[0].update(outputSchema={}), E.MALFORMED)
    for bad_name in ("Analyze", "1abc", "", "a" * 65, "has space", "dash-name", "é"):
        changed(f"the name {bad_name!r}", lambda t, n=bad_name: t[0].update(name=n), E.MALFORMED)
    for good_name in ("a", "a" * 64, "tool_2"):
        if parses(f"the name {good_name!r}", catalogue_body(lambda t, n=good_name: t[0].update(name=n))) is None:
            bad.append(f"the name {good_name!r} is valid")
    changed("a numeric name", lambda t: t[0].update(name=5), E.MALFORMED)
    changed("a numeric description", lambda t: t[0].update(description=5), E.MALFORMED)
    changed("a duplicated tool", lambda t: t.append(copy.deepcopy(t[0])), E.MALFORMED)
    changed("a schema that is a list", lambda t: t[0].update(inputSchema=[]), E.MALFORMED)
    changed("a schema that is not an object type", lambda t: t[0]["inputSchema"].update(type="array"), E.MALFORMED)
    changed("a schema with no type", lambda t: t[0]["inputSchema"].pop("type"), E.MALFORMED)
    changed("properties that are a list", lambda t: t[2]["inputSchema"].update(properties=[]), E.MALFORMED)
    changed("a missing properties member", lambda t: t[2]["inputSchema"].pop("properties"), E.MALFORMED)
    changed("33 properties", lambda t: t[0]["inputSchema"].update(properties={f"p{i}": {"type": "string"} for i in range(33)}), E.MALFORMED)
    if (
        parses(
            "32 properties",
            catalogue_body(lambda t: t[0]["inputSchema"].update(properties={f"p{i}": {"type": "string"} for i in range(32)})),
        )
        is None
    ):
        bad.append("32 properties are allowed")
    changed("a property that is a string", lambda t: t[2]["inputSchema"]["properties"].update(symbol="string"), E.MALFORMED)
    changed("required that is a string", lambda t: t[2]["inputSchema"].update(required="symbol"), E.MALFORMED)
    changed("required that lists an unknown property", lambda t: t[2]["inputSchema"].update(required=["symbol", "ghost"]), E.MALFORMED)
    changed("required that lists a number", lambda t: t[2]["inputSchema"].update(required=[1]), E.MALFORMED)
    changed("required that lists a list", lambda t: t[2]["inputSchema"].update(required=[["symbol"]]), E.MALFORMED)
    changed("required that lists an object", lambda t: t[2]["inputSchema"].update(required=[{"symbol": 1}]), E.MALFORMED)
    changed("required that is an object", lambda t: t[2]["inputSchema"].update(required={"symbol": True}), E.MALFORMED)
    changed("required that is a number", lambda t: t[2]["inputSchema"].update(required=5), E.MALFORMED)
    changed("required that repeats", lambda t: t[2]["inputSchema"].update(required=["symbol", "symbol"]), E.MALFORMED)
    if parses("an empty required list", catalogue_body(lambda t: t[2]["inputSchema"].update(required=[]))) is None:
        bad.append("an empty required list is valid JSON Schema")

    # --- a schema this client cannot check is CONTRACT_DRIFT, never silently ignored ------------------------------------------------------
    for keyword in ("$ref", "oneOf", "anyOf", "allOf", "patternProperties", "minProperties", "dependencies", "if"):
        changed(f"the schema keyword {keyword}", lambda t, k=keyword: t[2]["inputSchema"].update({k: {}}), E.CONTRACT_DRIFT)
    changed("additionalProperties true", lambda t: t[2]["inputSchema"].update(additionalProperties=True), E.CONTRACT_DRIFT)
    changed("additionalProperties missing", lambda t: t[2]["inputSchema"].pop("additionalProperties"), E.CONTRACT_DRIFT)
    changed(
        "additionalProperties a schema", lambda t: t[2]["inputSchema"].update(additionalProperties={"type": "string"}), E.CONTRACT_DRIFT
    )
    for keyword in ("minimum", "maximum", "pattern", "minLength", "maxLength", "items", "format", "examples", "$ref", "oneOf"):
        changed(
            f"the property keyword {keyword}",
            lambda t, k=keyword: t[2]["inputSchema"]["properties"]["symbol"].update({k: 1}),
            E.CONTRACT_DRIFT,
        )
    for kind in ("array", "object", "null", "string|null", 5, None):
        changed(
            f"the property type {kind!r}", lambda t, k=kind: t[2]["inputSchema"]["properties"]["symbol"].update(type=k), E.CONTRACT_DRIFT
        )
    for kind in ("string", "number", "integer", "boolean"):
        if (
            parses(
                f"the property type {kind}", catalogue_body(lambda t, k=kind: t[2]["inputSchema"]["properties"]["symbol"].update(type=k))
            )
            is None
        ):
            bad.append(f"the type {kind} is checkable")
    changed("an enum that is a string", lambda t: t[4]["inputSchema"]["properties"]["intent"].update(enum="swing"), E.CONTRACT_DRIFT)
    changed("an empty enum", lambda t: t[4]["inputSchema"]["properties"]["intent"].update(enum=[]), E.CONTRACT_DRIFT)
    changed("an enum with a number", lambda t: t[4]["inputSchema"]["properties"]["intent"].update(enum=["swing", 1]), E.CONTRACT_DRIFT)

    # --- drift between the pinned catalogue and the live one ----------------------------------------------------------------------------------
    pinned = cat

    def drift(label: str, mutate: Any, expected: list[tuple[str, str]]) -> None:
        live = parses(label, catalogue_body(mutate))
        if live is None:
            return
        got = [(d.kind, d.tool) for d in mod.diff(pinned, live)]
        if got != expected:
            bad.append(f"{label}: drift {got}, expected {expected}")

    drift("identical", None, [])
    drift(
        "a new tool",
        lambda t: t.append(
            {"name": "check_safety", "description": "d", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}
        ),
        [("TOOL_ADDED", "check_safety")],
    )
    drift("a tool missing", lambda t: t.pop(0), [("TOOL_MISSING", "market_overview")])
    drift(
        "a property added",
        lambda t: t[2]["inputSchema"]["properties"].update(extra={"type": "string"}),
        [("SCHEMA_CHANGED", "analyze_token")],
    )
    drift(
        "a type changed", lambda t: t[2]["inputSchema"]["properties"]["symbol"].update(type="number"), [("SCHEMA_CHANGED", "analyze_token")]
    )
    drift("a requirement dropped", lambda t: t[2]["inputSchema"].pop("required"), [("SCHEMA_CHANGED", "analyze_token")])
    drift(
        "an enum value added",
        lambda t: t[4]["inputSchema"]["properties"]["intent"]["enum"].append("day"),
        [("SCHEMA_CHANGED", "compare_tokens")],
    )
    drift("a description only", lambda t: t[3].update(description="Reworded."), [("DESCRIPTION_CHANGED", "deep_analysis")])
    drift(
        "a schema and a description",
        lambda t: (t[3].update(description="Reworded."), t[3]["inputSchema"]["properties"].update(extra={"type": "string"})),
        [("SCHEMA_CHANGED", "deep_analysis")],
    )
    drift(
        "several at once",
        lambda t: (
            t.pop(1),
            t.append(
                {"name": "zzz", "description": "d", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}
            ),
            t[1].update(description="x"),
        ),
        [("DESCRIPTION_CHANGED", "analyze_token"), ("TOOL_MISSING", "scan_market"), ("TOOL_ADDED", "zzz")],
    )
    extra_names = ["aaa", "bbb", "ccc", "mmm", "nnn", "ppp", "qqq", "zzz"]

    def everything(tools: list[dict[str, Any]]) -> None:
        for tool in tools:
            tool["description"] = tool["description"] + " Reworded."
        for name in extra_names:
            tools.append(
                {"name": name, "description": "d", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}
            )

    drift(
        "every tool reworded and eight added",
        everything,
        sorted([("DESCRIPTION_CHANGED", n) for n in SIX] + [("TOOL_ADDED", n) for n in extra_names], key=lambda pair: pair[1]),
    )  # fourteen names: the order of the report cannot be right by luck of the hash seed
    drift(
        "a property description reworded",
        lambda t: t[2]["inputSchema"]["properties"]["symbol"].update(description="Reworded."),
        [("DESCRIPTION_CHANGED", "analyze_token")],
    )
    drift("a schema description reworded", lambda t: t[2]["inputSchema"].update(description="A new line."), [])
    drift(
        "a property description removed",
        lambda t: t[2]["inputSchema"]["properties"]["symbol"].pop("description"),
        [("DESCRIPTION_CHANGED", "analyze_token")],
    )
    drift(
        "a default changed",
        lambda t: t[5]["inputSchema"]["properties"]["time_window"].update(default="30d"),
        [("SCHEMA_CHANGED", "monitor_market_sentiment_shift")],
    )
    kinds = {k: mod.Drift(k, "t", "d").blocks for k in ("TOOL_ADDED", "TOOL_MISSING", "SCHEMA_CHANGED", "DESCRIPTION_CHANGED")}
    if kinds != {"TOOL_ADDED": False, "TOOL_MISSING": True, "SCHEMA_CHANGED": True, "DESCRIPTION_CHANGED": False}:
        bad.append(f"which changes block: {kinds}")

    # --- the pin the product ships has structure and no prose ----------------------------------------------------------------------------------
    pin = mod.schema_pin(cat)
    if sorted(pin) != sorted(SIX):
        bad.append("the pin names the tools")
    if any("description" in rule for schema in pin.values() for rule in schema["properties"].values()) or any(
        "description" in schema for schema in pin.values()
    ):
        bad.append("the pin must carry no wording")
    if pin["analyze_token"]["required"] != ["symbol"] or set(pin["analyze_token"]["properties"]) != {"symbol", "chain"}:
        bad.append("the pin lost the structure")
    pinned_cat = mod.load_pin(pin)
    if pinned_cat.names() != cat.names() or any(spec.description for spec in pinned_cat.tools.values()):
        bad.append("a pinned catalogue has the tools and no descriptions")
    if mod.diff(pinned_cat, cat):
        bad.append(f"the live catalogue differs from its own pin: {mod.diff(pinned_cat, cat)}")
    reworded = parses(
        "reworded",
        catalogue_body(lambda t: (t[2].update(description="x"), t[2]["inputSchema"]["properties"]["symbol"].update(description="y"))),
    )
    if reworded is not None and mod.diff(pinned_cat, reworded):
        bad.append("wording cannot differ from a pin")
    changed_live = parses("changed", catalogue_body(lambda t: t[2]["inputSchema"]["properties"]["symbol"].update(type="number")))
    if changed_live is not None and [(d.kind, d.tool) for d in mod.diff(pinned_cat, changed_live)] != [("SCHEMA_CHANGED", "analyze_token")]:
        bad.append("a structural change against a pin")
    if mod.load_pin(pin).sha256 != pinned_cat.sha256:
        bad.append("loading the same pin twice gives different hashes")
    for label, broken in (
        ("an empty pin", {}),
        ("a bad name", {"Bad": pin["analyze_token"]}),
        ("a schema that allows extras", {"t": {**pin["analyze_token"], "additionalProperties": True}}),
        ("a schema with an unknown keyword", {"t": {**pin["analyze_token"], "oneOf": []}}),
    ):
        try:
            mod.load_pin(broken)
            bad.append(f"{label}: accepted")
        except mod.ConnectorError:
            pass
    try:
        mod.load_pin({f"t{i}": pin["analyze_token"] for i in range(65)})
        bad.append("65 pinned tools were accepted")
    except mod.ConnectorError:
        pass

    # --- arguments are checked against the schema -----------------------------------------------------------------------------------------------
    def args_ok(label: str, tool: str, args: dict[str, Any]) -> None:
        try:
            out = mod.check_arguments(cat.tools[tool], args)
        except mod.ConnectorError as exc:
            bad.append(f"{label}: refused ({exc})")
            return
        if out != args:
            bad.append(f"{label}: returned {out}")
        if out is args and args:
            bad.append(f"{label}: returned the caller's own object")

    def args_bad(label: str, tool: str, args: dict[str, Any]) -> None:
        try:
            mod.check_arguments(cat.tools[tool], args)
            bad.append(f"{label}: accepted")
        except mod.ConnectorError as exc:
            if exc.error_class is not E.INVALID_ARGS:
                bad.append(f"{label}: classed {exc.error_class.value}")
        except Exception as exc:
            bad.append(f"{label}: raised {type(exc).__name__}: {exc}")

    args_ok("no arguments", "market_overview", {})
    args_ok("a symbol", "analyze_token", {"symbol": "SOL"})
    args_ok("a symbol and a chain", "analyze_token", {"symbol": "SOL", "chain": "bsc"})
    args_ok("a boolean", "deep_analysis", {"symbol": "SOL", "include_perp": False})
    args_ok("a number", "scan_market", {"top_n": 5})
    args_ok("a float number", "scan_market", {"top_n": 2.5})
    args_ok("an enum", "compare_tokens", {"symbols": "SOL, AVAX", "intent": "swing"})
    args_ok("the only window", "monitor_market_sentiment_shift", {"time_window": "7d"})
    args_ok("an optional argument left out", "scan_market", {})
    args_bad("an unknown argument", "analyze_token", {"symbol": "SOL", "address": "0xabc"})
    args_bad("an argument for a tool that takes none", "market_overview", {"x": 1})
    args_bad("a missing required argument", "analyze_token", {})
    args_bad("a missing required argument with others present", "analyze_token", {"chain": "bsc"})
    args_bad("a number for a string", "analyze_token", {"symbol": 5})
    args_bad("a string for a number", "scan_market", {"top_n": "5"})
    args_bad("a boolean for a number", "scan_market", {"top_n": True})
    args_bad("a string for a boolean", "deep_analysis", {"symbol": "SOL", "include_perp": "false"})
    args_bad("a number for a boolean", "deep_analysis", {"symbol": "SOL", "include_perp": 0})
    args_bad("an outside enum value", "compare_tokens", {"symbols": "SOL, AVAX", "intent": "day"})
    args_bad("a wrong window", "monitor_market_sentiment_shift", {"time_window": "1d"})
    args_bad("a nan", "scan_market", {"top_n": float("nan")})
    args_bad("an infinity", "scan_market", {"top_n": float("inf")})
    args_bad("a negative infinity", "scan_market", {"top_n": float("-inf")})
    args_bad("None for a string", "analyze_token", {"symbol": None})
    args_bad("a list for a string", "analyze_token", {"symbol": ["SOL"]})
    # an integer property is checked as an integer, and a boolean is neither
    custom = parses(
        "a catalogue with an integer",
        dumps(
            [
                {
                    "name": "t",
                    "description": "d",
                    "inputSchema": {"type": "object", "properties": {"n": {"type": "integer"}}, "additionalProperties": False},
                }
            ]
        ),
    )
    if custom is not None:
        for value, ok in ((3, True), (3.0, False), (True, False), ("3", False)):
            try:
                mod.check_arguments(custom.tools["t"], {"n": value})
                accepted = True
            except mod.ConnectorError:
                accepted = False
            if accepted is not ok:
                bad.append(f"an integer property given {value!r}: accepted={accepted}")
    return bad
