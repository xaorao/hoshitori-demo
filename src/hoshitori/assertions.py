"""The typed assertion: how a model may state a number, a comparison or a direction (``GROUNDING_CONTRACT`` section 3).

A number is never parsed out of prose. A model that wants to say "the one-hour return was 0.4 percent" says it as an
assertion: the field it means (a path in a cited evidence item), what it claims of it (an operator and a value with a unit),
and over which window. The sentence a reader sees is rendered from a template out of exactly those parts, so the words and
the claim cannot differ, and ``grounding`` can check the claim against the cited field with arithmetic. Only an event, a
mechanism or an opinion carries the model's own words, and then it carries a quoted span of a cited text as well, because
those are the claims that the deterministic layer can only partly check.

The kinds and what each holds (anything else is refused):

* ``NUMERIC``: one cite, an operator (is, is about, above, at least, below, at most), a value, a unit and a window.
* ``COMPARISON``: two cites (the first field against the second), an operator of greater or less, a unit and a window; no
  value.
* ``DIRECTION``: one cite, ``rose``, ``fell`` or ``flat``, and a window; no value and no unit.
* ``EVENT`` and ``MECHANISM``: the model's words, a quoted span and at least one cite; no value.
* ``OPINION``: the model's words only (and optional cites); it is shown as context and never as support.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final

from hoshitori import typed

MAX_ASSERTIONS: Final = 12
GUIDE: Final = (
    "Assertions are optional: an empty list is allowed, and one assertion of the wrong shape makes the whole answer invalid, so state "
    "only what you are sure of. Give each assertion an id a1, a2 and so on. The kinds, and exactly what each holds: "
    "NUMERIC has op (eq, approx, gt, ge, lt or le), value, unit (ratio, percent, price or count), window (spot, 1h, 4h, 24h, 7d or 30d) "
    "and cites with exactly ONE entry. "
    "Copy every number exactly as it appears in the item, with all its digits. A field's window is the one its name says (ret_1h is 1h, "
    "ret_4h is 4h, ret_24h is 24h); a price, a count and atr14 are spot. "
    "COMPARISON has op (gt, ge, lt or le), unit, window and cites with exactly TWO entries, and no value. "
    "DIRECTION has op (rose, fell or flat), window and cites with exactly ONE entry, and no value and no unit. "
    "EVENT and MECHANISM have text, quote (words copied exactly from a cited item) and at least one cite, and no op, value, unit or "
    "window; if you have no quote, use OPINION. OPINION has only id, kind, text and an empty cites list. A cite is an object with "
    "evidence_id and field_path. Write one NUMERIC assertion for each number you state about the evidence."
)
NUMERIC_OPS: Final = ("eq", "approx", "gt", "ge", "lt", "le")
COMPARE_OPS: Final = ("gt", "ge", "lt", "le")
DIRECTION_OPS: Final = ("rose", "fell", "flat")
UNITS: Final = ("ratio", "percent", "price", "count")
WINDOWS: Final = ("spot", "1h", "4h", "24h", "7d", "30d")
EVIDENCE_ID: Final = "ev[a-z2-7]{26}"
FIELD_PATH: Final = "[A-Za-z0-9_.\\[\\]-]{1,120}"
_OPERATOR_WORDS: Final = {"eq": "is", "approx": "is about", "gt": "is above", "ge": "is at least", "lt": "is below", "le": "is at most"}
_DIRECTION_WORDS: Final = {"rose": "rose", "fell": "fell", "flat": "was flat"}
_UNIT_SUFFIX: Final = {"ratio": " (ratio)", "percent": "%", "price": " (price units)", "count": " (count)"}


class AssertionShapeError(ValueError):
    """An assertion that does not have the shape of its kind."""


class AssertionKind(StrEnum):
    NUMERIC = "NUMERIC"
    COMPARISON = "COMPARISON"
    DIRECTION = "DIRECTION"
    EVENT = "EVENT"
    MECHANISM = "MECHANISM"
    OPINION = "OPINION"


@dataclass(frozen=True)
class Cite:
    evidence_id: str
    field_path: str


@dataclass(frozen=True)
class Assertion:
    assertion_id: str
    kind: AssertionKind
    op: str | None
    value: Decimal | None
    unit: str | None
    window: str | None
    cites: tuple[Cite, ...]
    quote: str | None
    text: str | None


_CITE = typed.Object(
    (
        typed.Field("evidence_id", typed.Text(28, 28, None, EVIDENCE_ID)),
        typed.Field("field_path", typed.Text(120, 1, None, FIELD_PATH)),
    )
)
_ID = typed.Field("id", typed.Text(3, 2, None, "a[0-9]{1,2}"))
_VALUE = typed.Number(Decimal(-(10**12)), Decimal(10**12), 12)
_QUOTE, _TEXT = typed.Text(300), typed.Text(400)


def _kind(kind: AssertionKind, *members: typed.Field) -> typed.Object:
    """The one shape a provider is told for ``kind``: exactly the members its rule (``from_value``) requires, each required, no other."""
    return typed.Object((_ID, typed.Field("kind", typed.closed((kind.value,))), *members))


_FLAT = typed.Object(
    (
        _ID,
        typed.Field("kind", typed.closed(tuple(k.value for k in AssertionKind))),
        typed.Field("op", typed.closed(tuple(dict.fromkeys((*NUMERIC_OPS, *DIRECTION_OPS)))), False),
        typed.Field("value", _VALUE, False),
        typed.Field("unit", typed.closed(UNITS), False),
        typed.Field("window", typed.closed(WINDOWS), False),
        typed.Field("cites", typed.Items(_CITE, 0, 2)),
        typed.Field("quote", _QUOTE, False),
        typed.Field("text", _TEXT, False),
    )
)
# The check reads the flat object and ``from_value`` applies the rule of each kind; a provider is told the rule itself, one closed shape
# per kind, so a provider that enforces its schema (the endpoint does: C1) cannot give a kind a member its rule forbids.
_UNIT, _WINDOW = typed.Field("unit", typed.closed(UNITS)), typed.Field("window", typed.closed(WINDOWS))
ASSERTION_SCHEMA: Final = typed.Tagged(
    _FLAT,
    "kind",
    (
        _kind(
            AssertionKind.NUMERIC,
            typed.Field("op", typed.closed(NUMERIC_OPS)),
            typed.Field("value", _VALUE),
            _UNIT,
            _WINDOW,
            typed.Field("cites", typed.Items(_CITE, 1, 1)),
        ),
        _kind(
            AssertionKind.COMPARISON,
            typed.Field("op", typed.closed(COMPARE_OPS)),
            _UNIT,
            _WINDOW,
            typed.Field("cites", typed.Items(_CITE, 2, 2)),
        ),
        _kind(
            AssertionKind.DIRECTION, typed.Field("op", typed.closed(DIRECTION_OPS)), _WINDOW, typed.Field("cites", typed.Items(_CITE, 1, 1))
        ),
        _kind(
            AssertionKind.EVENT, typed.Field("cites", typed.Items(_CITE, 1, 2)), typed.Field("quote", _QUOTE), typed.Field("text", _TEXT)
        ),
        _kind(
            AssertionKind.MECHANISM,
            typed.Field("cites", typed.Items(_CITE, 1, 2)),
            typed.Field("quote", _QUOTE),
            typed.Field("text", _TEXT),
        ),
        _kind(AssertionKind.OPINION, typed.Field("cites", typed.Items(_CITE, 0, 2)), typed.Field("text", _TEXT)),
    ),
)
ASSERTIONS_SCHEMA: Final = typed.Items(ASSERTION_SCHEMA, 0, MAX_ASSERTIONS)


def _shape(ident: str, message: str) -> AssertionShapeError:
    return AssertionShapeError(f"assertion {ident}: {message}")


def from_value(raw: dict[str, Any]) -> Assertion:
    """The assertion in an answer already checked against ``ASSERTION_SCHEMA``, or the way in which it has the wrong shape for its kind."""
    ident = raw["id"]
    kind = AssertionKind(raw["kind"])
    op, value, unit, window = raw.get("op"), raw.get("value"), raw.get("unit"), raw.get("window")
    quote, text = raw.get("quote"), raw.get("text")
    cites = tuple(Cite(c["evidence_id"], c["field_path"]) for c in raw["cites"])
    if kind is AssertionKind.NUMERIC:
        if (
            op not in NUMERIC_OPS
            or value is None
            or unit is None
            or window is None
            or len(cites) != 1
            or quote is not None
            or text is not None
        ):
            raise _shape(ident, "a numeric assertion has an operator, a value, a unit, a window and one cite and nothing else")
    elif kind is AssertionKind.COMPARISON:
        if (
            op not in COMPARE_OPS
            or value is not None
            or unit is None
            or window is None
            or len(cites) != 2
            or quote is not None
            or text is not None
        ):
            raise _shape(ident, "a comparison has an operator of greater or less, a unit, a window and two cites and nothing else")
    elif kind is AssertionKind.DIRECTION:
        if (
            op not in DIRECTION_OPS
            or value is not None
            or unit is not None
            or window is None
            or len(cites) != 1
            or quote is not None
            or text is not None
        ):
            raise _shape(ident, "a direction has rose, fell or flat, a window and one cite and nothing else")
    elif kind is AssertionKind.OPINION:
        if text is None or op is not None or value is not None or unit is not None or window is not None or quote is not None:
            raise _shape(ident, "an opinion has words and no operator, value, unit, window or quote")
    elif text is None or quote is None or not cites or op is not None or value is not None or unit is not None or window is not None:
        raise _shape(ident, "an event or a mechanism has words, a quote and at least one cite and no operator, value, unit or window")
    return Assertion(ident, kind, op, value, unit, window, cites, quote, text)


def from_values(raw: list[dict[str, Any]]) -> tuple[Assertion, ...]:
    """Every assertion of an answer, in order; no id is used twice."""
    found = tuple(from_value(item) for item in raw)
    if len({a.assertion_id for a in found}) != len(found):
        raise AssertionShapeError("the ids of assertions are all different")
    return found


def plain(value: Decimal) -> str:
    """A decimal as plain digits without trailing zeros (``0.50`` is ``0.5``, ``100`` stays ``100``, a zero of any sign is ``0``)."""
    return "0" if value == 0 else format(value.normalize(), "f")


def render(a: Assertion) -> str:
    """The sentence a reader sees for an assertion, made from its parts. Words are the model's only for events, mechanisms and opinions."""
    if a.kind is AssertionKind.NUMERIC:
        return (
            f"{a.cites[0].field_path} {_OPERATOR_WORDS[str(a.op)]} {plain(Decimal(str(a.value)))}{_UNIT_SUFFIX[str(a.unit)]} {_window(a)}"
        )
    if a.kind is AssertionKind.COMPARISON:
        return f"{a.cites[0].field_path} {_OPERATOR_WORDS[str(a.op)]} {a.cites[1].field_path}{_UNIT_SUFFIX[str(a.unit)]} {_window(a)}"
    if a.kind is AssertionKind.DIRECTION:
        return f"{a.cites[0].field_path} {_DIRECTION_WORDS[str(a.op)]} {_window(a)}"
    return str(a.text)


def _window(a: Assertion) -> str:
    return "now" if a.window == "spot" else f"over the last {a.window}"
