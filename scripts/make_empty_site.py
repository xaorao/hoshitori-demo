#!/usr/bin/env python3
"""Export the site of a league that has run no cycle, for the browser state tests (``web/tools/states_test.py`` EMPTY_DATA).

The league is made the way the tests make one (a signing key, a root, a mapping, a protocol and settings, against the scripted outside
world of the tests) with its first slot in the future, and is ticked once: its ledger is open and empty, and the exporter writes the site
of a league with no cycle. Nothing here touches a live service or a key of the project.
Usage: python scripts/make_empty_site.py OUTDIR [SITE OPTION ...] (a folder that does not exist yet). The options go to ``hoshitori-league
site``: the states test needs ``--status docs/STATUS.md`` and ``--drill FILE`` (made by scripts/make_drill.py) for the pages that state
what has been shown and which drills held.
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the scripted world is a package of this folder

import importlib

from hoshitori import league_cli

# the scripted world of the tests, imported by name so that the type checker does not follow it into the tests
deployment_checks = importlib.import_module("tests.deployment_checks")
e2e_league = importlib.import_module("tests.e2e_league")


def make(out: Path, options: Sequence[str] = ()) -> int:
    """Write the site of a league with no cycle under ``out``, with the site ``options``; return the exit status of the export."""
    if out.exists():
        sys.stderr.write(f"make_empty_site: {out} exists already\n")
        return 1
    with tempfile.TemporaryDirectory() as tmp, e2e_league.outside(e2e_league.OffsetClock()) as world:
        # the first slot is an hour away: the one tick opens the ledger and finds nothing due, so no cycle is committed
        settings = deployment_checks.world_files(Path(tmp), world, ago_minutes=-60)
        if (status := league_cli.main(["run", str(settings), "--once"])) != 0:
            return int(status)
        return int(league_cli.main(["site", str(settings), str(out), *options]))


def main(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write("usage: make_empty_site.py OUTDIR [SITE OPTION ...]\n")
        return 2
    return make(Path(argv[0]), argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
