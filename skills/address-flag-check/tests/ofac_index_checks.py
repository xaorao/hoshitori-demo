"""Checks of ``address_flag_check.ofac_index``, runnable against a mutant: the streaming parse of OFAC's SDN advanced XML (the recorded trimmed
extract and a hand-written synthetic file), the index and its lookup, refusal of entity declarations and of files that are not the list, the
ZIP wrapper, and the stored form.

Expectations for the recorded extract were counted by B5A's separate ElementTree analysis (scratch ``expect/extract_analysis.txt``); those for
the synthetic file were written by hand from the file.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import io
import types
import zipfile

from tests.checks_support import Case, extract_xml, ofac_meta, synthetic_xml

NS = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ADVANCED_XML"
DEAD_KEY = "0x000000000000000000000000000000000000dead"


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _listing(item: object) -> tuple[object, ...]:
    return tuple(
        getattr(item, k)
        for k in ("key", "published", "label", "family", "format", "format_ok", "entry_id", "party_type", "programs", "reliability")
    )


def check_recorded_extract(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    data = extract_xml()
    index = c.run("parse the recorded extract", lambda: mod.parse_xml(data))
    if index is None:
        return [*c.bad, "parse the recorded extract: gave no index"]
    c.eq("the extract's hash is the recorded one", hashlib.sha256(data).hexdigest(), ofac_meta()["extract"]["sha256"])
    c.eq("date of issue", index.date_of_issue, "2026-10-28")
    c.eq("hash of the parsed bytes", index.xml_sha256, hashlib.sha256(data).hexdigest())
    c.eq("bytes parsed", index.xml_bytes, len(data))
    c.eq("address features", index.features, 63)
    c.eq("distinct address strings", index.address_strings, 63)
    c.eq(
        "digital currency labels",
        index.labels,
        (
            "ARB",
            "BCH",
            "BNB",
            "BSC",
            "BSV",
            "BTG",
            "DASH",
            "DOGE",
            "ETC",
            "ETH",
            "LTC",
            "SOL",
            "TRX",
            "USDC",
            "USDT",
            "XBT",
            "XMR",
            "XRP",
            "XVG",
            "ZEC",
        ),
    )
    c.eq("families", index.families, {"bitcoin": 36, "evm": 12, "other": 4, "tron": 11})
    c.eq("listings", len(index.listings), 63)
    c.eq("every listing is an entity", {x.party_type for x in index.listings}, {"entity"})
    c.eq(
        "a checksummed ETH string on entry 47635",
        [_listing(x) for x in index.lookup("0xe950dc316b836e4eefb8308bf32bf7c72a1358ff")],
        [
            (
                "0xe950dc316b836e4eefb8308bf32bf7c72a1358ff",
                "0xE950DC316b836e4EeFb8308bf32Bf7C72a1358FF",
                "ETH",
                "evm",
                "evm_hex",
                True,
                "47635",
                "entity",
                ("SDGT",),
                "Confirmed",
            )
        ],
    )
    c.eq(
        "a lower-case ETH string on entry 30518",
        [_listing(x) for x in index.lookup("0x1da5821544e25c636c1417ba96ade4cf6d2f9b5a")],
        [
            (
                "0x1da5821544e25c636c1417ba96ade4cf6d2f9b5a",
                "0x1da5821544e25c636c1417ba96ade4cf6d2f9b5a",
                "ETH",
                "evm",
                "evm_hex",
                True,
                "30518",
                "entity",
                ("CYBER2", "ELECTION-EO13848"),
                "Unknown",
            )
        ],
    )
    c.eq(
        "a TRX string on entry 55045",
        [_listing(x) for x in index.lookup("TL1k1U6SHohxBqb68kCodxHc9y2LXoDSep")],
        [
            (
                "TL1k1U6SHohxBqb68kCodxHc9y2LXoDSep",
                "TL1k1U6SHohxBqb68kCodxHc9y2LXoDSep",
                "TRX",
                "tron",
                "tron_base58check",
                True,
                "55045",
                "entity",
                ("CYBER4",),
                "Unknown",
            )
        ],
    )
    c.eq(
        "a Bitcoin-format string filed under USDT on entry 33854",
        [_listing(x) for x in index.lookup("3LtcaPbCj87CwJHnRX3vh7c2y9RZQqeSy8")],
        [
            (
                "3LtcaPbCj87CwJHnRX3vh7c2y9RZQqeSy8",
                "3LtcaPbCj87CwJHnRX3vh7c2y9RZQqeSy8",
                "USDT",
                "bitcoin",
                "bitcoin_p2sh",
                True,
                "33854",
                "entity",
                ("CYBER2",),
                "Unknown",
            )
        ],
    )
    c.eq("an address that is not listed", index.lookup(DEAD_KEY), ())
    c.eq("a key is matched exactly (no case folding of base58)", index.lookup("tl1k1u6shohxbqb68kcodxhc9y2lxodsep"), ())
    small = c.run("parse in 7-byte pieces", lambda: mod.parse_xml(data, chunk_size=7))
    if small is not None:
        c.eq("the same index in small pieces", small, index)
    return c.bad


def check_synthetic(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    data = synthetic_xml()
    index = c.run("parse the synthetic file", lambda: mod.parse_xml(data))
    if index is None:
        return [*c.bad, "parse the synthetic file: gave no index"]
    c.eq("date of issue", index.date_of_issue, "2026-01-02")
    c.eq("address features (the website feature is not one)", index.features, 10)
    c.eq("distinct published strings", index.address_strings, 9)
    c.eq("labels", index.labels, ("ETH", "SOL", "TRX", "USDT", "XBT", "XMR"))
    c.eq("families by feature", index.families, {"bitcoin": 2, "evm": 4, "other": 2, "tron": 2})
    c.eq("families in name order (the file meets evm first)", list(index.families), ["bitcoin", "evm", "other", "tron"])
    c.raises("a Listing cannot be changed", lambda: setattr(index.listings[0], "key", "x"), dataclasses.FrozenInstanceError)
    c.eq("a Listing can be a dict key", c.run("hashing a Listing", lambda: {index.listings[0]: 1}.get(index.listings[0])), 1)
    c.raises("a ListIndex cannot be changed", lambda: setattr(index, "features", 0), dataclasses.FrozenInstanceError)
    c.eq(
        "one key on three entries, in entry order",
        [_listing(x) for x in index.lookup(DEAD_KEY)],
        [
            (
                DEAD_KEY,
                "0x000000000000000000000000000000000000dEaD",
                "ETH",
                "evm",
                "evm_hex",
                True,
                "9001",
                "entity",
                ("CYBER2", "SDGT"),
                "Confirmed",
            ),
            (
                DEAD_KEY,
                "0x000000000000000000000000000000000000dEaD",
                "ETH",
                "evm",
                "evm_hex",
                True,
                "9002",
                "individual",
                ("IFSR",),
                "Unknown",
            ),
            (DEAD_KEY, "0x000000000000000000000000000000000000DeAD", "ETH", "evm", "evm_hex", False, "9003", "transport", (), "Unknown"),
        ],
    )
    c.eq(
        "a Tron string filed under XBT keeps its label and its own family",
        [_listing(x) for x in index.lookup("TBXSw8fM4jpQkGc6zZjsVABFpVN7UvXPdV")],
        [
            (
                "TBXSw8fM4jpQkGc6zZjsVABFpVN7UvXPdV",
                "TBXSw8fM4jpQkGc6zZjsVABFpVN7UvXPdV",
                "XBT",
                "tron",
                "tron_base58check",
                True,
                "9001",
                "entity",
                ("CYBER2", "SDGT"),
                "Unknown",
            )
        ],
    )
    c.eq(
        "a USDT string with a Reported reliability",
        [_listing(x) for x in index.lookup("T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb")],
        [
            (
                "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb",
                "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb",
                "USDT",
                "tron",
                "tron_base58check",
                True,
                "9001",
                "entity",
                ("CYBER2", "SDGT"),
                "Reported",
            )
        ],
    )
    c.eq(
        "an entry with no sanctions entry has no programmes",
        [_listing(x) for x in index.lookup("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq")],
        [
            (
                "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",
                "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",
                "XBT",
                "bitcoin",
                "bitcoin_bech32",
                True,
                "9005",
                "entity",
                (),
                "Unknown",
            )
        ],
    )
    c.eq(
        "another party type is other, and a repeated programme counts once",
        [_listing(x) for x in index.lookup("So11111111111111111111111111111111111111112")],
        [
            (
                "So11111111111111111111111111111111111111112",
                "So11111111111111111111111111111111111111112",
                "SOL",
                "other",
                None,
                None,
                "9004",
                "other",
                ("TCO",),
                "Unknown",
            )
        ],
    )
    c.eq(
        "a contract address on an entity entry with no sanctions entry",
        [_listing(x) for x in index.lookup("0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2")],
        [
            (
                "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
                "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                "ETH",
                "evm",
                "evm_hex",
                True,
                "9006",
                "entity",
                (),
                "Unknown",
            )
        ],
    )
    c.eq("a website is not an address", index.lookup("www.example.org"), ())
    return c.bad


def _bad_xml(body: str, head: str = "") -> bytes:
    return f'<?xml version="1.0" encoding="utf-8"?>{head}<Sanctions xmlns="{NS}">{body}</Sanctions>'.encode()


DATE = "<DateOfIssue><Year>2026</Year><Month>1</Month><Day>2</Day></DateOfIssue>"
TYPES = '<ReferenceValueSets><FeatureTypeValues><FeatureType ID="1">Digital Currency Address - ETH</FeatureType></FeatureTypeValues></ReferenceValueSets>'
PARTY = (
    '<DistinctParties><DistinctParty FixedRef="1"><Profile ID="1" PartySubTypeID="3"><Feature ID="1" FeatureTypeID="1"><FeatureVersion ID="1">'
    "<VersionDetail>0x000000000000000000000000000000000000dEaD</VersionDetail></FeatureVersion></Feature></Profile></DistinctParty></DistinctParties>"
)


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    err = mod.ListFormatError
    ok = c.run("a minimal file", lambda: mod.parse_xml(_bad_xml(DATE + TYPES + PARTY)))
    if ok is not None:
        c.eq(
            "a minimal file has one listing whose party type is unknown to it",
            [(x.key, x.party_type, x.reliability) for x in ok.listings],
            [(DEAD_KEY, "other", None)],
        )
    for label, data, says in (
        (
            "an entity declaration",
            _bad_xml(DATE + TYPES + PARTY, '<!DOCTYPE Sanctions [<!ENTITY x "y">]>'),
            "the list file declares a document type or entities, which are not accepted",
        ),
        (
            "a document type",
            _bad_xml(DATE + TYPES + PARTY, "<!DOCTYPE Sanctions>"),
            "the list file declares a document type or entities, which are not accepted",
        ),
        ("another root namespace", DATE.encode(), "the file is not an OFAC SDN advanced XML file"),
        ("another root element", f'<Other xmlns="{NS}"/>'.encode(), "the file is not an OFAC SDN advanced XML file"),
        ("XML that is not well formed", _bad_xml(DATE + "<Open>"), "the list file is not well-formed XML"),
        ("no date of issue", _bad_xml(TYPES + PARTY), "the list file has no DateOfIssue"),
        ("a date of issue that is not a date", _bad_xml(DATE.replace(">2<", ">x<") + TYPES + PARTY), "the list file has no DateOfIssue"),
        ("no digital currency address", _bad_xml(DATE + TYPES), "the list file holds no digital currency addresses"),
        (
            "parties before the reference values",
            _bad_xml(DATE + PARTY + TYPES),
            "the list file is not in the expected order (reference values first)",
        ),
        ("an empty file", b"", "the list file is not well-formed XML"),
    ):
        c.raises(label, lambda data=data: mod.parse_xml(data), err, says)
    c.raises(
        "a file over the size limit",
        lambda: mod.parse_xml(_bad_xml(DATE + TYPES + PARTY), max_bytes=100),
        err,
        "the list file is larger than the allowed size",
    )
    good = _bad_xml(DATE + TYPES + PARTY)
    z = c.run("a ZIP with the one member", lambda: mod.parse_zip(_zip({"SDN_ADVANCED.XML": good})))
    c.eq("the ZIP's index", None if z is None else [x.key for x in z.listings], [DEAD_KEY])
    exact = c.run("a file exactly at the size limit", lambda: mod.parse_xml(good, max_bytes=len(good)))
    c.eq("a file exactly at the size limit is read", None if exact is None else [x.key for x in exact.listings], [DEAD_KEY])
    # A syntax error in the first piece is reported at once, not after the later pieces have been read (which here would exceed the limit)
    c.raises(
        "a syntax error in the first piece",
        lambda: mod.parse_xml(b"<<<<<<<<<<" * 5, chunk_size=10, max_bytes=49),
        err,
        "the list file is not well-formed XML",
    )
    for label, data, says in (
        ("not a ZIP", b"PK not really", "the download is not a ZIP archive"),
        ("a ZIP with another member", _zip({"other.xml": good}), "the ZIP archive must hold exactly one member, SDN_ADVANCED.XML"),
        (
            "a ZIP with two members",
            _zip({"SDN_ADVANCED.XML": good, "x": b""}),
            "the ZIP archive must hold exactly one member, SDN_ADVANCED.XML",
        ),
    ):
        c.raises(label, lambda data=data: mod.parse_zip(data), err, says)
    for where in (40, 60):  # 40 is inside the member's name in its local header, 60 inside the compressed data
        damaged = bytearray(_zip({"SDN_ADVANCED.XML": good}))
        damaged[where] ^= 0xFF
        c.raises(
            f"a ZIP damaged at byte {where}",
            lambda damaged=damaged: mod.parse_zip(bytes(damaged)),
            err,
            "the ZIP archive could not be read",
        )
    calls = []

    def stop() -> bool:
        calls.append(1)
        return True

    c.raises("a stop request while reading", lambda: mod.parse_zip(_zip({"SDN_ADVANCED.XML": good}), should_stop=stop), TimeoutError)
    c.eq("the stop request was asked", len(calls) > 0, True)
    # The time budget is asked before each piece of at most 1 MiB: an XML of 1 MiB and 10 bytes is read in two pieces
    asked: list[int] = []
    big = _bad_xml(DATE + " " * (2**20 + 10 - len(good)) + TYPES + PARTY)
    c.eq("the padded file is 1 MiB and 10 bytes", len(big), 2**20 + 10)
    z = c.run(
        "a 1 MiB file that is never stopped",
        lambda: mod.parse_zip(_zip({"SDN_ADVANCED.XML": big}), should_stop=lambda: bool(asked.append(1))),
    )
    c.eq("its index", None if z is None else [x.key for x in z.listings], [DEAD_KEY])
    c.eq("the budget is asked once per MiB piece", len(asked), 2)
    # The size limit: 512 MB, four times the publication's size (about 128 MB); pinned so that changing it is a decision, not an accident
    c.eq("the size limit", mod.MAX_XML_BYTES, 512_000_000)
    c.eq("the size limit is the builder's default", inspect.signature(mod.IndexBuilder).parameters["max_bytes"].default, 512_000_000)
    return c.bad


# Elements out of OFAC's shape. In the recorded publication each element the index reads occurs at one path only and holds no child
# element when its text is read (B5A-2 scratch runs/ofac_paths.txt). The parser matches an element by its name and its parent, not by its
# whole path; these cases pin what it does today with elements out of place, so that any change to it is noticed. SYNTHETIC documents.
REFS = (
    '<ReferenceValueSets><FeatureTypeValues><FeatureType ID="1">Digital Currency Address - ETH</FeatureType></FeatureTypeValues>'
    '<PartySubTypeValues><PartySubType ID="3" PartyTypeID="2">Unknown</PartySubType><PartySubType ID="4" PartyTypeID="1">Unknown</PartySubType>'
    '</PartySubTypeValues><PartyTypeValues><PartyType ID="1">Individual</PartyType><PartyType ID="2">Entity</PartyType></PartyTypeValues>'
    '<ReliabilityValues><Reliability ID="1">Confirmed</Reliability></ReliabilityValues><SanctionsTypeValues><SanctionsType ID="1">Program'
    '</SanctionsType><SanctionsType ID="2">Block</SanctionsType></SanctionsTypeValues></ReferenceValueSets>'
)
EATER = "1BitcoinEaterAddressDontSendf59kuE"
BLACKHOLE = "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"
DEAD = "0x000000000000000000000000000000000000dEaD"


def _version(address: str) -> str:
    return f'<FeatureVersion ReliabilityID="1"><VersionDetail>{address}</VersionDetail></FeatureVersion>'


def _feature(address: str) -> str:
    return f'<Feature FeatureTypeID="1">{_version(address)}</Feature>'


def _party(fixed: str, profile: str) -> str:
    return f'<DistinctParty FixedRef="{fixed}">{profile}</DistinctParty>'


def _measure(kind: str, comment: str) -> str:
    return f'<SanctionsMeasure SanctionsTypeID="{kind}"><Comment>{comment}</Comment></SanctionsMeasure>'


def _doc(parties: str, entries: str = "", before_parties: str = "", date: str = DATE) -> bytes:
    return _bad_xml(
        f"{date}{REFS}{before_parties}<DistinctParties>{parties}</DistinctParties><SanctionsEntries>{entries}</SanctionsEntries>"
    )


ONE_PARTY = _party("1", f'<Profile ID="1" PartySubTypeID="3">{_feature(DEAD)}</Profile>')


def check_out_of_shape(mod: types.ModuleType) -> list[str]:
    c = Case(mod)

    def listings(label: str, data: bytes) -> object:
        index = c.run(label, lambda: mod.parse_xml(data))
        return None if index is None else [(x.key, x.entry_id, x.party_type, x.programs, x.reliability) for x in index.listings]

    only_dead = [(DEAD_KEY, "1", "entity", (), "Confirmed")]
    # A Program measure outside any SanctionsEntry, before the first one, adds no programme (here to the profile that has no ID)
    c.eq(
        "a programme measure before any sanctions entry",
        listings(
            "stray measure first", _doc(_party("1", f'<Profile PartySubTypeID="3">{_feature(DEAD)}</Profile>'), _measure("1", "STRAY"))
        ),
        only_dead,
    )
    # A Program measure outside a SanctionsEntry, after an entry whose last measure is a Block, adds no programme to that entry
    c.eq(
        "a programme measure after a sanctions entry",
        listings(
            "stray measure after",
            _doc(
                ONE_PARTY,
                f'<SanctionsEntry ProfileID="1">{_measure("1", "ONE")}{_measure("2", "")}</SanctionsEntry>{_measure("1", "STRAY")}',
            ),
        ),
        [(DEAD_KEY, "1", "entity", ("ONE",), "Confirmed")],
    )
    # A feature version left in the profile after its feature closed, and a feature directly in the party, are not read
    c.eq(
        "a feature version outside its feature and a feature outside its profile",
        listings(
            "misplaced features",
            _doc(
                _party("1", f'<Profile ID="1" PartySubTypeID="3">{_feature(DEAD)}{_version(EATER)}</Profile>')
                + _party("2", f'{_feature(BLACKHOLE)}<Profile ID="2" PartySubTypeID="3"></Profile>')
            ),
        ),
        only_dead,
    )
    # A Profile nested in the party's profile does not change the party's ids
    c.eq(
        "a profile nested in a profile",
        listings(
            "nested profile",
            _doc(
                _party("1", f'<Profile ID="1" PartySubTypeID="3"><Profile ID="2" PartySubTypeID="4"></Profile>{_feature(DEAD)}</Profile>'),
                f'<SanctionsEntry ProfileID="1">{_measure("1", "ONE")}</SanctionsEntry><SanctionsEntry ProfileID="2">{_measure("1", "TWO")}</SanctionsEntry>',
            ),
        ),
        [(DEAD_KEY, "1", "entity", ("ONE",), "Confirmed")],
    )
    # A SanctionsEntry is read by its name wherever it is, here inside a feature
    c.eq(
        "a sanctions entry inside a feature",
        listings(
            "nested entry",
            _doc(
                _party(
                    "1",
                    f'<Profile ID="1" PartySubTypeID="3"><Feature FeatureTypeID="1">{_version(DEAD)}'
                    f'<SanctionsEntry ProfileID="1">{_measure("1", "NESTED")}</SanctionsEntry></Feature></Profile>',
                )
            ),
        ),
        [(DEAD_KEY, "1", "entity", ("NESTED",), "Confirmed")],
    )
    # The order rule holds for a DistinctParty anywhere, also inside the date of issue
    c.raises(
        "a party inside the date of issue",
        lambda: mod.parse_xml(_doc(ONE_PARTY, date=DATE.replace("</DateOfIssue>", '<DistinctParty FixedRef="1"/></DateOfIssue>'))),
        mod.ListFormatError,
        "the list file is not in the expected order (reference values first)",
    )
    # Text is read only from an element that holds no other element: a child element ends the reading of its parent's text
    c.raises(
        "a year that holds an element",
        lambda: mod.parse_xml(_doc(ONE_PARTY, date=DATE.replace("<Year>2026</Year>", "<Year>20<b/>26</Year>"))),
        mod.ListFormatError,
        "the list file has no DateOfIssue",
    )
    c.eq(
        "a programme comment that holds an element",
        listings("comment with a child", _doc(ONE_PARTY, f'<SanctionsEntry ProfileID="1">{_measure("1", "AB<i/>CD")}</SanctionsEntry>')),
        only_dead,
    )
    # A Profile outside any DistinctParty, before the first one: the file is read or refused as a list error, never another exception
    stray = _doc(ONE_PARTY, before_parties=f'<Profile ID="5" PartySubTypeID="3">{_feature(EATER)}</Profile>')
    try:
        mod.parse_xml(stray)
    except mod.ListFormatError:
        pass
    except Exception as exc:
        c.bad.append(f"a profile outside any party: raised {type(exc).__name__}: {exc}")
    # One key on two entries is looked up shorter entry id first, then by id, whatever the order in the file
    index = c.run(
        "entries out of order",
        lambda: mod.parse_xml(
            _doc(
                _party("9010", f'<Profile ID="1" PartySubTypeID="3">{_feature(DEAD)}</Profile>')
                + _party("900", f'<Profile ID="2" PartySubTypeID="3">{_feature(DEAD)}</Profile>')
            )
        ),
    )
    c.eq("lookup order by entry id", None if index is None else [x.entry_id for x in index.lookup(DEAD_KEY)], ["900", "9010"])
    return c.bad


def check_stored_form(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    index = c.run("parse", lambda: mod.parse_xml(synthetic_xml()))
    if index is None:
        return [*c.bad, "parse: gave no index"]
    stored = mod.to_json(index)
    c.eq("format version", stored["format_version"], 1)
    c.eq("round trip", c.run("read back", lambda: mod.from_json(stored)), index)
    c.raises(
        "an unknown format version",
        lambda: mod.from_json({**stored, "format_version": 2}),
        mod.ListFormatError,
        "the stored index could not be read",
    )
    c.raises(
        "a damaged stored index",
        lambda: mod.from_json({**stored, "listings": [["x"]]}),
        mod.ListFormatError,
        "the stored index could not be read",
    )
    c.raises("not an object", lambda: mod.from_json([]), mod.ListFormatError, "the stored index could not be read")
    c.raises(
        "an empty stored index",
        lambda: mod.from_json({**stored, "listings": []}),
        mod.ListFormatError,
        "the stored index could not be read",
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    for check in (check_recorded_extract, check_synthetic, check_refusals, check_stored_form, check_out_of_shape):
        try:
            out.extend(check(mod))
        except Exception as exc:
            out.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    return out
