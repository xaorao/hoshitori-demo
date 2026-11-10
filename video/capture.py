#!/usr/bin/env python3
"""Capture the Real screens of the film from the built site served over the packed record.

Usage: capture.py [--no-build]
Builds web/ (npm ci, npm run build), copies public/record into dist/data, serves dist on
127.0.0.1 only, and records every PNG/webm of the asset table of VIDEO.md section D into
public/real/. Fails (exit 1) on any HTTP 4xx/5xx, console error, page error or failed
request. Nothing is retouched: every image is a Playwright screenshot or a crop of one.
Writes public/real/capture_manifest.json (merged into manifest.json by record_terminals.sh
or by `--manifest`).
"""
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import functools
import http.server
import socketserver
from pathlib import Path

from playwright.sync_api import sync_playwright

VIDEO = Path(__file__).resolve().parent
REPO = VIDEO.parent
WEB = REPO / "web"
REC = VIDEO / "public" / "record"
REAL = VIDEO / "public" / "real"
PACK = REPO / "docs" / "record" / "record.tar.gz"
CHROMIUM = os.environ.get("CHROMIUM") or os.path.expanduser(
    "~/Library/Caches/ms-playwright/chromium_headless_shell-1248/chrome-headless-shell-mac-arm64/chrome-headless-shell")

DESK = {"width": 1920, "height": 1080}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build():
    subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=WEB, check=True, stdout=subprocess.DEVNULL)
    shutil.rmtree(WEB / "dist" / "data", ignore_errors=True)
    subprocess.run(["npm", "run", "build"], cwd=WEB, check=True, stdout=subprocess.DEVNULL)
    shutil.copytree(REC, WEB / "dist" / "data", symlinks=False)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve():
    handler = functools.partial(Quiet, directory=str(WEB / "dist"))
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)  # loopback only
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


class Run:
    def __init__(self, pw, base):
        self.pw, self.base = pw, base
        self.problems = []
        self.items = []
        self.hero = json.load(open(REAL / "hero.json"))["forecast_id"]
        self.browser = pw.chromium.launch(executable_path=CHROMIUM)

    def context(self, **kw):
        ctx = self.browser.new_context(**kw)
        ctx.on("page", self.watch)
        return ctx

    def watch(self, page):
        page.on("console", lambda m: self.problems.append(f"console {m.type}: {m.text}") if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: self.problems.append(f"pageerror: {e}"))
        page.on("requestfailed", lambda r: self.problems.append(f"requestfailed: {r.url} {r.failure}"))
        page.on("response", lambda r: self.problems.append(f"http {r.status}: {r.url}") if r.status >= 400 else None)

    def open(self, ctx, route):
        page = ctx.new_page()
        page.goto(f"{self.base}/#{route}")
        page.wait_for_selector("main h1", timeout=20000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1200)
        if page.locator("main h1").first.inner_text().strip().lower().startswith(("not found", "no such")):
            self.problems.append(f"route looks missing: {route}")
        return page

    def note(self, name, kind, route, viewport, fallback=None, extra=None):
        p = REAL / name
        d = {"file": name, "kind": kind, "source": f"{self.base_label}#{route}" if route else extra,
             "viewport": viewport, "sha256": sha256(p), "bytes": p.stat().st_size,
             "record_id": sha256(PACK), "captured_at": now(), "fallback": fallback}
        self.items.append(d)

    base_label = "http://127.0.0.1:<port>/ (web/dist + public/record) "


def box(page, sel):
    """Bounding box in page coordinates (not viewport)."""
    return page.evaluate(
        """s => { const e = document.querySelector(s); const r = e.getBoundingClientRect();
        return {x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height}; }""", sel)


def mark(page):
    """Tag the main element, and every h2 with an index, so ranges can be cut between them."""
    page.evaluate("""() => { document.querySelector('main').setAttribute('data-cap','main');
      [...document.querySelectorAll('main h2')].forEach((h,i)=>h.setAttribute('data-cap-h2', i)); }""")


def h2_sel(page, text, nth=0):
    idx = page.evaluate(
        "t => [...document.querySelectorAll('main h2')].findIndex(h => h.textContent.trim().startsWith(t))", text)
    if idx < 0:
        raise SystemExit(f"FAIL: no h2 starting with {text!r}")
    return f'main h2[data-cap-h2="{idx}"]'


def clip_between(page, path, top_sel, bottom_sel=None, height=None, pad=24, max_h=None):
    """Screenshot from the top of top_sel to the top of bottom_sel (or a height), across the width of main."""
    m = box(page, "main")
    t = box(page, top_sel)
    y0 = t["y"] - (pad if top_sel != "main" else 0)
    if bottom_sel is not None:
        b = box(page, bottom_sel)
        y1 = b["y"] - 8
    else:
        y1 = y0 + height
    if max_h is not None:
        y1 = min(y1, y0 + max_h)
    clip = {"x": max(m["x"] - pad, 0), "y": max(y0, 0), "width": m["w"] + 2 * pad, "height": y1 - y0}
    page.screenshot(path=str(path), clip=clip, full_page=True)
    return clip


def main():
    if "--no-build" not in sys.argv:
        build()
    elif not (WEB / "dist" / "data").exists():
        shutil.copytree(REC, WEB / "dist" / "data")
    REAL.mkdir(parents=True, exist_ok=True)
    srv, port = serve()
    base = f"http://127.0.0.1:{port}"
    with sync_playwright() as pw:
        r = Run(pw, base)
        r.base_label = base + "/"
        hero = r.hero
        D = f"{DESK['width']}x{DESK['height']}@2x"

        # desktop context
        ctx = r.context(viewport=DESK, device_scale_factor=2, color_scheme="light", reduced_motion="reduce")

        page = r.open(ctx, f"/receipts/{hero}")
        mark(page)
        specs = [
            ("receipt_hero_head.png", "main", h2_sel(page, "What would prove it wrong"), 60),
            ("receipt_hero_prove.png", h2_sel(page, "What would prove it wrong"), h2_sel(page, "When it was fixed"), 600),
            ("receipt_hero_read.png", h2_sel(page, "What it read"), h2_sel(page, "What the model did"), 900),
            ("receipt_hero_model.png", h2_sel(page, "What the model did"), h2_sel(page, "What happened"), 600),
        ]
        for name, top, bottom, mx in specs:
            clip_between(page, REAL / name, top, bottom, max_h=mx * 3 if name != "receipt_hero_head.png" else None)
            r.note(name, "screen", f"/receipts/{hero}", D)
        page.close()

        page = r.open(ctx, "/")
        mark(page)
        page.screenshot(path=str(REAL / "today_top.png"), clip={"x": 0, "y": 0, **DESK})
        r.note("today_top.png", "screen", "/", D)
        for name, sel in (("today_changes.png", "section[aria-labelledby=top-changes]"),
                          ("today_disagree.png", "section[aria-labelledby=disagree-h]")):
            b = box(page, sel)
            m = box(page, "main")
            page.screenshot(path=str(REAL / name), full_page=True, clip={
                "x": max(m["x"] - 24, 0), "y": b["y"] - 16, "width": m["w"] + 48, "height": min(b["h"] + 32, 1400)})
            r.note(name, "screen", "/", D)
        page.close()

        # phone, light and dark by the site's own prefers-color-scheme rule
        for scheme in ("light", "dark"):
            pc = r.context(viewport={"width": 390, "height": 844}, device_scale_factor=3, color_scheme=scheme,
                           is_mobile=True, has_touch=True, reduced_motion="reduce")
            pp = r.open(pc, "/")
            theme = pp.evaluate("getComputedStyle(document.body).backgroundColor")
            pp.screenshot(path=str(REAL / f"today_phone_{scheme}.png"))
            r.note(f"today_phone_{scheme}.png", "screen", "/", f"390x844@3x ({scheme}; body bg {theme})")
            pc.close()

        # keyboard recording
        vdir = REAL / "_video"
        shutil.rmtree(vdir, ignore_errors=True)
        kc = r.context(viewport=DESK, reduced_motion="reduce", record_video_dir=str(vdir), record_video_size=DESK,
                       color_scheme="light")
        kp = r.open(kc, "/")
        kp.mouse.move(1900, 1070)
        focus = []
        for key in ("Tab", "Tab", "Enter"):
            kp.wait_for_timeout(900)
            kp.keyboard.press(key)
            kp.wait_for_timeout(900)
            focus.append(kp.evaluate("document.activeElement ? (document.activeElement.textContent||'').trim().slice(0,40) + ' | ' + location.hash : ''"))
        kp.wait_for_timeout(1200)
        vid = kp.video
        kc.close()
        shutil.move(vid.path(), REAL / "today_keyboard.webm")
        shutil.rmtree(vdir, ignore_errors=True)
        r.note("today_keyboard.webm", "recording", "/", "1920x1080 reduced motion", extra=None)
        r.items[-1]["after_each_key"] = focus

        # verify page and forged copy
        page = r.open(ctx, "/verify")
        mark(page)
        clip_between(page, REAL / "verify_page.png", "main", None, height=1000)
        r.note("verify_page.png", "screen", "/verify", D)
        btn = page.get_by_role("button", name="Check the forged copy")
        btn.scroll_into_view_if_needed()
        btn.click()
        page.wait_for_selector("text=Checked against", timeout=60000)
        page.wait_for_timeout(1500)
        fidx = h2_sel(page, "A forged copy")
        nxt = h2_sel(page, "Which trusted root")
        clip = clip_between(page, REAL / "verify_forgery.png", fidx, nxt, max_h=4000)
        r.note("verify_forgery.png", "screen", "/verify (click: Check the forged copy)", D)
        r.items[-1]["clip_px"] = clip
        page.close()

        page = r.open(ctx, "/record")
        mark(page)
        clip_between(page, REAL / "record_page.png", "main", h2_sel(page, "By how old"), max_h=2400)
        r.note("record_page.png", "screen", "/record", D)
        mi = h2_sel(page, "Cycles that did not run")
        clip_between(page, REAL / "record_missed.png", mi, h2_sel(page, "Other ways to read"))
        r.note("record_missed.png", "screen", "/record", D)
        page.close()

        for route, name in (("/drill", "drill_page.png"), ("/judges", "judges_page.png")):
            page = r.open(ctx, route)
            page.screenshot(path=str(REAL / name), clip={"x": 0, "y": 0, **DESK})
            r.note(name, "screen", route, D)
            page.close()

        # 404 route must be detected: the app shows a missing page, not an HTTP error
        ctx.close()
        r.browser.close()

    srv.shutdown()
    (REAL / "capture_manifest.json").write_text(json.dumps(r.items, indent=2))
    if r.problems:
        print("FAIL: problems during capture:")
        for p in sorted(set(r.problems)):
            print(" ", p[:200])
        return 1
    print("captured", len(r.items), "files; no console errors, no failed requests")
    return 0


LOG_SOURCES = {
    "verify_python": "python -m hoshitori.cli <first cycle bundle> --trusted-root trusted_root.json --now <utc>",
    "verify_node": "node verifier-js/bin/verify_bundle.mjs <first cycle bundle> --trusted-root trusted_root.json --now <utc>",
    "diff": "diff of the two reports (jq -S del(.verifier))",
    "verify_forgery": "python -m hoshitori.cli forged/<cycle> --trusted-root trusted_root.json (honest root)",
    "skill_address": "address-flag-check update-list; check on 0x...dEaD ethereum and 1BitcoinEater... bitcoin (headline lines)",
    "skill_envelope": "address-flag-check check, full JSON envelope, same two addresses",
    "skill_indicator": "indicator-condition-check check --symbol SOL --condition rsi<30 (README example)",
    "resettle": "python3 scripts/resettle_published.py video/public/record",
}


def manifest():
    """Merge the capture items, hero.json, logs and exit codes into public/real/manifest.json."""
    items = json.load(open(REAL / "capture_manifest.json"))
    rid, ts = sha256(PACK), now()
    for name, cmd in LOG_SOURCES.items():
        for ext in ("log", "exit"):
            p = REAL / f"{name}.{ext}"
            d = {"file": p.name, "kind": "terminal" if ext == "log" else "exit code", "source": cmd,
                 "viewport": None, "sha256": sha256(p), "bytes": p.stat().st_size, "record_id": rid,
                 "captured_at": datetime.datetime.fromtimestamp(p.stat().st_mtime, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "fallback": None}
            if ext == "exit":
                d["exit_code"] = int(p.read_text().strip())
            items.append(d)
    h = REAL / "hero.json"
    items.append({"file": "hero.json", "kind": "selection", "source": "video/select_hero.py (VIDEO.md section 0 rule)",
                  "viewport": None, "sha256": sha256(h), "bytes": h.stat().st_size, "record_id": rid,
                  "captured_at": ts, "fallback": None})
    for it in items:
        if it["file"] == "record_missed.png":
            it["fallback"] = ("none needed: the page's own line is captured; this pack has 0 missed cycles, so it reads "
                              "'Every one of the 7 cycles ... left a record'")
        if it["file"] == "verify_python.log" and False:
            pass
    json.dump({"record_id": rid, "record_id_note": "sha256 of docs/record/record.tar.gz (no git in this copy)",
               "generated_at": ts, "files": sorted(items, key=lambda i: i["file"])},
              open(REAL / "manifest.json", "w"), indent=2)
    print("manifest:", len(items), "entries")


if __name__ == "__main__":
    if "--manifest" in sys.argv:
        manifest()
        sys.exit(0)
    sys.exit(main())
