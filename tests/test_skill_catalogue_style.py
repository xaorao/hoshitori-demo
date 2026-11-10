"""The skills' catalogue entries follow the style of the live RYO catalogue.

The organisers publish no skill contract, so the only visible specification of "a tool in this catalogue" is the authenticated
catalogue itself: six entries captured by the first authenticated read. Their style is read from the capture, not typed here, and each
skill's entry is held to it:

* the entry has the live keys (``name``, ``description``, ``inputSchema``) and, beyond them, only the optional keys of the later
  protocol era (``title``, ``annotations``, ``outputSchema``): the live server speaks 2024-11-05 and carries none of them, and a
  client of that era ignores them;
* the description is four labelled parts in this order: ``Delivery:``, ``Use when:``, ``Core input`` (or ``Core inputs``), ``Limits:``;
* the input schema is an object with ``additionalProperties: false``, and a ``required`` list only when something is required;
* every property has a ``type`` (or an ``enum``) and a ``description`` that says what to put there.

The live entries are the reference, so the test first checks that the reference itself has the style it is about to enforce; a
catalogue that changes its style then fails here rather than letting the skills drift from it. The capture lives in ``research`` beside
the repository; without it the test is skipped with that reason, never passed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT.parent / "research" / "architecture" / "fixtures" / "ryo_live"
for src in ("address-flag-check", "evidence-delta", "exit-liquidity-check", "indicator-condition-check", "ryo-verdict-record"):
    sys.path.insert(0, str(ROOT / "skills" / src / "src"))

from address_flag_check import schema as address_schema  # noqa: E402
from evidence_delta import schema as delta_schema  # noqa: E402
from exit_liquidity_check import schema as exit_schema  # noqa: E402
from indicator_condition_check import schema as indicator_schema  # noqa: E402
from ryo_verdict_record import schema as verdict_schema  # noqa: E402

LIVE_KEYS = {"name", "description", "inputSchema"}
LATER_KEYS = {"title", "annotations", "outputSchema"}
PARTS = re.compile(r"^Delivery: .+ Use when: .+ Core inputs?: .+ Limits: .+$", re.S)


def live_catalogue() -> list[dict[str, Any]]:
    files = sorted(CAPTURE.glob("tools.*.json"))
    if not files:
        pytest.skip(f"the authenticated catalogue capture is not at {CAPTURE}")
    body = json.loads(files[-1].read_text(encoding="utf-8"))["response"]["body"]
    assert isinstance(body, list) and body
    return body


def style_problems(entry: dict[str, Any]) -> list[str]:
    """What is unlike the live style in one catalogue entry (an empty list when it is like it)."""
    name = entry.get("name", "?")
    out: list[str] = []
    if not set(entry) >= LIVE_KEYS or set(entry) - LIVE_KEYS - LATER_KEYS:
        out.append(f"{name}: the entry has the keys {sorted(entry)}, not the live ones plus at most title, annotations and outputSchema")
    text = entry.get("description", "")
    if not isinstance(text, str) or PARTS.match(text) is None:
        out.append(f"{name}: the description is not Delivery, Use when, Core input(s) and Limits, in that order")
    schema = entry.get("inputSchema", {})
    if schema.get("type") != "object":
        out.append(f"{name}: the input schema is not an object")
    if schema.get("additionalProperties") is not False:
        out.append(f"{name}: the input schema does not close the object with additionalProperties false")
    if "required" in schema and not schema["required"]:
        out.append(f"{name}: an empty required list (the live entries leave it out)")
    for prop, spec in schema.get("properties", {}).items():
        if "type" not in spec and "enum" not in spec:
            out.append(f"{name}.{prop}: no type")
        if not isinstance(spec.get("description"), str) or not spec["description"].strip():
            out.append(f"{name}.{prop}: no description")
    for req in schema.get("required", []):
        if req not in schema.get("properties", {}):
            out.append(f"{name}: required {req} is not a property")
    return out


def our_entries() -> list[dict[str, Any]]:
    return [
        *address_schema.TOOL_DEFINITIONS,
        *delta_schema.TOOL_DEFINITIONS,
        *exit_schema.TOOL_DEFINITIONS,
        indicator_schema.TOOL_DEFINITION,
        *verdict_schema.TOOL_DEFINITIONS,
    ]


def test_the_live_catalogue_has_the_style_the_skills_are_held_to() -> None:
    assert [p for entry in live_catalogue() for p in style_problems(entry)] == []


def test_the_checker_finds_each_departure_from_the_style() -> None:
    good = {
        "name": "t",
        "description": "Delivery: a. Use when: b. Core input: c. Limits: d.",
        "inputSchema": {"type": "object", "properties": {"x": {"type": "string", "description": "d"}}, "additionalProperties": False},
    }
    assert style_problems(good) == []
    assert style_problems({**good, "title": "t", "annotations": {}, "outputSchema": {}}) == []
    cases: dict[str, dict[str, Any]] = {
        "order": {**good, "description": "Use when: b. Delivery: a. Core input: c. Limits: d."},
        "missing part": {**good, "description": "Delivery: a. Use when: b. Limits: d."},
        "open object": {**good, "inputSchema": {"type": "object", "properties": {}}},
        "empty required": {**good, "inputSchema": {**good["inputSchema"], "required": []}},
        "no description": {**good, "inputSchema": {**good["inputSchema"], "properties": {"x": {"type": "string"}}}},
        "no type": {**good, "inputSchema": {**good["inputSchema"], "properties": {"x": {"description": "d"}}}},
        "required not a property": {**good, "inputSchema": {**good["inputSchema"], "required": ["y"]}},
        "unknown key": {**good, "version": "1"},
        "missing key": {k: v for k, v in good.items() if k != "description"},
    }
    for what, entry in cases.items():
        assert style_problems(entry) != [], what


@pytest.mark.parametrize("entry", our_entries(), ids=lambda e: e["name"])
def test_each_skill_entry_follows_the_live_style(entry: dict[str, Any]) -> None:
    live_catalogue()  # the reference must exist for this to mean anything
    assert style_problems(entry) == []
