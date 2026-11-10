"""Checks of ``address_flag_check.list_store``, runnable against a mutant: the local copy of OFAC's list, its confirmation by a one-byte Range
request at most once an hour, the download through exactly one redirect to the S3 GovCloud host, the refusals, and the atomic store.

The replies imitate the recorded OFAC exchange (``tests/fixtures/ofac/recorded_file.json``: a 302 to a pre-signed S3 link, then 206 or 200 with
Last-Modified and ETag); the ZIP served holds the recorded trimmed extract. Expected values are written by hand.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import io
import json
import os
import pathlib
import re
import tempfile
import types
import zipfile
from typing import Any

from address_flag_check.transport import Caller, HttpReply
from tests.checks_support import Case, extract_xml

URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.ZIP"
S3 = "https://wc2h-sls-prod-public-published.s3.us-gov-west-1.amazonaws.com/Published/abc/SDN_ADVANCED.ZIP?X-Amz-Signature=SECRETSIG"
SHOWN = "https://wc2h-sls-prod-public-published.s3.us-gov-west-1.amazonaws.com/<signed link>"
NOW = dt.datetime(2026, 11, 1, 8, 20, 29, tzinfo=dt.UTC)
LM = "Wed, 28 Oct 2026 14:03:09 GMT"
ETAG = '"7417b80d9c4142f5a8624c6ad06c5f3f"'


def _zip(data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo("SDN_ADVANCED.XML", (2026, 1, 1, 0, 0, 0)), data)
    return buf.getvalue()


ZIP = _zip(extract_xml())


class Clock:
    def __init__(self) -> None:
        self.t = 50.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class Router:
    """Answers by URL and Range header; ``routes`` maps (url, range) to a reply, a list of replies, or an exception."""

    live = False

    def __init__(self, clock: Clock, routes: dict[tuple[str, str | None], Any], seconds: float = 0.0) -> None:
        self.clock, self.routes, self.seconds = clock, routes, seconds
        self.calls: list[tuple[str, str | None]] = []
        self.sent: list[tuple[str, float]] = []  # (method, timeout) of each call

    def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> HttpReply:
        key = (url, headers.get("Range"))
        self.calls.append(key)
        self.sent.append((method, timeout))
        self.clock.t += self.seconds
        reply = self.routes.get(key)
        if isinstance(reply, list):
            reply = reply.pop(0) if len(reply) > 1 else reply[0]
        if isinstance(reply, BaseException):
            raise reply
        if reply is None:
            return HttpReply(404, {}, b"no route")
        assert isinstance(reply, HttpReply)
        return reply


def _caller(router: Router, clock: Clock, now: dt.datetime = NOW, budget: float = 25.0) -> Caller:
    return Caller(router, now=now, deadline=clock() + budget, clock=clock, sleep=clock.sleep, jitter=lambda cap: cap, cooldowns={})


def _redirect() -> HttpReply:
    return HttpReply(302, {"location": S3}, b"")


def _range(etag: str = ETAG, total: int = len(ZIP), status: int = 206) -> HttpReply:
    return HttpReply(status, {"content-range": f"bytes 0-0/{total}", "last-modified": LM, "etag": etag}, ZIP[:1])


def _full(data: bytes = ZIP, status: int = 200) -> HttpReply:
    return HttpReply(status, {"last-modified": LM, "etag": ETAG, "content-length": str(len(data))}, data)


def _iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def check_paths(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    d = mod.default_dir
    c.eq("an explicit home", d({"ADDRESS_FLAG_CHECK_HOME": "/x/y", "XDG_CACHE_HOME": "/c", "HOME": "/h"}), pathlib.Path("/x/y"))
    c.eq("the XDG cache", d({"XDG_CACHE_HOME": "/c", "HOME": "/h"}), pathlib.Path("/c/address-flag-check"))
    c.eq("the home cache", d({"HOME": "/h"}), pathlib.Path("/h/.cache/address-flag-check"))
    c.eq(
        "an empty setting is ignored",
        d({"ADDRESS_FLAG_CHECK_HOME": "", "XDG_CACHE_HOME": "", "HOME": "/h"}),
        pathlib.Path("/h/.cache/address-flag-check"),
    )
    c.eq("the store file name", mod.STORE_FILE, "ofac_sdn_index.json")
    c.eq("confirmation interval", mod.CONFIRM_EVERY_S, 3600)
    t = mod.content_range_total
    c.eq("Content-Range total", t("bytes 0-0/5741026"), 5741026)
    c.eq("Content-Range with an unknown total", t("bytes 0-0/*"), None)
    c.eq("no Content-Range", t(None), None)
    c.eq("garbage Content-Range", t("pages 1"), None)
    return c.bad


def check_download_and_confirm(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    with tempfile.TemporaryDirectory() as tmp:
        home = pathlib.Path(tmp) / "store"
        clock = Clock()
        router = Router(clock, {(URL, None): _redirect(), (S3, None): _full()})
        caller = _caller(router, clock)
        state = c.run("first download", lambda: mod.ensure(home, caller, NOW))
        if state is None:
            return c.bad
        c.eq("action", state.action, "downloaded")
        c.eq("no error", state.error, None)
        s = state.stored
        c.eq("date of issue", None if s is None else s.index.date_of_issue, "2026-10-28")
        c.eq("ZIP hash", None if s is None else s.zip_sha256, hashlib.sha256(ZIP).hexdigest())
        c.eq("ZIP size", None if s is None else s.zip_bytes, len(ZIP))
        c.eq("validators", None if s is None else (s.last_modified, s.etag), (LM, ETAG))
        c.eq("times", None if s is None else (s.downloaded_at, s.confirmed_at), (_iso(NOW), _iso(NOW)))
        c.eq("the two hops", router.calls, [(URL, None), (S3, None)])
        c.eq("the record shows the S3 host without its signed link", [r.url for r in caller.records], [URL, SHOWN])
        c.eq("both hops are GET requests of the ofac_sdn source", [(r.method, r.source) for r in caller.records], [("GET", "ofac_sdn")] * 2)
        c.eq("a download may take the whole 25 s budget (its own limit is 60 s)", router.sent, [("GET", 25.0)] * 2)
        text = (home / mod.STORE_FILE).read_text()
        c.true("the signature is never stored", "SECRETSIG" not in text and all("SECRETSIG" not in r.url for r in caller.records))
        c.eq("only the store file is left", sorted(p.name for p in home.iterdir()), [mod.STORE_FILE])
        c.eq("the stored copy reads back", mod.load(home), s)
        c.eq("the stored file names its source", json.loads(text)["source_url"], URL)
        c.eq("the stored file is format version 1", json.loads(text)["format_version"], 1)
        if s is not None:
            c.raises("a stored list cannot be changed", lambda: setattr(s, "etag", "x"), dataclasses.FrozenInstanceError)
            c.raises("a list state cannot be changed", lambda: setattr(state, "action", "x"), dataclasses.FrozenInstanceError)

        clock = Clock()
        router = Router(clock, {})
        state = c.run("at once", lambda: mod.ensure(home, _caller(router, clock, NOW), NOW))
        c.eq("confirmed 0 s ago is recent: nothing is fetched", (None if state is None else state.action, router.calls), ("recent", []))

        clock = Clock()
        router = Router(clock, {})
        later = NOW + dt.timedelta(minutes=59)
        state = c.run("within the hour", lambda: mod.ensure(home, _caller(router, clock, later), later))
        c.eq("within the hour nothing is fetched", (None if state is None else state.action, router.calls), ("recent", []))

        clock = Clock()
        router = Router(clock, {(URL, "bytes=0-0"): _redirect(), (S3, "bytes=0-0"): _range()})
        later = NOW + dt.timedelta(hours=2)
        state = c.run("after two hours", lambda: mod.ensure(home, _caller(router, clock, later), later))
        c.eq("unchanged file: confirmed by a one-byte request", None if state is None else state.action, "confirmed")
        c.eq("the Range request went through the redirect", router.calls, [(URL, "bytes=0-0"), (S3, "bytes=0-0")])
        c.eq("a one-byte confirmation gets 10 s per hop", router.sent, [("GET", 10.0)] * 2)
        c.eq(
            "confirmation time moves, download time does not",
            None if state is None or state.stored is None else (state.stored.downloaded_at, state.stored.confirmed_at),
            (_iso(NOW), _iso(later)),
        )
        c.eq("the confirmation is saved", None if (x := mod.load(home)) is None else x.confirmed_at, _iso(later))

        for hours, (label, reply) in zip(
            (3, 5, 7),
            (
                ("a new ETag", _range(etag='"other"')),
                ("a new size", _range(total=len(ZIP) + 1)),
                (
                    "a new Last-Modified",
                    HttpReply(
                        206,
                        {"content-range": f"bytes 0-0/{len(ZIP)}", "last-modified": "Thu, 29 Oct 2026 10:00:00 GMT", "etag": ETAG},
                        b"P",
                    ),
                ),
            ),
            strict=True,
        ):
            clock = Clock()
            router = Router(
                clock, {(URL, "bytes=0-0"): _redirect(), (S3, "bytes=0-0"): reply, (URL, None): _redirect(), (S3, None): _full()}
            )
            later = NOW + dt.timedelta(hours=hours)
            state = c.run(label, lambda router=router, clock=clock, later=later: mod.ensure(home, _caller(router, clock, later), later))
            c.eq(f"{label}: downloaded again", None if state is None else state.action, "downloaded")
            c.eq(f"{label}: Range, then the full file", [x[1] for x in router.calls], ["bytes=0-0", "bytes=0-0", None, None])

        clock = Clock()
        router = Router(clock, {(URL, "bytes=0-0"): _redirect(), (S3, "bytes=0-0"): _full()})
        later = NOW + dt.timedelta(hours=9)
        state = c.run("a server that ignores Range", lambda: mod.ensure(home, _caller(router, clock, later), later))
        c.eq(
            "a full reply to the Range request is used as the download",
            None if state is None else (state.action, len(router.calls)),
            ("downloaded", 2),
        )

        clock = Clock()
        router = Router(clock, {(URL, None): _redirect(), (S3, None): _full()})
        state = c.run("forced", lambda: mod.ensure(home, _caller(router, clock), NOW, force=True))
        c.eq("a forced refresh downloads even when recent", None if state is None else state.action, "downloaded")

        clock = Clock()
        router = Router(
            clock,
            {
                (URL, "bytes=0-0"): _redirect(),
                (S3, "bytes=0-0"): HttpReply(307, {"location": S3}, b""),
                (URL, None): _redirect(),
                (S3, None): _full(),
            },
        )
        later = NOW + dt.timedelta(hours=2)
        state = c.run("two redirects on a confirmation", lambda: mod.ensure(home, _caller(router, clock, later), later))
        c.eq(
            "two redirects on a confirmation keep the copy, with no download",
            None if state is None else (state.action, None if state.error is None else state.error.reason, len(router.calls)),
            ("kept", "bad_response", 2),
        )
    # Every redirect status is followed once: 301, 302, 303, 307 and 308
    for status in (301, 302, 303, 307, 308):
        with tempfile.TemporaryDirectory() as tmp:
            clock = Clock()
            router = Router(clock, {(URL, None): HttpReply(status, {"location": S3}, b""), (S3, None): _full()})
            store = pathlib.Path(tmp) / "a" / "b"  # two folders that do not exist yet
            state = c.run(
                f"a {status} redirect", lambda router=router, clock=clock, store=store: mod.ensure(store, _caller(router, clock), NOW)
            )
            c.eq(f"a {status} redirect is followed", None if state is None else (state.action, len(router.calls)), ("downloaded", 2))
    return c.bad


def check_store(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    with tempfile.TemporaryDirectory() as tmp:
        home = pathlib.Path(tmp)
        clock = Clock()
        state = c.run("seed", lambda: mod.ensure(home, _caller(Router(clock, {(URL, None): _redirect(), (S3, None): _full()}), clock), NOW))
        stored = None if state is None else state.stored
        if stored is None:
            return [*c.bad, "seed: no stored copy"]
        good = json.loads((home / mod.STORE_FILE).read_text())
        for field in ("confirmed_at", "downloaded_at"):
            (home / mod.STORE_FILE).write_text(json.dumps({**good, field: "yesterday"}))
            c.eq(f"a store whose {field} is not a time is no copy", mod.load(home), None)
        (home / mod.STORE_FILE).write_text(json.dumps(good))
        # A failed write leaves the old store and no temporary file, and the error is not swallowed
        c.raises("a value JSON cannot hold", lambda: mod.save(home, dataclasses.replace(stored, zip_bytes=object())), TypeError)
        c.eq("after a failed write only the old store is left", sorted(p.name for p in home.iterdir()), [mod.STORE_FILE])
        c.eq("and it is unchanged", mod.load(home), stored)
        # A rename that is done but reports an error (as can happen on a network file system): the error is raised as itself, and the
        # temporary file was a hidden .json file in the store folder, gone once the store is in place
        moved: list[pathlib.Path] = []

        def replace(src: str, dst: object) -> None:
            moved.append(pathlib.Path(src))
            os.replace(src, dst)  # type: ignore[arg-type]
            raise OSError("the rename reported an error after it was done")

        real_os = mod.os
        mod.os = types.SimpleNamespace(fdopen=os.fdopen, replace=replace)
        try:
            c.raises(
                "a rename that reports an error", lambda: mod.save(home, stored), OSError, "the rename reported an error after it was done"
            )
        finally:
            mod.os = real_os
        c.eq(
            "the temporary file's name and folder",
            [(p.parent == home, bool(re.fullmatch(r"\.tmp-[a-z0-9_]+\.json", p.name))) for p in moved],
            [(True, True)],
        )
        c.eq(
            "the store is in place, and nothing beside it",
            (mod.load(home), sorted(p.name for p in home.iterdir())),
            (stored, [mod.STORE_FILE]),
        )
        # A download that leaves exactly the minimum time (0.2 s, the transport's MIN_LEFT_S, plus its 1e-9 tolerance) when it is read is a
        # spent budget. The times are chosen so that the float arithmetic is exact: 2x - x == x.
        x = 0.2 + 1e-9
        clock = Clock()
        clock.t = 0.0
        router = Router(clock, {(URL, None): _redirect(), (S3, None): _full()}, seconds=x / 2)
        caller = Caller(router, now=NOW, deadline=2 * x, clock=clock, sleep=clock.sleep, jitter=lambda cap: cap, cooldowns={})
        state = c.run("exactly the minimum time left", lambda: mod.ensure(home / "edge", caller, NOW))
        c.eq(
            "exactly the minimum time left to read is a spent budget",
            None if state is None or state.error is None else (state.error.reason, caller.remaining() == x),
            ("budget_exhausted", True),
        )
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    with tempfile.TemporaryDirectory() as tmp:
        home = pathlib.Path(tmp)
        clock = Clock()
        router = Router(clock, {})
        state = c.run("no copy, no refresh", lambda: mod.ensure(home, _caller(router, clock), NOW, refresh=False))
        c.eq(
            "no copy and no refresh",
            None if state is None else (state.action, state.stored, None if state.error is None else state.error.reason),
            ("none", None, "list_not_loaded"),
        )
        c.eq("nothing was sent", router.calls, [])
        for label, routes, reason, calls in (
            (
                "a redirect to another host",
                {(URL, None): HttpReply(302, {"location": "https://evil.example/SDN_ADVANCED.ZIP"}, b"")},
                "bad_response",
                [(URL, None)],
            ),
            (
                "a redirect to the host over plain HTTP",
                {(URL, None): HttpReply(302, {"location": "http://x.s3.us-gov-west-1.amazonaws.com/f"}, b"")},
                "bad_response",
                [(URL, None)],
            ),
            (
                "a host that only ends like the allowed one",
                {(URL, None): HttpReply(302, {"location": "https://evils3.us-gov-west-1.amazonaws.com/f"}, b"")},
                "bad_response",
                [(URL, None)],
            ),
            ("a redirect without a location", {(URL, None): HttpReply(302, {}, b"")}, "bad_response", [(URL, None)]),
            (
                "two redirects",
                {(URL, None): _redirect(), (S3, None): HttpReply(307, {"location": S3}, b"")},
                "bad_response",
                [(URL, None), (S3, None)],
            ),
            ("no redirect at all", {(URL, None): _full()}, None, [(URL, None)]),
            (
                "a reply that is not a ZIP",
                {(URL, None): _redirect(), (S3, None): _full(b"<html>maintenance</html>")},
                "bad_response",
                [(URL, None), (S3, None)],
            ),
            (
                "a partial reply to a full request",
                {(URL, None): _redirect(), (S3, None): _full(status=206)},
                "bad_response",
                [(URL, None), (S3, None)],
            ),
            (
                "a ZIP of something else",
                {(URL, None): _redirect(), (S3, None): _full(_zip(b"<x/>"))},
                "bad_response",
                [(URL, None), (S3, None)],
            ),
            ("the list service is down", {(URL, None): OSError("down")}, "network_error", [(URL, None)] * 3),
        ):
            clock = Clock()
            router = Router(clock, routes)
            store = home / label.replace(" ", "_")
            state = c.run(label, lambda router=router, clock=clock, store=store: mod.ensure(store, _caller(router, clock), NOW))
            if reason is None:
                c.eq(f"{label}: a direct answer is accepted", None if state is None else state.action, "downloaded")
                continue
            c.eq(f"{label}: refused", None if state is None or state.error is None else state.error.reason, reason)
            c.eq(
                f"{label}: nothing stored",
                None if state is None else (state.action, state.stored, store.exists() and any(store.iterdir())),
                ("none", None, False),
            )
            c.eq(f"{label}: requests sent", router.calls, calls)
        clock = Clock()
        store = home / "kept"
        c.run("seed", lambda: mod.ensure(store, _caller(Router(clock, {(URL, None): _redirect(), (S3, None): _full()}), clock), NOW))
        before = mod.load(store)
        later = NOW + dt.timedelta(hours=5)
        router = Router(clock, {(URL, "bytes=0-0"): OSError("down")})
        state = c.run("a failed confirmation", lambda: mod.ensure(store, _caller(router, clock, later), later))
        c.eq(
            "the old copy is kept with its old confirmation",
            None if state is None else (state.action, state.stored, None if state.error is None else state.error.reason),
            ("kept", before, "network_error"),
        )
        c.eq("and the store is unchanged", mod.load(store), before)
        router = Router(clock, {})
        state = c.run("no refresh with a copy", lambda: mod.ensure(store, _caller(router, clock, later), later, refresh=False))
        c.eq("no refresh keeps the copy without an error", None if state is None else (state.action, state.error), ("kept", None))
        c.eq("a refresh-free call sends nothing", router.calls, [])
        earlier = NOW - dt.timedelta(minutes=5)
        router = Router(clock, {(URL, "bytes=0-0"): _redirect(), (S3, "bytes=0-0"): _range()})
        state = c.run("a clock behind the confirmation", lambda: mod.ensure(store, _caller(router, clock, earlier), earlier))
        c.eq("a confirmation in the future is not trusted as recent", None if state is None else state.action, "confirmed")
        clock = Clock()
        router = Router(clock, {(URL, None): _redirect(), (S3, None): _full()}, seconds=12.4)
        state = c.run("no time left to read", lambda: mod.ensure(home / "slow", _caller(router, clock, budget=25.0), NOW))
        c.eq(
            "a download that leaves no time to read is a spent budget",
            None if state is None or state.error is None else state.error.reason,
            "budget_exhausted",
        )
        (home / "bad").mkdir()
        (home / "bad" / mod.STORE_FILE).write_text("{not json")
        c.eq("a damaged store is no copy", mod.load(home / "bad"), None)
        (home / "bad" / mod.STORE_FILE).write_text(json.dumps({"format_version": 9}))
        c.eq("another store version is no copy", mod.load(home / "bad"), None)
        c.eq("a missing store is no copy", mod.load(home / "missing"), None)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    for check in (check_paths, check_download_and_confirm, check_refusals, check_store):
        try:
            out.extend(check(mod))
        except Exception as exc:
            out.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    return out
