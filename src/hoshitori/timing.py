"""Timing assurance of a commitment: which time-stamp tokens count, how many independent operators they come from,
and the level they support (contract sections 2, 8 and 26; verification spec section 3, items 5 to 7).

A token counts only if it verified (``tsa.verify_token``), its generation time lies in the counting window around the
record's own committed time and does not precede the record's own creation, and it precedes the declared outcome window by
at least the clock uncertainty ``u``. Counted tokens are independent only as far as the trusted root's operator registry
can show: one root, one signer certificate and one parent organisation each count once, and an operator without ownership
evidence of a listed kind about the very root the token chained to counts for nothing. The level is C3 when the required
number of independent operators agree within the permitted spread; otherwise only the ledger's own commitment (C1) stands.
Public-reference claims (C2) and witness cosignatures (C4) are handled by the bundle verifier and never change the level
computed here.

A record that says it was created after it was committed is in an impossible order (``IMPOSSIBLE_ORDER``) and gets no
counted token: the contract (section 2) has ``as_of <= created_at <= committed_at``. A token that precedes the record's own
``created_at`` does not count either: that is an impossible ordering under which timing fails (verification spec section
7). Both comparisons allow the local clock-offset limit that already opens the counting window before the commit
(``window_before_commit_s``, 1 s; verification spec section 3, item 6): ``created_at`` and ``committed_at`` are readings of
the host clock, and a token's time is the authority's.

Every verdict is TA-3: the clock uncertainty is an assumed constant plus the observed spread, not a proven bound.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from hoshitori.trusted_root import Operator, TimeStampRules
from hoshitori.tsa import VerifiedToken

LEVEL_C1: Final = "C1"
LEVEL_C3: Final = "C3"
IMPOSSIBLE_ORDER: Final = "IMPOSSIBLE_ORDER"


@dataclass(frozen=True)
class TokenEvidence:
    """A token as the bundle carries it: its name and either the verified facts or the reason it was rejected."""

    name: str
    verified: VerifiedToken | None
    reason: str | None = None


@dataclass(frozen=True)
class TokenAssessment:
    name: str
    valid: bool
    reason: str | None
    gen_time: datetime | None
    root_fingerprint: str | None
    signer_fingerprint: str | None
    operator: str | None
    parent: str | None  # None when the registry has no usable ownership evidence for the root this token chained to
    timely: bool  # verified, inside the counting window and before the outcome window: the token counts


@dataclass(frozen=True)
class TimingResult:
    tokens: tuple[TokenAssessment, ...]
    independent_operators: int  # over all counted tokens
    chosen: tuple[str, ...]  # the tokens that carry the level, empty when none does
    spread_s: float | None
    attested_at: datetime | None
    level: str
    eligible_after_offset_s: float | None
    window_declared: bool
    assurance_class: str
    error: str | None = None  # IMPOSSIBLE_ORDER when the record says it was created after it was committed


def order_detail(rules: TimeStampRules) -> str:
    """The words for ``IMPOSSIBLE_ORDER``, the same in both verifiers."""
    return f"created_at is more than {rules.window_before_commit_s} s later than committed_at"


def evidence_parent(operator: Operator | None, rules: TimeStampRules, root_fingerprint: str) -> str | None:
    """The parent organisation, if and only if the registry entry has accepted evidence about this very root."""
    if operator is None or operator.evidence is None:
        return None
    proof = operator.evidence
    if proof.kind not in rules.evidence_kinds_accepted or not proof.ref or proof.subject_sha256 != root_fingerprint:
        return None
    return operator.parent


def _independent(tokens: Sequence[TokenAssessment]) -> int:
    """Distinct signer certificates and parent organisations, whichever is fewer (a token with no parent counts for none).

    A root maps to one operator and an operator to one parent, so distinct parents already imply distinct roots.
    """
    usable = [t for t in tokens if t.parent is not None]
    if not usable:
        return 0
    return min(len({t.signer_fingerprint for t in usable}), len({t.parent for t in usable}))


def assess(
    committed_at: datetime,
    window_opens_at: datetime | None,
    tokens: Sequence[TokenEvidence],
    rules: TimeStampRules,
    created_at: datetime | None = None,
) -> TimingResult:
    """Judge the tokens against the record's committed time and, when given, its creation time and the start of its outcome window."""
    before = timedelta(seconds=rules.window_before_commit_s)
    after = timedelta(seconds=rules.window_after_commit_s)
    uncertainty = timedelta(seconds=rules.u_constant_s)
    impossible = created_at is not None and created_at > committed_at + before
    assessed: list[TokenAssessment] = []
    for item in tokens:
        proof = item.verified
        if proof is None:
            assessed.append(TokenAssessment(item.name, False, item.reason or "rejected", None, None, None, None, None, False))
            continue
        timely = committed_at - before <= proof.gen_time <= committed_at + after
        if created_at is not None:
            timely = timely and created_at - before <= proof.gen_time
        if window_opens_at is not None:
            timely = timely and proof.gen_time + uncertainty < window_opens_at
        if impossible:
            timely = False
        operator = rules.operators.get(proof.operator)
        parent = evidence_parent(operator, rules, proof.root_fingerprint)
        assessed.append(
            TokenAssessment(
                item.name, True, None, proof.gen_time, proof.root_fingerprint, proof.signer_fingerprint, proof.operator, parent, timely
            )
        )
    counted = [t for t in assessed if t.timely]
    independent = _independent(counted)
    chosen = _best_set(counted, rules)
    declared = window_opens_at is not None
    error = IMPOSSIBLE_ORDER if impossible else None
    if not chosen:
        return TimingResult(tuple(assessed), independent, (), None, None, LEVEL_C1, None, declared, _assurance(rules), error)
    times = [t.gen_time for t in chosen if t.gen_time is not None]
    spread = (max(times) - min(times)).total_seconds()
    return TimingResult(
        tuple(assessed),
        independent,
        tuple(t.name for t in chosen),
        spread,
        max(times),
        LEVEL_C3,
        rules.u_constant_s + spread,
        declared,
        _assurance(rules),
    )


def _assurance(rules: TimeStampRules) -> str:
    return f"TA-3 (assumed clock uncertainty u={rules.u_constant_s}s plus observed spread; not proven)"


def _best_set(counted: Sequence[TokenAssessment], rules: TimeStampRules) -> list[TokenAssessment]:
    """The first window of counted tokens with usable ownership evidence that is independent enough and agrees in time.

    Windows are contiguous runs of the tokens sorted by generation time. The first window found, scanning by start and
    then by end, also has the earliest latest time: a window that starts later and ends earlier would have to be a
    sub-window of an earlier one that already qualified.
    """
    ordered = sorted((t for t in counted if t.gen_time is not None and t.parent is not None), key=lambda t: (t.gen_time, t.name))
    for start in range(len(ordered)):
        for end in range(start, len(ordered)):
            first, last = ordered[start].gen_time, ordered[end].gen_time
            if first is None or last is None or (last - first).total_seconds() > rules.max_spread_s:
                break
            subset = ordered[start : end + 1]
            if _independent(subset) >= rules.required_independent:
                return subset
    return []
