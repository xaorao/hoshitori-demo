"""Time-stamp counting and levels against their scenarios, each rule protected by a mutant."""

from __future__ import annotations

import pytest

from hoshitori import timing
from tests.mutation import mutant
from tests.timing_checks import failures

MUTATIONS = {
    "the window start is exclusive": (
        "committed_at - before <= proof.gen_time <= committed_at + after",
        "committed_at - before < proof.gen_time <= committed_at + after",
    ),
    "the window end is exclusive": (
        "committed_at - before <= proof.gen_time <= committed_at + after",
        "committed_at - before <= proof.gen_time < committed_at + after",
    ),
    "the window is ignored": ("timely = committed_at - before <= proof.gen_time <= committed_at + after", "timely = True"),
    "the outcome window is ignored": ("            timely = timely and proof.gen_time + uncertainty < window_opens_at", "            pass"),
    "the uncertainty is not subtracted": ("proof.gen_time + uncertainty < window_opens_at", "proof.gen_time < window_opens_at"),
    "the uncertainty boundary is inclusive": (
        "proof.gen_time + uncertainty < window_opens_at",
        "proof.gen_time + uncertainty <= window_opens_at",
    ),
    "evidence of any kind is accepted": (
        "    if proof.kind not in rules.evidence_kinds_accepted or not proof.ref or proof.subject_sha256 != root_fingerprint:",
        "    if not proof.ref or proof.subject_sha256 != root_fingerprint:",
    ),
    "evidence about any root is accepted": (
        "    if proof.kind not in rules.evidence_kinds_accepted or not proof.ref or proof.subject_sha256 != root_fingerprint:",
        "    if proof.kind not in rules.evidence_kinds_accepted or not proof.ref:",
    ),
    "missing evidence is accepted": (
        "    if operator is None or operator.evidence is None:\n        return None",
        "    if operator is None:\n        return None",
    ),
    "signers are not counted": (
        "min(len({t.signer_fingerprint for t in usable}), len({t.parent for t in usable}))",
        "len({t.parent for t in usable})",
    ),
    "parents are not counted": (
        "min(len({t.signer_fingerprint for t in usable}), len({t.parent for t in usable}))",
        "len({t.signer_fingerprint for t in usable})",
    ),
    "tokens without a parent are counted": ("    usable = [t for t in tokens if t.parent is not None]", "    usable = list(tokens)"),
    "the spread is not limited": ("(last - first).total_seconds() > rules.max_spread_s", "False"),
    "the spread limit is exclusive": (
        "(last - first).total_seconds() > rules.max_spread_s",
        "(last - first).total_seconds() >= rules.max_spread_s",
    ),
    "one independent operator suffices": (
        "            if _independent(subset) >= rules.required_independent:",
        "            if _independent(subset) >= 1:",
    ),
    "tokens without evidence carry the level": ("t.gen_time is not None and t.parent is not None", "t.gen_time is not None"),
    "the attestation is the earliest token, not the latest": (
        "        spread,\n        max(times),\n        LEVEL_C3,",
        "        spread,\n        min(times),\n        LEVEL_C3,",
    ),
    "the eligible offset forgets the spread": ("        rules.u_constant_s + spread,\n", "        rules.u_constant_s,\n"),
    "the eligible offset forgets the uncertainty": ("        rules.u_constant_s + spread,\n", "        spread,\n"),
    "rejected tokens are counted": (
        'assessed.append(TokenAssessment(item.name, False, item.reason or "rejected", None, None, None, None, None, False))',
        'assessed.append(TokenAssessment(item.name, False, item.reason or "rejected", None, None, None, None, None, True))',
    ),
    "the declared window flag is wrong when no level is reached": (
        "LEVEL_C1, None, declared, _assurance(rules), error)",
        "LEVEL_C1, None, True, _assurance(rules), error)",
    ),
    "the declared window flag is wrong when a level is reached": (
        "        rules.u_constant_s + spread,\n        declared,\n        _assurance(rules),",
        "        rules.u_constant_s + spread,\n        True,\n        _assurance(rules),",
    ),
    "windows may start anywhere in the order": (
        "key=lambda t: (t.gen_time, t.name))\n    for start",
        "key=lambda t: (t.name, t.gen_time))\n    for start",
    ),
    # the order of a record's own times
    "a record created after its commit is not refused": (
        "    impossible = created_at is not None and created_at > committed_at + before",
        "    impossible = False",
    ),
    "the creation limit is exclusive": ("created_at > committed_at + before", "created_at >= committed_at + before"),
    "the creation limit ignores the clock offset": ("created_at > committed_at + before", "created_at > committed_at"),
    "a token may precede the record's creation": (
        "            timely = timely and created_at - before <= proof.gen_time\n",
        "            pass\n",
    ),
    "a token may precede the creation by more than the offset": (
        "timely and created_at - before <= proof.gen_time",
        "timely and created_at - 2 * before <= proof.gen_time",
    ),
    "an impossible order still counts tokens": ("        if impossible:\n            timely = False\n", ""),
    "an impossible order is not reported": ("    error = IMPOSSIBLE_ORDER if impossible else None", "    error = None"),
}


def test_scenario_passes_on_the_real_module() -> None:
    assert failures(timing) == []


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    assert failures(mutant("hoshitori.timing", old, new)), f"mutant not detected: {name}"
