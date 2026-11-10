#!/usr/bin/python3
"""The Verify page end to end, in a real browser: a click fetches a bundle's files and checks it in the page.

Cases: an honest bundle checked against the published root (every finding passes at level C3); the same bundle with one record changed
(it must fail and say the bundle is not intact); the same bundle with a trusted-root file of the reader's own that is not the root of the
league (it must not pass); the forged copy of the bundle, when the site holds one (the records pass and the time-stamps fail, so it cannot
count toward a score); the honest bundle with its files gone (the check cannot run: one alert, not inside another live region, so it is
read once and at once, WCAG 4.1.3). The honest check is started from the keyboard, and the button keeps the keyboard while the check runs
and after it (WCAG 2.4.3). The check that could not run is also seen at 390 and 320 CSS pixels wide: its message names a long path, and the
page must not become wider than the window (WCAG 1.4.10 Reflow; ACCESSIBILITY_SPEC: no horizontal scrolling at 320 CSS px).
Usage: /usr/bin/python3 tools/verify_flow_test.py DIST_DIR [--engine chromium|firefox|webkit] [--shots DIR]
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import page_test as pt

# where the keyboard is, and whether the control there is announced as unavailable
ON_BUTTON = """() => { const a = document.activeElement;
 return {tag: a ? a.tagName : null, text: a ? (a.textContent || '').trim().slice(0, 60) : null,
  unavailable: !!a && (a.disabled === true || a.getAttribute('aria-disabled') === 'true')}; }"""
# every alert on the page, and whether it sits inside another live region (then it may be read twice, or politely instead of at once)
ALERTS = """() => [...document.querySelectorAll('[role=alert]')].map(a => ({text: a.textContent.slice(0, 120),
 nested: !!(a.parentElement && a.parentElement.closest('[aria-live],[role=status],[role=log],[role=alert]'))}))"""


def holder(held):
    """A route handler that keeps the first request in ``held``, to be answered later, and lets every other one through. It takes one
    parameter: Playwright hands a handler that takes two the request as its second."""

    def hold(route):
        if held:
            route.continue_()
        else:
            held.append(route)

    return hold


def run(dist, engine, shots=None):
    work = tempfile.mkdtemp(prefix="verify-flow-")
    tampered = os.path.join(work, "tampered")
    shutil.copytree(dist, tampered)
    bundles = json.loads(Path(dist, "data", "bundles.json").read_text())
    first = bundles[0]
    record = next(f for f in first["files"] if f.startswith("items/") and f.endswith(".canonical.json"))
    path = os.path.join(tampered, "data", first["path"], record)
    data = Path(path).read_bytes()
    Path(path).write_bytes(data.replace(b"BTC", b"BTD", 1) if b"BTC" in data else data[:-2] + bytes([data[-2] ^ 1]) + data[-1:])
    # the digests of the site's own index cover documents, not bundle files: a bundle is checked by the verifier, not by the index
    results = {}
    bad_root = os.path.join(work, "other_root.json")
    root = json.loads(Path(dist, "data", "trusted_root.json").read_text())
    root["version"] = 7 if "version" in root else root.get("version")
    Path(bad_root).write_text(json.dumps(root))
    with sync_playwright() as p:
        kind = getattr(p, engine)
        launch = {}
        shell = os.path.expanduser(
            "~/Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell"
        )
        if engine == "chromium" and os.path.exists(shell):
            launch["executable_path"] = shell
        browser = kind.launch(**launch)
        cases = (
            ("honest", dist, None, (1280, 900)),
            ("from Today", dist, None, (1280, 900)),
            ("tampered", tampered, None, (1280, 900)),
            ("other root", dist, bad_root, (1280, 900)),
            ("missing file", dist, None, (1280, 900)),
            ("missing file at 390", dist, None, (390, 844)),
            ("missing file at 320", dist, None, (320, 640)),
        )
        if os.path.exists(os.path.join(dist, "data", "forgery.json")):
            cases = (*cases[:3], ("forged copy", dist, None, (1280, 900)), ("forged copy at 390", dist, None, (390, 844)), *cases[3:])
        for name, root_dir, own, (width, height) in cases:
            server = pt.serve(root_dir)
            ctx = browser.new_context(viewport={"width": width, "height": height})
            page = ctx.new_page()
            problems = []
            page.on("pageerror", lambda e, problems=problems: problems.append(str(e)))
            page.on("console", lambda m, problems=problems: problems.append(m.text) if m.type == "error" else None)
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/index.html#/" + ("" if name == "from Today" else "verify"))
            page.wait_for_selector("text=Check it here")
            if own:
                page.set_input_files("input[type=file]", own)
            focus = {}
            if name == "honest":
                # WCAG 2.4.3: the button is pressed from the keyboard and keeps the keyboard while the check runs and after it; the first
                # file of the bundle is held back a moment, so the page is seen while the check is running
                held = []  # answered below, once the page has been seen while the check runs
                page.route("**/data/bundles/**", holder(held))
                button = page.locator("button", has_text="Check it here").first
                button.focus()
                page.keyboard.press("Enter")
                for _ in range(100):
                    if held:
                        break
                    page.wait_for_timeout(50)
                page.wait_for_timeout(200)
                focus["during"] = page.evaluate(ON_BUTTON)
                if held:
                    held[0].continue_()
            elif name.startswith("missing file"):
                page.route("**/data/bundles/**", lambda route: route.fulfill(status=404, body="gone"))  # the check cannot run
                page.click("text=Check it here >> nth=0")
            elif name.startswith("forged copy"):
                page.click("text=Check the forged copy")
            else:
                page.click("text=Check it here >> nth=0")
            # the result of the check, in the page (the notice that the documents are old is in the frame, not in main)
            page.wait_for_selector("main ul.findings, main .notice.error", timeout=60000)
            if name == "honest":
                focus["after"] = page.evaluate(ON_BUTTON)
            texts = page.eval_on_selector_all("ul.findings li", "els => els.map(e => e.textContent)")
            failed = page.eval_on_selector_all("main .notice.error", "els => els.map(e => e.textContent)")
            alerts = page.evaluate(ALERTS)
            axe = []
            if name == "honest":
                pt.inject_axe(page)
                axe = page.evaluate(
                    "async () => (await axe.run(document, {resultTypes: ['violations']})).violations.map(v => v.id + ':' + v.nodes.length)"
                )
                if shots:
                    page.screenshot(path=os.path.join(shots, f"verify-checked-{engine}.png"), full_page=True)
                    ctx2 = browser.new_context(viewport={"width": 390, "height": 844})
                    ctx2.close()
            if name.startswith("forged copy") and shots:
                page.screenshot(path=os.path.join(shots, f"verify-forged-{width}-{engine}.png"), full_page=True)
            if name.startswith("missing file") and shots:
                page.screenshot(path=os.path.join(shots, f"verify-could-not-run-{width}-{engine}.png"), full_page=True)
            wide = page.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
            results[name] = {"findings": texts, "failed": failed, "problems": problems, "axe": axe, "focus": focus, "alerts": alerts}
            results[name]["overflow"] = page.evaluate(pt.OVERFLOW)
            results[name]["width"] = wide
            ctx.close()
            server.shutdown()
        browser.close()
    shutil.rmtree(work, ignore_errors=True)
    return results


def main():
    dist = sys.argv[1]
    engine = sys.argv[sys.argv.index("--engine") + 1] if "--engine" in sys.argv else "chromium"
    shots = sys.argv[sys.argv.index("--shots") + 1] if "--shots" in sys.argv else None
    if shots:
        os.makedirs(shots, exist_ok=True)
    r = run(dist, engine, shots)
    ok = True
    honest = r["honest"]
    want = [
        "Passed: Every record is intact",
        "Passed: Checked against a trusted root",
        "Passed: Independently time-stamped",
        "Passed: The whole batch its cycle committed is here",
        "Passed: It meets every condition a scored period asks for. A call counts only in a scored period; an exploratory run never does.",
    ]
    # A bundle exported after the log signed a later checkpoint holds the later steps of its calls, and says so in one more finding. Whether
    # a bundle does depends on when it was exported, and the check on Today reads the newest bundle while the Verify page reads another, so
    # the finding is allowed in either and not required in either; the other five are required, in order, in both.
    def core(result: dict) -> list[str]:
        return [x for x in result["findings"] if not x.startswith("Passed: The later steps of")]

    if len(core(honest)) != len(want) or not all(t.startswith(w) for t, w in zip(core(honest), want)) or honest["problems"]:
        ok = False
        print("HONEST BUNDLE DID NOT PASS", json.dumps(honest)[:600])
    if honest["axe"]:
        ok = False
        print("ACCESSIBILITY VIOLATIONS AFTER A CHECK", honest["axe"])
    today = r["from Today"]
    if (
        len(core(today)) != len(want)
        or not all(t.startswith(w) for t, w in zip(core(today), want))
        or today["problems"]
        or today["overflow"]
    ):
        ok = False
        print("THE CHECK FROM TODAY DID NOT PASS AS THE VERIFY PAGE DOES", json.dumps(today)[:600])
    t = r["tampered"]
    if (
        not t["findings"]
        or not t["findings"][0].startswith("Failed: The bundle is not intact")
        or any(x.startswith("Passed: It can count") for x in t["findings"])
    ):
        ok = False
        print("TAMPERED BUNDLE WAS NOT REFUSED", json.dumps(t)[:600])
    o = r["other root"]
    if any(x.startswith("Passed: Checked against a trusted root") for x in o["findings"]) and any(
        x.startswith("Passed: It can count") for x in o["findings"]
    ):
        ok = False
        print("A ROOT THAT IS NOT THE LEAGUE'S PASSED", json.dumps(o)[:600])
    for name in ("forged copy", "forged copy at 390"):
        if name not in r:
            continue
        f = r[name]
        want_forged = [
            "Passed: Every record is intact",
            "Passed: Checked against a trusted root",
            "Failed: Recorded by us only (0 of 2 time-stamps valid)",
            "Passed: The whole batch its cycle committed is here",
            "Failed: It cannot count toward a score",
        ]
        if len(f["findings"]) != 5 or not all(x.startswith(w) for x, w in zip(f["findings"], want_forged)) or f["problems"]:
            ok = False
            print(f"{name.upper()}: THE FORGED COPY WAS NOT SHOWN AS INTACT BUT NOT TIME-STAMPED", json.dumps(f)[:700])
        if f["overflow"]:
            ok = False
            print(f"{name.upper()}: THE PAGE IS WIDER THAN THE WINDOW", f["width"])
    during, after = honest["focus"].get("during", {}), honest["focus"].get("after", {})
    if during.get("tag") != "BUTTON" or not (during.get("text") or "").startswith("Checking") or not during.get("unavailable"):
        ok = False
        print("THE BUTTON LOST THE KEYBOARD WHILE THE CHECK RAN (or was not unavailable)", json.dumps(during))
    if after.get("tag") != "BUTTON" or not (after.get("text") or "").startswith("Check it here") or after.get("unavailable"):
        ok = False
        print("THE BUTTON DID NOT HAVE THE KEYBOARD AFTER THE CHECK", json.dumps(after))
    for name in ("missing file", "missing file at 390", "missing file at 320"):
        m = r[name]
        could_not = [a for a in m["alerts"] if a["text"].startswith("The check could not run:")]
        if len(could_not) != 1 or could_not[0]["nested"] or m["findings"]:
            ok = False
            print(f"{name.upper()}: A CHECK THAT COULD NOT RUN WAS NOT ONE ALERT OF ITS OWN", json.dumps(m["alerts"]))
        if m["overflow"]:
            ok = False
            print(
                f"{name.upper()}: THE PAGE IS WIDER THAN THE WINDOW (scroll width, window width)", m["width"], json.dumps(could_not)[:300]
            )
    for name, v in r.items():
        if any(a["nested"] for a in v["alerts"]):
            ok = False
            print(f"{name.upper()}: AN ALERT INSIDE ANOTHER LIVE REGION", json.dumps(v["alerts"]))
    print(engine, "ok" if ok else "FAILED", {k: (len(v["findings"]), len(v["failed"])) for k, v in r.items()})
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
