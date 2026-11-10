"""Checks of taking evidence out of a snapshot (``hoshitori.intervention``), runnable against a mutant."""

from __future__ import annotations

import types
from dataclasses import replace
from typing import Any

from hoshitori import snapshot
from hoshitori.evidence import record_digest
from hoshitori.lineage import EvidenceStore
from tests.snapshot_checks import World


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Any, says: str) -> None:
        try:
            call()
        except self.mod.InterventionError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def world() -> tuple[World, Any, str, str]:
    w = World()
    feats = w.derive("features", [w.btc_bars], source="hoshitori.features")
    more = w.derive("more", [feats], source="hoshitori.features")
    snap = w.freeze(snapshot, by_candidate={"cd-btc": [w.btc_ryo, w.btc_int, w.btc_bars, feats, more], "cd-eth": [w.eth_bars]})
    return w, snap, feats, more


def ids(snap: Any) -> list[str]:
    return sorted(item.evidence_id for item, _ in snap.items)


def check_withhold(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w, snap, feats, more = world()
    before = (snap.sha256, ids(snap))
    out = mod.withhold(snap, w.store, [w.btc_bars])
    c.eq(
        "a source's raw item takes everything derived from it",
        (out.status, out.removed, out.withheld),
        ("READY", (w.btc_bars,), tuple(sorted((w.btc_bars, feats, more)))),
    )
    c.eq("they are gone from the items", ids(out.snapshot), sorted(set(ids(snap)) - {w.btc_bars, feats, more}))
    c.eq("and from the candidate that read them", out.snapshot.slots[0].item_ids, (w.btc_ryo, w.btc_int))
    c.eq(
        "another candidate and the shared items are as they were",
        (out.snapshot.slots[1].item_ids, out.snapshot.shared),
        (snap.slots[1].item_ids, snap.shared),
    )
    c.eq("no leak", (out.leaks,), ((),))
    c.eq("the original is as it was", (snap.sha256, ids(snap)), before)
    c.eq("the what-if snapshot is another snapshot", out.snapshot.sha256 != snap.sha256, True)
    c.eq(
        "what else it keeps",
        (out.snapshot.cycle_id, out.snapshot.manifest_hash, out.snapshot.snapshot_at_ms, out.snapshot.required),
        (snap.cycle_id, snap.manifest_hash, snap.snapshot_at_ms, snap.required),
    )
    only = mod.withhold(snap, w.store, [feats])
    c.eq("a derived item takes its own descendants and not its parents", only.withheld, tuple(sorted((feats, more))))
    c.eq("the parent stays", w.btc_bars in ids(only.snapshot), True)
    last = mod.withhold(snap, w.store, [more])
    c.eq("a leaf takes only itself", last.withheld, (more,))
    shared = mod.withhold(snap, w.store, [w.market])
    c.eq("a shared item leaves the shared list", (shared.snapshot.shared, w.market in ids(shared.snapshot)), ((), False))
    c.eq("a repeated id is one", mod.withhold(snap, w.store, [w.btc_bars, w.btc_bars]).removed, (w.btc_bars,))
    # a required source taken out leaves the candidate missing it, as a snapshot frozen without it would say
    fresh = w.freeze(snapshot, by_candidate={"cd-btc": [w.btc_ryo, w.btc_int], "cd-eth": [w.eth_bars]})
    c.eq(
        "a required source taken out is missing in the what-if snapshot",
        (out.snapshot.slots[0].missing, out.snapshot.slots[1].missing),
        (fresh.slots[0].missing, fresh.slots[1].missing),
    )
    c.eq("which is the required source", out.snapshot.slots[0].missing, ("binance-spot",))
    c.eq(
        "and a reader says the candidate is not complete",
        snapshot.Reader(out.snapshot, snapshot.Access.INDEPENDENT).complete("cd-btc"),
        False,
    )
    c.eq("taking out what is not required leaves nothing missing", mod.withhold(snap, w.store, [w.btc_ryo]).snapshot.slots[0].missing, ())
    c.eq("what was asked for is sorted", mod.withhold(snap, w.store, [w.btc_int, w.btc_ryo]).removed, tuple(sorted((w.btc_int, w.btc_ryo))))
    c.eq("two sources", ids(mod.withhold(snap, w.store, [w.btc_bars, w.eth_bars]).snapshot), sorted({w.btc_ryo, w.btc_int, w.market}))
    every = [item.evidence_id for item, _ in snap.items]
    c.eq(
        "a long request comes back sorted whatever order it came in",
        mod.withhold(snap, w.store, list(reversed(every))).removed,
        tuple(sorted(every)),
    )
    c.eq("and the items that left are sorted too", mod.withhold(snap, w.store, list(reversed(every))).withheld, tuple(sorted(every)))
    c.eq("a generator is read once", mod.withhold(snap, w.store, (i for i in [w.btc_bars])).removed, (w.btc_bars,))
    c.eq("the constants", (mod.READY, mod.NOT_EVALUATED), ("READY", "NOT_EVALUATED"))
    c.eq("frozen", mod.Withheld.__dataclass_params__.frozen, True)
    return c.bad


def check_sources_and_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w, snap, feats, more = world()
    c.eq("a source's items", mod.source_ids(snap, "binance-spot"), tuple(sorted((w.btc_bars, w.eth_bars))))
    c.eq("a derived source", mod.source_ids(snap, "hoshitori.features"), tuple(sorted((feats, more))))
    c.eq(
        "one source out, by name",
        ids(mod.withhold(snap, w.store, mod.source_ids(snap, "fear-greed")).snapshot),
        sorted(set(ids(snap)) - {w.market}),
    )
    from dataclasses import replace

    backwards = replace(snap, items=tuple(reversed(snap.items)))
    c.eq(
        "a source's items are sorted whatever order the snapshot holds them in",
        mod.source_ids(backwards, "hoshitori.features"),
        tuple(sorted((feats, more))),
    )
    c.refuses("a source that is not there", lambda: mod.source_ids(snap, "nowhere"), "the snapshot holds nothing from 'nowhere'")
    c.refuses("nothing asked", lambda: mod.withhold(snap, w.store, []), "an intervention removes at least one item")
    c.refuses("an id not in the snapshot", lambda: mod.withhold(snap, w.store, ["ev-nothing"]), "ev-nothing is not in the snapshot")
    return c.bad


class Liar(EvidenceStore):
    """A store whose descendants forget a child, as a broken lineage would: the check from the other side must notice."""

    def descendants(self, ident: str) -> frozenset[str]:
        return frozenset()


def check_leak(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w, snap, feats, more = world()
    liar = Liar()
    for ident in sorted(w.store.ids(), key=lambda i: len(w.store.ancestors(i))):  # parents first
        liar.add(w.store.get(ident), w.store.digest(ident))
    out = mod.withhold(snap, liar, [w.btc_bars])
    c.eq(
        "something left that stands on a removed item is not evaluated",
        (out.status, out.snapshot, out.leaks),
        ("NOT_EVALUATED", None, tuple(sorted((feats, more)))),
    )
    from dataclasses import replace

    backwards = replace(snap, items=tuple(reversed(snap.items)))
    c.eq("what still stands is sorted", mod.withhold(backwards, liar, [w.btc_bars]).leaks, tuple(sorted((feats, more))))
    c.eq("and says what was asked and what was thought to leave", (out.removed, out.withheld), ((w.btc_bars,), (w.btc_bars,)))
    return c.bad


def text_size(snap: Any, wanted: list[str]) -> int:
    return sum(len(v) for item, _ in snap.items if item.evidence_id in wanted for _, v in item.fields if isinstance(v, str))


def check_placebo(mod: types.ModuleType) -> list[str]:
    """A neutral item of about the size of the evidence taken out in a real challenge, added to one candidate: the control that shows how
    much a decision moves when the evidence is only longer."""
    c = Case(mod)
    w, snap, feats, more = world()
    before = (snap.sha256, ids(snap))
    like = [w.btc_ryo, w.btc_int]
    out = mod.add_placebo(snap, "cd-btc", like)
    c.eq("it is one new item", sorted(set(ids(out.snapshot)) - set(ids(snap))), [out.item_id])
    c.eq("on the candidate asked for, after its others", out.snapshot.slots[0].item_ids, (*snap.slots[0].item_ids, out.item_id))
    c.eq(
        "another candidate and the shared items are as they were",
        (out.snapshot.slots[1], out.snapshot.shared),
        (snap.slots[1], snap.shared),
    )
    held = {item.evidence_id: (item, digest) for item, digest in out.snapshot.items}
    item = held[out.item_id][0]
    c.eq(
        "a neutral observation from its own source",
        (item.source, item.kind.value, item.origin.text, item.derived_from, item.asset_refs),
        ("hoshitori.placebo", "OBSERVATION", "PRIMARY_DOC:hoshitori-placebo", (), ()),
    )
    note = dict(item.fields)["note"]
    c.eq("its text is as long as the text of the evidence it stands in for", len(str(note)), text_size(snap, like))
    c.eq("and says nothing", "This reading carries no information about the token. ".startswith(str(note)[:54]), True)
    c.eq("it is known when the snapshot was fixed", (item.received_at_ms, item.known_at_ms), (snap.snapshot_at_ms, snap.snapshot_at_ms))
    c.eq("its digest is held with it", len(held[out.item_id][1]), 64)
    c.eq("the what-if snapshot is another snapshot", out.snapshot.sha256 != snap.sha256, True)
    c.eq("the original is as it was", (snap.sha256, ids(snap)), before)
    c.eq(
        "what else it keeps",
        (
            out.snapshot.cycle_id,
            out.snapshot.manifest_hash,
            out.snapshot.snapshot_at_ms,
            out.snapshot.required,
            out.snapshot.slots[0].missing,
        ),
        (snap.cycle_id, snap.manifest_hash, snap.snapshot_at_ms, snap.required, snap.slots[0].missing),
    )
    reader = snapshot.Reader(out.snapshot, snapshot.Access.INDEPENDENT)
    c.eq("an arm that reads only observations is given it", out.item_id in reader.ids("cd-btc"), True)
    c.eq("it names the size it matched", (out.matched, out.text_chars), (tuple(sorted(like)), text_size(snap, like)))
    c.eq("the same call gives the same item", mod.add_placebo(snap, "cd-btc", like).item_id, out.item_id)
    every = [w.btc_ryo, w.btc_int, w.btc_bars, feats, more]
    big = mod.add_placebo(snap, "cd-btc", every)
    bignote = str(dict(held_item(big))["note"])
    c.eq("a longer stand-in is exactly as long", (len(bignote), big.text_chars), (text_size(snap, every), text_size(snap, every)))
    c.eq("and repeats the one neutral sentence", bignote, ("This reading carries no information about the token. " * 99)[: len(bignote)])
    c.eq("what it matched is listed in order whatever the order asked", big.matched, tuple(sorted(every)))
    c.eq(
        "its constants",
        (mod.PLACEBO_SOURCE, mod.PLACEBO_SENTENCE, mod.PLACEBO_URN),
        ("hoshitori.placebo", "This reading carries no information about the token. ", "urn:hoshitori:placebo"),
    )
    c.eq("its path and capture", (item.path, item.capture_id), ("placebo/neutral", "placebo"))
    c.eq(
        "its digest is that of its record",
        held[out.item_id][1],
        record_digest(item.to_record("urn:hoshitori:placebo", snap.snapshot_at_ms)),
    )
    c.eq("frozen", mod.Placed.__dataclass_params__.frozen, True)
    textless = replace(snap, items=tuple((replace(i, fields=()) if i.evidence_id == w.btc_ryo else i, d) for i, d in snap.items))
    c.eq("an item with no text still gives a one-character stand-in", mod.add_placebo(textless, "cd-btc", [w.btc_ryo]).text_chars, 1)
    c.refuses("no evidence to match", lambda: mod.add_placebo(snap, "cd-btc", []), "a placebo is sized from at least one item")
    c.refuses("an item that is not there", lambda: mod.add_placebo(snap, "cd-btc", ["evnotthere"]), "evnotthere is not in the snapshot")
    c.refuses(
        "a candidate that is not there", lambda: mod.add_placebo(snap, "cd-none", like), "cd-none is not a candidate of this snapshot"
    )
    c.refuses("one placebo only", lambda: mod.add_placebo(out.snapshot, "cd-btc", like), "the snapshot already holds a placebo")
    return c.bad


def held_item(placed: Any) -> tuple[tuple[str, Any], ...]:
    return next(item.fields for item, _ in placed.snapshot.items if item.evidence_id == placed.item_id)


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (check_withhold, check_sources_and_refusals, check_leak, check_placebo)
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
