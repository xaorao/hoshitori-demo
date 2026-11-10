"""The history reader and the mapping job: the checks pass on the real module, every small change is noticed, and the job runs end to end
against a fake exchange on loopback."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hoshitori import history, mapping_job, rules_arm
from hoshitori.clock import SystemClock
from hoshitori.timeutil import format_rfc3339_ms
from hoshitori.venue_binance import BinanceSpot
from tests.collect_checks import klines
from tests.history_checks import failures
from tests.mutation_sweep import survivors
from tests.ryo_fake_server import Behaviour
from tests.venue_fake_server import FakeVenue


def test_real_module_passes_every_check() -> None:
    assert failures(history) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.history", "tests.history_checks")
    assert total > 70, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


def test_the_job_builds_a_mapping_file_from_a_fake_exchange(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clock = SystemClock()
    adapter = BinanceSpot()
    server = FakeVenue({})
    server.start()
    try:
        ok = {"content-type": "application/json"}
        now = clock.now()
        end = datetime.fromtimestamp(int(now.timestamp()) // 3600 * 3600, tz=UTC)
        told = int((end + timedelta(minutes=5)).timestamp() * 1000)
        built = adapter.build_request("time", {})
        server.routes[(built.method, built.path, built.body)] = Behaviour(200, f'{{"serverTime":{told}}}'.encode(), ok)
        for name in ("BTCUSDT", "ETHUSDT"):
            for page in range(3):
                stop = end - timedelta(hours=300 * page)
                window = {
                    "instrument": name,
                    "interval_s": 3600,
                    "start": format_rfc3339_ms(stop - timedelta(hours=300)),
                    "end": format_rfc3339_ms(stop),
                }
                args = adapter.normalise("candles", window)
                request = adapter.build_request("candles", args)
                server.routes[(request.method, request.path, request.body)] = Behaviour(200, klines(args), ok)
        out = tmp_path / "mapping.json"
        status = mapping_job.main(
            [
                "--venue", "binance-spot", "--quote-class", "USDT", "--instruments", "ETHUSDT,BTCUSDT", "--days", "26",
                "--out", str(out), "--work", str(tmp_path / "work"), "--base-url", server.url, "--allow-loopback", "--wait", "30", "--strict",
            ]
        )  # fmt: skip
        assert status == 0
        shown = json.loads(capsys.readouterr().out)
        loaded = rules_arm.load(out.read_bytes())
        assert shown["sha256"] == loaded.sha256 and loaded.instruments == ("BTCUSDT", "ETHUSDT")
        assert shown["bars"] == {"BTCUSDT": 900, "ETHUSDT": 900} and shown["failures"] == []
        assert loaded.period_end_ms == int(end.timestamp() * 1000) and loaded.venue == "binance-spot" and loaded.quote_class == "USDT"
        assert sum(c.n + c.ties for (h, _), c in loaded.cells if h == 1) > 0, "windows were counted"
        # the same history gives the same mapping
        assert (
            rules_arm.calibrate(
                {n: [] for n in ("BTCUSDT", "ETHUSDT")}, venue="v", quote_class="q", period_start_ms=0, period_end_ms=0
            ).skipped
            == 0
        )
    finally:
        server.stop()


def test_the_job_says_when_the_history_cannot_be_read(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    server = FakeVenue({})
    server.start()
    try:
        status = mapping_job.main(
            [
                "--venue", "binance-spot", "--quote-class", "USDT", "--instruments", "BTCUSDT", "--days", "1", "--out", str(tmp_path / "m.json"),
                "--work", str(tmp_path / "w"), "--base-url", server.url, "--allow-loopback", "--wait", "3",
            ]
        )  # fmt: skip
        assert status == 1
        assert "the history could not be read: the venue did not tell its time" in capsys.readouterr().err
        assert not (tmp_path / "m.json").exists()
    finally:
        server.stop()
