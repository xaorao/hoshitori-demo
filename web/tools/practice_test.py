#!/usr/bin/python3
"""The Practice trades page, in a real browser, against books made by the real read-model (``practice_books.json``) and against the site as it was exported.

The populated site is the built dist with its ``practice.json`` replaced by the fixture (and its digest in the index): a round trip with
its call linked, a position held and marked, a position never marked, a book stopped after a fall, a curve of 120 points with the numbers
behind it. The page must show every number as the document says it (thousands separators only, no rounding), say what the curve shows in
words, link each closed position to its call, show honest empty states, have no console error, not be wider than the window at 1280, 390
and 320 CSS pixels, and have no accessibility violation, in light and dark.
Usage: /usr/bin/python3 tools/practice_test.py DIST_DIR [--engine chromium|firefox|webkit] [--shots DIR]
"""

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

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "practice_books.json"
HEADING = "What the practice portfolio would have done, at three levels of cost."
AGENT_PHRASES = {
    "L0": "fixed rule",
    "L1": "reads the evidence once",
    "L2": "bull and a bear",
    "L1x": "spending about as much",
}  # a phrase of each explanation
LEVELS = {"BASE": "base costs", "STRESS": "stress costs", "DOUBLE_FEES": "double fees"}  # as the page words a level (format.practiceLevel)
AXE_RUN = (
    "async () => { const r = await axe.run(document, {resultTypes: ['violations', 'incomplete']}); "
    "return {violations: r.violations.map((v) => v.id + ': ' + v.nodes.length), incomplete: r.incomplete.map((v) => v.id + ': ' + v.nodes.length)}; }"
)
PAGE = """() => ({
  h1: document.querySelector('main h1').textContent,
  title: document.title,
  wide: [document.documentElement.scrollWidth, window.innerWidth],
  text: document.querySelector('main').innerText,
  summaryRows: [...document.querySelectorAll('table')]
    .filter((t) => t.querySelector('caption') && t.querySelector('caption').textContent === 'Every practice book')
    .map((t) => [...t.querySelectorAll('tbody tr')].map((r) => r.innerText)).flat(),
  sections: [...document.querySelectorAll('section.practice-book')].map((s) => ({
    h2: s.querySelector('h2').textContent,
    svg: s.querySelector('svg.curve') ? s.querySelector('svg.curve').getAttribute('aria-label') : null,
    svgRole: s.querySelector('svg.curve') ? s.querySelector('svg.curve').getAttribute('role') : null,
    dashed: [...s.querySelectorAll('svg.curve path[stroke-dasharray]')].map((p) => p.getAttribute('d')),
    points: s.querySelector('svg.curve') ? s.querySelectorAll('svg.curve circle').length : 0,
    curveRows: s.querySelectorAll('details tbody tr').length,
    links: [...s.querySelectorAll('a')].map((a) => a.getAttribute('href')),
    text: s.innerText,
  })),
  agents: [...document.querySelectorAll('main details')].filter((d) => d.querySelector('summary').textContent === 'Who the agents are').map((d) => d.querySelector('dl').textContent).join(' '),
  regions: [...document.querySelectorAll('.table-wrap')].map((r) => [r.getAttribute('role'), r.getAttribute('aria-label'), r.tabIndex]),
})"""


def money(text):
    """The page's own rule for a headline amount: cents, rounded half away from zero, with thousands separators."""
    from decimal import ROUND_HALF_UP, Decimal

    value = Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{value:,.2f}" if value != 0 else "0.00"


def grouped(text):
    """The page's own rule for an amount that keeps its digits: thousands separators and nothing else."""
    negative = text.startswith("-")
    whole, _, fraction = text.lstrip("-").partition(".")
    return f"{'-' if negative else ''}{int(whole):,}{'.' + fraction if fraction else ''}"


def gaps_in(curve):
    """How many stretches of a curve have no mark: a spacing of more than ten times the usual one (the median spacing), judged from four marks on.
    Written here from the rule, not from the page's code, so that the two can disagree."""
    from datetime import datetime

    if len(curve) < 4:
        return 0
    times = [datetime.fromisoformat(p["at"].replace("Z", "+00:00")).timestamp() for p in curve]
    spacing = [b - a for a, b in zip(times, times[1:])]
    ordered = sorted(spacing)
    n = len(ordered)
    usual = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    return 0 if usual <= 0 else sum(1 for s in spacing if s > 10 * usual)


def copy_site(dist, prefix):
    holder = Path(tempfile.mkdtemp(prefix=prefix))
    shutil.copytree(dist, holder / "site")
    return holder, holder / "site" / "data"


def populated(dist):
    """A copy of the site whose practice document is the fixture."""
    holder, data = copy_site(dist, "practice-site-")
    body = FIXTURE.read_bytes()
    (data / "practice.json").write_bytes(body)
    index = json.loads((data / "index.json").read_text())
    index["files"]["practice.json"] = hashlib.sha256(body).hexdigest()
    (data / "index.json").write_text(json.dumps(index))
    return holder, str(holder / "site")


def without(dist):
    """A copy of the site as an older export left it: no practice document."""
    holder, data = copy_site(dist, "practice-none-")
    if (data / "practice.json").exists():
        (data / "practice.json").unlink()
    index = json.loads((data / "index.json").read_text())
    index["files"].pop("practice.json", None)
    (data / "index.json").write_text(json.dumps(index))
    return holder, str(holder / "site")


def launch(p, engine):
    kw = {}
    shell = os.path.expanduser(
        "~/Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell"
    )
    if engine == "chromium" and os.path.exists(shell):
        kw["executable_path"] = shell
    return getattr(p, engine).launch(**kw)


def look(browser, base, width, scheme, problems, tag, shots=None, engine="chromium"):
    ctx = browser.new_context(viewport={"width": width, "height": 900 if width > 500 else 700}, color_scheme=scheme)
    page = ctx.new_page()
    page.on("pageerror", lambda e: problems.append(f"{tag} {width} {scheme}: page error {e}"))
    page.on(
        "console",
        lambda m: (
            problems.append(f"{tag} {width} {scheme}: console {m.text}")
            if m.type == "error" and not pt.harness_noise(m.text, engine)  # WebKit reports the style its own screenshot injects
            else None
        ),
    )
    page.goto(base + "#/practice")
    page.wait_for_selector("main h1", timeout=15000)
    page.wait_for_function(pt.LOADED, timeout=15000)
    page.wait_for_timeout(300)
    info = page.evaluate(PAGE)
    pt.inject_axe(page)
    axe = page.evaluate(AXE_RUN)
    if shots:
        page.screenshot(path=os.path.join(shots, f"practice-{tag}-{width}-{scheme}.png"), full_page=True)
    ctx.close()
    return info, axe


def verify_populated(books, info, axe, width, scheme, problems):
    where = f"populated {width} {scheme}"
    if info["h1"] != HEADING:
        problems.append(f"{where}: heading is {info['h1']!r}")
    if not info["title"].startswith("Practice trades "):
        problems.append(f"{where}: the title is {info['title']!r}")
    if info["wide"][0] > info["wide"][1]:
        problems.append(f"{where}: the page is wider than the window {info['wide']}")
    if len(info["summaryRows"]) != len(books) or len(info["sections"]) != len(books):
        problems.append(f"{where}: {len(info['summaryRows'])} summary rows and {len(info['sections'])} sections for {len(books)} books")
    for b, section in zip(books, info["sections"]):
        text = section["text"]
        want = f"{b['arm_name']}, {LEVELS[b['level']]}"
        if section["h2"] != want:
            problems.append(f"{where}: {b['book']}: the section is headed {section['h2']!r}, not {want!r}")
        for trade in b["trades"]:
            for price in (trade["entry_price"], trade["exit_price"]):
                if grouped(price) not in text:
                    problems.append(
                        f"{where}: {b['book']}: the price {price} of a closed {trade['symbol']} position is not on the page as {grouped(price)}"
                    )
        for amount in (b["opening_equity"], b["equity"], b["peak"], b["cash"]):
            if money(amount) not in text:
                problems.append(f"{where}: {b['book']}: {money(amount)} (the cents of {amount}) is not on the page")
        if b["curve"]:
            says = section["svg"] or ""
            if section["svgRole"] != "img" or (len(b["curve"]) > 1 and f"over {len(b['curve'])} marks" not in says):
                problems.append(f"{where}: {b['book']}: the curve does not say in words what it draws: {says!r}")
            wanted = gaps_in(b["curve"])
            drawn = sum(d.count("M") for d in section["dashed"])
            if drawn != wanted:
                problems.append(f"{where}: {b['book']}: {wanted} stretches have no mark and {drawn} are drawn dashed")
            if (wanted > 0) != ("A dashed stretch" in text) or (wanted > 0) != ("No mark is drawn between" in says):
                problems.append(
                    f"{where}: {b['book']}: the stretches without a mark are not said in words, on the page and in the picture's label"
                )
            drawn_of = f"draws {len(b['curve'])} of the book's {b['marks']} marks"
            thinned = b["marks"] > len(b["curve"])
            if thinned != (drawn_of in says) or thinned != (drawn_of in text):
                problems.append(
                    f"{where}: {b['book']}: the curve draws {len(b['curve'])} of {b['marks']} marks; "
                    "it must say so, in the picture's label and on the page, and a curve that draws them all must not"
                )
            if section["curveRows"] != len(b["curve"]) or section["points"] != len(b["curve"]):
                problems.append(
                    f"{where}: {b['book']}: {section['points']} points and {section['curveRows']} numbers for {len(b['curve'])} marks"
                )
        elif "No mark yet." not in text or section["svg"] is not None:
            problems.append(f"{where}: {b['book']}: a book with no mark says so and draws nothing")
        for held in b["positions"]:
            if held["mark"] is None and "never marked" not in text:
                problems.append(f"{where}: {b['book']}: a position never marked must say so, not show a value")
        if not b["positions"] and "Nothing is held." not in text:
            problems.append(f"{where}: {b['book']}: no position held is not said")
        if b["trades"]:
            wanted = [f"#/receipts/{t['forecast_id']}" for t in b["trades"] if t["forecast_id"]]
            if [h for h in section["links"] if h in wanted] != wanted:
                problems.append(f"{where}: {b['book']}: closed positions do not link to their calls: {section['links']}")
        elif "No position has been closed yet." not in text:
            problems.append(f"{where}: {b['book']}: no closed position is not said")
    for b in books:
        if AGENT_PHRASES[b["arm"]] not in info["agents"] or b["arm_name"] not in info["agents"]:
            problems.append(f"{where}: the agents are not explained: {b['arm']} ({b['arm_name']}) is missing from {info['agents'][:80]!r}")
    for token in re.findall(r"-?\d[\d,]*(?:\.\d+)?", info["text"]):
        if len(re.sub(r"\D", "", token).lstrip("0")) > 16:
            problems.append(f"{where}: a number on the page runs past sixteen digits: {token[:40]}")
    for needle in (
        "No order was sent anywhere",
        "not something anyone traded",
        "Stopped after a deep fall",
        "the target was reached",
        "the stop was reached",
    ):
        if needle not in info["text"]:
            problems.append(f"{where}: the page does not say {needle!r}")
    for region in info["regions"]:
        if region[0] != "region" or not region[1] or region[2] != 0:
            problems.append(f"{where}: a scrolling table is not a labelled, focusable region: {region}")
    if axe["violations"] or (
        width == 1280 and axe["incomplete"]
    ):  # at phone widths axe cannot decide the contrast of a scrolled table, as on every page
        problems.append(f"{where}: axe {axe}")


def main():
    dist = sys.argv[1]
    engine = sys.argv[sys.argv.index("--engine") + 1] if "--engine" in sys.argv else "chromium"
    shots = sys.argv[sys.argv.index("--shots") + 1] if "--shots" in sys.argv else None
    if shots:
        os.makedirs(shots, exist_ok=True)
    books = json.loads(FIXTURE.read_text())
    problems = []
    holders = []
    with sync_playwright() as p:
        browser = launch(p, engine)
        for label, build in (("populated", populated), ("none", without), ("as-exported", None)):
            site = dist
            if build is not None:
                holder, site = build(dist)
                holders.append(holder)
            server = pt.serve(site)
            base = f"http://127.0.0.1:{server.server_address[1]}/index.html"
            for width in (1280, 390, 320):
                for scheme in ("light", "dark"):
                    info, axe = look(browser, base, width, scheme, problems, label, shots if width in (1280, 390) else None, engine)
                    if label == "populated":
                        verify_populated(books, info, axe, width, scheme, problems)
                    elif label == "none":
                        if "exported without a practice portfolio" not in info["h1"]:
                            problems.append(f"none {width} {scheme}: heading is {info['h1']!r}")
                        if info["wide"][0] > info["wide"][1] or axe["violations"]:
                            problems.append(f"none {width} {scheme}: overflow or axe {axe} {info['wide']}")
                    else:
                        if info["h1"] != HEADING:
                            problems.append(f"as exported {width} {scheme}: heading is {info['h1']!r}")
                        if info["wide"][0] > info["wide"][1] or axe["violations"]:
                            problems.append(f"as exported {width} {scheme}: overflow or axe {axe} {info['wide']}")
            server.shutdown()
        browser.close()
    for holder in holders:
        shutil.rmtree(holder, ignore_errors=True)
    print("practice_test:", "ok" if not problems else "FAILED", f"({engine}, {len(books)} books)")
    for line in problems[:20]:
        print(" ", line)
    sys.exit(0 if not problems else 1)


if __name__ == "__main__":
    main()
