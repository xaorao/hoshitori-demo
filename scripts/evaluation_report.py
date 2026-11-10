#!/usr/bin/env python3
"""Write the two evaluation reports, belief revision and prompt injection, as plain text. Every line says the answers are simulated.

Both suites run offline, with no key and no network: the answerers and the model are scripted. A real model is run through the same
``Answerer`` signature by a caller that turns each card into evidence; that run is not part of this script. Usage:
python scripts/evaluation_report.py [OUT.txt]
"""

from __future__ import annotations

import sys
from pathlib import Path

from hoshitori import belief_revision, injection


def lines() -> list[str]:
    out = ["Belief revision", ""]
    out += belief_revision.report(belief_revision.evaluate(belief_revision.sound), "the sound scripted answerer")
    out += ["", "Controls that must fail", ""]
    out += belief_revision.report(belief_revision.evaluate(belief_revision.gullible), "the gullible scripted answerer")
    out += ["", "Prompt injection", ""]
    out += injection.report(injection.measure(), "the real defences")
    out += ["", "Control that must fail", ""]
    out += injection.report(injection.measure(injection.LEAKY), "defences that do what the planted text says")
    return out


def main() -> int:
    if len(sys.argv) > 2:
        print("usage: evaluation_report.py [OUT.txt]", file=sys.stderr)
        return 2
    text = "\n".join(lines()) + "\n"
    if len(sys.argv) == 2:
        Path(sys.argv[1]).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
