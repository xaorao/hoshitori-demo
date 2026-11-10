"""Judging the stored time-stamp answers again: what an attestation said, rebuilt from the capture store alone.

A batch is committed in one step (``commit.commit_batch``): its records are appended, one checkpoint is signed over them and the time-stamp
authorities are asked to stamp that checkpoint's digest. The gateway keeps every answer it receives as a capture whose request names the
operation (``stamp``) and its arguments (the digest and the nonce). A process that was killed after the answers were kept and before the
cycle's steps were written has the answers and not the verdict, so recovery reads them back and asks ``tsa.verify_token`` and
``timing.assess`` the same questions the live run asked: the verdict is a function of the digest, the commit time, the policy and the rules,
and this module changes none of the four.

Only an answer the gateway would have accepted is a stamp (the authority granted it); an answer that failed the policy is kept with its
reason and counts for nothing, exactly as in the live attestation. When two answers of one authority exist for one digest (a retry after
the first was lost) the newest granted one is the one that counts, as the live run takes the one it was given. Nothing here reads a
clock, a ledger or the network.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from hoshitori import canonical, commitment_store
from hoshitori.attest import Attestation, AttestError, Stamp
from hoshitori.capture import CaptureStore, RawCapture
from hoshitori.checkpoint import parse_text
from hoshitori.commit import Commitment
from hoshitori.ledger import Ledger
from hoshitori.note import split_note
from hoshitori.timeline import from_ms, to_ms
from hoshitori.timeutil import parse_rfc3339
from hoshitori.timing import LEVEL_C3, TokenEvidence, assess
from hoshitori.trusted_root import TimeStampRules
from hoshitori.tsa import TokenError, TokenPolicy, verify_token
from hoshitori.tsa_gateway import OPERATION, Authority, TsaAdapter

LOOK_BACK_MS: Final = 3_600_000
PROVIDER_PREFIX: Final = "tsa-"
_PLACEHOLDER_URL: Final = "https://rejudge.invalid/"


@dataclass(frozen=True)
class Judged:
    """What the stored answers support for one batch: the attestation, the rules it is judged by (each record of the batch is judged at its
    own commit time) and the id of the commitment (the signed checkpoint the answers stamped)."""

    attestation: Attestation
    rules: TimeStampRules
    commitment_id: str

    def level_at(self, committed_at_ms: int) -> str:
        return self.attestation.timing_at(committed_at_ms, self.rules).level


def _granted(capture: RawCapture) -> bool:
    """Whether the gateway took this answer for a granted time-stamp: the adapter's own judgement of a capture."""
    adapter = TsaAdapter(Authority(capture.source[len(PROVIDER_PREFIX) :], _PLACEHOLDER_URL))
    return adapter.classify(capture, capture.received_at) is None


def _is_stamp_of(capture: RawCapture, digest: bytes) -> int | None:
    """The nonce of the request if this capture is an answer to a stamp request for ``digest``, else None."""
    if not capture.source.startswith(PROVIDER_PREFIX) or capture.request.get("operation") != OPERATION:
        return None
    args = capture.request.get("arguments")
    if not isinstance(args, dict) or args.get("digest") != digest.hex():
        return None
    nonce = args.get("nonce")
    if isinstance(nonce, bool) or not isinstance(nonce, int):
        return None
    return nonce


def attestation_from_captures(
    captures: CaptureStore, digest: bytes, committed_at_ms: int, *, policy: TokenPolicy, rules: TimeStampRules
) -> Attestation | None:
    """The attestation the stored answers to ``digest`` support, judged against ``committed_at_ms``; None when no authority's granted answer
    to this digest was kept within an hour before the commit time."""
    if len(digest) != 32:
        raise AttestError("the digest is a SHA-256 value (32 bytes)")
    oldest = from_ms(committed_at_ms - LOOK_BACK_MS)
    newest: dict[str, tuple[RawCapture, int]] = {}
    for capture_id in reversed(captures.ids()):
        capture = captures.get(capture_id)
        if capture.received_at < oldest:
            break  # the store is in the order captures were written: nothing before this is within the look-back
        nonce = _is_stamp_of(capture, digest)
        if nonce is None or capture.source in newest or not _granted(capture):
            continue
        newest[capture.source] = (capture, nonce)
    if not newest:
        return None
    stamps: list[Stamp] = []
    evidence: list[TokenEvidence] = []
    for source, (capture, nonce) in newest.items():
        name = source[len(PROVIDER_PREFIX) :]
        stamps.append(Stamp(name, nonce, capture.body))
        try:
            evidence.append(TokenEvidence(name, verify_token(capture.body, digest, policy, nonce=nonce)))
        except TokenError as exc:
            evidence.append(TokenEvidence(name, None, f"{exc.code}: {exc}"))
    timing = assess(from_ms(committed_at_ms), None, evidence, rules)
    return Attestation(
        digest,
        committed_at_ms,
        committed_at_ms + rules.close_after_commit_s * 1000,
        tuple(sorted(stamps, key=lambda stamp: stamp.authority)),
        (),
        (),
        timing,
        rules.u_constant_s * 1000,
        tuple(evidence),
    )


def _signed_notes(directory: Path) -> list[tuple[int, bytes]]:
    """Every checkpoint the log signed, smallest first, as the ledger wrote them."""
    path = directory / "checkpoints.jsonl"
    if not path.exists():
        return []
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        found.append((int(entry["size"]), base64.b64decode(entry["note"])))
    return sorted(found)


def _is_registered(directory: Path, size: int, root: bytes) -> bool:
    path = directory / "attested.jsonl"
    if not path.exists():
        return False
    entries = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    return any(entry["size"] == size and entry["root"] == root.hex() for entry in entries)


class Rejudger:
    """What recovery asks of the stored stamps: the checkpoint that stamped a cycle's batch, found among the log's own, and what the answers
    kept for it support.

    The batch's checkpoint is the smallest one the log signed that holds every forecast of the cycle and whose digest some authority's
    granted answer names; its commit time is the last record's in it (the ledger's clock does not run backwards, so that is the time the
    live run stamped against). A checkpoint that reached C3 and was not registered as attested is registered here, and the commitment is
    saved for a bundle when the store does not hold it: both are what the live run does after the stamps, which a killed process did not.
    """

    def __init__(self, ledger: Ledger, captures: CaptureStore, *, policy: TokenPolicy, rules: TimeStampRules, store: Path | None) -> None:
        self._ledger = ledger
        self._captures = captures
        self._policy = policy
        self._rules = rules
        self._store = store

    def __call__(self, forecasts: Sequence[Mapping[str, Any]]) -> Judged | None:
        seqs: list[int] = []
        for record in forecasts:
            seq = self._ledger.find(str(record["source"]), str(record["id"]))
            if seq is None:
                return None  # a forecast the log does not hold has no batch to find
            seqs.append(seq)
        if not seqs:
            return None
        floor = max(seqs)
        for size, note in _signed_notes(self._ledger.directory):
            if size <= floor:
                continue
            text, _ = split_note(note)
            last = canonical.parse_record(self._ledger.read_record(size - 1))
            committed = to_ms(parse_rfc3339(str(last["committed_at"])))
            attestation = attestation_from_captures(
                self._captures, hashlib.sha256(text).digest(), committed, policy=self._policy, rules=self._rules
            )
            if attestation is None:
                continue
            checkpoint = parse_text(text)
            commitment = Commitment(note, checkpoint.size, checkpoint.root, (), attestation)
            self._keep(commitment)
            return Judged(attestation, self._rules, commitment.commitment_id)
        return None

    def _keep(self, commitment: Commitment) -> None:
        """Register a C3 checkpoint as attested and save the commitment, each only if it is not done already."""
        if commitment.attestation.level == LEVEL_C3 and not _is_registered(self._ledger.directory, commitment.size, commitment.root):
            evidence = "rfc3161:" + ",".join(sorted(commitment.attestation.timing.chosen))
            self._ledger.register_attested(commitment.size, commitment.root, evidence)
        if self._store is not None and not (self._store / commitment.commitment_id).exists():
            commitment_store.save(self._store, commitment)
