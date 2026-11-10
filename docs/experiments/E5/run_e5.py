#!/usr/bin/env python3
"""Experiment E5 runner: the 60-second sweep and a differential fuzz of contract D arithmetic."""
import json, random, subprocess, sys
from decimal import Decimal
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import contract_d as D

MIN = D.MIN
res = {"sweep": {}, "fuzz": {}}
# 1. every millisecond-class offset within a minute, for several u values: T0 > eligible_after, minute-aligned, and at most one minute later
bad = 0; n = 0
for u in (0, 1_000, 5_000, 6_000, 59_999, 60_000, 65_000):
    for base_minute in (0, 1, 7, 1439):
        for ms in list(range(0, 60_000, 250)) + [59_999, 0, 1]:
            att = base_minute * MIN + ms
            ea = att + u
            t0 = D.t0_closed_form(ea)
            ok = t0 > ea and t0 % MIN == 0 and t0 - ea <= MIN and D.t0_scan(ea, [o * MIN for o in range(0, 3000)]) == t0
            n += 1; bad += (not ok)
res["sweep"] = {"cases": n, "violations": bad}
# the contract's worked example: tokens 10:01:02 and 10:01:04, u = 5 s plus 2 s spread -> eligible_after 10:01:11 -> T0 10:02:00
att = (10 * 3600 + 1 * 60 + 4) * 1000; ea = att + 7_000
res["worked_example"] = {"eligible_after_s": ea / 1000 - 10 * 3600 - 60, "T0_hms": [D.t0_closed_form(ea) // 3_600_000, (D.t0_closed_form(ea) // 60_000) % 60, (D.t0_closed_form(ea) // 1000) % 60]}
# boundary: attested exactly on a minute boundary with u = 0 -> the candle closing at that boundary is NOT eligible
res["boundary_exact"] = {"attested_ms": 7 * MIN, "T0": D.t0_closed_form(7 * MIN), "expected": 8 * MIN}

# 2. differential fuzz: closed form vs scan vs JavaScript, with gaps, ties, ticks and planted future candles
rng = random.Random(2026)
items, expect = [], {}
for i in range(20_000):
    tick = rng.choice(["0.01", "0.1", "1", "0.00000001", "0.0001"])
    att = rng.randrange(0, 2 * 10**9)
    u = rng.choice([5_000, 6_000, 7_000, rng.randrange(0, 90_000)])
    hz = rng.choice([3_600_000, 4 * 3_600_000, 24 * 3_600_000, 60_000, 120_000])
    ea = att + u; t0 = D.t0_closed_form(ea); th = t0 + hz
    candles = {}
    base = Decimal(rng.choice(["100", "0.5", "43000.1", "1.00000001"]))
    def px(): return str(((base + Decimal(rng.randint(-5, 5)) * Decimal(tick)) / Decimal(tick)).to_integral_value() * Decimal(tick))
    def put(o):
        if rng.random() < 0.03: return  # a gap
        candles[o] = (px(), tick)
    for o in (t0 - MIN, th - MIN):
        put(o)
    for o in range(t0 - 5 * MIN, t0 - MIN, MIN): put(o)           # earlier candles (must not matter)
    for o in (th, th + MIN, th + 7 * MIN): candles[o] = (px(), tick)  # later candles, incl. planted futures (must not matter)
    if rng.random() < 0.15 and (t0 - MIN) in candles and (th - MIN) in candles:
        candles[th - MIN] = (candles[t0 - MIN][0], tick)           # force a tie
    items.append({"id": str(i), "attested_at_ms": att, "u_total_ms": u, "horizon_ms": hz, "candles": [[o, c, t] for o, (c, t) in candles.items()]})
    s = D.settle(candles, att, u, hz)
    expect[str(i)] = [s[0], str(s[1])]
payload = "\n".join(json.dumps(x) for x in items) + "\n"
r = subprocess.run(["/opt/homebrew/bin/node", str(HERE / "contract_d.mjs")], input=payload, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
got = {json.loads(l)["id"]: json.loads(l)["out"] for l in r.stdout.splitlines()}
diff = [i for i in expect if got.get(i) != expect[i]]
states = {}
for v in expect.values(): states[v[0]] = states.get(v[0], 0) + 1
# scan implementation agrees on T0 for every case that has the T0 candle
scan_bad = 0
for it in items:
    opens = [c[0] for c in it["candles"]]
    ea = it["attested_at_ms"] + it["u_total_ms"]
    ts = D.t0_scan(ea, [o for o in opens if o + MIN > ea - 6 * MIN] or opens)
    # the scan only sees candles that exist; where the T0 candle exists it must equal the closed form
    t0 = D.t0_closed_form(ea)
    if (t0 - MIN) in opens and ts != t0: scan_bad += 1
res["fuzz"] = {"cases": len(items), "python_vs_javascript_differences": len(diff), "scan_vs_closed_form_differences": scan_bad, "state_counts": states, "node_stderr": r.stderr[:200]}
(HERE / "E5_results.json").write_text(json.dumps(res, indent=1))
print(json.dumps(res, indent=1))
