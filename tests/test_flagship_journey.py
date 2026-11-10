"""The flagship journey on one real league, with only the outside world faked: discover, decide, commit, a premise changes after the call,
verify, settle, learn. Each step reads what the one before wrote, and the identity of the asset is checked at every link.

The league is the loopback one of ``e2e_league``: the real registry, cycle, commitment, scheduler, settlement, post-snapshot pass, read model
and offline verifier; two fake exchanges and three fake time-stamp authorities. The Desk (an on-demand investigation) and the evidence
challenge have their own end-to-end tests and are not part of this journey: this one is the league's.
"""

from __future__ import annotations

from pathlib import Path

from hoshitori import bundle, commitment_store, outcome_review, scheduler, today_items, verify, view_models
from hoshitori.timeline import to_ms
from tests.e2e_league import league
from tests.test_e2e_league import records, whole_batch


def test_the_league_journey_from_discovery_to_what_was_learned(tmp_path: Path) -> None:
    with league(tmp_path) as lg:
        # Discover, decide, commit: one cycle, six forecasts, all at C3, two assets
        first = scheduler.tick(lg.services, lg.config)
        assert first.ran is not None and first.ran.state.value == "COMMITTED", first.ran and first.ran.reason
        commitment = first.ran.commitment
        assert commitment is not None and commitment.level == "C3"
        slots = first.ran.slots
        forecasts = {r["id"]: r for r in records(lg.ledger, "forecast")}
        assert set(forecasts) == {s.forecast_id for s in slots} and len(forecasts) == 6
        manifest_assets = {e["symbol"]: e["asset_id"] for r in records(lg.ledger, "manifest") for e in r["body"]["entries"]}
        assert set(manifest_assets) == {"BTC", "ETH"}
        for s in slots:  # identity chain: the asset in the manifest is the asset of every forecast about that symbol
            assert forecasts[s.forecast_id]["body"]["asset_id"] == manifest_assets[s.symbol]
        before = [lg.ledger.read_record(seq) for seq in range(lg.ledger.size)]

        # Verify: the bundle of the cycle is complete and scorable, offline, before anything changes
        saved = commitment_store.load(lg.store_dir, commitment.commitment_id)
        wanted = set(forecasts) | {ref["id"] for r in forecasts.values() for ref in r["refs"]}
        seqs = sorted(seq for ident in wanted if (seq := lg.ledger.find(lg.services.source_urn, ident)) is not None)
        out = tmp_path / "bundle"
        bundle.export_bundle(out, lg.ledger, whole_batch(lg, seqs), checkpoint_note=saved.note, size=commitment.size, tokens=saved.tokens)
        verdict = verify.verify_bundle(out, lg.root)
        assert verdict["integrity"]["ok"] and verdict["eligibility"]["scorable"] and verdict["timing"]["level"] == "C3"

        # A premise changes after the call: Binance spot stops listing BTC; an hour passes
        lg.exchange.delisted.add("BTC")
        lg.clock.jump(62 * 60)
        second = scheduler.tick(lg.services, lg.config)
        assert second.passed is not None and second.passed.deferred == ()

        # Settle: the two one-hour calls of the first cycle matured and were settled as they were committed
        settlements = records(lg.ledger, "settlement")
        one_hour = {s.forecast_id for s in slots if s.hours == 1}
        assert {r["body"]["forecast_id"] for r in settlements} >= one_hour
        assert {r["body"]["state"] for r in settlements if r["body"]["forecast_id"] in one_hour} == {"SETTLED"}

        # Material update: one event, matched by asset and oracle venue, revising only the open BTC calls of the first cycle
        events = records(lg.ledger, "post_snapshot_event")
        assert [(e["body"]["kind"], e["body"]["venue"], e["body"]["asset_id"]) for e in events] == [
            ("REGISTRY_INSTRUMENT_REMOVED", "binance-spot", manifest_assets["BTC"])
        ]
        assert events[0]["body"]["previous_snapshot"] is not None
        revisions = records(lg.ledger, "thesis_revision")
        open_btc = {s.forecast_id for s in slots if s.symbol == "BTC" and s.hours in (4, 24)}
        assert {r["body"]["thesis_id"] for r in revisions} == open_btc
        assert {r["body"]["kind"] for r in revisions} == {"MATERIAL_UPDATE"}
        assert all(forecasts[r["body"]["thesis_id"]]["body"]["asset_id"] == events[0]["body"]["asset_id"] for r in revisions)
        # nothing written before was edited, and the bundle of the first cycle still verifies byte for byte
        assert [lg.ledger.read_record(seq) for seq in range(len(before))] == before
        again = verify.verify_bundle(out, lg.root)
        assert again["integrity"]["ok"] and again["eligibility"]["scorable"]

        # Today shows it, linked to the call, next to nothing else that claims the call changed
        everything = records(lg.ledger, "forecast") + [
            r for kind in ("post_snapshot_event", "thesis_revision") for r in records(lg.ledger, kind)
        ]
        shown = [i for i in today_items.items(everything, []) if i.state == today_items.MATERIAL_UPDATE]
        assert {i.symbol for i in shown} == {"BTC"} and {i.link for i in shown} == open_btc
        assert all(i.reasons == ("A premise of a recorded call changed.",) and i.score == 10 for i in shown)

        # The next tick adds nothing: the same change is the same event
        size = lg.ledger.size
        scheduler.post_snapshot_job.post_snapshot_pass(
            lg.ledger, lg.services.env.registry, now_ms=to_ms(lg.clock.now()), source=lg.services.source_urn
        )
        assert lg.ledger.size == size

        # Learn: the review describes the settled calls and builds no lesson
        parsed = [lg.ledger.read_record(seq) for seq in range(lg.ledger.size)]
        views = view_models.build(list(map(_parse, parsed)), lg.services.protocol, now_ms=to_ms(lg.clock.now()))
        review = outcome_review.build(views)
        assert {r.forecast_id for r in review.reviews} == one_hour
        assert (review.ledger.promoted, review.ledger.tested, review.ledger.proposed) == (0, 0, 0)
        text = " ".join(o.text for r in review.reviews for o in r.observations).lower()
        assert not any(word in text for word in ("caused", "explains", "unlucky", "bad luck"))


def _parse(raw: bytes) -> dict:  # type: ignore[type-arg]
    from hoshitori import canonical

    return canonical.parse_record(raw)  # type: ignore[no-any-return]
