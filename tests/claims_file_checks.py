"""Checks of the claims file (``hoshitori.claims_file``), runnable against a mutant: the entry made from a reading, and every way a file
can be wrong."""

from __future__ import annotations

import copy
import json
import types
from typing import Any

from hoshitori import claim_extract
from hoshitori.model_client import ModelClient
from tests.claim_extract_checks import good, reply
from tests.claim_graph_checks import TEXT
from tests.model_client_checks import Script, answer

AT = "2026-10-30T12:00:00Z"


def made(
    mod: types.ModuleType,
    replies: tuple[Any, ...] | None = None,
    text: str = TEXT,
    ident: str = "golden",
    assets: tuple[str, ...] = ("SOL",),
) -> dict[str, Any]:
    script = Script(*(replies or (reply(good(), 1), reply(good(), 2), reply(good(), 3))))
    reading = claim_extract.read_claim(ModelClient(script), text, claim_extract.Config("m"), assets)
    return mod.record(ident, text, reading, "m", AT, assets)  # type: ignore[no-any-return]


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, data: object, says: str) -> None:
        raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        try:
            self.mod.read_claims(raw)
        except self.mod.ClaimsError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def changed(base: dict[str, Any], *path: Any, to: Any) -> list[dict[str, Any]]:
    doc = copy.deepcopy(base)
    target: Any = doc
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = to
    return [doc]


def check_record(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    r = made(mod)
    c.eq("keys", tuple(r), mod.KEYS)
    c.eq("header", (r["id"], r["text"], r["model"], r["read_at"], r["runs"], r["message"]), ("golden", TEXT, "m", AT, 3, None))
    c.eq(
        "the page",
        (r["page"]["banner"], r["page"]["headline"], r["page"]["triage"]),
        ("Preliminary: read by a machine, nothing checked yet.", "Not yet independently supported.", "INVESTIGATE"),
    )
    c.eq(
        "parts",
        [(p["node_id"], p["kind"], p["status"]) for p in r["page"]["parts"]],
        [
            ("obs", "OBSERVATION", "UNCHECKED"),
            ("who", "INTERPRETATION", "UNCHECKED"),
            ("why", "CAUSAL_LINK", "UNCHECKED"),
            ("up", "FORECAST", "UNCHECKED"),
            ("ident", "ASSUMPTION_INFERRED", "UNCHECKED"),
        ],
    )
    c.eq("the reading", r["reading"], {"state": "STABLE", "score": "1.00", "validated": False, "unmatched": [], "type_conflicts": []})
    c.eq("no disputes", r["disputes"], [])
    c.eq(
        "a sketch for the forecast",
        [(s["node_id"], s["status"], s["asset"], s["direction"], s["magnitude"]) for s in r["sketches"]],
        [("up", "NEEDS_CHOICES", "SOL", "UP", "15 percent")],
    )
    c.eq(
        "its choices",
        [(x["field"], x["options"]) for x in r["sketches"][0]["choices"]],
        [("horizon", ["24 hours", "7 days"]), ("size", ["direction only", "leave it unscored"])],
    )
    c.eq(
        "the sketch's other fields",
        {k: r["sketches"][0][k] for k in ("contract", "horizon_hours", "reason")},
        {"contract": None, "horizon_hours": None, "reason": None},
    )
    two = made(mod, (reply(good(), 1), answer(None, 2, finish="length"), answer(None, 3, finish="length")))
    c.eq("one reading is a page and no comparison", (two["runs"], two["reading"], two["page"] is not None), (3, None, True))
    none = made(mod, (answer(None, 1, finish="length"),) * 3)
    c.eq(
        "no reading is a message",
        (none["page"], none["message"], none["reading"], none["sketches"]),
        (None, "This claim could not be read automatically.", None, []),
    )
    short = good(nodes=good()["nodes"][:2] + good()["nodes"][3:], links=[], implied=[])
    d = made(mod, (reply(short, 1), reply(short, 2), reply(short, 3)))
    c.eq(
        "a dispute is recorded where it is",
        [(x["kind"], TEXT[x["start"] : x["end"]]) for x in d["disputes"]],
        [("CAUSAL_WORD_WITHOUT_LINK", "therefore")],
    )
    c.eq("and the state says so", d["reading"]["state"], "DISPUTED")
    c.eq("a dispute's detail", d["disputes"][0]["detail"], "a causal word that no causal-link node contains")
    got = mod.read_claims(json.dumps([r, none | {"id": "unread"}, d | {"id": "short"}]).encode())
    c.eq("a file of three is read in order", [x.id for x in got], ["golden", "unread", "short"])
    c.eq(
        "the next check of a fresh reading",
        r["next_check"],
        {
            "status": "CHECK",
            "node_id": "obs",
            "words": "Whales are accumulating SOL",
            "check_id": "compare_exchange_candles",
            "instruction": "Compare the figure with the exchange's own candles for the same period.",
            "source": "the exchange's public price chart",
            "reason": None,
        },
    )
    c.eq("no page, no next check", none["next_check"], None)
    c.eq("the symbols the claim was read with", r["assets"], ["SOL"])
    c.eq(
        "the constants",
        (mod.STATES, mod.DISPUTES, mod.SKETCH_STATUSES),
        (
            ("STABLE", "UNSTABLE", "DISPUTED"),
            (
                "CAUSAL_WORD_WITHOUT_LINK",
                "NUMBER_WITHOUT_NODE",
                "NUMBER_WORD_WITHOUT_NODE",
                "TIME_WORD_WITHOUT_NODE",
                "QUOTE_OCCURS_TWICE",
                "SPAN_CROSSES_CLAUSES",
            ),
            ("RESOLVABLE", "NEEDS_CHOICES", "UNRESOLVED"),
        ),
    )
    c.eq("frozen", mod.Claim.__dataclass_params__.frozen, True)
    return c.bad


def check_reading_a_file(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    r = made(mod)
    none = made(mod, (answer(None, 1, finish="length"),) * 3, ident="unread")
    got = mod.read_claims(json.dumps([r, none]).encode())
    c.eq(
        "two claims",
        [(x.id, x.page is not None, x.message) for x in got],
        [("golden", True, None), ("unread", False, "This claim could not be read automatically.")],
    )
    c.eq("what is kept", (got[0].text, got[0].runs, got[0].reading["state"]), (TEXT, 3, "STABLE"))
    c.refuses("not JSON", b"{", "the claims file is not JSON: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)")
    c.refuses("not UTF-8", b"\xff", "the claims file is not JSON: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte")
    c.refuses("an object", {}, "the claims file is a list of at least one claim")
    c.refuses("an empty list", [], "the claims file is a list of at least one claim")
    keys = "id, text, model, read_at, runs, page, message, reading, disputes, sketches, next_check, assets, assumptions"
    c.refuses("a text instead of a claim", ["x"], f"a claim has {keys}, in that order")
    c.refuses("a missing key", [{k: v for k, v in r.items() if k != "model"}], f"a claim has {keys}, in that order")
    c.refuses("keys in another order", [dict(reversed(list(r.items())))], f"a claim has {keys}, in that order")
    for bad in ("", "Golden", "1a", "a b", "x" * 41):
        c.refuses(f"id {bad!r}", changed(r, "id", to=bad), "a claim id is lower-case words joined by - or _")
    c.eq("an id of forty characters", len(mod.read_claims(json.dumps(changed(r, "id", to="x" * 40)).encode())), 1)
    c.refuses("an id used twice", [r, r], "golden: a claim is named once")
    c.refuses("empty text", changed(r, "text", to=""), "golden: the text is a non-empty text")
    c.refuses("padded text", changed(r, "text", to=" " + TEXT), "golden: the text is a non-empty text")
    c.refuses("not NFC", changed(r, "text", to="é"), "golden: the text is not in NFC form")
    c.refuses("empty model", changed(r, "model", to=""), "golden: the model is a non-empty text")
    for bad in ("2026-10-30", "2026-10-30T12:00:00", "2026-10-30T12:00:00.5Z", 5):
        c.refuses(f"read_at {bad!r}", changed(r, "read_at", to=bad), "golden: read_at is a UTC time to the second")
    for bad in (0, 10, True, "3", 1.5):
        c.refuses(f"runs {bad!r}", changed(r, "runs", to=bad), "golden: runs is a whole number from 1 to 9")
    c.eq("runs of nine", len(mod.read_claims(json.dumps(changed(r, "runs", to=9)).encode())), 1)
    c.refuses(
        "neither a page nor a message",
        changed(none, "message", to=None),
        "unread: a claim has a page or a message, never both and never neither",
    )
    c.refuses(
        "both",
        changed(r, "message", to="This claim could not be read automatically."),
        "golden: a claim has a page or a message, never both and never neither",
    )
    c.refuses("another message", changed(none, "message", to="Verified."), "unread: the only message is that the claim could not be read")
    return c.bad


def check_page(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    r = made(mod)
    c.refuses("a page that is not a page", changed(r, "page", to={}), "golden: a page has banner, headline, parts, next_step and triage")
    c.refuses(
        "another banner", changed(r, "page", "banner", to="Verified."), "golden: the banner and the headline are the preliminary ones"
    )
    c.refuses(
        "another headline",
        changed(r, "page", "headline", to="Confirmed by sources."),
        "golden: the banner and the headline are the preliminary ones",
    )
    c.refuses("no parts", changed(r, "page", "parts", to=[]), "golden: a page has 1 to 8 parts")
    c.refuses("nine parts", changed(r, "page", "parts", to=r["page"]["parts"] * 2), "golden: a page has 1 to 8 parts")
    c.refuses("a part that is not one", changed(r, "page", "parts", 0, to={}), "golden: a part has node_id, kind, status, quote and line")
    c.refuses(
        "a part's id used twice", changed(r, "page", "parts", 1, "node_id", to="obs"), "golden: a part has its own id and a known kind"
    )
    c.refuses("an unknown kind", changed(r, "page", "parts", 0, "kind", to="VERDICT"), "golden: a part has its own id and a known kind")
    c.refuses(
        "a result on a part",
        changed(r, "page", "parts", 0, "status", to="SUPPORTED"),
        "golden: obs: a preliminary page has no status but UNCHECKED",
    )
    c.refuses(
        "a quote that is not the claim's",
        changed(r, "page", "parts", 0, "quote", to="Whales are selling SOL"),
        "golden: obs: the quote is not words of the claim",
    )
    c.refuses("an empty quote", changed(r, "page", "parts", 0, "quote", to=""), "golden: obs: the quote is a non-empty text")
    c.eq("an inferred step's words need not be the claim's", len(mod.read_claims(json.dumps([r]).encode())), 1)
    c.refuses(
        "a verdict as a line",
        changed(r, "page", "parts", 0, "line", to="The observation “Whales are accumulating SOL” is confirmed."),
        "golden: obs: not a sentence a preliminary page may say",
    )
    c.refuses(
        "a line that is not text", changed(r, "page", "parts", 0, "line", to=5), "golden: obs: not a sentence a preliminary page may say"
    )
    c.refuses(
        "another next step",
        changed(r, "page", "next_step", to="Accept this."),
        "golden: the next step is not one of the two a preliminary page may say",
    )
    c.refuses(
        "a triage that does not match",
        changed(r, "page", "triage", to="NOT_MARKET_TESTABLE"),
        "golden: the triage does not match the next step",
    )
    nothing = changed(r, "page", "next_step", to="Nothing here can be tested against market data, so no check is planned.")
    c.refuses(
        "a page that says nothing is testable but a triage that says investigate",
        nothing,
        "golden: the triage does not match the next step",
    )
    return c.bad


def check_limits(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    r = made(mod)
    part = r["page"]["parts"][0]

    def with_parts(n: int) -> list[dict[str, Any]]:
        doc = copy.deepcopy(r)
        doc["page"]["parts"] = [part | {"node_id": f"p{i}"} for i in range(n)]
        doc["sketches"] = []
        doc["assumptions"] = []
        doc["next_check"] = {
            "status": "NOTHING_LEFT",
            "node_id": None,
            "words": None,
            "check_id": None,
            "instruction": None,
            "source": None,
            "reason": "every part the claim needs has been checked",
        }
        return [doc]

    c.eq("eight parts are read", len(mod.read_claims(json.dumps(with_parts(8)).encode())), 1)
    c.refuses("nine parts", with_parts(9), "golden: a page has 1 to 8 parts")
    c.eq("one part is read", len(mod.read_claims(json.dumps(with_parts(1)).encode())), 1)
    c.eq("one run is read", len(mod.read_claims(json.dumps(changed(r, "runs", to=1)).encode())), 1)
    d = copy.deepcopy(r)
    d["disputes"] = [{"kind": "NUMBER_WITHOUT_NODE", "start": 0, "end": 1, "detail": "x"}]
    c.eq("a dispute from the first character", len(mod.read_claims(json.dumps([d]).encode())), 1)
    opinion = made(
        mod,
        text="I like SOL",
        ident="opinion",
        replies=tuple(
            reply({"nodes": [{"id": "o", "type": "OPINION", "quote": "I like SOL"}], "links": [], "implied": []}, i) for i in (1, 2, 3)
        ),
    )
    c.eq(
        "a page of nothing testable",
        (opinion["page"]["triage"], opinion["page"]["next_step"]),
        ("NOT_MARKET_TESTABLE", "Nothing here can be tested against market data, so no check is planned."),
    )
    c.eq("is read", len(mod.read_claims(json.dumps([opinion]).encode())), 1)
    c.refuses(
        "nothing testable but a triage that says investigate",
        changed(opinion, "page", "triage", to="INVESTIGATE"),
        "opinion: the triage does not match the next step",
    )
    return c.bad


def check_assumptions(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    r = made(mod)
    none = made(mod, (answer(None, 1, finish="length"),) * 3, ident="unread")
    c.eq("twelve suppositions of the golden claim", (len(r["assumptions"]), none["assumptions"]), (12, []))
    first = r["assumptions"][1]
    c.eq(
        "one of them",
        (first["node_id"], first["kind"], first["changed"]),
        (
            "obs",
            "FALSE",
            [
                {"node_id": "who", "before": "RESTS_ON_UNPROVEN_PREMISE", "after": "PREMISE_CONTRADICTED"},
                {"node_id": "up", "before": "RESTS_ON_UNPROVEN_PREMISE", "after": "PREMISE_CONTRADICTED"},
            ],
        ),
    )
    c.eq(
        "and its sentence",
        first["sentence"],
        "If “Whales are accumulating SOL” did not hold, 2 other parts would change. A supposition, not a finding.",
    )
    c.eq("it is read", len(mod.read_claims(json.dumps([r]).encode())), 1)
    lists = "golden: assumptions are a list, and a claim that could not be read has none"
    c.refuses("not a list", changed(r, "assumptions", to={}), lists)
    c.refuses(
        "assumptions on a claim that was not read",
        changed(none, "assumptions", to=r["assumptions"]),
        "unread: assumptions are a list, and a claim that could not be read has none",
    )
    c.refuses(
        "an assumption that is not one",
        changed(r, "assumptions", 0, to={}),
        "golden: an assumption has node_id, kind, changed and sentence",
    )
    about = "golden: an assumption is about a part of the page, of a known kind, once"
    c.refuses("a part not on the page", changed(r, "assumptions", 0, "node_id", to="zz"), about)
    c.refuses("an unknown kind", changed(r, "assumptions", 0, "kind", to="MAYBE"), about)
    twice = copy.deepcopy(r)
    twice["assumptions"][1] = copy.deepcopy(twice["assumptions"][0])
    c.refuses("one supposition twice", [twice], about)
    shape = "golden: what an assumption changes is other parts of the page, each from one status to another"
    c.refuses("changes not a list", changed(r, "assumptions", 1, "changed", to="x"), shape)
    c.refuses("a change that is not one", changed(r, "assumptions", 1, "changed", 0, to={}), shape)
    c.refuses("a change to a part not on the page", changed(r, "assumptions", 1, "changed", 0, "node_id", to="zz"), shape)
    c.refuses("a change to the part itself", changed(r, "assumptions", 1, "changed", 0, "node_id", to="obs"), shape)
    c.refuses(
        "a change from a status to itself", changed(r, "assumptions", 1, "changed", 0, "after", to="RESTS_ON_UNPROVEN_PREMISE"), shape
    )
    c.refuses("a status that is not text", changed(r, "assumptions", 1, "changed", 0, "before", to=3), shape)
    c.refuses("a status after that is not text", changed(r, "assumptions", 1, "changed", 0, "after", to=None), shape)
    c.refuses("a change with another key", changed(r, "assumptions", 1, "changed", 0, "extra", to=1), shape)
    sentence = "golden: the sentence of an assumption is the one its kind, its part and its count give"
    c.refuses("another sentence", changed(r, "assumptions", 1, "sentence", to="Whales are accumulating SOL, so it is true."), sentence)
    c.refuses("a count that does not match", changed(r, "assumptions", 1, "changed", to=[]), sentence)
    return c.bad


def check_assets(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    r = made(mod)
    why = "golden: assets are symbols in capitals"
    for bad in ("SOL", ["sol"], ["SOL", 5], ["SO L"], ["S-L"], [""]):
        c.refuses(f"assets {bad!r}", changed(r, "assets", to=bad), why)
    c.eq("no assets is fine, read with none", len(mod.read_claims(json.dumps([made(mod, assets=())]).encode())), 1)
    c.eq("several are fine", len(mod.read_claims(json.dumps([made(mod, assets=("BTC", "ETH", "1INCH", "SOL"))]).encode())), 1)
    return c.bad


def check_next_check(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    r = made(mod)
    none = made(mod, (answer(None, 1, finish="length"),) * 3, ident="unread")
    keys = "status, node_id, words, check_id, instruction, source, reason"
    c.refuses(
        "a next check on a claim that was not read",
        changed(none, "next_check", to=r["next_check"]),
        "unread: a claim that could not be read has no next check",
    )
    c.refuses("not a next check", changed(r, "next_check", to={}), f"golden: a next check has {keys}")
    c.refuses("no next check on a page", changed(r, "next_check", to=None), f"golden: a next check has {keys}")
    c.refuses(
        "an unknown status",
        changed(r, "next_check", "status", to="GO"),
        "golden: a next check is a check, no available check, or nothing left",
    )
    c.refuses(
        "words that are not the part's",
        changed(r, "next_check", "words", to="Whales"),
        "golden: the next check names a part of the page and says what that part says",
    )
    c.refuses(
        "a part that is not on the page",
        changed(r, "next_check", "node_id", to="zz"),
        "golden: the next check names a part of the page and says what that part says",
    )
    nowhere = changed(r, "next_check", "node_id", to=None)
    c.refuses(
        "words without a part",
        changed(nowhere[0], "next_check", "status", to="NOTHING_LEFT"),
        "golden: the next check names a part of the page and says what that part says",
    )
    c.refuses(
        "a check that is not registered",
        changed(r, "next_check", "check_id", to="guess"),
        "golden: a check is one that is registered, with its own words, for a part",
    )
    c.refuses(
        "a check with other words",
        changed(r, "next_check", "instruction", to="Trust it."),
        "golden: a check is one that is registered, with its own words, for a part",
    )
    c.refuses(
        "a check with another source",
        changed(r, "next_check", "source", to="a blog"),
        "golden: a check is one that is registered, with its own words, for a part",
    )
    c.refuses(
        "a check with a reason",
        changed(r, "next_check", "reason", to="x"),
        "golden: a check is one that is registered, with its own words, for a part",
    )
    opinion = made(
        mod,
        text="I like SOL",
        ident="opinion",
        replies=tuple(
            reply({"nodes": [{"id": "o", "type": "OPINION", "quote": "I like SOL"}], "links": [], "implied": []}, i) for i in (1, 2, 3)
        ),
    )
    c.eq(
        "an opinion has no check",
        (opinion["next_check"]["status"], opinion["next_check"]["reason"], opinion["next_check"]["check_id"]),
        ("NO_AVAILABLE_CHECK", "an opinion has no check", None),
    )
    c.eq("and is read", len(mod.read_claims(json.dumps([opinion]).encode())), 1)
    why = "opinion: no check is offered without a reason, and a reason comes with no check"
    c.refuses("no check but a check id", changed(opinion, "next_check", "check_id", to="compare_exchange_candles"), why)
    c.refuses("no check but an instruction", changed(opinion, "next_check", "instruction", to="x"), why)
    c.refuses("no check but a source", changed(opinion, "next_check", "source", to="x"), why)
    c.refuses("no check and no reason", changed(opinion, "next_check", "reason", to=None), why)
    # the reason for offering no check is the product's own sentence for that kind of part, never one written into the file
    own = "opinion: the reason for offering no check is the product's own for that part"
    c.refuses("no check, with a reason of its own", changed(opinion, "next_check", "reason", to="This token is safe."), own)
    c.refuses(
        "no check, with the reason of another kind of part",
        changed(opinion, "next_check", "reason", to="a forecast is checked by waiting for its outcome"),
        own,
    )
    c.eq(
        "no check because the one that exists was tried",
        len(
            mod.read_claims(
                json.dumps(changed(opinion, "next_check", "reason", to="the check that exists was tried and gave no answer")).encode()
            )
        ),
        1,
    )
    left = changed(opinion, "next_check", "status", to="NOTHING_LEFT")
    c.eq(
        "nothing left, because a part the claim needs does not hold",
        len(
            mod.read_claims(
                json.dumps(
                    changed(left[0], "next_check", "reason", to="a part the claim needs has been checked and does not hold")
                ).encode()
            )
        ),
        1,
    )
    over = "opinion: nothing is left only because a part does not hold, or, naming no part, because every part was checked"
    c.refuses("nothing left, with the reason of a part with no check", left, over)
    c.refuses("nothing left, with a reason of its own", changed(left[0], "next_check", "reason", to="Done."), over)
    c.refuses(
        "every part checked, naming a part",
        changed(left[0], "next_check", "reason", to="every part the claim needs has been checked"),
        over,
    )
    done = changed(changed(left[0], "next_check", "node_id", to=None)[0], "next_check", "words", to=None)
    c.eq(
        "every part checked, naming none",
        len(
            mod.read_claims(json.dumps(changed(done[0], "next_check", "reason", to="every part the claim needs has been checked")).encode())
        ),
        1,
    )
    c.refuses(
        "a part that does not hold, naming none",
        changed(done[0], "next_check", "reason", to="a part the claim needs has been checked and does not hold"),
        over,
    )
    return c.bad


def check_rest(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    r = made(mod)
    short = good(nodes=good()["nodes"][:2] + good()["nodes"][3:], links=[], implied=[])
    d = made(mod, (reply(short, 1), reply(short, 2), reply(short, 3)))
    keys = "state, score, validated, unmatched, type_conflicts"
    c.refuses("a reading that is not one", changed(r, "reading", to={}), f"golden: a reading has {keys}")
    c.refuses(
        "an unknown state",
        changed(r, "reading", "state", to="GREAT"),
        "golden: a reading has a known state and says whether it was validated",
    )
    c.refuses(
        "validated as text",
        changed(r, "reading", "validated", to="no"),
        "golden: a reading has a known state and says whether it was validated",
    )
    for bad in ("1", "0.5", "1.01", 1.0, "2.00"):
        c.refuses(f"score {bad!r}", changed(r, "reading", "score", to=bad), "golden: the score is a share written with two places")
    c.eq(
        "scores of 0.00 and 0.75",
        [len(mod.read_claims(json.dumps(changed(r, "reading", "score", to=s)).encode())) for s in ("0.00", "0.75")],
        [1, 1],
    )
    c.refuses("unmatched not a list", changed(r, "reading", "unmatched", to="up"), "golden: unmatched is a list of part ids")
    c.refuses("type conflicts of numbers", changed(r, "reading", "type_conflicts", to=[1]), "golden: type_conflicts is a list of part ids")
    c.refuses("disputes not a list", changed(r, "disputes", to={}), "golden: disputes are a list")
    c.refuses("a dispute that is not one", changed(d, "disputes", 0, to={}), "golden: a dispute has kind, start, end and detail")
    for label, field, value in (
        ("kind", "kind", "RUDE"),
        ("start", "start", -1),
        ("end past the text", "end", len(TEXT) + 1),
        ("an empty span", "end", d["disputes"][0]["start"]),
        ("a boolean", "start", True),
        ("text", "start", "0"),
    ):
        c.refuses(
            f"a dispute with {label}",
            changed(d, "disputes", 0, field, to=value),
            "golden: a dispute is of a known kind and lies inside the claim",
        )
    c.eq(
        "a dispute that ends at the end of the text",
        len(mod.read_claims(json.dumps(changed(d, "disputes", 0, "end", to=len(TEXT))).encode())),
        1,
    )
    c.refuses("an empty detail", changed(d, "disputes", 0, "detail", to=""), "golden: a dispute's detail is a non-empty text")
    c.refuses("no sketch for a forecast", changed(r, "sketches", to=[]), "golden: there is one sketch for each forecast part, in order")
    c.refuses(
        "a sketch for something else",
        changed(r, "sketches", 0, "node_id", to="obs"),
        "golden: there is one sketch for each forecast part, in order",
    )
    c.refuses("sketches not a list", changed(r, "sketches", to="x"), "golden: there is one sketch for each forecast part, in order")
    keys = "node_id, status, contract, asset, direction, magnitude, horizon_hours, choices, reason"
    c.refuses(
        "a sketch that is not one", changed(r, "sketches", 0, to={"node_id": "up"}), f"golden: a sketch has {keys} and a known status"
    )
    c.refuses(
        "an unknown sketch status", changed(r, "sketches", 0, "status", to="MAYBE"), f"golden: a sketch has {keys} and a known status"
    )
    c.refuses(
        "a contract on a sketch that needs choices",
        changed(r, "sketches", 0, "contract", to="D"),
        "golden: a sketch has a contract only when resolvable and a reason only when unresolved",
    )
    c.refuses(
        "a reason on a sketch that needs choices",
        changed(r, "sketches", 0, "reason", to="x"),
        "golden: a sketch has a contract only when resolvable and a reason only when unresolved",
    )
    c.refuses(
        "no choices though it needs them",
        changed(r, "sketches", 0, "choices", to=[]),
        "golden: a sketch has choices exactly when it needs them",
    )
    c.refuses("choices not a list", changed(r, "sketches", 0, "choices", to="x"), "golden: a sketch has choices exactly when it needs them")
    c.refuses(
        "a choice that is not one", changed(r, "sketches", 0, "choices", 0, to={}), "golden: a choice has field, question and options"
    )
    c.refuses(
        "options not a list", changed(r, "sketches", 0, "choices", 0, "options", to="x"), "golden: a choice has field, question and options"
    )
    c.refuses(
        "an empty question", changed(r, "sketches", 0, "choices", 0, "question", to=""), "golden: a choice's question is a non-empty text"
    )
    # a sketch is what the forecast part's words and the claim's assets give: nothing else may stand in it
    made_up = "golden: a sketch is the one its forecast part's words give"
    c.refuses("a sketch of another asset", changed(r, "sketches", 0, "asset", to="DOGE"), made_up)
    c.refuses("a sketch of another size", changed(r, "sketches", 0, "magnitude", to="500 percent"), made_up)
    c.refuses("a sketch of another direction", changed(r, "sketches", 0, "direction", to="DOWN"), made_up)
    c.refuses(
        "a choice with a question of its own",
        changed(r, "sketches", 0, "choices", 0, "question", to="Buy now before it is too late?"),
        made_up,
    )
    c.refuses("a choice with options of its own", changed(r, "sketches", 0, "choices", 0, "options", to=["now"]), made_up)
    c.refuses("a sketch read with other assets", changed(r, "assets", to=["ETH"]), made_up)
    resolvable = made(
        mod,
        text="SOL will fall in 24 hours",
        replies=tuple(
            reply({"nodes": [{"id": "f", "type": "FORECAST", "quote": "SOL will fall in 24 hours"}], "links": [], "implied": []}, i)
            for i in (1, 2, 3)
        ),
    )
    c.eq(
        "a resolvable sketch",
        {k: resolvable["sketches"][0][k] for k in ("status", "contract", "choices", "reason", "horizon_hours")},
        {"status": "RESOLVABLE", "contract": "D", "choices": [], "reason": None, "horizon_hours": 24},
    )
    c.eq("and is read", len(mod.read_claims(json.dumps([resolvable]).encode())), 1)
    c.refuses(
        "resolvable without a contract",
        changed(resolvable, "sketches", 0, "contract", to=None),
        "golden: a sketch has a contract only when resolvable and a reason only when unresolved",
    )
    vague = made(
        mod,
        text="ETH is going to moon",
        ident="vague",
        replies=tuple(
            reply({"nodes": [{"id": "f", "type": "FORECAST", "quote": "ETH is going to moon"}], "links": [], "implied": []}, i)
            for i in (1, 2, 3)
        ),
    )
    c.eq(
        "an unresolved sketch says why", (vague["sketches"][0]["status"], vague["sketches"][0]["reason"] is not None), ("UNRESOLVED", True)
    )
    c.eq("and is read", len(mod.read_claims(json.dumps([vague]).encode())), 1)
    c.refuses(
        "unresolved without a reason",
        changed(vague, "sketches", 0, "reason", to=None),
        "vague: a sketch has a contract only when resolvable and a reason only when unresolved",
    )
    return c.bad


RLO = "\u202e"
RULE = "breaks the rule for a claim's text: the claim text has a bidirectional control character"


def with_inferred_quote(r: dict[str, Any], quote: str) -> list[dict[str, Any]]:
    """The entry with its implied step's words changed everywhere the file repeats them (the part, its line, its suppositions)."""
    doc = copy.deepcopy(r)
    part = next(p for p in doc["page"]["parts"] if p["kind"] == "ASSUMPTION_INFERRED")
    old, part["quote"] = part["quote"], quote
    part["line"] = part["line"].replace(old, quote)
    for a in doc["assumptions"]:
        if a["node_id"] == part["node_id"]:
            a["sentence"] = a["sentence"].replace(old, quote)
    return [doc]


def check_shown_texts(mod: types.ModuleType) -> list[str]:
    """Every text the page shows is held to the rule for a claim's text (``claim_graph.check_text``), so it shows as it is written."""
    c = Case(mod)
    r = made(mod)
    short = good(nodes=good()["nodes"][:2] + good()["nodes"][3:], links=[], implied=[])
    d = made(mod, (reply(short, 1), reply(short, 2), reply(short, 3)))
    between = TEXT.replace("SOL, therefore", "SOL,{} therefore")
    for code in (0x061C, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069):
        c.refuses(f"U+{code:04X} in the claim's text", changed(r, "text", to=between.format(chr(code))), f"golden: the text {RULE}")
    c.refuses("one in the model's name", changed(r, "model", to=f"m{RLO}x"), f"golden: the model {RULE}")
    c.refuses(
        "one in an implied step's words",
        with_inferred_quote(r, f"The large{RLO} wallets belong to institutions"),
        f"golden: ident: the quote {RULE}",
    )
    c.refuses(
        "one in a dispute's detail",
        changed(d, "disputes", 0, "detail", to=f"a causal word{RLO} that no causal-link node contains"),
        f"golden: a dispute's detail {RULE}",
    )
    c.refuses(
        "a control character in the claim's text",
        changed(r, "text", to=between.format("\x85")),
        "golden: the text breaks the rule for a claim's text: the claim text has a control character",
    )
    c.refuses(
        "a claim's text longer than a claim may be",
        changed(r, "text", to=TEXT + " " + "x" * 4000),
        "golden: the text breaks the rule for a claim's text: the claim is longer than 4000 characters",
    )
    c.eq(
        "a newline inside a claim is shown as it is",
        len(mod.read_claims(json.dumps(changed(r, "text", to=between.format("\n"))).encode())),
        1,
    )
    c.eq(
        "and an implied step's own words are",
        len(mod.read_claims(json.dumps(with_inferred_quote(r, "Large wallets are institutions")).encode())),
        1,
    )
    return c.bad


def check_duplicate_keys(mod: types.ModuleType) -> list[str]:
    """A key given twice in one object is refused: a reader would keep one of its values and show something the file also says otherwise."""
    c = Case(mod)
    raw = json.dumps([made(mod)])
    c.refuses(
        "a claim that gives its id twice",
        raw.replace('"id": "golden"', '"id": "golden", "id": "other"', 1).encode(),
        "the claims file gives the key 'id' twice in one object",
    )
    quote = '"quote": "Whales are accumulating SOL"'
    c.refuses(
        "a part that gives the same quote twice",
        raw.replace(quote, f"{quote}, {quote}", 1).encode(),
        "the claims file gives the key 'quote' twice in one object",
    )
    c.eq("a file with no key twice is read", [x.id for x in mod.read_claims(raw.encode())], ["golden"])
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (
        check_record,
        check_reading_a_file,
        check_page,
        check_limits,
        check_next_check,
        check_assets,
        check_assumptions,
        check_rest,
        check_shown_texts,
        check_duplicate_keys,
    )
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
