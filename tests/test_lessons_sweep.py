"""Every small change of ``hoshitori.lessons`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

FROZEN = "the flag only makes the object read-only; no check can tell the difference"
EQUAL = "an exact tie of two floating-point numbers does not occur in a test"
EQUIVALENT: dict[str, str] = {
    "Lesson|True<->False|True|0": FROZEN,
    "Protocol|True<->False|True|0": FROZEN,
    "VersionView|True<->False|True|0": FROZEN,
    "Book._family|str+~|'HARM'|0": "a stream stopped by harm has a p-value of about one whatever the rule says, so counting it with its data or with one is the same",
    "Book.decide|Gt->GtE|mean > 0|0": "a mean of exactly zero has an adjusted p-value of one half, which fails the other condition",
    "Book.decide|LtE->Lt|adjusted <= self.protocol.alpha|0": EQUAL,
    "Book.decide|LtE->Lt|adjusted <= self.protocol.alpha|1": EQUAL,
    "Book.decide|Gt->GtE|mean > 0|1": "a mean of exactly zero has an adjusted p-value of one half, which fails the other condition",
    "Book.decide|LtE->Lt|null_p <= self.protocol.alpha|0": EQUAL,
    "Book.decide|LtE->Lt|null_p <= self.protocol.alpha|1": EQUAL,
    "Book.holdback_day|Lt->LtE|high < -self.protocol.margin|0": EQUAL,
    "_beta_continued_fraction|Gt->GtE|abs(c) > tiny|0": "a value of exactly 1e-300 does not occur",
    "_beta_continued_fraction|Gt->GtE|abs(d) > tiny|0": "a value of exactly 1e-300 does not occur",
    "_beta_continued_fraction|Gt->GtE|abs(d) > tiny|1": "a value of exactly 1e-300 does not occur",
    "_beta_continued_fraction|Lt->LtE|abs(d * c - 1.0) < 1e-14|0": EQUAL,
    "_beta_continued_fraction|Mult->Add|d * c|1": "the test only decides when to stop iterating; the fraction has converged to full precision either way",
    "_beta_continued_fraction|Sub->Add|d * c - 1.0|0": "the test only decides when to stop iterating; the fraction has converged to full precision either way",
    "_beta_continued_fraction|int+1|300|0": "the iteration limit is never reached for the degrees of freedom of a test",
    "_beta_continued_fraction|statement dropped|break|0": "the test only decides when to stop iterating; the extra iterations change nothing at full precision",
    "_beta_continued_fraction|statement dropped|c = c if abs(c) > tiny else tiny|0": "the guard is for a value of exactly zero, which does not occur for these inputs",
    "always_valid_p|int+1|1|1": "one look more than there are days repeats the full prefix, which is already counted",
    "interim|Lt->LtE|high < 0|0": EQUAL,
    "interim|Lt->LtE|high < protocol.smallest_effect|0": EQUAL,
    "newey_west_p|int+1|1|1": "the Bartlett weight of the lag after the last one is zero, so reading it changes nothing",
    "t_upper_tail|Add->Sub|a + 1.0|0": "each branch is valid everywhere; the switch point only chooses the faster-converging one",
    "t_upper_tail|Add->Sub|a + b|0": "each branch is valid everywhere; the switch point only chooses the faster-converging one",
    "t_upper_tail|Gt->GtE|t > 0|0": "a statistic of exactly zero returns one half before this line",
    "t_upper_tail|Lt->LtE|x < (a + 1.0) / (a + b + 2.0)|0": EQUAL,
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.lessons", "tests.lessons_checks")
    assert total > 150, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}"
