"""Scenarios for time-stamp counting, operator independence and the level they support (contract sections 2, 8 and 26)."""

from __future__ import annotations

import types
from collections.abc import Callable
from datetime import datetime, timedelta

from hoshitori import trusted_root
from hoshitori.tsa import PinnedRoot, VerifiedToken

COMMIT = datetime.fromisoformat("2026-10-01T12:00:00+00:00")
OPENS = COMMIT + timedelta(minutes=5)


def fp(name: str) -> str:
    import hashlib

    return hashlib.sha256(name.encode()).hexdigest()


def operator(
    name: str, parent: str | None = None, *, kind: str = "ccadb_ca_owner_of_record", about: str | None = None, evidence: bool = True
):  # type: ignore[no-untyped-def]
    proof = trusted_root.OwnershipEvidence(kind, f"record {name}", about or fp(f"root-{name}")) if evidence else None
    return trusted_root.Operator(name, parent or f"Organisation {name}", proof)


def make_rules(operators: list, *, required: int = 2, spread: int = 5, before: int = 1, after: int = 60, uncertainty: int = 5):  # type: ignore[no-untyped-def]
    return trusted_root.TimeStampRules(
        pinned=tuple(PinnedRoot(fp(f"root-{op.name}"), op.name) for op in operators),
        operators={op.name: op for op in operators},
        evidence_kinds_accepted=frozenset({"ccadb_ca_owner_of_record", "operator_published_root"}),
        required_independent=required,
        window_before_commit_s=before,
        window_after_commit_s=after,
        close_after_commit_s=90,
        max_spread_s=spread,
        u_constant_s=uncertainty,
    )


def token(mod: types.ModuleType, name: str, operator_name: str, offset_s: float, *, root: str | None = None, signer: str | None = None):  # type: ignore[no-untyped-def]
    verified = VerifiedToken(
        gen_time=COMMIT + timedelta(seconds=offset_s),
        policy_oid="1.2.3",
        serial=1,
        nonce=None,
        accuracy=None,
        root_fingerprint=root or fp(f"root-{operator_name}"),
        signer_fingerprint=signer or fp(f"signer-{name}"),
        operator=operator_name,
        path_fingerprints=(),
    )
    return mod.TokenEvidence(name, verified)


def level_of(mod: types.ModuleType, tokens: list, rules, *, opens: datetime | None = OPENS):  # type: ignore[no-untyped-def]
    return mod.assess(COMMIT, opens, tokens, rules)


A, B, C = operator("a"), operator("b"), operator("c")
RULES = make_rules([A, B, C])


def counts_and_levels(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []

    def expect(label: str, result, level: str, independent: int | None = None, chosen: tuple[str, ...] | None = None) -> None:  # type: ignore[no-untyped-def]
        if result.level != level:
            bad.append(f"{label}: level {result.level}, expected {level}")
        if independent is not None and result.independent_operators != independent:
            bad.append(f"{label}: {result.independent_operators} independent operators, expected {independent}")
        if chosen is not None and tuple(sorted(result.chosen)) != tuple(sorted(chosen)):
            bad.append(f"{label}: chosen {result.chosen}, expected {chosen}")

    two = level_of(mod, [token(mod, "ta", "a", 1), token(mod, "tb", "b", 3)], RULES)
    expect("two operators agree", two, "C3", 2, ("ta", "tb"))
    if two.attested_at != COMMIT + timedelta(seconds=3) or two.spread_s != 2 or two.eligible_after_offset_s != 7:
        bad.append(f"attestation figures wrong: {two.attested_at}, {two.spread_s}, {two.eligible_after_offset_s}")
    if not two.assurance_class.startswith("TA-3") or not two.window_declared:
        bad.append("assurance class or window flag wrong")
    expect("one token", level_of(mod, [token(mod, "ta", "a", 1)], RULES), "C1", 1, ())
    if (
        level_of(mod, [token(mod, "ta", "a", 1)], RULES, opens=None).window_declared
        or not level_of(mod, [token(mod, "ta", "a", 1)], RULES).window_declared
    ):
        bad.append("the declared-window flag is wrong when no level is reached")
    expect("no tokens", level_of(mod, [], RULES), "C1", 0, ())
    expect("the same operator twice", level_of(mod, [token(mod, "t1", "a", 1), token(mod, "t2", "a", 2)], RULES), "C1", 1, ())
    expect(
        "the same signer under two roots",
        level_of(mod, [token(mod, "t1", "a", 1, signer="s"), token(mod, "t2", "b", 2, signer="s")], RULES),
        "C1",
        1,
        (),
    )
    shared = make_rules([operator("a", "One Organisation"), operator("b", "One Organisation")])
    expect(
        "two roots under one parent organisation", level_of(mod, [token(mod, "ta", "a", 1), token(mod, "tb", "b", 2)], shared), "C1", 1, ()
    )

    # ownership evidence: absent, of an unlisted kind, or about another root counts for nothing
    for label, odd in (
        ("no evidence", operator("b", evidence=False)),
        ("an unlisted evidence kind", operator("b", kind="a_blog_post")),
        ("evidence about another root", operator("b", about=fp("some other root"))),
    ):
        rules = make_rules([A, odd])
        expect(f"operator with {label}", level_of(mod, [token(mod, "ta", "a", 1), token(mod, "tb", "b", 2)], rules), "C1", 1, ())

    # the counting window is inclusive at both ends (committed time minus 1 s, plus 60 s)
    expect("a token 1 s before the commit counts", level_of(mod, [token(mod, "ta", "a", -1), token(mod, "tb", "b", 0)], RULES), "C3")
    expect(
        "a token 1.001 s before the commit does not",
        level_of(mod, [token(mod, "ta", "a", -1.001), token(mod, "tb", "b", 0)], RULES),
        "C1",
        1,
    )
    expect("a token 60 s after the commit counts", level_of(mod, [token(mod, "ta", "a", 56), token(mod, "tb", "b", 60)], RULES), "C3")
    expect(
        "a token 60.001 s after the commit does not",
        level_of(mod, [token(mod, "ta", "a", 56), token(mod, "tb", "b", 60.001)], RULES),
        "C1",
        1,
    )
    expect("a token at the commit counts", level_of(mod, [token(mod, "ta", "a", 0), token(mod, "tb", "b", 0)], RULES), "C3")

    # the outcome window: the token must precede it by the uncertainty
    opens_at = COMMIT + timedelta(seconds=30)
    expect(
        "tokens before the window by more than u",
        level_of(mod, [token(mod, "ta", "a", 22), token(mod, "tb", "b", 24)], RULES, opens=opens_at),
        "C3",
    )
    expect(
        "a token exactly u before the window does not count",
        level_of(mod, [token(mod, "ta", "a", 22), token(mod, "tb", "b", 25)], RULES, opens=opens_at),
        "C1",
    )
    expect(
        "a token after the window does not count",
        level_of(mod, [token(mod, "ta", "a", 22), token(mod, "tb", "b", 40)], RULES, opens=opens_at),
        "C1",
    )
    undeclared = level_of(mod, [token(mod, "ta", "a", 10), token(mod, "tb", "b", 40)], make_rules([A, B], spread=60), opens=None)
    expect("no declared window", undeclared, "C3")
    if undeclared.window_declared:
        bad.append("an undeclared window was reported as declared")

    # agreement in time
    expect("tokens 5 s apart agree", level_of(mod, [token(mod, "ta", "a", 1), token(mod, "tb", "b", 6)], RULES), "C3")
    expect("tokens 5.001 s apart do not", level_of(mod, [token(mod, "ta", "a", 1), token(mod, "tb", "b", 6.001)], RULES), "C1")
    expect(
        "an outlier is left out when two others agree",
        level_of(mod, [token(mod, "ta", "a", 0), token(mod, "tb", "b", 20), token(mod, "tc", "c", 22)], RULES),
        "C3",
        3,
        ("tb", "tc"),
    )
    expect(
        "the earliest agreeing pair carries the level",
        level_of(mod, [token(mod, "ta", "a", 0), token(mod, "tb", "b", 2), token(mod, "tc", "c", 3)], RULES),
        "C3",
        3,
        ("ta", "tb"),
    )

    # tokens arrive in any order and are judged by their times, not their names or their position
    scrambled = level_of(mod, [token(mod, "t1", "a", 0), token(mod, "t2", "b", 10), token(mod, "t3", "c", 3)], RULES)
    expect("tokens listed out of time order", scrambled, "C3", 3, ("t1", "t3"))
    if scrambled.spread_s != 3:
        bad.append(f"spread for out-of-order tokens: {scrambled.spread_s}")

    # a token without usable evidence cannot carry the level, even when it sits between two that can
    odd_rules = make_rules([A, operator("x", evidence=False), B])
    carried = level_of(mod, [token(mod, "ta", "a", 0), token(mod, "tx", "x", 1), token(mod, "tb", "b", 2)], odd_rules)
    expect("a token without evidence in between", carried, "C3", 2, ("ta", "tb"))
    if carried.spread_s != 2:
        bad.append(f"spread should be measured over the carrying tokens: {carried.spread_s}")

    # three operators required
    three = make_rules([A, B, C], required=3)
    expect("three required, two present", level_of(mod, [token(mod, "ta", "a", 1), token(mod, "tb", "b", 2)], three), "C1", 2)
    expect(
        "three required, three present",
        level_of(mod, [token(mod, "ta", "a", 1), token(mod, "tb", "b", 2), token(mod, "tc", "c", 3)], three),
        "C3",
        3,
    )

    # rejected tokens are listed with their reason and never count
    rejected = mod.TokenEvidence("bad", None, "signature does not verify")
    mixed = level_of(mod, [token(mod, "ta", "a", 1), rejected], RULES)
    expect("a rejected token", mixed, "C1", 1, ())
    listed = {t.name: t for t in mixed.tokens}
    if listed["bad"].valid or listed["bad"].reason != "signature does not verify" or listed["bad"].timely:
        bad.append("a rejected token is not reported as such")
    if not listed["ta"].valid or listed["ta"].parent != "Organisation a" or listed["ta"].operator != "a":
        bad.append("a counted token is not reported fully")
    return bad


SCENARIOS: dict[str, Callable[[types.ModuleType], list[str]]] = {"counts_and_levels": counts_and_levels}


def time_order(mod: types.ModuleType) -> list[str]:
    """A record's own times must be in a possible order.

    Contract section 2 states ``as_of <= created_at <= committed_at`` and VERIFICATION_SPEC section 7 that a token which precedes the
    record's own ``created_at`` is an impossible ordering under which timing fails. Both are judged within the local clock-offset limit
    that already opens the counting window (``window_before_commit_s``, 1 s; VERIFICATION_SPEC section 3, item 6). Not in ``SCENARIOS``:
    the Node corpus records assessments without a creation time, so Node meets these rules through the recorded bundle cases.
    """
    bad: list[str] = []

    def pair(first: float = 1, second: float = 3) -> list:  # type: ignore[type-arg]
        return [token(mod, "ta", "a", first), token(mod, "tb", "b", second)]

    def run(created_s: float | None, tokens: list | None = None, rules=RULES):  # type: ignore[no-untyped-def,type-arg]
        created = None if created_s is None else COMMIT + timedelta(seconds=created_s)
        return mod.assess(COMMIT, OPENS, pair() if tokens is None else tokens, rules, created_at=created)

    def expect(label: str, result, level: str, error: str | None, timely: dict[str, bool] | None = None) -> None:  # type: ignore[no-untyped-def]
        got_timely = {t.name: t.timely for t in result.tokens}
        if result.level != level or result.error != error or (timely is not None and got_timely != timely):
            bad.append(f"{label}: level {result.level}, error {result.error}, timely {got_timely}; expected {level}, {error}, {timely}")

    if getattr(mod, "IMPOSSIBLE_ORDER", None) != "IMPOSSIBLE_ORDER":
        bad.append("the code of an impossible order is not IMPOSSIBLE_ORDER")
    impossible = getattr(mod, "IMPOSSIBLE_ORDER", "IMPOSSIBLE_ORDER")
    expect("no creation time given", run(None), "C3", None)
    expect("created before the commit", run(-30), "C3", None)
    expect("created at the commit", run(0), "C3", None)
    expect("created 1 s after the commit, the clock-offset limit", run(1), "C3", None)
    expect("created 1.001 s after the commit", run(1.001), "C1", impossible, {"ta": False, "tb": False})
    expect("created a day after the commit", run(86_400), "C1", impossible, {"ta": False, "tb": False})
    late = run(86_400)
    if late.independent_operators != 0 or late.chosen or late.attested_at is not None or not all(t.valid for t in late.tokens):
        bad.append("an impossible order still counts a token, or calls valid tokens invalid")
    expect("a token 1 s before the creation counts", run(1, pair(0, 3)), "C3", None, {"ta": True, "tb": True})
    expect("a token 1.001 s before the creation does not", run(1, pair(-0.001, 3)), "C1", None, {"ta": False, "tb": True})
    if run(1, pair(-0.001, 3)).independent_operators != 1:
        bad.append("a token before the creation is counted as an operator")
    expect("a token before an early creation still counts in the window", run(-30, pair(-1, 3)), "C3", None)
    # the limit is the trusted root's, the same number that opens the counting window
    strict = make_rules([A, B, C], before=0)
    expect("no offset allowed: created at the commit", run(0, rules=strict), "C3", None)
    expect("no offset allowed: created 1 ms after the commit", run(0.001, rules=strict), "C1", impossible)
    loose = make_rules([A, B, C], before=3)
    expect("an offset of 3 s allowed: created 2.5 s after the commit", run(2.5, rules=loose), "C3", None)
    expect("an offset of 3 s allowed: created 3.001 s after the commit", run(3.001, rules=loose), "C1", impossible)
    return bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for name, scenario in {**SCENARIOS, "time_order": time_order}.items():
        try:
            bad += [f"{name}: {message}" for message in scenario(mod)]
        except Exception as exc:
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
    return bad
