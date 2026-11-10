#!/usr/bin/env python3
"""Experiment E1, implementation A: a strict RFC 8785 (JCS) canonicaliser written from the RFC text.

Two profiles:
  jcs     every JSON value the RFC allows; numbers are IEEE 754 doubles serialised as ECMAScript does.
  record  the profile Hoshitori records use (contract section 30): top-level object, strings, booleans, null,
          arrays, objects and integers within +/-(2**53 - 1); no fractional or exponent numbers (money,
          prices and probabilities are decimal strings), NFC strings only, depth <= 64.
Both profiles reject: invalid UTF-8, a byte-order mark, duplicate object keys (after unescaping), lone
surrogates, NaN and infinities, trailing content, and anything that is not valid JSON.

Usage as a library: canonicalize(raw_bytes, profile) -> bytes, raising Reject on any violation.
Command line: canon.py PROFILE < vectors.jsonl  (one {"id", "hex"} object per line) -> {"id","ok","hex"|"error"}.
"""
from __future__ import annotations

import json
import sys
import unicodedata
from decimal import Decimal

MAX_DEPTH = 64
SAFE = 2**53 - 1


class Reject(Exception):
    pass


def _no_dups(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise Reject(f"duplicate key {k!r}")
        out[k] = v
    return out


def _no_const(name):
    raise Reject(f"non-finite literal {name}")


def _has_lone_surrogate(s: str) -> bool:
    return any(0xD800 <= ord(c) <= 0xDFFF for c in s)


def _walk(v, profile, depth=0):
    if depth > MAX_DEPTH:
        raise Reject("nesting too deep")
    if isinstance(v, str):
        if _has_lone_surrogate(v):
            raise Reject("lone surrogate")
        if profile == "record" and unicodedata.normalize("NFC", v) != v:
            raise Reject("string not NFC")
    elif isinstance(v, dict):
        for k, x in v.items():
            _walk(k, profile, depth + 1)
            _walk(x, profile, depth + 1)
    elif isinstance(v, list):
        for x in v:
            _walk(x, profile, depth + 1)
    elif isinstance(v, bool) or v is None:
        pass
    elif isinstance(v, (int, float)):
        if profile == "record":
            if isinstance(v, float) or abs(v) > SAFE:
                raise Reject("number not allowed in record profile")
    else:
        raise Reject("unexpected type")


def parse(raw: bytes, profile: str):
    if raw.startswith(b"\xef\xbb\xbf"):
        raise Reject("byte-order mark")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as e:
        raise Reject(f"invalid UTF-8: {e.reason}")
    if profile == "jcs":
        kw = dict(parse_float=float, parse_int=lambda s: float(s))
    else:
        def pint(s):
            if s == "-0" or (len(s.lstrip("-")) > 1 and s.lstrip("-").startswith("0")):
                raise Reject("integer spelling not allowed")
            return int(s)
        def pfloat(s):
            raise Reject("fractional or exponent number in record profile")
        kw = dict(parse_float=pfloat, parse_int=pint)
    try:
        v = json.loads(text, object_pairs_hook=_no_dups, parse_constant=_no_const, **kw)
    except Reject:
        raise
    except RecursionError:
        raise Reject("nesting too deep")
    except (ValueError, OverflowError) as e:
        raise Reject(f"invalid JSON: {e}")
    if profile == "record" and not isinstance(v, dict):
        raise Reject("record must be a JSON object")
    _walk(v, profile)
    return v


def es_number(x: float) -> str:
    """ECMAScript Number::toString (ECMA-262 7.1.12.1), as RFC 8785 section 3.2.2.3 requires."""
    if x != x or x in (float("inf"), float("-inf")):
        raise Reject("non-finite number")
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    t = Decimal(repr(abs(x))).as_tuple()  # repr() is the shortest round-trip digit string
    s = "".join(map(str, t.digits))
    e = t.exponent
    while len(s) > 1 and s.endswith("0"):
        s, e = s[:-1], e + 1
    k, n = len(s), len(s) + e
    if k <= n <= 21:
        body = s + "0" * (n - k)
    elif 0 < n <= 21:
        body = s[:n] + "." + s[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + s
    else:
        ex = n - 1
        es = ("+" if ex >= 0 else "-") + str(abs(ex))
        body = (s + "e" + es) if k == 1 else (s[0] + "." + s[1:] + "e" + es)
    return sign + body


_ESC = {0x08: "\\b", 0x09: "\\t", 0x0A: "\\n", 0x0C: "\\f", 0x0D: "\\r", 0x22: '\\"', 0x5C: "\\\\"}


def es_string(s: str) -> str:
    out = ['"']
    for ch in s:
        c = ord(ch)
        if c in _ESC:
            out.append(_ESC[c])
        elif c < 0x20:
            out.append("\\u%04x" % c)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _ser(v) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, str):
        return es_string(v)
    if isinstance(v, (int, float)):
        return es_number(float(v))
    if isinstance(v, list):
        return "[" + ",".join(_ser(x) for x in v) + "]"
    keys = sorted(v, key=lambda k: k.encode("utf-16-be"))  # UTF-16 code unit order (RFC 8785 3.2.3)
    return "{" + ",".join(es_string(k) + ":" + _ser(v[k]) for k in keys) + "}"


def canonicalize(raw: bytes, profile: str = "record") -> bytes:
    if profile not in ("jcs", "record"):
        raise ValueError(profile)
    return _ser(parse(raw, profile)).encode("utf-8")


def main():
    profile = sys.argv[1]
    for line in sys.stdin:
        item = json.loads(line)
        try:
            out = canonicalize(bytes.fromhex(item["hex"]), profile)
            print(json.dumps({"id": item["id"], "ok": True, "hex": out.hex()}))
        except Reject as e:
            print(json.dumps({"id": item["id"], "ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
