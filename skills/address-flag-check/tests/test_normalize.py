"""Address handling: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from address_flag_check import normalize
from tests.mutation_sweep import survivors
from tests.normalize_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(normalize) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "parse_address|statement dropped|raise _refuse('contains spaces, control or non-ASCII characters')|0": (
        "text that splits into several words holds whitespace, which is outside '!'..'~', so the next test raises the same refusal"
    ),
    "parse_address|str+~|'~'|0": "ch is one character, and no single character lies above '~' and at or below '~~', so the test is the same",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("address_flag_check.normalize", "tests.normalize_checks")
    assert total > 220, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
