"""Checks of the time-stamp authority adapter and transport (``hoshitori.tsa_gateway``), runnable against a mutant. They run without a
network: the transport's single exchange is replaced by a recorder."""

from __future__ import annotations

import types
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from hoshitori.capture import RawCapture
from hoshitori.connector import ConnectorError, ErrorClass
from hoshitori.gateway import TransportFailure
from tests import fake_tsa, tsa_pki

NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
DIGEST = "ab" * 32


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str, error: type[Exception] | None = None) -> None:
        want = error or ConnectorError
        try:
            call()
        except want as exc:
            text = exc.message if isinstance(exc, ConnectorError) else str(exc)
            if text != says:
                self.bad.append(f"{label}: refused with {text!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def capture(status: int | None, body: bytes = b"", headers: dict[str, str] | None = None) -> RawCapture:
    return RawCapture("cap-" + "0" * 32, "tsa-test", {"operation": "stamp", "arguments": {}}, status, headers or {}, body, NOW, NOW)


def check_authority(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("constants", (mod.OPERATION, mod.RATE_LIMIT, mod.RATE_WINDOW_S), ("stamp", 6.0, 60.0))
    a = mod.Authority("freetsa", "https://freetsa.org/tsr")
    c.eq("the provider is named after the authority", a.provider, "tsa-freetsa")
    name = "an authority's name is 1 to 32 characters of a-z, 0-9, - and _"
    url = "an authority's URL is an http(s) URL with a host and no credentials, query or fragment"
    for label, args, says in (
        ("an empty name", ("", "https://x.org/tsr"), name),
        ("a capital in the name", ("FreeTSA", "https://x.org/tsr"), name),
        ("a name of 33 characters", ("a" * 33, "https://x.org/tsr"), name),
        ("a name starting with a dash", ("-a", "https://x.org/tsr"), name),
        ("a name with a space", ("a b", "https://x.org/tsr"), name),
        ("a url with another scheme", ("a", "ftp://x.org/tsr"), url),
        ("a url with no host", ("a", "https:///tsr"), url),
        ("a url with a password", ("a", "https://u:p@x.org/tsr"), url),
        ("a url with a user", ("a", "https://u@x.org/tsr"), url),
        ("a url with a query", ("a", "https://x.org/tsr?k=v"), url),
        ("a url with a fragment", ("a", "https://x.org/tsr#f"), url),
    ):
        c.refuses(label, lambda args=args: mod.Authority(*args), says, mod.TsaConfigError)
    c.eq(
        "a name of 32 characters and a plain http url are fine",
        (mod.Authority("a" * 32, "http://127.0.0.1:1/x").name, mod.Authority("a", "http://h.org").url),
        ("a" * 32, "http://h.org"),
    )
    c.eq("an authority is frozen", mod.Authority.__dataclass_params__.frozen, True)
    return c.bad


def check_adapter(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    adapter = mod.TsaAdapter(mod.Authority("t", "https://t.org/tsr"))
    c.eq("one operation", (adapter.provider, adapter.operations()), ("tsa-t", frozenset({"stamp"})))
    c.eq("cost and timing", (adapter.plan("stamp", {}), adapter.attempt_cap_s("stamp"), adapter.min_attempt_s("stamp")), (1.0, 10.0, 1.0))
    c.eq(
        "a longer attempt cap on request",
        mod.TsaAdapter(mod.Authority("t", "https://t.org/tsr"), attempt_cap_s=3.0).attempt_cap_s("stamp"),
        3.0,
    )
    c.eq("it reads no rate headers", adapter.rate_observation(capture(200)), None)
    good = {"digest": DIGEST, "nonce": 5}
    c.eq("good arguments are returned as they are", adapter.normalise("stamp", good), good)
    c.eq(
        "the extremes of the nonce",
        (adapter.normalise("stamp", {**good, "nonce": 1})["nonce"], adapter.normalise("stamp", {**good, "nonce": 2**53 - 1})["nonce"]),
        (1, 2**53 - 1),
    )
    inv = ErrorClass.INVALID_ARGS
    for label, call, says in (
        ("another operation", lambda: adapter.normalise("time", good), "tsa-t has no operation 'time'"),
        ("a missing nonce", lambda: adapter.normalise("stamp", {"digest": DIGEST}), "stamp takes a digest and a nonce"),
        ("an extra argument", lambda: adapter.normalise("stamp", {**good, "x": 1}), "stamp takes a digest and a nonce"),
        (
            "a digest in capitals",
            lambda: adapter.normalise("stamp", {**good, "digest": "AB" * 32}),
            "the digest is 64 lower-case hexadecimal characters",
        ),
        (
            "a short digest",
            lambda: adapter.normalise("stamp", {**good, "digest": "ab" * 31}),
            "the digest is 64 lower-case hexadecimal characters",
        ),
        (
            "a digest of 65 characters",
            lambda: adapter.normalise("stamp", {**good, "digest": "ab" * 32 + "a"}),
            "the digest is 64 lower-case hexadecimal characters",
        ),
        (
            "a digest that is bytes",
            lambda: adapter.normalise("stamp", {**good, "digest": bytes(32)}),
            "the digest is 64 lower-case hexadecimal characters",
        ),
        ("a nonce of zero", lambda: adapter.normalise("stamp", {**good, "nonce": 0}), "the nonce is a whole number from 1 to 2^53 - 1"),
        ("a nonce of 2^53", lambda: adapter.normalise("stamp", {**good, "nonce": 2**53}), "the nonce is a whole number from 1 to 2^53 - 1"),
        ("a boolean nonce", lambda: adapter.normalise("stamp", {**good, "nonce": True}), "the nonce is a whole number from 1 to 2^53 - 1"),
        ("a text nonce", lambda: adapter.normalise("stamp", {**good, "nonce": "5"}), "the nonce is a whole number from 1 to 2^53 - 1"),
    ):
        try:
            call()
        except ConnectorError as exc:
            c.eq(f"{label}: the class and the words", (exc.error_class, exc.message), (inv, says))
        else:
            c.bad.append(f"{label}: was accepted")
    return c.bad


def check_classify(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    adapter = mod.TsaAdapter(mod.Authority("t", "https://t.org/tsr"))
    chain = tsa_pki.Chain.build("T")
    granted = tsa_pki.build_token(bytes(32), chain, tsa_pki.TokenOptions(gen_time=NOW, nonce=1))
    with_mods = tsa_pki.build_token(bytes(32), chain, tsa_pki.TokenOptions(gen_time=NOW, nonce=1, status="granted_with_mods"))
    c.eq("a granted token is good", adapter.classify(capture(200, granted), NOW), None)
    c.eq("a token granted with modifications is good", adapter.classify(capture(200, with_mods), NOW), None)

    def says(cap: RawCapture) -> tuple[Any, Any, Any]:
        error = adapter.classify(cap, NOW)
        return (None, None, None) if error is None else (error.error_class, error.message, error.retry_after_s)

    c.eq("no response at all", says(capture(None)), (ErrorClass.UPSTREAM, "no response", None))
    c.eq(
        "too many requests, with the wait it names", says(capture(429, b"", {"retry-after": "7"})), (ErrorClass.RATE_LIMIT, "HTTP 429", 7.0)
    )
    c.eq("a server error", says(capture(503)), (ErrorClass.UPSTREAM, "HTTP 503", None))
    c.eq("not found", says(capture(404))[:2], (ErrorClass.NOT_FOUND, "HTTP 404"))
    c.eq(
        "a good status with a body that is not a response",
        says(capture(200, b"<html>")),
        (ErrorClass.MALFORMED, "tsa-t: the answer is not a time-stamp response", None),
    )
    c.eq("an empty body", says(capture(200, b""))[:2], (ErrorClass.MALFORMED, "tsa-t: the answer is not a time-stamp response"))
    c.eq(
        "a refusal with no reason is the authority's trouble",
        says(capture(200, fake_tsa.rejection())),
        (ErrorClass.UPSTREAM, "tsa-t: the authority answered rejection", None),
    )
    c.eq(
        "a refusal for a bad algorithm is our mistake",
        says(capture(200, fake_tsa.rejection(["bad_alg"]))),
        (ErrorClass.INVALID_ARGS, "tsa-t: the authority answered rejection", None),
    )
    c.eq("and so is a bad request", says(capture(200, fake_tsa.rejection(["bad_request"])))[0], ErrorClass.INVALID_ARGS)
    c.eq("and a bad data format", says(capture(200, fake_tsa.rejection(["bad_data_format"])))[0], ErrorClass.INVALID_ARGS)
    c.eq(
        "another failure code is the authority's trouble",
        says(capture(200, fake_tsa.rejection(["system_failure"])))[0],
        ErrorClass.UPSTREAM,
    )
    c.eq(
        "a refusal that names several, one of them ours",
        says(capture(200, fake_tsa.rejection(["time_not_available", "bad_alg"])))[0],
        ErrorClass.INVALID_ARGS,
    )
    return c.bad


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []


def check_transport(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    seen: list[tuple[Any, ...]] = []

    class Probe(mod.TsaTransport):  # type: ignore[name-defined, misc]
        def _exchange(self, *args: Any) -> RawCapture:
            seen.append(args)
            return capture(200)

    probe = Probe(mod.Authority("t", "https://t.example/path/tsr"), lambda: NOW)
    got = probe.fetch("stamp", {"digest": DIGEST, "nonce": 77}, 9.0, "cap-" + "1" * 32, NOW)
    c.eq("a capture is returned", got.status, 200)
    operation, args, method, path, payload, headers, timeout, capture_id, sent_at = seen[0]
    c.eq(
        "a POST to the authority's path, with the query as the body",
        (operation, method, path, args),
        ("stamp", "POST", "/path/tsr", {"digest": DIGEST, "nonce": 77}),
    )
    c.eq("the body is the RFC 3161 query for that digest and that nonce", _read_query(payload), (bytes.fromhex(DIGEST), 77))
    c.eq(
        "it says what it sends and what it takes",
        headers,
        {"Content-Type": "application/timestamp-query", "Accept": "application/timestamp-reply"},
    )
    c.eq("the deadline, the capture id and the time are the caller's", (timeout, capture_id, sent_at), (9.0, "cap-" + "1" * 32, NOW))
    try:
        probe.fetch("stamp", {"digest": "zz", "nonce": 1}, 1.0, "cap-" + "1" * 32, NOW)
    except TransportFailure as exc:
        c.eq("a query that cannot be built is our mistake", (exc.error_class, "ValueError" in str(exc)), (ErrorClass.INVALID_ARGS, True))
    else:
        c.bad.append("a bad digest: was accepted")
    try:
        probe.fetch("stamp", {"digest": DIGEST}, 1.0, "cap-" + "1" * 32, NOW)
    except TransportFailure as exc:
        c.eq("a missing nonce is also our mistake", exc.error_class, ErrorClass.INVALID_ARGS)
    else:
        c.bad.append("a missing nonce: was accepted")
    return c.bad


def _read_query(payload: bytes) -> tuple[bytes, int]:
    from asn1crypto import tsp

    query = tsp.TimeStampReq.load(payload, strict=True)
    return query["message_imprint"]["hashed_message"].native, query["nonce"].native


def check_attach(mod: types.ModuleType) -> list[str]:
    import tempfile
    from pathlib import Path

    from hoshitori.breaker import BreakerConfig
    from hoshitori.capture import CaptureStore
    from hoshitori.clock import ManualClock
    from hoshitori.gateway import DispatchJournal, Gateway, GatewayError
    from hoshitori.governor import RateModel
    from hoshitori.http_transport import TransportConfigError

    c = Case(mod)
    with tempfile.TemporaryDirectory() as tmp:
        gateway = Gateway(CaptureStore(Path(tmp) / "c"), DispatchJournal(Path(tmp) / "d.jsonl"))
        clock = ManualClock(NOW)
        a = mod.Authority("t", "http://127.0.0.1:1/tsr")
        got = mod.attach_tsa(gateway, a, clock, allow_insecure_loopback=True, breaker=BreakerConfig(consecutive_failures=2))
        state = gateway.providers["tsa-t"]
        c.eq(
            "registered under its provider name, with one call at a time",
            (state.adapter is got.adapter, state.governor is got.governor, state.breakers is got.breakers, state.concurrency),
            (True, True, True, 1),
        )
        c.eq("the adapter knows its authority", got.adapter.authority, a)
        c.eq(
            "the governor is the prior: 6 requests in a fixed window of 60 s, starting now",
            (got.governor.name, got.governor.model, got.governor.limit, got.governor.window_s, got.governor.now),
            ("tsa-t", RateModel.FIXED_WINDOW, 6.0, 60.0, NOW),
        )
        c.eq(
            "the breaker is the one asked for",
            (
                got.breakers.provider.name,
                got.breakers.provider.config.consecutive_failures,
                got.breakers.tool_config.consecutive_failures,
                got.breakers.provider.now,
            ),
            ("tsa-t", 2, 2, NOW),
        )
        c.eq(
            "the transport is the authority's, with the loopback allowance",
            (type(got.transport).__name__, got.transport._prefix),
            ("TsaTransport", "/tsr"),
        )
        c.eq(
            "a default breaker when none is asked for",
            mod.attach_tsa(
                gateway, mod.Authority("u", "http://127.0.0.1:1/x"), clock, allow_insecure_loopback=True
            ).breakers.provider.config,
            BreakerConfig(),
        )
        c.refuses(
            "the same authority twice",
            lambda: mod.attach_tsa(gateway, a, clock, allow_insecure_loopback=True),
            "tsa-t is registered twice",
            GatewayError,
        )
        c.refuses(
            "plain http without the allowance",
            lambda: mod.attach_tsa(gateway, mod.Authority("v", "http://127.0.0.1:1/x"), clock),
            "plain http is allowed only for a loopback host, and only when asked for",
            TransportConfigError,
        )
        c.eq(
            "plain http to a remote authority, when it is asked for in so many words",
            type(mod.attach_tsa(gateway, mod.Authority("x", "http://x.example/tsr"), clock, allow_plain_http=True).transport).__name__,
            "TsaTransport",
        )
        c.refuses(
            "and not by the loopback allowance",
            lambda: mod.attach_tsa(gateway, mod.Authority("y", "http://y.example/tsr"), clock, allow_insecure_loopback=True),
            "plain http is allowed only for a loopback host, and only when asked for",
            TransportConfigError,
        )
        c.eq(
            "https needs no allowance",
            type(mod.attach_tsa(gateway, mod.Authority("w", "https://w.example/tsr"), clock).transport).__name__,
            "TsaTransport",
        )
    c.eq("what attach returns is frozen", mod.TsaAttached.__dataclass_params__.frozen, True)
    adapter = mod.TsaAdapter(mod.Authority("t", "https://t.org/tsr"))
    for label, body in (("a bare integer", b"\x02\x01\x01"), ("an empty sequence", b"\x30\x00")):
        error = adapter.classify(capture(200, body), NOW)
        c.eq(f"{label} is not a response", None if error is None else error.error_class, ErrorClass.MALFORMED)
    granted = tsa_pki.build_token(bytes(32), tsa_pki.Chain.build("T"), tsa_pki.TokenOptions(gen_time=NOW, nonce=1))
    status = fake_tsa.rejection()[2:]  # the status info of a refusal, without its wrapper
    as_set = b"\x31" + bytes([len(status)]) + status
    for label, body in (("a set that holds a status", as_set), ("a response with a byte after it", granted + b"\x00")):
        error = adapter.classify(capture(200, body), NOW)
        c.eq(f"{label} is not a response", None if error is None else error.error_class, ErrorClass.MALFORMED)
    c.eq(
        "the transport takes the plain-http option too",
        _config_error(mod, lambda: mod.TsaTransport(mod.Authority("t", "http://t.example/x"), lambda: NOW, allow_plain_http=True)),
        False,
    )
    c.eq(
        "a plain http authority is refused by default",
        _config_error(mod, lambda: mod.TsaTransport(mod.Authority("t", "http://127.0.0.1:1/x"), lambda: NOW)),
        True,
    )
    return c.bad


def _config_error(mod: types.ModuleType, call: Callable[[], object]) -> bool:
    from hoshitori.http_transport import TransportConfigError

    try:
        call()
    except TransportConfigError:
        return True
    return False


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_authority, check_adapter, check_classify, check_transport, check_attach):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
