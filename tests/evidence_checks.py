"""Checks of the evidence item (``hoshitori.evidence``), runnable against a mutant."""

from __future__ import annotations

import copy
import hashlib
import json
import types
from collections.abc import Callable
from typing import Any

from hoshitori import canonical
from hoshitori.ids import natural_id

R = 1_793_311_000_000
H1, H2, H3, H4 = "1" * 64, "2" * 64, "3" * 64, "4" * 64
_GONE = object()


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
        except self.mod.EvidenceError as exc:
            if says is not None and says != str(exc):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def observation(mod: types.ModuleType, **over: Any) -> Any:
    values: dict[str, Any] = {
        "adapter_version": "ryo-1",
        "path": "observation",
        "kind": mod.Kind.OBSERVATION,
        "source": "ryo",
        "origin": mod.RYO,
        "received_at_ms": R,
        "known_at_ms": R,
        "fields": (("asset.symbol", "BTC"), ("market.price_usd", "79012.5"), ("performance.change_24h_pct", "-0.25")),
        "unknown_fields": (("technical_analysis.rsi_14", mod.Unknown.NULL),),
        "capture_id": "cap-1",
        "raw_sha256": H1,
        "tool": "analyze_token",
        "request": (("symbol", "BTC"),),
        "status": "ok",
        "data_mode": "live",
        "availability": (("market_data", "available"), ("technical_analysis", "partial")),
        "warnings": ("one section is partial",),
        "event_time_ms": R - 5_000,
        "asset_refs": (("tk-btc", "binance-spot/BTCUSDT", "RESOLVED"),),
        "redistribution": mod.Redistribution.PRIVATE_ONLY,
    }
    values.update(over)
    return mod.EvidenceItem(**values)


def derived(mod: types.ModuleType, **over: Any) -> Any:
    values: dict[str, Any] = {
        "adapter_version": "calc-1",
        "path": "atr_14",
        "kind": mod.Kind.DERIVED,
        "source": "hoshitori",
        "origin": mod.DERIVED,
        "received_at_ms": R + 1_000,
        "known_at_ms": R + 1_000,
        "fields": (("atr_14", "185.2"),),
        "derived_from": (("evA", H2), ("evB", H3)),
        "transform": ("atr", "1.0.0"),
    }
    values.update(over)
    return mod.EvidenceItem(**values)


def check_constants_and_families(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("record type", (mod.TYPE, mod.TYPE_VERSION), ("evidence", 1))
    c.eq(
        "limits",
        (mod.MAX_FIELDS, mod.MAX_TEXT, mod.MAX_VALUE, mod.MAX_PATH, mod.MAX_SMALL, mod.MAX_INSTANT_MS),
        (5000, 2000, 20_000, 300, 64, 253_402_300_799_999),
    )
    c.eq("kinds", [k.value for k in mod.Kind], ["OBSERVATION", "DERIVED", "INTERPRETATION"])
    c.eq("unknown reasons", [u.value for u in mod.Unknown], ["NULL", "ABSENT", "UNAVAILABLE_BY_AVAILABILITY", "MALFORMED", "DEGRADED_ZERO"])
    c.eq("redistribution", [r.value for r in mod.Redistribution], ["PUBLIC_OK", "PRIVATE_ONLY", "DERIVED_ONLY"])
    c.eq("family kinds", [k.value for k in mod.FamilyKind], ["RYO", "EXCHANGE", "CHAIN", "PRIMARY_DOC", "NEWS", "SOCIAL", "DERIVED"])
    c.eq(
        "the two nameless families",
        (mod.RYO.text, mod.DERIVED.text, mod.RYO.is_origin, mod.DERIVED.is_origin),
        ("RYO", "DERIVED", True, False),
    )
    c.eq("named families", [mod.Family(k, n).text for k, n in ((mod.FamilyKind.EXCHANGE, "binance-spot"), (mod.FamilyKind.CHAIN, "solana"), (mod.FamilyKind.PRIMARY_DOC, "binance.com"), (mod.FamilyKind.NEWS, "coindesk.com"), (mod.FamilyKind.SOCIAL, "@alice_1"))],
         ["EXCHANGE:binance-spot", "CHAIN:solana", "PRIMARY_DOC:binance.com", "NEWS:coindesk.com", "SOCIAL:@alice_1"])  # fmt: skip
    for text in (
        "RYO",
        "DERIVED",
        "EXCHANGE:okx",
        "CHAIN:ethereum",
        "PRIMARY_DOC:sec.gov",
        "NEWS:the-block",
        "SOCIAL:@x",
        "SOCIAL:A.b_c-9",
    ):
        c.eq(f"{text} round-trips", mod.parse_family(text).text, text)
    c.eq(
        "families are ordered by their text",
        sorted(
            [
                mod.Family(mod.FamilyKind.NEWS, "b"),
                mod.RYO,
                mod.Family(mod.FamilyKind.EXCHANGE, "z"),
                mod.Family(mod.FamilyKind.EXCHANGE, "a"),
            ]
        ),
        [mod.Family(mod.FamilyKind.EXCHANGE, "a"), mod.Family(mod.FamilyKind.EXCHANGE, "z"), mod.Family(mod.FamilyKind.NEWS, "b"), mod.RYO],
    )
    c.refuses("a family named RYO", lambda: mod.Family(mod.FamilyKind.RYO, "x"), "the family RYO has no name")
    c.refuses("a derived family with a name", lambda: mod.Family(mod.FamilyKind.DERIVED, "x"), "the family DERIVED has no name")
    c.refuses(
        "an exchange without a name",
        lambda: mod.Family(mod.FamilyKind.EXCHANGE),
        "the family EXCHANGE has a name of 1 to 64 letters, digits and . _ @ -",
    )
    c.refuses(
        "a name with a colon",
        lambda: mod.Family(mod.FamilyKind.NEWS, "a:b"),
        "the family NEWS has a name of 1 to 64 letters, digits and . _ @ -",
    )
    c.refuses(
        "a name with a space",
        lambda: mod.Family(mod.FamilyKind.NEWS, "a b"),
        "the family NEWS has a name of 1 to 64 letters, digits and . _ @ -",
    )
    c.refuses(
        "a name starting with a dot",
        lambda: mod.Family(mod.FamilyKind.NEWS, ".a"),
        "the family NEWS has a name of 1 to 64 letters, digits and . _ @ -",
    )
    c.refuses(
        "a name of 65 characters",
        lambda: mod.Family(mod.FamilyKind.NEWS, "a" * 65),
        "the family NEWS has a name of 1 to 64 letters, digits and . _ @ -",
    )
    c.eq("a name of 64 characters", mod.Family(mod.FamilyKind.NEWS, "a" * 64).name, "a" * 64)
    c.refuses("a kind that is text", lambda: mod.Family("NEWS", "x"), "a family has a kind")
    c.refuses("a family that is not text", lambda: mod.parse_family(5), "a family is written as text")
    c.refuses("an unknown kind", lambda: mod.parse_family("WIRE:x"), "'WIRE' is not an origin family")
    c.refuses("RYO with a name", lambda: mod.parse_family("RYO:x"), "the family 'RYO:x' is not written the way its kind is written")
    c.refuses(
        "an exchange without a name",
        lambda: mod.parse_family("EXCHANGE"),
        "the family 'EXCHANGE' is not written the way its kind is written",
    )
    c.refuses(
        "an exchange with an empty name",
        lambda: mod.parse_family("EXCHANGE:"),
        "the family 'EXCHANGE:' is not written the way its kind is written",
    )
    c.refuses(
        "a name with a second colon",
        lambda: mod.parse_family("NEWS:a:b"),
        "the family 'NEWS:a:b' is not written the way its kind is written",
    )
    c.refuses("lower case", lambda: mod.parse_family("ryo"), "'ryo' is not an origin family")
    c.eq("a frozen family", mod.Family.__dataclass_params__.frozen, True)
    record = {"spec": "x"}
    c.eq("a record digest", mod.record_digest(record), hashlib.sha256(canonical.dumps(record)).hexdigest())
    return c.bad


def check_identity(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    a = observation(mod)
    c.eq("an observation's id", a.evidence_id, natural_id("ev", {"capture": "cap-1", "adapter": "ryo-1", "path": "observation"}))
    base = a.evidence_id
    c.eq("the capture matters", observation(mod, capture_id="cap-2").evidence_id != base, True)
    c.eq("the adapter version matters", observation(mod, adapter_version="ryo-2").evidence_id != base, True)
    c.eq("the path matters", observation(mod, path="interpretation").evidence_id != base, True)
    c.eq(
        "identical bytes fetched twice are two items",
        observation(mod, capture_id="cap-2", raw_sha256=a.raw_sha256).evidence_id != base,
        True,
    )
    c.eq("the content hash is an attribute, not the identity", observation(mod, raw_sha256=H4).evidence_id, base)
    c.eq("the fields are not the identity", observation(mod, fields=(("asset.symbol", "ETH"),)).evidence_id, base)
    c.eq("the times are not the identity", observation(mod, received_at_ms=R + 9, known_at_ms=R + 9).evidence_id, base)
    d = derived(mod)
    c.eq(
        "a derived id",
        d.evidence_id,
        natural_id("ev", {"transform": "atr", "version": "1.0.0", "parents": ["evA", "evB"], "path": "atr_14"}),
    )
    c.eq("the transform matters", derived(mod, transform=("rsi", "1.0.0")).evidence_id != d.evidence_id, True)
    c.eq("the transform version matters", derived(mod, transform=("atr", "1.0.1")).evidence_id != d.evidence_id, True)
    c.eq("the parents matter", derived(mod, derived_from=(("evA", H2), ("evC", H3))).evidence_id != d.evidence_id, True)
    c.eq("the path matters for a derived item", derived(mod, path="atr_20").evidence_id != d.evidence_id, True)
    c.eq("the parents' digests are not the identity", derived(mod, derived_from=(("evA", H4), ("evB", H3))).evidence_id, d.evidence_id)
    c.eq("a derived item's fields are not the identity", derived(mod, fields=(("atr_14", "1"),)).evidence_id, d.evidence_id)
    c.eq("the key of an item", a.key, ("ryo", "analyze_token", '{"request":{"symbol":"BTC"}}', "observation"))
    c.eq(
        "the key ignores the capture and the times",
        observation(mod, capture_id="cap-9", received_at_ms=R + 10, known_at_ms=R + 10).key,
        a.key,
    )
    c.eq("the key tells requests apart", observation(mod, request=(("symbol", "ETH"),)).key == a.key, False)
    c.eq("the key of a derived item", d.key, ("hoshitori", None, '{"request":{}}', "atr_14"))
    c.eq(
        "reading a field",
        (a.field("market.price_usd"), a.field("asset.symbol"), a.field("technical_analysis.rsi_14"), a.field("nothing")),
        ("79012.5", "BTC", None, None),
    )
    c.eq("a field that is false is not missing", observation(mod, fields=(("flag", False),)).field("flag"), False)
    c.eq("an item is frozen", mod.EvidenceItem.__dataclass_params__.frozen, True)
    return c.bad


def check_construction(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    U = mod.Unknown
    cases: list[tuple[str, Callable[[], Any], str]] = [
        (
            "an adapter version with a space",
            lambda: observation(mod, adapter_version="a b"),
            "an adapter version is 1 to 64 letters, digits and . _ + -",
        ),
        (
            "an empty adapter version",
            lambda: observation(mod, adapter_version=""),
            "an adapter version is 1 to 64 letters, digits and . _ + -",
        ),
        (
            "an adapter version that is a number",
            lambda: observation(mod, adapter_version=1),
            "an adapter version is 1 to 64 letters, digits and . _ + -",
        ),
        ("an empty path", lambda: observation(mod, path=""), "an item path is a non-empty string of at most 300 characters"),
        (
            "a path of 301 characters",
            lambda: observation(mod, path="p" * 301),
            "an item path is a non-empty string of at most 300 characters",
        ),
        ("a kind that is text", lambda: observation(mod, kind="OBSERVATION"), "kind, origin and redistribution have their own types"),
        ("an origin that is text", lambda: observation(mod, origin="RYO"), "kind, origin and redistribution have their own types"),
        (
            "a redistribution that is text",
            lambda: observation(mod, redistribution="PUBLIC_OK"),
            "kind, origin and redistribution have their own types",
        ),
        ("a source with a capital", lambda: observation(mod, source="RYO"), "a source is 1 to 64 characters of a-z, 0-9 and . _ : -"),
        ("an empty source", lambda: observation(mod, source=""), "a source is 1 to 64 characters of a-z, 0-9 and . _ : -"),
        (
            "a negative receive time",
            lambda: observation(mod, received_at_ms=-1),
            "received_at is a whole number of milliseconds up to the end of the year 9999",
        ),
        (
            "a boolean receive time",
            lambda: observation(mod, received_at_ms=True),
            "received_at is a whole number of milliseconds up to the end of the year 9999",
        ),
        (
            "a receive time past the year 9999",
            lambda: observation(mod, received_at_ms=253_402_300_800_000, known_at_ms=253_402_300_800_000),
            "received_at is a whole number of milliseconds up to the end of the year 9999",
        ),
        ("known before it was received", lambda: observation(mod, known_at_ms=R - 1), "an item is not known before it was received"),
        (
            "an event time that is negative",
            lambda: observation(mod, event_time_ms=-1),
            "event_time is a whole number of milliseconds up to the end of the year 9999",
        ),
        (
            "first seen after it was received",
            lambda: observation(mod, first_seen_at_ms=R + 1),
            "a provider cannot have first shown an item after we received it",
        ),
        (
            "a field value that is a number",
            lambda: observation(mod, fields=(("a", 1),)),
            "a field is text, a plain decimal in text, or true or false: never a number",
        ),
        (
            "a field value that is empty",
            lambda: observation(mod, fields=(("a", ""),)),
            "a field value is a non-empty string of at most 20000 characters",
        ),
        (
            "a field value that is not canonical",
            lambda: observation(mod, fields=(("a", "é"),)),
            "a field value is not valid canonical text: string is not NFC",
        ),
        (
            "a field path that is empty",
            lambda: observation(mod, fields=(("", "x"),)),
            "a field path is a non-empty string of at most 300 characters",
        ),
        ("fields out of order", lambda: observation(mod, fields=(("b", "1"), ("a", "2"))), "fields are in path order, each path once"),
        ("a field twice", lambda: observation(mod, fields=(("a", "1"), ("a", "2"))), "fields are in path order, each path once"),
        (
            "unknown fields out of order",
            lambda: observation(mod, unknown_fields=(("b", U.NULL), ("a", U.NULL))),
            "unknown fields are in path order, each path once",
        ),
        (
            "an unknown field twice",
            lambda: observation(mod, unknown_fields=(("a", U.NULL), ("a", U.ABSENT))),
            "unknown fields are in path order, each path once",
        ),
        ("an unknown reason that is text", lambda: observation(mod, unknown_fields=(("a", "NULL"),)), "an unknown field has a reason"),
        (
            "a field both known and unknown",
            lambda: observation(mod, fields=(("a", "1"),), unknown_fields=(("a", U.NULL),)),
            "a field is either known or unknown, never both",
        ),
        (
            "too many fields",
            lambda: observation(mod, fields=tuple((f"f{i:05d}", "1") for i in range(5001)), unknown_fields=()),
            "an item has at most 5000 fields",
        ),
        ("a tool that is empty", lambda: observation(mod, tool=""), "a tool is a non-empty string of at most 64 characters"),
        ("a status that is too long", lambda: observation(mod, status="s" * 65), "a status is a non-empty string of at most 64 characters"),
        ("a data mode that is empty", lambda: observation(mod, data_mode=""), "a data mode is a non-empty string of at most 64 characters"),
        (
            "too many warnings",
            lambda: observation(mod, warnings=("w",) * 65),
            "availability, warnings and asset references are at most 64 each",
        ),
        (
            "a warning that is empty",
            lambda: observation(mod, warnings=("",)),
            "a warning is a non-empty string of at most 20000 characters",
        ),
        (
            "availability out of order",
            lambda: observation(mod, availability=(("b", "x"), ("a", "x"))),
            "availability is in section order, each section once",
        ),
        (
            "availability twice",
            lambda: observation(mod, availability=(("a", "x"), ("a", "y"))),
            "availability is in section order, each section once",
        ),
        (
            "an availability word that is empty",
            lambda: observation(mod, availability=(("a", ""),)),
            "an availability word is a non-empty string of at most 64 characters",
        ),
        ("a request out of order", lambda: observation(mod, request=(("b", 1), ("a", 2))), "the request is in key order, each key once"),
        (
            "a request key that is empty",
            lambda: observation(mod, request=(("", 1),)),
            "a request key is a non-empty string of at most 64 characters",
        ),
        (
            "a request value that is a float",
            lambda: observation(mod, request=(("a", 1.5),)),
            "the request is not valid canonical data: unsupported type float; quantities are decimal strings",
        ),
        (
            "an asset reference of two parts",
            lambda: observation(mod, asset_refs=(("a", "b"),)),
            "an asset reference is (asset id, instrument id, status)",
        ),
        (
            "an asset reference part that is empty",
            lambda: observation(mod, asset_refs=(("a", "", "c"),)),
            "an asset reference part is a non-empty string of at most 128 characters",
        ),
        (
            "asset references out of order",
            lambda: observation(mod, asset_refs=(("b", "i", "s"), ("a", "i", "s"))),
            "asset references are in order",
        ),
        (
            "too many asset references",
            lambda: observation(mod, asset_refs=tuple((f"a{i:03d}", "i", "s") for i in range(65))),
            "availability, warnings and asset references are at most 64 each",
        ),
        (
            "an observation without a capture",
            lambda: observation(mod, capture_id=None),
            "an item that is not derived has an origin family, a capture and its hash, and no parents or transform",
        ),
        (
            "an observation without a raw hash",
            lambda: observation(mod, raw_sha256=None),
            "an item that is not derived has an origin family, a capture and its hash, and no parents or transform",
        ),
        (
            "an observation of the derived family",
            lambda: observation(mod, origin=mod.DERIVED),
            "an item that is not derived has an origin family, a capture and its hash, and no parents or transform",
        ),
        (
            "an observation with parents",
            lambda: observation(mod, derived_from=(("evA", H2),)),
            "an item that is not derived has an origin family, a capture and its hash, and no parents or transform",
        ),
        (
            "an observation with a transform",
            lambda: observation(mod, transform=("t", "1")),
            "an item that is not derived has an origin family, a capture and its hash, and no parents or transform",
        ),
        ("a raw hash in capitals", lambda: observation(mod, raw_sha256="A" * 64), "the raw hash is 64 lower-case hexadecimal characters"),
        ("an empty capture id", lambda: observation(mod, capture_id=""), "a capture id is a non-empty string of at most 128 characters"),
        (
            "a derived item without parents",
            lambda: derived(mod, derived_from=()),
            "a derived item has the DERIVED family, parents, a transform and no capture",
        ),
        (
            "a derived item without a transform",
            lambda: derived(mod, transform=None),
            "a derived item has the DERIVED family, parents, a transform and no capture",
        ),
        (
            "a derived item with an origin family",
            lambda: derived(mod, origin=mod.RYO),
            "a derived item has the DERIVED family, parents, a transform and no capture",
        ),
        (
            "a derived item with a capture",
            lambda: derived(mod, capture_id="cap-1", raw_sha256=H1),
            "a derived item has the DERIVED family, parents, a transform and no capture",
        ),
        ("parents out of order", lambda: derived(mod, derived_from=(("evB", H3), ("evA", H2))), "parents are in id order"),
        ("a parent twice", lambda: derived(mod, derived_from=(("evA", H2), ("evA", H3))), "parents are at most 64 distinct ids"),
        (
            "too many parents",
            lambda: derived(mod, derived_from=tuple((f"e{i:03d}", H2) for i in range(65))),
            "parents are at most 64 distinct ids",
        ),
        (
            "a parent digest that is short",
            lambda: derived(mod, derived_from=(("evA", "ab"),)),
            "a parent digest is 64 lower-case hexadecimal characters",
        ),
        (
            "a parent id that is empty",
            lambda: derived(mod, derived_from=(("", H2),)),
            "a parent id is a non-empty string of at most 128 characters",
        ),
        (
            "a transform name that is empty",
            lambda: derived(mod, transform=("", "1")),
            "a transform name is a non-empty string of at most 64 characters",
        ),
        (
            "a transform version with a space",
            lambda: derived(mod, transform=("t", "1 0")),
            "a transform version is 1 to 64 letters, digits and . _ + -",
        ),
        (
            "a superseded id that is empty",
            lambda: observation(mod, supersedes=""),
            "the superseded id is a non-empty string of at most 128 characters",
        ),
    ]
    for label, call, says in cases:
        c.refuses(label, call, says)
    c.eq("an observation", observation(mod).kind, mod.Kind.OBSERVATION)
    c.eq("an interpretation", observation(mod, kind=mod.Kind.INTERPRETATION, path="interpretation").kind, mod.Kind.INTERPRETATION)
    c.eq("a derived item", derived(mod).origin, mod.DERIVED)
    c.eq("known later than received", observation(mod, known_at_ms=R + 1).known_at_ms, R + 1)
    c.eq("first seen exactly when received", observation(mod, first_seen_at_ms=R).first_seen_at_ms, R)
    c.eq("a path of 300 characters", len(observation(mod, path="p" * 300).path), 300)
    c.eq("5000 fields", len(observation(mod, fields=tuple((f"f{i:05d}", "1") for i in range(5000)), unknown_fields=()).fields), 5000)
    c.eq(
        "5000 fields in all, known and unknown",
        len(observation(mod, fields=tuple((f"f{i:05d}", "1") for i in range(4999)), unknown_fields=(("z", mod.Unknown.NULL),)).fields),
        4999,
    )
    c.eq("64 warnings", len(observation(mod, warnings=("w",) * 64).warnings), 64)
    c.eq(
        "a bare minimum",
        observation(
            mod,
            fields=(),
            unknown_fields=(),
            tool=None,
            request=(),
            status=None,
            data_mode=None,
            availability=(),
            warnings=(),
            event_time_ms=None,
            asset_refs=(),
        ).fields,
        (),
    )
    c.eq("a superseding item", observation(mod, supersedes="evOld").supersedes, "evOld")
    c.refuses(
        "a known time past the year 9999",
        lambda: observation(mod, known_at_ms=253_402_300_800_000),
        "known_at is a whole number of milliseconds up to the end of the year 9999",
    )
    c.refuses(
        "a first-seen time that is negative",
        lambda: observation(mod, first_seen_at_ms=-1),
        "first_seen_at is a whole number of milliseconds up to the end of the year 9999",
    )
    c.refuses(
        "an unknown field path that is empty",
        lambda: observation(mod, unknown_fields=(("", mod.Unknown.NULL),)),
        "an unknown field path is a non-empty string of at most 300 characters",
    )
    c.refuses(
        "an availability section that is empty",
        lambda: observation(mod, availability=(("", "available"),)),
        "an availability section is a non-empty string of at most 64 characters",
    )
    c.refuses(
        "an availability section of 65 characters",
        lambda: observation(mod, availability=(("s" * 65, "available"),)),
        "an availability section is a non-empty string of at most 64 characters",
    )
    c.eq("an availability section of 64 characters", len(observation(mod, availability=(("s" * 64, "available"),)).availability), 1)
    c.refuses(
        "4999 known fields and 2 unknown",
        lambda: observation(
            mod, fields=tuple((f"f{i:05d}", "1") for i in range(4999)), unknown_fields=(("u1", mod.Unknown.NULL), ("u2", mod.Unknown.NULL))
        ),
        "an item has at most 5000 fields",
    )
    c.eq(
        "64 availability sections",
        len(observation(mod, availability=tuple((f"s{i:02d}", "available") for i in range(64))).availability),
        64,
    )
    c.eq("64 asset references", len(observation(mod, asset_refs=tuple((f"a{i:02d}", "i", "s") for i in range(64))).asset_refs), 64)
    c.eq("64 parents", len(derived(mod, derived_from=tuple((f"e{i:02d}", H2) for i in range(64))).derived_from), 64)
    c.eq("a field value of 20000 characters", len(observation(mod, fields=(("a", "x" * 20_000),)).fields[0][1]), 20_000)
    c.refuses(
        "a field value of 20001 characters",
        lambda: observation(mod, fields=(("a", "x" * 20_001),)),
        "a field value is a non-empty string of at most 20000 characters",
    )
    c.eq("a warning of 20000 characters", len(observation(mod, warnings=("w" * 20_000,)).warnings[0]), 20_000)
    c.refuses(
        "a warning of 20001 characters",
        lambda: observation(mod, warnings=("w" * 20_001,)),
        "a warning is a non-empty string of at most 20000 characters",
    )
    c.eq("an item at the epoch", observation(mod, received_at_ms=0, known_at_ms=0, event_time_ms=0).received_at_ms, 0)
    c.eq(
        "the last millisecond of the year 9999",
        observation(mod, received_at_ms=253_402_300_799_999, known_at_ms=253_402_300_799_999).known_at_ms,
        253_402_300_799_999,
    )
    return c.bad


READING: dict[str, str] = {
    "an unknown envelope member": "the envelope: unknown envelope members: ['extra']",
    "another type": "not an evidence record of a version this reader knows",
    "another version": "not an evidence record of a version this reader knows",
    "an id of another item": "the id is not the id of this item",
    "an extra body member": "the body has the members ['extra'] wrong",
    "a missing body member": "the body has the members ['path'] wrong",
    "a kind that is unknown": "kind is one of ['DERIVED', 'INTERPRETATION', 'OBSERVATION']",
    "a kind that is a number": "kind is one of ['DERIVED', 'INTERPRETATION', 'OBSERVATION']",
    "a redistribution that is unknown": "redistribution is one of ['DERIVED_ONLY', 'PRIVATE_ONLY', 'PUBLIC_OK']",
    "a field that is a number": "a field is text, a plain decimal in text, or true or false: never a number",
    "a field that is null": "a field is text, a plain decimal in text, or true or false: never a number",
    "fields that are a list": "fields is an object",
    "an unknown field of the wrong shape": "an unknown field is {path, reason}",
    "an unknown field with a reason that is unknown": "an unknown field's reason is one of ['ABSENT', 'DEGRADED_ZERO', 'MALFORMED', 'NULL', 'UNAVAILABLE_BY_AVAILABILITY']",
    "unknown fields that are not a list": "unknown_fields is a list",
    "an unknown field that is not an object": "an unknown field is an object",
    "a parent of the wrong shape": "a parent is {evidence_id, sha256}",
    "a parent that is not an object": "a parent is an object",
    "parents that are not a list": "derived_from is a list",
    "a parent id that is a number": "a parent id is text",
    "an asset reference of the wrong shape": "an asset reference is {asset_id, instrument_id, status}",
    "an asset reference that is not an object": "an asset reference is an object",
    "asset references that are not a list": "asset_refs is a list",
    "a transform of the wrong shape": "the transform is {name, version}",
    "a transform that is not an object": "the transform is an object",
    "a transform name that is a number": "the transform name is text",
    "availability that is a list": "availability is an object",
    "an availability word that is a number": "an availability word is text",
    "a request that is a list": "the request is an object",
    "warnings that are a string": "warnings is a list",
    "a warning that is a number": "a warning is text",
    "a received time without milliseconds": "received_at is written as UTC with milliseconds",
    "a received time that is a number": "received_at is an RFC 3339 string",
    "a received time with an offset": "received_at is written as UTC with milliseconds",
    "a received time that is nonsense": "received_at: not an RFC 3339 timestamp with an explicit offset: 'yesterday'",
    "an event time that is a number": "event_time is an RFC 3339 string",
    "a known time before the received time": "an item is not known before it was received",
    "an origin family that is unknown": "'WIRE' is not an origin family",
    "an origin family that is a number": "a family is written as text",
    "a tool that is a number": "the tool is text or null",
    "a status that is a number": "the status is text or null",
    "a capture id that is a number": "the capture id is text or null",
    "a superseded id that is a number": "supersedes is text or null",
    "an adapter version that is a number": "the adapter version is text",
    "a source that is a number": "the source is text",
    "a path that is a number": "the item path is text",
    "a received time that is missing": "received_at is an RFC 3339 string",
    "a known time that is missing": "known_at is an RFC 3339 string",
    "a known time that is a number": "known_at is an RFC 3339 string",
    "a first-seen time that is a number": "first_seen_at is an RFC 3339 string",
    "a raw hash that is a number": "the raw hash is text or null",
    "a data mode that is a number": "the data mode is text or null",
    "an unknown field path that is a number": "an unknown field path is text",
    "a parent digest that is a number": "a parent digest is text",
    "a transform version that is a number": "the transform version is text",
    "an asset id that is a number": "asset_id is text",
    "an instrument id that is a number": "instrument_id is text",
    "an asset status that is a number": "status is text",
}


def check_records(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    a = observation(mod, fields=(("asset.symbol", "BTC"), ("flag", True), ("market.price_usd", "79012.5")), first_seen_at_ms=R - 100)
    body = a.to_body()
    c.eq("an observation's body", body, {
        "kind": "OBSERVATION", "source": "ryo", "tool": "analyze_token", "capture_id": "cap-1", "raw_sha256": H1, "adapter_version": "ryo-1",
        "path": "observation", "request": {"symbol": "BTC"}, "status": "ok", "data_mode": "live",
        "availability": {"market_data": "available", "technical_analysis": "partial"}, "warnings": ["one section is partial"],
        "fields": {"asset.symbol": "BTC", "flag": True, "market.price_usd": "79012.5"},
        "unknown_fields": [{"path": "technical_analysis.rsi_14", "reason": "NULL"}],
        "event_time": "2026-10-29T21:56:35.000Z", "first_seen_at": "2026-10-29T21:56:39.900Z", "received_at": "2026-10-29T21:56:40.000Z",
        "known_at": "2026-10-29T21:56:40.000Z", "origin_family": "RYO", "derived_from": [], "transform": None, "supersedes": None,
        "asset_refs": [{"asset_id": "tk-btc", "instrument_id": "binance-spot/BTCUSDT", "status": "RESOLVED"}], "redistribution": "PRIVATE_ONLY",
    })  # fmt: skip
    d = derived(mod, supersedes="evOld", redistribution=mod.Redistribution.DERIVED_ONLY)
    db = d.to_body()
    c.eq("a derived body's own members", {k: db[k] for k in ("kind", "capture_id", "raw_sha256", "origin_family", "derived_from", "transform", "supersedes", "redistribution", "event_time", "first_seen_at", "known_at")},
         {"kind": "DERIVED", "capture_id": None, "raw_sha256": None, "origin_family": "DERIVED", "derived_from": [{"evidence_id": "evA", "sha256": H2}, {"evidence_id": "evB", "sha256": H3}],
          "transform": {"name": "atr", "version": "1.0.0"}, "supersedes": "evOld", "redistribution": "DERIVED_ONLY", "event_time": None, "first_seen_at": None, "known_at": "2026-10-29T21:56:41.000Z"})  # fmt: skip
    record = a.to_record("urn:hoshitori:evidence:test", R + 5)
    c.eq("the envelope", {k: record[k] for k in ("spec", "type", "type_version", "id", "source", "created_at", "refs", "privacy")},
         {"spec": "hoshitori.record/1", "type": "evidence", "type_version": 1, "id": a.evidence_id, "source": "urn:hoshitori:evidence:test", "created_at": "2026-10-29T21:56:40.005Z", "refs": [], "privacy": "private_digest_only"})  # fmt: skip
    c.eq(
        "a derived record's references", d.to_record("urn:x", R + 1_000)["refs"], [{"id": "evA", "sha256": H2}, {"id": "evB", "sha256": H3}]
    )
    c.eq(
        "privacy of a public item", observation(mod, redistribution=mod.Redistribution.PUBLIC_OK).to_record("urn:x", R)["privacy"], "public"
    )
    c.eq("privacy of a derived-only item", d.to_record("urn:x", R + 1_000)["privacy"], "public")
    c.eq("recorded exactly when known", a.to_record("urn:x", R)["created_at"], "2026-10-29T21:56:40.000Z")
    c.refuses(
        "recorded before it is known",
        lambda: observation(mod, known_at_ms=R + 10).to_record("urn:x", R + 9),
        "an item is recorded at or after the time it is known",
    )
    c.refuses(
        "a creation time that is negative",
        lambda: a.to_record("urn:x", -1),
        "the creation time is a whole number of milliseconds up to the end of the year 9999",
    )
    c.refuses(
        "a source that is not a urn",
        lambda: a.to_record("evidence", R),
        "the record does not satisfy the envelope: source must be an ASCII urn",
    )
    items = [
        a,
        observation(
            mod,
            kind=mod.Kind.INTERPRETATION,
            path="interpretation",
            fields=(("verdict", "buy"),),
            unknown_fields=(),
            redistribution=mod.Redistribution.PUBLIC_OK,
        ),
        d,
        observation(
            mod,
            fields=(),
            unknown_fields=(),
            tool=None,
            request=(),
            status=None,
            data_mode=None,
            availability=(),
            warnings=(),
            event_time_ms=None,
            asset_refs=(),
        ),
        observation(mod, request=(("a", [1, {"b": None}]), ("z", "x")), known_at_ms=R + 3),
    ]
    for item in items:
        rec = item.to_record("urn:hoshitori:evidence:test", item.known_at_ms + 1)
        again = mod.from_record(canonical.parse_record(canonical.dumps(rec)))
        c.eq(f"{item.path} reads back", again, item)
        c.eq(
            f"{item.path} writes back the same bytes",
            canonical.dumps(again.to_record("urn:hoshitori:evidence:test", item.known_at_ms + 1)),
            canonical.dumps(rec),
        )
    c.eq("the ledger's copy, with its commit time, is read", mod.from_record({**record, "committed_at": "2026-10-29T21:56:41.000Z"}), a)
    many = observation(
        mod,
        fields=(("a.b", "1"), ("a.c", "2"), ("z", "3")),
        unknown_fields=(("m", mod.Unknown.NULL), ("n", mod.Unknown.ABSENT)),
        request=(("a", 1), ("b", 2)),
        availability=(("market_data", "available"), ("technical_analysis", "partial")),
        asset_refs=(("tk-a", "i1", "RESOLVED"), ("tk-b", "i2", "AMBIGUOUS")),
    )
    mixed = many.to_record("urn:x", R)
    inner = mixed["body"]
    for name in ("fields", "request", "availability"):
        inner[name] = dict(reversed(list(inner[name].items())))  # members in the opposite order
    text = json.dumps(mixed, ensure_ascii=False, sort_keys=False).encode()
    c.eq("a record whose members are in another order is read to the same item", mod.from_record(canonical.parse_record(text)), many)
    for name, says in (
        ("unknown_fields", "unknown fields are in path order, each path once"),
        ("asset_refs", "asset references are in order"),
    ):
        flipped = many.to_record("urn:x", R)
        flipped["body"][name] = list(reversed(flipped["body"][name]))  # a list in another order is another record, so it is refused
        c.refuses(f"{name} in another order", lambda r=flipped: mod.from_record(r), says)
    parents = derived(mod, derived_from=(("evA", H2), ("evB", H3))).to_record("urn:x", R + 1_000)
    parents["body"]["derived_from"] = list(reversed(parents["body"]["derived_from"]))
    parents["refs"] = list(reversed(parents["refs"]))
    c.refuses("parents in another order", lambda: mod.from_record(parents), "parents are in id order")

    def changed(rec: dict[str, Any], path: tuple[str, ...], value: Any) -> dict[str, Any]:
        out = copy.deepcopy(rec)
        node = out
        for key in path[:-1]:
            node = node[key]
        if value is _GONE:
            del node[path[-1]]
        else:
            node[path[-1]] = value
        return out

    drec = d.to_record("urn:x", R + 1_000)
    cases: list[tuple[str, dict[str, Any]]] = [
        ("an unknown envelope member", changed(record, ("extra",), 1)),
        ("another type", changed(record, ("type",), "forecast")),
        ("another version", changed(record, ("type_version",), 2)),
        ("an id of another item", changed(record, ("id",), "evAAAAAAAAAAAAAAAAAAAAAAAAAA")),
        ("an extra body member", changed(record, ("body", "extra"), 1)),
        ("a missing body member", changed(record, ("body", "path"), _GONE)),
        ("a kind that is unknown", changed(record, ("body", "kind"), "GUESS")),
        ("a kind that is a number", changed(record, ("body", "kind"), 1)),
        ("a redistribution that is unknown", changed(record, ("body", "redistribution"), "PUBLIC")),
        ("a field that is a number", changed(record, ("body", "fields", "market.price_usd"), 79012.5)),
        ("a field that is null", changed(record, ("body", "fields", "market.price_usd"), None)),
        ("fields that are a list", changed(record, ("body", "fields"), [])),
        ("an unknown field of the wrong shape", changed(record, ("body", "unknown_fields"), [{"path": "x"}])),
        ("an unknown field with a reason that is unknown", changed(record, ("body", "unknown_fields"), [{"path": "x", "reason": "SHRUG"}])),
        ("unknown fields that are not a list", changed(record, ("body", "unknown_fields"), {})),
        ("an unknown field that is not an object", changed(record, ("body", "unknown_fields"), ["x"])),
        ("a parent of the wrong shape", changed(drec, ("body", "derived_from"), [{"evidence_id": "evA"}])),
        ("a parent that is not an object", changed(drec, ("body", "derived_from"), ["evA"])),
        ("parents that are not a list", changed(drec, ("body", "derived_from"), {})),
        ("a parent id that is a number", changed(drec, ("body", "derived_from"), [{"evidence_id": 1, "sha256": H2}])),
        ("an asset reference of the wrong shape", changed(record, ("body", "asset_refs"), [{"asset_id": "a"}])),
        ("an asset reference that is not an object", changed(record, ("body", "asset_refs"), ["a"])),
        ("asset references that are not a list", changed(record, ("body", "asset_refs"), {})),
        ("a transform of the wrong shape", changed(drec, ("body", "transform"), {"name": "atr"})),
        ("a transform that is not an object", changed(drec, ("body", "transform"), "atr")),
        ("a transform name that is a number", changed(drec, ("body", "transform"), {"name": 1, "version": "1"})),
        ("availability that is a list", changed(record, ("body", "availability"), [])),
        ("an availability word that is a number", changed(record, ("body", "availability"), {"a": 1})),
        ("a request that is a list", changed(record, ("body", "request"), [])),
        ("warnings that are a string", changed(record, ("body", "warnings"), "w")),
        ("a warning that is a number", changed(record, ("body", "warnings"), [1])),
        ("a received time without milliseconds", changed(record, ("body", "received_at"), "2026-10-29T21:56:40Z")),
        ("a received time that is a number", changed(record, ("body", "received_at"), 5)),
        ("a received time with an offset", changed(record, ("body", "received_at"), "2026-10-29T22:56:40.000+01:00")),
        ("a received time that is nonsense", changed(record, ("body", "received_at"), "yesterday")),
        ("an event time that is a number", changed(record, ("body", "event_time"), 5)),
        ("a known time before the received time", changed(record, ("body", "known_at"), "2026-10-29T21:56:39.000Z")),
        ("an origin family that is unknown", changed(record, ("body", "origin_family"), "WIRE:x")),
        ("an origin family that is a number", changed(record, ("body", "origin_family"), 1)),
        ("a tool that is a number", changed(record, ("body", "tool"), 5)),
        ("a status that is a number", changed(record, ("body", "status"), 5)),
        ("a capture id that is a number", changed(record, ("body", "capture_id"), 5)),
        ("a superseded id that is a number", changed(record, ("body", "supersedes"), 5)),
        ("an adapter version that is a number", changed(record, ("body", "adapter_version"), 5)),
        ("a source that is a number", changed(record, ("body", "source"), 5)),
        ("a path that is a number", changed(record, ("body", "path"), 5)),
        ("a received time that is missing", changed(record, ("body", "received_at"), None)),
        ("a known time that is missing", changed(record, ("body", "known_at"), None)),
        ("a known time that is a number", changed(record, ("body", "known_at"), 5)),
        ("a first-seen time that is a number", changed(record, ("body", "first_seen_at"), 5)),
        ("a raw hash that is a number", changed(record, ("body", "raw_sha256"), 5)),
        ("a data mode that is a number", changed(record, ("body", "data_mode"), 5)),
        ("an unknown field path that is a number", changed(record, ("body", "unknown_fields"), [{"path": 1, "reason": "NULL"}])),
        ("a parent digest that is a number", changed(drec, ("body", "derived_from"), [{"evidence_id": "evA", "sha256": 1}])),
        ("a transform version that is a number", changed(drec, ("body", "transform"), {"name": "atr", "version": 1})),
        ("an asset id that is a number", changed(record, ("body", "asset_refs"), [{"asset_id": 1, "instrument_id": "i", "status": "s"}])),
        (
            "an instrument id that is a number",
            changed(record, ("body", "asset_refs"), [{"asset_id": "a", "instrument_id": 1, "status": "s"}]),
        ),
        (
            "an asset status that is a number",
            changed(record, ("body", "asset_refs"), [{"asset_id": "a", "instrument_id": "i", "status": 1}]),
        ),
    ]
    for label, rec in cases:
        c.refuses(label, lambda r=rec: mod.from_record(r), READING[label])
    c.eq("every reading message belongs to a case", sorted(READING), sorted(label for label, _ in cases))
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants_and_families, check_identity, check_construction, check_records):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
