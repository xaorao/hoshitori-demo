"""The restore drill, in the suite: back up while the writer runs, destroy the primary, restore, and prove it with the product's own
recovery and verifiers.

Two writers. A whole league on loopback (``tests.e2e_league``: two fake exchanges and three fake time-stamp authorities through sockets;
the real gateway, ledger, scheduler, commitment and bundle) commits cycles in a thread while ``python -m hoshitori.backup take`` runs
again and again in another process. And ``tests/kill_child.py`` appends to a ledger without pause in another process while this one backs
it up, until it is killed. After each, the league folder is removed, a backup is restored into its place with the log key held apart and
an anchor kept "off the host", and the restored ledger must hold every record and checkpoint that existed when the backup began, open
cleanly through ``deployment.assemble`` (the ledger's recovery), give bundles the offline verifiers accept at C3, equal an index rebuilt
from its segments, and go on: the next cycle commits and its checkpoint is consistent with the anchor. A torn backup, a backup missing a
segment and a backup of another log are refused with their exact sentences.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from hoshitori import backup, canonical, cycle, deployment, merkle, scheduler, verify
from hoshitori.checkpoint import parse_text
from hoshitori.clock import ManualClock, SystemClock
from hoshitori.ledger import Ledger, read_log
from hoshitori.note import Signer, split_note
from tests import root_support as R
from tests.backup_checks import FIXTURE, Folder
from tests.deployment_checks import good_settings, world_files
from tests.e2e_league import OffsetClock, outside
from tests.ledger_checks import invariants

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
BIN = ROOT / "verifier-js" / "bin" / "verify_bundle.mjs"
CHILD = ROOT / "tests" / "kill_child.py"
CHILD_ORIGIN = "example.org/hoshitori-test-ledger"  # kill_child's log and key
CHILD_SIGNER = Signer.from_seed(CHILD_ORIGIN, bytes(range(32)))
HOUR_S = 3600


def _env() -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp"), "PYTHONPATH": str(ROOT / "src")}


def _backup_cli(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "hoshitori.backup", *argv], capture_output=True, text=True, env=_env(), cwd=ROOT, check=False, timeout=300
    )


def _complete_lines(path: Path) -> list[bytes]:
    data = path.read_bytes() if path.exists() else b""
    return data[: data.rfind(b"\n") + 1].splitlines()


def _note(line: bytes) -> bytes:
    return base64.b64decode(str(canonical.parse_record(line)["note"]))


def _size_of(note: bytes) -> int:
    return parse_text(split_note(note)[0]).size


def _rows(ledger_dir: Path) -> list[tuple[Any, ...]]:
    db = sqlite3.connect(f"file:{ledger_dir / 'index.db'}?mode=ro", uri=True)
    try:
        return db.execute("SELECT seq, source, rec_id, leaf, segment, off, len FROM ledger ORDER BY seq").fetchall()
    finally:
        db.close()


def _rebuilt_rows(ledger_dir: Path, scratch: Path, signer: Signer, origin: str) -> list[tuple[Any, ...]]:
    """The rows of an index rebuilt from the segments alone, by the ledger's own recovery, in a copy."""
    shutil.copytree(ledger_dir, scratch, ignore=shutil.ignore_patterns("index.db*", "LOCK"))
    Ledger.open(scratch, signer=signer, origin=origin, clock=SystemClock()).close(checkpoint=False)
    return _rows(scratch)


def _committed(records: list[dict[str, Any]]) -> list[str]:
    histories = cycle.rebuild([r for r in records if r.get("type") == cycle.TYPE])
    return sorted(c for c, h in histories.items() if cycle.current_state(h) is cycle.State.COMMITTED)


def test_a_league_backed_up_while_it_runs_is_restored_after_its_folder_is_destroyed(tmp_path: Path) -> None:
    clock = OffsetClock()
    offhost, escrow = tmp_path / "offhost", tmp_path / "escrow"
    offhost.mkdir()
    escrow.mkdir()
    with outside(clock) as world:
        (tmp_path / "w").mkdir()
        settings_file = world_files(tmp_path / "w", world)
        league = tmp_path / "w"
        shutil.copyfile(league / "root.json", offhost / "root.json")  # the trusted root is published: a copy lives off the host
        shutil.copyfile(league / "log.key", escrow / "log.key")  # the log key is held apart, never in a backup
        os.chmod(escrow / "log.key", 0o600)
        settings = deployment.load_settings(settings_file)
        root = backup.trusted_root.parse_root((offhost / "root.json").read_bytes())
        ledger_dir = settings.directory / "ledger"
        taken: list[dict[str, Any]] = []
        with deployment.assemble(settings, clock=clock) as running:
            ticks: list[Any] = []
            failed: list[BaseException] = []

            enough = threading.Event()  # set once two backups have been taken while the writer ran, each with a checkpoint to anchor

            def write() -> None:
                try:
                    while len(ticks) < 3 or (not enough.is_set() and len(ticks) < 40):
                        ticks.append(scheduler.tick(running.services, running.config))
                        clock.jump(HOUR_S)
                except BaseException as exc:  # reported by the test below
                    failed.append(exc)

            writer = threading.Thread(target=write, name="league-writer")
            writer.start()
            while writer.is_alive() or len(taken) < 2:
                before = {
                    "alive": writer.is_alive(),
                    "size": running.ledger.size,
                    "checkpoints": _complete_lines(ledger_dir / "checkpoints.jsonl"),
                    # a save that is not yet renamed into place ("<id>.new") is not a commitment, and a backup leaves it out on purpose
                    "commitments": sorted(
                        p.name
                        for p in (settings.directory / "commitments").glob("*")
                        if p.is_dir() and backup.COMMITMENT_ID.fullmatch(p.name) is not None
                    ),
                }
                done = _backup_cli("take", str(settings_file), str(tmp_path / "backups"))
                assert done.returncode == 0, done.stderr
                taken.append({**before, "during": before["alive"] and writer.is_alive(), "shown": json.loads(done.stdout)})
                if sum(1 for t in taken if t["during"] and t["checkpoints"]) >= 2:
                    enough.set()
                time.sleep(0.002)  # two backups never share a millisecond, which names them
            writer.join()
            assert failed == [], failed
            ran = [t.ran.state.value for t in ticks if t.ran is not None]
            assert len(ran) >= 3 and set(ran) == {"COMMITTED"}, ran
            final = scheduler.read_ledger(running.ledger)
            final_size, tree = running.ledger.size, merkle.MerkleTree(running.ledger.leaf_hash_at(i) for i in range(running.ledger.size))

        during = [t for t in taken if t["during"]]
        assert during, "no backup was taken while the writer ran"
        for t in taken:  # each backup is a prefix of the log as it ended, and holds what existed when it began
            shown = t["shown"]
            assert t["size"] <= shown["records"] <= final_size
            assert tree.root(shown["records"]).hex() == shown["root"]
            assert shown["checkpoints"] >= len(t["checkpoints"])
            assert set(t["commitments"]) <= set(shown["commitments"])
        chosen = next(t for t in reversed(during) if t["checkpoints"])
        anchor = offhost / "anchor.note"
        anchor.write_bytes(
            _note(chosen["checkpoints"][-1])
        )  # the newest checkpoint signed before that backup began, published off the host
        backup_dir = Path(chosen["shown"]["path"])

        shutil.rmtree(league)  # the host's league folder is lost: settings, protocol, mapping, ledger, captures, commitments
        restored = _backup_cli(
            "restore",
            str(backup_dir),
            str(league),
            "--trusted-root",
            str(offhost / "root.json"),
            "--log-key",
            str(escrow / "log.key"),
            "--anchor",
            str(anchor),
        )
        assert restored.returncode == 0, restored.stderr
        report = json.loads(restored.stdout)
        assert (report["records"], report["root"], report["anchors"]) == (chosen["shown"]["records"], chosen["shown"]["root"], 1)
        assert report["log_key"] == str(league / "log.key")
        shutil.copyfile(escrow / "log.key", league / "log.key")
        os.chmod(league / "log.key", 0o600)

        with deployment.assemble(deployment.load_settings(settings_file), clock=clock) as again:
            assert deployment.ledger_state(again) == {"state": "OK", "detail": ""}
            n = chosen["shown"]["records"]
            assert (again.ledger.size, again.ledger.root().hex()) == (n, tree.root(n).hex())  # every record the backup began with, and on
            signed = {_note(line) for line in _complete_lines(ledger_dir / "checkpoints.jsonl")}
            assert {_note(line) for line in chosen["checkpoints"]} <= signed  # and every checkpoint
            records = scheduler.read_ledger(again.ledger)
            assert records == final[:n]
            assert _rebuilt_rows(ledger_dir, tmp_path / "rebuilt", again.ledger.signer, root.origin) == _rows(ledger_dir)
            exported = 0
            for cycle_id in _committed(records):
                out = tmp_path / "bundles" / cycle_id
                try:
                    deployment.export_cycle(again, cycle_id, out)
                except deployment.DeploymentError as exc:
                    # a cycle committed in the ledger copy whose commitment was saved after the commitments were copied
                    assert str(exc).startswith("the cycle's commitment is not kept"), exc
                    continue
                verdict = verify.verify_bundle(out, root)
                assert verdict["integrity"]["ok"] and verdict["timing"]["level"] == "C3", verdict["timing"]
                if NODE is not None:
                    node = subprocess.run(
                        [NODE, str(BIN), str(out), "--trusted-root", str(offhost / "root.json")],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    assert node.returncode == 0, node.stderr
                    assert json.loads(node.stdout)["timing"]["level"] == "C3"
                exported += 1
            assert exported >= len(chosen["commitments"]) >= 1
            print(  # the drill's numbers, for the record (pytest -s shows them)
                f"drill: {len(ticks)} ticks, {len(taken)} backups, {len(during)} taken while the writer ran; restored "
                f"{n} of {final_size} records, {len(signed)} checkpoints, {exported} bundles verified at C3"
            )
            clock.jump(HOUR_S)
            nxt = scheduler.tick(again.services, again.config)
            assert nxt.ran is not None and nxt.ran.state.value == "COMMITTED" and nxt.ran.commitment is not None
            assert nxt.ran.commitment.level == "C3"
            head = nxt.ran.commitment.size
            old = _size_of(anchor.read_bytes())
            anchored = parse_text(split_note(anchor.read_bytes())[0])
            assert merkle.verify_consistency(old, head, again.ledger.consistency_proof(old, head), anchored.root, again.ledger.root(head))
            assert len(read_log(ledger_dir)) == again.ledger.size  # the chain of segments goes on, clean
            status = deployment.status(again)
            assert (
                status["ledger"] == {"state": "OK", "detail": ""} and status["cycles"].get("COMMITTED", 0) >= len(_committed(records)) + 1
            )

        # the refusals, on copies of the backup the drill used
        seg0 = "run/ledger/segments/segment-000000000000.jsonl"
        torn = tmp_path / "torn"
        shutil.copytree(backup_dir, torn)
        whole = (torn / seg0).read_bytes()
        (torn / seg0).write_bytes(whole[:-7])
        said = _backup_cli("verify", str(torn), "--trusted-root", str(offhost / "root.json"))
        assert (said.returncode, said.stderr) == (
            1,
            f"hoshitori-backup: {seg0} is not the file the manifest lists: {len(whole) - 7} bytes with SHA-256 "
            f"{hashlib.sha256(whole[:-7]).hexdigest()}, the manifest says {len(whole)} bytes with {hashlib.sha256(whole).hexdigest()}\n",
        )
        missing = tmp_path / "missing"
        shutil.copytree(backup_dir, missing)
        (missing / seg0).unlink()
        said = _backup_cli("verify", str(missing), "--trusted-root", str(offhost / "root.json"))
        assert (said.returncode, said.stderr) == (1, f"hoshitori-backup: the backup lacks {seg0}, which its manifest lists\n")
        other = Folder(tmp_path / "other" / "league")  # another log: its own key and trusted root
        other.close()
        foreign = backup.take(other.settings, tmp_path / "other" / "backups", clock=ManualClock(R.NOW)).path
        said = _backup_cli(
            "restore",
            str(foreign),
            str(tmp_path / "x"),
            "--trusted-root",
            str(offhost / "root.json"),
            "--log-key",
            str(escrow / "log.key"),
            "--anchor",
            str(anchor),
        )
        other_root = hashlib.sha256((other.base / "root.json").read_bytes()).hexdigest()
        assert (said.returncode, said.stderr) == (
            1,
            f"hoshitori-backup: the backup was taken under another trusted root (SHA-256 {other_root}), not the one given ({root.sha256})\n",
        )
        assert not (tmp_path / "x").exists()


def _child_root(tmp: Path) -> bytes:
    document = R.make_document(1, eras=[{"vkey": CHILD_SIGNER.verifier.vkey(), "from_size": 0, "through_size": None}])
    log = document["log"]
    assert isinstance(log, dict)
    log["origin"] = CHILD_ORIGIN
    return canonical.dumps(document)  # type: ignore[arg-type]


def test_a_backup_taken_while_another_process_writes_without_pause_keeps_every_acknowledged_command(tmp_path: Path) -> None:
    league = tmp_path / "league"
    league.mkdir()
    settings_file = league / "settings.json"
    settings_file.write_text(json.dumps({**good_settings(), "directory": "run"}))
    for name in ("protocol.json", "mapping.json"):
        (league / name).write_bytes(FIXTURE)  # a backup copies these and never reads them
    (league / "root.json").write_bytes(_child_root(tmp_path))
    root = backup.trusted_root.parse_root((league / "root.json").read_bytes())
    ledger_dir = league / "run" / "ledger"
    child = subprocess.Popen(
        [sys.executable, "-I", str(CHILD), str(ledger_dir), "50", "forever"],
        stdout=subprocess.PIPE,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    acked: list[str] = []
    lock = threading.Lock()

    def read() -> None:
        assert child.stdout is not None
        for line in child.stdout:
            if line.startswith("ACK "):
                with lock:
                    acked.append(line.split()[1])

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            with lock:
                if len(acked) >= 60 and _complete_lines(ledger_dir / "checkpoints.jsonl"):
                    break
            time.sleep(0.01)
        rounds: list[tuple[list[str], bytes, backup.Verified]] = []
        for _ in range(3):
            with lock:
                before = list(acked)
            anchor = _note(_complete_lines(ledger_dir / "checkpoints.jsonl")[-1])
            rounds.append((before, anchor, backup.take(settings_file, tmp_path / "backups", clock=SystemClock())))
            time.sleep(0.05)
        assert child.poll() is None, "the writer stopped by itself"
    finally:
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=60)
    reader.join(timeout=10)
    assert all(len(before) >= 60 for before, _, _ in rounds)
    sizes = [t.manifest.records for _, _, t in rounds]
    assert sizes == sorted(sizes) and sizes[0] > 0
    shutil.rmtree(league / "run")  # the primary is lost with the writer
    for number, (before, anchor, taken) in enumerate(rounds):
        target = tmp_path / f"restored-{number}"
        done = backup.restore(taken.path, target, root=root, seed=bytes(range(32)), anchors=[anchor], unanchored=False, clock=SystemClock())
        assert done.records == taken.manifest.records and done.anchors == 1
        led = Ledger.open(target / "run" / "ledger", signer=CHILD_SIGNER, origin=CHILD_ORIGIN, clock=SystemClock(), segment_records=50)
        try:
            assert led.state == "OK", led.state_detail
            led.append_pending()
            ids = [str(canonical.parse_record(line.record)["id"]) for line in read_log(target / "run" / "ledger")]
            assert set(before) <= set(ids), f"backup {number}: {sorted(set(before) - set(ids))[:5]} acknowledged before it began and lost"
            assert len(ids) == len(set(ids))
            assert invariants(target / "run" / "ledger", led) == []
        finally:
            led.abandon()
