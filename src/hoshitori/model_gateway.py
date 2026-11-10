"""A model endpoint as a gateway provider: a model outage is the same kind of failure as an exchange outage, and goes through the same door.

``AGENT_PROTOCOL`` section 3 makes ``ModelClient`` the only way any code reaches a model, and ``GATEWAY_DESIGN`` makes the gateway the only
network egress. The endpoint is an OpenAI-compatible chat API (``/chat/completions``, Bearer key; CF-62 is what it was measured to do).
It has one operation, ``chat``, whose arguments are the model id, the messages, the token ceiling and optionally a temperature, a seed and a
response format. The transport sends them and keeps the answer as it came; the adapter reads the answer only far enough to say whether it is
an answer at all (a choice with a message). Whether the content is what the stage asked for is the client's question, asked later on the
stored bytes.

* **Arguments are canonical records.** A capture stores its arguments, which hold the prompt, so they are plain JSON: the temperature is a
  decimal string (a binary float is not a record), and the transport turns it into the number the endpoint wants.
* **The key is never in a message.** It is read from a callable at each exchange, checked to be a plain header value, sent only to the
  configured host and never stored in a capture, an exception or a log. No key means no request.
* **The captures are private.** They hold prompts and answers, reasoning included; they stay in the capture store and a public record
  refers to them by digest only.
* **Limits are unknown.** The endpoint sends no rate-limit headers (CF-62), so the governor holds a conservative prior and the real
  limit is learned from a 429 and its ``Retry-After``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from urllib.parse import urlsplit

from hoshitori import canonical
from hoshitori.breaker import Breaker, BreakerConfig, BreakerSet
from hoshitori.capture import RawCapture
from hoshitori.clock import Clock
from hoshitori.connector import ConnectorError, ErrorClass, classify_status, parse_retry_after
from hoshitori.gateway import Gateway, RateObservation, TransportFailure
from hoshitori.governor import Governor, RateModel
from hoshitori.http_transport import HttpTransport
from hoshitori.ryo_transport import KEY_SHAPE

OPERATION: Final = "chat"
RATE_LIMIT: Final = 30.0
RATE_WINDOW_S: Final = 60.0
ROLES: Final = ("system", "user", "assistant")
MAX_MESSAGES: Final = 16
MAX_MESSAGE_CHARS: Final = 200_000
MAX_TOTAL_CHARS: Final = 400_000
MAX_TOKENS: Final = 65_536
MAX_SEED: Final = 2**31 - 1
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:-]{0,99}")
_SCHEMA_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
_TEMPERATURE = re.compile(r"(?:[01](?:\.[0-9]{1,3})?|2(?:\.0{1,3})?)")  # 0 to 2, at most three places
_ARGUMENTS: Final = frozenset({"model", "messages", "max_tokens", "temperature", "seed", "response_format", "thinking"})


class ModelConfigError(ValueError):
    """An endpoint that cannot be used."""


@dataclass(frozen=True)
class Endpoint:
    """A model endpoint: a short name (it names the provider) and the base URL of its chat API (without ``/chat/completions``)."""

    name: str
    url: str

    def __post_init__(self) -> None:
        parts = urlsplit(self.url)
        if _NAME.fullmatch(self.name) is None:
            raise ModelConfigError("an endpoint's name is 1 to 32 characters of a-z, 0-9, - and _")
        if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ModelConfigError("an endpoint's URL is an http(s) URL with a host and no credentials, query or fragment")

    @property
    def provider(self) -> str:
        return f"model-{self.name}"


def _invalid(message: str) -> ConnectorError:
    return ConnectorError(ErrorClass.INVALID_ARGS, message)


def _messages(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_MESSAGES:
        raise _invalid(f"the messages are a list of 1 to {MAX_MESSAGES}")
    out: list[dict[str, str]] = []
    total = 0
    for message in value:
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise _invalid("a message has a role and a content")
        role, content = message["role"], message["content"]
        if role not in ROLES:
            raise _invalid(f"a message's role is one of {list(ROLES)}")
        if not isinstance(content, str) or not 1 <= len(content) <= MAX_MESSAGE_CHARS:
            raise _invalid(f"a message's content is text of 1 to {MAX_MESSAGE_CHARS} characters")
        total += len(content)
        out.append({"role": role, "content": content})
    if total > MAX_TOTAL_CHARS:
        raise _invalid(f"the messages are at most {MAX_TOTAL_CHARS} characters in all")
    return out


def _response_format(value: object) -> dict[str, Any]:
    if isinstance(value, dict) and value == {"type": "json_object"}:
        return {"type": "json_object"}
    if isinstance(value, dict) and set(value) == {"type", "json_schema"} and value["type"] == "json_schema":
        spec = value["json_schema"]
        if isinstance(spec, dict) and set(spec) <= {"name", "schema", "strict"} and {"name", "schema"} <= set(spec):
            name, schema, strict = spec["name"], spec["schema"], spec.get("strict", True)
            if isinstance(name, str) and _SCHEMA_NAME.fullmatch(name) and isinstance(schema, dict) and isinstance(strict, bool):
                try:
                    canonical.dumps(schema)
                except canonical.CanonicalError:
                    raise _invalid("a response schema is plain JSON with no floating-point numbers") from None
                return {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": strict}}
    raise _invalid("a response format is a JSON object or a named JSON schema")


class ModelAdapter:
    """What the gateway needs of a model endpoint."""

    def __init__(self, endpoint: Endpoint, *, attempt_cap_s: float = 180.0, min_attempt_s: float = 5.0) -> None:
        self.endpoint = endpoint
        self.provider = endpoint.provider
        self._cap = attempt_cap_s
        self._min = min_attempt_s

    def operations(self) -> frozenset[str]:
        return frozenset({OPERATION})

    def normalise(self, operation: str, args: Mapping[str, Any]) -> dict[str, Any]:
        if operation != OPERATION:
            raise _invalid(f"{self.provider} has no operation {operation!r}")
        unknown = set(args) - _ARGUMENTS
        if unknown or not {"model", "messages", "max_tokens"} <= set(args):
            raise _invalid("chat takes a model, messages and max_tokens, and optionally temperature, seed and response_format")
        model, tokens = args["model"], args["max_tokens"]
        if not isinstance(model, str) or _MODEL.fullmatch(model) is None:
            raise _invalid("the model is an id of 1 to 100 characters")
        if isinstance(tokens, bool) or not isinstance(tokens, int) or not 1 <= tokens <= MAX_TOKENS:
            raise _invalid(f"max_tokens is a whole number from 1 to {MAX_TOKENS}")
        out: dict[str, Any] = {"model": model, "messages": _messages(args["messages"]), "max_tokens": tokens}
        if "temperature" in args:
            temperature = args["temperature"]
            if not isinstance(temperature, str) or _TEMPERATURE.fullmatch(temperature) is None:
                raise _invalid("the temperature is a decimal string from 0 to 2 with at most three places")
            out["temperature"] = temperature
        if "seed" in args:
            seed = args["seed"]
            if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= MAX_SEED:
                raise _invalid(f"the seed is a whole number from 0 to {MAX_SEED}")
            out["seed"] = seed
        if "response_format" in args:
            out["response_format"] = _response_format(args["response_format"])
        if "thinking" in args:
            if not isinstance(args["thinking"], bool):
                raise _invalid("thinking is true or false")
            out["thinking"] = args["thinking"]
        return out

    def plan(self, operation: str, args: Mapping[str, Any]) -> float:
        return 1.0

    def attempt_cap_s(self, operation: str) -> float:
        return self._cap

    def min_attempt_s(self, operation: str) -> float:
        return self._min

    def classify(self, capture: RawCapture, now: datetime) -> ConnectorError | None:
        if capture.status is None:
            return ConnectorError(ErrorClass.UPSTREAM, "no response")
        failed = classify_status(capture.status)
        if failed is not None:
            wait = parse_retry_after(capture.headers.get("retry-after"), now)
            return ConnectorError(failed, f"HTTP {capture.status}", retry_after_s=wait)
        try:
            body = json.loads(capture.body)
        except ValueError:
            return ConnectorError(ErrorClass.MALFORMED, f"{self.provider}: the answer is not JSON")
        choices = body.get("choices") if isinstance(body, dict) else None
        first = choices[0] if isinstance(choices, list) and choices else None
        if not isinstance(first, dict) or not isinstance(first.get("message"), dict):
            return ConnectorError(ErrorClass.MALFORMED, f"{self.provider}: the answer has no message")
        return None

    def rate_observation(self, capture: RawCapture) -> RateObservation | None:
        return None


class ModelTransport(HttpTransport):
    """Sends one chat request to the endpoint and returns what came back."""

    def __init__(
        self,
        endpoint: Endpoint,
        key: Callable[[], str | None],
        clock: Callable[[], datetime],
        *,
        allow_insecure_loopback: bool = False,
        ca_file: str | None = None,
    ) -> None:
        super().__init__(endpoint.url, clock, provider=endpoint.provider, allow_insecure_loopback=allow_insecure_loopback, ca_file=ca_file)
        self._key = key

    def _auth_header(self) -> dict[str, str]:
        key = self._key()
        if not key:
            raise TransportFailure(ErrorClass.AUTH, "no model key is configured")
        if KEY_SHAPE.fullmatch(key) is None:
            raise TransportFailure(ErrorClass.AUTH, "the model key is not a usable header value")
        return {"Authorization": f"Bearer {key}"}

    def fetch(self, operation: str, args: Mapping[str, Any], timeout_s: float, capture_id: str, sent_at: datetime) -> RawCapture:
        if operation != OPERATION:
            raise TransportFailure(ErrorClass.INVALID_ARGS, f"there is no operation {operation!r}")
        body = {
            k: float(v) if k == "temperature" else v for k, v in args.items() if k != "thinking"
        }  # a record holds the temperature as text
        if "thinking" in args:  # the endpoint's own name for it (CF-63): the chat template is told whether to reason first
            body["chat_template_kwargs"] = {"enable_thinking": args["thinking"]}
        payload = json.dumps(body, separators=(",", ":")).encode()
        return self._exchange(
            operation, args, "POST", self._prefix + "/chat/completions", payload, self._auth_header(), timeout_s, capture_id, sent_at
        )


@dataclass(frozen=True)
class ModelAttached:
    adapter: ModelAdapter
    governor: Governor
    breakers: BreakerSet
    transport: ModelTransport


def attach_model(
    gateway: Gateway,
    endpoint: Endpoint,
    key: Callable[[], str | None],
    clock: Clock,
    *,
    rate_limit: float = RATE_LIMIT,
    concurrency: int = 4,
    breaker: BreakerConfig | None = None,
    attempt_cap_s: float = 180.0,
    min_attempt_s: float = 5.0,
    allow_insecure_loopback: bool = False,
    ca_file: str | None = None,
) -> ModelAttached:
    """Register an endpoint with ``gateway`` and return its parts; the transport goes in the runner's table under its provider."""
    adapter = ModelAdapter(endpoint, attempt_cap_s=attempt_cap_s, min_attempt_s=min_attempt_s)
    now = clock.now()
    governor = Governor(adapter.provider, RateModel.FIXED_WINDOW, rate_limit, RATE_WINDOW_S, now)
    settings = breaker or BreakerConfig()
    breakers = BreakerSet(Breaker(adapter.provider, settings, now), settings)
    gateway.register(adapter, governor, breakers, concurrency=concurrency)
    transport = ModelTransport(endpoint, key, clock.now, allow_insecure_loopback=allow_insecure_loopback, ca_file=ca_file)
    return ModelAttached(adapter, governor, breakers, transport)
