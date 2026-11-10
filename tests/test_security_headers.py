"""The headers the static pages are served with (``deploy/security_headers.json``): a policy that cannot be weakened without a test failing.

The page test serves every page under these headers, so a page that needs something the policy forbids fails there. This test holds the
policy itself to the rules of the security model: nothing is allowed by default, no script or style runs inline or from eval, nothing leaves
the site's own origin, and the page cannot be framed, given a base address or posted to another site.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADERS = ROOT / "deploy" / "security_headers.json"
REQUIRED = {
    "default-src": ["'none'"],
    "script-src": ["'self'"],
    "style-src": ["'self'"],
    "connect-src": ["'self'"],
    "base-uri": ["'none'"],
    "form-action": ["'none'"],
    "frame-ancestors": ["'none'"],
    "object-src": ["'none'"],
}
OTHERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cross-Origin-Opener-Policy": "same-origin"}


def policy_problems(headers: dict[str, str]) -> list[str]:
    """What is wrong with a header set, in words."""
    out: list[str] = []
    csp = headers.get("Content-Security-Policy")
    if csp is None:
        return ["there is no Content-Security-Policy"]
    directives = {d.split()[0]: d.split()[1:] for d in (x.strip() for x in csp.split(";")) if d}
    for name, want in REQUIRED.items():
        if directives.get(name) != want:
            out.append(f"{name} is {directives.get(name)!r}, not {want!r}")
    for name, values in directives.items():
        for bad in ("'unsafe-inline'", "'unsafe-eval'", "*", "http:", "https:"):
            if bad in values:
                out.append(f"{name} allows {bad}")
    for name, value in OTHERS.items():
        if headers.get(name) != value:
            out.append(f"{name} is {headers.get(name)!r}, not {value!r}")
    if not re.fullmatch(r"max-age=\d{7,}", headers.get("Strict-Transport-Security", "")):
        out.append("Strict-Transport-Security has no long max-age")
    return out


def test_the_policy_catches_a_weakened_header_set() -> None:
    good = json.loads(HEADERS.read_text(encoding="utf-8"))
    assert policy_problems(good) == []
    assert policy_problems({}) == ["there is no Content-Security-Policy"]
    weak = {
        **good,
        "Content-Security-Policy": good["Content-Security-Policy"].replace("script-src 'self'", "script-src 'self' 'unsafe-inline'"),
    }
    assert any("script-src allows 'unsafe-inline'" in p for p in policy_problems(weak))
    open_connect = {**good, "Content-Security-Policy": good["Content-Security-Policy"].replace("connect-src 'self'", "connect-src *")}
    assert any("connect-src" in p for p in policy_problems(open_connect))
    assert any("Referrer-Policy" in p for p in policy_problems({k: v for k, v in good.items() if k != "Referrer-Policy"}))
    assert any("Strict-Transport-Security" in p for p in policy_problems({**good, "Strict-Transport-Security": "max-age=0"}))


def test_the_shipped_header_set_meets_the_rules() -> None:
    assert policy_problems(json.loads(HEADERS.read_text(encoding="utf-8"))) == []


def test_the_page_document_has_no_inline_script_or_style() -> None:
    page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", page, re.S)
    assert scripts, "the page loads no script at all"
    for attributes, body in scripts:
        assert "src=" in attributes and body.strip() == "", f"an inline script: {attributes!r} {body[:40]!r}"
    assert "<style" not in page and " style=" not in page, "an inline style"
