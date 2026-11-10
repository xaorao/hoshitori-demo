"""Write verifier-js/test/vectors/tsa_cases.json: tokens from the Python scenarios with the Python verifier's answers.

Run from the repository root: python tests/vectors/make_tsa_cases.py
The Node tests replay these cases without Python; the pytest differential test replays fresh ones through both verifiers.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.test_tsa_differential import Recorder  # noqa: E402
from tests.tsa_checks import SCENARIOS  # noqa: E402

TARGET = ROOT / "verifier-js" / "test" / "vectors" / "tsa_cases.json.gz"
SWEEP_SAMPLE = 40


def main() -> None:
    kept: list[dict[str, object]] = []
    for name, scenario in SCENARIOS.items():
        recorder = Recorder()
        scenario(recorder)  # type: ignore[arg-type]
        calls = recorder.calls
        if name == "live_tokens_sweep":
            step = max(1, len(calls) // SWEEP_SAMPLE)
            calls = calls[::step]
        kept += [{"scenario": name, **call} for call in calls]
    TARGET.write_bytes(gzip.compress((json.dumps({"cases": kept}) + "\n").encode(), mtime=0))
    print(f"wrote {TARGET} ({len(kept)} cases, {TARGET.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main()
