#!/usr/bin/env python3
"""Render the project submission form (``docs/submission/form.html``) to ``docs/submission/RYOCHAN-Project-Submission-Form.pdf``.

    python scripts/make_submission_form.py

Needs Playwright with a Chromium browser (``pip install playwright && python -m playwright install chromium``). Set ``CHROMIUM`` to use a
browser binary that is already installed instead.
"""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "submission" / "form.html"
OUT = ROOT / "docs" / "submission" / "RYOCHAN-Project-Submission-Form.pdf"


def main() -> int:
    with sync_playwright() as p:
        executable = os.environ.get("CHROMIUM")
        browser = p.chromium.launch(**({"executable_path": executable} if executable else {}))
        page = browser.new_page()
        page.goto(SOURCE.as_uri())
        page.pdf(path=str(OUT), format="A4", print_background=True, prefer_css_page_size=True)
        browser.close()
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
