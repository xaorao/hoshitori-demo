"""Checks of the evidence store (``hoshitori.lineage``), runnable against a mutant."""

from __future__ import annotations

import hashlib
import types
from collections.abc import Callable
from typing import Any

from hoshitori import canonical, evidence
from tests.evidence_checks import H1, H2, H3, R, derived, observation

HASH = "f" * 64


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
        except self.mod.LineageError as exc:
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
    """A diamond: A (RYO) and B (an exchange) are read; C comes from A; D from B and C; E from D; F from A."""

    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.store = mod.EvidenceStore()
        self.items: dict[str, Any] = {}
        self.a = observation(mod_ev(mod), capture_id="cap-A", known_at_ms=R, received_at_ms=R)
        self.b = observation(
            mod_ev(mod),
            capture_id="cap-B",
            path="series",
            source="binance-spot",
            origin=evidence.Family(evidence.FamilyKind.EXCHANGE, "binance-spot"),
            tool=None,
            request=(("instrument", "BTCUSDT"),),
            received_at_ms=R + 1_000,
            known_at_ms=R + 1_000,
        )
        self.c = self.derive("c", [self.a], R + 2_000)
        self.d = self.derive("d", [self.b, self.c], R + 3_000)
        self.e = self.derive("e", [self.d], R + 4_000)
        self.f = self.derive("f", [self.a], R + 2_500)
        for item in (self.a, self.b, self.c, self.d, self.e, self.f):
            self.store.add(item, digest_of(item))

    def derive(self, name: str, parents: list[Any], at: int) -> Any:
        pairs = tuple(sorted((p.evidence_id, digest_of(p)) for p in parents))
        return derived(
            mod_ev(self.mod),
            path=name,
            derived_from=pairs,
            received_at_ms=at,
            known_at_ms=at,
            fields=((name, "1"),),
            transform=(f"t-{name}", "1"),
        )


def mod_ev(_mod: types.ModuleType) -> types.ModuleType:
    return evidence


def check_writing(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World(mod)
    s = w.store
    c.eq("six items", (len(s), list(s.ids()) == sorted(s.ids())), (6, True))
    c.eq("membership", (w.a.evidence_id in s, "nothing" in s, 5 in s), (True, False, False))
    c.eq("get and digest", (s.get(w.a.evidence_id), s.digest(w.a.evidence_id)), (w.a, digest_of(w.a)))
    c.refuses("an item that is not held", lambda: s.get("nothing"), "there is no item 'nothing'")
    c.refuses("a digest of an item that is not held", lambda: s.digest("nothing"), "there is no item 'nothing'")
    c.eq("adding the same item again", s.add(w.a, digest_of(w.a)), False)
    c.eq("and nothing changed", len(s), 6)
    c.refuses("the same id with another digest", lambda: s.add(w.a, HASH), f"{w.a.evidence_id} is already held with another digest")
    c.refuses("a digest that is short", lambda: s.add(w.a, "ab"), "a digest is 64 lower-case hexadecimal characters")
    c.refuses("a digest that is a number", lambda: s.add(w.a, 5), "a digest is 64 lower-case hexadecimal characters")
    fresh = mod.EvidenceStore()
    c.refuses("a child before its parent", lambda: fresh.add(w.c, digest_of(w.c)), f"the parent {w.a.evidence_id} is not in the store")
    fresh.add(w.a, digest_of(w.a))
    wrong = derived(
        evidence, path="c", derived_from=((w.a.evidence_id, HASH),), received_at_ms=R + 2_000, known_at_ms=R + 2_000, transform=("t-c", "1")
    )
    c.refuses(
        "a parent named with another digest",
        lambda: fresh.add(wrong, digest_of(wrong)),
        f"the parent {w.a.evidence_id} is named with a digest other than the one held",
    )
    early = derived(
        evidence,
        path="c",
        derived_from=((w.a.evidence_id, digest_of(w.a)),),
        received_at_ms=R - 5,
        known_at_ms=R - 5,
        transform=("t-c", "1"),
    )
    c.refuses(
        "a child known before its parent",
        lambda: fresh.add(early, digest_of(early)),
        f"the parent {w.a.evidence_id} was known after the item derived from it",
    )
    same = derived(
        evidence, path="c2", derived_from=((w.a.evidence_id, digest_of(w.a)),), received_at_ms=R, known_at_ms=R, transform=("t-c", "1")
    )
    c.eq("a child known at the same instant as its parent", fresh.add(same, digest_of(same)), True)
    # revisions
    rev = observation(evidence, capture_id="cap-A2", known_at_ms=R + 10, received_at_ms=R + 10, supersedes=w.a.evidence_id)
    c.eq("a revision", s.add(rev, digest_of(rev)), True)
    c.refuses(
        "a second revision of the same item",
        lambda: s.add(
            observation(evidence, capture_id="cap-A3", known_at_ms=R + 20, received_at_ms=R + 20, supersedes=w.a.evidence_id), HASH
        ),
        f"{w.a.evidence_id} has already been superseded",
    )
    c.refuses(
        "a revision of something that is not held",
        lambda: s.add(observation(evidence, capture_id="cap-X", supersedes="evGone"), HASH),
        "the item it supersedes, evGone, is not in the store",
    )
    c.refuses(
        "a revision that measures something else",
        lambda: s.add(
            observation(
                evidence,
                capture_id="cap-Y",
                request=(("symbol", "ETH"),),
                known_at_ms=R + 30,
                received_at_ms=R + 30,
                supersedes=w.a.evidence_id,
            ),
            HASH,
        ),
        "a revision measures the same thing as the item it replaces",
    )
    other = mod.EvidenceStore()
    other.add(w.a, digest_of(w.a))
    simultaneous = observation(evidence, capture_id="cap-Z", supersedes=w.a.evidence_id)
    c.refuses(
        "a revision known at the same instant", lambda: other.add(simultaneous, HASH), "a revision is known later than the item it replaces"
    )
    earlier = observation(evidence, capture_id="cap-W", known_at_ms=R - 1, received_at_ms=R - 1, supersedes=w.a.evidence_id)
    c.refuses("a revision known earlier", lambda: other.add(earlier, HASH), "a revision is known later than the item it replaces")
    c.eq("the store is not changed by a refused write", len(other), 1)
    return c.bad


def check_graph(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World(mod)
    s = w.store
    ids = {n: getattr(w, n).evidence_id for n in "abcdef"}

    def names(found: Any) -> str:
        inverse = {v: k for k, v in ids.items()}
        return "".join(sorted(inverse[i] for i in found))

    c.eq(
        "parents",
        {
            n: tuple(sorted(s.parents(ids[n]))) == tuple(sorted(ids[p] for p in ps))
            for n, ps in (("a", ""), ("b", ""), ("c", "a"), ("d", "bc"), ("e", "d"), ("f", "a"))
        },
        dict.fromkeys("abcdef", True),
    )
    c.eq("children of a", names(s.children(ids["a"])), "cf")
    c.eq("children are in id order", s.children(ids["a"]) == tuple(sorted(s.children(ids["a"]))), True)
    c.eq("children of b", names(s.children(ids["b"])), "d")
    c.eq("children of e", s.children(ids["e"]), ())
    c.eq("ancestors", {n: names(s.ancestors(ids[n])) for n in "abcdef"}, {"a": "", "b": "", "c": "a", "d": "abc", "e": "abcd", "f": "a"})
    c.eq(
        "descendants", {n: names(s.descendants(ids[n])) for n in "abcdef"}, {"a": "cdef", "b": "de", "c": "de", "d": "e", "e": "", "f": ""}
    )
    c.eq("roots", {n: names(s.roots(ids[n])) for n in "abcdef"}, {"a": "a", "b": "b", "c": "a", "d": "ab", "e": "ab", "f": "a"})
    c.eq("families", {n: sorted(f.text for f in s.families(ids[n])) for n in "abcdef"}, {"a": ["RYO"], "b": ["EXCHANGE:binance-spot"], "c": ["RYO"], "d": ["EXCHANGE:binance-spot", "RYO"], "e": ["EXCHANGE:binance-spot", "RYO"], "f": ["RYO"]})  # fmt: skip
    for method in (s.parents, s.children, s.ancestors, s.descendants, s.roots, s.families):
        c.refuses(f"{method.__name__} of an item that is not held", lambda m=method: m("nothing"), "there is no item 'nothing'")
    # interventions
    c.eq("withholding a takes c, d, e, f with it", names(s.withheld([ids["a"]])), "acdef")
    c.eq("withholding b", names(s.withheld([ids["b"]])), "bde")
    c.eq("withholding c", names(s.withheld([ids["c"]])), "cde")
    c.eq("withholding two", names(s.withheld([ids["b"], ids["f"]])), "bdef")
    c.eq("withholding nothing", s.withheld([]), frozenset())
    c.eq("a context standing on a withheld source", names(s.leaks([ids["b"], ids["d"]], [ids["a"]])), "d")
    c.eq("a clean context", s.leaks([ids["b"]], [ids["a"]]), ())
    c.eq("the removed item itself is a leak", names(s.leaks([ids["a"]], [ids["a"]])), "a")
    c.eq(
        "a context is checked once and in order",
        s.leaks([ids["f"], ids["d"], ids["d"], ids["c"]], [ids["a"]]) == tuple(sorted({ids["c"], ids["d"], ids["f"]})),
        True,
    )
    recomputed = w.derive("d2", [w.b], R + 3_500)
    s.add(recomputed, digest_of(recomputed))
    c.eq("an item recomputed from the parents that remain is not a leak", s.leaks([recomputed.evidence_id], [ids["a"]]), ())
    c.eq("but it is when its own parent goes", s.leaks([recomputed.evidence_id], [ids["b"]]), (recomputed.evidence_id,))
    many = [w.derive(f"x{n}", [w.a], R + 5_000 + n) for n in range(10)]
    for item in many:
        s.add(item, digest_of(item))
    wide = s.leaks([item.evidence_id for item in many], [ids["a"]])
    c.eq("ten leaks are reported in id order, whatever order a set would give them", wide, tuple(sorted(item.evidence_id for item in many)))
    c.refuses("a context that names something not held", lambda: s.leaks(["nothing"], [ids["a"]]), "there is no item 'nothing'")
    c.refuses("removing something not held", lambda: s.withheld(["nothing"]), "there is no item 'nothing'")
    return c.bad


def check_as_of(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    s = mod.EvidenceStore()

    def reading(n: int, at: int, **over: Any) -> Any:
        item = observation(evidence, capture_id=f"cap-{n}", received_at_ms=at, known_at_ms=at, **over)
        s.add(item, digest_of(item))
        return item

    r1, r2, r3 = reading(1, R + 10), reading(2, R + 20), reading(3, R + 30)
    eth = reading(4, R + 25, request=(("symbol", "ETH"),))
    c.eq("before anything", s.as_of(R + 9), ())
    c.eq("at the first", s.as_of(R + 10), (r1,))
    c.eq("between the first and the second", s.as_of(R + 19), (r1,))
    c.eq("exactly at the second", s.as_of(R + 20), (r2,))
    c.eq("another key is another reading", {i.evidence_id for i in s.as_of(R + 26)}, {r2.evidence_id, eth.evidence_id})
    c.eq("later readings win", {i.evidence_id for i in s.as_of(R + 10**6)}, {r3.evidence_id, eth.evidence_id})
    c.eq("the order is by id", [i.evidence_id for i in s.as_of(R + 10**6)] == sorted(i.evidence_id for i in s.as_of(R + 10**6)), True)
    c.eq("by kind", s.as_of(R + 10**6, [evidence.Kind.INTERPRETATION]), ())
    c.eq("by kind, observations", len(s.as_of(R + 10**6, [evidence.Kind.OBSERVATION])), 2)
    c.eq("a kind given as a one-shot iterator", len(s.as_of(R + 10**6, iter([evidence.Kind.OBSERVATION]))), 2)
    # the same known time: the later received, then the larger id, wins
    tie = mod.EvidenceStore()
    p = observation(evidence, capture_id="cap-p", received_at_ms=R + 1, known_at_ms=R + 5)
    q = observation(evidence, capture_id="cap-q", received_at_ms=R + 2, known_at_ms=R + 5)
    tie.add(p, digest_of(p))
    tie.add(q, digest_of(q))
    c.eq("known together, received later wins", tie.as_of(R + 5), (q,))
    t2 = mod.EvidenceStore()
    x = observation(evidence, capture_id="cap-x", received_at_ms=R + 1, known_at_ms=R + 5)
    y = observation(evidence, capture_id="cap-y", received_at_ms=R + 1, known_at_ms=R + 5)
    t2.add(x, digest_of(x))
    t2.add(y, digest_of(y))
    c.eq("known and received together, the larger id wins", t2.as_of(R + 5), (max(x, y, key=lambda i: i.evidence_id),))
    # a backfill: a reading about the past that arrives later is not seen by an earlier time
    back = reading(5, R + 100, event_time_ms=R - 10_000)
    c.eq("a backfill is invisible before it was known", back.evidence_id in {i.evidence_id for i in s.as_of(R + 99)}, False)
    c.eq("and visible after", back.evidence_id in {i.evidence_id for i in s.as_of(R + 100)}, True)
    # revisions
    rs = mod.EvidenceStore()
    o = observation(evidence, capture_id="cap-o", received_at_ms=R, known_at_ms=R)
    r_1 = observation(evidence, capture_id="cap-r1", received_at_ms=R + 10, known_at_ms=R + 10, supersedes=o.evidence_id)
    r_2 = observation(evidence, capture_id="cap-r2", received_at_ms=R + 20, known_at_ms=R + 20, supersedes=r_1.evidence_id)
    for item in (o, r_1, r_2):
        rs.add(item, digest_of(item))
    c.eq("the original before any revision is known", rs.latest(o.evidence_id, R + 9), o)
    c.eq("the first revision once known", rs.latest(o.evidence_id, R + 10), r_1)
    c.eq("between revisions", rs.latest(o.evidence_id, R + 19), r_1)
    c.eq("the second revision", rs.latest(o.evidence_id, R + 20), r_2)
    c.eq("from a revision", rs.latest(r_1.evidence_id, R + 20), r_2)
    c.eq("the last has no later", rs.latest(r_2.evidence_id, R + 10**6), r_2)
    c.eq("as of reads the same chain", (rs.as_of(R + 9), rs.as_of(R + 15), rs.as_of(R + 25)), ((o,), (r_1,), (r_2,)))
    c.refuses("the latest of something not held", lambda: rs.latest("nothing", R), "there is no item 'nothing'")
    return c.bad


def check_set_hash(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    entries = [("evB", H2), ("evA", H1)]
    want = hashlib.sha256(
        canonical.dumps({"items": [{"evidence_id": "evA", "sha256": H1}, {"evidence_id": "evB", "sha256": H2}]})
    ).hexdigest()
    c.eq("a golden hash", mod.observation_set_hash(entries), want)
    c.eq("the order does not matter", mod.observation_set_hash(entries), mod.observation_set_hash(reversed(entries)))
    c.eq("a repeat of an entry counts once", mod.observation_set_hash([*entries, ("evA", H1)]), want)
    c.eq("the empty set", mod.observation_set_hash([]), hashlib.sha256(canonical.dumps({"items": []})).hexdigest())
    c.eq("another digest changes the hash", mod.observation_set_hash([("evA", H3), ("evB", H2)]) != want, True)
    c.eq("another id changes the hash", mod.observation_set_hash([("evC", H1), ("evB", H2)]) != want, True)
    c.eq("one more item changes the hash", mod.observation_set_hash([*entries, ("evC", H3)]) != want, True)
    c.refuses("an id given two digests", lambda: mod.observation_set_hash([("evA", H1), ("evA", H2)]), "evA is given two digests")
    c.refuses(
        "a digest that is short", lambda: mod.observation_set_hash([("evA", "ab")]), "a digest is 64 lower-case hexadecimal characters"
    )
    c.refuses("an empty id", lambda: mod.observation_set_hash([("", H1)]), "an id is a non-empty string")
    c.refuses("an id that is a number", lambda: mod.observation_set_hash([(5, H1)]), "an id is a non-empty string")
    w = World(mod)
    c.eq(
        "a store's hash of its items",
        w.store.set_hash([w.a.evidence_id, w.b.evidence_id]),
        mod.observation_set_hash([(w.a.evidence_id, digest_of(w.a)), (w.b.evidence_id, digest_of(w.b))]),
    )
    c.refuses("a store's hash of an item it does not hold", lambda: w.store.set_hash(["nothing"]), "there is no item 'nothing'")
    return c.bad


def check_independence(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    F, K = evidence.Family, evidence.FamilyKind
    s = mod.EvidenceStore()

    def put(n: int, family: Any, **over: Any) -> Any:
        item = observation(evidence, capture_id=f"cap-{n}", origin=family, known_at_ms=R + n, received_at_ms=R + n, **over)
        s.add(item, digest_of(item))
        return item

    ryo1, ryo2, ryo3 = (put(i, evidence.RYO, tool=f"tool{i}") for i in (1, 2, 3))
    binance = put(4, F(K.EXCHANGE, "binance-spot"), source="binance-spot", tool=None)
    okx = put(5, F(K.EXCHANGE, "okx"), source="okx", tool=None)
    outlet = put(6, F(K.NEWS, "coindesk.com"), source="coindesk", tool=None)
    doc = put(7, F(K.PRIMARY_DOC, "binance.com"), source="binance-docs", tool=None)

    def ids(*items: Any) -> list[str]:
        return [i.evidence_id for i in items]

    three = s.independence(ids(ryo1, ryo2, ryo3))
    c.eq("three RYO calls are one origin", (three.count, three.calls, three.origins, three.established), (1, 3, (evidence.RYO,), True))
    c.eq("and display it so", three.display(), "1 independent origin checked (3 calls)")
    c.eq("the display of one call", s.independence(ids(ryo1)).display(), "1 independent origin checked (1 call)")
    repeat = s.independence(ids(outlet, doc), originals={outlet.evidence_id: F(K.PRIMARY_DOC, "binance.com")})
    c.eq(
        "an outlet repeating an announcement adds nothing beyond it",
        (repeat.count, repeat.calls, repeat.origins),
        (1, 2, (F(K.PRIMARY_DOC, "binance.com"),)),
    )
    c.eq("without that knowledge they are two", s.independence(ids(outlet, doc)).count, 2)
    series = put(8, F(K.EXCHANGE, "binance-spot"), source="binance-spot", tool=None, path="candles")
    atr = derived(
        evidence,
        path="atr",
        derived_from=((series.evidence_id, digest_of(series)),),
        received_at_ms=R + 9,
        known_at_ms=R + 9,
        transform=("atr", "1"),
    )
    s.add(atr, digest_of(atr))
    from_exchange = s.independence(ids(atr))
    c.eq(
        "a derived indicator carries the exchange family",
        (from_exchange.origins, from_exchange.count),
        ((F(K.EXCHANGE, "binance-spot"),), 1),
    )
    two = s.independence(ids(ryo1, binance))
    c.eq("RYO and an exchange that agree are two origins", (two.count, two.calls), (2, 2))
    c.eq(
        "and nobody has shown them independent",
        (two.established, two.display()),
        (False, "2 independent origins checked (2 calls); independence not established"),
    )
    pair = frozenset({evidence.RYO, F(K.EXCHANGE, "binance-spot")})
    shown = s.independence(ids(ryo1, binance), independent=[pair])
    c.eq(
        "once someone has shown it, it is established",
        (shown.count, shown.established, shown.display()),
        (2, True, "2 independent origins checked (2 calls)"),
    )
    c.eq(
        "a pair given in the other order is the same pair",
        s.independence(ids(binance, ryo1), independent=[frozenset({F(K.EXCHANGE, "binance-spot"), evidence.RYO})]).established,
        True,
    )
    three_way = s.independence(ids(ryo1, binance, okx), independent=[pair])
    c.eq("three origins need every pair shown", (three_way.count, three_way.established), (3, False))
    all_pairs = [pair, frozenset({evidence.RYO, F(K.EXCHANGE, "okx")}), frozenset({F(K.EXCHANGE, "binance-spot"), F(K.EXCHANGE, "okx")})]
    c.eq("and is established when they are", s.independence(ids(ryo1, binance, okx), independent=all_pairs).established, True)
    index = frozenset({F(K.EXCHANGE, "binance-spot"), F(K.EXCHANGE, "okx")})
    sharing = s.independence(ids(binance, okx), shared=[index])
    c.eq(
        "two exchanges quoting one index are one origin",
        (sharing.count, sharing.origins, sharing.established),
        (1, (F(K.EXCHANGE, "binance-spot"),), True),
    )
    mixed = s.independence(ids(ryo1, binance, okx), shared=[index], independent=[pair])
    c.eq("a shared group stands in for its members in a pair", (mixed.count, mixed.established), (2, True))
    mixed_other = s.independence(ids(ryo1, okx), shared=[index], independent=[pair])
    c.eq(
        "a member of the group is matched through the group",
        (mixed_other.count, mixed_other.established, mixed_other.origins),
        (2, True, (F(K.EXCHANGE, "binance-spot"), evidence.RYO)),
    )
    c.eq("the same item twice is one call", s.independence([ryo1.evidence_id, ryo1.evidence_id]).calls, 1)
    nothing = s.independence([])
    c.eq(
        "nothing",
        (nothing.count, nothing.calls, nothing.established, nothing.display()),
        (0, 0, True, "0 independent origins checked (0 calls)"),
    )
    c.eq("a derived item over two families counts both", s.independence(ids(atr)).count, 1)
    w = World(mod)
    both = w.store.independence([w.d.evidence_id])
    c.eq("a derived item from two families counts both", (both.count, both.calls, both.established), (2, 1, False))
    c.eq(
        "an original for an item that is not in the list is ignored",
        s.independence(ids(ryo1), originals={binance.evidence_id: F(K.NEWS, "x")}).origins,
        (evidence.RYO,),
    )
    c.eq(
        "origins are in order",
        s.independence(ids(okx, ryo1, binance)).origins,
        tuple(sorted([evidence.RYO, F(K.EXCHANGE, "okx"), F(K.EXCHANGE, "binance-spot")])),
    )
    wide = [put(20 + n, F(K.EXCHANGE, f"venue-{n:02d}"), source=f"venue-{n:02d}", tool=None) for n in (7, 3, 9, 1, 5, 8, 2, 6, 4, 0)]
    wide_result = s.independence(ids(*wide))
    c.eq(
        "ten origins are reported in order, whatever order a set would give them",
        wide_result.origins,
        tuple(F(K.EXCHANGE, f"venue-{n:02d}") for n in range(10)),
    )
    c.refuses("an item that is not held", lambda: s.independence(["nothing"]), "there is no item 'nothing'")
    c.eq("a frozen result", mod.Independence.__dataclass_params__.frozen, True)
    return c.bad


def check_records(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World(mod)
    records = {n: getattr(w, n).to_record("urn:x", getattr(w, n).known_at_ms) for n in "abcdef"}
    order = ["a", "b", "c", "d", "e", "f"]
    store = mod.rebuild(records[n] for n in order)
    c.eq("a rebuild holds every item", (len(store), store.ids()), (6, w.store.ids()))
    c.eq("with the same digests", all(store.digest(i) == w.store.digest(i) for i in w.store.ids()), True)
    c.eq("a rebuild with a record twice", len(mod.rebuild([*(records[n] for n in order), records["a"]])), 6)
    c.eq(
        "add_record says whether it was new", (mod.EvidenceStore().add_record(records["a"]), store.add_record(records["a"])), (True, False)
    )
    c.refuses(
        "a child before its parent", lambda: mod.rebuild([records["c"], records["a"]]), f"the parent {w.a.evidence_id} is not in the store"
    )
    broken = {**records["a"], "type": "forecast"}
    c.refuses(
        "a record that is not an evidence record", lambda: mod.rebuild([broken]), "not an evidence record of a version this reader knows"
    )
    swapped = {**records["a"], "created_at": "2026-10-29T21:56:41.000Z"}
    c.refuses(
        "the same item as a different record", lambda: store.add_record(swapped), f"{w.a.evidence_id} is already held with another digest"
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_writing, check_graph, check_as_of, check_set_hash, check_independence, check_records):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
