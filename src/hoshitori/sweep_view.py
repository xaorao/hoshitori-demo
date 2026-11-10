"""How hard the product's checks were tried, as data for the Evidence of quality page.

A mutation sweep changes each small place of a module's code (a comparison turned round, an ``and`` made an ``or``, a statement dropped) and
asks the module's checks to notice. ``tests/sweep_report.py`` writes the results of every sweep to ``docs/SWEEP.md`` with the data they are
made from; this reads that data back, as strictly as it is written, so that the page shows what the sweeps found and not what anyone typed.

* **A sweep says nothing about code that changed after it.** Given the folder of the sources, each row says whether the module and its
  checks are still the files that were swept, by their digests, and why not when they are not. A row from a sweep that kept no digest is
  never current. Without the folder nothing is claimed either way: the row is neither current nor stale, and the summary counts it as
  unchecked.
* **Gaps first.** The rows are ordered so that what changed after its sweep comes first, then the most survivors, then what was noticed only
  by the clock, then by name.
* A survivor is a change no check noticed; each one is listed as harmless, with its reason, in its module's test, which this view does not
  read: it counts them and says no more.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

_MODULE: Final = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")
_RESULTS: Final = ("FAILED", "BUILD_FAILED", "CHECK_RAISED", "TIMED_OUT", "ENVIRONMENT", "SURVIVED")
_FENCE: Final = "```json\n"


class SweepViewError(ValueError):
    """Sweep data that cannot be shown."""


@dataclass(frozen=True)
class SweepRow:
    module: str
    checks: str
    places: int  # changes made
    noticed: int  # a check reported a failure on the changed module
    unbuilt: int  # the changed module could not be loaded
    raised: int  # the checks raised an error on it
    clock: int  # noticed by the clock or the machine only
    survived: int  # no check noticed
    current: bool | None  # the module and its checks are the swept files; none when no folder was given
    reason: str | None  # why not, when they are not


@dataclass(frozen=True)
class SweepSummary:
    modules: int
    places: int
    noticed: int
    unbuilt: int
    raised: int
    clock: int
    survived: int
    stale: int  # rows whose module or checks changed after the sweep, or kept no digest
    unchecked: int  # rows no folder was given for


@dataclass(frozen=True)
class Sweeps:
    summary: SweepSummary
    rows: tuple[SweepRow, ...]


def _count(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SweepViewError(f"{what} is a whole number, zero or more")
    return value


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _currency(entry: dict[str, Any], root: Path) -> tuple[bool, str | None]:
    if "module_sha256" not in entry or "checks_sha256" not in entry:
        return False, "no hash was kept by this sweep"
    module = _digest(root / "src" / (entry["module"].replace(".", "/") + ".py"))
    if module is None:
        return False, "the module is no longer there"
    moved = module != entry["module_sha256"]
    rechecked = _digest(root / (entry["checks"].replace(".", "/") + ".py")) != entry["checks_sha256"]
    if moved and rechecked:
        return False, "the module and its checks changed after the sweep"
    if moved:
        return False, "the module changed after its sweep"
    if rechecked:
        return False, "its checks changed after the sweep"
    return True, None


def _row(entry: object, root: Path | None) -> SweepRow:
    if not isinstance(entry, dict):
        raise SweepViewError("a sweep entry is an object")
    for key in ("module", "checks", "counts", "total", "unresolved"):
        if key not in entry:
            raise SweepViewError(f"a sweep entry has {key}")
    module, checks, counts = entry["module"], entry["checks"], entry["counts"]
    if not isinstance(module, str) or _MODULE.fullmatch(module) is None or not isinstance(checks, str) or _MODULE.fullmatch(checks) is None:
        raise SweepViewError("a sweep entry names its module and its checks by dotted path")
    if not isinstance(counts, dict) or not isinstance(entry["unresolved"], list):
        raise SweepViewError(f"{module}: the counts are an object and the unresolved changes a list")
    unknown = sorted(set(counts) - set(_RESULTS))
    if unknown:
        raise SweepViewError(f"{module}: {unknown} are not results a sweep has")
    made = {name: _count(counts.get(name, 0), f"{module}: {name}") for name in _RESULTS}
    total = _count(entry["total"], f"{module}: the total")
    if sum(made.values()) != total:
        raise SweepViewError(f"{module}: the results add up to {sum(made.values())}, not the total {total}")
    current, reason = (None, None) if root is None else _currency(entry, root)
    return SweepRow(
        module,
        checks,
        total,
        made["FAILED"],
        made["BUILD_FAILED"],
        made["CHECK_RAISED"],
        made["TIMED_OUT"] + made["ENVIRONMENT"],
        made["SURVIVED"],
        current,
        reason,
    )


def read(text: str, root: Path | None = None) -> Sweeps:
    """The sweeps a sweep page holds, with, when ``root`` (the repository folder) is given, whether each is still of the files it swept."""
    start = text.find(_FENCE)
    if start < 0:
        raise SweepViewError("the page holds no sweep data")
    start += len(_FENCE)
    end = text.find("\n```", start)
    if end < 0:
        raise SweepViewError("the sweep data is not closed")
    try:
        loaded = json.loads(text[start:end])
    except json.JSONDecodeError as exc:
        raise SweepViewError(f"the sweep data is not JSON: {exc}") from exc
    if not isinstance(loaded, list):
        raise SweepViewError("the sweep data is a list")
    rows = [_row(entry, root) for entry in loaded]
    names = [row.module for row in rows]
    if len(set(names)) != len(names):
        raise SweepViewError("a module is swept once on a page")
    rows.sort(key=lambda r: (r.current is not False, -r.survived, -r.clock, r.module))
    summary = SweepSummary(
        len(rows),
        sum(r.places for r in rows),
        sum(r.noticed for r in rows),
        sum(r.unbuilt for r in rows),
        sum(r.raised for r in rows),
        sum(r.clock for r in rows),
        sum(r.survived for r in rows),
        sum(1 for r in rows if r.current is False),
        sum(1 for r in rows if r.current is None),
    )
    return Sweeps(summary, tuple(rows))
