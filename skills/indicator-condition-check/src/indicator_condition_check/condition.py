"""Condition grammar: parse, validate, evaluate.

A condition is a closed JSON expression tree. There is no eval and no free
text; anything outside the grammar is rejected before a network call.

    condition := {"all": [condition, ...]}
               | {"any": [condition, ...]}
               | {"not": condition}
               | {"left": operand, "op": OP, "right": operand}
    operand   := {"indicator": NAME, ...params} | {"value": number}
    OP        := < <= > >= == cross_above cross_below

Truth is three-valued (Kleene): true, false or null (cannot be decided
because history is too short or a value is undefined). Null never collapses
to false.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from . import indicators as ind
from .errors import InputError

MAX_CLAUSES = 8
MAX_DEPTH = 3
OPS = ("<", "<=", ">", ">=", "==", "cross_above", "cross_below")
SOURCES = ("open", "high", "low", "close", "volume")
EQ_REL_TOL = 1e-9

EXAMPLE_CONDITION = {
    "all": [
        {"left": {"indicator": "rsi", "period": 14}, "op": "<", "right": {"value": 30}},
        {"left": {"indicator": "close"}, "op": "cross_above", "right": {"indicator": "ema", "period": 50}},
    ]
}

# name -> {param: (kind, default or None-if-required, lo, hi)} and allowed outputs
_SPEC: dict[str, dict[str, Any]] = {
    "open": {}, "high": {}, "low": {}, "close": {}, "volume": {},
    "sma": {"period": ("int", None, 1, 500), "source": ("source", "close")},
    "ema": {"period": ("int", None, 1, 500), "source": ("source", "close")},
    "rsi": {"period": ("int", 14, 2, 500), "source": ("source", "close")},
    "atr": {"period": ("int", 14, 1, 500)},
    "atr_pct": {"period": ("int", 14, 1, 500)},
    "macd": {"fast": ("int", 12, 1, 500), "slow": ("int", 26, 2, 500), "signal": ("int", 9, 1, 500),
             "output": ("enum", "macd", ("macd", "signal", "hist"))},
    "bbands": {"period": ("int", 20, 2, 500), "stddev": ("float", 2.0, 0.1, 10.0),
               "output": ("enum", "percent_b", ("upper", "middle", "lower", "percent_b", "width"))},
    "stoch": {"k_period": ("int", 14, 1, 500), "smooth": ("int", 3, 1, 500), "d_period": ("int", 3, 1, 500),
              "output": ("enum", "k", ("k", "d"))},
    "volume_zscore": {"period": ("int", 20, 2, 500)},
    "roc": {"period": ("int", 10, 1, 500), "source": ("source", "close")},
    "highest_prior": {"period": ("int", 20, 1, 500), "source": ("source", "high")},
    "lowest_prior": {"period": ("int", 20, 1, 500), "source": ("source", "low")},
}
INDICATOR_NAMES = tuple(_SPEC)


@dataclass(frozen=True)
class Operand:
    name: str | None  # None for a constant
    params: tuple[tuple[str, Any], ...] = ()
    value: float | None = None

    @property
    def is_const(self) -> bool:
        return self.name is None

    def text(self) -> str:
        if self.is_const:
            return _fmt(self.value)
        p = dict(self.params)
        n = self.name
        if n in ("open", "high", "low", "close", "volume"):
            return n
        if n in ("sma", "ema", "rsi", "roc", "highest_prior", "lowest_prior"):
            default_src = {"highest_prior": "high", "lowest_prior": "low"}.get(n, "close")
            src = "" if p["source"] == default_src else f",{p['source']}"
            return f"{n}({p['period']}{src})"
        if n in ("atr", "atr_pct", "volume_zscore"):
            return f"{n}({p['period']})"
        if n == "macd":
            return f"macd({p['fast']},{p['slow']},{p['signal']}).{p['output']}"
        if n == "bbands":
            return f"bbands({p['period']},{_fmt(p['stddev'])}).{p['output']}"
        if n == "stoch":
            return f"stoch({p['k_period']},{p['smooth']},{p['d_period']}).{p['output']}"
        raise AssertionError(n)

    def warmup(self) -> int:
        if self.name is None:
            return 1
        return ind.WARMUP[self.name](**dict(self.params))

    def stable_bars(self) -> int:
        """Bars needed before a recursively smoothed value has forgotten its seed (residual about e^-6, 0.25%).
        EMA: warm-up + 3n. Wilder RSI/ATR (alpha 1/n): warm-up + 6n. MACD: 4 x slow, plus 3 x signal for the
        signal line and histogram. Window-based indicators are exact as soon as they are defined."""
        if self.name is None:
            return 1
        p = dict(self.params)
        w = self.warmup()
        if self.name == "ema":
            return w + 3 * p["period"]
        if self.name in ("rsi", "atr", "atr_pct"):
            return w + 6 * p["period"]
        if self.name == "macd":
            return 4 * p["slow"] + (0 if p["output"] == "macd" else 3 * p["signal"])
        return w


@dataclass(frozen=True)
class Clause:
    id: str
    left: Operand
    op: str
    right: Operand

    def text(self) -> str:
        return f"{self.left.text()} {self.op} {self.right.text()}"

    def warmup(self) -> int:
        w = max(self.left.warmup(), self.right.warmup())
        return w + 1 if self.op in ("cross_above", "cross_below") else w

    def stable_bars(self) -> int:
        w = max(self.left.stable_bars(), self.right.stable_bars())
        return w + 1 if self.op in ("cross_above", "cross_below") else w


@dataclass(frozen=True)
class Group:
    kind: str  # all | any | not
    children: tuple[Node, ...]


Node = Clause | Group


def _fmt(x: Any) -> str:
    if isinstance(x, float) and x.is_integer():
        x = int(x)
    return str(x)


def _num(v: Any, path: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise InputError(path, "must be a finite number", EXAMPLE_CONDITION)
    return float(v)


def _parse_operand(raw: Any, path: str) -> Operand:
    if not isinstance(raw, dict):
        raise InputError(path, 'must be an object like {"indicator":"rsi","period":14} or {"value":30}', EXAMPLE_CONDITION)
    if "value" in raw:
        extra = set(raw) - {"value"}
        if extra:
            raise InputError(path, f"a constant takes only 'value'; unexpected {sorted(extra)}", EXAMPLE_CONDITION)
        return Operand(None, (), _num(raw["value"], f"{path}.value"))
    name = raw.get("indicator")
    if not isinstance(name, str) or name not in _SPEC:
        raise InputError(f"{path}.indicator", f"unknown indicator {name!r}; choose one of {list(INDICATOR_NAMES)}", EXAMPLE_CONDITION)
    spec = _SPEC[name]
    extra = set(raw) - {"indicator"} - set(spec)
    if extra:
        raise InputError(path, f"indicator {name!r} does not take {sorted(extra)}; allowed {sorted(spec)}", EXAMPLE_CONDITION)
    params: dict[str, Any] = {}
    for pname, rule in spec.items():
        kind = rule[0]
        if pname in raw:
            v = raw[pname]
        elif rule[1] is None:
            raise InputError(f"{path}.{pname}", f"required for indicator {name!r}", EXAMPLE_CONDITION)
        else:
            v = rule[1]
        ppath = f"{path}.{pname}"
        if kind == "int":
            if isinstance(v, bool) or not isinstance(v, int) or not (rule[2] <= v <= rule[3]):
                raise InputError(ppath, f"must be an integer between {rule[2]} and {rule[3]}", EXAMPLE_CONDITION)
        elif kind == "float":
            v = _num(v, ppath)
            if not (rule[2] <= v <= rule[3]):
                raise InputError(ppath, f"must be between {rule[2]} and {rule[3]}", EXAMPLE_CONDITION)
        elif kind == "source" and v not in SOURCES:
            raise InputError(ppath, f"must be one of {list(SOURCES)}", EXAMPLE_CONDITION)
        elif kind == "enum" and v not in rule[2]:
            raise InputError(ppath, f"must be one of {list(rule[2])}", EXAMPLE_CONDITION)
        params[pname] = v
    if name == "macd" and params["fast"] >= params["slow"]:
        raise InputError(path, "macd needs fast < slow", EXAMPLE_CONDITION)
    return Operand(name, tuple(sorted(params.items())))


@dataclass
class _Parser:
    clauses: int = 0

    def parse(self, raw: Any, path: str, depth: int) -> Node:
        if not isinstance(raw, dict) or not raw:
            raise InputError(path, "must be a non-empty object", EXAMPLE_CONDITION)
        if depth > MAX_DEPTH:
            raise InputError(path, f"nesting deeper than {MAX_DEPTH} levels", EXAMPLE_CONDITION)
        keys = set(raw)
        if keys & {"all", "any"}:
            kind = "all" if "all" in raw else "any"
            if keys != {kind}:
                raise InputError(path, f"a group takes only '{kind}'", EXAMPLE_CONDITION)
            items = raw[kind]
            if not isinstance(items, list) or not items:
                raise InputError(f"{path}.{kind}", "must be a non-empty list of conditions", EXAMPLE_CONDITION)
            return Group(kind, tuple(self.parse(c, f"{path}.{kind}[{i}]", depth + 1) for i, c in enumerate(items)))
        if "not" in keys:
            if keys != {"not"}:
                raise InputError(path, "'not' takes only one condition", EXAMPLE_CONDITION)
            return Group("not", (self.parse(raw["not"], f"{path}.not", depth + 1),))
        if keys != {"left", "op", "right"}:
            raise InputError(path, "a clause needs exactly left, op and right (or use all / any / not)", EXAMPLE_CONDITION)
        if raw["op"] not in OPS:
            raise InputError(f"{path}.op", f"must be one of {list(OPS)}", EXAMPLE_CONDITION)
        self.clauses += 1
        if self.clauses > MAX_CLAUSES:
            raise InputError(path, f"more than {MAX_CLAUSES} clauses", EXAMPLE_CONDITION)
        return Clause(
            f"c{self.clauses}",
            _parse_operand(raw["left"], f"{path}.left"),
            raw["op"],
            _parse_operand(raw["right"], f"{path}.right"),
        )


def parse_condition(raw: Any) -> Node:
    return _Parser().parse(raw, "condition", 1)


def clauses_of(node: Node) -> list[Clause]:
    if isinstance(node, Clause):
        return [node]
    return [c for ch in node.children for c in clauses_of(ch)]


def required_bars(node: Node) -> int:
    return max(c.warmup() for c in clauses_of(node))


def normalize(node: Node) -> dict:
    """Canonical JSON form with every default made explicit, for echoing back in `request`."""
    if isinstance(node, Group):
        if node.kind == "not":
            return {"not": normalize(node.children[0])}
        return {node.kind: [normalize(c) for c in node.children]}

    def op(o: Operand) -> dict:
        return {"value": o.value} if o.is_const else {"indicator": o.name, **dict(o.params)}

    return {"left": op(node.left), "op": node.op, "right": op(node.right)}


# ---------------------------------------------------------------------------
# Evaluation


@dataclass
class Bars:
    """Closed candles as parallel lists, oldest first."""

    ts: list[int]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]

    def __len__(self) -> int:
        return len(self.ts)

    def field(self, name: str) -> list[float]:
        return getattr(self, name)


@dataclass
class ClauseResult:
    clause: Clause
    left: float | None
    right: float | None
    satisfied: bool | None
    null_reason: str | None
    true_count: int
    defined_count: int
    bars_since_last_true: int | None
    last_true_index: int | None


@dataclass
class Evaluation:
    series: list[bool | None]  # whole-condition truth per bar
    clauses: list[ClauseResult]
    defined_bars: int
    true_bars: int
    last_true_index: int | None
    cache: dict = field(default_factory=dict)


def _operand_series(o: Operand, bars: Bars, cache: dict) -> list[float | None]:
    key = o.text()
    if key in cache:
        return cache[key]
    n = len(bars)
    if o.is_const:
        out: list[float | None] = [o.value] * n
    else:
        p = dict(o.params)
        name = o.name
        if name in SOURCES:
            out = list(bars.field(name))
        elif name == "sma":
            out = ind.sma(bars.field(p["source"]), p["period"])
        elif name == "ema":
            out = ind.ema(bars.field(p["source"]), p["period"])
        elif name == "rsi":
            out = ind.rsi(bars.field(p["source"]), p["period"])
        elif name == "atr":
            out = ind.atr(bars.high, bars.low, bars.close, p["period"])
        elif name == "atr_pct":
            out = ind.atr_pct(bars.high, bars.low, bars.close, p["period"])
        elif name == "macd":
            out = ind.macd(bars.close, p["fast"], p["slow"], p["signal"])[p["output"]]
        elif name == "bbands":
            out = ind.bbands(bars.close, p["period"], p["stddev"])[p["output"]]
        elif name == "stoch":
            out = ind.stoch(bars.high, bars.low, bars.close, p["k_period"], p["smooth"], p["d_period"])[p["output"]]
        elif name == "volume_zscore":
            out = ind.volume_zscore(bars.volume, p["period"])
        elif name == "roc":
            out = ind.roc(bars.field(p["source"]), p["period"])
        elif name == "highest_prior":
            out = ind.highest_prior(bars.field(p["source"]), p["period"])
        elif name == "lowest_prior":
            out = ind.lowest_prior(bars.field(p["source"]), p["period"])
        else:  # pragma: no cover - parser guarantees the name
            raise AssertionError(name)
    cache[key] = out
    return out


def _compare(op: str, a: float | None, b: float | None, pa: float | None, pb: float | None) -> bool | None:
    if op in ("cross_above", "cross_below"):
        if None in (a, b, pa, pb):
            return None
        if op == "cross_above":
            return pa <= pb and a > b  # type: ignore[operator]
        return pa >= pb and a < b  # type: ignore[operator]
    if a is None or b is None:
        return None
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    if op == ">=":
        return a >= b
    return abs(a - b) <= EQ_REL_TOL * max(1.0, abs(b))


def _clause_series(c: Clause, bars: Bars, cache: dict) -> tuple[list[bool | None], list, list]:
    ls, rs = _operand_series(c.left, bars, cache), _operand_series(c.right, bars, cache)
    out: list[bool | None] = []
    for i in range(len(bars)):
        pa = ls[i - 1] if i else None
        pb = rs[i - 1] if i else None
        out.append(_compare(c.op, ls[i], rs[i], pa, pb))
    return out, ls, rs


def _and(vals: Sequence[bool | None]) -> bool | None:
    if any(v is False for v in vals):
        return False
    return None if any(v is None for v in vals) else True


def _or(vals: Sequence[bool | None]) -> bool | None:
    if any(v is True for v in vals):
        return True
    return None if any(v is None for v in vals) else False


def evaluate(node: Node, bars: Bars, window: int | None = None) -> Evaluation:
    """Evaluate on every bar given (so exponential indicators have their full warm-up) and compute the
    history statistics over the last `window` bars only. Indices in the result are absolute."""
    n = len(bars)
    start = 0 if window is None else max(0, n - window)
    cache: dict = {}
    per_clause: dict[str, list[bool | None]] = {}
    vals: dict[str, tuple[list, list]] = {}
    for c in clauses_of(node):
        s, ls, rs = _clause_series(c, bars, cache)
        per_clause[c.id] = s
        vals[c.id] = (ls, rs)

    def tree(nd: Node) -> list[bool | None]:
        if isinstance(nd, Clause):
            return per_clause[nd.id]
        kids = [tree(k) for k in nd.children]
        if nd.kind == "not":
            return [None if v is None else (not v) for v in kids[0]]
        comb = _and if nd.kind == "all" else _or
        return [comb([k[i] for k in kids]) for i in range(n)]

    overall = tree(node)
    results: list[ClauseResult] = []
    for c in clauses_of(node):
        s = per_clause[c.id]
        ls, rs = vals[c.id]
        last_true = max((i for i in range(start, n) if s[i] is True), default=None)
        reason = None
        if n and s[-1] is None:
            need, have = c.warmup(), n
            reason = f"needs {need} bars for {c.text()}, have {have}" if have < need else f"{c.text()} is undefined on the last bar (flat or zero-range data)"
        results.append(
            ClauseResult(
                clause=c,
                left=ls[-1] if n else None,
                right=rs[-1] if n else None,
                satisfied=s[-1] if n else None,
                null_reason=reason,
                true_count=sum(1 for v in s[start:] if v is True),
                defined_count=sum(1 for v in s[start:] if v is not None),
                bars_since_last_true=None if last_true is None else n - 1 - last_true,
                last_true_index=last_true,
            )
        )
    last_true_all = max((i for i in range(start, n) if overall[i] is True), default=None)
    return Evaluation(
        series=overall,
        clauses=results,
        defined_bars=sum(1 for v in overall[start:] if v is not None),
        true_bars=sum(1 for v in overall[start:] if v is True),
        last_true_index=last_true_all,
        cache=cache,
    )
