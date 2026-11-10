"""Checks of the risk kernel (``hoshitori.risk_kernel``), runnable against a mutant: worked cases for every cap and for the ladder, the
refusals, and results that do not depend on the caller's decimal context. The properties (never raises, idempotent, order-free, limits
hold) are tested with hypothesis in ``test_risk_kernel``."""

from __future__ import annotations

import types
from collections.abc import Callable
from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import Any

D = Decimal


def contexts() -> dict[str, Context]:
    """Callers' decimal contexts other than the default: six, nine and 28 digits, each rounding half-even, toward minus infinity and toward
    plus infinity, with and without a trap on an inexact result; and one that traps nothing."""
    out: dict[str, Context] = {}
    for prec in (6, 9, 28):
        for name, rounding in (
            ("half-even", ROUND_HALF_EVEN),
            ("toward minus infinity", ROUND_FLOOR),
            ("toward plus infinity", ROUND_CEILING),
        ):
            for inexact in (False, True):
                if prec == 28 and rounding == ROUND_HALF_EVEN and not inexact:
                    continue  # the default context itself
                traps = [InvalidOperation, DivisionByZero, Overflow, *([Inexact] if inexact else [])]
                out[f"{prec} digits rounding {name}{' trapping Inexact' if inexact else ''}"] = Context(
                    prec=prec, rounding=rounding, traps=traps
                )
    out["28 digits trapping nothing"] = Context(traps=[])
    return out


def outcome(call: Callable[[], object]) -> str:
    """What a call gives, written out exactly (a decimal's digits and exponent count), or what it raised."""
    try:
        return repr(call())
    except TimeoutError:
        raise
    except Exception as exc:
        return f"raises {type(exc).__name__}: {exc}"


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
        except self.mod.RiskError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def weights(result: Any) -> dict[str, str]:
    return {k: format(v, "f") for k, v in result.weights.items()}


def bound(result: Any) -> dict[str, tuple[str, ...]]:
    return {s.symbol: s.bound for s in result.shrinks}


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("caps in the order they are named", mod.CAPS, ("LADDER", "TOKEN", "GROSS", "NET", "CLUSTER"))
    limits = mod.Limits()
    c.eq("the priors", (limits.per_token, limits.gross, limits.net, limits.cluster), (D("0.05"), D("0.30"), D("0.20"), D("0.10")))
    c.eq("the ladder", (limits.halve_at, limits.flat_at, limits.halt_at), (D("0.05"), D("0.075"), D("0.12")))
    c.eq("modes", [m.value for m in mod.Mode], ["ACTIVE", "HALVED", "FLAT", "HALTED"])
    for cls in (mod.Limits, mod.Shrink, mod.Kernel):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    own = getattr(mod, "_CTX", None)
    c.eq(
        "the kernel's own decimal context: sixty digits, half-even",
        None if own is None else (own.prec, own.rounding),
        (60, ROUND_HALF_EVEN),
    )
    return c.bad


def check_caps(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    limits = mod.Limits()
    one = mod.shrink({"A": D("0.10")}, limits)
    c.eq(
        "one token is cut to the per-token cap",
        (weights(one), bound(one), one.mode),
        ({"A": "0.050000"}, {"A": ("TOKEN",)}, mod.Mode.ACTIVE),
    )
    c.eq("the record keeps the request and the result", (one.shrinks[0].requested, one.shrinks[0].applied), (D("0.10"), D("0.050000")))
    fits = mod.shrink({"A": D("0.03")}, limits)
    c.eq("a target inside every limit is left as it is", (weights(fits), bound(fits)), ({"A": "0.030000"}, {"A": ()}))
    # six longs: the net cap (0.20) lets four through at 0.05 and nothing more
    longs = mod.shrink({s: D("0.05") for s in "ABCDEF"}, limits)
    c.eq(
        "six longs: the net cap lets four through",
        weights(longs),
        {"A": "0.050000", "B": "0.050000", "C": "0.050000", "D": "0.050000", "E": "0", "F": "0"},
    )
    c.eq("and names it, and not the gross cap, which still has room", bound(longs)["E"], ("NET",))
    # the net cap is signed: longs and shorts offset, the gross cap (0.30) binds first
    mixed = {
        "A": D("0.05"),
        "B": D("-0.05"),
        "C": D("0.05"),
        "D": D("-0.05"),
        "E": D("0.05"),
        "F": D("-0.05"),
        "G": D("0.05"),
        "H": D("-0.05"),
    }
    got = mod.shrink(mixed, limits)
    c.eq("eight tokens of 0.05, half short: six get through (gross 0.30)", sum(1 for v in got.weights.values() if v), 6)
    c.eq("the first six by symbol, the last two are cut to nothing", [k for k, v in got.weights.items() if not v], ["G", "H"])
    c.eq("it is the gross cap that bound them", (bound(got)["G"], bound(got)["H"]), (("GROSS",), ("GROSS",)))
    shorts = mod.shrink({s: D("-0.05") for s in "ABCDEF"}, limits)
    c.eq(
        "six shorts: the net cap works for them too",
        weights(shorts),
        {"A": "-0.050000", "B": "-0.050000", "C": "-0.050000", "D": "-0.050000", "E": "0", "F": "0"},
    )
    both = mod.shrink({"A": D("0.05"), "B": D("0.05"), "C": D("0.05"), "D": D("0.05"), "E": D("-0.05")}, limits)
    c.eq("a short is welcome where the longs are full: net room opens", weights(both)["E"], "-0.050000")
    # clusters
    cl = mod.shrink({"A": D("0.04"), "B": D("0.04"), "C": D("0.04"), "X": D("0.04")}, limits, clusters=[{"A", "B", "C"}])
    c.eq(
        "a cluster of three of 0.04 is cut to 0.10 in all; a token outside it is not",
        weights(cl),
        {"A": "0.040000", "B": "0.040000", "C": "0.020000", "X": "0.040000"},
    )
    c.eq("and the cluster is named for the one that was cut", bound(cl), {"A": (), "B": (), "C": ("CLUSTER",), "X": ()})
    full = mod.shrink({"A": D("0.05"), "B": D("0.05"), "C": D("0.05")}, limits, clusters=[("A", "B", "C")])
    c.eq("a cluster that is full takes no more", (weights(full)["C"], bound(full)["C"]), ("0", ("CLUSTER",)))
    two = mod.shrink({"A": D("0.05"), "B": D("0.05"), "C": D("0.05"), "D": D("0.05")}, limits, clusters=[{"A", "B"}, {"C", "D"}])
    c.eq("two clusters each have their own room", sum(v for v in two.weights.values()), D("0.2"))
    c.eq(
        "a long and a short of one cluster add up in gross, not net",
        weights(mod.shrink({"A": D("0.05"), "B": D("-0.05"), "C": D("0.02")}, limits, clusters=[{"A", "B", "C"}]))["C"],
        "0",
    )
    # order: the largest first, then by symbol, whatever the order they came in
    a = mod.shrink({"B": D("0.05"), "A": D("0.05"), "C": D("0.05"), "D": D("0.05"), "E": D("0.06")}, limits)
    b = mod.shrink(dict(reversed(list({"B": D("0.05"), "A": D("0.05"), "C": D("0.05"), "D": D("0.05"), "E": D("0.06")}.items()))), limits)
    c.eq("the largest first, and then by symbol", [s.symbol for s in a.shrinks], ["E", "A", "B", "C", "D"])
    c.eq("and the result does not depend on the order listed", (weights(a), a.shrinks), (weights(b), b.shrinks))
    c.eq("the weights are listed by symbol", list(a.weights), ["A", "B", "C", "D", "E"])
    c.eq(
        "a token of zero stays zero and binds nothing",
        (weights(mod.shrink({"A": D("0")}, limits)), bound(mod.shrink({"A": D("-0")}, limits))),
        ({"A": "0"}, {"A": ()}),
    )
    # a long cut by the net cap leaves room to its opposite, and the opposite is then cut by the room the long really took: found by hypothesis
    tight = mod.Limits(D("0.02"), D("0.03"), D("0.01"), D("0.01"), D("0.01"), D("0.02"), D("0.03"))
    pair = mod.shrink({"A": D("0.010001"), "B": D("-0.010001")}, tight)
    c.eq("a long and a short cut until a second walk changes nothing", weights(pair), {"A": "0.010000", "B": "-0.010000"})
    c.eq("and both name the net cap", bound(pair), {"A": ("NET",), "B": ("NET",)})
    c.eq("which is a fixed point", mod.shrink(pair.weights, tight).weights, pair.weights)
    # rounding is toward zero, and it is not a binding cap
    r = mod.shrink({"A": D("0.0499999999"), "B": D("-0.0499999999")}, limits)
    c.eq("six places, toward zero, for either sign", (weights(r), bound(r)), ({"A": "0.049999", "B": "-0.049999"}, {"A": (), "B": ()}))
    return c.bad


def check_ladder(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    limits = mod.Limits()
    targets = {"A": D("0.04"), "B": D("-0.03")}
    for drawdown, mode, a in (
        ("0", "ACTIVE", "0.040000"),
        ("0.049999", "ACTIVE", "0.040000"),
        ("0.05", "HALVED", "0.020000"),
        ("0.074999", "HALVED", "0.020000"),
        ("0.075", "FLAT", "0"),
        ("0.119999", "FLAT", "0"),
        ("0.12", "HALTED", "0"),
        ("0.5", "HALTED", "0"),
    ):
        got = mod.shrink(targets, limits, drawdown=D(drawdown))
        c.eq(f"drawdown {drawdown}", (got.mode.value, weights(got)["A"]), (mode, a))
    half = mod.shrink(targets, limits, drawdown=D("0.06"))
    c.eq("halved sizes keep their signs", weights(half), {"A": "0.020000", "B": "-0.015000"})
    c.eq("and the ladder is named as what bound them", bound(half), {"A": ("LADDER",), "B": ("LADDER",)})
    c.eq(
        "a flat arm binds on the ladder for every token",
        bound(mod.shrink(targets, limits, drawdown=D("0.08"))),
        {"A": ("LADDER",), "B": ("LADDER",)},
    )
    c.eq(
        "a halted arm stays flat whatever the drawdown",
        (mod.shrink(targets, limits, halted=True).mode.value, weights(mod.shrink(targets, limits, halted=True))),
        ("HALTED", {"A": "0", "B": "0"}),
    )
    c.eq(
        "the mode of a drawdown",
        [mod.mode_of(D(x), limits).value for x in ("0", "0.05", "0.075", "0.12")],
        ["ACTIVE", "HALVED", "FLAT", "HALTED"],
    )
    c.eq("and of a halted arm at no drawdown", mod.mode_of(D("0"), limits, halted=True).value, "HALTED")
    custom = mod.Limits(halve_at=D("0.01"), flat_at=D("0.02"), halt_at=D("0.03"))
    c.eq(
        "the thresholds are the limits' own",
        [mod.mode_of(D(x), custom).value for x in ("0.009", "0.01", "0.02", "0.03")],
        ["ACTIVE", "HALVED", "FLAT", "HALTED"],
    )
    return c.bad


def check_ladder_is_monotone(mod: types.ModuleType) -> list[str]:
    """A deeper rung never lets more through than a lighter one: the halved result is the ACTIVE result, halved, token by
    token, so a halved arm is half of what the arm would hold, and a cap that binds in either does not make the halved one larger."""
    import random

    c = Case(mod)
    limits = mod.Limits()
    big = mod.shrink({"A": D("0.30")}, limits, drawdown=D("0.06"))
    c.eq("a request above the per-token cap, halved, is half of what the active arm would hold", weights(big), {"A": "0.025000"})
    c.eq(
        "and it is named as bound by the ladder and by the caps that bound the active result", bound(big), {"A": ("LADDER", "TOKEN", "NET")}
    )
    rng = random.Random(9)
    symbols = ["A", "B", "C", "D", "E", "F"]
    for case in range(400):
        names = rng.sample(symbols, rng.randint(1, 6))
        targets = {n: D(rng.randint(-500_000, 500_000)) / D(1_000_000) for n in names}
        clusters = [names[: len(names) // 2]] if rng.random() < 0.5 and len(names) > 1 else []
        active = mod.shrink(targets, limits, drawdown=D("0.01"), clusters=clusters)
        halved = mod.shrink(targets, limits, drawdown=D("0.06"), clusters=clusters)
        gross_active, gross_halved = (sum(abs(w) for w in r.weights.values()) for r in (active, halved))
        net_active, net_halved = (abs(sum(r.weights.values())) for r in (active, halved))
        for symbol in names:
            a, h = active.weights[symbol], halved.weights[symbol]
            if abs(h) != abs(a) / 2 // D("0.000001") * D("0.000001") or (h != 0 and (h < 0) != (a < 0)):
                c.bad.append(f"case {case}: {symbol} halved is {h}, the active result is {a}")
                break
        else:
            if gross_halved > gross_active:
                c.bad.append(f"case {case}: the halved arm holds more gross ({gross_halved}) than the active one ({gross_active})")
            if net_halved > net_active:
                c.bad.append(f"case {case}: the halved arm holds more net ({net_halved}) than the active one ({net_active})")
        if len(c.bad) > 3:
            break
    twice = mod.shrink(halved.weights, limits, drawdown=D("0.06"), clusters=clusters)
    c.eq(
        "a halved result put through the halved rung again is halved again",
        abs(sum(twice.weights.values())) <= net_halved / 2 + D("0.000001"),
        True,
    )
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    limits = mod.Limits()
    says = "a target is a symbol and a finite weight"
    c.refuses("a weight that is not a decimal", lambda: mod.shrink({"A": 0.05}, limits), says)
    c.refuses("a weight that is not a number", lambda: mod.shrink({"A": D("NaN")}, limits), says)
    c.refuses("an infinite weight", lambda: mod.shrink({"A": D("Infinity")}, limits), says)
    c.refuses("an empty symbol", lambda: mod.shrink({"": D("0.01")}, limits), says)
    c.refuses("a symbol that is not text", lambda: mod.shrink({5: D("0.01")}, limits), says)
    c.refuses(
        "a token in two clusters",
        lambda: mod.shrink({"A": D("0.01")}, limits, clusters=[{"A"}, {"A", "B"}]),
        "a token is in one cluster at most",
    )
    c.refuses("a negative drawdown", lambda: mod.shrink({"A": D("0.01")}, limits, drawdown=D("-0.01")), "a drawdown is zero or more")
    c.refuses("a drawdown that is not a decimal", lambda: mod.mode_of(0.1, limits), "a drawdown is zero or more")  # type: ignore[arg-type]
    c.refuses("a drawdown that is not a number", lambda: mod.mode_of(D("NaN"), limits), "a drawdown is zero or more")
    share = " is a share of equity above zero and at most one"
    for name in ("per_token", "gross", "net", "cluster", "halve_at", "flat_at", "halt_at"):
        for bad in (D("0"), D("-0.1"), D("1.01"), 0.05, D("NaN")):
            c.refuses(f"{name} of {bad!r}", lambda name=name, bad=bad: mod.Limits(**{name: bad}), name + share)
    c.eq("a limit of exactly one is a limit", mod.Limits(per_token=D("1")).per_token, D("1"))
    c.refuses("a ladder that does not rise", lambda: mod.Limits(halve_at=D("0.08")), "the ladder rises: halve, then flatten, then halt")
    c.refuses("a ladder with equal steps", lambda: mod.Limits(flat_at=D("0.05")), "the ladder rises: halve, then flatten, then halt")
    c.refuses(
        "a ladder whose end is the middle", lambda: mod.Limits(halt_at=D("0.075")), "the ladder rises: halve, then flatten, then halt"
    )
    return c.bad


def check_contexts(mod: types.ModuleType) -> list[str]:
    """The same targets give the same weights and record whatever decimal context the caller has (``SIMULATOR_SPEC`` section 4: a pure
    function): six or nine digits, rounding toward either infinity, a trap on an inexact result, or no trap at all, each against the default
    context. At six digits ``abs`` and ``-`` round, so a request could be halved to more than half, sorted out of its order, or granted
    more than it asked for."""
    c = Case(mod)
    limits = mod.Limits()
    gross = mod.Limits(D("0.05"), D("0.06"), D("0.06"), D("0.06"))
    tight = mod.Limits(D("0.02"), D("0.03"), D("0.01"), D("0.01"), D("0.01"), D("0.02"), D("0.03"))
    operations: dict[str, Callable[[], object]] = {
        "0.0312359998 at a drawdown of 6 %": lambda: mod.shrink({"X": D("0.0312359998")}, limits, drawdown=D("0.06")),
        "-0.0312359998 at a drawdown of 6 %": lambda: mod.shrink({"X": D("-0.0312359998")}, limits, drawdown=D("0.06")),
        "0.04999995 and 0.05 under a gross cap of 0.06": lambda: mod.shrink({"A": D("0.04999995"), "B": D("0.05")}, gross),
        "a long and a short of seven digits under a net cap of 0.01": lambda: mod.shrink(
            {"A": D("0.0100001"), "B": D("-0.0100009")}, tight
        ),
        "longs and a short in a cluster": lambda: mod.shrink(
            {"A": D("0.0400001"), "B": D("-0.0399999"), "C": D("0.04"), "X": D("0.0499999999")}, limits, clusters=[{"A", "B", "C"}]
        ),
        "the rung of a drawdown of 0.0499999999": lambda: mod.mode_of(D("0.0499999999"), limits),
    }

    def results() -> dict[str, str]:
        return {name: outcome(call) for name, call in operations.items()}

    with localcontext(Context()):
        want = results()
        c.eq(
            "in the default context: half of 0.0312359998, toward zero, and the larger request first",
            (
                weights(operations["0.0312359998 at a drawdown of 6 %"]()),
                weights(operations["0.04999995 and 0.05 under a gross cap of 0.06"]()),
            ),
            ({"X": "0.015617"}, {"A": "0.010000", "B": "0.050000"}),
        )
    for label, ctx in contexts().items():
        with localcontext(ctx):
            got = results()
        for name, value in want.items():
            c.eq(f"{name}, in a caller's context of {label}", got[name], value)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_caps, check_ladder, check_ladder_is_monotone, check_refusals, check_contexts):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1000:]}")
    return bad
