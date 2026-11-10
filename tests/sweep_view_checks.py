"""Checks of the sweep view (``hoshitori.sweep_view``), runnable against a mutant.

The Evidence of quality page shows how hard the product's checks were tried: for each module that has a sweep, how many small changes of
its code were made, how many a check noticed, how many survived, and whether the module and its checks are still the files that were swept
(a sweep says nothing about code that changed after it). The view is read from the sweep page's own data, not written by hand.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import types
from pathlib import Path
from typing import Any

from hoshitori import view_models
from tests import sweep_report


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Any, says: str = "") -> None:
        try:
            call()
        except self.mod.SweepViewError as exc:
            if says not in str(exc):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected it to say {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def entry(module: str, counts: dict[str, int], module_text: str = "a = 1\n", checks_text: str = "b = 2\n", **over: Any) -> dict[str, Any]:
    name = module.split(".")[-1]
    out = {
        "checks": f"tests.{name}_checks",
        "checks_sha256": sha(checks_text),
        "counts": counts,
        "function": "failures",
        "module": module,
        "module_sha256": sha(module_text),
        "total": sum(counts.values()),
        "unresolved": [],
    }
    out.update(over)
    return out


def write_tree(root: Path, modules: dict[str, tuple[str, str]]) -> None:
    for module, (module_text, checks_text) in modules.items():
        name = module.split(".")[-1]
        (root / "src" / "hoshitori").mkdir(parents=True, exist_ok=True)
        (root / "tests").mkdir(parents=True, exist_ok=True)
        (root / "src" / "hoshitori" / f"{name}.py").write_text(module_text)
        (root / "tests" / f"{name}_checks.py").write_text(checks_text)


ENTRIES = [
    entry("hoshitori.alpha", {"FAILED": 90, "SURVIVED": 2}),
    entry("hoshitori.beta", {"FAILED": 40, "BUILD_FAILED": 3, "CHECK_RAISED": 2, "TIMED_OUT": 4, "ENVIRONMENT": 1}, unresolved=["f|x|y|0"]),
    entry("hoshitori.gamma", {"FAILED": 10}),
]


def page(entries: list[dict[str, Any]]) -> str:
    return sweep_report.render(entries)


def json_page(entries: list[dict[str, Any]]) -> str:
    return "```json\n" + json.dumps(entries) + "\n```\n"


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for cls in (mod.SweepRow, mod.SweepSummary, mod.Sweeps):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    return c.bad


def check_rows_and_summary(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    got = mod.read(page(ENTRIES))
    rows = {r.module: r for r in got.rows}
    a = rows["hoshitori.alpha"]
    c.eq("a module's numbers", (a.places, a.noticed, a.unbuilt, a.raised, a.clock, a.survived), (92, 90, 0, 0, 0, 2))
    b = rows["hoshitori.beta"]
    c.eq("a module with every kind of result", (b.places, b.noticed, b.unbuilt, b.raised, b.clock, b.survived), (50, 40, 3, 2, 5, 0))
    c.eq("and its checks' module", (a.checks, b.checks), ("tests.alpha_checks", "tests.beta_checks"))
    s = got.summary
    c.eq("the totals", (s.modules, s.places, s.noticed, s.unbuilt, s.raised, s.clock, s.survived), (3, 152, 140, 3, 2, 5, 2))
    c.eq("with no tree to read, nothing is said to be current or stale", (a.current, a.reason, s.stale, s.unchecked), (None, None, 0, 3))
    c.eq("a page with no module has an empty summary", mod.read(page([])).summary.modules, 0)
    return c.bad


def check_order(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    entries = [
        entry("hoshitori.zeta", {"FAILED": 5}),
        entry("hoshitori.alpha", {"FAILED": 5}),
        entry("hoshitori.one", {"FAILED": 5, "SURVIVED": 1}),
        entry("hoshitori.three", {"FAILED": 5, "SURVIVED": 3}),
        entry("hoshitori.clocked", {"FAILED": 5, "TIMED_OUT": 2}),
        entry("hoshitori.changed", {"FAILED": 5}, module_text="x = 1\n"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree(root, {e["module"]: ("a = 1\n", "b = 2\n") for e in entries})
        got = mod.read(page(entries), root)
    c.eq(
        "gaps first: what changed after its sweep, then the most survivors, then the clock, then by name",
        [r.module for r in got.rows],
        ["hoshitori.changed", "hoshitori.three", "hoshitori.one", "hoshitori.clocked", "hoshitori.alpha", "hoshitori.zeta"],
    )
    return c.bad


def check_currency(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    entries = [
        entry("hoshitori.same", {"FAILED": 5}),
        entry("hoshitori.moved", {"FAILED": 5}, module_text="old\n"),
        entry("hoshitori.rechecked", {"FAILED": 5}, checks_text="old\n"),
        entry("hoshitori.both", {"FAILED": 5}, module_text="old\n", checks_text="old\n"),
        entry("hoshitori.gone", {"FAILED": 5}),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree(root, {e["module"]: ("a = 1\n", "b = 2\n") for e in entries if e["module"] != "hoshitori.gone"})
        got = mod.read(page(entries), root)
    by = {r.module.split(".")[-1]: r for r in got.rows}
    c.eq("a module and checks that are the swept files are current", (by["same"].current, by["same"].reason), (True, None))
    c.eq(
        "a module that changed after its sweep says so",
        (by["moved"].current, by["moved"].reason),
        (False, "the module changed after its sweep"),
    )
    c.eq("so do its checks", (by["rechecked"].current, by["rechecked"].reason), (False, "its checks changed after the sweep"))
    c.eq("both together say both", by["both"].reason, "the module and its checks changed after the sweep")
    c.eq("a module that is not there says so", (by["gone"].current, by["gone"].reason), (False, "the module is no longer there"))
    c.eq("the summary counts them", (got.summary.stale, got.summary.unchecked), (4, 0))
    for dropped in ("module_sha256", "checks_sha256"):
        partial = [{k: v for k, v in entry("hoshitori.same", {"FAILED": 5}).items() if k != dropped}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_tree(root, {"hoshitori.same": ("a = 1\n", "b = 2\n")})
            one = mod.read(json_page(partial), root)
        c.eq(
            f"a sweep that kept only one digest ({dropped} missing) is never current",
            (one.rows[0].current, one.rows[0].reason),
            (False, "no hash was kept by this sweep"),
        )
    # the tree beside a page that was written before hashes were kept cannot show a row current
    old = [{k: v for k, v in entry("hoshitori.same", {"FAILED": 5}).items() if k not in ("module_sha256", "checks_sha256")}]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_tree(root, {"hoshitori.same": ("a = 1\n", "b = 2\n")})
        kept = mod.read(page(old), root)
    c.eq(
        "a sweep that kept no hash is never shown current",
        (kept.rows[0].current, kept.rows[0].reason),
        (False, "no hash was kept by this sweep"),
    )
    return c.bad


def check_strict(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    good = page(ENTRIES)

    def fenced(value: object) -> str:
        return "```json\n" + json.dumps(value) + "\n```\n"

    c.refuses("a page with no data", lambda: mod.read("# Mutation sweeps\n"), "the page holds no sweep data")
    c.refuses("data that is not closed", lambda: mod.read("```json\n[]"), "the sweep data is not closed")
    c.refuses("data that is not JSON", lambda: mod.read("```json\n{nope\n```\n"), "the sweep data is not JSON")
    c.refuses("data that is not a list", lambda: mod.read(fenced({"a": 1})), "the sweep data is a list")
    c.refuses("an entry that is not an object", lambda: mod.read(fenced([1])), "a sweep entry is an object")
    for key in ("module", "counts", "total", "checks", "unresolved"):
        broken = [{k: v for k, v in ENTRIES[0].items() if k != key}]
        c.refuses(f"an entry without {key}", lambda broken=broken: mod.read(fenced(broken)), f"a sweep entry has {key}")
    base = ENTRIES[0]
    c.refuses(
        "counts that are not whole numbers", lambda: mod.read(fenced([{**base, "counts": {"FAILED": 1.5}, "total": 1}])), "whole number"
    )
    c.refuses("a count that is true", lambda: mod.read(fenced([{**base, "counts": {"FAILED": True}, "total": 1}])), "whole number")
    c.refuses("a negative count", lambda: mod.read(fenced([{**base, "counts": {"FAILED": -1, "SURVIVED": 2}, "total": 1}])), "whole number")
    c.refuses("a total that is not a whole number", lambda: mod.read(fenced([{**base, "total": "92"}])), "the total is a whole number")
    c.refuses(
        "a total that the counts do not make",
        lambda: mod.read(fenced([{**base, "total": 7}])),
        "hoshitori.alpha: the results add up to 92, not the total 7",
    )
    c.refuses(
        "results the sweep does not have, however the total goes",
        lambda: mod.read(fenced([{**base, "counts": {"FAILED": 5, "ZED": 1, "ALSO": 2}, "total": 5}])),
        "hoshitori.alpha: ['ALSO', 'ZED'] are not results a sweep has",
    )
    c.refuses("counts that are a list", lambda: mod.read(fenced([{**base, "counts": [1]}])), "the counts are an object")
    c.refuses("unresolved changes that are an object", lambda: mod.read(fenced([{**base, "unresolved": {}}])), "the counts are an object")
    c.refuses("a module twice", lambda: mod.read(page([ENTRIES[0], ENTRIES[0]])), "a module is swept once on a page")
    c.refuses("a module name that is not a module path", lambda: mod.read(page([entry("../x", {"FAILED": 1})])), "by dotted path")
    c.refuses("a checks name that is not a module path", lambda: mod.read(fenced([{**base, "checks": "../x"}])), "by dotted path")
    c.refuses("a module name that is a number", lambda: mod.read(fenced([{**base, "module": 5}])), "by dotted path")
    c.eq("the good page is read", mod.read(good).summary.modules, 3)
    c.eq("and the data is read from the first block", mod.read("text\n" + good + "\n```json\nignored\n```\n").summary.modules, 3)
    c.eq("data at the very start of the page is read", mod.read(fenced([])).summary.modules, 0)
    return c.bad


def check_to_json(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    got = mod.read(page(ENTRIES))
    doc = json.loads(json.dumps(view_models.to_json(got)))
    c.eq("the document has the summary and the rows", sorted(doc), ["rows", "summary"])
    c.eq(
        "a row's members",
        sorted(doc["rows"][0]),
        ["checks", "clock", "current", "module", "noticed", "places", "raised", "reason", "survived", "unbuilt"],
    )
    c.eq(
        "a summary's members",
        sorted(doc["summary"]),
        ["clock", "modules", "noticed", "places", "raised", "stale", "survived", "unbuilt", "unchecked"],
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_rows_and_summary, check_order, check_currency, check_strict, check_to_json):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
