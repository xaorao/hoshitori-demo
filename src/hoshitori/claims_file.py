"""Claims that were read, as data for the Claims page: what a person wrote, how it was cut into parts, how far the readings agreed and
what the forecast in it would need before it could be scored.

``record`` makes one entry from a reading; ``read_claims`` reads a whole file strictly, so that the page cannot show a sentence a
preliminary page may not say, a quotation that is not in the claim, a status other than UNCHECKED, a result that nothing produced, a
text that would not show as it is written, or one of two values a file gives for the same key.
These are recorded readings of chosen claims, never a live check, and the page says so.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from hoshitori import claim_assumptions, claim_next_check, claim_preliminary
from hoshitori.claim_extract import UNREADABLE, Reading
from hoshitori.claim_graph import UNCHECKED, GraphError, NodeType, check_text
from hoshitori.claim_sketch import Sketch, sketch

KEYS: Final = (
    "id",
    "text",
    "model",
    "read_at",
    "runs",
    "page",
    "message",
    "reading",
    "disputes",
    "sketches",
    "next_check",
    "assets",
    "assumptions",
)
STATES: Final = ("STABLE", "UNSTABLE", "DISPUTED")
DISPUTES: Final = (
    "CAUSAL_WORD_WITHOUT_LINK",
    "NUMBER_WITHOUT_NODE",
    "NUMBER_WORD_WITHOUT_NODE",
    "TIME_WORD_WITHOUT_NODE",
    "QUOTE_OCCURS_TWICE",
    "SPAN_CROSSES_CLAUSES",
)
SKETCH_STATUSES: Final = ("RESOLVABLE", "NEEDS_CHOICES", "UNRESOLVED")
_ID: Final = re.compile(r"[a-z][a-z0-9_-]{0,39}")
_TIME: Final = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


class ClaimsError(ValueError):
    """A claims file that cannot be shown."""


@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    model: str
    read_at: str
    runs: int
    page: dict[str, Any] | None
    message: str | None
    reading: dict[str, Any] | None
    disputes: list[dict[str, Any]]
    sketches: list[dict[str, Any]]
    next_check: dict[str, Any] | None
    assets: list[str]
    assumptions: list[dict[str, Any]]


def _sketch_entry(node_id: str, s: Sketch) -> dict[str, Any]:
    """A forecast part's sketch as the file holds it."""
    return {
        "node_id": node_id,
        "status": s.status,
        "contract": s.contract,
        "asset": s.asset,
        "direction": s.direction,
        "magnitude": s.magnitude,
        "horizon_hours": s.horizon_hours,
        "choices": [{"field": c.field, "question": c.question, "options": list(c.options)} for c in s.choices],
        "reason": s.reason,
    }


def record(ident: str, text: str, reading: Reading, model: str, read_at: str, assets: tuple[str, ...]) -> dict[str, Any]:
    """The entry for one claim that was read; ``read_at`` is an RFC 3339 UTC time written by the caller."""
    graph = next((e.graph for e in reading.extractions if e.graph is not None), None)
    page = None
    nxt = None
    sketches: list[dict[str, Any]] = []
    if reading.page is not None:
        p = reading.page
        page = {
            "banner": p.banner,
            "headline": p.headline,
            "parts": [{"node_id": x.node_id, "kind": x.kind, "status": x.status, "quote": x.quote, "line": x.line} for x in p.parts],
            "next_step": p.next_step,
            "triage": p.triage,
        }
        for part in p.parts:
            if part.kind == NodeType.FORECAST.value:
                sketches.append(_sketch_entry(part.node_id, sketch(part.quote, assets)))
    supposed: list[dict[str, Any]] = []
    if graph is not None:
        for a in claim_assumptions.assumptions(graph, {}):
            changed = [{"node_id": i, "before": b, "after": f} for i, b, f in a.changed]
            supposed.append({"node_id": a.node_id, "kind": a.kind, "changed": changed, "sentence": a.sentence})
        n = claim_next_check.select_next_check(graph, {})
        c = n.check
        nxt = {
            "status": n.status,
            "node_id": n.node_id,
            "words": n.words,
            "check_id": c.check_id if c else None,
            "instruction": c.instruction if c else None,
            "source": c.source if c else None,
            "reason": n.reason,
        }
    d = reading.decomposition
    return {
        "id": ident,
        "text": text,
        "model": model,
        "read_at": read_at,
        "runs": len(reading.extractions),
        "page": page,
        "message": reading.message,
        "reading": None
        if d is None
        else {
            "state": d.state,
            "score": f"{Decimal(str(d.score)):.2f}",
            "validated": d.validated,
            "unmatched": list(d.unmatched),
            "type_conflicts": list(d.type_conflicts),
        },
        "disputes": [{"kind": x.kind, "start": x.start, "end": x.end, "detail": x.detail} for x in reading.disputes],
        "sketches": sketches,
        "next_check": nxt,
        "assets": list(assets),
        "assumptions": supposed,
    }


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ClaimsError(f"{what} is a non-empty text")
    return value


def _shown(value: str, what: str) -> None:
    """A text the page shows is held to the rule for a claim's own text (``claim_graph.check_text``), so that it shows as written."""
    try:
        check_text(value)
    except GraphError as exc:
        raise ClaimsError(f"{what} breaks the rule for a claim's text: {exc}") from exc


def _page(page: object, text: str, id_: str) -> list[tuple[str, str]]:
    if not isinstance(page, dict) or tuple(page) != ("banner", "headline", "parts", "next_step", "triage"):
        raise ClaimsError(f"{id_}: a page has banner, headline, parts, next_step and triage")
    if page["banner"] != claim_preliminary.BANNER or page["headline"] != claim_preliminary.HEADLINE:
        raise ClaimsError(f"{id_}: the banner and the headline are the preliminary ones")
    parts = page["parts"]
    if not isinstance(parts, list) or not 1 <= len(parts) <= 8:
        raise ClaimsError(f"{id_}: a page has 1 to 8 parts")
    quotes: list[str] = []
    kinds: dict[str, str] = {}
    for part in parts:
        if not isinstance(part, dict) or tuple(part) != ("node_id", "kind", "status", "quote", "line"):
            raise ClaimsError(f"{id_}: a part has node_id, kind, status, quote and line")
        node_id = _text(part["node_id"], f"{id_}: a part's id")
        if node_id in kinds or part["kind"] not in {t.value for t in NodeType}:
            raise ClaimsError(f"{id_}: a part has its own id and a known kind")
        if part["status"] != UNCHECKED:
            raise ClaimsError(f"{id_}: {node_id}: a preliminary page has no status but UNCHECKED")
        quote = _text(part["quote"], f"{id_}: {node_id}: the quote")
        _shown(quote, f"{id_}: {node_id}: the quote")
        if part["kind"] != NodeType.ASSUMPTION_INFERRED.value and quote not in text:
            raise ClaimsError(f"{id_}: {node_id}: the quote is not words of the claim")
        kinds[node_id] = part["kind"]
        quotes.append(quote)
    for part in parts:
        if not isinstance(part["line"], str) or not claim_preliminary.allowed(part["line"], tuple(quotes)):
            raise ClaimsError(f"{id_}: {part['node_id']}: not a sentence a preliminary page may say")
    if page["next_step"] not in (claim_preliminary.NEXT_STEP, claim_preliminary.NOTHING_TESTABLE):
        raise ClaimsError(f"{id_}: the next step is not one of the two a preliminary page may say")
    testable = page["next_step"] == claim_preliminary.NEXT_STEP
    if page["triage"] != ("INVESTIGATE" if testable else "NOT_MARKET_TESTABLE"):
        raise ClaimsError(f"{id_}: the triage does not match the next step")
    return [(p["node_id"], p["quote"]) for p in parts if p["kind"] == NodeType.FORECAST.value]


def _reading(value: object, id_: str) -> None:
    keys = ("state", "score", "validated", "unmatched", "type_conflicts")
    if not isinstance(value, dict) or tuple(value) != keys:
        raise ClaimsError(f"{id_}: a reading has {', '.join(keys)}")
    if value["state"] not in STATES or not isinstance(value["validated"], bool):
        raise ClaimsError(f"{id_}: a reading has a known state and says whether it was validated")
    if not isinstance(value["score"], str) or re.fullmatch(r"(?:0\.\d\d|1\.00)", value["score"]) is None:
        raise ClaimsError(f"{id_}: the score is a share written with two places")
    for name in ("unmatched", "type_conflicts"):
        if not isinstance(value[name], list) or not all(isinstance(x, str) for x in value[name]):
            raise ClaimsError(f"{id_}: {name} is a list of part ids")


def _disputes(value: object, text: str, id_: str) -> None:
    if not isinstance(value, list):
        raise ClaimsError(f"{id_}: disputes are a list")
    for d in value:
        if not isinstance(d, dict) or tuple(d) != ("kind", "start", "end", "detail"):
            raise ClaimsError(f"{id_}: a dispute has kind, start, end and detail")
        s, e = d["start"], d["end"]
        if (
            d["kind"] not in DISPUTES
            or isinstance(s, bool)
            or isinstance(e, bool)
            or not (isinstance(s, int) and isinstance(e, int) and 0 <= s < e <= len(text))
        ):
            raise ClaimsError(f"{id_}: a dispute is of a known kind and lies inside the claim")
        _text(d["detail"], f"{id_}: a dispute's detail")
        _shown(d["detail"], f"{id_}: a dispute's detail")


def _sketches(value: object, forecasts: list[tuple[str, str]], assets: tuple[str, ...], id_: str) -> None:
    keys = ("node_id", "status", "contract", "asset", "direction", "magnitude", "horizon_hours", "choices", "reason")
    if not isinstance(value, list) or [s.get("node_id") if isinstance(s, dict) else None for s in value] != [i for i, _ in forecasts]:
        raise ClaimsError(f"{id_}: there is one sketch for each forecast part, in order")
    for k, s in enumerate(value):
        node_id, quote = forecasts[k]  # the ids were compared one by one above, so there is one forecast for each sketch
        if tuple(s) != keys or s["status"] not in SKETCH_STATUSES:
            raise ClaimsError(f"{id_}: a sketch has {', '.join(keys)} and a known status")
        if (s["contract"] == "D") != (s["status"] == "RESOLVABLE") or (s["status"] == "UNRESOLVED") != isinstance(s["reason"], str):
            raise ClaimsError(f"{id_}: a sketch has a contract only when resolvable and a reason only when unresolved")
        choices = s["choices"]
        if not isinstance(choices, list) or bool(choices) != (s["status"] == "NEEDS_CHOICES"):
            raise ClaimsError(f"{id_}: a sketch has choices exactly when it needs them")
        for c in choices:
            if not isinstance(c, dict) or tuple(c) != ("field", "question", "options") or not isinstance(c["options"], list):
                raise ClaimsError(f"{id_}: a choice has field, question and options")
            _text(c["question"], f"{id_}: a choice's question")
        if s != _sketch_entry(node_id, sketch(quote, assets)):  # nothing the forecast's words and the assets do not give
            raise ClaimsError(f"{id_}: a sketch is the one its forecast part's words give")


def _next_check(value: object, page: dict[str, Any] | None, id_: str) -> None:
    keys = ("status", "node_id", "words", "check_id", "instruction", "source", "reason")
    if page is None:
        if value is not None:
            raise ClaimsError(f"{id_}: a claim that could not be read has no next check")
        return
    if not isinstance(value, dict) or tuple(value) != keys:
        raise ClaimsError(f"{id_}: a next check has {', '.join(keys)}")
    if value["status"] not in (claim_next_check.CHECK, claim_next_check.NO_AVAILABLE_CHECK, claim_next_check.NOTHING_LEFT):
        raise ClaimsError(f"{id_}: a next check is a check, no available check, or nothing left")
    quotes = {p["node_id"]: p["quote"] for p in page["parts"]}
    if value["node_id"] is not None and quotes.get(value["node_id"]) != value["words"]:
        raise ClaimsError(f"{id_}: the next check names a part of the page and says what that part says")
    if value["node_id"] is None and value["words"] is not None:
        raise ClaimsError(f"{id_}: the next check names a part of the page and says what that part says")
    registered = {c.check_id: (c.instruction, c.source) for c in claim_next_check.REGISTRY}
    if value["status"] == claim_next_check.CHECK:
        if (
            registered.get(value["check_id"]) != (value["instruction"], value["source"])
            or value["reason"] is not None
            or value["node_id"] is None
        ):
            raise ClaimsError(f"{id_}: a check is one that is registered, with its own words, for a part")
    elif (
        value["check_id"] is not None
        or value["instruction"] is not None
        or value["source"] is not None
        or not isinstance(value["reason"], str)
    ):
        raise ClaimsError(f"{id_}: no check is offered without a reason, and a reason comes with no check")
    elif value["status"] == claim_next_check.NO_AVAILABLE_CHECK:
        kinds = {p["node_id"]: NodeType(p["kind"]) for p in page["parts"]}
        kind = kinds.get(value["node_id"])
        if value["reason"] not in (claim_next_check.TRIED_REASON, claim_next_check.NO_CHECK_REASONS.get(kind) if kind else None):
            raise ClaimsError(f"{id_}: the reason for offering no check is the product's own for that part")
    elif (value["reason"], value["node_id"] is None) not in (
        (claim_next_check.AGAINST_REASON, False),
        (claim_next_check.DONE_REASON, True),
    ):
        raise ClaimsError(f"{id_}: nothing is left only because a part does not hold, or, naming no part, because every part was checked")


def _assumptions(value: object, page: dict[str, Any] | None, id_: str) -> None:
    if not isinstance(value, list) or (page is None and value):
        raise ClaimsError(f"{id_}: assumptions are a list, and a claim that could not be read has none")
    quotes = {} if page is None else {p["node_id"]: p["quote"] for p in page["parts"]}
    kinds = (claim_assumptions.TRUE, claim_assumptions.FALSE, claim_assumptions.REMOVED)
    seen: set[tuple[str, str]] = set()
    for a in value:
        if not isinstance(a, dict) or tuple(a) != ("node_id", "kind", "changed", "sentence"):
            raise ClaimsError(f"{id_}: an assumption has node_id, kind, changed and sentence")
        key = (a["node_id"], a["kind"])
        if a["node_id"] not in quotes or a["kind"] not in kinds or key in seen:
            raise ClaimsError(f"{id_}: an assumption is about a part of the page, of a known kind, once")
        seen.add(key)
        changed = a["changed"]
        if not isinstance(changed, list) or not all(_change(c, quotes, a["node_id"]) for c in changed):
            raise ClaimsError(f"{id_}: what an assumption changes is other parts of the page, each from one status to another")
        if a["sentence"] != claim_assumptions.sentence_of(a["kind"], claim_assumptions.short(quotes[a["node_id"]]), len(changed)):
            raise ClaimsError(f"{id_}: the sentence of an assumption is the one its kind, its part and its count give")


def _change(c: object, quotes: dict[str, str], own: str) -> bool:
    return (
        isinstance(c, dict)
        and tuple(c) == ("node_id", "before", "after")
        and c["node_id"] in quotes
        and c["node_id"] != own
        and isinstance(c["before"], str)
        and isinstance(c["after"], str)
        and c["before"] != c["after"]
    )


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """One object of the file. A key given twice is refused: an ordinary reader keeps the last of the two values, so the page would show
    one of two things the file says."""
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ClaimsError(f"the claims file gives the key {key!r} twice in one object")
        out[key] = value
    return out


def read_claims(data: bytes) -> tuple[Claim, ...]:
    """The claims of a file, each checked against its own text."""
    try:
        doc = json.loads(data.decode("utf-8"), object_pairs_hook=_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClaimsError(f"the claims file is not JSON: {exc}") from exc
    if not isinstance(doc, list) or not doc:
        raise ClaimsError("the claims file is a list of at least one claim")
    out: list[Claim] = []
    for item in doc:
        if not isinstance(item, dict) or tuple(item) != KEYS:
            raise ClaimsError(f"a claim has {', '.join(KEYS)}, in that order")
        ident = item["id"]
        if not isinstance(ident, str) or _ID.fullmatch(ident) is None:
            raise ClaimsError("a claim id is lower-case words joined by - or _")
        if ident in {c.id for c in out}:
            raise ClaimsError(f"{ident}: a claim is named once")
        text = _text(item["text"], f"{ident}: the text")
        if unicodedata.normalize("NFC", text) != text:
            raise ClaimsError(f"{ident}: the text is not in NFC form")
        _shown(text, f"{ident}: the text")
        _text(item["model"], f"{ident}: the model")
        _shown(item["model"], f"{ident}: the model")
        if not isinstance(item["read_at"], str) or _TIME.fullmatch(item["read_at"]) is None:
            raise ClaimsError(f"{ident}: read_at is a UTC time to the second")
        if isinstance(item["runs"], bool) or not isinstance(item["runs"], int) or not 1 <= item["runs"] <= 9:
            raise ClaimsError(f"{ident}: runs is a whole number from 1 to 9")
        if (item["page"] is None) == (item["message"] is None):
            raise ClaimsError(f"{ident}: a claim has a page or a message, never both and never neither")
        if item["message"] is not None and item["message"] != UNREADABLE:
            raise ClaimsError(f"{ident}: the only message is that the claim could not be read")
        if not isinstance(item["assets"], list) or not all(isinstance(a, str) and a.isupper() and a.isalnum() for a in item["assets"]):
            raise ClaimsError(f"{ident}: assets are symbols in capitals")
        forecasts = _page(item["page"], text, ident) if item["page"] is not None else []
        if item["reading"] is not None:
            _reading(item["reading"], ident)
        _disputes(item["disputes"], text, ident)
        _sketches(item["sketches"], forecasts, tuple(item["assets"]), ident)
        _next_check(item["next_check"], item["page"], ident)
        _assumptions(item["assumptions"], item["page"], ident)
        out.append(Claim(**item))
    return tuple(out)
