"""HTTP transport: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from address_flag_check import transport
from tests.mutation_sweep import survivors
from tests.transport_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(transport) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "Caller.request|int+1|1|0": (
        "range(retries + 2): the iteration with attempt == retries always returns or raises, so the extra one is never reached"
    ),
    "Caller.request|Or->And|last or SourceError('budget_exhausted')|0": (
        "the raise after the loop is unreachable: retries < 0 is refused, and the last iteration (attempt == retries) returns or raises"
    ),
    "Caller.request|statement dropped|raise last or SourceError('budget_exhausted')|0": (
        "the raise after the loop is unreachable: retries < 0 is refused, and the last iteration (attempt == retries) returns or raises"
    ),
    "Caller.request|str+~|'budget_exhausted'|0": (
        "the raise after the loop is unreachable: retries < 0 is refused, and the last iteration (attempt == retries) returns or raises"
    ),
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("address_flag_check.transport", "tests.transport_checks")
    assert total > 150, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
