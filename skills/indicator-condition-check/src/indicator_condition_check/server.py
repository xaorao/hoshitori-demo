"""Three ways to call the skill: Python (`run`), MCP (stdio or HTTP JSON-RPC), REST.

The REST shape mirrors RYO's builder API: GET /health, GET /tools, POST /tools/<name>/call with a bare argument object. MCP speaks JSON-RPC 2.0
in two eras on stdio and on one HTTP endpoint (POST /), following the MCP specification and the official TypeScript and Python SDK servers:

* 2024-11-05 to 2025-11-25 (handshake): `initialize` (the requested revision when served, otherwise the latest), `notifications/initialized`,
  `ping`, `tools/list`, `tools/call`, and JSON-RPC batches, which 2025-03-26 requires a server to receive.
* 2026-07-28 (stateless): no handshake; every request carries `_meta` with the protocol version and the client capabilities; `server/discover`;
  every result carries `resultType` and the server identity in `_meta`; `server/discover` and `tools/list` carry `ttlMs` and `cacheScope`;
  an unserved version is error -32022 with the served versions. On HTTP the `MCP-Protocol-Version`, `Mcp-Method` and `Mcp-Name` headers must
  agree with the body (error -32020).

`answer` is the transport-independent core: it returns the reply with the HTTP status it is owed, so stdio and HTTP give the same JSON-RPC answer.
"""

from __future__ import annotations

import base64
import json
import re
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import IO, Any
from urllib.parse import urlparse

from . import __version__, platform_skills
from .errors import InputError
from .schema import TOOL_DEFINITION, TOOL_NAME
from .skill import run

LEGACY_PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")  # newest first: the first is the initialize counter-offer
STATELESS_PROTOCOL = "2026-07-28"
MODERN_PROTOCOLS = (STATELESS_PROTOCOL,)
SUPPORTED_PROTOCOLS = (*MODERN_PROTOCOLS, *LEGACY_PROTOCOLS)
META_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
META_CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
HEADER_MISMATCH = -32020
UNSUPPORTED_PROTOCOL_VERSION = -32022
VERSION_HEADER = "mcp-protocol-version"
METHOD_HEADER = "mcp-method"
NAME_HEADER = "mcp-name"
NAME_BEARING = {"tools/call": "name", "prompts/get": "name", "resources/read": "uri"}  # the body field each method's Mcp-Name mirrors
MAX_BATCH = 100
TOOLS_TTL_MS = 300_000
MAX_BODY = 64 * 1024
MAX_DRAIN = 1024 * 1024
RATE_LIMIT_PER_MINUTE = 60
MAX_CONCURRENT = 8
SERVER_INFO = {"name": "indicator-condition-check", "version": __version__}
SENTINEL_PREFIX = "=?base64?"
SENTINEL_SUFFIX = "?="
CANONICAL_BASE64 = re.compile(r"(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?")
Runner = Callable[[dict[str, Any]], dict[str, Any]]
Headers = Mapping[str, str]  # lower-case header name -> value, repeated headers joined with ", "
Answer = tuple[int, Any]  # HTTP status, reply (None when no reply is owed)


def _error_body(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details, "trace_id": uuid.uuid4().hex}


def decode_header_value(value: str) -> str | None:
    """An `Mcp-Name` value as the body carries it. A value in the Base64 sentinel `=?base64?...?=` (lower-case markers) is decoded as UTF-8;
    any other value is returned unchanged. None when the sentinel does not hold padded, canonical Base64 of UTF-8 bytes."""
    if not (value.startswith(SENTINEL_PREFIX) and value.endswith(SENTINEL_SUFFIX)):
        return value
    payload = value[len(SENTINEL_PREFIX) : len(value) - len(SENTINEL_SUFFIX)]
    if CANONICAL_BASE64.fullmatch(payload) is None:
        return None
    try:
        return base64.b64decode(payload).decode("utf-8")  # the pattern above admits only well-formed, padded Base64
    except UnicodeDecodeError:
        return None


def is_json_content_type(value: str | None) -> bool:
    """Whether a Content-Type names application/json: the media type before any parameter, compared without case."""
    if not value:
        return False
    return value.partition(";")[0].strip().lower() == "application/json"


def _is_modern(version: str) -> bool:
    """Revision identifiers are ISO dates, so the stateless era is every identifier that sorts at or after its first revision."""
    return version >= STATELESS_PROTOCOL


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def _kind(msg: Any) -> str | None:
    """'request', 'notification' or 'response' for a well-formed JSON-RPC 2.0 message, otherwise None. Ids are text or whole numbers and
    never null; a method is text; params, when present, are an object."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return None
    if "method" in msg:
        if not isinstance(msg["method"], str) or not isinstance(msg.get("params", {}), dict):
            return None
        if "id" not in msg:
            return "notification"
        return "request" if _valid_id(msg["id"]) else None
    if "id" in msg and not _valid_id(msg["id"]):
        return None
    if "error" in msg and "result" not in msg:
        return "response"
    if "result" in msg and "error" not in msg and "id" in msg:
        return "response"
    return None


def _error(mid: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        body["data"] = data
    return {"jsonrpc": "2.0", "id": mid, "error": body}


def _mismatch(mid: Any, header: str, body: str) -> dict[str, Any]:
    return _error(
        mid, HEADER_MISMATCH, f"Bad Request: the request headers and body disagree: {body}", {"mismatch": {"header": header, "body": body}}
    )


def _unsupported(mid: Any, requested: str) -> dict[str, Any]:
    return _error(
        mid, UNSUPPORTED_PROTOCOL_VERSION, "Unsupported protocol version", {"supported": list(MODERN_PROTOCOLS), "requested": requested}
    )


def _bad_envelope(mid: Any, key: str, problem: str) -> dict[str, Any]:
    message = f"Invalid _meta envelope for protocol revision {STATELESS_PROTOCOL}: {key}: {problem}"
    return _error(mid, INVALID_PARAMS, message, {"envelope": {"key": key, "problem": problem}})


def _unserved_header(mid: Any, value: str) -> dict[str, Any]:
    return _error(
        mid, INVALID_PARAMS, f"Bad Request: Unsupported protocol version: {value} (supported versions: {', '.join(LEGACY_PROTOCOLS)})"
    )


def _missing_envelope(mid: Any, header: str, params: Mapping[str, Any]) -> dict[str, Any]:
    meta = _meta_of(params)
    missing = ["_meta"] if meta is None else [key for key in (META_VERSION_KEY, META_CAPABILITIES_KEY) if key not in meta]
    message = (
        f"Invalid params: the MCP-Protocol-Version header names protocol revision {header}, but the request is missing "
        f"the required per-request envelope key(s): {', '.join(missing)}"
    )
    return _error(mid, INVALID_PARAMS, message, {"envelope": {"missing": missing}})


def _meta_of(params: Mapping[str, Any]) -> dict[str, Any] | None:
    meta = params.get("_meta")
    return meta if isinstance(meta, dict) else None


def _claims(params: Mapping[str, Any]) -> bool:
    """Whether the message claims the stateless era: its `_meta` holds the protocol-version key, whatever the value."""
    meta = _meta_of(params)
    return meta is not None and META_VERSION_KEY in meta


def _valid_modern_claim(params: Mapping[str, Any]) -> bool:
    """A claim that names a stateless-era revision and carries the client capabilities: only this takes `initialize` out of the handshake."""
    meta = _meta_of(params)
    if meta is None:
        return False
    version = meta.get(META_VERSION_KEY)
    return isinstance(version, str) and _is_modern(version) and isinstance(meta.get(META_CAPABILITIES_KEY), dict)


def _header(headers: Headers | None, name: str) -> str | None:
    if headers is None:
        return None
    value = headers.get(name)
    return None if value is None else value.strip(" \t")


def _result(mid: Any, result: dict[str, Any], stateless: bool) -> dict[str, Any]:
    if stateless:
        result = {"resultType": "complete", **result, "_meta": {META_SERVER_INFO_KEY: dict(SERVER_INFO)}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def answer(body: Any, runner: Runner = run, headers: Headers | None = None) -> Answer:
    """The reply to one decoded JSON-RPC body (a message or a batch) and the HTTP status to send it with; (202, None) when no reply is owed.

    `headers` maps the lower-case names `mcp-protocol-version`, `mcp-method` and `mcp-name` to their values, or is None on a transport without
    headers (stdio), where the header rules do not apply."""
    if isinstance(body, list):
        return _answer_batch(body, runner, headers)
    return _answer_one(body, runner, headers)


def handle_rpc(msg: Any, runner: Runner = run) -> Any:
    """The reply to one JSON-RPC message or batch on a transport without headers; None when no reply is owed."""
    return answer(msg, runner, None)[1]


def _answer_one(msg: Any, runner: Runner, headers: Headers | None) -> Answer:
    kind = _kind(msg)
    if kind is None:
        return 400, _error(None, INVALID_REQUEST, "Invalid Request")
    if kind == "response":
        return 202, None
    method: str = msg["method"]
    mid = msg.get("id")
    params: dict[str, Any] = msg.get("params", {})
    if kind == "request" and method == "initialize" and not _valid_modern_claim(params):
        version_header = _header(headers, VERSION_HEADER)
        if version_header is not None and _is_modern(version_header):
            return 400, _mismatch(
                mid, version_header, "an initialize request (legacy handshake) was sent with a modern MCP-Protocol-Version header"
            )
        asked = params.get("protocolVersion")
        version = asked if asked in LEGACY_PROTOCOLS else LEGACY_PROTOCOLS[0]
        return 200, _result(
            mid, {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": dict(SERVER_INFO)}, False
        )
    if _claims(params):
        return _answer_stateless(kind, method, mid, params, runner, headers)
    return _answer_legacy(kind, method, mid, params, runner, headers)


def _answer_stateless(kind: str, method: str, mid: Any, params: dict[str, Any], runner: Runner, headers: Headers | None) -> Answer:
    meta: dict[str, Any] = params["_meta"]
    version = meta[META_VERSION_KEY]
    request = kind == "request"
    if request and META_CAPABILITIES_KEY not in meta:  # a missing key is reported before a malformed one
        return 400, _bad_envelope(mid, META_CAPABILITIES_KEY, "missing")
    if not isinstance(version, str):
        return 400, _bad_envelope(mid, META_VERSION_KEY, "expected a string")
    if request and not isinstance(meta[META_CAPABILITIES_KEY], dict):
        return 400, _bad_envelope(mid, META_CAPABILITIES_KEY, "expected an object")
    version_header = _header(headers, VERSION_HEADER)
    if version_header is not None and version_header != version:
        noun = "body" if request else "notification"
        return 400, _mismatch(
            mid,
            version_header,
            f"the {noun} envelope names protocol version {version} but the MCP-Protocol-Version header names {version_header}",
        )
    method_header = _header(headers, METHOD_HEADER)
    if method_header is not None and method_header != method and (request or _is_modern(version)):
        noun = "body" if request else "notification body"
        return 400, _mismatch(mid, method_header, f"the {noun} names method {method} but the Mcp-Method header names {method_header}")
    if version not in MODERN_PROTOCOLS:
        return 400, _unsupported(mid, version)
    if not request:
        return 202, None
    if headers is not None:
        refused = _standard_headers(mid, method, params, version, headers)
        if refused is not None:
            return 400, refused
    return _dispatch(method, mid, params, runner, True)


def _standard_headers(mid: Any, method: str, params: Mapping[str, Any], version: str, headers: Headers) -> dict[str, Any] | None:
    """Presence of the version and method headers, and the Mcp-Name rules, after the version gate."""
    if _header(headers, VERSION_HEADER) is None:
        return _mismatch(
            mid, "(missing)", f"the body envelope names protocol version {version} but the required MCP-Protocol-Version header is absent"
        )
    if _header(headers, METHOD_HEADER) is None:
        return _mismatch(mid, "(missing)", f"the body names method {method} but the required Mcp-Method header is absent")
    field = NAME_BEARING.get(method)
    if field is None:
        return None
    raw = params.get(field)
    value = raw if isinstance(raw, str) else None
    name_header = _header(headers, NAME_HEADER)
    if name_header is None:
        if value is None:
            return None
        return _mismatch(mid, "(missing)", f'the body carries params.{field}="{value}" but the required Mcp-Name header is absent')
    decoded = decode_header_value(name_header)
    if decoded is None:
        return _mismatch(mid, name_header, "the Mcp-Name header carries an invalid Base64 sentinel value")
    if value is not None and decoded != value:
        return _mismatch(mid, name_header, f'the body carries params.{field}="{value}" but the Mcp-Name header names "{decoded}"')
    return None


def _answer_legacy(kind: str, method: str, mid: Any, params: dict[str, Any], runner: Runner, headers: Headers | None) -> Answer:
    version_header = _header(headers, VERSION_HEADER)
    if version_header is not None and version_header not in LEGACY_PROTOCOLS:
        if not _is_modern(version_header):
            return 400, _unserved_header(mid, version_header)
        if kind == "request":
            return 400, _missing_envelope(mid, version_header, params)
        method_header = _header(headers, METHOD_HEADER)
        if method_header is not None and method_header != method:
            return 400, _mismatch(
                mid, method_header, f"the notification body names method {method} but the Mcp-Method header names {method_header}"
            )
        if version_header not in MODERN_PROTOCOLS:
            return 400, _unsupported(mid, version_header)
        return 202, None
    if kind == "notification":
        return 202, None
    return _dispatch(method, mid, params, runner, False)


def _dispatch(method: str, mid: Any, params: Mapping[str, Any], runner: Runner, stateless: bool) -> Answer:
    if stateless and method == "server/discover":
        discovered = {
            "supportedVersions": list(MODERN_PROTOCOLS),
            "capabilities": {"tools": {"listChanged": False}},
            "ttlMs": TOOLS_TTL_MS,
            "cacheScope": "public",
        }
        return 200, _result(mid, discovered, True)
    if not stateless and method == "ping":
        return 200, _result(mid, {}, False)
    if method == "tools/list":
        listing: dict[str, Any] = {"tools": [TOOL_DEFINITION]}
        if stateless:
            listing.update({"ttlMs": TOOLS_TTL_MS, "cacheScope": "public"})
        return 200, _result(mid, listing, stateless)
    if method == "tools/call":
        return 200, _call_tool(mid, params, runner, stateless)
    return (404 if stateless else 200), _error(mid, METHOD_NOT_FOUND, f"Method not found: {method}")


def _tool_error(body: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(body)}], "isError": True}


def _call_tool(mid: Any, params: Mapping[str, Any], runner: Runner, stateless: bool) -> dict[str, Any]:
    name = params.get("name")
    if name != TOOL_NAME:
        return _error(mid, INVALID_PARAMS, f"Unknown tool: {name!r}")
    try:
        envelope = runner(params.get("arguments") or {})
    except InputError as e:
        return _result(mid, _tool_error(e.to_envelope(uuid.uuid4().hex)), stateless)
    except Exception as e:  # noqa: BLE001 - a tool must never take the server down
        return _result(mid, _tool_error(_error_body("INTERNAL_ERROR", type(e).__name__)), stateless)
    return _result(
        mid, {"content": [{"type": "text", "text": json.dumps(envelope)}], "structuredContent": envelope, "isError": False}, stateless
    )


def _answer_batch(items: list[Any], runner: Runner, headers: Headers | None) -> Answer:
    if not items:
        return 400, _error(None, INVALID_REQUEST, "Bad Request: empty JSON-RPC batch")
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("params"), dict) and _claims(item["params"]):
            return 400, _error(
                None, INVALID_REQUEST, "Bad Request: JSON-RPC batches may not contain requests for protocol revision 2026-07-28 or later"
            )
        if _kind(item) is None:
            return 400, _error(None, INVALID_REQUEST, "Bad Request: JSON-RPC batch contains an invalid message")
    if len(items) > MAX_BATCH:
        return 400, _error(None, INVALID_REQUEST, f"Invalid Request: Batch must not exceed {MAX_BATCH} messages")
    initializing = any(_kind(item) == "request" and item["method"] == "initialize" for item in items)
    if initializing and len(items) > 1:
        return 400, _error(None, INVALID_REQUEST, "Invalid Request: Only one initialization request is allowed")
    version_header = _header(headers, VERSION_HEADER)
    if not initializing and version_header is not None and version_header not in LEGACY_PROTOCOLS:
        return 400, _unserved_header(None, version_header)
    replies = [reply for reply in (_answer_one(item, runner, None)[1] for item in items) if reply is not None]
    return (200, replies) if replies else (202, None)


def _cost(body: Any) -> int:
    """Rate-limit units of one POST: one per request in a batch, and never fewer than one."""
    if isinstance(body, list):
        return max(1, sum(1 for item in body if _kind(item) == "request"))
    return 1


def serve_stdio(runner: Runner = run, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> None:
    source = sys.stdin if stdin is None else stdin
    sink = sys.stdout if stdout is None else stdout
    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except (ValueError, RecursionError):
            reply: Any = _error(None, PARSE_ERROR, "Parse error")
        else:
            reply = handle_rpc(msg, runner)
        if reply is not None:
            sink.write(json.dumps(reply) + "\n")
            sink.flush()


class _RateLimiter:
    """Sliding one-minute window per client address. Servers MUST rate limit tool invocations (MCP tools spec)."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute, self.clock = per_minute, clock
        self.hits: dict[str, deque[float]] = {}
        self.lock = threading.Lock()

    def allow(self, who: str, cost: int = 1) -> bool:
        """Charge `cost` units to `who` if they fit in the window; a refused charge records nothing."""
        now = self.clock()
        with self.lock:
            q = self.hits.setdefault(who, deque())
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) + cost > self.per_minute:
                return False
            q.extend([now] * cost)
            return True


def _host_name(host_header: str | None) -> str:
    """The host of a Host header without its port; an IPv6 literal, which the header writes in brackets (RFC 3986 3.2.2), without them."""
    text = host_header or ""
    if text.startswith("["):
        name, closed, _ = text[1:].partition("]")
        return name if closed else ""
    return text.partition(":")[0]


def _origin_allowed(origin: str | None, host_header: str | None, extra: frozenset[str]) -> bool:
    """Reject browser requests from foreign origins (DNS-rebinding / CSRF defence). Non-browser clients send no Origin."""
    if not origin:
        return True
    try:
        host = urlparse(origin).hostname or ""
    except ValueError:
        return False
    if origin in extra:
        return True
    return host in ("localhost", "127.0.0.1", "::1") and _host_name(host_header) in ("localhost", "127.0.0.1", "::1")


class _Refused:
    """Marks a request body that was already answered with an error."""


REFUSED = _Refused()


def make_http_handler(
    runner: Runner = run,
    *,
    rate_limit_per_minute: int = RATE_LIMIT_PER_MINUTE,
    allowed_origins: frozenset[str] = frozenset(),
    clock: Callable[[], float] = time.monotonic,
) -> type[BaseHTTPRequestHandler]:
    limiter = _RateLimiter(rate_limit_per_minute, clock)
    slots = threading.BoundedSemaphore(MAX_CONCURRENT)
    call_path = f"/tools/{TOOL_NAME}/call"
    invoke_path = f"/api/skills/{TOOL_NAME}/invoke"

    class Handler(BaseHTTPRequestHandler):
        server_version = "indicator-condition-check"
        timeout = 10  # seconds a connection may stall: a stalled one gives its slot back instead of holding it for good

        def log_message(self, format: str, *args: Any) -> None:  # quiet by default
            return

        def _send(self, status: int, body: Any, extra_headers: Mapping[str, str] | None = None) -> None:
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def _error(self, status: int, code: str, message: str, **details: Any) -> None:
            self._send(status, _error_body(code, message, **details))

        def _read_body(self) -> bytes | None:
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_BODY:
                if length > MAX_BODY:  # drain a bounded amount so the client reads our 413 instead of a connection reset
                    remaining = min(length, MAX_DRAIN)
                    while remaining > 0:
                        chunk = self.rfile.read(min(65536, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                self.close_connection = True
                self._error(413 if length > MAX_BODY else 400, "INVALID_BODY", f"body must be at most {MAX_BODY} bytes")
                return None
            return self.rfile.read(length)

        def _read_json(self) -> Any:
            raw = self._read_body()
            if raw is None:
                return REFUSED
            try:
                return json.loads(raw or b"{}")
            except (ValueError, RecursionError):
                self._error(400, "INVALID_JSON", "body is not valid JSON")
                return REFUSED

        def _gate(self) -> bool:
            if not _origin_allowed(self.headers.get("Origin"), self.headers.get("Host"), allowed_origins):
                self._error(403, "FORBIDDEN_ORIGIN", "requests from this origin are not allowed")
                return False
            return True

        def _not_post(self) -> None:
            self._send(405, _error_body("METHOD_NOT_ALLOWED", "the MCP endpoint accepts POST only"), {"Allow": "POST"})

        def _mcp_headers(self) -> dict[str, str]:
            found: dict[str, str] = {}
            for name in (VERSION_HEADER, METHOD_HEADER, NAME_HEADER):
                values = self.headers.get_all(name)
                if values:
                    found[name] = ", ".join(str(v).strip(" \t") for v in values)
            return found

        def do_GET(self) -> None:
            if not self._gate():
                return
            if self.path == "/health":
                self._send(
                    200,
                    {
                        "status": "ok",
                        "protocol_version": LEGACY_PROTOCOLS[-1],
                        "supported_versions": list(SUPPORTED_PROTOCOLS),
                        "server": SERVER_INFO["name"],
                        "tools": 1,
                    },
                )
            elif self.path == "/tools":
                self._send(200, {"tools": [TOOL_DEFINITION]})
            elif self.path in ("/api/skills", "/api/skills/"):
                self._send(200, platform_skills.listing([TOOL_DEFINITION]))
            elif self.path == f"/api/skills/{TOOL_NAME}":
                self._send(200, platform_skills.definition(TOOL_DEFINITION))
            elif self.path == "/":
                self._not_post()
            else:
                self._error(404, "NOT_FOUND", f"no route {self.path}")

        def _other_method(self) -> None:
            if not self._gate():
                return
            if self.path == "/":
                self._not_post()
            else:
                self._error(404, "NOT_FOUND", f"no route {self.path}")

        do_DELETE = _other_method
        do_PUT = _other_method
        do_PATCH = _other_method

        def do_POST(self) -> None:
            if not self._gate():
                return
            if (self.path == call_path or self.path == invoke_path) and not limiter.allow(self.client_address[0]):
                self._send(429, _error_body("RATE_LIMITED", "too many requests; slow down"), {"Retry-After": "10"})
                return
            if not slots.acquire(blocking=False):
                self._send(503, _error_body("BUSY", "too many concurrent requests"), {"Retry-After": "2"})
                return
            try:
                self._post()
            finally:
                slots.release()

        def _post(self) -> None:
            if self.path == call_path:
                body = self._read_json()
                if body is REFUSED:
                    return
                try:
                    self._send(200, {"result": runner(body)})
                except InputError as e:
                    self._send(400, e.to_envelope(uuid.uuid4().hex))
                except Exception as e:  # noqa: BLE001
                    self._error(500, "INTERNAL_ERROR", type(e).__name__)
            elif self.path == invoke_path or (self.path.startswith("/api/skills/") and self.path.endswith("/invoke")):
                body = self._read_json()
                if body is REFUSED:
                    return
                name = self.path.split("/")[3]
                status, payload = platform_skills.invoke(name, body, {TOOL_NAME: runner})
                self._send(status, payload)
            elif self.path.startswith("/tools/"):
                self._error(404, "UNKNOWN_TOOL", f"unknown tool {self.path[7:].split('/')[0]!r}")
            elif self.path == "/":
                self._mcp()
            else:
                self._error(404, "NOT_FOUND", f"no route {self.path}")

        def _mcp(self) -> None:
            if not is_json_content_type(self.headers.get("Content-Type")):
                self._error(415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json")
                return
            raw = self._read_body()
            if raw is None:
                return
            try:
                body = json.loads(raw)
            except (ValueError, RecursionError):
                self._send(400, _error(None, PARSE_ERROR, "Parse error"))
                return
            if not limiter.allow(self.client_address[0], _cost(body)):
                self._send(429, _error_body("RATE_LIMITED", "too many requests; slow down"), {"Retry-After": "10"})
                return
            status, reply = answer(body, runner, self._mcp_headers())
            if reply is None:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._send(status, reply)

    return Handler


def serve_http(host: str = "127.0.0.1", port: int = 8765, runner: Runner = run, **kwargs: Any) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_http_handler(runner, **kwargs))
