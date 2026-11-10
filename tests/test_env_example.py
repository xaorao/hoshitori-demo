"""`.env.example` names every environment variable the code reads by a fixed name, and no variable the code never reads."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
READS = re.compile(r"\"([A-Z][A-Z0-9_]*_(?:API_KEY|MCP_KEY|BEARER_TOKEN|BASE_URL))\"")  # credentials and endpoints


def listed() -> set[str]:
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    return {line.split("=", 1)[0] for line in lines if line and not line.startswith("#") and "=" in line}


def read_by_code() -> set[str]:
    names: set[str] = set()
    for folder in ("src", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            names |= set(READS.findall(path.read_text(encoding="utf-8")))
    return names


def test_every_variable_the_code_reads_is_listed() -> None:
    missing = read_by_code() - listed()
    assert not missing, f".env.example misses {sorted(missing)}"


def test_every_listed_variable_is_read_somewhere() -> None:
    unused = listed() - read_by_code()
    assert not unused, f".env.example lists {sorted(unused)}, which no code reads"
