"""Fail if a credential-looking string is present in files that could be committed.

Two checks:
1. Patterns of known credential shapes (provider prefixes, bearer values, private-key blocks).
2. Optional: the exact values held in a local ``.env`` file must not appear in any other file.

Usage: ``python scripts/secret_scan.py [--env .env] [paths ...]``. Exit status 1 on any finding. Findings are
reported by file and line only; the matched text is never printed, so a log cannot leak what it found.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

PATTERNS: dict[str, re.Pattern[bytes]] = {
    "ryo builder key": re.compile(rb"ryo_mcp_[A-Za-z0-9_\-]{16,}"),
    "NVIDIA API key": re.compile(rb"nvapi-[A-Za-z0-9_\-]{20,}"),
    "OpenAI-style key": re.compile(rb"sk-[A-Za-z0-9_\-]{24,}"),
    "bearer value": re.compile(rb"[Bb]earer\s+[A-Za-z0-9_\-\.=]{24,}"),
    "private key block": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "hex secret assignment": re.compile(rb"(?i)(secret|token|api[_-]?key|password)\s*[:=]\s*['\"]?[A-Fa-f0-9]{32,}['\"]?"),
}

SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
}
SKIP_NAMES = {".env"}
MAX_BYTES = 5_000_000
# Only variables named like credentials are secret; an endpoint URL in the same file is configuration, not a secret.
SECRET_NAME = re.compile(rb"(?i)(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)")


def iter_files(roots: Iterable[Path]) -> Iterator[Path]:
    for root in roots:
        if root.is_file():
            yield root
            continue
        for path in sorted(root.rglob("*")):
            if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.parts):
                continue
            if path.is_file() and path.name not in SKIP_NAMES and not path.name.startswith(".env."):
                yield path


def env_values(env_file: Path) -> list[bytes]:
    """Values of assignments in an env file that are long enough to be secrets."""
    values: list[bytes] = []
    if not env_file.is_file():
        return values
    for raw in env_file.read_bytes().splitlines():
        line = raw.strip()
        if not line or line.startswith(b"#") or b"=" not in line:
            continue
        name, _, rest = line.partition(b"=")
        value = rest.strip().strip(b"'\"")
        if len(value) >= 16 and SECRET_NAME.search(name):
            values.append(value)
    return values


def scan_file(path: Path, known: list[bytes]) -> list[tuple[int, str]]:
    if path.stat().st_size > MAX_BYTES:
        return []
    data = path.read_bytes()
    findings: list[tuple[int, str]] = []
    for lineno, line in enumerate(data.splitlines(), start=1):
        for label, pattern in PATTERNS.items():
            if pattern.search(line):
                findings.append((lineno, label))
        for value in known:
            if value in line:
                findings.append((lineno, "value from the local env file"))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=[Path(".")], type=Path)
    parser.add_argument("--env", type=Path, default=None, help="local env file whose values must not appear elsewhere")
    args = parser.parse_args(argv)
    known = env_values(args.env) if args.env else []
    bad = 0
    for path in iter_files(args.paths):
        for lineno, label in scan_file(path, known):
            print(f"{path}:{lineno}: possible secret ({label})")
            bad += 1
    if bad:
        print(f"{bad} finding(s); nothing was printed from the matches", file=sys.stderr)
        return 1
    print("secret scan: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
