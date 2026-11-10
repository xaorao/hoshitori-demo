#!/usr/bin/env python3
"""E14: verify, from public keyless calls only, that a Bluesky post was signed by the account's key (contract section 28: SP-1 versus an author-signed
source). Steps follow the atproto repository and cryptography specifications (downloaded and read): DID document -> signing key (multikey),
com.atproto.sync.getRecord CAR -> block hashes -> signed commit -> ECDSA over SHA-256 of the DAG-CBOR unsigned commit (low-S required) ->
Merkle search tree walk to the record CID. Written from the specification; not copied from any library."""
import hashlib, json, sys, urllib.request
import cbor2
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.exceptions import InvalidSignature

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
N_K256 = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
N_P256 = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "hoshitori-spike/0 (research; read-only)"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def b58decode(s):
    n = 0
    for ch in s: n = n * 58 + B58.index(ch)
    out = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\x00" * (len(s) - len(s.lstrip("1"))) + out


def varint(buf, pos):
    shift = 0; val = 0
    while True:
        b = buf[pos]; pos += 1; val |= (b & 0x7F) << shift
        if not b & 0x80: return val, pos
        shift += 7


def parse_cid(buf, pos):
    start = pos
    ver, pos = varint(buf, pos); assert ver == 1, "only CIDv1"
    codec, pos = varint(buf, pos); mh, pos = varint(buf, pos); ln, pos = varint(buf, pos)
    digest = buf[pos:pos + ln]; pos += ln
    return {"raw": bytes(buf[start:pos]), "codec": codec, "hash": mh, "digest": digest}, pos


def read_car(data):
    hl, pos = varint(data, 0); header = cbor2.loads(data[pos:pos + hl]); pos += hl
    roots = [r.value[1:] for r in header["roots"]]
    blocks = {}
    while pos < len(data):
        l, pos = varint(data, pos); end = pos + l
        cid, p2 = parse_cid(data, pos); body = bytes(data[p2:end]); blocks[cid["raw"]] = (cid, body); pos = end
    return roots, blocks


def cid_link(raw): return cbor2.CBORTag(42, b"\x00" + raw)


def decode_multikey(mk):
    assert mk.startswith("z"); b = b58decode(mk[1:])
    if b[:2] == b"\xe7\x01": return "k256", b[2:]
    if b[:2] == b"\x80\x24": return "p256", b[2:]
    raise ValueError("unknown multikey codec")


def verify_sig(curve, pub, msg, sig, reject_high_s=True):
    if len(sig) != 64: return False, "bad signature length"
    r = int.from_bytes(sig[:32], "big"); s = int.from_bytes(sig[32:], "big")
    n = N_K256 if curve == "k256" else N_P256
    if reject_high_s and s > n // 2: return False, "high-S signature rejected (required by the specification)"
    c = ec.SECP256K1() if curve == "k256" else ec.SECP256R1()
    key = ec.EllipticCurvePublicKey.from_encoded_point(c, pub)
    try:
        key.verify(encode_dss_signature(r, s), msg, ec.ECDSA(hashes.SHA256())); return True, "ok"
    except InvalidSignature:
        return False, "signature does not verify"


def mst_find(blocks, node_cid, key):
    """Walk the MST. Node: {l: link|None, e: [{p: prefix_len, k: key_suffix, v: value_cid, t: subtree|None}]}. Keys in a node are prefix-compressed
    against the previous entry's full key."""
    def walk(cid_bytes):
        if cid_bytes not in blocks: raise KeyError("missing MST block")
        cid, body = blocks[cid_bytes]
        if hashlib.sha256(body).digest() != cid["digest"]: raise ValueError("MST block hash mismatch")
        node = cbor2.loads(body)
        prev = b""; entries = []
        for e in node["e"]:
            full = prev[:e["p"]] + e["k"]; entries.append((full, e)); prev = full
        # find position
        left = node.get("l")
        for i, (full, e) in enumerate(entries):
            if key == full: return e["v"].value[1:]
            if key < full:
                sub = (entries[i - 1][1].get("t") if i > 0 else left)
                return walk(sub.value[1:]) if sub is not None else None
        sub = entries[-1][1].get("t") if entries else left
        return walk(sub.value[1:]) if sub is not None else None
    return walk(node_cid)


def verify_record(did, collection, rkey, tamper=None, did_doc=None, use_key=None):
    out = {"did": did, "collection": collection, "rkey": rkey}
    doc = did_doc or json.loads(get(f"https://plc.directory/{did}" if did.startswith("did:plc:") else f"https://{did[8:]}/.well-known/did.json"))
    pds = [s["serviceEndpoint"] for s in doc["service"] if s["id"].endswith("#atproto_pds")][0]
    vm = [v for v in doc["verificationMethod"] if v["id"].endswith("#atproto")][0]
    curve, pub = decode_multikey(use_key or vm["publicKeyMultibase"]); out["curve"] = curve; out["pds"] = pds
    car = get(f"{pds}/xrpc/com.atproto.sync.getRecord?did={did}&collection={collection}&rkey={rkey}")
    out["car_bytes"] = len(car)
    if tamper == "flip_record_byte":
        car = bytearray(car); car[-5] ^= 1; car = bytes(car)
    roots, blocks = read_car(car); out["blocks"] = len(blocks)
    root = roots[0]
    # every block's digest must match
    for raw, (cid, body) in blocks.items():
        if hashlib.sha256(body).digest() != cid["digest"]:
            out["result"] = "REJECT: block hash mismatch"; return out
    commit = cbor2.loads(blocks[root][1])
    if commit["did"] != did: out["result"] = "REJECT: commit did differs"; return out
    sig = commit["sig"]
    unsigned = {k: v for k, v in commit.items() if k != "sig"}
    if tamper == "change_rev": unsigned["rev"] = "3zzzzzzzzzzzz"
    msg = cbor2.dumps(unsigned, canonical=True)
    ok, why = verify_sig(curve, pub, msg, sig, reject_high_s=True)
    if tamper == "malleate_high_s":
        r = int.from_bytes(sig[:32], "big"); s = int.from_bytes(sig[32:], "big"); n = N_K256 if curve == "k256" else N_P256
        ok, why = verify_sig(curve, pub, msg, r.to_bytes(32, "big") + (n - s).to_bytes(32, "big"), reject_high_s=True)
    out["signature"] = why
    if not ok: out["result"] = "REJECT: " + why; return out
    # MST walk to the record
    key = f"{collection}/{rkey}".encode()
    vcid = mst_find(blocks, commit["data"].value[1:], key)
    if vcid is None: out["result"] = "REJECT: record not in the signed tree"; return out
    rec_block = blocks.get(vcid)
    if rec_block is None: out["result"] = "REJECT: record block missing"; return out
    rec = cbor2.loads(rec_block[1])
    out["record_created_at"] = rec.get("createdAt"); out["commit_rev"] = commit["rev"]; out["record_text_prefix"] = (rec.get("text") or "")[:60]
    out["result"] = "VERIFIED"
    return out


if __name__ == "__main__":
    handles = sys.argv[1:] or ["bsky.app"]
    res = []
    for h in handles:
        did = json.loads(get(f"https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle={h}"))["did"]
        feed = json.loads(get(f"https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor={did}&limit=5&filter=posts_no_replies"))["feed"]
        for item in feed[:3]:
            uri = item["post"]["uri"]; _, _, rest = uri.partition("at://"); d, coll, rkey = rest.split("/")
            if d != did: continue
            r = verify_record(did, coll, rkey); r["handle"] = h; res.append(r); print(json.dumps(r)[:400], flush=True)
    json.dump(res, open("E14_live.json", "w"), indent=1)
