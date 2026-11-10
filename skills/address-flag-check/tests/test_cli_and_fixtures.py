"""The command line and the stdio MCP server as real subprocesses (offline: only the OFAC check, on a local copy made from the recorded
extract, with refreshing turned off), and the labels of every fixture."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import subprocess
import sys

import pytest

from address_flag_check import list_store, ofac_index
from tests.checks_support import FIXTURES, extract_xml, ofac_meta

ADDRESS = "3LtcaPbCj87CwJHnRX3vh7c2y9RZQqeSy8"


@pytest.fixture
def list_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    index = ofac_index.parse_xml(extract_xml())
    stamp = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    list_store.save(tmp_path, list_store.StoredList(index, index.xml_sha256, index.xml_bytes, "recorded", '"recorded"', stamp, stamp))
    return tmp_path


def cli(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "address_flag_check", *args], input=stdin, capture_output=True, text=True, timeout=60)


def test_tool_and_example() -> None:
    tools = json.loads(cli("tool").stdout)
    assert [t["name"] for t in tools] == ["address_flag_check", "address_flag_sources"]
    assert json.loads(cli("example").stdout) == {"address": "0x000000000000000000000000000000000000dEaD", "chain": "ethereum"}


def test_invalid_input_exits_2_with_the_exact_message(list_dir: pathlib.Path) -> None:
    p = cli("--list-dir", str(list_dir), "check", "--address", "0x123", "--chain", "ethereum", "--no-refresh")
    assert p.returncode == 2 and p.stdout == ""
    body = json.loads(p.stderr)
    assert body["code"] == "INVALID_ARGUMENTS" and body["message"] == "address: is not an EVM address (0x followed by 40 hex characters)"


def test_offline_check_from_the_local_copy(list_dir: pathlib.Path) -> None:
    p = cli("--list-dir", str(list_dir), "check", "--address", ADDRESS, "--chain", "bitcoin", "--checks", "ofac_sdn", "--no-refresh")
    assert p.returncode == 0, p.stderr
    env = json.loads(p.stdout)
    assert env["data"]["verdict"] == "flagged" and env["status"] == "ok"
    assert env["data_mode"] == "mixed"  # the real transport, and the list came from the local copy
    assert env["data"]["source_calls"] == [] and env["data"]["sent_to"] == []
    assert (
        env["summary"]["headline"]
        == "3LtcaP…eSy8 on bitcoin: flagged by 1 of 1 sources that answered (OFAC SDN list). Not covered on bitcoin: USDT freeze, USDC freeze."
    )
    assert env["warnings"][-1] == "The OFAC list came from the local copy confirmed current 2 hours ago."


def test_sources_command(list_dir: pathlib.Path) -> None:
    env = json.loads(cli("--list-dir", str(list_dir), "sources", "--chain", "tron").stdout)
    assert env["tool"] == "address_flag_sources" and env["status"] == "ok"
    assert [s["source_id"] for s in env["data"]["sources"] if s["used"]] == ["ofac_sdn", "usdt_freeze_tron"]


def test_stdio_subprocess_speaks_both_protocol_eras(list_dir: pathlib.Path) -> None:
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "server/discover"},
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "address_flag_check", "arguments": {"address": ADDRESS, "chain": "bitcoin", "checks": ["ofac_sdn"]}},
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "address_flag_check",
                "arguments": {"address": ADDRESS, "chain": "bitcoin", "checks": ["ofac_sdn"]},
                "_meta": meta,
            },
        },
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "address_flag_check", "arguments": {"chain": "bitcoin"}}},
    ]
    p = cli("--list-dir", str(list_dir), "mcp", "--no-refresh", stdin="\n".join(json.dumps(x) for x in lines) + "\n")
    replies = {r["id"]: r for r in map(json.loads, p.stdout.splitlines())}
    assert p.returncode == 0 and sorted(replies) == [1, 2, 3, 4, 5, 6]  # the notification produced no reply
    assert replies[1]["result"]["protocolVersion"] == "2024-11-05"
    assert [t["name"] for t in replies[2]["result"]["tools"]] == ["address_flag_check", "address_flag_sources"]
    assert replies[3]["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "address-flag-check"
    assert replies[4]["result"]["structuredContent"]["data"]["verdict"] == "flagged" and "resultType" not in replies[4]["result"]
    assert replies[5]["result"]["resultType"] == "complete" and replies[5]["result"]["isError"] is False
    assert (
        replies[6]["result"]["isError"] is True
        and json.loads(replies[6]["result"]["content"][0]["text"])["message"] == "address: is required"
    )


def test_every_recorded_fixture_is_labelled_with_its_source() -> None:
    files = sorted((FIXTURES / "rpc").rglob("*.json"))
    assert len(files) == 54
    for path in files:
        rec = json.loads(path.read_text())
        assert rec["label"] == f"recorded by B5A from {rec['source_url']}", path
        assert rec["source_url"].startswith("https://"), path
        assert hashlib.sha256(rec["body"].encode()).hexdigest() == rec["body_sha256"], path
    meta = ofac_meta()
    assert meta["label"] == f"recorded by B5A from {meta['source_url']}"
    assert hashlib.sha256(extract_xml()).hexdigest() == meta["extract"]["sha256"]
    assert b"SYNTHETIC" in (FIXTURES / "synthetic" / "sdn_synthetic.xml").read_bytes()[:400]
    assert "recorded by B5A" in (FIXTURES / "README.md").read_text()


def test_update_list_says_how_to_fix_a_certificate_the_machine_cannot_check(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from address_flag_check import __main__ as cli_main
    from address_flag_check.transport import CertificateError

    class Untrusted:
        def __call__(self, *args: object, **kwargs: object) -> object:
            raise CertificateError("self-signed")

    original = cli_main.default_transport
    cli_main.default_transport = Untrusted  # type: ignore[assignment]  # a source that fails even after the macOS retry
    try:
        code = cli_main.main(["--list-dir", str(tmp_path), "update-list"])
    finally:
        cli_main.default_transport = original
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["error"] == "tls_error"
    assert "SSL_CERT_FILE" in out["hint"] and "SystemRootCertificates.keychain" in out["hint"]
    assert "another trust store" in out["hint"]
