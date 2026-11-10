"""The first-layer card lint (description 2.4c; ``architecture/UX_SPEC.md`` section 3): word budgets per slot and per card, the closed
lists, the banned vocabulary and the generic labels. It reads view models, never pages, so a card that breaks the contract is refused
before anything is rendered.

A word is a whitespace-separated token with at least one letter or digit. The featured card has a headline of at most 8 words, four
slots of at most 14, a meta line of at most 12 that carries the trust phrase, an origin count of at most 4 and one control of at most 6,
and at most 90 words in all (the measured wording of the contract's own example was over its stated total, CF-17 and CF-18). A
condensed card has a headline of at most 8 words and one sentence of at most 16 about why it matters, and 35 words in all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from typing import Final

from hoshitori import labels

HEADLINE_WORDS: Final = 8
SLOT_WORDS: Final = 14
META_WORDS: Final = 12
ORIGIN_WORDS: Final = 4
CONTROL_WORDS: Final = 6
FEATURED_WORDS: Final = 90
WHY_WORDS: Final = 16
CONDENSED_WORDS: Final = 35


@dataclass(frozen=True)
class Featured:
    headline: str
    state_chip: str
    what_changed: str
    why_it_matters: str
    still_uncertain: str
    next_check: str
    meta: str
    trust_phrase: str
    origin_count: str
    control_label: str


@dataclass(frozen=True)
class Condensed:
    headline: str
    why_it_matters: str
    state_chip: str
    uncertainty_chip: str
    meta: str
    trust_phrase: str
    control_label: str


def words(text: str) -> int:
    return sum(1 for token in text.split() if re.search(r"[A-Za-z0-9]", token))


def total(card: Featured | Condensed) -> int:
    """Every word on the card that a person sees; the trust phrase is inside the meta line, so it is not counted twice."""
    return sum(words(getattr(card, f.name)) for f in fields(card) if f.name != "trust_phrase")


def lint_featured(card: Featured) -> tuple[str, ...]:
    out: list[str] = []
    _over(out, "headline", card.headline, HEADLINE_WORDS)
    for slot in ("what_changed", "why_it_matters", "still_uncertain", "next_check"):
        _over(out, slot, getattr(card, slot), SLOT_WORDS)
    _over(out, "meta", card.meta, META_WORDS)
    _over(out, "origin_count", card.origin_count, ORIGIN_WORDS)
    _over(out, "control_label", card.control_label, CONTROL_WORDS)
    if total(card) > FEATURED_WORDS:
        out.append(f"the card has {total(card)} words, more than {FEATURED_WORDS}")
    _closed(out, "state chip", card.state_chip, labels.STATE_CHIPS)
    out += _common(card.meta, card.trust_phrase, card.control_label, _text(card))
    return tuple(out)


def lint_condensed(card: Condensed) -> tuple[str, ...]:
    out: list[str] = []
    _over(out, "headline", card.headline, HEADLINE_WORDS)
    _over(out, "why_it_matters", card.why_it_matters, WHY_WORDS)
    _over(out, "meta", card.meta, META_WORDS)
    _over(out, "control_label", card.control_label, CONTROL_WORDS)
    if total(card) > CONDENSED_WORDS:
        out.append(f"the card has {total(card)} words, more than {CONDENSED_WORDS}")
    _closed(out, "state chip", card.state_chip, labels.STATE_CHIPS)
    _closed(out, "uncertainty chip", card.uncertainty_chip, labels.UNCERTAINTY_CHIPS)
    out += _common(card.meta, card.trust_phrase, card.control_label, _text(card))
    return tuple(out)


def _text(card: Featured | Condensed) -> str:
    return " ".join(getattr(card, f.name) for f in fields(card))


def _over(out: list[str], name: str, text: str, limit: int) -> None:
    if words(text) > limit:
        out.append(f"{name} has {words(text)} words, more than {limit}")


def _closed(out: list[str], name: str, value: str, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        out.append(f"{name} {value!r} is not in its closed list")


def _common(meta: str, trust_phrase: str, control_label: str, text: str) -> list[str]:
    out: list[str] = []
    _closed(out, "trust phrase", trust_phrase, labels.TRUST_PHRASES)
    if trust_phrase not in meta:
        out.append("the meta line does not carry the trust phrase")
    if control_label.strip().lower() in labels.GENERIC_LABELS:
        out.append(f"control label {control_label!r} is generic")
    for pattern in labels.BANNED:
        flags = re.IGNORECASE if pattern in labels.CASE_INSENSITIVE else 0
        if re.search(pattern, text, flags):
            out.append(f"banned vocabulary {pattern}")
    return out
