#!/usr/bin/python3
"""The page test (UX_SPEC section 3): every route at three viewports and two colour schemes, and at two viewports in forced colours and
with reduced motion, in a real browser.

For each page it records: visible words fully inside the first viewport (the information budget), console errors, page errors, failed requests,
horizontal overflow, axe-core violations and incomplete results, and the focus target after navigation; and it saves a screenshot.
It also follows the skip link on every page state (WCAG 2.4.1: the keyboard reaches the main content and the page stays as it was) and,
in the reduced-motion variant no element may transition or animate (WCAG 2.3.3 and the specification's motion rule); in the forced-colours variant
the same checks run (overflow, axe, the keyboard walk, focus rings) and the screenshot is saved to be read;
once per page, arrives at the page by a link from another one (WCAG 2.4.3: the keyboard is on the page's heading once it has loaded),
then goes Back; a receipt is also reached from the Calls table and from another receipt.
Usage: /usr/bin/python3 tools/page_test.py DIST_DIR OUT_DIR [--engine chromium|firefox|webkit] [--only page,page]
DIST_DIR is the built site with its data under data/ (npm run build, then copy the export into dist/data).
"""

import functools
import http.server
import json
import os
import re
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
# The headers the site is served with (deploy/security_headers.json): every page is tested under them, so a page that needs an inline script,
# a remote file or an inline style the policy forbids fails here and not in front of a visitor.
HEADERS = json.loads(Path(HERE, "..", "..", "deploy", "security_headers.json").read_text(encoding="utf-8"))
VIOLATIONS = "window.__csp = []; document.addEventListener('securitypolicyviolation', e => window.__csp.push(e.violatedDirective + ' ' + (e.blockedURI || 'inline')));"
AXE = Path(HERE, "..", "node_modules", "axe-core", "axe.min.js").read_text(encoding="utf-8")
VIEWPORTS = [(1440, 900), (390, 844), (320, 640)]
SCHEMES = ["light", "dark"]
# The variants: a colour scheme at every viewport, and forced colours and reduced motion at the first two (desktop and phone).
VARIANTS = [(s, {"color_scheme": s}, VIEWPORTS) for s in SCHEMES] + [
    ("forced", {"color_scheme": "light", "forced_colors": "active"}, VIEWPORTS[:2]),
    ("reduced", {"color_scheme": "light", "reduced_motion": "reduce"}, VIEWPORTS[:2]),
]
# The information budget of the first screen (the interface specification, section 2.4c): words fully inside the first viewport, by page and viewport. They are
# priors fixed before the page was written to them; a page over its budget is a failure of the page test, not a note. Text that is only read
# aloud (visually hidden) and the content of a closed disclosure are not counted: the budget is about what a person sees.
WORD_BUDGET = {("today", 1440, 900): 150, ("today", 390, 844): 90}
WORDS = """() => { const vh = innerHeight, vw = innerWidth; let words = 0;
 const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
 while ((n = w.nextNode())) { const t = n.textContent.trim(); if (!t) continue; const p = n.parentElement;
  if (['SCRIPT','STYLE','NOSCRIPT'].includes(p.tagName)) continue; const r = document.createRange(); r.selectNodeContents(n);
  const b = r.getBoundingClientRect(); const cs = getComputedStyle(p);
  let hidden = false; for (let e = p; e && e !== document.body; e = e.parentElement) { const s = getComputedStyle(e);
   if (s.position === 'absolute' && parseFloat(s.width) <= 1 && parseFloat(s.height) <= 1) { hidden = true; break; } /* visually hidden text is read aloud, not seen */
   if (e.tagName === 'DETAILS' && !e.open) { const sm = p.closest('summary'); if (!sm || sm.parentElement !== e) { hidden = true; break; } } } /* a closed disclosure shows its summary only */
  if (!hidden && b.width > 0 && b.height > 0 && b.top >= 0 && b.bottom <= vh && b.left >= 0 && b.right <= vw && cs.visibility !== 'hidden' && cs.display !== 'none')
   words += t.split(/\\s+/).filter(x => /[A-Za-z0-9]/.test(x)).length; } return words; }"""
OVERFLOW = "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
# Our own source ids (hoshitori.features, hoshitori.crosscheck, ...) are names for code, not for a reader: no page shows one in its text.
RAW_IDS = r"() => { const m = document.querySelector('main'); const t = m ? m.innerText : ''; return [...new Set(t.match(/\bhoshitori\.[a-z_]+/g) || [])]; }"
FOCUS = "() => { const a = document.activeElement; return a ? a.tagName + ':' + (a.textContent || '').slice(0, 40) : null }"
SMALL = """() => { const bad = []; for (const el of document.querySelectorAll('a,button,input,select,textarea,[tabindex]')) {
 const r = el.getBoundingClientRect(); if (r.width > 0 && r.height > 0 && (r.width < 24 || r.height < 24) && getComputedStyle(el).display !== 'inline')
  bad.push({tag: el.tagName, w: Math.round(r.width), h: Math.round(r.height), text: (el.textContent || '').slice(0, 30)}); } return bad; }"""
# a loading line has gone; the notice that the documents are old is a status too, and it stays (states_test.py waits the same way)
MOTION = """() => { const bad = []; for (const el of document.querySelectorAll('*')) { const s = getComputedStyle(el);
 const moves = s.transitionDuration.split(',').some(d => parseFloat(d) > 0) || (s.animationName !== 'none' && parseFloat(s.animationDuration) > 0);
 if (moves) bad.push(el.tagName + '.' + String(el.className).slice(0, 30)); } return bad.slice(0, 5); }"""
LOADED = "() => !document.querySelector('[role=status]:not(.notice)')"
TITLES = {
    "today": "Today",
    "calls": "Calls",
    "record": "Track record",
    "verify": "Check a record",
    "quality": "Evidence of quality",
    "drill": "Failure drills",
    "brand": "Brand",
    "claims": "Check a claim",
    "challenge": "Test a source",
    "desk": "The Desk",
    "practice": "Practice trades",
    "lab": "Laboratory",
    "judges": "For judges",
    "feed": "Feed",
    "voices": "Voices and whales",
    "missing": "Not found",
    "receipt": "Receipt",
    "callsby": "Calls",
}
WHERE = """() => { const main = document.querySelector('main'), a = document.activeElement, h1 = main && main.querySelector('h1');
 return {hash: location.hash, h1: h1 ? h1.textContent : null, on_h1: !!h1 && a === h1, on_main: !!main && a === main,
  focus: !a || a === document.body ? 'BODY' : a.tagName + ':' + (a.textContent || '').trim().slice(0, 40)}; }"""
ONWARD = """() => { const m = document.querySelector('main'), a = document.activeElement;
 return !!m && !!a && a !== document.body && (m.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0; }"""


def skip_link(page, engine):
    """WCAG 2.4.1: the first Tab stop is the skip link; following it puts the keyboard on the main content without changing the page or
    its address, and the next Tab goes on from there, not from the top."""
    tab = "Alt+Tab" if engine == "webkit" else "Tab"
    # blur() alone leaves the browser's sequential focus starting point where the keyboard walk stopped (deep in a long table), so the next
    # Tab would go on from there: focus a throwaway element at the very top and remove it, which moves the starting point to the top
    page.evaluate(
        "() => { const t = document.createElement('span'); t.tabIndex = -1; document.body.prepend(t); t.focus(); t.remove();"
        " window.scrollTo(0, 0); }"
    )
    before = page.evaluate(WHERE)
    page.keyboard.press(tab)
    first = page.evaluate("() => (document.activeElement && document.activeElement.textContent || '').trim()")
    page.keyboard.press("Enter")
    page.wait_for_timeout(200)
    after = page.evaluate(WHERE)
    page.keyboard.press(tab)
    onward = page.evaluate(ONWARD)
    problems = []
    if first != "Skip to the page":
        problems.append(f"the first Tab stop is {first!r}")
    if after["hash"] != before["hash"]:
        problems.append(f"the address went from {before['hash']!r} to {after['hash']!r}")
    if after["h1"] != before["h1"]:
        problems.append(f"the page went from {before['h1']!r} to {after['h1']!r}")
    if not after["on_main"]:
        problems.append(f"the keyboard is on {after['focus']!r}, not the main content")
    if not onward:
        problems.append("the next Tab did not go on from the main content")
    return problems


def arrive(page, title):
    """Wait until the page called ``title`` has loaded: its title, its heading and no loading line; then a moment for focus to move."""
    page.wait_for_function("t => document.title.startsWith(t + ' ')", arg=title, timeout=15000)
    page.wait_for_selector("main h1", timeout=15000)
    page.wait_for_function(LOADED, timeout=15000)
    page.wait_for_timeout(300)


def heading_of(dist, ident):
    """The heading a receipt's page shows, from its document: "BTC, 1 hour: Rules"."""
    with open(os.path.join(dist, "data", "receipts", ident + ".json"), encoding="utf-8") as handle:
        row = json.load(handle)["row"]
    hours = "1 hour" if row["hours"] == 1 else f"{row['hours']} hours"
    return f"{row['symbol']}, {hours}: {row['arm_name']}"


def arrival(browser, base, dist, name, hash_, receipts):
    """WCAG 2.4.3: a page reached by a link from another page has the keyboard on its heading once it has loaded, whether its heading
    is there at once or appears after its documents load, and Back does the same for the page before. The link is followed with the
    keyboard. A receipt is reached through its link in the Calls table (the link goes away with the old page) and from another receipt."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    problems = []
    start, start_title = ("#/calls", "Calls") if name in ("today", "receipt") else ("#/", "Today")
    page.goto(base + start)
    arrive(page, start_title)
    page.evaluate("() => document.querySelectorAll('nav details').forEach((d) => { d.open = true })")  # the menu
    link = page.locator(f"a[href='{hash_}']").first
    if hash_ in (
        "#/",
        "#/calls",
        "#/record",
        "#/verify",
        "#/quality",
        "#/drill",
        "#/claims",
        "#/challenge",
        "#/desk",
        "#/judges",
        "#/feed",
        "#/voices",
    ):
        link = page.locator(f"nav[aria-label=Pages] a[href='{hash_}']").first  # a primary page has a second link in the menu
    if link.count() > 0:
        link.focus()
        page.keyboard.press("Enter")
        how = "a link"
    else:
        page.evaluate("h => { location.hash = h }", hash_)
        how = "its address"
    arrive(page, TITLES[name])
    got = page.evaluate(WHERE)
    if not got["on_h1"]:
        problems.append(f"reached by {how} from {start_title}: the keyboard is on {got['focus']!r}, not the heading {got['h1']!r}")
    page.go_back()
    arrive(page, start_title)
    got = page.evaluate(WHERE)
    if not got["on_h1"]:
        problems.append(f"Back to {start_title}: the keyboard is on {got['focus']!r}, not the heading {got['h1']!r}")
    others = []
    if name == "receipt" and receipts:
        others = [r for r in receipts[1:] if heading_of(dist, r) != heading_of(dist, receipts[0])]
    if others:
        page.goto(base + f"#/receipts/{receipts[0]}")
        arrive(page, "Receipt")
        page.evaluate("h => { location.hash = h }", f"#/receipts/{others[0]}")
        page.wait_for_function("h => location.hash === h", arg=f"#/receipts/{others[0]}")
        arrive(page, "Receipt")
        heading = heading_of(dist, others[0])
        page.wait_for_function("t => document.querySelector('main h1').textContent === t", arg=heading, timeout=15000)
        page.wait_for_timeout(300)
        got = page.evaluate(WHERE)
        if not got["on_h1"]:
            problems.append(f"from one receipt to another: the keyboard is on {got['focus']!r}, not the heading {got['h1']!r}")
    ctx.close()
    return problems


def keyboard_walk(page, engine, limit=60):
    """Tab through the page from the top: what takes focus, in order, whether it shows a focus ring, and whether it is on screen."""
    page.evaluate("() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); }")
    seen = []
    for _ in range(limit):
        # Safari does not tab to links unless asked to (Option+Tab); Playwright's WebKit follows it
        page.keyboard.press("Alt+Tab" if engine == "webkit" else "Tab")
        info = page.evaluate(
            """() => { const a = document.activeElement; if (!a || a === document.body) return null;
 const cs = getComputedStyle(a); const r = a.getBoundingClientRect();
 return {tag: a.tagName, text: (a.getAttribute('aria-label') || a.textContent || '').trim().slice(0, 40),
  ring: cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) >= 2,
  onscreen: r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth, covered: (() => {
   const x = (Math.max(r.left, 0) + Math.min(r.right, innerWidth)) / 2, y = (Math.max(r.top, 0) + Math.min(r.bottom, innerHeight)) / 2;
   const top = document.elementFromPoint(x, y); return !!top && top !== a && !a.contains(top) && !top.contains(a); })(), key: a.tagName + '|' + (a.getAttribute('href') || '') + '|' + (a.textContent || '').slice(0, 20)}; }"""
        )
        if info is None:
            break
        if seen and seen[0]["key"] == info["key"]:
            break  # back at the start
        seen.append(info)
    return seen


NAV = "() => Array.from(document.querySelectorAll('nav[aria-label=Pages] a, nav[aria-label=Pages] summary')).map(e => (e.textContent || '').trim())"


def nav_inconsistencies(navs):
    """WCAG 3.2.6 (consistent help): the navigation, where the help and limits links sit, is the same list in the same order on every page of one
    layout. navs maps a page state's key (page-WxH-variant) to its list; each state is compared with the first state of the same layout."""
    first = {}
    out = []
    for key in sorted(navs):
        layout = key.split("-", 1)[1]
        if layout not in first:
            first[layout] = (key, navs[key])
        elif navs[key] != first[layout][1]:
            out.append(f"{key}: the navigation differs from {first[layout][0]}")
    return out


def name_problems(tree):
    """What a screen reader would announce, read from the accessibility tree: names that repeat themselves, a bare arrow, and two buttons
    or two links with one name that lead to different places (a person hearing a list of controls cannot tell them apart)."""
    out = []
    buttons, links, last = {}, {}, None
    for line in tree.splitlines():
        text = line.strip()
        m = re.match(r'- (?:link|button) "(.*)"(?::)?$', text) or re.match(r"- '(?:link|button) \"(.*)\"'(?::)?$", text)
        if text.startswith("- /url:"):
            if last is not None:
                links.setdefault(last, set()).add(text)
            continue
        kind = "link" if text.startswith(("- link", "- 'link")) else ("button" if text.startswith(("- button", "- 'button")) else None)
        last = None
        if kind is None or m is None:
            continue
        label = m.group(1)
        if re.search(r"([A-Za-z]{4,})\1", label):
            out.append(f"{kind} name repeats itself: {label}")
        if "\u2192" in label:
            out.append(f"{kind} name has a bare arrow: {label}")
        if kind == "button":
            buttons[label] = buttons.get(label, 0) + 1
        else:
            last = label
    out += [f"{n} buttons are all called {label}" for label, n in buttons.items() if n > 1]
    out += [f"{len(urls)} links called {label} lead to different places" for label, urls in links.items() if len(urls) > 1]
    return out


def harness_noise(text, engine):
    """WebKit reports the style that Playwright's own screenshot injects as a refused stylesheet. The page's real violations are caught by the
    page's own securitypolicyviolation listener (VIOLATIONS), which that injected style does not trigger, so this one console line is not a finding."""
    return engine == "webkit" and text.startswith("Refused to apply a stylesheet because")


def inject_axe(page):
    """Put axe-core into the page. Evaluated, not added as a script: the pages are served under a policy that forbids an injected inline script."""
    page.evaluate("() => {" + AXE + "\n}")


def serve(root):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def end_headers(self):
            for name, value in HEADERS.items():
                self.send_header(name, value)
            super().end_headers()

    handler = functools.partial(Quiet, directory=root)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def routes(dist):
    index = json.loads(Path(dist, "data", "index.json").read_text())
    receipts = sorted(p for p in index["files"] if p.startswith("receipts/"))
    first = receipts[0][len("receipts/") : -len(".json")] if receipts else None
    out = [
        ("today", "#/"),
        ("calls", "#/calls"),
        ("record", "#/record"),
        ("verify", "#/verify"),
        ("quality", "#/quality"),
        ("drill", "#/drill"),
        ("brand", "#/brand"),
        ("claims", "#/claims"),
        ("challenge", "#/challenge"),
        ("desk", "#/desk"),
        ("practice", "#/practice"),
        ("lab", "#/lab"),
        ("judges", "#/judges"),
        ("feed", "#/feed"),
        ("voices", "#/voices"),
        ("missing", "#/nowhere"),
    ]
    if first:
        out.append(("receipt", f"#/receipts/{first}"))
        row = json.loads(Path(dist, "data", "receipts", f"{first}.json").read_text())["row"]
        out.append(("callsby", f"#/calls/token/{row['symbol']}"))  # every call on one token: the lens side pages open
    return out


def receipt_ids(dist):
    with open(os.path.join(dist, "data", "index.json"), encoding="utf-8") as handle:
        files = json.load(handle)["files"]
    return sorted(p[len("receipts/") : -len(".json")] for p in files if p.startswith("receipts/"))


def guarded(check, *args):
    """A check's problems; a check that cannot finish (a page that never loads) is a problem too, not the end of the run."""
    try:
        return check(*args)
    except Exception as exc:  # any failure of the page is reported against it
        return [f"{check.__name__} could not finish: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}"]


def main():
    dist, out = sys.argv[1], sys.argv[2]
    engine = sys.argv[sys.argv.index("--engine") + 1] if "--engine" in sys.argv else "chromium"
    only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
    os.makedirs(out, exist_ok=True)
    server = serve(dist)
    base = f"http://127.0.0.1:{server.server_address[1]}/index.html"
    report = {}
    navs = {}
    with sync_playwright() as p:
        kind = getattr(p, engine)
        launch = {}
        shell = os.path.expanduser(
            "~/Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell"
        )
        if engine == "chromium" and os.path.exists(shell):
            launch["executable_path"] = shell
        browser = kind.launch(**launch)
        for name, hash_ in routes(dist):
            if only is not None and name not in only:
                continue
            # a fresh browser for each page: one WebKit process stopped answering after about sixty contexts (a harness limit, not a page fault)
            browser.close()
            browser = kind.launch(**launch)
            arrived = guarded(arrival, browser, base, dist, name, hash_, receipt_ids(dist))
            for scheme, options, viewports in VARIANTS:
                for w, h in viewports:
                    ctx = browser.new_context(viewport={"width": w, "height": h}, **options)
                    page = ctx.new_page()
                    page.add_init_script(VIOLATIONS)
                    problems = []
                    page.on(
                        "console",
                        lambda m, problems=problems: (
                            problems.append(("console", m.text)) if m.type == "error" and not harness_noise(m.text, engine) else None
                        ),
                    )
                    page.on("pageerror", lambda e, problems=problems: problems.append(("pageerror", str(e))))
                    page.on("requestfailed", lambda r, problems=problems: problems.append(("requestfailed", r.url)))
                    page.goto(base + hash_)
                    page.wait_for_selector("h1")
                    page.wait_for_function(LOADED, timeout=10000)
                    if scheme == "reduced":
                        for moving in page.evaluate(MOTION):
                            problems.append(("motion with reduced motion asked for", moving))
                    for blocked in page.evaluate("() => window.__csp"):
                        problems.append(("blocked by the content security policy", blocked))
                    internal = page.evaluate(RAW_IDS)
                    words = page.evaluate(WORDS)  # counted before axe, the screenshot and the keyboard walk, which can scroll the page
                    for message in name_problems(page.locator("body").aria_snapshot()):
                        problems.append(("accessible name", message))
                    inject_axe(page)
                    axe = page.evaluate(
                        "async () => { const r = await axe.run(document, {resultTypes: ['violations', 'incomplete']}); return {violations: r.violations.map(v => ({id: v.id, impact: v.impact, nodes: v.nodes.length})), incomplete: r.incomplete.map(v => ({id: v.id, nodes: v.nodes.length}))}; }"
                    )
                    key = f"{name}-{w}x{h}-{scheme}"
                    page.screenshot(path=os.path.join(out, key + ".png"), full_page=False)
                    walk = keyboard_walk(page, engine)
                    report[key] = {
                        "title": page.title(),
                        "first_viewport_words": words,
                        "internal_ids": internal,
                        "words_over_budget": words > WORD_BUDGET.get((name, w, h), words),
                        "overflow_x": page.evaluate(OVERFLOW),
                        "focus_after_load": page.evaluate(FOCUS),
                        "small_targets": page.evaluate(SMALL),
                        "axe": axe,
                        "tab_order": [f"{x['tag']}:{x['text']}" for x in walk],
                        "focus_without_ring": [f"{x['tag']}:{x['text']}" for x in walk if not x["ring"]],
                        "focus_offscreen": [f"{x['tag']}:{x['text']}" for x in walk if not x["onscreen"]],
                        "focus_covered": [f"{x['tag']}:{x['text']}" for x in walk if x["covered"]],
                        "nav": navs.setdefault(key, page.evaluate(NAV)),
                        "skip_link": guarded(skip_link, page, engine),
                        "arrival": arrived if (w, h, scheme) == (*VIEWPORTS[0], SCHEMES[0]) else [],
                        "problems": problems,
                    }
                    ctx.close()
        browser.close()
    server.shutdown()
    Path(out, "report.json").write_text(json.dumps(report, indent=1))
    bad = {
        k: v
        for k, v in report.items()
        if v["problems"]
        or v["axe"]["violations"]
        or v["overflow_x"]
        or v["focus_without_ring"]
        or v["focus_covered"]
        or v["small_targets"]
        or not v["tab_order"]
        or v["tab_order"][0] != "A:Skip to the page"
        or v["skip_link"]
        or v["arrival"]
        or v["words_over_budget"]
        or v["internal_ids"]
    }
    differing = nav_inconsistencies(navs)
    print(f"{len(report)} page states; {len(bad)} with a problem; {len(differing)} with a navigation that differs")
    for line in differing:
        print(line)
    for k, v in bad.items():
        shown = {"problems": v["problems"], "axe": v["axe"]["violations"], "overflow_x": v["overflow_x"]}
        shown["no_ring"] = v["focus_without_ring"]
        shown["internal_ids"] = v["internal_ids"]
        shown["covered"] = v["focus_covered"]
        shown["small"] = v["small_targets"][:3]
        shown["words"] = [v["first_viewport_words"], "over its budget"] if v["words_over_budget"] else v["first_viewport_words"]
        print(k, json.dumps({**shown, "first": v["tab_order"][:1], "skip_link": v["skip_link"], "arrival": v["arrival"]})[:600])


if __name__ == "__main__":
    main()
