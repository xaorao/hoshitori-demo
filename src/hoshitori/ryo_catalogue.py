"""RYO's tool catalogue: the authoritative list of tools and their input schemas (``RYO_API_COMPATIBILITY`` sections 2, 5 and 9).

The guide says the live catalogue is "the final source of truth if this guide and the deployed server ever differ", so the client is
built from it: the tools it may call are the tools the catalogue lists, and a call's arguments are checked against that tool's
``inputSchema`` before anything is sent (a request RYO would reject must not cost an attempt, and one it would accept but that is
not what the caller meant must not be sent).

The schema language RYO uses is small. This module checks only that subset and refuses a catalogue that uses more (a keyword it does
not understand would be silently ignored by a validator, which is how a wrong request gets through), reporting it as drift.

``diff`` compares a live catalogue with the pinned one the product was built against. A change is not an error to hide or a reason to
guess: a tool that is new, missing or changed is reported, and the adapter treats a changed or missing tool as unavailable until a
person has looked at it.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from hoshitori import canonical, jsonnum
from hoshitori.connector import ConnectorError, ErrorClass

TOOL_NAME: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
SCHEMA_KEYS: Final = frozenset({"type", "properties", "required", "additionalProperties", "description"})
PROPERTY_KEYS: Final = frozenset({"type", "description", "enum", "default"})
PROPERTY_TYPES: Final = frozenset({"string", "number", "integer", "boolean"})
MAX_TOOLS: Final = 64
MAX_PROPERTIES: Final = 32


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: Mapping[str, Any]

    def properties(self) -> Mapping[str, Mapping[str, Any]]:
        return self.schema["properties"]  # type: ignore[no-any-return]

    def required(self) -> tuple[str, ...]:
        return tuple(self.schema.get("required", ()))

    def structure(self) -> dict[str, Any]:
        """The schema without any wording: what a caller may send, which a reworded description does not change."""
        schema = {k: v for k, v in self.schema.items() if k != "description"}
        schema["properties"] = {p: {k: v for k, v in rule.items() if k != "description"} for p, rule in self.properties().items()}
        return schema

    def wording(self) -> tuple[str, tuple[tuple[str, str], ...]]:
        """The tool's description and each property's, which are prose for people."""
        return self.description, tuple(sorted((p, str(rule.get("description", ""))) for p, rule in self.properties().items()))


@dataclass(frozen=True)
class Catalogue:
    tools: Mapping[str, ToolSpec]
    sha256: str

    def names(self) -> frozenset[str]:
        return frozenset(self.tools)


@dataclass(frozen=True)
class Drift:
    kind: str  # TOOL_ADDED, TOOL_MISSING, SCHEMA_CHANGED, DESCRIPTION_CHANGED
    tool: str
    detail: str

    @property
    def blocks(self) -> bool:
        """Whether the change makes the tool unusable until a person has looked: everything except a new tool or new wording."""
        return self.kind in ("TOOL_MISSING", "SCHEMA_CHANGED")


def _malformed(message: str) -> ConnectorError:
    return ConnectorError(ErrorClass.MALFORMED, f"the tool catalogue {message}")


def _check_schema(name: str, schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise _malformed(f"gives {name} a schema that is not an object")
    unknown = set(schema) - SCHEMA_KEYS
    if unknown:
        raise ConnectorError(ErrorClass.CONTRACT_DRIFT, f"the schema of {name} uses keywords this client cannot check: {sorted(unknown)}")
    if schema.get("additionalProperties") is not False:
        raise ConnectorError(ErrorClass.CONTRACT_DRIFT, f"the schema of {name} allows arguments it does not list")
    properties = schema.get("properties")
    if not isinstance(properties, dict) or len(properties) > MAX_PROPERTIES:
        raise _malformed(f"gives {name} no usable properties")
    for prop, rule in properties.items():
        if not isinstance(prop, str) or not isinstance(rule, dict):
            raise _malformed(f"gives {name} a property that is not an object")
        extra = set(rule) - PROPERTY_KEYS
        if extra:
            raise ConnectorError(ErrorClass.CONTRACT_DRIFT, f"property {prop} of {name} uses keywords it cannot check: {sorted(extra)}")
        if rule.get("type") not in PROPERTY_TYPES:
            kind = rule.get("type")
            raise ConnectorError(ErrorClass.CONTRACT_DRIFT, f"property {prop} of {name} has a type it cannot check: {kind!r}")
        enum = rule.get("enum")
        if enum is not None and (not isinstance(enum, list) or not enum or not all(isinstance(v, str) for v in enum)):
            raise ConnectorError(ErrorClass.CONTRACT_DRIFT, f"property {prop} of {name} has an enum this client cannot check")
    required = schema.get("required", [])
    listed = isinstance(required, list) and all(isinstance(r, str) and r in properties for r in required)
    if not listed or len(set(required)) != len(required):
        raise _malformed(f"requires arguments that {name} does not list")
    return schema


def _plain(value: Any) -> Any:
    """The catalogue as plain JSON values (numbers as text) so it can be hashed canonically."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, jsonnum.Num):
        return str(value)
    return value


def _hash(tools: Mapping[str, ToolSpec]) -> str:
    ordered = sorted(tools.values(), key=lambda t: t.name)
    listing: list[canonical.JsonValue] = [{"description": t.description, "inputSchema": dict(t.schema), "name": t.name} for t in ordered]
    return hashlib.sha256(canonical.dumps({"tools": listing})).hexdigest()


def parse_catalogue(raw: bytes) -> Catalogue:
    """The catalogue in the body of ``GET /tools``. Raises ``ConnectorError`` (MALFORMED or CONTRACT_DRIFT) for anything unusable."""
    try:
        body = jsonnum.parse(raw)
    except jsonnum.JsonError as exc:
        raise ConnectorError(ErrorClass.MALFORMED, f"the tool catalogue is not readable JSON: {exc}") from exc
    if not isinstance(body, list) or not body or len(body) > MAX_TOOLS:
        raise _malformed("is not a list of tools")
    tools: dict[str, ToolSpec] = {}
    for entry in body:
        if not isinstance(entry, dict) or set(entry) != {"name", "description", "inputSchema"}:
            raise _malformed("has an entry without exactly a name, a description and an inputSchema")
        name, description = entry["name"], entry["description"]
        if not (isinstance(name, str) and TOOL_NAME.fullmatch(name)) or not isinstance(description, str):
            raise _malformed("has an entry with a bad name or description")
        if name in tools:
            raise _malformed(f"lists {name} twice")
        tools[name] = ToolSpec(name, description, _plain(_check_schema(name, entry["inputSchema"])))
    return Catalogue(tools, _hash(tools))


def diff(pinned: Catalogue, live: Catalogue) -> list[Drift]:
    """What changed between the catalogue the product was built against and the one the service now serves."""
    drift: list[Drift] = []
    for name in sorted(pinned.names() | live.names()):
        before, after = pinned.tools.get(name), live.tools.get(name)
        if before is None:
            drift.append(Drift("TOOL_ADDED", name, "a tool the product was not built against"))
        elif after is None:
            drift.append(Drift("TOOL_MISSING", name, "a tool the product was built against is no longer listed"))
        elif before.structure() != after.structure():
            drift.append(Drift("SCHEMA_CHANGED", name, "the input schema differs from the pinned one"))
        elif before.description and before.wording() != after.wording():
            drift.append(Drift("DESCRIPTION_CHANGED", name, "only the wording differs"))
    return drift


# --- the pin the product ships ------------------------------------------------------------------------------------------------------------
def schema_pin(catalogue: Catalogue) -> dict[str, Any]:
    """The structure of every tool (names, argument types, requirements, enums) without RYO's prose, for the product to ship."""
    return {name: spec.structure() for name, spec in sorted(catalogue.tools.items())}


def load_pin(pin: Mapping[str, Mapping[str, Any]]) -> Catalogue:
    """A catalogue made from ``schema_pin``: no descriptions, so only a change of structure can ever differ from it."""
    if not pin or len(pin) > MAX_TOOLS:
        raise _malformed("pin is not a list of tools")
    tools: dict[str, ToolSpec] = {}
    for name, schema in sorted(pin.items()):
        if not TOOL_NAME.fullmatch(name):
            raise _malformed(f"pin has a bad name {name!r}")
        tools[name] = ToolSpec(name, "", _check_schema(name, dict(schema)))
    return Catalogue(tools, _hash(tools))


# --- argument checking against a schema -------------------------------------------------------------------------------------------------
def check_arguments(spec: ToolSpec, args: Mapping[str, Any]) -> dict[str, Any]:
    """The arguments if they satisfy the tool's schema; ``INVALID_ARGS`` otherwise. Absent optional arguments stay absent."""
    properties = spec.properties()
    out: dict[str, Any] = {}
    for key, value in args.items():
        rule = properties.get(key)
        if rule is None:
            raise ConnectorError(ErrorClass.INVALID_ARGS, f"{spec.name} takes no argument named {key!r}")
        kind = rule["type"]
        ok = (
            (kind == "string" and isinstance(value, str))
            or (kind == "boolean" and isinstance(value, bool))
            or (kind == "integer" and isinstance(value, int) and not isinstance(value, bool))
            or (kind == "number" and isinstance(value, int | float) and not isinstance(value, bool))
        )
        if not ok:
            raise ConnectorError(ErrorClass.INVALID_ARGS, f"{key} of {spec.name} must be a {kind}")
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            raise ConnectorError(ErrorClass.INVALID_ARGS, f"{key} of {spec.name} must be finite")
        if "enum" in rule and value not in rule["enum"]:
            raise ConnectorError(ErrorClass.INVALID_ARGS, f"{key} of {spec.name} must be one of {rule['enum']}")
        out[key] = value
    for key in spec.required():
        if key not in out:
            raise ConnectorError(ErrorClass.INVALID_ARGS, f"{spec.name} requires {key!r}")
    return out
