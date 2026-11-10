#!/usr/bin/env python3
"""Layer-1 card lint (description 2.4c, contract section 38): word budgets per slot and per card, closed state-chip vocabulary,
banned internal vocabulary, generic labels. Words are whitespace-separated tokens containing at least one letter or digit."""
import re

WORDS_FEATURED = {"what_changed": 16, "why_it_matters": 16, "still_uncertain": 16, "next_check": 16, "meta": 12}
FEATURED_TOTAL = 80
CONDENSED = {"headline": 8, "why_it_matters": 16}
CONDENSED_TOTAL = 35
BANNED = [r"\bL[012]\b", r"\bR arm\b", r"\bclaim nodes?\b", r"\bC[0-4]\b", r"\bBrier\b", r"\bHolm\b", r"\bsensitivity\b", r"\btiming class\b",
          r"\bprovenance level\b", r"\bKachikoshi\b", r"\bMakekoshi\b", r"\bMitodoke\b", r"\barm\b", r"\bcommitment level\b", r"\bnode\b"]
GENERIC_LABELS = {"more", "details", "learn more", "open analysis", "view", "click here", "read more"}
STATE_CHIPS = {"Breaking, unverified", "Being checked", "No reliable conclusion", "Investigated thesis", "Material update"}
UNCERTAINTY_CHIPS = {"Supported", "Contradicted", "Mixed", "Not found in sources checked", "Not checkable here", "Unresolved", "Source unavailable",
                     "Unproven", "Rests on an unproven premise"}
TRUST_PHRASES = {"No forecast recorded", "Recorded by us only", "Publication reported, not independently time-verified",
                 "Independently time-stamped before the outcome window", "Independently time-stamped and publicly logged"}


def words(s):
    return [w for w in s.split() if re.search(r"[A-Za-z0-9]", w)]


def n(s):
    return len(words(s))


def lint_featured(card):
    errs = []
    total = 0
    for slot, limit in WORDS_FEATURED.items():
        c = n(card[slot]); total += c
        if c > limit:
            errs.append(f"{slot}: {c} words > {limit}")
    if card.get("headline") and n(card["headline"]) > 8:
        errs.append("headline > 8 words")
    total += n(card.get("headline", "")) + n(card["control"]["label"])
    if total > FEATURED_TOTAL:
        errs.append(f"card total {total} > {FEATURED_TOTAL}")
    errs += _vocab(card)
    if card["state_chip"] not in STATE_CHIPS:
        errs.append("state chip outside the closed list")
    return errs, total


def lint_condensed(card):
    errs = []
    total = 0
    for slot, limit in CONDENSED.items():
        c = n(card[slot]); total += c
        if c > limit:
            errs.append(f"{slot}: {c} words > {limit}")
    total += n(card["meta"]) + n(card["control"]["label"]) + n(card["state_chip"]) + n(card["uncertainty_chip"])
    if total > CONDENSED_TOTAL:
        errs.append(f"card total {total} > {CONDENSED_TOTAL}")
    errs += _vocab(card)
    if card["state_chip"] not in STATE_CHIPS:
        errs.append("state chip outside the closed list")
    if card["uncertainty_chip"] not in UNCERTAINTY_CHIPS:
        errs.append("uncertainty chip outside the closed list")
    return errs, total


def _vocab(card):
    errs = []
    text = " ".join(str(v) for k, v in card.items() if isinstance(v, str))
    for pat in BANNED:
        if re.search(pat, text, flags=re.I if pat.startswith(r"\bnode") or pat.startswith(r"\barm") else 0):
            errs.append(f"banned vocabulary /{pat}/")
    if card["control"]["label"].strip().lower() in GENERIC_LABELS:
        errs.append(f"generic control label '{card['control']['label']}'")
    return errs
