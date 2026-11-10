#!/usr/bin/python3
"""The "How hard the checks were tried" part of the Evidence of quality page, in a real browser, against the site as it was exported and
against two variants of its sweep document (every swept file still current; no source beside the page, so nothing compared).

The paragraph must say what the document says (the numbers, whether any module changed after its sweep, whether any could not be compared),
the table must hold every module with the least proven first, a module whose swept files changed must be marked as such, and the page must
have no console error, no overflow at 1280, 390 and 320 and no accessibility violation, in light and dark.
Usage: /usr/bin/python3 tools/sweeps_test.py DIST_DIR [--engine chromium|firefox|webkit] [--shots DIR]
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import page_test as pt
import practice_test as pr

PAGE = """() => {
  const h = [...document.querySelectorAll('main h2')].find((x) => x.textContent === 'How hard the checks were tried');
  if (!h) return null;
  const section = h.closest('section');
  const details = section.querySelector('details');
  if (details) details.open = true;
  return {
    sentence: section.querySelector('p').textContent,
    summary: details ? details.querySelector('summary').textContent : null,
    rows: details ? [...details.querySelectorAll('tbody tr')].map((r) => [...r.children].map((c) => c.textContent)) : [],
    pills: details ? [...details.querySelectorAll('.pill.warn')].length : 0,
    wide: [document.documentElement.scrollWidth, window.innerWidth],
  };
}"""


def number(n):
    return f"{n:,}"


def sentence(s):
    if s["modules"] == 0:
        return "No module has a sweep."
    pct = 100 * s["noticed"] // s["places"] if s["places"] else 0
    others = []
    if s["unbuilt"]:
        others.append(f"{number(s['unbuilt'])} could not be built")
    if s["raised"]:
        others.append(f"{number(s['raised'])} made a check raise an error")
    if s["clock"]:
        verb = "was" if s["clock"] == 1 else "were"
        others.append(
            f"{number(s['clock'])} {verb} noticed only because a check ran out of time or the machine failed, which shows nothing about the checks"
        )
    joined = others[0] if len(others) == 1 else (", ".join(others[:-1]) + " and " + others[-1] if others else "")
    first = f"{number(s['modules'])} {'module was' if s['modules'] == 1 else 'modules were'} each changed in every small way their code allows: {number(s['places'])} changes in all."
    second = f"A check noticed {number(s['noticed'])} of them ({pct}%)" + (f"; {joined}." if joined else ".")
    third = (
        "Nothing went unnoticed."
        if s["survived"] == 0
        else f"{number(s['survived'])} went unnoticed; {'it is' if s['survived'] == 1 else 'each is'} listed as harmless, with its reason, in its module's test."
    )
    parts = [first, second, third]
    if s["stale"]:
        parts.append(
            f"{number(s['stale'])} {'module changed after its sweep' if s['stale'] == 1 else 'modules changed after their sweep'}, so what the sweep found says nothing about the code as it is now."
        )
    if s["unchecked"]:
        parts.append(f"{number(s['unchecked'])} {'module' if s['unchecked'] == 1 else 'modules'} could not be compared with the code here.")
    return " ".join(parts)


def variant(dist, change):
    holder = Path(tempfile.mkdtemp(prefix="sweeps-site-"))
    shutil.copytree(dist, holder / "site")
    data = holder / "site" / "data"
    doc = json.loads((data / "sweeps.json").read_text())
    change(doc)
    body = (json.dumps(doc, sort_keys=True, separators=(",", ":"))).encode()
    (data / "sweeps.json").write_bytes(body)
    index = json.loads((data / "index.json").read_text())
    index["files"]["sweeps.json"] = hashlib.sha256(body).hexdigest()
    (data / "index.json").write_text(json.dumps(index))
    return holder, str(holder / "site")


def all_current(doc):
    for r in doc["rows"]:
        r["current"], r["reason"] = True, None
    doc["summary"]["stale"] = 0


def none_compared(doc):
    for r in doc["rows"]:
        r["current"], r["reason"] = None, None
    doc["summary"]["stale"], doc["summary"]["unchecked"] = 0, len(doc["rows"])


def look(browser, base, width, scheme, problems, tag, shots):
    ctx = browser.new_context(viewport={"width": width, "height": 900 if width > 500 else 700}, color_scheme=scheme)
    page = ctx.new_page()
    page.on("pageerror", lambda e: problems.append(f"{tag} {width} {scheme}: page error {e}"))
    page.on("console", lambda m: problems.append(f"{tag} {width} {scheme}: console {m.text}") if m.type == "error" else None)
    page.goto(base + "#/quality")
    page.wait_for_selector("main h1", timeout=15000)
    page.wait_for_function(pt.LOADED, timeout=15000)
    page.wait_for_selector(
        "section[aria-labelledby=sweeps-title] h2", timeout=15000
    )  # not by its words: a status row quotes them in a closed disclosure
    page.wait_for_timeout(300)
    info = page.evaluate(PAGE)
    pt.inject_axe(page)
    axe = page.evaluate(pr.AXE_RUN)
    if shots and width in (1280, 390):
        page.locator("section[aria-labelledby=sweeps-title]").screenshot(path=os.path.join(shots, f"sweeps-{tag}-{width}-{scheme}.png"))
    ctx.close()
    return info, axe


def main():
    dist = sys.argv[1]
    engine = sys.argv[sys.argv.index("--engine") + 1] if "--engine" in sys.argv else "chromium"
    shots = sys.argv[sys.argv.index("--shots") + 1] if "--shots" in sys.argv else None
    if shots:
        os.makedirs(shots, exist_ok=True)
    problems = []
    holders = []
    with sync_playwright() as p:
        browser = pr.launch(p, engine)
        for tag, build in (("as-exported", None), ("all-current", all_current), ("none-compared", none_compared)):
            site = dist
            if build is not None:
                holder, site = variant(dist, build)
                holders.append(holder)
            doc = json.loads((Path(site) / "data" / "sweeps.json").read_text())
            server = pt.serve(site)
            base = f"http://127.0.0.1:{server.server_address[1]}/index.html"
            for width in (1280, 390, 320):
                for scheme in ("light", "dark"):
                    info, axe = look(browser, base, width, scheme, problems, tag, shots)
                    where = f"{tag} {width} {scheme}"
                    if info is None:
                        problems.append(f"{where}: the part is not on the page")
                        continue
                    if info["sentence"] != sentence(doc["summary"]):
                        problems.append(f"{where}: the paragraph is not what the document says: {info['sentence']!r}")
                    if len(info["rows"]) != len(doc["rows"]):
                        problems.append(f"{where}: {len(info['rows'])} rows for {len(doc['rows'])} modules")
                    elif [r[0] for r in info["rows"]] != [r["module"] for r in doc["rows"]]:
                        problems.append(f"{where}: the modules are not in the document's order, least proven first")
                    stale = sum(1 for r in doc["rows"] if r["current"] is False)
                    wrong = [
                        r["module"]
                        for r, cells in zip(doc["rows"], info["rows"])
                        if [cells[1], cells[2], cells[3], cells[4], cells[5]]
                        != [
                            number(r["places"]),
                            number(r["noticed"]),
                            number(r["survived"]),
                            number(r["unbuilt"] + r["raised"]),
                            number(r["clock"]),
                        ]
                    ]
                    if wrong:
                        problems.append(f"{where}: numbers differ from the document for {wrong[:3]}")
                    if info["pills"] != stale:
                        problems.append(f"{where}: {info['pills']} marked rows for {stale} modules that changed after their sweep")
                    first_stale = tag == "as-exported" and doc["rows"] and doc["rows"][0]["current"] is False
                    if first_stale and "no: " not in info["rows"][0][6]:
                        problems.append(f"{where}: a module that changed after its sweep is not marked: {info['rows'][0]}")
                    if info["wide"][0] > info["wide"][1]:
                        problems.append(f"{where}: the page is wider than the window {info['wide']}")
                    if axe["violations"] or (width == 1280 and axe["incomplete"]):
                        problems.append(f"{where}: axe {axe}")
            server.shutdown()
        browser.close()
    for holder in holders:
        shutil.rmtree(holder, ignore_errors=True)
    print("sweeps_test:", "ok" if not problems else "FAILED", f"({engine})")
    for line in problems[:20]:
        print(" ", line)
    sys.exit(0 if not problems else 1)


if __name__ == "__main__":
    main()
