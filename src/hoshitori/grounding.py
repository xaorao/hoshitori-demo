"""Does the evidence say what the model said it says? (``GROUNDING_CONTRACT`` sections 3 to 5 and 7)

A citation that exists is not a citation that supports: a model can cite a real funding rate in support of "funding rose"
when the rate is negative and unchanged. Every typed assertion of an answer is checked here against the items the arm was
shown, in layers, and the strictest failure wins:

1. **Deterministic.** The cited item was shown; it is about the asset (or about the whole market); the cited field is known
and a number; its window and its unit are what the assertion says (from a small registry of what each field means); and then
arithmetic: the value at the precision stated, an inequality, a sign, a direction against a flat band, one field against
another, compared in the module's own decimal context (a field with more digits than it holds at the places stated is
``NOT_ENOUGH_INFO``, never an error). A failure here is ``REFUTES`` or a ``WRONG_SCOPE`` and nothing below may upgrade it.
2. **Structural.** For events and mechanisms the quoted span must be found in a text field of a cited item, and its negations, hedges and
qualifiers must not be dropped by the model's words. 3. **Semantic.** There is no validated judge yet, so an event or a mechanism that
passes layers 1 and 2 is ``SUPPORT_UNVERIFIED``, never ``SUPPORTS``. An opinion is context and never support. There is no default to
supported anywhere.

The report keeps the two numbers the contract asks for apart: how many citations exist, and how many assertions are
supported. A false statement (``REFUTES`` or any ``WRONG_SCOPE``) is what a gate may act on; a statement that is only
unverified is shown and marked, and never decides anything. Numbers in the model's free words that no shown field holds are
listed (``ungrounded_numbers``), not rejected.

The registry of field meanings and the two bands below are priors written down before any scored period; changing them is a
new version.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from enum import StrEnum
from typing import Final, cast

from hoshitori.assertions import EVIDENCE_ID, Assertion, AssertionKind, plain, render
from hoshitori.evidence import EvidenceItem

FLAT_BAND: Final = Decimal("0.0005")  # a change of up to 5 basis points either way is flat
APPROX_REL: Final = Decimal("0.01")  # "about" is within one percent of the value stated
# Never the caller's context: a statement is compared in these 28 digits (those of ``features`` and Python's default), in which every
# field ``features`` writes (at most 16 digits before the point, 12 after) compares at any places an assertion may state (at most 12).
_CTX: Final = Context(prec=28, rounding=ROUND_HALF_EVEN)


class Label(StrEnum):
    SUPPORTS = "SUPPORTS"
    REFUTES = "REFUTES"
    NOT_ENOUGH_INFO = "NOT_ENOUGH_INFO"
    WRONG_SCOPE_ASSET = "WRONG_SCOPE_ASSET"
    WRONG_SCOPE_TIME = "WRONG_SCOPE_TIME"
    WRONG_SCOPE_UNIT = "WRONG_SCOPE_UNIT"
    WRONG_SCOPE_SIGN = "WRONG_SCOPE_SIGN"
    WRONG_SCOPE_QUALIFIER = "WRONG_SCOPE_QUALIFIER"
    SUPPORT_UNVERIFIED = "SUPPORT_UNVERIFIED"
    OPINION_CONTEXT = "OPINION_CONTEXT"


FALSE_STATEMENTS: Final = frozenset(
    {
        Label.REFUTES,
        Label.WRONG_SCOPE_ASSET,
        Label.WRONG_SCOPE_TIME,
        Label.WRONG_SCOPE_UNIT,
        Label.WRONG_SCOPE_SIGN,
        Label.WRONG_SCOPE_QUALIFIER,
    }
)


@dataclass(frozen=True)
class Fact:
    """What a field means: the window it covers, its unit, and whether it is a signed change."""

    window: str
    unit: str
    change: bool = False


FIELD_FACTS: Final[Mapping[str, Fact]] = {
    "ret_1h": Fact("1h", "ratio", True),
    "ret_4h": Fact("4h", "ratio", True),
    "ret_24h": Fact("24h", "ratio", True),
    "close": Fact("spot", "price"),
    "atr14": Fact("spot", "price"),
    "atr14_share": Fact("spot", "ratio"),
    "last_close": Fact("spot", "price"),
    "count": Fact("spot", "count"),
    "missing": Fact("spot", "count"),
    "pending": Fact("spot", "count"),
    "excluded_unfinished": Fact("spot", "count"),
    "interval_s": Fact("spot", "count"),
    # the numeric observations of the RYO tools that the assertion schema can state (its units and windows are a closed list): a percentage
    # change over a window, and the price. RYO's own fields are read as they are, in the unit RYO states, and compared to what is asserted.
    "performance.change_1h_pct": Fact("1h", "percent", True),
    "performance.change_24h_pct": Fact("24h", "percent", True),
    "performance.change_7d_pct": Fact("7d", "percent", True),
    "performance.change_30d_pct": Fact("30d", "percent", True),
    "technical_analysis.momentum_30d_pct": Fact("30d", "percent", True),
    "market.price_usd": Fact("spot", "price"),
}
HEDGES: Final = (
    "reportedly",
    "allegedly",
    "alleged",
    "rumor",
    "rumour",
    "unconfirmed",
    "may",
    "might",
    "could",
    "possibly",
    "expected to",
    "plans to",
    "said to",
)
NEGATIONS: Final = (
    "not",
    "no",
    "none",
    "never",
    "without",
    "denies",
    "denied",
    "unless",
    "cannot",
    "neither",
    "nor",
    "isn't",
    "aren't",
    "wasn't",
    "weren't",
    "doesn't",
    "don't",
    "didn't",
    "won't",
    "can't",
    "couldn't",
    "shouldn't",
    "hasn't",
    "haven't",
    "hadn't",
    "fails to",
    "failed to",
    "rejected",
    "rejects",
    "refuted",
    "false",
)
QUALIFIERS: Final = ("at least", "up to", "only", "so far", "over the last", "per")


@dataclass(frozen=True)
class Verdict:
    assertion_id: str
    kind: AssertionKind
    label: Label
    layer: int  # 1 deterministic, 2 structural, 3 for a statement no layer could settle
    citation_exists: bool
    reason: str
    sentence: str  # the assertion as a reader sees it


@dataclass(frozen=True)
class Report:
    verdicts: tuple[Verdict, ...]
    ungrounded_numbers: tuple[str, ...]

    @property
    def citations_exist(self) -> int:
        return sum(1 for v in self.verdicts if v.citation_exists)

    @property
    def supported(self) -> int:
        return sum(1 for v in self.verdicts if v.label is Label.SUPPORTS)

    @property
    def false_statements(self) -> tuple[str, ...]:
        return tuple(v.assertion_id for v in self.verdicts if v.label in FALSE_STATEMENTS)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in self.verdicts:
            out[v.label.value] = out.get(v.label.value, 0) + 1
        return out


def _phrase(words: tuple[str, ...], text: str) -> bool:
    text = text.replace("\u2019", "'")  # a typographic apostrophe is the same contraction
    return any(re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", text, re.IGNORECASE) for w in words)


def _number(item: EvidenceItem, path: str) -> Decimal | str:
    """The value of a known numeric field, or the reason it is not one."""
    known = dict(item.fields)
    if path not in known:
        unknown = dict(item.unknown_fields)
        return (
            f"{path} is not measured in the cited item ({unknown[path].value})"
            if path in unknown
            else f"{path} is not a field of the cited item"
        )
    value = known[path]
    if isinstance(value, bool):
        return f"{path} is a flag, not a number"
    try:
        number = Decimal(value)
    except InvalidOperation:
        return f"{path} is text, not a number"
    return number if number.is_finite() else f"{path} is not a finite number"


def _places(value: Decimal) -> int:
    """The decimal places of a finite value, trailing zeros not counted (none for an integer)."""
    return max(0, -cast(int, value.normalize().as_tuple().exponent))


def _quantised(value: Decimal, places: int, rounding: str = ROUND_HALF_EVEN) -> Decimal:
    return value.quantize(Decimal(10) ** -places, rounding=rounding)


def _same_asset(item: EvidenceItem, asset_id: str) -> bool:
    return not item.asset_refs or any(ref[0] == asset_id and ref[2] == "RESOLVED" for ref in item.asset_refs)


def _unit_value(field_value: Decimal, field_unit: str, unit: str) -> Decimal | None:
    """The field's number in the unit the assertion states, or ``None`` if the unit cannot be what the field is."""
    if unit == field_unit:
        return field_value
    if unit == "percent" and field_unit == "ratio":
        return field_value * 100
    if unit == "ratio" and field_unit == "percent":
        return field_value / 100
    return None


def _compare(op: str, left: Decimal, right: Decimal) -> bool:
    return {"gt": left > right, "ge": left >= right, "lt": left < right, "le": left <= right}[op]


def _verdict(a: Assertion, label: Label, layer: int, exists: bool, reason: str) -> Verdict:
    return Verdict(a.assertion_id, a.kind, label, layer, exists, reason, render(a))


def _cited(a: Assertion, items: Mapping[str, EvidenceItem]) -> tuple[EvidenceItem, ...] | None:
    found = tuple(items[c.evidence_id] for c in a.cites if c.evidence_id in items)
    return found if len(found) == len(a.cites) else None


def _holds(op: str, field: Decimal, stated: Decimal) -> bool:
    """Whether what a model stated of a field's number is so, in the module's own decimal context. A field with more digits than that
    context holds at the places stated raises ``decimal.InvalidOperation``."""
    with localcontext(_CTX):
        if op == "eq":
            # equal at the precision stated: the field's number rounded to the places stated, or cut off there (a model that copies the
            # digits it sees and stops early has said something true, to the places it stated; one that changes a digit has not)
            places = _places(stated)
            return stated in (_quantised(field, places), _quantised(field, places, ROUND_DOWN))
        if op == "approx":
            return abs(field - stated) <= APPROX_REL * abs(stated)
        return _compare(op, field, stated)


def _numeric(a: Assertion, found: tuple[EvidenceItem, ...], asset_id: str) -> Verdict:
    cite, item = a.cites[0], found[0]
    if not _same_asset(item, asset_id):
        return _verdict(a, Label.WRONG_SCOPE_ASSET, 1, True, f"the cited item is about another asset than {asset_id}")
    fact = FIELD_FACTS.get(cite.field_path)
    if fact is None:
        return _verdict(
            a, Label.NOT_ENOUGH_INFO, 1, True, f"{cite.field_path} has no registered meaning, so its unit and window cannot be checked"
        )
    got = _number(item, cite.field_path)
    if isinstance(got, str):
        return _verdict(a, Label.NOT_ENOUGH_INFO, 1, True, got)
    if a.window != fact.window:
        return _verdict(a, Label.WRONG_SCOPE_TIME, 1, True, f"{cite.field_path} covers {fact.window}, the assertion says {a.window}")
    converted = _unit_value(got, fact.unit, str(a.unit))
    if converted is None:
        return _verdict(a, Label.WRONG_SCOPE_UNIT, 1, True, f"{cite.field_path} is a {fact.unit}, the assertion says {a.unit}")
    value = Decimal(str(a.value))
    shown = f"{cite.field_path} is {plain(converted)} {a.unit}; the assertion says {plain(value)}"
    try:
        if _holds(str(a.op), converted, value):
            return _verdict(a, Label.SUPPORTS, 1, True, shown)
        if a.op in ("eq", "approx") and value != 0 and _holds(str(a.op), converted, -value):
            return _verdict(a, Label.WRONG_SCOPE_SIGN, 1, True, shown + " (the sign was dropped)")
    except InvalidOperation:  # a statement that cannot be compared is not settled, either way
        return _verdict(
            a, Label.NOT_ENOUGH_INFO, 1, True, f"{cite.field_path} has too many digits to be compared at the places the assertion states"
        )
    return _verdict(a, Label.REFUTES, 1, True, shown)


def _direction(a: Assertion, found: tuple[EvidenceItem, ...], asset_id: str) -> Verdict:
    cite, item = a.cites[0], found[0]
    if not _same_asset(item, asset_id):
        return _verdict(a, Label.WRONG_SCOPE_ASSET, 1, True, f"the cited item is about another asset than {asset_id}")
    fact = FIELD_FACTS.get(cite.field_path)
    if fact is None or not fact.change:
        return _verdict(
            a, Label.NOT_ENOUGH_INFO, 1, True, f"{cite.field_path} is not a registered signed change, so a direction cannot be read from it"
        )
    got = _number(item, cite.field_path)
    if isinstance(got, str):
        return _verdict(a, Label.NOT_ENOUGH_INFO, 1, True, got)
    if a.window != fact.window:
        return _verdict(a, Label.WRONG_SCOPE_TIME, 1, True, f"{cite.field_path} covers {fact.window}, the assertion says {a.window}")
    shown = f"{cite.field_path} is {plain(got)}"
    holds = {"rose": got > FLAT_BAND, "fell": got < -FLAT_BAND, "flat": abs(got) <= FLAT_BAND}[str(a.op)]
    return _verdict(a, Label.SUPPORTS if holds else Label.REFUTES, 1, True, shown)


def _comparison(a: Assertion, found: tuple[EvidenceItem, ...], asset_id: str) -> Verdict:
    if not all(_same_asset(item, asset_id) for item in found):
        return _verdict(a, Label.WRONG_SCOPE_ASSET, 1, True, f"a cited item is about another asset than {asset_id}")
    first, second = (FIELD_FACTS.get(c.field_path) for c in a.cites)
    if first is None or second is None:
        return _verdict(
            a, Label.NOT_ENOUGH_INFO, 1, True, "a compared field has no registered meaning, so its unit and window cannot be checked"
        )
    if first.unit != second.unit or a.unit != first.unit:
        return _verdict(a, Label.WRONG_SCOPE_UNIT, 1, True, "the compared fields do not share the unit the assertion states")
    if first.window != second.window or a.window != first.window:
        return _verdict(a, Label.WRONG_SCOPE_TIME, 1, True, "the compared fields do not cover the window the assertion states")
    left = _number(found[0], a.cites[0].field_path)
    if isinstance(left, str):
        return _verdict(a, Label.NOT_ENOUGH_INFO, 1, True, left)
    right = _number(found[1], a.cites[1].field_path)
    if isinstance(right, str):
        return _verdict(a, Label.NOT_ENOUGH_INFO, 1, True, right)
    shown = f"{a.cites[0].field_path} is {plain(left)}, {a.cites[1].field_path} is {plain(right)}"
    return _verdict(a, Label.SUPPORTS if _compare(str(a.op), left, right) else Label.REFUTES, 1, True, shown)


def _text_values(item: EvidenceItem) -> list[str]:
    return [unicodedata.normalize("NFC", v) for _, v in item.fields if isinstance(v, str)] + [
        unicodedata.normalize("NFC", w) for w in item.warnings
    ]


def _statement(a: Assertion, found: tuple[EvidenceItem, ...], asset_id: str) -> Verdict:
    if not all(_same_asset(item, asset_id) for item in found):
        return _verdict(a, Label.WRONG_SCOPE_ASSET, 1, True, f"a cited item is about another asset than {asset_id}")
    quote = unicodedata.normalize("NFC", str(a.quote))
    holder = next((text for item in found for text in _text_values(item) if quote in text), None)
    if holder is None:
        return _verdict(a, Label.NOT_ENOUGH_INFO, 1, True, "the quoted words are not in any text of the cited items")
    words = unicodedata.normalize("NFC", str(a.text))
    if _phrase(NEGATIONS, quote) and not _phrase(NEGATIONS, words):
        return _verdict(a, Label.REFUTES, 2, True, "the quoted words are negated and the assertion is not")
    if _phrase(HEDGES, quote) and not _phrase(HEDGES, words):
        return _verdict(a, Label.WRONG_SCOPE_QUALIFIER, 2, True, "the quoted words are hedged and the assertion is not")
    if _phrase(QUALIFIERS, quote) and not _phrase(QUALIFIERS, words):
        return _verdict(a, Label.WRONG_SCOPE_QUALIFIER, 2, True, "the quoted words carry a limit that the assertion drops")
    return _verdict(a, Label.SUPPORT_UNVERIFIED, 3, True, "the quote is in the cited items and no validated judge has read the claim")


def ground_one(a: Assertion, items: Mapping[str, EvidenceItem], asset_id: str) -> Verdict:
    """The verdict on one assertion against the items the arm was shown."""
    found = _cited(a, items)
    if found is None:
        return _verdict(a, Label.NOT_ENOUGH_INFO, 1, False, "a cited item was not among the items shown")
    if a.kind is AssertionKind.OPINION:
        return _verdict(a, Label.OPINION_CONTEXT, 3, True, "an opinion is shown as context and is never support")
    if a.kind is AssertionKind.NUMERIC:
        return _numeric(a, found, asset_id)
    if a.kind is AssertionKind.DIRECTION:
        return _direction(a, found, asset_id)
    if a.kind is AssertionKind.COMPARISON:
        return _comparison(a, found, asset_id)
    return _statement(a, found, asset_id)


_NUMBER = re.compile(r"(?<![A-Za-z0-9.])[+-]?\d[\d,]*(?:\.\d+)?%?(?![A-Za-z0-9])")
_EVIDENCE = re.compile(EVIDENCE_ID)


def _decimal(text: str) -> Decimal | None:
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _shown_numbers(items: Mapping[str, EvidenceItem]) -> set[Decimal]:
    shown = {n for item in items.values() for _, v in item.fields if isinstance(v, str) and (n := _decimal(v)) is not None}
    return shown | {n * 100 for n in shown}


def scan_numbers(text: str, items: Mapping[str, EvidenceItem]) -> tuple[str, ...]:
    """The numbers in a free text that no shown field holds (a percent sign allows the field times a hundred), in order."""
    shown = _shown_numbers(items)
    cleaned = _EVIDENCE.sub(" ", text)
    return tuple(t for t in _NUMBER.findall(cleaned) if Decimal(t.removesuffix("%").replace(",", "")) not in shown)


def ground(assertions: tuple[Assertion, ...], items: Mapping[str, EvidenceItem], asset_id: str, free_text: str = "") -> Report:
    """Every assertion of an answer against the items shown, and the numbers of its free words that nothing shown holds."""
    verdicts = tuple(ground_one(a, items, asset_id) for a in assertions)
    return Report(verdicts, scan_numbers(free_text, items) if free_text else ())
