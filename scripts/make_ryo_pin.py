"""Write ``src/hoshitori/ryo_pin.py`` from a catalogue body read with a builder key (``GET /tools``).

Usage: ``python scripts/make_ryo_pin.py <catalogue.json> [<output.py>]``. The catalogue file is the raw body RYO sent. The script refuses a
catalogue the product cannot check (``ryo_catalogue.parse_catalogue``) and never reads a key.
"""

from __future__ import annotations

import pprint
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hoshitori import ryo_catalogue  # noqa: E402

HEADER = '''"""The structure of RYO's six tools as read from the authenticated catalogue: what the product was built against.

This is the pin of ``ryo_catalogue.schema_pin``: argument names, types, requirements and enums, without RYO's descriptions. A live catalogue
that differs from it in structure is drift (``ryo_catalogue.diff``); a live catalogue that only words things differently is not.
Regenerate with ``scripts/make_ryo_pin.py`` from a catalogue read with a builder key; a change here is a change of the product's contract.
"""

from __future__ import annotations

from typing import Any, Final

PIN: Final[dict[str, dict[str, Any]]] = '''


def render(body: bytes) -> str:
    pin = ryo_catalogue.schema_pin(ryo_catalogue.parse_catalogue(body))
    return HEADER + pprint.pformat(pin, width=130, sort_dicts=True) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__)
        return 2
    target = Path(argv[2]) if len(argv) == 3 else ROOT / "src" / "hoshitori" / "ryo_pin.py"
    target.write_text(render(Path(argv[1]).read_bytes()))
    print(f"wrote {target}; run `ruff format --line-length 140` on it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
