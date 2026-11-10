"""The skill: read a claim by its words alone and say what it is made of and what nothing has checked.

Nothing is looked up. Every mark and every finding carries the exact words it rests on (a substring of the claim as normalised), every part
is ``UNCHECKED``, and there is no stance, probability or call: the only triage is INVESTIGATE (the claim names an asset or a number, so a
source could test it) or NOT_MARKET_TESTABLE. A claim is data: words in it that look like instructions are read as words.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Final

from .schema import TOOL_NAME, validate_input

SCHEMA_VERSION = "1.0"
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
MAX_CLAUSES: Final = 8
ASSETS: Final = {"BTC": ("bitcoin", "btc"), "ETH": ("ethereum", "ether", "eth"), "SOL": ("solana", "sol")}
CAUSAL: Final = ("therefore", "because", "hence", "thus", "consequently", "since")
CONDITIONAL: Final = ("if", "unless", "provided", "assuming")
FUTURE: Final = ("will", "may", "might", "could", "should", "would", "going to")
UNITS: Final = r"(?:%|k\b|m\b|b\b|x\b|bps\b|usdt?\b|usd\b|btc\b|eth\b|sol\b|days?\b|hours?\b|weeks?\b|months?\b|minutes?\b)"
NUMBER: Final = re.compile(
    rf"(?<![A-Za-z0-9_.])(?P<sign>-)?\$?(?P<digits>[0-9]+(?:,[0-9]{{3}})*(?:\.[0-9]+)?)(?P<unit>\s?{UNITS})?", re.IGNORECASE
)
NUMBER_WORD: Final = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|fifty|hundred|thousand|million|billion|half|double|triple)\b",
    re.IGNORECASE,
)
TIME_WORD: Final = re.compile(
    r"\b(?:today|tomorrow|tonight|yesterday|monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|april|june|july|august|september"
    r"|october|november|december|(?:next|last|this|every) (?:day|week|weekend|month|quarter|year)"
    r"|end of (?:the )?(?:day|week|month|quarter|year))\b",
    re.IGNORECASE,
)
DURATION: Final = re.compile(r"[0-9]+\s?(?:minutes?|hours?|days?|weeks?|months?)\b", re.IGNORECASE)
CASHTAG: Final = re.compile(r"\$([A-Za-z]{2,10})\b")
_BREAK: Final = re.compile(
    r"(?<=[.!?;])\s+|,\s+(?=(?:therefore|because|hence|thus|consequently|so|since|if|unless|while|which|but|and)\b)"
    r"|\s+(?=(?:therefore|because|hence|thus|consequently)\b)",
    re.IGNORECASE,
)
FINDINGS: Final = {
    "CAUSAL_LINK_PRESENT": "The claim says one thing leads to or follows from another. Nothing here tests whether that link holds.",
    "CONDITION_PRESENT": "Part of the claim holds only if a condition does. The condition has not been checked.",
    "FORECAST_NO_HORIZON": "The claim looks ahead but names no time within which it must happen, so it cannot be settled as written.",
    "FORECAST_NO_LEVEL": "The claim looks ahead but names no number or size, so it cannot be told right or wrong as written.",
    "FORECAST_NO_ASSET": "The claim looks ahead but names no asset, so no price could settle it.",
    "NUMBER_WITHOUT_UNIT": "A number has no unit or currency next to it, so what it measures is not clear.",
    "NUMBER_IN_WORDS": "A number is written in words. Only digits are read here, so it is not counted.",
    "TIME_IN_WORDS": "A time is given in words. It is marked and not turned into a date.",
    "NO_ASSET_NAMED": "The claim names none of the assets this skill knows (BTC, ETH, SOL).",
    "UNKNOWN_CASHTAG": "The claim names an asset by a cashtag this skill does not know, so it is left unresolved.",
    "MANY_CLAUSES": f"The claim has more than {MAX_CLAUSES} clauses. Choose the one claim to audit.",
}
NOT_CHECKED = "Nothing was checked against any source: no part of the claim is called true or false, and no stance or call is given."
NOT_FETCHED = "Computed from the words alone; nothing was fetched and no model was used."


def _clauses(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _BREAK.finditer(text):
        if text[start : m.start()].strip():
            spans.append((start, m.start()))
        start = m.end()
    if text[start:].strip():
        spans.append((start, len(text)))
    return [(a + (len(text[a:b]) - len(text[a:b].lstrip())), b - (len(text[a:b]) - len(text[a:b].rstrip()))) for a, b in spans]


def _word_marks(text: str, words: tuple[str, ...], kind: str) -> list[dict[str, Any]]:
    out = []
    for w in words:
        for m in re.finditer(rf"\b{re.escape(w)}\b", text, re.IGNORECASE):
            out.append({"kind": kind, "start": m.start(), "end": m.end(), "text": m.group(0)})
    return out


def read(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[dict[str, Any]], str]:
    """The clauses, the marks, the assets named, the findings (each with its words) and the triage."""
    marks: list[dict[str, Any]] = []
    for kind, words in (("CAUSAL", CAUSAL), ("CONDITIONAL", CONDITIONAL), ("FUTURE", FUTURE)):
        marks += _word_marks(text, words, kind)
    for m in NUMBER.finditer(text):
        marks.append({"kind": "NUMBER", "start": m.start(), "end": m.end(), "text": m.group(0), "unit": bool(m.group("unit"))})
    marks += [{"kind": "NUMBER_WORD", "start": m.start(), "end": m.end(), "text": m.group(0)} for m in NUMBER_WORD.finditer(text)]
    marks += [{"kind": "TIME_WORD", "start": m.start(), "end": m.end(), "text": m.group(0)} for m in TIME_WORD.finditer(text)]
    assets: list[str] = []
    for symbol, names in ASSETS.items():
        for name in names:
            for m in re.finditer(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", text, re.IGNORECASE):
                marks.append({"kind": "ASSET", "start": m.start(), "end": m.end(), "text": m.group(0), "asset": symbol})
                if symbol not in assets:
                    assets.append(symbol)
    unknown = [m for m in CASHTAG.finditer(text) if m.group(1).upper() not in ASSETS and m.group(1).lower() not in sum(ASSETS.values(), ())]
    marks.sort(key=lambda m: (m["start"], m["kind"], m["end"]))
    has = {
        k: [m for m in marks if m["kind"] == k] for k in ("CAUSAL", "CONDITIONAL", "FUTURE", "NUMBER", "NUMBER_WORD", "TIME_WORD", "ASSET")
    }
    spans = _clauses(text)
    findings: list[dict[str, Any]] = []

    def add(code: str, found: list[dict[str, Any]]) -> None:
        findings.append({"code": code, "text": FINDINGS[code], "words": sorted({m["text"] for m in found})})

    if has["CAUSAL"]:
        add("CAUSAL_LINK_PRESENT", has["CAUSAL"])
    if has["CONDITIONAL"]:
        add("CONDITION_PRESENT", has["CONDITIONAL"])
    if has["FUTURE"]:
        if not has["TIME_WORD"] and not DURATION.search(text):
            add("FORECAST_NO_HORIZON", has["FUTURE"])
        if not has["NUMBER"] and not has["NUMBER_WORD"]:
            add("FORECAST_NO_LEVEL", has["FUTURE"])
        if not has["ASSET"] and not unknown:
            add("FORECAST_NO_ASSET", has["FUTURE"])
    bare = [m for m in has["NUMBER"] if not m["unit"] and not m["text"].lstrip("-").startswith("$")]
    if bare:
        add("NUMBER_WITHOUT_UNIT", bare)
    if has["NUMBER_WORD"]:
        add("NUMBER_IN_WORDS", has["NUMBER_WORD"])
    if has["TIME_WORD"]:
        add("TIME_IN_WORDS", has["TIME_WORD"])
    if not has["ASSET"] and not unknown:
        findings.append({"code": "NO_ASSET_NAMED", "text": FINDINGS["NO_ASSET_NAMED"], "words": []})
    if unknown:
        findings.append({"code": "UNKNOWN_CASHTAG", "text": FINDINGS["UNKNOWN_CASHTAG"], "words": sorted({m.group(0) for m in unknown})})
    if len(spans) > MAX_CLAUSES:
        findings.append({"code": "MANY_CLAUSES", "text": FINDINGS["MANY_CLAUSES"], "words": []})
    clauses = []
    for n, (a, b) in enumerate(spans):
        inside = [m["kind"] for m in marks if a <= m["start"] < b]
        first = text[a:b].split(" ", 1)[0].lower().strip(",")
        hint = (
            "CAUSAL_LINK" if first in CAUSAL else "CONDITION" if first in CONDITIONAL else "FORECAST" if "FUTURE" in inside else "STATEMENT"
        )
        clauses.append({"n": n, "start": a, "end": b, "text": text[a:b], "kind_hint": hint, "status": "UNCHECKED"})
    triage = "INVESTIGATE" if has["ASSET"] or has["NUMBER"] or unknown else "NOT_MARKET_TESTABLE"
    return clauses, marks, assets, findings, triage


def run(args: object, *, now: dt.datetime | None = None) -> dict[str, Any]:
    text = validate_input(args)
    now = now or dt.datetime.now(dt.UTC)
    clauses, marks, assets, findings, triage = read(text)
    head = (
        f"{len(clauses)} clause{'s' if len(clauses) != 1 else ''}, {len(findings)} structural "
        f"point{'s' if len(findings) != 1 else ''}; nothing has been checked. Triage: {triage.replace('_', ' ').lower()}."
    )
    points = [f"{f['text']}" + (f" ({', '.join(repr(w) for w in f['words'])})" if f["words"] else "") for f in findings]
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": "ok",
        "data_mode": "unknown",
        "as_of": now.strftime(TIME_FORMAT),
        "request": {"characters": len(text)},
        "data": {"triage": triage, "clauses": clauses, "marks": marks, "findings": findings, "assets": assets},
        "summary": {"headline": head, "key_points": points},
        "availability": {"claim": "available"},
        "warnings": [NOT_CHECKED, NOT_FETCHED],
    }
