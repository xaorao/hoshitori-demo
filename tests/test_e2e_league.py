"""The league end to end, on loopback, with nothing inside the product replaced (see ``e2e_league``).

One tick runs a cycle against two fake exchanges and three fake time-stamp authorities and commits it into a real ledger; the committed
forecasts and the evidence they cite are exported as a bundle and verified offline, by the Python verifier and by the independent Node one;
an hour is then let pass and the next tick settles what has matured, from the candles of both exchanges.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from hoshitori import bundle, canonical, commitment_store, lifecycle, scheduler, settlement_record, verify
from hoshitori.evidence import record_digest
from hoshitori.timeline import origin
from tests.e2e_league import HOUR, price

NODE = shutil.which("node")
BIN = Path(__file__).resolve().parents[1] / "verifier-js" / "bin" / "verify_bundle.mjs"


def records(ledger: Any, kind: str) -> list[dict[str, Any]]:
    return [r for r in (canonical.parse_record(ledger.read_record(seq)) for seq in range(ledger.size)) if r["type"] == kind]


def whole_batch(lg, seqs):  # type: ignore[no-untyped-def]
    """The ledger positions of a committed cycle's whole batch and its committing step, with the given ones."""
    (step,) = [r for r in records(lg.ledger, "cycle_state") if r["body"]["state"] == "COMMITTING"]
    at = lg.ledger.find(lg.services.source_urn, step["id"])
    assert at is not None
    return sorted(set(seqs) | set(range(at - step["body"]["batch_size"], at + 1)))


def test_a_cycle_is_committed_verified_offline_and_settled(tmp_path: Path) -> None:
    from tests.e2e_league import league

    with league(tmp_path) as lg:
        first = scheduler.tick(lg.services, lg.config)
        assert first.ran is not None and first.ran.state.value == "COMMITTED", first.ran and first.ran.reason
        commitment = first.ran.commitment
        assert commitment is not None and commitment.level == "C3" and commitment.uniform
        assert first.findings == () and first.closed == ()
        slots = first.ran.slots
        assert sorted((s.symbol, s.hours) for s in slots) == [(sym, h) for sym in ("BTC", "ETH") for h in (1, 4, 24)]
        assert {s.fate.value for s in slots} == {"FORECAST"} and {s.level for s in slots} == {"C3"}
        assert len(lg.exchange.requests) > 0

        # the bundle: the forecasts and the evidence they cite, against the checkpoint the authorities stamped
        forecasts = records(lg.ledger, "forecast")
        assert len(forecasts) == 6
        wanted = {r["id"] for r in forecasts} | {ref["id"] for r in forecasts for ref in r["refs"]}
        seqs = sorted(seq for source in (lg.services.source_urn,) for ident in wanted if (seq := lg.ledger.find(source, ident)) is not None)
        assert len(seqs) == len(wanted) == 6 + 6
        saved = commitment_store.load(lg.store_dir, commitment.commitment_id)
        out = tmp_path / "bundle"
        # the forecasts and what they cite alone do not show that nothing was left out of the cycle's batch: not scorable, by name
        partial = tmp_path / "partial"
        bundle.export_bundle(partial, lg.ledger, seqs, checkpoint_note=saved.note, size=commitment.size, tokens=saved.tokens)
        shown = verify.verify_bundle(partial, lg.root)["eligibility"]
        assert (shown["scorable"], shown["reasons"]) == (False, ["COMPLETENESS_NOT_SHOWN"])
        # the whole batch and the committing step that names it: complete, so scorable
        whole = whole_batch(lg, seqs)
        bundle.export_bundle(out, lg.ledger, whole, checkpoint_note=saved.note, size=commitment.size, tokens=saved.tokens)
        verdict = verify.verify_bundle(out, lg.root)
        assert verdict["cycle"]["complete"] and verdict["cycle"]["steps"] == 1
        assert verdict["integrity"]["ok"], json.dumps(
            {k: v for k, v in verdict["integrity"].items() if k != "items"}
            | {"bad": {k: v for k, v in verdict["integrity"]["items"].items() if v["error"] or not v["inclusion"] or not v["canonical"]}},
            default=str,
        )[:3000]
        assert verdict["root_of_trust"]["state"] == "PINNED"
        assert verdict["timing"]["level"] == "C3", verdict["timing"]
        assert verdict["eligibility"]["data_modes"] == ["live"]
        assert verdict["eligibility"]["scorable"], verdict["eligibility"]
        if NODE is not None:
            root_file = tmp_path / "root.json"
            root_file.write_bytes(lg.root.payload)
            done = subprocess.run([NODE, str(BIN), str(out), "--trusted-root", str(root_file)], capture_output=True, text=True, check=False)
            assert done.returncode == 0, done.stderr
            node = json.loads(done.stdout)
            assert node["timing"]["level"] == "C3" and node["eligibility"]["scorable"] and node["integrity"]["ok"]
            assert node["integrity"]["checkpoint"] == verdict["integrity"]["checkpoint"]
        # python -m hoshitori.cli gives the same
        cli = (
            subprocess.run(
                [sys.executable, "-m", "hoshitori.cli", str(out), "--trusted-root", str(root_file)],
                capture_output=True,
                text=True,
                check=False,
            )
            if NODE
            else None
        )
        assert cli is None or (cli.returncode == 0 and json.loads(cli.stdout)["timing"]["level"] == "C3")

        # a tampered forecast is caught
        evil = tmp_path / "evil"
        shutil.copytree(out, evil)
        victim = next(p for p in (evil / "items").iterdir() if p.name.endswith(".json") and b'"type":"forecast"' in p.read_bytes())
        victim.write_bytes(victim.read_bytes().replace(b'"p":"', b'"p":"0.', 1))
        assert not verify.verify_bundle(evil, lg.root)["integrity"]["ok"]

        # an hour passes: the next tick settles what matured, and also runs the cycle that has come in the meantime
        lg.clock.jump(62 * 60)
        second = scheduler.tick(lg.services, lg.config)
        assert second.ran is not None and second.ran.state.value == "COMMITTED"
        assert second.passed is not None and second.passed.deferred == ()
        settlements = records(lg.ledger, "settlement")
        assert len(settlements) == 2
        by_symbol = {s.symbol: s for s in slots if s.hours == 1}
        for rec in settlements:
            body = rec["body"]
            symbol = next(sym for sym, s in by_symbol.items() if s.forecast_id == body["forecast_id"])
            assert body["state"] == "SETTLED" and body["outcome"] == ("1" if symbol == "BTC" else "0"), body
            reference = by_symbol[symbol].origin_ms - 60_000
            assert Decimal(body["p0"]) == price(symbol, reference, lg.exchange.anchor)
            assert Decimal(body["outcome_price"]) == price(symbol, reference + HOUR, lg.exchange.anchor)
            settled, digest = settlement_record.from_record({k: v for k, v in rec.items() if k != "committed_at"})
            assert settled.pair_id == "A" and digest
            forecast_record = next(r for r in forecasts if r["id"] == body["forecast_id"])
            assert digest == record_digest({k: v for k, v in forecast_record.items() if k != "committed_at"})
        histories = lifecycle.rebuild(records(lg.ledger, "forecast_state"))
        ends = sorted(h[-1].step.state.value for fid, h in histories.items() if fid in {s.forecast_id for s in slots})
        assert ends == ["OPEN"] * 4 + ["SETTLED"] * 2
        assert origin(commitment.attestation.eligible_after_ms) == slots[0].origin_ms


def test_barrier_forecasts_are_committed_verified_and_settled(tmp_path: Path) -> None:
    """Contract B beside contract D: an hour out the trend has not reached 1.5 ATR; four hours out it has, at the 90th minute."""
    from tests.e2e_league import MINUTE, league
    from tests.league_checks import barrier_map

    with league(tmp_path, barrier=barrier_map()) as lg:
        first = scheduler.tick(lg.services, lg.config)
        assert first.ran is not None and first.ran.state.value == "COMMITTED", first.ran and first.ran.reason
        commitment = first.ran.commitment
        assert commitment is not None and commitment.level == "C3"
        slots = first.ran.slots
        assert len(slots) == 12 and {(s.contract, s.fate.value) for s in slots} == {("D", "FORECAST"), ("B", "FORECAST")}
        forecasts = records(lg.ledger, "forecast")
        barrier = [r["body"] for r in forecasts if r["body"]["contract"] == "B"]
        assert len(barrier) == 6 and {Decimal(b["atr"]) for b in barrier} == {Decimal(30)}  # a trend of 30 an hour: a true range of 30
        assert {(b["target_offset"], b["stop_offset"]) for b in barrier} == {("45", "45")}
        # the bundle holds the barrier forecasts and verifies
        wanted = {r["id"] for r in forecasts} | {ref["id"] for r in forecasts for ref in r["refs"]}
        seqs = sorted(seq for ident in wanted if (seq := lg.ledger.find(lg.services.source_urn, ident)) is not None)
        saved = commitment_store.load(lg.store_dir, commitment.commitment_id)
        out = tmp_path / "bundle"
        # the forecasts and what they cite alone do not show that nothing was left out of the cycle's batch: not scorable, by name
        partial = tmp_path / "partial"
        bundle.export_bundle(partial, lg.ledger, seqs, checkpoint_note=saved.note, size=commitment.size, tokens=saved.tokens)
        shown = verify.verify_bundle(partial, lg.root)["eligibility"]
        assert (shown["scorable"], shown["reasons"]) == (False, ["COMPLETENESS_NOT_SHOWN"])
        # the whole batch and the committing step that names it: complete, so scorable
        whole = whole_batch(lg, seqs)
        bundle.export_bundle(out, lg.ledger, whole, checkpoint_note=saved.note, size=commitment.size, tokens=saved.tokens)
        verdict = verify.verify_bundle(out, lg.root)
        assert verdict["cycle"]["complete"] and verdict["cycle"]["steps"] == 1
        assert verdict["integrity"]["ok"] and verdict["timing"]["level"] == "C3" and verdict["eligibility"]["scorable"]
        origin_ms = slots[0].origin_ms
        assert origin_ms is not None
        by_id = {s.forecast_id: s for s in slots}

        def settled_b(hours: int) -> dict[str, dict[str, Any]]:
            out_: dict[str, dict[str, Any]] = {}
            for rec in records(lg.ledger, "settlement"):
                body = rec["body"]
                made = by_id.get(body["forecast_id"])
                if made is not None and made.contract == "B" and made.hours == hours:
                    out_[made.symbol] = body
            return out_

        lg.clock.jump(62 * 60)
        scheduler.tick(lg.services, lg.config)
        one = settled_b(1)
        assert sorted(one) == ["BTC", "ETH"]
        for symbol, body in one.items():
            assert (body["contract"], body["state"], body["outcome"], body["touch_open_ms"]) == ("B", "SETTLED", "TIMEOUT", None), body
            assert Decimal(body["p0"]) == price(symbol, origin_ms - MINUTE, lg.exchange.anchor)
        lg.clock.jump(3 * 3600)
        scheduler.tick(lg.services, lg.config)
        four = settled_b(4)
        assert sorted(four) == ["BTC", "ETH"]
        touch = origin_ms + 89 * MINUTE  # the bar that opens 89 minutes in closes at 45 away from the reference price
        assert (four["BTC"]["outcome"], four["BTC"]["touch_open_ms"]) == ("TARGET", touch), four["BTC"]
        assert (four["ETH"]["outcome"], four["ETH"]["touch_open_ms"]) == ("STOP", touch), four["ETH"]
        histories = lifecycle.rebuild(records(lg.ledger, "forecast_state"))
        assert {histories[fid][-1].step.state.value for fid, s in by_id.items() if s.contract == "B" and s.hours in (1, 4)} == {"SETTLED"}


def test_practice_portfolio_buys_holds_and_sells_what_the_rules_arm_called_long(tmp_path: Path) -> None:
    """The practice portfolio beside the cycle: books open at once, wait for the bar they are filled in, buy BTC (the trend is up), mark,
    and sell the one-hour purchase at the end of its horizon, at the price the exchange's own formula gives."""
    import dataclasses

    from hoshitori import book, practice, practice_record
    from hoshitori.timeline import to_ms
    from tests.e2e_league import MINUTE, league

    with league(tmp_path) as lg:
        config = dataclasses.replace(lg.config, practice=(practice.Config(),))

        def books() -> dict[str, Any]:
            return practice.books_of(records(lg.ledger, "practice"))

        first = scheduler.tick(lg.services, config)
        assert first.ran is not None and first.ran.state.value == "COMMITTED" and first.practiced is not None
        assert sorted(books()) == ["L0:BASE", "L0:DOUBLE_FEES", "L0:STRESS"]
        assert all(len(b.events) == 1 and not b.buys for b in books().values())  # opened, and nothing bought before the origin
        origin_ms = first.ran.slots[0].origin_ms
        assert origin_ms is not None

        lg.clock.jump(4 * 60)
        second = scheduler.tick(lg.services, config)
        assert second.ran is None and second.practiced is not None and [x.unread for x in second.practiced] == [()]
        for name, held in books().items():
            assert len(held.buys) == 3, name  # the three BTC forecasts of the cycle; ETH is not a LONG call
            state = book.replay(held.events)
            assert [p.symbol for p in state.positions] == ["BTC"] and state.cash >= 0
            assert state.equity > 0 and state.mode.value == "ACTIVE"
        forecast_ids = {r["id"] for r in records(lg.ledger, "forecast")}
        assert set(books()["L0:BASE"].buys) <= forecast_ids

        lg.clock.jump(62 * 60)
        third = scheduler.tick(lg.services, config)
        assert third.practiced is not None
        base = books()["L0:BASE"]
        assert len(base.sold) >= 1
        end_open = (origin_ms + 3_600_000 + MINUTE - 1) // MINUTE * MINUTE
        sells = [
            practice_record.from_record({k: v for k, v in r.items() if k != "committed_at"})
            for r in records(lg.ledger, "practice")
            if r["body"]["event"]["event"] == "sell" and r["body"]["book"] == "L0:BASE"
        ]
        by_forecast = {e.forecast_id: e.event for e in sells}
        one_hour = {s.forecast_id for s in first.ran.slots if s.hours == 1 and s.symbol == "BTC"}
        assert set(by_forecast) & one_hour, "the one-hour BTC purchase was not sold at the end of its horizon"
        assert to_ms(lg.clock.now()) > end_open
        sold_price = by_forecast[next(iter(set(by_forecast) & one_hour))].price
        assert sold_price == price("BTC", end_open, lg.exchange.anchor) * (1 - Decimal("0.0005"))
        # another tick starts nothing new: no opening, no purchase and no sale
        again = scheduler.tick(lg.services, config)
        assert again.practiced is not None
        assert {
            type(entry.event).__name__ for entry in (practice_record.from_record(r) for each in again.practiced for r in each.records)
        } <= {"Mark"}


def test_funding_is_read_through_the_registry_committed_and_absent_without_the_source(tmp_path: Path) -> None:
    """The optional funding source in a whole cycle, offline. The fake exchange also serves the Binance USD-M exchange list (every recorded
    BTC and ETH row, no SOL row) and ``premiumIndex`` (``tests/e2e_binance_usdm.json``). With the source named, BTC and ETH each get the
    funding of the one perpetual the registry joins to their token, cited by the snapshot and held by the commitment; SOL has no perpetual,
    and that failure comes back with the cycle and is written nowhere in the ledger (worklog finding F-A1-3: the product as it is today).
    Without the source, the same world commits the same slots with no funding item and no funding call."""
    from hoshitori import collect
    from hoshitori.timeline import from_ms
    from hoshitori.timeutil import format_rfc3339_ms
    from hoshitori.venue_data import InstrumentKind, instrument_id_of
    from tests.e2e_league import FUNDING, league

    watch = ("BTC", "ETH", "SOL")
    lists = [("binance-spot", "spot", "accepted", 3), ("okx", "spot", "accepted", 3), ("binance-usdm", "perpetual", "accepted", 9)]
    funded: dict[str, Any] = {}

    def look(lg: Any, ran: Any) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        """The snapshot's entry of each symbol, and every evidence record by id, as the ledger holds them."""
        taken = next(r for r in records(lg.ledger, "snapshot") if r["body"]["cycle_id"] == ran.cycle_id)
        symbol_of = {s.candidate_id: s.symbol for s in ran.slots}
        return {symbol_of[c["candidate_id"]]: c for c in taken["body"]["candidates"]}, {r["id"]: r for r in records(lg.ledger, "evidence")}

    def shape(entry: dict[str, Any], evidence: dict[str, dict[str, Any]]) -> tuple[list[str], list[Any], list[Any]]:
        return sorted(evidence[ref["evidence_id"]]["body"]["path"] for ref in entry["items"]), entry["missing"], entry["rejected"]

    def read_lists(tick: Any) -> list[tuple[str, str, str, int]]:
        assert tick.refreshed is not None and tick.refreshed.failures == (), tick.refreshed and tick.refreshed.failures
        headers = [e for r in tick.refreshed.records for e in r["events"] if e["t"] == "snapshot"]
        return [(e["venue"], e["scope"], e["outcome"], e["count"]) for e in headers]

    with league(tmp_path / "with", watchlist=watch, perpetuals=True, optional_sources=("binance-funding",)) as lg:
        rules = lg.services.protocol
        assert rules.optional_sources == ("binance-funding",) and rules.body()["optional_sources"] == ["binance-funding"]
        first = scheduler.tick(lg.services, lg.config)
        ran = first.ran
        assert ran is not None and ran.state.value == "COMMITTED", ran and ran.reason
        assert ran.history[0].step.protocol_hash == rules.sha256
        commitment = ran.commitment
        assert commitment is not None and commitment.level == "C3" and commitment.uniform
        assert first.findings == () and first.closed == ()
        assert read_lists(first) == lists  # the USD-M list keeps its nine perpetuals; its four dated futures are not perpetuals
        # one funding read for each token the registry joins to a perpetual of multiplier one quoted in USDT, and none for its others
        asked = sorted(query for path, query in lg.exchange.requests if path == "/fapi/v1/premiumIndex")
        assert asked == ["symbol=BTCUSDT", "symbol=ETHUSDT"], asked
        registry = lg.services.env.registry
        entries, evidence = look(lg, ran)
        funding_ids = set()
        for symbol, others in (("BTC", ["BTCU", "BTCUSD1", "BTCUSDC"]), ("ETH", ["ETHBTC", "ETHU", "ETHUSD1", "ETHUSDC"])):
            asset = registry.lookup_asset(entries[symbol]["asset_id"])
            assert asset is not None
            on_usdm = {}
            for iid in registry.instruments_of(asset.token_id):
                listed = registry.instrument_at(iid)
                if listed is not None and listed.venue == "binance-usdm":
                    on_usdm[listed.raw_id] = listed
            assert sorted(on_usdm) == sorted([f"{symbol}USDT", *others]), sorted(on_usdm)
            joined = on_usdm[f"{symbol}USDT"]
            perpetual = instrument_id_of("binance-usdm", InstrumentKind.PERPETUAL, f"{symbol}USDT")
            assert joined.instrument_id == perpetual
            assert (joined.kind, joined.multiplier, joined.quote_class) == (InstrumentKind.PERPETUAL, "1", "USDT")
            (record,) = [r for r in (evidence[ref["evidence_id"]] for ref in entries[symbol]["items"]) if r["body"]["path"] == "funding"]
            body, served = record["body"], lg.exchange.premium[f"{symbol}USDT"]
            assert served["lastFundingRate"] == FUNDING[symbol][0]
            assert body["fields"] == {
                "index_price": served["indexPrice"],
                "last_funding_rate": served["lastFundingRate"],
                "mark_price": served["markPrice"],
                "next_funding_time": format_rfc3339_ms(from_ms(served["nextFundingTime"])),
                "server_time": format_rfc3339_ms(from_ms(served["time"])),
            }, body["fields"]
            assert (body["kind"], body["source"], body["origin_family"], body["adapter_version"], body["data_mode"]) == (
                "OBSERVATION",
                "binance-funding",
                "EXCHANGE:binance-funding",
                "collect-funding-1",
                "live",
            )
            assert body["request"] == {"instrument": f"{symbol}USDT"}
            assert body["asset_refs"] == [{"asset_id": asset.asset_id, "instrument_id": perpetual, "status": "RESOLVED"}]
            # the snapshot cites it by the digest of its record, and the commitment holds it at C3 with the forecasts
            (ref,) = [ref for ref in entries[symbol]["items"] if ref["evidence_id"] == record["id"]]
            assert ref["sha256"] == record_digest({k: v for k, v in record.items() if k != "committed_at"})
            assert commitment.level_of(lg.services.source_urn, record["id"]) == "C3"
            funding_ids.add(record["id"])
        shapes = {symbol: shape(entries[symbol], evidence) for symbol in watch}
        assert shapes == {
            "BTC": (["features", "funding", "series", "series"], [], []),
            "ETH": (["features", "funding", "series", "series"], [], []),
            "SOL": (["features", "series", "series"], [], []),
        }, shapes
        # the rules arm reads the features item and nothing else, so no forecast cites the funding (nor would the cycle's bundle hold it)
        forecasts = records(lg.ledger, "forecast")
        cited = [sorted(evidence[ref["id"]]["body"]["path"] for ref in r["refs"]) for r in forecasts]
        assert cited == [["features", "series", "series"]] * 6, cited
        assert funding_ids.isdisjoint(ref["id"] for r in forecasts for ref in r["refs"])
        slots = sorted((s.symbol, s.hours, s.fate.value, s.reason, s.level) for s in ran.slots)
        assert slots == [
            *((symbol, hours, "FORECAST", None, "C3") for symbol in ("BTC", "ETH") for hours in (1, 4, 24)),
            *(("SOL", hours, "NO_CALL", "NO_RANGE", "C3") for hours in (1, 4, 24)),  # the fake's SOL price is flat: no range, no call
        ], slots
        # SOL, with no perpetual: the failure comes back with the cycle, and the snapshot in the ledger holds its closed reason and the source,
        # never the words
        sol = next(s.candidate_id for s in ran.slots if s.symbol == "SOL")
        assert ran.failures == (
            collect.Failure(
                sol, collect.Reason.NO_PERPETUAL, "the registry lists no perpetual of the token on binance-usdm", source="binance-funding"
            ),
        ), ran.failures
        written = b"".join(lg.ledger.read_record(seq) for seq in range(lg.ledger.size))
        assert b'{"interval_s":null,"reason":"NO_PERPETUAL","source":"binance-funding"}' in written and b"lists no perpetual" not in written
        funded.update(slots=slots, shapes=shapes)

    with league(tmp_path / "without", watchlist=watch, perpetuals=True) as lg:
        rules = lg.services.protocol
        assert rules.optional_sources == () and "optional_sources" not in rules.body()
        first = scheduler.tick(lg.services, lg.config)
        ran = first.ran
        assert ran is not None and ran.state.value == "COMMITTED", ran and ran.reason
        assert ran.commitment is not None and ran.commitment.level == "C3" and ran.commitment.uniform
        assert read_lists(first) == lists  # the same world: the USD-M list is read, the adapter is there
        assert [path for path, _ in lg.exchange.requests if path == "/fapi/v1/premiumIndex"] == []
        assert ran.failures == ()
        entries, evidence = look(lg, ran)
        assert [r["id"] for r in evidence.values() if r["body"]["path"] == "funding"] == []
        shapes = {symbol: shape(entries[symbol], evidence) for symbol in watch}
        assert shapes == dict.fromkeys(watch, (["features", "series", "series"], [], [])), shapes
        assert sorted((s.symbol, s.hours, s.fate.value, s.reason, s.level) for s in ran.slots) == funded["slots"]
        # F-A1-3 seen from the ledger: SOL's failed funding read and funding never asked for leave the same snapshot entry
        assert shapes["SOL"] == funded["shapes"]["SOL"]


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_node_is_present() -> None:
    assert BIN.is_file()
