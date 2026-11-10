"""Checks of the cross-source price check (``hoshitori.crosscheck``), runnable against a mutant. Expected differences are computed with
fractions, a different path from the module's decimals."""

from __future__ import annotations

import types
from dataclasses import replace
from fractions import Fraction
from typing import Any

from hoshitori import evidence
from tests.snapshot_checks import World, digest_of


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def rounded(value: Fraction) -> str:
    scaled = round(value * 10**6)
    return f"{scaled // 10**6}.{scaled % 10**6:06d}"


def with_price(item: Any, price: str | None) -> Any:
    fields = tuple((k, v) for k, v in item.fields if k != "market.price_usd")
    return replace(item, fields=fields if price is None else tuple(sorted((*fields, ("market.price_usd", price)))))


def with_close(item: Any, close: str | None, **over: Any) -> Any:
    fields = tuple((k, v) for k, v in item.fields if k != "last_close")
    return replace(item, fields=fields if close is None else tuple(sorted((*fields, ("last_close", close)))), **over)


def pair(w: World) -> tuple[Any, str, Any, str]:
    ryo, bars = w.store.get(w.btc_ryo), w.store.get(w.btc_bars)
    return ryo, digest_of(ryo), bars, digest_of(bars)


def check_one_pair(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World()
    ryo, rd, bars, bd = pair(w)
    built = 1_793_311_020_000
    item = mod.price_check(with_price(ryo, "79012.5"), rd, bars, bd, built)
    gap = abs(Fraction("79012.5") - Fraction(79000)) / Fraction(79000)
    c.eq(
        "what is said",
        dict(item.fields),
        {
            "ryo_price_usd": "79012.5",
            "exchange_close": "79000",
            "exchange": "binance-spot",
            "difference": rounded(gap),
            "tolerance": "0.020000",
            "state": "AGREE",
        },
    )
    c.eq(
        "where it stands",
        (item.kind.value, item.source, item.origin.text, item.path, item.transform, item.known_at_ms, item.received_at_ms),
        ("DERIVED", "hoshitori.crosscheck", "DERIVED", "crosscheck/observation", ("crosscheck", "1"), built, built),
    )
    c.eq("on both parents, in order", item.derived_from, tuple(sorted(((ryo.evidence_id, rd), (bars.evidence_id, bd)))))
    c.eq("about the asset of the exchange series", item.asset_refs, bars.asset_refs)
    c.eq("as old as its older parent", item.event_time_ms, min(ryo.event_time_ms, bars.event_time_ms))
    c.eq(
        "live with live is live, anything else is mixed",
        (item.data_mode, mod.price_check(ryo, rd, replace(bars, data_mode="live"), bd, built).data_mode),
        ("mixed", "live"),
    )
    conflict = mod.price_check(with_price(ryo, "110600"), rd, bars, bd, built)
    c.eq("a price 40 percent over the exchange is a conflict", dict(conflict.fields)["state"], "CONFLICT")
    c.eq("with the gap said", dict(conflict.fields)["difference"], rounded(Fraction("110600") / 79000 - 1))
    edge = mod.price_check(with_price(ryo, "80580"), rd, bars, bd, built)
    c.eq("exactly at the tolerance is agreement", (dict(edge.fields)["state"], dict(edge.fields)["difference"]), ("AGREE", "0.020000"))
    over = mod.price_check(with_price(ryo, "80580.01"), rd, bars, bd, built)
    c.eq("a hair over is a conflict", dict(over.fields)["state"], "CONFLICT")
    below = mod.price_check(with_price(ryo, "77420"), rd, bars, bd, built)
    c.eq("below by the same gap is symmetric", (dict(below.fields)["state"], dict(below.fields)["difference"]), ("AGREE", "0.020000"))
    c.eq(
        "a price below by more is a conflict",
        dict(mod.price_check(with_price(ryo, "77419.99"), rd, bars, bd, built).fields)["state"],
        "CONFLICT",
    )
    looser = mod.price_check(with_price(ryo, "110600"), rd, bars, bd, built, tolerance="0.5")
    c.eq("a looser tolerance is written", (dict(looser.fields)["state"], dict(looser.fields)["tolerance"]), ("AGREE", "0.500000"))
    c.eq(
        "the same call is the same item", mod.price_check(with_price(ryo, "110600"), rd, bars, bd, built).evidence_id, conflict.evidence_id
    )
    c.eq(
        "another RYO tool is another item",
        mod.price_check(replace(ryo, path="deep"), rd, bars, bd, built).evidence_id != item.evidence_id,
        True,
    )
    huge_close = with_close(bars, "1000000000000000000000000000000")
    huge = mod.price_check(with_price(ryo, "1020000000000000000000000000001"), rd, huge_close, bd, built)
    c.eq("the gap is worked out exactly however many digits the prices have", dict(huge.fields)["state"], "CONFLICT")
    small = mod.price_check(with_price(ryo, "0.5"), rd, with_close(bars, "0.5"), bd, built)
    c.eq("a price below one is a price", (dict(small.fields)["state"], dict(small.fields)["difference"]), ("AGREE", "0.000000"))
    two = replace(ryo, asset_refs=(*ryo.asset_refs, ("tk-eth", "venue/eth", "RESOLVED")))
    other_instrument = replace(bars, asset_refs=(("tk-btc", "kraken/XBTUSD", "RESOLVED"),))
    c.eq("one asset on another instrument is still the asset", mod.price_check(ryo, rd, other_instrument, bd, built) is not None, True)
    c.eq("the first asset of each is the one compared", mod.price_check(two, rd, bars, bd, built) is not None, True)
    return c.bad


def check_nothing_to_compare(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World()
    ryo, rd, bars, bd = pair(w)
    built = 1_793_311_020_000
    c.eq("no price from RYO", mod.price_check(with_price(ryo, None), rd, bars, bd, built), None)
    c.eq("a price that is not a number", mod.price_check(with_price(ryo, "n/a"), rd, bars, bd, built), None)
    c.eq("a price that is not positive", mod.price_check(with_price(ryo, "0"), rd, bars, bd, built), None)
    c.eq("no close from the exchange", mod.price_check(ryo, rd, with_close(bars, None), bd, built), None)
    c.eq("a close that is not a number", mod.price_check(ryo, rd, with_close(bars, "x"), bd, built), None)
    c.eq("a close of zero", mod.price_check(ryo, rd, with_close(bars, "0"), bd, built), None)
    other = replace(bars, asset_refs=(("tk-eth", "venue/eth", "RESOLVED"),))
    c.eq("another asset is not compared", mod.price_check(ryo, rd, other, bd, built), None)
    c.eq("an item with no asset is not compared", mod.price_check(replace(ryo, asset_refs=()), rd, bars, bd, built), None)
    return c.bad


def check_candidate(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World()
    ryo, rd, bars, bd = pair(w)
    built = 1_793_311_020_000
    older = with_close(replace(bars, path="older", event_time_ms=bars.event_time_ms - 3_600_000), "70000")
    newer = with_close(replace(bars, path="newer", event_time_ms=bars.event_time_ms + 60_000), "79500")
    deep = replace(ryo, path="deep", tool="deep_analysis")
    interp = replace(ryo, path="verdict", kind=evidence.Kind.INTERPRETATION, fields=(("verdict", "bullish"),), unknown_fields=())
    items = [(older, digest_of(older)), (deep, digest_of(deep)), (ryo, rd), (newer, digest_of(newer)), (interp, digest_of(interp))]
    out = mod.candidate_checks(items, built)
    c.eq(
        "one per RYO item that states a price, in the order of their paths",
        [i.path for i in out],
        ["crosscheck/deep", "crosscheck/observation"],
    )
    c.eq("each against the newest exchange series", {dict(i.fields)["exchange_close"] for i in out}, {"79500"})
    c.eq("and standing on it", all(newer.evidence_id in {p for p, _ in i.derived_from} for i in out), True)
    other = replace(ryo, source="elsewhere", path="zz", origin=evidence.Family(evidence.FamilyKind.NEWS, "elsewhere"))
    priced = replace(interp, path="priced", fields=tuple(sorted((*interp.fields, ("market.price_usd", "79012.5")))))
    mixed = mod.candidate_checks([*items, (other, digest_of(other)), (priced, digest_of(priced))], built)
    c.eq("a price from another source or from an interpretation is not RYO's", [i.path for i in mixed], [i.path for i in out])
    for name in "abcdefgh":
        none = replace(bars, path=f"n{name}", event_time_ms=None)
        zero = with_close(replace(bars, path=f"z{name}", event_time_ms=0), "70000")
        chosen = mod.candidate_checks([(none, digest_of(none)), (zero, digest_of(zero)), (ryo, rd)], built)
        c.eq(f"a series with no time is older than one with a time ({name})", dict(chosen[0].fields)["exchange_close"], "70000")
    c.eq("none without RYO", mod.candidate_checks([(bars, bd)], built), ())
    c.eq("none without an exchange series", mod.candidate_checks([(ryo, rd)], built), ())
    c.eq("none from an interpretation alone", mod.candidate_checks([(interp, digest_of(interp)), (bars, bd)], built), ())
    tie_a = replace(bars, path="a", event_time_ms=bars.event_time_ms)
    tie_b = with_close(replace(bars, path="b", event_time_ms=bars.event_time_ms), "78000")
    first = min((tie_a, tie_b), key=lambda i: i.evidence_id)
    tied = mod.candidate_checks([(tie_b, digest_of(tie_b)), (tie_a, digest_of(tie_a)), (ryo, rd)], built)
    c.eq(
        "two series as new as each other: the one with the smaller id",
        dict(tied[0].fields)["exchange_close"],
        dict(first.fields)["last_close"],
    )
    c.eq(
        "the order of the items given does not matter",
        [i.evidence_id for i in mod.candidate_checks(list(reversed(items)), built)],
        [i.evidence_id for i in out],
    )
    c.eq("the constants", (mod.SOURCE, mod.TRANSFORM, mod.TOLERANCE), ("hoshitori.crosscheck", ("crosscheck", "1"), "0.02"))
    return c.bad


def check_conflicts(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World()
    ryo, rd, bars, bd = pair(w)
    built = 1_793_311_020_000
    bad = [mod.price_check(replace(with_price(ryo, "110600"), path=f"p{n}"), rd, bars, bd, built) for n in "cab"]
    good = mod.price_check(replace(with_price(ryo, "79012.5"), path="pg"), rd, bars, bd, built)
    foreign = replace(bad[0], source="elsewhere")
    observed = replace(
        ryo, source=mod.SOURCE, path="o", fields=(("state", "CONFLICT"),), unknown_fields=(), derived_from=(), transform=None
    )
    clashes = sorted(i.evidence_id for i in bad)
    c.eq(
        "the checks that found a conflict, sorted, whatever order they came in",
        mod.conflicts([bad[0], good, foreign, observed, bad[1], bad[2]]),
        tuple(clashes),
    )
    c.eq("none when every check agrees", mod.conflicts([good]), ())
    c.eq("none from an item of another source that says conflict", mod.conflicts([foreign]), ())
    c.eq("none from an observation of the same source", mod.conflicts([observed]), ())
    c.eq("a generator is read once", mod.conflicts(i for i in bad[:1]), (bad[0].evidence_id,))
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (check_one_pair, check_nothing_to_compare, check_candidate, check_conflicts)
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
