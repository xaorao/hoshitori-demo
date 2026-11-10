"""The asset registry: every check passes on the real module, and every small change of its code is noticed (or is harmless, and says why)."""

from __future__ import annotations

from hoshitori import registry
from hoshitori.registry import Registry
from hoshitori.registry_aliases import ALIASES
from tests.mutation_sweep import survivors
from tests.registry_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(registry) == []


def test_the_curated_spellings_are_well_formed() -> None:
    assert ALIASES, "the table is not empty"
    for operator, table in ALIASES.items():
        assert operator == operator.lower() and "-" not in operator and operator, operator
        for spelling, target in table.items():
            assert spelling == spelling.upper() and target == target.upper() and spelling != target, (operator, spelling)
            assert target not in table, f"{operator}: {spelling} leads to {target}, which is itself rewritten"
    Registry(aliases=ALIASES)  # the registry accepts the shipped table


# Changes that no check can notice because the code does the same thing either way. Each one was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_looks_like_another|int+1|1|0": "the script is the first word of a character's name, so splitting after one word or after two leaves the first word the same",
    "_list_hash|str+~|'instruments'|0": "a label inside the input of a hash: any name gives a hash that is as consistent",
    "_list_hash|str+~|'unmapped'|0": "a label inside the input of a hash: any name gives a hash that is as consistent",
    "_list_hash|int+1|0|0": "the order the unmapped entries are sorted in; the hash is the same on both sides whatever the order, and it is shown not to depend on it",
    "Registry.join|Or->And|row is None or mapping is None|0": "a row and its mapping are opened and closed at the same instants, so neither is ever missing alone",
    "Registry.join|Or->And|before is None or earlier is None|0": "a row and its mapping are opened and closed at the same instants, so neither is ever missing alone",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.registry", "tests.registry_checks")
    assert total > 800, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
