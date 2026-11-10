"""Checks of ``address_gateway``: the address-flag skill as a gateway provider, runnable against a mutant.

The skill itself is replaced by a scripted runner that answers with the recorded envelopes of ``tests/fixtures/address_flag``, so nothing
here needs the skill to be installed or the network. What is checked is what the gateway needs of a provider: arguments refused before
anything is paid for, an answer that is classified (a screen with no source answering is a failure, an answer about another address is
malformed), a transport that never lets text of the skill's failures into a capture, and the whole door: a call through a real gateway
and runner ends in a capture the reader of ``address_screen`` accepts.
"""

from __future__ import annotations

import json
import tempfile
import threading
import types
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hoshitori.capture import RawCapture
from hoshitori.connector import ConnectorError, ErrorClass
from hoshitori.gateway import TransportFailure

NOW = datetime(2026, 11, 1, 8, 25, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures" / "address_flag"
DEAD = "0x000000000000000000000000000000000000dEaD"
ENTITY = "0xE950DC316b836e4EeFb8308bf32Bf7C72a1358FF"
CAP = "cap-" + "b" * 32


def envelope(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return loaded


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str, error: type[Exception] = ConnectorError) -> None:
        try:
            call()
        except error as exc:
            text = exc.message if isinstance(exc, ConnectorError) else str(exc)
            if says not in text:
                self.bad.append(f"{label}: refused with {text!r}, expected it to contain {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


class Runner:
    """What the skill is: a function of the arguments and the time that answers with an envelope, and records what it was asked."""

    def __init__(self, answers: Mapping[str, dict[str, Any]] | None = None, fail: Exception | None = None) -> None:
        self.answers = dict(answers or {})
        self.fail = fail
        self.asked: list[tuple[dict[str, Any], datetime]] = []

    def __call__(self, args: Mapping[str, Any], now: datetime) -> dict[str, Any]:
        self.asked.append((dict(args), now))
        if self.fail is not None:
            raise self.fail
        return self.answers[str(args["address"]).lower()]


def default_runner() -> Runner:
    return Runner({DEAD.lower(): envelope("no_flag_found"), ENTITY.lower(): envelope("flagged")})


def capture(status: int | None, body: bytes, address: str = DEAD, chain: str = "ethereum") -> RawCapture:
    request = {"operation": "check", "args": {"address": address, "chain": chain}}
    return RawCapture(CAP, "address-flag", request, status, {}, body, NOW, NOW)


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("the provider and its one operation", (mod.PROVIDER, mod.OPERATION), ("address-flag", "check"))
    c.eq(
        "the chains the skill covers",
        mod.CHAINS,
        ("ethereum", "arbitrum", "base", "bsc", "optimism", "polygon", "tron", "bitcoin"),
    )
    return c.bad


def check_adapter(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    a = mod.AddressFlagAdapter()
    c.eq("it names its provider and its one operation", (a.provider, a.operations()), ("address-flag", frozenset({"check"})))
    c.eq(
        "a good call is kept as it was written",
        a.normalise("check", {"chain": "ethereum", "address": DEAD}),
        {"chain": "ethereum", "address": DEAD},
    )
    c.eq("one call costs one", (a.plan("check", {}), a.attempt_cap_s("check"), a.min_attempt_s("check")), (1.0, 30.0, 5.0))
    c.eq("it reads no rate limit from an answer", a.rate_observation(capture(200, b"{}")), None)
    c.refuses("another operation", lambda: a.normalise("lookup", {"chain": "ethereum", "address": DEAD}), "no operation")
    c.refuses(
        "an argument too many",
        lambda: a.normalise("check", {"chain": "ethereum", "address": DEAD, "checks": ["ofac_sdn"]}),
        "takes an address",
    )
    c.refuses("no address", lambda: a.normalise("check", {"chain": "ethereum"}), "takes an address")
    c.refuses("no chain", lambda: a.normalise("check", {"address": DEAD}), "takes an address")
    c.refuses("a chain the skill does not cover", lambda: a.normalise("check", {"chain": "solana", "address": DEAD}), "chain is one of")
    c.refuses("a chain that is not text", lambda: a.normalise("check", {"chain": 1, "address": DEAD}), "chain is one of")
    c.refuses("an address that is not text", lambda: a.normalise("check", {"chain": "ethereum", "address": 5}), "address is plain text")
    c.refuses("an empty address", lambda: a.normalise("check", {"chain": "ethereum", "address": ""}), "address is plain text")
    c.refuses(
        "an address with a space", lambda: a.normalise("check", {"chain": "ethereum", "address": DEAD + " "}), "address is plain text"
    )
    c.refuses(
        "an address that is far too long",
        lambda: a.normalise("check", {"chain": "ethereum", "address": "a" * 129}),
        "address is plain text",
    )
    return c.bad


def check_classify(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    a = mod.AddressFlagAdapter()

    def error_class(status: int | None, body: bytes, **where: str) -> ErrorClass | None:
        found = a.classify(capture(status, body, **where), NOW)
        return None if found is None else found.error_class

    good = json.dumps(envelope("no_flag_found")).encode()
    c.eq("a screen that answered is accepted", error_class(200, good), None)
    c.eq("a flag is a successful answer, not a failure", error_class(200, json.dumps(envelope("flagged")).encode(), address=ENTITY), None)
    partial = envelope("no_flag_found")
    partial["status"] = "partial"
    partial["data"]["verdict"] = "incomplete"
    partial["data"]["checks"][1].update({"state": "unavailable", "reason": "timeout", "raw_value": None, "block": None})
    partial["data"]["counts"] = {"in_scope": 3, "listed": 0, "not_listed": 2, "stale": 0, "unavailable": 1}
    c.eq("one source down is still an answer", error_class(200, json.dumps(partial).encode()), None)
    c.eq(
        "no source answering is the provider's failure", error_class(200, json.dumps(envelope("unavailable")).encode()), ErrorClass.UPSTREAM
    )
    c.eq("nothing came back", error_class(None, b""), ErrorClass.UPSTREAM)
    none_back = a.classify(capture(None, b""), NOW)
    c.eq("and it says so", None if none_back is None else none_back.message, "no answer")
    slow = RawCapture(
        CAP,
        "address-flag",
        {"operation": "check", "args": {"address": DEAD, "chain": "ethereum"}},
        429,
        {"retry-after": "7"},
        b"",
        NOW,
        NOW,
    )
    waited = a.classify(slow, NOW)
    c.eq(
        "a rate limit says how long to wait",
        None if waited is None else (waited.error_class, waited.retry_after_s),
        (ErrorClass.RATE_LIMIT, 7.0),
    )
    c.eq("a status that is not 200", error_class(500, good), ErrorClass.UPSTREAM)
    c.eq("not JSON", error_class(200, b"{nope"), ErrorClass.MALFORMED)
    c.eq("an answer about another address than the call", error_class(200, good, address=ENTITY), ErrorClass.MALFORMED)
    c.eq("an answer about another chain than the call", error_class(200, good, chain="base"), ErrorClass.MALFORMED)
    liar = envelope("no_flag_found")
    liar["data"]["verdict"] = "flagged"
    c.eq("an answer that contradicts itself", error_class(200, json.dumps(liar).encode()), ErrorClass.MALFORMED)
    bare = a.classify(RawCapture(CAP, "address-flag", {"operation": "check"}, 200, {}, good, NOW, NOW), NOW)
    c.eq("a capture that does not say what was asked is malformed", None if bare is None else bare.error_class, ErrorClass.MALFORMED)
    return c.bad


def check_transport(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    runner = default_runner()
    transport = mod.SkillTransport(runner, lambda: NOW + timedelta(seconds=2))
    args = {"chain": "ethereum", "address": DEAD}
    got = transport.fetch("check", args, 30.0, CAP, NOW)
    c.eq(
        "the capture says who answered and what was asked",
        (got.source, got.status, got.capture_id, got.request),
        ("address-flag", 200, CAP, {"operation": "check", "args": args, "surface": "skill"}),
    )
    c.eq("it holds the skill's answer", json.loads(got.body), envelope("no_flag_found"))
    c.eq(
        "as compact JSON with its keys in order and its text as it is, not escaped",
        got.body,
        json.dumps(envelope("no_flag_found"), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )
    c.eq("and the text is really there", "0x0000…dEaD".encode() in got.body, True)
    unordered = mod.SkillTransport(lambda args, now: {"b": 1, "a": {"d": 2, "c": 3}}, lambda: NOW).fetch("check", args, 30.0, CAP, NOW)
    c.eq("whatever order the skill writes its keys in", unordered.body, b'{"a":{"c":3,"d":2},"b":1}')
    c.eq("sent when asked, received when the clock says", (got.t_send, got.received_at), (NOW, NOW + timedelta(seconds=2)))
    c.eq("the skill was asked once, with the arguments and the moment of the call", runner.asked, [(args, NOW)])
    c.eq(
        "a clock that runs backwards does not make a capture received before it was sent",
        mod.SkillTransport(runner, lambda: NOW - timedelta(seconds=9)).fetch("check", args, 30.0, CAP, NOW).received_at,
        NOW,
    )
    c.refuses("another operation", lambda: transport.fetch("lookup", args, 30.0, CAP, NOW), "no operation", TransportFailure)
    for label, error, kind in (
        ("an input the skill refuses", ValueError("the skill says: the address is not valid, secret text"), ErrorClass.INVALID_ARGS),
        ("a bug in the skill", RuntimeError("secret text of a bug"), ErrorClass.UPSTREAM),
        ("a time out", TimeoutError("secret text"), ErrorClass.TIMEOUT),
    ):
        broken = mod.SkillTransport(Runner(fail=error), lambda: NOW)
        try:
            broken.fetch("check", args, 30.0, CAP, NOW)
        except TransportFailure as exc:
            c.eq(f"{label}: its class", exc.error_class, kind)
            c.eq(f"{label}: no text of the skill's is passed on", "secret" in str(exc), False)
        except Exception as exc:
            c.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
        else:
            c.bad.append(f"{label}: was accepted")
    return c.bad


def check_the_whole_door(mod: types.ModuleType) -> list[str]:
    """A call through a real gateway and runner: admitted, paid for, run on a worker, stored, classified, and read back as a screen."""
    from hoshitori import address_screen
    from hoshitori.breaker import BreakerConfig
    from hoshitori.capture import CaptureStore
    from hoshitori.clock import SystemClock
    from hoshitori.gateway import ON_DEMAND, Call, DispatchJournal, Gateway, Outcome
    from hoshitori.governor import Priority
    from hoshitori.runner import Runner as GatewayRunner
    from hoshitori.runner import ThreadExecutor

    c = Case(mod)
    clock = SystemClock()
    with tempfile.TemporaryDirectory() as tmp:
        store = CaptureStore(Path(tmp) / "captures")
        gateway = Gateway(store, DispatchJournal(Path(tmp) / "dispatch.jsonl"))
        runner = default_runner()
        attached = mod.attach_address(gateway, runner, clock, breaker=BreakerConfig(consecutive_failures=5))
        c.eq("what attach returns is frozen", mod.AddressAttached.__dataclass_params__.frozen, True)
        c.eq("a breaker that was asked for is the one used", attached.breakers.provider.config, BreakerConfig(consecutive_failures=5))
        c.eq(
            "a default breaker when none is asked for",
            mod.attach_address(Gateway(store, DispatchJournal(Path(tmp) / "other.jsonl")), runner, clock).breakers.provider.config,
            BreakerConfig(),
        )
        state = gateway.providers["address-flag"]
        c.eq(
            "registered with its own governor and breakers, one screen at a time",
            (state.adapter is attached.adapter, state.governor is attached.governor, state.concurrency, attached.governor.limit),
            (True, True, 1, 20.0),
        )
        pool = ThreadExecutor(2)
        door = GatewayRunner(gateway, {"address-flag": attached.transport}, clock, pool)
        loop = threading.Thread(target=door.run_forever, daemon=True)
        loop.start()
        try:

            def screen(address: str) -> Any:
                call = Call(
                    "address-flag",
                    "check",
                    {"chain": "ethereum", "address": address},
                    Priority.P2,
                    clock.now() + timedelta(seconds=60),
                    "check",
                    ON_DEMAND,
                )
                return door.submit(call).result(timeout=30)

            ok = screen(DEAD)
            c.eq("a screen is paid for and done", (ok.outcome, ok.attempts, ok.error), (Outcome.OK, 1, None))
            capture_ = store.get(ok.capture_id or "")
            c.eq("its capture is the skill's answer", json.loads(capture_.body)["data"]["verdict"], "no_flag_found")
            read = address_screen.read_screen(capture_.body, chain="ethereum", address=DEAD)
            c.eq("and reads back as a screen", (read.verdict, read.hazard), ("no_flag_found", False))
            flagged = screen(ENTITY)
            c.eq("a flagged address is an answer too", (flagged.outcome, flagged.error), (Outcome.OK, None))
            bad = door.submit(
                Call(
                    "address-flag",
                    "check",
                    {"chain": "solana", "address": DEAD},
                    Priority.P2,
                    clock.now() + timedelta(seconds=60),
                    "check",
                    ON_DEMAND,
                )
            ).result(timeout=30)
            c.eq("a chain the skill does not cover is refused before it is paid for", bad.outcome, Outcome.REFUSED)
        finally:
            door.stop()
            loop.join(timeout=3)
            pool.shutdown()
            store.close()
    return c.bad


def check_the_binding(mod: types.ModuleType) -> list[str]:
    """The binding to the skill: absent when the skill is not installed, a runner when it is. Nothing is fetched: a stand-in package takes the
    skill's place, so what the binding passes to it (the arguments, the moment and the folder of the list) is seen."""
    import importlib
    import importlib.util
    import sys

    c = Case(mod)
    with tempfile.TemporaryDirectory() as tmp:
        c.eq(
            "there is a runner exactly when the skill is installed",
            mod.skill_runner(Path(tmp)) is not None,
            importlib.util.find_spec("address_flag_check") is not None,
        )
        package = Path(tmp) / "standin" / "address_flag_check"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "skill.py").write_text(
            "def run(args, *, now=None, list_dir=None):\n    return {'args': args, 'now': now.isoformat(), 'list_dir': str(list_dir)}\n"
        )
        kept = {
            name: sys.modules.pop(name)
            for name in list(sys.modules)
            if name == "address_flag_check" or name.startswith("address_flag_check.")
        }
        sys.path.insert(0, str(package.parent))
        importlib.invalidate_caches()
        try:
            runner = mod.skill_runner(Path(tmp) / "lists")
            c.eq("a runner when the package is there", runner is not None, True)
            if runner is not None:
                c.eq(
                    "it hands the skill the arguments, the moment and the folder of the list",
                    runner({"chain": "ethereum", "address": DEAD}, NOW),
                    {"args": {"chain": "ethereum", "address": DEAD}, "now": NOW.isoformat(), "list_dir": str(Path(tmp) / "lists")},
                )
        finally:
            sys.path.remove(str(package.parent))
            for name in [n for n in sys.modules if n == "address_flag_check" or n.startswith("address_flag_check.")]:
                del sys.modules[name]
            sys.modules.update(kept)
            importlib.invalidate_caches()
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_adapter, check_classify, check_transport, check_the_whole_door, check_the_binding):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
