"""Checks of the model endpoint adapter and transport (``hoshitori.model_gateway``), runnable against a mutant. They run without a network:
the transport's single exchange is replaced by a recorder."""

from __future__ import annotations

import json
import types
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from hoshitori.capture import RawCapture
from hoshitori.connector import ConnectorError, ErrorClass
from hoshitori.gateway import TransportFailure

NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
GOOD: dict[str, Any] = {"model": "nvidia/nemotron-3-ultra-550b-a55b", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 600}
ANSWER = json.dumps({"choices": [{"message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}]}).encode()


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
    return RawCapture("cap-" + "0" * 32, "model-t", {"operation": "chat", "arguments": {}}, status, headers or {}, body, NOW, NOW)


def adapter(mod: types.ModuleType, **kw: Any) -> Any:
    return mod.ModelAdapter(mod.Endpoint("t", "https://api.example/v1"), **kw)


def check_endpoint(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "constants",
        (mod.OPERATION, mod.RATE_LIMIT, mod.RATE_WINDOW_S, mod.ROLES, mod.MAX_MESSAGES, mod.MAX_MESSAGE_CHARS, mod.MAX_TOTAL_CHARS),
        ("chat", 30.0, 60.0, ("system", "user", "assistant"), 16, 200_000, 400_000),
    )
    c.eq("more constants", (mod.MAX_TOKENS, mod.MAX_SEED), (65_536, 2**31 - 1))
    e = mod.Endpoint("nvidia", "https://integrate.api.nvidia.com/v1")
    c.eq("the provider is named after the endpoint", e.provider, "model-nvidia")
    name = "an endpoint's name is 1 to 32 characters of a-z, 0-9, - and _"
    url = "an endpoint's URL is an http(s) URL with a host and no credentials, query or fragment"
    for label, args, says in (
        ("an empty name", ("", "https://x.org/v1"), name),
        ("a capital in the name", ("Nv", "https://x.org/v1"), name),
        ("a name of 33 characters", ("a" * 33, "https://x.org/v1"), name),
        ("a name starting with a dash", ("-a", "https://x.org/v1"), name),
        ("a name with a space", ("a b", "https://x.org/v1"), name),
        ("a url with another scheme", ("a", "ftp://x.org/v1"), url),
        ("a url with no host", ("a", "https:///v1"), url),
        ("a url with a password", ("a", "https://u:p@x.org/v1"), url),
        ("a url with a user", ("a", "https://u@x.org/v1"), url),
        ("a url with a query", ("a", "https://x.org/v1?k=v"), url),
        ("a url with a fragment", ("a", "https://x.org/v1#f"), url),
    ):
        c.refuses(label, lambda args=args: mod.Endpoint(*args), says, mod.ModelConfigError)
    c.eq(
        "a name of 32 characters and a plain http url are fine",
        (mod.Endpoint("a" * 32, "http://h.org").name, mod.Endpoint("a", "http://h.org").url),
        ("a" * 32, "http://h.org"),
    )
    c.eq("an endpoint is frozen", mod.Endpoint.__dataclass_params__.frozen, True)
    return c.bad


def check_normalise(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    a = adapter(mod)
    c.eq("one operation", (a.provider, a.operations()), ("model-t", frozenset({"chat"})))
    c.eq("cost and timing", (a.plan("chat", {}), a.attempt_cap_s("chat"), a.min_attempt_s("chat")), (1.0, 180.0, 5.0))
    c.eq("a longer attempt cap on request", adapter(mod, attempt_cap_s=7.0).attempt_cap_s("chat"), 7.0)
    c.eq("and a shorter least attempt", adapter(mod, min_attempt_s=1.5).min_attempt_s("chat"), 1.5)
    c.eq("it reads no rate headers", a.rate_observation(capture(200)), None)
    c.eq("good arguments come back as they are, and nothing is added", a.normalise("chat", GOOD), GOOD)
    full = {
        **GOOD,
        "temperature": "0.7",
        "seed": 7,
        "response_format": {"type": "json_schema", "json_schema": {"name": "forecast", "schema": {"type": "object"}, "strict": False}},
    }
    c.eq("and every optional member is kept", a.normalise("chat", full), full)
    c.eq(
        "a schema is strict unless it says otherwise",
        a.normalise("chat", {**GOOD, "response_format": {"type": "json_schema", "json_schema": {"name": "f", "schema": {}}}})[
            "response_format"
        ],
        {"type": "json_schema", "json_schema": {"name": "f", "schema": {}, "strict": True}},
    )
    c.eq(
        "a json object format",
        a.normalise("chat", {**GOOD, "response_format": {"type": "json_object"}})["response_format"],
        {"type": "json_object"},
    )
    c.eq(
        "the extremes",
        (
            a.normalise("chat", {**GOOD, "max_tokens": 1})["max_tokens"],
            a.normalise("chat", {**GOOD, "max_tokens": 65_536})["max_tokens"],
            a.normalise("chat", {**GOOD, "seed": 0})["seed"],
            a.normalise("chat", {**GOOD, "seed": 2**31 - 1})["seed"],
            [a.normalise("chat", {**GOOD, "temperature": t})["temperature"] for t in ("0", "0.000", "1", "2", "2.0", "1.999", "0.5")],
        ),
        (1, 65_536, 0, 2**31 - 1, ["0", "0.000", "1", "2", "2.0", "1.999", "0.5"]),
    )
    c.eq(
        "a message of 200,000 characters and sixteen messages are fine",
        len(a.normalise("chat", {**GOOD, "messages": [{"role": "user", "content": "x" * 200_000}]})["messages"]),
        1,
    )
    both = [{"role": "user", "content": "x" * 200_000}] * 2
    c.eq("messages of exactly 400,000 characters in all", len(a.normalise("chat", {**GOOD, "messages": both})["messages"]), 2)
    c.eq("sixteen messages", len(a.normalise("chat", {**GOOD, "messages": [{"role": "user", "content": "x"}] * 16})["messages"]), 16)
    c.eq(
        "and all the roles",
        [
            m["role"]
            for m in a.normalise("chat", {**GOOD, "messages": [{"role": r, "content": "x"} for r in ("system", "user", "assistant")]})[
                "messages"
            ]
        ],
        ["system", "user", "assistant"],
    )
    c.eq(
        "thinking, on or off, is kept",
        [a.normalise("chat", {**GOOD, "thinking": t})["thinking"] for t in (True, False)],
        [True, False],
    )
    c.eq("and absent when not given", "thinking" in a.normalise("chat", GOOD), False)
    for bad in (1, 0, "no", None):
        c.refuses("thinking of " + repr(bad), lambda bad=bad: a.normalise("chat", {**GOOD, "thinking": bad}), "thinking is true or false")
    shape = "chat takes a model, messages and max_tokens, and optionally temperature, seed and response_format"
    for label, call, says in (
        ("another operation", lambda: a.normalise("time", GOOD), "model-t has no operation 'time'"),
        ("a missing model", lambda: a.normalise("chat", {k: v for k, v in GOOD.items() if k != "model"}), shape),
        ("a missing message list", lambda: a.normalise("chat", {k: v for k, v in GOOD.items() if k != "messages"}), shape),
        ("a missing token ceiling", lambda: a.normalise("chat", {k: v for k, v in GOOD.items() if k != "max_tokens"}), shape),
        ("an extra argument", lambda: a.normalise("chat", {**GOOD, "top_p": 1}), shape),
        ("a model that is empty", lambda: a.normalise("chat", {**GOOD, "model": ""}), "the model is an id of 1 to 100 characters"),
        ("a model with a space", lambda: a.normalise("chat", {**GOOD, "model": "a b"}), "the model is an id of 1 to 100 characters"),
        (
            "a model of 101 characters",
            lambda: a.normalise("chat", {**GOOD, "model": "a" * 101}),
            "the model is an id of 1 to 100 characters",
        ),
        ("a model that is not text", lambda: a.normalise("chat", {**GOOD, "model": 5}), "the model is an id of 1 to 100 characters"),
        ("no tokens", lambda: a.normalise("chat", {**GOOD, "max_tokens": 0}), "max_tokens is a whole number from 1 to 65536"),
        ("too many tokens", lambda: a.normalise("chat", {**GOOD, "max_tokens": 65_537}), "max_tokens is a whole number from 1 to 65536"),
        (
            "tokens that are a boolean",
            lambda: a.normalise("chat", {**GOOD, "max_tokens": True}),
            "max_tokens is a whole number from 1 to 65536",
        ),
        ("tokens that are text", lambda: a.normalise("chat", {**GOOD, "max_tokens": "5"}), "max_tokens is a whole number from 1 to 65536"),
        ("messages that are not a list", lambda: a.normalise("chat", {**GOOD, "messages": "hi"}), "the messages are a list of 1 to 16"),
        ("no messages", lambda: a.normalise("chat", {**GOOD, "messages": []}), "the messages are a list of 1 to 16"),
        (
            "seventeen messages",
            lambda: a.normalise("chat", {**GOOD, "messages": [{"role": "user", "content": "x"}] * 17}),
            "the messages are a list of 1 to 16",
        ),
        (
            "a message that is not an object",
            lambda: a.normalise("chat", {**GOOD, "messages": ["hi"]}),
            "a message has a role and a content",
        ),
        (
            "a message with an extra member",
            lambda: a.normalise("chat", {**GOOD, "messages": [{"role": "user", "content": "x", "name": "n"}]}),
            "a message has a role and a content",
        ),
        (
            "a message with no content",
            lambda: a.normalise("chat", {**GOOD, "messages": [{"role": "user"}]}),
            "a message has a role and a content",
        ),
        (
            "a role that is not one",
            lambda: a.normalise("chat", {**GOOD, "messages": [{"role": "tool", "content": "x"}]}),
            "a message's role is one of ['system', 'user', 'assistant']",
        ),
        (
            "empty content",
            lambda: a.normalise("chat", {**GOOD, "messages": [{"role": "user", "content": ""}]}),
            "a message's content is text of 1 to 200000 characters",
        ),
        (
            "content that is not text",
            lambda: a.normalise("chat", {**GOOD, "messages": [{"role": "user", "content": 5}]}),
            "a message's content is text of 1 to 200000 characters",
        ),
        (
            "content of 200,001 characters",
            lambda: a.normalise("chat", {**GOOD, "messages": [{"role": "user", "content": "x" * 200_001}]}),
            "a message's content is text of 1 to 200000 characters",
        ),
        (
            "messages of 400,001 characters in all",
            lambda: a.normalise(
                "chat",
                {
                    **GOOD,
                    "messages": [
                        {"role": "user", "content": "x" * 200_000},
                        {"role": "user", "content": "x" * 200_000},
                        {"role": "user", "content": "x"},
                    ],
                },
            ),
            "the messages are at most 400000 characters in all",
        ),
        (
            "a temperature that is a number",
            lambda: a.normalise("chat", {**GOOD, "temperature": 0.5}),
            "the temperature is a decimal string from 0 to 2 with at most three places",
        ),
        (
            "a temperature above two",
            lambda: a.normalise("chat", {**GOOD, "temperature": "2.1"}),
            "the temperature is a decimal string from 0 to 2 with at most three places",
        ),
        (
            "a negative temperature",
            lambda: a.normalise("chat", {**GOOD, "temperature": "-1"}),
            "the temperature is a decimal string from 0 to 2 with at most three places",
        ),
        (
            "four places",
            lambda: a.normalise("chat", {**GOOD, "temperature": "0.0005"}),
            "the temperature is a decimal string from 0 to 2 with at most three places",
        ),
        ("a seed below zero", lambda: a.normalise("chat", {**GOOD, "seed": -1}), "the seed is a whole number from 0 to 2147483647"),
        ("a seed above the limit", lambda: a.normalise("chat", {**GOOD, "seed": 2**31}), "the seed is a whole number from 0 to 2147483647"),
        (
            "a seed that is a boolean",
            lambda: a.normalise("chat", {**GOOD, "seed": True}),
            "the seed is a whole number from 0 to 2147483647",
        ),
    ):
        c.refuses(label, call, says)
    fmt = "a response format is a JSON object or a named JSON schema"
    schema = {"type": "json_schema", "json_schema": {"name": "f", "schema": {}}}
    for label, value, says in (
        ("a text format", {"type": "text"}, fmt),
        ("not an object", "json_object", fmt),
        ("a json object with a member", {"type": "json_object", "x": 1}, fmt),
        ("a schema without its name", {"type": "json_schema", "json_schema": {"schema": {}}}, fmt),
        ("a schema without its body", {"type": "json_schema", "json_schema": {"name": "f"}}, fmt),
        ("a schema with an extra member", {"type": "json_schema", "json_schema": {"name": "f", "schema": {}, "x": 1}}, fmt),
        ("a schema name with a space", {"type": "json_schema", "json_schema": {"name": "f g", "schema": {}}}, fmt),
        ("a schema name that is empty", {"type": "json_schema", "json_schema": {"name": "", "schema": {}}}, fmt),
        ("a schema that is not an object", {"type": "json_schema", "json_schema": {"name": "f", "schema": []}}, fmt),
        ("a strict flag that is not a boolean", {"type": "json_schema", "json_schema": {"name": "f", "schema": {}, "strict": 1}}, fmt),
        ("a format with an extra member", {**schema, "x": 1}, fmt),
        (
            "a schema with a floating-point number",
            {"type": "json_schema", "json_schema": {"name": "f", "schema": {"minimum": 0.5}}},
            "a response schema is plain JSON with no floating-point numbers",
        ),
    ):
        c.refuses(label, lambda value=value: a.normalise("chat", {**GOOD, "response_format": value}), says)
    c.eq(
        "a schema name of 64 characters",
        a.normalise("chat", {**GOOD, "response_format": {"type": "json_schema", "json_schema": {"name": "a" * 64, "schema": {}}}})[
            "response_format"
        ]["json_schema"]["name"],
        "a" * 64,
    )
    c.refuses(
        "and of 65",
        lambda: a.normalise("chat", {**GOOD, "response_format": {"type": "json_schema", "json_schema": {"name": "a" * 65, "schema": {}}}}),
        fmt,
    )
    return c.bad


def check_classify(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    a = adapter(mod)

    def says(cap: RawCapture) -> tuple[ErrorClass | None, float | None, str | None]:
        error = a.classify(cap, NOW)
        return (None, None, None) if error is None else (error.error_class, error.retry_after_s, error.message)

    c.eq("no response at all", says(capture(None)), (ErrorClass.UPSTREAM, None, "no response"))
    c.eq("an answer with a message", says(capture(200, ANSWER)), (None, None, None))
    c.eq(
        "a message whose content is null is still an answer (the client decides what it is worth)",
        says(capture(200, json.dumps({"choices": [{"message": {"content": None}, "finish_reason": "length"}]}).encode())),
        (None, None, None),
    )
    c.eq("text that is not JSON", says(capture(200, b"<html>")), (ErrorClass.MALFORMED, None, "model-t: the answer is not JSON"))
    no_message = "model-t: the answer has no message"
    for label, body in (
        ("a list", b"[]"),
        ("an object with no choices", b"{}"),
        ("choices that are not a list", b'{"choices":{}}'),
        ("an empty list of choices", b'{"choices":[]}'),
        ("a choice that is not an object", b'{"choices":[1]}'),
        ("a choice with no message", b'{"choices":[{}]}'),
        ("a message that is not an object", b'{"choices":[{"message":"x"}]}'),
    ):
        c.eq(label, says(capture(200, body)), (ErrorClass.MALFORMED, None, no_message))
    c.eq("a wrong key", says(capture(401, b"{}"))[0], ErrorClass.AUTH)
    c.eq("a model this account cannot use", says(capture(404, b"{}"))[0], ErrorClass.NOT_FOUND)
    c.eq("a bad request", says(capture(400, b"{}"))[0], ErrorClass.INVALID_ARGS)
    c.eq("the provider's trouble", says(capture(502, b"{}"))[0], ErrorClass.UPSTREAM)
    c.eq(
        "back-pressure with the wait it asks for", says(capture(429, b"{}", {"retry-after": "7"})), (ErrorClass.RATE_LIMIT, 7.0, "HTTP 429")
    )
    c.eq("and with none", says(capture(429, b"{}"))[1], None)
    return c.bad


def check_transport(mod: types.ModuleType) -> list[str]:
    from hoshitori.http_transport import TransportConfigError

    c = Case(mod)
    seen: list[tuple[Any, ...]] = []
    keys = ["test-key-0001"]
    reads: list[int] = []

    def key() -> str | None:
        reads.append(1)
        return keys[0]

    class Probe(mod.ModelTransport):  # type: ignore[name-defined, misc]
        def _exchange(self, *args: Any) -> RawCapture:
            seen.append(args)
            return capture(200, ANSWER)

    probe = Probe(mod.Endpoint("t", "https://api.example/v1"), key, lambda: NOW)
    args = {**GOOD, "temperature": "0.7", "seed": 3}
    got = probe.fetch("chat", args, 9.0, "cap-" + "1" * 32, NOW)
    c.eq("a capture is returned", got.status, 200)
    operation, passed, method, path, payload, headers, timeout, capture_id, sent_at = seen[0]
    c.eq("a POST to the chat route under the endpoint's path", (operation, method, path), ("chat", "POST", "/v1/chat/completions"))
    c.eq("the arguments are passed on as they are, and hold no key", (passed, "nvapi" in json.dumps(passed)), (args, False))
    sent = json.loads(payload)
    c.eq("the body is the request, with the temperature as a number", sent, {**GOOD, "temperature": 0.7, "seed": 3})
    c.eq(
        "and is compact JSON in the order of the arguments",
        payload,
        json.dumps({**GOOD, "temperature": 0.7, "seed": 3}, separators=(",", ":")).encode(),
    )
    c.eq("the key is a Bearer header and nothing else is added", headers, {"Authorization": "Bearer test-key-0001"})
    c.eq("the deadline, the capture id and the time are the caller's", (timeout, capture_id, sent_at), (9.0, "cap-" + "1" * 32, NOW))
    probe.fetch("chat", GOOD, 9.0, "cap-" + "2" * 32, NOW)
    c.eq("without a temperature none is sent", "temperature" in json.loads(seen[1][4]), False)
    c.eq("without thinking none is sent", "chat_template_kwargs" in json.loads(seen[1][4]), False)
    keys[0] = "test-key-0002"
    probe.fetch("chat", GOOD, 9.0, "cap-" + "3" * 32, NOW)
    c.eq("the key is read at every exchange", (len(reads), seen[2][5]), (3, {"Authorization": "Bearer test-key-0002"}))
    for wanted in (True, False):
        probe.fetch("chat", {**GOOD, "thinking": wanted}, 9.0, "cap-" + "9" * 32, NOW)
        body = json.loads(seen[-1][4])
        c.eq(f"thinking {wanted} is sent in the endpoint's own words", body.get("chat_template_kwargs"), {"enable_thinking": wanted})
        c.eq("and not as a member of the request", "thinking" in body, False)
        c.eq("the other members are as they were", {k: v for k, v in body.items() if k != "chat_template_kwargs"}, GOOD)
    for label, value in (
        ("no key", None),
        ("an empty key", ""),
        ("a key with a space", "nvapi secret"),
        ("a key that is too short", "short"),
        ("a key with a newline", "test-key\n"),
    ):
        keys[0] = value  # type: ignore[call-overload]
        try:
            probe.fetch("chat", GOOD, 1.0, "cap-" + "4" * 32, NOW)
        except TransportFailure as exc:
            c.eq(f"{label} is an authentication failure", exc.error_class, ErrorClass.AUTH)
            c.eq(f"{label}: and the message holds no key", "nvapi" in str(exc), False)
        else:
            c.bad.append(f"{label}: was accepted")
    keys[0] = "test-key-0001"
    try:
        probe.fetch("stamp", GOOD, 1.0, "cap-" + "5" * 32, NOW)
    except TransportFailure as exc:
        c.eq(
            "another operation is our mistake",
            (exc.error_class, str(exc)),
            (ErrorClass.INVALID_ARGS, "INVALID_ARGS: there is no operation 'stamp'"),
        )
    else:
        c.bad.append("another operation: was accepted")
    c.refuses(
        "plain http is not allowed by default",
        lambda: mod.ModelTransport(mod.Endpoint("t", "http://127.0.0.1:1/v1"), key, lambda: NOW),
        "plain http is allowed only for a loopback host, and only when asked for",
        TransportConfigError,
    )
    c.eq(
        "a key of eight characters is usable",
        len(
            Probe(mod.Endpoint("t", "https://api.example/v1"), lambda: "12345678", lambda: NOW)
            .fetch("chat", GOOD, 1.0, "cap-" + "6" * 32, NOW)
            .body
        )
        > 0,
        True,
    )
    return c.bad


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
        e = mod.Endpoint("t", "http://127.0.0.1:1/v1")
        got = mod.attach_model(
            gateway, e, lambda: "k" * 8, clock, allow_insecure_loopback=True, breaker=BreakerConfig(consecutive_failures=2)
        )
        state = gateway.providers["model-t"]
        c.eq(
            "registered under its provider name, four calls at a time",
            (state.adapter is got.adapter, state.governor is got.governor, state.breakers is got.breakers, state.concurrency),
            (True, True, True, 4),
        )
        c.eq("the adapter knows its endpoint and its attempt cap", (got.adapter.endpoint, got.adapter.attempt_cap_s("chat")), (e, 180.0))
        c.eq(
            "the governor is the prior: 30 requests in a fixed window of 60 s, starting now",
            (got.governor.name, got.governor.model, got.governor.limit, got.governor.window_s, got.governor.now),
            ("model-t", RateModel.FIXED_WINDOW, 30.0, 60.0, NOW),
        )
        c.eq(
            "the breaker is the one asked for",
            (
                got.breakers.provider.name,
                got.breakers.provider.config.consecutive_failures,
                got.breakers.tool_config.consecutive_failures,
                got.breakers.provider.now,
            ),
            ("model-t", 2, 2, NOW),
        )
        c.eq("the transport is the endpoint's", (type(got.transport).__name__, got.transport._prefix), ("ModelTransport", "/v1"))
        other = mod.attach_model(
            gateway,
            mod.Endpoint("u", "http://127.0.0.1:1/v1"),
            lambda: "k" * 8,
            clock,
            rate_limit=5.0,
            concurrency=2,
            attempt_cap_s=9.0,
            min_attempt_s=2.0,
            allow_insecure_loopback=True,
        )
        c.eq(
            "a limit, a concurrency, a cap and a least attempt can be set",
            (
                other.governor.limit,
                gateway.providers["model-u"].concurrency,
                other.adapter.attempt_cap_s("chat"),
                other.adapter.min_attempt_s("chat"),
            ),
            (5.0, 2, 9.0, 2.0),
        )
        c.eq("a default breaker when none is asked for", other.breakers.provider.config, BreakerConfig())
        c.refuses(
            "the same endpoint twice",
            lambda: mod.attach_model(gateway, e, lambda: "k" * 8, clock, allow_insecure_loopback=True),
            "model-t is registered twice",
            GatewayError,
        )
        c.refuses(
            "plain http without the allowance",
            lambda: mod.attach_model(gateway, mod.Endpoint("v", "http://127.0.0.1:1/v1"), lambda: "k" * 8, clock),
            "plain http is allowed only for a loopback host, and only when asked for",
            TransportConfigError,
        )
        c.refuses(
            "and never for a remote host",
            lambda: mod.attach_model(
                gateway, mod.Endpoint("w", "http://w.example/v1"), lambda: "k" * 8, clock, allow_insecure_loopback=True
            ),
            "plain http is allowed only for a loopback host, and only when asked for",
            TransportConfigError,
        )
        c.eq(
            "https needs no allowance",
            type(mod.attach_model(gateway, mod.Endpoint("x", "https://x.example/v1"), lambda: "k" * 8, clock).transport).__name__,
            "ModelTransport",
        )
    c.eq("what attach returns is frozen", mod.ModelAttached.__dataclass_params__.frozen, True)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_endpoint, check_normalise, check_classify, check_transport, check_attach):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-800:]}")
    return bad
