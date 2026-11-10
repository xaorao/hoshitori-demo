#!/usr/bin/env python3
"""Experiment E1 runner: cross-language canonical-JSON vectors and differential fuzzing.

Implementations compared (each run as a separate process with an empty environment):
  ours-py    canon.py (this directory)                        profiles jcs, record
  ours-js    canon.mjs (this directory)                       profiles jcs, record
  tp-py      rfc8785 0.1.4, Trail of Bits, Apache-2.0         fed by plain json.loads (what an integrator would do)
  tp-js      canonicalize 5.1.0, Apache-2.0 (reference JS)    fed by plain JSON.parse
Third-party packages live in an untrusted, isolated directory outside the repository (see THIRD_PARTY below).
"""
from __future__ import annotations

import json
import os
import random
import struct
import subprocess
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
THIRD = Path(os.environ.get("E1_THIRD_PARTY", HERE / "third_party"))
NODE = "/opt/homebrew/bin/node" if Path("/opt/homebrew/bin/node").exists() else "node"
PY_OURS = sys.executable
PY_TP = str(THIRD / "py/.venv/bin/python")

IMPLS = {
    "ours-py": lambda p: ([PY_OURS, "-I", str(HERE / "canon.py"), p], HERE),
    "ours-js": lambda p: ([NODE, str(HERE / "canon.mjs"), p], HERE),
    "tp-py": lambda p: ([PY_TP, "-I", str(THIRD / "py/adapter.py")], THIRD / "py"),
    "tp-js": lambda p: ([NODE, str(THIRD / "node/adapter.mjs")], THIRD / "node"),
}


def run(impl: str, profile: str, items: list[dict]) -> dict:
    cmd, cwd = IMPLS[impl](profile)
    payload = "\n".join(json.dumps({"id": i["id"], "hex": i["hex"]}) for i in items) + "\n"
    r = subprocess.run(cmd, input=payload, capture_output=True, text=True, cwd=cwd, env={"PATH": "/usr/bin:/bin"}, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"{impl} failed: {r.stderr[:500]}")
    return {o["id"]: o for o in (json.loads(l) for l in r.stdout.splitlines() if l.strip())}


def B(s: str) -> bytes:
    return s.encode("utf-8")


def J(s: str) -> bytes:
    """JSON text with @ standing for a backslash (this source file is kept ASCII-only for escapes)."""
    return s.replace("@", chr(92)).encode("utf-8")


def C(*cps: int) -> str:
    return "".join(chr(c) for c in cps)


# ---- hand-built vectors -------------------------------------------------------------------------------------
VEC: list[dict] = []


def V(id, raw: bytes, jcs, record="same", note=""):
    """jcs / record: expected canonical bytes, or None for 'must reject'. record='same' means identical to jcs."""
    VEC.append({"id": id, "hex": raw.hex(), "jcs": jcs, "record": jcs if record == "same" else record, "note": note})


# RFC 8785 section 3.2.2 / 3.2.4 sample (expected text from the RFC)
V("rfc-3.2.2", J('{"numbers":[333333333.33333329,1E30,4.50,2e-3,0.000000000000000000000000001],"string":"@u20ac$@u000F@u000aA\'@u0042@u0022@u005c@@@"@/","literals":[null,true,false]}'),
  B('{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],"string":"' + C(0x20ac) + '$' + chr(92) + 'u000f' + chr(92) + 'nA\'B' + chr(92) + '"' + chr(92)*4 + chr(92) + '"/"}'), None, "RFC sample; record profile has no floats")
# RFC section 3.2.3 sorting sample: expected order Carriage Return, One, Control, Latin, Euro, Emoji, Hebrew
sort_in = J('{"@u20ac":"Euro Sign","@r":"Carriage Return","@ufb33":"Hebrew Letter Dalet With Dagesh","1":"One","@ud83d@ude00":"Emoji: Grinning Face","@u0080":"Control","@u00f6":"Latin Small Letter O With Diaeresis"}')
sort_out = '{"' + chr(92) + 'r":"Carriage Return","1":"One","' + C(0x80) + '":"Control","' + C(0xf6) + '":"Latin Small Letter O With Diaeresis","' + C(0x20ac) + '":"Euro Sign","' + C(0x1F600) + '":"Emoji: Grinning Face","' + C(0xfb33) + '":"Hebrew Letter Dalet With Dagesh"}'
V("rfc-3.2.3-sort", sort_in, B(sort_out), None, "UTF-16 code unit order: U+20AC sorts before U+1F600 (surrogate D83D) before U+FB33; U+FB33 is not NFC-stable, so the record profile rejects it")
# RFC Appendix B number samples (IEEE hex -> expected JSON spelling); input spelled as 17 significant digits
APPX_B = [("0000000000000000", "0"), ("8000000000000000", "0"), ("0000000000000001", "5e-324"), ("8000000000000001", "-5e-324"),
          ("7fefffffffffffff", "1.7976931348623157e+308"), ("ffefffffffffffff", "-1.7976931348623157e+308"), ("4340000000000000", "9007199254740992"),
          ("c340000000000000", "-9007199254740992"), ("4430000000000000", "295147905179352830000"), ("44b52d02c7e14af5", "9.999999999999997e+22"),
          ("44b52d02c7e14af6", "1e+23"), ("44b52d02c7e14af7", "1.0000000000000001e+23"), ("444b1ae4d6e2ef4e", "999999999999999700000"),
          ("444b1ae4d6e2ef4f", "999999999999999900000"), ("444b1ae4d6e2ef50", "1e+21"), ("3eb0c6f7a0b5ed8c", "9.999999999999997e-7"),
          ("3eb0c6f7a0b5ed8d", "0.000001"), ("41b3de4355555553", "333333333.3333332"), ("41b3de4355555554", "333333333.33333325"),
          ("41b3de4355555555", "333333333.3333333"), ("41b3de4355555556", "333333333.3333334"), ("41b3de4355555557", "333333333.33333343"),
          ("becbf647612f3696", "-0.0000033333333333333333"), ("43143ff3c1cb0959", "1424953923781206.2")]
for hx, spelled in APPX_B:
    x = struct.unpack(">d", bytes.fromhex(hx))[0]
    exp = B('{"n":' + spelled + "}")
    V(f"appxB-{hx}-17sig", B('{"n":' + ("%.17e" % x) + "}"), exp, None, "number given as 17-digit exponent form")
    V(f"appxB-{hx}-repr", B('{"n":' + repr(x) + "}"), exp, None, "number given as Python repr")
# number spellings
for i, (src, out) in enumerate([("1E30", "1e+30"), ("4.50", "4.5"), ("-0", "0"), ("-0.0", "0"), ("0e0", "0"), ("1e-7", "1e-7"), ("0.0000001", "1e-7"), ("123456789012345678901", "123456789012345680000"),
                                ("1e21", "1e+21"), ("1e20", "100000000000000000000"), ("12.0", "12"), ("0.1", "0.1"), ("1.0e+2", "100"), ("5E-324", "5e-324"), ("2.2250738585072014e-308", "2.2250738585072014e-308"),
                                ("9007199254740993", "9007199254740992")]):
    V(f"num-{i}-{src}", B('{"n":' + src + "}"), B('{"n":' + out + "}"), None, "number respelling")
for i, (src, out) in enumerate([("0", "0"), ("1", "1"), ("-1", "-1"), ("9007199254740991", "9007199254740991"), ("-9007199254740991", "-9007199254740991"), ("1000000", "1000000")]):
    V(f"int-{i}-{src}", B('{"n":' + src + "}"), B('{"n":' + out + "}"), "same", "safe integer")
V("int-unsafe-2^53", B('{"n":9007199254740992}'), B('{"n":9007199254740992}'), None, "record profile limit is 2^53-1")
V("int-neg-zero-record", B('{"n":-0}'), B('{"n":0}'), None, "record profile refuses the -0 spelling")
V("int-leading-zero", B('{"n":01}'), None, None, "invalid JSON")
# strings
all_ctl = "".join(chr(c) for c in range(0x20))
V("str-all-controls", B(json.dumps({"s": all_ctl})), B('{"s":"\\u0000\\u0001\\u0002\\u0003\\u0004\\u0005\\u0006\\u0007\\b\\t\\n\\u000b\\f\\r\\u000e\\u000f\\u0010\\u0011\\u0012\\u0013\\u0014\\u0015\\u0016\\u0017\\u0018\\u0019\\u001a\\u001b\\u001c\\u001d\\u001e\\u001f"}'))
V("str-del-and-c1", J('{"s":"@u007f@u0080@u009f"}'), B('{"s":"' + C(0x7f, 0x80, 0x9f) + '"}'))
V("str-line-separators", J('{"s":"@u2028@u2029"}'), B('{"s":"' + C(0x2028, 0x2029) + '"}'))
V("str-solidus-escape", B(r'{"s":"a\/b"}'), B('{"s":"a/b"}'))
V("str-astral-escaped-pair", J('{"s":"@ud83d@ude00"}'), B('{"s":"' + C(0x1F600) + '"}'))
V("str-astral-raw", B('{"s":"' + C(0x1F600) + '"}'), B('{"s":"' + C(0x1F600) + '"}'))
V("str-quote-backslash", B(r'{"s":"\"\\"}'), B('{"s":"\\"\\\\"}'))
V("str-combining-not-nfc", B('{"s":"e' + C(0x301) + '"}'), B('{"s":"e' + C(0x301) + '"}'), None, "JCS does not normalise; the record profile rejects non-NFC")
V("str-nfc-precomposed", B('{"s":"' + C(0xe9) + '"}'), B('{"s":"' + C(0xe9) + '"}'))
V("str-empty-key", B('{"":1,"a":2}'), B('{"":1,"a":2}'))
V("str-key-prefixes", B('{"ab":1,"a":2,"aa":3,"":4}'), B('{"":4,"a":2,"aa":3,"ab":1}'))
V("str-numeric-keys", B('{"10":1,"9":2,"2":3,"1":4}'), B('{"1":4,"10":1,"2":3,"9":2}'), note="string order, not numeric")
V("str-proto-key", B('{"__proto__":1,"a":2}'), B('{"__proto__":1,"a":2}'), note="must not touch the prototype")
V("str-constructor-key", B('{"constructor":1,"toString":2}'), B('{"constructor":1,"toString":2}'))
V("str-key-escape-spelling", B('{"\\u0061":1}'), B('{"a":1}'))
# structure
V("struct-empty-obj", B("{}"), B("{}"))
V("struct-empty-top-array", B("[]"), B("[]"), None, "record must be an object")
V("struct-nested-order", B('{"b":[{"z":1,"a":2},{"y":[]}],"a":{"d":{},"c":{"f":1,"e":2}}}'), B('{"a":{"c":{"e":2,"f":1},"d":{}},"b":[{"a":2,"z":1},{"y":[]}]}'))
V("struct-array-order-kept", B('{"a":[3,1,2,"b","a"]}'), B('{"a":[3,1,2,"b","a"]}'))
V("struct-whitespace", B(' \n\t{ "a" : [ 1 , 2 ] ,\r\n "b" : null } \n'), B('{"a":[1,2],"b":null}'))
V("struct-booleans-null", B('{"t":true,"f":false,"n":null}'), B('{"f":false,"n":null,"t":true}'))
V("struct-top-scalar-string", B('"x"'), B('"x"'), None)
V("struct-top-scalar-number", B("1"), B("1"), None)
V("struct-depth-64", B("{" + '"a":{' * 63 + "}" * 64), B("{" + '"a":{' * 63 + "}" * 64), note="exactly at the limit")
V("struct-depth-70", B("{" + '"a":{' * 69 + "}" * 70), None, None, "both profiles cap nesting at 64 so a verifier cannot be exhausted")
# hostile: every one must be rejected by both profiles in our implementations
HOSTILE = {
    "dup-key": '{"a":1,"a":2}', "dup-key-escaped-spelling": '{"a":1,"\\u0061":2}', "dup-key-nested": '{"o":{"k":1,"k":1}}',
    "lone-high-surrogate": '{"s":"\\ud800"}', "lone-low-surrogate": '{"s":"\\udc00"}', "reversed-surrogate-pair": '{"s":"\\ude00\\ud83d"}',
    "lone-surrogate-in-key": '{"\\ud800":1}', "nan": '{"n":NaN}', "infinity": '{"n":Infinity}', "neg-infinity": '{"n":-Infinity}',
    "overflow-1e400": '{"n":1e400}', "trailing-comma-obj": '{"a":1,}', "trailing-comma-arr": '{"a":[1,]}', "single-quotes": "{'a':1}",
    "comment": '{"a":1}/*x*/', "line-comment": '{"a":1}//x', "trailing-garbage": '{"a":1} x', "two-documents": '{"a":1}{"b":2}', "unquoted-key": '{a:1}',
    "empty-input": "", "only-whitespace": "  ", "raw-newline-in-string": '{"s":"a\nb"}', "raw-tab-in-string": '{"s":"a\tb"}',
    "bad-escape": '{"s":"\\x41"}', "short-unicode-escape": '{"s":"\\u12"}', "plus-number": '{"n":+1}', "dot-number": '{"n":.5}', "trailing-dot": '{"n":1.}',
    "hex-number": '{"n":0x10}', "unterminated-string": '{"s":"abc', "unterminated-object": '{"a":1', "missing-colon": '{"a" 1}', "undefined": '{"a":undefined}',
}
for k, s in HOSTILE.items():
    V("hostile-" + k, B(s), None, None)
for k, raw in {"bom": b"\xef\xbb\xbf{\"a\":1}", "invalid-utf8-continuation": b'{"s":"\x80"}', "overlong-slash": b'{"s":"\xc0\xaf"}',
               "cesu8-surrogate": b'{"s":"\xed\xa0\x80"}', "truncated-multibyte": b'{"s":"\xe2\x82"}', "utf16-le-bom": b"\xff\xfe{\x00}\x00",
               "null-byte-outside-string": b'{"a":1}\x00', "latin1-e-acute": b'{"s":"\xe9"}', "code-point-above-10ffff": b'{"s":"\xf4\x90\x80\x80"}'}.items():
    V("hostile-bytes-" + k, raw, None, None)
V("hostile-deep-5000", B("[" * 5000 + "]" * 5000), None, None, "must not crash the verifier")
V("hostile-huge-exponent-digits", B('{"n":1e' + "9" * 400 + "}"), None, None)
V("hostile-long-digit-run", B('{"n":' + "9" * 5000 + "}"), None, None, "overflows to infinity as a double")
V("hostile-record-float", B('{"price":1.5}'), B('{"price":1.5}'), None)


# ---- differential fuzzing -----------------------------------------------------------------------------------
def rand_string(rng, allow_nonnfc=True):
    pool = ["a", "b", "z", "A", " ", "0", chr(34), chr(92), "/", C(0x7f), C(0x80), C(0xe9), C(0x20ac), C(0xfb33), C(0x1F600), C(0x10FFFF), C(0x2028), C(0xfffd), C(0x301)]
    pool += [chr(c) for c in range(0, 0x20)]
    s = "".join(rng.choice(pool) for _ in range(rng.randint(0, 8)))
    if not allow_nonnfc:
        s = unicodedata.normalize("NFC", s)
    return s


def rand_double(rng):
    r = rng.random()
    if r < 0.5:
        while True:
            x = struct.unpack(">d", rng.getrandbits(64).to_bytes(8, "big"))[0]
            if x == x and abs(x) != float("inf"):
                return x
    if r < 0.75:
        return rng.choice([10.0 ** rng.randint(-30, 30), -(10.0 ** rng.randint(-30, 30)), float(rng.randint(-10**6, 10**6)), rng.randint(1, 10**9) / 10 ** rng.randint(0, 12)])
    return rng.uniform(-1e6, 1e6)


def rand_value(rng, depth, profile):
    r = rng.random()
    if depth > 4 or r < 0.3:
        c = rng.random()
        if c < 0.25:
            return rand_string(rng, profile == "jcs")
        if c < 0.5:
            return rng.randint(-(2**53 - 1), 2**53 - 1) if profile == "record" else rand_double(rng)
        if c < 0.6:
            return rng.choice([True, False, None])
        return rng.randint(-1000, 1000)
    if r < 0.6:
        return [rand_value(rng, depth + 1, profile) for _ in range(rng.randint(0, 4))]
    return {rand_string(rng, profile == "jcs"): rand_value(rng, depth + 1, profile) for _ in range(rng.randint(0, 5))}


def spell(v, rng):
    """Render a Python value as JSON text with randomised but valid spelling (escapes, spacing, number form)."""
    if isinstance(v, float):
        return rng.choice([repr(v), "%.17e" % v, "%.17g" % v]) if v == v else "0"
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=rng.random() < 0.5)
    if isinstance(v, list):
        sep = rng.choice([",", ", ", " , "])
        return "[" + sep.join(spell(x, rng) for x in v) + "]"
    if isinstance(v, dict):
        sep = rng.choice([",", ",\n  "])
        return "{" + sep.join(spell(k, rng) + rng.choice([":", " : "]) + spell(x, rng) for k, x in v.items()) + "}"
    return json.dumps(v)


def fuzz(n, seed):
    rng = random.Random(seed)
    items = {"jcs": [], "record": []}
    for i in range(n):
        for profile in ("jcs", "record"):
            doc = {rand_string(rng, profile == "jcs"): rand_value(rng, 0, profile) for _ in range(rng.randint(1, 5))}
            items[profile].append({"id": f"fz-{profile}-{i}", "hex": spell(doc, rng).encode("utf-8").hex()})
    return items


def mutate(items, seed):
    rng = random.Random(seed)
    out = []
    for it in items:
        b = bytearray(bytes.fromhex(it["hex"]))
        if not b:
            continue
        for _ in range(rng.randint(1, 3)):
            op = rng.random()
            pos = rng.randrange(len(b))
            if op < 0.4:
                b[pos] = rng.randrange(256)
            elif op < 0.7:
                del b[pos]
            else:
                b.insert(pos, rng.choice(b'{}[]",:\\0123456789eE+-. \t\n'))
        out.append({"id": "mu-" + it["id"], "hex": bytes(b).hex()})
    return out


def main():
    n_fuzz = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    report = {"implementations": {}, "vectors": len(VEC)}
    # versions
    report["implementations"] = {"ours-py": f"canon.py (Python {sys.version.split()[0]})", "ours-js": f"canon.mjs (Node {subprocess.run([NODE,'--version'],capture_output=True,text=True).stdout.strip()})",
                                 "tp-py": "rfc8785 0.1.4 (Trail of Bits, Apache-2.0)", "tp-js": "canonicalize 5.1.0 (Apache-2.0)"}
    # 1. hand-built vectors
    res = {(impl, prof): run(impl, prof, VEC) for impl in ("ours-py", "ours-js") for prof in ("jcs", "record")}
    for impl in ("tp-py", "tp-js"):
        res[(impl, "jcs")] = run(impl, "jcs", VEC)
    fails, third_party_findings = [], []
    for v in VEC:
        for prof in ("jcs", "record"):
            exp = v[prof]
            for impl in ("ours-py", "ours-js"):
                o = res[(impl, prof)][v["id"]]
                got = bytes.fromhex(o["hex"]) if o["ok"] else None
                if got != exp:
                    fails.append({"vector": v["id"], "impl": impl, "profile": prof, "expected": None if exp is None else exp.decode("utf-8", "replace"), "got": o.get("error") if not o["ok"] else got.decode("utf-8", "replace")})
        for impl in ("tp-py", "tp-js"):
            o = res[(impl, "jcs")][v["id"]]
            got = bytes.fromhex(o["hex"]) if o["ok"] else None
            if got != v["jcs"]:
                third_party_findings.append({"vector": v["id"], "impl": impl, "expected": None if v["jcs"] is None else v["jcs"].decode("utf-8", "replace"), "got": o.get("error") if not o["ok"] else got.decode("utf-8", "replace"), "note": v["note"]})
    report["hand_built"] = {"count": len(VEC), "ours_failures": fails, "third_party_deviations": third_party_findings}
    # 2. differential fuzz on valid documents
    fz = fuzz(n_fuzz, 20261026)
    dis = {"valid_jcs": [], "valid_record": []}
    for prof in ("jcs", "record"):
        pairs = {impl: run(impl, prof, fz[prof]) for impl in (("ours-py", "ours-js", "tp-py", "tp-js") if prof == "jcs" else ("ours-py", "ours-js"))}
        for it in fz[prof]:
            outs = {impl: (pairs[impl][it["id"]]["hex"] if pairs[impl][it["id"]]["ok"] else "REJECT:" + pairs[impl][it["id"]]["error"][:40]) for impl in pairs}
            if len(set(outs.values())) != 1:
                dis["valid_" + prof].append({"ours_agree": outs["ours-py"] == outs["ours-js"], "tp_matches_ours": {k: outs[k] == outs["ours-py"] for k in outs if k.startswith("tp")}, **({"id": it["id"], "input": bytes.fromhex(it["hex"]).decode("utf-8")[:200], "outs": {k: (bytes.fromhex(v).decode("utf-8", "replace")[:120] if not v.startswith("REJECT") else v) for k, v in outs.items()}})})
    summ = {}
    for k, v in dis.items():
        summ[k] = {"any_disagreement": len(v), "ours_py_vs_ours_js": sum(1 for x in v if not x["ours_agree"]),
                   "tp_py_differs_from_ours": sum(1 for x in v if x["tp_matches_ours"].get("tp-py") is False),
                   "tp_js_differs_from_ours": sum(1 for x in v if x["tp_matches_ours"].get("tp-js") is False)}
    tp_py_reject = [x for x in dis["valid_jcs"] if "REJECT" in json.dumps(x["outs"].get("tp-py", ""))]
    summ["valid_jcs"]["tp_py_rejects_valid_document"] = len(tp_py_reject)
    report["fuzz_valid"] = {"documents_per_profile": n_fuzz, "summary": summ, "disagreements": {k: len(v) for k, v in dis.items()}, "examples": {k: v[:5] for k, v in dis.items()}}
    # 3. differential fuzz on mutated (mostly invalid) bytes: our two implementations must agree on accept versus reject and on output
    mu = mutate(fz["jcs"] + fz["record"], 77)
    mdis = []
    for prof in ("jcs", "record"):
        a, b = run("ours-py", prof, mu), run("ours-js", prof, mu)
        acc = 0
        for it in mu:
            x, y = a[it["id"]], b[it["id"]]
            acc += x["ok"]
            if x["ok"] != y["ok"] or (x["ok"] and x["hex"] != y["hex"]):
                mdis.append({"profile": prof, "hex": it["hex"][:200], "py": x.get("error") or x["hex"][:80], "js": y.get("error") or y["hex"][:80]})
        report.setdefault("fuzz_mutated", {})[prof] = {"inputs": len(mu), "accepted_by_py": acc}
    report["fuzz_mutated"]["disagreements"] = len(mdis)
    report["fuzz_mutated"]["examples"] = mdis[:8]
    out = HERE / "E1_results.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("vectors",)}), json.dumps({"ours_failures": len(fails), "third_party_deviations": len(third_party_findings)}), json.dumps(report["fuzz_valid"]["disagreements"]), json.dumps({"mutated_disagreements": len(mdis)}))


if __name__ == "__main__":
    main()
