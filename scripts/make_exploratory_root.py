"""Make a trusted root for an exploratory run against the real time-stamp authorities (see ``hoshitori.exploratory_root``).

    python scripts/make_exploratory_root.py OUT.json ORIGIN LOG_VKEY digicert globalsign [apple ...]

One request for a random digest goes to each named authority. The registry of operators (who runs each authority, with the public record
that says so) is ``data/operator_registry.json``.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from hoshitori import exploratory_root

REGISTRY = Path(__file__).resolve().parents[1] / "data" / "operator_registry.json"
URLS = {
    "digicert": "http://timestamp.digicert.com",
    "globalsign": "http://timestamp.globalsign.com/tsa/r6advanced1",
    "apple": "http://timestamp.apple.com/ts01",
    "sectigo": "http://timestamp.sectigo.com",
}


def post(url: str, request: bytes) -> bytes:
    sent = urllib.request.Request(url, data=request, headers={"Content-Type": "application/timestamp-query"}, method="POST")  # noqa: S310
    with urllib.request.urlopen(sent, timeout=30) as reply:  # noqa: S310 - the authority's own public endpoint
        return bytes(reply.read(1_000_000))


def main(argv: list[str]) -> int:
    if len(argv) < 5:
        sys.stderr.write(__doc__ or "")
        return 2
    out, origin, vkey, names = Path(argv[0]), argv[1], argv[2], argv[3:]
    if out.exists():
        sys.stderr.write("a root file is never overwritten\n")
        return 2
    unknown = [n for n in names if n not in URLS]
    if unknown:
        sys.stderr.write(f"no endpoint is known for {unknown}; known: {sorted(URLS)}\n")
        return 2
    throwaway = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)  # the private half is never kept
    try:
        document = exploratory_root.build(
            origin=origin,
            log_vkey=vkey,
            authorities=[(n, URLS[n]) for n in names],
            registry=json.loads(REGISTRY.read_text()),
            post=post,
            root_public_key=throwaway,
            expires=datetime.now(UTC).replace(microsecond=0) + timedelta(days=90),
        )
        out.write_bytes(exploratory_root.write(document))
    except exploratory_root.ExploratoryRootError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(f"wrote {out}: exploratory root, update key discarded; a run under it is not a scored period\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
