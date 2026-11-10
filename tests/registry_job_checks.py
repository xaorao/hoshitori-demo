"""Checks of the registry refresh (``hoshitori.registry_job``), runnable against a mutant. The gateway is a scripted runner that writes real
captures; the adapters and the registry are the real ones."""

from __future__ import annotations

import json
import tempfile
import types
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from hoshitori.capture import CaptureStore
from hoshitori.clock import ManualClock
from hoshitori.collect import Environment
from hoshitori.gateway import Call, Outcome
from hoshitori.governor import Priority
from hoshitori.ids import natural_id
from hoshitori.lineage import EvidenceStore
from hoshitori.registry import Registry
from hoshitori.venue_binance import BinanceSpot, BinanceUsdm
from tests.collect_checks import NOW, FakeRunner, Reply
from tests.profiles_binance import futures_symbol, spot_symbol


def spot_body(*names: str) -> bytes:
    return json.dumps({"symbols": [spot_symbol(f"{n}USDT", n, "USDT") for n in names]}).encode()


def perp_body(*names: str) -> bytes:
    return json.dumps({"symbols": [futures_symbol(f"{n}USDT", n, "USDT", "USDT") for n in names]}).encode()


class World:
    def __init__(self, root: Path, script: Callable[[Call], Reply] | None = None) -> None:
        self.clock = ManualClock(NOW)
        self.captures = CaptureStore(root / "captures")
        self.registry = Registry()
        self.spot, self.usdm = BinanceSpot(), BinanceUsdm()

        def default(call: Call) -> Reply:
            return Reply(spot_body("BTC", "ETH") if call.provider == self.spot.provider else perp_body("BTC", "ETH"))

        self.runner = FakeRunner(self.captures, self.clock, script or default)

    def env(self) -> Environment:
        return Environment(
            self.runner, self.captures, {"binance-spot": self.spot, "binance-usdm": self.usdm}, self.registry, EvidenceStore(), self.clock
        )


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str) -> None:
        try:
            call()
        except self.mod.RegistryJobError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def specs(mod: types.ModuleType) -> list[Any]:
    return [mod.ListSpec("binance-spot", "spot"), mod.ListSpec("binance-usdm", "perpetual", "perpetual")]


def outcome_of(record: dict[str, Any]) -> tuple[str, str, str | None]:
    header = next(e for e in record["events"] if e["t"] == "snapshot")
    return header["venue"], header["outcome"], header["reason"]


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("lists at most", mod.MAX_LISTS, 32)
    for cls in (mod.ListSpec, mod.ListFailure, mod.Refresh):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    return c.bad


def check_happy(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root)
    got = mod.refresh(w.env(), specs(mod), caller="league", wait_s=7, earmark=4)
    c.eq("no failure", got.failures, ())
    c.eq(
        "a record per list, in order",
        [outcome_of(r) for r in got.records],
        [("binance-spot", "accepted", None), ("binance-usdm", "accepted", None)],
    )
    c.eq("the registry holds them", w.registry.records(), list(got.records))
    c.eq("two calls: the instrument list of each venue", [(x.provider, x.operation, dict(x.args)) for x in w.runner.calls], [
        (w.spot.provider, "instruments", {}), (w.usdm.provider, "instruments", {"kind": "perpetual"})])  # fmt: skip
    c.eq(
        "at the highest priority, for the caller, with the window and the deadline",
        {(x.priority, x.caller, x.window, x.deadline) for x in w.runner.calls},
        {(Priority.P0, "league", 4, NOW + timedelta(seconds=7))},
    )
    c.eq(
        "the capture id of each record is the capture read",
        [next(e for e in r["events"] if e["t"] == "snapshot")["capture_id"] for r in got.records],
        [f"cap-{n:032x}" for n in (1, 2)],
    )
    c.eq("the instruments are in the registry", len(w.registry.records()[0]["events"]), 3)
    again = mod.refresh(w.env(), specs(mod)[:1])
    c.eq(
        "a second refresh is a second record, an accepted list with no change",
        [outcome_of(r) for r in again.records],
        [("binance-spot", "accepted", None)],
    )
    c.eq(
        "with the defaults",
        [(x.caller, x.window, x.deadline) for x in w.runner.calls[2:]],
        [("registry", None, NOW + timedelta(seconds=30))],
    )
    return c.bad


def check_failures(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)

    def script(call: Call) -> Reply:
        if call.provider == BinanceUsdm().provider:
            return Reply(outcome=Outcome.FAILED, reason="UPSTREAM")
        return Reply(spot_body("BTC"))

    w = World(root / "a", script)
    got = mod.refresh(w.env(), specs(mod))
    c.eq("the other list is admitted", [outcome_of(r) for r in got.records], [("binance-spot", "accepted", None)])
    c.eq(
        "the failing one is listed with the gateway's word",
        [(f.venue, f.scope, f.reason, f.detail) for f in got.failures],
        [("binance-usdm", "perpetual", "CALL_FAILED", "FAILED: UPSTREAM")],
    )
    c.eq("and nothing is known of its scope", [s for s in w.registry.status_counts().values() if s], [1])
    hang = World(root / "b", lambda call: Reply(hang=True))
    got = mod.refresh(hang.env(), specs(mod)[:1], wait_s=0.05)
    c.eq(
        "a list that does not come in time",
        [(f.reason, f.detail) for f in got.failures],
        [("TIMED_OUT", "no result before the wait ended")],
    )
    junk = World(root / "c", lambda call: Reply(b"not json"))
    got = mod.refresh(junk.env(), specs(mod)[:1])
    c.eq("an unreadable list", [(f.venue, f.reason) for f in got.failures], [("binance-spot", "UNREADABLE")])
    c.eq("and no record", got.records, ())
    empty = World(root / "d", lambda call: Reply(b'{"symbols": []}'))
    got = mod.refresh(empty.env(), specs(mod)[:1])
    c.eq(
        "a list the registry rejects is a record that says so, not a failure",
        ([outcome_of(r) for r in got.records], got.failures),
        ([("binance-spot", "rejected", "EMPTY")], ()),
    )
    bad = World(root / "e")
    got = mod.refresh(bad.env(), [mod.ListSpec("binance-spot", "spot", "future")])
    c.eq(
        "a kind the venue does not list is not asked",
        (got.records, [(f.reason, f.venue) for f in got.failures], bad.runner.calls),
        ((), [("BAD_REQUEST", "binance-spot")], []),
    )
    return c.bad


def check_validation(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root)
    one = "a refresh reads from 1 to 32 lists, each venue and scope once"
    c.refuses("no lists", lambda: mod.refresh(w.env(), []), one)
    c.refuses("the same list twice", lambda: mod.refresh(w.env(), [mod.ListSpec("binance-spot", "spot")] * 2), one)
    many = [mod.ListSpec("binance-spot", f"s{n}") for n in range(33)]
    c.refuses("too many lists", lambda: mod.refresh(w.env(), many), one)
    c.refuses(
        "an unknown venue", lambda: mod.refresh(w.env(), [mod.ListSpec("nowhere", "spot")]), "nowhere is not a venue of this environment"
    )
    c.refuses(
        "an unknown scope",
        lambda: mod.refresh(w.env(), [mod.ListSpec("binance-spot", "moon")]),
        "a scope is one of ('all', 'spot', 'perpetual', 'future')",
    )
    for wait in (0, -1, True, "1"):
        c.refuses(
            f"a wait of {wait!r}",
            lambda wait=wait: mod.refresh(w.env(), specs(mod), wait_s=wait),
            "the wait is a positive number of seconds",
        )
    c.eq("nothing was asked", w.runner.calls, [])
    two = [mod.ListSpec("binance-spot", "spot"), mod.ListSpec("binance-spot", "all")]
    got = mod.refresh(w.env(), two)
    c.eq(
        "one venue in two scopes is two lists, the second judged by the registry",
        ([outcome_of(r) for r in got.records], got.failures, len(w.runner.calls)),
        ([("binance-spot", "accepted", None), ("binance-spot", "rejected", "SCOPE_OVERLAP")], (), 2),
    )
    mod_limit = mod.MAX_LISTS
    mod.MAX_LISTS = 2  # the limit is applied where the lists are counted, so a small one shows its edge with the few lists that exist
    try:
        spare = World(root / "limit")
        three = [
            mod.ListSpec("binance-spot", "spot"),
            mod.ListSpec("binance-spot", "all"),
            mod.ListSpec("binance-usdm", "perpetual", "perpetual"),
        ]
        c.refuses(
            "one more than the limit",
            lambda: mod.refresh(spare.env(), three),
            "a refresh reads from 1 to 2 lists, each venue and scope once",
        )
        c.eq("exactly the limit is fine", len(mod.refresh(spare.env(), three[:2]).records), 2)
    finally:
        mod.MAX_LISTS = mod_limit
    return c.bad


def check_ledger(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root)
    got = mod.refresh(w.env(), specs(mod))
    c.eq("the type", (mod.TYPE, mod.TYPE_VERSION), ("registry", 1))
    wrapped = [mod.ledger_record(r, "urn:hoshitori:league:test") for r in got.records]
    c.eq(
        "one envelope for each, public, made when the registry learned it",
        [(r["type"], r["type_version"], r["privacy"], r["created_at"] == r["body"]["known_at"], r["refs"]) for r in wrapped],
        [("registry", 1, "public", True, [])] * 2,
    )
    c.eq("with ids of their moment, all different", len({r["id"] for r in wrapped}), 2)
    c.eq("an id is the natural id of the moment", wrapped[0]["id"], natural_id("rg", {"known_at": got.records[0]["known_at"]}))
    c.eq("the body is the registry's record, untouched", [r["body"] for r in wrapped], list(got.records))
    c.refuses(
        "a record the envelope refuses",
        lambda: mod.ledger_record(got.records[0], "not a urn"),
        "the record does not satisfy the envelope: source must be an ASCII urn",
    )
    other = [{"type": "forecast", "type_version": 1, "body": {"x": 1}}, *wrapped, {"type": "registry", "type_version": 2, "body": {"y": 1}}]
    rebuilt = mod.load(other)
    c.eq("the registry is rebuilt from the ledger's records, whatever else they hold", rebuilt.records(), w.registry.records())
    c.eq("and answers as the original", rebuilt.status_counts(), w.registry.status_counts())
    c.eq("an empty ledger is an empty registry", mod.load([]).records(), [])
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_happy, check_failures, check_validation, check_ledger):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                found = check(mod) if check is check_constants else check(mod, Path(tmp))  # type: ignore[call-arg]
            bad += [f"{check.__name__}: {message}" for message in found]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
