#!/usr/bin/python3
"""Layout shift and largest contentful paint of every route, on a desktop and on a phone profile (4x slower CPU, 150 ms and 1.6 Mbit/s).

Usage: /usr/bin/python3 tools/vitals_test.py SITE     (SITE is a built page with its documents under data/)

Fails if a route shifts more than 0.02 (the good limit is 0.1; the footer used to jump when a page filled in), or if the largest contentful
paint is over one second on the desktop profile or four seconds on the phone profile (the good limit is 2.5 seconds on a phone on a good
connection; this profile is slower than that on purpose). Measured in Chromium only: layout shift and paint timing are Chromium's.
"""

import datetime
import json
import os
import shutil
import sys
import tempfile

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import page_test as pt

ROUTES = (
    "#/",
    "#/calls",
    "#/record",
    "#/verify",
    "#/claims",
    "#/desk",
    "#/lab",
    "#/drill",
    "#/judges",
    "#/quality",
    "#/practice",
    "#/signals",
)
PROFILES = (
    ("desktop", 1440, 900, 1, None, 0, 1000),
    ("phone", 390, 844, 4, 1.6e6 / 8, 150, 4000),
)
MAX_SHIFT = 0.02
JS = """() => new Promise(res => { let cls = 0, lcp = 0;
  new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) cls += e.value; }).observe({type: 'layout-shift', buffered: true});
  new PerformanceObserver(l => { for (const e of l.getEntries()) lcp = e.startTime; }).observe({type: 'largest-contentful-paint', buffered: true});
  setTimeout(() => res({cls: cls, lcp: lcp}), 1500); })"""


def fresh_copy(site: str, scratch: str) -> str:
    """A copy of the site whose export says it was made now: a page that judges its data old shows a notice, which is not the layout measured here."""
    copy = os.path.join(scratch, "site")
    shutil.copytree(site, copy)
    path = os.path.join(copy, "data", "index.json")
    with open(path, encoding="utf-8") as handle:
        index = json.load(handle)
    index["generated_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(index, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        handle.write("\n")
    return copy


def main(site: str) -> int:
    with tempfile.TemporaryDirectory() as scratch:
        return measure(fresh_copy(site, scratch))


def measure(site: str) -> int:
    server = pt.serve(site)
    port = server.server_address[1]
    bad = []
    with sync_playwright() as p:
        shell = os.path.expanduser(
            "~/Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell"
        )
        browser = p.chromium.launch(executable_path=shell) if os.path.exists(shell) else p.chromium.launch()
        for name, width, height, cpu, down, latency, lcp_limit in PROFILES:
            for route in ROUTES:
                ctx = browser.new_context(viewport={"width": width, "height": height})
                page = ctx.new_page()
                cdp = ctx.new_cdp_session(page)
                if cpu > 1:
                    cdp.send("Emulation.setCPUThrottlingRate", {"rate": cpu})
                if down:
                    cdp.send("Network.enable")
                    cdp.send(
                        "Network.emulateNetworkConditions",
                        {"offline": False, "latency": latency, "downloadThroughput": down, "uploadThroughput": down},
                    )
                page.goto(f"http://127.0.0.1:{port}/index.html{route}")
                page.wait_for_selector("h1", timeout=60000)
                got = page.evaluate(JS)
                ctx.close()
                line = f"{name} {route}: shift {got['cls']:.3f}, paint {got['lcp']:.0f} ms"
                if got["cls"] > MAX_SHIFT or got["lcp"] > lcp_limit:
                    bad.append(line)
                print(line)
        browser.close()
    server.shutdown()
    print("vitals: " + ("FAILED\n  " + "\n  ".join(bad) if bad else "ok"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
