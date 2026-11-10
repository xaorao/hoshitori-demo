// Canonical record form (RFC 8785 restricted to the I-JSON record profile of LEDGER_SPEC section 2).
// This is the second, independently written implementation; src/hoshitori/canonical.py is the first.
// It has its own strict parser: JSON.parse cannot report duplicate keys, lone-surrogate escapes or the spelling
// of a number, and a runtime defect once returned wrong property names (contract section 30).
// A string must be free of lone surrogates, made only of code points assigned in Unicode 15.0 (./unicode15.mjs, the same table
// as the Python implementation's) and NFC, checked in that order. The repertoire is pinned because NFC is only as stable as the
// Unicode tables behind it: Unicode's normalization stability policy keeps the NFC form of a string of characters assigned in a
// version the same in every later version, but Python on Unicode 15.0 and Node on 17.0 disagreed on U+105D2 U+0307, which only
// 16.0 and later compose (finding D-NFC). A runtime whose Unicode is older than 15.0 checks no string at all.
import { ASSIGNED } from "./unicode15.mjs";

export const MAX_DEPTH = 64;
export const MAX_SAFE = 9007199254740991;
export const RUNTIME_TOO_OLD = "Unicode 15.0 or later is needed to check NFC; this runtime has an older version";

export class CanonicalError extends Error {}

// Not a CanonicalError: the record is not at fault, and no caller may report it as a bad record.
export class UnicodeVersionError extends Error {}

const fail = (message) => {
  throw new CanonicalError(message);
};

const atLeast15 = (version) => Number(version.split(".")[0]) >= 15;

// Whether this runtime's NFC knows Unicode 15.0. Node states its Unicode version in process.versions.unicode. A browser has no
// such field, so its normalizer is asked directly about U+1E08F, a combining mark (class 230) first assigned in Unicode 15.0: NFC
// puts it after U+0323 (class 220) and composes "a" with U+0323 to U+1EA1 only if it knows the mark's class; an older normalizer
// treats the unknown mark as a starter and leaves the text as it is.
export function normalizerKnowsUnicode15(nfc = (text) => text.normalize("NFC")) {
  return nfc("a\u{1E08F}\u0323") === "\u1EA1\u{1E08F}";
}

export function unicodeAtLeast15(scope = globalThis, nfc = (text) => text.normalize("NFC")) {
  const reported = scope.process?.versions?.unicode;
  if (typeof reported === "string") return atLeast15(reported);
  return normalizerKnowsUnicode15(nfc);
}

// Decided once, when the module loads; the tests set it to see the refusal.
export const runtime = { unicodeAtLeast15: unicodeAtLeast15() };

export function requireRuntimeUnicode() {
  if (!runtime.unicodeAtLeast15) throw new UnicodeVersionError(RUNTIME_TOO_OLD);
}

const STARTS = ASSIGNED.map((range) => range[0]);

// Whether `code` lies in one of the ranges of the Unicode 15.0 table.
function assigned(code) {
  let low = 0;
  let high = STARTS.length - 1;
  let found = -1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (STARTS[middle] <= code) {
      found = middle;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  return found >= 0 && code <= ASSIGNED[found][1];
}

export function decodeUtf8(bytes) {
  if (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) fail("byte-order mark");
  try {
    return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
  } catch {
    return fail("invalid UTF-8");
  }
}

function checkString(text) {
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) {
      const d = text.charCodeAt(i + 1);
      if (d >= 0xdc00 && d <= 0xdfff) {
        i++;
        continue;
      }
      fail("lone surrogate");
    } else if (c >= 0xdc00 && c <= 0xdfff) {
      fail("lone surrogate");
    }
  }
  if (!/^[\x00-\x7f]*$/.test(text)) {
    // every ASCII code point is assigned
    for (const ch of text) {
      const code = ch.codePointAt(0);
      if (!assigned(code)) fail(`code point U+${code.toString(16).toUpperCase().padStart(4, "0")} is not assigned in Unicode 15.0`);
    }
  }
  if (text.normalize("NFC") !== text) fail("string is not NFC");
  return text;
}

const isDigit = (c) => c >= "0" && c <= "9";

// Parsed objects are Maps so that keys such as "__proto__" are ordinary keys.
export function parseRecordText(text) {
  let i = 0;
  const space = () => {
    while (i < text.length && (text[i] === " " || text[i] === "\t" || text[i] === "\n" || text[i] === "\r")) i++;
  };

  function value(depth) {
    if (i >= text.length) fail("unexpected end of input");
    const c = text[i];
    if (c === "{") return object(depth + 1);
    if (c === "[") return array(depth + 1);
    if (c === '"') return string();
    if (c === "-" || isDigit(c)) return integer();
    for (const [literal, result] of [["true", true], ["false", false], ["null", null]]) {
      if (text.startsWith(literal, i)) {
        i += literal.length;
        return result;
      }
    }
    return fail("unexpected character");
  }

  function object(depth) {
    if (depth > MAX_DEPTH) fail("nesting too deep");
    i++;
    const result = new Map();
    space();
    if (text[i] === "}") {
      i++;
      return result;
    }
    for (;;) {
      space();
      if (text[i] !== '"') fail("object key must be a string");
      const key = string();
      if (result.has(key)) fail("duplicate key");
      space();
      if (text[i] !== ":") fail("expected ':'");
      i++;
      space();
      result.set(key, value(depth));
      space();
      if (text[i] === ",") {
        i++;
        continue;
      }
      if (text[i] === "}") {
        i++;
        return result;
      }
      fail("expected ',' or '}'");
    }
  }

  function array(depth) {
    if (depth > MAX_DEPTH) fail("nesting too deep");
    i++;
    const result = [];
    space();
    if (text[i] === "]") {
      i++;
      return result;
    }
    for (;;) {
      space();
      result.push(value(depth));
      space();
      if (text[i] === ",") {
        i++;
        continue;
      }
      if (text[i] === "]") {
        i++;
        return result;
      }
      fail("expected ',' or ']'");
    }
  }

  function hex4() {
    const digits = text.slice(i, i + 4);
    if (!/^[0-9a-fA-F]{4}$/.test(digits)) fail("bad \\u escape");
    i += 4;
    return parseInt(digits, 16);
  }

  function string() {
    i++;
    const parts = [];
    for (;;) {
      if (i >= text.length) fail("unterminated string");
      const c = text[i];
      if (c === '"') {
        i++;
        return checkString(parts.join(""));
      }
      if (c < " ") fail("raw control character in string");
      if (c !== "\\") {
        parts.push(c);
        i++;
        continue;
      }
      i++;
      if (i >= text.length) fail("unterminated escape");
      const e = text[i++];
      const simple = { '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t" };
      if (Object.hasOwn(simple, e)) {
        parts.push(simple[e]);
      } else if (e === "u") {
        const unit = hex4();
        if (unit >= 0xd800 && unit <= 0xdbff) {
          if (!text.startsWith("\\u", i)) fail("lone high surrogate");
          i += 2;
          const low = hex4();
          if (low < 0xdc00 || low > 0xdfff) fail("high surrogate not followed by a low surrogate");
          parts.push(String.fromCharCode(unit, low));
        } else if (unit >= 0xdc00 && unit <= 0xdfff) {
          fail("lone low surrogate");
        } else {
          parts.push(String.fromCharCode(unit));
        }
      } else {
        fail("bad escape");
      }
    }
  }

  function integer() {
    const start = i;
    if (text[i] === "-") i++;
    if (i >= text.length || !isDigit(text[i])) fail("malformed number");
    if (text[i] === "0") {
      i++;
      if (i < text.length && isDigit(text[i])) fail("leading zero");
    } else {
      while (i < text.length && isDigit(text[i])) i++;
    }
    if (i < text.length && ".eE".includes(text[i])) fail("fractional or exponent number; quantities are decimal strings");
    const spelled = text.slice(start, i);
    if (spelled === "-0") fail("negative zero");
    if (spelled.replace("-", "").length > String(MAX_SAFE).length) fail("integer outside +/-(2**53 - 1)");
    const number = Number(spelled);
    if (Math.abs(number) > MAX_SAFE) fail("integer outside +/-(2**53 - 1)");
    return number;
  }

  space();
  const result = value(0);
  space();
  if (i !== text.length) fail("trailing content");
  return result;
}

export function parseRecord(bytes) {
  requireRuntimeUnicode();
  const result = parseRecordText(decodeUtf8(bytes));
  if (!(result instanceof Map)) fail("a record must be a JSON object");
  return result;
}

const OUTPUT_ESCAPES = { 8: "\\b", 9: "\\t", 10: "\\n", 12: "\\f", 13: "\\r", 34: '\\"', 92: "\\\\" };

function quote(text) {
  let out = '"';
  for (const ch of text) {
    const code = ch.codePointAt(0);
    if (Object.hasOwn(OUTPUT_ESCAPES, code)) out += OUTPUT_ESCAPES[code];
    else if (code < 0x20) out += "\\u" + code.toString(16).padStart(4, "0");
    else out += ch;
  }
  return out + '"';
}

function serialise(value) {
  if (value === null) return "null";
  if (value === true) return "true";
  if (value === false) return "false";
  if (typeof value === "string") return quote(value);
  if (typeof value === "number") return String(value);
  if (Array.isArray(value)) return "[" + value.map(serialise).join(",") + "]";
  // JavaScript compares strings by UTF-16 code unit, which is the order RFC 8785 section 3.2.3 requires.
  const keys = [...value.keys()].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  return "{" + keys.map((k) => quote(k) + ":" + serialise(value.get(k))).join(",") + "}";
}

/** The canonical bytes of an already parsed record (a Map, as ``parseRecord`` returns it). */
export function dumpsRecord(record) {
  return Buffer.from(serialise(record), "utf-8");
}

export function canonicalize(bytes) {
  return Buffer.from(serialise(parseRecord(bytes)), "utf-8");
}
