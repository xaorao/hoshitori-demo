"""Scenarios for the venue survey, runnable against the real module or a mutant of it (``failures(mod)``).

The outside world is the loopback exchange of ``e2e_league``: it speaks the public routes of Binance spot and of OKX (time, instrument
lists with BTC, ETH and SOL quoted in USDT, one-minute bars with a volume of 12.5 for every minute) and answers 404 to anything else, so
Kraken pointed at it cannot read its list. Everything inside the product is real: the gateway, the runner, the venue adapters and HTTP
transports, the registry and its two-operator rule, the oracle rule and the venue probe. Expected values are worked out from those rules
and from what the fake serves (``e2e_league.FakeExchange``), never taken from the survey's own output.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hoshitori.runner import Runner
from tests.e2e_league import FakeExchange, OffsetClock, Seen
from tests.profiles_okx import spot_row
from tests.ryo_fake_server import Behaviour

MINUTES = 5
SPOT = ("binance-spot", "coinbase", "hyperliquid", "kraken", "okx")
WORKERS = 4  # how many calls to the venues the survey has in flight at most
STOP_WAIT_S = 5  # how long the survey waits for its loop to stop before it returns anyway


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str) -> None:
        try:
            call()
        except self.mod.SurveyError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


class Gappy(FakeExchange):
    """The loopback exchange, except that OKX serves no one-minute bar for SOL-USDT."""

    def default(self, seen: Seen) -> Behaviour:
        if seen.path.startswith("/api/v5/market/history-candles") and "instId=SOL-USDT" in seen.path and "bar=1m" in seen.path:
            return self.json({"code": "0", "msg": "", "data": []})
        return super().default(seen)


class Clockless(FakeExchange):
    """The loopback exchange, except that OKX does not tell its time, and Binance refuses the bars of SOLUSDT."""

    def default(self, seen: Seen) -> Behaviour:
        if seen.path.startswith("/api/v5/public/time") or (seen.path.startswith("/api/v3/klines") and "symbol=SOLUSDT" in seen.path):
            return Behaviour(404, b'{"message":"no such route"}', {"content-type": "application/json"})
        return super().default(seen)


class Suspended(FakeExchange):
    """The loopback exchange, except that OKX lists SOL-USDT as suspended: SOL still has two venues behind its symbol, so the registry
    resolves it, but one of its two instruments does not trade."""

    def default(self, seen: Seen) -> Behaviour:
        if seen.path.startswith("/api/v5/public/instruments"):
            rows = [
                spot_row("BTC-USDT", "BTC", "USDT"),
                spot_row("ETH-USDT", "ETH", "USDT"),
                spot_row("SOL-USDT", "SOL", "USDT", "suspend"),
            ]
            return self.json({"code": "0", "msg": "", "data": rows})
        return super().default(seen)


class Slow(FakeExchange):
    """The loopback exchange answering each request after a fifth of a second, counting how many it holds at once."""

    def __init__(self, clock: OffsetClock) -> None:
        super().__init__(clock)
        self.lock = threading.Lock()
        self.held = 0
        self.most = 0

    def default(self, seen: Seen) -> Behaviour:
        with self.lock:
            self.held += 1
            self.most = max(self.most, self.held)
        time.sleep(0.2)
        with self.lock:
            self.held -= 1
        return super().default(seen)


class Watching(OffsetClock):
    """The test clock, which also notes, each time it is read, the thread that reads it and what the survey has in the temporary folder."""

    def __init__(self, temporary: Path) -> None:
        super().__init__()
        self.temporary = temporary
        self.threads: set[tuple[str, bool]] = set()
        self.folders: set[str] = set()
        self.inside: set[tuple[str, bool]] = set()

    def now(self) -> Any:
        here = threading.current_thread()
        self.threads.add((here.name, here.daemon))
        with contextlib.suppress(OSError):
            for folder in self.temporary.iterdir():
                self.folders.add(folder.name)
                for item in folder.iterdir():
                    self.inside.add((item.name, item.is_dir()))
        return super().now()


def open_descriptors() -> set[int]:
    return {int(name) for name in os.listdir("/dev/fd")}


@contextlib.contextmanager
def world(kind: type[FakeExchange] = FakeExchange) -> Any:
    clock = OffsetClock()
    exchange = kind(clock)
    exchange.start()
    try:
        yield clock, exchange
    finally:
        exchange.stop()


def survey(
    mod: types.ModuleType, clock: Any, urls: dict[str, str], venues: tuple[str, ...], watchlist: tuple[str, ...], wait_s: float = 10
) -> Any:
    return mod.run(venues, watchlist, minutes=MINUTES, wait_s=wait_s, base_urls=urls, allow_loopback=True, clock=clock)


def _counts(finding: Any) -> tuple[Any, ...]:
    if finding.coverage is None:
        return (finding.instrument, None, None, None)
    return (finding.instrument, finding.coverage.closed_slots, finding.coverage.present, finding.coverage.padded)


def as_rows(found: Any) -> dict[str, Any]:
    return {
        "lists": [(r.venue, r.read, r.detail) for r in found.lists],
        "probed": [(f.venue, f.reachable, [_counts(i) for i in f.instruments]) for f in found.probes],
        "pairs": [
            (p.primary, p.second, p.can_settle, [(t.symbol, t.primary, t.second, t.can_settle, t.why) for t in p.tokens])
            for p in found.pairs
        ],
        "usable": list(found.usable),
    }


def check_constants(mod: types.ModuleType, tmp: Path) -> list[str]:
    from hoshitori import venues

    c = Case(mod)
    c.eq("the spot venues the survey can read", mod.SPOT_VENUES, SPOT)
    c.eq("each is a venue of the product", all(v in venues.VENUES for v in mod.SPOT_VENUES), True)
    c.eq("the defaults", (mod.DEFAULT_MINUTES, mod.DEFAULT_WAIT_S), (60, 30.0))
    pick = mod.TokenPick("BTC", "BTCUSDT", "BTC-USDT", True, None)
    pair = mod.PairResult("binance-spot", "okx", (pick,))
    read = mod.ListRead("okx", True, "accepted")
    for label, value, member in (
        ("a list read", read, "read"),
        ("a token's pick", pick, "can_settle"),
        ("a pair's result", pair, "tokens"),
        ("a survey", mod.Survey((read,), (), (pair,)), "pairs"),
    ):
        try:
            setattr(value, member, getattr(value, member))
            c.bad.append(f"{label}: its {member} can be set after it is made")
        except dataclasses.FrozenInstanceError:
            pass
    return c.bad


def check_two_venues_that_answer(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    with world() as (clock, exchange):
        urls = {"binance-spot": exchange.url, "okx": exchange.url, "kraken": exchange.url}
        found = as_rows(survey(mod, clock, urls, ("okx", "kraken", "binance-spot"), ("SOL", "BTC", "ETH")))
    c.eq(
        "the lists: two read, Kraken's refused by the fake",
        [(v, read) for v, read, _ in found["lists"]],
        [("binance-spot", True), ("kraken", False), ("okx", True)],
    )
    c.eq("a list read says so", found["lists"][0][2], "accepted")
    c.eq("a list not read says why", found["lists"][1][2].startswith("CALL_FAILED: "), True)
    whole = [(MINUTES, MINUTES, 0)] * 3
    c.eq(
        "each venue probed once, on the instruments the oracle rule chose",
        found["probed"],
        [
            ("binance-spot", True, [(name, *counts) for name, counts in zip(("BTCUSDT", "ETHUSDT", "SOLUSDT"), whole, strict=True)]),
            ("okx", True, [(name, *counts) for name, counts in zip(("BTC-USDT", "ETH-USDT", "SOL-USDT"), whole, strict=True)]),
        ],
    )
    no_list = "kraken's instrument list was not read"
    c.eq(
        "every ordered pair, in order",
        [(p, s, ok) for p, s, ok, _ in found["pairs"]],
        [
            ("binance-spot", "kraken", False),
            ("binance-spot", "okx", True),
            ("kraken", "binance-spot", False),
            ("kraken", "okx", False),
            ("okx", "binance-spot", True),
            ("okx", "kraken", False),
        ],
    )
    c.eq(
        "a usable pair, token by token, in symbol order",
        found["pairs"][1][3],
        [
            ("BTC", "BTCUSDT", "BTC-USDT", True, None),
            ("ETH", "ETHUSDT", "ETH-USDT", True, None),
            ("SOL", "SOLUSDT", "SOL-USDT", True, None),
        ],
    )
    c.eq("the same pair the other way round", found["pairs"][4][3][0], ("BTC", "BTC-USDT", "BTCUSDT", True, None))
    c.eq("a pair with a venue whose list was not read", found["pairs"][0][3][0], ("BTC", None, None, False, no_list))
    c.eq("and as the primary", found["pairs"][3][3][2], ("SOL", None, None, False, no_list))
    c.eq("what this host can use", found["usable"], ["binance-spot,okx", "okx,binance-spot"])
    return c.bad


def check_what_stops_a_pair(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)
    with world(Gappy) as (clock, exchange):
        urls = {"binance-spot": exchange.url, "okx": exchange.url}
        found = as_rows(survey(mod, clock, urls, ("binance-spot", "okx"), ("BTC", "DOGE", "SOL")))
    c.eq(
        "OKX's SOL bars are missing (DOGE has no instrument, so two are probed)",
        found["probed"][1][2],
        [("BTC-USDT", MINUTES, MINUTES, 0), ("SOL-USDT", MINUTES, 0, 0)],
    )
    c.eq(
        "so no pair settles every token",
        found["pairs"][0][3],
        [
            ("BTC", "BTCUSDT", "BTC-USDT", True, None),
            ("DOGE", None, None, False, "the registry does not resolve DOGE to one token (UNKNOWN)"),
            ("SOL", "SOLUSDT", "SOL-USDT", False, f"okx SOL-USDT: 0 of {MINUTES} closed minutes have a bar"),
        ],
    )
    c.eq("and none is usable", (found["pairs"][0][2], found["pairs"][1][2], found["usable"]), (False, False, []))
    with world() as (clock, exchange):
        urls = {"binance-spot": exchange.url, "okx": "http://127.0.0.1:9"}  # nothing listens on the discard port
        found = as_rows(survey(mod, clock, urls, ("binance-spot", "okx"), ("BTC",), wait_s=2))
    c.eq("a venue that does not answer", [(v, read) for v, read, _ in found["lists"]], [("binance-spot", True), ("okx", False)])
    c.eq(
        "is named as the reason for its pairs",
        found["pairs"][0][3],
        [("BTC", None, None, False, "okx's instrument list was not read")],
    )
    c.eq("and nothing is probed", found["probed"], [])
    with world(Clockless) as (clock, exchange):
        urls = {"binance-spot": exchange.url, "okx": exchange.url}
        got = survey(mod, clock, urls, ("binance-spot", "okx"), ("BTC", "SOL"), wait_s=2)
    rows = as_rows(got)
    okx = next(f for f in got.probes if f.venue == "okx")
    sol = next(f for f in next(f for f in got.probes if f.venue == "binance-spot").instruments if f.instrument == "SOLUSDT")
    c.eq(
        "a venue whose list is read but which does not tell its time",
        (okx.reachable, rows["pairs"][0][3][0][4]),
        (False, f"okx does not answer: {okx.problem}"),
    )
    c.eq("a bar request refused", (sol.coverage, rows["pairs"][1][3][1][4]), (None, f"okx does not answer: {okx.problem}"))
    c.eq("the primary's refusal named first", rows["pairs"][0][3][1][4], f"binance-spot SOLUSDT: {sol.problem}")
    c.eq("and it has a reason", isinstance(sol.problem, str) and bool(sol.problem), True)
    with world(Suspended) as (clock, exchange):
        urls = {"binance-spot": exchange.url, "okx": exchange.url}
        found = as_rows(survey(mod, clock, urls, ("binance-spot", "okx"), ("BTC", "SOL")))
    no_pick = "the oracle rule finds no one trading spot instrument quoted in USDT on each venue"
    c.eq(
        "an instrument that does not trade on one venue: no oracle pair, either way round",
        [(p, s, ok, tokens) for p, s, ok, tokens in found["pairs"]],
        [
            ("binance-spot", "okx", False, [("BTC", "BTCUSDT", "BTC-USDT", True, None), ("SOL", None, None, False, no_pick)]),
            ("okx", "binance-spot", False, [("BTC", "BTC-USDT", "BTCUSDT", True, None), ("SOL", None, None, False, no_pick)]),
        ],
    )
    c.eq(
        "and its instruments are not probed",
        found["probed"],
        [("binance-spot", True, [("BTCUSDT", MINUTES, MINUTES, 0)]), ("okx", True, [("BTC-USDT", MINUTES, MINUTES, 0)])],
    )
    return c.bad


def check_requests(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)

    def ask(**changes: Any) -> Callable[[], object]:
        args: dict[str, Any] = {"minutes": MINUTES, "wait_s": 10, "base_urls": {}, "allow_loopback": True, "clock": OffsetClock()}
        venues, watchlist = changes.pop("venues", ("binance-spot", "okx")), changes.pop("watchlist", ("BTC",))
        args.update(changes)
        return lambda: mod.run(venues, watchlist, **args)

    c.refuses("no symbol", ask(watchlist=()), "the watchlist names at least one symbol, each once, in capitals")
    c.refuses("a symbol twice", ask(watchlist=("BTC", "BTC")), "the watchlist names at least one symbol, each once, in capitals")
    c.refuses("a symbol in small letters", ask(watchlist=("btc",)), "the watchlist names at least one symbol, each once, in capitals")
    c.refuses(
        "one symbol of several in small letters",
        ask(watchlist=("BTC", "eth")),
        "the watchlist names at least one symbol, each once, in capitals",
    )
    c.refuses("one venue", ask(venues=("okx",)), "a pair needs two venues, each named once")
    c.refuses("a venue twice", ask(venues=("okx", "okx")), "a pair needs two venues, each named once")
    c.refuses(
        "not a spot venue",
        ask(venues=("okx", "binance-usdm")),
        f"binance-usdm is not a spot venue the survey reads (they are {list(SPOT)})",
    )
    c.refuses("too few minutes", ask(minutes=0), "a probe looks at from 1 to 300 minutes")
    c.refuses("too many minutes", ask(minutes=301), "a probe looks at from 1 to 300 minutes")
    c.refuses("a wait of nothing", ask(wait_s=0), "the wait is a positive number of seconds")
    c.refuses(
        "a base URL for a venue not surveyed",
        ask(base_urls={"kraken": "http://127.0.0.1:1"}),
        "a base URL is given for kraken, which is not surveyed",
    )
    before = open_descriptors()
    c.refuses(
        "a base URL the venue's transport refuses",
        ask(base_urls={"okx": "http://127.0.0.1:1"}, allow_loopback=False),
        "plain http is allowed only for a loopback host, and only when asked for",
    )
    c.eq("and no file is left open by the refusal", sorted(open_descriptors() - before), [])
    with world() as (clock, exchange):
        urls = {"binance-spot": exchange.url, "okx": exchange.url}
        try:
            got = mod.run(("binance-spot", "okx"), ("BTC",), minutes=1, wait_s=10, base_urls=urls, allow_loopback=True, clock=clock)
            c.eq(
                "one minute is a probe the survey makes",
                [_counts(f.instruments[0]) for f in got.probes],
                [("BTCUSDT", 1, 1, 0), ("BTC-USDT", 1, 1, 0)],
            )
        except mod.SurveyError as exc:
            c.bad.append(f"one minute: refused with {exc}")
        try:
            mod.run(("binance-spot", "okx"), ("BTC",), minutes=MINUTES, wait_s=0.5, base_urls=urls, allow_loopback=True, clock=clock)
        except mod.SurveyError as exc:
            c.bad.append(f"a wait of half a second: refused with {exc}")
    return c.bad


def check_what_it_leaves(mod: types.ModuleType, tmp: Path) -> list[str]:
    """While it runs: its loop thread is named and does not keep the process alive; its work folder is named and holds the captures and
    the dispatch journal. When it returns: no thread it started, no file it opened and no folder it made is left. And a loop that does not
    stop when told holds it for its five seconds, and no longer."""
    c = Case(mod)
    temporary = tmp / "temporary"
    temporary.mkdir()
    saved = tempfile.tempdir
    tempfile.tempdir = str(temporary)
    try:
        with world() as (_clock, exchange):
            watching = Watching(temporary)
            before_threads, before_files = set(threading.enumerate()), open_descriptors()
            urls = {"binance-spot": exchange.url, "okx": exchange.url}
            mod.run(("binance-spot", "okx"), ("BTC",), minutes=MINUTES, wait_s=10, base_urls=urls, allow_loopback=True, clock=watching)
            c.eq("no thread is left running", sorted(t.name for t in set(threading.enumerate()) - before_threads if t.is_alive()), [])
            c.eq("no file is left open", sorted(open_descriptors() - before_files), [])
            c.eq("no folder is left in the temporary folder", sorted(p.name for p in temporary.iterdir()), [])
        c.eq("the loop thread, by name, and it does not keep the process alive", ("venue-survey-runner", True) in watching.threads, True)
        c.eq(
            "no other thread of that name",
            sorted(t for t in watching.threads if t[0].startswith("venue-survey")),
            [("venue-survey-runner", True)],
        )
        c.eq(
            "one work folder, named for the survey",
            [re.fullmatch(r"hoshitori-venue-survey-[a-z0-9_]{8}", name) is not None for name in watching.folders],
            [True],
        )
        c.eq("holding the captures and the dispatch journal", sorted(watching.inside), [("captures", True), ("dispatches.jsonl", False)])
    finally:
        tempfile.tempdir = saved

    real = mod.Runner
    released = threading.Event()
    stopped: list[float] = []
    loops: list[threading.Thread] = []

    class Stubborn(Runner):
        """A runner whose loop goes on after it is told to stop, as one held in a call that does not end would."""

        def stop(self) -> None:
            stopped.append(time.monotonic())
            super().stop()

        def run_forever(self) -> None:
            loops.append(threading.current_thread())
            super().run_forever()
            released.wait(30)

    mod.Runner = Stubborn
    try:
        with world() as (clock, exchange):
            urls = {"binance-spot": exchange.url, "okx": exchange.url}
            mod.run(("binance-spot", "okx"), ("BTC",), minutes=MINUTES, wait_s=10, base_urls=urls, allow_loopback=True, clock=clock)
            returned = time.monotonic()
            alive = [loop.is_alive() for loop in loops]
        c.eq("the loop was told to stop", len(stopped), 1)
        held = returned - stopped[0] if stopped else None
        c.eq(
            f"a loop that does not stop holds the survey {STOP_WAIT_S} s and no longer",
            held is not None and STOP_WAIT_S <= held < STOP_WAIT_S + 0.9,
            True,
        )
        c.eq("and is left to end on its own", alive, [True])
    finally:
        mod.Runner = real
        released.set()
    return c.bad


def check_command(mod: types.ModuleType, tmp: Path) -> list[str]:
    c = Case(mod)

    def run(*argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mod.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    with world() as (_clock, exchange):
        common = ("--minutes", str(MINUTES), "--wait", "10", "--allow-loopback", "--base-url", f"binance-spot={exchange.url}")
        code, out, err = run(
            "--watchlist",
            "BTC,ETH",
            "--venues",
            "binance-spot,okx",
            *common,
            "--base-url",
            f"okx={exchange.url}",
            "--label",
            "loopback test",
        )
        shown = json.loads(out) if out else {}
        c.eq("a usable pair: exit 0", (code, err), (0, ""))
        c.eq("one sorted line", out, json.dumps(shown, sort_keys=True) + "\n")
        c.eq("its parts", sorted(shown), ["label", "lists", "pairs", "usable", "venues"])
        c.eq("the label kept", shown.get("label"), "loopback test")
        c.eq("the usable pairs", shown.get("usable"), ["binance-spot,okx", "okx,binance-spot"])
        c.eq(
            "a pair as written",
            shown.get("pairs", [{}])[0],
            {
                "primary": "binance-spot",
                "second": "okx",
                "can_settle": True,
                "tokens": [
                    {"symbol": "BTC", "primary": "BTCUSDT", "second": "BTC-USDT", "can_settle": True, "why": None},
                    {"symbol": "ETH", "primary": "ETHUSDT", "second": "ETH-USDT", "can_settle": True, "why": None},
                ],
            },
        )
        c.eq("a list as written", shown.get("lists", [{}])[0], {"venue": "binance-spot", "read": True, "detail": "accepted"})
        c.eq(
            "a venue as the venue probe writes it",
            (shown.get("venues", [{}])[0].get("venue"), shown["venues"][0].get("can_settle")) if shown.get("venues") else None,
            ("binance-spot", True),
        )
        code, out, _ = run(
            "--watchlist", "BTC", "--venues", "binance-spot,okx", *common, "--wait", "2", "--base-url", "okx=http://127.0.0.1:9"
        )
        c.eq("no usable pair: exit 1", (code, json.loads(out).get("usable") if out else None), (1, []))
        code, _, err = run("--watchlist", "BTC", "--venues", "okx", *common)
        c.eq("a request that cannot be made: exit 2", (code, err), (2, "venue_survey: a pair needs two venues, each named once\n"))
        code, _, err = run("--watchlist", "BTC", "--base-url", "no-equals-sign")
        c.eq("a base URL without its venue: exit 2", (code, err), (2, "venue_survey: a base URL is VENUE=URL\n"))
    code, _, _ = run()
    c.eq("no watchlist: usage", code, 2)
    code, out, _ = run("--help")
    c.eq("help: exit 0", (code, out.startswith("usage: venue_survey")), (0, True))
    c.eq("the usage names the program", out.startswith("usage: venue_survey "), True)
    for phrase in (
        "Which pairs of oracle venues can this host settle against?",
        "comma-separated symbols, as the protocol's watchlist",
        "comma-separated spot venues (default: all of them)",
        "what this host is (provider, region, size), kept in the output",
        "--base-url VENUE=URL",
        "another base URL for a venue (tests)",
        "allow http loopback base URLs (tests only)",
    ):
        c.eq(f"the help says {phrase!r}", _says(out, phrase), True)
    with world(Slow) as (_clock, exchange):
        urls = [arg for venue in SPOT for arg in ("--base-url", f"{venue}={exchange.url}")]
        recorder = Flushes()
        with contextlib.redirect_stdout(recorder), contextlib.redirect_stderr(io.StringIO()):
            code = mod.main(["--watchlist", "BTC", "--minutes", str(MINUTES), "--allow-loopback", *urls])
        most = exchange.most
    shown = json.loads(recorder.getvalue()) if recorder.getvalue() else {}
    c.eq("without --venues, every spot venue, in name order", [item["venue"] for item in shown.get("lists", [])], list(SPOT))
    c.eq("and a usable pair among them: exit 0", (code, shown.get("usable")), (0, ["binance-spot,okx", "okx,binance-spot"]))
    c.eq(f"at most {WORKERS} calls to the venues at once, though five lists are read", most, WORKERS)
    c.eq(
        "the line is flushed once it is whole, so a pipe sees it at once",
        (recorder.seen[-1:], recorder.getvalue().endswith("}\n")),
        ([recorder.getvalue()], True),
    )
    return c.bad


def _says(text: str, phrase: str) -> bool:
    """Whether the phrase stands in the text as written, with nothing joined to its end (argparse wraps lines, so spaces are folded)."""
    return re.search(re.escape(phrase) + r"(\s|$)", " ".join(text.split())) is not None


class Flushes(io.StringIO):
    """Standard output that keeps what had been written each time it was flushed."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[str] = []

    def flush(self) -> None:
        self.seen.append(self.getvalue())
        super().flush()


SCENARIOS: dict[str, Callable[[types.ModuleType, Path], list[str]]] = {
    "constants": check_constants,
    "two_venues_that_answer": check_two_venues_that_answer,
    "what_stops_a_pair": check_what_stops_a_pair,
    "requests": check_requests,
    "what_it_leaves": check_what_it_leaves,
    "command": check_command,
}


def failures(mod: types.ModuleType, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        tmp = Path(tempfile.mkdtemp(prefix=f"hoshitori-survey-{name}-"))
        try:
            bad += [f"{name}: {message}" for message in scenario(mod, tmp)]
        except Exception as exc:  # a crash in the scenario is a failure of the module under test
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return bad
