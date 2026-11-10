"""The command line against every small change of ``__main__.py``: the checks pass on the real module and each change of its code is noticed or
listed as harmless."""

from __future__ import annotations

import ryo_verdict_record.__main__ as main
from tests.main_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(main) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "|statement dropped|raise SystemExit(main())|0": "it runs only when the module is run as __main__, which an imported mutant never is; main() itself is checked directly",
    "|str+~|'__main__'|0": "the guard compares __name__ with a string; an imported module is never named '__main__' or '__main__~', so the branch is not taken either way",
    "main|str+~|'utf-8'|0": "Python's codec lookup normalises the name 'utf-8~' to 'utf-8' (codecs.lookup('utf-8~').name == 'utf-8')",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("ryo_verdict_record.__main__", "tests.main_checks")
    assert total > 60, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
