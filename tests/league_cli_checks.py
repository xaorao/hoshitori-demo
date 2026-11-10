"""Checks of the league command (``hoshitori.league_cli``), runnable against a mutant. Standard output and error are captured; the outside
world is the loopback fake of ``e2e_league``."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import signal
import tempfile
import threading
import types
from pathlib import Path
from typing import Any

from hoshitori import claims_file, deployment
from tests.claims_file_checks import made
from tests.deployment_checks import world_files
from tests.e2e_league import OffsetClock, outside


def call_raw(mod: types.ModuleType, *argv: str) -> tuple[int, str, str]:
    """Run the command in a thread so that a command that never returns (a loop that is not stopped) is a failed check and not a hang."""
    out, err = io.StringIO(), io.StringIO()
    result: list[int | BaseException] = []

    def go() -> None:
        try:
            result.append(mod.main(list(argv)))
        except BaseException as exc:
            result.append(exc)

    thread = threading.Thread(target=go, daemon=True)
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        thread.start()
        thread.join(10)
    if thread.is_alive():
        raise AssertionError(f"the command {argv[:2]} did not return")
    code = result[0]
    if isinstance(code, BaseException):
        raise code
    return code, out.getvalue(), err.getvalue()


def call(mod: types.ModuleType, *argv: str) -> tuple[int, list[Any], str]:
    code, text, err = call_raw(mod, *argv)
    found: list[Any] = []
    for line in text.splitlines():
        with contextlib.suppress(json.JSONDecodeError):
            found.append(json.loads(line))
    return code, found, err


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def check_usage(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    c.eq("no command is a usage error", call(mod)[0], 2)
    c.eq("an unknown command is a usage error", call(mod, "dance")[0], 2)
    c.eq("a missing argument is a usage error", call(mod, "status")[0], 2)
    c.eq("help is not an error", call(mod, "--help")[0], 0)
    key = root / "k"
    code, lines, err = call(mod, "init-key", str(key), "hoshitori.example/log")
    c.eq("a key is made", (code, list(lines[0]) if lines else None, err), (0, ["file", "public_key"], ""))
    c.eq("and its public key is the log's", lines[0]["public_key"].startswith("hoshitori.example/log+") if lines else None, True)
    code, lines, err = call(mod, "init-key", str(key), "hoshitori.example/log")
    c.eq("a key is not made twice", (code, lines, err), (2, [], "hoshitori-league: a key file is never overwritten\n"))
    from hoshitori import canonical, rules_arm
    from tests import root_support as R
    from tests.rules_arm_checks import graded

    (root / "root.json").write_bytes(canonical.dumps(R.make_document(1, root_keys=[R.new_signer(1)])))  # type: ignore[arg-type]
    (root / "map.json").write_bytes(rules_arm.dump(graded(rules_arm)))
    made = [
        "make-protocol",
        str(root / "p.json"),
        "--root",
        str(root / "root.json"),
        "--mapping",
        f"L0={root / 'map.json'}",
        "--name",
        "x",
        "--start-ms",
        "1793275200000",
        "--every-minutes",
        "60",
        "--watchlist",
        "BTC,ETH",
    ]
    code, lines, err = call(mod, *made)
    c.eq(
        "a protocol is written",
        (code, sorted(lines[0]), err, (root / "p.json").is_file()),
        (0, ["file", "root_sha256", "sha256", "slots_from_ms"], "", True),
    )
    code, lines, err = call(mod, *made)
    c.eq("and never over another", (code, lines, err), (2, [], "hoshitori-league: a protocol file is never overwritten\n"))
    c.eq("an argument that is missing is a usage error", call(mod, "make-protocol", str(root / "q.json"))[0], 2)
    code, _, err = call(mod, "status", str(root / "none.json"))
    c.eq("settings that are not there", (code, err), (2, "hoshitori-league: the settings cannot be read: No such file or directory\n"))
    return c.bad


def check_commands(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    with outside(OffsetClock()) as world:
        settings = str(world_files(root, world))
        code, lines, err = call(mod, "run", settings, "--once")
        c.eq("one tick", (code, len(lines), err), (0, 1, ""))
        tick = lines[0]
        c.eq(
            "it ran a cycle at C3",
            (tick["ran"]["state"], tick["ran"]["level"], tick["ran"]["slots"], tick["ran"]["reason"]),
            ("COMMITTED", "C3", {"FORECAST": 6}, None),
        )
        c.eq(
            "after refreshing the lists, with nothing to close and nothing missing",
            (tick["refreshed"], tick["closed"], tick["findings"]),
            ({"records": 2, "failures": 0, "failed": []}, [], []),
        )
        c.eq(
            "and the pass over the waiting forecasts",
            tick["settled"],
            {"records": 0, "pending": 0, "deferred": 0, "failed": 0, "failures": []},
        )
        c.eq("no practice pass unless the settings ask for it", tick["practiced"], None)
        c.eq(
            "the keys of a tick",
            sorted(tick),
            ["closed", "desk", "findings", "now_ms", "on_demand", "planned", "practiced", "ran", "refreshed", "settled"],
        )
        (root / "pr").mkdir()
        code, lines, err = call(mod, "run", str(world_files(root / "pr", world, practice=True)), "--once")
        c.eq(
            "with the practice pass on: three books opened and nine entries waited for before the origin",
            (code, err, lines[0]["practiced"]),
            (0, "", {"L0": {"records": 3, "waiting": 9, "missed": 0, "refused": 0, "unread": ["BTC"]}}),
        )
        code, lines, err = call(mod, "run", settings, "--once")
        c.eq("the same slot is not run twice", (code, lines[0]["ran"], lines[0]["refreshed"]), (0, None, None))
        code, lines, err = call(mod, "status", settings)
        c.eq("the status", (code, lines[0]["cycles"], lines[0]["forecasts"], lines[0]["failures"]), (0, {"COMMITTED": 1}, 6, 0))
        code, lines, err = call(mod, "replay", settings, tick["ran"]["cycle"])
        c.eq(
            "the replay of the cycle: every slot as it was committed, and the exit status says so",
            (code, err, lines[0]),
            (
                0,
                "",
                {
                    "cycle": tick["ran"]["cycle"],
                    "slots": 6,
                    "match": 6,
                    "different": 0,
                    "not_reconstructible": 0,
                    "not_compared": 0,
                    "clean": True,
                    "exceptions": [],
                },
            ),
        )
        real_replay = mod.deployment.replay_cycle
        mod.deployment.replay_cycle = lambda running, cycle: {"cycle": cycle, "clean": False}  # type: ignore[assignment]
        try:
            code, lines, err = call(mod, "replay", settings, tick["ran"]["cycle"])
        finally:
            mod.deployment.replay_cycle = real_replay
        c.eq("a replay that is not clean is shown and exits 1", (code, lines), (1, [{"cycle": tick["ran"]["cycle"], "clean": False}]))
        code, lines, err = call(mod, "replay", settings, "nope")
        c.eq(
            "a cycle the ledger does not hold",
            (code, err),
            (2, "hoshitori-league: the ledger holds no snapshot and manifest of this cycle\n"),
        )
        out = root / "bundle"
        code, lines, err = call(mod, "export", settings, tick["ran"]["cycle"], str(out))
        held = len(list((out / "items").glob("*.canonical.json")))
        c.eq(
            "the export holds the whole batch, not only the forecasts and what they cite",
            (code, lines[0]["records"] + lines[0]["later_records"] == held and held > 12, lines[0]["cycle"]),
            (0, True, tick["ran"]["cycle"]),
        )
        code, lines, err = call(mod, "export", settings, tick["ran"]["cycle"], str(out))
        c.eq("a bundle is never written over another", (code, err.startswith("hoshitori-league: BundleError: ")), (1, True))
        site = root / "site"
        code, lines, err = call(mod, "site", settings, str(site))
        c.eq(
            "the site",
            (code, lines[0]["bundles"], lines[0]["without_bundle"], lines[0]["directory"]),
            (0, [tick["ran"]["cycle"]], [], str(site)),
        )
        c.eq(
            "exploratory unless it is said to be scored",
            json.loads((site / "index.json").read_text())["mode"],
            "Exploratory run: not a scored period",
        )
        status = root / "status.md"
        status.write_text("| Unit | Status | Evidence |\n|---|---|---|\n| A part | TESTED | its checks |\n", encoding="utf-8")
        code, lines, err = call(mod, "site", settings, str(site), "--status", str(status))
        c.eq("the site with a status table", (code, json.loads((site / "quality.json").read_text())[0]["status"]), (0, "TESTED"))
        from tests.sweep_view_checks import ENTRIES, page

        swept = root / "sweeps.md"
        swept.write_text(page(ENTRIES), encoding="utf-8")
        code, lines, err = call(mod, "site", settings, str(site), "--sweeps", str(swept))
        c.eq("the site with a sweep page", (code, json.loads((site / "sweeps.json").read_text())["summary"]["modules"]), (0, 3))
        from tests.coverage_view_checks import contract, rubric

        (root / "RUBRIC_MATRIX.md").write_text(rubric(), encoding="utf-8")
        (root / "CONTRACT_MATRIX.md").write_text(contract(), encoding="utf-8")
        code, lines, err = call(mod, "site", settings, str(root / "site9c"), "--coverage", str(root))
        c.eq("the site with the matrices", (code, json.loads((root / "site9c" / "coverage.json").read_text())["rubric_total"]), (0, 3))
        code, lines, err = call(mod, "site", settings, str(root / "site9d"), "--coverage", str(root / "nowhere"))
        c.eq("matrices that are not there", (code, err.startswith("hoshitori-league: the coverage matrices cannot be shown: ")), (2, True))
        code, lines, err = call(mod, "site", settings, str(root / "site9s"), "--sweeps", str(root / "missing.md"))
        c.eq("a sweep page that is not there", (code, err.startswith("hoshitori-league: the sweep page cannot be shown: ")), (2, True))
        code, lines, err = call(mod, "site", settings, str(root / "site9"), "--status", str(root / "missing.md"))
        c.eq("a status file that is not there", (code, err.startswith("hoshitori-league: the status file cannot be shown: ")), (2, True))
        observed = {"cycle": "Committed", "level": "C3", "slots": [["Open", 1]], "reasons": [], "forecasts": 1}
        drills = root / "drill.json"
        drills.write_text(
            json.dumps(
                [
                    {
                        "id": "d",
                        "title": "t",
                        "injected": "i",
                        "expected": "e",
                        "observed": observed,
                        "expected_observed": observed,
                        "held": True,
                    }
                ]
            ),
            encoding="utf-8",
        )
        code, lines, err = call(mod, "site", settings, str(site), "--drill", str(drills))
        c.eq("the site with drills", (code, json.loads((site / "drill.json").read_text())[0]["held"]), (0, True))
        claims = root / "claims.json"
        claims.write_text(json.dumps([made(claims_file)]), encoding="utf-8")
        code, lines, err = call(mod, "site", settings, str(site), "--claims", str(claims))
        c.eq("the site with claims", (code, json.loads((site / "claims.json").read_text())[0]["id"]), (0, "golden"))
        from hoshitori import lab as lab_module

        log = root / "lab.jsonl"
        book = lab_module.Lab(log)
        proposal = {
            "statement": "BTC bounces after a low RSI",
            "symbol": "BTC",
            "direction": "UP",
            "horizon_h": 4,
            "condition": {"left": {"indicator": "rsi", "period": 14}, "op": "<", "right": {"value": 35}},
            "invalidation": {"left": {"indicator": "close"}, "op": "<", "right": {"value": 90}},
            "max_weight": "0.02",
            "assumptions": [{"id": "a1", "kind": "OBSERVATION", "text": "the RSI falls below 35 now and then"}],
        }
        frozen = book.freeze(lab_module.draft_from(proposal), "check", at_ms=1_700_000_000_000)
        candles = Path(__file__).parent / "fixtures" / "lab_preview" / "btc_hourly.json"
        runs = root / "lab-runs.json"
        runs.write_text("{}", encoding="utf-8")
        code, lines, err = call(
            mod, "site", settings, str(root / "site-lab"), "--lab", str(log), "--lab-candles", str(candles), "--lab-runs", str(runs)
        )
        shown = json.loads((root / "site-lab" / "lab.json").read_text())
        c.eq(
            "the site with a Laboratory shows its version, its preview and no run",
            (
                code,
                [e["version"] for e in shown["versions"]],
                shown["versions"][0]["preview"]["bars"],
                shown["versions"][0]["forward"]["run"],
            ),
            (0, [frozen], 300, False),
        )
        code, lines, err = call(mod, "site", settings, str(root / "site-lab2"), "--lab", str(root / "no-log.jsonl"))
        c.eq(
            "a Laboratory log that is not there",
            (code, err),
            (2, f"hoshitori-league: the Laboratory log {root / 'no-log.jsonl'} does not exist\n"),
        )
        code, lines, err = call(mod, "site", settings, str(root / "site-lab3"), "--lab-candles", str(candles))
        c.eq(
            "candles without a log",
            (code, err),
            (2, "hoshitori-league: stored candles and runs are shown beside a Laboratory log; give the log\n"),
        )
        code, lines, err = call(mod, "site", settings, str(root / "site11"), "--claims", str(root / "gone.json"))
        c.eq("a claims file that is not there", (code, err.startswith("hoshitori-league: the claims file cannot be shown: ")), (2, True))
        code, lines, err = call(mod, "site", settings, str(root / "site12"), "--model-challenge", str(root / "gone.json"))
        c.eq(
            "a model challenge file that is not there",
            (code, err.startswith(f"hoshitori-league: the model challenge file {root / 'gone.json'} cannot be shown: ")),
            (2, True),
        )
        real_export_site = mod.deployment.export_site
        given: list[Any] = []

        def export_site(running: Any, out: Any, **kw: Any) -> dict[str, Any]:
            given.append(kw)
            return {"directory": str(out)}

        mod.deployment.export_site = export_site
        try:
            once = call(mod, "site", settings, str(site), "--model-challenge", "a.json", "--model-challenge", "b.json")
            never = call(mod, "site", settings, str(site))
        finally:
            mod.deployment.export_site = real_export_site
        c.eq(
            "--model-challenge is repeatable and each file is passed on, in order; none given is none passed",
            (once[0], never[0], [kw.get("model_challenges") for kw in given]),
            (0, 0, [[Path("a.json"), Path("b.json")], []]),
        )
        code, lines, err = call(mod, "site", settings, str(root / "site10"), "--drill", str(root / "missing.json"))
        c.eq("a drill file that is not there", (code, err.startswith("hoshitori-league: the drill file cannot be shown: ")), (2, True))
        code, lines, err = call(mod, "site", settings, str(site), "--scored")
        c.eq("a scored period says so", (code, json.loads((site / "index.json").read_text())["mode"]), (0, "Scored period"))
        code, lines, err = call(mod, "export", settings, "nope", str(root / "b2"))
        c.eq("only a committed cycle is exported", (code, err), (2, "hoshitori-league: only a committed cycle can be exported\n"))
        # a tick that fails is shown and, with --once, makes the exit status 1
        real = mod.scheduler.tick

        def boom(*args: Any, **kw: Any) -> Any:
            raise RuntimeError("the tick failed")

        mod.scheduler.tick = boom
        try:
            code, lines, err = call(mod, "run", settings, "--once")
        finally:
            mod.scheduler.tick = real
        c.eq("a failed tick is one line and a failed run", (code, lines), (1, [{"error": "RuntimeError", "message": "the tick failed"}]))
        long = "x" * 900

        def long_boom(*args: Any, **kw: Any) -> Any:
            raise RuntimeError(long)

        mod.scheduler.tick = long_boom
        try:
            _, lines, _ = call(mod, "run", settings, "--once")
        finally:
            mod.scheduler.tick = real
        c.eq("a long message is cut to 500 characters", len(lines[0]["message"]), 500)
    return c.bad


def _says(text: str, phrase: str) -> bool:
    """Whether the phrase stands in the text as written, with nothing joined to its end."""
    return re.search(re.escape(phrase) + r"(\s|$)", " ".join(text.split())) is not None


def check_output_and_help(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    _code, text, _ = call_raw(mod, "--help")
    c.eq("the usage names the program", text.startswith("usage: hoshitori-league "), True)
    for phrase in (
        "Run the Hoshitori league and look at what it wrote.",
        "make a log signing key",
        "write a protocol file",
        "tick until stopped",
        "what the ledger holds",
        "the bundle of a committed cycle",
        "the static documents of the interface",
        "rebuild a committed cycle's decisions from the ledger and the stored answers, and compare",
        "a forged copy of a committed cycle's bundle, to show what the time-stamps are for",
        "confirm that the host clock's present reading is the true time after the writer refused a stepped clock",
    ):
        c.eq(f"the help says {phrase!r}", _says(text, phrase), True)
    for command, phrase in (
        ("run", "one tick, then exit"),
        ("make-protocol", "comma-separated symbols"),
        ("make-protocol", "also forecast contract B for this arm"),
        ("site", "say the period is a scored one (the default says it is exploratory)"),
        ("site", "the status table (docs/STATUS.md) for the Evidence of quality page"),
        ("site", "the recorded failure drills (scripts/make_drill.py) for the Drill page"),
        ("site", "the recorded claim readings (scripts/make_claims.py) for the Claims page"),
        ("site", "a file of recorded Desk runs (hoshitori.desk_view.file_bytes) for the Desk page"),
        ("site", "--desk-runs FILE"),
        ("site", "a Laboratory command log, for the Laboratory page"),
        ("site", "--lab LOG"),
        (
            "site",
            "stored real hourly candles of one token (scripts/make_lab_preview.py), for the Laboratory previews; repeatable, needs --lab",
        ),
        ("site", "--lab-candles FILE"),
        ("site", "stored runs of Laboratory versions, needs --lab"),
        ("site", "--lab-runs FILE"),
        ("site", "the mutation sweep page (docs/SWEEP.md) for the Evidence of quality page"),
        ("site", "the folder of the rubric and contract matrices (docs/specs) for the same page"),
        ("site", "a recorded challenge of the model arm (scripts/challenge_live.py) for the What if page; repeatable"),
        ("make-protocol", "the model arms' settings file; the protocol pins the versions it describes"),
        ("make-protocol", "the time each model arm has, per horizon"),
        ("make-protocol", "ARM=HOURS:MS,..."),
        ("make-protocol", "a source read when it can be and never required (binance-funding)"),
        ("make-protocol", "--optional-source NAME"),
        ("site", "--model-challenge FILE"),
        ("site", "--model-sources FILE"),
        ("site", "recorded runs of the model arm with and without a source (scripts/model_source_value.py); repeatable"),
        ("site", "the forged copy made by forge-demo, shown on the Verify page as a forgery"),
        ("rebase-clock", "what was checked (for example two independent time references)"),
    ):
        _, sub, _ = call_raw(mod, command, "--help")
        c.eq(f"{command} --help says {phrase!r}", _says(sub, phrase), True)
    _, sub, _ = call_raw(mod, "make-protocol", "--help")
    c.eq(
        "both mapping options show their metavar, in the usage and in the list",
        len(re.findall(r"ARM=FILE(?=[\s\]]|$)", " ".join(sub.split()))),
        4,
    )
    # one line of JSON, ending with a newline, and flushed
    key = root / "k"
    _code, text, _ = call_raw(mod, "init-key", str(key), "hoshitori.example/log")
    c.eq("one JSON line, ending with one newline", (text.count("\n"), text.endswith("}\n"), json.loads(text)["file"]), (1, True, str(key)))

    class Counting(io.StringIO):
        flushed = 0

        def flush(self) -> None:
            Counting.flushed += 1
            super().flush()

    recorder = Counting()
    with contextlib.redirect_stdout(recorder):
        mod.main(["init-key", str(root / "k2"), "hoshitori.example/log"])
    c.eq("and flushed, so that a pipe sees each tick when it happens", Counting.flushed >= 1, True)
    return c.bad


def check_make_protocol_options(mod: types.ModuleType, root: Path) -> list[str]:
    from hoshitori import canonical, protocol, rules_arm
    from tests import root_support as R
    from tests.rules_arm_checks import graded

    c = Case(mod)
    (root / "root.json").write_bytes(canonical.dumps(R.make_document(1, root_keys=[R.new_signer(1)])))  # type: ignore[arg-type]
    (root / "map.json").write_bytes(rules_arm.dump(graded(rules_arm)))
    full = {
        "--root": str(root / "root.json"),
        "--mapping": f"L0={root / 'map.json'}",
        "--name": "x",
        "--start-ms": "1793275200000",
        "--every-minutes": "60",
        "--watchlist": "BTC,ETH",
    }
    for left_out in full:
        argv = ["make-protocol", str(root / f"p{left_out}.json")]
        for option, value in full.items():
            if option != left_out:
                argv += [option, value]
        code, _, err = call(mod, *argv)
        c.eq(f"{left_out} is required, and the parser says so", (code, "arguments are required" in err and left_out in err), (2, True))
    argv = ["make-protocol", str(root / "default.json")] + [x for option, value in full.items() for x in (option, value)]
    code, _lines, _ = call(mod, *argv)
    written = protocol.load((root / "default.json").read_bytes())
    c.eq(
        "the primary and second venues default to binance-spot and okx",
        (code, written.primary_venue, written.second_venue),
        (0, "binance-spot", "okx"),
    )
    from hoshitori import barrier_rates
    from tests.league_checks import barrier_map

    barrier = barrier_map()
    (root / "barrier.json").write_bytes(barrier_rates.dump(barrier))
    argv = ["make-protocol", str(root / "barrier_p.json"), "--barrier-mapping", f"L0={root / 'barrier.json'}"] + [
        x for option, value in full.items() for x in (option, value)
    ]
    code, _, _ = call(mod, *argv)
    c.eq(
        "a barrier mapping can be pinned from the command",
        (code, protocol.load((root / "barrier_p.json").read_bytes()).barrier_arms),
        (0, (("L0", barrier.sha256),)),
    )
    c.eq("and there is none by default", written.barrier_arms, ())
    argv = ["make-protocol", str(root / "named.json"), "--primary", "okx", "--second", "binance-spot"] + [
        x for option, value in full.items() for x in (option, value)
    ]
    call(mod, *argv)
    named = protocol.load((root / "named.json").read_bytes())
    c.eq("and may be named", (named.primary_venue, named.second_venue), ("okx", "binance-spot"))
    from hoshitori import model_settings
    from tests.deployment_checks import models_document

    (root / "models.json").write_text(json.dumps(models_document("http://127.0.0.1:1/v1")))
    versions = model_settings.load(root / "models.json").versions()
    argv = ["make-protocol", str(root / "models_p.json"), "--models", str(root / "models.json"), "--cutoff", "L1=1:200000,4:500000"] + [
        x for option, value in full.items() for x in (option, value)
    ]
    argv += ["--cutoff", "L2=4:500000", "--cutoff", "L1x=4:500000"]
    code, _, _ = call(mod, *argv)
    c.eq(
        "model arms can be pinned from the command, with their cutoffs",
        (code, protocol.load((root / "models_p.json").read_bytes()).model_arms),
        (
            0,
            (
                ("L1", versions["L1"], ((1, 200_000), (4, 500_000))),
                ("L1x", versions["L1x"], ((4, 500_000),)),
                ("L2", versions["L2"], ((4, 500_000),)),
            ),
        ),
    )
    c.eq("and there are none by default", written.model_arms, ())
    code, _, err = call(
        mod,
        "make-protocol",
        str(root / "no_cutoff.json"),
        "--models",
        str(root / "models.json"),
        *[x for option, value in full.items() for x in (option, value)],
    )
    c.eq(
        "a model settings file with no cutoffs is refused by the command",
        (code, "each model arm in the model settings has its cutoffs" in err),
        (2, True),
    )
    return c.bad


def check_tick_lines(mod: types.ModuleType, root: Path) -> list[str]:
    from hoshitori import cycle, deployment
    from hoshitori.collect import now_ms
    from hoshitori.league import put

    c = Case(mod)
    clock = OffsetClock()
    with outside(clock) as world:
        settings = world_files(root, world, ago_minutes=185)
        with deployment.assemble(deployment.load_settings(settings), clock=clock) as running:
            rules = running.services.protocol
            planned = cycle.begin(rules.sha256, rules.slot(0), now_ms(clock))
            put(running.ledger, [cycle.to_record(planned[0], running.services.source_urn, planned[0].step.at_ms)])
        _code, lines, _ = call(mod, "run", str(settings), "--once")
        (tick,) = lines
        _, text, _ = call_raw(mod, "status", str(settings))
        keys = list(json.loads(text))
        c.eq("the keys of a line are sorted", (keys, keys == sorted(keys)), (keys, True))
        c.eq("a cycle a lost process left is closed", tick["closed"], [{"cycle": planned[0].cycle_id, "was": "PLANNED", "slots_failed": 0}])
        c.eq(
            "and the heartbeat tells what became of the slots before this one",
            tick["findings"],
            [
                {"slot": rules.slot(0), "finding": "ABANDONED"},
                {"slot": rules.slot(1), "finding": "MISSING"},
                {"slot": rules.slot(2), "finding": "MISSING"},
            ],
        )
        c.eq(
            "the cycle of this slot",
            (tick["ran"]["state"], tick["ran"]["level"], tick["ran"]["slots"]),
            ("COMMITTED", "C3", {"FORECAST": 6}),
        )
    return c.bad


def check_signals(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    installed: dict[int, Any] = {}
    real_signal, real_main = signal.signal, threading.main_thread
    with outside(OffsetClock()) as world:
        settings = str(world_files(root, world))
        signal.signal = lambda number, handler: installed.__setitem__(number, handler)  # type: ignore[assignment,misc]
        threading.main_thread = threading.current_thread  # type: ignore[assignment]
        try:
            call(mod, "run", settings, "--once")
            c.eq("handlers for an interrupt and a terminate", sorted(installed), sorted([signal.SIGINT, signal.SIGTERM]))
            installed.clear()
            timer = threading.Timer(2.0, lambda: installed[signal.SIGTERM](signal.SIGTERM, None))
            timer.start()
            code, lines, _ = call(mod, "run", settings)
            timer.cancel()
            c.eq("a terminate signal stops the loop after its tick, cleanly", (code, len(lines) >= 1), (0, True))
        finally:
            signal.signal, threading.main_thread = real_signal, real_main
    return c.bad


def check_voices_command(mod: types.ModuleType, root: Path) -> list[str]:
    """``signals CONFIG OUT``: what is read is counted and listed, a bad settings file is refused, and the document is written."""
    c = Case(mod)
    out = root / "signals.json"
    config = root / "empty.json"
    config.write_text("{}")
    code, lines, _ = call(mod, "signals", str(config), str(out))
    doc = json.loads(out.read_text())
    c.eq("nothing named, nothing read", (code, lines), (0, [{"accounts_read": 0, "out": str(out), "wallets_read": 0}]))
    c.eq(
        "the document names its format, its three tokens and what it does not know",
        (doc["format"], [t["symbol"] for t in doc["tokens"]], len(doc["not_known"]) > 0),
        (1, ["BTC", "ETH", "SOL"], True),
    )
    config.write_text(json.dumps({"symbols": ["SOL"]}))
    call(mod, "signals", str(config), str(out))
    c.eq("a token asked for is the only one", [t["symbol"] for t in json.loads(out.read_text())["tokens"]], ["SOL"])
    inbox = root / "inbox.json"
    inbox.write_text(json.dumps({"posts": [{"id": "1", "author": "x", "text": "SOL up", "created_at": "2026-11-04T09:00:00Z"}]}))
    config.write_text(json.dumps({"inbox": "inbox.json", "symbols": ["SOL"]}))
    code, lines, _ = call(mod, "signals", str(config), str(out))
    doc = json.loads(out.read_text())
    c.eq("a read source is counted", (code, lines[0]["accounts_read"], lines[0]["wallets_read"]), (0, 1, 0))
    c.eq("and listed with its posts", doc["accounts"], [{"actor": "connector inbox", "posts": 1, "reason": None, "state": "READ"}])
    inbox.write_text("nope")
    code, lines, _ = call(mod, "signals", str(config), str(out))
    c.eq(
        "an unreadable inbox is listed as failed and not counted",
        (code, lines[0]["accounts_read"], json.loads(out.read_text())["accounts"][0]["state"]),
        (0, 0, "FAILED"),
    )
    from hoshitori import signals as signals_module

    real_collect = signals_module.collect
    signals_module.collect = lambda settings: {  # type: ignore[assignment]
        "accounts": [{"state": "READ"}, {"state": "FAILED"}],
        "wallets": [{"state": "READ"}, {"state": "READ"}, {"state": "FAILED"}],
        "tokens": [],
    }
    try:
        code, lines, _ = call(mod, "signals", str(config), str(out))
    finally:
        signals_module.collect = real_collect  # type: ignore[assignment]
    c.eq(
        "the accounts and the wallets that were read are counted apart",
        (code, lines[0]["accounts_read"], lines[0]["wallets_read"]),
        (0, 1, 2),
    )
    for words, needle in (
        (("--help",), "read the public voices and wallets named in a file once and write the document the Voices page shows"),
        (("--help",), "serve the site, and read the claims that are pasted into its Check a claim page"),
        (("serve", "--help"), "the folder `site` wrote, with the built page beside its documents"),
        (
            ("serve", "--help"),
            "the file that keeps the evidence challenges people file on receipts (made if it is not there); without it none are taken",
        ),
        (("serve", "--help"), "the model settings: the single judgment's model reads the claims; without them none is read"),
        (("site", "--help"), "the collected voices and wallets (the signals command) for the Voices page"),
    ):
        _, text, _ = call_raw(mod, *words)
        flat, want = " ".join(text.split()), " ".join(needle.split())
        c.eq(f"{words[0]} says: {needle[:30]}", (want + " " in flat + " ") and (want + "~") not in flat, True)
    config.write_text("not json")
    code, lines, _ = call(mod, "signals", str(config), str(out))
    c.eq("a settings file that is not JSON is refused", (code, list(lines[0])), (2, ["error"]))
    code, lines, _ = call(mod, "signals", str(root / "missing.json"), str(out))
    c.eq("a missing settings file is refused", (code, list(lines[0])), (2, ["error"]))
    return c.bad


def check_serve_command(mod: types.ModuleType, root: Path) -> list[str]:
    """``serve SITE``: the folder is checked, the model's key is read from its variable and never from the file, the reader is passed on."""
    import os
    from types import SimpleNamespace

    from hoshitori import model_settings, serve

    c = Case(mod)
    site = root / "site"
    site.mkdir()
    served: list[tuple[Any, ...]] = []
    opened: list[tuple[Any, ...]] = []
    sentinel = object()

    @contextlib.contextmanager
    def fake_open(base_url: str, key: Any, model: str) -> Any:
        opened.append((base_url, key(), model))
        yield sentinel

    real = (serve.serve, serve.open_reader, model_settings.load)
    serve.serve = lambda *args: served.append(args)  # type: ignore[assignment]
    serve.open_reader = fake_open  # type: ignore[assignment]
    here = Path(mod.__file__).resolve().parents[2]
    try:
        code, lines, _ = call(mod, "serve", str(site))
        c.eq(
            "no model: no reader",
            (code, lines, served),
            (
                0,
                [{"address": "http://127.0.0.1:8080/", "reads_claims": False, "serving": str(site), "takes_challenges": False}],
                [(site, None, "127.0.0.1", 8080, here, None)],
            ),
        )
        served.clear()
        kept = root / "challenges" / "c.jsonl"
        code, lines, _ = call(mod, "serve", str(site), "--challenges", str(kept))
        c.eq(
            "a file for challenges: it is made and handed on",
            (code, lines[0]["takes_challenges"], kept.exists(), served[0][5] is not None and served[0][5].records() == []),
            (0, True, True, True),
        )
        served.clear()
        code, lines, _ = call(mod, "serve", str(site), "--host", "0.0.0.0", "--port", "9100")  # noqa: S104
        c.eq("a host and a port", (lines[0]["address"], served[0][2:4]), ("http://0.0.0.0:9100/", ("0.0.0.0", 9100)))  # noqa: S104
        code, _, err = call_raw(mod, "serve", str(root / "nothing"))[0], None, call_raw(mod, "serve", str(root / "nothing"))[2]
        c.eq("a folder that is not one is refused", (code, "is not a folder" in err), (2, True))
        model_settings.load = lambda path: SimpleNamespace(l1=None, key_env="HOSHITORI_TEST_KEY", base_url="http://m")  # type: ignore[assignment]
        code, _, err = (
            call_raw(mod, "serve", str(site), "--models", "m.json")[0],
            None,
            call_raw(mod, "serve", str(site), "--models", "m.json")[2],
        )
        c.eq("settings with no single judgment are refused", (code, "no single judgment" in err), (2, True))
        model_settings.load = lambda path: SimpleNamespace(
            l1=SimpleNamespace(model="the-model"), key_env="HOSHITORI_TEST_KEY", base_url="http://m"
        )  # type: ignore[assignment]
        os.environ.pop("HOSHITORI_TEST_KEY", None)
        code, _, err = (
            call_raw(mod, "serve", str(site), "--models", "m.json")[0],
            None,
            call_raw(mod, "serve", str(site), "--models", "m.json")[2],
        )
        c.eq("a key that is not set is refused, naming its variable", (code, "HOSHITORI_TEST_KEY is empty" in err, opened), (2, True, []))
        os.environ["HOSHITORI_TEST_KEY"] = "the-key"
        served.clear()
        code, lines, _ = call(mod, "serve", str(site), "--models", "m.json")
        c.eq(
            "with a model the claims are read",
            (code, lines[0]["reads_claims"], opened, served[0][1] is sentinel),
            (0, True, [("http://m", "the-key", "the-model")], True),
        )
    finally:
        serve.serve, serve.open_reader, model_settings.load = real  # type: ignore[assignment]
        os.environ.pop("HOSHITORI_TEST_KEY", None)
    return c.bad


def snapshot(directory: Path) -> dict[str, tuple[int, int, str]]:
    """Every file under the folder: size, modification time and SHA-256."""
    return {
        p.relative_to(directory).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def check_rebase_clock(mod: types.ModuleType, root: Path) -> list[str]:
    """``rebase-clock`` is the operator's explicit confirmation of a stepped host clock: it needs a stated reason, and it refuses to
    confirm a step that was never refused."""
    c = Case(mod)
    with outside(OffsetClock()) as world:
        settings = str(world_files(root, world))
        call(mod, "run", settings, "--once")
        code, _, err = call(mod, "rebase-clock", settings)
        c.eq("a rebase without a reason is a usage error that names the option", (code, "--reason" in err), (2, True))
        code, _, err = call(mod, "rebase-clock", settings, "--reason", "checked against two references")
        c.eq("a step that was never refused cannot be confirmed", (code, "no refused clock step" in err), (2, True))
        events = deployment.load_settings(Path(settings)).directory / "ledger" / "clock_events.jsonl"
        events.write_text(
            json.dumps({"kind": "forward_step", "at": "2026-10-29T13:00:00.000Z", "reference": "2026-10-29T12:00:00.000Z"}) + "\n"
        )
        code, lines, err = call(mod, "rebase-clock", settings, "--reason", "checked against two references")
        c.eq("a refused step can be confirmed, with its reason", (code, err, [line.get("rebased") for line in lines]), (0, "", [True]))
        c.eq(
            "and the line says what was recorded: the confirmation, last of the events",
            (sorted(lines[0]), lines[0]["events"]["kind"], lines[0]["events"]["reason"]),
            (["events", "rebased"], "rebase", "checked against two references"),
        )
        code, lines, _ = call(mod, "status", settings)
        c.eq(
            "and the status then says it no longer stands",
            (code, lines[0]["clock"]["standing"], lines[0]["clock"]["events"]),
            (0, False, 2),
        )
        c.eq("the confirmation is a record in the ledger", lines[0]["records"] > 0, True)
        code, text, _ = call_raw(mod, "rebase-clock", "--help")
        c.eq("the command is documented", (code, "--reason" in text), (0, True))
    return c.bad


def check_forgery_help(mod: types.ModuleType, root: Path) -> list[str]:
    """What the two forgery commands say of themselves: a person who asks for help is told what they do."""
    c = Case(mod)
    code, text, _ = call_raw(mod, "--help")
    words = " ".join(text.split())
    c.eq(
        "the command list names forge-demo and what it makes",
        (code, "forge-demo" in words, "a forged copy of a committed cycle's bundle, to show what the time-stamps are for" in words),
        (0, True, True),
    )
    code, text, _ = call_raw(mod, "site", "--help")
    words = " ".join(text.split())
    c.eq(
        "site names --forgery and what it shows",
        (code, "[--forgery DIR]" in words, "the forged copy made by forge-demo, shown on the Verify page as a forgery" in words),
        (0, True, True),
    )
    return c.bad


def check_readers_do_not_write(mod: types.ModuleType, root: Path) -> list[str]:
    """``status``, ``site``, ``export``, ``replay`` and ``forge-demo`` look and do not write (finding F3): they run beside a league that holds the ledger,
    need no log key, and leave every file of the league's state exactly as it was; a failure they find is not saved. A league that has
    never run has no ledger to read, and reading does not make one."""
    c = Case(mod)
    with outside(OffsetClock()) as world:
        settings = str(world_files(root, world))
        code, lines, err = call(mod, "run", settings, "--once")
        c.eq("a cycle to read", (code, err), (0, ""))
        cycle_id = lines[0]["ran"]["cycle"]
        loaded = mod.deployment.load_settings(Path(settings))
        with mod.deployment.assemble(loaded):  # a running league: it holds the ledger's lock and the capture store's
            moved = loaded.log_key.with_name("log.key.moved")
            loaded.log_key.rename(moved)  # the readers have no use for the log key
            try:
                before = snapshot(loaded.directory)
                code, lines, err = call(mod, "status", settings)
                got = lines[0] if lines else {}
                c.eq(
                    "status beside the writer, with no key",
                    (code, err, got.get("ledger"), got.get("cycles"), got.get("forecasts"), got.get("failures")),
                    (0, "", {"state": "OK", "detail": ""}, {"COMMITTED": 1}, 6, 0),
                )
                code, lines, err = call(mod, "replay", settings, cycle_id)
                got = lines[0] if lines else {}
                c.eq("replay beside the writer", (code, err, got.get("clean"), got.get("match")), (0, "", True, 6))
                code, lines, err = call(mod, "export", settings, cycle_id, str(root / "bundle"))
                got = lines[0] if lines else {}
                held = len(list((root / "bundle" / "items").glob("*.canonical.json")))
                c.eq(
                    "export beside the writer",
                    (code, err, got.get("records", 0) + got.get("later_records", 0) == held and held > 12),
                    (0, "", True),
                )
                code, lines, err = call(mod, "site", settings, str(root / "site"))
                got = lines[0] if lines else {}
                c.eq("site beside the writer", (code, err, got.get("bundles")), (0, "", [cycle_id]))
                code, lines, err = call(mod, "forge-demo", settings, cycle_id, str(root / "forged"))
                got = lines[0] if lines else {}
                c.eq(
                    "forge-demo beside the writer, with no key, makes the copy, its document and its root",
                    (code, err, got.get("cycle"), sorted(p.name for p in (root / "forged").iterdir())),
                    (0, "", cycle_id, ["bundle", "forgery.json", "trusted_root.json"]),
                )
                code, lines, err = call(mod, "site", settings, str(root / "site-forged"), "--forgery", str(root / "forged"))
                got = lines[0] if lines else {}
                c.eq(
                    "site --forgery beside the writer shows it, apart from the bundles",
                    (
                        code,
                        err,
                        got.get("bundles"),
                        (root / "site-forged" / "forgery.json").is_file(),
                        (root / "site-forged" / "forged" / cycle_id).is_dir(),
                    ),
                    (0, "", [cycle_id], True, True),
                )
                code, lines, err = call(mod, "forge-demo", settings, cycle_id, str(root / "forged"))
                c.eq(
                    "a second forge-demo into the same directory is refused",
                    (code, lines, err),
                    (2, [], "hoshitori-league: the output directory must be new or empty\n"),
                )
                c.eq("the state of the league is exactly as it was", snapshot(loaded.directory) == before, True)
            finally:
                moved.rename(loaded.log_key)
        ledger_dir = loaded.directory / "ledger"
        (ledger_dir / "segments" / "segment-000000000000.jsonl").write_bytes(b"not a segment\n")
        before = snapshot(loaded.directory)
        code, lines, err = call(mod, "status", settings)
        got = lines[0] if lines else {}
        c.eq(
            "a ledger that is not clean is shown as it is",
            (code, (got.get("ledger") or {}).get("state"), got.get("records")),
            (0, "INTEGRITY_FAILURE", None),
        )
        c.eq(
            "and the failure is not saved into STATE",
            ((ledger_dir / "STATE").exists(), snapshot(loaded.directory) == before),
            (False, True),
        )
        (root / "fresh").mkdir()
        fresh = world_files(root / "fresh", world)
        state = mod.deployment.load_settings(fresh).directory
        code, lines, err = call(mod, "status", str(fresh))
        c.eq(
            "a league that has never run has no ledger to read",
            (code, lines, err),
            (2, [], f"hoshitori-league: there is no ledger in {state / 'ledger'}\n"),
        )
        c.eq("and reading made none", state.exists(), False)
    return c.bad


def check_ryo_run(mod: types.ModuleType, root: Path) -> list[str]:
    """A league that reads RYO reads its tool list once before the first cycle, says what it showed, and goes on whatever it showed."""
    import os

    from tests.deployment_checks import RYO_KEY_ENV, good_ryo, ryo_script
    from tests.ryo_fake_server import KEY, Behaviour, FakeRyo, catalogue_body

    c = Case(mod)
    clock = OffsetClock()
    previous = os.environ.get(RYO_KEY_ENV)
    os.environ[RYO_KEY_ENV] = KEY

    def listing(change: str | None) -> Any:
        base = ryo_script(clock)

        def script(seen: Any) -> Any:
            if seen.method == "GET" and seen.path.endswith("/api/mcp/tools"):
                if change == "down":  # an answer that is not a tool list: not retried, so the check does not wait out a deadline
                    return Behaviour(200, b"this is not a tool list", {"content-type": "application/json"})
                if change == "drift":
                    body = catalogue_body().replace(
                        b'"symbol":{"type":"string","description":"Symbol."}', b'"symbol":{"type":"number","description":"Symbol."}'
                    )
                    return Behaviour(200, body, {"content-type": "application/json"})
            return base(seen)

        return script

    try:
        for label, change, want in (
            ("as pinned", None, {"drift": [], "ryo_catalogue": "as pinned"}),
            (
                "a tool whose structure changed",
                "drift",
                {
                    "drift": [
                        "analyze_token: SCHEMA_CHANGED (unavailable until looked at)",
                        "deep_analysis: SCHEMA_CHANGED (unavailable until looked at)",
                    ],
                    "ryo_catalogue": "drift",
                },
            ),
        ):
            fake = FakeRyo(script=listing(change)).start()
            try:
                with outside(clock) as world:
                    (root / label.split()[0]).mkdir()
                    settings = str(
                        world_files(
                            root / label.split()[0], world, optional_sources=("ryo",), ryo={**good_ryo(fake.url), "calls_per_minute": 600}
                        )
                    )
                    code, lines, err = call(mod, "run", settings, "--once")
                    c.eq(f"{label}: the catalogue line, then the tick", (code, err, len(lines)), (0, "", 2))
                    c.eq(f"{label}: what the tool list showed", lines[0], want)
                    c.eq(f"{label}: and the cycle ran either way", lines[-1]["ran"]["state"], "COMMITTED")
                    c.eq(
                        f"{label}: the list was asked for once",
                        sum(1 for s in fake.seen if s.method == "GET" and s.path.endswith("/api/mcp/tools")),
                        1,
                    )
            finally:
                fake.stop()
        fake = FakeRyo(script=listing("down")).start()
        try:
            with outside(clock) as world:
                (root / "down").mkdir()
                settings = str(
                    world_files(root / "down", world, optional_sources=("ryo",), ryo={**good_ryo(fake.url), "calls_per_minute": 600})
                )
                code, lines, err = call(mod, "run", settings, "--once")
                c.eq(
                    "a tool list that cannot be read: the line says so, and the cycle runs",
                    (code, lines[0]["ryo_catalogue"], lines[-1]["ran"]["state"]),
                    (0, "not read", "COMMITTED"),
                )
                c.eq("with the reason", lines[0]["message"].startswith("RYO's tool list could not be read: "), True)
        finally:
            fake.stop()
        with outside(clock) as world:
            (root / "none").mkdir()
            code, lines, err = call(mod, "run", str(world_files(root / "none", world)), "--once")
            c.eq("a league without RYO prints no catalogue line", (code, len(lines), "ryo_catalogue" in lines[0]), (0, 1, False))
    finally:
        if previous is None:
            os.environ.pop(RYO_KEY_ENV, None)
        else:
            os.environ[RYO_KEY_ENV] = previous
    return c.bad


def check_catalogue_line(mod: types.ModuleType, root: Path) -> list[str]:
    """What the line before the first cycle says, with the tool-list read replaced by a stand-in so that each answer is exact."""
    from types import SimpleNamespace

    from hoshitori.ryo_gateway import RyoGatewayError

    c = Case(mod)
    real = mod.refresh_catalogue
    running = SimpleNamespace(captures=object())
    wiring = SimpleNamespace(env=SimpleNamespace(runner=object(), clock=object()), scan_wait_s=200.0)
    attached = SimpleNamespace(adapter=object())
    try:
        mod.refresh_catalogue = lambda *args: (_ for _ in ()).throw(RyoGatewayError("x" * 400))
        line = mod._catalogue_line(running, attached, wiring)
        c.eq(
            "a tool list that cannot be read: the state and the message cut to 300 characters",
            line,
            {"ryo_catalogue": "not read", "message": "x" * 300},
        )
        mod.refresh_catalogue = lambda *args: (_ for _ in ()).throw(RyoGatewayError("short"))
        c.eq("a short message is kept whole", mod._catalogue_line(running, attached, wiring)["message"], "short")
        mod.refresh_catalogue = lambda *args: []
        c.eq("nothing changed", mod._catalogue_line(running, attached, wiring), {"drift": [], "ryo_catalogue": "as pinned"})
    finally:
        mod.refresh_catalogue = real
    return c.bad


def check_failed_lists(mod: types.ModuleType, root: Path) -> list[str]:
    """A list the registry could not read is named in the tick line, with its venue, scope, reason and what the venue said."""
    from types import SimpleNamespace

    from hoshitori.registry_job import ListFailure, Refresh

    c = Case(mod)
    failed = ListFailure("binance-spot", "spot", "BAD_REQUEST", "binance-spot lists instruments of kind [], not 'spot'")
    item = SimpleNamespace(
        now_ms=1,
        closed=(),
        refreshed=Refresh((), (failed, ListFailure("kraken", "spot", "TIMED_OUT", "x" * 500))),
        ran=None,
        passed=None,
        practiced=None,
        on_demand=None,
        on_demand_failed=None,
        planned=None,
        desked=None,
        desk_failed=None,
        findings=(),
    )
    line = mod._tick_line(item)
    c.eq("the count and the lists", line["refreshed"]["failures"], 2)
    c.eq(
        "each failed list, its reason and its detail cut to two hundred characters",
        line["refreshed"]["failed"],
        [
            {
                "venue": "binance-spot",
                "scope": "spot",
                "reason": "BAD_REQUEST",
                "detail": "binance-spot lists instruments of kind [], not 'spot'",
            },
            {"venue": "kraken", "scope": "spot", "reason": "TIMED_OUT", "detail": "x" * 200},
        ],
    )
    c.eq("no refresh, no lists", mod._tick_line(SimpleNamespace(**{**vars(item), "refreshed": None}))["refreshed"], None)
    # a slot the pass could not move: which one and why, not only how many
    passed = SimpleNamespace(
        records=(), pending=(), deferred=(), failed=(("fc1", "step 6 of fc1 arrived when 0 were known"), ("fc2", "y" * 500))
    )
    line = mod._tick_line(SimpleNamespace(**{**vars(item), "passed": passed}))
    c.eq("the count of slots that could not be moved", line["settled"]["failed"], 2)
    c.eq(
        "and each slot with the reason it was refused, cut to two hundred characters",
        line["settled"]["failures"],
        [{"slot": "fc1", "reason": "step 6 of fc1 arrived when 0 were known"}, {"slot": "fc2", "reason": "y" * 200}],
    )
    c.eq("no on-demand pass, nothing said", line["on_demand"], None)
    ran = SimpleNamespace(records=("a", "b"), pending=("x",), deferred=(), failed=())
    said = mod._tick_line(SimpleNamespace(**{**vars(item), "on_demand": ran}))["on_demand"]
    c.eq("an on-demand pass is counted", said, {"records": 2, "pending": 1, "deferred": 0, "failed": None})
    said = mod._tick_line(
        SimpleNamespace(**{**vars(item), "on_demand": SimpleNamespace(records=(), pending=(), deferred=("y", "z"), failed=())})
    )
    c.eq("with its deferred slots", said["on_demand"]["deferred"], 2)
    broke = mod._tick_line(SimpleNamespace(**{**vars(item), "on_demand_failed": "RuntimeError: the venue was down"}))["on_demand"]
    c.eq(
        "a pass that failed says why and counts nothing",
        broke,
        {"records": 0, "pending": 0, "deferred": 0, "failed": "RuntimeError: the venue was down"},
    )
    c.eq("no read of RYO and no Desk, nothing said", (line["planned"], line["desk"]), (None, None))
    plans = SimpleNamespace(records=("p1",), pending=("r2", "r3"), failed=(("r4", "NO_PAIR " + "z" * 300),))
    c.eq(
        "arm R's plans: settled, waiting, and each that could not be settled with its reason cut to two hundred characters",
        mod._tick_line(SimpleNamespace(**{**vars(item), "planned": plans}))["planned"],
        {"records": 1, "pending": 2, "failed": [{"read": "r4", "reason": ("NO_PAIR " + "z" * 300)[:200]}]},
    )
    desked = SimpleNamespace(outcomes=(("t1", "COMMITTED", "fc1"), ("t2", "ABSTAINED", None)), failed=(("t3", "LiveError: x"),))
    c.eq(
        "the Desk's runs, their ends and forecasts, and a run that raised",
        mod._tick_line(SimpleNamespace(**{**vars(item), "desked": desked}))["desk"],
        {
            "runs": [{"trigger": "t1", "state": "COMMITTED", "forecast": "fc1"}, {"trigger": "t2", "state": "ABSTAINED", "forecast": None}],
            "raised": [{"trigger": "t3", "reason": "LiveError: x"}],
            "failed": None,
        },
    )
    c.eq(
        "a Desk step that failed says why",
        mod._tick_line(SimpleNamespace(**{**vars(item), "desk_failed": "RuntimeError: boom"}))["desk"],
        {"runs": [], "raised": [], "failed": "RuntimeError: boom"},
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_usage,
        check_commands,
        check_output_and_help,
        check_make_protocol_options,
        check_tick_lines,
        check_failed_lists,
        check_signals,
        check_readers_do_not_write,
        check_forgery_help,
        check_rebase_clock,
        check_ryo_run,
        check_catalogue_line,
        check_voices_command,
        check_serve_command,
    ):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                found = check(mod, Path(tmp))
            bad += [f"{check.__name__}: {message}" for message in found]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}")
    return bad
