"""The site and one live thing: paste a claim, get it read.

The interface is static documents; this serves them (``hoshitori-league serve``) and answers one request the documents cannot: ``POST
/api/claim`` with ``{"text": "..."}`` reads the claim as ``claim_extract`` reads the examples of the Claims page and returns the same
record (``claims_file.record``), which the page already knows how to show. Nothing is stored: the text goes to the model provider and the
answer back to the person, and the server keeps neither (the private-by-default rule of the contract, section 36). The request is bounded
before any model is asked: at most 600 characters, five claims an hour for one address, a budget for the whole day and two readings at a
time; a refusal says which limit and when to ask again. The server holds no key in a page or a log: the key is read from the environment
by the model gateway, as everywhere else.

The static files are sent with the security headers the host must send (``deploy/security_headers.json``), so a person who runs it locally
sees the same page the page test ran under.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import parse_qs, urlsplit

from hoshitori import challenge_store, claim_extract, claims_file, model_client, model_gateway, safe_fetch
from hoshitori.capture import CaptureStore
from hoshitori.clock import SystemClock
from hoshitori.gateway import DispatchJournal, Gateway
from hoshitori.runner import Runner, ThreadExecutor

MAX_CHARS: Final = 600
PER_CLIENT: Final = 5  # claims an hour for one address
PER_DAY: Final = 120  # claims a day for the whole server (a prior: each is three model calls)
HOUR_S: Final = 3600.0
DAY_S: Final = 86400.0
BODY_LIMIT: Final = 4096
REQUEST_TIMEOUT_S: Final = 15.0  # a connection that sends nothing for this long is dropped, so open sockets cannot use up every thread
MAX_CLIENTS: Final = 10_000  # addresses counted at once; the table is cleared of the forgotten ones and never grows past this
LIVE_MARK: Final = "hoshitori-live"  # the name of the meta mark `serve` adds to the page it serves
CONCURRENT: Final = 2
CHALLENGE_PER_CLIENT: Final = 10  # challenges an hour from one address
CHALLENGE_PER_DAY: Final = 300  # a day, for the whole server
CHALLENGE_FETCHES: Final = 4  # sources being fetched at once
ASSETS: Final = ("BTC", "ETH", "SOL")


class Reader(Protocol):
    def read(self, text: str) -> dict[str, Any]: ...


class ModelReader:
    """Reads a claim with the model, as ``scripts/make_claims.py`` does for its examples."""

    def __init__(self, client: model_client.ModelClient, model: str, assets: tuple[str, ...] = ASSETS) -> None:
        self._client = client
        self._config = claim_extract.Config(model, max_tokens=2500, timeout_s=120)
        self._model = model
        self._assets = assets

    def read(self, text: str) -> dict[str, Any]:
        reading = claim_extract.read_claim(self._client, text, self._config, self._assets)
        at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        ident = "live-" + hashlib.sha256(text.encode()).hexdigest()[:16]
        return claims_file.record(ident, text, reading, self._model, at, self._assets)


@contextmanager
def open_reader(base_url: str, key: Callable[[], str], model: str) -> Iterator[ModelReader]:
    """A model reader on its own gateway, with no ledger: nothing it asks is recorded beyond its own captures, which are temporary."""
    import shutil
    import tempfile

    directory = Path(tempfile.mkdtemp(prefix="hoshitori-serve-"))
    clock = SystemClock()
    captures = CaptureStore(directory / "captures")
    gateway = Gateway(captures, DispatchJournal(directory / "dispatch.jsonl"))
    attached = model_gateway.attach_model(gateway, model_gateway.Endpoint("claims", base_url), key, clock)
    pool = ThreadExecutor(CONCURRENT)
    runner = Runner(gateway, {attached.adapter.provider: attached.transport}, clock, pool)
    loop = threading.Thread(target=runner.run_forever, daemon=True)
    loop.start()
    try:
        yield ModelReader(model_client.ModelClient(model_client.GatewayFetcher(runner, captures, clock, attached.adapter.provider)), model)
    finally:
        runner.stop()
        loop.join(timeout=3)
        pool.shutdown()
        captures.close()
        shutil.rmtree(directory, ignore_errors=True)  # the captures were temporary: nothing of a person's claim stays on the disk


class _Server(ThreadingHTTPServer):
    """The standard threaded server with a listen queue sized for a burst: the default of five makes a crowd of simultaneous clients wait
    for a TCP retry of seconds (measured: a worst case of 3.9 s at fifty simultaneous clients, with no failed request)."""

    request_queue_size = 256


class Limits:
    """How many claims one address and the whole server may ask for, counted over a sliding window."""

    def __init__(
        self,
        per_client: int = PER_CLIENT,
        per_day: int = PER_DAY,
        now: Callable[[], float] = time.monotonic,
        max_clients: int = MAX_CLIENTS,
    ) -> None:
        self._per_client, self._per_day, self._now, self._max_clients = per_client, per_day, now, max_clients
        self._clients: dict[str, list[float]] = {}
        self._day: list[float] = []
        self._lock = threading.Lock()

    def take(self, client: str) -> tuple[bool, str, int]:
        """Whether one more claim may be read, why not if it may not, and the seconds until it may."""
        now = self._now()
        with self._lock:
            self._day = [t for t in self._day if now - t < DAY_S]
            mine = [t for t in self._clients.get(client, []) if now - t < HOUR_S]
            if len(mine) >= self._per_client:
                return False, f"{self._per_client} claims an hour from one address", int(HOUR_S - (now - mine[0])) + 1
            if len(self._day) >= self._per_day:
                return False, "the day's budget of claims for this server is spent", int(DAY_S - (now - self._day[0])) + 1
            if client not in self._clients and len(self._clients) >= self._max_clients:
                self._clients = {
                    c: ts for c, ts in self._clients.items() if ts and now - ts[-1] < HOUR_S
                }  # forget the addresses of an hour ago
                if len(self._clients) >= self._max_clients:
                    oldest = min(ts[0] for ts in self._clients.values())
                    return False, "too many addresses are asking just now", int(HOUR_S - (now - oldest)) + 1
            mine.append(now)
            self._clients[client] = mine
            self._day.append(now)
            return True, "", 0


def _public(row: Mapping[str, Any]) -> dict[str, Any]:
    """What a visitor may see of a challenge: all of it that the file holds (it holds nothing about who filed it)."""
    return {k: row[k] for k in ("id", "received_at", "receipt", "kind", "url", "quote", "note", "state", "origin", "reason")}


def _security(root: Path) -> dict[str, str]:
    path = root / "deploy" / "security_headers.json"
    return {str(k): str(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()} if path.is_file() else {}


def make_server(
    site: Path,
    reader: Reader | None,
    address: tuple[str, int],
    limits: Limits | None = None,
    headers: Mapping[str, str] | None = None,
    challenges: challenge_store.ChallengeStore | None = None,
    fetch: Callable[[str], Any] | None = None,
    challenge_limits: Limits | None = None,
) -> ThreadingHTTPServer:
    """A server for ``site`` that also reads claims with ``reader`` (none: the endpoint says it is not available)."""
    limit = limits or Limits()
    challenge_limit = challenge_limits or Limits(CHALLENGE_PER_CLIENT, CHALLENGE_PER_DAY)
    fetching = threading.BoundedSemaphore(CHALLENGE_FETCHES)
    fetcher = fetch or (lambda url: safe_fetch.SafeFetcher().fetch(url))
    extra = dict(headers or {})
    busy = threading.BoundedSemaphore(CONCURRENT)

    class Handler(SimpleHTTPRequestHandler):
        timeout = REQUEST_TIMEOUT_S

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(site), **kwargs)

        def end_headers(self) -> None:
            for name, value in extra.items():
                self.send_header(name, value)
            super().end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _json(self, status: int, body: Mapping[str, Any], retry: int | None = None) -> None:
            data = (json.dumps(body, ensure_ascii=False, sort_keys=True) + "\n").encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if retry is not None:
                self.send_header("Retry-After", str(retry))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path == "/api/health":
                self._json(200, {"claims": reader is not None, "max_chars": MAX_CHARS, "challenges": challenges is not None})
                return
            if self.path.split("?")[0] == "/api/challenges":
                if challenges is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "this server keeps no challenges"})
                    return
                query = parse_qs(urlsplit(self.path).query).get("receipt", [""])[0]
                if challenge_store.RECEIPT.fullmatch(query) is None:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "ask for the challenges of one receipt: ?receipt=<its identifier>"})
                    return
                self._json(200, {"challenges": [_public(r) for r in challenges.for_receipt(query)]})
                return
            if self.path.split("?")[0] in ("/", "/index.html") and (site / "index.html").is_file():
                # The page learns that it is served with a live endpoint from this mark, so a static copy never probes for one.
                page = (
                    (site / "index.html")
                    .read_text(encoding="utf-8")
                    .replace("</head>", f'<meta name="{LIVE_MARK}" content="1"></head>', 1)
                    .encode()
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                return
            super().do_GET()

        def _body(self) -> tuple[bool, object]:
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                size = -1
            if size <= 0 or size > BODY_LIMIT:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "send a small JSON body"})
                return False, None
            try:
                loaded: object = json.loads(self.rfile.read(size).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "the body is JSON"})
                return False, None
            return True, loaded

        def _challenge(self) -> None:
            if challenges is None:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "this server keeps no challenges"})
                return
            ok_body, body = self._body()
            if not ok_body:
                return
            try:
                submission = challenge_store.read_submission(body)
            except challenge_store.ChallengeError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            ok, why, wait = challenge_limit.take(self.client_address[0])
            if not ok:
                self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": f"limit: {why}", "retry_after_s": wait}, retry=wait)
                return
            if not fetching.acquire(blocking=False):
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE, {"error": "sources are being checked for other people; try again in a minute"}, retry=60
                )
                return
            try:
                row, new = challenges.file(submission, fetcher)
            except challenge_store.ChallengeError as exc:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})
                return
            finally:
                fetching.release()
            self._json(200 if not new else 201, {"challenge": _public(row), "new": new})

        def do_POST(self) -> None:
            if self.path == "/api/challenge":
                self._challenge()
                return
            if self.path != "/api/claim":
                self._json(HTTPStatus.NOT_FOUND, {"error": "there is nothing here"})
                return
            if reader is None:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "this server was started without a model, so it cannot read claims"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                size = -1
            if size <= 0 or size > BODY_LIMIT:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "send a small JSON body with the claim's text"})
                return
            try:
                body = json.loads(self.rfile.read(size).decode("utf-8"))
                text = body["text"]
            except (ValueError, KeyError, TypeError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": 'the body is {"text": "the claim"}'})
                return
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_CHARS:
                self._json(HTTPStatus.BAD_REQUEST, {"error": f"the claim is text of 1 to {MAX_CHARS} characters"})
                return
            ok, why, wait = limit.take(self.client_address[0])
            if not ok:
                self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": f"limit: {why}", "retry_after_s": wait}, retry=wait)
                return
            if not busy.acquire(blocking=False):
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "two claims are being read; try again in a minute"}, retry=60)
                return
            try:
                record = reader.read(text.strip())
            except Exception:  # the model or its gateway failed: say so, never a reading that was not made
                self._json(HTTPStatus.BAD_GATEWAY, {"error": "the claim could not be read just now; nothing was recorded"})
                return
            finally:
                busy.release()
            self._json(200, record)

    return _Server(address, Handler)


def serve(
    site: Path, reader: Reader | None, host: str, port: int, root: Path, challenges: challenge_store.ChallengeStore | None = None
) -> None:
    server = make_server(site, reader, (host, port), headers=_security(root), challenges=challenges)
    try:
        server.serve_forever()
    finally:
        server.server_close()
