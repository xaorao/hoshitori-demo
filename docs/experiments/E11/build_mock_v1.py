#!/usr/bin/env python3
"""Builds a static Today page from card data that follows the card contract, for measurement only (E11). Not product code.
Every string is a placeholder written for the spike and labelled as such in the page; no market data is implied."""
import html, json, sys
sys.path.insert(0, ".")
from card_lint import lint_featured, lint_condensed

FEATURED = {"headline": "SOL: an important claim is being challenged", "what_changed": "New independent evidence conflicts with an earlier claim.",
            "why_it_matters": "The agent's original reading may no longer hold.", "still_uncertain": "One underlying assertion is unchecked.",
            "next_check": "Verify the original source of the conflicting evidence before relying on the earlier reading.",
            "meta": "Practice only. Updated 4 min ago. Independently time-stamped before the outcome window.",
            "control": {"label": "See why this call is shaky", "href": "#thesis-sol"}, "state_chip": "Material update"}
UNCHECKED = {"what": "A large exchange announced a delisting notice for TOKEN-A.", "unknown": "Whether the notice applies to spot or perpetual markets is unknown.",
             "source": "Exchange announcement page", "control": "Check this notice"}
CONDENSED = [
    ("TOKEN-B: ownership claim contradicted", "A fund named in a post says it never held the token.", "Investigated thesis", "Contradicted", "Practice only. Updated 6 min ago.", "Read the correction"),
    ("TOKEN-C: unusual funding reading", "Funding is far from its own recent range on one venue.", "Investigated thesis", "Unresolved", "Practice only. Updated 9 min ago.", "See what supports this"),
    ("TOKEN-D: source went offline", "A followed source stopped updating; results may be stale.", "Material update", "Source unavailable", "Practice only. Updated 12 min ago.", "See which checks are affected"),
    ("TOKEN-E: sentiment shift week over week", "Market sentiment fell for seven days in a row.", "Investigated thesis", "Mixed", "Practice only. Updated 15 min ago.", "See what changed this week"),
    ("TOKEN-F: wallet activity claim unproven", "A post says large wallets are buying; wallet evidence is missing.", "Being checked", "Unproven", "Practice only. Updated 18 min ago.", "Check the wallet evidence"),
]
CSS = """
:root{--bg:#fff;--fg:#14171a;--muted:#4a5560;--line:#c9d1d9;--accent:#0b57d0;--chip:#eef2f7;--warn:#8a4b00;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#101418;--fg:#eef1f4;--muted:#b6c0ca;--line:#38424d;--accent:#8ab4ff;--chip:#1b232c;--warn:#ffb45e}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 system-ui,sans-serif}
header,main{max-width:1100px;margin:0 auto;padding:12px 16px}header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line)}
h1{font-size:1.1rem;margin:0}h2{font-size:1.05rem;margin:0 0 6px}
.check{display:flex;gap:8px;margin:12px 0}.check input{flex:1;min-height:44px;padding:0 10px;border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:6px}
button,a.btn{min-height:44px;min-width:44px;padding:0 14px;border:1px solid var(--accent);background:transparent;color:var(--accent);border-radius:6px;font:inherit;display:inline-flex;align-items:center;text-decoration:none;cursor:pointer}
:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.card{border:1px solid var(--line);border-radius:8px;padding:14px;margin:0 0 12px}
.slots{display:grid;grid-template-columns:repeat(2,1fr);gap:6px 18px;margin:8px 0}.slots dt{font-weight:600;font-size:.85rem;color:var(--muted)}.slots dd{margin:0 0 4px}
.chip{display:inline-block;background:var(--chip);border:1px solid var(--line);border-radius:999px;padding:1px 10px;font-size:.85rem;margin-right:6px}
.meta{color:var(--muted);font-size:.9rem;margin:6px 0 10px}.unchecked{border-left:6px solid var(--warn)}
.list{list-style:none;margin:0;padding:0}.list .card{display:grid;grid-template-columns:1fr auto;gap:4px 12px;align-items:center}
@media (max-width:600px){.slots{grid-template-columns:1fr}.list .card{grid-template-columns:1fr}}
@media (prefers-reduced-motion:no-preference){.card{transition:border-color .15s}}
.skip{position:absolute;left:-999px}.skip:focus{left:8px;top:8px;background:var(--bg);padding:12px 8px;min-height:44px;z-index:5}
"""

def e(s): return html.escape(s)

def page(include_unchecked=True):
    f = FEATURED
    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
           '<title>Today: what changed and what to check next</title><style>' + CSS + '</style></head><body>',
           '<a class="skip" href="#main">Skip to the list</a>',
           '<header><h1>Hoshitori</h1><span class="chip">Practice only</span></header><main id="main">',
           '<form class="check" role="search" aria-label="Check a claim" onsubmit="return false"><input aria-label="Paste a claim to check" placeholder="Paste a market claim" type="text"><button type="submit">Check this claim</button></form>',
           '<section aria-labelledby="feat"><article class="card"><h2 id="feat">' + e(f["headline"]) + '</h2>',
           '<span class="chip">' + e(f["state_chip"]) + '</span>',
           '<dl class="slots"><div><dt>What changed</dt><dd>' + e(f["what_changed"]) + '</dd></div><div><dt>Why it matters</dt><dd>' + e(f["why_it_matters"]) + '</dd></div>'
           '<div><dt>What is still uncertain</dt><dd>' + e(f["still_uncertain"]) + '</dd></div><div><dt>Next check</dt><dd>' + e(f["next_check"]) + '</dd></div></dl>',
           '<p class="meta">' + e(f["meta"]) + '</p><a class="btn" href="' + f["control"]["href"] + '">' + e(f["control"]["label"]) + '</a></article></section>']
    if include_unchecked:
        u = UNCHECKED
        out.append('<section aria-labelledby="unc"><article class="card unchecked"><h2 id="unc">Important, not yet checked</h2><p>' + e(u["what"]) + ' <span class="meta">' + e(u["source"]) + '.</span></p><p>' + e(u["unknown"]) + '</p><button type="button">' + e(u["control"]) + '</button></article></section>')
    out.append('<section aria-labelledby="more"><h2 id="more">Also changed</h2><ul class="list">')
    for h, why, st, un, meta, ctl in CONDENSED:
        out.append('<li class="card"><div><h3 style="margin:0;font-size:1rem">' + e(h) + '</h3><p style="margin:2px 0">' + e(why) + '</p><span class="chip">' + e(st) + '</span><span class="chip">' + e(un) + '</span><span class="meta">' + e(meta) + '</span></div><a class="btn" href="#t">' + e(ctl) + '</a></li>')
    out.append('</ul></section></main></body></html>')
    return "".join(out)

if __name__ == "__main__":
    errs, tot = lint_featured(FEATURED)
    print("featured", tot, errs)
    for h, why, st, un, meta, ctl in CONDENSED:
        c = {"headline": h, "why_it_matters": why, "state_chip": st, "uncertainty_chip": un, "meta": meta, "control": {"label": ctl}}
        print(h, lint_condensed(c))
    open("today_mock.html", "w").write(page())
