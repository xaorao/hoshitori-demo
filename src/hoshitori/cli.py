"""``hoshitori-verify``: check a verification bundle offline.

    hoshitori-verify BUNDLE [--trusted-root FILE] [--channel FILE ...] [--now TIME] [--floor-version N]

``--trusted-root`` is the root document shipped with the verifier release. ``--channel`` files (JSON, version -> envelope)
are newer roots fetched from independent places; the update rules of ``hoshitori.trusted_root`` decide which root is
current. ``--now`` is the fixed time the root's expiry is judged against (RFC 3339 with an offset; the default is the clock of
this machine, which makes the verdict depend on it, so pass it to reproduce a verdict). Without a trusted root the bundle is
only checked for internal consistency. The verdict is printed as JSON; the exit status is 0 when the integrity of the bundle
is established against the trusted root, 1 when it is not, 2 for a usage error or a runtime whose Unicode is older than 15.0
(which cannot check the canonical form, so no verdict is printed).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from hoshitori import canonical, trusted_root, verify
from hoshitori.timeutil import TimeError, parse_rfc3339

_VERSION_KEY = re.compile(r"0|[1-9][0-9]*", re.ASCII)


def _load_channel(path: Path) -> dict[int, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(_VERSION_KEY.fullmatch(key) for key in data):  # ASCII digits, no leading zero
        raise ValueError("a channel file maps versions to envelopes")
    return {int(key): value for key, value in data.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hoshitori-verify", description="Verify a Hoshitori bundle offline.")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--trusted-root", type=Path, help="the root document shipped with this verifier")
    parser.add_argument("--channel", type=Path, action="append", default=[], help="a file of newer roots (version -> envelope); repeatable")
    parser.add_argument("--now", help="the fixed time for root expiry, RFC 3339 with an offset")
    parser.add_argument("--floor-version", type=int, default=0, help="refuse a final root below this version (rollback protection)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        now = parse_rfc3339(args.now) if args.now else datetime.now(UTC)
        root = None
        status = "CURRENT"
        update_summary = None
        if args.trusted_root is not None:
            initial = trusted_root.parse_root(args.trusted_root.read_bytes())
            channels = [_load_channel(path) for path in args.channel]
            result = trusted_root.update(initial, channels, now, floor_version=args.floor_version)
            root, status = result.root, result.status
            update_summary = {
                "status": result.status,
                "code": result.code,
                "version": result.root.version,
                "single_channel": result.single_channel,
            }
        elif args.channel:
            raise ValueError("--channel needs --trusted-root")
    except (OSError, ValueError, TimeError, trusted_root.RootError, canonical.UnicodeVersionError) as exc:
        print(f"hoshitori-verify: {exc}", file=sys.stderr)
        return 2
    try:
        verdict = verify.verify_bundle(args.bundle, root, status)
    except canonical.UnicodeVersionError as exc:  # no verdict on a runtime that cannot check the canonical form
        print(f"hoshitori-verify: {exc}", file=sys.stderr)
        return 2
    if update_summary is not None:
        verdict["root_update"] = update_summary
    print(json.dumps(verdict, indent=2, sort_keys=True))
    return 0 if verdict["integrity"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
