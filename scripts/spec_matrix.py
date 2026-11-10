#!/usr/bin/env python3
"""Write ``SPEC_MATRIX.md``: each numbered section of the specifications named in ``SPECS`` with the checks that hold the code to it.

The sections are read from each specification's own headings (``## N. title``). The checks are named in ``docs/spec_evidence.json``, keyed
``"SPEC N"``, and looked up as a test or check function, a test file or a script in the repository, by the same search as the other
matrices. An entry is a list of check names, an object with ``checks`` and an optional ``note`` (what the checks leave out), or a stated
verdict (``NOT BUILT``, ``NOT TESTED``, ``NOT APPLICABLE`` or ``OUTSIDE THIS REPOSITORY``) with its reason. A section the file does not
mention, a check that cannot be found, or a section a specification does not have is an error. The page says a check exists; it does not
run it.

Usage: python scripts/spec_matrix.py [OUT.md]
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

REPO: Final = Path(__file__).resolve().parents[1]
ARCHITECTURE: Final = REPO / "docs" / "specs"
DEFAULT_OUT: Final = ARCHITECTURE / "SPEC_MATRIX.md"
SPECS: Final = (
    "SECURITY_MODEL",
    "PRIVACY_DATA_FLOWS",
    "OPERATIONS_SLOS",
    "OPERATIONS_ARCHITECTURE",
    "LEDGER_SPEC",
    "TRANSPARENCY_LOG_SPEC",
    "VERIFICATION_SPEC",
    "VERIFIER_THREAT_MODEL",
    "CLAIM_GRAPH_SPEC",
    "GROUNDING_CONTRACT",
    "EVIDENCE_CHALLENGE_SPEC",
    "FALSIFICATION_PROTOCOL",
    "AGENT_PROTOCOL",
    "LEAGUE_EXPERIMENT",
    "RULES_ARM_SPEC",
    "EVALUATION_PROTOCOL",
    "DATA_PROVENANCE",
    "GATEWAY_DESIGN",
    "DOMAIN_MODEL",
    "ASSET_REGISTRY_SPEC",
    "UX_SPEC",
    "SIMULATOR_SPEC",
    "DISCOVERY_MANIFEST_SPEC",
    "CYCLE_RUNNER_SPEC",
    "ARCHITECTURE",
    "INTEGRATION_PROOF",
    "LICENSE_REGISTER",
    "CONNECTOR_CONTRACTS",
)
HEADING: Final = re.compile(r"^## (\d+)\. (.+?)\s*$", re.M)
STATED: Final = ("NOT BUILT", "NOT TESTED", "NOT APPLICABLE", "OUTSIDE THIS REPOSITORY")
VERDICTS: Final = ("CHECKS FOUND", "CHECK NOT FOUND", *STATED, "NO CHECK NAMED")


def _contract() -> Any:
    spec = importlib.util.spec_from_file_location("contract_matrix", Path(__file__).with_name("contract_matrix.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("contract_matrix", module)
    spec.loader.exec_module(module)
    return module


class SpecError(ValueError):
    """Evidence that does not fit the specifications."""


@dataclass(frozen=True)
class Section:
    spec: str
    number: str
    title: str


@dataclass(frozen=True)
class Judged:
    section: Section
    verdict: str
    found: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    reason: str = ""
    note: str = ""


def read_specs() -> dict[str, str]:
    """The text of each specification of the matrix."""
    return {name: (ARCHITECTURE / f"{name}.md").read_text(encoding="utf-8") for name in SPECS}


def read_sections(texts: Mapping[str, str]) -> list[Section]:
    """The numbered sections of each specification, in the order given and the order of each text."""
    return [Section(spec, m.group(1), m.group(2)) for spec, text in texts.items() for m in HEADING.finditer(text)]


def judge(sections: Sequence[Section], evidence: Mapping[str, Any], root: Path) -> list[Judged]:
    keys = {f"{s.spec} {s.number}" for s in sections}
    for key in evidence:
        if key not in keys:
            raise SpecError(f"{key!r} is not a section of the specifications")
    contract = _contract()
    text, stems = contract._rubric()._haystack(root)
    out: list[Judged] = []
    for s in sections:
        entry = evidence.get(f"{s.spec} {s.number}")
        if entry is None:
            out.append(Judged(s, "NO CHECK NAMED"))
            continue
        if isinstance(entry, Mapping) and "verdict" in entry:
            verdict, reason = entry.get("verdict"), entry.get("reason")
            if verdict not in STATED:
                raise SpecError(f"{s.spec} {s.number}: the verdict is one of {STATED}")
            if not isinstance(reason, str) or not reason.strip():
                raise SpecError(f"{s.spec} {s.number}: a stated verdict needs a reason")
            out.append(Judged(s, str(verdict), reason=reason))
            continue
        names = list(entry.get("checks", [])) if isinstance(entry, Mapping) else list(entry)
        note = str(entry.get("note", "")) if isinstance(entry, Mapping) else ""
        if not names or any(not isinstance(n, str) or not n.strip() for n in names):
            raise SpecError("a check is a name")
        found = [n for n in names if contract._exists(n, text, stems)]
        missing = [n for n in names if n not in found]
        out.append(Judged(s, "CHECK NOT FOUND" if missing else "CHECKS FOUND", tuple(found), tuple(missing), note=note))
    return out


INTRO: Final = """# Specification matrix

Scope: the numbered sections of the specifications named in the first column of the table.
Status: generated from the specifications' headings and `docs/spec_evidence.json`.

Generated by `scripts/spec_matrix.py`; do not edit by hand. Each section is shown with the checks that hold the code to it.
A check counts when a test or check function, a test file or a script of that name exists in the repository.
A check that exists is not a check that passes: the suites say that. A section with no check named is NOT VERIFIED.
NOT BUILT, NOT TESTED, NOT APPLICABLE and OUTSIDE THIS REPOSITORY say why no check exists; a note says what the named checks leave out.

"""


def render(judged: Sequence[Judged]) -> str:
    out = [INTRO, "| Verdict | Sections |\n|---|---|\n"]
    for verdict in VERDICTS:
        names = [f"{j.section.spec} {j.section.number}" for j in judged if j.verdict == verdict]
        out.append(f"| {verdict}: {len(names)} | {', '.join(names) or '-'} |\n")
    out.append("\n| Specification | Section | Title | Verdict | Checked by, or why not |\n|---|---|---|---|---|\n")
    for j in judged:
        s = j.section
        detail = j.reason or ", ".join(j.found + j.missing) or "-"
        if j.note:
            detail += f". Not covered: {j.note}"
        out.append(f"| {s.spec} | {s.number} | {s.title.replace('|', '/')} | {j.verdict} | {detail.replace('|', '/')} |\n")
    return "".join(out)


def main(argv: list[str]) -> int:
    out = Path(argv[0]) if argv else DEFAULT_OUT
    sections = read_sections(read_specs())
    evidence = json.loads((REPO / "docs" / "spec_evidence.json").read_text(encoding="utf-8"))
    judged = judge(sections, evidence, REPO)
    out.write_text(render(judged), encoding="utf-8")
    return 0 if all(j.verdict not in ("CHECK NOT FOUND", "NO CHECK NAMED") for j in judged) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
