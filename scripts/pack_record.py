#!/usr/bin/env python3
"""Pack a league's public record into one file a reader can browse and verify offline.

    python scripts/pack_record.py SETTINGS OUT.tar.gz [--status docs/STATUS.md] [--sweeps docs/SWEEP.md] [--signals SIGNALS.json]

It exports the interface's documents for the league named by SETTINGS (the same export as ``hoshitori-league site``: receipts, cycles,
the record and a bundle per committed cycle, with exchange data listed by digest only), then writes them as one gzip-compressed tar in a
fixed order with fixed times and owners, so the same record packs to the same bytes. Unpack it into ``web/dist/data`` to browse it, or point
the offline verifiers at a folder under ``bundles/`` with ``trusted_root.json`` as the root.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pack(folder: Path, out: Path) -> int:
    """Write every file under ``folder`` into ``out``, sorted by path, with time 0 and no owner names; return the number of files."""
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for path in files:
            info = tarfile.TarInfo(path.relative_to(folder).as_posix())
            info.size = path.stat().st_size
            info.mtime = 0
            info.mode = 0o644
            info.uname = info.gname = ""
            with path.open("rb") as source:
                tar.addfile(info, source)
    with out.open("wb") as target, gzip.GzipFile(fileobj=target, mode="wb", mtime=0, filename="") as zipped:
        zipped.write(raw.getvalue())
    return len(files)


def forge_newest(settings: Path, cycles: Path, out: Path) -> Path | None:
    """The forged copy of the newest committed cycle (``hoshitori-league forge-demo``); none if there is no cycle or it cannot be made."""
    try:
        rows = json.loads(cycles.read_text(encoding="utf-8"))
        newest = max(
            (r for r in rows if r["state"] == "COMMITTED" and isinstance(r["committed_at"], str)), key=lambda r: r["committed_at"]
        )["cycle_id"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    made = subprocess.run(  # noqa: S603 - our own command
        [sys.executable, "-m", "hoshitori.league_cli", "forge-demo", str(settings), newest, str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if made.returncode != 0:
        sys.stderr.write(f"the forged copy was not made:\n{made.stderr[-800:]}\n")
        return None
    return out


def credential_findings(folder: Path) -> list[str]:
    """Where a credential-looking string sits in a folder about to be packed (the scan skips compressed files, so it runs first)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import secret_scan  # the repository's own scan, the one the CI runs
    finally:
        sys.path.pop(0)
    env = ROOT / ".env"
    known = secret_scan.env_values(env) if env.is_file() else []
    return [f"{path}:{line}: {label}" for path in secret_scan.iter_files([folder]) for line, label in secret_scan.scan_file(path, known)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pack_record", description=__doc__.split("\n\n")[0])
    parser.add_argument("settings", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--status", default="docs/STATUS.md")
    parser.add_argument("--sweeps", default="docs/SWEEP.md")
    parser.add_argument(
        "--with-examples",
        action="store_true",
        help="add the drills (run on the scripted world of the tests) and the invented examples of the Claims, Desk and Laboratory pages, "
        "each labelled on its page, as the quickstart does",
    )
    parser.add_argument(
        "--signals", type=Path, default=None, help="the collected voices and wallets (hoshitori-league signals) for the Voices page"
    )
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp) / "site"
        extra: list[str] = [] if args.signals is None else ["--signals", str(args.signals)]
        if args.with_examples:
            env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), *filter(None, [os.environ.get("PYTHONPATH")])])}
            for script, name, flag in (
                ("scripts/make_drill.py", "drill.json", "--drill"),
                ("scripts/make_synthetic_claims.py", "claims.json", "--claims"),
                ("scripts/make_synthetic_desk_runs.py", "desk_runs.json", "--desk-runs"),
                ("scripts/make_synthetic_lab.py", "lab.jsonl", "--lab"),
            ):
                target = str(Path(tmp) / name)
                made = subprocess.run(  # noqa: S603 - our own scripts
                    [sys.executable, script, target], cwd=ROOT, env=env, capture_output=True, text=True, check=False
                )
                if made.returncode != 0:
                    sys.stderr.write(f"{script} failed:\n{made.stderr[-1500:]}\n")
                    return 1
                extra += [flag, target]

        def export(target: Path, more: list[str]) -> subprocess.CompletedProcess[str]:
            return subprocess.run(  # noqa: S603 - our own command
                [
                    sys.executable,
                    "-m",
                    "hoshitori.league_cli",
                    "site",
                    str(args.settings),
                    str(target),
                    "--status",
                    args.status,
                    "--sweeps",
                    args.sweeps,
                    *extra,
                    *more,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        done = export(site, [])
        if done.returncode != 0:
            sys.stderr.write(f"the export failed:\n{done.stderr[-1500:]}\n")
            return 1
        # The Verify page shows a forged copy of the newest cycle's bundle beside the genuine one: the records pass and the time-stamps do
        # not. It is made from the first export, then the site is exported again with it.
        forged = forge_newest(args.settings, site / "cycles.json", Path(tmp) / "forged")
        if forged is not None:
            shutil.rmtree(site)
            done = export(site, ["--forgery", str(forged)])
            if done.returncode != 0:
                sys.stderr.write(f"the export with the forged copy failed:\n{done.stderr[-1500:]}\n")
                return 1
        if found := credential_findings(site):
            sys.stderr.write("the record was not packed: a credential-looking string is in the export:\n" + "\n".join(found[:20]) + "\n")
            return 1
        count = pack(site, args.out)
    print(f"{args.out}: {count} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
