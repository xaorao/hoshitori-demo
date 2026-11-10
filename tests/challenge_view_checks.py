"""Checks of the challenge rows (``hoshitori.challenge_view``), runnable against a mutant, on the records of a real league cycle on loopback."""

from __future__ import annotations

import atexit
import copy
import dataclasses
import functools
import re
import shutil
import tempfile
import types
from contextlib import ExitStack
from decimal import Decimal
from pathlib import Path
from typing import Any

from hoshitori import labels, rules_arm, scheduler
from tests.e2e_league import league
from tests.league_checks import barrier_map


@functools.cache
def fixture() -> tuple[list[dict[str, Any]], str, Any, Any]:
    """The records of a league that has run one cycle, the cycle's id, the protocol and the mappings; kept for the process."""
    stack = ExitStack()
    atexit.register(stack.close)
    directory = Path(tempfile.mkdtemp(prefix="challenge-view-"))
    stack.callback(shutil.rmtree, directory, ignore_errors=True)
    lg = stack.enter_context(league(directory, barrier=barrier_map()))
    scheduler.tick(lg.services, lg.config)
    records = scheduler.read_all(lg.services)
    cycle = next(r["body"]["cycle_id"] for r in records if r.get("type") == "snapshot")
    return records, cycle, lg.services.protocol, lg.services.mappings


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Any, says: str) -> None:
        try:
            call()
        except self.mod.ChallengeViewError as exc:
            if not str(exc).startswith(says):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected to start with {says!r}")
            return
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def decision(verdict: Any, p: str | None = None) -> Any:
    return rules_arm.Decision("c", 1, verdict, None, None, None if p is None else Decimal(p), None, (), "h", "m", ())


def check_describe(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("a call", mod.describe(decision(rules_arm.Verdict.CALL, "0.6")), "60% up")
    c.eq("a probability that is not whole", mod.describe(decision(rules_arm.Verdict.CALL, "0.004")), "1% up")
    c.eq("missing evidence", mod.describe(decision(rules_arm.Verdict.FAILED_EVIDENCE)), "No call: the evidence it needs was missing")
    c.eq("no call", mod.describe(decision(rules_arm.Verdict.NO_CALL)), "No call")
    c.eq("a call without a probability is no call", mod.describe(decision(rules_arm.Verdict.CALL)), "No call")
    c.eq("frozen", mod.Row.__dataclass_params__.frozen, True)
    return c.bad


def check_ordered(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    names = [f"source-{i:02d}" for i in range(12)]
    c.eq("distinct names in alphabetical order whatever they came in", mod.ordered(reversed(names + names[:3])), names)
    c.eq("nothing gives nothing", mod.ordered([]), [])
    return c.bad


NO_CALL_WORDS = "no call: too few past windows in that state"
# (symbol, horizon): what the rule says in the state it was in, and per direction what it would say in the next state that way; a
# chance of None is a state with too few windows. The rest keep the fixture's own answer.
STEPS: dict[tuple[str, int], tuple[str | None, tuple[tuple[str, str | None, str | None], ...]]] = {
    ("BTC", 1): (None, (("UP", None, None), ("DOWN", None, None))),  # as on every 24-hour row of the quickstart: no call either way
    ("BTC", 4): ("0.5", (("UP", "0.5", "FLAT"), ("DOWN", "0.3", "FLAT"))),
    ("BTC", 24): (None, (("UP", "0.7", "LONG"), ("DOWN", None, None))),
    ("ETH", 1): ("0.48", (("UP", "0.49", "FLAT"), ("DOWN", None, None))),
    ("ETH", 24): ("0.5", (("UP", "0.5", "LONG"), ("DOWN", "0.5", "FLAT"))),
}
WANT = {
    ("BTC", 1): (NO_CALL_WORDS, []),
    ("BTC", 4): ("50% up, flat", [("DOWN", "30% up, flat")]),
    ("BTC", 24): (NO_CALL_WORDS, [("UP", "70% up, long")]),
    ("ETH", 1): ("48% up, flat", [("UP", "49% up, flat"), ("DOWN", NO_CALL_WORDS)]),
    ("ETH", 4): ("11% up, flat", [("UP", "30% up, flat")]),
    ("ETH", 24): ("50% up, flat", [("UP", "50% up, long")]),
}


def check_moves_change_the_call(c: Case, records: Any, cycle: str, protocol: Any, mappings: Any) -> None:
    """A move is the nearest state, each way, in which the rule would have said something else (``rules_sensitivity.changes``; a state
    that says the same chance and action is no change of the call). The view lists what the sensitivity gives and adds nothing, so here
    the sensitivity is stood in for by steps worked out by hand: a row whose states around all say the same has no move."""
    mod = c.mod
    real = mod.rules_sensitivity
    manifest = next(r for r in records if r.get("type") == "manifest" and r["body"]["cycle_id"] == cycle)
    symbol = {e["candidate_id"]: e["symbol"] for e in manifest["body"]["entries"] if e["inclusion"] == "INCLUDED"}

    def stepped(reader: Any, candidate_id: str, mapping: Any, hours: int) -> Any:
        s = real.sensitivity(reader, candidate_id, mapping, hours)
        step = STEPS.get((symbol[candidate_id], hours))
        if s is None or step is None:
            return s
        p, around = step
        sign = {"UP": Decimal("0.001"), "DOWN": Decimal("-0.001")}  # a move up is a positive distance and a move down a negative one
        near = tuple(real.Neighbour(d, "S3", s.ret + sign[d], sign[d], None if q is None else Decimal(q), a) for d, q, a in around)
        said = (None if p is None else Decimal(p), real.action_of(None if p is None else Decimal(p)))
        different = tuple(n for n in near if (n.p, n.action) != said)
        return dataclasses.replace(s, p=None if p is None else Decimal(p), neighbours=near, changes=different)

    mod.rules_sensitivity = types.SimpleNamespace(sensitivity=stepped, action_of=real.action_of)
    try:
        got = {
            (b.symbol, b.hours): (b.says, [(m.direction, m.says) for m in b.moves])
            for b in mod.boundaries(records, cycle, protocol, mappings)
        }
    finally:
        mod.rules_sensitivity = real
    c.eq("only the moves that change the call, and a row whose states around all say the same has none", got, WANT)


def check_boundaries(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    records, cycle, protocol, mappings = fixture()
    got = mod.boundaries(records, cycle, protocol, mappings)
    c.eq(
        "a boundary for each candidate and horizon of the rules arm",
        (len(got), {b.hours for b in got}, {b.arm for b in got}),
        (6, {1, 4, 24}, {protocol.arms[0][0]}),
    )
    c.eq("both candidates, together", sorted({b.symbol for b in got}), ["BTC", "ETH"])
    words = set(labels.RULE_STATE_WORDS.values())
    for b in got:
        c.eq("a return written as a signed percentage", re.fullmatch(r"(?:0\.00|[+-]\d+\.\d\d)", b.ret) is not None, True)
        c.eq("a state in words", b.state in words, True)
        c.eq(
            "what the rule said",
            re.fullmatch(r"\d+% up, (?:long|flat)|no call: too few past windows in that state", b.says) is not None,
            True,
        )
        c.eq("one or two moves", 1 <= len(b.moves) <= 2, True)
        for m in b.moves:
            c.eq(
                "an up move is a positive distance and a down move a negative one",
                m.distance.startswith("+") if m.direction == "UP" else m.distance.startswith("-"),
                True,
            )
            c.eq("a state in words", m.state in words, True)
    # Whether a row of this fixture has two moves depends on when it runs: started at hh:01, its first slot is on the hour and the 1-hour
    # BTC row is in the top state with only a down move, and then no row has two. So the order is checked on every row that has two, and
    # the stepped rows of check_moves_change_the_call pin it whatever the clock.
    c.eq("up moves come first", {tuple(m.direction for m in b.moves) for b in got if len(b.moves) == 2} - {("UP", "DOWN")}, set())
    c.eq("every move listed changes what the rule says", [(b.symbol, b.hours) for b in got if any(m.says == b.says for m in b.moves)], [])
    c.eq(
        "a signed percentage",
        [mod._points(Decimal(v)) for v in ("0.0025", "-0.0025", "0", "0.00004", "-0.00004", "0.00005", "0.00015", "-0.00015", "0.1234")],
        ["+0.25", "-0.25", "0.00", "0.00", "0.00", "0.00", "+0.02", "-0.02", "+12.34"],
    )
    c.eq(
        "a gap too small to show",
        [mod._gap(Decimal(v)) for v in ("0.0025", "-0.0025", "0.00004", "-0.00004", "0", "0.0001", "-0.0001")],
        ["+0.25", "-0.25", "+<0.01", "-<0.01", "0.00", "+0.01", "-0.01"],
    )
    c.eq(
        "what the rule says",
        [
            mod._says(Decimal("0.6"), "LONG"),
            mod._says(Decimal("0.4"), "FLAT"),
            mod._says(None, None),
            mod._says(Decimal("0.6"), None),
            mod._says(None, "LONG"),
        ],
        [
            "60% up, long",
            "40% up, flat",
            "no call: too few past windows in that state",
            "no call: too few past windows in that state",
            "no call: too few past windows in that state",
        ],
    )
    real = mod.rules_sensitivity
    mod.rules_sensitivity = types.SimpleNamespace(sensitivity=lambda *_: None, action_of=real.action_of)
    try:
        c.eq("a call the rule had no state for has no boundary", mod.boundaries(records, cycle, protocol, mappings), ())
    finally:
        mod.rules_sensitivity = real
    check_moves_change_the_call(c, records, cycle, protocol, mappings)
    c.refuses(
        "a cycle with no snapshot",
        lambda: mod.boundaries(records, "cy-none", protocol, mappings),
        "the ledger holds no snapshot and manifest of this cycle",
    )
    c.eq("frozen", [cls.__dataclass_params__.frozen for cls in (mod.Move, mod.Boundary)], [True, True])
    return c.bad


def check_rows(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    records, cycle, protocol, mappings = fixture()
    rows = mod.rows(records, cycle, protocol, mappings)
    c.eq("two candidates, three horizons, two sources", len(rows), 12)
    first = rows[0].symbol
    c.eq(
        "within a candidate the order is horizon, then source",
        [(r.symbol, r.hours, r.source) for r in rows[:6]],
        [(first, h, s) for h in (1, 4, 24) for s in ("binance-spot", "hoshitori.features")],
    )
    c.eq("a candidate's rows are together", [r.symbol for r in rows], [first] * 6 + [rows[6].symbol] * 6)
    c.eq("and the second is the other one", {first, rows[6].symbol}, {"BTC", "ETH"})
    c.eq("every row is of this cycle and of the rules arm", {(r.cycle_id, r.arm) for r in rows}, {(cycle, protocol.arms[0][0])})
    c.eq("this rule reads only what is derived, so every source is required", {r.outcome for r in rows}, {"EVIDENCE_REQUIRED"})
    c.eq("its words", {r.words for r in rows}, {"The rule cannot decide without it: it is evidence the rule requires."})
    c.eq(
        "what the rule said and what it says without",
        (rows[0].original.endswith("% up"), rows[0].changed),
        (True, "No call: the evidence it needs was missing"),
    )
    c.eq("the items that left with a source", {r.source: r.items for r in rows}, {"binance-spot": 6, "hoshitori.features": 2})
    c.eq("both candidates", sorted({r.symbol for r in rows}), ["BTC", "ETH"])
    c.refuses(
        "a cycle with no snapshot",
        lambda: mod.rows(records, "cy-none", protocol, mappings),
        "the ledger holds no snapshot and manifest of this cycle",
    )
    c.refuses(
        "no manifest",
        lambda: mod.rows([r for r in records if r.get("type") != "manifest"], cycle, protocol, mappings),
        "the ledger holds no snapshot and manifest of this cycle",
    )
    broken = copy.deepcopy(records)
    victim = next(r for r in broken if r.get("type") == "evidence")
    victim["body"]["adapter_version"] = "tampered-1"
    c.refuses(
        "an evidence record that is not the one the snapshot names",
        lambda: mod.rows(broken, cycle, protocol, mappings),
        "the snapshot of the cycle cannot be rebuilt: ",
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (check_describe, check_ordered, check_rows, check_boundaries)
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
