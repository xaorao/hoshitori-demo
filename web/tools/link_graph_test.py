"""The link graph of the built site on real documents (plan W3): every page leads to a receipt, and every receipt leads to its lenses.

Each page of the site is opened in Chromium on the exported documents and every link it shows to another page of the site is read off the
page as a person would follow it. From those links:

* every page reaches some receipt in at most ``HOPS`` clicks, through the pages it links to (a page with no such path is named);
* a receipt shows a link to each other agent's receipt on the same call, to each practice book that followed it, to each Desk run on its
  token that it lists, and to the page that says how to check it, exactly as its own document lists them.

Usage: /usr/bin/python3 tools/link_graph_test.py DIST_DIR [RECEIPTS_TO_CHECK]
Exit 0 when every page reaches a receipt and every checked receipt shows all its links; 1 otherwise, with the failures printed.
"""

from __future__ import annotations

import json
import os
import sys
from collections import deque
from pathlib import Path

from page_test import routes, serve
from playwright.sync_api import sync_playwright

HOPS = 2
SHELL = "~/Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell"


def kind(hash_: str) -> str:
    """The node a link points at: a receipt is one kind of page, whichever call it shows; a run or a book is its page."""
    path = hash_.split("#", 1)[-1]
    if path.startswith("/receipts/"):
        return "receipt"
    parts = [p for p in path.split("/") if p]
    return parts[0] if parts else "today"


def links_on(page, base: str, hash_: str) -> set[str]:
    page.goto(f"{base}{hash_}")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)
    found = page.eval_on_selector_all("a[href^='#/']", "els => els.map(e => e.getAttribute('href'))")
    return set(found)


def main() -> int:
    dist = sys.argv[1]
    sample = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    server = serve(dist)
    base = f"http://127.0.0.1:{server.server_address[1]}/index.html"
    bad: list[str] = []
    graph: dict[str, set[str]] = {}
    with sync_playwright() as p:
        launch = {"executable_path": os.path.expanduser(SHELL)} if os.path.exists(os.path.expanduser(SHELL)) else {}
        browser = p.chromium.launch(**launch)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        for name, hash_ in routes(dist):
            if name == "missing":
                continue
            graph[name] = {kind(h) for h in links_on(page, base, hash_)}
        # every page reaches a receipt in at most HOPS clicks
        for start in graph:
            if start == "receipt":
                continue
            seen, queue, reached = {start}, deque([(start, 0)]), None
            while queue:
                node, depth = queue.popleft()
                if node == "receipt":
                    reached = depth
                    break
                if depth == HOPS:
                    continue
                for nxt in graph.get(node, set()):
                    if nxt not in seen:
                        seen.add(nxt)
                        queue.append((nxt, depth + 1))
            if reached is None:
                bad.append(f"{start}: no receipt within {HOPS} clicks (links to {sorted(graph[start])})")
            else:
                print(f"{start}: a receipt in {reached} click{'s' if reached != 1 else ''}")
        # every receipt shows its own links
        data = Path(dist, "data")
        receipts = sorted(data.glob("receipts/*.json"))
        rich = [r for r in receipts if json.loads(r.read_text())["links"]["others"] or json.loads(r.read_text())["links"]["followed"]]
        checked = rich[:sample] + receipts[:2]
        for path in checked:
            doc = json.loads(path.read_text())
            shown = links_on(page, base, f"#/receipts/{path.stem}")
            want = {f"#/receipts/{o['forecast_id']}" for o in doc["links"]["others"]}
            want |= {f"#/practice/{t['book']}" for t in doc["links"]["followed"]}
            want |= {f"#/desk/{d['run_id']}" for d in doc["links"]["desk"]}
            want.add("#/verify")
            missing = sorted(want - shown)
            if missing:
                bad.append(f"receipt {path.stem}: does not show {missing}")
        print(f"receipts checked: {len(checked)} ({len(rich[:sample])} with peers or trades)")
        browser.close()
    server.shutdown()
    for line in bad:
        print("FAIL", line)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
