#!/usr/bin/env python3
"""E11: measure the Today mock built from the card contract: visible words in the first viewport at two sizes, axe-core 4.10.2 violations
(light, dark, forced colours), tab order and focus visibility, target size, 400 percent zoom reflow, reduced motion. Screenshots to shots/.
Run with a Python that has playwright and the chromium headless shell installed (-I mode; paths are explicit)."""
import json, os, sys
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.expanduser("~/Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell")
AXE = open(os.environ["E11_AXE_JS"]).read()  # the axe-core 4.10.2 file named in the results
URL = "file://" + HERE + "/today_mock.html"
WORDS = """()=>{const vh=innerHeight,vw=innerWidth;let words=0;const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let n;
while(n=w.nextNode()){const t=n.textContent.trim();if(!t)continue;const p=n.parentElement;if(['SCRIPT','STYLE'].includes(p.tagName))continue;const r=document.createRange();r.selectNodeContents(n);const b=r.getBoundingClientRect();
if(b.width>0&&b.height>0&&b.top<vh&&b.bottom>0&&b.left<vw){const cs=getComputedStyle(p);if(cs.visibility!=='hidden'&&cs.display!=='none'&&cs.position!=='absolute'||p.closest('a.skip')==null&&cs.display!=='none')words+=t.split(/\\s+/).filter(x=>/[A-Za-z0-9]/.test(x)).length}}return words}"""
FULL = """()=>{const vh=innerHeight,vw=innerWidth;let words=0;const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let n;while(n=w.nextNode()){const t=n.textContent.trim();if(!t)continue;const p=n.parentElement;if(['SCRIPT','STYLE'].includes(p.tagName))continue;const r=document.createRange();r.selectNodeContents(n);const b=r.getBoundingClientRect();if(b.width>0&&b.height>0&&b.top>=0&&b.bottom<=vh&&b.left>=0&&b.right<=vw)words+=t.split(/\\s+/).filter(x=>/[A-Za-z0-9]/.test(x)).length}return words}"""
TOTAL = """()=>{let words=0;const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let n;while(n=w.nextNode()){const p=n.parentElement;if(['SCRIPT','STYLE'].includes(p.tagName))continue;words+=n.textContent.split(/\\s+/).filter(x=>/[A-Za-z0-9]/.test(x)).length}return words}"""
TARGETS = """()=>{const bad=[];for(const el of document.querySelectorAll('a,button,input,select,textarea,[tabindex]')){const r=el.getBoundingClientRect();if(r.width<24||r.height<24)bad.push({tag:el.tagName,w:r.width,h:r.height,text:(el.textContent||'').slice(0,30)})}return bad}"""
out = {}
os.makedirs(HERE + "/shots", exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=EXE)
    for w, h in [(1440, 900), (390, 844), (320, 640)]:
        for scheme in ("light", "dark"):
            ctx = b.new_context(viewport={"width": w, "height": h}, color_scheme=scheme)
            pg = ctx.new_page(); errs = []
            pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None); pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(URL)
            key = f"{w}x{h}-{scheme}"
            rec = {"first_viewport_words": pg.evaluate(WORDS), "first_viewport_words_fully_visible": pg.evaluate(FULL), "page_words": pg.evaluate(TOTAL), "console_errors": errs}
            pg.add_script_tag(content=AXE)
            res = pg.evaluate("async()=>{const r=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa','best-practice']}});return {violations:r.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.length})),incomplete:r.incomplete.map(v=>({id:v.id,nodes:v.nodes.length})),passes:r.passes.length}}")
            rec["axe"] = res
            rec["small_targets"] = pg.evaluate(TARGETS)
            rec["horizontal_overflow"] = pg.evaluate("()=>document.documentElement.scrollWidth>document.documentElement.clientWidth")
            pg.screenshot(path=f"{HERE}/shots/{key}.png")
            if (w, h, scheme) == (1440, 900, "light"):
                order = []
                for _ in range(14):
                    pg.keyboard.press("Tab")
                    order.append(pg.evaluate("()=>{const a=document.activeElement;const r=a.getBoundingClientRect();const cs=getComputedStyle(a);return {el:a.tagName+':'+((a.getAttribute('aria-label')||a.textContent||'').trim().slice(0,28)),visible:r.width>0&&r.height>0&&r.top>=0&&r.bottom<=innerHeight,outline:cs.outlineStyle+' '+cs.outlineWidth}}"))
                rec["tab_order"] = order
            out[key] = rec
            ctx.close()
    # forced colours, 400 percent zoom equivalent (320 css px wide), reduced motion
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, forced_colors="active", reduced_motion="reduce")
    pg = ctx.new_page(); pg.goto(URL); pg.screenshot(path=HERE + "/shots/forced-colors.png"); ctx.close()
    b.close()
json.dump(out, open(HERE + "/E11_results.json", "w"), indent=1)
for k, v in out.items():
    print(k, "viewport words", v["first_viewport_words"], "fully", v["first_viewport_words_fully_visible"], "| page words", v["page_words"], "| axe violations", [(x["id"], x["nodes"]) for x in v["axe"]["violations"]], "| incomplete", [(x["id"], x["nodes"]) for x in v["axe"]["incomplete"]], "| small targets", len(v["small_targets"]), "| overflow", v["horizontal_overflow"], "| console", v["console_errors"])
print(json.dumps(out["1440x900-light"]["tab_order"], indent=0)[:1800])
