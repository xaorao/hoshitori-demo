#!/usr/bin/python3
"""The states of the data, in a real browser: an empty league, old documents, and a cycle in which part did not finish; and documents that
hold what a page cannot show, or showed as codes.

Usage: /usr/bin/python3 tools/states_test.py DIST EMPTY_DATA FULL_DATA OUT [--engine ...]
DIST is a built site; EMPTY_DATA and FULL_DATA are exported site directories (a league with no cycle, and one with cycles). The partial case
takes FULL_DATA and marks three slots of its latest cycle as failed in today.json, keeping the index's digest of that file right: a fixture of
the shape of such a cycle (the real ledgers of the exploratory runs predate the current model prompts, so they cannot be exported now).
The failure cases change one document of FULL_DATA the same way: a receipt whose evidence has the time "unknown" (as the view models wrote
evidence missing from the ledger until they wrote none) must show a plain failure for that page, with the page bar and a way back, and the
site must work again after it; the same evidence with no time must show the receipt with a dash; a receipt of a model agent that called and
settled must say so in words ("Call", "settled, rose"), never "CALL" or "outcome 1"; an index whose time cannot be read must show its
failure, with the page bar still there; the evidence with no time must say the view model's own words for it ("not in this ledger"), never
"derived from the others", which only a derived item is.
Three more cases (batch 6): a claims document whose claim has a character outside the Basic Multilingual Plane before a dispute and its parts
(the claims file counts offsets in code points, as Python does; the page must quote the words they name); a record whose up-or-down score
counts fewer calls than settled (the Track record and Today must show the score of every settled call and the share left out beside it,
contract section 8); and an index that says the ledger is in INTEGRITY_FAILURE (made from EMPTY_DATA, as the exporter writes such a site:
no record, and the state with the ledger's reason), on which every page must say so and none may say that nothing was committed (ADR-026,
LEDGER_SPEC section 8).
Each case copies DIST, puts the data under data/, opens the pages, checks the sentences that must be there and saves screenshots.
"""

import atexit
import contextlib
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import page_test as pt


_COPIES = []  # every copy of the site this run made: each case gets its own, and all are removed when the run ends


def _remove_copies():
    for work in _COPIES:
        shutil.rmtree(work, ignore_errors=True)


atexit.register(_remove_copies)


def site(dist, data):
    work = tempfile.mkdtemp(prefix="states-")
    _COPIES.append(work)

    def top(where, _names):  # a built site that already holds data (as the page test wants it) is copied without it
        return ["data"] if os.path.samefile(where, dist) else []

    shutil.copytree(dist, os.path.join(work, "dist"), ignore=top)
    shutil.copytree(data, os.path.join(work, "dist", "data"))
    return os.path.join(work, "dist")


def changed(dist, data, path, edit):
    """A copy of the site whose document ``path`` is ``edit``ed, written as the exporter writes it, with its digest in the index right."""
    root = site(dist, data)
    target = os.path.join(root, "data", path)
    doc = read_json(target)
    edit(doc)
    body = (json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    with open(target, "wb") as handle:
        handle.write(body)
    if path != "index.json":
        index_path = os.path.join(root, "data", "index.json")
        index = read_json(index_path)
        index["files"][path] = hashlib.sha256(body).hexdigest()
        with open(index_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(index))
    return root


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def visible(page):
    return page.evaluate("() => document.body.innerText")


def run(dist, empty, partial, out, engine):  # partial: a full export, marked below
    os.makedirs(out, exist_ok=True)
    bad = []
    with sync_playwright() as p:
        kind = getattr(p, engine)
        launch = {}
        shell = os.path.expanduser(
            "~/Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell"
        )
        if engine == "chromium" and os.path.exists(shell):
            launch["executable_path"] = shell
        browser = kind.launch(**launch)

        def open_page(root, hash_, later_days=0, width=1280):
            server = pt.serve(root)

            def setup(page):
                problems = []
                page.on("pageerror", lambda e: problems.append(str(e)))
                page.on("console", lambda m: problems.append(m.text) if m.type == "error" and not pt.harness_noise(m.text, engine) else None)
                if later_days:
                    generated = json.loads(Path(root, "data", "index.json").read_text())["generated_at"]
                    then = datetime.datetime.fromisoformat(generated.replace("Z", "+00:00")) + datetime.timedelta(days=later_days)
                    page.clock.set_fixed_time(then)
                return problems

            url = f"http://127.0.0.1:{server.server_address[1]}/index.html{hash_}"
            ctx, page, problems = start(browser, {"width": width, "height": 900}, url, setup)
            page.wait_for_selector("h1")
            page.wait_for_function("() => !document.querySelector('[role=status]:not(.notice)')", timeout=10000)
            return server, ctx, page, problems

        def expect(label, text, *needles):
            for needle in needles:
                if needle not in text:
                    bad.append(f"{label}: missing {needle!r}")

        empty_dist = site(dist, empty)
        for route, needles in (
            ("#/", ["No cycle has run yet.", "The league records its first cycle when it commits it"]),
            ("#/calls", ["No cycle has run yet."]),
            ("#/record", ["No agent has had a call yet, so there is nothing to count."]),
            ("#/verify", ["No cycle has been committed yet."]),
            ("#/quality", ["What has been shown, and what has not."]),
            ("#/drill", ["All 16 drills held."]),
        ):
            server, ctx, page, problems = open_page(empty_dist, route)
            expect(f"empty {route}", visible(page), *needles)
            if problems:
                bad.append(f"empty {route}: {problems}")
            page.screenshot(path=os.path.join(out, f"empty-{route.strip('#/') or 'today'}-{engine}.png"))
            ctx.close()
            server.shutdown()
        server, ctx, page, problems = open_page(empty_dist, "#/", later_days=3)
        expect("old documents", visible(page), "These documents were exported 3 days ago. Nothing newer is shown")
        if problems:
            bad.append(f"old: {problems}")
        page.screenshot(path=os.path.join(out, f"old-today-{engine}.png"))
        ctx.close()
        server.shutdown()
        server, ctx, page, problems = open_page(empty_dist, "#/", later_days=0)
        if "These documents were exported" in visible(page):
            bad.append("fresh documents were called old")
        ctx.close()
        server.shutdown()
        part_dist = site(dist, partial)
        today_path = os.path.join(part_dist, "data", "today.json")
        today = json.loads(Path(today_path).read_text())
        today["latest"]["rows_by_state"] = [["Open", 30], ["Failed", 2], ["Too late", 1]]
        body = (json.dumps(today, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        Path(today_path).write_bytes(body)
        index_path = os.path.join(part_dist, "data", "index.json")
        index = json.loads(Path(index_path).read_text())
        import hashlib

        index["files"]["today.json"] = hashlib.sha256(body).hexdigest()
        Path(index_path).write_text(json.dumps(index))
        server, ctx, page, problems = open_page(part_dist, "#/")
        expect("partial cycle", visible(page), "failed or came too late; the rest stands.")
        page.screenshot(path=os.path.join(out, f"partial-today-{engine}.png"))
        if problems:
            bad.append(f"partial: {problems}")
        ctx.close()
        server.shutdown()
        # a fresh browser for each later batch, as the page test does: a browser process stops answering after many contexts (Firefox here,
        # WebKit there), a harness limit and not a page fault
        for batch, data in ((broken_documents, (partial,)), (batch6_cases, (empty, partial)), (model_challenge_cases, (empty, partial))):
            browser.close()
            browser = kind.launch(**launch)
            bad += batch(browser, dist, *data, out, engine)
        browser.close()
    return bad


# the evidence line the view models wrote for evidence the ledger does not hold, before an unknown time was written as none
MISSING_EVIDENCE = {
    "source": "unknown",
    "path": "unknown",
    "kind": "unknown",
    "event_time": "unknown",
    "data_mode": "unknown",
    "origin_family": "unknown",
    "raw_sha256": None,
    "shown": "not in this ledger",
}
DASH = "\u2013"  # what the pages show for no value
FIRST_EVIDENCE_ROW = """() => { const t = [...document.querySelectorAll('main table')].find(x => x.caption && x.caption.textContent === 'Evidence behind this call');
 const r = t ? t.querySelector('tbody tr') : null;
 return r ? [...r.children].map(c => c.textContent) : null; }"""
LOADING_GONE = "!document.querySelector('[role=status]:not(.notice)')"
FACT = """(term) => { const dt = [...document.querySelectorAll('main dt')].find(d => d.textContent === term);
 return dt && dt.nextElementSibling ? dt.nextElementSibling.textContent : null; }"""


def model_call(doc):
    """A receipt of the single-model agent's up-or-down call that started, settled and rose: the run and settlement lines as the view models
    write them (codes as the records hold them), and the history and row to match."""
    started, settled = "2026-10-31T17:15:00.000Z", "2026-10-31T21:15:31.000Z"
    doc["row"].update({"arm": "L1", "arm_name": "Single model", "chance": "55% up", "state": "Settled", "result": "Rose"})
    doc["row"].update({"origin": started, "settled_at": settled})
    doc["timeline"] += [
        {"state": "Started", "at": started, "reason": None},
        {"state": "Settling", "at": "2026-10-31T21:15:01.000Z", "reason": None},
        {"state": "Settled", "at": settled, "reason": None},
    ]
    doc["run"] = {
        "verdict": "CALL",
        "reason": None,
        "gate": "NONE",
        "proposed_chance": "55%",
        "final_chance": "55%",
        "calls": 3,
        "tokens": 1234,
        "cutoff_s": 240,
    }
    doc["settlement"] = {
        "state": "SETTLED",
        "outcome": "1",
        "reference_price": "100.5",
        "outcome_price": "101",
        "oracles": doc["oracles"],
        "rule_version": "settle/1",
        "flags": [],
    }


def start(browser, viewport, url, setup):
    """A new context and page, the page's listeners set by ``setup(page)`` (whose result is returned), then the page opened at ``url``.

    Firefox under the driver now and then never begins a navigation: no request is sent and the load times out. A page that was never asked
    for cannot be what failed, so that case alone is tried once more in a fresh context; a load that timed out after a request was sent is
    the page's own failure and is raised."""
    for attempt in (1, 2):
        ctx = browser.new_context(viewport=viewport)
        page = ctx.new_page()
        sent, done = [], []
        page.on("request", lambda r: sent.append(r.url))
        page.on("requestfinished", lambda r: done.append(r.url))
        page.on("requestfailed", lambda r: done.append(f"failed {r.url}"))
        state = setup(page)
        try:
            page.goto(url)
            return ctx, page, state
        except Exception as exc:
            ctx.close()
            if sent or attempt == 2:
                raise AssertionError(f"{url} did not load (attempt {attempt}, unanswered: {[u for u in sent if u not in done]}, failed: {[u for u in done if u.startswith('failed')]})") from exc
    raise AssertionError("unreachable")


def open_plain(browser, root, hash_, width=1280, height=900):
    """Open a page that may have no heading at all (a page that failed, or a blank one): wait for the page bar, then for loading to end."""
    server = pt.serve(root)

    def setup(page):
        problems, consoles = [], []
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        page.on(
            "console",
            lambda m: consoles.append(m) if m.type == "error" and not pt.harness_noise(m.text, browser.browser_type.name) else None,
        )
        return problems, consoles

    url = f"http://127.0.0.1:{server.server_address[1]}/index.html{hash_}"
    ctx, page, (problems, consoles) = start(browser, {"width": width, "height": height}, url, setup)
    with contextlib.suppress(Exception):  # a blank page is what some of these cases look for
        page.wait_for_selector("nav[aria-label=Pages]", timeout=8000)
        page.wait_for_function("() => !document.querySelector('[role=status]:not(.notice)')", timeout=8000)
    page.wait_for_timeout(300)
    for m in consoles:  # an error logged as an object reads "Error" in Firefox: its message is asked of the object itself
        try:
            said = m.args[0].evaluate("e => (e && e.message) || String(e)") if m.args else m.text
        except Exception:  # the object is gone: what the browser printed is all there is
            said = m.text
        problems.append(f"console: {said}")
    return server, ctx, page, problems


# axe rules reported but not held against a case (none: the failure view of a site that cannot read its index has its page heading)
KNOWN: tuple[str, ...] = ()
VIOLATIONS = "async () => (await axe.run(document, {resultTypes: ['violations']})).violations.map(v => v.id + ':' + v.nodes.length)"


PAGE_LINKS = 18  # the four primary links in the bar and the 14 pages of the menu: Today, Calls, Track record, Check a record, Check a claim, Test a source, The Desk, Practice trades, Laboratory, Feed, Voices and whales, For judges, Evidence of quality, Failure drills


def shell_problems(page, label, known=()):
    """The frame every page keeps: the page bar with its eleven links, and the footer; and no axe-core violation in the state shown."""
    out = []
    if page.locator("nav[aria-label=Pages]").count() == 1:
        pt.inject_axe(page)
        found = page.evaluate(VIOLATIONS)
        if [f for f in found if f.split(":")[0] not in known]:
            out.append(f"{label}: axe-core violations {found}")
        elif found:
            print(f"  note, {label}: axe-core reports {found}, known and not fixed here")
    if page.locator("nav[aria-label=Pages] a").count() != PAGE_LINKS:
        out.append(f"{label}: the page bar is gone (the site is blank: {visible(page)[:80]!r})")
    if page.locator("footer a[href='#/brand']").count() != 1:
        out.append(f"{label}: the footer is gone")
    return out


def broken_documents(browser, dist, full, out, engine):
    bad = []
    receipts = sorted(p for p in read_json(os.path.join(full, "index.json"))["files"] if p.startswith("receipts/"))
    first = receipts[0]
    hash_ = f"#/receipts/{first[len('receipts/') : -len('.json')]}"
    call_d = next(p for p in receipts if read_json(os.path.join(full, p))["row"]["contract"] == "D")

    # a time the page cannot read: a plain failure for this page, the page bar and a way back; the site works again after it
    def unknown_time(doc):
        doc["evidence"][0] = {"evidence_id": doc["evidence"][0]["evidence_id"], **MISSING_EVIDENCE}

    server, ctx, page, problems = open_plain(browser, changed(dist, full, first, unknown_time), hash_)
    text = visible(page)
    for needle in ("This page could not be shown.", "A document of this site could not be read.", "not a time: unknown", "Go to Today"):
        if needle not in text:
            bad.append(f"unreadable time: missing {needle!r}")
    bad += shell_problems(page, "unreadable time")
    if page.locator("main h1").count() != 1:
        bad.append("unreadable time: the failure has no heading")
    unexpected = [p for p in problems if "not a time: unknown" not in p or p.startswith("pageerror")]
    if unexpected:
        bad.append(f"unreadable time: {unexpected[:2]}")
    page.screenshot(path=os.path.join(out, f"broken-receipt-{engine}.png"))
    if page.locator("main a", has_text="Go to Today").count() == 1:
        page.locator("main a", has_text="Go to Today").click()
        today = "() => document.title.startsWith('Today ') && !!document.querySelector('main h1') && " + LOADING_GONE
        with contextlib.suppress(Exception):  # what came instead is said below
            page.wait_for_function(today, timeout=8000)
        if "What changed" not in visible(page) or "This page could not be shown." in visible(page):
            bad.append(f"unreadable time: Today does not come back after it: {visible(page)[:120]!r}")
    ctx.close()
    server.shutdown()

    # the same evidence with no time, as the view models write it now: the receipt, with a dash for the time
    def no_time(doc):
        doc["evidence"][0] = {"evidence_id": doc["evidence"][0]["evidence_id"], **MISSING_EVIDENCE, "event_time": None}

    server, ctx, page, problems = open_plain(browser, changed(dist, full, first, no_time), hash_)
    cells = page.evaluate(FIRST_EVIDENCE_ROW)
    if cells is None or len(cells) != 4 or cells[2] != DASH:
        bad.append(f"no time: the evidence row is {cells!r}, expected a dash for its time")
    if cells is None or len(cells) != 4 or cells[3] != "not in this ledger":
        bad.append(f"no time: the evidence row is {cells!r}, expected the view model's words for its digest, 'not in this ledger'")
    if "This page could not be shown." in visible(page) or problems:
        bad.append(f"no time: the receipt was not shown: {problems[:2]}")
    bad += shell_problems(page, "no time")
    page.screenshot(path=os.path.join(out, f"no-time-receipt-{engine}.png"))
    ctx.close()
    server.shutdown()

    # a model agent's up-or-down call that started and settled: its answer and its outcome in words
    call_hash = f"#/receipts/{call_d[len('receipts/') : -len('.json')]}"
    server, ctx, page, problems = open_plain(browser, changed(dist, full, call_d, model_call), call_hash)
    answer, settled = page.evaluate(FACT, "Answer"), page.evaluate(FACT, "Settlement")
    if (answer, settled) != ("Call", "settled, rose"):
        bad.append(f"codes: Answer is {answer!r} and Settlement {settled!r}, expected 'Call' and 'settled, rose'")
    text = visible(page)
    if re.search(r"\bCALL\b", text) or "outcome 1" in text:
        bad.append("codes: a raw code is on the page")
    if problems:
        bad.append(f"codes: {problems[:2]}")
    bad += shell_problems(page, "codes")
    page.screenshot(path=os.path.join(out, f"codes-receipt-{engine}.png"), full_page=True)
    ctx.close()
    server.shutdown()

    # an index whose time the pages cannot read: its failure in place of the page, and the page bar still there
    def unknown_generated(doc):
        doc["generated_at"] = "unknown"

    server, ctx, page, problems = open_plain(browser, changed(dist, full, "index.json", unknown_generated), "#/")
    text = visible(page)
    for needle in ("A document of this site could not be read.", "index.json is not a site index this page understands", "Try again"):
        if needle not in text:
            bad.append(f"unreadable index time: missing {needle!r}")
    bad += shell_problems(page, "unreadable index time", known=KNOWN)
    if any(p.startswith("pageerror") for p in problems):
        bad.append(f"unreadable index time: {problems[:2]}")
    page.screenshot(path=os.path.join(out, f"broken-index-{engine}.png"))
    ctx.close()
    server.shutdown()
    return bad


ASTRAL = "\U0001f680"  # one code point outside the Basic Multilingual Plane: two UTF-16 units in a page's string


def astral_claim():
    """A claim in the shape the claims file writes it (scripts/make_claims.py, hoshitori.claims_file), read on scripted replies by the league's
    own reader: its text starts with a rocket, the plain reader disputes "therefore" (no part is a causal link), and the offsets of the dispute
    are counted in code points, as Python counts them."""
    text = f"{ASTRAL} Whales are accumulating SOL, therefore institutions are entering, and SOL will rise 15% this week."
    start = text.index("therefore")
    parts = [
        ("obs", "OBSERVATION", "Whales are accumulating SOL"),
        ("who", "INTERPRETATION", "institutions are entering"),
        ("up", "FORECAST", "SOL will rise 15% this week"),
    ]
    return {
        "id": "astral", "text": text, "model": "m", "read_at": "2026-10-30T12:00:00Z", "runs": 3,
        "page": {
            "banner": "Preliminary: read by a machine, nothing checked yet.", "headline": "Not yet independently supported.",
            "parts": [{"node_id": i, "kind": k, "status": "UNCHECKED", "quote": q, "line": ""} for i, k, q in parts],
            "next_step": "Next step: investigate, do not accept as confirmed.", "triage": "INVESTIGATE",
        },
        "message": None,
        "reading": {"state": "DISPUTED", "score": "1.00", "validated": False, "unmatched": [], "type_conflicts": []},
        "disputes": [{"kind": "CAUSAL_WORD_WITHOUT_LINK", "start": start, "end": start + len("therefore"), "detail": "a causal word that no causal-link node contains"}],
        "sketches": [], "next_check": None, "assets": ["SOL"], "assumptions": [],
    }  # fmt: skip


def added(dist, data, path, doc):
    """A copy of the site with one more document ``path``, written as the exporter writes it, with its digest in the index."""
    root = site(dist, data)
    body = (json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    with open(os.path.join(root, "data", path), "wb") as handle:
        handle.write(body)
    index_path = os.path.join(root, "data", "index.json")
    index = read_json(index_path)
    index["files"][path] = hashlib.sha256(body).hexdigest()
    with open(index_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(index))
    return root


# the reason a ledger gives when one byte of a middle line of its segment was changed (as hoshitori.ledger writes it)
FAILED_DETAIL = "segment segment-000000000000.jsonl: leaf hash does not match the record at offset 3256; not repaired"
EMPTY_WORDS = ("No cycle has run yet", "No cycle has been committed yet", "No agent has had a call yet", "nothing here is made up")
FAILED_ROUTES = (
    "#/",
    "#/calls",
    "#/record",
    "#/verify",
    "#/receipts/fc" + "a" * 26,
    "#/challenge",
    "#/quality",
    "#/drill",
    "#/claims",
    "#/brand",
    "#/nowhere",
)
LEFT_OUT_TODAY = (
    "Rules: 3 settled calls independently time-stamped, too few for a score to mean anything. "
    "All settled calls: 5, too few for a score to mean anything; 40% were not independently time-stamped."
)


MARKUP = (
    '<img src="http://attacker.invalid/pixel.png" onerror="alert(1)"> '
    '<a href="http://attacker.invalid/steal">click here</a> [link](http://attacker.invalid/md) ![x](http://attacker.invalid/i.png)'
)
FOREIGN = """() => ({
  images: document.querySelectorAll('main img, main picture, main iframe, main object, main embed, main svg image').length,
  links: [...document.querySelectorAll('a[href]')].map((a) => a.getAttribute('href')).filter((h) => !h.startsWith('#') && !h.startsWith('data/') && !h.endsWith('.svg') && !h.endsWith('.png')),
  text: document.querySelector('main').innerText,
})"""


def markup_case(browser, dist, full, out, engine):
    """Contract 53.4 and the injection goal G8: words of a claim that are markup, a link or an image are shown as words. No element is made
    from them, nothing is fetched from their address, and the only links on the page are the product's own. A control puts the same markup
    into the page as HTML, to show that the check would notice an element made from it."""
    bad = []
    doc = astral_claim()
    doc["id"] = "markup"
    doc["text"] = f"{MARKUP} Whales are accumulating SOL, therefore institutions are entering, and SOL will rise 15% this week."
    doc["disputes"] = []
    server, ctx, page, problems = open_plain(browser, added(dist, full, "claims.json", [doc]), "#/claims")
    requested = []
    page.on("request", lambda r: requested.append(r.url))
    page.reload()
    page.wait_for_selector("nav[aria-label=Pages]", timeout=8000)
    page.wait_for_timeout(500)
    seen = page.evaluate(FOREIGN)
    if seen["images"] or seen["links"]:
        bad.append(f"markup claim: elements were made from the words: {seen}")
    if any("attacker.invalid" in url for url in requested):
        bad.append(f"markup claim: the page asked for an attacker's address: {[u for u in requested if 'attacker.invalid' in u]}")
    if "attacker.invalid/steal" not in seen["text"]:
        bad.append("markup claim: the words are not shown as words (the claim's text is not on the page)")
    if problems:
        bad.append(f"markup claim: {problems[:2]}")
    page.evaluate("(m) => document.querySelector('main').insertAdjacentHTML('beforeend', m)", MARKUP)
    control = page.evaluate(FOREIGN)
    if control["images"] < 1 or not control["links"]:
        bad.append(f"markup claim: the control did not make an element from the markup, so the check cannot notice one: {control}")
    page.screenshot(path=os.path.join(out, f"markup-claims-{engine}.png"), full_page=True)
    ctx.close()
    server.shutdown()
    return bad


def batch6_cases(browser, dist, empty, full, out, engine):
    bad = markup_case(browser, dist, full, out, engine)
    # a character outside the Basic Multilingual Plane before a dispute and the parts: the words quoted are the ones the offsets name
    server, ctx, page, problems = open_plain(browser, added(dist, full, "claims.json", [astral_claim()]), "#/claims")
    text = visible(page)
    for needle in (
        "\u201ctherefore\u201d: a causal word that no causal-link node contains.",
        "\u201cWhales are accumulating SOL\u201d",
        "\u201cSOL will rise 15% this week\u201d",
    ):
        if needle not in text:
            bad.append(
                f"astral claim: missing {needle!r} (the dispute reads {[line for line in text.splitlines() if 'causal word' in line]!r})"
            )
    if problems:
        bad.append(f"astral claim: {problems[:2]}")
    bad += shell_problems(page, "astral claim")
    page.screenshot(path=os.path.join(out, f"astral-claims-{engine}.png"), full_page=True)
    ctx.close()
    server.shutdown()

    # a score of fewer calls than settled: the score of every settled call and the share left out beside it, on the Track record and Today
    def fewer(doc):
        arm = next(a for a in doc if a["arm"] == "L0" and a["contract"] == "D")
        arm.update(
            {"settled": 5, "open": arm["open"] - 5, "mean_brier": "0.2400", "brier_n": 3, "all_mean_brier": "0.2510", "all_brier_n": 5}
        )
        arm.update({"missed_c3": "40%", "label": "Provisional: 5 settled calls; nothing is ranked"})

    root = changed(dist, full, "record.json", fewer)
    for hash_, needles in (
        (
            "#/record",
            (
                "Score, all settled",
                "Left out",
                "0.2400 (3)",
                "0.2510 (5)",
                "40%",
                "The share of every settled call that was not",
            ),
        ),
        ("#/", (LEFT_OUT_TODAY,)),
    ):
        server, ctx, page, problems = open_plain(browser, root, hash_)
        text = " ".join(visible(page).split())
        for needle in needles:
            if needle not in text:
                bad.append(f"score left out {hash_}: missing {needle!r}")
        if problems:
            bad.append(f"score left out {hash_}: {problems[:2]}")
        bad += shell_problems(page, f"score left out {hash_}")
        page.screenshot(path=os.path.join(out, f"left-out-{hash_.strip('#/') or 'today'}-{engine}.png"), full_page=True)
        ctx.close()
        server.shutdown()

    # a ledger in INTEGRITY_FAILURE: every page says so, in the specification's words, and none says that nothing was committed
    def failure(doc):
        doc["ledger"] = {"state": "INTEGRITY_FAILURE", "detail": FAILED_DETAIL}

    root = changed(dist, empty, "index.json", failure)
    for hash_ in FAILED_ROUTES:
        for width, height in ((1280, 900), (390, 844), (320, 640)):
            server, ctx, page, problems = open_plain(browser, root, hash_, width=width, height=height)
            text = visible(page)
            for needle in ("The league's ledger is in INTEGRITY_FAILURE.", "Its records are NOT RECONSTRUCTIBLE", FAILED_DETAIL):
                if needle not in text:
                    bad.append(f"integrity failure {hash_} at {width}: missing {needle!r}")
            for words in EMPTY_WORDS:
                if words in text:
                    bad.append(f"integrity failure {hash_} at {width}: says {words!r}")
            if page.evaluate(pt.OVERFLOW):
                bad.append(f"integrity failure {hash_} at {width}: the page is wider than the window")
            if problems:
                bad.append(f"integrity failure {hash_} at {width}: {problems[:2]}")
            if width == 1280:
                bad += shell_problems(page, f"integrity failure {hash_}")
            if hash_ in ("#/", "#/verify", "#/quality") or width == 1280:
                name = hash_.strip("#/").replace("/", "-") or "today"
                page.screenshot(path=os.path.join(out, f"integrity-{name[:20]}-{width}-{engine}.png"), full_page=True)
            ctx.close()
            server.shutdown()
    return bad


# what a challenge of the model agent may never be read as saying (EVIDENCE_CHALLENGE_SPEC section 1; web/test/model_challenge.test.ts)
NEVER = re.compile(
    r"\bused?\b|\buses\b|\busing\b|\brel(y|ied|ies) on\b|because|caus|irrelevant|ruled out|\bproves?\b|\bmarket (moved|rose|fell)|\bprice (moved|rose|fell)",
    re.I,
)
MODEL_SECTION = "section[aria-labelledby=model-challenges]"


def model_challenge_cases(browser, dist, empty, full, out, engine):
    """The What if page's section of the model agent (FULL_DATA exported with --model-challenge): every recorded challenge in words, never
    one of the document's codes and never a sentence that says the model drew on the source or why a market moved; the empty state of a
    site exported without them; a document that is not the one the index names (a failure in the section, the rest of the page still
    there); and a document that holds a result the page has no words for (the page fails plainly, with its page bar)."""
    bad = []
    path = os.path.join(full, "model_challenges.json")
    if not os.path.exists(path):
        return [f"model challenges: {full} holds no model_challenges.json (export it with --model-challenge FILE)"]
    rows = read_json(path)
    server, ctx, page, problems = open_plain(browser, site(dist, full), "#/challenge")
    section = page.locator(MODEL_SECTION).inner_text() if page.locator(MODEL_SECTION).count() == 1 else ""
    text = " ".join(section.split())
    for needle in (
        "The model agent without one source",
        "This measures decision sensitivity",
        "A failed run stays in its pair",
        "(the control)",
    ):
        if needle not in text:
            bad.append(f"model challenges: missing {needle!r}")
    if page.locator(f"{MODEL_SECTION} h3").count() != len(rows):
        bad.append(f"model challenges: {page.locator(f'{MODEL_SECTION} h3').count()} challenges shown of {len(rows)}")
    for code in sorted({r["outcome"] for r in rows} | {r["source"] for r in rows} | {r["original"] for r in rows} | {"RUN_FAILED"}):
        if re.search(rf"(?<![A-Za-z_-]){re.escape(code)}(?![A-Za-z_-])", section):
            bad.append(f"model challenges: the page shows the code {code!r}")
    if NEVER.search(section):
        bad.append(f"model challenges: the section says {NEVER.search(section).group(0)!r}")
    if problems:
        bad.append(f"model challenges: {problems[:2]}")
    bad += shell_problems(page, "model challenges")
    page.screenshot(path=os.path.join(out, f"model-challenges-{engine}.png"), full_page=True)
    ctx.close()
    server.shutdown()

    server, ctx, page, problems = open_plain(browser, site(dist, empty), "#/challenge")
    if "No challenge of the model agent has been run for this copy of the site yet" not in visible(page):
        bad.append("model challenges, none given: the empty state is not said")
    if problems:
        bad.append(f"model challenges, none given: {problems[:2]}")
    bad += shell_problems(page, "model challenges, none given")
    ctx.close()
    server.shutdown()

    root = site(dist, full)  # one byte more than the index names: the section says so, and the rules agent's part is still shown
    with open(os.path.join(root, "data", "model_challenges.json"), "ab") as handle:
        handle.write(b" ")
    server, ctx, page, problems = open_plain(browser, root, "#/challenge")
    text = visible(page)
    for needle in (
        "A document does not match its digest in the index, so it is not shown.",
        "model_challenges.json is not the file the index describes",
        "The rules agent: how far from a different call",
    ):
        if needle not in text:
            bad.append(f"model challenges, changed: missing {needle!r}")
    if page.locator(f"{MODEL_SECTION} [role=alert] button").count() != 1:
        bad.append("model challenges, changed: no way to try again")
    bad += shell_problems(page, "model challenges, changed", KNOWN)
    page.screenshot(path=os.path.join(out, f"model-challenges-changed-{engine}.png"), full_page=True)
    ctx.close()
    server.shutdown()

    def unworded(doc):
        doc[0]["outcome"] = "RULED_OUT"

    server, ctx, page, problems = open_plain(browser, changed(dist, full, "model_challenges.json", unworded), "#/challenge")
    text = visible(page)
    if "This page could not be shown." not in text or "RULED_OUT" in text.replace("not a challenge result: RULED_OUT", ""):
        bad.append(f"model challenges, unworded result: the page did not fail plainly ({text[:120]!r})")
    bad += shell_problems(page, "model challenges, unworded result", KNOWN)
    ctx.close()
    server.shutdown()
    return bad


def main():
    dist, empty, partial, out = sys.argv[1:5]
    engine = sys.argv[sys.argv.index("--engine") + 1] if "--engine" in sys.argv else "chromium"
    bad = run(dist, empty, partial, out, engine)
    print(engine, "ok" if not bad else "FAILED", *bad, sep="\n  " if bad else " ")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
