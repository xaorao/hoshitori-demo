"""The macOS trust-store retry: only on a certificate failure, only once, never over the person's own SSL_CERT_FILE."""

from __future__ import annotations

import subprocess
import sys

import pytest

from address_flag_check import trust
from address_flag_check.transport import CertificateError, HttpReply

OK = HttpReply(200, {}, b"ok")
PEM = "-----BEGIN CERTIFICATE-----\nAA==\n-----END CERTIFICATE-----\n"


class Scripted:
    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> HttpReply:
        self.calls += 1
        out = self.outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        assert isinstance(out, HttpReply)
        return out


def make_recorder(reply: Scripted) -> tuple[list[object], object]:
    made: list[object] = []

    def make(context: object) -> Scripted:
        made.append(context)
        return reply

    return made, make


def test_a_certificate_failure_is_retried_once_with_the_system_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trust.ssl, "create_default_context", lambda cadata: ("context", cadata))
    primary, second = Scripted(CertificateError("x")), Scripted(OK, OK)
    made, make = make_recorder(second)
    t = trust.FallbackTransport(primary, make, roots=lambda: PEM, environ={})  # type: ignore[arg-type]
    assert t("GET", "https://x", {}, None, 1.0) is OK
    assert made == [("context", PEM)]
    assert t("GET", "https://x", {}, None, 1.0) is OK  # the trust that worked is kept: no second failure first
    assert primary.calls == 1 and second.calls == 2


def test_no_retry_over_the_persons_own_trust_store() -> None:
    primary = Scripted(CertificateError("x"))
    made, make = make_recorder(Scripted(OK))
    t = trust.FallbackTransport(primary, make, roots=lambda: PEM, environ={"SSL_CERT_FILE": "/mine.pem"})  # type: ignore[arg-type]
    with pytest.raises(CertificateError):
        t("GET", "https://x", {}, None, 1.0)
    assert made == []


def test_no_retry_without_system_roots_or_for_another_failure() -> None:
    made, make = make_recorder(Scripted(OK))
    t = trust.FallbackTransport(Scripted(CertificateError("x")), make, roots=lambda: None, environ={})  # type: ignore[arg-type]
    with pytest.raises(CertificateError):
        t("GET", "https://x", {}, None, 1.0)
    t2 = trust.FallbackTransport(Scripted(TimeoutError("slow")), make, roots=lambda: PEM, environ={})  # type: ignore[arg-type]
    with pytest.raises(TimeoutError):
        t2("GET", "https://x", {}, None, 1.0)
    assert made == []


def test_an_answer_is_passed_through_untouched() -> None:
    t = trust.FallbackTransport(Scripted(OK), lambda c: Scripted(), roots=lambda: PEM, environ={})  # type: ignore[arg-type,return-value]
    assert t("GET", "https://x", {}, None, 1.0) is OK and t.fallback is None


def test_system_roots_only_on_macos_and_only_with_certificates() -> None:
    def run(out: str, code: int = 0) -> object:
        return lambda *a, **k: subprocess.CompletedProcess(a, code, out, "")

    assert trust.system_roots(run(PEM), platform="linux") is None
    assert trust.system_roots(run(PEM), platform="darwin") == PEM
    assert trust.system_roots(run(PEM, 1), platform="darwin") is None
    assert trust.system_roots(run("nothing"), platform="darwin") is None

    def broken(*a: object, **k: object) -> object:
        raise FileNotFoundError("no security tool")

    assert trust.system_roots(broken, platform="darwin") is None  # type: ignore[arg-type]


@pytest.mark.skipif(sys.platform != "darwin", reason="reads the macOS keychain")
def test_the_real_keychain_answers_with_certificates() -> None:
    pem = trust.system_roots()
    assert pem is not None and pem.count(trust.MARK) > 50
    trust.ssl.create_default_context(cadata=pem)  # the roots load as a trust store
