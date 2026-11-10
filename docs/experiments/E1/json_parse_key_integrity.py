#!/usr/bin/env python3
"""E1 finding: does JSON.parse in a given Node binary keep every object key exactly as spelled?

Generates N random JSON documents (random keys that include control characters, non-BMP characters,
backslashes and quotes), computes the expected list of all keys with Python's json module, then runs one
Node process that parses the documents in sequence with JSON.parse and compares the keys it got.

Usage: json_parse_key_integrity.py NODE_BINARY [SEED ...]   (default seeds: 20261026 101 102 103)
Result per seed: the ids of documents whose keys JSON.parse changed. The fuzz generator is run_e1.fuzz.
Run each Node binary with an empty environment; the script does so.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_e1  # noqa: E402

JS = r"""
import { readFileSync } from "node:fs";
const [dir] = process.argv.slice(2);
const items = readFileSync(dir + "/docs.jsonl", "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
const exp = JSON.parse(readFileSync(dir + "/expected_keys.json", "utf8"));
const deep = (o, acc = []) => { if (o && typeof o === "object") { if (Array.isArray(o)) o.forEach((v) => deep(v, acc)); else for (const k of Object.keys(o)) { acc.push(k); deep(o[k], acc); } } return acc; };
const same = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
const wrong = [];
for (const it of items) {
  const text = Buffer.from(it.hex, "hex").toString("utf8");
  if (!same(deep(JSON.parse(text)).sort(), [...exp[it.id]].sort())) wrong.push(it.id);
}
console.log(JSON.stringify({ node: process.version, v8: process.versions.v8, docs: items.length, json_parse_wrong_keys: wrong }));
"""


def deep_keys(o, acc):
    if isinstance(o, dict):
        for k, v in o.items():
            acc.append(k)
            deep_keys(v, acc)
    elif isinstance(o, list):
        for v in o:
            deep_keys(v, acc)
    return acc


def main():
    node = sys.argv[1]
    seeds = [int(s) for s in sys.argv[2:]] or [20261026, 101, 102, 103]
    for seed in seeds:
        docs = run_e1.fuzz(5000, seed)["jcs"]
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "docs.jsonl").write_text("\n".join(json.dumps(x) for x in docs) + "\n")
            (Path(d) / "expected_keys.json").write_text(json.dumps({x["id"]: deep_keys(json.loads(bytes.fromhex(x["hex"]).decode()), []) for x in docs}))
            (Path(d) / "check.mjs").write_text(JS)
            r = subprocess.run([node, str(Path(d) / "check.mjs"), d], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
            print(seed, r.stdout.strip() or r.stderr.strip()[:300])


if __name__ == "__main__":
    main()
