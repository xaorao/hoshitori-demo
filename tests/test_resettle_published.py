"""The outsider's re-settlement (``scripts/resettle_published.py``) against a loopback Binance: a published call that matches the candles
passes, an edited start price, end price or outcome fails, a call with no candle is not checked, and calls it cannot check are counted."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "resettle_published.py"
MINUTE = 60_000
START = 1_794_225_600_000  # 2026-11-09T12:00:00Z
CLOSES = {START - MINUTE: "100.50", START + 3_600_000 - MINUTE: "101.25"}  # the candle closing at the start, and one hour later


class Exchange(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        first = int(query["startTime"][0])
        rows = [[t, "0", "0", "0", c, "0"] for t, c in sorted(CLOSES.items()) if t >= first and query["symbol"][0] == "BTCUSDT"]
        body = json.dumps(rows).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


def receipt(fid: str, *, symbol: str = "BTC", p0: str = "100.5", ph: str = "101.25", result: str = "Rose", contract: str = "D") -> dict:
    return {
        "row": {
            "forecast_id": fid,
            "symbol": symbol,
            "hours": 1,
            "contract": contract,
            "origin": "2026-11-09T12:00:00.000Z",
            "result": result,
        },
        "settlement": {"state": "SETTLED", "reference_price": p0, "outcome_price": ph},
    }


def run(tmp_path: Path, receipts: list[dict]) -> subprocess.CompletedProcess[str]:
    folder = tmp_path / "data" / "receipts"
    folder.mkdir(parents=True)
    for r in receipts:
        (folder / f"{r['row']['forecast_id']}.json").write_text(json.dumps(r))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Exchange)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(tmp_path / "data"), "--binance", f"http://127.0.0.1:{server.server_address[1]}"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        server.shutdown()


def test_a_published_call_that_matches_the_candles_passes(tmp_path: Path) -> None:
    done = run(tmp_path, [receipt("fc-a"), receipt("fc-b", contract="B"), {**receipt("fc-c"), "settlement": None}])
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.strip().endswith(
        "1 match, 0 do not, 0 without a candle; 2 not checked (not settled, target-or-stop, or past the limit)"
    )


def test_an_edited_start_end_or_outcome_is_caught(tmp_path: Path) -> None:
    done = run(tmp_path, [receipt("fc-a", p0="100.49"), receipt("fc-b", ph="101.3"), receipt("fc-c", result="Fell"), receipt("fc-d")])
    assert done.returncode == 1
    failed = [line.split()[1] for line in done.stdout.splitlines() if line.startswith("FAIL")]
    assert failed == ["fc-a", "fc-b", "fc-c"]
    assert "1 match, 3 do not" in done.stdout


def test_a_call_with_no_candle_is_not_checked_and_never_passes(tmp_path: Path) -> None:
    done = run(tmp_path, [receipt("fc-e", symbol="ETH")])
    assert done.returncode == 0
    assert "NOT CHECKED fc-e ETH 1h: Binance has no candle there" in done.stdout
    assert "0 match, 0 do not, 1 without a candle" in done.stdout


def test_trailing_zeros_are_the_same_price(tmp_path: Path) -> None:
    done = run(tmp_path, [receipt("fc-a", p0="100.50000000", ph="101.25000000")])
    assert done.returncode == 0 and "1 match" in done.stdout


def test_without_a_folder_it_says_how_to_use_it() -> None:
    done = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=30, check=False)
    assert done.returncode == 2 and "resettle_published.py DATA_DIR" in done.stderr
