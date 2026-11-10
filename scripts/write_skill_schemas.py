"""Write each skill's output schemas to ``skills/<skill>/schema/<tool>.output.schema.json``.

The committed files are what another team reads to learn the shape of a reply without installing the package. Each skill's
``test_publication.py`` compares them with the ``outputSchema`` of its tool definitions and fails when they differ, so after a change
to a skill's output schema run this script and commit the result:

    python3 scripts/write_skill_schemas.py          # write
    python3 scripts/write_skill_schemas.py --check  # exit 1 when a committed file is out of date

It needs Python 3.12 or newer (the skills' own floor) and nothing else: the skills have no runtime dependencies for their schemas.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = {
    "address-flag-check": "address_flag_check",
    "evidence-delta": "evidence_delta",
    "evidence-sensitivity": "evidence_sensitivity",
    "exit-liquidity-check": "exit_liquidity_check",
    "indicator-condition-check": "indicator_condition_check",
    "research-integrity-check": "research_integrity_check",
    "ryo-verdict-record": "ryo_verdict_record",
}


def render(schema: object) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def expected() -> dict[Path, str]:
    """Every file the skills should carry, keyed by path."""
    files: dict[Path, str] = {}
    for skill, package in SKILLS.items():
        sys.path.insert(0, str(ROOT / "skills" / skill / "src"))
        module = importlib.import_module(f"{package}.schema")
        definitions = getattr(module, "TOOL_DEFINITIONS", None) or [module.TOOL_DEFINITION]
        for definition in definitions:
            files[ROOT / "skills" / skill / "schema" / f"{definition['name']}.output.schema.json"] = render(definition["outputSchema"])
        sys.path.pop(0)
        for name in [m for m in sys.modules if m == package or m.startswith(package + ".")]:
            del sys.modules[name]
    return files


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale = 0
    for path, text in expected().items():
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            continue
        stale += 1
        if check:
            print(f"out of date: {path.relative_to(ROOT)}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(ROOT)}")
    return 1 if check and stale else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
