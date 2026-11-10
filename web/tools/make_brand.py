#!/usr/bin/env python3
"""Write the brand tokens (tokens.css, tokens.json) from the one stylesheet, with the contrast of every text pair measured.

The stylesheet (src/styles.css) is the only place a colour is defined; the files written here are copies that cannot drift, because
tests/test_brand.py regenerates them and fails on any difference. Usage: python3 tools/make_brand.py [--check]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
STYLES = WEB / "src" / "styles.css"
OUT = WEB / "public"

# (foreground, background, what it is used for, minimum ratio): 4.5 for text, 3 for large text and the lines and marks a person must see
PAIRS = (
    ("stone", "paper", "body text", 4.5),
    ("ink", "paper", "headlines", 4.5),
    ("graphite", "paper", "secondary text", 4.5),
    ("graphite", "veil", "secondary text on a card", 4.5),
    ("accent", "paper", "verified and settled marks", 4.5),
    ("accent", "accent-soft", "a verified pill", 4.5),
    ("redline", "paper", "risk and failure marks", 4.5),
    ("redline", "redline-soft", "a failure pill", 4.5),
    ("paper", "ink", "text on the ink button", 4.5),
    ("focus", "paper", "the focus ring", 3.0),
)

ROLES = {
    "paper": "the page",
    "halo": "a warm surface",
    "veil": "cards and fields",
    "line": "1 px borders",
    "fog": "decoration only, never text",
    "graphite": "secondary text",
    "stone": "body text",
    "ink": "headlines, the primary button, the mark",
    "accent": "independently verified, and settled outcomes; nothing else",
    "accent-soft": "the ground of a verified pill",
    "redline": "risk and failure",
    "redline-soft": "the ground of a failure pill",
    "focus": "the keyboard focus ring",
}


def block(css: str, opener: str) -> dict[str, str]:
    start = css.index(opener) + len(opener)
    end = css.index("}", start)
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6});", css[start:end]))


def luminance(hex_: str) -> float:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def build() -> dict[str, str]:
    css = STYLES.read_text(encoding="utf-8")
    light = block(css, ":root {")
    dark = block(css, ':root[data-theme="dark"] {')
    for name in ROLES:
        if name not in light or name not in dark:
            raise SystemExit(f"colour {name} is missing from the stylesheet")
    tokens = {
        "name": "Hoshitori",
        "colours": {n: {"role": ROLES[n], "light": light[n], "dark": dark[n]} for n in ROLES},
        "contrast": [
            {
                "foreground": f,
                "background": b,
                "use": use,
                "minimum": low,
                "light": round(contrast(light[f], light[b]), 2),
                "dark": round(contrast(dark[f], dark[b]), 2),
            }
            for f, b, use, low in PAIRS
        ],
        "type": {"sans": "system-ui stack, weights 400 and 800", "mono": "ui-monospace stack, for ids, hashes, levels and kickers"},
        "radii": {"small": "4px", "medium": "6px", "pill": "999px"},
    }
    lines = ["/* Hoshitori tokens. Generated from src/styles.css by tools/make_brand.py; do not edit. */", ":root {"]
    lines += [f"  --{n}: {light[n]};" for n in ROLES]
    lines += ["}", ':root[data-theme="dark"] {']
    lines += [f"  --{n}: {dark[n]};" for n in ROLES]
    lines += ["}", ""]
    return {"tokens.css": "\n".join(lines), "tokens.json": json.dumps(tokens, indent=2, ensure_ascii=False) + "\n"}


def main() -> int:
    files = build()
    if "--check" in sys.argv:
        stale = [n for n, text in files.items() if not (OUT / n).is_file() or (OUT / n).read_text(encoding="utf-8") != text]
        print("brand tokens: " + ("stale: " + ", ".join(stale) if stale else "current"))
        return 1 if stale else 0
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
