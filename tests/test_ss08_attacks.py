"""The attacks on the time-stamp and trusted-root checks (T1 to T7 on time-stamp tokens, R1 to R6 on the trusted-root client), end to end.

Every attack is run through the two command-line verifiers, `hoshitori-verify` in Python and in Node, on a real bundle and a real
trusted root, and the two must give the same verdict. T6 and R6 are stated limits of the design, not defects: they are asserted as
the documented behaviour so that a change to them is noticed.
"""

from __future__ import annotations

import base64
import copy
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from hoshitori import timeutil, timing
from tests import root_support as R
from tests import timing_checks
from tests import verify_checks as V
from tests.test_cli import NODE, NOW, run_node, run_python, scrub

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

KEY1 = R.new_signer(1)


@pytest.fixture(scope="module")
def cases(tmp_path_factory: pytest.TempPathFactory) -> dict[str, V.Case]:
    return {case.name: case for case in V.build_cases(tmp_path_factory.mktemp("ss08"))}


def both_clis(args: list[str]) -> tuple[tuple[int, Any, str], tuple[int, Any, str]]:
    return run_python(*args), run_node(*args)


TOKEN_ATTACKS = {
    "T1": ("a time-stamp certificate issued by an ordinary TLS leaf", "PATH"),
    "T2": ("one token and a copy with another operator's certificates injected", None),
    "T4": ("a time-stamp certificate with an unknown critical extension", "CERT"),
    "T5": ("an intermediate certificate without the certificate-signing usage", "PATH"),
}


@pytest.mark.parametrize("attack", TOKEN_ATTACKS)
def test_a_token_attack_never_reaches_c3(attack: str, cases: dict[str, V.Case], tmp_path: Path) -> None:
    name, reason = TOKEN_ATTACKS[attack]
    case = cases[name]
    root_file = tmp_path / "root.json"
    assert case.root is not None
    root_file.write_bytes(case.root.payload)
    python, node = both_clis([str(case.directory), "--trusted-root", str(root_file), "--now", NOW])
    assert python[0] == node[0] == 0  # the bundle itself is sound; only the time stamps are attacked
    assert python[1] is not None and node[1] is not None
    assert python[1]["timing"]["level"] == "C1" and python[1]["eligibility"]["scorable"] is False
    if reason is not None:
        assert python[1]["timing"]["tokens"][0]["reason"] == reason
    assert scrub(python[1]) == scrub(node[1])


def test_t3_a_time_without_an_offset_is_refused_by_both() -> None:
    for text in ("2026-10-29T12:00:00", "2026-10-29 12:00:00Z", "2026-10-29"):
        with pytest.raises(timeutil.TimeError):
            timeutil.parse_rfc3339(text)
    python, node = run_python("/nonexistent", "--now", "2026-10-29T12:00:00"), run_node("/nonexistent", "--now", "2026-10-29T12:00:00")
    assert python[0] == node[0] == 2


def test_t6_independence_is_judged_between_organisations_of_record() -> None:
    """A stated limit, not a defect: two roots whose registry entries name one parent count as one operator; one party that holds roots
    registered under different parents is outside what the registry can see (contract section 8)."""
    assert timing_checks.failures(timing) == []


def test_t7_a_signer_that_expired_after_the_time_stamp_still_counts(cases: dict[str, V.Case], tmp_path: Path) -> None:
    case = cases["signer expired after the time stamp was made"]
    root_file = tmp_path / "root.json"
    assert case.root is not None
    root_file.write_bytes(case.root.payload)
    python, node = both_clis([str(case.directory), "--trusted-root", str(root_file), "--now", NOW])
    assert python[1] is not None and node[1] is not None
    assert python[1]["timing"]["level"] == "C3" and python[1]["eligibility"]["scorable"] is True
    assert scrub(python[1]) == scrub(node[1])


# ---------------------------------------------------------------------------------------------------------------------
def document(number: int, **changes: Any) -> dict[str, Any]:
    doc = R.make_document(number)
    doc.update(changes)
    return doc


ROOT_ATTACKS: dict[str, tuple[dict[int, dict[str, Any]], str, str, int]] = {
    # id: (channel, status, code, version of the root the client ends with)
    "R1 the same key listed twice": (
        {2: document(2, root_keys=[base64.b64encode(R.public_bytes(KEY1)).decode()] * 2, root_threshold=2)},
        "REJECTED",
        "MALFORMED",
        1,
    ),
    "R2 threshold 0": ({2: document(2, root_threshold=0)}, "REJECTED", "MALFORMED", 1),
    "R2 no keys at all": ({2: document(2, root_keys=[], root_threshold=0)}, "REJECTED", "MALFORMED", 1),
    "R4 a version that is true": ({2: document(2, version=True)}, "REJECTED", "MALFORMED", 1),
    "R5 an unknown field": ({2: document(2, extra=1)}, "REJECTED", "MALFORMED", 1),
    "R5 format version 99": ({2: document(2, format_version=99)}, "REJECTED", "MALFORMED", 1),
}


# The attacker is given the strongest position the contract allows: the same document on two channels, so that the two-channel rule
# (contract 8) does not stop it and the checks of the document itself must.
def channel_file(directory: Path, name: str, documents: dict[int, dict[str, Any]]) -> Path:
    path = directory / f"{name.split()[0]}-{abs(hash(name)) % 10**6}.json"
    path.write_text(json.dumps({str(v): R.envelope(d, [KEY1]) for v, d in documents.items()}))
    return path


@pytest.mark.parametrize("attack", ROOT_ATTACKS)
def test_a_root_attack_leaves_the_client_on_the_root_it_trusted(attack: str, cases: dict[str, V.Case], tmp_path: Path) -> None:
    documents, status, code, version = ROOT_ATTACKS[attack]
    honest = cases["honest"]
    assert honest.root is not None
    root_file = tmp_path / "root.json"
    root_file.write_bytes(honest.root.payload)
    channel = channel_file(tmp_path, attack, copy.deepcopy(documents))
    python, node = both_clis(
        [str(honest.directory), "--trusted-root", str(root_file), "--channel", str(channel), "--channel", str(channel), "--now", NOW]
    )
    assert python[1] is not None and node[1] is not None
    assert python[1]["root_update"]["status"] == status, python[1]["root_update"]
    assert python[1]["root_update"]["code"] == code, python[1]["root_update"]
    assert python[1]["root_update"]["version"] == version
    assert scrub(python[1]) == scrub(node[1])
    assert python[0] == node[0]


def test_r3_a_document_without_its_threshold_is_refused_not_a_crash(cases: dict[str, V.Case], tmp_path: Path) -> None:
    honest = cases["honest"]
    assert honest.root is not None
    root_file = tmp_path / "root.json"
    root_file.write_bytes(honest.root.payload)
    doc = document(2)
    del doc["root_threshold"]
    channel = channel_file(tmp_path, "R3", {2: doc})
    python, node = both_clis(
        [str(honest.directory), "--trusted-root", str(root_file), "--channel", str(channel), "--channel", str(channel), "--now", NOW]
    )
    assert (
        python[0] == node[0] == 1
    )  # a rejected update fails closed: nothing is believed on the strength of a root whose chain is in question
    assert python[1] is not None and node[1] is not None
    assert python[1]["root_update"]["status"] == "REJECTED" and python[1]["root_update"]["version"] == 1
    assert python[1]["root_of_trust"]["state"] == "BUNDLE_ASSERTED" and python[1]["eligibility"]["scorable"] is False
    assert scrub(python[1]) == scrub(node[1])


def test_r6_a_withheld_version_ends_the_chain_at_the_last_one_served(cases: dict[str, V.Case], tmp_path: Path) -> None:
    """The documented window: versions 1 and 2 are served, version 3 is withheld; the client follows to 2 and no further."""
    honest = cases["honest"]
    assert honest.root is not None
    root_file = tmp_path / "root.json"
    root_file.write_bytes(honest.root.payload)
    channel = channel_file(tmp_path, "R6", {2: document(2)})
    python, node = both_clis(
        [str(honest.directory), "--trusted-root", str(root_file), "--channel", str(channel), "--channel", str(channel), "--now", NOW]
    )
    assert python[0] == node[0] == 0
    assert python[1] is not None and node[1] is not None
    assert python[1]["root_update"]["status"] == "CURRENT" and python[1]["root_update"]["version"] == 2
    assert scrub(python[1]) == scrub(node[1])
    shutil.rmtree(tmp_path, ignore_errors=True)
