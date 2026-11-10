"""A fake exchange on 127.0.0.1 for the full-stack venue tests.

It answers exactly the requests it has routes for, byte for byte, and everything else with 404. A route is ``(method, path, body)`` as the
adapter builds it. A ``script`` can replace any answer with a ``Behaviour`` (an error, a delay, a body that stops early): the same machinery
as the fake RYO, without its service. The answers in the tests are the synthetic bodies of the venue profiles.
"""

from __future__ import annotations

from collections.abc import Callable

from tests.ryo_fake_server import Behaviour, FakeRyo, Seen


class FakeVenue(FakeRyo):
    def __init__(
        self, routes: dict[tuple[str, str, bytes | None], Behaviour], script: Callable[[Seen], Behaviour | None] | None = None
    ) -> None:
        super().__init__(script=script)
        self.routes = routes

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def default(self, seen: Seen) -> Behaviour:
        found = self.routes.get((seen.method, seen.path, seen.body or None))
        return found if found is not None else Behaviour(404, b'{"message":"no such route"}', {"content-type": "application/json"})
