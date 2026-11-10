"""A mutation sweep: every small change of a module's code, made once, and which of them a set of checks catches.

``mutation.mutant`` breaks one rule chosen by hand. A sweep breaks every place the code can break in a simple way (a comparison turned round, an
``and`` made an ``or``, a condition negated, a constant moved, a statement or a return dropped) and asks the checks to notice each. A change that
no check notices is either harmless (an equivalent mutant: the code does the same thing either way) or a rule nobody tests. Each survivor is looked
at; the harmless ones are listed with the reason, and the list must match the survivors exactly, so it cannot go stale.

Sites inside docstrings, annotations, f-strings and the message of a raised error are left out: they carry words, not rules.

Copied unchanged in behaviour from ``ryo-agent-league/tests/mutation_sweep.py`` (our own code) so that this skill's package stands alone. Module
names are given by the caller (``survivors("address_flag_check.verdict", "tests.verdict_checks")``), so nothing here imports the league.
Run from the skill's folder with the skill's own venv; ``tests`` is importable because ``pyproject.toml`` puts the folder on the path.
One adaptation: the skill's modules import their siblings relatively (``from .errors import ...``), so a mutant is built with
``__package__`` set to the module's package. Without it every mutant failed to import, and the sweep counted each as noticed: a sweep of
``server`` reported 0 of 584 alive in 1.9 s (found by the main builder before any sweep of this skill was trusted).
"""

from __future__ import annotations

import ast
import concurrent.futures
import importlib
import importlib.util
import signal
import sys
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


def build(module_name: str, site: Site) -> types.ModuleType:
    origin = _origin(module_name)
    with open(origin, encoding="utf-8") as handle:
        source = handle.read()
    name = f"_sweep_{module_name.replace('.', '_')}_{site.index}"
    module = types.ModuleType(name)
    module.__file__ = origin
    module.__package__ = module_name.rpartition(".")[0]  # relative imports of the mutant resolve against the real package
    sys.modules[name] = module
    try:
        exec(mutated_code(source, origin, site), module.__dict__)  # noqa: S102
    finally:
        sys.modules.pop(name, None)
    return module


MUTANT_LIMIT_S = 60  # a mutant that runs longer is taken to hang; the checks of the slowest module take about 20 s under load


def _killed(job: tuple[str, str, str, Site]) -> tuple[Site, bool]:
    module_name, checks_module, checks_name, site = job

    def timeout(_signum: int, _frame: object) -> None:
        raise TimeoutError("a mutant ran too long")

    signal.signal(signal.SIGALRM, timeout)
    for attempt in (1, 2):
        signal.alarm(MUTANT_LIMIT_S)
        try:
            checks = getattr(importlib.import_module(checks_module), checks_name)
            found = checks(build(module_name, site))
            return site, bool(found)
        except (TimeoutError, OSError):
            if attempt == 2:  # the same mutant failed to finish twice, or twice met an environment error: it is noticed
                return site, True
            # a machine under load can starve a slow check or a port: try the mutant once more before it counts as noticed
        except BaseException:
            return site, True  # a mutant that breaks the module is noticed
        finally:
            signal.alarm(0)
    return site, True  # pragma: no cover


def survivors(module_name: str, checks_module: str, checks_name: str = "failures", workers: int = 8) -> tuple[list[str], int]:
    """The names of the sites whose change no check noticed, and how many sites there are."""
    with open(_origin(module_name), encoding="utf-8") as handle:
        source = handle.read()
    baseline = getattr(importlib.import_module(checks_module), checks_name)(importlib.import_module(module_name))
    if baseline:
        raise AssertionError(f"the checks fail on the unchanged module, so no mutant would survive: {baseline[:3]}")
    sites = enumerate_sites(source)
    names = signatures(sites)
    jobs = [(module_name, checks_module, checks_name, site) for site in sites]
    alive: list[str] = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
        for name, (_, killed) in zip(names, pool.map(_killed, jobs, chunksize=8), strict=True):
            if not killed:
                alive.append(name)
    return alive, len(sites)


def signatures(sites: list[Site]) -> list[str]:
    """A stable name for each site: the function, the operator, the code and which of the identical ones it is."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for site in sites:
        n = seen.get(site.signature, 0)
        seen[site.signature] = n + 1
        out.append(f"{site.signature}|{n}")
    return out
