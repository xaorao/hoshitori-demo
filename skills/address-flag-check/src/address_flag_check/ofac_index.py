"""The OFAC SDN list as an address index, read from OFAC's own "advanced" XML (``SDN_ADVANCED.XML``, inside ``SDN_ADVANCED.ZIP``).

The file (about 128 MB) is read as a stream with the standard library's expat parser; only the digital-currency features and the
programme tags of their entries are kept. A document type or an entity declaration is refused (no entity is ever expanded).

Address features are found by the text prefix "Digital Currency Address - " of their feature type, not by a fixed list of assets, so a new
asset OFAC adds is read without a code change. That idea, and looking the feature type up in the file's reference values, are adapted from
0xB10C's ofac-sanctioned-digital-currency-addresses (MIT, ``get_address_id`` and ``get_sanctioned_addresses``; credited in NOTICE.md).

What is kept for each address string: the string as published, its matching key and family by its own format (``normalize``), the asset
label as published, the entry id (``FixedRef``), the party type from the file's own reference values, the programme tags and OFAC's
reliability value. No name is read or kept.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from xml.parsers import expat

from .normalize import classify_published

NAMESPACE = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ADVANCED_XML"
FEATURE_PREFIX = "Digital Currency Address - "
XML_MEMBER = "SDN_ADVANCED.XML"
MAX_XML_BYTES = 512_000_000
CHUNK = 1 << 20
FORMAT_VERSION = 1
PARTY_TYPES = {"Individual": "individual", "Entity": "entity", "Transport": "transport"}


class ListFormatError(Exception):
    """The bytes are not a usable OFAC list. The message is fixed text."""


@dataclass(frozen=True)
class Listing:
    key: str
    published: str
    label: str
    family: str
    format: str | None
    format_ok: bool | None
    entry_id: str
    party_type: str
    programs: tuple[str, ...]
    reliability: str | None


@dataclass(frozen=True)
class ListIndex:
    date_of_issue: str
    xml_sha256: str
    xml_bytes: int
    features: int
    address_strings: int
    labels: tuple[str, ...]
    families: dict[str, int]
    listings: tuple[Listing, ...]

    def lookup(self, key: str) -> tuple[Listing, ...]:
        found = [x for x in self.listings if x.key == key]
        return tuple(sorted(found, key=lambda x: (len(x.entry_id), x.entry_id, x.label, x.published)))


class IndexBuilder:
    """Feed the XML in pieces, then ``close()`` for the index."""

    def __init__(self, max_bytes: int = MAX_XML_BYTES) -> None:
        self._max = max_bytes
        self._size = 0
        self._sha = hashlib.sha256()
        self._parser = expat.ParserCreate(namespace_separator="}")
        self._parser.buffer_text = True
        self._parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
        self._parser.StartDoctypeDeclHandler = self._refuse_declaration
        self._parser.EntityDeclHandler = self._refuse_declaration
        self._parser.StartElementHandler = self._start
        self._parser.EndElementHandler = self._end
        self._parser.CharacterDataHandler = self._text
        self._stack: list[str] = []
        self._capture: list[str] | None = None
        self._date: dict[str, str] = {}
        self._digital: dict[str, str] = {}
        self._types_done = False
        self._subtype_party: dict[str, str] = {}
        self._party_names: dict[str, str] = {}
        self._reliability: dict[str, str] = {}
        self._sanction_types: dict[str, str] = {}
        self._ref_id = ""
        self._fixed = ""
        self._profile = ""
        self._subtype = ""
        self._label: str | None = None
        self._version_reliability: str | None = None
        self._entry_profile = ""
        self._program_measure = False
        self._programs: dict[str, set[str]] = {}
        self._raw: list[tuple[str, str, str, str, str, str | None]] = []

    def _refuse_declaration(self, *_args: Any) -> None:
        raise ListFormatError("the list file declares a document type or entities, which are not accepted")

    def _text(self, data: str) -> None:
        if self._capture is not None:
            self._capture.append(data)

    def _start(self, name: str, attrs: dict[str, str]) -> None:
        if not self._stack and name != f"{NAMESPACE}}}Sanctions":
            raise ListFormatError("the file is not an OFAC SDN advanced XML file")
        local = name.rsplit("}", 1)[-1]
        parent = self._stack[-1] if self._stack else ""
        self._stack.append(local)
        if parent == "DateOfIssue" and local in ("Year", "Month", "Day"):
            self._capture = []
        elif local in ("FeatureType", "PartyType", "Reliability", "SanctionsType"):
            self._ref_id = attrs.get("ID", "")
            self._capture = []
        elif local == "PartySubType":
            self._subtype_party[attrs.get("ID", "")] = attrs.get("PartyTypeID", "")
        elif local == "DistinctParty":
            if not self._types_done:
                raise ListFormatError("the list file is not in the expected order (reference values first)")
            self._fixed = attrs.get("FixedRef", "")
        elif local == "Profile" and parent == "DistinctParty":
            self._profile = attrs.get("ID", "")
            self._subtype = attrs.get("PartySubTypeID", "")
        elif local == "Feature" and parent == "Profile":
            self._label = self._digital.get(attrs.get("FeatureTypeID", ""))
        elif local == "FeatureVersion" and self._label is not None:
            self._version_reliability = attrs.get("ReliabilityID")
        elif local == "VersionDetail" and parent == "FeatureVersion" and self._label is not None:
            self._capture = []
        elif local == "SanctionsEntry":
            self._entry_profile = attrs.get("ProfileID", "")
        elif local == "SanctionsMeasure" and parent == "SanctionsEntry":
            self._program_measure = self._sanction_types.get(attrs.get("SanctionsTypeID", "")) == "Program"
        elif local == "Comment" and parent == "SanctionsMeasure" and self._program_measure:
            self._capture = []

    def _end(self, name: str) -> None:
        local = self._stack.pop()
        text = "".join(self._capture) if self._capture is not None else ""
        self._capture = None
        parent = self._stack[-1] if self._stack else ""
        if parent == "DateOfIssue" and local in ("Year", "Month", "Day"):
            self._date[local] = text.strip()
        elif local == "FeatureType":
            if text.startswith(FEATURE_PREFIX):
                self._digital[self._ref_id] = text[len(FEATURE_PREFIX) :].strip()
        elif local == "FeatureTypeValues":
            self._types_done = True
        elif local == "PartyType":
            self._party_names[self._ref_id] = text.strip()
        elif local == "Reliability":
            self._reliability[self._ref_id] = text.strip()
        elif local == "SanctionsType":
            self._sanction_types[self._ref_id] = text.strip()
        elif local == "Feature":
            self._label = None
        elif local == "VersionDetail" and self._label is not None and parent == "FeatureVersion":
            self._raw.append((self._fixed, self._profile, self._subtype, self._label, text, self._version_reliability))
        elif local == "Comment" and parent == "SanctionsMeasure" and self._program_measure:
            tag = text.strip()
            if tag:
                self._programs.setdefault(self._entry_profile, set()).add(tag)

    def feed(self, chunk: bytes) -> None:
        self._size += len(chunk)
        if self._size > self._max:
            raise ListFormatError("the list file is larger than the allowed size")
        self._sha.update(chunk)
        try:
            self._parser.Parse(chunk, False)
        except expat.ExpatError as exc:
            raise ListFormatError("the list file is not well-formed XML") from exc

    def close(self) -> ListIndex:
        try:
            self._parser.Parse(b"", True)
        except expat.ExpatError as exc:
            raise ListFormatError("the list file is not well-formed XML") from exc
        try:
            year, month, day = (int(self._date[k]) for k in ("Year", "Month", "Day"))
        except (KeyError, ValueError):
            raise ListFormatError("the list file has no DateOfIssue") from None
        if not self._raw:
            raise ListFormatError("the list file holds no digital currency addresses")
        listings: list[Listing] = []
        families: dict[str, int] = {}
        for fixed, profile, subtype, label, published, reliability_id in self._raw:
            family, fmt, key, ok = classify_published(published)
            families[family] = families.get(family, 0) + 1
            party = PARTY_TYPES.get(self._party_names.get(self._subtype_party.get(subtype, ""), ""), "other")
            reliability = None if reliability_id is None else self._reliability.get(reliability_id)
            programs = tuple(sorted(self._programs.get(profile, set())))
            listings.append(Listing(key, published, label, family, fmt, ok, fixed, party, programs, reliability))
        return ListIndex(
            date_of_issue=f"{year:04d}-{month:02d}-{day:02d}",
            xml_sha256=self._sha.hexdigest(),
            xml_bytes=self._size,
            features=len(listings),
            address_strings=len({x.published for x in listings}),
            labels=tuple(sorted(set(self._digital.values()))),
            families=dict(sorted(families.items())),
            listings=tuple(listings),
        )


def parse_xml(data: bytes, chunk_size: int = CHUNK, max_bytes: int = MAX_XML_BYTES) -> ListIndex:
    builder = IndexBuilder(max_bytes)
    for start in range(0, len(data), chunk_size):
        builder.feed(data[start : start + chunk_size])
    return builder.close()


def parse_zip(data: bytes, should_stop: Callable[[], bool] | None = None) -> ListIndex:
    """The index of the one XML member of OFAC's ZIP. ``should_stop`` is asked between pieces; when it says so, TimeoutError is raised."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ListFormatError("the download is not a ZIP archive") from None
    if archive.namelist() != [XML_MEMBER]:
        raise ListFormatError(f"the ZIP archive must hold exactly one member, {XML_MEMBER}")
    builder = IndexBuilder()
    try:
        with archive.open(XML_MEMBER) as member:
            while chunk := member.read(CHUNK):
                if should_stop is not None and should_stop():
                    raise TimeoutError("the time budget ran out while reading the list")
                builder.feed(chunk)
    except (zipfile.BadZipFile, zlib.error, EOFError):
        raise ListFormatError("the ZIP archive could not be read") from None
    return builder.close()


def to_json(index: ListIndex) -> dict[str, Any]:
    return {
        "format_version": FORMAT_VERSION,
        "date_of_issue": index.date_of_issue,
        "xml_sha256": index.xml_sha256,
        "xml_bytes": index.xml_bytes,
        "features": index.features,
        "address_strings": index.address_strings,
        "labels": list(index.labels),
        "families": index.families,
        "listings": [
            [x.key, x.published, x.label, x.family, x.format, x.format_ok, x.entry_id, x.party_type, list(x.programs), x.reliability]
            for x in index.listings
        ],
    }


def from_json(data: object) -> ListIndex:
    try:
        if not isinstance(data, dict) or data["format_version"] != FORMAT_VERSION or not data["listings"]:
            raise ValueError
        listings = tuple(
            Listing(str(k), str(p), str(lab), str(fam), fmt, ok, str(entry), str(party), tuple(str(t) for t in progs), rel)
            for k, p, lab, fam, fmt, ok, entry, party, progs, rel in data["listings"]
        )
        return ListIndex(
            date_of_issue=str(data["date_of_issue"]),
            xml_sha256=str(data["xml_sha256"]),
            xml_bytes=int(data["xml_bytes"]),
            features=int(data["features"]),
            address_strings=int(data["address_strings"]),
            labels=tuple(str(x) for x in data["labels"]),
            families={str(k): int(v) for k, v in data["families"].items()},
            listings=listings,
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ListFormatError("the stored index could not be read") from None
