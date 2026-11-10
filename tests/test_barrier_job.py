"""The barrier mapping job against a fake exchange on loopback: the file it writes, the summary it prints, the exit status."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from hoshitori import barrier_job, barrier_rates
from tests.ryo_fake_server import Behaviour, FakeRyo, Seen

OK = {"content-type": "application/json"}


def exchange(seen: Seen) -> Behaviour | None:
    """Binance's clock and klines: a gentle zigzag around 1000 for every slot of the window asked for, hourly or by the minute."""
    parts = urlsplit(seen.path)
    if parts.path.endswith("/time"):
        import time

        return Behaviour(200, json.dumps({"serverTime": int(time.time() * 1000)}).encode(), OK)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    step = {"1h": 3_600_000, "1m": 60_000}[query["interval"]]
    start, end = int(query["startTime"]), int(query["endTime"])
    rows, previous = [], 1000
    for n, opened in enumerate(range(start, end, step)):
        close = 1000 + (n * 7) % 23
        high, low = max(previous, close) + 1, min(previous, close) - 1
        rows.append(
            f'[{opened},"{previous}.00","{high}.00","{low}.00","{close}.00","12.5",{opened + step - 1},"987.6",120,"6.1","482.0","0"]'
        )
        previous = close
    return Behaviour(200, ("[" + ",".join(rows) + "]").encode(), OK)


def test_the_job_builds_a_barrier_mapping_from_a_fake_exchange(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "barrier.json"
    with FakeRyo(script=exchange) as server:
        status = barrier_job.main(
            [
                "--venue", "binance-spot", "--quote-class", "USDT", "--instruments", "ETHUSDT,BTCUSDT", "--days", "1",
                "--out", str(out), "--work", str(tmp_path / "work"), "--base-url", server.url, "--allow-loopback", "--wait", "60", "--strict",
            ]
        )  # fmt: skip
    assert status == 0
    shown = json.loads(capsys.readouterr().out)
    loaded = barrier_rates.load(out.read_bytes())
    assert shown["sha256"] == loaded.sha256 and loaded.instruments == ("BTCUSDT", "ETHUSDT")
    assert shown["hourly_bars"] == {"BTCUSDT": 300, "ETHUSDT": 300} and shown["minute_bars"] == {"BTCUSDT": 1500, "ETHUSDT": 1500}
    assert shown["failures"] == [] and loaded.venue == "binance-spot" and loaded.quote_class == "USDT"
    assert shown["horizons"]["1"]["p"] is None, "a day of history is far below the minimum of settled windows"
    assert sum(c.n + c.ambiguous for _, c in loaded.cells) > 0, "windows were counted"
    assert loaded.skipped > 0 and loaded.period_start_ms < loaded.period_end_ms


def test_the_job_says_when_the_history_cannot_be_read_and_refuses_a_bad_request(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with FakeRyo(script=lambda seen: Behaviour(hang_up=True)) as server:
        status = barrier_job.main(
            ["--venue", "binance-spot", "--quote-class", "USDT", "--instruments", "BTCUSDT", "--days", "1", "--out", str(tmp_path / "x.json"),
             "--work", str(tmp_path / "w"), "--base-url", server.url, "--allow-loopback", "--wait", "3"]
        )  # fmt: skip
    assert status == 1 and "the history could not be read" in capsys.readouterr().err
    assert not (tmp_path / "x.json").exists()
    assert barrier_job.main(["--venue", "nowhere"]) == 2
    assert barrier_job.main(["--help"]) == 0
