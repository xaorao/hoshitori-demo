"""Checks of the gap numbers (``hoshitori.gap_record``), runnable against a mutant."""

from __future__ import annotations

import types
from decimal import Decimal
from typing import Any

from tests import view_models_checks as world


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def _ms(text: str) -> int:
    """A millisecond instant read a second way from ``YYYY-MM-DDTHH:MM:SS.mmmZ``."""
    from datetime import UTC, datetime

    moment = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    return (moment - datetime(1970, 1, 1, tzinfo=UTC)) // __import__("datetime").timedelta(milliseconds=1)


def _second_way(records: list[dict[str, Any]]) -> dict[str, tuple[int | None, Decimal | None]]:
    origin: dict[str, int] = {}
    for r in records:
        if r["type"] == "forecast_state" and r["body"].get("origin") is not None:
            origin.setdefault(r["body"]["forecast_id"], _ms(r["body"]["origin"]))
    features = {r["id"]: r["body"]["fields"] for r in records if r["type"] == "evidence" and r["body"].get("path") == "features"}
    p0 = {r["body"]["forecast_id"]: r["body"].get("p0") for r in records if r["type"] == "settlement"}
    out: dict[str, tuple[int | None, Decimal | None]] = {}
    for r in records:
        if r["type"] != "forecast":
            continue
        fid, body = r["id"], r["body"]
        age = origin[fid] - _ms(body["as_of"]) if fid in origin else None
        drift = None
        for ident in body["evidence_ids"]:
            if ident in features and p0.get(fid) is not None:
                atr = Decimal(features[ident]["atr14"])
                if atr > 0:
                    drift = (abs(Decimal(p0[fid]) - Decimal(features[ident]["close"])) / atr).quantize(Decimal("1e-20"))
        out[fid] = (age, drift)
    return out


def check_gaps(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    records, *_ = world.extended()
    got = mod.gaps(records)
    want = _second_way(records)
    c.eq("the same forecasts", sorted(got), sorted(want))
    c.eq(
        "age and drift of every forecast (the drift to twenty places)",
        {f: (g.age_ms, None if g.drift_atr is None else g.drift_atr.quantize(Decimal("1e-20"))) for f, g in got.items()},
        want,
    )
    c.eq(
        "some forecast has a drift and some do not",
        (any(v[1] is not None for v in want.values()), any(v[1] is None for v in want.values())),
        (True, True),
    )
    c.eq("the age is positive wherever it is known", all(v[0] is None or v[0] > 0 for v in want.values()), True)
    # a settlement with no reference price, or features with an ATR of zero, give no drift (never zero)
    no_p0 = [dict(r, body={**r["body"], "p0": None}) if r["type"] == "settlement" else r for r in records]
    c.eq("without a reference price there is no drift", all(g.drift_atr is None for g in mod.gaps(no_p0).values()), True)
    flat = [
        dict(r, body={**r["body"], "fields": {**r["body"]["fields"], "atr14": "0"}})
        if r["type"] == "evidence" and r["body"].get("path") == "features"
        else r
        for r in records
    ]
    c.eq("an ATR of zero gives no drift", all(g.drift_atr is None for g in mod.gaps(flat).values()), True)
    odd = [
        dict(r, body={**r["body"], "fields": {**r["body"]["fields"], "close": "x"}})
        if r["type"] == "evidence" and r["body"].get("path") == "features"
        else r
        for r in records
    ]
    c.eq("a close that is not a number gives no drift", all(g.drift_atr is None for g in mod.gaps(odd).values()), True)
    other = [
        dict(r, body={**r["body"], "source": "somewhere"}) if r["type"] == "evidence" and r["body"].get("path") == "features" else r
        for r in records
    ]
    c.eq("features from another source are not read", all(g.drift_atr is None for g in mod.gaps(other).values()), True)
    c.eq("a gap is a value", mod.Gap.__dataclass_params__.frozen, True)
    c.eq("sixty digits", mod.PRECISION, 60)

    def with_features(**fields: str) -> list[dict[str, Any]]:
        return [
            dict(r, body={**r["body"], "fields": {**r["body"]["fields"], **fields}})
            if r["type"] == "evidence" and r["body"].get("path") == "features"
            else r
            for r in records
        ]

    small = mod.gaps(with_features(close="1", atr14="0.5"))
    p0s = {
        r["body"]["forecast_id"]: Decimal(r["body"]["p0"]) for r in records if r["type"] == "settlement" and r["body"].get("p0") is not None
    }
    c.eq(
        "an ATR below one is an ATR",
        {f: g.drift_atr for f, g in small.items() if g.drift_atr is not None},
        {f: abs(p - 1) / Decimal("0.5") for f, p in p0s.items() if f in small},
    )
    long_ = mod.gaps(with_features(close="1", atr14="7"))
    from decimal import localcontext

    with localcontext() as ctx:
        ctx.prec = 60
        exact = {f: abs(p - 1) / Decimal(7) for f, p in p0s.items()}
    c.eq(
        "a repeating quotient is carried to sixty digits",
        {f: g.drift_atr for f, g in long_.items() if g.drift_atr is not None},
        {f: v for f, v in exact.items() if f in long_},
    )
    stamped = [dict(r, committed_at="2026-01-01T00:00:00.000Z") if r["type"] == "forecast_state" else r for r in records]
    c.eq(
        "the time the ledger wrote is not part of a state",
        {f: g.age_ms for f, g in mod.gaps(stamped).items()},
        {f: g.age_ms for f, g in got.items()},
    )
    bad_time = [dict(r, body={**r["body"], "as_of": "soon"}) if r["type"] == "forecast" else r for r in records]
    c.eq("an evidence time that cannot be read gives no age", all(g.age_ms is None for g in mod.gaps(bad_time).values()), True)
    return c.bad


def check_strata(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    D = Decimal
    c.eq("the group size to fill", mod.MIN_PER_STRATUM, 3)
    c.eq("too few values", mod.tercile_cuts([1, 2, 3, 4, 5, 6, 7, 8]), None)
    c.eq("nine values", mod.tercile_cuts([9, 1, 8, 2, 7, 3, 6, 4, 5]), (4, 7))
    c.eq("twelve values", mod.tercile_cuts(list(range(12, 0, -1))), (5, 9))
    c.eq("decimals", mod.tercile_cuts([D(n) / 10 for n in range(9)]), (D("0.3"), D("0.6")))
    cuts = (4, 7)
    c.eq("the groups", [mod.stratum(v, cuts) for v in (1, 3, 4, 5, 6, 7, 8, 100)], [0, 0, 1, 1, 1, 2, 2, 2])
    c.eq("every third has a third of nine", sorted(mod.stratum(v, (4, 7)) for v in range(1, 10)), [0, 0, 0, 1, 1, 1, 2, 2, 2])
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_gaps, check_strata):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
