"""What the published documents must not hold: the path of the folder they were made in, the owner's home folder, and the log signing key.

The site and every bundle are public. A league is made in a folder of the operator's machine and signs with a private key; neither may reach
the documents (the privacy specification, section 1: only public records leave the machine). The test makes a real export from a loopback
league and searches every byte of every file for those strings; a planted leak shows that the search sees one.
"""

from __future__ import annotations

from pathlib import Path

from hoshitori import deployment
from tests.deployment_checks import world_files
from tests.e2e_league import OffsetClock, outside


def leaks(folder: Path, secrets: list[str]) -> list[str]:
    """The files of ``folder`` that hold any of ``secrets``, by relative name."""
    found: list[str] = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        data = path.read_bytes()
        for secret in secrets:
            if secret and secret.encode() in data:
                found.append(f"{path.relative_to(folder)}: {secret[:24]}")
    return found


def test_the_search_sees_a_planted_leak(tmp_path: Path) -> None:
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "clean.json").write_text('{"a": 1}')
    (folder / "bad.json").write_text('{"path": "/some/where/league"}')
    assert leaks(folder, ["/some/where/league", ""]) == ["bad.json: /some/where/league"]
    assert leaks(folder, ["not there"]) == []


def test_an_export_holds_no_path_and_no_key(tmp_path: Path) -> None:
    with outside(OffsetClock()) as world:
        directory = tmp_path / "league"
        directory.mkdir()
        settings = world_files(directory, world)
        key_text = (directory / "log.key").read_text(encoding="utf-8").strip()
        assert len(key_text) >= 32, "the key file is not what this test thinks it is"
        with deployment.assemble(deployment.load_settings(settings), clock=OffsetClock()) as running:
            from hoshitori import scheduler

            assert scheduler.tick(running.services, running.config).ran is not None
            out = tmp_path / "site"
            deployment.export_site(running, out)
    files = [p for p in out.rglob("*") if p.is_file()]
    assert any(p.parent.name == "items" for p in files), "the export holds no bundle: the search looked at too little"
    secrets = [str(tmp_path), str(directory), str(Path.home()), key_text]
    assert leaks(out, secrets) == []
