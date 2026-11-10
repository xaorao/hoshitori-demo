"""Checks of ``exit_liquidity_check.trust``, runnable against a mutant: the macOS keychain read and the one-time certificate fallback."""

from __future__ import annotations

import subprocess
import types
from typing import Any

from tests.checks_support import Case

PEM = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"


class _Done:
    def __init__(self, code: int, out: str) -> None:
        self.returncode, self.stdout = code, out


def failures(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("keychain path", mod.KEYCHAIN, "/System/Library/Keychains/SystemRootCertificates.keychain")
    c.eq("mark", mod.MARK, "-----BEGIN CERTIFICATE-----")

    # system_roots
    calls: list[tuple[Any, dict[str, Any]]] = []

    def run_ok(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return _Done(0, PEM)

    c.eq("macOS returns the keychain text", mod.system_roots(run_ok, "darwin"), PEM)
    c.eq(
        "the tool is called by its absolute path with fixed arguments and bounds",
        calls,
        [
            (
                (["/usr/bin/security", "find-certificate", "-a", "-p", "/System/Library/Keychains/SystemRootCertificates.keychain"],),
                {"capture_output": True, "text": True, "timeout": 20, "check": False},
            )
        ],
    )
    for platform in ("linux", "win32", "Darwin", ""):
        calls.clear()
        c.eq(f"{platform!r} is not macOS", (mod.system_roots(run_ok, platform), calls), (None, []))
    c.eq("a failing tool", mod.system_roots(lambda *a, **k: _Done(1, PEM), "darwin"), None)
    c.eq("a tool that answers with no certificate", mod.system_roots(lambda *a, **k: _Done(0, "nothing"), "darwin"), None)
    c.eq("a tool that answers with nothing", mod.system_roots(lambda *a, **k: _Done(0, ""), "darwin"), None)
    c.eq("a failing tool with no certificate", mod.system_roots(lambda *a, **k: _Done(2, ""), "darwin"), None)

    def raises(exc: BaseException) -> Any:
        def run(*a: Any, **k: Any) -> Any:
            raise exc

        return run

    c.eq("no such tool", mod.system_roots(raises(FileNotFoundError("x")), "darwin"), None)
    c.eq("a tool that hangs", mod.system_roots(raises(subprocess.TimeoutExpired("x", 20)), "darwin"), None)
    c.eq("a tool that fails to start", mod.system_roots(raises(subprocess.SubprocessError("x")), "darwin"), None)
    try:
        mod.system_roots(raises(ValueError("bug")), "darwin")
    except ValueError:
        pass
    else:
        c.failed.append("an unexpected error was swallowed")
    c.eq("the platform defaults to this one", mod.system_roots.__defaults__[1] if mod.system_roots.__defaults__ else None, mod.sys.platform)

    # FallbackTransport
    class Primary:
        live = True

        def __init__(self, script: list[Any]) -> None:
            self.script, self.calls = list(script), []

        def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> Any:
            self.calls.append((method, url, headers, body, timeout))
            step = self.script.pop(0)
            if isinstance(step, BaseException):
                raise step
            return step

    cert = mod.CertificateError("x")
    made: list[Any] = []
    contexts: list[dict[str, Any]] = []
    real_ssl = mod.ssl
    mod.ssl = types.SimpleNamespace(create_default_context=lambda **kw: (contexts.append(kw), "context")[1])  # type: ignore[assignment]
    try:
        primary = Primary(["first", "second"])
        roots_called: list[int] = []
        ft = mod.FallbackTransport(
            primary, lambda ctx: made.append(ctx) or Primary(["via system roots"]), lambda: roots_called.append(1) or PEM, {}
        )
        c.eq("live", ft.live, True)
        c.eq("a good first answer", ft("GET", "https://h.example/", {"a": "b"}, b"x", 3.0), "first")
        c.eq("... passed straight through", primary.calls, [("GET", "https://h.example/", {"a": "b"}, b"x", 3.0)])
        c.eq("... with no fallback built", (made, roots_called, ft.fallback), ([], [], None))

        primary = Primary([cert])
        made.clear()
        inner = Primary(["via system roots", "again"])
        ft = mod.FallbackTransport(primary, lambda ctx: made.append(ctx) or inner, lambda: PEM, {})
        c.eq(
            "a certificate failure is retried through the system roots", ft("GET", "https://h.example/", {}, None, 3.0), "via system roots"
        )
        c.eq("... the context is built from those roots", (contexts, made), ([{"cadata": PEM}], ["context"]))
        c.eq("... and kept", ft.fallback is inner, True)
        c.eq("the fallback gets the same request", inner.calls, [("GET", "https://h.example/", {}, None, 3.0)])
        c.eq(
            "later calls go straight to the fallback",
            (ft("GET", "https://i.example/", {}, None, 1.0), len(primary.calls), len(made)),
            ("again", 1, 1),
        )

        for label, environ in (("SSL_CERT_FILE is set", {"SSL_CERT_FILE": "/some/file.pem"}),):
            roots_called.clear()
            ft = mod.FallbackTransport(Primary([cert]), lambda ctx: Primary([]), lambda: roots_called.append(1) or PEM, environ)
            try:
                ft("GET", "https://h.example/", {}, None, 1.0)
            except mod.CertificateError:
                c.eq(f"{label}: the person's trust store is not second-guessed", roots_called, [])
            else:
                c.failed.append(f"{label}: the failure was retried")
        roots_called.clear()
        ft = mod.FallbackTransport(
            Primary([cert]), lambda ctx: Primary(["ok"]), lambda: roots_called.append(1) or PEM, {"SSL_CERT_FILE": ""}
        )
        c.eq("an empty SSL_CERT_FILE chooses nothing", ft("GET", "https://h.example/", {}, None, 1.0), "ok")
        ft = mod.FallbackTransport(Primary([cert]), lambda ctx: Primary(["ok"]), lambda: PEM, {"OTHER": "1"})
        c.eq("other variables are not the setting", ft("GET", "https://h.example/", {}, None, 1.0), "ok")
        ft = mod.FallbackTransport(Primary([cert]), lambda ctx: Primary(["ok"]), lambda: None, {})
        try:
            ft("GET", "https://h.example/", {}, None, 1.0)
        except mod.CertificateError:
            c.eq("no roots to fall back on: the failure stands and nothing is built", ft.fallback, None)
        else:
            c.failed.append("a failure with no roots was answered")
        for label, exc in (("a network error", OSError("n")), ("a time-out", TimeoutError("t")), ("an unrelated error", ValueError("v"))):
            roots_called.clear()
            ft = mod.FallbackTransport(Primary([exc]), lambda ctx: Primary(["ok"]), lambda: roots_called.append(1) or PEM, {})
            try:
                ft("GET", "https://h.example/", {}, None, 1.0)
            except type(exc):
                c.eq(f"{label} is not retried", (roots_called, ft.fallback), ([], None))
            else:
                c.failed.append(f"{label} was swallowed")
        ft = mod.FallbackTransport(Primary([cert]), lambda ctx: Primary([mod.CertificateError("again")]), lambda: PEM, {})
        try:
            ft("GET", "https://h.example/", {}, None, 1.0)
        except mod.CertificateError:
            pass
        else:
            c.failed.append("a second certificate failure was swallowed")
        c.eq(
            "the defaults read the process's environment and the keychain",
            (
                mod.FallbackTransport.__init__.__defaults__[0] is mod.system_roots,
                mod.FallbackTransport.__init__.__defaults__[1] is mod.os.environ,
            ),
            (True, True),
        )
    finally:
        mod.ssl = real_ssl

    # default_transport
    t = mod.default_transport()
    c.eq(
        "the default transport",
        (
            type(t).__name__,
            t.live,
            type(t.primary).__name__,
            t.primary.context,
            t.roots is mod.system_roots,
            t.environ is mod.os.environ,
            t.fallback,
        ),
        ("FallbackTransport", True, "UrllibTransport", None, True, True, None),
    )
    ctx = object()
    made_default = t.make(ctx)
    c.eq(
        "... builds its second transport with the roots' context",
        (type(made_default).__name__, made_default.context is ctx),
        ("UrllibTransport", True),
    )
    return c.failed
