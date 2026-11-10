"""E24: trusted-root lifecycle (an outside review point 4: distribution, rotation, expiry, rollback protection). Modelled on The Update Framework's root update (specification
section 5.3, read in full): version N+1 must be signed by a threshold of version N's root keys AND a threshold of its own, versions go up by exactly one, expiry is checked on the final
root against a fixed start time, a downloaded chain is bounded. Differences from TUF, stated: (a) the signed payload is signed as raw bytes in an envelope (no canonicalisation needed);
(b) several independent channels are consulted and two different documents for one version raise FORK; (c) the root also carries log-key ERAS with a closing size, so a retired log key
cannot vouch for a checkpoint larger than the size at which its era closed. Python implementation of the client update; verify_chain.mjs is the second implementation. Not product code."""
import base64, hashlib, json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization

MAX_CHAIN = 16


def b64(b): return base64.b64encode(b).decode()
def unb64(s): return base64.b64decode(s)


class Key:
    def __init__(self):
        self.sk = Ed25519PrivateKey.generate()
        self.pub = self.sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.keyid = hashlib.sha256(self.pub).hexdigest()[:16]
    def sign(self, payload: bytes): return {"keyid": self.keyid, "sig_b64": b64(self.sk.sign(payload))}


def make_root(version, expires, root_keys, root_threshold, eras, extra=None):
    doc = {"type": "hoshitori-trusted-root", "version": version, "expires": expires,
           "root_keys": {k.keyid: b64(k.pub) for k in root_keys}, "root_threshold": root_threshold,
           "log_eras": [{"keyid": e["key"].keyid, "pub_b64": b64(e["key"].pub), "from_size": e["from"], "through_size": e["through"]} for e in eras]}
    if extra: doc.update(extra)
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()


def envelope(payload: bytes, signers):
    return {"payload_b64": b64(payload), "signatures": [s.sign(payload) if isinstance(s, Key) else s for s in signers]}


def _verify(pub_b64, sig_b64, payload):
    try:
        Ed25519PublicKey.from_public_bytes(unb64(pub_b64)).verify(unb64(sig_b64), payload); return True
    except Exception:
        return False


def count_valid(root_doc, sigs, payload):
    seen = set()
    for s in sigs:
        kid = s.get("keyid")
        if kid in seen or kid not in root_doc["root_keys"]: continue          # each key contributes one signature
        if _verify(root_doc["root_keys"][kid], s.get("sig_b64", ""), payload): seen.add(kid)
    return len(seen)


def parse(payload: bytes):
    d = json.loads(payload)
    if d.get("type") != "hoshitori-trusted-root" or not isinstance(d.get("version"), int) or not isinstance(d.get("expires"), int): raise ValueError("shape")
    return d


def client_update(trusted_env, channels, now, max_chain=MAX_CHAIN):
    """trusted_env: the root shipped with the verifier release (trusted out of band). channels: list of {version: envelope}. now: the fixed update start time (seconds).
    Returns {status, version, code, single_channel, root}. Codes: OK, FORK, MALFORMED, OLD_THRESHOLD, NEW_THRESHOLD, VERSION_STEP, CHAIN_BOUND, EXPIRED."""
    cur = parse(unb64(trusted_env["payload_b64"])); steps = 0; single = False
    def out(status, code): return {"status": status, "version": cur["version"], "code": code, "single_channel": single, "root": cur}
    while True:
        n = cur["version"] + 1
        cands = [ch[n] for ch in channels if n in ch]
        if not cands: break
        if steps >= max_chain: return out("REJECTED", "CHAIN_BOUND")
        if len({c["payload_b64"] for c in cands}) > 1: return out("FORK", "FORK")
        env = cands[0]; single = len(cands) == 1 and len(channels) > 1
        try:
            payload = unb64(env["payload_b64"]); new = parse(payload)
        except Exception:
            return out("REJECTED", "MALFORMED")
        if count_valid(cur, env["signatures"], payload) < cur["root_threshold"]: return out("REJECTED", "OLD_THRESHOLD")
        if count_valid(new, env["signatures"], payload) < new["root_threshold"]: return out("REJECTED", "NEW_THRESHOLD")
        if new["version"] != n: return out("REJECTED", "VERSION_STEP")
        cur = new; steps += 1
    if cur["expires"] <= now: return out("EXPIRED", "EXPIRED")
    return out("CURRENT", "OK")


def era_accepts(root, keyid, size):
    for e in root["log_eras"]:
        if e["keyid"] == keyid:
            if size < e["from_size"]: return "REJECT_BEFORE_ERA"
            if e["through_size"] is not None and size > e["through_size"]: return "REJECT_BEYOND_CLOSING_SIZE"
            return "ACCEPT"
    return "UNKNOWN_ERA"
