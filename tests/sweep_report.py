"""The account of the mutation sweeps as a page (``docs/SWEEP.md``), made from the log the sweeps write.

    HOSHITORI_SWEEP_LOG=sweeps.jsonl python -m pytest -k every_change      # every sweep appends one line
    python tests/sweep_report.py sweeps.jsonl docs/SWEEP.md

The page holds a table and, under it, the data the table is made from; ``tests/sweep_report_checks.py`` makes the table again from that data
and requires it to be the same, so the published figure cannot drift from its data. Only the latest line of each module and checks function
is kept.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

HEADER = (
    "| Module | Places changed | Noticed by a check | Could not be built | Check raised an error | Clock or machine only | Survived |\n"
    "|---|---|---|---|---|---|---|\n"
)
INTRO = """# Mutation sweeps

Each module has checks, and a sweep makes every small change of the module's code (a comparison turned round, an `and` made an `or`, a
constant moved, a statement dropped) and asks the checks to notice it. This page says how each change was noticed, because "noticed" is not
one thing:

* **Noticed by a check**: a check reported a failure on the changed module. This is the evidence a sweep exists to give.
* **Could not be built**: the changed module could not be loaded at all.
* **Check raised an error**: the checks raised an error on the changed module.
* **Clock or machine only**: the checks ran out of time on two tries, or met an operating-system error that did not come back the same.
  Nothing about the checks is shown by these; they are counted as noticed so that a busy machine cannot invent a survivor, and they are
  named below the table.
* **Survived**: no check noticed the change. Each is listed in the module's test with the reason it is harmless.

A sweep passes when its survivors are exactly the listed harmless ones. A module without a sweep is not in the table.

## What is changed

Each place in a module's syntax tree is changed once, in one of these ways:

* a comparison turned round (`<` to `<=`, `==` to `!=`, `in` to `not in`, `is` to `is not`);
* `and` made `or` and `or` made `and`; a `not` removed; the test of an `if`, a conditional expression or a `while` negated;
* `+` and `-` swapped, `*` made `+`;
* `any` and `all`, `max` and `min`, `.add` and `.discard` swapped; `sorted(x)` replaced by `x`;
* `True` made `False` and the reverse; an integer constant raised by one; a non-empty string constant given a `~` at the end;
* a returned value replaced by `None`;
* a statement dropped: an assignment, an augmented assignment, a `raise`, a `break`, a `continue` or a call used as a statement.

Places that carry words and not rules are left out: docstrings, annotations, the parts of f-strings and the message of a raised error.

## Running one

Each module with a sweep has `tests/<module>_checks.py` (the checks) and `tests/test_<module>.py`, which runs the checks on the real module and
then the sweep. Deselect the slow sweep while working with `-k "not every_change"`. To see the survivors themselves, call
`tests.mutation_sweep.survivors("hoshitori.<module>", "tests.<module>_checks")`. Set `HOSHITORI_SWEEP_LOG` to a file and each call appends one
line; `python tests/sweep_report.py LOG docs/SWEEP.md` makes this page from it. Run sweeps one at a time, and do not edit a module or its
checks while its sweep runs: the sweep imports both.

## Survivors and the list of harmless changes

Every survivor is read. If it is a rule nobody tests, write the check first, see it fail against the changed module, and add it to the
module's checks. If the change does not alter what the code does, list it in the `EQUIVALENT` dictionary of the module's test with the reason.
The key is `function|operator|code|index`, as the sweep prints it. The test requires the list to equal the survivors, so a new survivor fails it
and so does an entry that no longer survives. A change that only a loaded machine can make look noticed (a codec name, a file name) may be
named as load-sensitive, and is then allowed to be missing from the survivors; an unlisted survivor still fails.

## What a clean sweep shows

For the changes it makes, some check notices each one. It does not show that the checks are right, that the module meets its specification,
or that the module is free of faults the changes do not imitate (a missing feature, a wrong design, a race). It is one layer beside the checks
derived from the specification, the property tests, the differential tests against independent implementations and the journey tests.
The table below is made from the sweeps of the code as it was when the page was made; the test beside it fails if a swept file has changed since.
"""


def read_log(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def latest(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The last line of each module and checks function, only the facts the page shows, sorted by module."""
    kept: dict[tuple[str, str], dict[str, Any]] = {}
    for line in lines:
        entry = {
            "module": line["module"],
            "function": line["function"],
            "total": line["total"],
            "counts": line["counts"],
            "unresolved": line["unresolved"],
        }
        for name in ("checks", "module_sha256", "checks_sha256"):  # what the sweep ran on, when the line says
            if name in line:
                entry[name] = line[name]
        kept[(line["module"], line["function"])] = entry
    return [kept[key] for key in sorted(kept)]


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def stale(entries: list[dict[str, Any]], root: Path) -> list[str]:
    """The swept modules whose file, or whose checks' file, is not the one that was swept: a sweep says nothing about code that changed
    after it. ``root`` is the repository folder; a module ``hoshitori.x`` is ``src/hoshitori/x.py`` and a checks module ``tests.x`` is
    ``tests/x.py``. A line written before hashes were kept cannot be shown current."""
    out: list[str] = []
    for entry in entries:
        name = entry["module"]
        if "module_sha256" not in entry or "checks_sha256" not in entry or "checks" not in entry:
            out.append(f"{name}: no hash was kept by this sweep")
            continue
        now = _sha256(root / "src" / (name.replace(".", "/") + ".py"))
        if now is None:
            out.append(f"{name}: the module no longer exists")
            continue
        if now != entry["module_sha256"]:
            out.append(f"{name}: the module changed after its sweep")
        if _sha256(root / (entry["checks"].replace(".", "/") + ".py")) != entry["checks_sha256"]:
            out.append(f"{name}: its checks changed after the sweep")
    return out


def _cells(entry: dict[str, Any]) -> list[int]:
    c = entry["counts"]
    return [
        entry["total"],
        c.get("FAILED", 0),
        c.get("BUILD_FAILED", 0),
        c.get("CHECK_RAISED", 0),
        c.get("TIMED_OUT", 0) + c.get("ENVIRONMENT", 0),
        c.get("SURVIVED", 0),
    ]


def render(entries: list[dict[str, Any]]) -> str:
    rows = [(e["module"], _cells(e)) for e in entries]
    total = [sum(cells[i] for _, cells in rows) for i in range(6)]
    body = "".join(f"| {name} | " + " | ".join(str(n) for n in cells) + " |\n" for name, cells in rows)
    body += "| **All** | " + " | ".join(str(n) for n in total) + " |\n"
    places, noticed, unbuilt, raised, clock, survived = total
    percent = 100 * noticed // places if places else 0
    summary = (
        f"{noticed} of {places} places ({percent}%) were noticed by a check; {unbuilt} could not be built, {raised} made the checks raise an "
        f"error, {clock} were noticed by the clock or the machine only, and {survived} survived (each is listed as harmless, with its reason, in its module's test).\n"
    )
    unresolved = [f"* {e['module']}: {name}\n" for e in entries for name in e["unresolved"]]
    named = ("\nChanges noticed by the clock or the machine only:\n\n" + "".join(unresolved)) if unresolved else ""
    data = json.dumps(entries, indent=1, sort_keys=True)
    return f"{INTRO}\n{HEADER}{body}\n{summary}{named}\nThe data the table is made from:\n\n```json\n{data}\n```\n"


def entries_from(text: str) -> list[dict[str, Any]]:
    start = text.index("```json\n") + len("```json\n")
    loaded: list[dict[str, Any]] = json.loads(text[start : text.index("\n```", start)])
    return loaded


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: sweep_report.py LOG OUT\n")
        return 2
    Path(argv[1]).write_text(render(latest(read_log(Path(argv[0])))), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
