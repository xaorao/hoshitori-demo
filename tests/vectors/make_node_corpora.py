"""Write the case files the Node tests replay without Python (verifier-js/test/vectors/):

* root_cases.json.gz    trusted-root documents, damaged documents and update chains with the Python answers;
* proof_cases.json.gz   tlog-proof texts with the Python answers;
* timing_cases.json.gz  time-stamp counting inputs and the Python assessments;
* bundle_cases.json.gz  bundle directories (as base64 file maps), the trusted root and status, and the Python verdicts.

Run from the repository root: python tests/vectors/make_node_corpora.py
"""

from __future__ import annotations

import base64
import gzip
import json
import random
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests import proof_checks, timing_checks  # noqa: E402
from tests import test_trusted_root_differential as TRD  # noqa: E402
from tests.root_checks import SCENARIOS as ROOT_SCENARIOS  # noqa: E402
from tests.test_verify_differential import scrub  # noqa: E402
from tests.verify_checks import build_cases  # noqa: E402

from hoshitori import timing, tlog_proof, verify  # noqa: E402

OUT = ROOT / "verifier-js" / "test" / "vectors"


def root_cases() -> list[dict[str, Any]]:
    recorder = TRD.Recorder()
    for scenario in ROOT_SCENARIOS.values():
        scenario(recorder)  # type: ignore[arg-type]
    queries = list(recorder.queries)
    rng = random.Random(7)
    base = TRD.R.make_document(
        2, root_keys=[TRD.R.new_signer(1), TRD.R.new_signer(2)], threshold=2, witnesses=(1, [TRD.R.log_signer(9).verifier.vkey()])
    )
    base["log"]["eras"] = [  # type: ignore[index]
        {"vkey": TRD.R.log_signer(7).verifier.vkey(), "from_size": 0, "through_size": 9},
        {"vkey": TRD.R.log_signer(8).verifier.vkey(), "from_size": 10, "through_size": None},
    ]
    every_path = TRD.paths(base)

    def add(candidate: dict[str, Any]) -> None:
        try:
            payload = TRD.canonical.dumps(candidate)
        except TRD.canonical.CanonicalError:
            return
        query: dict[str, Any] = {"op": "parse", "payload": base64.b64encode(payload).decode()}
        try:
            query["python"] = {"ok": True, "facts": TRD.facts(TRD.trusted_root.parse_root(payload))}
        except TRD.trusted_root.RootError as exc:
            query["python"] = {"ok": False, "code": exc.code}
        queries.append(query)

    # three damaged documents for every path of the document, so that every field is damaged in several ways
    for path in every_path:
        for _ in range(3):
            candidate = TRD.edit(base, path, rng.choice(["replace", "replace", "delete", "add"]), rng.choice(TRD.ALTERNATIVES))
            if candidate is not None:
                add(candidate)
    # strings of astral characters right at the limits (a length counts characters, not UTF-16 code units)
    astral = chr(0x1F600)
    for path, limit in (
        (("log", "origin"), 256),
        (("time_stamp", "operators", "operator-a", "parent"), 256),
        (("time_stamp", "operators", "operator-a", "ownership_evidence", "ref"), 1024),
    ):
        for count in (limit, limit + 1):
            candidate = TRD.edit(base, path, "replace", astral * count)
            if candidate is not None:
                add(candidate)
    return queries


def proof_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    class Recorder:
        def __getattr__(self, name: str) -> Any:
            return getattr(tlog_proof, name)

        def parse_proof(self, data: bytes) -> tlog_proof.TlogProof:
            case: dict[str, Any] = {"data": base64.b64encode(data).decode()}
            try:
                proof = tlog_proof.parse_proof(data)
            except tlog_proof.ProofError:
                case["python"] = {"ok": False}
                cases.append(case)
                raise
            case["python"] = {
                "ok": True,
                "index": str(proof.index),
                "path": [h.hex() for h in proof.path],
                "note": base64.b64encode(proof.note).decode(),
                "extra": None if proof.extra is None else base64.b64encode(proof.extra).decode(),
            }
            cases.append(case)
            return proof

    for scenario in proof_checks.SCENARIOS.values():
        scenario(Recorder())  # type: ignore[arg-type]
    return cases


def timing_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    class Recorder:
        def __getattr__(self, name: str) -> Any:
            return getattr(timing, name)

        def assess(self, committed_at: Any, window_opens_at: Any, tokens: Any, rules: Any) -> Any:
            result = timing.assess(committed_at, window_opens_at, tokens, rules)
            ms = lambda moment: None if moment is None else int(moment.timestamp() * 1000)  # noqa: E731
            cases.append(
                {
                    "committed_ms": ms(committed_at),
                    "window_ms": ms(window_opens_at),
                    "tokens": [
                        {
                            "name": t.name,
                            "reason": t.reason,
                            "verified": None
                            if t.verified is None
                            else {
                                "gen_ms": ms(t.verified.gen_time),
                                "operator": t.verified.operator,
                                "root": t.verified.root_fingerprint,
                                "signer": t.verified.signer_fingerprint,
                            },
                        }
                        for t in tokens
                    ],
                    "rules": {
                        "operators": {
                            name: [
                                op.parent,
                                None if op.evidence is None else [op.evidence.kind, op.evidence.ref, op.evidence.subject_sha256],
                            ]
                            for name, op in rules.operators.items()
                        },
                        "kinds": sorted(rules.evidence_kinds_accepted),
                        "required": rules.required_independent,
                        "before": rules.window_before_commit_s,
                        "after": rules.window_after_commit_s,
                        "spread": rules.max_spread_s,
                        "u": rules.u_constant_s,
                    },
                    "python": {
                        "tokens": [
                            [
                                t.name,
                                t.valid,
                                t.reason,
                                ms(t.gen_time),
                                t.root_fingerprint,
                                t.signer_fingerprint,
                                t.operator,
                                t.parent,
                                t.timely,
                            ]
                            for t in result.tokens
                        ],
                        "independent": result.independent_operators,
                        "chosen": sorted(result.chosen),
                        "spread_ms": None if result.spread_s is None else round(result.spread_s * 1000),
                        "attested_ms": ms(result.attested_at),
                        "level": result.level,
                        "eligible_ms": None if result.eligible_after_offset_s is None else round(result.eligible_after_offset_s * 1000),
                        "declared": result.window_declared,
                        "assurance": result.assurance_class,
                    },
                }
            )
            return result

    for scenario in timing_checks.SCENARIOS.values():
        scenario(Recorder())  # type: ignore[arg-type]
    return cases


def bundle_cases() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for case in build_cases(Path(tempfile.mkdtemp())):
        files = {
            str(p.relative_to(case.directory)): base64.b64encode(p.read_bytes()).decode()
            for p in sorted(case.directory.rglob("*"))
            if p.is_file()
        }
        directories = sorted(str(p.relative_to(case.directory)) for p in case.directory.rglob("*") if p.is_dir())
        verdict = scrub(verify.verify_bundle(case.directory, case.root, case.status))
        out.append(
            {
                "name": case.name,
                "files": files,
                "directories": directories,
                "root": None if case.root is None else base64.b64encode(case.root.payload).decode(),
                "status": case.status,
                "python": verdict,
            }
        )
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, cases in (
        ("root_cases", root_cases()),
        ("proof_cases", proof_cases()),
        ("timing_cases", timing_cases()),
        ("bundle_cases", bundle_cases()),
    ):
        target = OUT / f"{name}.json.gz"
        target.write_bytes(gzip.compress((json.dumps({"cases": cases}) + "\n").encode(), mtime=0))
        print(f"wrote {target} ({len(cases)} cases, {target.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main()
