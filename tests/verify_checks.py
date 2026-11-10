"""Cases for the bundle verifier: an honest bundle and every way found so far to damage, forge or mis-time one.

A case is a bundle directory, a trusted root (or none) and the facts the verdict must show. ``build_cases`` makes them in
a temporary directory; ``check`` runs a verifier module over a case. The Node verifier is run over the same directories.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import shutil
import threading
import tracemalloc
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from cryptography.x509.oid import ExtendedKeyUsageOID

from hoshitori import bundle as layout
from hoshitori import canonical, merkle
from hoshitori.checkpoint import Checkpoint, parse_text, sign_checkpoint
from hoshitori.note import split_note
from hoshitori.timeutil import format_rfc3339_ms, parse_rfc3339
from hoshitori.tlog_proof import TlogProof, format_proof, parse_proof
from hoshitori.trusted_root import TrustedRoot, parse_root
from tests import bundle_support as B
from tests import der_tools as D
from tests import root_support as R
from tests import tsa_pki as P

# The standing list of what a verdict does not prove (VERIFICATION_SPEC section 1, item 4; VERIFIER_THREAT_MODEL section 2, attack A4),
# and the item it gains when no trusted root vouches for the bundle (VERIFICATION_SPEC section 2).
NOT_PROVEN = [
    "that the evidence was true or complete",
    "that the forecast is good",
    "that the agent reasoned as stored",
    "who controls the signing key",
    "that the log held nothing before its first append, or that no other history exists beyond the checkpoints seen",
    "that any witness cosigned: this verifier does not check cosignatures, so no record reaches level C4",
    "that no time-stamp certificate had been revoked: revocation evidence is neither archived nor checked by this verifier version",
    "both authorities honest at once: the level assumes that the time-stamp authorities did not collude or share a time source",
    "the clock of the machine that ran this verifier",
]
NOT_PROVEN_WITHOUT_ROOT = [*NOT_PROVEN, "that this bundle came from Hoshitori"]
# What the trusted root asked for, as the verdict states it (the test root's own rules): a loose root gives a loose pass, so the verdict says
# which rules the pass was judged by.
BASIS = {
    "required_independent": 2,
    "window_before_commit_s": 1,
    "window_after_commit_s": 60,
    "close_after_commit_s": 90,
    "max_spread_s": 5,
    "u_constant_s": 5,
}
UNASSIGNED_105D2 = "code point U+105D2 is not assigned in Unicode 15.0"
RUNTIME_TOO_OLD = "Unicode 15.0 or later is needed to check NFC; this runtime has an older version"
IMPOSSIBLE_ORDER = "IMPOSSIBLE_ORDER"
CREATED_AFTER_COMMIT = "created_at is more than 1 s later than committed_at"
NOT_REGULAR = "not a regular file"
MAX_RECORD_FILE = 1_048_576  # VERIFIER_THREAT_MODEL section 4: 1 MiB record
MAX_TOKEN_FILE = 131_072  # what verify_token accepts, so the most a valid token file can hold
MAX_PUBLICATION_FILE = 1_048_576  # no limit is stated for publication.json; the verifiers apply the 1 MiB record limit


@dataclass
class Case:
    name: str
    directory: Path
    root: TrustedRoot | None
    status: str = "CURRENT"
    expect: dict[str, object] = field(default_factory=dict)
    fifos: list[Path] = field(default_factory=list)  # FIFOs no process writes: an ordinary open of one waits for ever
    max_peak_bytes: int | None = None  # the most memory the verifier may allocate for this case (traced with tracemalloc)


def lookup(verdict: object, path: str) -> object:
    node = verdict
    for part in path.split("."):
        if isinstance(node, list):
            node = node[int(part)]
        elif isinstance(node, dict):
            if part not in node:
                return "<absent>"
            node = node[part]
        else:
            return "<absent>"
    return node


def run_guarded(call: Callable[[], Any], fifos: list[Path], timeout_s: float = 10.0) -> Any:
    """Run ``call`` in a thread, for a case with FIFOs that no process writes.

    A verifier that opens such a FIFO with an ordinary (blocking) open waits for ever. After ``timeout_s`` each FIFO is opened for writing
    and closed again, which releases a blocked open, until the call ends; the case then fails as blocked, whatever the verdict was.
    """
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["value"] = call()
        except BaseException as exc:  # handed to the caller below
            outcome["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout_s)
    blocked = worker.is_alive()
    while worker.is_alive():
        for fifo in fifos:
            with contextlib.suppress(OSError):  # no reader has it open yet
                os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        worker.join(0.2)
    if blocked:
        raise TimeoutError("the verifier blocked on a FIFO that no process writes")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def verdict_of(mod: types.ModuleType, case: Case) -> dict[str, Any]:
    """The verdict of ``mod`` for ``case``, run guarded when the case has FIFOs and traced when its memory is bounded."""

    def call() -> dict[str, Any]:
        return mod.verify_bundle(case.directory, case.root, case.status)  # type: ignore[no-any-return]

    if case.max_peak_bytes is not None:
        tracemalloc.start()
        try:
            verdict = call()
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        if peak > case.max_peak_bytes:
            raise MemoryError(f"the verifier allocated {peak} bytes at its peak, more than {case.max_peak_bytes}")
        return verdict
    if case.fifos:
        return run_guarded(call, case.fifos)  # type: ignore[no-any-return]
    return call()


def check(mod: types.ModuleType, case: Case) -> list[str]:
    verdict = verdict_of(mod, case)
    bad = []
    for path, wanted in case.expect.items():
        got = lookup(verdict, path)
        if got != wanted:
            bad.append(f"{case.name}: {path} is {got!r}, expected {wanted!r}")
    return bad


ITEM_EV, ITEM_FC = layout.item_stem(0), layout.item_stem(1)


def read(path: Path) -> bytes:
    return path.read_bytes()


def edit_record(directory: Path, stem: str, change: Callable[[bytes], bytes]) -> None:
    path = directory / layout.ITEMS / f"{stem}{layout.CANONICAL_SUFFIX}"
    path.write_bytes(change(path.read_bytes()))


def edit_proof(directory: Path, stem: str, change: Callable[[TlogProof], TlogProof]) -> None:
    path = directory / layout.ITEMS / f"{stem}{layout.PROOF_SUFFIX}"
    path.write_bytes(format_proof(change(parse_proof(path.read_bytes()))))


def set_tokens(directory: Path, tokens: dict[str, bytes]) -> None:
    folder = directory / layout.TSA
    if folder.exists():
        shutil.rmtree(folder)
    if tokens:
        folder.mkdir()
        for name, blob in tokens.items():
            (folder / f"{name}{layout.TOKEN_SUFFIX}").write_bytes(blob)


def rewrite_record(directory: Path, stem: str, change: Callable[[dict[str, object]], None]) -> None:
    """Replace a record by the canonical form of an edited copy (its proof no longer matches, but the first failure is the envelope)."""
    path = directory / layout.ITEMS / f"{stem}{layout.CANONICAL_SUFFIX}"
    record = canonical.parse_record(path.read_bytes())
    change(record)  # type: ignore[arg-type]
    path.write_bytes(canonical.dumps(record))


PLACEHOLDER = "@placeholder@"


def committed_by_hand(
    directory: Path,
    world: B.World,
    *,
    note: str = "plain",
    forecast_created_at: str = "2026-10-29T11:59:30.000Z",
    committed_at: str = "2026-10-29T12:00:00.000Z",
    token_offsets: tuple[float, float] = (1, 3),
) -> Path:
    """A bundle written without the ledger, as an honest writer of an earlier version would have committed it.

    The two records of the world (an evidence item whose body carries ``note`` and a forecast that cites it) get their canonical bytes, a
    tree, a checkpoint signed by the world's log key, inclusion proofs and two tokens over the checkpoint from the world's authorities. The
    note is written into the bytes after serialisation, so a string that today's writer refuses still stands in the record exactly as an
    older writer would have hashed it; the times are free, so a record may say it was created after it was committed.
    """
    evidence = {**B.evidence_record(), "committed_at": committed_at}
    evidence["body"] = {**evidence["body"], "note": PLACEHOLDER}  # type: ignore[dict-item]
    evidence_bytes = canonical.dumps(evidence).replace(PLACEHOLDER.encode(), note.encode())
    producer = canonical.dumps({k: v for k, v in evidence.items() if k != "committed_at"}).replace(PLACEHOLDER.encode(), note.encode())
    opens = format_rfc3339_ms(parse_rfc3339(committed_at) + timedelta(seconds=300))
    forecast = {
        **B.forecast_record(opens, [{"id": "ev1", "sha256": hashlib.sha256(producer).hexdigest()}]),
        "created_at": forecast_created_at,
        "committed_at": committed_at,
    }
    records = [evidence_bytes, canonical.dumps(forecast)]
    tree = merkle.MerkleTree()
    for record in records:
        tree.append(record)
    signed = sign_checkpoint(Checkpoint(R.LOG_ORIGIN, tree.size, tree.root()), [R.log_signer(7)])
    digest = hashlib.sha256(split_note(signed)[0]).digest()
    (directory / layout.ITEMS).mkdir(parents=True)
    for index, record in enumerate(records):
        stem = layout.item_stem(index)
        (directory / layout.ITEMS / f"{stem}{layout.CANONICAL_SUFFIX}").write_bytes(record)
        proof = TlogProof(index, tuple(tree.inclusion_proof(index)), signed)
        (directory / layout.ITEMS / f"{stem}{layout.PROOF_SUFFIX}").write_bytes(format_proof(proof))
    (directory / layout.TSA).mkdir()
    for name, chain, offset in (("a", world.chain_a, token_offsets[0]), ("b", world.chain_b, token_offsets[1])):
        moment = parse_rfc3339(committed_at) + timedelta(seconds=offset)
        token = P.build_token(digest, chain, P.TokenOptions(gen_time=moment, nonce=None))
        (directory / layout.TSA / f"{name}{layout.TOKEN_SUFFIX}").write_bytes(token)
    (directory / layout.RAW).mkdir()
    (directory / layout.RAW / B.RAW_HASH).write_bytes(B.RAW_PAYLOAD)
    return directory


def json_of_size(size: int, claims: int = 0) -> bytes:
    """A JSON object of exactly ``size`` bytes with ``claims`` empty claims, padded with a string member."""
    head = b'{"claims":[' + b",".join([b"{}"] * claims) + b'],"pad":"'
    return head + b"x" * (size - len(head) - 2) + b'"}'


def build_cases(tmp: Path) -> list[Case]:
    world = B.make_world(tmp / "world")
    root = world.root
    cases: list[Case] = []
    a, b = world.chain_a, world.chain_b

    def add(
        name: str,
        change: Callable[[Path], None] | None = None,
        expect: dict[str, object] | None = None,
        *,
        source: B.World | None = None,
        root: TrustedRoot | None = root,
        status: str = "CURRENT",
    ) -> Case:
        directory = (source or world).copy_bundle(name)
        if change is not None:
            change(directory)
        case = Case(name, directory, root, status, expect or {})
        cases.append(case)
        return case

    honest = {
        "integrity.ok": True,
        "integrity.self_consistent": True,
        "integrity.one_checkpoint": True,
        "integrity.errors": [],
        "integrity.checkpoints.consistency": "none",
        "integrity.checkpoints.stamped.size": 3,
        "integrity.checkpoints.later": None,
        "timing.later_items": [],
        "cycle.later.shown": False,
        "integrity.refs.resolved": 1,
        "integrity.raw.verified.0": B.RAW_HASH,
        "timing.level": "C3",
        "timing.basis": BASIS,
        "timing.subjects": [ITEM_FC],
        f"timing.items.{ITEM_FC}.independent_operators": 2,
        f"timing.items.{ITEM_FC}.eligible_after_offset_ms": 7000,
        "eligibility.scorable": True,
        "root_of_trust.state": "PINNED",
        "timing.public_commit.claimed": 1,
        "timing.public_commit.authenticated_offline": 0,
        "not_proven": NOT_PROVEN,
    }
    add("honest", expect=honest)

    def many_tokens_named_beyond_the_plane(d: Path) -> None:
        # more tokens than the verifier reads (16), two of them genuine with names that sort one way by code point and the other by
        # UTF-16 code unit: both verifiers must keep the same sixteen (a producer must not be able to pick which verifier says C3)
        first, second = sorted((d / "tsa").glob("*.tsr"))[:2]
        genuine = (first.read_bytes(), second.read_bytes())
        for token in list((d / "tsa").glob("*.tsr")):
            token.unlink()
        for i in range(14):
            (d / "tsa" / f"j{i:02d}.tsr").write_bytes(b"junk")
        (d / "tsa" / "\uff21.tsr").write_bytes(genuine[0])
        (d / "tsa" / "\U00010000.tsr").write_bytes(genuine[1])
        (d / "tsa" / "\U00010001.tsr").write_bytes(genuine[1])

    add("token names that sort differently by code point and by UTF-16 unit", many_tokens_named_beyond_the_plane)

    broken = {"integrity.ok": False, "eligibility.scorable": False}

    def fc(error: str) -> dict[str, object]:
        return {**broken, f"integrity.items.{ITEM_FC}.error": error}

    # --- the record itself ---------------------------------------------------------------------------------------------
    add(
        "record edited",
        lambda d: edit_record(d, ITEM_FC, lambda x: x.replace(b'"horizon":"24h"', b'"horizon":"25h"')),
        {**fc("INCLUSION"), "timing.level": "none", "timing.reason": "INTEGRITY_FAILED", "eligibility.reasons.0": "INTEGRITY"},
    )

    def make_unreadable(d: Path) -> None:
        path = d / layout.ITEMS / f"{ITEM_FC}{layout.CANONICAL_SUFFIX}"
        path.unlink()
        path.mkdir()

    add("record file that cannot be read", make_unreadable, fc("UNREADABLE"))
    add(
        "record not in canonical form",
        lambda d: edit_record(d, ITEM_FC, lambda x: x.replace(b'{"body"', b'{ "body"', 1)),
        {**fc("CANONICAL"), f"integrity.items.{ITEM_FC}.canonical": False},
    )
    add(
        "committed time edited",
        lambda d: edit_record(
            d, ITEM_FC, lambda x: x.replace(b'"committed_at":"2026-10-29T12:00:00.000Z"', b'"committed_at":"2026-10-29T11:00:00.000Z"')
        ),
        fc("INCLUSION"),
    )
    add(
        "record without a committed time",
        lambda d: edit_record(d, ITEM_FC, lambda x: x.replace(b'"committed_at":"2026-10-29T12:00:00.000Z",', b"")),
        fc("ENVELOPE"),
    )
    add("record that is not JSON", lambda d: edit_record(d, ITEM_FC, lambda x: b"not json"), fc("CANONICAL"))
    add("record too large", lambda d: edit_record(d, ITEM_FC, lambda x: b"{" + b" " * (2 * 1024 * 1024) + b"}"), fc("TOO_LARGE"))

    def swap_with_other(d: Path) -> None:
        other = (d / layout.ITEMS / f"{ITEM_EV}{layout.CANONICAL_SUFFIX}").read_bytes()
        edit_record(d, ITEM_FC, lambda x: other)

    add("one record replaced by the other", swap_with_other, fc("INCLUSION"))

    def envelope_case(label: str, change: Callable[[dict[str, object]], None]) -> None:
        add(f"envelope: {label}", lambda d: rewrite_record(d, ITEM_FC, change), fc("ENVELOPE"))

    envelope_case("an unknown member", lambda r: r.__setitem__("zzz", 1))
    envelope_case("a missing body", lambda r: r.pop("body"))
    envelope_case("a missing source", lambda r: r.pop("source"))
    envelope_case("the wrong spec", lambda r: r.__setitem__("spec", "other/1"))
    envelope_case("a bad type", lambda r: r.__setitem__("type", "Bad-Type"))
    envelope_case("a type with a trailing newline", lambda r: r.__setitem__("type", "forecast\n"))
    envelope_case("type version zero", lambda r: r.__setitem__("type_version", 0))
    envelope_case("type version true", lambda r: r.__setitem__("type_version", True))
    envelope_case("an id with a space", lambda r: r.__setitem__("id", "a b"))
    envelope_case("an id with a trailing newline", lambda r: r.__setitem__("id", "fc1\n"))
    envelope_case("a source that is not a urn", lambda r: r.__setitem__("source", "https://example.org"))
    envelope_case("a source that is not ascii", lambda r: r.__setitem__("source", "urn:caf" + chr(0xE9)))
    envelope_case("a source that is too long", lambda r: r.__setitem__("source", "urn:" + "a" * 260))
    envelope_case("a creation time without an offset", lambda r: r.__setitem__("created_at", "2026-10-29T11:59:30"))
    envelope_case("a creation time that is not a string", lambda r: r.__setitem__("created_at", 5))
    envelope_case("a committed time without an offset", lambda r: r.__setitem__("committed_at", "2026-10-29T12:00:00"))
    envelope_case("a body that is not an object", lambda r: r.__setitem__("body", []))
    envelope_case("refs that are not a list", lambda r: r.__setitem__("refs", "x"))
    envelope_case("a ref with an extra member", lambda r: r.__setitem__("refs", [{"id": "ev1", "sha256": "0" * 64, "x": 1}]))
    envelope_case("a ref hash in upper case", lambda r: r.__setitem__("refs", [{"id": "ev1", "sha256": "A" * 64}]))
    envelope_case("a ref hash with a trailing newline", lambda r: r.__setitem__("refs", [{"id": "ev1", "sha256": "0" * 64 + "\n"}]))
    envelope_case("a ref that is not an object", lambda r: r.__setitem__("refs", [5]))
    envelope_case("a privacy value that is unknown", lambda r: r.__setitem__("privacy", "secret"))
    envelope_case("a privacy value that is a list", lambda r: r.__setitem__("privacy", []))

    # --- the proof ---------------------------------------------------------------------------------------------------
    def with_index(delta: int) -> Callable[[Path], None]:
        return lambda d: edit_proof(d, ITEM_FC, lambda p: TlogProof(p.index + delta, p.path, p.note, p.extra))

    add("proof index moved", with_index(1), fc("INCLUSION"))
    add("proof index moved far", with_index(10**6), fc("INCLUSION"))
    add("proof path emptied", lambda d: edit_proof(d, ITEM_FC, lambda p: TlogProof(p.index, (), p.note, p.extra)), fc("INCLUSION"))
    add(
        "proof path with an extra hash",
        lambda d: edit_proof(d, ITEM_FC, lambda p: TlogProof(p.index, (*p.path, p.path[0]), p.note, p.extra)),
        fc("INCLUSION"),
    )
    add(
        "proof path hash changed",
        lambda d: edit_proof(d, ITEM_FC, lambda p: TlogProof(p.index, (b"\x01" * 32, *p.path[1:]), p.note, p.extra)),
        fc("INCLUSION"),
    )
    add("proof file removed", lambda d: (d / layout.ITEMS / f"{ITEM_FC}{layout.PROOF_SUFFIX}").unlink(), fc("MISSING_PROOF"))
    add("proof file garbage", lambda d: (d / layout.ITEMS / f"{ITEM_FC}{layout.PROOF_SUFFIX}").write_bytes(b"garbage"), fc("PROOF"))
    add("proof file too large", lambda d: (d / layout.ITEMS / f"{ITEM_FC}{layout.PROOF_SUFFIX}").write_bytes(b"x" * 70000), fc("PROOF"))
    add(
        "extra data in the proof changes nothing",
        lambda d: edit_proof(d, ITEM_FC, lambda p: TlogProof(p.index, p.path, p.note, b"unauthenticated")),
        honest,
    )

    # --- the checkpoint ----------------------------------------------------------------------------------------------
    def tamper_note(change: Callable[[bytes], bytes]) -> Callable[[Path], None]:
        def apply(d: Path) -> None:
            for stem in (ITEM_EV, ITEM_FC):
                edit_proof(d, stem, lambda p: TlogProof(p.index, p.path, change(p.note), p.extra))

        return apply

    sig_failed = {**broken, f"integrity.items.{ITEM_FC}.signature": "failed", f"integrity.items.{ITEM_FC}.error": "SIGNATURE"}
    add("checkpoint size edited", tamper_note(lambda n: n.replace(b"\n3\n", b"\n4\n", 1)), sig_failed)
    add("checkpoint root edited", tamper_note(lambda n: n.replace(n.split(b"\n")[2], base64.b64encode(b"\x07" * 32), 1)), sig_failed)
    add("checkpoint for another log", tamper_note(lambda n: n.replace(R.LOG_ORIGIN.encode(), b"other.example/log", 1)), sig_failed)

    def resigned_for_another_origin(d: Path) -> None:
        def change(n: bytes) -> bytes:
            text, _ = split_note(n)
            cp = parse_text(text)
            return sign_checkpoint(Checkpoint("other.example/log", cp.size, cp.root), [R.log_signer(7)])

        tamper_note(change)(d)

    add("log key signs a checkpoint for another origin", resigned_for_another_origin, sig_failed)
    add("checkpoint without signatures", tamper_note(lambda n: n[: n.index(b"\n\n") + 2]), fc("CHECKPOINT"))

    other_world = B.make_world(tmp / "second", window_s=301)  # a different forecast, so a different tree and checkpoint
    add(
        "items proven at different checkpoints",
        lambda d: edit_proof(
            d,
            ITEM_EV,
            lambda p: TlogProof(
                p.index,
                p.path,
                parse_proof((other_world.bundle_dir / layout.ITEMS / f"{ITEM_EV}{layout.PROOF_SUFFIX}").read_bytes()).note,
                p.extra,
            ),
        ),
        {**broken, "integrity.one_checkpoint": False, "integrity.errors": ["CHECKPOINTS_DIFFER"]},
    )

    # --- who signed it: eras, then a bundle that brings its own world ------------------------------------------------
    key7 = R.log_signer(7).verifier.vkey()
    key8 = R.log_signer(8).verifier.vkey()
    closed = B.make_world(
        tmp / "closed", eras=[{"vkey": key7, "from_size": 0, "through_size": 1}, {"vkey": key8, "from_size": 2, "through_size": None}]
    )
    add(
        "retired key signs past its closing size",
        source=closed,
        root=closed.root,
        expect={**broken, f"integrity.items.{ITEM_FC}.error": "ERA", "timing.level": "none"},
    )
    not_yet = B.make_world(
        tmp / "notyet", eras=[{"vkey": key8, "from_size": 0, "through_size": 4}, {"vkey": key7, "from_size": 5, "through_size": None}]
    )
    add("key signs before its era", source=not_yet, root=not_yet.root, expect={**broken, f"integrity.items.{ITEM_FC}.error": "ERA"})
    other_key = B.make_world(tmp / "otherkey", eras=[{"vkey": key8, "from_size": 0, "through_size": None}])
    add(
        "log key of the root is not the signing key",
        source=other_key,
        root=other_key.root,
        expect={**broken, f"integrity.items.{ITEM_FC}.signature": "failed"},
    )

    forger = B.make_world(tmp / "forger", log_seed=99)
    forged_dir = forger.bundle_dir
    forger_root = parse_root(
        canonical.dumps(
            R.make_document(
                1,
                root_keys=[R.new_signer(1)],
                eras=[{"vkey": R.log_signer(99).verifier.vkey(), "from_size": 0, "through_size": None}],
                pinned=[(forger.chain_a.root, "operator-a"), (forger.chain_b.root, "operator-b")],
            )
        )
    )  # type: ignore[arg-type]
    cases.append(
        Case(
            "forger's whole bundle, checked against the real root",
            forged_dir,
            root,
            "CURRENT",
            {**broken, "integrity.items.item-00000001.signature": "failed", "timing.level": "none"},
        )
    )
    cases.append(
        Case(
            "forger's whole bundle, checked against the forger's own root",
            forged_dir,
            forger_root,
            "CURRENT",
            {"integrity.ok": True, "timing.level": "C3", "eligibility.scorable": True},
        )
    )
    cases.append(
        Case(
            "forger's whole bundle, no root",
            forged_dir,
            None,
            "CURRENT",
            {
                "integrity.ok": False,
                "integrity.self_consistent": True,
                "root_of_trust.state": "BUNDLE_ASSERTED",
                "timing.level": "none",
                "eligibility.scorable": False,
                "not_proven": NOT_PROVEN_WITHOUT_ROOT,
            },
        )
    )

    # the insider: someone who holds the log's real key changes a forecast, rebuilds every hash and signs the new checkpoint with the real
    # key. Record integrity passes (that is what it can show); the time-stamps are over the original checkpoint, so neither matches
    # the new one, and nothing is believed to have been stamped (contract section 11: only the external commitment defeats this forger)
    insider = B.make_world(tmp / "insider", tokens=False, body_extra={"p_up": "0.99"})
    stamped = {path.stem: path.read_bytes() for path in (world.bundle_dir / layout.TSA).glob(f"*{layout.TOKEN_SUFFIX}")}
    set_tokens(insider.bundle_dir, stamped)
    cases.append(
        Case(
            "insider with the real key rebuilds the log: the original time-stamps no longer match",
            insider.bundle_dir,
            root,
            "CURRENT",
            {
                "integrity.ok": True,
                "integrity.self_consistent": True,
                "root_of_trust.state": "PINNED",
                "timing.level": "C1",
                "timing.tokens.0.valid": False,
                "timing.tokens.0.reason": "IMPRINT",
                "timing.tokens.1.valid": False,
                "timing.tokens.1.reason": "IMPRINT",
                f"timing.items.{ITEM_FC}.independent_operators": 0,
                "eligibility.scorable": False,
                "eligibility.reasons": ["LEVEL_BELOW_C3"],
            },
        )
    )

    loose_document = json.loads(world.extra["root_document"])  # type: ignore[arg-type]
    loose_document["time_stamp"] = {
        **loose_document["time_stamp"],
        "window_after_commit_s": 3600,
        "close_after_commit_s": 7200,
        "max_spread_s": 600,
    }
    loose = parse_root(canonical.dumps(loose_document))
    add(
        "a looser root states its looser basis",
        root=loose,
        expect={**honest, "timing.basis": {**BASIS, "window_after_commit_s": 3600, "close_after_commit_s": 7200, "max_spread_s": 600}},
    )

    def planted_policy(d: Path) -> None:
        (d / "policy.json").write_text(json.dumps({"log": {"origin": "forged"}, "tsa": {"pinned_roots_sha256": {}}}))

    add("a policy file inside the bundle is ignored", planted_policy, honest)

    # --- the root of trust -------------------------------------------------------------------------------------------
    add(
        "no root: asserted by the bundle",
        root=None,
        expect={
            "integrity.ok": False,
            "integrity.self_consistent": True,
            "root_of_trust.state": "BUNDLE_ASSERTED",
            f"integrity.items.{ITEM_FC}.signature": "unauthenticated",
            "timing.level": "none",
            "timing.reason": "UNAUTHENTICATED: no trusted root, so no token is believed",
            "timing.basis": None,
            "eligibility.scorable": False,
            "eligibility.reasons.0": "ROOT_NOT_CURRENT",
            "not_proven": NOT_PROVEN_WITHOUT_ROOT,
        },
    )
    add(
        "expired root",
        status="EXPIRED",
        expect={
            "integrity.ok": True,
            "root_of_trust.state": "EXPIRED",
            "timing.level": "C3",
            "eligibility.scorable": False,
            "eligibility.reasons": ["ROOT_NOT_CURRENT"],
            "not_proven": NOT_PROVEN,
        },
    )
    add(
        "root whose update forked",
        status="FORK",
        expect={
            "root_of_trust.state": "BUNDLE_ASSERTED",
            "integrity.ok": False,
            "eligibility.scorable": False,
            "not_proven": NOT_PROVEN_WITHOUT_ROOT,
        },
    )
    add(
        "root whose update was rejected",
        status="REJECTED",
        expect={"root_of_trust.state": "BUNDLE_ASSERTED", "timing.level": "none", "eligibility.scorable": False},
    )

    # --- the time stamps ---------------------------------------------------------------------------------------------
    def tokens(**named: bytes) -> Callable[[Path], None]:
        return lambda d: set_tokens(d, dict(named))

    c_chain = P.Chain.build("Authority C")

    def inject_certificates(blob: bytes, extra: list[P.Authority]) -> bytes:
        """The same token with other certificates added to its unsigned certificate set."""
        tree = D.parse(blob)
        certificates = (
            tree.children[1].children[1].children[0].children[3]
        )  # response -> content info -> [0] -> signed data -> [0] certificates
        certificates.children.extend(D.parse(c.der) for c in extra)
        return tree.encode()

    ta1, tb3 = world.token(a, 1), world.token(b, 3)
    low = {"timing.level": "C1", "eligibility.scorable": False, "eligibility.reasons": ["LEVEL_BELOW_C3"]}
    add("no tokens", tokens(), {**low, "integrity.ok": True, f"timing.items.{ITEM_FC}.independent_operators": 0})
    add("one token", tokens(a=ta1), {**low, f"timing.items.{ITEM_FC}.independent_operators": 1})
    add("one operator stamps twice", tokens(a=ta1, a2=world.token(a, 2)), {**low, f"timing.items.{ITEM_FC}.independent_operators": 1})
    injected = inject_certificates(ta1, [b.tsa, b.root])
    add(
        "one token and a copy with another operator's certificates injected",
        tokens(a=ta1, injected=injected),
        {**low, f"timing.items.{ITEM_FC}.independent_operators": 1},
    )
    add("the same token under two names", tokens(a=ta1, copy=ta1), {**low, f"timing.items.{ITEM_FC}.independent_operators": 1})
    other_digest = hashlib.sha256(b"another checkpoint").digest()
    add(
        "token over another checkpoint",
        tokens(a=ta1, b=world.token(b, 3, digest=other_digest)),
        {**low, "timing.tokens.1.valid": False, "timing.tokens.1.reason": "IMPRINT"},
    )
    add(
        "token from an authority that is not pinned",
        tokens(a=ta1, c=world.token(c_chain, 3)),
        {**low, "timing.tokens.1.valid": False, "timing.tokens.1.reason": "PATH"},
    )
    add(
        "tokens from the same signer certificate under two names",
        tokens(a=ta1, again=world.token(a, 3)),
        {**low, f"timing.items.{ITEM_FC}.independent_operators": 1},
    )
    add(
        "sha-1 CMS digest",
        tokens(a=ta1, b=world.token(b, 3, digest_algorithm="sha1", signature_algorithm="sha1_ecdsa")),
        {**low, "timing.tokens.1.reason": "ALGORITHM"},
    )
    add("garbage token", tokens(a=ta1, junk=b"not a token"), {**low, "timing.tokens.1.reason": "PARSE"})
    add(
        "token window: 60 s after the commit counts",
        tokens(a=world.token(a, 57), b=world.token(b, 60)),
        {"timing.level": "C3", "eligibility.scorable": True},
    )
    add("token window: 61 s after the commit does not", tokens(a=world.token(a, 57), b=world.token(b, 61)), low)
    add("token window: 1 s before the commit counts", tokens(a=world.token(a, -1), b=world.token(b, 1)), {"timing.level": "C3"})
    add("token window: 2 s before the commit does not", tokens(a=world.token(a, -2), b=world.token(b, 1)), low)
    add(
        "tokens too far apart",
        tokens(a=world.token(a, 1), b=world.token(b, 7)),
        {**low, f"timing.items.{ITEM_FC}.independent_operators": 2},
    )
    add(
        "tokens exactly at the spread limit",
        tokens(a=world.token(a, 1), b=world.token(b, 6)),
        {"timing.level": "C3", f"timing.items.{ITEM_FC}.spread_ms": 5000},
    )
    add("many tokens", tokens(**{f"t{i:02d}": ta1 for i in range(17)}), {**low, "timing.tokens.16.reason": "TOO_MANY"})
    expired_signer = P.Chain(a.root, a.intermediate, P.make_tsa(a.intermediate, "Expired TSA", not_after=P.BASE + timedelta(days=10)))
    add("signer expired before the commit", tokens(a=world.token(expired_signer, 1), b=tb3), {**low, "timing.tokens.0.reason": "CERT"})
    # a signer certificate that has expired since the time stamp was made still proves what it proved: validity is judged at the stamp time
    short_lived = P.Chain(a.root, a.intermediate, P.make_tsa(a.intermediate, "Short-lived TSA", not_after=P.BASE + timedelta(days=29)))
    add(
        "signer expired after the time stamp was made",
        tokens(a=world.token(short_lived, 1), b=tb3),
        {"timing.level": "C3", "timing.tokens.0.valid": True, "eligibility.scorable": True},
    )

    # path attacks: certificates issued under a pinned root that the path rules must refuse, in a bundle that is otherwise honest
    leaf = P.make_cert("TLS leaf", a.intermediate, ca=False, digital_signature=True, eku=[ExtendedKeyUsageOID.SERVER_AUTH])
    under_leaf = P.Chain(a.root, leaf, P.make_tsa(leaf, "TSA under a TLS leaf"))
    add(
        "a time-stamp certificate issued by an ordinary TLS leaf",
        tokens(a=world.token(under_leaf, 1, certificates=[under_leaf.tsa, leaf, a.intermediate, a.root]), b=tb3),
        {**low, "timing.tokens.0.reason": "PATH"},
    )
    flagged = P.Chain(a.root, a.intermediate, P.make_tsa(a.intermediate, "TSA with an unknown critical extension", unknown_critical=True))
    add(
        "a time-stamp certificate with an unknown critical extension",
        tokens(a=world.token(flagged, 1), b=tb3),
        {**low, "timing.tokens.0.reason": "CERT"},
    )
    weak_intermediate = P.make_intermediate(a.root, "Issuer without certificate signing", key_cert_sign=False, digital_signature=True)
    without_usage = P.Chain(a.root, weak_intermediate, P.make_tsa(weak_intermediate, "TSA under it"))
    add(
        "an intermediate certificate without the certificate-signing usage",
        tokens(a=world.token(without_usage, 1), b=tb3),
        {**low, "timing.tokens.0.reason": "PATH"},
    )

    early = B.make_world(tmp / "early", window_s=3)
    add(
        "tokens after the outcome window opened",
        source=early,
        root=early.root,
        expect={**low, f"timing.items.{ITEM_FC}.window_declared": True},
    )
    slow = B.make_world(tmp / "slow", gap_s=600)
    add(
        "older evidence is judged by its own commit time",
        source=slow,
        root=slow.root,
        expect={
            "timing.level": "C3",
            f"timing.items.{ITEM_EV}.level": "C1",
            f"timing.items.{ITEM_FC}.level": "C3",
            "timing.subjects": [ITEM_FC],
            "eligibility.scorable": True,
        },
    )

    bad_window = B.make_world(tmp / "badwindow", opens_at_override="tomorrow")
    add(
        "outcome window that is not a time",
        source=bad_window,
        root=bad_window.root,
        expect={**broken, f"integrity.items.{ITEM_FC}.error": "WINDOW", "integrity.self_consistent": False},
    )

    two = B.make_world(tmp / "twoforecasts", second_forecast_gap_s=600)
    add(
        "two forecasts, only the later one is timely",
        source=two,
        root=two.root,
        expect={
            "timing.level": "C1",
            f"timing.items.{ITEM_FC}.level": "C1",
            f"timing.items.{layout.item_stem(2)}.level": "C3",
            "timing.subjects": [ITEM_FC, layout.item_stem(2)],
            "eligibility.scorable": False,
        },
    )
    unlabelled = B.make_world(tmp / "unlabelled", evidence_without_mode=True)
    add(
        "one evidence item has no data mode",
        source=unlabelled,
        root=unlabelled.root,
        expect={
            "integrity.ok": True,
            "timing.level": "C3",
            "eligibility.data_modes": ["live", "missing"],
            "eligibility.scorable": False,
            "eligibility.reasons": ["DATA_MODE"],
        },
    )

    # --- links and raw payloads --------------------------------------------------------------------------------------
    bad_ref = B.make_world(tmp / "badref", ref_hash_override="0" * 64)
    add(
        "a reference that does not match the record it names",
        source=bad_ref,
        root=bad_ref.root,
        expect={**broken, "integrity.self_consistent": False, "integrity.refs.mismatched": [f"{ITEM_FC}->ev1"]},
    )

    def drop_evidence(d: Path) -> None:
        for suffix in (layout.CANONICAL_SUFFIX, layout.PROOF_SUFFIX):
            (d / layout.ITEMS / f"{ITEM_EV}{suffix}").unlink()

    def drop_forecast(d: Path) -> None:
        for suffix in (layout.CANONICAL_SUFFIX, layout.PROOF_SUFFIX):
            (d / layout.ITEMS / f"{ITEM_FC}{suffix}").unlink()

    add(
        # a bundle that holds the cycle's committing step shows what it holds: a record of the batch left out is noticed
        "a forecast left out of the bundle is noticed",
        drop_forecast,
        {
            "integrity.ok": True,
            "integrity.errors": [],
            "timing.level": "C3",
            "timing.subjects": [ITEM_EV, "item-00000002"],
            "cycle.declared": True,
            "cycle.members_present": 1,
            "cycle.complete": False,
            "eligibility.scorable": False,
            "eligibility.reasons": ["COMPLETENESS_NOT_SHOWN", "NO_FORECAST"],
        },
    )
    add(
        "evidence left out of the bundle",
        drop_evidence,
        {
            "integrity.ok": True,
            "integrity.refs.unresolved": [f"{ITEM_FC}->ev1"],
            "eligibility.scorable": False,
            "eligibility.reasons": ["UNRESOLVED_REFERENCE", "COMPLETENESS_NOT_SHOWN", "DATA_MODE"],
        },
    )
    # --- the whole batch: the cycle's committing step names the records before it, and the verifier recomputes their root ---
    add(
        "the cycle's whole batch",
        lambda d: None,
        {
            "integrity.ok": True,
            "cycle.declared": True,
            "cycle.batch_size": 2,
            "cycle.members_present": 2,
            "cycle.complete": True,
            "eligibility.forecasts": 1,
            "eligibility.scorable": True,
            "eligibility.reasons": [],
        },
    )
    bare = B.make_world(tmp / "nocycle", cycle_batch=False)
    add(
        "a bundle that holds no committing step",
        source=bare,
        root=bare.root,
        expect={
            "integrity.ok": True,
            "cycle.declared": False,
            "cycle.complete": False,
            "eligibility.scorable": False,
            "eligibility.reasons": ["COMPLETENESS_NOT_SHOWN"],
        },
    )
    for label, kwargs, members in (
        ("a committing step that names another root", {"batch_root_override": "0" * 64}, 2),
        ("a committing step that names more records than the log holds before it", {"batch_size_override": 5}, 2),
        ("a committing step that names fewer records than the batch", {"batch_size_override": 1}, 1),
    ):
        broken_cycle = B.make_world(tmp / label.replace(" ", "_"), **kwargs)
        add(
            label,
            source=broken_cycle,
            root=broken_cycle.root,
            expect={
                "integrity.ok": True,
                "cycle.declared": True,
                "cycle.members_present": members,
                "cycle.complete": False,
                "eligibility.scorable": False,
                "eligibility.reasons": ["COMPLETENESS_NOT_SHOWN"],
            },
        )

    def break_proof(stem: str) -> Callable[[Path], None]:
        return lambda d: edit_proof(d, stem, lambda p: TlogProof(p.index, p.path, p.note.replace(b"\n3\n", b"\n4\n", 1), p.extra))

    add(
        "a committing step whose proof fails",
        break_proof("item-00000002"),
        {"integrity.ok": False, "cycle.declared": True, "cycle.members_present": 0, "cycle.complete": False, "eligibility.scorable": False},
    )

    def break_path(stem: str) -> Callable[[Path], None]:
        def flip(p: TlogProof) -> TlogProof:
            first = bytes([p.path[0][0] ^ 1]) + p.path[0][1:]
            return TlogProof(p.index, (first, *p.path[1:]), p.note, p.extra)

        return lambda d: edit_proof(d, stem, flip)

    add(
        "a committing step whose inclusion proof fails",
        break_path("item-00000002"),
        {"integrity.ok": False, "cycle.declared": True, "cycle.members_present": 0, "cycle.complete": False, "eligibility.scorable": False},
    )
    add(
        "a member of the batch whose inclusion proof fails",
        break_path(ITEM_FC),
        {
            "integrity.ok": False,
            "cycle.declared": True,
            "cycle.members_present": 1,
            "cycle.complete": False,
            "eligibility.forecasts": 0,
            "eligibility.scorable": False,
        },
    )
    for label, kwargs, shown in (
        ("a batch of zero records", {"committing_body": {"batch_size": 0}}, 0),
        (
            "a batch of zero records that names the root of nothing",
            {"committing_body": {"batch_size": 0, "batch_root": merkle.MerkleTree().root().hex()}},
            0,
        ),
        ("a batch of a negative size", {"committing_body": {"batch_size": -2}}, -2),
        ("a batch size that is a boolean", {"committing_body": {"batch_size": True}}, None),
        ("a batch size that is text", {"committing_body": {"batch_size": "2"}}, None),
        ("a batch size that is a fraction of a record", {"committing_body": {"batch_size": "2.5"}}, None),
    ):
        hostile = B.make_world(tmp / label.replace(" ", "_"), **kwargs)  # type: ignore[arg-type]
        add(
            label,
            source=hostile,
            root=hostile.root,
            expect={
                "integrity.ok": True,
                "cycle.declared": True,
                "cycle.batch_size": shown,
                "cycle.complete": False,
                "eligibility.scorable": False,
                "eligibility.reasons": ["COMPLETENESS_NOT_SHOWN"],
            },
        )
    two_steps = B.make_world(tmp / "twosteps", second_committing=True)
    add(
        "a bundle that holds two committing steps shows no cycle as complete",
        source=two_steps,
        root=two_steps.root,
        expect={
            "integrity.ok": True,
            "cycle.declared": True,
            "cycle.steps": 2,
            "cycle.complete": False,
            "eligibility.reasons": ["COMPLETENESS_NOT_SHOWN"],
        },
    )
    no_call = B.make_world(tmp / "nocall", forecast=False)
    add(
        "a whole batch that holds no forecast",
        source=no_call,
        root=no_call.root,
        expect={
            "integrity.ok": True,
            "cycle.complete": True,
            "eligibility.forecasts": 0,
            "eligibility.scorable": False,
            "eligibility.reasons": ["NO_FORECAST"],
        },
    )
    three = B.make_world(tmp / "threerecords", second_forecast_gap_s=600)
    add(
        "two forecasts and their evidence, all in the batch",
        source=three,
        root=three.root,
        expect={"cycle.batch_size": 3, "cycle.members_present": 3, "cycle.complete": True},
    )
    cited = B.make_world(tmp / "cited", second_live_evidence=True)

    def drop_cited(d: Path) -> None:
        """Leave out the second evidence record, which the forecast cites, and keep the first: the data mode that remains is fine."""
        for stem in sorted(p.name.removesuffix(layout.CANONICAL_SUFFIX) for p in (d / layout.ITEMS).glob(f"*{layout.CANONICAL_SUFFIX}")):
            if canonical.parse_record((d / layout.ITEMS / f"{stem}{layout.CANONICAL_SUFFIX}").read_bytes()).get("id") == "ev2":
                for suffix in (layout.CANONICAL_SUFFIX, layout.PROOF_SUFFIX):
                    (d / layout.ITEMS / f"{stem}{suffix}").unlink()

    add(
        "a cited record left out while the others stand",
        drop_cited,
        {
            "integrity.ok": True,
            "timing.level": "C3",
            "eligibility.data_modes": ["live"],
            "eligibility.scorable": False,
            "eligibility.reasons": ["UNRESOLVED_REFERENCE", "COMPLETENESS_NOT_SHOWN"],
        },
        source=cited,
        root=cited.root,
    )
    add(
        "every cited record present",
        lambda d: None,
        {"integrity.ok": True, "integrity.refs.unresolved": [], "eligibility.scorable": True, "eligibility.reasons": []},
        source=cited,
        root=cited.root,
    )
    add(
        "raw payload altered",
        lambda d: (d / layout.RAW / B.RAW_HASH).write_bytes(b"altered"),
        {**broken, "integrity.self_consistent": False, "integrity.raw.mismatched": [B.RAW_HASH]},
    )
    add(
        "raw payload left out",
        lambda d: (d / layout.RAW / B.RAW_HASH).unlink(),
        {"integrity.ok": True, "integrity.raw.missing": [B.RAW_HASH], "integrity.raw.verified": []},
    )
    add(
        "raw file whose name is not its hash",
        lambda d: (d / layout.RAW / "stray").write_bytes(b"x"),
        {**broken, "integrity.raw.mismatched": ["stray"], "integrity.raw.unreferenced": ["stray"]},
    )

    def payload_as_directory(d: Path) -> None:
        (d / layout.RAW / B.RAW_HASH).unlink()
        (d / layout.RAW / B.RAW_HASH).mkdir()

    add(
        "a raw payload that is a directory",
        payload_as_directory,
        {**broken, "integrity.self_consistent": False, "integrity.raw.mismatched": [B.RAW_HASH], "integrity.raw.verified": []},
    )
    add(
        "a stray directory in the raw folder",
        lambda d: (d / layout.RAW / "subdir").mkdir(),
        {**broken, "integrity.raw.mismatched": ["subdir"], "integrity.raw.unreferenced": ["subdir"]},
    )
    extra = b"an unreferenced but honest payload"
    add(
        "an unreferenced raw payload that is honest",
        lambda d: (d / layout.RAW / hashlib.sha256(extra).hexdigest()).write_bytes(extra),
        {"integrity.ok": True, "integrity.raw.unreferenced": [hashlib.sha256(extra).hexdigest()]},
    )

    simulated = B.make_world(tmp / "simulated", data_mode="simulated")
    add(
        "evidence that is not live",
        source=simulated,
        root=simulated.root,
        expect={
            "integrity.ok": True,
            "timing.level": "C3",
            "eligibility.scorable": False,
            "eligibility.reasons": ["DATA_MODE"],
            "eligibility.data_modes": ["simulated"],
        },
    )

    # --- the shape of the bundle and its publication claims ---------------------------------------------------------
    add(
        "a badly named item",
        lambda d: (d / layout.ITEMS / "bad stem.canonical.json").write_bytes(b"{}"),
        {"integrity.self_consistent": False, "integrity.errors": ["BAD_STEM"]},
    )
    add(
        "an item name with a trailing newline",
        lambda d: (d / layout.ITEMS / "item-9\n.canonical.json").write_bytes(b"{}"),
        {"integrity.self_consistent": False, "integrity.errors": ["BAD_STEM"]},
    )
    add(
        "no items",
        lambda d: shutil.rmtree(d / layout.ITEMS),
        {"integrity.ok": False, "integrity.errors": ["NO_ITEMS"], "eligibility.scorable": False},
    )
    add(
        "an empty items directory",
        lambda d: [p.unlink() for p in (d / layout.ITEMS).iterdir()],
        {"integrity.ok": False, "integrity.errors": ["NO_ITEMS"]},
    )
    add("no publication claims", lambda d: (d / layout.PUBLICATION).unlink(), {**honest, "timing.public_commit.claimed": 0})
    add(
        "publication file that is not JSON",
        lambda d: (d / layout.PUBLICATION).write_text("{"),
        {**honest, "timing.public_commit.claimed": -1},
    )
    add(
        "publication file with NaN",
        lambda d: (d / layout.PUBLICATION).write_text('{"claims": [NaN]}'),
        {**honest, "timing.public_commit.claimed": -1},
    )
    add(
        "publication file whose claims are not a list",
        lambda d: (d / layout.PUBLICATION).write_text('{"claims": 5}'),
        {**honest, "timing.public_commit.claimed": -1},
    )
    add(
        "publication file that is not an object",
        lambda d: (d / layout.PUBLICATION).write_text("[1, 2]"),
        {**honest, "timing.public_commit.claimed": -1},
    )
    add(
        "publication claims never raise a level",
        lambda d: ((d / layout.PUBLICATION).write_text(json.dumps({"claims": [{}] * 5})), set_tokens(d, {"a": ta1})),
        {"timing.level": "C1", "timing.public_commit.claimed": 5, "timing.public_commit.authenticated_offline": 0},
    )

    # --- files at their size limits: 1 MiB for a record (VERIFIER_THREAT_MODEL section 4), what verify_token accepts for a token, and, as
    # no limit is stated for publication.json, the 1 MiB record limit ------------------------------------------------------
    def padded_object(size: int) -> bytes:
        return b'{"pad":"' + b"x" * (size - 10) + b'"}'

    add(
        "a record file at the size limit is read",
        lambda d: edit_record(d, ITEM_FC, lambda x: padded_object(MAX_RECORD_FILE)),
        fc("ENVELOPE"),
    )
    add(
        "a record file one byte over the size limit is refused",
        lambda d: edit_record(d, ITEM_FC, lambda x: padded_object(MAX_RECORD_FILE + 1)),
        {**fc("TOO_LARGE"), f"integrity.items.{ITEM_FC}.detail": "record file"},
    )
    add(
        "a token file at the size limit is read",
        tokens(a=ta1, b=tb3, c=b"\x00" * MAX_TOKEN_FILE),
        {"timing.level": "C3", "timing.tokens.2.reason": "PARSE"},
    )
    add(
        "a token file one byte over the size limit is refused",
        tokens(a=ta1, b=tb3, c=b"\x00" * (MAX_TOKEN_FILE + 1)),
        {"timing.level": "C3", "timing.tokens.2.reason": "SIZE"},
    )
    add(
        "a publication file at the size limit is read",
        lambda d: (d / layout.PUBLICATION).write_bytes(json_of_size(MAX_PUBLICATION_FILE, claims=2)),
        {**honest, "timing.public_commit.claimed": 2},
    )
    add(
        "a publication file one byte over the size limit is refused",
        lambda d: (d / layout.PUBLICATION).write_bytes(json_of_size(MAX_PUBLICATION_FILE + 1, claims=2)),
        {**honest, "timing.public_commit.claimed": -1},
    )

    # --- records committed by hand, as an honest writer of an earlier version would have done it ---------------------------------------
    def by_hand(name: str, expect: dict[str, object], **options: Any) -> None:
        cases.append(Case(name, committed_by_hand(tmp / f"hand-{len(cases)}", world, **options), root, "CURRENT", expect))

    on_time = {"integrity.ok": True, f"timing.items.{ITEM_FC}.error": None, f"timing.items.{ITEM_FC}.detail": None}
    by_hand(
        "committed by hand: an honest pair of records",
        {
            **on_time,
            "integrity.errors": [],
            "integrity.refs.resolved": 1,
            "timing.level": "C3",
            "eligibility.scorable": False,
            "eligibility.reasons": ["COMPLETENESS_NOT_SHOWN"],
        },
    )
    # D-NFC: U+105D2 U+0307 is NFC under Unicode 15.0 and composes to U+105C9 under 16.0 and later; the code point is outside the repertoire
    by_hand(
        "committed by hand: a record string that only a newer Unicode composes",
        {
            **broken,
            f"integrity.items.{ITEM_EV}.error": "CANONICAL",
            f"integrity.items.{ITEM_EV}.detail": UNASSIGNED_105D2,
            "integrity.errors": ["NO_CHECKPOINT"],
            "timing.level": "none",
        },
        note="\U000105d2\u0307",
    )
    # as_of <= created_at <= committed_at (contract section 2), and a token never precedes the record's own creation (VERIFICATION_SPEC
    # section 7), each within the 1 s local clock-offset limit that already opens the counting window (VERIFICATION_SPEC section 3, item 6)
    impossible = {
        "integrity.ok": True,
        "timing.level": "C1",
        f"timing.items.{ITEM_FC}.level": "C1",
        f"timing.items.{ITEM_FC}.error": IMPOSSIBLE_ORDER,
        f"timing.items.{ITEM_FC}.detail": CREATED_AFTER_COMMIT,
        f"timing.items.{ITEM_FC}.independent_operators": 0,
        f"timing.items.{ITEM_FC}.tokens": {"a": False, "b": False},
        f"timing.items.{ITEM_EV}.error": None,
        "eligibility.scorable": False,
        "eligibility.reasons": ["LEVEL_BELOW_C3", "COMPLETENESS_NOT_SHOWN"],
    }
    by_hand("committed by hand: a forecast created a day after its commit", impossible, forecast_created_at="2026-10-30T12:00:00.000Z")
    by_hand(
        "committed by hand: a forecast created 1 s after its commit",
        {**on_time, "timing.level": "C3", "eligibility.scorable": False, "eligibility.reasons": ["COMPLETENESS_NOT_SHOWN"]},
        forecast_created_at="2026-10-29T12:00:01.000Z",
    )
    by_hand("committed by hand: a forecast created 1.001 s after its commit", impossible, forecast_created_at="2026-10-29T12:00:01.001Z")
    # both verifiers read times to the millisecond (the Node verifier cannot read finer), so 1.0005 s is 1 s in both
    by_hand(
        "committed by hand: a forecast created 1.0005 s after its commit is judged to the millisecond",
        {**on_time, "timing.level": "C3", "eligibility.scorable": False, "eligibility.reasons": ["COMPLETENESS_NOT_SHOWN"]},
        forecast_created_at="2026-10-29T12:00:01.0005Z",
    )
    by_hand(
        "committed by hand: a token more than 1 s before the forecast was created does not count",
        {**on_time, "timing.level": "C1", f"timing.items.{ITEM_FC}.tokens": {"a": False, "b": True}, "eligibility.scorable": False},
        forecast_created_at="2026-10-29T12:00:01.000Z",
        token_offsets=(-1, 3),
    )
    by_hand(
        "committed by hand: a token 1 s before the forecast was created counts",
        {
            **on_time,
            "timing.level": "C3",
            f"timing.items.{ITEM_FC}.tokens": {"a": True, "b": True},
            "eligibility.scorable": False,
            "eligibility.reasons": ["COMPLETENESS_NOT_SHOWN"],
        },
        forecast_created_at="2026-10-29T12:00:01.000Z",
        token_offsets=(0, 3),
    )
    cases += build_later_cases(tmp)
    cases += build_settlement_cases(tmp)
    return cases


def _rewrite_consistency(directory: Path, change: Callable[[dict[str, Any]], None]) -> None:
    path = directory / layout.CONSISTENCY
    document = json.loads(path.read_bytes())
    change(document)
    path.write_bytes(canonical.dumps(document))


def _resign_later(directory: Path, world: B.World, signer_seed: int) -> None:
    """The later items proven under the same tree head but signed by another log key."""
    assert world.later_note is not None
    head = parse_text(split_note(world.later_note)[0])
    note = sign_checkpoint(Checkpoint(head.origin, head.size, head.root), [R.log_signer(signer_seed)])
    for seq in world.later_seqs:
        path = directory / layout.ITEMS / f"{layout.item_stem(seq)}{layout.PROOF_SUFFIX}"
        proof = parse_proof(path.read_bytes())
        path.write_bytes(format_proof(TlogProof(proof.index, proof.path, note)))


def _add_third_checkpoint(directory: Path, world: B.World) -> None:
    """One more record under a third checkpoint, written into the bundle: a bundle shows at most two."""
    from hoshitori import lifecycle

    step = lifecycle.Step(lifecycle.State.LEVEL, 1, level=lifecycle.Level.C3, reason="extra")
    world.ledger.submit(lifecycle.to_record(lifecycle.Transition("fc1", 99, step), B.SOURCE, 99))
    world.ledger.append_pending()
    seq = world.ledger.size - 1
    note = world.ledger.checkpoint()
    stem = layout.item_stem(seq)
    (directory / layout.ITEMS / f"{stem}{layout.CANONICAL_SUFFIX}").write_bytes(world.ledger.read_record(seq))
    proof = TlogProof(seq, tuple(world.ledger.inclusion_proof(seq, world.ledger.size)), note)
    (directory / layout.ITEMS / f"{stem}{layout.PROOF_SUFFIX}").write_bytes(format_proof(proof))


def build_settlement_cases(tmp: Path) -> list[Case]:
    """Cases for the settlement of a forecast in the later part (D01_VERIFIER_CHANGES, stage C part 3): the record that ends the call is read,
    held to the forecast it names by digest, to the step that cites it, to the directional rule (an outcome of 1 is a price above the first,
    0 below it, a tie is void) and to the time the call's window ends. Nothing here says the prices are the market's."""
    cases: list[Case] = []
    fc = "fc1"
    base = f"cycle.later.forecasts.{fc}"

    def world(name: str, kind: str | None, **kw: Any) -> B.World:
        return B.make_world(
            tmp / f"settle-{name}",
            later=True,
            later_open=True,
            later_ending=kind is not None,
            later_settlement=kind,
            body_extra=B.DIRECTIONAL,
            **kw,
        )

    def add(name: str, source: B.World, expect: dict[str, object]) -> None:
        cases.append(Case(name, source.copy_bundle(name), source.root, "CURRENT", expect))

    def settled(state: str, checks: list[str]) -> dict[str, object]:
        return {
            "cycle.later.shown": True,
            "cycle.later.failures": 0,
            f"{base}.settlement": "ok",
            f"{base}.settlement_state": state,
            f"{base}.settlement_checked": checks,
            f"{base}.ended": state,
            "eligibility.scorable": True,
        }

    full = ["names_forecast", "names_step", "outcome_from_prices", "after_window"]
    add("settlement: a call settled up whose outcome follows from its prices", world("up", "up"), settled("SETTLED", full))
    add("settlement: a call settled down whose outcome follows from its prices", world("down", "down"), settled("SETTLED", full))
    add("settlement: a tie is void for its tie, as the rule says", world("tie", "tie"), settled("VOID", full))
    add(
        "settlement: a void for another reason has no price to hold to the rule",
        world("gap", "void_gap"),
        settled("VOID", ["names_forecast", "names_step"]),
    )

    # a barrier call: the record must agree with itself (one of three outcomes, a first price, no outcome price, a whole-minute touch inside the
    # window exactly when a level was touched); which level was touched is for the oracle's candles, which the bundle does not hold
    def barrier(name: str, kind: str) -> B.World:
        return B.make_world(
            tmp / f"settle-{name}",
            later=True,
            later_open=True,
            later_ending=True,
            later_settlement=kind,
            body_extra=B.BARRIER_FORECAST,
        )

    barrier_full = ["names_forecast", "names_step", "barrier_touch_consistent", "after_window"]
    for kind, words in (("b_target", "target"), ("b_stop", "stop"), ("b_timeout", "timeout")):
        add(
            f"settlement: a barrier call that ended on its {words} agrees with itself",
            barrier(kind, kind),
            settled("SETTLED", barrier_full),
        )
    for kind, says in (
        ("b_timeout_touch", "a timeout that names a touch minute"),
        ("b_target_no_touch", "a touched level with no touch minute"),
        ("b_touch_at_end", "a touch minute at the end of the window"),
        ("b_touch_before", "a touch minute before the window"),
        ("b_touch_unaligned", "a touch minute that is not a whole minute"),
        ("b_no_p0", "a barrier call with no first price"),
        ("b_other_outcome", "an outcome that is none of the three"),
        ("b_with_price", "a barrier call with an outcome price"),
    ):
        add(
            f"settlement: {says} is refused",
            barrier(kind, kind),
            {
                "cycle.later.failures": 1,
                f"{base}.settlement": "mismatch",
                f"{base}.settlement_state": "SETTLED",
                "eligibility.scorable": False,
            },
        )
    none = world("none", None)
    add(
        "settlement: a call that has not ended has no settlement and is not faulted",
        none,
        {f"{base}.settlement": "none", f"{base}.settlement_state": None, "cycle.later.failures": 0},
    )

    def faulted(name: str, kind: str, says: str, state: str | None = "SETTLED", **kw: Any) -> None:
        add(
            f"settlement: {name}",
            world(f"fault-{len(cases)}-{kind}", kind, **kw),
            {"cycle.later.failures": 1, f"{base}.settlement": says, f"{base}.settlement_state": state, "eligibility.scorable": False},
        )

    faulted("an outcome that is the other way from its prices (up)", "wrong_up", "mismatch")
    faulted("a state that is not one a settlement ends in", "bad_state", "malformed", None)
    faulted("a member that is not text or nothing", "number_outcome", "malformed", None)
    faulted("a contract that is not text", "number_contract", "malformed", None)
    faulted("a member the settlement does not have", "extra_member", "malformed", None)
    faulted("a settlement that names a record other than its forecast", "ref_other", "mismatch")
    faulted("a settlement of another contract than its forecast's", "wrong_contract", "mismatch")
    faulted("a settled call with no first price", "no_prices", "mismatch")
    faulted("a settled call whose first price is zero", "zero_price", "mismatch")
    faulted("an outcome that is the other way from its prices (down)", "wrong_down", "mismatch")
    faulted("a tie that is settled and not void", "tie_settled", "mismatch")
    faulted("a void for a tie whose prices are not equal", "tie_wrong", "mismatch", "VOID")
    faulted("a settlement written before the call's window ended", "up", "early", later_due_after_s=-1)
    faulted("a step that cites a settlement the bundle does not hold", "absent", "absent", None)
    faulted("a step that cites another settlement than the record there is", "id_mismatch", "mismatch")
    add(
        "settlement: a settlement that names its forecast by another digest fails the integrity of the references, and no later part is shown",
        world("bad-ref", "bad_ref"),
        {"integrity.ok": False, "cycle.later.shown": False, "eligibility.scorable": False},
    )
    faulted("a settlement with a member missing", "malformed", "malformed", None)
    faulted("two settlements for one call", "conflicting", "conflicting", None)
    return cases


def build_later_cases(tmp: Path) -> list[Case]:
    """Cases for a bundle with a later part: records the log appended after the stamped checkpoint, a later signed checkpoint and the
    consistency proof between them (D01_VERIFIER_CHANGES, stage C parts 2 and 3)."""
    cases: list[Case] = []
    world = B.make_world(tmp / "later-world", later=True, later_open=True, later_ending=True)
    root = world.root
    later_stems = [layout.item_stem(s) for s in world.later_seqs]
    fc = "fc1"

    def add(
        name: str,
        change: Callable[[Path], None] | None = None,
        expect: dict[str, object] | None = None,
        *,
        source: B.World | None = None,
    ) -> None:
        directory = (source or world).copy_bundle(name)
        if change is not None:
            change(directory)
        cases.append(Case(name, directory, root, "CURRENT", expect or {}))

    honest = {
        "integrity.ok": True,
        "integrity.self_consistent": True,
        "integrity.one_checkpoint": False,
        "integrity.errors": [],
        "integrity.checkpoints.consistency": "verified",
        "integrity.checkpoints.stamped.size": world.stamped_size,
        "integrity.checkpoints.later.size": world.ledger.size,
        "timing.level": "C3",
        "timing.later_items": later_stems,
        **{f"timing.items.{stem}": "<absent>" for stem in later_stems},  # a later record is not judged by the token window
        "cycle.complete": True,
        "cycle.later.shown": True,
        "cycle.later.failures": 0,
        f"cycle.later.forecasts.{fc}.committed_level": "C3",
        f"cycle.later.forecasts.{fc}.awaiting": "ok",
        f"cycle.later.forecasts.{fc}.origin": "ok",
        f"cycle.later.forecasts.{fc}.ended": "SETTLED",
        "eligibility.scorable": True,
    }
    add("later part: the later steps of a forecast are read and checked", expect=honest)
    bad_proof = {
        "integrity.ok": False,
        "integrity.checkpoints.consistency": "failed",
        "integrity.errors": ["CONSISTENCY_FAILED"],
        "eligibility.scorable": False,
    }

    def flip(document: dict[str, Any]) -> None:
        first = document["hashes"][0]
        document["hashes"][0] = ("1" if first[0] == "0" else "0") + first[1:]

    add("later part: one hash of the consistency proof changed", lambda d: _rewrite_consistency(d, flip), bad_proof)

    def swap(document: dict[str, Any]) -> None:
        document["from_size"], document["to_size"] = document["to_size"], document["from_size"]

    add("later part: the sizes of the consistency proof swapped", lambda d: _rewrite_consistency(d, swap), bad_proof)
    add("later part: the consistency proof one hash short", lambda d: _rewrite_consistency(d, lambda x: x["hashes"].pop()), bad_proof)
    add(
        "later part: the consistency proof one hash long",
        lambda d: _rewrite_consistency(d, lambda x: x["hashes"].append("0" * 64)),
        bad_proof,
    )
    add(
        "later part: the consistency file is missing",
        lambda d: (d / layout.CONSISTENCY).unlink(),
        {
            "integrity.ok": False,
            "integrity.checkpoints.consistency": "missing",
            "integrity.errors": ["CHECKPOINTS_DIFFER"],
            "eligibility.scorable": False,
        },
    )
    add(
        "later part: the consistency file is not JSON",
        lambda d: (d / layout.CONSISTENCY).write_bytes(b"not json"),
        {
            "integrity.ok": False,
            "integrity.checkpoints.consistency": "failed",
            "integrity.errors": ["CONSISTENCY_FAILED"],
            "eligibility.scorable": False,
        },
    )
    add(
        "later part: a third checkpoint in the bundle",
        lambda d: _add_third_checkpoint(d, world),
        {"integrity.ok": False, "integrity.errors": ["CHECKPOINTS_DIFFER"], "eligibility.scorable": False},
    )
    add(
        "later part: the later checkpoint signed by a key the root does not name",
        lambda d: _resign_later(d, world, 99),
        {"integrity.ok": False, "integrity.checkpoints.consistency": "failed", "eligibility.scorable": False},
    )
    add(
        "later part: the tokens stamp the later checkpoint and not the first",
        lambda d: _tokens_over(d, world, hashlib.sha256(split_note(world.later_note or b"")[0]).digest()),
        {"integrity.ok": True, "timing.level": "C1", "eligibility.scorable": False, "eligibility.reasons.0": "LEVEL_BELOW_C3"},
    )

    def edit_later(directory: Path) -> None:
        edit_record(directory, later_stems[0], lambda x: x.replace(b'"state":"COMMITTED"', b'"state":"COMMITTEX"'))

    add(
        "later part: a later record edited",
        edit_later,
        {"integrity.ok": False, f"integrity.items.{later_stems[0]}.error": "INCLUSION", "eligibility.scorable": False},
    )
    early = {"integrity.ok": True, "timing.level": "C3", "cycle.later.failures": 1, "eligibility.scorable": False}
    for label, options, facts in (
        (
            "the eligible time one millisecond before the derived one",
            {"later_eligible_delta_ms": -1, "later_open": True},
            {
                f"cycle.later.forecasts.{fc}.awaiting": "early",
                f"cycle.later.forecasts.{fc}.origin": "wrong",  # an origin taken from an eligible time that is itself too early
                "eligibility.reasons": ["ORIGIN_BEFORE_ATTESTATION"],
            },
        ),
        (
            "the origin one minute before the derived one",
            {"later_open": True, "later_origin_delta_ms": -60_000},
            {f"cycle.later.forecasts.{fc}.origin": "early", "eligibility.reasons": ["ORIGIN_BEFORE_ATTESTATION"]},
        ),
        (
            "the origin one minute after the next close",
            {"later_open": True, "later_origin_delta_ms": 60_000},
            {f"cycle.later.forecasts.{fc}.origin": "wrong", "eligibility.reasons": ["ORIGIN_BEFORE_ATTESTATION"]},
        ),
    ):
        world2 = B.make_world(tmp / f"later-{label[:12]}-{len(cases)}", later=True, **options)  # type: ignore[arg-type]
        directory = world2.copy_bundle(f"later-{len(cases)}")
        cases.append(Case(f"later part: {label}", directory, world2.root, "CURRENT", {**early, **facts}))

    add(
        "later part: a record of the stamped group edited, so the later part is not shown",
        lambda d: edit_record(d, ITEM_FC, lambda x: x.replace(b'"horizon":"24h"', b'"horizon":"25h"')),
        {
            "integrity.ok": False,
            "integrity.checkpoints.consistency": "verified",
            "cycle.later.shown": False,
            "cycle.later.failures": 0,
            "eligibility.scorable": False,
        },
    )

    def _not_canonical(directory: Path) -> None:
        document = json.loads((directory / layout.CONSISTENCY).read_bytes())
        (directory / layout.CONSISTENCY).write_text(json.dumps(document, indent=1, sort_keys=True), encoding="utf-8")

    consistency_failed = {"integrity.ok": False, "integrity.checkpoints.consistency": "failed", "integrity.errors": ["CONSISTENCY_FAILED"]}
    add("later part: the consistency file is valid JSON but not canonical", _not_canonical, consistency_failed)
    add(
        "later part: the consistency file has another member",
        lambda d: _rewrite_consistency(d, lambda x: x.update(extra=1)),
        consistency_failed,
    )
    add(
        "later part: the consistency file is of another format",
        lambda d: _rewrite_consistency(d, lambda x: x.update(format=2)),
        consistency_failed,
    )
    add(
        "later part: a consistency hash is not hexadecimal",
        lambda d: _rewrite_consistency(d, lambda x: x["hashes"].__setitem__(0, "zz")),
        consistency_failed,
    )
    add(
        "later part: a consistency hash in upper case",
        lambda d: _rewrite_consistency(d, lambda x: x["hashes"].__setitem__(0, x["hashes"][0].upper())),
        consistency_failed,
    )
    add("later part: a consistency size of zero", lambda d: _rewrite_consistency(d, lambda x: x.update(from_size=0)), consistency_failed)
    variants = (
        ("the waiting step missing", {"later_open": True, "later_skip_awaiting": True}, {"awaiting": "absent", "origin": "wrong"}, 1),
        (
            "the waiting step written twice",
            {"later_open": True, "later_duplicate": "AWAITING_ORIGIN"},
            {"awaiting": "conflicting", "origin": "wrong"},
            1,
        ),
        ("the opening step written twice", {"later_open": True, "later_duplicate": "OPEN"}, {"awaiting": "ok", "origin": "conflicting"}, 1),
        (
            "the waiting step with an eligible time that is not a time",
            {"later_open": True, "later_bad_eligible": True},
            {"awaiting": "malformed", "origin": "wrong"},
            1,
        ),
        (
            "the committing step written twice",
            {"later_open": True, "later_duplicate": "COMMITTED"},
            {"awaiting": "ok", "origin": "ok", "committed_level": None},
            0,
        ),
        (
            "the settlement written twice",
            {"later_open": True, "later_ending": True, "later_duplicate": "SETTLED"},
            {"awaiting": "ok", "origin": "ok", "ended": "conflicting"},
            0,
        ),
    )
    for label, options, facts, failures in variants:
        world_v = B.make_world(tmp / f"later-v{len(cases)}", later=True, **options)  # type: ignore[arg-type]
        expect = {f"cycle.later.forecasts.{fc}.{k}": v for k, v in facts.items()} | {"cycle.later.failures": failures, "integrity.ok": True}
        if failures:
            expect |= {"eligibility.scorable": False, "eligibility.reasons": ["ORIGIN_BEFORE_ATTESTATION"]}
        cases.append(Case(f"later part: {label}", world_v.copy_bundle(f"later-{len(cases)}"), world_v.root, "CURRENT", expect))
    world_c1 = B.make_world(tmp / "later-c1", later=True, later_open=True, tokens_offsets=(100, 101))
    cases.append(
        Case(
            "later part: the tokens too late for the window leave nothing to derive an origin from",
            world_c1.copy_bundle("later-c1"),
            world_c1.root,
            "CURRENT",
            {
                "timing.level": "C1",
                f"cycle.later.forecasts.{fc}.awaiting": "unchecked",
                f"cycle.later.forecasts.{fc}.origin": "unchecked",
                f"cycle.later.forecasts.{fc}.derived_eligible_after": None,
                "cycle.later.failures": 0,
                "eligibility.scorable": False,
                "eligibility.reasons.0": "LEVEL_BELOW_C3",
            },
        )
    )
    world3 = B.make_world(tmp / "later-no-steps", later=True, later_open=False)
    cases.append(
        Case(
            "later part: a forecast with its steps but no origin yet",
            world3.copy_bundle("later-no-open"),
            world3.root,
            "CURRENT",
            {f"cycle.later.forecasts.{fc}.awaiting": "ok", f"cycle.later.forecasts.{fc}.origin": "absent", "eligibility.scorable": True},
        )
    )
    return cases


def _tokens_over(directory: Path, world: B.World, digest: bytes) -> None:
    for name, chain, offset in (("a", world.chain_a, 1), ("b", world.chain_b, 3)):
        (directory / layout.TSA / f"{name}{layout.TOKEN_SUFFIX}").write_bytes(world.token(chain, offset, digest=digest))


def build_special_cases(tmp: Path) -> list[Case]:
    """Bundle files that are not regular files, or far larger than any valid file.

    A FIFO or a link to a device reports size 0, and a FIFO that no process writes blocks an ordinary open for ever; each must be refused
    from its opened descriptor without being read. These cases are not recorded for the Node replay (a FIFO cannot be stored as a file
    map), so the differential test runs both verifiers on these directories as they are.
    """
    world = B.make_world(tmp / "special-world")
    root = world.root
    cases: list[Case] = []
    fc_record = layout.ITEMS + "/" + f"{ITEM_FC}{layout.CANONICAL_SUFFIX}"
    fc_proof = layout.ITEMS + "/" + f"{ITEM_FC}{layout.PROOF_SUFFIX}"
    third_token = layout.TSA + "/" + f"c{layout.TOKEN_SUFFIX}"
    refused_record = {
        "integrity.ok": False,
        f"integrity.items.{ITEM_FC}.error": "UNREADABLE",
        f"integrity.items.{ITEM_FC}.detail": NOT_REGULAR,
    }
    refused_proof = {
        "integrity.ok": False,
        f"integrity.items.{ITEM_FC}.error": "MISSING_PROOF",
        f"integrity.items.{ITEM_FC}.detail": f"{ITEM_FC}{layout.PROOF_SUFFIX}",
    }
    third_unreadable = {"integrity.ok": True, "timing.level": "C3", "timing.tokens.2.name": "c", "timing.tokens.2.reason": "UNREADABLE"}

    def special(name: str, where: str, kind: str, expect: dict[str, object]) -> None:
        directory = world.copy_bundle(f"special-{len(cases)}")
        target = directory / where
        target.unlink(missing_ok=True)
        fifos: list[Path] = []
        peak: int | None = None
        if kind == "fifo":
            os.mkfifo(target)
            fifos.append(target)
        elif kind == "null":
            target.symlink_to(os.devnull)
        else:  # a sparse regular file of 64 MiB: nothing of it may be read
            with open(target, "wb") as handle:
                handle.truncate(64 * 1024 * 1024)
            peak = 8 * 1024 * 1024
        cases.append(Case(name, directory, root, "CURRENT", expect, fifos, peak))

    if hasattr(os, "mkfifo"):
        special("a record file that is a FIFO no process writes", fc_record, "fifo", refused_record)
        special("a proof file that is a FIFO no process writes", fc_proof, "fifo", refused_proof)
        special("a token that is a FIFO no process writes", third_token, "fifo", third_unreadable)
        special(
            "publication.json that is a FIFO no process writes",
            layout.PUBLICATION,
            "fifo",
            {"integrity.ok": True, "timing.level": "C3", "timing.public_commit.claimed": -1},
        )
    special("a record file that links to the null device", fc_record, "null", refused_record)
    special("a proof file that links to the null device", fc_proof, "null", refused_proof)
    special("a token that links to the null device", third_token, "null", third_unreadable)
    special(
        "publication.json that links to the null device",
        layout.PUBLICATION,
        "null",
        {"integrity.ok": True, "timing.public_commit.claimed": -1},
    )
    special(
        "a 64 MiB record file",
        fc_record,
        "sparse",
        {"integrity.ok": False, f"integrity.items.{ITEM_FC}.error": "TOO_LARGE", f"integrity.items.{ITEM_FC}.detail": "record file"},
    )
    special(
        "a 64 MiB proof file",
        fc_proof,
        "sparse",
        {"integrity.ok": False, f"integrity.items.{ITEM_FC}.error": "PROOF", f"integrity.items.{ITEM_FC}.detail": "proof file too large"},
    )
    special("a 64 MiB token", third_token, "sparse", {"integrity.ok": True, "timing.level": "C3", "timing.tokens.2.reason": "SIZE"})
    special("a 64 MiB publication.json", layout.PUBLICATION, "sparse", {"integrity.ok": True, "timing.public_commit.claimed": -1})
    world.ledger.close()
    return cases


def limits(mod: types.ModuleType, cases: list[Case]) -> list[str]:
    """The size limits, with the module's own constants lowered for the duration of the check."""
    honest = next(c for c in cases if c.name == "honest")
    bad: list[str] = []
    saved_items, saved_tokens, saved_raw = mod.MAX_ITEMS, mod.MAX_TOKENS, mod.MAX_RAW_BYTES
    try:
        mod.MAX_ITEMS = 1
        verdict = mod.verify_bundle(honest.directory, honest.root)
        if "TOO_MANY_ITEMS" not in verdict["integrity"]["errors"] or verdict["integrity"]["ok"] or len(verdict["integrity"]["items"]) != 1:
            bad.append(f"too many items: {verdict['integrity']['errors']}")
        mod.MAX_ITEMS = saved_items
        mod.MAX_TOKENS = 1
        verdict = mod.verify_bundle(honest.directory, honest.root)
        reasons = [t["reason"] for t in verdict["timing"]["tokens"]]
        if reasons != [None, "TOO_MANY"] or verdict["timing"]["level"] != "C1":
            bad.append(f"too many tokens: {reasons}, level {verdict['timing']['level']}")
        mod.MAX_TOKENS = saved_tokens
        mod.MAX_RAW_BYTES = 1
        verdict = mod.verify_bundle(honest.directory, honest.root)
        if verdict["integrity"]["raw"]["mismatched"] != [B.RAW_HASH] or verdict["integrity"]["raw"]["verified"]:
            bad.append("a raw payload over the size limit is not refused")
    finally:
        mod.MAX_ITEMS, mod.MAX_TOKENS, mod.MAX_RAW_BYTES = saved_items, saved_tokens, saved_raw
    missing = mod.verify_bundle(honest.directory / "does-not-exist", honest.root)
    if missing["integrity"]["errors"] != ["NO_ITEMS"] or missing["integrity"]["ok"]:
        bad.append("a missing directory is not reported as an empty bundle")
    first = json.dumps(mod.verify_bundle(honest.directory, honest.root), sort_keys=True)
    second = json.dumps(mod.verify_bundle(honest.directory, honest.root), sort_keys=True)
    if first != second:
        bad.append("the verdict is not a pure function of its inputs")
    return bad


def runtime_refusal(mod: types.ModuleType, cases: list[Case]) -> list[str]:
    """On a runtime whose Unicode is older than 15.0 the verifier gives no verdict at all, whatever the bundle holds (finding D-NFC)."""
    honest = next(c for c in cases if c.name == "honest")
    engine = mod.canonical
    if not hasattr(engine, "RUNTIME_UNICODE") or not hasattr(engine, "UnicodeVersionError"):
        return ["the verifier does not check the Unicode version of the runtime it runs on"]
    bad: list[str] = []
    saved = engine.RUNTIME_UNICODE
    try:
        engine.RUNTIME_UNICODE = "14.0.0"
        for label, directory in (("an honest bundle", honest.directory), ("a missing directory", honest.directory / "does-not-exist")):
            try:
                mod.verify_bundle(directory, honest.root)
                got = "<a verdict>"
            except engine.UnicodeVersionError as exc:
                got = str(exc)
            except Exception as exc:
                got = f"<raised {type(exc).__name__}: {exc}>"
            if got != RUNTIME_TOO_OLD:
                bad.append(f"{label} on a runtime with Unicode 14.0: {got!r}, expected the refusal {RUNTIME_TOO_OLD!r}")
    finally:
        engine.RUNTIME_UNICODE = saved
    return bad


def failures(mod: types.ModuleType, cases: list[Case]) -> list[str]:
    bad: list[str] = []
    for case in cases:
        try:
            bad += check(mod, case)
        except Exception as exc:
            bad.append(f"{case.name}: raised {type(exc).__name__}: {exc}")
    bad += limits(mod, cases)
    bad += runtime_refusal(mod, cases)
    return bad
