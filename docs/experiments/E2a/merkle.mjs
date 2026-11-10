// Experiment E2a, implementation B (JavaScript): RFC 9162 section 2.1, written separately from merkle.py.
// Generation here is iterative over a built node table (not the recursive definitions); verification follows 2.1.3.2 and 2.1.4.2.
import { createHash } from "node:crypto";
const H = (...parts) => { const h = createHash("sha256"); for (const p of parts) h.update(p); return h.digest(); };
export const leafHash = (d) => H(Buffer.from([0]), d);
export const nodeHash = (l, r) => H(Buffer.from([1]), l, r);
const ceilPow2Below = (n) => { let k = 1n; while (k * 2n < n) k *= 2n; return k; };

// Root of leaves[lo, hi) without recursion on array slices: uses the compact-range / binary-counter method.
export function root(leaves) {
  if (leaves.length === 0) return H(Buffer.alloc(0));
  // stack of [hash, height]; merge equal heights; fold right to left at the end (RFC 9162 tree shape)
  const st = [];
  for (const lh of leaves) {
    let cur = [lh, 0];
    while (st.length && st[st.length - 1][1] === cur[1]) { const [lft] = st.pop(); cur = [nodeHash(lft, cur[0]), cur[1] + 1]; }
    st.push(cur);
  }
  let acc = st.pop()[0];
  while (st.length) acc = nodeHash(st.pop()[0], acc);
  return acc;
}
const sub = (leaves, lo, hi) => root(leaves.slice(lo, hi));

export function inclusion(m, leaves) {
  let lo = 0, hi = leaves.length; const out = [];
  if (m < 0 || m >= hi) throw new Error("range");
  const stack = [];
  while (hi - lo > 1) {
    const k = Number(ceilPow2Below(BigInt(hi - lo)));
    if (m - lo < k) { stack.push(sub(leaves, lo + k, hi)); hi = lo + k; }
    else { stack.push(sub(leaves, lo, lo + k)); lo = lo + k; }
  }
  return stack.reverse();
}
export function consistency(m, leaves) {
  const n = leaves.length; if (!(m > 0 && m <= n)) throw new Error("range");
  let lo = 0, hi = n, complete = true; const stack = [];
  while (true) {
    if (m - lo === hi - lo) { if (!complete) stack.push(sub(leaves, lo, hi)); break; }
    const k = Number(ceilPow2Below(BigInt(hi - lo)));
    if (m - lo <= k) { stack.push(sub(leaves, lo + k, hi)); hi = lo + k; }
    else { stack.push(sub(leaves, lo, lo + k)); lo = lo + k; complete = false; }
  }
  return stack.reverse();
}
export function verifyInclusion(leaf, index, size, path, rootHash) {
  if (index < 0 || index >= size) return false;
  let fn = BigInt(index), sn = BigInt(size) - 1n, r = leaf;
  for (const p of path) {
    if (sn === 0n) return false;
    if ((fn & 1n) === 1n || fn === sn) {
      r = nodeHash(p, r);
      if ((fn & 1n) === 0n) while ((fn & 1n) === 0n && fn !== 0n) { fn >>= 1n; sn >>= 1n; }
    } else r = nodeHash(r, p);
    fn >>= 1n; sn >>= 1n;
  }
  return sn === 0n && r.equals(rootHash);
}
export function verifyConsistency(first, second, path, firstHash, secondHash) {
  if (!(first > 0 && first <= second)) return false;
  if (first === second) return path.length === 0 && firstHash.equals(secondHash);
  if (path.length === 0) return false;
  const p = [...path];
  if ((first & (first - 1)) === 0) p.unshift(firstHash);
  let fn = BigInt(first) - 1n, sn = BigInt(second) - 1n;
  while ((fn & 1n) === 1n) { fn >>= 1n; sn >>= 1n; }
  let fr = p[0], sr = p[0];
  for (const c of p.slice(1)) {
    if (sn === 0n) return false;
    if ((fn & 1n) === 1n || fn === sn) {
      fr = nodeHash(c, fr); sr = nodeHash(c, sr);
      if ((fn & 1n) === 0n) while ((fn & 1n) === 0n && fn !== 0n) { fn >>= 1n; sn >>= 1n; }
    } else sr = nodeHash(sr, c);
    fn >>= 1n; sn >>= 1n;
  }
  return fr.equals(firstHash) && sr.equals(secondHash) && sn === 0n;
}

// command mode: read JSON lines {op, ...} from stdin, answer JSON lines
import { createInterface } from "node:readline";
if (process.argv[2] === "serve") {
  const hex = (b) => b.toString("hex"); const unhex = (s) => Buffer.from(s, "hex");
  const rl = createInterface({ input: process.stdin });
  for await (const line of rl) {
    if (!line.trim()) continue; const q = JSON.parse(line);
    try {
      if (q.op === "root") console.log(JSON.stringify({ id: q.id, root: hex(root(q.leaves.map(unhex))) }));
      else if (q.op === "gen_inclusion") console.log(JSON.stringify({ id: q.id, path: inclusion(q.m, q.leaves.map(unhex)).map(hex) }));
      else if (q.op === "gen_consistency") console.log(JSON.stringify({ id: q.id, path: consistency(q.m, q.leaves.map(unhex)).map(hex) }));
      else if (q.op === "verify_inclusion") console.log(JSON.stringify({ id: q.id, ok: verifyInclusion(unhex(q.leaf), q.index, q.size, q.path.map(unhex), unhex(q.root)) }));
      else if (q.op === "verify_consistency") console.log(JSON.stringify({ id: q.id, ok: verifyConsistency(q.first, q.second, q.path.map(unhex), unhex(q.first_hash), unhex(q.second_hash)) }));
    } catch (e) { console.log(JSON.stringify({ id: q.id, error: String(e.message) })); }
  }
}
