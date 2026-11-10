"""Checks of the frozen snapshot (``hoshitori.snapshot``), runnable against a mutant."""

from __future__ import annotations

import copy
import hashlib
import types
from collections.abc import Callable
from typing import Any

from hoshitori import canonical, evidence, lineage, manifest
from hoshitori.ids import natural_id
from tests.evidence_checks import R, derived, observation

S = R + 10_000
MH = "9" * 64
F = evidence.FamilyKind
BINANCE = evidence.Family(F.EXCHANGE, "binance")
SRC = "urn:hoshitori:scheduler:test"


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
        except self.mod.SnapshotError as exc:
            if says is not None and says != str(exc):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def digest_of(item: Any) -> str:
    return evidence.record_digest(item.to_record("urn:x", item.known_at_ms))


class World:
    """Two candidates (BTC, ETH), the items a collector might bring back for them, and one item for the whole cycle."""

    def __init__(self) -> None:
        self.store = lineage.EvidenceStore()
        self.ev = evidence
        self.c1 = ("cd-btc", "tk-btc")
        self.c2 = ("cd-eth", "tk-eth")
        self.btc_ryo = self.add(self.ryo("btc", "tk-btc"))
        self.btc_int = self.add(
            self.ryo(
                "btc",
                "tk-btc",
                kind=evidence.Kind.INTERPRETATION,
                path="interpretation",
                fields=(("verdict", "bullish"),),
                unknown_fields=(),
            )
        )
        self.btc_bars = self.add(self.bars("btc", "tk-btc"))
        self.eth_bars = self.add(self.bars("eth", "tk-eth"))
        self.market = self.add(self.bars("market", None, source="fear-greed"))

    def ryo(self, name: str, asset: str | None, **over: Any) -> Any:
        values: dict[str, Any] = {
            "capture_id": f"cap-ryo-{name}",
            "asset_refs": () if asset is None else ((asset, f"venue/{name}", "RESOLVED"),),
        }
        values.update(over)
        return observation(evidence, **values)

    def bars(self, name: str, asset: str | None, **over: Any) -> Any:
        values: dict[str, Any] = {
            "adapter_version": "bars-1",
            "path": "series",
            "source": "binance-spot",
            "origin": BINANCE,
            "tool": None,
            "request": (("instrument", name.upper()),),
            "fields": (("last_close", "79000"),),
            "unknown_fields": (),
            "status": None,
            "data_mode": None,
            "availability": (),
            "warnings": (),
            "capture_id": f"cap-bars-{name}",
            "event_time_ms": S - 30_000,
            "received_at_ms": R,
            "known_at_ms": R,
            "asset_refs": () if asset is None else ((asset, f"venue/{name}", "RESOLVED"),),
        }
        values.update(over)
        return observation(evidence, **values)

    def add(self, item: Any) -> str:
        self.store.add(item, digest_of(item))
        return str(item.evidence_id)

    def derive(self, name: str, parents: list[str], asset: str | None = "tk-btc", **over: Any) -> str:
        pairs = tuple(sorted((p, self.store.digest(p)) for p in parents))
        values: dict[str, Any] = {
            "path": name,
            "derived_from": pairs,
            "fields": ((name, "1"),),
            "transform": (f"t-{name}", "1"),
            "asset_refs": () if asset is None else ((asset, "venue/btc", "RESOLVED"),),
        }
        values.update(over)
        return self.add(derived(evidence, **values))

    def candidates(self, mod: types.ModuleType) -> list[Any]:
        return [mod.Candidate(*self.c1), mod.Candidate(*self.c2)]

    def freeze(self, mod: types.ModuleType, **over: Any) -> Any:
        args: dict[str, Any] = {
            "cycle_id": "cy-1",
            "manifest_hash": MH,
            "snapshot_at_ms": S,
            "candidates": self.candidates(mod),
            "shared": [self.market],
            "by_candidate": {"cd-btc": [self.btc_ryo, self.btc_int, self.btc_bars], "cd-eth": [self.eth_bars]},
            "required": ["binance-spot"],
        }
        args.update(over)
        return mod.freeze(self.store, **args)


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "record",
        (mod.TYPE, mod.TYPE_VERSION, mod.MAX_INSTANT_MS, mod.MAX_LIMIT_MS),
        ("snapshot", 1, 253_402_300_799_999, 7 * 24 * 3600 * 1000),
    )
    c.eq("default freshness", dict(mod.DEFAULT_FRESHNESS), {F.EXCHANGE: 60_000, F.RYO: 300_000, F.NEWS: 900_000})
    c.eq("accesses", [a.value for a in mod.Access], ["INDEPENDENT", "EVERYTHING"])
    c.eq("reasons", [r.value for r in mod.Reject], ["STALE", "PARENT_NOT_ADMITTED"])
    c.eq(
        "the closed reasons of an unread source",
        mod.UNAVAILABLE_REASONS,
        (
            "NO_ASSET",
            "NO_ADAPTER",
            "NO_SERVER_TIME",
            "BAD_REQUEST",
            "CALL_FAILED",
            "UNREADABLE",
            "TIMED_OUT",
            "NO_PERPETUAL",
            "PERPETUAL_LIST_UNUSABLE",
        ),
    )
    for cls in (mod.Candidate, mod.Rejection, mod.Slot, mod.Snapshot):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    return c.bad


def check_freeze(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World()
    snap = w.freeze(mod)
    d = w.store.digest
    c.eq("the cycle", (snap.cycle_id, snap.manifest_hash, snap.snapshot_at_ms), ("cy-1", MH, S))
    c.eq("the whole-cycle items", snap.shared, (w.market,))
    c.eq("two slots in candidate order", [(s.candidate_id, s.asset_id) for s in snap.slots], [("cd-btc", "tk-btc"), ("cd-eth", "tk-eth")])
    c.eq("the btc items, by id", snap.slots[0].item_ids, tuple(sorted([w.btc_ryo, w.btc_int, w.btc_bars])))
    c.eq("the eth items", snap.slots[1].item_ids, (w.eth_bars,))
    c.eq("nothing is missing", [s.missing for s in snap.slots], [(), ()])
    c.eq("nothing is rejected", ([s.rejected for s in snap.slots], snap.shared_rejected), ([(), ()], ()))
    c.eq(
        "the snapshot holds every item once, by id",
        [i.evidence_id for i, _ in snap.items],
        sorted([w.market, w.btc_ryo, w.btc_int, w.btc_bars, w.eth_bars]),
    )
    c.eq(
        "with the digests of their records",
        {i.evidence_id: dg for i, dg in snap.items},
        {k: d(k) for k in (w.market, w.btc_ryo, w.btc_int, w.btc_bars, w.eth_bars)},
    )
    c.eq("the freshness limits are kept", snap.freshness, (("EXCHANGE", 60_000), ("NEWS", 900_000), ("RYO", 300_000)))
    c.eq("and the required sources", snap.required, ("binance-spot",))
    ref = lambda i: {"evidence_id": i, "sha256": d(i)}  # noqa: E731
    want = {
        "cycle_id": "cy-1",
        "manifest_hash": MH,
        "snapshot_at": "2026-10-29T21:56:50.000Z",
        "freshness": {"EXCHANGE": 60_000, "NEWS": 900_000, "RYO": 300_000},
        "required": ["binance-spot"],
        "shared": [ref(w.market)],
        "shared_rejected": [],
        "candidates": [
            {
                "candidate_id": "cd-btc",
                "asset_id": "tk-btc",
                "items": [ref(i) for i in sorted([w.btc_ryo, w.btc_int, w.btc_bars])],
                "missing": [],
                "rejected": [],
            },
            {"candidate_id": "cd-eth", "asset_id": "tk-eth", "items": [ref(w.eth_bars)], "missing": [], "rejected": []},
        ],
    }
    c.eq("the body", snap.body(), want)
    c.eq("the hash is of the canonical body", snap.sha256, hashlib.sha256(canonical.dumps(want)).hexdigest())
    # the order of the lists the collector hands over does not matter
    again = w.freeze(
        mod,
        by_candidate={"cd-eth": [w.eth_bars], "cd-btc": [w.btc_bars, w.btc_int, w.btc_ryo]},
        candidates=list(reversed(w.candidates(mod))),
    )
    c.eq("order of the inputs does not matter", again.sha256, snap.sha256)
    # every part of the inputs is in the hash
    for label, over in (
        ("the cycle", {"cycle_id": "cy-2"}),
        ("the manifest hash", {"manifest_hash": "8" * 64}),
        ("the time", {"snapshot_at_ms": S + 1}),
        ("the required sources", {"required": ["binance-spot", "ryo"]}),
        ("the freshness limits", {"freshness": {F.EXCHANGE: 61_000, F.RYO: 300_000, F.NEWS: 900_000}}),
        ("an item", {"by_candidate": {"cd-btc": [w.btc_ryo, w.btc_bars], "cd-eth": [w.eth_bars]}}),
    ):
        try:
            other = w.freeze(mod, **over)
        except mod.SnapshotError as exc:
            c.bad.append(f"{label}: could not be frozen differently: {exc}")
            continue
        c.eq(f"{label} is in the hash", other.sha256 != snap.sha256, True)
    return c.bad


def check_candidates_of(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    resolve = lambda s: manifest.Resolved("tk-" + s.lower(), True, True)  # noqa: E731
    scan = tuple(manifest.ScanRow(r, s) for r, s in enumerate(("BTC", "ETH", "SOL"), 1))
    inputs = manifest.Inputs("cy-1", "a" * 64, R, R + 5_000, scan, manifest.Capture("c", "4" * 64), (), manifest.Capture("l", "5" * 64), ())
    built = manifest.build(inputs, manifest.Rules("s", "1" * 64, 2, "e", "2" * 64, 5, "3" * 64), resolve)
    got = mod.candidates_of(built)
    c.eq("the eligible union only", sorted(x.asset_id for x in got), ["tk-btc", "tk-eth"])
    c.eq("with the manifest's candidate ids", [x.candidate_id for x in got], [e.candidate_id for e in built.union()])
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World()
    other = World()
    outside = w.add(w.bars("sol", "tk-sol"))
    future = w.add(w.bars("fut", "tk-btc", received_at_ms=S + 1, known_at_ms=S + 1))
    interp_child = w.derive("from-interp", [w.btc_int])
    leaky = w.add(w.ryo("leaky", "tk-btc", fields=(("asset.symbol", "BTC"), ("verdict", "bullish")), unknown_fields=()))
    unknown_path = w.add(w.ryo("mystery", "tk-btc", fields=(("mystery.field", "1"),), unknown_fields=()))
    ambiguous = w.add(w.bars("amb", "tk-btc", asset_refs=(("tk-btc", "venue/x", "AMBIGUOUS"),)))
    two_assets = w.add(w.bars("two", "tk-btc", asset_refs=(("tk-btc", "venue/a", "RESOLVED"), ("tk-eth", "venue/b", "RESOLVED"))))
    no_refs = w.add(w.bars("norefs", None))
    revised_old = w.add(w.bars("rev", "tk-btc"))
    old_item = w.store.get(revised_old)
    revision = w.bars(
        "rev",
        "tk-btc",
        capture_id="cap-bars-rev-2",
        received_at_ms=R + 2_000,
        known_at_ms=R + 2_000,
        supersedes=revised_old,
        fields=(("last_close", "79100"),),
    )
    w.add(revision)
    del old_item
    for_btc: Callable[..., Any] = lambda ids, **kw: w.freeze(mod, by_candidate={"cd-btc": ids}, **kw)  # noqa: E731
    cases: list[tuple[str, Callable[[], Any]]] = [
        ("an item listed twice in one list", lambda: for_btc([w.btc_bars, w.btc_bars])),
        ("an item listed for the cycle and a candidate", lambda: for_btc([w.market])),
        ("an item for two candidates", lambda: w.freeze(mod, by_candidate={"cd-btc": [w.btc_bars], "cd-eth": [w.btc_bars]})),
        ("an item the store does not hold", lambda: for_btc(["evnotthere"])),
        ("a candidate outside the manifest", lambda: w.freeze(mod, by_candidate={"cd-sol": [outside]})),
        ("an item known after the snapshot", lambda: for_btc([future])),
        ("an item with a newer revision", lambda: for_btc([revised_old])),
        ("an item about an asset that is not the candidate", lambda: for_btc([w.eth_bars])),
        ("an item about two candidates", lambda: for_btc([two_assets])),
        ("an item of an ambiguous mapping", lambda: for_btc([ambiguous])),
        ("an item with no asset for a candidate", lambda: for_btc([no_refs])),
        ("an item about an asset listed for the whole cycle", lambda: w.freeze(mod, shared=[w.btc_bars], by_candidate={})),
        ("a ryo observation that carries an interpretation field", lambda: for_btc([leaky])),
        ("a ryo observation with a field the table does not know", lambda: for_btc([unknown_path])),
        ("an item derived from an interpretation", lambda: for_btc([interp_child])),
    ]
    twice, unfit, brings = (
        "{} is listed twice",
        "{} is not about what it was listed for",
        "{} brings something other than an observation to the independent arms",
    )
    says = {
        "an item listed twice in one list": twice.format(w.btc_bars),
        "an item listed for the cycle and a candidate": twice.format(w.market),
        "an item for two candidates": twice.format(w.btc_bars),
        "an item the store does not hold": "the store does not hold evnotthere",
        "a candidate outside the manifest": "evidence was collected for a candidate outside the manifest",
        "an item known after the snapshot": f"{future} was known after the snapshot",
        "an item with a newer revision": f"{revised_old} has a newer revision known by the snapshot; list the revision",
        "an item about an asset that is not the candidate": unfit.format(w.eth_bars),
        "an item about two candidates": unfit.format(two_assets),
        "an item of an ambiguous mapping": unfit.format(ambiguous),
        "an item with no asset for a candidate": unfit.format(no_refs),
        "an item about an asset listed for the whole cycle": unfit.format(w.btc_bars),
        "a ryo observation that carries an interpretation field": brings.format(leaky),
        "a ryo observation with a field the table does not know": brings.format(unknown_path),
        "an item derived from an interpretation": brings.format(interp_child),
    }
    for label, call in cases:
        c.refuses(label, call, says[label])
    # a derived item from an item of another candidate
    child = w.derive("cross", [w.eth_bars], asset="tk-btc")
    c.refuses(
        "an item derived from another candidate's item",
        lambda: w.freeze(mod, by_candidate={"cd-btc": [child], "cd-eth": [w.eth_bars]}),
        f"{child} is derived from an item of another candidate",
    )
    # an item derived from an item that is not in the snapshot is refused as not admitted, not as an error
    # transitive taint: a derived item whose grandparent is an interpretation
    mid = w.derive("mid", [w.btc_int])
    grand = w.derive("grand", [mid])
    c.refuses("an item whose grandparent is an interpretation", lambda: for_btc([grand, mid, w.btc_int]), brings.format(grand))
    del other
    return c.bad


def check_staleness(mod: types.ModuleType) -> list[str]:
    """Age is judged when an item was received (contract section 16): from what the source says it describes to the receipt. Every item
    in the default world is received at R, ten seconds before the snapshot S; the hold is the cycle's ten minutes."""
    c = Case(mod)
    w = World()
    edge = w.add(w.bars("edge", "tk-btc", event_time_ms=R - 60_000))
    over = w.add(w.bars("over", "tk-btc", event_time_ms=R - 60_001))
    snap = w.freeze(mod, by_candidate={"cd-btc": [edge, over]}, required=[])
    c.eq("an age at receipt equal to the limit is admitted", edge in snap.slots[0].item_ids, True)
    c.eq("one millisecond over is rejected, with its reason", snap.slots[0].rejected, (mod.Rejection(over, mod.Reject.STALE),))
    c.eq("a rejected item is not held", [i.evidence_id for i, _ in snap.items if i.evidence_id == over], [])
    # the case the rule is for: a slow source's answer, fresh when it came, sealed later than its limit
    slow = w.add(w.ryo("slow", "tk-btc", event_time_ms=S - 290_000 - 20_000, received_at_ms=S - 290_000, known_at_ms=S - 290_000))
    snap = w.freeze(mod, by_candidate={"cd-btc": [slow]}, required=[])
    c.eq("a RYO answer 20 s old when it came and sealed 290 s later is admitted (310 s old at sealing)", snap.slots[0].item_ids, (slow,))
    # the hold: an item received before the cycle could have begun collecting is no item of this cycle
    held = w.add(w.bars("held", "tk-btc", event_time_ms=None, received_at_ms=S - 600_000, known_at_ms=S - 600_000))
    gone = w.add(w.bars("gone", "tk-btc", event_time_ms=None, received_at_ms=S - 600_001, known_at_ms=S - 600_001))
    snap = w.freeze(mod, by_candidate={"cd-btc": [held, gone]}, required=[])
    c.eq(
        "received ten minutes before the snapshot is kept, a millisecond earlier is not",
        (snap.slots[0].item_ids, [r.evidence_id for r in snap.slots[0].rejected]),
        ((held,), [gone]),
    )
    c.eq("the hold is the cycle's discovery-to-snapshot bound", mod.DISCOVERY_TO_SNAPSHOT_MS, 600_000)
    nolimit = w.add(w.bars("chain-old", "tk-btc", origin=evidence.Family(F.CHAIN, "eth"), source="eth-rpc", received_at_ms=S - 600_001))
    snap = w.freeze(mod, by_candidate={"cd-btc": [nolimit]}, required=[])
    c.eq("the hold applies to a family with no age limit too", [r.evidence_id for r in snap.slots[0].rejected], [nolimit])
    # an event time ahead of the receipt is the source's word and gives no credit: the age at receipt is never below nothing
    ahead = w.add(w.bars("ahead", "tk-btc", event_time_ms=S + 3_600_000))
    snap = w.freeze(mod, by_candidate={"cd-btc": [ahead]}, required=[], freshness={F.EXCHANGE: 1})
    c.eq("an event time in the future counts as an age of nothing, not less", snap.slots[0].item_ids, (ahead,))
    # an item from the whole cycle that is stale
    old_market = w.add(w.bars("oldmarket", None, source="fear-greed", event_time_ms=R - 61_000))
    snap = w.freeze(mod, shared=[old_market], required=[])
    c.eq(
        "a stale item for the cycle is listed apart",
        (snap.shared, snap.shared_rejected),
        ((), (mod.Rejection(old_market, mod.Reject.STALE),)),
    )
    # families: RYO 300 s, news 900 s, others without a limit
    ryo_ok = w.add(w.ryo("ok", "tk-btc", event_time_ms=R - 300_000))
    ryo_old = w.add(w.ryo("old", "tk-btc", event_time_ms=R - 300_001))
    news_ok = w.add(w.bars("newsok", "tk-btc", origin=evidence.Family(F.NEWS, "wire"), source="wire", event_time_ms=R - 900_000))
    news_old = w.add(w.bars("newsold", "tk-btc", origin=evidence.Family(F.NEWS, "wire"), source="wire", event_time_ms=R - 900_001))
    chain = w.add(
        w.bars("chain", "tk-btc", origin=evidence.Family(F.CHAIN, "eth"), source="eth-rpc", event_time_ms=S - 10**9 if S > 10**9 else 0)
    )
    snap = w.freeze(mod, by_candidate={"cd-btc": [ryo_ok, ryo_old, news_ok, news_old, chain]}, required=[])
    c.eq(
        "ryo and news limits at their edges",
        (sorted(snap.slots[0].item_ids), sorted(r.evidence_id for r in snap.slots[0].rejected)),
        (sorted([ryo_ok, news_ok, chain]), sorted([ryo_old, news_old])),
    )
    # a custom limit: the default bar is 20 s old when received
    snap = w.freeze(mod, by_candidate={"cd-btc": [w.btc_bars]}, freshness={F.EXCHANGE: 19_999}, required=[])
    c.eq("a tighter limit rejects a bar 20 s old at receipt", snap.slots[0].rejected, (mod.Rejection(w.btc_bars, mod.Reject.STALE),))
    snap = w.freeze(mod, by_candidate={"cd-btc": [w.btc_bars]}, freshness={F.EXCHANGE: 20_000}, required=[])
    c.eq("and the same limit as the age admits it", snap.slots[0].rejected, ())
    snap = w.freeze(mod, by_candidate={"cd-btc": [w.btc_bars]}, freshness={}, required=[])
    c.eq("no limits at all", snap.slots[0].rejected, ())
    # derived items: no limit of their own, they follow their parents
    d1 = w.derive("atr", [edge])
    d2 = w.derive("atr-over", [over])
    d3 = w.derive("second-order", [d2])
    snap = w.freeze(mod, by_candidate={"cd-btc": [edge, over, d1, d2, d3]}, required=[])
    c.eq(
        "a derived item of a stale parent is not admitted, and neither is its child",
        sorted(r.evidence_id for r in snap.slots[0].rejected),
        sorted([over, d2, d3]),
    )
    c.eq(
        "with the reasons",
        {r.evidence_id: r.reason for r in snap.slots[0].rejected},
        {over: mod.Reject.STALE, d2: mod.Reject.PARENT_NOT_ADMITTED, d3: mod.Reject.PARENT_NOT_ADMITTED},
    )
    c.eq("the derived item of a good parent is admitted", d1 in snap.slots[0].item_ids, True)
    snap = w.freeze(mod, by_candidate={"cd-btc": [d1]}, required=[])
    c.eq(
        "a parent that was not listed at all is not admitted", snap.slots[0].rejected, (mod.Rejection(d1, mod.Reject.PARENT_NOT_ADMITTED),)
    )
    return c.bad


def check_boundaries(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World()
    exact = w.add(w.bars("exact", "tk-btc", received_at_ms=S, known_at_ms=S))
    c.eq(
        "an item known at the very instant of the snapshot is in it",
        w.freeze(mod, by_candidate={"cd-btc": [exact]}, required=[]).slots[0].item_ids,
        (exact,),
    )
    snap0 = w.freeze(mod, snapshot_at_ms=0, shared=[], by_candidate={}, required=[])
    c.eq("a record at the epoch", mod.to_record(snap0, SRC, 0)["created_at"], "1970-01-01T00:00:00.000Z")
    # a chain of eight derived items on a stale reading: every one is rejected, whatever the order of their ids
    stale = w.add(w.bars("chain-root", "tk-btc", event_time_ms=R - 61_000))
    chain = [stale]
    for n in range(8):
        chain.append(w.derive(f"step-{n}", [chain[-1]]))
    snap = w.freeze(mod, by_candidate={"cd-btc": chain}, required=[])
    c.eq(
        "a deep chain on a stale reading is wholly rejected",
        (snap.slots[0].item_ids, sorted(r.evidence_id for r in snap.slots[0].rejected)),
        ((), sorted(chain)),
    )
    c.eq(
        "the root is stale and the rest are not admitted",
        sorted(r.reason.value for r in snap.slots[0].rejected),
        ["PARENT_NOT_ADMITTED"] * 8 + ["STALE"],
    )
    body = snap.body()["candidates"][0]["rejected"]  # type: ignore[index]
    c.eq(
        "the body lists the rejections with their reasons",
        sorted(body, key=lambda r: r["evidence_id"]),
        sorted(({"evidence_id": r.evidence_id, "reason": r.reason.value} for r in snap.slots[0].rejected), key=lambda r: r["evidence_id"]),
    )
    old_market = w.add(w.bars("oldm", None, source="fear-greed", event_time_ms=R - 61_000))
    shared_body = w.freeze(mod, shared=[old_market], by_candidate={}, required=[]).body()["shared_rejected"]
    c.eq("and so does the cycle-wide list", shared_body, [{"evidence_id": old_market, "reason": "STALE"}])
    # the first problem found follows the sorted candidate order, whatever order the collector built its mapping in
    future = w.add(w.bars("fut", "tk-btc", received_at_ms=S + 1, known_at_ms=S + 1))
    unfit = w.add(w.bars("unfit", "tk-eth"))
    both = {"cd-eth": [w.btc_bars], "cd-btc": [future]}
    c.refuses(
        "two candidates with problems: the first by id is reported",
        lambda: w.freeze(mod, by_candidate=both, shared=[]),
        f"{future} was known after the snapshot",
    )
    del unfit
    return c.bad


def check_missing(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World()
    snap = w.freeze(mod, required=["binance-spot", "ryo", "news"])
    c.eq("what each candidate lacks", [s.missing for s in snap.slots], [("news",), ("ryo", "news")])
    c.eq("in the order required", w.freeze(mod, required=["news", "ryo"]).slots[1].missing, ("news", "ryo"))
    # an interpretation does not satisfy a source
    only_int = w.freeze(mod, by_candidate={"cd-btc": [w.btc_int], "cd-eth": []}, required=["ryo"])
    c.eq("an interpretation is not the observation", [s.missing for s in only_int.slots], [("ryo",), ("ryo",)])
    # a stale source is missing
    stale = w.add(w.bars("stale", "tk-eth", event_time_ms=R - 70_000))
    snap = w.freeze(mod, by_candidate={"cd-eth": [stale]}, required=["binance-spot"])
    c.eq("a source whose only item is stale is missing", snap.slots[1].missing, ("binance-spot",))
    # the item of the whole cycle does not stand in for a candidate's source
    snap = w.freeze(mod, shared=[w.market], by_candidate={}, required=["fear-greed"])
    c.eq("a cycle-wide item does not satisfy a candidate", [s.missing for s in snap.slots], [("fear-greed",), ("fear-greed",)])
    # a derived item counts as its own source
    d = w.derive("atr", [w.btc_bars])
    snap = w.freeze(mod, by_candidate={"cd-btc": [w.btc_bars, d]}, required=["hoshitori"])
    c.eq("a derived item of source hoshitori", snap.slots[0].missing, ())
    c.eq("nothing required, nothing missing", [s.missing for s in w.freeze(mod, by_candidate={}, required=[]).slots], [(), ()])
    no_candidates = w.freeze(mod, candidates=[], by_candidate={})
    c.eq("a cycle with no candidates still has its cycle-wide items", (no_candidates.slots, no_candidates.shared), ((), (w.market,)))
    many = World()
    cands = [mod.Candidate(f"cd-{n:02d}", f"tk-{n:02d}") for n in range(14)]
    ids = {c_.candidate_id: [many.add(many.bars(f"n{n}", c_.asset_id))] for n, c_ in enumerate(cands)}
    snap = many.freeze(mod, candidates=cands, shared=[], by_candidate=ids, required=["binance-spot"])
    c.eq("fourteen candidates in id order", [s.candidate_id for s in snap.slots], sorted(ids))
    return c.bad


def check_reader(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World()
    snap = w.freeze(mod, required=["binance-spot", "ryo"])
    indep, everything = mod.Reader(snap, mod.Access.INDEPENDENT), mod.Reader(snap, mod.Access.EVERYTHING)
    c.eq("candidates", indep.candidate_ids(), ("cd-btc", "cd-eth"))
    c.eq("independent: observations, with the cycle-wide item", indep.ids("cd-btc"), tuple(sorted([w.market, w.btc_ryo, w.btc_bars])))
    c.eq("everything: also the interpretation", everything.ids("cd-btc"), tuple(sorted([w.market, w.btc_ryo, w.btc_int, w.btc_bars])))
    c.eq("another candidate sees only its own and the cycle-wide", indep.ids("cd-eth"), tuple(sorted([w.market, w.eth_bars])))
    c.eq("the items themselves", [i.evidence_id for i in indep.items("cd-eth")], sorted([w.market, w.eth_bars]))
    c.eq("get returns an item it may read", indep.get(w.btc_bars, "cd-btc").evidence_id, w.btc_bars)
    c.eq("get returns the cycle-wide item", indep.get(w.market, "cd-eth").evidence_id, w.market)
    c.refuses(
        "an independent reader asked for the interpretation",
        lambda: indep.get(w.btc_int, "cd-btc"),
        f"{w.btc_int} is not in what this reader may read for cd-btc",
    )
    c.refuses(
        "a reader asked for another candidate's item",
        lambda: indep.get(w.btc_bars, "cd-eth"),
        f"{w.btc_bars} is not in what this reader may read for cd-eth",
    )
    c.refuses(
        "a reader asked for an item outside the snapshot",
        lambda: indep.get(w.eth_bars + "x", "cd-eth"),
        f"{w.eth_bars}x is not in what this reader may read for cd-eth",
    )
    c.refuses(
        "a reader asked about a candidate it does not have", lambda: indep.ids("cd-sol"), "cd-sol is not a candidate of this snapshot"
    )
    late = w.add(w.bars("late", "tk-btc"))
    c.refuses(
        "a reader asked for a thing in the store but not the snapshot",
        lambda: indep.get(late, "cd-btc"),
        f"{late} is not in what this reader may read for cd-btc",
    )
    c.eq(
        "missing and complete",
        (indep.missing("cd-btc"), indep.complete("cd-btc"), indep.missing("cd-eth"), indep.complete("cd-eth")),
        ((), True, ("ryo",), False),
    )
    ok = mod.Reader(w.freeze(mod, required=["binance-spot"]), mod.Access.INDEPENDENT)
    c.eq("complete when nothing is missing", (ok.complete("cd-btc"), ok.complete("cd-eth")), (True, True))
    # hashes
    want = lineage.observation_set_hash((i, w.store.digest(i)) for i in indep.ids("cd-btc"))
    c.eq("the set hash is of what the reader may read", indep.set_hash("cd-btc"), want)
    c.eq("another access gives another hash", everything.set_hash("cd-btc") != indep.set_hash("cd-btc"), True)
    c.eq("another candidate gives another hash", indep.set_hash("cd-eth") != indep.set_hash("cd-btc"), True)
    used = [w.btc_bars, w.market]
    c.eq(
        "the hash of what a decision used",
        indep.set_hash("cd-btc", used),
        lineage.observation_set_hash((i, w.store.digest(i)) for i in used),
    )
    c.eq("a subset in any order is the same hash", indep.set_hash("cd-btc", list(reversed(used))), indep.set_hash("cd-btc", used))
    c.eq("an empty set of used items", indep.set_hash("cd-btc", []), lineage.observation_set_hash([]))
    c.refuses(
        "a decision that used what its reader could not read",
        lambda: indep.set_hash("cd-btc", [w.btc_int]),
        "a decision cannot have used what its reader could not read",
    )
    c.refuses("an access that is not one", lambda: mod.Reader(snap, "INDEPENDENT"), "an access is one of the listed kinds")
    return c.bad


def check_frozen(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World()
    snap = w.freeze(mod)
    before = (snap.sha256, mod.Reader(snap, mod.Access.INDEPENDENT).set_hash("cd-btc"))
    # a revision of a listed item, a new reading and a new interpretation are written after the freeze
    revision = w.bars(
        "btc",
        "tk-btc",
        capture_id="cap-bars-btc-2",
        received_at_ms=S + 1_000,
        known_at_ms=S + 1_000,
        supersedes=w.btc_bars,
        fields=(("last_close", "1"),),
    )
    w.add(revision)
    w.add(w.bars("btc-new", "tk-btc", received_at_ms=S + 2_000, known_at_ms=S + 2_000))
    after = (snap.sha256, mod.Reader(snap, mod.Access.INDEPENDENT).set_hash("cd-btc"))
    c.eq("nothing written later changes the snapshot", after, before)
    c.eq("and it still holds the old reading", [i.field("last_close") for i, _ in snap.items if i.evidence_id == w.btc_bars], ["79000"])
    # a revision known by the snapshot time must be listed instead of the original
    early = w.bars("e", "tk-btc", received_at_ms=R - 100, known_at_ms=R - 100)
    early_id = w.add(early)
    rev = w.bars(
        "e",
        "tk-btc",
        capture_id="cap-bars-e-2",
        received_at_ms=S - 1,
        known_at_ms=S - 1,
        supersedes=early_id,
        fields=(("last_close", "2"),),
    )
    rev_id = w.add(rev)
    snap = w.freeze(mod, by_candidate={"cd-btc": [rev_id]}, required=[])
    c.eq("the revision can be listed", snap.slots[0].item_ids, (rev_id,))
    snap2 = w.freeze(mod, snapshot_at_ms=S - 2, by_candidate={"cd-btc": [early_id]}, required=[], shared=[])
    c.eq("before the revision was known the original is the reading", snap2.slots[0].item_ids, (early_id,))
    return c.bad


def check_record(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World()
    snap = w.freeze(mod)
    rec = mod.to_record(snap, SRC, S + 1_000)
    c.eq("the envelope", {k: rec[k] for k in ("spec", "type", "type_version", "id", "source", "created_at", "refs", "privacy")}, {"spec": "hoshitori.record/1", "type": "snapshot", "type_version": 1, "id": natural_id("sn", {"cycle": "cy-1"}), "source": SRC, "created_at": "2026-10-29T21:56:51.000Z", "refs": [], "privacy": "public"})  # fmt: skip
    c.eq("the body carries the hash", rec["body"], {**snap.body(), "sha256": snap.sha256})
    c.eq("a record made at the freezing", mod.to_record(snap, SRC, S)["created_at"], "2026-10-29T21:56:50.000Z")
    c.refuses(
        "a record made before the freezing",
        lambda: mod.to_record(snap, SRC, S - 1),
        "a snapshot is recorded at or after the time it was frozen",
    )
    c.refuses(
        "a negative creation time",
        lambda: mod.to_record(snap, SRC, -1),
        "the creation time is a whole number of milliseconds up to the end of the year 9999",
    )
    c.refuses(
        "a boolean creation time",
        lambda: mod.to_record(snap, SRC, True),
        "the creation time is a whole number of milliseconds up to the end of the year 9999",
    )
    c.refuses(
        "a creation time past the year 9999",
        lambda: mod.to_record(snap, SRC, mod.MAX_INSTANT_MS + 1),
        "the creation time is a whole number of milliseconds up to the end of the year 9999",
    )
    c.refuses(
        "a source that is not a urn",
        lambda: mod.to_record(snap, "scheduler", S),
        "the record does not satisfy the envelope: source must be an ASCII urn",
    )
    again = w.freeze(mod)
    c.eq("a replay from the same items matches", mod.matches(rec, again), True)
    other = w.freeze(mod, by_candidate={"cd-btc": [w.btc_bars], "cd-eth": [w.eth_bars]})
    c.eq("a snapshot of other items does not", mod.matches(rec, other), False)
    c.eq("a record of another type does not", mod.matches({**rec, "type": "manifest"}, again), False)
    c.eq("a record without a body does not", mod.matches({"type": "snapshot"}, again), False)
    forged = {**rec, "body": {**rec["body"], "sha256": "0" * 64}}
    c.eq("a record with a changed hash does not", mod.matches(forged, again), False)
    stripped = {**rec, "body": {k: v for k, v in rec["body"].items() if k != "sha256"}}
    c.eq("a record without the hash does not", mod.matches(stripped, again), False)
    return c.bad


def check_validation(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World()
    cases: list[tuple[str, dict[str, Any]]] = [
        ("an empty cycle id", {"cycle_id": ""}),
        ("a cycle id of 201 characters", {"cycle_id": "c" * 201}),
        ("a cycle id that is not canonical text", {"cycle_id": "é"}),
        ("a short manifest hash", {"manifest_hash": "9" * 63}),
        ("a manifest hash in capitals", {"manifest_hash": "A" * 64}),
        ("a manifest hash with one wrong character", {"manifest_hash": "9" * 63 + "g"}),
        ("a manifest hash with a tilde", {"manifest_hash": "9" * 63 + "~"}),
        ("a negative time", {"snapshot_at_ms": -1}),
        ("a boolean time", {"snapshot_at_ms": True}),
        ("a time past the year 9999", {"snapshot_at_ms": 253_402_300_800_000}),
        ("two candidates with one id", {"candidates": [w.candidates(mod)[0], w.candidates(mod)[0]]}),
        ("two candidates with one asset", {"candidates": [mod.Candidate("cd-a", "tk-btc"), mod.Candidate("cd-b", "tk-btc")]}),
        ("a candidate with no id", {"candidates": [mod.Candidate("", "tk-btc")]}),
        ("a candidate with no asset", {"candidates": [mod.Candidate("cd-a", "")]}),
        ("a required source that is empty", {"required": [""]}),
        ("a required source listed twice", {"required": ["a", "a"]}),
        ("a freshness limit of zero", {"freshness": {F.EXCHANGE: 0}}),
        ("a freshness limit beyond seven days", {"freshness": {F.EXCHANGE: 7 * 24 * 3600 * 1000 + 1}}),
        ("a boolean freshness limit", {"freshness": {F.EXCHANGE: True}}),
        ("a freshness limit for something that is not a family kind", {"freshness": {"EXCHANGE": 5}}),
    ]
    texts = {
        "the cycle id": "the cycle id is a non-empty string of at most 200 characters",
        "hash": "the manifest hash is 64 lower-case hexadecimal characters",
        "time": "snapshot_at is a whole number of milliseconds up to the end of the year 9999",
        "distinct": "candidates are distinct, and so are their assets",
        "limit": "a freshness limit is a whole number of milliseconds from 1 to 7 days, for a family kind",
    }
    says = {
        "an empty cycle id": texts["the cycle id"],
        "a cycle id of 201 characters": texts["the cycle id"],
        "a cycle id that is not canonical text": "the cycle id is not valid canonical text: string is not NFC",
        "a short manifest hash": texts["hash"],
        "a manifest hash in capitals": texts["hash"],
        "a manifest hash with one wrong character": texts["hash"],
        "a manifest hash with a tilde": texts["hash"],
        "a negative time": texts["time"],
        "a boolean time": texts["time"],
        "a time past the year 9999": texts["time"],
        "two candidates with one id": texts["distinct"],
        "two candidates with one asset": texts["distinct"],
        "a candidate with no id": "a candidate id is a non-empty string of at most 200 characters",
        "a candidate with no asset": "a candidate's asset id is a non-empty string of at most 200 characters",
        "a required source that is empty": "a required source is a non-empty string of at most 200 characters",
        "a required source listed twice": "required sources are distinct",
        "a freshness limit of zero": texts["limit"],
        "a freshness limit beyond seven days": texts["limit"],
        "a boolean freshness limit": texts["limit"],
        "a freshness limit for something that is not a family kind": texts["limit"],
    }
    for label, over in cases:
        c.refuses(label, lambda o=over: w.freeze(mod, **o), says[label])
    c.eq("a freshness limit of one millisecond", len(w.freeze(mod, freshness={F.EXCHANGE: 1}, required=[]).slots), 2)
    c.eq("a freshness limit of seven days", len(w.freeze(mod, freshness={F.EXCHANGE: 7 * 24 * 3600 * 1000}).slots), 2)
    c.eq("a cycle id of 200 characters", w.freeze(mod, cycle_id="c" * 200).cycle_id, "c" * 200)
    c.eq("the epoch", w.freeze(mod, snapshot_at_ms=0, shared=[], by_candidate={}, required=[]).snapshot_at_ms, 0)
    c.eq(
        "the last millisecond of 9999",
        w.freeze(mod, snapshot_at_ms=mod.MAX_INSTANT_MS, shared=[], by_candidate={}, required=[]).snapshot_at_ms,
        mod.MAX_INSTANT_MS,
    )
    return c.bad


def _records(snap: Any) -> dict[str, dict[str, Any]]:
    """The evidence records of a snapshot's items, as a ledger would hold them (the ledger adds the time it committed them)."""
    return {
        item.evidence_id: {**item.to_record("urn:x", item.known_at_ms), "committed_at": "2026-10-29T21:57:00.000Z"}
        for item, _ in snap.items
    }


def check_unavailable(mod: types.ModuleType) -> list[str]:
    """What the collector could not read stays in the snapshot, so that a failed read does not look like a read never asked for."""
    c = Case(mod)
    w = World()
    plain = w.freeze(mod)
    gone = [mod.Unavailable("binance-funding", "CALL_FAILED", None), mod.Unavailable("binance-spot", "TIMED_OUT", 3600)]
    snap = w.freeze(mod, unavailable={"cd-eth": gone})
    c.eq(
        "the slot holds what was not read, in a fixed order",
        snap.slots[1].unavailable,
        tuple(sorted(gone, key=lambda u: (u.source, u.reason))),
    )
    c.eq("a slot with nothing unread holds nothing", snap.slots[0].unavailable, ())
    rows = [mod.Unavailable("s", "CALL_FAILED", 1), mod.Unavailable("s", "CALL_FAILED", None), mod.Unavailable("s", "CALL_FAILED", 3600)]
    c.eq(
        "a read with no window comes before the same read with one, and a window of one second is a window",
        [u.interval_s for u in w.freeze(mod, unavailable={"cd-eth": rows}).slots[1].unavailable],
        [None, 1, 3600],
    )
    body = snap.body()["candidates"]  # type: ignore[index]
    c.eq(
        "the body names the source, the closed reason and the window",
        body[1]["unavailable"],  # type: ignore[index]
        [
            {"source": "binance-funding", "reason": "CALL_FAILED", "interval_s": None},
            {"source": "binance-spot", "reason": "TIMED_OUT", "interval_s": 3600},
        ],
    )
    c.eq("a slot with nothing unread has no such member, so every earlier hash stands", "unavailable" in body[0], False)  # type: ignore[operator]
    c.eq("the failure is in the hash", snap.sha256 != plain.sha256, True)
    c.eq("and a snapshot with nothing unread is the one it was", w.freeze(mod, unavailable={}).sha256, plain.sha256)
    c.eq(
        "the order the collector hands them over does not matter",
        w.freeze(mod, unavailable={"cd-eth": list(reversed(gone))}).sha256,
        snap.sha256,
    )
    for label, bad in (
        ("a candidate outside the manifest", {"cd-zzz": gone}),
        ("a reason that is not a closed word", {"cd-eth": [mod.Unavailable("binance-spot", "it broke", None)]}),
        ("an empty source", {"cd-eth": [mod.Unavailable("", "CALL_FAILED", None)]}),
        ("a window that is not a number of seconds", {"cd-eth": [mod.Unavailable("binance-spot", "CALL_FAILED", 0)]}),
        ("the same failure twice", {"cd-eth": [gone[0], gone[0]]}),
    ):
        try:
            w.freeze(mod, unavailable=bad)
            c.bad.append(f"{label}: was accepted")
        except mod.SnapshotError:
            pass
    again = mod.rebuild(mod.to_record(snap, SRC, S + 1_000), _records(snap))
    c.eq("a snapshot with unread sources is rebuilt from its record", (again, again.sha256), (snap, snap.sha256))
    c.eq("and the check that a record is this snapshot's still holds", mod.matches(mod.to_record(snap, SRC, S + 1_000), again), True)
    # a record that was edited, or written by something else, is refused on the way back
    good = mod.to_record(snap, SRC, S + 1_000)

    def edited(change: Any) -> dict[str, Any]:
        record = copy.deepcopy(good)
        change(record["body"]["candidates"][1]["unavailable"])
        body = {k: v for k, v in record["body"].items() if k != "sha256"}
        record["body"]["sha256"] = hashlib.sha256(canonical.dumps(body)).hexdigest()  # a forger recomputes the hash
        return record

    for label, change in (
        ("a reason that is not a closed word", lambda u: u[0].__setitem__("reason", "it broke")),
        ("an entry out of order", lambda u: u.reverse()),
        ("the same entry twice", lambda u: u.append(dict(u[0]))),
        ("an extra member", lambda u: u[0].__setitem__("note", "x")),
        ("a window that is true", lambda u: u[1].__setitem__("interval_s", True)),
        ("a window that is a string", lambda u: u[1].__setitem__("interval_s", "3600")),
        ("a window of zero", lambda u: u[1].__setitem__("interval_s", 0)),
        ("an empty source", lambda u: u[0].__setitem__("source", "")),
    ):
        try:
            mod.rebuild(edited(change), _records(snap))
            c.bad.append(f"a record with {label} was rebuilt")
        except mod.SnapshotError:
            pass
    for label, change, words in (
        ("an extra member", lambda u: u[0].__setitem__("note", "x"), "an unread source has the members"),
        ("something that is not a list", None, "a candidate's unread sources is a list"),
    ):
        record = edited(change) if change is not None else edited(lambda u: None)
        if change is None:
            record["body"]["candidates"][1]["unavailable"] = "x"
            body = {k: v for k, v in record["body"].items() if k != "sha256"}
            record["body"]["sha256"] = hashlib.sha256(canonical.dumps(body)).hexdigest()
        try:
            mod.rebuild(record, _records(snap))
            c.bad.append(f"{label}: was rebuilt")
        except mod.SnapshotError as exc:
            c.eq(f"{label}: is refused in words", words in str(exc), True)
    return c.bad


def check_rebuild(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World()
    snap = w.freeze(mod)
    rec = mod.to_record(snap, SRC, S + 1_000)
    records = _records(snap)
    again = mod.rebuild(rec, records)
    c.eq("a snapshot is rebuilt from its record and its evidence", again, snap)
    c.eq("with the same hash", again.sha256, snap.sha256)
    c.eq(
        "and a reader of it reads what the first reader read",
        [mod.Reader(again, mod.Access.INDEPENDENT).ids(cid) for cid in ("cd-btc", "cd-eth")],
        [mod.Reader(snap, mod.Access.INDEPENDENT).ids(cid) for cid in ("cd-btc", "cd-eth")],
    )
    c.eq(
        "the record the ledger holds is read (it has the time of its commit)",
        mod.rebuild({**rec, "committed_at": "2026-10-29T21:57:00.000Z"}, records),
        snap,
    )
    # a snapshot with rejected and missing things
    edge = w.add(w.bars("edge", "tk-btc", event_time_ms=R - 60_000))
    over = w.add(w.bars("over", "tk-btc", event_time_ms=R - 60_001))
    old_market = w.add(w.bars("oldmarket", None, source="fear-greed", event_time_ms=R - 61_000))
    hard = w.freeze(
        mod,
        by_candidate={"cd-btc": [edge, over], "cd-eth": [w.eth_bars]},
        shared=[old_market, w.market],
        required=["fear-greed", "binance-spot"],
    )
    c.eq(
        "rejected items and missing sources are rebuilt",
        (len(hard.slots[0].rejected), len(hard.shared_rejected), hard.slots[1].missing),
        (1, 1, ("fear-greed",)),
    )
    hard_rec = mod.to_record(hard, SRC, S)
    c.eq("and the snapshot is the same", mod.rebuild(hard_rec, _records(hard)), hard)
    lacking = w.freeze(mod, by_candidate={"cd-btc": [w.btc_bars]}, required=["binance-spot", "kraken"])
    c.eq(
        "a candidate with a missing source and one with nothing",
        [s.missing for s in lacking.slots],
        [("kraken",), ("binance-spot", "kraken")],
    )
    c.eq("is rebuilt whole", mod.rebuild(mod.to_record(lacking, SRC, S), _records(lacking)), lacking)
    nothing = w.freeze(mod, shared=[], by_candidate={}, required=[])
    c.eq("a snapshot of nothing", mod.rebuild(mod.to_record(nothing, SRC, S), {}), nothing)
    # refusals
    plain = "not a snapshot record of a version this reader knows"
    c.refuses("another type", lambda: mod.rebuild({**rec, "type": "manifest"}, records), plain)
    c.refuses("another version", lambda: mod.rebuild({**rec, "type_version": 2}, records), plain)
    c.refuses("no type at all", lambda: mod.rebuild({}, records), plain)
    members = sorted(rec["body"])
    c.refuses("a body that is not an object", lambda: mod.rebuild({**rec, "body": []}, records), f"a snapshot has the members {members}")
    c.refuses(
        "a body with a member short",
        lambda: mod.rebuild({**rec, "body": {k: v for k, v in rec["body"].items() if k != "required"}}, records),
        f"a snapshot has the members {members}",
    )
    c.refuses(
        "a body with a further member",
        lambda: mod.rebuild({**rec, "body": {**rec["body"], "extra": 1}}, records),
        f"a snapshot has the members {members}",
    )

    def changed(**body: Any) -> dict[str, Any]:
        return {**rec, "body": {**rec["body"], **body}}

    c.refuses(
        "a time that is not one", lambda: mod.rebuild(changed(snapshot_at="yesterday"), records), "the snapshot time is an RFC 3339 instant"
    )
    c.refuses("a time that is a number", lambda: mod.rebuild(changed(snapshot_at=5), records), "the snapshot time is an RFC 3339 instant")
    c.refuses("shared items that are not a list", lambda: mod.rebuild(changed(shared="x"), records), "the shared items is a list")
    c.refuses("candidates that are not a list", lambda: mod.rebuild(changed(candidates={}), records), "the candidates is a list")
    c.refuses("required sources that are not a list", lambda: mod.rebuild(changed(required={}), records), "the required sources is a list")
    c.refuses(
        "shared rejections that are not a list", lambda: mod.rebuild(changed(shared_rejected=1), records), "the shared rejections is a list"
    )
    c.refuses(
        "freshness limits that are not an object", lambda: mod.rebuild(changed(freshness=[]), records), "the freshness limits are an object"
    )
    c.refuses(
        "a shared item with a member short",
        lambda: mod.rebuild(changed(shared=[{"evidence_id": w.market}]), records),
        "an item of the shared items has the members ['evidence_id', 'sha256']",
    )
    first = rec["body"]["candidates"][0]
    c.refuses(
        "a candidate with a member short",
        lambda: mod.rebuild(changed(candidates=[{k: v for k, v in first.items() if k != "missing"}]), records),
        "a candidate has the members ['asset_id', 'candidate_id', 'items', 'missing', 'rejected']",
    )
    c.refuses(
        "a candidate's items that are not a list",
        lambda: mod.rebuild(changed(candidates=[{**first, "items": 3}]), records),
        "a candidate's items is a list",
    )
    c.refuses(
        "a candidate's missing sources that are not a list",
        lambda: mod.rebuild(changed(candidates=[{**first, "missing": 3}]), records),
        "a candidate's missing sources is a list",
    )
    reason = "a rejection in the shared rejections has a reason from ['STALE', 'PARENT_NOT_ADMITTED']"
    c.refuses(
        "a rejection with a reason that is not one",
        lambda: mod.rebuild(changed(shared_rejected=[{"evidence_id": "x", "reason": "OLD"}]), records),
        reason,
    )
    c.refuses(
        "a rejection with a member short",
        lambda: mod.rebuild(changed(shared_rejected=[{"evidence_id": "x"}]), records),
        "a rejection in the shared rejections has the members ['evidence_id', 'reason']",
    )
    c.refuses(
        "a candidate's rejection with a reason that is not one",
        lambda: mod.rebuild(changed(candidates=[{**first, "rejected": [{"evidence_id": "x", "reason": "OLD"}]}]), records),
        "a rejection in a candidate's rejections has a reason from ['STALE', 'PARENT_NOT_ADMITTED']",
    )
    limits = rec["body"]["freshness"]
    c.eq(
        "the freshness limits are read in order whatever order the record lists them in",
        mod.rebuild(changed(freshness=dict(reversed(list(limits.items())))), records),
        snap,
    )
    c.eq("and there are several of them", len(limits) > 1, True)
    some = next(iter(records))
    c.refuses(
        "an evidence record that was not given",
        lambda: mod.rebuild(rec, {k: v for k, v in records.items() if k != some}),
        f"the evidence record {some} was not given",
    )
    c.refuses(
        "an evidence record that is not the one named",
        lambda: mod.rebuild(rec, {**records, some: {**records[some], "created_at": "2030-01-01T00:00:00.000Z"}}),
        f"{some} is not the record the snapshot names",
    )
    junk: dict[str, Any] = {"type": "evidence"}
    forged = changed(shared=[{"evidence_id": w.market, "sha256": evidence.record_digest(junk)}])
    c.refuses(
        "a record that has the digest named and cannot be read",
        lambda: mod.rebuild(forged, {**records, w.market: junk}),
        f"{w.market} cannot be read: the envelope: missing envelope members: ['body', 'created_at', 'id', 'source', 'spec', 'type_version']",
    )
    c.refuses(
        "a hash that was edited",
        lambda: mod.rebuild(changed(sha256="0" * 64), records),
        "the snapshot rebuilt from these items is not the recorded one",
    )
    c.refuses(
        "a time that was edited",
        lambda: mod.rebuild(changed(snapshot_at="2030-01-01T00:00:00.000Z"), records),
        "the snapshot rebuilt from these items is not the recorded one",
    )
    c.refuses(
        "an order of required sources that was edited",
        lambda: mod.rebuild(changed(required=["x", "binance-spot"]), records),
        "the snapshot rebuilt from these items is not the recorded one",
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_freeze,
        check_candidates_of,
        check_refusals,
        check_staleness,
        check_boundaries,
        check_missing,
        check_reader,
        check_frozen,
        check_record,
        check_validation,
        check_unavailable,
        check_rebuild,
    ):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
