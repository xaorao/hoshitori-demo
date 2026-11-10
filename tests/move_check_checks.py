"""Checks of the price-move check (``hoshitori.move_check``), runnable against a mutant."""

from __future__ import annotations

import copy
import types
from decimal import Decimal
from typing import Any

from hoshitori.claim_move import MoveAssertion
from tests.challenge_view_checks import fixture
from tests.rules_arm_checks import FEATURES, reader_with

D = Decimal


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Any, starts: str) -> None:
        try:
            call()
        except self.mod.MoveCheckError as exc:
            if not str(exc).startswith(starts):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected to start with {starts!r}")
            return
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def held(mod: types.ModuleType, **returns: str) -> dict[str, Any]:
    fields = dict(FEATURES)
    fields.update(returns)
    w, _reader, _everything, feats = reader_with(fields)
    return {"BTC": mod.Held("BTC", w.store.get(feats), feats, "cy1", 1_000)}


def claim(direction: str | None = "DOWN", size: str | None = "4", window: int | None = 24, asset: str | None = "BTC") -> Any:
    missing = tuple(n for n, v in (("asset", asset), ("direction", direction), ("window", window)) if v is None)
    return MoveAssertion(asset, direction, None if size is None else D(size), window, missing)


def check_judging(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    h = held(mod, ret_24h="-0.039000000000", ret_4h="0.010000000000", ret_1h="0.000000000000")
    r = mod.check_move(claim(), h)
    c.eq(
        "a move that agrees",
        (r.status, r.reason, r.evidence_id, r.cycle_id, r.snapshot_at_ms, r.moved_percent),
        ("SUPPORTED", "BTC moved -3.90% over the 24 hours; the claim says 4%", h["BTC"].evidence_id, "cy1", 1_000, D("-3.9")),
    )
    r = mod.check_move(claim(direction="UP"), h)
    c.eq("the other way", (r.status, r.reason), ("CONTRADICTED", "BTC moved -3.90% over the 24 hours, the other way"))
    r = mod.check_move(claim(size="1"), h)
    c.eq(
        "the right way and the wrong size", (r.status, r.reason), ("CONTRADICTED", "BTC moved -3.90% over the 24 hours; the claim says 1%")
    )
    r = mod.check_move(claim(size=None), h)
    c.eq("direction only", (r.status, r.reason), ("SUPPORTED", "BTC moved -3.90% over the 24 hours, in that direction"))
    c.eq(
        "the four-hour window reads the four-hour move", mod.check_move(claim(direction="UP", size="1", window=4), h).moved_percent, D("1")
    )
    c.eq(
        "the one-hour window reads the one-hour move", mod.check_move(claim(direction="UP", size=None, window=1), h).status, "CONTRADICTED"
    )
    c.eq(
        "a flat move is neither up nor down",
        mod.check_move(claim(direction="UP", size=None, window=1), h).reason,
        "BTC moved +0.00% over the 1 hours, the other way",
    )
    for stated, actual, status in (
        ("4", "-0.030000000000", "SUPPORTED"),
        ("4", "-0.029900000000", "CONTRADICTED"),
        ("4", "-0.050000000000", "SUPPORTED"),
        ("4", "-0.050100000000", "CONTRADICTED"),
        ("1", "-0.015000000000", "SUPPORTED"),
        ("1", "-0.015100000000", "CONTRADICTED"),
        ("1", "-0.005000000000", "SUPPORTED"),
        ("1", "-0.004900000000", "CONTRADICTED"),
    ):
        c.eq(f"stated {stated}, moved {actual}", mod.check_move(claim(size=stated), held(mod, ret_24h=actual)).status, status)
    up = held(mod, ret_4h="0.005000000000", ret_1h="-0.020000000000")
    r = mod.check_move(claim(direction="UP", size=None, window=4), up)
    c.eq("a small move up is up", (r.status, r.reason), ("SUPPORTED", "BTC moved +0.50% over the 4 hours, in that direction"))
    r = mod.check_move(claim(direction="UP", size="0.5", window=4), up)
    c.eq("and its size is read", r.status, "SUPPORTED")
    r = mod.check_move(claim(direction="DOWN", size=None, window=1), up)
    c.eq("a move down is down", (r.status, r.reason), ("SUPPORTED", "BTC moved -2.00% over the 1 hours, in that direction"))
    r = mod.check_move(claim(direction="UP", size=None, window=1), up)
    c.eq("and not up", r.status, "CONTRADICTED")
    flat = held(mod, ret_24h="0.000000000000")
    c.eq(
        "a flat move contradicts a claim of a fall",
        (mod.check_move(claim(size=None), flat).status, mod.check_move(claim(size=None), flat).reason),
        ("CONTRADICTED", "BTC moved +0.00% over the 24 hours, the other way"),
    )
    c.eq("and a claim of a rise", mod.check_move(claim(direction="UP", size=None), flat).status, "CONTRADICTED")
    unknown = {"BTC": h["BTC"]}
    fields = {k: v for k, v in FEATURES.items() if k != "ret_24h"}
    w, _r, _e, feats = reader_with(fields, unknown=("ret_24h",))
    unknown["BTC"] = mod.Held("BTC", w.store.get(feats), feats, "cy9", 5)
    r = mod.check_move(claim(), unknown)
    c.eq(
        "a move the evidence does not know",
        (r.status, r.reason, r.evidence_id, r.cycle_id, r.snapshot_at_ms, r.moved_percent),
        ("SOURCE_UNAVAILABLE", "the 24-hour move of BTC is unknown in the latest snapshot", feats, "cy9", 5, None),
    )
    r = mod.check_move(claim(asset="ETH"), h)
    c.eq(
        "an asset no snapshot holds",
        (r.status, r.reason, r.evidence_id),
        ("NOT_FOUND_IN_SOURCES_CHECKED", "no committed snapshot holds ETH", None),
    )
    r = mod.check_move(claim(window=72), h)
    c.eq("a window the league does not hold", (r.status, r.reason), ("NOT_CHECKABLE", "the league holds moves over 1, 4, 24 hours, not 72"))
    r = mod.check_move(claim(window=None), h)
    c.eq("an open window", (r.status, r.reason, r.cycle_id), ("UNRESOLVED", "the words leave open: window", None))
    r = mod.check_move(claim(direction=None, asset=None), h)
    c.eq("two parts open", (r.status, r.reason), ("UNRESOLVED", "the words leave open: asset, direction"))
    c.eq("the constants", (mod.WINDOWS, mod.TOLERANCE_POINTS, mod.TOLERANCE_SHARE), ((1, 4, 24), D("0.5"), D("0.25")))
    c.eq("frozen", [cls.__dataclass_params__.frozen for cls in (mod.Held, mod.MoveResult)], [True, True])
    return c.bad


def check_held_from(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    records, cycle, _protocol, _mappings = fixture()
    got = mod.held_from(records)
    symbols = sorted(
        e["symbol"]
        for r in records
        if r.get("type") == "manifest" and r["body"]["cycle_id"] == cycle
        for e in r["body"]["entries"]
        if e["inclusion"] == "INCLUDED"
    )
    c.eq("an asset for each candidate of the cycle", sorted(got), symbols)
    c.eq(
        "every one is of this cycle and reads its own features",
        {(h.cycle_id, h.item.path, h.symbol == s) for s, h in got.items()},
        {(cycle, "features", True)},
    )
    snap = next(r for r in records if r.get("type") == "snapshot" and r["body"]["cycle_id"] == cycle)
    c.eq("with the time the snapshot was taken", {h.snapshot_at_ms for h in got.values()}, {got[symbols[0]].snapshot_at_ms})
    c.eq("which is the snapshot's own", snap["body"]["snapshot_at"] is not None, True)
    first = got[symbols[0]]
    c.eq("and a checked move from it", mod.check_move(MoveAssertion(first.symbol, "UP", None, 24, ()), got).evidence_id, first.evidence_id)
    from tests.view_models_checks import ledger as two_cycles

    many, _protocol2, _now = two_cycles()
    snaps = [r for r in many if r.get("type") == "snapshot"]
    c.eq("a ledger of two cycles", len(snaps) >= 2, True)
    latest = max(snaps, key=lambda r: r["body"]["snapshot_at"])["body"]["cycle_id"]
    c.eq("the latest cycle's evidence is the one held", {h.cycle_id for h in mod.held_from(many).values()}, {latest})
    c.eq("no records, no assets", mod.held_from([]), {})
    c.refuses(
        "a snapshot without its manifest",
        lambda: mod.held_from([r for r in records if r.get("type") != "manifest"]),
        "the ledger holds a snapshot without its manifest",
    )
    broken = copy.deepcopy(records)
    victim = next(r for r in broken if r.get("type") == "evidence")
    victim["body"]["adapter_version"] = "tampered-1"
    c.refuses(
        "an evidence record that is not the one the snapshot names",
        lambda: mod.held_from(broken),
        f"the snapshot of {cycle} cannot be rebuilt: ",
    )
    # the latest snapshot wins, and an older one never replaces a newer one
    later = copy.deepcopy([r for r in records if r.get("type") in ("snapshot", "manifest", "evidence")])
    c.eq(
        "a second copy of the same cycle does not change what is held",
        {s: h.evidence_id for s, h in mod.held_from(records + later).items()},
        {s: h.evidence_id for s, h in got.items()},
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (check_judging, check_held_from)
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
