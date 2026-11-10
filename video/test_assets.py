"""Checks the Real assets of the film. Run: python3 -m pytest video/test_assets.py"""
import hashlib
import json
import re
from pathlib import Path

VIDEO = Path(__file__).resolve().parent
REAL = VIDEO / "public" / "real"
REC = VIDEO / "public" / "record"
ASSETS = (
    [f"receipt_hero_{s}.png" for s in ("head", "read", "prove", "model")]
    + ["today_top.png", "today_changes.png", "today_disagree.png", "today_phone_light.png", "today_phone_dark.png",
       "today_keyboard.webm", "verify_page.png", "verify_forgery.png", "record_page.png", "record_missed.png",
       "drill_page.png", "judges_page.png"]
    + [f"{n}.log" for n in ("verify_python", "verify_node", "diff", "verify_forgery", "skill_address",
                            "skill_envelope", "skill_indicator", "resettle")]
    + ["hero.json", "manifest.json"]
)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def manifest():
    return {e["file"]: e for e in json.load(open(REAL / "manifest.json"))["files"]}


def test_every_listed_asset_exists():
    for a in ASSETS:
        assert (REAL / a).is_file(), a


def test_manifest_matches_files():
    m = manifest()
    for a in ASSETS:
        if a == "manifest.json":
            continue
        assert a in m, f"{a} missing from manifest"
        assert m[a]["sha256"] == sha(REAL / a), f"sha256 differs for {a}"
        assert m[a]["record_id"] and m[a]["captured_at"] and m[a]["source"]
    for name in re.findall(r"(\w+)\.log", " ".join(ASSETS)):
        assert f"{name}.exit" in m and (REAL / f"{name}.exit").read_text().strip().isdigit()


def test_no_tiny_png():
    for p in REAL.glob("*.png"):
        assert p.stat().st_size >= 5000, p.name


def test_hero_refers_to_real_receipt():
    h = json.load(open(REAL / "hero.json"))
    rc = json.load(open(REC / "receipts" / f"{h['forecast_id']}.json"))
    assert rc["settlement"] and rc["invalidations"] and rc["run"] and rc["evidence"]
    assert rc["row"]["forecast_id"] == h["forecast_id"]
