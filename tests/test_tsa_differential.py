"""The Python and Node token verifiers on the same tokens: every token every scenario of ``tsa_checks`` presents, including the
bit-flip sweep over the live responses. They must agree on acceptance and on what they extract; when both reject, the rule
family (the code) should agree too, and every disagreement is listed."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import types
from pathlib import Path
from typing import Any

import pytest

from hoshitori import tsa
from tests.tsa_checks import SCENARIOS

CLI = Path(__file__).resolve().parents[1] / "verifier-js" / "src" / "tsa_cli.mjs"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")


class Recorder(types.ModuleType):
    """The real module, except that every verify_token call is recorded with its inputs and Python's answer."""

    def __init__(self) -> None:
        super().__init__("recorder")
        self.calls: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(tsa, name)

    def verify_token(self, token: bytes, digest: bytes, policy: tsa.TokenPolicy, *, nonce: int | None = None) -> tsa.VerifiedToken:
        call: dict[str, Any] = {
            "token": base64.b64encode(token).decode(),
            "digest": digest.hex(),
            "pins": [
                {"sha256": p.sha256, "operator": p.operator, "der": None if p.der is None else base64.b64encode(p.der).decode()}
                for p in policy.pinned
            ],
            "nonce": None if nonce is None else str(nonce),
        }
        try:
            result = tsa.verify_token(token, digest, policy, nonce=nonce)
        except tsa.TokenError as exc:
            call["python"] = {"ok": False, "code": exc.code}
            self.calls.append(call)
            raise
        call["python"] = {
            "ok": True,
            "gen_ms": int(result.gen_time.timestamp() * 1000),
            "root": result.root_fingerprint,
            "signer": result.signer_fingerprint,
            "operator": result.operator,
            "serial": str(result.serial),
            "policy": result.policy_oid,
            "path": list(result.path_fingerprints),
        }
        self.calls.append(call)
        return result


def run_node(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = "".join(json.dumps({k: c[k] for k in ("token", "digest", "pins", "nonce")}) + "\n" for c in calls)
    done = subprocess.run([NODE or "node", str(CLI)], input=payload, capture_output=True, text=True, check=True, timeout=600)
    return [json.loads(line) for line in done.stdout.splitlines()]


def test_both_verifiers_agree_on_every_token_the_scenarios_present() -> None:
    recorder = Recorder()
    for name, scenario in SCENARIOS.items():
        before = len(recorder.calls)
        scenario(recorder)  # type: ignore[arg-type]
        for call in recorder.calls[before:]:
            call["scenario"] = name
    calls = recorder.calls
    assert len(calls) > 1000
    answers = run_node(calls)
    assert len(answers) == len(calls)
    disagreements: list[str] = []
    code_differences: list[str] = []
    for index, (call, node) in enumerate(zip(calls, answers, strict=True)):
        py = call["python"]
        if py["ok"] != node["ok"]:
            disagreements.append(f"#{index}: python {py} node {node}")
        elif py["ok"]:
            for key in ("gen_ms", "root", "signer", "operator", "serial", "policy", "path"):
                if py[key] != node[key]:
                    disagreements.append(f"#{index}: {key} differs: python {py[key]} node {node[key]}")
        elif py["code"] != node["code"] and call["scenario"] != "live_tokens_sweep":
            code_differences.append(f"#{index}: python {py['code']} node {node['code']} ({node['message']})")
    assert disagreements == []
    # In the bit-flip sweep both reject, but damage in a part only one of them reads can make them name different rules first.
    assert code_differences == [], f"{len(code_differences)} code differences, first: {code_differences[:5]}"
