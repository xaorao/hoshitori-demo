#!/usr/bin/env python3
"""Rebuild the verification bundle of an on-demand commitment from what the run left behind, for a run that kept no commitment store.

    python scripts/ondemand_bundle.py SETTINGS DIR OUT

``DIR/ondemand`` is the on-demand ledger; the league copy named by ``SETTINGS`` holds the gateway's capture store, where the time-stamp
authorities' answers were kept. The checkpoint note is the one the ledger remembered as attested (``attested.jsonl``); each authority's
answer is the capture whose request carried that note's digest. The bundle holds the forecast, proved against that checkpoint. Verify it
offline with ``python -m hoshitori.cli OUT --trusted-root ROOT`` and with
``node verifier-js/bin/verify_bundle.mjs OUT --trusted-root ROOT``, the second written independently of the first.
A lone forecast has no cycle around it, so the bundle is not scorable (completeness is not shown); its integrity and its time level are.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

from hoshitori import bundle, canonical, deployment
from hoshitori.ledger import Ledger
from hoshitori.note import Signer, split_note


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.stderr.write(__doc__ or "")
        return 2
    settings_path, directory, out = Path(argv[0]), Path(argv[1]), Path(argv[2])
    settings = deployment.load_settings(settings_path)
    base = settings_path.parent / settings.directory
    attested = [json.loads(line) for line in (directory / "ondemand" / "attested.jsonl").read_text().splitlines() if line.strip()]
    last = attested[-1]
    note = base64.b64decode(last["note"])
    digest = hashlib.sha256(split_note(note)[0]).hexdigest()
    tokens: dict[str, bytes] = {}
    for line in (base / "captures" / "captures.jsonl").read_text().splitlines():
        entry = json.loads(line)
        request = entry.get("request", {})
        if request.get("operation") == "stamp" and request.get("arguments", {}).get("digest") == digest:
            name = str(request["url"]).split("//")[1].split("/")[0].split(".")[1]  # timestamp.digicert.com -> digicert
            sha = entry["body_sha256"]
            tokens[name] = (base / "captures" / "blobs" / sha[:2] / sha).read_bytes()
    if len(tokens) < 2:
        sys.stderr.write(f"found {len(tokens)} answers for the digest {digest}; at least two are needed\n")
        return 1
    with deployment.assemble(settings) as running:
        signer = Signer.from_seed(running.root.origin, deployment.read_key(running.settings.log_key))
        ledger = Ledger.open(directory / "ondemand", signer=signer, origin=running.root.origin, clock=running.services.env.clock)
        size = int(last["size"])
        forecasts = [seq for seq in range(size) if canonical.parse_record(ledger.read_record(seq))["type"] == "forecast"]
        bundle.export_bundle(out, ledger, forecasts, checkpoint_note=note, size=size, tokens=tokens)
        ledger.close()
    sys.stdout.write(f"bundle written to {out}: answers from {sorted(tokens)} for the checkpoint of size {last['size']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
