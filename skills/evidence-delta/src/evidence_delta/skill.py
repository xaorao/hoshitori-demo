"""Compare two reads of the same evidence, field by field, and never read a missing value as no change.

Every field of either read is looked at (or the ones asked for). For one path there are four outcomes:

* ``changed``: both reads have a value and the values differ (a number moved by at least ``min_change_pct`` percent counts as moved);
* ``unchanged``: both reads have a value and they are equal (or a number moved by less than ``min_change_pct``);
* ``unknown``: a read has no value there. The reason is ``null`` (the field is present and null), ``absent`` (the read has no such field) or
  ``unusable_number`` (a number that is not finite). Neither "changed" nor "unchanged" is inferred from it.

The verdict is ``changed`` when any field changed; otherwise ``unchanged`` when every field compared is known on both sides,
``unchanged_where_known`` when some fields could not be compared, and ``insufficient_evidence`` when none could. A list is compared index by
index, so an inserted element shifts the ones after it; that is a limit of the rule, stated in ``evidence_delta_rules``.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Any

from .errors import InputError
from .schema import EXAMPLE_CALL, INPUT_SCHEMA, MAX_CHANGES_SHOWN, MAX_DEPTH, MAX_LEAVES, RULES_TOOL_NAME, SCHEMA_VERSION, TOOL_NAME

_UNSET = object()
CHANGE_WORDS = {"changed": "changed", "unchanged": "unchanged"}


@dataclass(frozen=True)
class Read:
    tool: str | None
    as_of: str | None
    status: str | None
    data_mode: str | None
    data: dict[str, Any]


def _fail(field: str, message: str) -> InputError:
    return InputError(field, message, EXAMPLE_CALL)


def parse_read(name: str, value: object) -> Read:
    if not isinstance(value, dict):
        raise _fail(name, "must be an object: an RYO envelope or its data")
    enveloped = isinstance(value.get("data"), dict) and any(k in value for k in ("tool", "as_of", "status", "schema_version", "data_mode"))
    body = value["data"] if enveloped else value
    meta = value if enveloped else {}

    def text(key: str) -> str | None:
        item = meta.get(key)
        return item if isinstance(item, str) and item.strip() else None

    return Read(text("tool"), text("as_of"), text("status"), text("data_mode"), body)


Key = tuple[str | int, ...]


def shown(key: Key) -> str:
    """The dotted way a path is written (a list index as its number); two different paths can be written alike, and are still told apart."""
    return ".".join(str(part) for part in key)


def flatten(value: object, path: Key = (), depth: int = 0, out: dict[Key, object] | None = None) -> dict[Key, object]:
    """Every leaf of a JSON value by path (the keys of objects and the indexes of lists, kept apart); an empty object or list is a leaf
    of its own. Raises ``InputError`` past the limits."""
    out = {} if out is None else out
    if depth > MAX_DEPTH:
        raise _fail("before" if not path else str(path[0]), f"is nested deeper than {MAX_DEPTH} levels")
    if isinstance(value, dict):
        if not value:
            out[path] = {}
        for key, item in value.items():
            flatten(item, (*path, str(key)), depth + 1, out)
    elif isinstance(value, list):
        if not value:
            out[path] = []
        for index, item in enumerate(value):
            flatten(item, (*path, index), depth + 1, out)
    else:
        out[path] = value
    if len(out) > MAX_LEAVES:
        raise _fail("before", f"has more than {MAX_LEAVES} fields")
    return out


def _usable(value: object) -> bool:
    return not (isinstance(value, float) and not math.isfinite(value))


def compare_leaf(before: object, after: object, min_change_pct: float) -> dict[str, Any]:
    """One path's outcome. ``before`` and ``after`` may be the module's ``_UNSET`` for a field the read does not have."""
    reasons: dict[str, str] = {}
    for side, value in (("before", before), ("after", after)):
        if value is _UNSET:
            reasons[side] = "absent"
        elif value is None:
            reasons[side] = "null"
        elif not _usable(value):
            reasons[side] = "unusable_number"
    if reasons:
        return {"outcome": "unknown", "reasons": reasons}
    numbers = (int, float)
    if isinstance(before, numbers) and isinstance(after, numbers) and not isinstance(before, bool) and not isinstance(after, bool):
        if before == after:
            return {"outcome": "unchanged"}
        try:
            pct = None if before == 0 else (after - before) / abs(before) * 100
        except OverflowError:  # integers too large to divide as floats: the change is reported without a percentage
            pct = None
        if pct is not None and abs(pct) < min_change_pct:
            return {"outcome": "unchanged", "within_min_change": True, "change_pct": pct}
        return {"outcome": "changed", "change_pct": pct}
    if type(before) is type(after) and before == after:
        return {"outcome": "unchanged"}
    return {"outcome": "changed", "change_pct": None}


def _shown(value: object) -> object:
    return None if value is _UNSET else value


def _select(keys: set[Key], wanted: list[str] | None) -> list[Key]:
    if wanted is None:
        return sorted(keys, key=lambda k: (shown(k), repr(k)))
    chosen: set[Key] = set()
    for want in wanted:
        hits = {k for k in keys if shown(k) == want or shown(k).startswith(want + ".")}
        chosen |= hits or {(want,)}  # a path neither read holds is still reported, as unknown/absent
    return sorted(chosen, key=lambda k: (shown(k), repr(k)))


def _parse_time(value: str | None) -> dt.datetime | None:
    if value is None:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=dt.UTC)


def _mode(a: str | None, b: str | None) -> str:
    """``live`` only when both reads say so; ``simulated`` if either does; ``mixed`` for any other known combination; else ``unknown``."""
    modes = {a, b} - {None}
    if "simulated" in modes:
        return "simulated"
    if a == "live" and b == "live":
        return "live"
    return "mixed" if modes else "unknown"


def _validate(args: object) -> tuple[Read, Read, list[str] | None, float]:
    if not isinstance(args, dict):
        raise _fail("arguments", "must be an object")
    unknown = sorted(set(args) - set(INPUT_SCHEMA["properties"]))
    if unknown:
        raise _fail(unknown[0], "is not an argument of this tool")
    for key in ("before", "after"):
        if key not in args:
            raise _fail(key, "is required")
    before, after = parse_read("before", args["before"]), parse_read("after", args["after"])
    if before.tool and after.tool and before.tool != after.tool:
        raise _fail("after", f"is a read of {after.tool}, not of {before.tool}; compare two reads of the same tool")
    fields = args.get("fields")
    if fields is not None:
        if (
            not isinstance(fields, list)
            or not fields
            or len(fields) > 200
            or not all(isinstance(f, str) and 0 < len(f) <= 200 for f in fields)
        ):
            raise _fail("fields", "must be a list of 1 to 200 dotted paths")
        if len(set(fields)) != len(fields):
            raise _fail("fields", "must not repeat a path")
    pct = args.get("min_change_pct", 0)
    if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not math.isfinite(pct) or not 0 <= pct <= 100:
        raise _fail("min_change_pct", "must be a number from 0 to 100")
    return before, after, fields, float(pct)


def run(args: object, *, now: dt.datetime | None = None, **_: Any) -> dict[str, Any]:
    before, after, fields, min_pct = _validate(args)
    now = now or dt.datetime.now(dt.UTC)
    left, right = flatten(before.data), flatten(after.data)
    paths = _select(set(left) | set(right), fields)
    changed: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    unchanged: list[str] = []
    for key in paths:
        path = shown(key)
        b, a = left.get(key, _UNSET), right.get(key, _UNSET)
        res = compare_leaf(b, a, min_pct)
        if res["outcome"] == "changed":
            changed.append(
                {"path": path, "before": b, "after": a, "change_pct": None if res["change_pct"] is None else round(res["change_pct"], 6)}
            )
        elif res["outcome"] == "unchanged":
            unchanged.append(path)
        else:
            unknown.append({"path": path, "before": _shown(b), "after": _shown(a), "reasons": res["reasons"]})
    changed.sort(key=lambda c: (-abs(c["change_pct"]) if c["change_pct"] is not None else -math.inf, c["path"]))
    known_both = len(changed) + len(unchanged)
    if changed:
        verdict = "changed"
    elif known_both == 0:
        verdict = "insufficient_evidence"
    elif unknown:
        verdict = "unchanged_where_known"
    else:
        verdict = "unchanged"

    warnings: list[str] = []
    for name, read in (("before", before), ("after", after)):
        if read.status in ("partial", "unavailable"):
            warnings.append(f"{name} is a {read.status} read: its missing fields are unknown, not unchanged")
        if read.data_mode == "simulated":
            warnings.append(f"{name} is simulated data")
    t0, t1 = _parse_time(before.as_of), _parse_time(after.as_of)
    interval = None if t0 is None or t1 is None else round((t1 - t0).total_seconds())
    if interval is not None and interval < 0:
        warnings.append("after is dated earlier than before; the comparison is made as given")
    if before.as_of is None or after.as_of is None:
        warnings.append("a read has no as_of, so the time between the reads is not known")
    if fields is not None:
        missing = [u["path"] for u in unknown if u["reasons"].get("before") == "absent" and u["reasons"].get("after") == "absent"]
        if missing:
            warnings.append(f"{len(missing)} requested field(s) are in neither read")

    twins = len(paths) - len({shown(k) for k in paths})
    if twins:
        warnings.append(
            f"{twins} path(s) are written the same as another but differ in structure (a key that holds a dot or a number, or a list "
            "index); each is compared on its own and they are told apart by their values and places"
        )
    counts = {"compared": len(paths), "changed": len(changed), "unchanged": len(unchanged), "unknown": len(unknown)}
    tool = before.tool or after.tool or "the evidence"
    span = "" if interval is None or interval < 0 else f" over {_span(interval)}"
    if verdict == "changed":
        top = ", ".join(_top(c) for c in changed[:3])
        headline = f"{tool}: {len(changed)} of {len(paths)} fields changed{span}: {top}."
    elif verdict == "unchanged":
        headline = f"{tool}: no field changed{span}; all {len(paths)} compared fields are known in both reads."
    elif verdict == "unchanged_where_known":
        headline = f"{tool}: no known field changed{span}, but {len(unknown)} of {len(paths)} could not be compared. That is not no change."
    else:
        headline = f"{tool}: nothing could be compared{span}; no field is known in both reads. That is not no change."
    points = [f"{counts['changed']} changed, {counts['unchanged']} unchanged, {counts['unknown']} could not be compared."]
    if unknown:
        points.append("Not compared: " + ", ".join(u["path"] for u in unknown[:5]) + ("…" if len(unknown) > 5 else "") + ".")
    data = {
        "verdict": verdict,
        "counts": counts,
        "interval_seconds": interval,
        "before": {"tool": before.tool, "as_of": before.as_of, "status": before.status, "data_mode": before.data_mode},
        "after": {"tool": after.tool, "as_of": after.as_of, "status": after.status, "data_mode": after.data_mode},
        "changes": changed[:MAX_CHANGES_SHOWN],
        "unknown": unknown[:MAX_CHANGES_SHOWN],
        "unchanged": unchanged[:MAX_CHANGES_SHOWN],
        "truncated": {
            "changes": len(changed) > MAX_CHANGES_SHOWN,
            "unknown": len(unknown) > MAX_CHANGES_SHOWN,
            "unchanged": len(unchanged) > MAX_CHANGES_SHOWN,
        },
        "min_change_pct": min_pct,
    }
    partial_read = "partial" in (before.status, after.status) or "unavailable" in (before.status, after.status)
    status = "unavailable" if known_both == 0 else ("partial" if unknown or partial_read else "ok")
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": status,
        "data_mode": _mode(before.data_mode, after.data_mode),
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "request": {"fields": fields, "min_change_pct": min_pct, "before_tool": before.tool, "after_tool": after.tool},
        "data": data,
        "summary": {"headline": headline, "key_points": points},
        "availability": {"comparison": "available" if known_both else "unavailable"},
        "warnings": warnings,
    }


def _span(seconds: int) -> str:
    if seconds < 120:
        return f"{seconds} seconds"
    if seconds < 7200:
        return f"{round(seconds / 60)} minutes"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} hours"
    return f"{seconds / 86400:.1f} days"


def _top(change: dict[str, Any]) -> str:
    pct = change["change_pct"]
    if pct is None:
        return f"{change['path']} {change['before']!r} to {change['after']!r}"
    return f"{change['path']} {pct:+.2f}%"


def run_rules(args: object, *, now: dt.datetime | None = None, **_: Any) -> dict[str, Any]:
    """Describe the rules of the comparison; compares nothing."""
    if not isinstance(args, dict) or args:
        raise InputError("arguments", "this tool takes none", {})
    now = now or dt.datetime.now(dt.UTC)
    rules = [
        "changed: both reads have a value there and the values differ (a number moves by at least min_change_pct percent)",
        "unchanged: both reads have a value there and they are equal",
        "unknown: a read has no value there (null, absent, or a number that is not finite); neither changed nor unchanged is inferred",
        "verdict changed: at least one field changed",
        "verdict unchanged: nothing changed and every compared field is known in both reads",
        "verdict unchanged_where_known: nothing known changed, and some fields could not be compared",
        "verdict insufficient_evidence: no field is known in both reads",
        "lists are compared index by index: an inserted element shifts the ones after it",
        "strings are compared exactly, with no case or space normalisation; booleans and numbers are never equal to each other",
        f"limits: depth {MAX_DEPTH}, {MAX_LEAVES} fields per read, {MAX_CHANGES_SHOWN} entries shown per list; "
        "a call reads nothing from the network",
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": RULES_TOOL_NAME,
        "status": "ok",
        "data_mode": "unknown",
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "request": {},
        "data": {"rules": rules},
        "summary": {"headline": "evidence_delta never reads a missing value as no change.", "key_points": rules[:3]},
        "availability": {"rules": "available"},
        "warnings": [],
    }
