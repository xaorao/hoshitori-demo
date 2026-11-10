"""The read-only ledger reader: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

import codecs
from pathlib import Path

import pytest

from hoshitori import ledger_reader
from tests.ledger_reader_checks import SCENARIOS, failures
from tests.mutation_sweep import survivors


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str, tmp_path: Path) -> None:
    assert SCENARIOS[name](ledger_reader, tmp_path) == []


def test_real_module_passes_every_check() -> None:
    assert failures(ledger_reader) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
_UTF_8 = "the codec name is looked up after normalising it, and 'utf-8~' is the same codec as 'utf-8' (test_the_codec_name)"
EQUIVALENT: dict[str, str] = {
    "LedgerReader._saved_state|str+~|'utf-8'|0": _UTF_8,
    "check_format|str+~|'utf-8'|0": _UTF_8,
    "LedgerReader._scan_segments|Gt->GtE|index > 0|0": (
        "an empty first segment: stopping there or scanning no bytes with no segment before it gives no record and no problem "
        "(segment.scan_segment), the same result"
    ),
}


# A change of a codec's name is harmless, but on a loaded machine the checks can run out of time on it and the sweep then counts it as
# noticed, so these two may be missing from the survivors without the list being stale. The `index > 0` entry is not tolerated: it is exact.
LOAD_SENSITIVE: set[str] = {"LedgerReader._saved_state|str+~|'utf-8'|0", "check_format|str+~|'utf-8'|0"}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.ledger_reader", "tests.ledger_reader_checks")
    assert total > 100, f"only {total} places to change: the sweep is not looking at the module"
    unexplained, stale = set(alive) - set(EQUIVALENT), set(EQUIVALENT) - set(alive)
    assert not unexplained and stale <= LOAD_SENSITIVE, (
        f"unexplained survivors: {sorted(unexplained)}; stale entries: {sorted(stale - LOAD_SENSITIVE)}"
    )


def test_the_codec_name() -> None:
    assert codecs.lookup("utf-8~").name == codecs.lookup("utf-8").name
