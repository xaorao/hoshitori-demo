"""The RYO catalogue reader, drift detector and argument checker: scenarios, a golden test on the live recording, and mutants."""

from __future__ import annotations

import pytest

from hoshitori import ryo_catalogue
from tests.mutation import mutant
from tests.ryo_catalogue_checks import failures
from tests.ryo_support import SIX, live_fixture


def test_real_module_passes_every_check() -> None:
    assert failures(ryo_catalogue) == []


def test_the_live_catalogue_reads_and_has_the_six_tools() -> None:
    live = live_fixture("tools.")
    cat = ryo_catalogue.parse_catalogue(live["raw"])
    assert cat.names() == frozenset(SIX)
    assert cat.tools["compare_tokens"].required() == ("symbols",)
    assert ryo_catalogue.check_arguments(cat.tools["compare_tokens"], {"symbols": "SOL, AVAX", "intent": "swing"})
    for tool in cat.tools.values():
        for line in ("Delivery:", "Use when:", "Core input", "Limits:"):
            assert line in tool.description, f"{tool.name} lacks {line}"


# Redundant guard without a mutant: the `isinstance(body, list)` test of the catalogue (a non-list body that is not empty fails the
# entry checks that follow with the same class).
MUTATIONS: dict[str, tuple[str, str]] = {
    "the catalogue hash depends on order": (
        "    ordered = sorted(tools.values(), key=lambda t: t.name)\n",
        "    ordered = list(tools.values())\n",
    ),
    "the catalogue hash ignores the description": ('"description": t.description, "inputSchema"', '"description": "", "inputSchema"'),
    "the catalogue hash ignores the schema": ('"inputSchema": dict(t.schema)', '"inputSchema": {}'),
    "an empty catalogue is accepted": (
        "    if not isinstance(body, list) or not body or len(body) > MAX_TOOLS:",
        "    if not isinstance(body, list) or len(body) > MAX_TOOLS:",
    ),
    "any number of tools is accepted": (
        "    if not isinstance(body, list) or not body or len(body) > MAX_TOOLS:",
        "    if not isinstance(body, list) or not body:",
    ),
    "the tool limit is one too small": ("len(body) > MAX_TOOLS:", "len(body) >= MAX_TOOLS:"),
    "entry keys are not checked": ('set(entry) != {"name", "description", "inputSchema"}', "False"),
    "a name is not checked": (
        "        if not (isinstance(name, str) and TOOL_NAME.fullmatch(name)) or not isinstance(description, str):",
        "        if not isinstance(description, str):",
    ),
    "a name may start with a digit": (
        'TOOL_NAME: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")',
        'TOOL_NAME: Final = re.compile(r"[a-z0-9][a-z0-9_]{0,63}")',
    ),
    "a name may be longer": ('r"[a-z][a-z0-9_]{0,63}"', 'r"[a-z][a-z0-9_]{0,127}"'),
    "a name may be matched in part": (
        "(isinstance(name, str) and TOOL_NAME.fullmatch(name))",
        "(isinstance(name, str) and TOOL_NAME.match(name))",
    ),
    "a pinned name is not checked": (
        '        if not TOOL_NAME.fullmatch(name):\n            raise _malformed(f"pin has a bad name {name!r}")\n',
        "",
    ),
    "a pin may be empty": ("    if not pin or len(pin) > MAX_TOOLS:", "    if len(pin) > MAX_TOOLS:"),
    "a pin may be any size": ("    if not pin or len(pin) > MAX_TOOLS:", "    if not pin:"),
    "a pinned schema is not checked": ('ToolSpec(name, "", _check_schema(name, dict(schema)))', 'ToolSpec(name, "", dict(schema))'),
    "the pin keeps the wording": (
        "    return {name: spec.structure() for name, spec in sorted(catalogue.tools.items())}",
        "    return {name: dict(spec.schema) for name, spec in sorted(catalogue.tools.items())}",
    ),
    "a pinned tool gets a description": (
        'ToolSpec(name, "", _check_schema(name, dict(schema)))',
        'ToolSpec(name, "pinned", _check_schema(name, dict(schema)))',
    ),
    "the structure keeps the property wording": (
        '        schema["properties"] = {p: {k: v for k, v in rule.items() if k != "description"} for p, rule in self.properties().items()}',
        '        schema["properties"] = {p: dict(rule) for p, rule in self.properties().items()}',
    ),
    "the structure keeps the schema wording": (
        '        schema = {k: v for k, v in self.schema.items() if k != "description"}',
        "        schema = dict(self.schema)",
    ),
    "the wording leaves out the properties": (
        '        return self.description, tuple(sorted((p, str(rule.get("description", ""))) for p, rule in self.properties().items()))',
        "        return self.description, ()",
    ),
    "a description need not be text": ("or not isinstance(description, str):", "or False:"),
    "a duplicated tool is kept": ('        if name in tools:\n            raise _malformed(f"lists {name} twice")\n', ""),
    "the schema type is not checked": ('schema.get("type") != "object"', "False"),
    "the schema need not be an object": (
        'if not isinstance(schema, dict) or schema.get("type") != "object":',
        'if schema.get("type") != "object":',
    ),
    "schema keywords are not checked": (
        '    if unknown:\n        raise ConnectorError(ErrorClass.CONTRACT_DRIFT, f"the schema of {name} uses keywords this client cannot check: {sorted(unknown)}")\n',
        "",
    ),
    "extra properties are allowed": ('    if schema.get("additionalProperties") is not False:', "    if False:"),
    "extra properties are allowed when the key is missing": (
        'schema.get("additionalProperties") is not False',
        'schema.get("additionalProperties") is True',
    ),
    "properties need not be a mapping": (
        "    if not isinstance(properties, dict) or len(properties) > MAX_PROPERTIES:",
        "    if len(properties) > MAX_PROPERTIES:",
    ),
    "any number of properties": ("len(properties) > MAX_PROPERTIES", "False"),
    "the property limit is one too small": ("len(properties) > MAX_PROPERTIES", "len(properties) >= MAX_PROPERTIES"),
    "a property need not be an object": ("if not isinstance(prop, str) or not isinstance(rule, dict):", "if not isinstance(prop, str):"),
    "property keywords are not checked": (
        '        if extra:\n            raise ConnectorError(ErrorClass.CONTRACT_DRIFT, f"property {prop} of {name} uses keywords it cannot check: {sorted(extra)}")\n',
        "",
    ),
    "property types are not checked": ('        if rule.get("type") not in PROPERTY_TYPES:', "        if False:"),
    "an array type is accepted": (
        'PROPERTY_TYPES: Final = frozenset({"string", "number", "integer", "boolean"})',
        'PROPERTY_TYPES: Final = frozenset({"string", "number", "integer", "boolean", "array"})',
    ),
    "an enum is not checked": (
        "        if enum is not None and (not isinstance(enum, list) or not enum or not all(isinstance(v, str) for v in enum)):",
        "        if False:",
    ),
    "an empty enum is accepted": ("not isinstance(enum, list) or not enum or not all", "not isinstance(enum, list) or not all"),
    "a non-text enum value is accepted": ("or not all(isinstance(v, str) for v in enum)):", "):"),
    "required members are not checked": ("    if not listed or len(set(required)) != len(required):", "    if False:"),
    "required members may be unknown": ("isinstance(r, str) and r in properties for r in required", "isinstance(r, str) for r in required"),
    "required members may repeat": ("    if not listed or len(set(required)) != len(required):", "    if not listed:"),
    "required members need not be text": ("isinstance(r, str) and r in properties", "r in properties"),
    "a description is looked at before the schema": (
        '        elif before.structure() != after.structure():\n            drift.append(Drift("SCHEMA_CHANGED", name, "the input schema differs from the pinned one"))\n        elif before.description and before.wording() != after.wording():\n            drift.append(Drift("DESCRIPTION_CHANGED", name, "only the wording differs"))',
        '        elif before.description and before.wording() != after.wording():\n            drift.append(Drift("DESCRIPTION_CHANGED", name, "only the wording differs"))\n        elif before.structure() != after.structure():\n            drift.append(Drift("SCHEMA_CHANGED", name, "the input schema differs from the pinned one"))',
    ),
    "required need not be a list": ("listed = isinstance(required, list) and all(", "listed = all("),
    "the drift of an added tool is missed": (
        '        if before is None:\n            drift.append(Drift("TOOL_ADDED", name, "a tool the product was not built against"))\n        elif after is None:',
        "        if before is None:\n            pass\n        elif after is None:",
    ),
    "the drift of a missing tool is missed": (
        '        elif after is None:\n            drift.append(Drift("TOOL_MISSING", name, "a tool the product was built against is no longer listed"))',
        "        elif after is None:\n            pass",
    ),
    "a schema change is missed": (
        '        elif before.structure() != after.structure():\n            drift.append(Drift("SCHEMA_CHANGED", name, "the input schema differs from the pinned one"))\n        elif',
        "        elif before.structure() != after.structure():\n            pass\n        elif",
    ),
    "wording counts as a schema change": (
        "        elif before.structure() != after.structure():",
        "        elif before.schema != after.schema:",
    ),
    "a pin's missing wording is a change": (
        "        elif before.description and before.wording() != after.wording():",
        "        elif before.wording() != after.wording():",
    ),
    "a description change is missed": (
        '        elif before.description and before.wording() != after.wording():\n            drift.append(Drift("DESCRIPTION_CHANGED", name, "only the wording differs"))',
        "        elif before.description and before.wording() != after.wording():\n            pass",
    ),
    "only the tool description counts as wording": (
        "        elif before.description and before.wording() != after.wording():",
        "        elif before.description and before.description != after.description:",
    ),
    "the drift order is not by name": (
        "    for name in sorted(pinned.names() | live.names()):",
        "    for name in list(pinned.names() | live.names()):",
    ),
    "which changes block is wrong for a missing tool": (
        'return self.kind in ("TOOL_MISSING", "SCHEMA_CHANGED")',
        'return self.kind in ("SCHEMA_CHANGED",)',
    ),
    "which changes block is wrong for a changed schema": (
        'return self.kind in ("TOOL_MISSING", "SCHEMA_CHANGED")',
        'return self.kind in ("TOOL_MISSING",)',
    ),
    "a new tool blocks": (
        'return self.kind in ("TOOL_MISSING", "SCHEMA_CHANGED")',
        'return self.kind in ("TOOL_MISSING", "SCHEMA_CHANGED", "TOOL_ADDED")',
    ),
    "an unknown argument is passed": (
        '        if rule is None:\n            raise ConnectorError(ErrorClass.INVALID_ARGS, f"{spec.name} takes no argument named {key!r}")\n',
        "        if rule is None:\n            out[key] = value\n            continue\n",
    ),
    "types are not checked": (
        '        if not ok:\n            raise ConnectorError(ErrorClass.INVALID_ARGS, f"{key} of {spec.name} must be a {kind}")\n',
        "",
    ),
    "a boolean is a number": (
        '(kind == "number" and isinstance(value, int | float) and not isinstance(value, bool))',
        '(kind == "number" and isinstance(value, int | float))',
    ),
    "a boolean is an integer": (
        '(kind == "integer" and isinstance(value, int) and not isinstance(value, bool))',
        '(kind == "integer" and isinstance(value, int))',
    ),
    "a float is an integer": (
        '(kind == "integer" and isinstance(value, int) and not isinstance(value, bool))',
        '(kind == "integer" and isinstance(value, int | float) and not isinstance(value, bool))',
    ),
    "a number is a string": ('(kind == "string" and isinstance(value, str))', '(kind == "string" and isinstance(value, str | int))'),
    "a string is a boolean": ('(kind == "boolean" and isinstance(value, bool))', '(kind == "boolean" and isinstance(value, bool | str))'),
    "non-finite numbers pass": (
        '        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):\n            raise ConnectorError(ErrorClass.INVALID_ARGS, f"{key} of {spec.name} must be finite")\n',
        "",
    ),
    "infinity passes": ('value != value or value in (float("inf"), float("-inf"))', "value != value"),
    "negative infinity passes": ('value in (float("inf"), float("-inf"))', 'value in (float("inf"),)'),
    "nan passes": ('value != value or value in (float("inf"), float("-inf"))', 'value in (float("inf"), float("-inf"))'),
    "enums are not checked": ('        if "enum" in rule and value not in rule["enum"]:', "        if False:"),
    "required arguments are not checked": (
        '    for key in spec.required():\n        if key not in out:\n            raise ConnectorError(ErrorClass.INVALID_ARGS, f"{spec.name} requires {key!r}")\n',
        "",
    ),
    "arguments are returned as given": ("        out[key] = value\n    for key", "        pass\n    for key"),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.ryo_catalogue", old, new)
    try:
        found = failures(module)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
