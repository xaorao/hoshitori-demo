"""The probe command against a venue on loopback: exit status and the JSON it prints."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

import pytest

from hoshitori import venue_probe_job
from tests.ryo_fake_server import Behaviour, FakeRyo, Seen


def venue(keep: Callable[[int], bool]) -> Callable[[Seen], Behaviour | None]:
    def script(seen: Seen) -> Behaviour | None:
        parts = urlsplit(seen.path)
        if parts.path.endswith("/time"):
            return Behaviour(200, json.dumps({"serverTime": int(time.time() * 1000)}).encode(), {"content-type": "application/json"})
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        start, end = int(query["startTime"]), int(query["endTime"])
        rows = []
        for n, opened in enumerate(range(start, end, 60_000)):
            if keep(n):
                rows.append(f'[{opened},"1.0","1.1","0.9","1.0","5.0",{opened + 59_999},"5.0",1,"1","1","0"]')
        return Behaviour(200, ("[" + ",".join(rows) + "]").encode(), {"content-type": "application/json"})

    return script


def run(server: FakeRyo, *extra: str) -> int:
    return venue_probe_job.main(
        ["--venue", "binance-spot", "--instruments", "ETHUSDT,BTCUSDT", "--base-url", server.url, "--allow-loopback", *extra]
    )


def test_a_venue_with_every_bar_can_settle(capsys: pytest.CaptureFixture[str]) -> None:
    with FakeRyo(script=venue(lambda n: True)) as server:
        assert run(server, "--minutes", "30") == 0
    found = json.loads(capsys.readouterr().out)
    assert (found["venue"], found["reachable"], found["can_settle"]) == ("binance-spot", True, True)
    assert [(i["instrument"], i["closed_slots"], i["present"], i["present_share"]) for i in found["instruments"]] == [
        ("BTCUSDT", 30, 30, "1"),
        ("ETHUSDT", 30, 30, "1"),
    ]


def test_a_thin_venue_cannot_settle(capsys: pytest.CaptureFixture[str]) -> None:
    with FakeRyo(script=venue(lambda n: n % 6 == 0)) as server:
        assert run(server, "--minutes", "30") == 1
    found = json.loads(capsys.readouterr().out)
    assert (found["reachable"], found["can_settle"], found["instruments"][0]["present"]) == (True, False, 5)


def test_a_venue_that_does_not_answer_is_a_finding_and_a_bad_request_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    with FakeRyo(script=lambda seen: Behaviour(hang_up=True)) as server:
        assert run(server, "--wait", "3") == 1
    found = json.loads(capsys.readouterr().out)
    assert (found["reachable"], found["can_settle"], found["instruments"], bool(found["problem"])) == (False, False, [], True)
    with FakeRyo(script=venue(lambda n: True)) as server:
        assert run(server, "--minutes", "0") == 2
    assert capsys.readouterr().out == "probe_venues: a probe looks at from 1 to 300 minutes\n"
    assert venue_probe_job.main(["--venue", "nowhere", "--instruments", "A"]) == 2
    assert venue_probe_job.main(["--help"]) == 0
