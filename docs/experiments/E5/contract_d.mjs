// Experiment E5, implementation B (JavaScript): contract D arithmetic, separately written. Integer milliseconds as BigInt.
// Reads JSON lines {id, attested_at_ms, u_total_ms, horizon_ms, candles:[[open_ms, close, tick], ...]} on stdin.
import { createInterface } from "node:readline";
const MIN = 60000n;
function dec(s) { // exact decimal as {n: BigInt, scale: Number}
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(s); if (!m) throw new Error("bad decimal " + s);
  const frac = m[3] || ""; return { n: BigInt(m[1] + m[2] + frac), scale: frac.length };
}
function cmpRounded(a, b, tick) { // compare round(a/tick) with round(b/tick) exactly, ties-to-even like Decimal's default context
  const s = Math.max(a.scale, b.scale, tick.scale);
  const up = (d) => d.n * 10n ** BigInt(s - d.scale);
  const A = up(a), B = up(b), T = up(tick);
  const r = (x) => { let q = x / T, rem = x % T; if (rem < 0n) { q -= 1n; rem += T; } const twice = rem * 2n; if (twice > T || (twice === T && (q % 2n !== 0n))) q += 1n; return q; };
  const ra = r(A), rb = r(B); return ra > rb ? 1 : ra < rb ? -1 : 0;
}
const rl = createInterface({ input: process.stdin });
for await (const line of rl) {
  if (!line.trim()) continue; const q = JSON.parse(line);
  const ea = BigInt(q.attested_at_ms) + BigInt(q.u_total_ms);
  const t0 = (ea / MIN + 1n) * MIN;                    // BigInt division floors for non-negative values
  const byOpen = new Map(q.candles.map(([o, c, t]) => [BigInt(o), [c, t]]));
  const c0 = byOpen.get(t0 - MIN);
  if (!c0) { console.log(JSON.stringify({ id: q.id, out: ["UNSETTLEABLE_MISSING_T0", String(t0)] })); continue; }
  const th = t0 + BigInt(q.horizon_ms); const ch = byOpen.get(th - MIN);
  if (!ch) { console.log(JSON.stringify({ id: q.id, out: ["UNSETTLEABLE_MISSING_H", String(t0)] })); continue; }
  const c = cmpRounded(dec(ch[0]), dec(c0[0]), dec(c0[1]));
  console.log(JSON.stringify({ id: q.id, out: [c > 0 ? "SETTLED_1" : c < 0 ? "SETTLED_0" : "VOID_TIE", String(t0)] }));
}
