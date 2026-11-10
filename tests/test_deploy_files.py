"""The files of ``deploy/`` against the code they start: nothing deploys anything here.

The systemd units cannot be loaded on the machine that runs the suite (no systemd), so they are checked as text against the rules this
repository relies on, and their commands are run for real, with the host's paths moved into a temporary folder: the backup, the prune,
the health probe and the drill run against a real league folder exactly as the units would start them. ``systemd-analyze verify`` on the
target host is still to be run (docs/DEPLOYMENT.md).
"""

from __future__ import annotations

import base64
import importlib.metadata
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from hoshitori import backup, deployment, league_cli
from tests.host_probe_checks import League, committed, planned

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
UNITS = DEPLOY / "systemd"
SERVICES = ("hoshitori-league", "hoshitori-backup", "hoshitori-health", "hoshitori-restore-drill")
TIMERS = ("hoshitori-backup", "hoshitori-health", "hoshitori-restore-drill")

# The directives the units may use, by section, each read in the systemd manual pages named in docs/DEPLOYMENT.md (systemd.unit,
# systemd.service, systemd.exec, systemd.kill, systemd.timer). A directive outside this list was not read, so it is not used.
ALLOWED = {
    "Unit": {"Description", "Documentation", "Wants", "After"},
    "Service": {
        "Type", "User", "Group", "WorkingDirectory", "ExecStart", "ExecStartPost", "Restart", "RestartSec", "RestartPreventExitStatus",
        "TimeoutStopSec", "StateDirectory", "StateDirectoryMode", "UMask", "NoNewPrivileges", "ProtectSystem", "ProtectHome",
        "PrivateTmp", "PrivateDevices", "Nice", "IOSchedulingClass",
    },
    "Timer": {"OnCalendar", "Persistent", "AccuracySec"},
    "Install": {"WantedBy"},
}  # fmt: skip


def unit(name: str) -> dict[str, list[tuple[str, str]]]:
    """A unit file as its sections, each a list of (directive, value); comments and blank lines dropped, nothing merged."""
    sections: dict[str, list[tuple[str, str]]] = {}
    current = ""
    for line in (UNITS / name).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith(("#", ";")):
            continue
        heading = re.fullmatch(r"\[(\w+)\]", line)
        if heading:
            current = heading.group(1)
            assert current not in sections, f"{name}: section {current} twice"
            sections[current] = []
            continue
        key, sep, value = line.partition("=")
        assert sep and current, f"{name}: a line that is neither a section, a comment nor a directive: {line!r}"
        sections[current].append((key, value))
    return sections


def one(sections: dict[str, list[tuple[str, str]]], section: str, key: str) -> str:
    values = [v for k, v in sections.get(section, []) if k == key]
    assert len(values) == 1, f"{key} in [{section}]: {values}"
    return values[0]


def test_the_folder_holds_these_units_and_no_other() -> None:
    assert sorted(p.name for p in UNITS.iterdir()) == sorted([f"{s}.service" for s in SERVICES] + [f"{t}.timer" for t in TIMERS])


@pytest.mark.parametrize("name", [f"{s}.service" for s in SERVICES] + [f"{t}.timer" for t in TIMERS])
def test_every_directive_is_one_that_was_read(name: str) -> None:
    for section, items in unit(name).items():
        assert section in ALLOWED, f"{name}: section {section}"
        unknown = {key for key, _ in items} - ALLOWED[section]
        assert not unknown, f"{name} [{section}]: {sorted(unknown)}"


@pytest.mark.parametrize("name", SERVICES)
def test_every_service_runs_as_the_league_user_sandboxed(name: str) -> None:
    s = unit(f"{name}.service")
    assert (one(s, "Service", "User"), one(s, "Service", "Group")) == ("hoshitori", "hoshitori")
    for key, value in (("NoNewPrivileges", "yes"), ("ProtectSystem", "strict"), ("ProtectHome", "yes"), ("PrivateTmp", "yes")):
        assert one(s, "Service", key) == value, (name, key)
    assert one(s, "Service", "UMask") == "0077"


def test_only_the_units_that_write_have_a_state_directory() -> None:
    assert one(unit("hoshitori-league.service"), "Service", "StateDirectory") == "hoshitori"
    assert one(unit("hoshitori-backup.service"), "Service", "StateDirectory") == "hoshitori hoshitori/backups"
    assert one(unit("hoshitori-restore-drill.service"), "Service", "StateDirectory") == "hoshitori hoshitori/drill"
    assert "StateDirectory" not in {k for k, _ in unit("hoshitori-health.service")["Service"]}  # every path is read-only for the probe


def test_the_league_restarts_on_a_failed_run_and_never_on_settings_that_cannot_run(tmp_path: Path) -> None:
    s = unit("hoshitori-league.service")
    assert (one(s, "Service", "Type"), one(s, "Service", "Restart"), one(s, "Service", "RestartPreventExitStatus")) == (
        "exec",
        "on-failure",
        "2",
    )
    assert shlex.split(one(s, "Service", "ExecStart")) == [
        "/opt/hoshitori/venv/bin/hoshitori-league",
        "run",
        "/var/lib/hoshitori/league/settings.json",
    ]
    assert (
        league_cli.main(["run", str(tmp_path / "settings.json")]) == 2
    )  # settings that cannot be read: the status the unit does not restart on
    assert (one(s, "Unit", "Wants"), one(s, "Unit", "After")) == ("network-online.target", "network-online.target time-sync.target")
    assert one(s, "Install", "WantedBy") == "multi-user.target"


@pytest.mark.parametrize(
    ("name", "calendar", "persistent"),
    [
        ("hoshitori-backup", "*-*-* *:30:00", "true"),
        ("hoshitori-health", "minutely", None),
        ("hoshitori-restore-drill", "Sun *-*-* 03:30:00", "true"),
    ],
)
def test_each_timer_starts_its_service(name: str, calendar: str, persistent: str | None) -> None:
    t = unit(f"{name}.timer")
    assert one(t, "Timer", "OnCalendar") == calendar
    assert dict(t["Timer"]).get("Persistent") == persistent
    assert one(t, "Install", "WantedBy") == "timers.target"
    assert one(unit(f"{name}.service"), "Service", "Type") == "oneshot"  # a timer starts the service of its own name (systemd.timer)


def _host(tmp_path: Path) -> dict[str, str]:
    """The host's paths, moved into ``tmp_path``."""
    return {
        "/opt/hoshitori/venv/bin/python": sys.executable,
        "/var/lib/hoshitori": str(tmp_path / "var-lib-hoshitori"),
        "/etc/hoshitori": str(tmp_path / "etc-hoshitori"),
    }


def _command(line: str, host: dict[str, str]) -> list[str]:
    words = shlex.split(line)
    return [next((word.replace(a, b) for a, b in host.items() if word.startswith(a)), word) for word in words]


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp"), "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(argv, capture_output=True, text=True, env=env, cwd=ROOT, check=False, timeout=300)


def test_the_commands_of_the_backup_health_and_drill_units_run_as_written(tmp_path: Path) -> None:
    host = _host(tmp_path)
    league = League(Path(host["/var/lib/hoshitori"]) / "league", [committed(4, 2), planned(5)], checkpoint_after=8)
    try:
        key = Path(host["/etc/hoshitori"]) / "log.key"
        key.parent.mkdir(parents=True)
        descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(base64.b64encode((7).to_bytes(32, "big")) + b"\n")  # the test ledger's key (tests.backup_checks.SIGNER)
        b = unit("hoshitori-backup.service")
        took = _run(_command(one(b, "Service", "ExecStart"), host))
        assert took.returncode == 0, took.stderr
        assert json.loads(took.stdout)["records"] == 9
        pruned = _run(_command(one(b, "Service", "ExecStartPost"), host))
        assert (pruned.returncode, json.loads(pruned.stdout)) == (0, {"removed": []}), pruned.stderr
        probed = _run(_command(one(unit("hoshitori-health.service"), "Service", "ExecStart"), host))
        shown = json.loads(probed.stdout)
        assert [f["name"] for f in shown["findings"]] == [
            "ledger_state",
            "segments",
            "heartbeat",
            "checkpoint_lag",
            "wal_bytes",
            "free_bytes",
            "backup",
        ]
        assert {f["name"]: f["ok"] for f in shown["findings"]}["backup"], shown
        assert probed.returncode == (0 if shown["ok"] else 1)
        drill_dir = Path(host["/var/lib/hoshitori"]) / "drill"
        drill_dir.mkdir()
        drilled = _run(_command(one(unit("hoshitori-restore-drill.service"), "Service", "ExecStart"), host))
        assert drilled.returncode == 0, drilled.stderr
        assert (json.loads(drilled.stdout)["records"], json.loads(drilled.stdout)["anchors"]) == (9, 0)
        assert list(drill_dir.iterdir()) == []
    finally:
        league.close()


def test_the_settings_template_is_a_settings_file_a_backup_can_keep(tmp_path: Path) -> None:
    text = (DEPLOY / "settings.template.json").read_text(encoding="utf-8")
    raw = json.loads(text)
    assert sorted(raw) == sorted(deployment.MEMBERS)
    settings = deployment.parse_settings(text, tmp_path)
    assert settings.log_key == Path("/etc/hoshitori/log.key")  # outside the league folder: never in a backup
    assert {spec.venue for spec in settings.lists} <= set(settings.venues)
    assert len(settings.authorities) >= 2 and settings.models is None
    assert [str(p.relative_to(tmp_path)) for p in (settings.protocol, settings.trusted_root, *settings.mappings.values())] == [
        "protocol.json",
        "root.json",
        "mapping.json",
    ]
    assert backup._named(settings, tmp_path)  # every file it names is inside the folder, as a backup requires


def test_the_lock_pins_the_runtime_dependencies_at_the_versions_the_suite_ran_with() -> None:
    text = (DEPLOY / "requirements.lock").read_text(encoding="utf-8")
    pins = dict(re.findall(r"^([a-z0-9-]+)==([^ \\]+) \\$", text, flags=re.MULTILINE))
    assert sorted(pins) == ["asn1crypto", "cffi", "cryptography", "pycparser"]
    for name, version in pins.items():
        assert importlib.metadata.version(name) == version, name
    blocks = re.split(r"^(?=[a-z0-9-]+==)", text, flags=re.MULTILINE)[1:]
    assert all(re.search(r"--hash=sha256:[0-9a-f]{64}", block) for block in blocks)
    assert "/Users/" not in text and "/home/" not in text  # no path of the machine it was made on
