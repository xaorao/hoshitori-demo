"""The two command-line verifiers: same arguments, same verdict, same exit status."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from hoshitori.cli import main
from tests import bundle_support as B
from tests import root_support as R

NODE = shutil.which("node")
BIN = Path(__file__).resolve().parents[1] / "verifier-js" / "bin" / "verify_bundle.mjs"


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> B.World:
    return B.make_world(tmp_path_factory.mktemp("cli"))


def run_python(*args: str) -> tuple[int, dict[str, Any] | None, str]:
    done = subprocess.run([sys.executable, "-m", "hoshitori.cli", *args], capture_output=True, text=True, check=False)
    return done.returncode, json.loads(done.stdout) if done.stdout else None, done.stderr


def run_node(*args: str) -> tuple[int, dict[str, Any] | None, str]:
    done = subprocess.run([NODE or "node", str(BIN), *args], capture_output=True, text=True, check=False)
    return done.returncode, json.loads(done.stdout) if done.stdout else None, done.stderr


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k not in ("verifier", "detail")}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


@pytest.fixture(scope="module")
def root_file(world: B.World, tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("rootfile") / "root.json"
    path.write_bytes(world.root.payload)
    return path


NOW = "2026-10-29T12:30:00Z"


def both(args: list[str]) -> tuple[tuple[int, dict[str, Any] | None, str], tuple[int, dict[str, Any] | None, str]]:
    return run_python(*args), run_node(*args)


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_a_bundle_against_the_trusted_root(world: B.World, root_file: Path) -> None:
    python, node = both([str(world.bundle_dir), "--trusted-root", str(root_file), "--now", NOW])
    assert python[0] == node[0] == 0
    assert python[1] is not None and node[1] is not None
    assert python[1]["root_of_trust"]["state"] == "PINNED" and python[1]["timing"]["level"] == "C3" and python[1]["eligibility"]["scorable"]
    assert python[1]["root_update"] == {"status": "CURRENT", "code": "OK", "version": 1, "single_channel": False}
    assert scrub(python[1]) == scrub(node[1])


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_an_expired_root_is_never_scorable(world: B.World, root_file: Path) -> None:
    python, node = both([str(world.bundle_dir), "--trusted-root", str(root_file), "--now", "2028-01-01T00:00:00Z"])
    assert python[0] == node[0] == 0
    assert python[1] is not None and node[1] is not None
    assert python[1]["root_of_trust"]["state"] == "EXPIRED" and not python[1]["eligibility"]["scorable"]
    assert scrub(python[1]) == scrub(node[1])


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_without_a_root_the_bundle_only_asserts_itself(world: B.World) -> None:
    python, node = both([str(world.bundle_dir)])
    assert python[0] == node[0] == 1
    assert python[1] is not None and node[1] is not None
    assert python[1]["root_of_trust"]["state"] == "BUNDLE_ASSERTED" and python[1]["timing"]["level"] == "none"
    assert scrub(python[1]) == scrub(node[1])


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_a_newer_root_from_a_channel_is_followed(world: B.World, root_file: Path, tmp_path: Path) -> None:
    document = json.loads(world.root.payload)  # version 2 of the same root (the pinned roots stay, so the bundle still verifies)
    document["version"] = 2
    served = json.dumps({"2": R.envelope(document, [R.new_signer(1)])})
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    first.write_text(served)
    second.write_text(served)
    python, node = both(
        [str(world.bundle_dir), "--trusted-root", str(root_file), "--channel", str(first), "--channel", str(second), "--now", NOW]
    )
    assert python[0] == node[0] == 0
    assert python[1] is not None and node[1] is not None
    assert python[1]["root_update"]["version"] == 2 and python[1]["root_of_trust"]["version"] == 2
    assert python[1]["root_update"]["single_channel"] is False
    assert scrub(python[1]) == scrub(node[1])


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_a_newer_root_from_one_channel_is_not_followed_and_says_so(world: B.World, root_file: Path, tmp_path: Path) -> None:
    document = json.loads(world.root.payload)
    document["version"] = 2
    channel = tmp_path / "channel.json"
    channel.write_text(json.dumps({"2": R.envelope(document, [R.new_signer(1)])}))
    python, node = both([str(world.bundle_dir), "--trusted-root", str(root_file), "--channel", str(channel), "--now", NOW])
    assert python[0] == node[0]
    assert python[1] is not None and node[1] is not None
    assert python[1]["root_update"]["version"] == 1 and python[1]["root_update"]["single_channel"] is True
    assert scrub(python[1]) == scrub(node[1])


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_a_forked_update_leaves_the_bundle_unauthenticated(world: B.World, root_file: Path, tmp_path: Path) -> None:
    document = json.loads(world.root.payload)
    document["version"] = 2
    other = dict(document, expires="2027-12-31T00:00:00Z")
    one, two = tmp_path / "one.json", tmp_path / "two.json"
    one.write_text(json.dumps({"2": R.envelope(document, [R.new_signer(1)])}))
    two.write_text(json.dumps({"2": R.envelope(other, [R.new_signer(1)])}))
    python, node = both(
        [str(world.bundle_dir), "--trusted-root", str(root_file), "--channel", str(one), "--channel", str(two), "--now", NOW]
    )
    assert python[0] == node[0] == 1
    assert python[1] is not None and node[1] is not None
    assert python[1]["root_update"]["status"] == "FORK" and python[1]["root_of_trust"]["state"] == "BUNDLE_ASSERTED"
    assert scrub(python[1]) == scrub(node[1])


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_usage_errors_exit_with_status_two(world: B.World, root_file: Path, tmp_path: Path) -> None:
    for args in (
        [],
        [str(world.bundle_dir), "--now", "yesterday"],
        [str(world.bundle_dir), "--now", "2026-10-29T12:00:00"],
        [str(world.bundle_dir), "--trusted-root", str(tmp_path / "missing.json")],
        [str(world.bundle_dir), "--channel", str(root_file)],
        [str(world.bundle_dir), "--bogus"],
    ):
        python, node = both(args)
        assert python[0] == 2, (args, python)
        assert node[0] == 2, (args, node)


def test_main_returns_the_status_without_a_subprocess(world: B.World, root_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(world.bundle_dir), "--trusted-root", str(root_file), "--now", NOW]) == 0
    assert json.loads(capsys.readouterr().out)["integrity"]["ok"] is True
    assert main([str(world.bundle_dir), "--now", "nonsense"]) == 2


OLD_RUNTIME = "Unicode 15.0 or later is needed to check NFC; this runtime has an older version"


def test_a_runtime_older_than_unicode_15_gives_no_verdict(
    world: B.World, root_file: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from hoshitori import canonical

    monkeypatch.setattr(canonical, "RUNTIME_UNICODE", "14.0.0")
    for args in ([str(world.bundle_dir), "--trusted-root", str(root_file), "--now", NOW], [str(world.bundle_dir)]):
        assert main(args) == 2
        captured = capsys.readouterr()
        assert (captured.out, captured.err) == ("", f"hoshitori-verify: {OLD_RUNTIME}\n")


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_the_node_verifier_on_a_runtime_older_than_unicode_15_gives_no_verdict(world: B.World, root_file: Path, tmp_path: Path) -> None:
    # the runtime's answer is fixed when canonical.mjs loads; a module loaded first sets it to "older than 15.0"
    preload = tmp_path / "old_unicode.mjs"
    preload.write_text(
        f'import {{ runtime }} from "{(BIN.parents[1] / "src" / "canonical.mjs").as_uri()}";\nruntime.unicodeAtLeast15 = false;\n'
    )
    for args in ([str(world.bundle_dir), "--trusted-root", str(root_file), "--now", NOW], [str(world.bundle_dir)]):
        done = subprocess.run([NODE or "node", "--import", preload.as_uri(), str(BIN), *args], capture_output=True, text=True, check=False)
        assert (done.returncode, done.stdout, done.stderr) == (2, "", f"hoshitori-verify: {OLD_RUNTIME}\n")
