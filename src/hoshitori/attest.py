"""Independent time-stamp tokens for a checkpoint: ask the authorities, verify what comes back, and say what level it supports.

Contract sections 2, 8 and 25 are the rules. A commitment is only as independent as the clocks that stamped it, so the checkpoint that holds
a batch is sent to several authorities at once through the gateway's attestation class, each with a fresh nonce:

* **Everything verified, nothing believed.** The answer of an authority is kept as it came; it counts only if ``tsa.verify_token`` accepts
  it for this digest and this nonce against the pinned roots of the trusted root, and ``timing.assess`` then decides which tokens are in
  the counting window, which come from independent operators and whether they agree. A token that fails is listed with its reason and
  still stored: an auditor may want to see it.
* **The phase has an end.** It closes when two tokens from distinct operators count, or when the retry window after the commitment has
  passed, whichever is first. A token that arrives later is not waited for and changes nothing; an authority not waited for because
  the level was already reached is listed as unfinished, not as a failure.
* **The level is a result, not a default.** ``C3`` if the tokens support it, otherwise ``C1`` with the reason in the failures; the
  forecast carries the level it reached.
* **The origin follows the close.** The scored origin is the first one-minute close after the attested time plus the uncertainty; with no
  counted token it is the first after the commit time itself, and the forecast says so by its level.
"""

from __future__ import annotations

import secrets
from concurrent.futures import FIRST_COMPLETED, Future, wait
from dataclasses import dataclass
from typing import Final, cast

from hoshitori.capture import CaptureStore
from hoshitori.clock import Clock
from hoshitori.gateway import Call, CallResult, Outcome
from hoshitori.governor import Priority
from hoshitori.runner import Runner
from hoshitori.timeline import eligible_after, from_ms, origin, to_ms
from hoshitori.timing import LEVEL_C3, TimingResult, TokenEvidence, assess
from hoshitori.trusted_root import TimeStampRules
from hoshitori.tsa import TokenError, TokenPolicy, verify_token
from hoshitori.tsa_gateway import OPERATION, Authority

MAX_DETAIL: Final = 200


class AttestError(ValueError):
    """The attestation is set up wrongly."""


@dataclass(frozen=True)
class Stamp:
    """What an authority sent back, kept as it came."""

    authority: str
    nonce: int
    response: bytes


@dataclass(frozen=True)
class StampFailure:
    authority: str
    detail: str


@dataclass(frozen=True)
class Attestation:
    digest: bytes
    committed_at_ms: int
    closes_at_ms: int
    stamps: tuple[Stamp, ...]
    failures: tuple[StampFailure, ...]
    unfinished: tuple[str, ...]  # authorities not waited for, because the tokens that counted were enough
    timing: TimingResult
    uncertainty_ms: int
    evidence: tuple[TokenEvidence, ...]  # every answer, verified or rejected, as the judge saw it

    def timing_at(self, committed_at_ms: int, rules: TimeStampRules) -> TimingResult:
        """The same tokens judged against another record's commit time (every record of a batch has its own)."""
        return assess(from_ms(committed_at_ms), None, self.evidence, rules)

    @property
    def level(self) -> str:
        return self.timing.level

    @property
    def attested_at_ms(self) -> int | None:
        return None if self.timing.attested_at is None else to_ms(self.timing.attested_at)

    @property
    def eligible_after_ms(self) -> int:
        """The instant before which nothing derived from the commitment may start: the attested time plus the uncertainty and the spread
        of the tokens, or the commit time itself when no token counts."""
        attested = self.attested_at_ms
        if attested is None:
            return self.committed_at_ms
        return eligible_after(attested, self.uncertainty_ms, round(cast(float, self.timing.spread_s) * 1000))

    @property
    def origin_ms(self) -> int:
        return origin(self.eligible_after_ms)

    def tokens(self) -> dict[str, bytes]:
        """The stored answers by authority name, for a verification bundle."""
        return {stamp.authority: stamp.response for stamp in self.stamps}


def _detail(text: str) -> str:
    return " ".join(text.split())[:MAX_DETAIL]


def attest(
    runner: Runner,
    captures: CaptureStore,
    clock: Clock,
    authorities: list[Authority],
    digest: bytes,
    *,
    committed_at_ms: int,
    policy: TokenPolicy,
    rules: TimeStampRules,
    caller: str = "ledger",
) -> Attestation:
    """Ask every authority to stamp ``digest`` and judge the answers against the commit time, within the attestation phase."""
    if len(digest) != 32:
        raise AttestError("the digest is a SHA-256 value (32 bytes)")
    if not authorities or len({a.name for a in authorities}) != len(authorities):
        raise AttestError("authorities are named, and each is named once")
    closes_ms = committed_at_ms + rules.close_after_commit_s * 1000
    deadline = from_ms(closes_ms)
    nonces = {a.name: secrets.randbits(52) + 1 for a in authorities}
    pending: dict[Future[CallResult], Authority] = {
        runner.submit(Call(a.provider, OPERATION, {"digest": digest.hex(), "nonce": nonces[a.name]}, Priority.PA, deadline, caller)): a
        for a in authorities
    }
    stamps: list[Stamp] = []
    failures: list[StampFailure] = []
    evidence: list[TokenEvidence] = []
    committed = from_ms(committed_at_ms)
    while pending:
        remaining = (deadline - clock.now()).total_seconds()
        if remaining <= 0:
            break
        done, _ = wait(list(pending), timeout=remaining, return_when=FIRST_COMPLETED)
        for future in done:
            authority = pending.pop(future)
            result = future.result()
            if result.outcome is not Outcome.OK or result.capture_id is None:
                why = result.error.message if result.error is not None else result.reason
                failures.append(StampFailure(authority.name, _detail(f"{result.outcome.value}: {why}")))
                continue
            response = captures.get(result.capture_id).body
            stamps.append(Stamp(authority.name, nonces[authority.name], response))
            try:
                evidence.append(TokenEvidence(authority.name, verify_token(response, digest, policy, nonce=nonces[authority.name])))
            except TokenError as exc:
                evidence.append(TokenEvidence(authority.name, None, f"{exc.code}: {exc}"))
        if assess(committed, None, evidence, rules).level == LEVEL_C3:
            break
    timing = assess(committed, None, evidence, rules)
    left = sorted(authority.name for authority in pending.values())
    if timing.level != LEVEL_C3:
        failures.extend(StampFailure(name, "no answer before the attestation closed") for name in left)
        left = []
    return Attestation(
        digest,
        committed_at_ms,
        closes_ms,
        tuple(sorted(stamps, key=lambda stamp: stamp.authority)),
        tuple(sorted(failures, key=lambda f: f.authority)),
        tuple(left),
        timing,
        rules.u_constant_s * 1000,
        tuple(evidence),
    )
