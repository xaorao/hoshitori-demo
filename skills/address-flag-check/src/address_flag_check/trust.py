"""The real transport, with one retry for a trust store that cannot verify a source.

A Python from python.org or from uv on macOS verifies TLS with an OpenSSL CA file that can lack a root the system itself trusts; OFAC's site
is one that fails there. When a source's certificate cannot be verified on macOS, the request is made once more with the roots of the
system's keychain, read with the system's ``security`` tool. Nothing else is retried here: not another platform, not another error, and not
when the person has chosen a trust store with ``SSL_CERT_FILE``. A source whose certificate still cannot be verified fails as before.
"""

from __future__ import annotations

import os
import ssl
import subprocess
import sys
from collections.abc import Callable, Mapping

from .transport import CertificateError, HttpReply, Transport, UrllibTransport

KEYCHAIN = "/System/Library/Keychains/SystemRootCertificates.keychain"
MARK = "-----BEGIN CERTIFICATE-----"


def system_roots(run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run, platform: str = sys.platform) -> str | None:
    """The roots of the macOS system keychain as PEM text, or none (another platform, no tool, or no certificate in its answer)."""
    if platform != "darwin":
        return None
    try:
        done = run(["/usr/bin/security", "find-certificate", "-a", "-p", KEYCHAIN], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 and MARK in done.stdout else None


class FallbackTransport:
    """``primary``, and on a certificate failure on macOS a transport that trusts the system's roots, made once and kept."""

    live = True

    def __init__(
        self,
        primary: Transport,
        make: Callable[[ssl.SSLContext], Transport],
        roots: Callable[[], str | None] = system_roots,
        environ: Mapping[str, str] = os.environ,
    ) -> None:
        self.primary = primary
        self.make = make
        self.roots = roots
        self.environ = environ
        self.fallback: Transport | None = None

    def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> HttpReply:
        if self.fallback is not None:
            return self.fallback(method, url, headers, body, timeout)
        try:
            return self.primary(method, url, headers, body, timeout)
        except CertificateError:
            if self.environ.get("SSL_CERT_FILE"):
                raise  # the person chose the trust store; it is not second-guessed
            pem = self.roots()
            if pem is None:
                raise
            self.fallback = self.make(ssl.create_default_context(cadata=pem))
            return self.fallback(method, url, headers, body, timeout)


def default_transport() -> Transport:
    """The transport the skill and its command line use."""
    return FallbackTransport(UrllibTransport(), lambda context: UrllibTransport(context=context))
