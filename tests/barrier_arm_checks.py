"""Checks of the rules arm's barrier decision (``hoshitori.barrier_arm``), runnable against a mutant."""

from __future__ import annotations

import hashlib
import types
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from hoshitori import barrier_rates, canonical, snapshot
from hoshitori.rules_arm import Reason, RulesError, Verdict
from tests.rules_arm_checks import FEATURES, SnapWorld, reader_with

WITH_ATR = {**FEATURES, "atr14": "10.000000000000"}


def mapping(target: int = 30, stop: int = 30, timeout: int = 40, ambiguous: int = 0, venue: str = "binance-spot") -> Any:
    cells = tuple((h, barrier_rates.Cell(target, stop, timeout, ambiguous)) for h in barrier_rates.HORIZONS)
    return barrier_rates.BarrierRates(cells, venue, "USDT", ("BTCUSDT",), 0, 1, 0)


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
        except RulesError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def check_decisions(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    m = mapping()
    w, reader, _everything, feats = reader_with(WITH_ATR)
    d = mod.decide(reader, "cd-btc", m, 4)
    parents = sorted(p for p, _ in w.store.get(feats).derived_from)
    used = (feats, *parents)
    c.eq(
        "a call: the mapped probabilities and the ATR",
        (d.candidate_id, d.horizon_hours, d.verdict, d.reason, d.p, d.atr),
        ("cd-btc", 4, Verdict.CALL, None, (Decimal("0.3"), Decimal("0.3"), Decimal("0.4")), Decimal("10")),
    )
    c.eq("the evidence it used: the features and the two series", d.evidence_ids, used)
    c.eq("the hash of exactly what it read", d.observation_set_hash, reader.set_hash("cd-btc", used))
    c.eq("the mapping that gave the probabilities", d.mapping_sha256, m.sha256)
    c.eq("the invalidation", d.invalidation, ("close below entry minus 1.5 ATR14",))
    for hours in barrier_rates.HORIZONS:
        c.eq(f"horizon {hours}", mod.decide(reader, "cd-btc", m, hours).horizon_hours, hours)
    # too few windows
    thin = mapping(30, 30, 39)
    d = mod.decide(reader_with(WITH_ATR)[1], "cd-btc", thin, 1)
    c.eq(
        "too few settled windows: a voluntary no call that still says what it read",
        (d.verdict, d.reason, d.p, d.atr, d.invalidation, len(d.evidence_ids)),
        (Verdict.NO_CALL, Reason.UNMAPPED, None, Decimal("10"), (), 3),
    )
    # no range
    for label, atr in (("zero", "0.000000000000"), ("negative", "-1")):
        d = mod.decide(reader_with({**FEATURES, "atr14": atr})[1], "cd-btc", m, 1)
        c.eq(
            f"a {label} ATR is no range",
            (d.verdict, d.reason, d.p, d.atr, d.invalidation, len(d.evidence_ids)),
            (Verdict.NO_CALL, Reason.NO_RANGE, None, None, (), 3),
        )
    small = mod.decide(reader_with({**FEATURES, "atr14": "0.000001"})[1], "cd-btc", m, 1)
    c.eq("a small positive ATR is a range", (small.verdict, small.atr), (Verdict.CALL, Decimal("0.000001")))
    # unknown
    d = mod.decide(reader_with(FEATURES, unknown=("atr14",))[1], "cd-btc", m, 1)
    c.eq(
        "an unknown ATR is failed evidence",
        (d.verdict, d.reason, d.p, d.atr, d.invalidation, len(d.evidence_ids)),
        (Verdict.FAILED_EVIDENCE, Reason.FEATURE_UNKNOWN, None, None, (), 3),
    )
    for bad in ("x", "NaN", "Infinity", "-Infinity"):
        d = mod.decide(reader_with({**FEATURES, "atr14": bad})[1], "cd-btc", m, 1)
        c.eq(f"an ATR of {bad!r} is unknown", (d.verdict, d.reason), (Verdict.FAILED_EVIDENCE, Reason.FEATURE_UNKNOWN))
    # features items
    sw = SnapWorld()
    bare = snapshot.Reader(
        sw.freeze(
            snapshot, candidates=[snapshot.Candidate("cd-btc", "tk-btc")], shared=[], by_candidate={"cd-btc": [sw.btc_bars]}, required=[]
        ),
        snapshot.Access.INDEPENDENT,
    )
    d = mod.decide(bare, "cd-btc", m, 4)
    c.eq(
        "no features item: failed evidence, nothing read",
        (d.verdict, d.reason, d.evidence_ids, d.observation_set_hash),
        (Verdict.FAILED_EVIDENCE, Reason.NO_FEATURES, (), bare.set_hash("cd-btc", [])),
    )
    d = mod.decide(reader_with(WITH_ATR, extra_features=True)[1], "cd-btc", m, 4)
    c.eq("two features items: failed evidence", (d.verdict, d.reason), (Verdict.FAILED_EVIDENCE, Reason.NO_FEATURES))
    w2 = SnapWorld()
    a, b = w2.add(w2.bars("m", "tk-btc")), w2.add(w2.bars("h", "tk-btc"))
    impostor = w2.add(w2.bars("impostor", "tk-btc", source="hoshitori.features", fields=tuple(sorted(WITH_ATR.items()))))
    other = w2.derive("other", [a, b], fields=tuple(sorted(WITH_ATR.items())))
    for label, listed in (("an observation that calls itself features", [a, b, impostor]), ("another derived item", [a, b, other])):
        snap = w2.freeze(
            snapshot, candidates=[snapshot.Candidate("cd-btc", "tk-btc")], shared=[], by_candidate={"cd-btc": listed}, required=[]
        )
        d = mod.decide(snapshot.Reader(snap, snapshot.Access.INDEPENDENT), "cd-btc", m, 4)
        c.eq(f"{label} is not the features", (d.verdict, d.reason), (Verdict.FAILED_EVIDENCE, Reason.NO_FEATURES))
    c.refuses("a horizon the arm does not have", lambda: mod.decide(reader, "cd-btc", m, 2), "the horizon is one of (1, 4, 24) hours")
    c.eq(
        "the independent reader gave it nothing but observations",
        all(i.kind.value != "INTERPRETATION" for i in reader.items("cd-btc")),
        True,
    )
    return c.bad


def check_version(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    m = mapping()
    want = hashlib.sha256(
        canonical.dumps(
            {"arm": "L0", "contract": "B", "rule_version": "1", "mapping": m.sha256, "min_cell": 100, "k_target": "1.5", "k_stop": "1.5"}
        )
    ).hexdigest()
    c.eq("the hash of the rule, its constants and the mapping", mod.agent_version_hash(m), want)
    c.eq("another mapping is another version", mod.agent_version_hash(mapping(venue="other")) != want, True)
    c.eq("constants", (mod.ARM, mod.CONTRACT), ("L0", "B"))
    c.eq("BarrierDecision is frozen", mod.BarrierDecision.__dataclass_params__.frozen, True)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_decisions, check_version):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1000:]}")
    return bad
