"""The closed lists of words the first layer of the interface may use, as data (``architecture/UX_SPEC.md`` sections 3 and 6).

Every view model takes its chips and its trust phrase from here, and the card lint rejects anything else, so the screen cannot say more than
the records say. The five state chips name where an investigation stands; the nine uncertainty chips are the statuses of a claim-graph node;
the five trust phrases are the only wording of the commitment level on the first layer (a public reference offline is a claim, not proof,
so only a level of C3 or more says "independently time-stamped"); the league's own row states have a short list of their own.
"""

from __future__ import annotations

from typing import Final

STATE_CHIPS: Final = (
    "Breaking, unverified",
    "Being checked",
    "No reliable conclusion",
    "Investigated thesis",
    "Material update",
)
UNCERTAINTY_CHIPS: Final = (
    "Supported",
    "Contradicted",
    "Mixed",
    "Not found in sources checked",
    "Not checkable here",
    "Unresolved",
    "Source unavailable",
    "Unproven",
    "Rests on an unproven premise",
)
TRUST_PHRASES: Final = (
    "No forecast recorded",
    "Recorded by us only",
    "Publication reported, not independently time-verified",
    "Independently time-stamped",
    "Independently time-stamped and publicly logged",
)
ROW_STATES: Final = ("Deciding", "Open", "Settled", "Not settled", "Abstained", "Failed", "Too late", "Void")
STEP_WORDS: Final = {
    "SCHEDULED": "Scheduled",
    "CREATED": "Created",
    "EVIDENCE_FROZEN": "Evidence frozen",
    "DECIDED": "Decided",
    "COMMITTED": "Committed",
    "AWAITING_ORIGIN": "Waiting for the start",
    "OPEN": "Started",
    "SETTLING": "Settling",
    "SETTLED": "Settled",
    "AMBIGUOUS": "Could not be told",
    "VOID": "Voided",
    "UNSETTLEABLE": "Could not be settled",
    "NO_CALL": "Abstained",
    "FAILED": "Failed",
    "FAILED_EVIDENCE": "Failed for lack of evidence",
    "FAILED_LATE": "Too late",
    "FAILED_STALE": "Evidence went stale",
    "FAILED_LOST": "Lost",
    "LEVEL": "Commitment level changed",
}
# the five states of the rules agent, from how far the move so far is from the move expected over the horizon
RULE_STATE_WORDS: Final = {"S1": "falling hard", "S2": "falling", "S3": "flat", "S4": "rising", "S5": "rising hard"}
ARM_NAMES: Final = {"L0": "Rules", "L1": "Single model", "L2": "Debate", "L1x": "Matched control"}
SHADOW_SUFFIX: Final = ".shadow"  # a practice book of what an agent passed on, followed by the rules agent: hypothetical


def arm_name(arm: str) -> str:
    """How a page names an agent, or the hypothetical book of the calls it passed on."""
    if arm.endswith(SHADOW_SUFFIX):
        base = arm.removesuffix(SHADOW_SUFFIX)
        return f"If {ARM_NAMES.get(base, base).lower()} had followed the rules agent where it passed (hypothetical)"
    return ARM_NAMES.get(arm, arm)


OTHER_REASON: Final = "Other reason"
UNREAD_WORDS: Final = {  # why a source that was asked for a call's evidence brought nothing: the collector's reasons, in words
    "NO_ASSET": "the registry no longer resolved the asset",
    "NO_ADAPTER": "the asset's venue was not connected",
    "NO_SERVER_TIME": "the venue did not tell its time",
    "BAD_REQUEST": "the venue cannot be asked that window",
    "CALL_FAILED": "no answer came",
    "UNREADABLE": "the answer could not be read",
    "TIMED_OUT": "no answer came in time",
    "NO_PERPETUAL": "the token has no perpetual to read funding from",
    "PERPETUAL_LIST_UNUSABLE": "the list of perpetuals was refused or not read, so funding could not be joined to the token",
}
REASON_WORDS: Final = {
    "MISSING_EVIDENCE": "Evidence was missing",
    "MODEL_UNAVAILABLE": "The model did not answer in time",
    "MODEL_OUTPUT_INVALID": "The model's answer did not fit the form",
    "MODEL_TRUNCATED": "The model ran out of room",
    "MODEL_EMPTY": "The model said nothing",
    "MODEL_REFUSED": "The model declined",
    "BUDGET_SPENT": "The token budget was spent",
    "EVIDENCE_CONFLICT_UNRESOLVED": "The evidence conflicted",
    "ESSENTIAL_PREMISE_UNPROVEN": "A key premise was not proven",
    "BELOW_MINIMUM_EDGE": "Too little edge to call",
    "OUT_OF_SCOPE": "Out of scope",
    "FINISHED_AFTER_CUTOFF": "The answer came after the deadline",
    "NO_MATCH_TARGET": "There was no debate to match",
    "VOID_GAP": "Too long between the evidence and the start",
    "VOID_TIE": "The price did not change",
    "VOID_OUTAGE": "An exchange was down",
    "VOID_DELISTED": "The token was delisted",
    "VOID_REDENOMINATION": "The token was redenominated",
    "VOID_TOKEN_EVENT": "A token event voided it",
    "UNSETTLEABLE_DISAGREE": "The two exchanges disagreed",
    "UNSETTLEABLE_MISSING": "Price data was missing",
    "CANDLE_MISSING": "Price data was missing",
    "NO_FEATURES": "No evidence could be built for the token",
    "CLOCK_OFFSET": "The exchange's clock was off",
    "DISCOVERY_SNAPSHOT_GAP": "Collecting the evidence took too long",
    "SNAPSHOT_TOO_OLD": "The evidence was too old when it was frozen",
    "PROCESS_LOST": "The process was lost during the cycle",
}

_BY_LEVEL: Final = {"C1": TRUST_PHRASES[1], "C2": TRUST_PHRASES[2], "C3": TRUST_PHRASES[3], "C4": TRUST_PHRASES[4]}

# words and phrases of the machinery that the first layer never shows (a person reads "the agents", never "L1"; a level is a phrase)
BANNED: Final = (
    r"\bL[012]\b",
    r"\bL1x\b",
    r"\bR arm\b",
    r"\bclaim nodes?\b",
    r"\bC[0-4]\b",
    r"\bBrier\b",
    r"\bHolm\b",
    r"\bsensitivity\b",
    r"\btiming class\b",
    r"\bprovenance level\b",
    r"\bKachikoshi\b",
    r"\bMakekoshi\b",
    r"\bMitodoke\b",
    r"\barm\b",
    r"\bcommitment level\b",
    r"\bnode\b",
)
CASE_INSENSITIVE: Final = (r"\barm\b", r"\bnode\b")  # the rest are names and codes, matched as written
GENERIC_LABELS: Final = ("more", "details", "learn more", "open analysis", "view", "click here", "read more")


class LabelError(ValueError):
    """A level or a word that has no place on the first layer."""


def trust_phrase(level: str | None) -> str:
    """The first-layer wording of a forecast's commitment level; no level at all is "No forecast recorded"."""
    if level is None:
        return TRUST_PHRASES[0]
    try:
        return _BY_LEVEL[level]
    except KeyError:
        raise LabelError(f"{level!r} is not a commitment level the first layer can word (C1 to C4, or none)") from None
