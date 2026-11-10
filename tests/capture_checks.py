"""Checks of the raw capture store, runnable against the real module or a mutant of it."""

from __future__ import annotations

import hashlib
import shutil
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hoshitori.timeutil import TimeError

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)


class Crash(Exception):
    pass


def make(mod: types.ModuleType, n: int, *, body: bytes | None = None, source: str = "ryo", status: int | None = 200, **kwargs: object):  # type: ignore[no-untyped-def]
    return mod.RawCapture(
        capture_id=kwargs.pop("capture_id", None) or mod.new_capture_id(),
        source=source,
        request=kwargs.pop("request", None) or {"method": "POST", "tool": "analyze_token", "arguments": {"symbol": f"S{n}"}},
        status=status,
        headers=kwargs.pop("headers", None) or {"content-type": "application/json", "x-trace-id": f"t{n}"},
        body=body if body is not None else f'{{"n":{n}}}'.encode(),
        t_send=T0 + timedelta(seconds=n),
        received_at=T0 + timedelta(seconds=n, milliseconds=250),
        trace_id=kwargs.pop("trace_id", f"t{n}"),
        **kwargs,
    )


def rejected(bad: list[str], label: str, fn, *errors: type[BaseException], why: str | None = None) -> None:  # type: ignore[no-untyped-def]
    try:
        fn()
    except errors as exc:
        if why is not None and why not in str(exc):
            bad.append(f"{label}: rejected for another reason ({exc})")
        return
    except Exception as exc:
        bad.append(f"{label}: raised {type(exc).__name__} instead of the expected error")
        return
    bad.append(f"{label}: accepted")


def failures(mod: types.ModuleType, tmp: Path) -> list[str]:
    bad: list[str] = []
    counter = iter(range(10_000))

    def fresh() -> Path:
        return tmp / f"store-{next(counter)}"

    # --- a round trip keeps every field and the exact bytes --------------------------------------------------------------
    root = fresh()
    odd_bodies = [b"", b"\x00\xff\xfe binary", "héllo ☃".encode(), b"x" * 1_000_000, b'{"n":1}\n', b"\r\n"]
    with mod.CaptureStore(root) as store:
        captures = [make(mod, i, body=b) for i, b in enumerate(odd_bodies)]
        for i, c in enumerate(captures):
            stored = store.append(c)
            if stored.seq != i or stored.capture_id != c.capture_id or stored.body_sha256 != hashlib.sha256(c.body).hexdigest():
                bad.append(f"stored reference of capture {i} is wrong")
        for c in captures:
            got = store.get(c.capture_id)
            if got != c:
                bad.append(f"capture {c.capture_id} does not round-trip")
        if len(store) != len(odd_bodies) or store.ids() != [c.capture_id for c in captures]:
            bad.append("the store's order or length is wrong")
    with mod.CaptureStore(root) as again:
        if again.ids() != [c.capture_id for c in captures] or again.get(captures[3].capture_id) != captures[3]:
            bad.append("captures do not survive a reopen")
        if again.recovery.truncated_bytes != 0 or again.recovery.captures != len(captures):
            bad.append("a clean store reports a repair")

    # --- every fetch is its own observation: identical bytes make two captures and one blob ---------------------------
    root = fresh()
    with mod.CaptureStore(root) as store:
        a, b = make(mod, 1, body=b"same bytes"), make(mod, 2, body=b"same bytes")
        sa, sb = store.append(a), store.append(b)
        if a.capture_id == b.capture_id or sa.body_sha256 != sb.body_sha256 or sa.seq == sb.seq or sa.line_sha256 == sb.line_sha256:
            bad.append("two fetches of identical bytes were merged or lost")
        if store.blob_count() != 1:
            bad.append("identical bytes are stored twice")
        if store.get(a.capture_id).t_send == store.get(b.capture_id).t_send:
            bad.append("the two observations lost their own times")

    # --- re-appending is idempotent; a different capture under the same id is a conflict -----------------------------
    root = fresh()
    with mod.CaptureStore(root) as store:
        c = make(mod, 1)
        first = store.append(c)
        if store.append(c) != first or len(store) != 1:
            bad.append("re-appending the same capture is not a no-op")
        other = make(mod, 1, capture_id=c.capture_id, body=b"different")
        rejected(bad, "same id, other body", lambda: store.append(other), mod.CaptureConflict)
        other_meta = make(mod, 2, capture_id=c.capture_id, body=c.body)
        rejected(bad, "same id, other metadata", lambda: store.append(other_meta), mod.CaptureConflict)
        if len(store) != 1:
            bad.append("a conflicting capture was stored")
    with mod.CaptureStore(root) as store:  # the idempotence also holds across a restart (a crashed caller re-delivers)
        if store.append(c).seq != 0 or len(store) != 1:
            bad.append("re-delivery after a restart duplicated the capture")

    # --- a torn tail at every cut is removed and the store carries on -----------------------------------------------------
    base = fresh()
    with mod.CaptureStore(base) as store:
        caps = [make(mod, i) for i in range(4)]
        for c in caps:
            store.append(c)
    log = (base / "captures.jsonl").read_bytes()
    last_start = log[:-1].rfind(b"\n") + 1
    cuts = sorted(
        {*range(last_start, last_start + 8), *range(len(log) - 8, len(log)), *range(last_start, len(log), 41)}
    )  # the ends and a sample
    for cut in cuts:
        copy = fresh()
        shutil.copytree(base, copy)
        (copy / "captures.jsonl").write_bytes(log[:cut])
        try:
            with mod.CaptureStore(copy) as store:
                if cut == last_start:
                    if len(store) != 3 or store.recovery.truncated_bytes != 0:
                        bad.append("a cut on a line boundary is not a clean shorter store")
                elif len(store) != 3 or store.recovery.truncated_bytes != cut - last_start:
                    bad.append(
                        f"torn tail at {cut - last_start} bytes: {len(store)} captures, {store.recovery.truncated_bytes} bytes removed"
                    )
                extra = make(mod, 9)
                store.append(extra)
            with mod.CaptureStore(copy) as store:
                if store.ids() != [c.capture_id for c in caps[:3]] + [extra.capture_id] or store.get(extra.capture_id) != extra:
                    bad.append(f"the store did not carry on after a torn tail at {cut - last_start}")
        except Exception as exc:
            bad.append(f"torn tail at {cut - last_start} bytes raised {type(exc).__name__}: {exc}")
            break

    # --- damage before the end is never repaired -------------------------------------------------------------------------
    def damaged(label: str, change) -> None:  # type: ignore[no-untyped-def]
        copy = fresh()
        shutil.copytree(base, copy)
        change(copy)
        rejected(bad, label, lambda: mod.CaptureStore(copy).close(), mod.CaptureDamage)

    def flip_in_line(n: int):  # type: ignore[no-untyped-def]
        def change(copy: Path) -> None:
            data = bytearray((copy / "captures.jsonl").read_bytes())
            starts = [0]
            for i, byte in enumerate(data):
                if byte == 0x0A and i + 1 < len(data):
                    starts.append(i + 1)
            data[starts[n] + 30] ^= 1
            (copy / "captures.jsonl").write_bytes(bytes(data))

        return change

    damaged("flip in the first line", flip_in_line(0))
    damaged("flip in a middle line", flip_in_line(2))
    damaged(
        "a line removed from the middle",
        lambda c: (c / "captures.jsonl").write_bytes(b"".join(log.splitlines(keepends=True)[:1] + log.splitlines(keepends=True)[2:])),
    )
    damaged(
        "two lines swapped",
        lambda c: (c / "captures.jsonl").write_bytes(b"".join([log.splitlines(keepends=True)[i] for i in (1, 0, 2, 3)])),
    )
    damaged(
        "a line repeated",
        lambda c: (c / "captures.jsonl").write_bytes(b"".join([log.splitlines(keepends=True)[i] for i in (0, 1, 1, 2, 3)])),
    )
    damaged("a blank line in the middle", lambda c: (c / "captures.jsonl").write_bytes(log.replace(b"\n", b"\n\n", 1)))
    damaged(
        "a stored body is missing",
        lambda c: (
            next((c / "blobs").rglob("*")).parent.joinpath(caps[1].body_sha256).unlink()
            if False
            else (c / "blobs" / caps[1].body_sha256[:2] / caps[1].body_sha256).unlink()
        ),
    )

    # a middle line that is valid JSON but breaks one rule of the format; the lines after it are re-chained so only that rule is at fault
    import json

    def rewrite(copy: Path, n: int, change) -> None:  # type: ignore[no-untyped-def]
        lines = (copy / "captures.jsonl").read_bytes().splitlines(keepends=True)
        metas = [mod.canonical.parse_record(line[:-1]) for line in lines]
        out = []
        previous = mod.GENESIS
        for i, meta in enumerate(metas):
            if i == n:
                text = change(meta)
                raw = (text if isinstance(text, bytes) else mod.canonical.dumps(meta)) + b"\n"
            else:
                meta["prev"] = previous
                raw = mod.canonical.dumps(meta) + b"\n"
            out.append(raw)
            previous = hashlib.sha256(raw).hexdigest()
        (copy / "captures.jsonl").write_bytes(b"".join(out))

    def broken(label: str, change, why: str | None = None) -> None:  # type: ignore[no-untyped-def]
        copy = fresh()
        shutil.copytree(base, copy)
        rewrite(copy, 1, change)
        rejected(bad, label, lambda: mod.CaptureStore(copy).close(), mod.CaptureDamage, why=why)

    def setting(key: str, value):  # type: ignore[no-untyped-def]
        def change(meta):  # type: ignore[no-untyped-def]
            meta[key] = value

        return change

    def removing(key: str):  # type: ignore[no-untyped-def]
        def change(meta):  # type: ignore[no-untyped-def]
            del meta[key]

        return change

    broken("a line that is too long", setting("request", {"x": "y" * 300_000}), "too long")
    broken("a line that is not canonical", lambda meta: json.dumps(meta, sort_keys=True).encode(), "canonical")
    broken("a line with an extra member", setting("extra", 1), "members")
    for key in (
        "body_len",
        "body_sha256",
        "capture_id",
        "headers",
        "prev",
        "received_at",
        "request",
        "seq",
        "source",
        "status",
        "t_send",
        "trace_id",
    ):
        broken(f"a line without {key}", removing(key), "members")
    for label, key, value, why in [
        ("a bad id", "capture_id", "cap-zzz", "capture id"),
        ("a short id", "capture_id", "cap-abcdef", "capture id"),
        ("a bad hash", "body_sha256", "xyz", "body hash"),
        ("an upper-case hash", "body_sha256", "A" * 64, "body hash"),
        ("a negative length", "body_len", -1, "body length"),
        ("a text length", "body_len", "5", "body length"),
        ("a boolean length", "body_len", True, "body length"),
        ("an absurd length", "body_len", 2**40, "body length"),
        ("a headers list", "headers", [], "headers or request"),
        ("a header with a number", "headers", {"a": 1}, "headers or request"),
        ("a request list", "request", [], "headers or request"),
        ("a status of 99", "status", 99, "status"),
        ("a status of 700", "status", 700, "status"),
        ("a boolean status", "status", True, "status"),
        ("a text status", "status", "200", "status"),
        ("a numeric trace id", "trace_id", 5, "trace id"),
        ("a numeric source", "source", 5, "source"),
        ("a bad send time", "t_send", "tomorrow", "bad time"),
        ("a bad receive time", "received_at", "tomorrow", "bad time"),
        ("a wrong sequence number", "seq", 5, "sequence number"),
        ("a boolean sequence number", "seq", True, "sequence number"),
        ("a wrong chain", "prev", "1" * 64, "does not follow"),
    ]:
        broken(label, setting(key, value), why)
    broken("a repeated capture id", lambda meta: meta.update(capture_id=caps[0].capture_id), "appears twice")
    broken("a length that does not match the body", lambda meta: meta.update(body_len=int(meta["body_len"]) + 1), "bytes")

    # a flip in the final line makes it unreadable; it is removed like a torn tail (it cannot have been acknowledged and verified)
    copy = fresh()
    shutil.copytree(base, copy)
    data = bytearray(log)
    data[last_start + 5] ^= 1
    (copy / "captures.jsonl").write_bytes(bytes(data))
    with mod.CaptureStore(copy) as store:
        if len(store) != 3 or store.recovery.truncated_bytes != len(log) - last_start:
            bad.append("an invalid final line was not removed")

    # an altered blob is noticed when it is read
    copy = fresh()
    shutil.copytree(base, copy)
    victim = copy / "blobs" / caps[2].body_sha256[:2] / caps[2].body_sha256
    victim.write_bytes(b"altered")
    with mod.CaptureStore(copy) as store:
        rejected(bad, "altered body", lambda: store.get(caps[2].capture_id), mod.CaptureDamage)
        rejected(bad, "altered body by hash", lambda: store.body(caps[2].body_sha256), mod.CaptureDamage)
        if store.get(caps[1].capture_id) != caps[1]:
            bad.append("one altered body affected another capture")
        rejected(bad, "unknown capture", lambda: store.get("cap-" + "0" * 32), KeyError)
        rejected(bad, "body of a bad hash", lambda: store.body("xyz"), mod.CaptureStoreError, why="SHA-256")

    # a body that vanishes while the store is open is noticed when it is read
    copy = fresh()
    shutil.copytree(base, copy)
    with mod.CaptureStore(copy) as store:
        (copy / "blobs" / caps[3].body_sha256[:2] / caps[3].body_sha256).unlink()
        rejected(bad, "a body deleted under an open store", lambda: store.get(caps[3].capture_id), mod.CaptureDamage, why="missing")

    # --- crash points around the append ---------------------------------------------------------------------------------
    for point in ("blob-written", "blob-durable", "line-written", "line-durable"):
        root = fresh()
        with mod.CaptureStore(root) as store:
            store.append(make(mod, 0))

        def crash_at(name: str, wanted: str = point) -> None:
            if name == wanted:
                raise Crash(name)

        crashing = mod.CaptureStore(root, crash=crash_at)
        victim_capture = make(mod, 1, body=b"in flight")
        try:
            crashing.append(victim_capture)
            bad.append(f"crash point {point} was never reached")
        except Crash:
            pass
        crashing.close()
        if point == "blob-written" and not list((root / "blobs").rglob("*.tmp")):
            bad.append("the interrupted blob left no temporary file to clean")
        with mod.CaptureStore(root) as store:
            present = victim_capture.capture_id in store
            if point in ("blob-written", "blob-durable") and present:
                bad.append(f"a capture whose line was never written exists after a crash at {point}")
            if point == "line-durable" and not present:
                bad.append("an acknowledged-or-durable capture was lost")
            if present and store.get(victim_capture.capture_id) != victim_capture:
                bad.append(f"the capture is damaged after a crash at {point}")
            if store.recovery.captures != len(store):
                bad.append("recovery counts the wrong number of captures")
            if store.recovery.removed_temporaries != (1 if point == "blob-written" else 0):
                bad.append(f"recovery removed {store.recovery.removed_temporaries} temporary files after a crash at {point}")
            store.append(victim_capture)  # re-delivery completes it whatever happened
            if store.get(victim_capture.capture_id) != victim_capture or len(store) != 2:
                bad.append(f"re-delivery after a crash at {point} failed")
        with mod.CaptureStore(root) as store:
            if len(store) != 2:
                bad.append(f"the store changed on reopening after a crash at {point}")
            if store.recovery.removed_temporaries != 0 or list((root / "blobs").rglob("*.tmp")):
                bad.append("a temporary file survived the first recovery")

    # an orphan blob (a body without a line) is reported and reused
    root = fresh()
    with mod.CaptureStore(root) as store:
        store.append(make(mod, 0))
    orphan_body = b"orphan"
    digest = hashlib.sha256(orphan_body).hexdigest()
    (root / "blobs" / digest[:2]).mkdir(parents=True, exist_ok=True)
    (root / "blobs" / digest[:2] / digest).write_bytes(orphan_body)
    with mod.CaptureStore(root) as store:
        if store.recovery.orphan_blobs != 1:
            bad.append("an orphan body is not reported")
        store.append(make(mod, 1, body=orphan_body))
        if store.blob_count() != 2 or store.get(next(i for i in store.ids() if store.stored(i).body_sha256 == digest)).body != orphan_body:
            bad.append("an orphan body is not reused")
    # a blob of the right name but the wrong size is replaced, not trusted
    root = fresh()
    digest = hashlib.sha256(b"real body").hexdigest()
    (root / "blobs" / digest[:2]).mkdir(parents=True)
    (root / "blobs" / digest[:2] / digest).write_bytes(b"short")
    with mod.CaptureStore(root) as store:
        store.append(make(mod, 0, body=b"real body"))
        if store.body(digest) != b"real body":
            bad.append("a blob of the wrong size was trusted")

    # --- one writer ---------------------------------------------------------------------------------------------------------
    root = fresh()
    with mod.CaptureStore(root):
        rejected(bad, "a second writer", lambda: mod.CaptureStore(root), mod.StoreLocked)
    with mod.CaptureStore(root):
        pass  # released on close
    closed = mod.CaptureStore(root)
    closed.close()
    rejected(bad, "appending to a closed store", lambda: closed.append(make(mod, 0)), mod.CaptureStoreError)

    # --- credentials are never stored -------------------------------------------------------------------------------------
    headers = {
        "Authorization": "Bearer abc",
        "X-Trace-Id": "t",
        "set-cookie": "a=b",
        "x-api-key": "k",
        "X-RateLimit-Remaining": "5",
        "Retry-After": "3",
        "x-mbx-used-weight-1m": "20",
        "Content-Type": "application/json",
        "keep-alive": "timeout=5",
        "X-Auth-Token": "z",
    }
    clean = mod.redact_headers(headers)
    if set(clean) != {"x-trace-id", "x-ratelimit-remaining", "retry-after", "x-mbx-used-weight-1m", "content-type", "keep-alive"}:
        bad.append(f"redact_headers kept or dropped the wrong headers: {sorted(clean)}")
    # one header per credential-looking word, each containing no other, so that dropping a word from the list is noticed
    for name in (
        "proxy-authorization",
        "x-api-key",
        "x-csrf-token",
        "x-client-secret",
        "cookie",
        "ok-access-sign",
        "x-password",
        "ok-access-passphrase",
        "x-credential-id",
        "x-session-id",
    ):
        rejected(bad, f"header {name}", lambda name=name: make(mod, 0, headers={name: "v"}), mod.CaptureError)
    rejected(bad, "an upper-case header name", lambda: make(mod, 0, headers={"Content-Type": "x"}), mod.CaptureError)
    cases = [
        ("https://user:pw@api.example/x?a=1", "https://api.example/x?a=1"),
        ("https://api.example:8443/x?apikey=SECRET&symbol=BTC", "https://api.example:8443/x?apikey=REDACTED&symbol=BTC"),
        (
            "https://api.example/x?token=t&Signature=s&q=1&access_token=a",
            "https://api.example/x?token=REDACTED&Signature=REDACTED&q=1&access_token=REDACTED",
        ),
        ("https://api.example/x#frag", "https://api.example/x"),
        ("https://[2001:db8::1]:443/x", "https://[2001:db8::1]:443/x"),
        ("https://api.example/x?empty=&b=2", "https://api.example/x?empty=&b=2"),
    ]
    for given, wanted in cases:
        if mod.redact_url(given) != wanted:
            bad.append(f"redact_url({given!r}) = {mod.redact_url(given)!r}, expected {wanted!r}")
    rejected(bad, "user information in the request URL", lambda: make(mod, 0, request={"url": "https://u:p@x.example/"}), mod.CaptureError)
    rejected(bad, "user name in the request URL", lambda: make(mod, 0, request={"url": "https://u@x.example/"}), mod.CaptureError)
    rejected(bad, "a secret query value", lambda: make(mod, 0, request={"url": "https://x.example/?api_key=abc"}), mod.CaptureError)
    make(mod, 0, request={"url": "https://x.example/?api_key=REDACTED&symbol=BTC"})

    # --- validation of the fields -------------------------------------------------------------------------------------------
    for label, kwargs in [
        ("bad id", {"capture_id": "cap-xyz"}),
        ("upper-case id", {"capture_id": "cap-" + "A" * 32}),
        ("id with a newline", {"capture_id": "cap-" + "a" * 32 + "\n"}),
        ("empty source", {"source": ""}),
        ("upper-case source", {"source": "RYO"}),
        ("source with a space", {"source": "r y"}),
        ("status 99", {"status": 99}),
        ("status 600", {"status": 600}),
        ("empty trace id", {"trace_id": ""}),
        ("long trace id", {"trace_id": "t" * 129}),
    ]:
        rejected(bad, label, lambda kwargs=kwargs: make(mod, 0, **kwargs), mod.CaptureError)
    rejected(bad, "text body", lambda: mod.RawCapture("cap-" + "a" * 32, "ryo", {}, 200, {}, "text", T0, T0), mod.CaptureError)  # type: ignore[arg-type]
    rejected(
        bad,
        "received before sent",
        lambda: mod.RawCapture("cap-" + "a" * 32, "ryo", {}, 200, {}, b"", T0, T0 - timedelta(seconds=1)),
        mod.CaptureError,
    )
    rejected(bad, "naive time", lambda: mod.RawCapture("cap-" + "a" * 32, "ryo", {}, 200, {}, b"", T0.replace(tzinfo=None), T0), TimeError)
    rejected(bad, "headers too large", lambda: make(mod, 0, headers={"x-a": "v" * 20_000}), mod.CaptureError)
    rejected(bad, "a short id", lambda: make(mod, 0, capture_id="cap-abcdef"), mod.CaptureError)
    saved_limit = mod.MAX_BODY_BYTES
    mod.MAX_BODY_BYTES = 100  # the real limit is 64 MiB; the rule is the same and the check stays fast
    try:
        rejected(bad, "a body over the limit", lambda: make(mod, 0, body=bytes(101)), mod.CaptureError)
        make(mod, 0, body=bytes(100))
    finally:
        mod.MAX_BODY_BYTES = saved_limit
    rejected(bad, "a header with a number", lambda: make(mod, 0, headers={"x-a": 5}), mod.CaptureError)
    rejected(bad, "an empty header name", lambda: make(mod, 0, headers={"": "v"}), mod.CaptureError)
    with mod.CaptureStore(fresh()) as huge_store:
        rejected(
            bad,
            "metadata that does not fit a line",
            lambda: huge_store.append(make(mod, 0, request={"x": "y" * 300_000})),
            mod.CaptureError,
        )
        if len(huge_store) != 0:
            bad.append("a capture with oversized metadata was stored")
    make(mod, 0, status=None, body=b"")  # no response at all is a valid capture (a timeout leaves a record)
    make(mod, 0, status=100)
    make(mod, 0, status=599)
    when = T0 + timedelta(hours=9)
    local = mod.RawCapture(
        "cap-" + "b" * 32, "ryo", {}, 200, {}, b"", when.astimezone(timezone_plus_nine()), when.astimezone(timezone_plus_nine())
    )
    if local.t_send != when or local.t_send.utcoffset() != timedelta(0):
        bad.append("times are not normalised to UTC")
    elapsed = make(mod, 3)
    if elapsed.elapsed_s() != 0.25:
        bad.append("elapsed time is wrong")
    if make(mod, 0, body=b"abc").body_sha256 != hashlib.sha256(b"abc").hexdigest():
        bad.append("the body hash is wrong")
    bad += read_only_failures(mod, tmp / "read-only")
    bad += count_failures(mod, tmp / "count")
    return bad


def timezone_plus_nine():  # type: ignore[no-untyped-def]
    from datetime import timezone

    return timezone(timedelta(hours=9))


def files_of(root: Path) -> dict[str, tuple[int, int, str]]:
    """Every file under the folder: size, modification time and SHA-256 (empty for a folder that is not there)."""
    return {
        p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def read_only_failures(mod: types.ModuleType, tmp: Path) -> list[str]:
    """A store opened ``read_only`` reads what a writer holding the lock has stored, and writes nothing: no lock, no folder made, no torn
    tail cut, no temporary file removed, no capture appended. A line the writer is part-way through is not a capture."""
    bad: list[str] = []
    root = tmp / "store"
    writer = mod.CaptureStore(root)  # holds the lock for the whole check, as a running league does
    captures = [make(mod, i) for i in range(3)]
    for item in captures:
        writer.append(item)
    with open(root / "captures.jsonl", "ab") as handle:
        handle.write(b'{"body_len":1,"half":"a line being writ')  # no newline: the writer is part-way through it
    (root / "blobs" / "ab").mkdir()
    (root / "blobs" / "ab" / f"{'ab' * 32}.1234.tmp").write_bytes(b"x")  # a body being written
    orphan = hashlib.sha256(b"orphan").hexdigest()
    (root / "blobs" / orphan[:2]).mkdir(exist_ok=True)
    (root / "blobs" / orphan[:2] / orphan).write_bytes(b"orphan")  # a body whose line is not written yet
    before = files_of(root)
    try:
        reader = mod.CaptureStore(root, read_only=True)
    except Exception as exc:
        writer.close()
        return [f"read-only: a store a writer holds could not be opened: {type(exc).__name__}: {exc}"]
    if (len(reader), reader.ids()) != (3, [c.capture_id for c in captures]):
        bad.append(f"read-only: the captures it holds are {reader.ids()}, expected the three the writer stored")
    if any(reader.get(c.capture_id) != c for c in captures):
        bad.append("read-only: a capture it reads is not the one stored")
    if (reader.recovery.truncated_bytes, reader.recovery.removed_temporaries, reader.recovery.orphan_blobs) != (0, 0, 1):
        bad.append(f"read-only: it reports {reader.recovery!r}, expected nothing cut, nothing removed and one orphan body")
    if files_of(root) != before:
        bad.append("read-only: the store's files changed (a torn tail cut, a temporary file removed, or a lock file made)")
    rejected(bad, "read-only: appending", lambda: reader.append(make(mod, 9)), mod.CaptureStoreError, why="the store is read-only")
    if files_of(root) != before:
        bad.append("read-only: a refused append still changed the store")
    reader.close()
    writer.append(make(mod, 3))  # the writer is still the writer, and its lock was never taken from it
    writer.close()
    absent = tmp / "absent"
    nobody = mod.CaptureStore(absent, read_only=True)
    if len(nobody) != 0 or absent.exists():
        bad.append("read-only: a store that is not there is an empty one and is not made")
    damaged = tmp / "damaged"
    with mod.CaptureStore(damaged) as store:
        item = make(mod, 0)
        store.append(item)
    (damaged / "blobs" / item.body_sha256[:2] / item.body_sha256).unlink()
    rejected(
        bad,
        "read-only: a stored body that is missing",
        lambda: mod.CaptureStore(damaged, read_only=True),
        mod.CaptureDamage,
        why=f"the body {item.body_sha256} of a stored capture is missing",
    )
    return bad


def count_failures(mod: types.ModuleType, tmp: Path) -> list[str]:
    """``count_since`` counts a provider's captures from a moment on: that source only, the moment itself included, nothing before it."""
    bad: list[str] = []
    with mod.CaptureStore(tmp / "store") as store:
        for n, source in enumerate(["ryo", "ryo", "other", "ryo", "ryo", "other"]):
            store.append(make(mod, n, source=source))
        got = {
            "ryo from the start": store.count_since("ryo", T0),
            "ryo from the second, which is counted": store.count_since("ryo", T0 + timedelta(seconds=1)),
            "ryo from just after the second": store.count_since("ryo", T0 + timedelta(seconds=1, milliseconds=1)),
            "ryo after the last": store.count_since("ryo", T0 + timedelta(seconds=5)),
            "other from the start": store.count_since("other", T0),
            "a source never stored": store.count_since("nobody", T0),
        }
        want = {
            "ryo from the start": 4,
            "ryo from the second, which is counted": 3,
            "ryo from just after the second": 2,
            "ryo after the last": 0,
            "other from the start": 2,
            "a source never stored": 0,
        }
        bad += [f"count_since, {label}: {got[label]}, expected {want[label]}" for label in want if got[label] != want[label]]
        rejected(
            bad, "count_since with a time that has no zone", lambda: store.count_since("ryo", datetime(2026, 10, 29, 12, 0, 0)), TimeError
        )
    return bad
