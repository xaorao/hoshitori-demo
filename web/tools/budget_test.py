#!/usr/bin/python3
"""The delivery budgets of the interface (UX_SPEC section 7), measured on the built site.

Two budgets, both in bytes as a server sends them (gzip at the usual level 6, one kilobyte = 1,000 bytes):
  * at most 100 KB of JavaScript before interaction on Today: every script the page document loads, and every module it preloads;
  * at most 200 KB for the whole initial route: the document, its stylesheets and that JavaScript.
A page that is opened later (the Desk, the claim reader, the practice trades and the others loaded when their link is followed) and the
verifier that loads when "Check it here" is pressed are not part of either: they are loaded on interaction.
Usage: /usr/bin/python3 tools/budget_test.py DIST_DIR
"""

import gzip
import re
import sys
from pathlib import Path

SCRIPT_BUDGET = 100_000
ROUTE_BUDGET = 200_000


def gz(path):
    return len(gzip.compress(Path(path).read_bytes(), compresslevel=6, mtime=0))


def initial_files(dist):
    """The files the page document names: its scripts, preloaded modules and stylesheets, as paths under ``dist``."""
    html = Path(dist, "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r'<script[^>]*\bsrc="([^"]+)"', html) + re.findall(r'<link[^>]*rel="modulepreload"[^>]*href="([^"]+)"', html)
    styles = re.findall(r'<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"', html)
    return [Path(dist, s) for s in sorted(set(scripts))], [Path(dist, s) for s in sorted(set(styles))]


def measure(dist):
    scripts, styles = initial_files(dist)
    if not scripts:
        raise SystemExit("the page document loads no script: the build is not what this test measures")
    js = sum(gz(p) for p in scripts)
    route = gz(Path(dist, "index.html")) + sum(gz(p) for p in styles) + js
    return {"scripts": [p.name for p in scripts], "javascript": js, "route": route}


def main():
    got = measure(sys.argv[1])
    print(
        f"initial JavaScript {got['javascript']:,} bytes of {SCRIPT_BUDGET:,}; whole initial route {got['route']:,} bytes of {ROUTE_BUDGET:,}; {got['scripts']}"
    )
    bad = []
    if got["javascript"] > SCRIPT_BUDGET:
        bad.append(f"the JavaScript before interaction is {got['javascript']:,} bytes, over {SCRIPT_BUDGET:,}")
    if got["route"] > ROUTE_BUDGET:
        bad.append(f"the initial route is {got['route']:,} bytes, over {ROUTE_BUDGET:,}")
    for line in bad:
        print("FAILED:", line)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
