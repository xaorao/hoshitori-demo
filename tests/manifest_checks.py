"""Checks of the candidate manifest (``hoshitori.manifest``), runnable against a mutant."""

from __future__ import annotations

import hashlib
import types
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from hoshitori import canonical, evidence
from hoshitori.ids import natural_id

D = Decimal
T = 1_793_311_000_000
H = "a" * 64
F = evidence.Family
K = evidence.FamilyKind
BIN = F(K.EXCHANGE, "binance-spot")
OKX = F(K.EXCHANGE, "okx")


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str | None = None) -> None:
        try:
            call()
        except self.mod.ManifestError as exc:
            if says is not None and says != str(exc):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def resolver(mod: types.ModuleType) -> Callable[[str], Any]:
    """S-names are assets with an oracle, N-names are assets without one, X-names are ambiguous, anything else is unknown."""

    def resolve(symbol: str) -> Any:
        head = symbol[:1].upper()
        if head == "S":
            return mod.Resolved("tk-" + symbol.lower(), True, True)
        if head == "N":
            return mod.Resolved("tk-" + symbol.lower(), True, False)
        if head == "X":
            return mod.Resolved(None, False, True)
        return mod.Resolved(None, True, True)

    return resolve


def rules(mod: types.ModuleType, **over: Any) -> Any:
    values: dict[str, Any] = {
        "scan_rule_version": "scan-1",
        "scan_rule_hash": "1" * 64,
        "scan_n": 10,
        "event_rule_version": "event-1",
        "event_rule_hash": "2" * 64,
        "event_cap": 5,
        "watchlist_config_hash": "3" * 64,
    }
    values.update(over)
    return mod.Rules(**values)


def trigger(mod: types.ModuleType, ident: str, symbol: str, **over: Any) -> Any:
    values: dict[str, Any] = {
        "trigger_id": ident,
        "rule": "LISTING_ANNOUNCED",
        "rule_version": "1",
        "symbol": symbol,
        "primary": True,
        "families": frozenset({BIN}),
        "first_seen_at_ms": T,
        "impact": D("1"),
    }
    values.update(over)
    return mod.Trigger(**values)


def inputs(mod: types.ModuleType, **over: Any) -> Any:
    values: dict[str, Any] = {
        "cycle_id": "cy-1",
        "protocol_hash": H,
        "discovery_at_ms": T,
        "sealed_at_ms": T + 5_000,
        "scan": tuple(mod.ScanRow(rank, f"S{rank:02d}") for rank in range(1, 13)),
        "scan_capture": mod.Capture("cap-scan", "4" * 64),
        "triggers": (),
        "trigger_capture": mod.Capture("cap-log", "5" * 64),
        "watchlist": (),
    }
    values.update(over)
    return mod.Inputs(**values)


def build(mod: types.ModuleType, **over: Any) -> Any:
    return mod.build(inputs(mod, **over), rules(mod), resolver(mod))


def by_symbol(manifest: Any, symbol: str) -> Any:
    wanted = natural_id("cd", {"cycle": manifest.inputs.cycle_id, "key": "tk-" + symbol.lower()})
    return next((e for e in manifest.entries if e.candidate_id == wanted), None)


def decisions(entry: Any) -> list[tuple[str, bool, str | None]]:
    return [(r.route.value, r.included, None if r.reason is None else r.reason.value) for r in entry.routes]


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("record type", (mod.TYPE, mod.TYPE_VERSION, mod.DEFAULT_SCAN_N, mod.DEFAULT_EVENT_CAP, mod.MAX_ROWS), ("manifest", 1, 10, 5, 1000))
    c.eq("routes", [r.value for r in mod.Route], ["RYO_SCAN", "EVENT", "WATCHLIST"])
    c.eq(
        "exclusions",
        [e.value for e in mod.Exclusion],
        ["below_rank_cutoff", "duplicate", "capped", "ineligible_asset", "no_oracle", "trigger_not_qualified"],
    )
    for cls in (
        mod._Proposal,
        mod.Resolved,
        mod.ScanRow,
        mod.Trigger,
        mod.Capture,
        mod.Rules,
        mod.Failed,
        mod.Inputs,
        mod.RouteEntry,
        mod.Entry,
        mod.Manifest,
    ):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    return c.bad


def check_scan_route(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    m = build(mod)
    inc = {e.asset_id for e in m.union()}
    c.eq("the first ten of twelve are in", inc, {f"tk-s{n:02d}" for n in range(1, 11)})
    c.eq(
        "the last two are below the cutoff",
        [decisions(by_symbol(m, f"S{n:02d}")) for n in (11, 12)],
        [[("RYO_SCAN", False, "below_rank_cutoff")]] * 2,
    )
    c.eq("a candidate carries its rank", [r.rank for r in by_symbol(m, "S04").routes], [4])
    c.eq("every row is in the manifest, in or out", len(m.entries), 12)
    # rows in any order give the same manifest
    shuffled = build(mod, scan=tuple(reversed(inputs(mod).scan)))
    c.eq("the order of the rows does not matter", shuffled.sha256, m.sha256)
    # a candidate that cannot be settled does not let a lower rank in
    scan = (
        mod.ScanRow(1, "S01"),
        mod.ScanRow(2, "N02"),
        mod.ScanRow(3, "X03"),
        mod.ScanRow(4, "Q04"),
        *(mod.ScanRow(r, f"S{r:02d}") for r in range(5, 13)),
    )
    m2 = build(mod, scan=scan)
    c.eq("no oracle", decisions(by_symbol(m2, "N02")), [("RYO_SCAN", False, "no_oracle")])
    c.eq("an ambiguous symbol", decisions(next(e for e in m2.entries if e.symbol == "X03")), [("RYO_SCAN", False, "ineligible_asset")])
    c.eq("an unknown symbol", decisions(next(e for e in m2.entries if e.symbol == "Q04")), [("RYO_SCAN", False, "ineligible_asset")])
    c.eq(
        "the cutoff is on RYO's rank, not on how many were eligible",
        by_symbol(m2, "S11") is not None and decisions(by_symbol(m2, "S11")),
        [("RYO_SCAN", False, "below_rank_cutoff")],
    )
    c.eq("so ten rows were looked at and seven are in", len(m2.union()), 7)
    c.eq(
        "an excluded candidate says why, at the entry",
        sorted((e.symbol, e.reason.value) for e in m2.entries if e.symbol in ("N02", "X03")),
        [("N02", "no_oracle"), ("X03", "ineligible_asset")],
    )
    # duplicates keep the better rank
    dup = build(mod, scan=(mod.ScanRow(7, "s01"), mod.ScanRow(2, "S01"), mod.ScanRow(1, "S02")))
    c.eq("the same asset twice", len(dup.entries), 2)
    c.eq(
        "the better rank stays, whatever order the rows came in",
        [(r.rank, r.included, r.reason.value if r.reason else None) for r in by_symbol(dup, "S01").routes],
        [(2, True, None), (7, False, "duplicate")],
    )
    below = build(mod, scan=(*(mod.ScanRow(r, f"S{r:02d}") for r in range(1, 11)), mod.ScanRow(11, "S20"), mod.ScanRow(12, "S20")))
    c.eq(
        "a repeat of an asset that was already below the cutoff is a duplicate",
        [(r.rank, r.reason.value) for r in by_symbol(below, "S20").routes],
        [(11, "below_rank_cutoff"), (12, "duplicate")],
    )
    # the cutoff is on the rank value: gaps count
    gap = build(mod, scan=(mod.ScanRow(1, "S01"), mod.ScanRow(2, "S02"), mod.ScanRow(5, "S03"), mod.ScanRow(20, "S04")))
    c.eq(
        "a rank beyond the cutoff",
        (decisions(by_symbol(gap, "S03")), decisions(by_symbol(gap, "S04"))),
        ([("RYO_SCAN", True, None)], [("RYO_SCAN", False, "below_rank_cutoff")]),
    )
    exactly = mod.build(inputs(mod), rules(mod, scan_n=3), resolver(mod))
    c.eq(
        "1000 triggers are allowed",
        len(
            mod.build(
                inputs(mod, scan=(), triggers=tuple(trigger(mod, f"t{n}", "S01") for n in range(1000))), rules(mod), resolver(mod)
            ).entries
        ),
        1,
    )
    c.eq("a cutoff of three", sorted(e.asset_id for e in exactly.union()), ["tk-s01", "tk-s02", "tk-s03"])
    one = mod.build(inputs(mod, scan=(mod.ScanRow(1, "S01"),)), rules(mod, scan_n=1), resolver(mod))
    c.eq("a cutoff of one", len(one.union()), 1)
    return c.bad


def check_event_route(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    empty = {"scan": ()}
    m = build(mod, **empty, triggers=(trigger(mod, "t1", "S01"),))
    c.eq("a primary document is enough", decisions(by_symbol(m, "S01")), [("EVENT", True, None)])
    c.eq("and the trigger is named", (by_symbol(m, "S01").routes[0].trigger_id, by_symbol(m, "S01").routes[0].first_seen_at_ms), ("t1", T))
    unq = build(mod, **empty, triggers=(trigger(mod, "t1", "S01", primary=False),))
    c.eq("one origin and no primary document is not enough", decisions(by_symbol(unq, "S01")), [("EVENT", False, "trigger_not_qualified")])
    two = build(mod, **empty, triggers=(trigger(mod, "t1", "S01", primary=False, families=frozenset({BIN, OKX})),))
    c.eq("two origins are enough", decisions(by_symbol(two, "S01")), [("EVENT", True, None)])
    derived_only = build(mod, **empty, triggers=(trigger(mod, "t1", "S01", primary=False, families=frozenset({evidence.DERIVED, BIN})),))
    c.eq("a derived family is not an origin", decisions(by_symbol(derived_only, "S01")), [("EVENT", False, "trigger_not_qualified")])
    bad = build(mod, **empty, triggers=(trigger(mod, "t1", "N01"), trigger(mod, "t2", "X01"), trigger(mod, "t3", "Q01")))
    c.eq(
        "an unqualified or unsettleable trigger is listed with its reason",
        sorted((e.symbol, e.reason.value) for e in bad.entries),
        [("N01", "no_oracle"), ("Q01", "ineligible_asset"), ("X01", "ineligible_asset")],
    )
    # qualification is checked before eligibility
    both = build(mod, **empty, triggers=(trigger(mod, "t1", "N01", primary=False),))
    c.eq(
        "an unqualified trigger on an asset without an oracle is unqualified",
        decisions(by_symbol(both, "N01")),
        [("EVENT", False, "trigger_not_qualified")],
    )
    # duplicates: one rule, one asset, the earliest stays
    dup = build(
        mod,
        **empty,
        triggers=(
            trigger(mod, "t2", "S01", first_seen_at_ms=T + 20),
            trigger(mod, "t1", "S01", first_seen_at_ms=T + 10),
            trigger(mod, "t3", "S01", rule="DELISTING_ANNOUNCED", first_seen_at_ms=T + 30),
        ),
    )
    c.eq(
        "two triggers of one rule on one asset",
        sorted(decisions_with_trigger(by_symbol(dup, "S01"))),
        sorted([("EVENT", "t1", None), ("EVENT", "t2", "duplicate"), ("EVENT", "t3", None)]),
    )
    tie = build(mod, **empty, triggers=(trigger(mod, "b", "S01"), trigger(mod, "a", "S01")))
    c.eq(
        "with the same time, the lower id stays",
        sorted(decisions_with_trigger(by_symbol(tie, "S01"))),
        [("EVENT", "a", None), ("EVENT", "b", "duplicate")],
    )
    # the cap: highest impact first, then earliest, then id
    many = tuple(trigger(mod, f"t{n}", f"S{n:02d}", impact=D(n % 4), first_seen_at_ms=T + n) for n in range(1, 9))
    capped = mod.build(inputs(mod, scan=(), triggers=many), rules(mod, event_cap=3), resolver(mod))
    included = sorted(e.symbol for e in capped.union())
    # impacts: S03 -> 3, S07 -> 3, S02 -> 2, S06 -> 2, S01 -> 1, S05 -> 1, S04 -> 0, S08 -> 0
    c.eq("the three of highest impact, the earlier first among equals", included, ["S02", "S03", "S07"])
    c.eq(
        "the others are capped",
        sorted(e.symbol for e in capped.entries if e.reason is mod.Exclusion.CAPPED),
        ["S01", "S04", "S05", "S06", "S08"],
    )
    shuffled = mod.build(inputs(mod, scan=(), triggers=tuple(reversed(many))), rules(mod, event_cap=3), resolver(mod))
    c.eq("the order of the triggers does not matter", shuffled.sha256, capped.sha256)
    none = mod.build(inputs(mod, scan=(), triggers=many), rules(mod, event_cap=0), resolver(mod))
    c.eq("a cap of zero", (len(none.union()), len([e for e in none.entries if e.reason is mod.Exclusion.CAPPED])), (0, 8))
    plus = mod.build(inputs(mod, scan=(), triggers=many[:5]), rules(mod, event_cap=2), resolver(mod))
    c.eq(
        "E plus 3 triggers admit E and list 3 as capped",
        (len(plus.union()), len([e for e in plus.entries if e.reason is mod.Exclusion.CAPPED])),
        (2, 3),
    )
    negative = mod.build(
        inputs(mod, scan=(), triggers=(trigger(mod, "n", "S01", impact=D("-1")), trigger(mod, "z", "S02", impact=D("0")))),
        rules(mod, event_cap=1),
        resolver(mod),
    )
    c.eq("a negative impact ranks below zero", [e.symbol for e in negative.union()], ["S02"])
    return c.bad


def decisions_with_trigger(entry: Any) -> list[tuple[str, str | None, str | None]]:
    return [(r.route.value, r.trigger_id, None if r.reason is None else r.reason.value) for r in entry.routes]


def check_watchlist_and_merging(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    m = build(mod, scan=(), watchlist=("S01", "N02", "X03", "S01", "s01", "Q04"))
    c.eq(
        "a watchlist symbol with an oracle",
        decisions(by_symbol(m, "S01")),
        [("WATCHLIST", True, None), ("WATCHLIST", False, "duplicate"), ("WATCHLIST", False, "duplicate")],
    )
    c.eq("without an oracle", decisions(by_symbol(m, "N02")), [("WATCHLIST", False, "no_oracle")])
    c.eq(
        "ambiguous and unknown",
        sorted(e.reason.value for e in m.entries if e.symbol in ("X03", "Q04")),
        ["ineligible_asset", "ineligible_asset"],
    )
    # one asset, three routes
    three = build(mod, scan=(mod.ScanRow(1, "S01"),), triggers=(trigger(mod, "t1", "S01"),), watchlist=("S01",))
    entry = by_symbol(three, "S01")
    c.eq(
        "one candidate with three routes",
        (len(three.entries), entry.tags, entry.included),
        (1, (mod.Route.RYO_SCAN, mod.Route.EVENT, mod.Route.WATCHLIST), True),
    )
    c.eq("and the routes are in the order of the routes", [r.route.value for r in entry.routes], ["RYO_SCAN", "EVENT", "WATCHLIST"])
    # included by one route and out by another
    mixed = build(
        mod,
        scan=tuple(mod.ScanRow(r, f"S{r:02d}") for r in range(1, 13)),
        triggers=(trigger(mod, "t1", "S12", primary=False),),
        watchlist=("S12",),
    )
    e12 = by_symbol(mixed, "S12")
    c.eq(
        "out of the scan, out of the event route, in the watchlist",
        (decisions(e12), e12.included, e12.reason, e12.tags),
        (
            [("RYO_SCAN", False, "below_rank_cutoff"), ("EVENT", False, "trigger_not_qualified"), ("WATCHLIST", True, None)],
            True,
            None,
            (mod.Route.WATCHLIST,),
        ),
    )
    # excluded everywhere: the first route's reason
    out = build(mod, scan=tuple(mod.ScanRow(r, f"S{r:02d}") for r in range(1, 13)), triggers=(trigger(mod, "t1", "S12", primary=False),))
    c.eq(
        "excluded everywhere",
        (by_symbol(out, "S12").included, by_symbol(out, "S12").reason, by_symbol(out, "S12").tags),
        (False, mod.Exclusion.BELOW_RANK_CUTOFF, ()),
    )
    only_event = build(mod, scan=(), triggers=(trigger(mod, "t1", "S01", primary=False),), watchlist=("N01",))
    c.eq(
        "an entry takes the reason of its route",
        sorted((e.symbol, e.reason.value) for e in only_event.entries),
        [("N01", "no_oracle"), ("S01", "trigger_not_qualified")],
    )
    # the same unresolved symbol reached by two routes is one candidate, whatever its case
    un = build(mod, scan=(mod.ScanRow(1, "Q01"),), watchlist=("q01",))
    c.eq(
        "one unresolved candidate",
        (len(un.entries), un.entries[0].asset_id, un.entries[0].candidate_id),
        (1, None, natural_id("cd", {"cycle": "cy-1", "key": "unresolved:Q01"})),
    )
    c.eq("with both its routes", [r.route.value for r in un.entries[0].routes], ["RYO_SCAN", "WATCHLIST"])
    c.eq(
        "the union is what every arm gets",
        sorted(e.asset_id for e in mixed.union()),
        sorted({f"tk-s{r:02d}" for r in range(1, 11)} | {"tk-s12"}),
    )
    c.eq("entries are in id order", [e.candidate_id for e in mixed.entries] == sorted(e.candidate_id for e in mixed.entries), True)
    c.eq("the union is in id order too", [e.candidate_id for e in mixed.union()] == sorted(e.candidate_id for e in mixed.union()), True)
    return c.bad


def check_failed_routes(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    f = mod.Failed
    m = mod.build(
        inputs(mod, scan=f("RYO answered 503"), scan_capture=None, triggers=(trigger(mod, "t1", "S01"),), watchlist=("S02",)),
        rules(mod),
        resolver(mod),
    )
    c.eq("a failed scan route", m.route_status(), {"RYO_SCAN": "FAILED_EVIDENCE", "EVENT": "OK", "WATCHLIST": "OK"})
    c.eq("the other routes carry on", sorted(e.symbol for e in m.union()), ["S01", "S02"])
    body = m.body()
    c.eq(
        "the failure is in the body",
        (body["failures"], body["routes"], body["inputs"]),
        (
            {"RYO_SCAN": "RYO answered 503"},
            {"RYO_SCAN": "FAILED_EVIDENCE", "EVENT": "OK", "WATCHLIST": "OK"},
            [{"kind": "event_trigger_log", "capture_id": "cap-log", "sha256": "5" * 64}],
        ),
    )
    g = mod.build(inputs(mod, triggers=f("feed shape changed"), trigger_capture=None), rules(mod), resolver(mod))
    c.eq(
        "a failed event route",
        (g.route_status(), g.body()["failures"], len(g.union())),
        ({"RYO_SCAN": "OK", "EVENT": "FAILED_EVIDENCE", "WATCHLIST": "OK"}, {"EVENT": "feed shape changed"}, 10),
    )
    both = mod.build(
        inputs(mod, scan=f("a"), scan_capture=None, triggers=f("b"), trigger_capture=None, watchlist=("S01",)), rules(mod), resolver(mod)
    )
    c.eq(
        "both failed, the watchlist alone",
        (len(both.union()), both.body()["failures"], both.body()["inputs"]),
        (1, {"RYO_SCAN": "a", "EVENT": "b"}, []),
    )
    kept = mod.build(inputs(mod, scan=f("a")), rules(mod), resolver(mod))
    c.eq(
        "a capture kept for a failed route is still listed",
        [i["kind"] for i in kept.body()["inputs"]],
        ["ryo_scan_response", "event_trigger_log"],
    )
    return c.bad


def check_body_and_record(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    m = mod.build(
        inputs(
            mod,
            scan=(mod.ScanRow(1, "S01"), mod.ScanRow(12, "S02")),
            triggers=(trigger(mod, "t1", "S01", first_seen_at_ms=T - 1_000),),
            watchlist=("N03",),
        ),
        rules(mod),
        resolver(mod),
    )
    cid = {s: natural_id("cd", {"cycle": "cy-1", "key": f"tk-{s}"}) for s in ("s01", "s02", "n03")}
    want = {
        "cycle_id": "cy-1", "protocol_hash": H, "discovery_at": "2026-10-29T21:56:40.000Z", "sealed_at": "2026-10-29T21:56:45.000Z",
        "inputs": [{"kind": "ryo_scan_response", "capture_id": "cap-scan", "sha256": "4" * 64}, {"kind": "event_trigger_log", "capture_id": "cap-log", "sha256": "5" * 64}],
        "rules": {"ryo_scan": {"rule_version": "scan-1", "rule_hash": "1" * 64, "n": 10}, "event": {"rule_version": "event-1", "rule_hash": "2" * 64, "cap": 5}, "watchlist": {"config_hash": "3" * 64}},
        "routes": {"RYO_SCAN": "OK", "EVENT": "OK", "WATCHLIST": "OK"}, "failures": {},
        "entries": sorted([
            {"candidate_id": cid["s01"], "asset_id": "tk-s01", "symbol": "S01", "inclusion": "INCLUDED", "exclusion": None, "routes": [
                {"route": "RYO_SCAN", "decision": "INCLUDED", "reason": None, "rank": 1, "trigger_id": None, "first_seen_at": None, "dedupe_key": "ryo_scan:tk-s01"},
                {"route": "EVENT", "decision": "INCLUDED", "reason": None, "rank": None, "trigger_id": "t1", "first_seen_at": "2026-10-29T21:56:39.000Z", "dedupe_key": "LISTING_ANNOUNCED:tk-s01"}]},
            {"candidate_id": cid["s02"], "asset_id": "tk-s02", "symbol": "S02", "inclusion": "EXCLUDED", "exclusion": "below_rank_cutoff", "routes": [
                {"route": "RYO_SCAN", "decision": "EXCLUDED", "reason": "below_rank_cutoff", "rank": 12, "trigger_id": None, "first_seen_at": None, "dedupe_key": "ryo_scan:tk-s02"}]},
            {"candidate_id": cid["n03"], "asset_id": "tk-n03", "symbol": "N03", "inclusion": "EXCLUDED", "exclusion": "no_oracle", "routes": [
                {"route": "WATCHLIST", "decision": "EXCLUDED", "reason": "no_oracle", "rank": None, "trigger_id": None, "first_seen_at": None, "dedupe_key": "watchlist:tk-n03"}]},
        ], key=lambda e: e["candidate_id"]),
    }  # fmt: skip
    c.eq("a body", m.body(), want)
    c.eq("the hash is of the canonical body", m.sha256, hashlib.sha256(canonical.dumps(m.body())).hexdigest())
    record = mod.to_record(m, "urn:hoshitori:scheduler:test", T + 6_000)
    c.eq("the envelope", {k: record[k] for k in ("spec", "type", "type_version", "id", "source", "created_at", "refs", "privacy")},
         {"spec": "hoshitori.record/1", "type": "manifest", "type_version": 1, "id": natural_id("mf", {"cycle": "cy-1"}), "source": "urn:hoshitori:scheduler:test", "created_at": "2026-10-29T21:56:46.000Z", "refs": [], "privacy": "public"})  # fmt: skip
    c.eq("a record made at the sealing", mod.to_record(m, "urn:x", T + 5_000)["created_at"], "2026-10-29T21:56:45.000Z")
    c.refuses(
        "a negative creation time",
        lambda: mod.to_record(m, "urn:x", -1),
        "the creation time is a whole number of milliseconds up to the end of the year 9999",
    )
    c.refuses(
        "a record made before the sealing",
        lambda: mod.to_record(m, "urn:x", T + 4_999),
        "a manifest is recorded at or after the time it was sealed",
    )
    c.refuses(
        "a source that is not a urn",
        lambda: mod.to_record(m, "scheduler", T + 6_000),
        "the record does not satisfy the envelope: source must be an ASCII urn",
    )
    rebuilt = mod.build(m.inputs, m.rules, resolver(mod))
    c.eq("a replay matches", mod.matches(record, rebuilt), True)
    c.eq("replaying again gives the same hash", rebuilt.sha256, m.sha256)
    forged = {**record, "body": {**record["body"], "entries": record["body"]["entries"][:-1]}}
    c.eq("a manifest with an entry removed does not match", mod.matches(forged, rebuilt), False)
    other = mod.build(inputs(mod, scan=(mod.ScanRow(1, "S09"),)), m.rules, resolver(mod))
    c.eq("a manifest of other inputs does not match", mod.matches(record, other), False)
    c.eq("a record of another type does not match", mod.matches({**record, "type": "forecast"}, rebuilt), False)
    c.eq("a record with no body does not match", mod.matches({"type": "manifest"}, rebuilt), False)
    c.eq(
        "the cycle id is part of the candidate id",
        by_symbol(mod.build(inputs(mod, cycle_id="cy-2"), rules(mod), resolver(mod)), "S01") is not None,
        True,
    )
    c.eq(
        "another cycle, another candidate id",
        mod.build(inputs(mod, cycle_id="cy-2"), rules(mod), resolver(mod)).entries[0].candidate_id != build(mod).entries[0].candidate_id,
        True,
    )
    return c.bad


def check_validation(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    R = lambda **o: mod.build(inputs(mod, **o), rules(mod), resolver(mod))  # noqa: E731
    W = lambda **o: mod.build(inputs(mod), rules(mod, **o), resolver(mod))  # noqa: E731
    cases: list[tuple[str, Callable[[], Any]]] = [
        ("an empty cycle id", lambda: R(cycle_id="")),
        ("a cycle id of 201 characters", lambda: R(cycle_id="c" * 201)),
        ("a cycle id that is not canonical text", lambda: R(cycle_id="é")),
        ("a protocol hash in capitals", lambda: R(protocol_hash="A" * 64)),
        ("a short protocol hash", lambda: R(protocol_hash="a" * 63)),
        ("a negative discovery time", lambda: R(discovery_at_ms=-1)),
        ("a boolean sealing time", lambda: R(sealed_at_ms=True)),
        ("a sealing time past the year 9999", lambda: R(sealed_at_ms=253_402_300_800_000)),
        ("sealed before the discovery", lambda: R(sealed_at_ms=T - 1)),
        ("an empty scan rule version", lambda: W(scan_rule_version="")),
        ("a scan rule hash that is short", lambda: W(scan_rule_hash="1")),
        ("a scan cutoff of zero", lambda: W(scan_n=0)),
        ("a scan cutoff of 1001", lambda: W(scan_n=1001)),
        ("a boolean scan cutoff", lambda: W(scan_n=True)),
        ("an empty event rule version", lambda: W(event_rule_version="")),
        ("an event rule hash that is short", lambda: W(event_rule_hash="2")),
        ("a negative event cap", lambda: W(event_cap=-1)),
        ("an event cap of 1001", lambda: W(event_cap=1001)),
        ("a watchlist hash that is short", lambda: W(watchlist_config_hash="3")),
        ("a scan capture with an empty id", lambda: R(scan_capture=mod.Capture("", "4" * 64))),
        ("a scan capture with a short hash", lambda: R(scan_capture=mod.Capture("cap", "4"))),
        ("a scan that was read without its capture", lambda: R(scan_capture=None)),
        ("a trigger log that was read without its capture", lambda: R(trigger_capture=None)),
        ("a trigger capture with a short hash", lambda: R(trigger_capture=mod.Capture("cap", "5"))),
        ("a failure with no reason", lambda: R(scan=mod.Failed(""), scan_capture=None)),
        ("a trigger failure with no reason", lambda: R(triggers=mod.Failed(""), trigger_capture=None)),
        ("1001 scan rows", lambda: R(scan=tuple(mod.ScanRow(r, f"S{r}") for r in range(1, 1002)))),
        ("two rows with one rank", lambda: R(scan=(mod.ScanRow(1, "S01"), mod.ScanRow(1, "S02")))),
        ("a rank of zero", lambda: R(scan=(mod.ScanRow(0, "S01"),))),
        ("a rank that is a boolean", lambda: R(scan=(mod.ScanRow(True, "S01"),))),
        ("a rank that is text", lambda: R(scan=(mod.ScanRow("1", "S01"),))),
        ("an empty scan symbol", lambda: R(scan=(mod.ScanRow(1, ""),))),
        ("1001 triggers", lambda: R(triggers=tuple(trigger(mod, f"t{n}", "S01") for n in range(1001)))),
        ("two triggers with one id", lambda: R(triggers=(trigger(mod, "t", "S01"), trigger(mod, "t", "S02")))),
        ("a trigger with an empty id", lambda: R(triggers=(trigger(mod, "", "S01"),))),
        ("a trigger with an empty rule", lambda: R(triggers=(trigger(mod, "t", "S01", rule=""),))),
        ("a trigger with an empty rule version", lambda: R(triggers=(trigger(mod, "t", "S01", rule_version=""),))),
        ("a trigger with an empty symbol", lambda: R(triggers=(trigger(mod, "t", ""),))),
        ("a trigger seen at a negative time", lambda: R(triggers=(trigger(mod, "t", "S01", first_seen_at_ms=-1),))),
        ("a trigger with an impact that is not finite", lambda: R(triggers=(trigger(mod, "t", "S01", impact=D("NaN")),))),
        ("a trigger with an impact that is infinite", lambda: R(triggers=(trigger(mod, "t", "S01", impact=D("Infinity")),))),
        ("a trigger with an impact that is a float", lambda: R(triggers=(trigger(mod, "t", "S01", impact=1.5),))),
        ("a trigger whose primary flag is a number", lambda: R(triggers=(trigger(mod, "t", "S01", primary=1),))),
        ("1001 watchlist symbols", lambda: R(watchlist=tuple(f"S{n}" for n in range(1001)))),
        ("an empty watchlist symbol", lambda: R(watchlist=("",))),
    ]
    messages = {
        "an empty cycle id": "the cycle id is a non-empty string of at most 200 characters",
        "a cycle id of 201 characters": "the cycle id is a non-empty string of at most 200 characters",
        "a cycle id that is not canonical text": "the cycle id is not valid canonical text: string is not NFC",
        "a protocol hash in capitals": "the protocol hash is 64 lower-case hexadecimal characters",
        "a short protocol hash": "the protocol hash is 64 lower-case hexadecimal characters",
        "a negative discovery time": "discovery_at is a whole number of milliseconds up to the end of the year 9999",
        "a boolean sealing time": "sealed_at is a whole number of milliseconds up to the end of the year 9999",
        "a sealing time past the year 9999": "sealed_at is a whole number of milliseconds up to the end of the year 9999",
        "sealed before the discovery": "the manifest is sealed no earlier than the discovery collection",
        "an empty scan rule version": "the scan rule version is a non-empty string of at most 200 characters",
        "a scan rule hash that is short": "the scan rule hash is 64 lower-case hexadecimal characters",
        "a scan cutoff of zero": "the scan cutoff is a whole number from 1 to 1000",
        "a scan cutoff of 1001": "the scan cutoff is a whole number from 1 to 1000",
        "a boolean scan cutoff": "the scan cutoff is a whole number from 1 to 1000",
        "an empty event rule version": "the event rule version is a non-empty string of at most 200 characters",
        "an event rule hash that is short": "the event rule hash is 64 lower-case hexadecimal characters",
        "a negative event cap": "the event cap is a whole number from 0 to 1000",
        "an event cap of 1001": "the event cap is a whole number from 0 to 1000",
        "a watchlist hash that is short": "the watchlist configuration hash is 64 lower-case hexadecimal characters",
        "a scan capture with an empty id": "the scan capture id is a non-empty string of at most 200 characters",
        "a scan capture with a short hash": "the scan capture hash is 64 lower-case hexadecimal characters",
        "a scan that was read without its capture": "a scan that was read comes with the capture it was read from",
        "a trigger log that was read without its capture": "a trigger log that was read comes with the capture it was read from",
        "a trigger capture with a short hash": "the trigger log capture hash is 64 lower-case hexadecimal characters",
        "a failure with no reason": "the scan failure reason is a non-empty string of at most 200 characters",
        "a trigger failure with no reason": "the trigger log failure reason is a non-empty string of at most 200 characters",
        "1001 scan rows": "a scan has at most 1000 rows",
        "two rows with one rank": "the rows of a scan have distinct ranks from 1",
        "a rank of zero": "the rows of a scan have distinct ranks from 1",
        "a rank that is a boolean": "the rows of a scan have distinct ranks from 1",
        "a rank that is text": "the rows of a scan have distinct ranks from 1",
        "an empty scan symbol": "a scan symbol is a non-empty string of at most 200 characters",
        "1001 triggers": "a trigger log has at most 1000 triggers",
        "two triggers with one id": "trigger ids are distinct",
        "a trigger with an empty id": "a trigger id is a non-empty string of at most 200 characters",
        "a trigger with an empty rule": "a trigger rule is a non-empty string of at most 200 characters",
        "a trigger with an empty rule version": "a rule version is a non-empty string of at most 200 characters",
        "a trigger with an empty symbol": "a trigger symbol is a non-empty string of at most 200 characters",
        "a trigger seen at a negative time": "first_seen_at is a whole number of milliseconds up to the end of the year 9999",
        "a trigger with an impact that is not finite": "a trigger's impact is a finite decimal",
        "a trigger with an impact that is infinite": "a trigger's impact is a finite decimal",
        "a trigger with an impact that is a float": "a trigger's impact is a finite decimal",
        "a trigger whose primary flag is a number": "a trigger is primary or it is not",
        "1001 watchlist symbols": "a watchlist has at most 1000 symbols",
        "an empty watchlist symbol": "a watchlist symbol is a non-empty string of at most 200 characters",
    }
    for label, call in cases:
        c.refuses(label, call, messages[label])
    c.eq("1000 watchlist symbols", len(R(scan=(), watchlist=tuple(f"S{n}" for n in range(1000))).entries), 1000)
    c.eq("a cycle id of 200 characters", len(R(cycle_id="c" * 200).inputs.cycle_id), 200)
    c.refuses(
        "a hash with one wrong character",
        lambda: R(protocol_hash="a" * 63 + "g"),
        "the protocol hash is 64 lower-case hexadecimal characters",
    )
    c.refuses("a hash with a tilde", lambda: R(protocol_hash="a" * 63 + "~"), "the protocol hash is 64 lower-case hexadecimal characters")
    c.eq("the epoch", len(R(discovery_at_ms=0, sealed_at_ms=0).entries), 12)
    c.eq("a cutoff of 1000 and a cap of 1000", (W(scan_n=1000).rules.scan_n, W(event_cap=1000).rules.event_cap), (1000, 1000))
    c.eq("a cap of zero is allowed", W(event_cap=0).rules.event_cap, 0)
    c.eq("1000 scan rows", len(R(scan=tuple(mod.ScanRow(r, f"S{r}") for r in range(1, 1001))).entries), 1000)
    c.eq("sealed at the discovery", len(R(sealed_at_ms=T).entries), 12)
    c.eq("a failed scan needs no capture", len(R(scan=mod.Failed("x"), scan_capture=None).entries), 0)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_scan_route,
        check_event_route,
        check_watchlist_and_merging,
        check_failed_routes,
        check_body_and_record,
        check_validation,
    ):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
