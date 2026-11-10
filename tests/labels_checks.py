"""Checks of the first-layer word lists (``hoshitori.labels``), runnable against a mutant."""

from __future__ import annotations

import re
import types


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def check_lists(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "state chips",
        mod.STATE_CHIPS,
        ("Breaking, unverified", "Being checked", "No reliable conclusion", "Investigated thesis", "Material update"),
    )
    c.eq(
        "uncertainty chips",
        mod.UNCERTAINTY_CHIPS,
        (
            "Supported",
            "Contradicted",
            "Mixed",
            "Not found in sources checked",
            "Not checkable here",
            "Unresolved",
            "Source unavailable",
            "Unproven",
            "Rests on an unproven premise",
        ),
    )
    c.eq(
        "trust phrases",
        mod.TRUST_PHRASES,
        (
            "No forecast recorded",
            "Recorded by us only",
            "Publication reported, not independently time-verified",
            "Independently time-stamped",
            "Independently time-stamped and publicly logged",
        ),
    )
    c.eq("row states", mod.ROW_STATES, ("Deciding", "Open", "Settled", "Not settled", "Abstained", "Failed", "Too late", "Void"))
    c.eq(
        "step words",
        mod.STEP_WORDS,
        {
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
        },
    )
    c.eq(
        "the states of the rules agent in words",
        mod.RULE_STATE_WORDS,
        {"S1": "falling hard", "S2": "falling", "S3": "flat", "S4": "rising", "S5": "rising hard"},
    )
    c.eq("arm names", mod.ARM_NAMES, {"L0": "Rules", "L1": "Single model", "L2": "Debate", "L1x": "Matched control"})
    c.eq("a reason that has no wording", mod.OTHER_REASON, "Other reason")
    c.eq(
        "reason words",
        mod.REASON_WORDS,
        {
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
        },
    )
    c.eq(
        "generic labels",
        mod.GENERIC_LABELS,
        ("more", "details", "learn more", "open analysis", "view", "click here", "read more"),
    )
    c.eq("no list repeats a word", [len(set(x)) == len(x) for x in (mod.STATE_CHIPS, mod.UNCERTAINTY_CHIPS, mod.TRUST_PHRASES)], [True] * 3)
    c.eq(
        "the banned patterns, as written",
        mod.BANNED,
        (
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
        ),
    )
    c.eq("every banned pattern compiles", [bool(re.compile(p)) for p in mod.BANNED], [True] * len(mod.BANNED))
    c.eq("the case-insensitive ones are banned patterns", all(p in mod.BANNED for p in mod.CASE_INSENSITIVE), True)
    c.eq("they are the two ordinary words", mod.CASE_INSENSITIVE, (r"\barm\b", r"\bnode\b"))
    return c.bad


def check_unread_words(mod: types.ModuleType) -> list[str]:
    """The words for a source that was asked and not read: one for each reason the collector can give, and no other."""
    from hoshitori import collect

    c = Case(mod)
    c.eq(
        "what each reason says",
        mod.UNREAD_WORDS,
        {
            "NO_ASSET": "the registry no longer resolved the asset",
            "NO_ADAPTER": "the asset's venue was not connected",
            "NO_SERVER_TIME": "the venue did not tell its time",
            "BAD_REQUEST": "the venue cannot be asked that window",
            "CALL_FAILED": "no answer came",
            "UNREADABLE": "the answer could not be read",
            "TIMED_OUT": "no answer came in time",
            "NO_PERPETUAL": "the token has no perpetual to read funding from",
            "PERPETUAL_LIST_UNUSABLE": "the list of perpetuals was refused or not read, so funding could not be joined to the token",
        },
    )
    c.eq("in the collector's own words, no more and no fewer", sorted(mod.UNREAD_WORDS), sorted(r.value for r in collect.Reason))
    return c.bad


def check_trust_phrase(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("no level", mod.trust_phrase(None), "No forecast recorded")
    c.eq("C1", mod.trust_phrase("C1"), "Recorded by us only")
    c.eq("C2 never says the publication was verified", mod.trust_phrase("C2"), "Publication reported, not independently time-verified")
    c.eq("C3", mod.trust_phrase("C3"), "Independently time-stamped")
    c.eq("C4", mod.trust_phrase("C4"), "Independently time-stamped and publicly logged")
    for level in ("C0", "C5", "c3", "", "3"):
        try:
            mod.trust_phrase(level)
        except mod.LabelError as exc:
            c.eq(f"{level!r} is refused", str(exc), f"{level!r} is not a commitment level the first layer can word (C1 to C4, or none)")
        else:
            c.bad.append(f"{level!r} was worded")
    return c.bad


def check_arm_name(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("a named agent", [mod.arm_name(a) for a in ("L0", "L1", "L2", "L1x")], ["Rules", "Single model", "Debate", "Matched control"])
    c.eq("an agent with no name keeps its code", mod.arm_name("L9"), "L9")
    c.eq(
        "the book of what an agent passed on says it is hypothetical",
        mod.arm_name("L1.shadow"),
        "If single model had followed the rules agent where it passed (hypothetical)",
    )
    c.eq("and so for an agent with no name", mod.arm_name("L9.shadow"), "If l9 had followed the rules agent where it passed (hypothetical)")
    c.eq("the suffix", mod.SHADOW_SUFFIX, ".shadow")
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_lists, check_unread_words, check_trust_phrase, check_arm_name):
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
