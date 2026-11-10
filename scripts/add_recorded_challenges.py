#!/usr/bin/env python3
"""Add the recorded model-arm challenges to an exported site directory, as ``model_challenges.json`` with its digest in the index.

For the browser state tests (``web/tools/states_test.py`` FULL_DATA needs the document). The exporter holds each challenge to the ledger it
was made on (``model_challenge_file.for_site``); the packed record (``docs/record``) does not hold that cycle, so this script cannot do that
check and does not pretend to: it writes the rows of the recorded fixtures (``tests/recorded_model_challenges``) exactly as the exporter
writes them, so the page can be driven with real rows of the real shape. Use it on a copy used for testing, never on a published site.
Usage: python scripts/add_recorded_challenges.py SITEDIR
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from hoshitori import model_challenge_file, site_export

RECORDED = Path(__file__).resolve().parents[1] / "tests" / "recorded_model_challenges"
NAME = "model_challenges.json"


def add(site: Path) -> int:
    """Write the document into ``site`` and its digest into the index; return how many rows it holds."""
    challenges = [model_challenge_file.read(path.read_bytes()) for path in sorted(RECORDED.glob("*.json"))]
    rows = sorted(
        (model_challenge_file.row(c) for c in challenges),
        key=lambda r: (r.symbol, r.hours, r.source, r.forecast_id),
    )
    body = site_export.canonical_bytes(rows)
    (site / NAME).write_bytes(body)
    index_path = site / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["files"][NAME] = hashlib.sha256(body).hexdigest()
    index_path.write_bytes(site_export.canonical_bytes(index))
    return len(rows)


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not (Path(argv[0]) / "index.json").is_file():
        sys.stderr.write("usage: add_recorded_challenges.py SITEDIR (a folder with an index.json)\n")
        return 2
    sys.stdout.write(f"{add(Path(argv[0]))} recorded challenges added\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
