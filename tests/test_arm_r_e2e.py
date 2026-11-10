"""Arm R on one real league, with only the outside world faked: RYO's own verdict is read, sealed in the cycle's batch beside the agents'
calls, kept from every agent, and settled a day later by the same window as the agents' 24-hour calls on the same token.

The league is the loopback one of ``e2e_league`` with RYO read from a loopback server; the cycle, commitment, scheduler, settlement, views
and offline verifier are the real ones.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from hoshitori import canonical, commitment_store, lifecycle, ryo_read, scheduler, verify, view_models
from hoshitori.timeline import to_ms
from tests.e2e_league import MINUTE, RYO_PLAN_OFFSET, league, price
from tests.test_e2e_league import records, whole_batch


def everything(lg) -> list[dict]:  # type: ignore[no-untyped-def,type-arg]
    return [canonical.parse_record(lg.ledger.read_record(seq)) for seq in range(lg.ledger.size)]


def test_ryo_s_own_verdict_is_sealed_kept_from_the_agents_and_settled_by_the_same_window(tmp_path: Path) -> None:
    with league(tmp_path, ryo=True) as lg:
        first = scheduler.tick(lg.services, lg.config)
        first_cycle = first.ran.cycle_id if first.ran is not None else ""
        assert first.ran is not None and first.ran.state.value == "COMMITTED", first.ran and first.ran.reason
        commitment = first.ran.commitment
        assert commitment is not None and commitment.level == "C3"

        # One read per candidate RYO's deep analysis answered, in the cycle's batch
        reads = {r["body"]["symbol"]: r for r in records(lg.ledger, "ryo_read")}
        assert sorted(reads) == ["BTC", "ETH"]
        for read in reads.values():
            body = read["body"]
            assert (body["call"], body["reading"], body["cycle_id"]) == ("avoid", "NOT_LONG", first.ran.cycle_id)
            plan = body["plan"]
            assert Decimal(plan["target"]) - Decimal(plan["entry"]) == Decimal(plan["entry"]) - Decimal(plan["stop"]) == RYO_PLAN_OFFSET

        # Kept from the agents: the read's interpretation item is committed, and no snapshot an arm reads lists it
        interpreted = {r["id"] for r in records(lg.ledger, "evidence") if r["body"]["kind"] == "INTERPRETATION"}
        assert {r["body"]["evidence_id"] for r in reads.values()} <= interpreted
        listed = set(canonical.dumps(records(lg.ledger, "snapshot")[0]["body"]).decode().split('"'))
        assert interpreted and not interpreted & listed

        # Sealed: the batch that holds the reads verifies offline and is scorable
        saved = commitment_store.load(lg.store_dir, commitment.commitment_id)
        seqs = sorted(seq for r in reads.values() if (seq := lg.ledger.find(lg.services.source_urn, r["id"])) is not None)
        assert len(seqs) == 2
        out = tmp_path / "bundle"
        from hoshitori import bundle

        bundle.export_bundle(out, lg.ledger, whole_batch(lg, seqs), checkpoint_note=saved.note, size=commitment.size, tokens=saved.tokens)
        verdict = verify.verify_bundle(out, lg.root)
        assert verdict["integrity"]["ok"] and verdict["eligibility"]["scorable"] and verdict["timing"]["level"] == "C3"

        # Before the day has passed, the reads wait for the window
        views = view_models.build(everything(lg), lg.services.protocol, now_ms=to_ms(lg.clock.now()))
        assert views.ryo is not None and {o.state for o in views.ryo.outcomes} == {"Waiting"}

        # A day later the 24-hour calls settle, and each read is scored by the same window
        lg.clock.jump(62 * 60)
        scheduler.tick(lg.services, lg.config)
        lg.clock.jump(23 * 3600)
        scheduler.tick(lg.services, lg.config)
        views = view_models.build(everything(lg), lg.services.protocol, now_ms=to_ms(lg.clock.now()))
        assert views.ryo is not None
        day = {
            (row.cycle_id, row.symbol): row.result
            for row in views.rows
            if row.contract == "D" and row.hours == 24 and row.cycle_id == first.ran.cycle_id and row.state == "Settled"
        }
        assert sorted(day) == [(first.ran.cycle_id, "BTC"), (first.ran.cycle_id, "ETH")], views.rows
        mine = [o for o in views.ryo.outcomes if o.cycle_id == first.ran.cycle_id]
        assert sorted(o.symbol for o in mine) == ["BTC", "ETH"]
        for o in mine:
            assert o.state == "Settled", o
            assert o.direction == {"Rose": "ROSE", "Fell": "FELL", "Tie": "TIE"}[str(day[(o.cycle_id, o.symbol)])], (o, day)
            assert o.hit == ryo_read.hit("NOT_LONG", o.direction)
        # The plans: settled on the same window by the barrier rule; BTC rises to its target, ETH falls to its stop, at minutes worked out
        # here from the exchange's price formula
        plans = {r["body"]["read_id"]: r["body"] for r in records(lg.ledger, "ryo_plan_settlement")}
        assert sorted(plans) == sorted(r["id"] for r in reads.values())
        histories = lifecycle.rebuild(records(lg.ledger, "forecast_state"))
        settled_d = {r["body"]["forecast_id"]: r["body"] for r in records(lg.ledger, "settlement")}
        for symbol, want in (("BTC", "TARGET"), ("ETH", "STOP")):
            got = plans[reads[symbol]["id"]]
            level = Decimal(got["target"] if want == "TARGET" else got["stop"])
            opened = next(t.step.origin_ms for t in histories[got["window_forecast_id"]] if t.step.state is lifecycle.State.OPEN)
            first = next(
                opened + k * MINUTE
                for k in range(24 * 60)
                if (
                    price(symbol, opened + k * MINUTE, lg.exchange.anchor) >= level
                    if want == "TARGET"
                    else price(symbol, opened + k * MINUTE, lg.exchange.anchor) <= level
                )
            )
            assert (got["state"], got["outcome"], got["touch_open_ms"]) == ("SETTLED", want, first), (symbol, got)
            assert Decimal(got["p0"]) == Decimal(settled_d[got["window_forecast_id"]]["p0"]), (
                "the plan starts at the window's own start price"
            )
        views = view_models.build(everything(lg), lg.services.protocol, now_ms=to_ms(lg.clock.now()))
        assert views.ryo is not None
        mine = {o.symbol: o.plan for o in views.ryo.outcomes if o.cycle_id == first_cycle}
        assert mine == {"BTC": "TARGET", "ETH": "STOP"}, mine
        summary = views.ryo.summary
        assert summary.scored == sum(1 for o in views.ryo.outcomes if o.hit is not None)
        assert summary.hits == sum(1 for o in views.ryo.outcomes if o.hit)
