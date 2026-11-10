"""A fake time-stamp authority on 127.0.0.1: it reads the RFC 3161 query it is sent and answers with a real token signed by a test CA.

The token carries the digest and the nonce of the query and a generation time chosen by the test, so the whole path from the gateway to
``tsa.verify_token`` and ``timing.assess`` can be walked with tokens that verify, and with the failures an authority can show (a refusal,
a late answer, an answer for another digest, garbage). Nothing here is used by the product.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from asn1crypto import tsp

from tests import tsa_pki
from tests.ryo_fake_server import Behaviour, FakeRyo, Seen

GRANTED = {"content-type": "application/timestamp-reply"}


class FakeTsa(FakeRyo):
    def __init__(
        self,
        chain: tsa_pki.Chain,
        now: Callable[[], datetime],
        *,
        script: Callable[[Seen], Behaviour | None] | None = None,
        options: Callable[[bytes, int], tsa_pki.TokenOptions] | None = None,
    ) -> None:
        super().__init__(script=script)
        self.chain = chain
        self.now = now
        self.options = options

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/tsr"

    def default(self, seen: Seen) -> Behaviour:
        try:
            query = tsp.TimeStampReq.load(seen.body, strict=True)
            digest = query["message_imprint"]["hashed_message"].native
            nonce = query["nonce"].native
        except Exception:
            return Behaviour(400, b"not a time-stamp query", {"content-type": "text/plain"})
        chosen: Any = self.options(digest, nonce) if self.options else tsa_pki.TokenOptions(gen_time=self.now(), nonce=nonce)
        return Behaviour(200, tsa_pki.build_token(digest, self.chain, chosen), GRANTED)


def rejection(fail_info: list[str] | None = None) -> bytes:
    """A refusal as an authority sends it: a response of status ``rejection`` with no token and, if it says why, a failure code."""
    status: dict[str, Any] = {"status": "rejection"}
    if fail_info:
        status["fail_info"] = set(fail_info)
    inner = tsp.PKIStatusInfo(status).dump()
    length = len(inner)
    header = bytes([0x30, length]) if length < 0x80 else bytes([0x30, 0x81, length])
    return header + inner
