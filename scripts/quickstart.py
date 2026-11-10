#!/usr/bin/env python3
"""Start a league on this machine from nothing, run one cycle against the real exchanges, and write the interface's documents.

    python scripts/quickstart.py WORKDIR [--days 60]

It needs no key and no account. It makes a log signing key, asks two public time-stamp authorities for a token (so the trusted root names
them), reads about two months of hourly history from Binance and Kraken to freeze the rules agent's mapping, writes the protocol and the
settings, runs one cycle (collecting evidence, deciding, committing with two independent time-stamp tokens, opening practice
positions), runs the failure drills on the scripted world of the tests (nothing live), writes invented examples for the Claims, Desk and
Laboratory pages (labelled invented on the pages; no model, no RYO data) and exports the documents of the interface, the
drills among them, with a bundle for the committed cycle. Everything is an exploratory run: not a scored period, no RYO tool, no model. The
one-hour calls settle an hour later: run ``hoshitori-league run WORKDIR/settings.json`` to keep it going.

The network is used: Binance (not reachable from every country), Kraken, DigiCert and GlobalSign. Nothing is sent but public market-data
requests and one random digest to each authority.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTRUMENTS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
    "ADA": "ADAUSDT",
    "DOGE": "DOGEUSDT",
    "LINK": "LINKUSDT",
}
AUTHORITIES = (
    {"name": "digicert", "url": "http://timestamp.digicert.com"},
    {"name": "globalsign", "url": "http://timestamp.globalsign.com/tsa/r6advanced1"},
)


def run(*args: str, out: Path | None = None) -> str:
    """Run one step in a child process; stop with its error output if it fails."""
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(ROOT), *filter(None, [os.environ.get("PYTHONPATH")])]),
    }  # the drills import the test world
    done = subprocess.run([sys.executable, *args], capture_output=True, text=True, cwd=ROOT, check=False, env=env)  # noqa: S603 - our own scripts
    if done.returncode != 0:
        sys.stderr.write(f"step failed: {' '.join(args[:3])}\n{done.stderr[-1500:]}\n")
        raise SystemExit(1)
    if out is not None:
        out.write_text(done.stdout, encoding="utf-8")
    return done.stdout


def main() -> int:
    parser = argparse.ArgumentParser(prog="quickstart", description=__doc__.split("\n\n")[0])
    parser.add_argument("workdir", type=Path)
    parser.add_argument("--days", type=int, default=60, help="days of hourly history for the rules agent's mapping")
    args = parser.parse_args()
    work: Path = args.workdir.resolve()
    try:  # the Laboratory's examples are checked against the indicator skill's grammar; find out now, not after seven minutes
        import indicator_condition_check  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "the indicator skill is not installed, and the Laboratory step needs its grammar.\n"
            "Install it, then run this again: uv pip install ./skills/indicator-condition-check\n"
            "(without uv: .venv/bin/pip install ./skills/indicator-condition-check)\n"
        )
        return 2
    if work.exists() and any(work.iterdir()):
        sys.stderr.write(f"{work} is not empty; use a new folder\n")
        return 2
    work.mkdir(parents=True, exist_ok=True)
    instruments = ",".join(INSTRUMENTS.values())
    print("1/6 a log signing key")
    key = json.loads(run("-m", "hoshitori.league_cli", "init-key", str(work / "log.key"), "hoshitori.example/exploratory-log"))
    print("2/6 a trusted root naming two time-stamp authorities (asks each for a token)")
    run(
        "scripts/make_exploratory_root.py",
        str(work / "root.json"),
        "hoshitori.example/exploratory-log",
        key["public_key"],
        "digicert",
        "globalsign",
    )
    print(f"3/6 the rules agent's mapping from {args.days} days of real hourly history (Binance)")
    run(
        "scripts/build_mapping.py", "--venue", "binance-spot", "--quote-class", "USDT", "--instruments", instruments,
        "--days", str(args.days), "--out", str(work / "mapping.json"), "--work", str(work / "mapwork"),
    )  # fmt: skip
    print("4/6 the barrier mapping from the same history")
    run(
        "scripts/build_barrier_mapping.py", "--venue", "binance-spot", "--quote-class", "USDT", "--instruments", instruments,
        "--days", str(args.days), "--out", str(work / "barrier.json"), "--work", str(work / "barrierwork"),
    )  # fmt: skip
    print("5/6 the protocol and the settings")
    start = (int(time.time()) // 60 - 1) * 60_000
    run(
        "-m", "hoshitori.league_cli", "make-protocol", str(work / "protocol.json"), "--root", str(work / "root.json"),
        "--mapping", f"L0={work / 'mapping.json'}", "--barrier-mapping", f"L0={work / 'barrier.json'}", "--name", "quickstart",
        "--start-ms", str(start), "--every-minutes", "60", "--watchlist", ",".join(INSTRUMENTS), "--second", "kraken",
    )  # fmt: skip
    settings = {
        "directory": "run",
        "protocol": "protocol.json",
        "mappings": {"L0": "mapping.json"},
        "trusted_root": "root.json",
        "log_key": "log.key",
        "source": "urn:hoshitori:league:quickstart", "venues": {"binance-spot": None, "kraken": None}, "authorities": list(AUTHORITIES),
        "lists": [{"venue": "binance-spot", "scope": "spot"}, {"venue": "kraken", "scope": "spot"}], "tick_every_s": 30,
        "start_grace_ms": 600000, "finish_within_ms": 1200000, "wait_s": 60, "allow_insecure_loopback": False,
        "allow_plain_http_authorities": True, "barrier_mappings": {"L0": "barrier.json"}, "practice": True, "models": None,
    }  # fmt: skip
    (work / "settings.json").write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print("6/6 one cycle, then the interface's documents")
    run("-m", "hoshitori.league_cli", "run", str(work / "settings.json"), "--once", out=work / "first_tick.json")
    run("scripts/make_drill.py", str(work / "drill.json"))
    # invented examples for the three pages that are empty on a fresh install; each says on the page that it is invented
    run("scripts/make_synthetic_claims.py", str(work / "claims.json"))
    run("scripts/make_synthetic_desk_runs.py", str(work / "desk_runs.json"))
    run("scripts/make_synthetic_lab.py", str(work / "lab.jsonl"))
    run(
        "-m",
        "hoshitori.league_cli",
        "site",
        str(work / "settings.json"),
        str(work / "site"),
        "--status",
        "docs/STATUS.md",
        "--sweeps",
        "docs/SWEEP.md",
        "--drill",
        str(work / "drill.json"),
        "--claims",
        str(work / "claims.json"),
        "--desk-runs",
        str(work / "desk_runs.json"),
        "--lab",
        str(work / "lab.jsonl"),
        *(("--coverage", "docs/specs") if Path("docs/specs/CONTRACT_MATRIX.md").exists() else ()),
    )
    league = Path(sys.executable).parent / "hoshitori-league"
    print(f"\ndone. Documents: {work / 'site'}")
    print(f"Keep it going (one cycle an hour, settling as the hours pass): {league} run {work / 'settings.json'}")
    print(f"Look at the ledger: {league} status {work / 'settings.json'}")
    print(
        "See the pages: cd web && npm ci && npm run build, copy the documents to web/dist/data, and serve web/dist with any static server."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
