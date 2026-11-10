"""Typed answer: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import typed
from tests.mutation_sweep import survivors
from tests.typed_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(typed) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "|str+~|'\\u200c\\u200d'|0": "adds a tilde to the two allowed joiners; the allowance is only read for characters of category Cf or Co, and a tilde is neither",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.typed", "tests.typed_checks")
    assert total > 150, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


from decimal import Decimal  # noqa: E402

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from hoshitori import jsonnum  # noqa: E402


def _places(text: str) -> int:
    """The decimal places of a plain decimal written as text, trailing zeros counted (they are places as written): an independent count,
    by string."""
    if "." not in text:
        return 0
    return len(text.split(".")[1])


@settings(max_examples=300, deadline=None)
@given(st.integers(-5000, 5000), st.integers(0, 6), st.integers(0, 6))
def test_a_number_is_accepted_exactly_when_it_is_in_bounds_and_has_few_enough_places(units: int, written: int, allowed: int) -> None:
    value = Decimal(units).scaleb(-written)
    text = format(value, "f")
    schema = typed.Number(Decimal("-10"), Decimal("10"), allowed)
    want = Decimal(-10) <= value <= Decimal(10) and _places(text) <= allowed
    try:
        got = typed.validate(schema, jsonnum.parse(text.encode()))
        ok = got == value
    except typed.TypedError:
        ok = False
    assert ok == want, (text, allowed)


@settings(max_examples=200, deadline=None)
@given(st.lists(st.text(alphabet="abcxyz", min_size=1, max_size=4), max_size=6))
def test_a_unique_list_is_accepted_exactly_when_it_has_no_repeat(items: list[str]) -> None:
    import json

    schema = typed.Items(typed.Text(4), 0, 6, True)
    try:
        typed.validate(schema, jsonnum.parse(json.dumps(items).encode()))
        ok = True
    except typed.TypedError:
        ok = False
    assert ok == (len(set(items)) == len(items))
