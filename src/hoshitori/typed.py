"""The typed answer: what a model may say, as a schema, and a strict check of what it said (``AGENT_PROTOCOL`` section 3).

A model stage names the shape of its answer with the vocabulary below and nothing else; the answer is read with the strict JSON reader
(``jsonnum``: no duplicate key, no ``NaN``, every number kept as it was written) and checked against the schema. A schema also renders
itself, for the prompt that tells the model the shape and for a provider's response format; the check, not the provider, is what decides.

* **Closed.** An object has the members its schema names and no others; a list is bounded; a text is bounded; a number is bounded and has a
  number of places. Anything else is an error, and the first error is a path and a reason short enough to put into the one repair request.
* **No null, no default.** A JSON ``null`` is never an answer, a member that is absent stays absent, and nothing is filled in: a missing
  measurement is never turned into a zero.
* **Numbers are decimals.** A number is read from its text into a ``Decimal`` and never passes through a binary float; a whole number must
  be written as one (``5``, not ``5.0``). A number is kept as it was written, so its places are counted as it was written: a trailing zero
  is a place (``0.500`` has three), and so is each place of a zero's exponent (``0e-9`` has nine), which no normalising may drop. A number
  whose low bound is zero or more is written without a minus sign: a negative zero (``-0``, ``-0.0``) is not one there, since a record
  writes such a number as a decimal with no sign; where the low bound is negative it is taken as written.
* **Text is normalised to NFC** (the form the records use) and may hold a newline and a tab but no other control character. A text that
  is still not valid canonical text once normalised (a code point the records' Unicode 15.0 does not assign, a lone surrogate) is refused,
  in the words of ``canonical``, which is asked rather than copied; so is a member whose name is not, without the name, since an error
  that quoted it would carry it into the repair request and a call record.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from hoshitori import canonical, jsonnum

MAX_TEXT: Final = 20_000
MAX_ITEMS: Final = 200
MAX_FIELDS: Final = 64
MAX_PLACES: Final = 12
_FIELD_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
_WHOLE = re.compile(r"-?(?:0|[1-9][0-9]*)")


class SchemaError(ValueError):
    """A schema that cannot be used."""


class TypedError(ValueError):
    """An answer that does not fit its schema; the message is a path and a reason."""


@dataclass(frozen=True)
class Text:
    max_len: int
    min_len: int = 1
    one_of: tuple[str, ...] | None = None  # a closed list of the only texts allowed
    pattern: str | None = None  # the whole text must match

    def __post_init__(self) -> None:
        if isinstance(self.max_len, bool) or isinstance(self.min_len, bool) or not 0 <= self.min_len <= self.max_len <= MAX_TEXT:
            raise SchemaError(f"a text has 0 <= min_len <= max_len <= {MAX_TEXT}")
        if self.one_of is not None and (
            not self.one_of
            or len(set(self.one_of)) != len(self.one_of)
            or not all(isinstance(x, str) and self.min_len <= len(x) <= self.max_len for x in self.one_of)
        ):
            raise SchemaError("a closed list of texts is not empty, has no repeat, and each text fits the length")
        if self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error:
                raise SchemaError("the pattern of a text does not compile") from None


@dataclass(frozen=True)
class Whole:
    low: int
    high: int

    def __post_init__(self) -> None:
        if isinstance(self.low, bool) or isinstance(self.high, bool) or not isinstance(self.low, int) or not isinstance(self.high, int):
            raise SchemaError("the bounds of a whole number are whole numbers")
        if self.low > self.high:
            raise SchemaError("the low bound of a whole number is at most the high bound")


@dataclass(frozen=True)
class Number:
    low: Decimal
    high: Decimal
    places: int  # the most digits after the decimal point, as the number is written: trailing zeros and a zero's own are counted

    def __post_init__(self) -> None:
        for bound in (self.low, self.high):
            if not isinstance(bound, Decimal) or not bound.is_finite():
                raise SchemaError("the bounds of a number are finite decimals")
        if self.low > self.high:
            raise SchemaError("the low bound of a number is at most the high bound")
        if isinstance(self.places, bool) or not isinstance(self.places, int) or not 0 <= self.places <= MAX_PLACES:
            raise SchemaError(f"the places of a number are 0 to {MAX_PLACES}")


@dataclass(frozen=True)
class Flag:
    """True or false."""


@dataclass(frozen=True)
class Items:
    item: Schema
    low: int = 0
    high: int = 20
    unique: bool = False  # no two items equal; only for items that are texts, whole numbers or numbers

    def __post_init__(self) -> None:
        if isinstance(self.low, bool) or isinstance(self.high, bool) or not 0 <= self.low <= self.high <= MAX_ITEMS:
            raise SchemaError(f"a list has 0 <= low <= high <= {MAX_ITEMS} items")
        if self.unique and not isinstance(self.item, Text | Whole | Number):
            raise SchemaError("only a list of texts or numbers can be unique")


@dataclass(frozen=True)
class Field:
    name: str
    schema: Schema
    required: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _FIELD_NAME.fullmatch(self.name) is None:
            raise SchemaError("a member's name is 1 to 64 characters of a-z, 0-9 and _, starting with a letter")


@dataclass(frozen=True)
class Object:
    fields: tuple[Field, ...]

    def __post_init__(self) -> None:
        names = [f.name for f in self.fields]
        if not 1 <= len(names) <= MAX_FIELDS or len(set(names)) != len(names):
            raise SchemaError(f"an object has 1 to {MAX_FIELDS} members, each named once")


@dataclass(frozen=True)
class Tagged:
    """An object the check reads as ``base``, while a provider is told one closed shape for each value of one member, its tag.

    Each variant is an object of some of the base's members whose tag is a closed text of one value, so a provider that enforces the schema
    it is given cannot emit a member that the rule for that value forbids; what decides is still the check, which reads the base and leaves
    the rule of each value to its caller (``assertions.from_value``). The variants give the tag's values once each, in the base's order.
    """

    base: Object
    tag: str
    variants: tuple[Object, ...]

    def __post_init__(self) -> None:
        members = {f.name: f for f in self.base.fields}
        tag = members.get(self.tag)
        if tag is None or not isinstance(tag.schema, Text) or tag.schema.one_of is None:
            raise SchemaError("a tagged object's tag is a member of its base with a closed list of texts")
        values: list[str] = []
        for variant in self.variants:
            own = {f.name: f for f in variant.fields}
            mark = own.get(self.tag)
            if (
                mark is None
                or not mark.required
                or not isinstance(mark.schema, Text)
                or mark.schema.one_of is None
                or len(mark.schema.one_of) != 1
                or not set(own) <= set(members)
            ):
                raise SchemaError("each variant of a tagged object has its tag, with one value, and only members of its base")
            values.append(mark.schema.one_of[0])
        if tuple(values) != tag.schema.one_of:
            raise SchemaError("the variants of a tagged object give the tag's values once each, in the order of the base")


type Schema = Text | Whole | Number | Flag | Items | Object | Tagged


def closed(words: tuple[str, ...]) -> Text:
    """One of a closed list of words: its length bounds are those of the words, so they are never written twice."""
    return Text(max(len(w) for w in words), min(len(w) for w in words), words)


def _fail(path: str, reason: str) -> TypedError:
    return TypedError(f"{path}: {reason}")


def not_canonical(text: str) -> str | None:
    """Why ``text`` is not valid canonical text, in the words of ``canonical`` (it is asked, so every rule of the record profile applies,
    the Unicode 15.0 repertoire included), or ``None`` if it is."""
    try:
        canonical.dumps({"": text})
    except canonical.CanonicalError as exc:
        return str(exc)
    return None


JOINERS: Final = "\u200c\u200d"  # the zero-width joiners that scripts and emoji need
# characters that are not format or private-use characters and yet draw nothing: the combining grapheme joiner, the Hangul fillers and the
# braille blank. A span made of them looks empty to a reader and is not empty to a check that counts characters.
BLANKS: Final = "\u034f\u115f\u1160\u3164\uffa0\u2800"


def hidden_character(text: str) -> str | None:
    """The first character of ``text`` that shows nothing, as ``U+XXXX``, or ``None``: a format character (Unicode category Cf: a
    zero-width space, a word joiner, a soft hyphen, a byte-order mark, a Tag character), a private-use one (Co), which can carry words a
    reader never sees, or one of ``BLANKS``, which draw nothing. The two joiners that scripts and emoji need stay."""
    for c in text:
        if c in BLANKS or (c not in JOINERS and unicodedata.category(c) in ("Cf", "Co")):
            return f"U+{ord(c):04X}"
    return None


def _text(schema: Text, value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise _fail(path, "expected text")
    text = unicodedata.normalize("NFC", value)
    if any((ord(c) < 32 and c not in ("\n", "\t")) or ord(c) == 127 for c in text):
        raise _fail(path, "contains a control character")
    reason = not_canonical(text)
    if reason is not None:
        raise _fail(path, f"is not valid canonical text: {reason}")
    hidden = hidden_character(text)
    if hidden is not None:
        raise _fail(path, f"contains a hidden formatting character ({hidden})")
    if schema.one_of is not None and text not in schema.one_of:
        raise _fail(path, f"the text is not one of {list(schema.one_of)}")
    if not schema.min_len <= len(text) <= schema.max_len:
        raise _fail(path, f"the text is {len(text)} characters, not {schema.min_len} to {schema.max_len}")
    if schema.pattern is not None and re.fullmatch(schema.pattern, text) is None:
        raise _fail(path, "the text does not match the pattern")
    return text


def _whole(schema: Whole, value: Any, path: str) -> int:
    if not isinstance(value, jsonnum.Num) or _WHOLE.fullmatch(value.text) is None:
        raise _fail(path, "expected a whole number")
    number = int(value.text)
    if not schema.low <= number <= schema.high:
        raise _fail(path, f"the number is not from {schema.low} to {schema.high}")
    return number


def _number(schema: Number, value: Any, path: str) -> Decimal:
    if not isinstance(value, jsonnum.Num):
        raise _fail(path, "expected a number")
    try:
        number = Decimal(value.text)
    except InvalidOperation:
        raise _fail(path, "expected a number") from None
    if not number.is_finite():
        raise _fail(path, "expected a number")
    if not schema.low <= number <= schema.high:
        raise _fail(path, f"the number is not from {format(schema.low, 'f')} to {format(schema.high, 'f')}")
    exponent = number.as_tuple().exponent  # as written: normalising would drop a trailing zero, and every place of a zero
    if isinstance(exponent, int) and -exponent > schema.places:
        raise _fail(path, f"the number has more than {schema.places} decimal places")
    if schema.low >= 0 and number.is_signed():  # only a negative zero can get here: a record writes it with no sign, or not at all
        raise _fail(path, f"a number from {format(schema.low, 'f')} to {format(schema.high, 'f')} is written without a minus sign")
    return number


def validate(schema: Schema, value: Any, path: str = "$") -> Any:
    """The value as the schema reads it (texts normalised, numbers as ``int`` or ``Decimal``), or the first ``TypedError``."""
    if value is None:
        raise _fail(path, "null is not an answer")
    if isinstance(schema, Text):
        return _text(schema, value, path)
    if isinstance(schema, Whole):
        return _whole(schema, value, path)
    if isinstance(schema, Number):
        return _number(schema, value, path)
    if isinstance(schema, Flag):
        if not isinstance(value, bool):
            raise _fail(path, "expected true or false")
        return value
    if isinstance(schema, Items):
        if not isinstance(value, list):
            raise _fail(path, "expected a list")
        if not schema.low <= len(value) <= schema.high:
            raise _fail(path, f"the list has {len(value)} items, not {schema.low} to {schema.high}")
        items = [validate(schema.item, v, f"{path}[{i}]") for i, v in enumerate(value)]
        if schema.unique and len(set(items)) != len(items):
            raise _fail(path, "the list repeats an item")
        return items
    if isinstance(schema, Tagged):
        return validate(schema.base, value, path)
    if not isinstance(value, dict):
        raise _fail(path, "expected an object")
    out: dict[str, Any] = {}
    for field in schema.fields:
        if field.name in value:
            out[field.name] = validate(field.schema, value[field.name], f"{path}.{field.name}")
        elif field.required:
            raise _fail(f"{path}.{field.name}", "is missing")
    known = {f.name for f in schema.fields}
    for name in value:
        if name not in known:
            reason = not_canonical(name)
            if reason is not None:  # the error would carry the name into the repair request and a call record
                raise _fail(path, f"a member's name is not valid canonical text: {reason}")
            raise _fail(f"{path}.{name}", "is not a member of this answer")
    return out


def parse(schema: Schema, raw: bytes) -> Any:
    """Read ``raw`` as strict JSON and check it against the schema."""
    try:
        value = jsonnum.parse(raw)
    except jsonnum.JsonError as exc:
        raise _fail("$", f"not valid JSON ({exc})") from None
    return validate(schema, value)


def json_schema(schema: Schema) -> dict[str, Any]:
    """The schema as a JSON schema for a provider's response format: plain JSON with whole numbers only (a record holds no float).

    A bound that is not a whole number is left out, and so is ``uniqueItems``: the endpoint's decoder refuses a schema that carries it
    (``Grammar error: Unimplemented keys``, CF-63), and every other keyword used here it accepts. A tagged object is an ``anyOf`` of its
    closed variants, which the endpoint was seen to enforce together with ``enum`` and ``additionalProperties`` (a probe asked for members
    and values outside them and got none). ``validate`` checks every bound, and that a list has no repeat, whatever the provider enforces;
    the prompt (``describe``) says the items are all different."""
    if isinstance(schema, Text):
        out: dict[str, Any] = {"type": "string", "minLength": schema.min_len, "maxLength": schema.max_len}
        if schema.one_of is not None:
            out["enum"] = list(schema.one_of)
        if schema.pattern is not None:
            out["pattern"] = schema.pattern
        return out
    if isinstance(schema, Whole):
        return {"type": "integer", "minimum": schema.low, "maximum": schema.high}
    if isinstance(schema, Number):
        number: dict[str, Any] = {"type": "number"}
        if schema.low == schema.low.to_integral_value():
            number["minimum"] = int(schema.low)
        if schema.high == schema.high.to_integral_value():
            number["maximum"] = int(schema.high)
        return number
    if isinstance(schema, Flag):
        return {"type": "boolean"}
    if isinstance(schema, Items):
        return {"type": "array", "items": json_schema(schema.item), "minItems": schema.low, "maxItems": schema.high}
    if isinstance(schema, Tagged):
        return {"anyOf": [json_schema(variant) for variant in schema.variants]}
    return {
        "type": "object",
        "properties": {f.name: json_schema(f.schema) for f in schema.fields},
        "required": [f.name for f in schema.fields if f.required],
        "additionalProperties": False,
    }


def describe(schema: Schema) -> str:
    """The shape in one compact line, for the prompt that tells a model what to answer."""
    if isinstance(schema, Text):
        if schema.one_of is not None:
            return " | ".join(json_text(x) for x in schema.one_of)
        return f"text({schema.min_len}..{schema.max_len} characters)"
    if isinstance(schema, Whole):
        return f"whole number({schema.low}..{schema.high})"
    if isinstance(schema, Number):
        return f"number({format(schema.low, 'f')}..{format(schema.high, 'f')}, at most {schema.places} decimal places)"
    if isinstance(schema, Flag):
        return "true | false"
    if isinstance(schema, Items):
        return f"[{describe(schema.item)}] ({schema.low}..{schema.high} items{', all different' if schema.unique else ''})"
    if isinstance(schema, Tagged):
        return " | ".join(describe(variant) for variant in schema.variants)
    parts = [f"{json_text(f.name)}{'' if f.required else '?'}: {describe(f.schema)}" for f in schema.fields]
    return "{" + ", ".join(parts) + "}"


def json_text(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
