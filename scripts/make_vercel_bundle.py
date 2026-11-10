#!/usr/bin/env python3
"""Assemble the static site for a Vercel deployment: the built interface, an exported record under ``data/`` and a ``vercel.json`` that
sends the security headers of ``deploy/security_headers.json`` on every response.

Usage: python scripts/make_vercel_bundle.py WEB_DIST RECORD_DIR OUT_DIR [--keep-bundles N]    (OUT_DIR must not exist yet)

A host limits the number of files in one deployment, and each cycle's verification bundle holds about two thousand files.
``--keep-bundles N`` hosts the bundles of the newest N cycles (and of the cycle the forged copy was made from); the index and the list
of bundles are rewritten to say so, and the repository's record still holds every bundle.
Then, from OUT_DIR and with the owner's own Vercel login: ``npx vercel deploy --prod``. Nothing is uploaded by this script. The site is
static, so Vercel needs no build step and no environment variable. Run ``scripts/secret_scan.py`` on the folder before deploying.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADERS = ROOT / "deploy" / "security_headers.json"


def vercel_config(headers: dict[str, str]) -> dict[str, object]:
    """The project file: no build, the folder as it is, and the policy headers on every path."""
    return {
        "$schema": "https://openapi.vercel.sh/vercel.json",
        "buildCommand": None,
        "framework": None,
        "outputDirectory": ".",
        "headers": [{"source": "/(.*)", "headers": [{"key": key, "value": value} for key, value in headers.items()]}],
    }


def keep_newest_bundles(site: Path, keep: int) -> int:
    """Drop the bundles of all but the newest ``keep`` cycles (the forged copy's cycle stays); return how many bundles remain."""
    if keep < 1:
        raise SystemExit("make_vercel_bundle: --keep-bundles must be at least 1")
    listed = json.loads((site / "bundles.json").read_text(encoding="utf-8"))
    forged = site / "forgery.json"
    pinned = json.loads(forged.read_text(encoding="utf-8"))["cycle_id"] if forged.is_file() else None
    kept = {b["cycle_id"] for b in listed[-keep:]} | ({pinned} if pinned else set())
    remaining = [b for b in listed if b["cycle_id"] in kept]
    for entry in listed:
        if entry["cycle_id"] not in kept:
            shutil.rmtree(site / entry["path"])
    body = canonical(remaining)
    (site / "bundles.json").write_bytes(body)
    index_path = site / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["files"]["bundles.json"] = hashlib.sha256(body).hexdigest()
    index["counts"]["bundles"] = len(remaining)
    index_path.write_bytes(canonical(index))
    return len(remaining)


def canonical(value: object) -> bytes:
    """The bytes the exporter writes for a document: sorted keys, no spaces, UTF-8, one newline."""
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def assemble(dist: Path, record: Path, out: Path, keep_bundles: int | None = None) -> int:
    """Write the folder; return how many files it holds."""
    if out.exists():
        raise SystemExit(f"make_vercel_bundle: {out} exists already")
    if not (dist / "index.html").is_file():
        raise SystemExit(f"make_vercel_bundle: {dist} holds no built interface (index.html)")
    if (dist / "data").exists():
        raise SystemExit(f"make_vercel_bundle: {dist}/data exists; build the interface without data, the record is copied here")
    if not (record / "index.json").is_file():
        raise SystemExit(f"make_vercel_bundle: {record} is not an exported site (no index.json)")
    shutil.copytree(dist, out)
    shutil.copytree(record, out / "data")
    if keep_bundles is not None:
        keep_newest_bundles(out / "data", keep_bundles)
    headers = json.loads(HEADERS.read_text(encoding="utf-8"))
    (out / "vercel.json").write_text(json.dumps(vercel_config(headers), indent=2) + "\n", encoding="utf-8")
    return sum(1 for p in out.rglob("*") if p.is_file())


def main(argv: list[str]) -> int:
    keep = None
    if "--keep-bundles" in argv:
        at = argv.index("--keep-bundles")
        try:
            keep = int(argv[at + 1])
        except (IndexError, ValueError):
            sys.stderr.write("usage: --keep-bundles needs a whole number\n")
            return 2
        argv = argv[:at] + argv[at + 2 :]
    if len(argv) != 3:
        sys.stderr.write("usage: make_vercel_bundle.py WEB_DIST RECORD_DIR OUT_DIR [--keep-bundles N]\n")
        return 2
    sys.stdout.write(f"{assemble(Path(argv[0]), Path(argv[1]), Path(argv[2]), keep)} files written to {argv[2]}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
