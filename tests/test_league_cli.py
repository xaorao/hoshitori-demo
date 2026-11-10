"""``hoshitori-league`` against the fake outside world: a key, a settings file, one tick, the status, an export and the offline verdict."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hoshitori import canonical, deployment, league_cli, protocol, rules_arm
from hoshitori.cli import main as verify_main
from hoshitori.timeline import to_ms
from tests import root_support as R
from tests.e2e_league import MINUTE, OffsetClock, outside
from tests.protocol_checks import make
from tests.rules_arm_checks import graded


def write_world(directory: Path, world: Any, *, key_origin: str = R.LOG_ORIGIN) -> Path:
    """A settings file and every file it names, for a league that runs against ``world``."""
    key = directory / "log.key"
    vkey = deployment.new_key(key, key_origin)
    document = R.make_document(
        1,
        root_keys=[R.new_signer(1)],
        eras=[{"vkey": vkey, "from_size": 0, "through_size": None}],
        pinned=world.pinned(),
    )
    root_bytes = canonical.dumps(document)  # type: ignore[arg-type]
    (directory / "root.json").write_bytes(root_bytes)
    import hashlib

    mapping = graded(rules_arm)
    (directory / "mapping.json").write_bytes(rules_arm.dump(mapping))
    start = (to_ms(OffsetClock().now()) // MINUTE - 1) * MINUTE
    rules = make(
        protocol,
        name="cli",
        primary_venue="binance-spot",
        second_venue="okx",
        schedule_start_ms=start,
        watchlist=("BTC", "ETH"),
        arms=(("L0", mapping.sha256),),
        clock_offset_limit_ms=5_000,
        wait_s=20,
        root_version=1,
        root_sha256=hashlib.sha256(root_bytes).hexdigest(),
    )
    (directory / "protocol.json").write_bytes(protocol.dump(rules))
    settings = {
        "directory": "run",
        "protocol": "protocol.json",
        "mappings": {"L0": "mapping.json"},
        "barrier_mappings": {},
        "trusted_root": "root.json",
        "log_key": "log.key",
        "source": "urn:hoshitori:league:cli",
        "venues": {"binance-spot": world.exchange.url, "okx": world.exchange.url},
        "authorities": world.authorities,
        "lists": [{"venue": "binance-spot", "scope": "spot"}, {"venue": "okx", "scope": "spot", "kind": "spot"}],
        "tick_every_s": 0.05,
        "start_grace_ms": 10 * MINUTE,
        "finish_within_ms": 20 * MINUTE,
        "wait_s": 20,
        "allow_insecure_loopback": True,
        "allow_plain_http_authorities": False,
        "practice": False,
        "models": None,
    }
    path = directory / "settings.json"
    path.write_text(json.dumps(settings))
    return path


def lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_a_league_from_a_settings_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with outside(OffsetClock()) as world:
        settings = write_world(tmp_path, world)
        assert league_cli.main(["run", str(settings), "--once"]) == 0
        (tick,) = lines(capsys)
        assert tick["ran"]["state"] == "COMMITTED" and tick["ran"]["level"] == "C3" and tick["ran"]["slots"] == {"FORECAST": 6}
        assert tick["closed"] == [] and tick["findings"] == [] and tick["refreshed"] == {"records": 2, "failures": 0, "failed": []}

        assert league_cli.main(["status", str(settings)]) == 0
        (state,) = lines(capsys)
        assert state["cycles"] == {"COMMITTED": 1} and state["forecasts"] == 6 and state["failures"] == 0 and state["heartbeat"] == []

        cycle_id = tick["ran"]["cycle"]
        out = tmp_path / "bundle"
        assert league_cli.main(["export", str(settings), cycle_id, str(out)]) == 0
        (done,) = lines(capsys)
        # the whole batch of the cycle and its committing step, not only the six forecasts and the six records they cite
        held = len(list((out / "items").glob("*.canonical.json")))
        assert done["records"] + done["later_records"] == held and held > 12
        assert verify_main([str(out), "--trusted-root", str(tmp_path / "root.json")]) == 0
        verdict = json.loads(capsys.readouterr().out)
        assert verdict["timing"]["level"] == "C3" and verdict["eligibility"]["scorable"] and verdict["integrity"]["ok"]


def test_a_second_run_does_not_repeat_a_slot_and_a_bad_settings_file_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with outside(OffsetClock()) as world:
        settings = write_world(tmp_path, world)
        assert league_cli.main(["run", str(settings), "--once"]) == 0
        capsys.readouterr()
        assert league_cli.main(["run", str(settings), "--once"]) == 0
        (again,) = lines(capsys)
        assert again["ran"] is None and again["closed"] == []
        # another root than the one the protocol was pre-registered with
        document = json.loads((tmp_path / "root.json").read_text())
        document["version"] = 2
        (tmp_path / "root.json").write_bytes(canonical.dumps(document))
        assert league_cli.main(["status", str(settings)]) == 2
        assert "pre-registered under another trusted root" in capsys.readouterr().err


def test_init_key_refuses_to_overwrite_and_run_refuses_an_open_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    key = tmp_path / "k"
    assert league_cli.main(["init-key", str(key), R.LOG_ORIGIN]) == 0
    (made,) = lines(capsys)
    assert made["public_key"].startswith(R.LOG_ORIGIN + "+")
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    assert league_cli.main(["init-key", str(key), R.LOG_ORIGIN]) == 2
    key.chmod(0o644)
    with pytest.raises(deployment.DeploymentError, match="can be read by others"):
        deployment.read_key(key)


def test_the_league_command_passes_every_check() -> None:
    from tests.league_cli_checks import failures

    assert failures(league_cli) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
CLI_EQUIVALENT: dict[str, str] = {
    "|statement dropped|raise SystemExit(main())|0": "the entry guard cannot run inside a sweep; test_the_module_runs_as_a_program runs the real one",
    "|str+~|'__main__'|0": "the entry guard cannot run inside a sweep; test_the_module_runs_as_a_program runs the real one",
    "_run|And->Or|running.ryo is not None and running.services.ryo is not None|0": (
        "`assemble` sets the RYO adapter and the league's wiring together or neither, so either half alone says the same; the `and` only "
        "narrows both types for the code under it"
    ),
}

# The two lines of the entry guard: a change to one of them can look noticed on a machine under load, where a check runs out of time.
GUARD: set[str] = {
    "|statement dropped|raise SystemExit(main())|0",
    "|str+~|'__main__'|0",
    # Run alone, this change survives (no check can tell it: see its reason). Under eight workers a check of the RYO run can fail on a
    # slow machine and then the sweep reports it as noticed, so the sweep may list it as stale.
    "_run|And->Or|running.ryo is not None and running.services.ryo is not None|0",
}


@pytest.mark.slow
def test_every_change_of_the_league_command_is_noticed_or_harmless() -> None:
    from tests.mutation_sweep import survivors

    alive, total = survivors("hoshitori.league_cli", "tests.league_cli_checks")
    assert total > 40, f"only {total} places to change: the sweep is not looking at the module"
    unexplained, stale = set(alive) - set(CLI_EQUIVALENT), set(CLI_EQUIVALENT) - set(alive)
    assert not unexplained and stale <= GUARD, f"unexplained survivors: {sorted(unexplained)}; stale entries: {sorted(stale - GUARD)}"


def test_the_module_runs_as_a_program(tmp_path: Path) -> None:
    import subprocess
    import sys

    done = subprocess.run(
        [sys.executable, "-m", "hoshitori.league_cli", "status", str(tmp_path / "missing.json")],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        check=False,
    )
    assert (done.returncode, done.stdout, done.stderr) == (
        2,
        "",
        "hoshitori-league: the settings cannot be read: No such file or directory\n",
    )
