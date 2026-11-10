"""A forecast written in a person's words, as a draft of a forecast contract (contract 34.9).

Nothing is completed silently. The draft reads from the words only what they say: which asset, which way, how big a move or what level,
and over how long. Anything they leave open, or say in a way that could mean two things ("this week"), is listed as a choice for the
person to confirm. A forecast that names no event the oracle could resolve is UNRESOLVED and says so. Only a direction over a stated
horizon fits contract D as it stands; a stated size or level has no contract yet, and the draft says what would be dropped if the person
chose direction only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

UP: Final = (
    "rise",
    "rises",
    "rising",
    "up",
    "increase",
    "higher",
    "gain",
    "gains",
    "climb",
    "climbs",
    "pump",
    "pumps",
    "rally",
    "rallies",
    "above",
    "exceed",
)
DOWN: Final = (
    "fall",
    "falls",
    "falling",
    "down",
    "drop",
    "drops",
    "decline",
    "declines",
    "lower",
    "lose",
    "loses",
    "crash",
    "crashes",
    "dump",
    "dumps",
    "below",
)
SCALE: Final = {"k": Decimal(1_000), "m": Decimal(1_000_000), "b": Decimal(1_000_000_000)}
HOURS: Final = {"hour": 1, "hours": 1, "h": 1, "day": 24, "days": 24, "d": 24, "week": 168, "weeks": 168}
OPEN_HORIZON: Final = (
    "today",
    "tomorrow",
    "tonight",
    "this week",
    "next week",
    "this month",
    "next month",
    "weekend",
    "soon",
    "eventually",
)
OFFERED: Final = ("24 hours", "7 days")
_WORD = re.compile(r"[A-Za-z']+")
_HORIZON = re.compile(r"(?<![\d.,])(\d+)\s*(hours?|h|days?|d|weeks?)\b", re.IGNORECASE)
_PERCENT = re.compile(r"(?<![\d.,])(\d+(?:\.\d+)?)\s?%")
_LEVEL = re.compile(r"(\$)?(?<![\d.,])(\d[\d,]*(?:\.\d+)?)\s?([kmb])?\b(?![\d%])", re.IGNORECASE)
_BEFORE_LEVEL = ("above", "below", "over", "under", "reach", "reaches", "to", "at", "hit", "hits", "exceed")


@dataclass(frozen=True)
class Choice:
    field: str
    question: str
    options: tuple[str, ...]


@dataclass(frozen=True)
class Sketch:
    status: str  # RESOLVABLE, NEEDS_CHOICES or UNRESOLVED
    contract: str | None  # D when the words fit it as they are
    asset: str | None
    direction: str | None  # UP or DOWN
    magnitude: str | None  # "15 percent" or "level 100000", as written, never turned into a score
    horizon_hours: int | None
    choices: tuple[Choice, ...]
    reason: str | None  # why it is UNRESOLVED


def _asset(text: str, assets: tuple[str, ...]) -> tuple[str | None, tuple[str, ...]]:
    seen = tuple(dict.fromkeys(w for w in (m.group() for m in _WORD.finditer(text)) if w in assets and w.isupper()))
    return (seen[0] if len(seen) == 1 else None), seen


def _direction(text: str) -> tuple[str | None, bool]:
    """The direction the words state, and whether they speak of one at all (they may speak of both)."""
    words = {m.group().lower() for m in _WORD.finditer(text)}
    up, down = bool(words & set(UP)), bool(words & set(DOWN))
    return (None if up == down else ("UP" if up else "DOWN")), up or down


def _horizon(text: str) -> tuple[int | None, bool]:
    """The hours when the words state a count of units, and whether a looser time word is present."""
    found = _HORIZON.search(text)
    loose = any(re.search(rf"\b{re.escape(w)}\b", text, re.IGNORECASE) for w in OPEN_HORIZON) or bool(
        re.search(r"\bby\s+\w+", text, re.IGNORECASE)
    )
    if found is None:
        return None, loose
    return int(found.group(1)) * HOURS[found.group(2).lower()], loose


def _magnitude(text: str) -> str | None:
    pct = _PERCENT.search(text)
    if pct is not None:
        return f"{pct.group(1)} percent"
    for m in _LEVEL.finditer(text):
        if _HORIZON.match(text, m.start()):
            continue  # a count of hours or days is a horizon, not a level
        before = text[: m.start()].split()[-1:] or [""]
        if m.group(1) or m.group(3) or before[0].lower() in _BEFORE_LEVEL:
            value = Decimal(m.group(2).replace(",", ""))
            scale = SCALE.get((m.group(3) or "").lower(), Decimal(1))
            return "level " + format((value * scale).normalize(), "f")
    return None


def sketch(words: str, assets: tuple[str, ...]) -> Sketch:
    """The draft of a forecast contract from a forecast's words; ``assets`` are the symbols the registry knows, in capitals."""
    asset, seen = _asset(words, assets)
    direction, spoken = _direction(words)
    magnitude = _magnitude(words)
    hours, loose = _horizon(words)
    if not spoken and magnitude is None:
        return Sketch(
            "UNRESOLVED", None, asset, None, None, hours, (), "the words name no direction, size or level that a price series could settle"
        )
    choices: list[Choice] = []
    if asset is None:
        question = "Which asset is meant?" if not seen else "Which of these assets is the forecast about?"
        choices.append(Choice("asset", question, seen))
    if direction is None:
        choices.append(Choice("direction", "Does the forecast say up or down?", ("up", "down")))
    if hours is None:
        why = (
            "Over how long? The words say something looser than a number of hours or days."
            if loose
            else "Over how long? The words give no time."
        )
        choices.append(Choice("horizon", why, OFFERED))
    if magnitude is not None:
        choices.append(
            Choice(
                "size",
                f"A size is stated ({magnitude}); no contract scores it yet. Score the direction only?",
                ("direction only", "leave it unscored"),
            )
        )
    status = "NEEDS_CHOICES" if choices else "RESOLVABLE"
    return Sketch(status, "D" if not choices else None, asset, direction, magnitude, hours, tuple(choices), None)
