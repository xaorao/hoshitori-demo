"""Adjudication of contributions (contract section 32.1a): evidence decides, not votes; duplicates and coordinated identities count once; a
reviewer is never the author or the author's group; credit comes only after the appeal window; an appeal is decided by someone else."""

from __future__ import annotations

import pytest

from hoshitori import adjudication as adj

DAY = 86_400.0


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def court(reviewers: tuple[str, ...] = ("rev1", "rev2")) -> tuple[adj.Adjudicator, Clock]:
    clock = Clock()
    return adj.Adjudicator(clock, reviewers), clock


def submit(a: adj.Adjudicator, case: str, who: str = "alice", **over: object) -> adj.Case:
    fields: dict[str, object] = {
        "network": f"net-{who}",
        "kind": "CORRECT_FACT",
        "claim": "claim-1",
        "url": "https://example.org/a",
        "source_class": "SOCIAL_POST",
        "quote_found": True,
        "origin": "EXCHANGE:x",
        "checkable": False,
        "machine_result": None,
    }
    fields.update(over)
    return a.submit(case, who, **fields)  # type: ignore[arg-type]


def refuses(call: object, says: str) -> None:
    with pytest.raises(adj.AdjudicationError) as raised:
        call()  # type: ignore[operator]
    assert str(raised.value) == says


def test_a_decisive_social_post_is_never_machine_accepted_and_waits_for_a_reviewer() -> None:
    a, _ = court()
    c = submit(a, "c1", source_class="SOCIAL_POST", checkable=True, machine_result="CONFIRMED")
    assert c.state == "PENDING" and c.outcome is None and c.reason == "NEEDS_REVIEW"
    assert [h["state"] for h in c.history] == ["SUBMITTED", "TRIAGED", "NEEDS_REVIEW", "PENDING"]


def test_a_primary_source_that_a_machine_can_check_is_decided_without_a_reviewer() -> None:
    a, _ = court()
    ok = submit(a, "c1", source_class="PRIMARY_DOCUMENT", checkable=True, machine_result="CONFIRMED")
    assert (ok.state, ok.outcome, ok.decided_by) == ("DECIDED", "ACCEPTED_CORRECTION", "machine")
    assert [h["state"] for h in ok.history][:4] == ["SUBMITTED", "TRIAGED", "MACHINE_CHECKED", "DECIDED"]
    other = submit(
        a, "c2", who="bob", claim="claim-2", kind="ADD_SOURCE", source_class="ONCHAIN_RECORD", checkable=True, machine_result="CONFIRMED"
    )
    assert other.outcome == "CORROBORATED"
    refuted = submit(a, "c3", who="carol", claim="claim-3", source_class="EXCHANGE_DATA", checkable=True, machine_result="REFUTED")
    assert (refuted.outcome, refuted.reason) == ("REJECTED", "CLAIM_REFUTED")
    for cls in ("OFFICIAL_ANNOUNCEMENT",):
        assert submit(a, "c4", who="dan", claim="claim-4", source_class=cls, checkable=True, machine_result="CONFIRMED").state == "DECIDED"
    not_checkable = submit(
        a, "c5", who="erin", claim="claim-5", source_class="PRIMARY_DOCUMENT", checkable=False, machine_result="CONFIRMED"
    )
    assert not_checkable.state == "PENDING"  # a machine decides only what it can check deterministically


def test_a_link_that_cannot_be_fetched_or_a_quote_that_is_not_in_it_leaves_the_item_unresolved() -> None:
    a, _ = court()
    missing = submit(a, "c1", quote_found=False, source_class="PRIMARY_DOCUMENT", checkable=True, machine_result="CONFIRMED")
    assert (missing.state, missing.outcome, missing.reason, missing.origin_class) == (
        "DECIDED",
        "UNRESOLVED",
        "QUOTE_NOT_FOUND",
        "SUBMITTER_ASSERTED",
    )
    unfetchable = submit(a, "c2", who="bob", claim="claim-2", quote_found=None)
    assert (unfetchable.outcome, unfetchable.reason) == ("UNRESOLVED", "UNFETCHABLE")
    fetched = submit(a, "c3", who="carol", claim="claim-3", quote_found=True)
    assert fetched.origin_class == "ORIGIN_FETCHED_BY_US"


def test_five_identities_submitting_one_correction_are_one_contribution_and_four_duplicates() -> None:
    a, clock = court()
    names = ["a1", "a2", "a3", "a4", "a5"]
    first = submit(a, "k1", names[0], source_class="PRIMARY_DOCUMENT", checkable=True, machine_result="CONFIRMED")
    rest = [
        submit(a, f"k{i}", n, source_class="PRIMARY_DOCUMENT", checkable=True, machine_result="CONFIRMED")
        for i, n in enumerate(names[1:], start=2)
    ]
    assert first.outcome == "ACCEPTED_CORRECTION" and [c.outcome for c in rest] == ["DUPLICATE"] * 4
    assert all(c.duplicate_of == "k1" for c in rest)
    clock.advance(8 * DAY)
    assert sum(a.credit(n) for n in names) == 1  # one contribution, one credit unit


def test_a_later_report_of_the_same_correction_from_an_independent_origin_is_corroboration_of_the_first() -> None:
    a, _ = court()
    submit(a, "k1", "a1", source_class="PRIMARY_DOCUMENT", checkable=True, machine_result="CONFIRMED", origin="PRIMARY_DOC:x")
    second = submit(a, "k2", "a2", source_class="PRIMARY_DOCUMENT", checkable=True, machine_result="CONFIRMED", origin="CHAIN:y")
    assert second.outcome == "DUPLICATE" and second.corroborates == "k1" and second.duplicate_of == "k1"
    same = submit(a, "k3", "a3", source_class="PRIMARY_DOCUMENT", checkable=True, machine_result="CONFIRMED", origin="PRIMARY_DOC:x")
    assert same.corroborates is None and same.duplicate_of == "k1"


def test_mutual_endorsement_changes_no_status_and_there_is_no_vote_to_cast() -> None:
    a, _ = court()
    c = submit(a, "c1", "a1")
    before = (c.state, c.outcome)
    a.record_support("a2", "a1")
    a.record_support("a1", "a2")
    a.record_support("a3", "a1")
    assert (a.case("c1").state, a.case("c1").outcome) == before
    assert not hasattr(a, "endorse") and not hasattr(a, "vote")
    assert "support" not in adj.Case.__dataclass_fields__ and "votes" not in adj.Case.__dataclass_fields__


def test_a_reviewer_decides_a_pending_item_but_never_their_own_or_their_group_s() -> None:
    a, _ = court(("alice", "rev2"))
    c = submit(a, "c1", "alice", url="https://example.org/own")
    refuses(lambda: a.review("c1", "alice", "ACCEPTED_CORRECTION", "ok"), "a reviewer never reviews their own item or their group's")
    refuses(lambda: a.review("c1", "nobody", "ACCEPTED_CORRECTION", "ok"), "nobody is not a reviewer")
    refuses(lambda: a.review("c1", "rev2", "MAYBE", "ok"), f"an outcome is one of {list(adj.OUTCOMES)}")
    refuses(lambda: a.review("c1", "rev2", "REJECTED", ""), "a decision states its reason")
    done = a.review("c1", "rev2", "REJECTED", "the source does not say so")
    assert (done.state, done.outcome, done.decided_by, done.reason) == ("DECIDED", "REJECTED", "rev2", "the source does not say so")
    assert c.case_id == done.case_id
    refuses(lambda: a.review("c1", "rev2", "REJECTED", "again"), "only a pending item is reviewed, not DECIDED")
    b, _ = court(("alice", "bob"))
    submit(b, "g1", "carol", network="shared")
    submit(b, "g2", "bob", network="shared", claim="claim-2")  # bob shares a network with carol, so they are one contributor
    refuses(lambda: b.review("g1", "bob", "REJECTED", "x"), "a reviewer never reviews their own item or their group's")


def test_without_a_free_reviewer_the_item_stays_pending_and_says_so() -> None:
    a, _ = court(("alice",))
    c = submit(a, "c1", "alice")
    assert c.state == "PENDING" and a.reviewers_for("c1") == ()
    assert a.pending_reason("c1") == "no reviewer other than the contributor and their group is available"
    d, _ = court(("rev1", "rev2"))
    submit(d, "c2", "bob")
    assert d.reviewers_for("c2") == ("rev1", "rev2") and d.pending_reason("c2") == "waiting for a reviewer"


def test_credit_is_pending_until_the_appeal_window_has_closed() -> None:
    a, clock = court()
    submit(a, "c1", "alice", source_class="PRIMARY_DOCUMENT", checkable=True, machine_result="CONFIRMED")
    assert a.credit("alice") == 0 and a.pending_credit("alice") == 1
    clock.advance(7 * DAY - 1)
    assert a.credit("alice") == 0
    clock.advance(1)
    assert a.credit("alice") == 1 and a.pending_credit("alice") == 0
    submit(a, "c2", "alice", claim="claim-2", source_class="PRIMARY_DOCUMENT", checkable=True, machine_result="REFUTED")
    clock.advance(8 * DAY)
    assert a.credit("alice") == 1  # a rejected contribution earns nothing


def test_an_appeal_is_one_per_decision_inside_the_window_and_decided_by_a_different_reviewer() -> None:
    a, clock = court(("rev1", "rev2", "rev3"))
    submit(a, "c1", "alice")
    a.review("c1", "rev1", "REJECTED", "no support in the source")
    refuses(lambda: a.appeal("c1", "mallory", "mine"), "only the contributor appeals")
    clock.advance(7 * DAY + 1)
    refuses(lambda: a.appeal("c1", "alice", "late"), "the appeal window has closed")
    b, clock2 = court(("rev1", "rev2", "rev3"))
    submit(b, "c1", "alice")
    b.review("c1", "rev1", "REJECTED", "no support in the source")
    refuses(lambda: b.appeal("c1", "alice", ""), "an appeal states its reason")
    opened = b.appeal("c1", "alice", "the quote is on page 3")
    assert opened.state == "APPEAL_OPEN"
    assert b.credit("alice") == 0
    refuses(lambda: b.appeal("c1", "alice", "again"), "one appeal per decision")
    refuses(lambda: b.review_appeal("c1", "rev1", "ACCEPTED_CORRECTION", "same reviewer"), "an appeal is decided by a different reviewer")
    decided = b.review_appeal("c1", "rev2", "ACCEPTED_CORRECTION", "the quote is there")
    assert (decided.state, decided.outcome, decided.decided_by) == ("APPEAL_DECIDED", "ACCEPTED_CORRECTION", "rev2")
    assert [d["outcome"] for d in decided.decisions] == ["REJECTED", "ACCEPTED_CORRECTION"]  # the original decision stays visible
    assert decided.decisions[0]["by"] == "rev1" and decided.decisions[0]["reason"] == "no support in the source"
    clock2.advance(7 * DAY + 1)
    assert b.credit("alice") == 1
    refuses(lambda: b.review_appeal("c1", "rev3", "REJECTED", "x"), "only an open appeal is reviewed, not APPEAL_DECIDED")


def test_an_appeal_with_no_other_reviewer_waits_and_is_not_decided_by_the_first() -> None:
    a, _ = court(("rev1",))
    submit(a, "c1", "alice")
    a.review("c1", "rev1", "REJECTED", "no")
    a.appeal("c1", "alice", "please")
    assert a.appeal_reviewers("c1") == () and a.case("c1").state == "APPEAL_OPEN"


def test_coordinated_identities_are_one_contributor_for_credit_and_rate_limits_and_the_flag_is_private() -> None:
    a, clock = court()
    for k, who in enumerate(("w1", "w2", "w3")):
        submit(
            a,
            f"c{k}",
            who,
            network="same-net",
            claim=f"claim-{k}",
            source_class="PRIMARY_DOCUMENT",
            checkable=True,
            machine_result="CONFIRMED",
        )
    assert {a.group_of(w) for w in ("w1", "w2", "w3")} == {a.group_of("w1")}
    assert a.group_of("solo") == "solo"
    clock.advance(8 * DAY)
    assert sum(a.credit(w) for w in ("w1", "w2", "w3")) == 3 and a.group_credit(a.group_of("w1")) == 3
    view = a.public_view()
    assert "groups" not in view and all("group" not in row for row in view["cases"])
    assert a.moderation_view()["groups"] == [sorted(("w1", "w2", "w3"))]
    for k in range(adj.DAILY_CAP):
        submit(a, f"d{k}", "w1", network="same-net", claim=f"other-{k}")
    refuses(
        lambda: submit(a, "dx", "w2", network="same-net", claim="more"),
        "the contributor (with the identities grouped to them) has made 5 submissions today",
    )


def test_the_grouping_rules_are_a_shared_network_the_same_claim_in_a_window_reciprocal_support_and_bursts() -> None:
    a, _ = court()
    submit(a, "s1", "x1", network="n1", claim="same-claim")
    submit(a, "s2", "x2", network="n2", claim="same-claim")
    assert a.group_of("x1") == a.group_of("x2")  # one claim from two identities inside the window
    a.record_support("y1", "y2")
    assert a.group_of("y1") != a.group_of("y2")  # one-way support is not enough
    a.record_support("y2", "y1")
    assert a.group_of("y1") == a.group_of("y2")  # reciprocal support
    far, clock = court()
    submit(far, "t1", "z1", network="n1", claim="late-claim")
    clock.advance(adj.CLAIM_WINDOW_S + 1)
    submit(far, "t2", "z2", network="n2", claim="late-claim")
    assert far.group_of("z1") != far.group_of("z2")  # outside the window the same claim is a coincidence
    burst, bclock = court()
    for k in range(adj.BURST_COUNT):
        submit(burst, f"b{k}", f"u{k}", network=f"n{k}", claim=f"c{k}")
        bclock.advance(1.0)
    assert len({burst.group_of(f"u{k}") for k in range(adj.BURST_COUNT)}) == 1


def test_the_closed_lists_and_the_constants_are_pinned() -> None:
    assert adj.SOURCE_CLASSES == (
        "PRIMARY_DOCUMENT",
        "OFFICIAL_ANNOUNCEMENT",
        "ONCHAIN_RECORD",
        "EXCHANGE_DATA",
        "REPUTABLE_REPORTING",
        "SOCIAL_POST",
        "ANONYMOUS_OR_UNKNOWN",
    )
    assert adj.SOURCE_CLASSES[:4] == adj.MACHINE_CLASSES
    assert adj.KINDS == ("CORRECT_FACT", "ADD_SOURCE", "DISPUTE_CLAIM", "ORIGIN_PROBLEM")
    assert adj.OUTCOMES == ("ACCEPTED_CORRECTION", "CORROBORATED", "REJECTED", "DUPLICATE", "UNRESOLVED")
    assert (adj.APPEAL_WINDOW_S, adj.DAILY_CAP, adj.CLAIM_WINDOW_S, adj.BURST_COUNT) == (7 * DAY, 5, 3600.0, 5)


def test_a_submission_is_refused_when_its_fields_are_not_in_their_lists() -> None:
    a, _ = court()
    refuses(lambda: submit(a, "c1", kind="LIKE"), f"a kind is one of {list(adj.KINDS)}")
    refuses(lambda: submit(a, "c1", source_class="BLOG"), f"a source class is one of {list(adj.SOURCE_CLASSES)}")
    refuses(lambda: submit(a, "c1", machine_result="MAYBE"), "a machine result is CONFIRMED, REFUTED or none")
    submit(a, "c1")
    refuses(lambda: submit(a, "c1", "bob"), "a case id is used once")
    refuses(lambda: a.case("zz"), "no case zz")


def test_the_public_view_lists_decisions_with_reasons_and_never_the_contributor_s_network() -> None:
    a, _ = court()
    submit(a, "c1", "alice", source_class="PRIMARY_DOCUMENT", checkable=True, machine_result="CONFIRMED")
    view = a.public_view()
    assert view["cases"] == [
        {
            "case": "c1",
            "kind": "CORRECT_FACT",
            "state": "DECIDED",
            "outcome": "ACCEPTED_CORRECTION",
            "reason": None,
            "decided_by": "machine",
            "source_class": "PRIMARY_DOCUMENT",
            "origin_class": "ORIGIN_FETCHED_BY_US",
        }
    ]
    assert "net-alice" not in str(view) and "alice" not in str(view)


def test_history_and_decisions_carry_the_time_and_the_decider_of_every_step() -> None:
    a, clock = court()
    c = submit(a, "c1", source_class="PRIMARY_DOCUMENT", checkable=True, machine_result="CONFIRMED")
    assert c.history[0] == {"state": "SUBMITTED", "at": clock.t} and c.decisions == [
        {"outcome": "ACCEPTED_CORRECTION", "by": "machine", "reason": None, "at": clock.t}
    ]
    assert c.decided_at == clock.t
    clock.advance(10)
    gone = submit(a, "c2", "bob", claim="claim-2", quote_found=False)
    refuted = submit(a, "c3", "carol", claim="claim-3", source_class="EXCHANGE_DATA", checkable=True, machine_result="REFUTED")
    again = submit(a, "c4", "dan", claim="claim-1")
    assert [x.decided_by for x in (gone, refuted, again)] == ["machine", "machine", "machine"]
    assert again.decisions == [{"outcome": "DUPLICATE", "by": "machine", "reason": "DUPLICATE_OF:c1", "at": clock.t}]


def test_a_corroborated_contribution_earns_credit_and_a_solo_identity_has_group_credit() -> None:
    a, clock = court()
    submit(a, "c1", "alice", kind="ADD_SOURCE", source_class="ONCHAIN_RECORD", checkable=True, machine_result="CONFIRMED")
    clock.advance(DAY * 8)
    assert a.credit("alice") == 1 and a.group_credit(a.group_of("alice")) == 1 and a.group_credit("nobody") == 0


def test_a_refused_submission_leaves_no_trace_in_the_grouping() -> None:
    a, _ = court()
    for k in range(adj.DAILY_CAP):
        submit(a, f"d{k}", "w1", network="same-net", claim=f"claim-{k}")
    refuses(
        lambda: submit(a, "dx", "w9", network="same-net", claim="another"),
        "the contributor (with the identities grouped to them) has made 5 submissions today",
    )
    assert a.group_of("w9") == "w9" and a.moderation_view()["groups"] == []
    with pytest.raises(adj.AdjudicationError):
        a.case("dx")


def test_the_daily_cap_counts_the_last_day_only_and_a_day_exactly_ago_is_outside_it() -> None:
    a, clock = court()
    for k in range(adj.DAILY_CAP):
        submit(a, f"d{k}", "w1", claim=f"claim-{k}")
    refuses(
        lambda: submit(a, "dx", "w1", claim="more"), "the contributor (with the identities grouped to them) has made 5 submissions today"
    )
    clock.advance(DAY - 1)
    refuses(
        lambda: submit(a, "dy", "w1", claim="more"), "the contributor (with the identities grouped to them) has made 5 submissions today"
    )
    clock.advance(1)
    assert submit(a, "dz", "w1", claim="more").state == "PENDING"


def test_the_grouping_windows_include_their_exact_edges() -> None:
    a, clock = court()
    submit(a, "e1", "x1", network="n1", claim="edge")
    clock.advance(adj.CLAIM_WINDOW_S)
    submit(a, "e2", "x2", network="n2", claim="edge")
    assert a.group_of("x1") == a.group_of("x2")
    b, bclock = court()
    for k in range(adj.BURST_COUNT):
        submit(b, f"b{k}", f"u{k}", network=f"n{k}", claim=f"c{k}")
        if k < adj.BURST_COUNT - 1:
            bclock.advance(adj.BURST_WINDOW_S / (adj.BURST_COUNT - 1))
    assert len({b.group_of(f"u{k}") for k in range(adj.BURST_COUNT)}) == 1  # the last arrives exactly one window after the first
    c, cclock = court()
    for k in range(adj.BURST_COUNT - 1):
        submit(c, f"b{k}", f"u{k}", network=f"n{k}", claim=f"c{k}")
    cclock.advance(adj.BURST_WINDOW_S + 1)
    submit(c, "last", "u9", network="n9", claim="c9")
    assert c.group_of("u9") == "u9"  # four plus one outside the window is not a burst


def test_the_moderation_view_lists_groups_of_two_or_more_in_sorted_order() -> None:
    a, clock = court()
    for k, (who, network) in enumerate((("zz", "shared"), ("aa", "shared"), ("solo", "alone"), ("pp", "pair"), ("qq", "pair"))):
        submit(a, f"m{k}", who, network=network, claim=f"c{k}")
        clock.advance(100)  # far enough apart not to be a burst
    assert a.moderation_view() == {"groups": [["aa", "zz"], ["pp", "qq"]]}
    assert a.group_of("zz") == "aa"  # a group is named by its first name in order


def test_a_contribution_under_appeal_is_pending_credit_and_only_for_its_contributor() -> None:
    a, clock = court(("rev1", "rev2"))
    submit(a, "c1", "alice", source_class="PRIMARY_DOCUMENT", checkable=True, machine_result="CONFIRMED")
    submit(a, "c2", "bob", claim="claim-2", source_class="PRIMARY_DOCUMENT", checkable=True, machine_result="CONFIRMED")
    a.appeal("c1", "alice", "the date is wrong")
    assert (a.pending_credit("alice"), a.credit("alice")) == (1, 0)
    assert a.pending_credit("bob") == 1  # bob's own, still inside its window
    a.review_appeal("c1", "rev1", "REJECTED", "the date is wrong, the rest is not")
    assert a.pending_credit("alice") == 0
    c = submit(a, "c3", "carol", claim="claim-3")
    a.review("c3", "rev1", "REJECTED", "no")
    a.appeal("c3", "carol", "please")
    assert a.pending_credit("carol") == 0  # a rejected item under appeal is not credit
    clock.advance(DAY * 8)
    assert c.case_id == "c3" and a.pending_credit("bob") == 0 and a.credit("bob") == 1


def test_an_appeal_is_refused_on_an_item_that_is_not_decided_and_at_the_exact_end_of_the_window() -> None:
    a, clock = court()
    submit(a, "c1", "alice")
    refuses(lambda: a.appeal("c1", "alice", "too early"), "only a decided item is appealed, not PENDING")
    a.review("c1", "rev1", "REJECTED", "no support")
    clock.advance(adj.APPEAL_WINDOW_S)
    refuses(lambda: a.appeal("c1", "alice", "exactly at the end"), "the appeal window has closed")


def test_an_appeal_decision_is_checked_like_any_decision() -> None:
    a, _ = court(("rev1", "rev2", "alice"))
    submit(a, "c1", "alice")
    a.review("c1", "rev1", "REJECTED", "no")
    a.appeal("c1", "alice", "please")
    refuses(lambda: a.review_appeal("c1", "outsider", "ACCEPTED_CORRECTION", "ok"), "outsider is not a reviewer")
    refuses(
        lambda: a.review_appeal("c1", "alice", "ACCEPTED_CORRECTION", "own"), "a reviewer never reviews their own item or their group's"
    )
    refuses(lambda: a.review_appeal("c1", "rev2", "MAYBE", "ok"), f"an outcome is one of {list(adj.OUTCOMES)}")
    refuses(lambda: a.review_appeal("c1", "rev2", "ACCEPTED_CORRECTION", ""), "a decision states its reason")
    assert a.case("c1").state == "APPEAL_OPEN"
