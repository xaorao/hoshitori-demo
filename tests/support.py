"""Helpers shared by tests: running the Node implementation in a clean environment."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFIER_JS = ROOT / "verifier-js"
NODE = shutil.which("node")


def run_node_canonical(items: list[tuple[str, bytes]]) -> dict[str, dict[str, object]]:
    """Canonicalise every item with the Node implementation; returns {id: {"ok": bool, "hex"|"error": str}}."""
    assert NODE is not None, "node is required for the cross-language tests"
    payload = "".join(json.dumps({"id": ident, "hex": raw.hex()}) + "\n" for ident, raw in items)
    done = subprocess.run(
        [NODE, str(VERIFIER_JS / "src" / "canon_cli.mjs")],
        input=payload,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        check=True,
        timeout=300,
    )
    return {out["id"]: out for out in (json.loads(line) for line in done.stdout.splitlines() if line.strip())}
