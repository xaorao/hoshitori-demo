"""The developments list of Today: every check passes on the real module, every small change is noticed, and no item carries a verdict."""

from __future__ import annotations

import re

from hoshitori import today_items
from tests.mutation_sweep import survivors
from tests.today_items_checks import failures, manifest

VERDICT_WORDS = re.compile(r"\b(buy|sell|long|short|bullish|bearish|true|false|confirmed|verified|supported)\b", re.IGNORECASE)


def test_real_module_passes_every_check() -> None:
    assert failures(today_items) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "updates|Or->And|i.first_seen or ''|0": "an update item always carries the time its event was first seen, so the empty-string fallback is never used",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.today_items", "tests.today_items_checks")
    assert total > 30, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


def test_an_unchecked_item_has_no_stance_probability_or_verdict_word() -> None:
    for item in today_items.items([manifest(("DOGE", "t9", "2026-11-06T10:00:00Z", "INCLUDED"))], []):
        text = " ".join((item.headline, item.unknown, item.reason or ""))
        assert VERDICT_WORDS.search(text) is None
