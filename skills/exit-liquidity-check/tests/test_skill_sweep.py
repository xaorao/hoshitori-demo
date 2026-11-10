"""The skill: envelope, verdict, confidence and refusals: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from exit_liquidity_check import skill
from tests.mutation_sweep import survivors
from tests.skill_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(skill) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "run|And->Or|size is None and liquidity is not None|0": (
        "have_cost is read only when liquidity or a quote exists; a quote needs a size and a liquidity that is not None is truthy, so the "
        "estimate exists whenever a size does, and with no size and no liquidity the status is already 'unavailable'"
    ),
    "run|And->Or|size is not None and (quote is not None or estimate is not None)|0": (
        "a quote or an estimate exists only when a size was given, so `size is not None` is already implied by the second operand; the "
        "operators differ only for a size with neither (a liquidity of exactly 0.0, which pool parsing never produces)"
    ),
    "run|str+~|'no_pool'|0": "the `reason` entry of a failure record is never read: only `source` and `text` reach the answer",
    "run|str+~|'reason'|0": "the `reason` key of a failure record is never read: only `source` and `text` reach the answer",
    "run|str+~|'reason'|1": "the `reason` key of a failure record is never read: only `source` and `text` reach the answer",
    "run|str+~|'reason'|2": "the `reason` key of a failure record is never read: only `source` and `text` reach the answer",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("exit_liquidity_check.skill", "tests.skill_checks")
    assert total > 300, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
