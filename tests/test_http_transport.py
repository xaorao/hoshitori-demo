"""The shared HTTP exchange against a fake server on loopback: scenarios and mutants."""

from __future__ import annotations

import pytest

from hoshitori import http_transport
from tests.http_transport_checks import failures
from tests.mutation import mutant


def test_real_module_passes_every_check() -> None:
    assert failures(http_transport) == []


# Redundant guards without mutants, each because another rule already decides the same outcome:
# * the per-value header cut (the cut after joining covers it);
# * the redaction of the request URL (configuration already refuses credentials and queries, so there is nothing to redact);
# * the connection's own timeout, which is only a backstop for the connect phase that no portable test can reach.

MUTATIONS: dict[str, tuple[str, str]] = {
    "the connection's timeout is the deadline": (
        "        conn = self._connection(timeout_s * BACKSTOP + 1.0)",
        "        conn = self._connection(timeout_s)",
    ),
    "the connection's timeout is huge": (
        "        conn = self._connection(timeout_s * BACKSTOP + 1.0)",
        "        conn = self._connection(timeout_s * 100)",
    ),
    "a verified certificate is not used": (
        "ssl.create_default_context(cafile=ca_file) if parts.scheme",
        "ssl.create_default_context() if parts.scheme",
    ),
    "every header is kept": ("        if lowered in KEPT_HEADERS or lowered.startswith(KEPT_PREFIXES):", "        if True:"),
    "the rate prefix is not kept": (
        "        if lowered in KEPT_HEADERS or lowered.startswith(KEPT_PREFIXES):",
        "        if lowered in KEPT_HEADERS:",
    ),
    "only the rate prefix is kept": (
        "        if lowered in KEPT_HEADERS or lowered.startswith(KEPT_PREFIXES):",
        "        if lowered.startswith(KEPT_PREFIXES):",
    ),
    "the retry-after header is not kept": ('"date", "retry-after", "x-trace-id"', '"date", "x-trace-id"'),
    "the trace header is not kept": ('"retry-after", "x-trace-id", "server"', '"retry-after", "server"'),
    "the date header is not kept": ('"content-length", "date", "retry-after"', '"content-length", "retry-after"'),
    "the content length is not kept": ('"content-type", "content-length", "date"', '"content-type", "date"'),
    "the content type is not kept": ('{"content-type", "content-length"', '{"content-length"'),
    "header values are not trimmed": ("            text = value.strip()[:MAX_HEADER_VALUE]", "            text = value[:MAX_HEADER_VALUE]"),
    "the joined value is not cut": ("    return {k: v[:MAX_HEADER_VALUE] for k, v in out.items()}", "    return out"),
    "repeated headers replace each other": (
        '            out[lowered] = f"{out[lowered]}, {text}" if lowered in out else text',
        "            out[lowered] = text",
    ),
    "repeated headers are joined without a space": ('f"{out[lowered]}, {text}"', 'f"{out[lowered]},{text}"'),
    "header names keep their case": ("        lowered = name.lower()", "        lowered = name"),
    "any scheme is accepted": ('        if parts.scheme not in ("https", "http") or not parts.hostname', "        if not parts.hostname"),
    "a missing host is accepted": ("or not parts.hostname or parts.username", "or parts.username"),
    "a user name is accepted": ("or parts.username or parts.password", "or parts.password"),
    "a password is accepted": ("or parts.username or parts.password or parts.query", "or parts.username or parts.query"),
    "a query is accepted": ("or parts.password or parts.query or parts.fragment:", "or parts.password or parts.fragment:"),
    "a fragment is accepted": ("or parts.query or parts.fragment:", "or parts.query:"),
    "plain http to anywhere": (
        '        if parts.scheme == "http" and not allow_plain_http and not (allow_insecure_loopback and parts.hostname in LOOPBACK):',
        "        if False:",
    ),
    "plain http is never allowed to a remote host": (
        'parts.scheme == "http" and not allow_plain_http and not',
        'parts.scheme == "http" and not',
    ),
    "plain http is allowed only without the option": (
        'parts.scheme == "http" and not allow_plain_http and not',
        'parts.scheme == "http" and allow_plain_http and not',
    ),
    "plain http without asking": ("not (allow_insecure_loopback and parts.hostname in LOOPBACK)", "not (parts.hostname in LOOPBACK)"),
    "plain http to any host when asked": ("not (allow_insecure_loopback and parts.hostname in LOOPBACK)", "not allow_insecure_loopback"),
    "the loopback list is matched in part": ("parts.hostname in LOOPBACK", "parts.hostname.startswith(('127.0.0.1', 'localhost'))"),
    "localhost is not loopback": (
        'LOOPBACK: Final = frozenset({"127.0.0.1", "localhost", "::1"})',
        'LOOPBACK: Final = frozenset({"127.0.0.1", "::1"})',
    ),
    "the IPv6 loopback is not loopback": (
        'LOOPBACK: Final = frozenset({"127.0.0.1", "localhost", "::1"})',
        'LOOPBACK: Final = frozenset({"127.0.0.1", "localhost"})',
    ),
    "a tiny body limit is accepted": ("        if max_body_bytes < 1024:", "        if max_body_bytes < 0:"),
    "a body limit of 1024 is refused": ("        if max_body_bytes < 1024:", "        if max_body_bytes <= 1024:"),
    "the trailing slash is kept": ('        self._prefix = parts.path.rstrip("/")', "        self._prefix = parts.path"),
    "the port is dropped": ("        self._port = parts.port\n", "        self._port = None\n"),
    "the host is not the configured one": ("        self._host = parts.hostname\n", '        self._host = "127.0.0.1"\n'),
    "TLS is not verified": (
        'ssl.create_default_context(cafile=ca_file) if parts.scheme == "https" else None',
        'ssl._create_unverified_context() if parts.scheme == "https" else None',
    ),
    "compression is accepted": ('"Accept-Encoding": "identity",', '"Accept-Encoding": "gzip",'),
    "the connection is kept": ('"Connection": "close",', '"Connection": "keep-alive",'),
    "the content type is missing": ('            headers.setdefault("Content-Type", "application/json")', "            pass"),
    "the adapter's content type is overwritten": (
        '            headers.setdefault("Content-Type", "application/json")',
        '            headers["Content-Type"] = "application/json"',
    ),
    "the agent is not named": ('USER_AGENT: Final = "hoshitori-gateway/0"', 'USER_AGENT: Final = "python"'),
    "there is no watchdog": (
        "        watchdog = threading.Timer(timeout_s, abort)",
        "        watchdog = threading.Timer(timeout_s * 1000, abort)",
    ),
    "the watchdog fires late": (
        "        watchdog = threading.Timer(timeout_s, abort)",
        "        watchdog = threading.Timer(timeout_s * 20, abort)",
    ),
    "the socket is not cut by the watchdog": (
        "                with contextlib.suppress(OSError):\n                    sock.shutdown(socket.SHUT_RDWR)",
        "                pass",
    ),
    "the watchdog only knows the connection's socket": (
        "            for sock in connected or ([conn.sock] if conn.sock is not None else []):",
        "            for sock in ([conn.sock] if conn.sock is not None else []):",
    ),
    "the socket is not remembered": ("            if conn.sock is not None:\n                connected.append(conn.sock)\n", ""),
    "the watchdog is not cancelled": (
        "        finally:\n            watchdog.cancel()\n            conn.close()",
        "        finally:\n            conn.close()",
    ),
    "the connection is not closed": (
        "        finally:\n            watchdog.cancel()\n            conn.close()",
        "        finally:\n            watchdog.cancel()",
    ),
    "an expired exchange is an answer": (
        '        if expired.is_set():\n            raise TransportFailure(ErrorClass.TIMEOUT, f"no complete answer within {timeout_s:g} s")\n        kept',
        "        kept",
    ),
    "failures are not translated": (
        "        except Exception as exc:\n            raise self._failure(exc, expired.is_set(), timeout_s) from None",
        "        except Exception:\n            raise",
    ),
    "transport failures are translated again": ("        except TransportFailure:\n            raise\n", ""),
    "a short body is accepted": (
        '        if declared is not None and declared.isdigit() and int(declared) != len(body):\n            raise TransportFailure(ErrorClass.UPSTREAM, f"the body is {len(body)} bytes but {declared} were announced")\n',
        "",
    ),
    "the announced length is not checked when it is numeric": ("declared.isdigit() and int(declared) != len(body)", "False"),
    "the body limit is not applied": (
        '            if total > self._max_body:\n                raise TransportFailure(ErrorClass.MALFORMED, f"the answer is longer than {self._max_body} bytes")\n',
        "",
    ),
    "the body limit is one too small": ("            if total > self._max_body:", "            if total >= self._max_body:"),
    "the body limit is a failure of another kind": (
        'raise TransportFailure(ErrorClass.MALFORMED, f"the answer is longer than',
        'raise TransportFailure(ErrorClass.UPSTREAM, f"the answer is longer than',
    ),
    "only the first chunk is kept": (
        '            chunks.append(chunk)\n        return b"".join(chunks)',
        '            chunks.append(chunk)\n            break\n        return b"".join(chunks)',
    ),
    "the body is cut": ('        return b"".join(chunks)', '        return b"".join(chunks)[:100]'),
    "the receive time is the send time": (
        "        received = max(self._clock(), sent_at)\n        return RawCapture(capture_id, self._provider, request, status",
        "        received = sent_at\n        return RawCapture(capture_id, self._provider, request, status",
    ),
    "the receive time may precede the send": (
        "        received = max(self._clock(), sent_at)\n        return RawCapture(capture_id, self._provider, request, status",
        "        received = self._clock()\n        return RawCapture(capture_id, self._provider, request, status",
    ),
    "the provider is fixed": (
        "        return RawCapture(capture_id, self._provider, request, status",
        "        return RawCapture(capture_id, PROVIDER, request, status",
    ),
    "the trace id is not taken": (
        'RawCapture(capture_id, self._provider, request, status, kept, body, sent_at, received, kept.get("x-trace-id"))',
        "RawCapture(capture_id, self._provider, request, status, kept, body, sent_at, received, None)",
    ),
    "the whole headers are stored": (
        "RawCapture(capture_id, self._provider, request, status, kept, body",
        "RawCapture(capture_id, self._provider, request, status, dict(response_headers), body",
    ),
    "the request is not recorded with its arguments": (
        '            "arguments": dict(args),\n            "method": method,',
        '            "arguments": {},\n            "method": method,',
    ),
    "the URL loses its port": (
        '        scheme_host = f"{self._scheme}://{self._host}" + (f":{self._port}" if self._port else "")',
        '        scheme_host = f"{self._scheme}://{self._host}"',
    ),
    "an expired exchange is not a timeout": (
        "        if expired or isinstance(exc, TimeoutError | socket.timeout):",
        "        if isinstance(exc, TimeoutError | socket.timeout):",
    ),
    "a timeout error is not a timeout": ("        if expired or isinstance(exc, TimeoutError | socket.timeout):", "        if expired:"),
    "a timeout is an upstream failure": (
        '            return TransportFailure(ErrorClass.TIMEOUT, f"no complete answer within {timeout_s:g} s")\n        if isinstance(exc, ssl.SSLError):',
        '            return TransportFailure(ErrorClass.UPSTREAM, f"no complete answer within {timeout_s:g} s")\n        if isinstance(exc, ssl.SSLError):',
    ),
    "TLS failures are upstream failures": (
        'return TransportFailure(ErrorClass.UNCLASSIFIED, f"TLS failure: {type(exc).__name__}")',
        'return TransportFailure(ErrorClass.UPSTREAM, f"TLS failure: {type(exc).__name__}")',
    ),
    "TLS failures are not recognised": ("        if isinstance(exc, ssl.SSLError):", "        if False:"),
    "a refused connection is unclassified": (
        '            return TransportFailure(ErrorClass.UPSTREAM, "the connection was refused")',
        '            return TransportFailure(ErrorClass.UNCLASSIFIED, "the connection was refused")',
    ),
    "a lost connection is unclassified": (
        'return TransportFailure(ErrorClass.UPSTREAM, f"the connection was lost: {type(exc).__name__}")',
        'return TransportFailure(ErrorClass.UNCLASSIFIED, f"the connection was lost: {type(exc).__name__}")',
    ),
    "invalid HTTP is unclassified": (
        'return TransportFailure(ErrorClass.UPSTREAM, f"the answer was not valid HTTP: {type(exc).__name__}")',
        'return TransportFailure(ErrorClass.UNCLASSIFIED, f"the answer was not valid HTTP: {type(exc).__name__}")',
    ),
    "other network errors are unclassified": (
        'return TransportFailure(ErrorClass.UPSTREAM, f"network error: {type(exc).__name__}")',
        'return TransportFailure(ErrorClass.UNCLASSIFIED, f"network error: {type(exc).__name__}")',
    ),
    "an unexpected error is an upstream failure": (
        'return TransportFailure(ErrorClass.UNCLASSIFIED, f"unexpected {type(exc).__name__}")',
        'return TransportFailure(ErrorClass.UPSTREAM, f"unexpected {type(exc).__name__}")',
    ),
}

# Which parts of the scenarios can notice each mutant. A mutant is run only against these (a part that starts a server costs about a second);
# the full set runs against the real module.
PARTS: dict[str, tuple[str, ...]] = {}


def _assign(parts: tuple[str, ...], *names: str) -> None:
    for name in names:
        assert name in MUTATIONS, name
        assert name not in PARTS, name
        PARTS[name] = parts


_assign(
    ("hygiene",),
    "the connection's timeout is the deadline",
    "the connection's timeout is huge",
    "the watchdog is not cancelled",
    "the connection is not closed",
)
_assign(("tls",), "a verified certificate is not used", "TLS is not verified")
_assign(
    ("headers",),
    "every header is kept",
    "the rate prefix is not kept",
    "only the rate prefix is kept",
    "the retry-after header is not kept",
    "the trace header is not kept",
    "the date header is not kept",
    "the content length is not kept",
    "the content type is not kept",
    "header values are not trimmed",
    "the joined value is not cut",
    "repeated headers replace each other",
    "repeated headers are joined without a space",
    "header names keep their case",
)
_assign(
    ("config",),
    "any scheme is accepted",
    "a missing host is accepted",
    "a user name is accepted",
    "a password is accepted",
    "a query is accepted",
    "a fragment is accepted",
    "plain http to anywhere",
    "plain http is never allowed to a remote host",
    "plain http is allowed only without the option",
    "plain http without asking",
    "plain http to any host when asked",
    "the loopback list is matched in part",
    "localhost is not loopback",
    "the IPv6 loopback is not loopback",
    "a tiny body limit is accepted",
    "a body limit of 1024 is refused",
)
_assign(("config", "faults"), "the trailing slash is kept")
_assign(
    ("exchange",),
    "the port is dropped",
    "the host is not the configured one",
    "compression is accepted",
    "the connection is kept",
    "the content type is missing",
    "the adapter's content type is overwritten",
    "the agent is not named",
    "the trace id is not taken",
    "the whole headers are stored",
    "the request is not recorded with its arguments",
    "the URL loses its port",
)
_assign(
    ("faults",),
    "there is no watchdog",
    "the watchdog fires late",
    "the socket is not cut by the watchdog",
    "the watchdog only knows the connection's socket",
    "the socket is not remembered",
    "an expired exchange is an answer",
    "failures are not translated",
    "transport failures are translated again",
    "a short body is accepted",
    "the announced length is not checked when it is numeric",
    "the body limit is not applied",
    "the body limit is one too small",
    "the body limit is a failure of another kind",
    "only the first chunk is kept",
    "the body is cut",
    "the provider is fixed",
)
_assign(("exchange", "parallel"), "the receive time is the send time", "the receive time may precede the send")
_assign(
    ("failure_map",),
    "an expired exchange is not a timeout",
    "a timeout error is not a timeout",
    "a timeout is an upstream failure",
    "TLS failures are upstream failures",
    "TLS failures are not recognised",
    "a refused connection is unclassified",
    "a lost connection is unclassified",
    "invalid HTTP is unclassified",
    "other network errors are unclassified",
    "an unexpected error is an upstream failure",
)


def test_every_mutant_has_the_parts_that_can_notice_it() -> None:
    assert set(PARTS) == set(MUTATIONS)


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.http_transport", old, new)
    try:
        found = failures(module, PARTS[name])
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
