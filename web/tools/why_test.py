#!/usr/bin/python3
"""The "Why this call" part of a receipt, in a real browser.

A call of the rules agent says where the market stood (the state, in words) and how far the data was from a different call, as sentences;
it prints no return value of the exchanges' data. A call of a model agent has no such part. The page must have no console error, must not be
wider than the window at 390 and 320 CSS pixels, and must have no accessibility violation in the part.
Usage: /usr/bin/python3 tools/why_test.py DIST_DIR [--engine chromium|firefox|webkit] [--shots DIR]
"""

import json
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import page_test as pt

SECTION = """() => { const h = [...document.querySelectorAll('main h2')].find((x) => x.textContent === 'Why this call');
 if (!h) return null;
 const items = []; for (let n = h.nextElementSibling; n && n.tagName === 'UL'; n = n.nextElementSibling) items.push(...[...n.querySelectorAll('li')].map((li) => li.textContent));
 return { items }; }"""


def receipts(dist):
    folder = os.path.join(dist, "data", "receipts")
    return [(name[:-5], json.load(open(os.path.join(folder, name)))) for name in sorted(os.listdir(folder)) if name.endswith(".json")]


def run(dist, engine, shots=None):
    boundaries_file = Path(dist, "data", "boundaries.json")
    boundaries = json.loads(boundaries_file.read_text()) if boundaries_file.exists() else []
    wanted = {(b["cycle_id"], b["arm"], b["symbol"], b["hours"]): b for b in boundaries}
    found = [
        (i, r["row"])
        for i, r in receipts(dist)
        # a boundary describes the up-or-down call of its slot; the barrier call of the same slot shares its key and has no such part
        if r["row"]["contract"] == "D" and (r["row"]["cycle_id"], r["row"]["arm"], r["row"]["symbol"], r["row"]["hours"]) in wanted
    ]
    others = [
        (i, r["row"])
        for i, r in receipts(dist)
        if (r["row"]["cycle_id"], r["row"]["arm"], r["row"]["symbol"], r["row"]["hours"]) not in wanted
    ]
    results = {"with": [], "without": [], "narrow": [], "problems": []}
    with sync_playwright() as p:
        launch = {}
        shell = os.path.expanduser(
            "~/Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell"
        )
        if engine == "chromium" and os.path.exists(shell):
            launch["executable_path"] = shell
        browser = getattr(p, engine).launch(**launch)
        server = pt.serve(dist)
        base = f"http://127.0.0.1:{server.server_address[1]}/index.html"
        for width, height in ((1280, 900), (390, 844), (320, 640)):
            ctx = browser.new_context(viewport={"width": width, "height": height})
            page = ctx.new_page()
            page.on("pageerror", lambda e: results["problems"].append(str(e)))
            page.on("console", lambda m: results["problems"].append(m.text) if m.type == "error" else None)
            for ident, row in found[:3]:
                page.goto(f"{base}#/receipts/{ident}")
                page.wait_for_selector("main h1")
                page.wait_for_selector("text=Why this call", timeout=15000)
                section = page.evaluate(SECTION)
                b = wanted[(row["cycle_id"], row["arm"], row["symbol"], row["hours"])]
                if width == 1280:
                    results["with"].append(
                        {"id": ident, "items": section["items"], "ret": b["ret"], "moves": [m["at"] for m in b["moves"]]}
                    )
                width_now = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
                if width_now[0] > width_now[1]:
                    results["narrow"].append({"id": ident, "width": width, "scroll": width_now[0]})
                if shots and ident == found[0][0]:
                    page.screenshot(path=os.path.join(shots, f"why-{engine}-{width}.png"), full_page=True)
            if width == 1280:
                for ident, row in others[:3]:
                    page.goto(f"{base}#/receipts/{ident}")
                    page.wait_for_selector("main h1")
                    page.wait_for_timeout(600)
                    results["without"].append({"id": ident, "arm": row["arm"], "section": page.evaluate(SECTION)})
            ctx.close()
        server.shutdown()
        browser.close()
    return results


def main():
    dist = sys.argv[1]
    engine = sys.argv[sys.argv.index("--engine") + 1] if "--engine" in sys.argv else "chromium"
    shots = sys.argv[sys.argv.index("--shots") + 1] if "--shots" in sys.argv else None
    if shots:
        os.makedirs(shots, exist_ok=True)
    r = run(dist, engine, shots)
    ok = True
    if not r["with"] and not r["without"] and not os.listdir(os.path.join(dist, "data", "receipts")):
        print("why_test: ok (the site holds no receipt, so there is nothing to check)")
        sys.exit(0)
    if not r["with"]:
        ok = False
        print("NO RECEIPT WITH A MEASURE OF HOW FAR THE CALL WAS FROM CHANGING WAS FOUND (does the site have boundaries.json?)")
    for item in r["with"]:
        lines = item["items"]
        text = " ".join(lines)
        if not lines or not lines[0].startswith("The state of the market the rule saw was "):
            ok = False
            print("THE FIRST SENTENCE IS NOT THE STATE", item["id"], lines[:1])
        for value in [item["ret"], *item["moves"]]:
            if re.search(re.escape(value) + r"(?!\d)", text):
                ok = False
                print("A RETURN VALUE OF THE DATA IS PRINTED", item["id"], value)
        if any("held for" in x.lower() for x in lines):
            ok = False
    for item in r["without"]:
        if item["section"] is not None:
            ok = False
            print("A RECEIPT WITH NO MEASURE SHOWS THE PART", item)
    if r["narrow"]:
        ok = False
        print("THE PAGE IS WIDER THAN THE WINDOW", r["narrow"])
    if r["problems"]:
        ok = False
        print("CONSOLE OR PAGE ERRORS", r["problems"][:3])
    print("why_test:", "ok" if ok else "FAILED", f"({len(r['with'])} receipts with it, {len(r['without'])} without)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
