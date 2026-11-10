from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "secret_scan.py"
spec = importlib.util.spec_from_file_location("secret_scan", SCRIPT)
assert spec is not None and spec.loader is not None
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


def test_clean_tree_passes(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "a.py").write_text("print('hello')\n")
    assert scan.main([str(tmp_path)]) == 0


def test_planted_fake_keys_fail_and_are_not_echoed(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    planted = {
        "ryo": "KEY = 'ryo_mcp_" + "A1b2C3d4E5f6G7h8I9j0" + "'",
        "nvidia": "k = 'nvapi-" + "abcdefghijklmnopqrstuvwxyz0123" + "'",
        "pem": "-----BEGIN " + "PRIVATE KEY-----",
        "bearer": "Authorization: Bearer " + "abcdefghijklmnopqrstuvwxyz012345",
    }
    for name, text in planted.items():
        (tmp_path / f"{name}.txt").write_text(text + "\n")
    assert scan.main([str(tmp_path)]) == 1
    out = capsys.readouterr()
    assert "ryo_mcp_A1b2" not in out.out + out.err  # the match itself is never printed
    assert out.out.count("possible secret") == len(planted)


def test_exact_env_value_is_caught_in_another_file(tmp_path: Path) -> None:
    env = tmp_path / "local.env"
    env.write_text("SOME_TOKEN=Zx9Qw8Er7Ty6Ui5Op4As3Df2\n# comment\nSHORT=abc\n")
    (tmp_path / "notes.md").write_text("the token is Zx9Qw8Er7Ty6Ui5Op4As3Df2 ok\n")
    (tmp_path / "ok.md").write_text("short=abc is fine\n")
    assert scan.main(["--env", str(env), str(tmp_path / "notes.md"), str(tmp_path / "ok.md")]) == 1
    assert scan.main(["--env", str(env), str(tmp_path / "ok.md")]) == 0


def test_env_files_themselves_are_skipped(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("RYO_MCP_KEY=ryo_mcp_" + "A1b2C3d4E5f6G7h8I9j0\n")
    assert scan.main([str(tmp_path)]) == 0


def test_non_secret_variables_in_the_env_file_are_not_treated_as_secrets(tmp_path: Path) -> None:
    env = tmp_path / "local.env"
    env.write_text("RYO_MCP_URL=https://example.invalid/api/mcp\nAPI_TOKEN=Qq1Ww2Ee3Rr4Tt5Yy6Uu7Ii8\n")
    (tmp_path / "doc.md").write_text("endpoint https://example.invalid/api/mcp is public\n")
    assert scan.main(["--env", str(env), str(tmp_path / "doc.md")]) == 0
