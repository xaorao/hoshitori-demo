"""Checks of the first-layer card lint (``hoshitori.card_lint``), runnable against a mutant: every budget at its edge and one past it,
every closed list, the banned vocabulary and the generic labels, with the exact message of each refusal."""

from __future__ import annotations

import dataclasses
import types
from typing import Any

from hoshitori import labels

TRUST = labels.TRUST_PHRASES[3]
TRUST_N = len(TRUST.split())  # the checks below are written for any length of the phrase: the meta line is padded to its size


def w(n: int, word: str = "word") -> str:
    return " ".join([word] * n)


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def featured(mod: types.ModuleType, **over: Any) -> Any:
    values: dict[str, Any] = {
        "headline": "Bitcoin one-hour call settled",
        "state_chip": "Investigated thesis",
        "what_changed": "The hour closed above where the call started.",
        "why_it_matters": "It is the first settled call of this agent.",
        "still_uncertain": "One settled call says little about skill.",
        "next_check": "Wait for the other horizons to settle.",
        "meta": f"Checked 4 minutes ago. {TRUST}",
        "trust_phrase": TRUST,
        "origin_count": "2 independent sources",
        "control_label": "Read the receipt",
    }
    values.update(over)
    return mod.Featured(**values)


def condensed(mod: types.ModuleType, **over: Any) -> Any:
    values: dict[str, Any] = {
        "headline": "Ether four-hour call open",
        "why_it_matters": "It settles in three hours.",
        "state_chip": "Being checked",
        "uncertainty_chip": "Unresolved",
        "meta": f"Checked 4 minutes ago. {TRUST}",
        "trust_phrase": TRUST,
        "control_label": "Read the receipt",
    }
    values.update(over)
    return mod.Condensed(**values)


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "the budgets",
        (
            mod.HEADLINE_WORDS,
            mod.SLOT_WORDS,
            mod.META_WORDS,
            mod.ORIGIN_WORDS,
            mod.CONTROL_WORDS,
            mod.FEATURED_WORDS,
            mod.WHY_WORDS,
            mod.CONDENSED_WORDS,
        ),
        (8, 14, 12, 4, 6, 90, 16, 35),
    )
    c.eq(
        "the cards",
        (
            [f.name for f in dataclasses.fields(mod.Featured)],
            [f.name for f in dataclasses.fields(mod.Condensed)],
            mod.Featured.__dataclass_params__.frozen,
            mod.Condensed.__dataclass_params__.frozen,
        ),
        (
            [
                "headline",
                "state_chip",
                "what_changed",
                "why_it_matters",
                "still_uncertain",
                "next_check",
                "meta",
                "trust_phrase",
                "origin_count",
                "control_label",
            ],
            ["headline", "why_it_matters", "state_chip", "uncertainty_chip", "meta", "trust_phrase", "control_label"],
            True,
            True,
        ),
    )
    return c.bad


def check_words(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("a plain sentence", mod.words("The hour closed above."), 4)
    c.eq("punctuation alone is not a word", mod.words("A - B , C . 7"), 4)
    c.eq("digits are words", mod.words("1 2 3"), 3)
    c.eq("a hyphenated word is one", mod.words("one-hour call"), 2)
    c.eq("nothing", mod.words("   "), 0)
    c.eq("whitespace of any kind splits", mod.words("a\tb\nc  d"), 4)
    c.eq(
        "the total leaves the trust phrase out (it is in the meta line)",
        mod.total(condensed(mod)),
        19 + TRUST_N,
    )
    return c.bad


def check_featured(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    good = featured(mod)
    c.eq("a good card", (mod.lint_featured(good), mod.total(good) <= 90), ((), True))
    c.eq("its total", mod.total(good), 47 + TRUST_N)
    for name, limit in (("headline", 8), ("what_changed", 14), ("why_it_matters", 14), ("still_uncertain", 14), ("next_check", 14)):
        at = mod.lint_featured(featured(mod, **{name: w(limit)}))
        over = mod.lint_featured(featured(mod, **{name: w(limit + 1)}))
        c.eq(f"{name} at {limit}", [m for m in at if m.startswith(name)], [])
        c.eq(f"{name} at {limit + 1}", [m for m in over if m.startswith(name)], [f"{name} has {limit + 1} words, more than {limit}"])
    meta_at = f"{w(12 - TRUST_N, 'x')} {TRUST}"  # 12 words in all
    c.eq("a meta line of 12", mod.lint_featured(featured(mod, meta=meta_at)), ())
    c.eq("a meta line of 13", mod.lint_featured(featured(mod, meta=meta_at + " y")), ("meta has 13 words, more than 12",))
    c.eq("an origin count of 4", mod.lint_featured(featured(mod, origin_count=w(4))), ())
    c.eq("an origin count of 5", mod.lint_featured(featured(mod, origin_count=w(5))), ("origin_count has 5 words, more than 4",))
    c.eq(
        "a featured card with a banned word",
        mod.lint_featured(featured(mod, what_changed="The L1 agent called it.")),
        (r"banned vocabulary \bL[012]\b",),
    )
    c.eq(
        "a featured card with a generic control",
        mod.lint_featured(featured(mod, control_label="More")),
        ("control label 'More' is generic",),
    )
    c.eq(
        "a featured card with a trust phrase that is not one of the five",
        mod.lint_featured(featured(mod, trust_phrase="Verified", meta="Verified")),
        ("trust phrase 'Verified' is not in its closed list",),
    )
    c.eq(
        "a featured meta line without its trust phrase",
        mod.lint_featured(featured(mod, meta="Checked 4 minutes ago.")),
        ("the meta line does not carry the trust phrase",),
    )
    c.eq("a control label of 6", mod.lint_featured(featured(mod, control_label=w(6))), ())
    c.eq("a control label of 7", mod.lint_featured(featured(mod, control_label=w(7))), ("control_label has 7 words, more than 6",))
    # every part at its limit still fits the total, so the total can only bind if a limit is raised
    longest_chip = max(labels.STATE_CHIPS, key=lambda chip: len(chip.split()))
    most = featured(
        mod,
        headline=w(8),
        state_chip=longest_chip,
        what_changed=w(14),
        why_it_matters=w(14),
        still_uncertain=w(14),
        next_check=w(14),
        meta=f"{TRUST} " + w(12 - TRUST_N),
        origin_count=w(4),
        control_label=w(6),
    )
    c.eq("a card with every part at its limit", (mod.lint_featured(most), mod.total(most)), ((), 89))
    c.eq("and its total is within the card's", mod.total(most) <= mod.FEATURED_WORDS, True)
    c.eq(
        "a chip that is not on the list",
        mod.lint_featured(featured(mod, state_chip="Hot")),
        ("state chip 'Hot' is not in its closed list",),
    )
    c.eq(
        "an uncertainty chip is not a state chip",
        mod.lint_featured(featured(mod, state_chip="Unresolved")),
        ("state chip 'Unresolved' is not in its closed list",),
    )
    return c.bad


def check_condensed(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    good = condensed(mod)
    c.eq("a good condensed card", mod.lint_condensed(good), ())
    c.eq("headline at 8", [m for m in mod.lint_condensed(condensed(mod, headline=w(8))) if m.startswith("headline")], [])
    c.eq("headline at 9", mod.lint_condensed(condensed(mod, headline=w(9))), ("headline has 9 words, more than 8",))
    c.eq("why at 16", mod.lint_condensed(condensed(mod, why_it_matters=w(16), headline="A", meta=TRUST[:0] + TRUST)), ())
    c.eq(
        "why at 17",
        [m for m in mod.lint_condensed(condensed(mod, why_it_matters=w(17))) if m.startswith("why")],
        ["why_it_matters has 17 words, more than 16"],
    )
    c.eq(
        "a meta line of 13",
        mod.lint_condensed(condensed(mod, meta=f"{w(13 - TRUST_N, 'x')} {TRUST}")),
        ("meta has 13 words, more than 12",),
    )
    c.eq("a control label of 7", mod.lint_condensed(condensed(mod, control_label=w(7))), ("control_label has 7 words, more than 6",))
    fixed = {
        "state_chip": "No reliable conclusion",
        "uncertainty_chip": "Not found in sources checked",
        "meta": f"{w(12 - TRUST_N, 'x')} {TRUST}",
        "headline": "A",
    }  # 3 + 5 + 12 + 1 words and a control of 3: 24 before the sentence
    c.eq("a total of exactly 35", mod.lint_condensed(condensed(mod, why_it_matters=w(11), **fixed)), ())
    c.eq("a total of 36", mod.lint_condensed(condensed(mod, why_it_matters=w(12), **fixed)), ("the card has 36 words, more than 35",))
    c.eq("an unknown state chip", mod.lint_condensed(condensed(mod, state_chip="Cool")), ("state chip 'Cool' is not in its closed list",))
    c.eq(
        "an unknown uncertainty chip",
        mod.lint_condensed(condensed(mod, uncertainty_chip="Maybe")),
        ("uncertainty chip 'Maybe' is not in its closed list",),
    )
    c.eq(
        "a state chip is not an uncertainty chip",
        mod.lint_condensed(condensed(mod, uncertainty_chip="Being checked")),
        ("uncertainty chip 'Being checked' is not in its closed list",),
    )
    return c.bad


def check_common(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "a trust phrase that is not one of the five",
        mod.lint_condensed(condensed(mod, trust_phrase="Verified", meta="Verified")),
        ("trust phrase 'Verified' is not in its closed list",),
    )
    c.eq(
        "a meta line without its trust phrase",
        mod.lint_condensed(condensed(mod, meta="Checked 4 minutes ago.")),
        ("the meta line does not carry the trust phrase",),
    )
    for label in ("More", "details", "  Learn more ", "Open analysis", "VIEW", "click here", "Read more"):
        c.eq(f"control label {label!r}", mod.lint_condensed(condensed(mod, control_label=label)), (f"control label {label!r} is generic",))
    c.eq("a control label that says the outcome", mod.lint_condensed(condensed(mod, control_label="Read the receipt")), ())
    c.eq("a label that merely contains a generic one", mod.lint_condensed(condensed(mod, control_label="More about the call")), ())
    for text, pattern in (
        ("The L1 agent called it", r"\bL[012]\b"),
        ("L2 called it", r"\bL[012]\b"),
        ("L0 sat out", r"\bL[012]\b"),
        ("the L1x control", r"\bL1x\b"),
        ("a C3 forecast", r"\bC[0-4]\b"),
        ("level C0", r"\bC[0-4]\b"),
        ("the Brier score", r"\bBrier\b"),
        ("Holm adjusted", r"\bHolm\b"),
        ("a sensitivity run", r"\bsensitivity\b"),
        ("the timing class", r"\btiming class\b"),
        ("provenance level 2", r"\bprovenance level\b"),
        ("the Kachikoshi badge", r"\bKachikoshi\b"),
        ("the Makekoshi badge", r"\bMakekoshi\b"),
        ("the Mitodoke engine", r"\bMitodoke\b"),
        ("the ARM agent", r"\barm\b"),
        ("a commitment level", r"\bcommitment level\b"),
        ("six claim nodes", r"\bclaim nodes?\b"),
        ("each Node", r"\bnode\b"),
        ("the R arm", r"\bR arm\b"),
    ):
        got = mod.lint_condensed(condensed(mod, why_it_matters=text))
        c.eq(f"{text!r}", [m for m in got if m.startswith("banned")], [f"banned vocabulary {pattern}", *_also(text, pattern)])
    c.eq(
        "an ordinary sentence with a near miss",
        mod.lint_condensed(condensed(mod, why_it_matters="Harmless carbon and alarm and inode")),
        (),
    )
    c.eq("a code inside a longer word", mod.lint_condensed(condensed(mod, why_it_matters="ABC3 L10 CL1")), ())
    c.eq("a banned word in the headline", mod.lint_condensed(condensed(mod, headline="L1 called it"))[0][:17], "banned vocabulary")
    c.eq(
        "a banned word in the meta line",
        [m for m in mod.lint_condensed(condensed(mod, meta=f"C3 {TRUST}")) if m.startswith("banned")],
        [r"banned vocabulary \bC[0-4]\b"],
    )
    c.eq(
        "a banned word in a chip",
        [m for m in mod.lint_condensed(condensed(mod, control_label="Open the C3 receipt")) if m.startswith("banned")],
        [r"banned vocabulary \bC[0-4]\b"],
    )
    return c.bad


def _also(text: str, pattern: str) -> list[str]:
    """Some sentences trip a second pattern too (a 'R arm' is also an 'arm'); say which."""
    return [r"banned vocabulary \barm\b"] if text == "the R arm" else []


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_words, check_featured, check_condensed, check_common):
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
