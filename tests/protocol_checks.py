"""Checks of the protocol document (``hoshitori.protocol``), runnable against a mutant."""

from __future__ import annotations

import hashlib
import types
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from hoshitori import canonical

H1, H2, H3 = "a" * 64, "b" * 64, "c" * 64
MINUTE, HOUR = 60_000, 3_600_000
START = 1_790_000_000_000 - 1_790_000_000_000 % HOUR


def make(mod: types.ModuleType, **changes: Any) -> Any:
    base = {
        "name": "league-spot-1",
        "schedule_start_ms": START,
        "schedule_every_ms": HOUR,
        "horizons_hours": (1, 4, 24),
        "scan_n": 10,
        "event_cap": 5,
        "scan_rule_version": "scan/1",
        "scan_rule_hash": H1,
        "event_rule_version": "event/1",
        "event_rule_hash": H2,
        "watchlist": ("BTC", "ETH"),
        "oracle_pair": "A",
        "primary_venue": "binance",
        "second_venue": "okx",
        "quote_class": "USDT",
        "instrument_kind": "spot",
        "windows": ((60, 120), (3600, 40)),
        "wait_s": 30,
        "freshness_ms": (("EXCHANGE", 60_000), ("NEWS", 900_000), ("RYO", 300_000)),
        "required_sources": ("hoshitori.features",),
        "arms": (("L0", H3),),
        "root_version": 1,
        "root_sha256": H1,
        "clock_offset_limit_ms": 2_000,
        "snapshot_freshness_ms": 600_000,
    }
    base.update(changes)
    return mod.Protocol(**base)


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
        except self.mod.ProtocolError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("format", mod.FORMAT, 1)
    c.eq("arms", mod.ARMS, ("L0",))
    c.eq("pairs", mod.PAIRS, ("A",))
    c.eq("kinds", mod.KINDS, ("spot",))
    c.eq("a minute", mod.MINUTE_MS, 60_000)
    return c.bad


def check_document(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    p = make(mod)
    body = p.body()
    c.eq("the hash is of the canonical body", p.sha256, hashlib.sha256(canonical.dumps(body)).hexdigest())
    c.eq(
        "every top member",
        sorted(body),
        ["arms", "collect", "format", "horizons_hours", "manifest", "name", "oracle", "schedule", "timing"],
    )
    c.eq("schedule", body["schedule"], {"start_ms": START, "every_ms": HOUR})
    c.eq(
        "manifest",
        body["manifest"],
        {
            "scan_n": 10,
            "event_cap": 5,
            "scan_rule": {"version": "scan/1", "hash": H1},
            "event_rule": {"version": "event/1", "hash": H2},
            "watchlist": ["BTC", "ETH"],
        },
    )
    c.eq("oracle", body["oracle"], {"pair": "A", "primary": "binance", "second": "okx", "quote_class": "USDT", "kind": "spot"})
    c.eq(
        "collect",
        body["collect"],
        {
            "windows": [{"interval_s": 60, "bars": 120}, {"interval_s": 3600, "bars": 40}],
            "wait_s": 30,
            "freshness_ms": {"EXCHANGE": 60_000, "NEWS": 900_000, "RYO": 300_000},
            "required_sources": ["hoshitori.features"],
        },
    )
    c.eq("arms", body["arms"], {"L0": H3})
    c.eq("timing", body["timing"], {"root_version": 1, "root_sha256": H1, "clock_offset_limit_ms": 2_000, "snapshot_freshness_ms": 600_000})
    c.eq("horizons", body["horizons_hours"], [1, 4, 24])
    c.eq(
        "watchlist hash is of the canonical list",
        p.watchlist_hash,
        hashlib.sha256(canonical.dumps({"watchlist": ["BTC", "ETH"]})).hexdigest(),
    )
    c.eq("another name is another protocol", make(mod, name="other").sha256 == p.sha256, False)
    c.eq("another arm mapping is another protocol", make(mod, arms=(("L0", H1),)).sha256 == p.sha256, False)
    rules = p.manifest_rules()
    c.eq(
        "manifest rules",
        (
            rules.scan_rule_version,
            rules.scan_rule_hash,
            rules.scan_n,
            rules.event_rule_version,
            rules.event_rule_hash,
            rules.event_cap,
            rules.watchlist_config_hash,
        ),
        ("scan/1", H1, 10, "event/1", H2, 5, p.watchlist_hash),
    )
    c.eq("windows", [(w.interval_s, w.bars) for w in p.collect_windows()], [(60, 120), (3600, 40)])
    c.eq("mapping of L0", p.mapping_of("L0"), H3)
    c.refuses("mapping of an unknown arm", lambda: p.mapping_of("L9"), "the protocol has no arm 'L9'")
    return c.bad


def check_barrier_pins(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    plain = make(mod)
    c.eq("a protocol with no barrier arm has no such member", "barrier_arms" in plain.body(), False)
    c.eq("and its hash is that of the body it always had", plain.sha256, hashlib.sha256(canonical.dumps(plain._core())).hexdigest())
    pinned = make(mod, barrier_arms=(("L0", H2),))
    c.eq("a pinned barrier mapping is in the body", pinned.body()["barrier_arms"], {"L0": H2})
    c.eq("and changes the hash", pinned.sha256 != plain.sha256, True)
    c.eq("another mapping is another hash", make(mod, barrier_arms=(("L0", H1),)).sha256 != pinned.sha256, True)
    raw = mod.dump(pinned)
    c.eq("a file with it reads back whole", (mod.load(raw), mod.dump(mod.load(raw))), (pinned, raw))
    c.eq("the pin is found by arm", pinned.barrier_mapping_of("L0"), H2)
    c.refuses("an arm with no barrier pin", lambda: plain.barrier_mapping_of("L0"), "the protocol has no barrier arm 'L0'")
    c.eq("the directional mapping is not touched", pinned.mapping_of("L0"), H3)
    c.refuses(
        "an arm the protocol does not have",
        lambda: make(mod, barrier_arms=(("L9", H2),)),
        "the barrier arms are some of the arms, sorted and once each",
    )
    c.refuses(
        "an arm twice",
        lambda: make(mod, barrier_arms=(("L0", H2), ("L0", H2))),
        "the barrier arms are some of the arms, sorted and once each",
    )
    c.refuses(
        "a hash that is not one",
        lambda: make(mod, barrier_arms=(("L0", "xyz"),)),
        "an arm's barrier mapping hash is 64 lower-case hexadecimal characters",
    )
    record = canonical.parse_record(raw)

    def tampered(change: Any) -> bytes:
        copy = canonical.parse_record(raw)
        change(copy)
        return canonical.dumps(copy)

    c.refuses(
        "an empty member, which the body would not have written",
        lambda: mod.load(tampered(lambda r: r.update(barrier_arms={}))),
        "the protocol file's hash is not the hash of its contents",
    )
    c.refuses(
        "a member that is not an object",
        lambda: mod.load(tampered(lambda r: r.update(barrier_arms=[1]))),
        "horizons, the watchlist, windows and sources are lists, and freshness and arms are objects",
    )
    c.eq(
        "the keys of a pinned file",
        sorted(record),
        sorted(
            ["format", "name", "schedule", "horizons_hours", "manifest", "oracle", "collect", "arms", "timing", "barrier_arms", "sha256"]
        ),
    )
    return c.bad


def check_roundtrip(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    p = make(mod)
    raw = mod.dump(p)
    c.eq("a file reads back to the same protocol", mod.load(raw), p)
    c.eq("and writes the same bytes", mod.dump(mod.load(raw)), raw)
    c.eq("the file carries the hash", canonical.parse_record(raw)["sha256"], p.sha256)
    record = canonical.parse_record(raw)

    def edit(path: list[str], value: Any) -> bytes:
        copy = canonical.parse_record(raw)
        node: Any = copy
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        return canonical.dumps(copy)

    c.refuses(
        "a changed value under the old hash",
        lambda: mod.load(edit(["name"], "other")),
        "the protocol file's hash is not the hash of its contents",
    )
    c.refuses("a wrong hash", lambda: mod.load(edit(["sha256"], H1)), "the protocol file's hash is not the hash of its contents")
    c.refuses(
        "no hash",
        lambda: mod.load(canonical.dumps({k: v for k, v in record.items() if k != "sha256"})),
        "the protocol file's hash is not the hash of its contents",
    )
    c.refuses("not canonical", lambda: mod.load(raw.replace(b'"format":1', b'"format": 1')), "the protocol file is not in canonical form")
    # other bytes for the same protocol: members the writer leaves out when they are empty, written empty; each loaded before this check
    # as the same protocol under the same hash, so one pre-registered protocol had more than one file
    for member, empty in (("optional_sources", []), ("barrier_arms", {}), ("model_arms", {})):
        c.refuses(
            f"an empty {member} member, which the writer leaves out",
            lambda member=member, empty=empty: mod.load(canonical.dumps({**record, member: empty})),
            "the protocol file is not the one its writer makes for what it says",
        )
    c.refuses(
        "not a record",
        lambda: mod.load(b"[1]"),
        "the protocol file is not a canonical record: " + _why(lambda: canonical.parse_record(b"[1]")),
    )
    c.refuses("another format", lambda: mod.load(edit(["format"], 2)), "the protocol format is 1")
    for member in ("name", "schedule", "horizons_hours", "manifest", "oracle", "collect", "arms", "timing"):
        c.refuses(
            f"missing {member}",
            lambda m=member: mod.load(canonical.dumps({k: v for k, v in record.items() if k != m})),
            "the protocol has the members "
            + str(sorted(["format", "name", "schedule", "horizons_hours", "manifest", "oracle", "collect", "arms", "timing"])),
        )
    c.refuses(
        "an extra member",
        lambda: mod.load(canonical.dumps({**record, "extra": 1})),
        "the protocol has the members "
        + str(sorted(["format", "name", "schedule", "horizons_hours", "manifest", "oracle", "collect", "arms", "timing"])),
    )
    c.refuses(
        "schedule with a member short",
        lambda: mod.load(edit(["schedule"], {"start_ms": START})),
        "the schedule has the members ['every_ms', 'start_ms']",
    )
    c.refuses(
        "manifest settings with an extra",
        lambda: mod.load(canonical.dumps({**record, "manifest": {**record["manifest"], "x": 1}})),
        "the manifest settings has the members ['event_cap', 'event_rule', 'scan_n', 'scan_rule', 'watchlist']",
    )  # type: ignore[dict-item]
    c.refuses(
        "a rule with a member short",
        lambda: mod.load(edit(["manifest", "scan_rule"], {"version": "scan/1"})),
        "the scan rule has the members ['hash', 'version']",
    )
    c.refuses(
        "an event rule with a member short",
        lambda: mod.load(edit(["manifest", "event_rule"], {"version": "e"})),
        "the event rule has the members ['hash', 'version']",
    )
    c.refuses(
        "oracle short",
        lambda: mod.load(edit(["oracle"], {"pair": "A"})),
        "the oracle has the members ['kind', 'pair', 'primary', 'quote_class', 'second']",
    )
    c.refuses(
        "collect short",
        lambda: mod.load(edit(["collect"], {"wait_s": 3})),
        "the collection settings has the members ['freshness_ms', 'required_sources', 'wait_s', 'windows']",
    )
    c.refuses(
        "timing short",
        lambda: mod.load(edit(["timing"], {"root_version": 1})),
        "the timing settings has the members ['clock_offset_limit_ms', 'root_sha256', 'root_version', 'snapshot_freshness_ms']",
    )
    c.refuses(
        "a window short",
        lambda: mod.load(edit(["collect", "windows"], [{"interval_s": 60}])),
        "a window has the members ['bars', 'interval_s']",
    )
    shape = "horizons, the watchlist, windows and sources are lists, and freshness and arms are objects"
    c.refuses("horizons not a list", lambda: mod.load(edit(["horizons_hours"], "x")), shape)
    c.refuses("watchlist not a list", lambda: mod.load(edit(["manifest", "watchlist"], "x")), shape)
    c.refuses("windows not a list", lambda: mod.load(edit(["collect", "windows"], "x")), shape)
    c.refuses("sources not a list", lambda: mod.load(edit(["collect", "required_sources"], "x")), shape)
    c.refuses("freshness not an object", lambda: mod.load(edit(["collect", "freshness_ms"], [1])), shape)
    c.refuses("arms not an object", lambda: mod.load(edit(["arms"], [1])), shape)
    return c.bad


def _why(call: Callable[[], object]) -> str:
    try:
        call()
    except canonical.CanonicalError as exc:
        return str(exc)
    return ""


def check_validation(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    hx = "64 lower-case hexadecimal characters"
    c.refuses("empty name", lambda: make(mod, name=""), "the name is a non-empty string of at most 200 characters")
    c.refuses("long name", lambda: make(mod, name="x" * 201), "the name is a non-empty string of at most 200 characters")
    make(mod, name="x" * 200)
    c.refuses(
        "a name that is not canonical text",
        lambda: make(mod, name="\ud800"),
        "the name is not valid canonical text: " + _why(lambda: canonical.dumps({"v": "\ud800"})),
    )
    c.refuses("negative start", lambda: make(mod, schedule_start_ms=-1), "the schedule start is a whole number from 0 to 253402300799999")
    make(mod, schedule_start_ms=0)
    make(mod, schedule_start_ms=253_402_300_799_999)
    c.refuses(
        "start past the end of year 9999",
        lambda: make(mod, schedule_start_ms=253_402_300_800_000),
        "the schedule start is a whole number from 0 to 253402300799999",
    )
    c.refuses("a bool start", lambda: make(mod, schedule_start_ms=True), "the schedule start is a whole number from 0 to 253402300799999")
    c.refuses(
        "every under a minute",
        lambda: make(mod, schedule_every_ms=59_000),
        "the interval between slots is a whole number from 60000 to 31622400000",
    )
    c.refuses(
        "every over a year",
        lambda: make(mod, schedule_every_ms=366 * 86_400_000 + MINUTE),
        "the interval between slots is a whole number from 60000 to 31622400000",
    )
    make(mod, schedule_every_ms=MINUTE)
    make(mod, schedule_every_ms=366 * 86_400_000)
    c.refuses("not whole minutes", lambda: make(mod, schedule_every_ms=90_000), "the interval between slots is a whole number of minutes")
    msg = "horizons are some of (1, 4, 24) hours, sorted and once each"
    c.refuses("no horizons", lambda: make(mod, horizons_hours=()), msg)
    c.refuses("unsorted horizons", lambda: make(mod, horizons_hours=(4, 1)), msg)
    c.refuses("repeated horizons", lambda: make(mod, horizons_hours=(1, 1)), msg)
    c.refuses("unknown horizon", lambda: make(mod, horizons_hours=(2,)), msg)
    make(mod, horizons_hours=(24,))
    c.refuses("scan n zero", lambda: make(mod, scan_n=0), "the scan cutoff is a whole number from 1 to 1000")
    c.refuses("scan n large", lambda: make(mod, scan_n=1001), "the scan cutoff is a whole number from 1 to 1000")
    make(mod, scan_n=1000)
    c.refuses("negative cap", lambda: make(mod, event_cap=-1), "the event cap is a whole number from 0 to 1000")
    c.refuses("big cap", lambda: make(mod, event_cap=1001), "the event cap is a whole number from 0 to 1000")
    make(mod, event_cap=0)
    make(mod, event_cap=1000)
    c.refuses(
        "scan version", lambda: make(mod, scan_rule_version=""), "the scan rule version is a non-empty string of at most 200 characters"
    )
    c.refuses(
        "event version", lambda: make(mod, event_rule_version=""), "the event rule version is a non-empty string of at most 200 characters"
    )
    c.refuses("scan hash", lambda: make(mod, scan_rule_hash="A" * 64), f"the scan rule hash is {hx}")
    c.refuses("scan hash short", lambda: make(mod, scan_rule_hash="a" * 63), f"the scan rule hash is {hx}")
    c.refuses("one bad character in a hash", lambda: make(mod, scan_rule_hash="a" * 63 + "g"), f"the scan rule hash is {hx}")
    c.refuses("a tilde in a hash", lambda: make(mod, scan_rule_hash="a" * 63 + "~"), f"the scan rule hash is {hx}")
    make(mod, scan_rule_hash="0123456789abcdef" * 4)
    c.refuses("scan hash not text", lambda: make(mod, scan_rule_hash=5), f"the scan rule hash is {hx}")
    c.refuses("event hash", lambda: make(mod, event_rule_hash="g" * 64), f"the event rule hash is {hx}")
    wl = "the watchlist is upper-case symbols, sorted and once each"
    c.refuses("unsorted watchlist", lambda: make(mod, watchlist=("ETH", "BTC")), wl)
    c.refuses("repeated watchlist", lambda: make(mod, watchlist=("BTC", "BTC")), wl)
    c.refuses("lower-case symbol", lambda: make(mod, watchlist=("btc",)), wl)
    c.refuses("empty symbol", lambda: make(mod, watchlist=("",)), wl)
    c.refuses("long symbol", lambda: make(mod, watchlist=("A" * 41,)), wl)
    make(mod, watchlist=("A" * 40,))
    make(mod, watchlist=())
    pk = "the oracle pair is one of ('A',) and the instrument kind one of ('spot',)"
    c.refuses("pair B", lambda: make(mod, oracle_pair="B"), pk)
    c.refuses("perp kind", lambda: make(mod, instrument_kind="perp"), pk)
    c.refuses("empty primary", lambda: make(mod, primary_venue=""), "the primary venue is a non-empty string of at most 200 characters")
    c.refuses("empty second", lambda: make(mod, second_venue=""), "the second venue is a non-empty string of at most 200 characters")
    c.refuses("empty quote class", lambda: make(mod, quote_class=""), "the quote class is a non-empty string of at most 200 characters")
    c.refuses("one venue twice", lambda: make(mod, second_venue="binance"), "the two oracles are different venues")
    wm = "there is at least one window and no interval twice"
    c.refuses("no windows", lambda: make(mod, windows=()), wm)
    c.refuses("same interval twice", lambda: make(mod, windows=((60, 1), (60, 2))), wm)
    c.refuses("interval zero", lambda: make(mod, windows=((0, 1),)), "a window's interval is a whole number from 1 to 86400")
    c.refuses("interval over a day", lambda: make(mod, windows=((86_401, 1),)), "a window's interval is a whole number from 1 to 86400")
    make(mod, windows=((86_400, 300),))
    c.refuses("bars zero", lambda: make(mod, windows=((60, 0),)), "a window's bars is a whole number from 1 to 300")
    c.refuses("bars over 300", lambda: make(mod, windows=((60, 301),)), "a window's bars is a whole number from 1 to 300")
    c.refuses("wait zero", lambda: make(mod, wait_s=0), "the wait is a whole number from 1 to 3600")
    c.refuses("wait over an hour", lambda: make(mod, wait_s=3601), "the wait is a whole number from 1 to 3600")
    make(mod, wait_s=3600)
    fm = "freshness limits are by family kind, sorted and once each"
    c.refuses("unsorted freshness", lambda: make(mod, freshness_ms=(("RYO", 1), ("NEWS", 1))), fm)
    c.refuses("repeated freshness", lambda: make(mod, freshness_ms=(("RYO", 1), ("RYO", 2))), fm)
    c.refuses("unknown family", lambda: make(mod, freshness_ms=(("ZZ", 1),)), fm)
    c.refuses("zero freshness", lambda: make(mod, freshness_ms=(("RYO", 0),)), "a freshness limit is a whole number from 1 to 604800000")
    c.refuses(
        "freshness over a week",
        lambda: make(mod, freshness_ms=(("RYO", 604_800_001),)),
        "a freshness limit is a whole number from 1 to 604800000",
    )
    make(mod, freshness_ms=())
    make(mod, freshness_ms=tuple((kind, 1) for kind in sorted(["RYO", "EXCHANGE", "CHAIN", "PRIMARY_DOC", "NEWS", "SOCIAL", "DERIVED"])))
    make(mod, freshness_ms=(("RYO", 604_800_000),))
    c.refuses("unsorted sources", lambda: make(mod, required_sources=("b", "a")), "required sources are sorted and once each")
    c.refuses("repeated sources", lambda: make(mod, required_sources=("a", "a")), "required sources are sorted and once each")
    c.refuses(
        "empty source", lambda: make(mod, required_sources=("",)), "a required source is a non-empty string of at most 200 characters"
    )
    make(mod, required_sources=())
    am = "the arms are some of ('L0',), sorted and once each"
    c.refuses("no arms", lambda: make(mod, arms=()), am)
    c.refuses("unknown arm", lambda: make(mod, arms=(("L1", H1),)), am)
    c.refuses("repeated arm", lambda: make(mod, arms=(("L0", H1), ("L0", H2))), am)
    c.refuses("arm hash", lambda: make(mod, arms=(("L0", "x"),)), f"an arm's mapping hash is {hx}")
    c.refuses("root version zero", lambda: make(mod, root_version=0), "the trusted-root version is a whole number from 1 to 1000000000")
    c.refuses(
        "root version large", lambda: make(mod, root_version=10**9 + 1), "the trusted-root version is a whole number from 1 to 1000000000"
    )
    c.refuses("root hash", lambda: make(mod, root_sha256="z" * 64), f"the trusted-root hash is {hx}")
    c.refuses("offset limit zero", lambda: make(mod, clock_offset_limit_ms=0), "the clock offset limit is a whole number from 1 to 3600000")
    c.refuses(
        "offset limit over an hour",
        lambda: make(mod, clock_offset_limit_ms=3_600_001),
        "the clock offset limit is a whole number from 1 to 3600000",
    )
    make(mod, clock_offset_limit_ms=3_600_000)
    c.refuses(
        "snapshot limit zero",
        lambda: make(mod, snapshot_freshness_ms=0),
        "the snapshot freshness limit is a whole number from 1 to 86400000",
    )
    c.refuses(
        "snapshot limit over a day",
        lambda: make(mod, snapshot_freshness_ms=86_400_001),
        "the snapshot freshness limit is a whole number from 1 to 86400000",
    )
    make(mod, snapshot_freshness_ms=86_400_000)
    return c.bad


def check_standard(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    p = mod.standard(
        name="first", start_ms=START, every_ms=HOUR, root_version=3, root_sha256=H1, mappings={"L0": H3}, watchlist=("ETH", "BTC", "ETH")
    )
    scan = hashlib.sha256(
        canonical.dumps({"rule": "ryo_scan", "version": "scan/1", "text": "the first N rows of RYO's own scan, by its rank"})
    ).hexdigest()
    event = hashlib.sha256(
        canonical.dumps(
            {
                "rule": "event",
                "version": "event/1",
                "text": "registry changes known since the last discovery, earliest first, at most the cap",
            }
        )
    ).hexdigest()
    c.eq("the rules are named and hashed", (mod.SCAN_RULE, mod.EVENT_RULE), (("scan/1", scan), ("event/1", event)))
    c.eq("name and schedule", (p.name, p.schedule_start_ms, p.schedule_every_ms), ("first", START, HOUR))
    c.eq("the three horizons", p.horizons_hours, (1, 4, 24))
    c.eq(
        "the manifest settings",
        (p.scan_n, p.event_cap, p.scan_rule_version, p.scan_rule_hash, p.event_rule_version, p.event_rule_hash),
        (10, 5, "scan/1", scan, "event/1", event),
    )
    c.eq("a watchlist is sorted and once each", p.watchlist, ("BTC", "ETH"))
    names = ("SOL", "ADA", "XRP", "DOGE", "LINK", "AVAX", "BNB", "DOT", "LTC", "TRX", "ATOM", "NEAR", "ADA", "SOL")
    many = mod.standard(
        name="first",
        start_ms=START,
        every_ms=HOUR,
        root_version=3,
        root_sha256=H1,
        mappings=dict.fromkeys(reversed(mod.ARMS), H3),
        watchlist=names,
    )
    c.eq("a long watchlist is sorted, once each", many.watchlist, tuple(sorted(set(names))))
    c.eq("and the arms are sorted", many.arms, tuple((a, H3) for a in sorted(mod.ARMS)))
    c.eq(
        "the oracle",
        (p.oracle_pair, p.primary_venue, p.second_venue, p.quote_class, p.instrument_kind),
        ("A", "binance-spot", "okx", "USDT", "spot"),
    )
    c.eq(
        "the collection",
        (p.windows, p.wait_s, p.freshness_ms, p.required_sources),
        (((60, 120), (3600, 40)), 30, (("EXCHANGE", 60_000), ("NEWS", 900_000), ("RYO", 300_000)), ("hoshitori.features",)),
    )
    c.eq("the arms and the root", (p.arms, p.root_version, p.root_sha256), ((("L0", H3),), 3, H1))
    c.eq("the timing limits", (p.clock_offset_limit_ms, p.snapshot_freshness_ms), (2_000, 600_000))
    c.eq("it is a protocol that can be written and read", mod.load(mod.dump(p)), p)
    other = mod.standard(
        name="first",
        start_ms=START,
        every_ms=HOUR,
        root_version=3,
        root_sha256=H1,
        mappings={"L0": H3},
        watchlist=("BTC",),
        primary_venue="a",
        second_venue="b",
    )
    c.eq("the venues can be named", (other.primary_venue, other.second_venue), ("a", "b"))
    both = mod.standard(
        name="first",
        start_ms=START,
        every_ms=HOUR,
        root_version=3,
        root_sha256=H1,
        mappings={"L0": H3},
        watchlist=("BTC",),
        barrier_mappings={"L0": H2},
    )
    c.eq("a barrier mapping can be pinned", both.barrier_arms, (("L0", H2),))
    c.eq("and without one there is none", p.barrier_arms, ())
    return c.bad


def check_slots(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    p = make(mod)
    c.eq("slot 0", p.slot(0), START)
    c.eq("slot 5", p.slot(5), START + 5 * HOUR)
    c.refuses("negative slot", lambda: p.slot(-1), "a slot number is a whole number from 0 to 10000000")
    c.refuses("a slot beyond the bound", lambda: p.slot(10**7 + 1), "a slot number is a whole number from 0 to 10000000")
    p.slot(10**7)
    c.eq("the start is a slot", p.is_slot(START), True)
    c.eq("so is every interval after it", [p.is_slot(START + k * HOUR) for k in (1, 2, 50)], [True] * 3)
    c.eq(
        "a moment after or before a slot is not one",
        [p.is_slot(START + 1), p.is_slot(START + HOUR - 1), p.is_slot(START + 90 * MINUTE)],
        [False] * 3,
    )
    c.eq("nothing before the start is a slot", [p.is_slot(START - HOUR), p.is_slot(START - 1), p.is_slot(0)], [False] * 3)
    c.eq("no slot through a moment before the start", p.slots_through(START - 1), ())
    c.eq("the first slot, at the very moment it comes", p.slots_through(START), (START,))
    c.eq("and still only it a millisecond before the next", p.slots_through(START + HOUR - 1), (START,))
    c.eq("the next when its moment comes", p.slots_through(START + HOUR), (START, START + HOUR))
    c.eq("a day of them", p.slots_through(START + 23 * HOUR + 59 * MINUTE), tuple(START + k * HOUR for k in range(24)))
    c.eq(
        "with a stride that does not divide the hour",
        make(mod, schedule_every_ms=7 * MINUTE).slots_through(START + 15 * MINUTE),
        (START, START + 7 * MINUTE, START + 14 * MINUTE),
    )
    c.eq("none between equal times", p.slots_between(START, START), ())
    c.eq("a window of exactly one slot, open at the left and closed at the right", p.slots_between(START, START + HOUR), (START + HOUR,))
    c.eq("a slot at the left edge is not in the window", p.slots_between(START + HOUR, START + HOUR + 1), ())
    c.eq("a window from just before a slot", p.slots_between(START - 1, START), (START,))
    c.eq("a window before the first slot has none", p.slots_between(START - 2 * HOUR, START - HOUR), ())
    c.eq(
        "several, in order",
        p.slots_between(START + 30 * MINUTE, START + 3 * HOUR + 30 * MINUTE),
        (START + HOUR, START + 2 * HOUR, START + 3 * HOUR),
    )
    c.eq("the first slot is in a window that starts before it", p.slots_between(0, START + HOUR), (START, START + HOUR))
    c.eq("a window starting mid-slot", p.slots_between(START + 1, START + HOUR - 1), ())
    c.eq("a window starting exactly one millisecond before a slot", p.slots_between(START + HOUR - 1, START + HOUR), (START + HOUR,))
    c.refuses("reversed", lambda: p.slots_between(5, 4), "the window starts no later than it ends")
    q = make(mod, schedule_every_ms=7 * MINUTE, schedule_start_ms=START + 3 * MINUTE)
    c.eq(
        "a stride that does not divide the hour",
        q.slots_between(START, START + 20 * MINUTE),
        (START + 3 * MINUTE, START + 10 * MINUTE, START + 17 * MINUTE),
    )
    c.eq(
        "slots of a schedule that does not start on its interval",
        [q.is_slot(START + 3 * MINUTE + k * 7 * MINUTE) for k in (0, 1, 4)],
        [True] * 3,
    )
    c.eq("and the moments between them", [q.is_slot(START + 7 * MINUTE), q.is_slot(START + 3 * MINUTE + 8 * MINUTE)], [False] * 2)
    c.eq("a protocol is frozen", _refused_attr(p), True)
    c.eq("replace keeps the checks", _refused(lambda: replace(p, name="")), True)
    return c.bad


def _refused_attr(p: Any) -> bool:
    try:
        p.name = "x"
    except AttributeError:
        return True
    return False


def _refused(call: Callable[[], object]) -> bool:
    try:
        call()
    except ValueError:
        return True
    return False


def check_optional_sources(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    plain = make(mod)
    c.eq("a protocol that names none has no such member", ("optional_sources" in plain.body(), plain.optional_sources), (False, ()))
    c.eq("and the hash it always had", plain.sha256, hashlib.sha256(canonical.dumps(plain._core())).hexdigest())
    named = make(mod, optional_sources=("binance-funding",))
    c.eq("a named source is in the body", named.body()["optional_sources"], ["binance-funding"])
    c.eq("and changes the hash", named.sha256 != plain.sha256, True)
    c.eq("another source is another hash", make(mod, optional_sources=("kraken-funding",)).sha256 != named.sha256, True)
    raw = mod.dump(named)
    c.eq("a file with it reads back whole", (mod.load(raw), mod.dump(mod.load(raw))), (named, raw))
    c.eq(
        "the keys of the file",
        sorted(canonical.parse_record(raw)),
        sorted(
            [
                "format",
                "name",
                "schedule",
                "horizons_hours",
                "manifest",
                "oracle",
                "collect",
                "arms",
                "timing",
                "optional_sources",
                "sha256",
            ]
        ),
    )
    rule = "optional sources are sorted, once each, and none of them is also required"
    c.refuses("twice", lambda: make(mod, optional_sources=("a", "a")), rule)
    c.refuses("out of order", lambda: make(mod, optional_sources=("b", "a")), rule)
    c.refuses("also required", lambda: make(mod, optional_sources=("hoshitori.features",)), rule)
    c.eq("two, in order", make(mod, optional_sources=("a", "b")).optional_sources, ("a", "b"))
    c.refuses("not text", lambda: make(mod, optional_sources=("",)), "an optional source is a non-empty string of at most 200 characters")
    tampered_raw = canonical.parse_record(raw)
    tampered_raw["optional_sources"] = []
    c.refuses(
        "an empty member, which the body would not have written",
        lambda: mod.load(canonical.dumps(tampered_raw)),
        "the protocol file's hash is not the hash of its contents",
    )
    tampered_raw["optional_sources"] = {"x": 1}
    c.refuses(
        "a member that is not a list",
        lambda: mod.load(canonical.dumps(tampered_raw)),
        "horizons, the watchlist, windows and sources are lists, and freshness and arms are objects",
    )
    std = mod.standard(
        name="n",
        start_ms=START,
        every_ms=HOUR,
        root_version=1,
        root_sha256=H1,
        mappings={"L0": H3},
        watchlist=("BTC",),
        optional_sources=(*reversed([f"source-{n:02d}" for n in range(12)]), "source-03"),
    )
    c.eq(
        "the standard protocol sorts and dedupes them, whatever order a set gives",
        std.optional_sources,
        tuple(f"source-{n:02d}" for n in range(12)),
    )
    c.eq(
        "and by default has none",
        mod.standard(
            name="n", start_ms=START, every_ms=HOUR, root_version=1, root_sha256=H1, mappings={"L0": H3}, watchlist=("BTC",)
        ).optional_sources,
        (),
    )
    return c.bad


def check_model_arms(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    plain = make(mod)

    def mk(**changes: Any) -> Any:
        return make(mod, snapshot_freshness_ms=900_000, **changes)

    c.eq("a protocol with no model arm has no such member", "model_arms" in plain.body(), False)
    c.eq("the arms a model may be", mod.MODEL_ARMS, ("L1", "L1x", "L2"))
    arms = (("L1", H1, ((1, 200_000), (4, 600_000))), ("L1x", H3, ((4, 780_000),)), ("L2", H2, ((4, 700_000),)))
    pinned = mk(model_arms=arms)
    c.eq(
        "the members of the body",
        pinned.body()["model_arms"],
        {
            "L1": {"version": H1, "cutoffs_ms": {"1": 200_000, "4": 600_000}},
            "L1x": {"version": H3, "cutoffs_ms": {"4": 780_000}},
            "L2": {"version": H2, "cutoffs_ms": {"4": 700_000}},
        },
    )
    c.eq("and the hash is another", pinned.sha256 != plain.sha256, True)
    c.eq(
        "one cutoff changed is another hash",
        mk(model_arms=(("L1", H1, ((1, 200_001), (4, 600_000))), *arms[1:])).sha256 != pinned.sha256,
        True,
    )
    c.eq(
        "one version changed is another hash",
        mk(model_arms=(("L1", H2, ((1, 200_000), (4, 600_000))), *arms[1:])).sha256 != pinned.sha256,
        True,
    )
    raw = mod.dump(pinned)
    c.eq("a file with them reads back whole", (mod.load(raw), mod.dump(mod.load(raw))), (pinned, raw))
    c.eq("the pin and the cutoffs are found by arm", pinned.model_arm_of("L1"), (H1, ((1, 200_000), (4, 600_000))))
    c.eq(
        "a cutoff is found by arm and hours",
        (pinned.cutoff_ms("L1", 1), pinned.cutoff_ms("L1", 4), pinned.cutoff_ms("L2", 4)),
        (200_000, 600_000, 700_000),
    )
    c.eq("an arm not admitted to a horizon has no cutoff there", (pinned.cutoff_ms("L2", 1), pinned.cutoff_ms("L2", 24)), (None, None))
    c.refuses("an arm the protocol does not have", lambda: plain.model_arm_of("L1"), "the protocol has no model arm 'L1'")
    c.eq("the directional mapping is not touched", (pinned.mapping_of("L0"), pinned.barrier_arms), (H3, ()))
    shape = "the model arms are some of ('L1', 'L1x', 'L2'), sorted and once each"
    c.refuses("an arm that is not a model arm", lambda: mk(model_arms=(("L0", H1, ((1, 1000),)),)), shape)
    c.refuses("an arm twice", lambda: mk(model_arms=(("L1", H1, ((1, 1000),)), ("L1", H1, ((1, 1000),)))), shape)
    c.refuses("arms out of order", lambda: mk(model_arms=(("L2", H1, ((1, 1000),)), ("L1", H1, ((1, 1000),)))), shape)
    c.refuses(
        "a version that is not a hash",
        lambda: mk(model_arms=(("L1", "xyz", ((1, 1000),)),)),
        "a model arm's version hash is 64 lower-case hexadecimal characters",
    )
    horizons = "a model arm is admitted to some of the protocol's horizons, sorted and once each"
    c.refuses("no horizon", lambda: mk(model_arms=(("L1", H1, ()),)), horizons)
    c.refuses("a horizon twice", lambda: mk(model_arms=(("L1", H1, ((1, 1000), (1, 1000))),)), horizons)
    c.refuses("horizons out of order", lambda: mk(model_arms=(("L1", H1, ((4, 1000), (1, 1000))),)), horizons)
    c.refuses("a horizon the protocol does not have", lambda: mk(horizons_hours=(1, 4), model_arms=(("L1", H1, ((24, 1000),)),)), horizons)
    # a cutoff leaves room for the attestation inside the gap bound: 6 minutes for an hour, 15 for the others, less 120 seconds
    c.eq("the largest cutoff for an hour", mk(model_arms=(("L1", H1, ((1, 240_000),)),)).cutoff_ms("L1", 1), 240_000)
    c.eq("and for four hours", mk(model_arms=(("L1", H1, ((4, 780_000),)),)).cutoff_ms("L1", 4), 780_000)
    c.eq("and for a day", mk(model_arms=(("L1", H1, ((24, 780_000),)),)).cutoff_ms("L1", 24), 780_000)
    cutoff = "a decision cutoff is a whole number from 1 to {}"
    c.refuses("an hour cutoff one millisecond too long", lambda: mk(model_arms=(("L1", H1, ((1, 240_001),)),)), cutoff.format(240_000))
    c.refuses("a four-hour cutoff too long", lambda: mk(model_arms=(("L1", H1, ((4, 780_001),)),)), cutoff.format(780_000))
    c.refuses("a cutoff of nothing", lambda: mk(model_arms=(("L1", H1, ((1, 0),)),)), cutoff.format(240_000))
    c.refuses("a cutoff that is a flag", lambda: mk(model_arms=(("L1", H1, ((1, True),)),)), cutoff.format(240_000))
    fits = "a decision cutoff leaves the snapshot time to be recorded before it is too old to commit"
    c.eq("a cutoff that leaves ten seconds", make(mod, model_arms=(("L1", H1, ((4, 590_000),)),)).cutoff_ms("L1", 4), 590_000)
    c.refuses("a cutoff that leaves a millisecond less", lambda: make(mod, model_arms=(("L1", H1, ((4, 590_001),)),)), fits)
    c.eq(
        "a snapshot that stays fresh longer allows a longer one", mk(model_arms=(("L1", H1, ((4, 780_000),)),)).cutoff_ms("L1", 4), 780_000
    )
    # the control belongs to the debate
    only = "the matched-compute control runs only where the debate it is matched to runs"
    c.refuses("a control with no debate", lambda: mk(model_arms=(("L1x", H1, ((4, 1000),)),)), only)
    c.refuses("a control where the debate does not run", lambda: mk(model_arms=(("L1x", H1, ((1, 1000),)), ("L2", H2, ((4, 1000),)))), only)
    c.refuses(
        "a control that cuts off before the debate",
        lambda: mk(model_arms=(("L1x", H1, ((4, 999),)), ("L2", H2, ((4, 1000),)))),
        "the control starts when the debate has finished, so its cutoff is no earlier than the debate's",
    )
    c.refuses(
        "a control that cuts off before the debate at only one of two horizons",
        lambda: mk(model_arms=(("L1x", H1, ((1, 100_000), (4, 2000))), ("L2", H2, ((1, 200_000), (4, 1000))))),
        "the control starts when the debate has finished, so its cutoff is no earlier than the debate's",
    )
    c.eq(
        "an arm admitted to every horizon of the protocol",
        mk(model_arms=(("L1", H1, ((1, 200_000), (4, 600_000), (24, 700_000))),)).model_arm_of("L1")[1],
        ((1, 200_000), (4, 600_000), (24, 700_000)),
    )
    c.eq(
        "a control with the same cutoff",
        mk(model_arms=(("L1x", H1, ((4, 1000),)), ("L2", H2, ((4, 1000),)))).cutoff_ms("L1x", 4),
        1000,
    )
    spread = mk(model_arms=(("L1", H1, ((1, 200_000), (4, 600_000), (24, 700_000))),))
    c.eq("a file keeps the hours in order whatever order the keys are written in", mod.load(mod.dump(spread)).model_arms, spread.model_arms)
    c.eq("and the hash of the file is the hash of the protocol", mod.load(mod.dump(spread)).sha256, spread.sha256)
    # the file
    record = canonical.parse_record(raw)

    def tampered(change: Any) -> bytes:
        copy = canonical.parse_record(raw)
        change(copy)
        return canonical.dumps(copy)

    c.refuses(
        "an empty member, which the body would not have written",
        lambda: mod.load(tampered(lambda r: r.update(model_arms={}))),
        "the protocol file's hash is not the hash of its contents",
    )
    c.refuses(
        "a member that is not an object",
        lambda: mod.load(tampered(lambda r: r.update(model_arms=[1]))),
        "horizons, the watchlist, windows and sources are lists, and freshness and arms are objects",
    )
    c.refuses(
        "an arm with another member",
        lambda: mod.load(tampered(lambda r: r["model_arms"]["L1"].update(extra=1))),
        "a model arm has the members ['cutoffs_ms', 'version']",
    )
    c.refuses(
        "cutoffs named by something that is not hours",
        lambda: mod.load(tampered(lambda r: r["model_arms"]["L1"].update(cutoffs_ms={"one": 1000}))),
        "a model arm's cutoffs are named by the hours of the horizon",
    )
    for spelling in ("01", "+1", " 1"):  # hours that int() reads as 1 and the writer writes as "1": the same hash, other bytes
        c.refuses(
            f"the hour of a cutoff written {spelling!r}",
            lambda spelling=spelling: mod.load(
                tampered(lambda r: r["model_arms"]["L1"].update(cutoffs_ms={spelling: 200_000, "4": 600_000}))
            ),
            "the protocol file is not the one its writer makes for what it says",
        )
    c.refuses(
        "cutoffs that are not an object",
        lambda: mod.load(tampered(lambda r: r["model_arms"]["L1"].update(cutoffs_ms=[1]))),
        "horizons, the watchlist, windows and sources are lists, and freshness and arms are objects",
    )
    c.eq(
        "the keys of a pinned file",
        sorted(record),
        sorted(["format", "name", "schedule", "horizons_hours", "manifest", "oracle", "collect", "arms", "timing", "model_arms", "sha256"]),
    )
    both = mk(barrier_arms=(("L0", H2),), model_arms=arms)
    c.eq("barrier and model arms together read back whole", mod.load(mod.dump(both)), both)
    std = mod.standard(
        name="s",
        start_ms=START,
        every_ms=HOUR,
        root_version=1,
        root_sha256=H1,
        mappings={"L0": H3},
        watchlist=(),
        model_arms=(("L2", H2, ((4, 1000),)), ("L1", H1, ((4, 1000),))),
    )
    c.eq("the standard protocol sorts them", [a for a, _, _ in std.model_arms], ["L1", "L2"])
    return c.bad


def check_practice_terms(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    defaults = mod.PRACTICE_DEFAULTS
    c.eq("the keys, in the order of the layout", [k for k, _ in defaults], list(mod.PRACTICE_KEYS))
    c.eq("thirteen terms", len(defaults), 13)
    c.eq("the layout is version one", mod.PRACTICE_VERSION, 1)
    plain = make(mod)
    c.eq("a protocol that records none has no such member", ("practice" in plain.body(), plain.practice_terms), (False, ()))
    c.eq("and the hash it always had", plain.sha256, hashlib.sha256(canonical.dumps(plain._core())).hexdigest())
    c.eq("its books run under the defaults", plain.practice_values(), {k: __import__("decimal").Decimal(v) for k, v in defaults})
    recorded = make(mod, practice_terms=defaults)
    c.eq("recorded terms are in the body", recorded.body()["practice"], {"version": 1, "terms": dict(defaults)})
    c.eq("and make another hash", recorded.sha256 != plain.sha256, True)
    raw = mod.dump(recorded)
    c.eq("a file with them reads back whole", (mod.load(raw), mod.dump(mod.load(raw))), (recorded, raw))
    c.eq("the terms read back in the order of the layout", mod.load(raw).practice_terms, defaults)
    changed = dict(defaults)
    changed["fee_bps"] = "20"
    other = make(mod, practice_terms=tuple(changed.items()))
    c.eq("another term is another hash", other.sha256 != recorded.sha256, True)
    c.eq("and is read back", mod.load(mod.dump(other)).practice_values()["fee_bps"], __import__("decimal").Decimal(20))
    head = "the practice terms are not accepted by the books: "
    layout = "the practice terms are " + ", ".join(mod.PRACTICE_KEYS) + ", in that order"
    for label, key, value, says in (
        (
            "a fee the amount could not carry",
            "fee_bps",
            "9000",
            head + "fee_bps is at most five thousand, so that even doubled a fee is at most the amount it is charged on",
        ),
        ("no equity", "equity", "0", head + "the opening equity is positive"),
        ("a request above all of equity", "request_weight", "1.5", head + "a request is a share above zero and at most one"),
        ("a request of nothing", "request_weight", "0", head + "a request is a share above zero and at most one"),
        ("a latency in fractions", "latency_ms", "1.5", head + "the latency is a whole number of milliseconds"),
        ("a negative", "slip_bps", "-1", head + "a term is a plain non-negative decimal"),
        ("not a number", "gross", "NaN", head + "a term is a plain non-negative decimal"),
        ("another script's digit", "equity", "\u0661", head + "a term is a plain non-negative decimal"),
        ("an exponent", "equity", "1e5", head + "a term is a plain non-negative decimal"),
        ("a limit above all of equity", "halt_at", "2", head + "halt_at is a share of equity above zero and at most one"),
        ("a limit of nothing", "per_token", "0", head + "per_token is a share of equity above zero and at most one"),
    ):
        terms = dict(defaults)
        terms[key] = value
        c.refuses(label, lambda t=terms: make(mod, practice_terms=tuple(t.items())), says)
    c.eq(
        "a request of all of equity is allowed",
        make(mod, practice_terms=tuple({**dict(defaults), "request_weight": "1"}.items())).practice_values()["request_weight"],
        1,
    )
    c.eq(
        "an equity of one is allowed",
        make(mod, practice_terms=tuple({**dict(defaults), "equity": "1"}.items())).practice_values()["equity"],
        1,
    )
    c.eq(
        "a request of almost nothing is allowed",
        make(mod, practice_terms=tuple({**dict(defaults), "request_weight": "0.0001"}.items())).practice_values()["request_weight"] > 0,
        True,
    )
    c.refuses("one term short", lambda: make(mod, practice_terms=tuple(defaults[:-1])), layout)
    c.refuses("one term over", lambda: make(mod, practice_terms=(*defaults, ("extra", "1"))), layout)
    c.refuses("in the wrong order", lambda: make(mod, practice_terms=tuple(reversed(defaults))), layout)
    record = canonical.parse_record(raw)
    for label, edit, says in (
        ("another version", lambda r: r["practice"].__setitem__("version", 2), "the practice terms are version 1"),
        (
            "a term that is not text",
            lambda r: r["practice"]["terms"].__setitem__("equity", 100000),
            "a practice term is a decimal written as text",
        ),
        ("a missing term", lambda r: r["practice"]["terms"].pop("net"), "the practice terms are " + ", ".join(mod.PRACTICE_KEYS)),
        ("an extra member", lambda r: r["practice"].__setitem__("extra", 1), "the practice terms has the members ['terms', 'version']"),
    ):
        edited = canonical.parse_record(raw)
        edit(edited)
        c.refuses(label, lambda e=edited: mod.load(canonical.dumps(e)), says)
    c.eq("the file's keys include the member", "practice" in record, True)
    return c.bad


def check_slot_plan(mod: types.ModuleType) -> list[str]:
    """The slots a candidate has, from the protocol alone: a rules arm has one for contract D at each horizon and one more for contract B
    if its barrier mapping is pinned; a model arm has one for contract D at each horizon it is admitted to. The cycle and the recovery of
    a lost cycle both take their slots from here, so that no slot the cycle creates is one that recovery does not look for."""
    c = Case(mod)
    plain = make(mod, arms=(("L0", H3),))
    c.eq(
        "a rules arm alone",
        plain.slot_plan(),
        (("L0", "D", 1), ("L0", "D", 4), ("L0", "D", 24)),
    )
    barrier = make(mod, arms=(("L0", H3),), barrier_arms=(("L0", H2),))
    c.eq(
        "with a barrier mapping, one contract B slot per horizon, beside its D slot",
        barrier.slot_plan(),
        (("L0", "D", 1), ("L0", "B", 1), ("L0", "D", 4), ("L0", "B", 4), ("L0", "D", 24), ("L0", "B", 24)),
    )
    models = make(mod, arms=(("L0", H3),), model_arms=(("L1", H1, ((4, 60_000), (24, 60_000))), ("L2", H2, ((24, 60_000),))))
    c.eq(
        "model arms follow, at the horizons each is admitted to",
        models.slot_plan()[3:],
        (("L1", "D", 4), ("L1", "D", 24), ("L2", "D", 24)),
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_document,
        check_roundtrip,
        check_validation,
        check_standard,
        check_barrier_pins,
        check_model_arms,
        check_optional_sources,
        check_slots,
        check_slot_plan,
        check_practice_terms,
    ):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
