// Experiment E1, implementation B: the same profiles in JavaScript, written independently from the RFC text.
// It has its own strict JSON parser because JSON.parse cannot report duplicate keys, lone-surrogate escapes
// or the exact spelling of a number. Usage: node canon.mjs PROFILE < vectors.jsonl
import { createInterface } from "node:readline";

const MAX_DEPTH = 64;
const SAFE = 9007199254740991;
class Reject extends Error {}

function decodeUtf8(bytes) {
  if (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) throw new Reject("byte-order mark");
  try { return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes); }
  catch { throw new Reject("invalid UTF-8"); }
}

function hasLoneSurrogate(s) {
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) { const d = s.charCodeAt(i + 1); if (d >= 0xdc00 && d <= 0xdfff) { i++; continue; } return true; }
    if (c >= 0xdc00 && c <= 0xdfff) return true;
  }
  return false;
}

function parse(text, profile) {
  let i = 0;
  const ws = () => { while (i < text.length && " \t\n\r".includes(text[i])) i++; };
  const fail = (m) => { throw new Reject(m); };
  function value(depth) {
    if (depth > MAX_DEPTH) fail("nesting too deep");
    ws();
    const c = text[i];
    if (c === "{") return object(depth);
    if (c === "[") return array(depth);
    if (c === '"') return string();
    if (c === "-" || (c >= "0" && c <= "9")) return number();
    for (const [lit, v] of [["true", true], ["false", false], ["null", null]]) if (text.startsWith(lit, i)) { i += lit.length; return v; }
    return fail("invalid JSON");
  }
  function object(depth) {
    i++; const m = new Map(); ws();
    if (text[i] === "}") { i++; return m; }
    for (;;) {
      ws(); if (text[i] !== '"') fail("invalid JSON");
      const k = string(); ws(); if (text[i] !== ":") fail("invalid JSON"); i++;
      if (m.has(k)) fail("duplicate key");
      m.set(k, value(depth + 1)); ws();
      if (text[i] === ",") { i++; continue; }
      if (text[i] === "}") { i++; return m; }
      fail("invalid JSON");
    }
  }
  function array(depth) {
    i++; const a = []; ws();
    if (text[i] === "]") { i++; return a; }
    for (;;) {
      a.push(value(depth + 1)); ws();
      if (text[i] === ",") { i++; continue; }
      if (text[i] === "]") { i++; return a; }
      fail("invalid JSON");
    }
  }
  function string() {
    i++; let out = "";
    for (;;) {
      if (i >= text.length) fail("invalid JSON");
      const c = text[i];
      if (c === '"') { i++; break; }
      if (c < " ") fail("control character in string");
      if (c === "\\") {
        i++; const e = text[i++];
        const simple = { '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t" };
        if (e in simple) out += simple[e];
        else if (e === "u") {
          const h = text.slice(i, i + 4);
          if (!/^[0-9a-fA-F]{4}$/.test(h)) fail("invalid JSON");
          out += String.fromCharCode(parseInt(h, 16)); i += 4;
        } else fail("invalid JSON");
      } else { out += c; i++; }
    }
    if (hasLoneSurrogate(out)) fail("lone surrogate");
    if (profile === "record" && out.normalize("NFC") !== out) fail("string not NFC");
    return out;
  }
  function number() {
    const m = /^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?/.exec(text.slice(i));
    if (!m) fail("invalid JSON");
    const tok = m[0]; i += tok.length;
    if (profile === "record") {
      if (m[2] !== undefined || m[3] !== undefined) fail("fractional or exponent number in record profile");
      if (tok === "-0") fail("integer spelling not allowed");
      const n = Number(tok);
      if (Math.abs(n) > SAFE) fail("number not allowed in record profile");
      return n;
    }
    const n = Number(tok);
    if (!Number.isFinite(n)) fail("non-finite number");
    return n;
  }
  const v = value(0); ws();
  if (i !== text.length) fail("trailing content");
  if (profile === "record" && !(v instanceof Map)) fail("record must be a JSON object");
  return v;
}

const ESC = { 8: "\\b", 9: "\\t", 10: "\\n", 12: "\\f", 13: "\\r", 34: '\\"', 92: "\\\\" };
function str(s) {
  let o = '"';
  for (const ch of s) {
    const c = ch.codePointAt(0);
    if (c in ESC) o += ESC[c];
    else if (c < 0x20) o += "\\u" + c.toString(16).padStart(4, "0");
    else o += ch;
  }
  return o + '"';
}
function ser(v) {
  if (v === null) return "null";
  if (v === true) return "true";
  if (v === false) return "false";
  if (typeof v === "string") return str(v);
  if (typeof v === "number") return Object.is(v, -0) ? "0" : String(v); // ECMAScript Number::toString
  if (Array.isArray(v)) return "[" + v.map(ser).join(",") + "]";
  const keys = [...v.keys()].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0)); // UTF-16 code unit order
  return "{" + keys.map((k) => str(k) + ":" + ser(v.get(k))).join(",") + "}";
}

export function canonicalizeBytes(bytes, profile) {
  return Buffer.from(ser(parse(decodeUtf8(bytes), profile)), "utf-8");
}
export { Reject };

if (process.argv[1] && process.argv[1].endsWith("canon.mjs")) {
  const profile = process.argv[2];
  const rl = createInterface({ input: process.stdin });
  for await (const line of rl) {
    if (!line.trim()) continue;
    const item = JSON.parse(line);
    try {
      const out = canonicalizeBytes(Buffer.from(item.hex, "hex"), profile).toString("hex");
      console.log(JSON.stringify({ id: item.id, ok: true, hex: out }));
    } catch (e) {
      if (!(e instanceof Reject)) { console.log(JSON.stringify({ id: item.id, ok: false, error: "INTERNAL " + e.message })); continue; }
      console.log(JSON.stringify({ id: item.id, ok: false, error: e.message }));
    }
  }
}
