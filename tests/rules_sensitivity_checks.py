"""Checks of how far a rules-arm call is from changing (``hoshitori.rules_sensitivity``), runnable against a mutant."""

from __future__ import annotations

import random
import types
from decimal import Decimal
from typing import Any

from hoshitori import rules_arm, snapshot
from tests.rules_arm_checks import FEATURES, graded, rates, reader_with
from tests.snapshot_checks import World as SnapWorld

D = Decimal


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def sens(mod: types.ModuleType, ret: str, atr: str, hours: int, mapping: Any = None, **kw: Any) -> Any:
    fields = dict(FEATURES)
    fields[f"ret_{hours}h"] = ret
    fields["atr14_share"] = atr
    _w, reader, _everything, _feats = reader_with(fields, **kw)
    return mod.sensitivity(reader, "cd-btc", mapping or graded(rules_arm), hours)


def check_known(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    m = graded(rules_arm)
    s = sens(mod, "0.000000000000", "0.010000000000", 1)
    c.eq("the call", (s.hours, s.ret, s.atr_share, s.state, s.p), (1, D("0"), D("0.01"), "S3", m.p(1, "S3")))
    up, down = s.neighbours
    c.eq(
        "up: the return that reaches S4",
        (up.direction, up.state, up.ret, up.distance, up.p),
        ("UP", "S4", D("0.0025"), D("0.0025"), m.p(1, "S4")),
    )
    c.eq(
        "down: the return that reaches S2",
        (down.direction, down.state, down.ret, down.distance, down.p),
        ("DOWN", "S2", D("-0.0025"), D("-0.0025"), m.p(1, "S2")),
    )
    c.eq("the actions follow the probabilities", (up.action, down.action), ("LONG", "FLAT"))
    s4 = sens(mod, "0.000000000000", "0.010000000000", 4)
    c.eq("four hours doubles the distance", (s4.neighbours[0].ret, s4.neighbours[1].ret), (D("0.005"), D("-0.005")))
    top = sens(mod, "0.050000000000", "0.010000000000", 1)
    c.eq("the highest state has no state above", ([n.direction for n in top.neighbours], top.state), (["DOWN"], "S5"))
    c.eq("and the state below begins at the edge", (top.neighbours[0].state, top.neighbours[0].ret), ("S4", D("0.00999999999900")))
    low = sens(mod, "-0.050000000000", "0.010000000000", 1)
    c.eq("the lowest state has no state below", ([n.direction for n in low.neighbours], low.state), (["UP"], "S1"))
    c.eq("and the state above begins just past the edge", (low.neighbours[0].state, low.neighbours[0].ret), ("S2", D("-0.009999999999")))
    moved = sens(mod, "0.001000000000", "0.010000000000", 1)
    c.eq(
        "a distance is from where the return was", (moved.neighbours[0].distance, moved.neighbours[1].distance), (D("0.0015"), D("-0.0035"))
    )
    c.eq("the grid", mod.GRID, D("1E-12"))
    c.eq("the directions", (mod.UP, mod.DOWN), ("UP", "DOWN"))
    c.eq("frozen", [cls.__dataclass_params__.frozen for cls in (mod.Neighbour, mod.Sensitivity)], [True, True])
    return c.bad


def check_every_state(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for ret, state, around in (
        ("-0.020000000000", "S1", [("UP", "S2")]),
        ("-0.005000000000", "S2", [("UP", "S3"), ("DOWN", "S1")]),
        ("0.000000000000", "S3", [("UP", "S4"), ("DOWN", "S2")]),
        ("0.005000000000", "S4", [("UP", "S5"), ("DOWN", "S3")]),
        ("0.020000000000", "S5", [("DOWN", "S4")]),
    ):
        s = sens(mod, ret, "0.010000000000", 1)
        c.eq(f"{state} and what is next to it", (s.state, [(n.direction, n.state) for n in s.neighbours]), (state, around))
    return c.bad


def check_actions(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    edge = rates(rules_arm, lambda h, st: (148, 77, 0) if st == "S4" else (100, 50, 0))
    c.eq("the mapping puts 0.52 in S4", edge.p(1, "S4"), D("0.52"))
    up = sens(mod, "0.000000000000", "0.010000000000", 1, mapping=edge).neighbours[0]
    c.eq("a probability exactly the edge above one half is a long", (up.p, up.action), (D("0.52"), "LONG"))
    below = rates(rules_arm, lambda h, st: (198, 101, 0) if st == "S4" else (100, 50, 0))
    up = sens(mod, "0.000000000000", "0.010000000000", 1, mapping=below).neighbours[0]
    c.eq("a hundredth below it is flat", (up.p, up.action), (D("0.51"), "FLAT"))
    thin = rates(rules_arm, lambda h, st: (10 if st == "S4" else 100, 5, 0))
    up = sens(mod, "0.000000000000", "0.010000000000", 1, mapping=thin).neighbours[0]
    c.eq("a state with too few windows behind it says nothing", (up.p, up.action), (None, None))
    return c.bad


def check_changes(mod: types.ModuleType) -> list[str]:
    """The nearest state in each direction in which the call would differ, which is not always the state next door."""
    c = Case(mod)

    def says_long_at_the_ends(h: int, st: str) -> tuple[int, int, int]:
        return (100, 90, 0) if st in ("S1", "S5") else (100, 50, 0)

    m = rates(rules_arm, says_long_at_the_ends)
    s = sens(mod, "0.000000000000", "0.010000000000", 1, mapping=m)
    c.eq(
        "the neighbours next door say what S3 says",
        [(n.direction, n.state, n.p) for n in s.neighbours],
        [("UP", "S4", D("0.5")), ("DOWN", "S2", D("0.5"))],
    )
    c.eq(
        "the nearest state in which the call would differ is two states away each way",
        [(n.direction, n.state, n.ret, n.distance, n.p, n.action) for n in s.changes],
        [
            ("UP", "S5", D("0.010000000000"), D("0.010000000000"), D("0.892157"), "LONG"),  # 91 of 102 windows: (90 + 1) / (100 + 2)
            ("DOWN", "S1", D("-0.010000000000"), D("-0.010000000000"), D("0.892157"), "LONG"),  # the edge itself is in S1
        ],
    )
    flat = rates(rules_arm, lambda h, st: (100, 50, 0))
    c.eq(
        "a mapping that says the same everywhere has no return that changes the call",
        sens(mod, "0.000000000000", "0.010000000000", 1, mapping=flat).changes,
        (),
    )
    g = sens(mod, "0.000000000000", "0.010000000000", 1)
    c.eq(
        "where the state next door differs it is the nearest change",
        [(n.direction, n.state, n.ret) for n in g.changes],
        [(n.direction, n.state, n.ret) for n in g.neighbours],
    )
    top = sens(mod, "0.050000000000", "0.010000000000", 1, mapping=m)
    c.eq(
        "from the top state, which says long, the only way is down, and S4 already says otherwise",
        [(n.direction, n.state) for n in top.changes],
        [("DOWN", "S4")],
    )
    nocall = rates(rules_arm, lambda h, st: (10, 5, 0) if st in ("S1", "S2", "S3") else (100, 50, 0))
    n = sens(mod, "0.000000000000", "0.010000000000", 1, mapping=nocall)
    c.eq(
        "a state with too few windows says nothing, and a call is a change from it",
        ([(x.direction, x.state, x.p, x.action) for x in n.changes]),
        [("UP", "S4", D("0.5"), "FLAT")],
    )
    return c.bad


def check_other_sources(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = SnapWorld()
    minute, hour = w.add(w.bars("m", "tk-btc")), w.add(w.bars("h", "tk-btc"))
    feats = w.derive("features", [minute, hour], source="hoshitori.features", fields=tuple(sorted(FEATURES.items())))
    other = w.derive("other", [minute], source="some-other-source", fields=(("x", "1"),))
    snap = w.freeze(
        snapshot,
        candidates=[snapshot.Candidate("cd-btc", "tk-btc")],
        shared=[],
        by_candidate={"cd-btc": [minute, hour, feats, other]},
        required=[],
    )
    reader = snapshot.Reader(snap, snapshot.Access.INDEPENDENT)
    got = mod.sensitivity(reader, "cd-btc", graded(rules_arm), 1)
    c.eq("a derived item of another source is not the features", got is not None and got.state, "S3")
    return c.bad


def check_none(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("two features items: the rule had no state to be in", sens(mod, "0.0", "0.010000000000", 1, extra_features=True), None)
    c.eq("no range", sens(mod, "0.000000000000", "0.000000000000", 1), None)
    fields = {k: v for k, v in FEATURES.items() if k != "ret_1h"}
    _w, reader, _e, _f = reader_with(fields, unknown=("ret_1h",))
    c.eq("an unknown return", mod.sensitivity(reader, "cd-btc", graded(rules_arm), 1), None)
    return c.bad


def check_property(mod: types.ModuleType) -> list[str]:
    """For every neighbour: the rule says its state at the return given, and does not one step of the grid away from it."""
    c = Case(mod)
    rng = random.Random(3)
    checked = 0
    for _ in range(120):
        ret = D(rng.randint(-3000, 3000)) / D(100000)
        atr = D(rng.randint(1, 300)) / D(10000)
        hours = rng.choice((1, 4, 24))
        s = sens(mod, f"{ret:.12f}", f"{atr:.12f}", hours)
        if s is None:
            continue

        def state(r: Decimal, atr: Decimal = atr, hours: int = hours) -> str:
            z = rules_arm.normalised(r, atr, hours)
            assert z is not None
            return rules_arm.state_of(z)

        for nb in s.neighbours:
            checked += 1
            away = nb.ret - mod.GRID if nb.direction == "UP" else nb.ret + mod.GRID
            if state(nb.ret) != nb.state or state(away) == nb.state:
                c.bad.append(
                    f"ret {ret} atr {atr} h {hours}: {nb.direction} {nb.state} at {nb.ret}: says {state(nb.ret)}, one step away {state(away)}"
                )
            c.eq("the distance", nb.distance, nb.ret - s.ret)
    c.eq("enough were checked", checked > 150, True)
    # the nearest state in which the call would differ, against a walk over the states with the mapping's own answers
    pick = random.Random(11)
    changed = 0
    for _ in range(150):
        cells: dict[tuple[int, str], tuple[int, int, int]] = {}
        for h in rules_arm.HORIZONS:
            for st in rules_arm.STATES:
                n = pick.choice((10, 100, 100))
                cells[(h, st)] = (n, int(n * pick.choice((0.05, 0.5, 0.5, 0.5, 0.9))), 0)
        mapping = rates(rules_arm, lambda h, st, cells=cells: cells[(h, st)])
        ret = D(pick.randint(-3000, 3000)) / D(100000)
        atr = D(pick.randint(1, 300)) / D(10000)
        hours = pick.choice((1, 4, 24))
        s = sens(mod, f"{ret:.12f}", f"{atr:.12f}", hours, mapping=mapping)
        if s is None:
            continue
        at = rules_arm.STATES.index(s.state)
        said = (mapping.p(hours, s.state), mod.action_of(mapping.p(hours, s.state)))
        want: list[tuple[str, str]] = []
        for direction, order in (("UP", range(at + 1, len(rules_arm.STATES))), ("DOWN", range(at - 1, -1, -1))):
            for k in order:
                p = mapping.p(hours, rules_arm.STATES[k])
                if (p, mod.action_of(p)) != said:
                    want.append((direction, rules_arm.STATES[k]))
                    break
        got = [(n.direction, n.state) for n in s.changes]
        changed += len(got)
        if got != want:
            c.bad.append(f"ret {ret} atr {atr} h {hours} from {s.state}: the nearest change is {got}, a walk over the states gives {want}")
    c.eq("some changes were found, so the walk was not vacuous", changed > 60, True)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (
        check_known,
        check_every_state,
        check_actions,
        check_changes,
        check_other_sources,
        check_none,
        check_property,
    )
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
