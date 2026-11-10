#!/usr/bin/env python3
"""Demonstration of the hole an outside review pointed at, in the FIRST version of the E2c verifier (kept in ../E2c_old_v1): committed_at and the outcome window
were read from an unsigned commit.json inside the bundle. A bundle stamped LATE (tokens after the stated commit) is honestly BELOW C3; an operator who edits
only the unsigned file to match the token times turns it into C3. The same attack on the current format edits a signed record and fails integrity (E2c case
'signed outcome window edited after stamping'). Needs live time-stamp authorities and the E2b environment."""
import json, subprocess, sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
HERE = Path(__file__).resolve().parent; OLD = HERE.parent / "E2c_old_v1"
sys.path.insert(0, str(OLD))
import make_bundle as MB            # the old builder
W = Path(tempfile.mkdtemp(prefix="e2c_old_"))
tsas = {"globalsign": "http://timestamp.globalsign.com/tsa/r6advanced1", "swisssign": "http://tsa.swisssign.net"}
now = datetime.now(timezone.utc)
late = {"committed_at": (now - timedelta(seconds=600)).isoformat(timespec="milliseconds"), "outcome_window_opens_at": (now - timedelta(seconds=480)).isoformat(timespec="milliseconds")}
MB.build(W / "b", tsas, seed=b"demo", commit_override=late)
def verify(d):
    r = subprocess.run([sys.executable, "-W", "ignore", "-I", str(OLD / "verify_bundle.py"), str(d)], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    return json.loads(r.stdout)["timing"]["level_supported"]
before = verify(W / "b")
t = datetime.now(timezone.utc)
(W / "b" / "commit.json").write_text(json.dumps({"committed_at": (t - timedelta(seconds=3)).isoformat(timespec="milliseconds"), "outcome_window_opens_at": (t + timedelta(seconds=300)).isoformat(timespec="milliseconds")}))
after = verify(W / "b")
res = {"honestly_late_bundle": before, "after_editing_only_the_unsigned_commit_json": after}
(HERE / "old_hole_demo.json").write_text(json.dumps(res, indent=1)); print(json.dumps(res, indent=1))
