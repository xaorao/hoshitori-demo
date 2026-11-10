"""Publication files of the skill: the output schemas on disk are current, and ``server.json`` agrees with the package.

``schema/<tool>.output.schema.json`` is what another team reads to learn the shape of a reply without installing anything; the test
compares each file with the ``outputSchema`` the tool definition carries. ``server.json`` is the MCP Registry manifest, prepared and not
published (see the README)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import ryo_verdict_record
from ryo_verdict_record import schema

SKILL = Path(__file__).resolve().parents[1]
NAME = "ryo-verdict-record"
REGENERATE = "run python3 scripts/write_skill_schemas.py at the repository root"


def _definitions() -> list[dict[str, Any]]:
    return list(getattr(schema, "TOOL_DEFINITIONS", None) or [schema.TOOL_DEFINITION])


def test_every_tool_definition_carries_its_output_schema_and_annotations() -> None:
    for definition in _definitions():
        assert definition["outputSchema"]["type"] == "object", definition["name"]
        a = definition["annotations"]
        assert a["readOnlyHint"] is True and a["destructiveHint"] is False and a["idempotentHint"] is True, definition["name"]
        assert isinstance(a["openWorldHint"], bool), definition["name"]


def test_the_committed_output_schemas_are_the_generated_ones() -> None:
    expected = {d["name"]: d["outputSchema"] for d in _definitions()}
    committed = {p.name.removesuffix(".output.schema.json"): p for p in (SKILL / "schema").glob("*.output.schema.json")}
    assert set(committed) == set(expected), f"schema/ does not hold exactly one file per tool: {REGENERATE}"
    for tool, path in committed.items():
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk == expected[tool], f"{path.name} is out of date: {REGENERATE}"


def test_server_json_agrees_with_the_package() -> None:
    manifest = json.loads((SKILL / "server.json").read_text(encoding="utf-8"))
    project = tomllib.loads((SKILL / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    (package,) = manifest["packages"]
    assert manifest["version"] == package["version"] == project["version"] == ryo_verdict_record.__version__
    assert 0 < len(manifest["description"]) < 100
    assert manifest["name"].startswith("io.github.") and manifest["name"].endswith("/" + NAME)
    assert package["registryType"] == "pypi" and package["identifier"] == project["name"] == NAME
    assert package["transport"] == {"type": "stdio"}
    assert project["scripts"][NAME].endswith(":main")
    readme = (SKILL / "README.md").read_text(encoding="utf-8")
    assert f"mcp-name: {manifest['name']}" in readme
