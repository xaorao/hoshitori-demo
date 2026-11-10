#!/usr/bin/env python3
"""Add to an on-demand ledger the instrument lists a forecast was made against, rebuilt from the captures the run stored.

    python scripts/ondemand_registry_from_captures.py SETTINGS DIR VENUE:CAPTURE_ID [VENUE:CAPTURE_ID ...]

For a run made before the commit wrote these lists itself. Each capture is the exchange's own answer, kept by the gateway at the time, so
the
registry rebuilt from them is the one the run read; but the lists reach the ledger after the commitment, so the time-stamped
checkpoint does not cover them (a commit made now writes them before the stamp). The run is the one whose forecast the ledger holds.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hoshitori import canonical, deployment, forecast, ondemand
from hoshitori.ledger import Ledger
from hoshitori.note import Signer
from hoshitori.registry import Registry
from hoshitori.venue_data import InstrumentList


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        sys.stderr.write(__doc__ or "")
        return 2
    settings = deployment.load_settings(Path(argv[0]))
    directory = Path(argv[1])
    with deployment.assemble(settings) as running:
        env = running.services.env
        signer = Signer.from_seed(running.root.origin, deployment.read_key(running.settings.log_key))
        ledger = Ledger.open(directory / "ondemand", signer=signer, origin=running.root.origin, clock=env.clock)
        records = [canonical.parse_record(ledger.read_record(seq)) for seq in range(ledger.size)]
        (urn,) = {str(r["source"]) for r in records if r["type"] == forecast.TYPE}
        registry = Registry()
        for pair in argv[2:]:
            venue, capture_id = pair.split(":", 1)
            capture = env.captures.get(capture_id)
            listing = env.adapters[venue].parse(capture)
            assert isinstance(listing, InstrumentList), f"{capture_id} is not an instrument list"
            registry.admit(listing, scope="spot", known_at=capture.received_at, capture_id=capture.capture_id)
        ondemand.write_registry(ledger, registry, urn)
        sys.stdout.write(f"{len(registry.records())} lists written for {urn}; the ledger now holds {ledger.size} records\n")
        ledger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
