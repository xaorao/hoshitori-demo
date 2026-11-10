"""First-layer card lint: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import card_lint
from tests.card_lint_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(card_lint) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "lint_featured|Gt->GtE|total(card) > FEATURED_WORDS|0": "with every part at its limit a featured card has 89 words, so its total cannot be 90 or more",
    "lint_featured|statement dropped|out.append(f'the card has {total(card)} words, more than {FEATURED_WORDS}')|0": (
        "the total of a featured card cannot exceed 90 while each part is within its limit; the check stays so a raised limit cannot break the total"
    ),
    "_text|str+~|' '|0": "the words of the fields are joined by a space; any other separator that is not a letter or digit joins them as well",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.card_lint", "tests.card_lint_checks")
    assert total > 40, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
