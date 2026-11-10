"""The MCP Registry manifest (`server.json`): valid against the registry's recorded schema, refused by it when broken, consistent with the
package, and its launch arguments start this skill's stdio MCP server. Prepared only: nothing is published (see the README)."""

from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tomllib
from typing import Any

import pytest

from indicator_condition_check import __version__

jsonschema = pytest.importorskip("jsonschema")

SKILL = pathlib.Path(__file__).resolve().parents[1]
MANIFEST_FILE = SKILL / "server.json"
SCHEMA_FILE = SKILL / "tests" / "fixtures" / "registry" / "server.schema.json"
SCHEMA_SHA256 = (
    "3fba09590c99f61735d234822279f4223fab9e300c0a81e81c91ab62a4114de0"  # the file as fetched (tests/fixtures/registry/README.md)
)
NAME = "io.github.RYO-Digital/indicator-condition-check"


def manifest() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    return loaded


def validator() -> Any:
    return jsonschema.Draft7Validator(json.loads(SCHEMA_FILE.read_text(encoding="utf-8")))


def pyproject() -> dict[str, Any]:
    loaded: dict[str, Any] = tomllib.loads((SKILL / "pyproject.toml").read_text(encoding="utf-8"))
    return loaded


RANGE_FORMS = re.compile(r"^\s*(\^|~|>=|<=|>|<)|(^|\.)(x|X|\*)(\.|$)|\s-\s|\|\|")


def looks_like_range(version: str) -> bool:
    """The version forms the registry refuses as ranges (docs/registry/versioning.mdx, table of examples)."""
    return RANGE_FORMS.search(version) is not None


def test_the_recorded_schema_is_the_fetched_file_and_the_one_the_manifest_names() -> None:
    raw = SCHEMA_FILE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SCHEMA_SHA256
    schema = json.loads(raw)
    jsonschema.Draft7Validator.check_schema(schema)
    assert schema["$schema"] == "http://json-schema.org/draft-07/schema#"
    assert schema["$id"] == manifest()["$schema"] == "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"


def test_the_manifest_is_valid_against_the_registry_schema() -> None:
    errors = [f"{list(e.absolute_path)}: {e.message}" for e in validator().iter_errors(manifest())]
    assert errors == []


def _broken(change: str) -> dict[str, Any]:
    m = copy.deepcopy(manifest())
    package = m["packages"][0]
    if change == "description over 100 characters":
        m["description"] = "x" * 101
    elif change == "empty description":
        m["description"] = ""
    elif change == "no description":
        del m["description"]
    elif change == "no version":
        del m["version"]
    elif change == "no name":
        del m["name"]
    elif change == "a name without a namespace":
        m["name"] = "indicator-condition-check"
    elif change == "a name with two slashes":
        m["name"] = "io.github.RYO-Digital/indicator/condition"
    elif change == "an empty title":
        m["title"] = ""
    elif change == "a package without a transport":
        del package["transport"]
    elif change == "a package without an identifier":
        del package["identifier"]
    elif change == "an unknown transport":
        package["transport"] = {"type": "websocket"}
    elif change == "a streamable-http package without a url":
        package["transport"] = {"type": "streamable-http"}
    elif change == "the package version latest":
        package["version"] = "latest"
    elif change == "an empty package version":
        package["version"] = ""
    elif change == "a positional argument with neither value nor valueHint":
        package["packageArguments"] = [{"type": "positional"}]
    elif change == "a malformed file hash":
        package["fileSha256"] = "abc"
    else:  # pragma: no cover - the parameter list below names every change
        raise AssertionError(change)
    return m


@pytest.mark.parametrize(
    "change",
    [
        "description over 100 characters",
        "empty description",
        "no description",
        "no version",
        "no name",
        "a name without a namespace",
        "a name with two slashes",
        "an empty title",
        "a package without a transport",
        "a package without an identifier",
        "an unknown transport",
        "a streamable-http package without a url",
        "the package version latest",
        "an empty package version",
        "a positional argument with neither value nor valueHint",
        "a malformed file hash",
    ],
)
def test_the_schema_refuses_a_broken_manifest(change: str) -> None:
    assert not validator().is_valid(_broken(change)), change


@pytest.mark.parametrize(
    "version,is_range",
    [
        ("1.0.0", False),
        ("2.1.3-alpha", False),
        ("1.0.0-beta.1", False),
        ("2025.11.25", False),
        ("2025-06-18", False),
        ("v1.0", False),
        ("^1.2.3", True),
        ("~1.2.3", True),
        (">=1.2.3", True),
        ("<=1.2.3", True),
        (">1.2.3", True),
        ("<1.2.3", True),
        ("1.x", True),
        ("1.2.*", True),
        ("1 - 2", True),
        ("1.2 || 1.3", True),
    ],
)
def test_the_range_rule_matches_the_registry_table(version: str, is_range: bool) -> None:
    assert looks_like_range(version) is is_range


def test_the_manifest_follows_the_registry_rules_the_schema_leaves_to_the_registry() -> None:
    m = manifest()
    project = pyproject()["project"]
    (package,) = m["packages"]
    assert m["name"] == NAME and m["name"].startswith("io.github.")  # GitHub namespace (docs/registry/authentication.mdx)
    assert m["version"] == package["version"] == project["version"] == __version__  # one version everywhere
    assert not looks_like_range(m["version"]) and not looks_like_range(package["version"])
    assert package["registryType"] == "pypi" and package["identifier"] == project["name"]
    assert package["transport"] == {"type": "stdio"} and package["runtimeHint"] == "uvx"
    assert (
        project["scripts"][package["identifier"]] == "indicator_condition_check.__main__:main"
    )  # uvx runs the script named like the package
    assert "remotes" not in m  # nothing is hosted
    readme = (SKILL / "README.md").read_text(encoding="utf-8")
    assert f"mcp-name: {NAME}" in readme  # PyPI ownership proof (docs/registry/package-types.mdx)


def test_the_manifest_launch_arguments_start_the_stdio_mcp_server() -> None:
    """The console script with the manifest's package arguments, as uvx would start it once published; no network is used."""
    m = manifest()
    (package,) = m["packages"]
    script = pathlib.Path(sys.executable).parent / package["identifier"]
    args = [a["value"] for a in package["packageArguments"]]
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {}}
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": meta}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
        },
    ]
    p = subprocess.run(
        [str(script), *args], input="\n".join(json.dumps(x) for x in lines) + "\n", capture_output=True, text=True, timeout=30
    )
    assert p.returncode == 0, p.stderr
    replies = {r["id"]: r for r in map(json.loads, p.stdout.splitlines())}
    found = replies[1]["result"]
    assert found["supportedVersions"] == ["2026-07-28"]
    assert found["_meta"]["io.modelcontextprotocol/serverInfo"] == {"name": "indicator-condition-check", "version": m["version"]}
    assert replies[2]["result"]["protocolVersion"] == "2025-11-25" and replies[2]["result"]["serverInfo"]["version"] == m["version"]
