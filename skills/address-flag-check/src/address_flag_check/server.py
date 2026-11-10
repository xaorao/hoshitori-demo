"""MCP (stdio, or JSON-RPC over HTTP on ``POST /``) and REST, as in the indicator skill, with the official TypeScript SDK's HTTP behaviour.

REST follows the recorded shapes of RYO's builder API: ``GET /health``, ``GET /tools`` (a list of tool definitions),
``POST /tools/<name>/call`` with a bare argument object, answered ``{"tool", "result", "latency_ms"}``; failures are
``{code, message, details, trace_id}``.

MCP serves two protocol eras on the same endpoint:

* 2024-11-05 to 2025-11-25: ``initialize`` (an unknown revision is answered with 2025-11-25, never the stateless one),
  ``notifications/initialized``, ``ping``, ``tools/list``, ``tools/call``. Over HTTP a present ``MCP-Protocol-Version`` header must name
  one of these (HTTP 400, -32000 otherwise); a missing one is accepted.
* 2026-07-28 (stateless): every request carries ``_meta["io.modelcontextprotocol/protocolVersion"]``; ``server/discover`` puts the server's
  identity in ``_meta["io.modelcontextprotocol/serverInfo"]``; every result carries ``resultType``; ``tools/list`` carries ``ttlMs`` and
  ``cacheScope``; an unsupported revision is HTTP 400 with -32022 and ``{supported, requested}``. Over HTTP the ``MCP-Protocol-Version``,
  ``Mcp-Method`` and (for ``tools/call``) ``Mcp-Name`` headers must be present and agree with the body, else HTTP 400 with -32020.

These behaviours were read in the TypeScript SDK (``createMcpHandler.ts``, ``inboundClassification.ts``,
``rev2026-07-28/buildSchemas.ts``) and the draft specification (``streamable-http.mdx``, ``discover.mdx``); nothing of that code is copied.
Batches are refused (HTTP 400, -32600). GET and DELETE on the MCP endpoint are 405 with ``Allow: POST``. Foreign browser origins get 403;
tool calls are rate limited per client.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import IO, Any
from urllib.parse import urlparse

from . import __version__, platform_skills
from .errors import InputError
from .schema import SOURCES_TOOL_NAME, TOOL_DEFINITIONS, TOOL_NAME
from .skill import run, run_sources

LEGACY_PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
STATELESS_PROTOCOL = "2026-07-28"
SUPPORTED_PROTOCOLS = (STATELESS_PROTOCOL, *LEGACY_PROTOCOLS)
META_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"
UNSUPPORTED_PROTOCOL_VERSION = -32022
HEADER_MISMATCH = -32020
TOOLS_TTL_MS = 300_000
MAX_BODY = 64 * 1024
MAX_DRAIN = 1024 * 1024
RATE_LIMIT_PER_MINUTE = 60
MAX_CONCURRENT = 8
SERVER_INFO = {"name": "address-flag-check", "version": __version__}
SENTINEL = ("=?base64?", "?=")
_BASE64 = re.compile(r"(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?")
Runner = Callable[[object], dict[str, Any]]
RUNNERS: dict[str, Runner] = {TOOL_NAME: run, SOURCES_TOOL_NAME: run_sources}


@dataclass(frozen=True)
class RpcOutcome:
    reply: dict[str, Any] | None  # None: a notification, answered 202 with no body
    http_status: int


def _error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details, "trace_id": uuid.uuid4().hex}


def decode_header_value(value: str) -> str | None:
    """An ``Mcp-Name`` value: plain, or ``=?base64?…?=`` with canonical Base64 of UTF-8. None when the sentinel form is broken."""
    if not (value.startswith(SENTINEL[0]) and value.endswith(SENTINEL[1])):
        return value
    payload = value[len(SENTINEL[0]) : len(value) - len(SENTINEL[1])]
    if not _BASE64.fullmatch(payload):
        return None
    try:
        return base64.b64decode(payload, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None


def _rpc_error(mid: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        body["data"] = data
    return {"jsonrpc": "2.0", "id": mid, "error": body}


def _mismatch(mid: Any, header: str, body: str) -> RpcOutcome:
    message = f"Bad Request: the request headers and body disagree: {body}"
    return RpcOutcome(_rpc_error(mid, HEADER_MISMATCH, message, {"mismatch": {"header": header, "body": body}}), 400)


def _header_checks(mid: Any, method: str, params: dict[str, Any], declared: object, hdr: dict[str, str]) -> RpcOutcome | None:
    version = hdr.get("mcp-protocol-version")
    if version is None:
        return _mismatch(
            mid, "(missing)", f"the body envelope names protocol version {declared} but the required MCP-Protocol-Version header is absent"
        )
    if hdr.get("mcp-method") is None:
        return _mismatch(mid, "(missing)", f"the body names method {method} but the required Mcp-Method header is absent")
    name = params.get("name")
    if method != "tools/call" or not isinstance(name, str):
        return None
    sent = hdr.get("mcp-name")
    if sent is None:
        return _mismatch(mid, "(missing)", f'the body carries params.name="{name}" but the required Mcp-Name header is absent')
    decoded = decode_header_value(sent)
    if decoded is None:
        return _mismatch(mid, sent, "the Mcp-Name header carries an invalid Base64 sentinel value")
    if decoded != name:
        return _mismatch(mid, sent, f'the body carries params.name="{name}" but the Mcp-Name header names "{decoded}"')
    return None


def handle_rpc(msg: Any, runners: Mapping[str, Runner] = RUNNERS, headers: Mapping[str, str] | None = None) -> RpcOutcome:
    """Answer one JSON-RPC message. ``headers`` are the HTTP request headers, or None on stdio (no header rules there)."""
    if isinstance(msg, list):
        return RpcOutcome(_rpc_error(None, -32600, "Bad Request: JSON-RPC batches are not supported by this endpoint"), 400)
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return RpcOutcome(_rpc_error(None, -32600, "Invalid Request"), 400)
    method: str = msg["method"]
    mid = msg.get("id")
    notification = "id" not in msg
    raw_params = msg.get("params")
    params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
    raw_meta = params.get("_meta")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    stateless = META_VERSION_KEY in meta
    declared = meta.get(META_VERSION_KEY)
    hdr = None if headers is None else {k.lower(): v.strip(" \t") for k, v in headers.items()}
    header_version = None if hdr is None else hdr.get("mcp-protocol-version")
    if stateless:
        if hdr is not None and not notification:
            if header_version is not None and header_version != declared:
                body = f"the body envelope names protocol version {declared} but the MCP-Protocol-Version header names {header_version}"
                return _mismatch(mid, header_version, body)
            named = hdr.get("mcp-method")
            if named is not None and named != method:
                return _mismatch(mid, named, f"the body names method {method} but the Mcp-Method header names {named}")
        if declared != STATELESS_PROTOCOL:
            requested = declared if isinstance(declared, str) else "unknown"
            data = {"supported": list(SUPPORTED_PROTOCOLS), "requested": requested}
            return RpcOutcome(_rpc_error(mid, UNSUPPORTED_PROTOCOL_VERSION, f"Unsupported protocol version: {requested}", data), 400)
        if hdr is not None and not notification:
            refused = _header_checks(mid, method, params, declared, hdr)
            if refused is not None:
                return refused
    elif hdr is not None and header_version is not None and not notification:
        if header_version == STATELESS_PROTOCOL:
            if method == "initialize":
                return _mismatch(
                    mid, header_version, "an initialize request (legacy handshake) was sent with a modern MCP-Protocol-Version header"
                )
            missing = "_meta" if raw_meta is None else META_VERSION_KEY
            message = (
                f"Invalid params: the MCP-Protocol-Version header names protocol revision {header_version}, "
                f"but the request is missing the required per-request envelope key(s): {missing}"
            )
            return RpcOutcome(_rpc_error(mid, -32602, message, {"envelope": {"missing": [missing]}}), 400)
        if header_version not in LEGACY_PROTOCOLS:
            message = f"Bad Request: Unsupported protocol version: {header_version} (supported versions: {', '.join(SUPPORTED_PROTOCOLS)})"
            return RpcOutcome(_rpc_error(mid, -32000, message), 400)

    def ok(result: dict[str, Any]) -> RpcOutcome:
        return RpcOutcome({"jsonrpc": "2.0", "id": mid, "result": {"resultType": "complete", **result} if stateless else result}, 200)

    if method == "server/discover":
        return RpcOutcome(
            {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "resultType": "complete",
                    "supportedVersions": list(SUPPORTED_PROTOCOLS),
                    "capabilities": {"tools": {"listChanged": False}},
                    "_meta": {SERVER_INFO_KEY: dict(SERVER_INFO)},
                    "ttlMs": TOOLS_TTL_MS,
                    "cacheScope": "public",
                },
            },
            200,
        )
    if method == "initialize" and not stateless:
        asked = params.get("protocolVersion")
        version = asked if asked in LEGACY_PROTOCOLS else LEGACY_PROTOCOLS[0]
        return ok({"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": dict(SERVER_INFO)})
    if notification:
        return RpcOutcome(None, 202)
    if method == "ping":
        return ok({})
    if method == "tools/list":
        listing: dict[str, Any] = {"tools": TOOL_DEFINITIONS}
        if stateless:
            listing.update({"ttlMs": TOOLS_TTL_MS, "cacheScope": "public"})
        return ok(listing)
    if method == "tools/call":
        name = params.get("name")
        runner = runners.get(name) if isinstance(name, str) else None
        if runner is None:
            return RpcOutcome(_rpc_error(mid, -32602, f"Unknown tool: {name!r}"), 200)
        try:
            envelope = runner(params.get("arguments") or {})
        except InputError as exc:
            return ok({"content": [{"type": "text", "text": json.dumps(exc.to_envelope(uuid.uuid4().hex))}], "isError": True})
        except Exception as exc:  # a tool must never take the server down; only the class name leaves
            return ok(
                {"content": [{"type": "text", "text": json.dumps(_error_body("INTERNAL_ERROR", type(exc).__name__))}], "isError": True}
            )
        return ok({"content": [{"type": "text", "text": json.dumps(envelope)}], "structuredContent": envelope, "isError": False})
    return RpcOutcome(_rpc_error(mid, -32601, f"Method not found: {method}"), 404 if stateless else 200)


def serve_stdio(runners: Mapping[str, Runner] = RUNNERS, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> None:
    import sys

    source = stdin or sys.stdin
    sink = stdout or sys.stdout
    for line in source:
        text = line.strip()
        if not text:
            continue
        try:
            msg = json.loads(text)
        except ValueError:
            reply: dict[str, Any] | None = _rpc_error(None, -32700, "Parse error")
        else:
            reply = handle_rpc(msg, runners, None).reply
        if reply is not None:
            sink.write(json.dumps(reply) + "\n")
            sink.flush()


class _RateLimiter:
    """A sliding one-minute window per client address (the MCP tools specification asks servers to rate limit tool calls)."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute, self.clock = per_minute, clock
        self.hits: dict[str, deque[float]] = {}
        self.lock = threading.Lock()

    def allow(self, who: str) -> bool:
        now = self.clock()
        with self.lock:
            q = self.hits.setdefault(who, deque())
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True


def _origin_allowed(origin: str | None, host_header: str | None) -> bool:
    """Refuse browser requests from foreign origins (DNS rebinding). Clients that are not browsers send no Origin."""
    if not origin:
        return True
    try:
        host = urlparse(origin).hostname or ""
    except ValueError:
        return False
    return host in ("localhost", "127.0.0.1", "::1") and (host_header or "").rsplit(":", 1)[0] in ("localhost", "127.0.0.1", "[::1]")


def make_http_handler(
    runners: Mapping[str, Runner] = RUNNERS, *, rate_limit_per_minute: int = RATE_LIMIT_PER_MINUTE
) -> type[BaseHTTPRequestHandler]:
    limiter = _RateLimiter(rate_limit_per_minute)
    slots = threading.BoundedSemaphore(MAX_CONCURRENT)

    class Handler(BaseHTTPRequestHandler):
        server_version = "address-flag-check"
        timeout = 10  # seconds a connection may stall: a stalled one gives its slot back instead of holding it for good

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send(self, status: int, body: Any, extra: dict[str, str] | None = None) -> None:
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def _read_json(self) -> tuple[bool, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length > MAX_BODY:
                remaining = min(length, MAX_DRAIN)  # drain a bounded amount so the client reads the 413 instead of a reset
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                self.close_connection = True
                self._send(413, _error_body("INVALID_BODY", f"body must be at most {MAX_BODY} bytes"))
                return False, None
            if length < 0:
                self._send(400, _error_body("INVALID_BODY", "Content-Length is not a number"))
                return False, None
            try:
                return True, json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, RecursionError):  # not JSON, or nested past what the parser reads
                self._send(400, _error_body("INVALID_JSON", "body is not valid JSON"))
                return False, None

        def _gate(self) -> bool:
            if not _origin_allowed(self.headers.get("Origin"), self.headers.get("Host")):
                self._send(403, _error_body("FORBIDDEN_ORIGIN", "requests from this origin are not allowed"))
                return False
            return True

        def _not_allowed(self) -> None:
            self._send(405, _rpc_error(None, -32000, "Method not allowed."), {"Allow": "POST"})

        def do_GET(self) -> None:
            if not self._gate():
                return
            if self.path == "/health":
                health = {"status": "ok", "protocol_version": LEGACY_PROTOCOLS[-1], "supported_versions": list(SUPPORTED_PROTOCOLS)}
                self._send(200, {**health, "server": SERVER_INFO["name"], "tools": len(TOOL_DEFINITIONS)})
            elif self.path == "/tools":
                self._send(200, TOOL_DEFINITIONS)
            elif self.path in ("/api/skills", "/api/skills/"):
                self._send(200, platform_skills.listing(TOOL_DEFINITIONS))
            elif self.path.startswith("/api/skills/") and self.path.count("/") == 3:
                wanted = [t for t in TOOL_DEFINITIONS if t["name"] == self.path.rsplit("/", 1)[1]]
                if wanted:
                    self._send(200, platform_skills.definition(wanted[0]))
                else:
                    self._send(404, _error_body("NOT_FOUND", f"no skill {self.path.rsplit('/', 1)[1]}"))
            elif self.path == "/":
                self._not_allowed()
            else:
                self._send(404, _error_body("NOT_FOUND", f"no route {self.path}"))

        def do_DELETE(self) -> None:
            if not self._gate():
                return
            if self.path == "/":
                self._not_allowed()
            else:
                self._send(404, _error_body("NOT_FOUND", f"no route {self.path}"))

        def do_POST(self) -> None:
            if not self._gate():
                return
            is_call = (
                self.path == "/"
                or (self.path.startswith("/tools/") and self.path.endswith("/call"))
                or (self.path.startswith("/api/skills/") and self.path.endswith("/invoke"))
            )
            if is_call and not limiter.allow(self.client_address[0]):
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
            parts = self.path.split("/")
            if len(parts) == 4 and parts[1] == "tools" and parts[3] == "call":
                name = parts[2]
                runner = runners.get(name)
                if runner is None:
                    self._send(404, _error_body("NOT_FOUND", f"Unknown tool '{name}'."))
                    return
                good, body = self._read_json()
                if not good:
                    return
                started = time.monotonic()
                try:
                    envelope = runner(body)
                except InputError as exc:
                    self._send(400, exc.to_envelope(uuid.uuid4().hex))
                    return
                except Exception as exc:  # the class name only
                    self._send(500, _error_body("INTERNAL_ERROR", type(exc).__name__))
                    return
                self._send(200, {"tool": name, "result": envelope, "latency_ms": int((time.monotonic() - started) * 1000)})
            elif len(parts) == 5 and parts[1:3] == ["api", "skills"] and parts[4] == "invoke":
                good, body = self._read_json()
                if not good:
                    return
                status, payload = platform_skills.invoke(parts[3], body, runners)
                self._send(status, payload)
            elif self.path == "/":
                good, msg = self._read_json()
                if not good:
                    return
                outcome = handle_rpc(msg, runners, dict(self.headers.items()))
                if outcome.reply is None:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self._send(outcome.http_status, outcome.reply)
            else:
                self._send(404, _error_body("NOT_FOUND", f"no route {self.path}"))

    return Handler


def serve_http(host: str = "127.0.0.1", port: int = 8766, runners: Mapping[str, Runner] = RUNNERS, **kwargs: Any) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_http_handler(runners, **kwargs))
