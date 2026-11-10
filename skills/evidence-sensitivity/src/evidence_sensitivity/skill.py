"""The skill: validate, decide the baseline call, re-decide under each intervention, and return the RYO-style envelope.

The rule is one a reader can recompute by hand: the weighted mean of the available signals, LONG when it is at least the threshold,
SHORT when it is at most minus the threshold, otherwise NO_CALL; fewer available items than ``min_evidence`` is NO_CALL. Arithmetic is
exact (fractions).

* An item whose signal is null counts for nothing and is listed; whether a value it might have had could have changed the call is reported.
* The result is decision sensitivity of this rule on these inputs, not whether the evidence is true or what the market does.
* Nothing is fetched, so ``data_mode`` is ``unknown``: the skill cannot know where the evidence came from.
"""

from __future__ import annotations

import datetime as dt
import itertools
from fractions import Fraction
from typing import Any

from .schema import TOOL_NAME, validate_input

SCHEMA_VERSION = "1.0"
EXACT_LIMIT = 16  # up to this many available items the smallest sufficient set is found by trying every subset
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
NOT_ABOUT = (
    "This is how the rule responds to the evidence supplied, not whether the evidence is true, "
    "why any model decided, or what the market will do."
)
NOT_FETCHED = "Computed from the evidence you supplied; nothing was fetched, so its origin is unknown."

type Item = tuple[str, Fraction | None, Fraction, str | None]


def _num(x: Fraction | None) -> float | None:
    return None if x is None else round(float(x), 6)


def _call(items: list[tuple[Fraction, Fraction]], threshold: Fraction, minimum: int) -> tuple[Fraction | None, str]:
    """The score and action of (signal, weight) pairs."""
    total = sum((w for _, w in items), Fraction(0))
    if len(items) < minimum or total == 0:
        return None, "NO_CALL"
    score = sum((s * w for s, w in items), Fraction(0)) / total
    return score, "LONG" if score >= threshold else "SHORT" if score <= -threshold else "NO_CALL"


def _available(items: list[Item]) -> list[tuple[str, Fraction, Fraction, str | None]]:
    return [(i, s, w, g) for i, s, w, g in items if s is not None]


def _outcome(score: Fraction | None, action: str, available: int, baseline: str) -> dict[str, Any]:
    return {"score": _num(score), "action": action, "available": available, "changed": action != baseline}


def _flip(score: Fraction, total: Fraction, signal: Fraction, weight: Fraction, threshold: Fraction, action: str) -> dict[str, Any] | None:
    """How far this item's signal would have to move, the others unchanged, for the action to change (strictly past the boundary)."""
    if weight == 0:
        return None
    rest = score * total - weight * signal
    upper = (threshold * total - rest) / weight  # at or above: LONG
    lower = (-threshold * total - rest) / weight  # at or below: SHORT
    options: list[tuple[Fraction, str, Fraction]] = []
    if action == "LONG" and upper > -1:
        options.append((signal - upper, "below", upper))
    elif action == "SHORT" and lower < 1:
        options.append((lower - signal, "above", lower))
    elif action == "NO_CALL":
        if upper <= 1:
            options.append((upper - signal, "at or above", upper))
        if lower >= -1:
            options.append((signal - lower, "at or below", lower))
    if not options:
        return None
    distance, relation, boundary = min(options, key=lambda o: (o[0], o[1]))
    return {"signal_must_be": relation, "boundary": _num(boundary), "distance": _num(distance)}


def _could_change(rest: list[tuple[Fraction, Fraction]], weight: Fraction, threshold: Fraction, minimum: int, baseline: str) -> bool:
    return any(_call([*rest, (Fraction(x), weight)], threshold, minimum)[1] != baseline for x in (-1, 1))


def _sufficient(
    avail: list[tuple[str, Fraction, Fraction, str | None]], threshold: Fraction, minimum: int, baseline: str
) -> dict[str, Any] | None:
    if baseline == "NO_CALL":
        return None
    keyed = sorted(avail, key=lambda a: (-abs(a[1] * a[2]), a[0]))
    exact = len(keyed) <= EXACT_LIMIT
    best: tuple[str, ...] | None = None
    if exact:
        for size in range(max(minimum, 1), len(keyed) + 1):
            hits = [
                c for c in itertools.combinations(keyed, size) if _call([(s, w) for _, s, w, _ in c], threshold, minimum)[1] == baseline
            ]
            if hits:
                best = min(((tuple(i for i, *_ in c), -sum(abs(s * w) for _, s, w, _ in c)) for c in hits), key=lambda t: (t[1], t[0]))[0]
                break
    else:
        chosen: list[tuple[str, Fraction, Fraction, str | None]] = []
        for a in keyed:
            chosen.append(a)
            if _call([(s, w) for _, s, w, _ in chosen], threshold, minimum)[1] == baseline:
                best = tuple(i for i, *_ in chosen)
                break
    if best is None:
        return None
    return {
        "ids": sorted(best),
        "size": len(best),
        "method": "every subset tried" if exact else "greedy by weight times signal; may not be the smallest",
    }


def _label(action: str, signal: Fraction | None, required: bool) -> str:
    if signal is None:
        return "MISSING"
    if required:
        return "REQUIRED"
    if signal == 0:
        return "NEUTRAL"
    if action == "NO_CALL":
        return "LEANS_UP" if signal > 0 else "LEANS_DOWN"
    return "SUPPORTING" if (signal > 0) == (action == "LONG") else "OPPOSING"


def run(args: object, *, now: dt.datetime | None = None) -> dict[str, Any]:
    a = validate_input(args)
    items: list[Item] = a["items"]
    threshold: Fraction = a["threshold"]
    minimum: int = a["min_evidence"]
    now = now or dt.datetime.now(dt.UTC)
    avail = _available(items)
    pairs = [(s, w) for _, s, w, _ in avail]
    score, action = _call(pairs, threshold, minimum)
    total = sum((w for _, w in pairs), Fraction(0))
    rows: list[dict[str, Any]] = []
    for ident, signal, weight, group in items:
        row: dict[str, Any] = {"id": ident, "group": group, "signal": _num(signal), "weight": _num(weight)}
        rest = [(s, w) for i, s, w, _ in avail if i != ident]
        if signal is None:
            row.update(
                {
                    "reading": "MISSING",
                    "withheld": None,
                    "neutralised": None,
                    "flip": None,
                    "could_change_call_if_it_arrived": _could_change(rest, weight, threshold, minimum, action),
                }
            )
        else:
            without = _call(rest, threshold, minimum)
            zeroed = _call([*rest, (Fraction(0), weight)], threshold, minimum)
            required = without[1] != action
            row.update(
                {
                    "reading": _label(action, signal, required),
                    "withheld": _outcome(without[0], without[1], len(rest), action),
                    "neutralised": _outcome(zeroed[0], zeroed[1], len(rest) + 1, action),
                    "flip": None if score is None else _flip(score, total, signal, weight, threshold, action),
                    "could_change_call_if_it_arrived": None,
                }
            )
        rows.append(row)
    groups: list[dict[str, Any]] = []
    for name in sorted({g for _, _, _, g in items if g is not None}):
        rest = [(s, w) for _, s, w, g in avail if g != name]
        out = _call(rest, threshold, minimum)
        groups.append({"group": name, "items": [i for i, _, _, g in items if g == name], **_outcome(out[0], out[1], len(rest), action)})
    required_ids = [r["id"] for r in rows if r["reading"] == "REQUIRED"]
    missing = [i for i, s, _, _ in items if s is None]
    sufficient = _sufficient(avail, threshold, minimum, action)
    data = {
        "baseline": {
            "score": _num(score),
            "action": action,
            "available": len(avail),
            "threshold": _num(threshold),
            "min_evidence": minimum,
        },
        "items": rows,
        "groups": groups,
        "required_items": required_ids,
        "sufficient_set": sufficient,
        "missing": missing,
    }
    changing = [g["group"] for g in groups if g["changed"]]
    head = (
        f"The rule makes no call: too little evidence ({len(avail)} of {minimum} needed available)."
        if score is None
        else f"The rule says {action} (score {_num(score)} against a threshold of {_num(threshold)}) from {len(avail)} items."
    )
    points = [
        f"Required (withholding it changes the call): {', '.join(required_ids)}."
        if required_ids
        else "No single item is required: the call survives withholding any one.",
    ]
    if changing:
        points.append(f"Removing the whole source group changes the call: {', '.join(changing)}.")
    if sufficient is not None:
        points.append(f"Alone, {', '.join(sufficient['ids'])} gives the same call.")
    if missing:
        points.append(f"Unavailable: {', '.join(missing)}.")
    warnings = [NOT_ABOUT, NOT_FETCHED]
    if missing:
        warnings.append(f"{len(missing)} item(s) had no signal and count for nothing.")
    if sufficient is not None and sufficient["size"] and "greedy" in sufficient["method"]:
        warnings.append("More than 16 available items: the sufficient set is greedy and may not be the smallest.")
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": "partial" if missing else "ok",
        "data_mode": "unknown",
        "as_of": now.strftime(TIME_FORMAT),
        "request": {
            "items": len(items),
            "threshold": _num(threshold),
            "min_evidence": minimum,
            "defaults_applied": [k for k, used in a["defaults"].items() if used],
        },
        "data": data,
        "summary": {"headline": head, "key_points": points},
        "availability": {"evidence": "partial" if missing else "available"},
        "warnings": warnings,
    }
