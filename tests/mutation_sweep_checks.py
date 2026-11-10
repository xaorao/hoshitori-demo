"""Scenarios for the sweep harness itself (``tests/mutation_sweep.py``), runnable against the harness module: ``failures(mod)``.

A sweep says which small changes of a module no check noticed. It is only as honest as its account of how it decided that a change *was*
noticed. Every expectation below is worked out by hand from ``sweep_planted`` (seven places to change, listed in its docstring) and from what
each function of ``sweep_planted_checks`` does to each wrong ``double(3)``:

* the module cannot be built (places 1, 2 and 3): BUILD_FAILED, whatever the checks are;
* ``strict``: places 4, 6 and 7 leave ``double(3)`` at None, 5 and 9, so a check reports it (FAILED); place 5 changes nothing: SURVIVED;
* ``lenient`` looks at ``double(0)`` only: None and 2 are caught, 3 * 0 is still 0, so place 7 SURVIVES with place 5;
* ``odd``: None raises an operating-system error whose message differs each time (ENVIRONMENT: the machine, not the change, is the
  likeliest cause), 5 hangs and swallows every ordinary exception (TIMED_OUT), 9 raises an ordinary error (CHECK_RAISED);
* ``steady``: 9 raises an operating-system error that comes back exactly the same on the second try (CHECK_RAISED: the change caused it);
* ``stubborn``: None, 5 and 9 hang, and the first limit is swallowed even by ``except BaseException``; the limit must come back (TIMED_OUT).

A change counts as noticed in every verdict but SURVIVED, so a loaded machine cannot invent a survivor; what the report adds is which
mutants were noticed by a check (FAILED) and which only by an error, a failure to build, or the clock.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import types
from collections.abc import Callable
from pathlib import Path

PLANTED = "tests.sweep_planted"
CHECKS = "tests.sweep_planted_checks"

S1 = "|statement dropped|WIDTH = 4|0"
S2 = "|int+1|4|0"
S3 = "|NotEq->Eq|WIDTH != 5|0"
S4 = "double|return None|return n * 2|0"
S5 = "|int+1|5|0"
S6 = "double|Mult->Add|n * 2|0"
S7 = "double|int+1|2|0"
ORDER = [S1, S2, S3, S4, S5, S6, S7]
UNBUILDABLE = {S1: "BUILD_FAILED", S2: "BUILD_FAILED", S3: "BUILD_FAILED"}

# (checks function, time limit in seconds, the verdict of places 4 to 7 in order)
CASES: tuple[tuple[str, float, tuple[str, str, str, str]], ...] = (
    ("strict", 10.0, ("FAILED", "SURVIVED", "FAILED", "FAILED")),
    ("lenient", 10.0, ("FAILED", "SURVIVED", "FAILED", "SURVIVED")),
    ("odd", 0.4, ("ENVIRONMENT", "SURVIVED", "TIMED_OUT", "CHECK_RAISED")),
    ("steady", 10.0, ("FAILED", "SURVIVED", "FAILED", "CHECK_RAISED")),
    ("stubborn", 0.4, ("TIMED_OUT", "SURVIVED", "TIMED_OUT", "TIMED_OUT")),
)


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str) -> None:
        try:
            call()
        except AssertionError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def check_every_verdict(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    for name, limit, wanted in CASES:
        report = mod.sweep(PLANTED, CHECKS, name, workers=2, limit_s=limit)
        want = {**UNBUILDABLE, **dict(zip((S4, S5, S6, S7), wanted, strict=True))}
        c.eq(
            f"{name}: the verdict of each place, in the order found",
            [(o.signature, str(o.verdict)) for o in report.outcomes],
            [(s, want[s]) for s in ORDER],
        )
        c.eq(f"{name}: places to change", report.total, 7)
        expected_alive = [s for s in ORDER if want[s] == "SURVIVED"]
        c.eq(f"{name}: survivors", report.alive, expected_alive)
        tally: dict[str, int] = {}
        for verdict in want.values():
            tally[verdict] = tally.get(verdict, 0) + 1
        c.eq(f"{name}: the tally", report.counts, tally)
        c.eq(
            f"{name}: noticed with no evidence (only the clock or the machine says so)",
            report.unresolved,
            [s for s in ORDER if want[s] in ("TIMED_OUT", "ENVIRONMENT")],
        )
    return c.bad


def check_survivors_keeps_its_answer(mod: types.ModuleType, tmp: Path) -> list[str]:
    """The 88 test files that call ``survivors`` get the pair they always got, and the sweep appends one line to the log named by
    ``HOSHITORI_SWEEP_LOG`` when it is set (and writes nothing when it is not)."""
    c = Case(mod)
    log = tmp / "sweeps.jsonl"
    saved = os.environ.get("HOSHITORI_SWEEP_LOG")
    try:
        os.environ.pop("HOSHITORI_SWEEP_LOG", None)
        c.eq("without a log", mod.survivors(PLANTED, CHECKS, "strict", workers=2), ([S5], 7))
        c.eq("and nothing written", log.exists(), False)
        os.environ["HOSHITORI_SWEEP_LOG"] = str(log)
        c.eq("with a log", mod.survivors(PLANTED, CHECKS, "strict", workers=2), ([S5], 7))
        mod.survivors(PLANTED, CHECKS, "lenient", workers=2)
    finally:
        if saved is None:
            os.environ.pop("HOSHITORI_SWEEP_LOG", None)
        else:
            os.environ["HOSHITORI_SWEEP_LOG"] = saved
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    c.eq("one line a sweep", len(lines), 2)
    first = json.loads(lines[0]) if lines else {}
    c.eq(
        "what a line says",
        {k: first.get(k) for k in ("module", "checks", "function", "total", "counts", "unresolved", "workers")},
        {
            "module": PLANTED,
            "checks": CHECKS,
            "function": "strict",
            "total": 7,
            "counts": {"BUILD_FAILED": 3, "FAILED": 3, "SURVIVED": 1},
            "unresolved": [],
            "workers": 2,
        },
    )
    c.eq(
        "and when, how loaded, and what the sweep ran on",
        sorted(first.keys() - {"module", "checks", "function", "total", "counts", "unresolved", "workers"}),
        ["checks_sha256", "load_end", "load_start", "module_sha256", "seconds"],
    )
    import hashlib
    import importlib.util

    def sha(name: str) -> str:
        spec = importlib.util.find_spec(name)
        assert spec is not None and spec.origin is not None
        return hashlib.sha256(Path(spec.origin).read_bytes()).hexdigest()

    c.eq(
        "the two hashes are those of the module file and the checks file",
        (first.get("module_sha256"), first.get("checks_sha256")),
        (sha(PLANTED), sha(CHECKS)),
    )
    second = json.loads(lines[1]) if len(lines) > 1 else {}
    c.eq(
        "the second line is the second sweep",
        (second.get("function"), second.get("counts")),
        ("lenient", {"BUILD_FAILED": 3, "FAILED": 2, "SURVIVED": 2}),
    )
    return c.bad


def check_a_package_module_is_built(mod: types.ModuleType, tmp: Path) -> list[str]:
    """A module that imports its neighbour with a relative import is built, so its one change is judged by the checks and not counted
    as noticed because it could not be built (finding F1)."""
    c = Case(mod)
    report = mod.sweep("tests.sweep_planted_relative", CHECKS, "twice", workers=2)
    c.eq(
        "one place, noticed by a check",
        ([o.signature for o in report.outcomes], [str(o.verdict) for o in report.outcomes]),
        (["twice|return None|return sweep_planted.double(n)|0"], ["FAILED"]),
    )
    return c.bad


def check_refusals(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    c.refuses(
        "checks that fail on the unchanged module",
        lambda: mod.sweep(PLANTED, CHECKS, "broken", workers=2),
        "the checks fail on the unchanged module, so no mutant would survive: ['fails on the unchanged module']",
    )

    def cannot_build(_module_name: str, _source: str) -> types.ModuleType:
        raise RuntimeError("no")

    real = mod.build_source
    mod.build_source = cannot_build
    try:
        c.refuses(
            "a harness that cannot build the unchanged module",
            lambda: mod.sweep(PLANTED, CHECKS, "strict", workers=2),
            "the sweep cannot build the unchanged module, so every mutant would count as noticed: RuntimeError: no",
        )
    finally:
        mod.build_source = real
    return c.bad


def check_a_swallowed_limit_comes_back(mod: types.ModuleType, tmp: Path) -> list[str]:
    """Covered by ``check_every_verdict`` (``odd`` and ``stubborn``); here only that a sweep over checks that finish at once ends at once."""
    c = Case(mod)
    report = mod.sweep(PLANTED, CHECKS, "strict", workers=2, limit_s=60.0)
    c.eq("a quick sweep is quick", report.seconds < 30, True)
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType, Path], list[str]]] = {
    "every_verdict": check_every_verdict,
    "survivors_keeps_its_answer": check_survivors_keeps_its_answer,
    "a_package_module_is_built": check_a_package_module_is_built,
    "refusals": check_refusals,
    "a_swallowed_limit_comes_back": check_a_swallowed_limit_comes_back,
}


def failures(mod: types.ModuleType, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        tmp = Path(tempfile.mkdtemp(prefix=f"hoshitori-sweep-{name}-"))
        try:
            bad += [f"{name}: {message}" for message in scenario(mod, tmp)]
        except Exception as exc:  # a crash in the scenario is a failure of the module under test
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return bad
