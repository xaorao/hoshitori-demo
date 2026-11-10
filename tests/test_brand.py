"""The brand kit: the token files are copies of the stylesheet, and every text pair is readable in both colour schemes."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

WEB = Path(__file__).resolve().parents[1] / "web"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_brand", WEB / "tools" / "make_brand.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_token_files_equal_the_stylesheet() -> None:
    for name, text in load().build().items():
        assert (WEB / "public" / name).read_text(encoding="utf-8") == text, f"{name} is stale: run web/tools/make_brand.py"


def test_every_pair_meets_its_minimum_in_both_schemes() -> None:
    tokens = json.loads((WEB / "public" / "tokens.json").read_text(encoding="utf-8"))
    for pair in tokens["contrast"]:
        for scheme in ("light", "dark"):
            assert pair[scheme] >= pair["minimum"], f"{pair['foreground']} on {pair['background']} ({scheme}): {pair[scheme]}"


def test_contrast_maths_is_the_wcag_formula() -> None:
    m = load()
    assert round(m.contrast("#000000", "#ffffff"), 2) == 21.0
    assert round(m.contrast("#ffffff", "#ffffff"), 2) == 1.0
    assert round(m.contrast("#777777", "#ffffff"), 2) == 4.48  # the known borderline grey


def test_the_accent_and_the_redline_are_distinct_and_defined_once() -> None:
    colours = json.loads((WEB / "public" / "tokens.json").read_text(encoding="utf-8"))["colours"]
    assert colours["accent"]["light"] != colours["redline"]["light"]
    css = (WEB / "src" / "styles.css").read_text(encoding="utf-8")
    assert css.count("--accent:") == 3  # light, dark, and the dark-by-system-preference copy


def test_the_assets_a_page_links_exist() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for name in ("favicon.svg", "apple-touch-icon.png", "social-card.png"):
        assert name in html
        assert (WEB / "public" / name).is_file()
    assert (WEB / "public" / "social-card.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
