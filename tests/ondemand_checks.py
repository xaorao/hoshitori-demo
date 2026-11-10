"""Checks of the on-demand forecast (``hoshitori.ondemand``): the sealed submission and its refusals, runnable against a mutant (plan J-01)."""

from __future__ import annotations

import dataclasses
import json
import tempfile
import types
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from hoshitori import commitment_store, desk, desk_gates, forecast, lifecycle
from hoshitori.clock import ManualClock
from hoshitori.league import put
from hoshitori.registry import Registry
from hoshitori.timeline import from_ms, origin
from hoshitori.timeutil import format_rfc3339_ms
from tests.commit_checks import RULES, START, TickClock, attestor, ledger_in
from tests.settle_job_checks import MINUTE, Rig

HOUR = 3_600_000
NOW = 1_800_000_000_000
D = Decimal
EVIDENCE = (("ev1", "a" * 64), ("ev2", "b" * 64))


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def true(self, label: str, value: object) -> None:
        if not value:
            self.bad.append(f"{label}: expected a true value, got {value!r}")

    def raises(self, label: str, call: Callable[[], object], text: str | None = None) -> None:
        try:
            call()
        except self.mod.OnDemandError as exc:
            if text is not None and text not in str(exc):
                self.bad.append(f"{label}: raised without {text!r}: {exc}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}, expected OnDemandError")
            return
        self.bad.append(f"{label}: raised nothing")


def context(mod: types.ModuleType, **over: Any) -> Any:
    values: dict[str, Any] = {
        "asset_id": "asset1",
        "symbol": "SOL",
        "quote_class": "USDT",
        "instrument_kind": "spot",
        "oracle_pair": "A",
        "oracle_primary": "binance-spot",
        "oracle_second": "okx-spot",
        "as_of_ms": NOW - 60_000,
        "horizon_ms": HOUR,
        "agent_version_hash": "c" * 64,
        "protocol_hash": "d" * 64,
        "observation_set_hash": "e" * 64,
        "evidence": EVIDENCE,
    }
    values.update(over)
    return mod.Context(**values)


def long(p: str = "0.62", weight: str = "0.02") -> Any:
    return desk_gates.FinalAction("LONG", D(p), D(weight))


def flat() -> Any:
    return desk_gates.FinalAction("FLAT", D("0.5"), D(0))


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("the class", mod.CLASS, "ON_DEMAND")
    c.eq("the arm", mod.ARM_ID, "ON_DEMAND")
    c.eq("the cycle prefix", mod.CYCLE_PREFIX, "od-")
    c.eq("the oldest evidence a commit may rest on", mod.MAX_EVIDENCE_AGE_MS, 900_000)
    c.eq("the source prefix", mod.SOURCE_PREFIX, "urn:hoshitori:ondemand:")
    c.eq("the invalidation sentence", mod.INVALIDATION, ("The move began before the announcement or the market moved alike.",))
    c.eq("the source of a run", mod.source_urn("run-9"), "urn:hoshitori:ondemand:run-9")
    c.eq("a committed result is frozen", mod.Committed.__dataclass_params__.frozen, True)
    return c.bad


def check_submission(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    made = mod.submission_from_desk(long(), "run-1", context(mod), committed_at_ms=NOW)
    c.true("a directional desk call becomes a submission", isinstance(made, mod.Submission))
    if not isinstance(made, mod.Submission):
        return c.bad
    f = made.forecast
    c.true("it is contract D", isinstance(f, forecast.DirectionalForecast))
    c.eq("its probability is the call's", f.p, D("0.62"))
    c.eq("its cycle id is the class prefix and the run", f.common.cycle_id, "od-run-1")
    c.eq("its arm is the class's", f.common.arm_id, "ON_DEMAND")
    c.eq("its horizon is the context's", f.common.horizon_ms, HOUR)
    c.eq("its evidence is the context's", f.common.evidence, EVIDENCE)
    c.eq("it shows no interpretation", f.common.interpretation_visible, False)
    again = mod.submission_from_desk(long(), "run-1", context(mod), committed_at_ms=NOW)
    c.eq("the same run is the same forecast id", again.forecast.common.forecast_id, f.common.forecast_id)  # type: ignore[union-attr]
    for name, value in (("a context", context(mod)), ("a submission", made), ("a no-forecast", mod.NoForecast("r", "FLAT_REFERENCE"))):
        try:
            setattr(value, "run_id" if name != "a context" else "symbol", "x")
        except dataclasses.FrozenInstanceError:
            continue
        c.bad.append(f"{name} can be changed once made")
    other = mod.submission_from_desk(long(), "run-2", context(mod), committed_at_ms=NOW)
    c.true("another run is another forecast id", other.forecast.common.forecast_id != f.common.forecast_id)  # type: ignore[union-attr]
    return c.bad


def check_no_forecast(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    refused = mod.submission_from_desk(flat(), "run-1", context(mod), committed_at_ms=NOW)
    c.true("a flat reference is not a forecast", isinstance(refused, mod.NoForecast))
    if isinstance(refused, mod.NoForecast):
        c.eq("it says why", refused.reason, "FLAT_REFERENCE")
    c.raises(
        "a stance that is neither long nor flat is refused",
        lambda: mod.submission_from_desk(desk_gates.FinalAction("SHORT", D("0.4"), D("0.01")), "run-1", context(mod), committed_at_ms=NOW),
        "stance",
    )
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.raises(
        "evidence older than the limit is refused before anything is stamped",
        lambda: mod.submission_from_desk(long(), "r", context(mod, as_of_ms=NOW - 900_001), committed_at_ms=NOW),
        "stale",
    )
    ok = mod.submission_from_desk(long(), "r", context(mod, as_of_ms=NOW - 900_000), committed_at_ms=NOW)
    c.true("evidence exactly at the limit is accepted", isinstance(ok, mod.Submission))
    same = mod.submission_from_desk(long(), "r", context(mod, as_of_ms=NOW), committed_at_ms=NOW)
    c.true("evidence from the instant of the commit is accepted", isinstance(same, mod.Submission))
    c.raises(
        "evidence from after the commit is refused",
        lambda: mod.submission_from_desk(long(), "r", context(mod, as_of_ms=NOW + 1), committed_at_ms=NOW),
        "after",
    )
    c.raises(
        "a horizon under an hour is refused",
        lambda: mod.submission_from_desk(long(), "r", context(mod, horizon_ms=HOUR - 60_000), committed_at_ms=NOW),
    )
    c.raises(
        "a run id that is not an id is refused", lambda: mod.submission_from_desk(long(), "a b", context(mod), committed_at_ms=NOW), "run"
    )
    c.raises("a run id that is empty is refused", lambda: mod.submission_from_desk(long(), "", context(mod), committed_at_ms=NOW), "run")
    c.raises(
        "a probability outside zero to one is refused",
        lambda: mod.submission_from_desk(long("1.2"), "r", context(mod), committed_at_ms=NOW),
    )
    return c.bad


def check_privacy(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    sentinel = "my private note about this claim"
    for name in ("asset_id", "symbol", "quote_class", "instrument_kind", "oracle_pair", "oracle_primary", "oracle_second"):
        c.raises(
            f"free text in {name} is refused",
            lambda n=name: mod.submission_from_desk(long(), "r", context(mod, **{n: sentinel}), committed_at_ms=NOW),
            "identifier",
        )
    for name in ("agent_version_hash", "protocol_hash", "observation_set_hash"):
        c.raises(
            f"a {name} that is not a digest is refused",
            lambda n=name: mod.submission_from_desk(long(), "r", context(mod, **{n: sentinel}), committed_at_ms=NOW),
            "digest",
        )
    c.raises(
        "free text as an evidence id is refused",
        lambda: mod.submission_from_desk(long(), "r", context(mod, evidence=((sentinel, "a" * 64),)), committed_at_ms=NOW),
        "evidence",
    )
    c.raises(
        "a bad evidence digest is refused",
        lambda: mod.submission_from_desk(long(), "r", context(mod, evidence=(("ev1", sentinel),)), committed_at_ms=NOW),
        "evidence",
    )
    made = mod.submission_from_desk(long(), "r", context(mod), committed_at_ms=NOW)
    c.eq("the invalidation is the Desk's own sentence", made.forecast.common.invalidation, mod.INVALIDATION)
    c.eq("and nothing in the forecast body carries the sentinel", sentinel in json.dumps(made.forecast.to_body()), False)
    c.eq("the context has no field for invalidation text", "invalidation" in {f.name for f in dataclasses.fields(mod.Context)}, False)
    return c.bad


def _records(ledger: Any) -> list[dict[str, Any]]:
    return [json.loads(ledger.read_record(seq)) for seq in range(ledger.size)]


def _states(records: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    return [(r["body"]["state"], r["body"]) for r in records if r["type"] == "forecast_state"]


def check_commit(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    start_ms = int(START.timestamp() * 1000)
    with tempfile.TemporaryDirectory() as tmp:
        ledger = ledger_in(Path(tmp), TickClock(START, 1.0))
        sub = mod.submission_from_desk(long(), "run-c3", context(mod, as_of_ms=start_ms - 60_000), committed_at_ms=start_ms)
        store = Path(tmp) / "store"
        got = mod.commit(
            sub, ledger=ledger, attestor=attestor([("a", "ta", 1), ("b", "tb", 3)]), rules=RULES, at_ms=start_ms - 5_000, store=store
        )
        saved = commitment_store.load(store, got.commitment.commitment_id)
        c.eq(
            "the note and the tokens of the commitment are kept",
            (saved.note, saved.tokens),
            (got.commitment.note, got.commitment.attestation.tokens()),
        )
        c.eq("two operators in the window reach C3", got.level, "C3")
        c.eq("the id is the forecast's", got.forecast_id, sub.forecast.common.forecast_id)
        c.eq("it was committed when the ledger's clock said", got.committed_at_ms, start_ms)
        c.eq("eligibility is the attestation's", got.eligible_after_ms, got.commitment.attestation.eligible_after_ms)
        c.eq("the origin follows eligibility and not the submitter's time", got.origin_ms, origin(got.eligible_after_ms))
        c.true("the origin is after the commit", got.origin_ms > start_ms)
        records = _records(ledger)
        c.eq(
            "the forecast and the six steps of the slot",
            [r["type"] for r in records],
            ["forecast"] + ["forecast_state"] * 6,
        )
        c.eq("the forecast record is the on-demand class's", records[0]["body"]["arm_id"], "ON_DEMAND")
        c.true("every record is of the on-demand source", all(r["source"].startswith("urn:hoshitori:ondemand:") for r in records))
        states = _states(records)
        c.eq(
            "the steps in order",
            [name for name, _ in states],
            ["SCHEDULED", "CREATED", "EVIDENCE_FROZEN", "DECIDED", "COMMITTED", "AWAITING_ORIGIN"],
        )
        c.eq("the committed step names the level", states[4][1].get("level"), "C3")
        c.eq(
            "the committed step is at the later of our time and the ledger's",
            states[4][1]["at"],
            format_rfc3339_ms(from_ms(got.commitment.attestation.committed_at_ms)),
        )
        c.eq("it names the forecast's own commit time", states[4][1]["committed_at"], "2026-10-29T12:00:00.000Z")
        c.eq("the wait names the spread of the tokens in milliseconds", states[5][1]["spread_ms"], 2000)
        c.true("a C3 wait names the attested time", "attested_at" in states[5][1])
        ledger.close()
    for label, offsets in (
        ("one counted token", [("a", "ta", 1)]),
        ("no token", []),
        ("two tokens of one operator", [("a", "ta", 1), ("a", "ta2", 2)]),
        ("a second token after the window", [("a", "ta", 1), ("b", "tb", 100)]),
    ):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ledger_in(Path(tmp), ManualClock(START))
            sub = mod.submission_from_desk(long(), "run-low", context(mod, as_of_ms=start_ms - 60_000), committed_at_ms=start_ms)
            low = mod.commit(sub, ledger=ledger, attestor=attestor(offsets), rules=RULES, at_ms=start_ms)
            c.eq(f"{label} is C1 and never C3", low.level, "C1")
            wait = _states(_records(ledger))[-1][1]
            c.true(f"{label}: the wait does not name an attested time", "attested_at" not in wait)
            ledger.close()
    return c.bad


def _sub(mod: types.ModuleType, p: str = "0.62", run: str = "run-r") -> Any:
    start_ms = int(START.timestamp() * 1000)
    return mod.submission_from_desk(long(p), run, context(mod, as_of_ms=start_ms - 60_000), committed_at_ms=start_ms)


def check_idempotent_and_resume(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    start_ms = int(START.timestamp() * 1000)
    good = attestor([("a", "ta", 1), ("b", "tb", 3)])

    def boom(digest: bytes, committed_at_ms: int) -> Any:
        raise RuntimeError("the process was killed after the append")

    with tempfile.TemporaryDirectory() as tmp:
        ledger = ledger_in(Path(tmp), ManualClock(START))
        first = mod.commit(_sub(mod), ledger=ledger, attestor=good, rules=RULES, at_ms=start_ms)
        size = ledger.size
        again = mod.commit(_sub(mod), ledger=ledger, attestor=boom, rules=RULES, at_ms=start_ms + 5_000)
        c.eq("the same submission writes nothing", ledger.size, size)
        c.eq(
            "and finds the first result",
            (again.forecast_id, again.level, again.committed_at_ms, again.eligible_after_ms, again.origin_ms),
            (first.forecast_id, first.level, first.committed_at_ms, first.eligible_after_ms, first.origin_ms),
        )
        c.eq("a found result carries no commitment object", again.commitment, None)
        c.raises(
            "a different forecast under the same run is refused",
            lambda: mod.commit(_sub(mod, "0.7"), ledger=ledger, attestor=good, rules=RULES, at_ms=start_ms),
            "different",
        )
        ledger.close()
    for label, resume_offsets, level in (
        ("tokens that arrive in the window", [("a", "ta", 1), ("b", "tb", 3)], "C3"),
        ("tokens after the window", [("a", "ta", 100), ("b", "tb", 101)], "C1"),
    ):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ledger_in(Path(tmp), ManualClock(START))
            try:
                mod.commit(_sub(mod), ledger=ledger, attestor=boom, rules=RULES, at_ms=start_ms)
                c.bad.append("a killed stamp did not stop the commit")
            except RuntimeError:
                pass
            before = _states(_records(ledger))
            c.eq("a kill after the append leaves the slot decided and not committed", [n for n, _ in before][-1:], ["DECIDED"])
            done = mod.commit(
                _sub(mod), ledger=ledger, attestor=attestor(resume_offsets), rules=RULES, at_ms=start_ms, store=Path(tmp) / "s"
            )
            c.eq(
                f"a resumed commit keeps its commitment ({label})",
                commitment_store.load(Path(tmp) / "s", done.commitment.commitment_id).note,
                done.commitment.note,
            )
            c.eq(f"resuming with {label} gives {level}", done.level, level)
            after = [n for n, _ in _states(_records(ledger))]
            c.eq(
                f"the steps after resuming with {label}",
                after,
                ["SCHEDULED", "CREATED", "EVIDENCE_FROZEN", "DECIDED", "COMMITTED", "AWAITING_ORIGIN"],
            )
            c.eq("no record was written twice", len({r["id"] for r in _records(ledger)}), ledger.size)
            ledger.close()
    with tempfile.TemporaryDirectory() as tmp:
        ledger = ledger_in(Path(tmp), ManualClock(START))
        sub = _sub(mod)
        urn = mod.source_urn(sub.run_id)
        history = lifecycle.begin(sub.forecast.common.forecast_id, "D", sub.forecast.common.horizon_ms, start_ms)
        history = lifecycle.advance(history, lifecycle.Step(lifecycle.State.CREATED, start_ms))
        put(ledger, [forecast.to_record(sub.forecast, urn, start_ms), *(lifecycle.to_record(t, urn, start_ms) for t in history)])
        c.raises(
            "a commit that stopped before it was decided cannot be resumed",
            lambda: mod.commit(sub, ledger=ledger, attestor=good, rules=RULES, at_ms=start_ms),
            "resumed",
        )
        ledger.close()
    return c.bad


def _world_context(mod: types.ModuleType, rig: Any, symbol: str, now: int) -> Any:
    body = next(r["body"] for r in rig.w.records("forecast") if r["body"]["symbol"] == symbol)
    return mod.Context(
        body["asset_id"],
        symbol,
        body["quote_class"],
        body["instrument_kind"],
        body["oracle_pair"],
        body["oracle_primary"],
        body["oracle_second"],
        now - 60_000,
        HOUR,
        "c" * 64,
        "d" * 64,
        "e" * 64,
        EVIDENCE,
    )


def check_settle(mod: types.ModuleType) -> list[str]:
    from hoshitori import settle_job
    from hoshitori.timeline import to_ms

    c = Case(mod)
    with tempfile.TemporaryDirectory() as tmp:
        rig = Rig(Path(tmp) / "world")
        now = to_ms(rig.w.clock.now())
        ledger = ledger_in(Path(tmp) / "od", rig.w.clock)
        results = {}
        for symbol in ("BTC", "ETH"):
            sub = mod.submission_from_desk(long(), f"run-{symbol}", _world_context(mod, rig, symbol, now), committed_at_ms=now)
            results[symbol] = mod.commit(
                sub, ledger=ledger, attestor=attestor([("a", "ta", 1), ("b", "tb", 3)]), rules=RULES, at_ms=now, registry=rig.w.registry
            )
        listed = [r for r in _records(ledger) if r["type"] == "registry"]
        c.eq(
            "each run keeps the instrument lists it was made against, under its own source",
            sorted({r["source"] for r in listed}),
            sorted(mod.source_urn(f"run-{symbol}") for symbol in ("BTC", "ETH")),
        )
        c.eq("and each has all of them", len(listed), 2 * len(rig.w.registry.records()))
        origin_ms = results["BTC"].origin_ms
        rig.market.reference_open = origin_ms - MINUTE
        full = rig.services(settle_job)
        # the caller's registry is empty: the pair is found from the lists the ledger holds
        services = dataclasses.replace(full, env=dataclasses.replace(full.env, registry=Registry()))
        rig.at(origin_ms + HOUR // 2)
        early = mod.settle(services, ledger)
        c.eq("before the horizon nothing is settled", [r["type"] for r in early.records if r["type"] == "settlement"], [])
        rig.market.no_candles = {"binance-spot", "okx"}
        rig.at(origin_ms + HOUR + 5 * MINUTE)
        late = mod.settle(services, ledger)
        c.eq(
            "with no candles after the horizon nothing is settled and both wait",
            (len(late.pending), [r["type"] for r in late.records if r["type"] == "settlement"]),
            (2, []),
        )
        rig.market.no_candles = set()
        done = mod.settle(services, ledger)
        by_symbol = {}
        for r in done.records:
            if r["type"] == "settlement":
                by_symbol[next(k for k, v in results.items() if v.forecast_id == r["body"]["forecast_id"])] = r["body"]
        c.eq("two settlements are written", sorted(by_symbol), ["BTC", "ETH"])
        c.eq(
            "and the slots that moved are returned with their histories",
            sorted(done.histories),
            sorted(r.forecast_id for r in results.values()),
        )
        c.eq(
            "a rise is outcome one at the references of the origin",
            (by_symbol["BTC"]["state"], by_symbol["BTC"]["outcome"], by_symbol["BTC"]["p0"], by_symbol["BTC"]["outcome_price"]),
            ("SETTLED", "1", "100", "101"),
        )
        c.eq(
            "a fall is outcome zero",
            (by_symbol["ETH"]["state"], by_symbol["ETH"]["outcome"], by_symbol["ETH"]["outcome_price"]),
            ("SETTLED", "0", "99"),
        )
        histories = lifecycle.rebuild([r for r in _records(ledger) if r["type"] == "forecast_state"])
        ends = {lifecycle.current_state(h).value for h in histories.values()}
        c.eq("both end settled", ends, {"SETTLED"})
        size = ledger.size
        again = mod.settle(services, ledger)
        c.eq("a second pass writes nothing", (again.records, ledger.size), ((), size))
        ledger.close()
        # a ledger that was committed without the lists cannot be settled against whatever the caller happens to have read
        bare = ledger_in(Path(tmp) / "bare", rig.w.clock)
        sub = mod.submission_from_desk(long(), "run-bare", _world_context(mod, rig, "BTC", now), committed_at_ms=now)
        mod.commit(sub, ledger=bare, attestor=attestor([("a", "ta", 1), ("b", "tb", 3)]), rules=RULES, at_ms=now)
        c.raises(
            "settling against a ledger with no lists",
            lambda: mod.settle(full, bare),
            "the ledger holds no instrument lists for a run, so the oracle of its forecast cannot be found",
        )
        # two runs with different lists: each finds its pair in its own, never in the other's
        split = ledger_in(Path(tmp) / "split", rig.w.clock)
        made_split = {}
        only_first = Registry.replay(rig.w.registry.records()[:1])  # one venue's list: no second oracle, so no pair
        for symbol, lists in (("BTC", rig.w.registry), ("ETH", only_first)):
            sub = mod.submission_from_desk(long(), f"split-{symbol}", _world_context(mod, rig, symbol, now), committed_at_ms=now)
            made_split[symbol] = mod.commit(
                sub, ledger=split, attestor=attestor([("a", "ta", 1), ("b", "tb", 3)]), rules=RULES, at_ms=now, registry=lists
            )
        rig.market.reference_open = made_split["BTC"].origin_ms - MINUTE
        rig.at(made_split["BTC"].origin_ms + HOUR + 5 * MINUTE)
        got_split = mod.settle(services, split)
        settled_ids = {r["body"]["forecast_id"] for r in got_split.records if r["type"] == "settlement"}
        c.eq(
            "the run with both lists settles and the run with one waits",
            (settled_ids, got_split.pending),
            ({made_split["BTC"].forecast_id}, (made_split["ETH"].forecast_id,)),
        )
        split.close()
        # a run without any lists among runs with theirs is refused as a whole
        mixed = ledger_in(Path(tmp) / "mixed", rig.w.clock)
        for symbol, lists in (("BTC", rig.w.registry), ("ETH", Registry())):
            sub = mod.submission_from_desk(long(), f"mix-{symbol}", _world_context(mod, rig, symbol, now), committed_at_ms=now)
            mod.commit(sub, ledger=mixed, attestor=attestor([("a", "ta", 1), ("b", "tb", 3)]), rules=RULES, at_ms=now, registry=lists)
        c.raises("a run without its lists among runs with theirs", lambda: mod.settle(full, mixed), "for a run")
        mixed.close()
        # two more ledgers for what a pass reports: a venue that gives no time defers, a slot that cannot be decided fails alone
        for name in ("deferred", "failed"):
            more = ledger_in(Path(tmp) / name, rig.w.clock)
            made = {}
            for symbol in ("BTC", "ETH"):
                sub = mod.submission_from_desk(long(), f"{name}-{symbol}", _world_context(mod, rig, symbol, now), committed_at_ms=now)
                made[symbol] = mod.commit(
                    sub, ledger=more, attestor=attestor([("a", "ta", 1), ("b", "tb", 3)]), rules=RULES, at_ms=now, registry=rig.w.registry
                )
            ids = sorted(m.forecast_id for m in made.values())
            rig.market.reference_open = made["BTC"].origin_ms - MINUTE
            rig.at(made["BTC"].origin_ms + HOUR + 5 * MINUTE)
            if name == "deferred":
                rig.market.no_time.add("okx")
                got = mod.settle(services, more)
                rig.market.no_time.clear()
                c.eq(
                    "a venue that gives no time defers every slot and settles none",
                    (sorted(got.deferred), got.pending, got.failed),
                    (ids, (), ()),
                )
            else:
                real = settle_job._decide

                def poisoned(slot: Any, *args: Any, bad: str = made["ETH"].forecast_id, original: Any = real, **kwargs: Any) -> Any:
                    if slot.forecast.common.forecast_id == bad:
                        raise settle_job.SettleJobError("damaged")
                    return original(slot, *args, **kwargs)

                settle_job._decide = poisoned
                try:
                    got = mod.settle(services, more)
                finally:
                    settle_job._decide = real
                c.eq(
                    "a slot that cannot be decided is named and the other settles",
                    (got.failed, len(got.records) > 0),
                    (((made["ETH"].forecast_id, "damaged"),), True),
                )
                c.eq(
                    "the histories of every slot that moved come back",
                    sorted(got.histories),
                    ids,
                )
            more.close()
        nothing = ledger_in(Path(tmp) / "empty", rig.w.clock)
        c.eq("a ledger with nothing waiting needs no lists", mod.settle(full, nothing).records, ())
        nothing.close()
        bare.close()
    return c.bad


class _Store:
    def digest(self, ident: str) -> str:
        return {"ev1": "a" * 64, "ev2": "b" * 64}[ident]


def check_desk_port(mod: types.ModuleType) -> list[str]:
    import hashlib

    from hoshitori import canonical

    c = Case(mod)
    start_ms = int(START.timestamp() * 1000)

    def port(ledger: Any, started: int) -> Any:
        wiring = mod.Wiring(ledger, attestor([("a", "ta", 1), ("b", "tb", 3)]), RULES, "USDT", "spot", "A", "binance-spot", "okx-spot")
        return mod.desk_commit_port(
            wiring,
            asset_id="asset1",
            symbol="SOL",
            horizon_ms=HOUR,
            started_ms=started,
            store=_Store(),
            versions=("c" * 64, "d" * 64),
            now_ms=lambda: start_ms,
        )

    def request(final: Any, run: str = "run-p") -> Any:
        return desk.CommitRequest(run, final, {}, {"evidence_ids": ["ev2", "ev1"]}, "live")

    with tempfile.TemporaryDirectory() as tmp:
        ledger = ledger_in(Path(tmp), ManualClock(START))
        named = port(ledger, start_ms - 60_000)(request(long()))
        c.true("a long call returns a forecast id", named.startswith("fc"))
        records = _records(ledger)
        body = next(r["body"] for r in records if r["type"] == "forecast")
        c.eq("the forecast carries the run's evidence by id, in the order the run read it", body["evidence_ids"], ["ev2", "ev1"])
        want = hashlib.sha256(canonical.dumps({"evidence": [["ev1", "a" * 64], ["ev2", "b" * 64]]})).hexdigest()
        c.eq("and the digest of that list as its observation set", body["observation_set_hash"], want)
        c.eq("the versions are the run's", (body["agent_version_hash"], body["protocol_hash"]), ("c" * 64, "d" * 64))
        c.eq("its horizon is the trigger's", body["horizon_ms"], HOUR)
        c.eq(
            "its oracle facts are the league's",
            (body["oracle_pair"], body["oracle_primary"], body["oracle_second"]),
            ("A", "binance-spot", "okx-spot"),
        )
        wiring_now = mod.Wiring(ledger, attestor([]), RULES, "USDT", "spot", "A", "p", "s")
        try:
            wiring_now.quote_class = "x"  # type: ignore[misc]
            c.bad.append("a wiring can be changed once made")
        except dataclasses.FrozenInstanceError:
            pass
        size = ledger.size
        flat_id = port(ledger, start_ms - 60_000)(request(flat(), "run-q"))
        c.eq("a flat call returns the run's own name and writes nothing", (flat_id, ledger.size), ("live-run-q", size))
        c.raises(
            "a run that started too long ago is refused and writes nothing",
            lambda: port(ledger, start_ms - 900_001)(request(long(), "run-r")),
            "stale",
        )
        c.eq("nothing was written for the refused run", ledger.size, size)
        ledger.close()
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_submission,
        check_no_forecast,
        check_refusals,
        check_privacy,
        check_desk_port,
        check_commit,
        check_idempotent_and_resume,
        check_settle,
    ):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
