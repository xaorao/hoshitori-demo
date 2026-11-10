"""A mutation sweep: every small change of a module's code, made once, and which of them a set of checks catches.

``mutation.mutant`` breaks one rule chosen by hand. A sweep breaks every place the code can break in a simple way (a comparison turned round, an
``and`` made an ``or``, a condition negated, a constant moved, a statement or a return dropped) and asks the checks to notice each. A change that
no check notices is either harmless (an equivalent mutant: the code does the same thing either way) or a rule nobody tests. Each survivor is looked
at; the harmless ones are listed with the reason, and the list must match the survivors exactly, so it cannot go stale.

Sites inside docstrings, annotations, f-strings and the message of a raised error are left out: they carry words, not rules.

How a change is judged. Every change ends in one verdict, and a sweep reports all of them (``sweep``), because "noticed" is not one thing:

* ``FAILED``: a check reported a failure on the changed module. This is the evidence a sweep exists to give.
* ``CHECK_RAISED``: the checks raised an error on the changed module (an operating-system error counts only if the second try raised exactly
  the same one: then the change caused it, not the machine).
* ``BUILD_FAILED``: the changed module could not be built at all.
* ``TIMED_OUT``: the checks did not finish in ``limit_s`` seconds on either of two tries.
* ``ENVIRONMENT``: an operating-system error or a time-out that the second try did not repeat.
* ``SURVIVED``: the checks passed on the changed module.

Every verdict but SURVIVED counts as noticed, so a loaded machine cannot invent a survivor. But the last two say nothing about the checks (the
clock or the machine decided), so they are named in the report as ``unresolved`` and counted. The limit is raised as a ``BaseException`` and
comes back every second, so a check (or a changed module) that catches ``Exception``, or even ``BaseException`` once, cannot hang the sweep.
The unchanged module is built by the same code as a changed one and its checks must pass, so a harness that cannot build a module (finding F1)
fails loudly instead of reporting that nothing survived.

``survivors`` keeps its answer (the survivors and the number of places) for the many test files that call it. Set ``HOSHITORI_SWEEP_LOG`` to
a file and each ``survivors`` call appends one JSON line with the tally, the unresolved changes, the time and the load of the machine;
``tests/sweep_report.py`` reads such a log.
"""

from __future__ import annotations

import ast
import concurrent.futures
import enum
import hashlib
import importlib
import importlib.util
import json
import os
import signal
import sys
import time
import types
from dataclasses import dataclass
from typing import Any

COMPARE = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
}
ARITHMETIC = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Add}
SWAPPED_CALLS = {"any": "all", "all": "any", "max": "min", "min": "max"}
SWAPPED_METHODS = {"add": "discard", "discard": "add"}


@dataclass(frozen=True)
class Site:
    index: int  # position in the ast.walk order of the module
    operator: str
    line: int
    function: str
    text: str

    @property
    def signature(self) -> str:
        return f"{self.function}|{self.operator}|{self.text}"


def _skipped(tree: ast.Module) -> set[int]:
    """Ids of nodes that carry words and not rules: docstrings, annotations, f-string parts and the messages of raised errors."""
    skip: set[int] = set()

    def mark(node: ast.AST | None) -> None:
        if node is not None:
            skip.update(id(n) for n in ast.walk(node))

    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                mark(body[0])
        if isinstance(node, ast.AnnAssign):
            mark(node.annotation)
        if isinstance(node, ast.arg):
            mark(node.annotation)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            mark(node.returns)
        if isinstance(node, ast.JoinedStr):
            mark(node)
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            for arg in node.exc.args:
                if isinstance(arg, ast.Constant | ast.JoinedStr):
                    mark(arg)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            mark(node)
    return skip


def _functions(tree: ast.Module) -> dict[int, str]:
    names: dict[int, str] = {}

    def visit(node: ast.AST, current: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = current
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                inner = f"{current}.{child.name}" if current else child.name
            names[id(child)] = inner
            visit(child, inner)

    visit(tree, "")
    return names


def _operators(node: ast.AST) -> list[str]:
    found: list[str] = []
    if isinstance(node, ast.Compare) and type(node.ops[0]) in COMPARE:
        found.append(f"{type(node.ops[0]).__name__}->{COMPARE[type(node.ops[0])].__name__}")
    elif isinstance(node, ast.BoolOp):
        found.append("And->Or" if isinstance(node.op, ast.And) else "Or->And")
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        found.append("Not removed")
    elif isinstance(node, ast.BinOp) and type(node.op) in ARITHMETIC:
        found.append(f"{type(node.op).__name__}->{ARITHMETIC[type(node.op)].__name__}")
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in SWAPPED_CALLS:
        found.append(f"{node.func.id}->{SWAPPED_CALLS[node.func.id]}")
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sorted" and node.args:
        found.append("sorted removed")
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in SWAPPED_METHODS:
        found.append(f".{node.func.attr}->.{SWAPPED_METHODS[node.func.attr]}")
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            found.append("True<->False")
        elif isinstance(node.value, int):
            found.append("int+1")
        elif isinstance(node.value, str) and node.value:
            found.append("str+~")
    elif isinstance(node, ast.If | ast.IfExp | ast.While):
        found.append("test negated")
    elif (
        isinstance(node, ast.Return) and node.value is not None and not (isinstance(node.value, ast.Constant) and node.value.value is None)
    ):
        found.append("return None")
    elif isinstance(node, ast.Raise | ast.Assign | ast.AugAssign | ast.Continue | ast.Break) or (
        isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    ):
        found.append("statement dropped")
    return found


def enumerate_sites(source: str) -> list[Site]:
    tree = ast.parse(source)
    skip = _skipped(tree)
    names = _functions(tree)
    sites: list[Site] = []
    for index, node in enumerate(ast.walk(tree)):
        if id(node) in skip:
            continue
        for operator in _operators(node):
            sites.append(
                Site(index, operator, getattr(node, "lineno", 0), names.get(id(node), ""), ast.unparse(node).replace("\n", " ")[:160])
            )
    return sites


class _Mutate(ast.NodeTransformer):
    def __init__(self, target: ast.AST, operator: str) -> None:
        self.target, self.operator = target, operator

    def visit(self, node: ast.AST) -> Any:
        if node is not self.target:
            return self.generic_visit(node)
        op = self.operator
        if isinstance(node, ast.Compare):
            node.ops[0] = {v.__name__: v for v in COMPARE.values()}[op.split("->")[1]]()
            return node
        if isinstance(node, ast.BoolOp):
            node.op = ast.Or() if op == "And->Or" else ast.And()
            return node
        if isinstance(node, ast.UnaryOp):
            return node.operand
        if isinstance(node, ast.BinOp):
            node.op = {v.__name__: v for v in ARITHMETIC.values()}[op.split("->")[1]]()
            return node
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in SWAPPED_CALLS:
                node.func.id = SWAPPED_CALLS[node.func.id]
            elif isinstance(node.func, ast.Name):
                return node.args[0]  # sorted(x) is x
            elif isinstance(node.func, ast.Attribute):
                node.func.attr = SWAPPED_METHODS[node.func.attr]
            return node
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return ast.copy_location(ast.Constant(not node.value), node)
            if isinstance(node.value, int):
                return ast.copy_location(ast.Constant(node.value + 1), node)
            return ast.copy_location(ast.Constant(node.value + "~"), node)
        if isinstance(node, ast.If | ast.IfExp | ast.While):
            node.test = ast.copy_location(ast.UnaryOp(ast.Not(), node.test), node.test)
            return node
        if isinstance(node, ast.Return):
            return ast.copy_location(ast.Return(ast.Constant(None)), node)
        return ast.copy_location(ast.Pass(), node)


def mutated_code(source: str, filename: str, site: Site) -> Any:
    tree = ast.parse(source)
    target = list(ast.walk(tree))[site.index]
    tree = ast.fix_missing_locations(_Mutate(target, site.operator).visit(tree))
    return compile(tree, filename, "exec")


def _origin(module_name: str) -> str:
    spec = importlib.util.find_spec(module_name)
    assert spec is not None and spec.origin is not None
    return spec.origin


def _exec(module_name: str, name: str, code: Any, origin: str) -> types.ModuleType:
    """Run compiled code as a module of its own, in the package of ``module_name`` (a relative import needs it: finding F1)."""
    module = types.ModuleType(name)
    module.__file__ = origin
    module.__package__ = module_name.rpartition(".")[0]
    sys.modules[name] = module
    try:
        exec(code, module.__dict__)  # noqa: S102
    finally:
        sys.modules.pop(name, None)
    return module


def build_source(module_name: str, source: str) -> types.ModuleType:
    """The module exactly as its source says, built the way a changed one is."""
    origin = _origin(module_name)
    return _exec(module_name, f"_sweep_{module_name.replace('.', '_')}_unchanged", compile(source, origin, "exec"), origin)


def build(module_name: str, site: Site) -> types.ModuleType:
    origin = _origin(module_name)
    with open(origin, encoding="utf-8") as handle:
        source = handle.read()
    return _exec(module_name, f"_sweep_{module_name.replace('.', '_')}_{site.index}", mutated_code(source, origin, site), origin)


MUTANT_LIMIT_S = 60.0  # a mutant that runs longer is taken to hang; the checks of the slowest module take about 20 s under load


class Verdict(enum.StrEnum):
    FAILED = "FAILED"
    CHECK_RAISED = "CHECK_RAISED"
    BUILD_FAILED = "BUILD_FAILED"
    TIMED_OUT = "TIMED_OUT"
    ENVIRONMENT = "ENVIRONMENT"
    SURVIVED = "SURVIVED"


NO_EVIDENCE = (Verdict.TIMED_OUT, Verdict.ENVIRONMENT)  # counted as noticed, but the clock or the machine said so, not a check


@dataclass(frozen=True)
class Outcome:
    signature: str
    verdict: Verdict
    detail: str  # what the first failure, error or build error said, cut short


@dataclass(frozen=True)
class Report:
    module: str
    checks: str
    function: str
    outcomes: tuple[Outcome, ...]
    seconds: float
    workers: int
    load_start: float
    load_end: float

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def alive(self) -> list[str]:
        return [o.signature for o in self.outcomes if o.verdict is Verdict.SURVIVED]

    @property
    def unresolved(self) -> list[str]:
        return [o.signature for o in self.outcomes if o.verdict in NO_EVIDENCE]

    @property
    def counts(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for outcome in self.outcomes:
            tally[str(outcome.verdict)] = tally.get(str(outcome.verdict), 0) + 1
        return dict(sorted(tally.items()))

    def log_line(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "checks": self.checks,
            "function": self.function,
            "total": self.total,
            "counts": self.counts,
            "unresolved": self.unresolved,
            "seconds": round(self.seconds, 1),
            "workers": self.workers,
            "load_start": round(self.load_start, 2),
            "load_end": round(self.load_end, 2),
        }


class _Limit(BaseException):
    """The sweep's own time limit. It is not an Exception, so a check or a changed module that catches Exception cannot swallow it."""


_armed = False


def _on_alarm(_signum: int, _frame: object) -> None:
    if _armed:
        raise _Limit


def _attempt(job: tuple[str, str, str, Site, float]) -> tuple[str, str]:
    """One try at one change: (what happened, what it said). ``OS_ERROR`` is not a verdict yet: it takes a second try to tell the change
    from the machine."""
    global _armed
    module_name, checks_module, checks_name, site, limit_s = job
    _armed = True
    signal.setitimer(signal.ITIMER_REAL, limit_s, min(1.0, limit_s))
    try:
        checks = getattr(importlib.import_module(checks_module), checks_name)
        try:
            mutant = build(module_name, site)
        except _Limit:
            raise
        except BaseException as exc:  # a changed module may fail in any way, even by exiting
            return "BUILD_FAILED", f"{type(exc).__name__}: {exc}"[:200]
        try:
            found = checks(mutant)
        except _Limit:
            raise
        except OSError as exc:
            return "OS_ERROR", f"{type(exc).__name__}: {exc}"[:200]
        except BaseException as exc:
            return "CHECK_RAISED", f"{type(exc).__name__}: {exc}"[:200]
        return ("FAILED", str(found[0])[:200]) if found else ("SURVIVED", "")
    except _Limit:
        return "TIMED_OUT", ""
    finally:
        _armed = False
        signal.setitimer(signal.ITIMER_REAL, 0)


def _judge(job: tuple[str, str, str, Site, float]) -> tuple[Verdict, str]:
    signal.signal(signal.SIGALRM, _on_alarm)
    first = _attempt(job)
    if first[0] not in ("TIMED_OUT", "OS_ERROR"):
        return Verdict(first[0]), first[1]
    # a machine under load can starve a slow check or a port: try the change once more before deciding what it was
    second = _attempt(job)
    if second[0] == "OS_ERROR":
        # an error that comes back exactly the same was caused by the change; one that differs, or that follows a time-out, was the machine's
        return (Verdict.CHECK_RAISED if first == second else Verdict.ENVIRONMENT), second[1]
    if second[0] == "TIMED_OUT":
        return (Verdict.TIMED_OUT if first[0] == "TIMED_OUT" else Verdict.ENVIRONMENT), first[1]
    return Verdict(second[0]), second[1]


def sweep(module_name: str, checks_module: str, checks_name: str = "failures", workers: int = 8, limit_s: float = MUTANT_LIMIT_S) -> Report:
    """Every change of ``module_name``, each judged by the checks ``checks_module.checks_name``, with its verdict."""
    started = time.monotonic()
    load_start = os.getloadavg()[0]
    with open(_origin(module_name), encoding="utf-8") as handle:
        source = handle.read()
    checks = getattr(importlib.import_module(checks_module), checks_name)
    baseline = checks(importlib.import_module(module_name))
    if baseline:
        raise AssertionError(f"the checks fail on the unchanged module, so no mutant would survive: {baseline[:3]}")
    try:
        rebuilt = build_source(module_name, source)
    except Exception as exc:
        raise AssertionError(
            f"the sweep cannot build the unchanged module, so every mutant would count as noticed: {type(exc).__name__}: {exc}"
        ) from exc
    baseline = checks(rebuilt)
    if baseline:
        raise AssertionError(f"the checks fail on the unchanged module as the sweep builds it, so no mutant would survive: {baseline[:3]}")
    sites = enumerate_sites(source)
    names = signatures(sites)
    jobs = [(module_name, checks_module, checks_name, site, limit_s) for site in sites]
    outcomes: list[Outcome] = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
        for name, (verdict, detail) in zip(names, pool.map(_judge, jobs, chunksize=8), strict=True):
            outcomes.append(Outcome(name, verdict, detail))
    return Report(
        module_name, checks_module, checks_name, tuple(outcomes), time.monotonic() - started, workers, load_start, os.getloadavg()[0]
    )


def _file_sha256(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def survivors(module_name: str, checks_module: str, checks_name: str = "failures", workers: int = 8) -> tuple[list[str], int]:
    """The names of the sites whose change no check noticed, and how many sites there are. Appends the sweep's account to the file named by
    ``HOSHITORI_SWEEP_LOG`` when it is set."""
    report = sweep(module_name, checks_module, checks_name, workers)
    log = os.environ.get("HOSHITORI_SWEEP_LOG")
    if log:
        line = report.log_line()
        line["module_sha256"] = _file_sha256(_origin(module_name))  # what the sweep ran on, so a later change is known
        line["checks_sha256"] = _file_sha256(_origin(checks_module))
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, sort_keys=True) + "\n")
    return report.alive, report.total


def signatures(sites: list[Site]) -> list[str]:
    """A stable name for each site: the function, the operator, the code and which of the identical ones it is."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for site in sites:
        n = seen.get(site.signature, 0)
        seen[site.signature] = n + 1
        out.append(f"{site.signature}|{n}")
    return out
