"""E24 runner: builds the attack and lifecycle cases, runs the Python client and the Node client on each, and compares them with the expected verdict."""
import json, os, subprocess, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import rootchain as R
from rootchain import Key, make_root, envelope

NOW = 1_800_000_000; DAY = 86400; FUT = NOW + 365 * DAY; PAST = NOW - DAY
def mk(): return Key()

def chain(n_versions=3, threshold=2, expiry=FUT):
    """roots v1..vN; each version uses its own set of 3 root keys (rotated each time); log eras close as the log key is rotated."""
    keys = [[mk() for _ in range(3)] for _ in range(n_versions)]
    logk = [mk() for _ in range(n_versions)]
    docs, envs = [], {}
    for i in range(n_versions):
        eras = [{"key": logk[j], "from": 0 if j == 0 else 1000 * j, "through": (1000 * (j + 1) - 1) if j < i else None} for j in range(i + 1)]
        payload = make_root(i + 1, expiry, keys[i], threshold, eras)
        signers = keys[i][:threshold] + (keys[i - 1][:threshold] if i else [])
        envs[i + 1] = envelope(payload, signers); docs.append(payload)
    return keys, logk, envs

def case(name, trusted, channels, expect, queries=None, now=NOW, max_chain=None):
    return {"name": name, "trusted": trusted, "channels": [{str(k): v for k, v in ch.items()} for ch in channels], "now": now, "queries": queries or [], "expect": expect, **({"max_chain": max_chain} if max_chain else {})}

cases = []
keys, logk, envs = chain(3)
# 1 honest rotation chain
cases.append(case("honest chain v1 to v3", envs[1], [{2: envs[2], 3: envs[3]}, {2: envs[2], 3: envs[3]}], {"status": "CURRENT", "version": 3, "code": "OK"}))
# 2 no update available, root fresh
cases.append(case("no newer root, fresh", envs[1], [{}, {}], {"status": "CURRENT", "version": 1, "code": "OK"}))
# 3 only new keys sign (attacker's own root)
atk = [mk() for _ in range(3)]; p = make_root(2, FUT, atk, 2, [{"key": mk(), "from": 0, "through": None}])
cases.append(case("v2 signed only by its own new keys", envs[1], [{2: envelope(p, atk[:2])}], {"status": "REJECTED", "version": 1, "code": "OLD_THRESHOLD"}))
# 4 only old keys sign
p2 = json.loads(R.unb64(envs[2]["payload_b64"])); payload2 = R.unb64(envs[2]["payload_b64"])
cases.append(case("v2 signed only by the old keys", envs[1], [{2: envelope(payload2, keys[0][:2])}], {"status": "REJECTED", "version": 1, "code": "NEW_THRESHOLD"}))
# 5 below threshold on the old side
cases.append(case("v2 with one of two required old signatures", envs[1], [{2: envelope(payload2, keys[1][:2] + keys[0][:1])}], {"status": "REJECTED", "version": 1, "code": "OLD_THRESHOLD"}))
# 6 duplicate signature from the same key counted once
cases.append(case("v2 with one old key signing twice", envs[1], [{2: {"payload_b64": envs[2]["payload_b64"], "signatures": [keys[1][0].sign(payload2), keys[1][1].sign(payload2), keys[0][0].sign(payload2), keys[0][0].sign(payload2)]}}], {"status": "REJECTED", "version": 1, "code": "OLD_THRESHOLD"}))
# 7 skip a version: file named 2 holds version 3
cases.append(case("file for v2 actually holds v3 (skip)", envs[1], [{2: envs[3]}], {"status": "REJECTED", "version": 1, "code": "OLD_THRESHOLD"}))
# 7b skip with a validly signed document: build v3 signed by v1 keys + own keys
k3 = keys[2]; pl3 = R.unb64(envs[3]["payload_b64"])
cases.append(case("v3 validly signed by v1 and v3 keys offered as v2 (skip)", envs[1], [{2: envelope(pl3, keys[0][:2] + k3[:2])}], {"status": "REJECTED", "version": 1, "code": "VERSION_STEP"}))
# 8 rollback: trusted v2, channel offers only v2 again and v1
cases.append(case("rollback: trusted v2, channels offer only v1 and v2", envs[2], [{1: envs[1], 2: envs[2]}], {"status": "CURRENT", "version": 2, "code": "OK"}))
# 9 fork: two channels give different v2
atk2 = make_root(2, FUT, keys[1], 2, [{"key": logk[0], "from": 0, "through": 999}, {"key": mk(), "from": 1000, "through": None}])
forked = envelope(atk2, keys[1][:2] + keys[0][:2])
cases.append(case("channels disagree on v2", envs[1], [{2: envs[2]}, {2: forked}], {"status": "FORK", "version": 1, "code": "FORK"}))
# 10 one channel only (the other has nothing): accepted but flagged
cases.append(case("only one of two channels serves v2", envs[1], [{2: envs[2]}, {}], {"status": "CURRENT", "version": 2, "code": "OK", "single_channel": True}))
# 11 freeze: no newer root and the trusted one has expired
ke, le, ee = chain(1, expiry=PAST)
cases.append(case("freeze: trusted root expired and nothing newer", ee[1], [{}, {}], {"status": "EXPIRED", "version": 1, "code": "EXPIRED"}))
# 12 expired intermediate root does not matter when the final one is fresh
k3x = [[mk() for _ in range(3)] for _ in range(3)]; lg = [mk() for _ in range(3)]
pa = make_root(1, PAST, k3x[0], 2, [{"key": lg[0], "from": 0, "through": None}])
pb = make_root(2, PAST, k3x[1], 2, [{"key": lg[0], "from": 0, "through": 999}, {"key": lg[1], "from": 1000, "through": None}])
pc = make_root(3, FUT, k3x[2], 2, [{"key": lg[0], "from": 0, "through": 999}, {"key": lg[1], "from": 1000, "through": 1999}, {"key": lg[2], "from": 2000, "through": None}])
ea, eb, ec = envelope(pa, k3x[0][:2]), envelope(pb, k3x[1][:2] + k3x[0][:2]), envelope(pc, k3x[2][:2] + k3x[1][:2])
cases.append(case("intermediate root expired, final fresh", ea, [{2: eb, 3: ec}], {"status": "CURRENT", "version": 3, "code": "OK"}))
# 13 final root expired after a good chain
pe = make_root(2, PAST, k3x[1], 2, [{"key": lg[0], "from": 0, "through": 999}, {"key": lg[1], "from": 1000, "through": None}])
cases.append(case("chain ends at an expired root", ea, [{2: envelope(pe, k3x[1][:2] + k3x[0][:2])}], {"status": "EXPIRED", "version": 2, "code": "EXPIRED"}))
# 14 chain longer than the bound
long_keys = [[mk() for _ in range(3)] for _ in range(20)]; lk = mk(); lenv = {}
for i in range(20):
    pl = make_root(i + 1, FUT, long_keys[i], 2, [{"key": lk, "from": 0, "through": None}])
    lenv[i + 1] = envelope(pl, long_keys[i][:2] + (long_keys[i - 1][:2] if i else []))
cases.append(case("chain of 19 updates with a bound of 16", lenv[1], [lenv], {"status": "REJECTED", "version": 17, "code": "CHAIN_BOUND"}))
# 15 malformed document
cases.append(case("v2 payload is not a root document", envs[1], [{2: {"payload_b64": R.b64(b"not json"), "signatures": []}}], {"status": "REJECTED", "version": 1, "code": "MALFORMED"}))
# 16 era queries on the final root of the honest chain
q = [[logk[0].keyid, 500], [logk[0].keyid, 999], [logk[0].keyid, 1000], [logk[0].keyid, 5000], [logk[1].keyid, 999], [logk[1].keyid, 1500], [logk[1].keyid, 2000], [logk[2].keyid, 2500], [mk().keyid, 10]]
cases.append(case("era acceptance on the honest chain", envs[1], [{2: envs[2], 3: envs[3]}], {"status": "CURRENT", "version": 3, "code": "OK",
                  "era": ["ACCEPT", "ACCEPT", "REJECT_BEYOND_CLOSING_SIZE", "REJECT_BEYOND_CLOSING_SIZE", "REJECT_BEFORE_ERA", "ACCEPT", "REJECT_BEYOND_CLOSING_SIZE", "ACCEPT", "UNKNOWN_ERA"]}, queries=q))
# 17 stale root: old key (retired era) forges a larger checkpoint; the verifier holding only v1 cannot know the era closed
cases.append(case("verifier stuck on v1 (no newer root reachable) accepts the old era beyond its real closing size", envs[1], [{}], {"status": "CURRENT", "version": 1, "code": "OK", "era": ["ACCEPT"]}, queries=[[logk[0].keyid, 5000]]))
# 18 same but the freeze is caught by expiry
ks, ls, es = chain(1, expiry=NOW + 10)
cases.append(case("stuck on a root that expires soon: freeze caught once time passes", es[1], [{}], {"status": "EXPIRED", "version": 1, "code": "EXPIRED"}, now=NOW + 20))
# 19 one old root key compromised alone
cases.append(case("one stolen old root key signs a new root alone", envs[1], [{2: envelope(R.unb64(envs[2]["payload_b64"]), keys[0][:1] + keys[1][:2])}], {"status": "REJECTED", "version": 1, "code": "OLD_THRESHOLD"}))
# 20 channel offers an older chain than the verifier already holds (whole-channel rollback)
cases.append(case("whole channel rolled back to v1 while the verifier holds v3", envs[3], [{1: envs[1], 2: envs[2]}], {"status": "CURRENT", "version": 3, "code": "OK"}))
# 21 unsupported shape: root with the wrong type
bad = json.dumps({"type": "other", "version": 2, "expires": FUT}).encode()
cases.append(case("document of the wrong type", envs[1], [{2: envelope(bad, keys[0][:2])}], {"status": "REJECTED", "version": 1, "code": "MALFORMED"}))
# 22 signature over different bytes than the payload
tamper = dict(envs[2]); tamper["payload_b64"] = R.b64(R.unb64(envs[2]["payload_b64"]).replace(b'"version":2', b'"version":2 '))
cases.append(case("payload edited after signing", envs[1], [{2: tamper}], {"status": "REJECTED", "version": 1, "code": "OLD_THRESHOLD"}))

def run_py(c):
    ch = [{int(k): v for k, v in x.items()} for x in c["channels"]]
    r = R.client_update(c["trusted"], ch, c["now"], c.get("max_chain", R.MAX_CHAIN)); root = r.pop("root")
    r["era"] = [R.era_accepts(root, k, s) for k, s in c["queries"]]; return r

def run_js(path):
    return json.loads(subprocess.run(["node", os.path.join(HERE, "verify_chain.mjs"), path], capture_output=True, text=True, check=True).stdout)

res, bad = [], 0
for c in cases:
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); json.dump(c, f); f.close()
    py, js = run_py(c), run_js(f.name); os.unlink(f.name)
    exp = c["expect"]
    py_ok = all(py.get(k) == v for k, v in exp.items()); js_ok = all(js.get(k) == v for k, v in exp.items()); agree = py == js
    res.append({"case": c["name"], "python": {k: py[k] for k in ("status", "version", "code")}, "node": {k: js[k] for k in ("status", "version", "code")}, "expected_met_python": py_ok, "expected_met_node": js_ok, "implementations_agree": agree})
    bad += (not py_ok) + (not js_ok) + (not agree)
    print(("PASS " if py_ok and js_ok and agree else "FAIL ") + c["name"], "" if py_ok and js_ok and agree else f"\n   py={py}\n   js={js}\n   exp={exp}")
json.dump({"cases": len(cases), "failures": bad, "results": res}, open(os.path.join(HERE, "E24_results.json"), "w"), indent=1)
print(f"{len(cases)} cases, failures {bad}")
