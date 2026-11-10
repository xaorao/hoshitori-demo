"""Checks of the post-snapshot events and thesis revisions (``hoshitori.post_snapshot``), runnable against a mutant."""

from __future__ import annotations

import json
import tempfile
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hoshitori.clock import ManualClock
from hoshitori.evidence import record_digest
from hoshitori.registry import Registry
from tests.commit_checks import START, ledger_in
from tests.registry_checks import DELISTED, HALTED, inst
from tests.triggers_checks import admit, ms, spot
from tests.triggers_checks import lst as listing

NOW = 1_800_000_000_000
H = "a" * 64
SOURCE = "urn:hoshitori:cycle:test"


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
        except self.mod.PostSnapshotError as exc:
            if text is not None and text not in str(exc):
                self.bad.append(f"{label}: raised without {text!r}: {exc}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}, expected PostSnapshotError")
            return
        self.bad.append(f"{label}: raised nothing")


def event(mod: types.ModuleType, **over: Any) -> Any:
    values: dict[str, Any] = {
        "kind": "REGISTRY_INSTRUMENT_REMOVED",
        "asset_id": "asset-btc",
        "instrument_id": "inst-btc-binance",
        "venue": "binance-spot",
        "status": None,
        "first_seen_ms": NOW,
        "ingested_ms": NOW + 1_000,
        "previous_snapshot": ("snap-1", H),
        "relation": "NEW",
        "related": None,
        "captures": (("cap-1", H),),
    }
    values.update(over)
    return mod.Event(**values)


def thesis(
    mod: types.ModuleType,
    ident: str = "fc-a",
    asset: str = "asset-btc",
    venues: tuple[str, ...] = ("binance-spot", "okx"),
    as_of_ms: int = NOW - 60_000,
) -> Any:
    return mod.Thesis(ident, "b" * 64, asset, venues, as_of_ms)


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("types", (mod.EVENT_TYPE, mod.REVISION_TYPE, mod.TYPE_VERSION), ("post_snapshot_event", "thesis_revision", 1))
    c.eq("kinds", mod.KINDS, ("REGISTRY_NEW_INSTRUMENT", "REGISTRY_INSTRUMENT_REMOVED", "REGISTRY_STATUS_CHANGED"))
    c.eq("statuses", mod.STATUSES, ("TRADING", "HALTED", "DELISTED", "UNKNOWN"))
    c.eq("the last instant of year 9999", mod.MAX_INSTANT_MS, 253_402_300_799_999)
    c.eq("relations", mod.RELATIONS, ("NEW", "DUPLICATE_OF", "CORRECTS"))
    c.eq("revision kinds", (mod.MATERIAL, mod.NOT_MATERIAL), ("MATERIAL_UPDATE", "NO_MATERIAL_CHANGE"))
    c.eq(
        "the premise and the reasons",
        (mod.PREMISE, mod.REASONS),
        ("ORACLE_INSTRUMENT_AVAILABLE", ("ORACLE_IMPAIRED", "INSTRUMENT_ADDED", "TRADING_RESUMED")),
    )
    for cls in (mod.Event, mod.Thesis, mod.Revision):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    return c.bad


def check_event_rules(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.raises("an unknown kind", lambda: event(mod, kind="SOMETHING"), "one of")
    c.raises("a status change without a status", lambda: event(mod, kind="REGISTRY_STATUS_CHANGED", status=None), "status")
    c.raises("a status on another kind", lambda: event(mod, status="HALTED"), "status")
    c.raises("a status outside the list", lambda: event(mod, kind="REGISTRY_STATUS_CHANGED", status="PAUSED"), "status")
    for field, label in (("asset_id", "the asset"), ("instrument_id", "the instrument"), ("venue", "the venue")):
        c.raises(f"free text in {field}", lambda f=field: event(mod, **{f: "a headline about the token"}), f"{label} is an identifier")
    c.raises("a first-seen time after ingestion", lambda: event(mod, first_seen_ms=NOW + 2_000), "after we ingested")
    c.raises("a negative first-seen time", lambda: event(mod, first_seen_ms=-1), "the first-seen time is a whole number")
    c.raises("a boundary ingestion time", lambda: event(mod, ingested_ms=mod.MAX_INSTANT_MS + 1), "the ingestion time is a whole number")
    c.raises("a negative instant", lambda: event(mod, first_seen_ms=-1), "milliseconds")
    c.raises("an instant beyond year 9999", lambda: event(mod, ingested_ms=mod.MAX_INSTANT_MS + 1), "milliseconds")
    c.raises("a boolean instant", lambda: event(mod, first_seen_ms=True), "milliseconds")
    c.raises("a new event that names another", lambda: event(mod, related="pe-x"), "new event")
    c.raises("a duplicate that names none", lambda: event(mod, relation="DUPLICATE_OF"), "new event")
    c.raises("an unknown relation", lambda: event(mod, relation="REPEATS", related="pe-x"), "new event")
    c.raises("a related event that is not an id", lambda: event(mod, relation="CORRECTS", related="free text here"), "event id")
    c.raises("a previous snapshot with a bad digest", lambda: event(mod, previous_snapshot=("snap-1", "nope")), "previous snapshot")
    c.raises("a capture with a bad digest", lambda: event(mod, captures=(("cap-1", "nope"),)), "capture")
    edge = event(mod, first_seen_ms=mod.MAX_INSTANT_MS, ingested_ms=mod.MAX_INSTANT_MS)
    c.eq("the last instant of year 9999 is allowed", edge.ingested_ms, mod.MAX_INSTANT_MS)
    zero = event(mod, first_seen_ms=0, ingested_ms=0)
    c.eq("instant zero is allowed", zero.first_seen_ms, 0)
    ok = event(mod, first_seen_ms=NOW, ingested_ms=NOW)
    c.eq("first seen at the instant of ingestion is allowed", ok.first_seen_ms, ok.ingested_ms)
    same, other = event(mod), event(mod, instrument_id="inst-btc-binance2")
    c.eq("the dedupe key is a fixed function of what the change is", same.dedupe_key, "dkz7pogxkih5xpzqcr4hdnuv6doy")
    c.eq("the event id is a fixed function of the change, the time and the relation", same.event_id, "pejg555shudqhziu5dr2abxj3bvk")
    c.true(
        "another status is another key",
        event(mod, kind="REGISTRY_STATUS_CHANGED", status="HALTED").dedupe_key
        != event(mod, kind="REGISTRY_STATUS_CHANGED", status="DELISTED").dedupe_key,
    )
    c.true("another venue is another key", event(mod, venue="okx").dedupe_key != same.dedupe_key)
    c.true("another asset is another key", event(mod, asset_id="asset-eth").dedupe_key != same.dedupe_key)
    c.true("another kind is another key", event(mod, kind="REGISTRY_NEW_INSTRUMENT").dedupe_key != same.dedupe_key)
    c.true(
        "a correction of one event is another id from a correction of another",
        event(mod, relation="CORRECTS", related="pe-1").event_id != event(mod, relation="CORRECTS", related="pe-2").event_id,
    )
    c.true(
        "a relation is part of the id",
        event(mod, relation="CORRECTS", related="pe-1").event_id != event(mod, relation="DUPLICATE_OF", related="pe-1").event_id,
    )
    c.eq("the same change has one dedupe key and one id", (same.dedupe_key, same.event_id), (event(mod).dedupe_key, event(mod).event_id))
    c.true("another instrument is another key", same.dedupe_key != other.dedupe_key)
    c.true(
        "another time is another id but the same key",
        event(mod, first_seen_ms=NOW + 5).event_id != same.event_id and event(mod, first_seen_ms=NOW + 5).dedupe_key == same.dedupe_key,
    )
    return c.bad


def check_matching_and_materiality(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    e = event(mod)
    c.true("the same asset on an oracle venue", mod.affects(e, thesis(mod)))
    c.true("the same asset on the second oracle venue", mod.affects(event(mod, venue="okx"), thesis(mod)))
    c.eq("the same ticker on another asset is not reached", mod.affects(e, thesis(mod, asset="asset-btc-other")), False)
    c.eq("the same asset on a venue the thesis does not use is not reached", mod.affects(event(mod, venue="kraken"), thesis(mod)), False)
    c.eq("an event first seen before the thesis's evidence was taken is not reached", mod.affects(e, thesis(mod, as_of_ms=NOW + 1)), False)
    c.eq("one first seen at the same instant is not reached either", mod.affects(e, thesis(mod, as_of_ms=NOW)), False)
    c.eq("one first seen a millisecond after is", mod.affects(e, thesis(mod, as_of_ms=NOW - 1)), True)
    removed = mod.evaluate(e, thesis(mod))
    c.eq(
        "a removed oracle instrument is material",
        (removed.kind, removed.changed, removed.reason),
        ("MATERIAL_UPDATE", ("ORACLE_INSTRUMENT_AVAILABLE",), "ORACLE_IMPAIRED"),
    )
    for status in ("HALTED", "DELISTED", "UNKNOWN"):
        got = mod.evaluate(event(mod, kind="REGISTRY_STATUS_CHANGED", status=status), thesis(mod))
        c.eq(
            f"{status} is material (the oracle cannot be shown to be available)",
            (got.kind, got.reason),
            ("MATERIAL_UPDATE", "ORACLE_IMPAIRED"),
        )
    back = mod.evaluate(event(mod, kind="REGISTRY_STATUS_CHANGED", status="TRADING"), thesis(mod))
    c.eq("trading resumed is not material", (back.kind, back.changed, back.reason), ("NO_MATERIAL_CHANGE", (), "TRADING_RESUMED"))
    new = mod.evaluate(event(mod, kind="REGISTRY_NEW_INSTRUMENT"), thesis(mod))
    c.eq("a new instrument is not material", (new.kind, new.changed, new.reason), ("NO_MATERIAL_CHANGE", (), "INSTRUMENT_ADDED"))
    c.eq(
        "a revision names its event and thesis",
        (removed.event_id, removed.thesis_id, removed.thesis_digest),
        (e.event_id, "fc-a", "b" * 64),
    )
    c.raises(
        "a material revision names a premise", lambda: mod.Revision("pe-1", "fc-a", H, "MATERIAL_UPDATE", (), "ORACLE_IMPAIRED"), "premise"
    )
    c.raises(
        "a non-material revision names none",
        lambda: mod.Revision("pe-1", "fc-a", H, "NO_MATERIAL_CHANGE", ("ORACLE_INSTRUMENT_AVAILABLE",), "INSTRUMENT_ADDED"),
        "premise",
    )
    c.raises("an unknown premise", lambda: mod.Revision("pe-1", "fc-a", H, "MATERIAL_UPDATE", ("OTHER",), "ORACLE_IMPAIRED"), "premise")
    c.raises("an unknown reason", lambda: mod.Revision("pe-1", "fc-a", H, "NO_MATERIAL_CHANGE", (), "WHATEVER"), "reason")
    c.eq("a revision's id is a fixed function of the event and the thesis", removed.revision_id, "trop426ijlcvdkyd7oyr5cmhxgl4")
    c.true("another thesis is another revision id", removed.revision_id != mod.evaluate(e, thesis(mod, "fc-b")).revision_id)
    return c.bad


def _records(ledger: Any) -> list[dict[str, Any]]:
    return [json.loads(ledger.read_record(seq)) for seq in range(ledger.size)]


def check_theses_of(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    forecast = {
        "type": "forecast",
        "id": "fc-1",
        "committed_at": "2026-10-29T12:00:00.000Z",
        "body": {"asset_id": "asset-btc", "oracle_primary": "binance-spot", "oracle_second": "okx", "as_of": "2026-10-29T11:59:00.000Z"},
        "source": SOURCE,
    }
    other = {"type": "forecast_state", "id": "st-1", "body": {}}
    got = mod.theses_of([other, forecast])
    c.eq(
        "only forecasts are theses",
        [(t.thesis_id, t.asset_id, t.oracle_venues, t.as_of_ms) for t in got],
        [("fc-1", "asset-btc", ("binance-spot", "okx"), 1_793_275_140_000)],
    )
    done = {"type": "settlement", "id": "se-1", "body": {"forecast_id": "fc-1"}}
    c.eq("a settled forecast is no longer a thesis", mod.theses_of([forecast, done]), ())
    elsewhere = {"type": "settlement", "id": "se-2", "body": {"forecast_id": "fc-2"}}
    c.eq("another forecast's settlement changes nothing", [t.thesis_id for t in mod.theses_of([forecast, elsewhere])], ["fc-1"])
    clean = {k: v for k, v in forecast.items() if k != "committed_at"}
    c.eq("the digest is of the record without the ledger's commit time", got[0].digest, record_digest(clean))
    c.eq("nothing in, nothing out", mod.theses_of([]), ())
    return c.bad


def check_events_of(mod: types.ModuleType) -> list[str]:
    c = Case(mod)

    def okx(raw: str, base: str, **over: Any) -> Any:
        return inst("okx-spot", raw, base, **over)

    reg = Registry()
    admit(reg, listing("binance-spot", spot(), spot("SOLUSDT", "SOL"), spot("ETHUSDT", "ETH")), 0)
    admit(reg, listing("okx-spot", okx("BTC-USDT", "BTC"), okx("SOL-USDT", "SOL"), okx("ETH-USDT", "ETH")), 1)
    admit(reg, listing("binance-spot", spot(), spot("ETHUSDT", "ETH", status=HALTED)), 60)
    admit(reg, listing("okx-spot", okx("BTC-USDT", "BTC"), okx("SOL-USDT", "SOL"), okx("ETH-USDT", "ETH"), okx("DOGE-USDT", "DOGE")), 80)
    admit(reg, listing("binance-spot", spot(), spot("ETHUSDT", "ETH", status=HALTED), spot("DOGEUSDT", "DOGE")), 90)
    snap = ("snap-1", H)
    events, unresolved = mod.events_of(reg, after_ms=ms(30), until_ms=ms(1000), previous_snapshot=snap, ingested_ms=ms(500))
    c.eq("a removal, a status change and a new instrument on two sources are events", (len(events), unresolved), (3, 1))
    added = next(e for e in events if e.kind == "REGISTRY_NEW_INSTRUMENT")
    c.eq(
        "a new instrument is joined as it stands when it appears: both venues list it",
        (added.venue, added.first_seen_ms),
        ("binance-spot", ms(90)),
    )
    removed = next(e for e in events if e.kind == "REGISTRY_INSTRUMENT_REMOVED")
    halted = next(e for e in events if e.kind == "REGISTRY_STATUS_CHANGED")
    joined = reg.join(spot("SOLUSDT", "SOL").instrument_id, __import__("hoshitori.registry", fromlist=["Want"]).Want(), mod.from_ms(ms(59)))
    c.eq("a removed instrument keeps the asset it had just before the list lost it", removed.asset_id, joined.asset.asset_id)
    c.eq(
        "its venue and instrument",
        (removed.venue, removed.instrument_id, removed.status),
        ("binance-spot", spot("SOLUSDT", "SOL").instrument_id, None),
    )
    c.eq("a status change names the status it moved to", (halted.venue, halted.status), ("binance-spot", "HALTED"))
    c.eq(
        "events are new, carry the sealed snapshot and the capture list",
        {(e.relation, e.related, e.previous_snapshot, e.captures) for e in events},
        {("NEW", None, snap, ())},
    )
    c.eq("first seen is when the registry learned of the change", {removed.first_seen_ms, halted.first_seen_ms}, {ms(60)})
    c.eq("ingestion is at or after first seen", {e.ingested_ms for e in events}, {ms(500)})
    late, _ = mod.events_of(reg, after_ms=ms(30), until_ms=ms(1000), previous_snapshot=None, ingested_ms=ms(10))
    c.eq("an ingestion time before the change is lifted to it", {e.ingested_ms for e in late}, {ms(60), ms(90)})
    c.eq(
        "a window that ends before the change holds none",
        mod.events_of(reg, after_ms=ms(0), until_ms=ms(30), previous_snapshot=None, ingested_ms=ms(100)),
        ((), 0),
    )
    solo = Registry()
    admit(solo, listing("binance-spot", spot(), spot("SOLUSDT", "SOL")), 0)
    admit(solo, listing("binance-spot", spot()), 60)
    got, count = mod.events_of(solo, after_ms=ms(30), until_ms=ms(1000), previous_snapshot=None, ingested_ms=ms(100))
    c.eq("a token with one source behind it has no identity: counted, never guessed", (got, count), ((), 1))
    _ = DELISTED
    return c.bad


def check_ingest(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    with tempfile.TemporaryDirectory() as tmp:
        ledger = ledger_in(Path(tmp), ManualClock(START))
        theses = [thesis(mod, "fc-a"), thesis(mod, "fc-d", venues=("kraken", "coinbase")), thesis(mod, "fc-e", asset="asset-eth")]
        # an earlier record that must stay byte-identical
        before = [ledger.read_record(s) for s in range(ledger.size)]
        got, revisions = mod.ingest(ledger, event(mod), theses, source=SOURCE)
        c.eq("only the thesis on that asset and oracle is reached", [r.thesis_id for r in revisions], ["fc-a"])
        c.eq("it is a material update", [r.kind for r in revisions], ["MATERIAL_UPDATE"])
        recs = _records(ledger)
        c.eq("an event and one revision were written", [r["type"] for r in recs], ["post_snapshot_event", "thesis_revision"])
        c.eq("the event names the snapshot that was sealed before it", recs[0]["body"]["previous_snapshot"], ["snap-1", H])
        c.eq(
            "and the revision says which premise changed",
            (recs[1]["body"]["kind"], recs[1]["body"]["changed"], recs[1]["body"]["reason"]),
            ("MATERIAL_UPDATE", ["ORACLE_INSTRUMENT_AVAILABLE"], "ORACLE_IMPAIRED"),
        )
        c.eq("earlier records are byte-identical", [ledger.read_record(s) for s in range(len(before))], before)
        digest = record_digest({k: v for k, v in recs[0].items() if k != "committed_at"})
        refs = {r["id"]: r["sha256"] for r in recs[1]["refs"]}
        c.eq("the revision's references are the thesis digest and the event's own digest", refs, {"fc-a": "b" * 64, got.event_id: digest})
        c.eq("the records are public", {r["privacy"] for r in recs}, {"public"})
        c.eq(
            "the event holds identity and no free text",
            sorted(recs[0]["body"]),
            sorted(
                [
                    "kind",
                    "asset_id",
                    "instrument_id",
                    "venue",
                    "status",
                    "first_seen_at",
                    "previous_snapshot",
                    "dedupe_key",
                    "relation",
                    "related",
                    "captures",
                ]
            ),
        )
        size = ledger.size
        again, again_revisions = mod.ingest(ledger, event(mod), theses, source=SOURCE)
        c.eq("the same event again writes nothing", ledger.size, size)
        c.eq("and returns what was written", (again.event_id, again_revisions), (got.event_id, revisions))
        dup, dup_revisions = mod.ingest(ledger, event(mod, first_seen_ms=NOW + 500), theses, source=SOURCE)
        c.eq("the same change reported later is a duplicate of the first", (dup.relation, dup.related), ("DUPLICATE_OF", got.event_id))
        c.eq("and causes no revision", dup_revisions, ())
        c.eq("it is recorded once more, as an event only", [r["type"] for r in _records(ledger)][size:], ["post_snapshot_event"])
        _second, second_revisions = mod.ingest(ledger, event(mod, venue="okx", instrument_id="inst-btc-okx"), theses, source=SOURCE)
        c.eq("another venue's event reaches the same thesis on its own", [r.thesis_id for r in second_revisions], ["fc-a"])
        c.true("with its own revision id", second_revisions[0].revision_id != revisions[0].revision_id)
        fix = event(
            mod,
            kind="REGISTRY_STATUS_CHANGED",
            status="TRADING",
            relation="CORRECTS",
            related=got.event_id,
            instrument_id="inst-btc-binance",
        )
        fixed, fixed_revisions = mod.ingest(ledger, fix, theses, source=SOURCE)
        c.eq("a correction names what it corrects", (fixed.relation, fixed.related), ("CORRECTS", got.event_id))
        c.eq(
            "and is evaluated on its own: trading resumed is not material",
            [(r.kind, r.reason) for r in fixed_revisions],
            [("NO_MATERIAL_CHANGE", "TRADING_RESUMED")],
        )
        fresh, _ = mod.ingest(
            ledger, event(mod, asset_id="asset-ada", instrument_id="inst-ada", previous_snapshot=None), theses, source=SOURCE
        )
        c.eq("an event with no sealed snapshot before it says so", _records(ledger)[-1]["body"]["previous_snapshot"], None)
        c.true("and is recorded", fresh.previous_snapshot is None)
        none, none_revisions = mod.ingest(ledger, event(mod, asset_id="asset-sol", instrument_id="inst-sol"), theses, source=SOURCE)
        c.eq("an event no thesis concerns is recorded and causes no revision", (none.relation, none_revisions), ("NEW", ()))
        ledger.close()
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_event_rules, check_matching_and_materiality, check_theses_of, check_events_of, check_ingest):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
