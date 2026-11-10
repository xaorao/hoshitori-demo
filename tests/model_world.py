"""A model endpoint on loopback and the real gateway, runner and transport in front of it: nothing inside the product is replaced.

The server speaks the one route the product uses, ``POST /v1/chat/completions`` with a Bearer key, and answers from a queue of scripted
replies (or a default valid answer). It records every request it saw, so a check can read exactly what was sent.
"""

from __future__ import annotations

import json
import random
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoshitori import model_gateway
from hoshitori.breaker import BreakerConfig
from hoshitori.capture import CaptureStore
from hoshitori.clock import SystemClock
from hoshitori.gateway import DispatchJournal, Gateway
from hoshitori.retry import RetryPolicy
from hoshitori.runner import Runner, ThreadExecutor
from tests.ryo_fake_server import Behaviour, FakeRyo, Seen

KEY = "fake-model-key-0123456789"


def chat_body(
    content: str | None,
    *,
    finish: str = "stop",
    reasoning: str | None = None,
    usage: dict[str, Any] | None = None,
    model: str = "fake/model",
) -> bytes:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    body: dict[str, Any] = {"id": "chatcmpl-1", "model": model, "choices": [{"index": 0, "message": message, "finish_reason": finish}]}
    body["usage"] = usage if usage is not None else {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60}
    return json.dumps(body).encode()


def ok(content: str | None, **kw: Any) -> Behaviour:
    return Behaviour(200, chat_body(content, **kw), {"content-type": "application/json"})


class FakeModel(FakeRyo):
    """The chat route, a Bearer key and a queue of scripted replies."""

    def __init__(self, replies: list[Behaviour] | None = None, key: str = KEY) -> None:
        super().__init__(key=key)
        self.replies = list(replies or [])
        self.requests: list[dict[str, Any]] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def default(self, seen: Seen) -> Behaviour:
        if seen.method != "POST" or seen.path != "/v1/chat/completions":
            return Behaviour(404, b"404 page not found\n", {"content-type": "text/plain"})
        if seen.headers.get("authorization") != f"Bearer {self.key}":
            return Behaviour(
                401, b'{"status":401,"title":"Unauthorized","detail":"Invalid JWT serialization"}', {"content-type": "application/json"}
            )
        with self._lock:
            self.requests.append(json.loads(seen.body))
            reply = self.replies.pop(0) if self.replies else None
        return reply if reply is not None else ok('{"p": 0.5}')


@dataclass
class ModelWorld:
    server: FakeModel
    captures: CaptureStore
    runner: Runner
    clock: SystemClock
    provider: str
    attached: model_gateway.ModelAttached


@contextmanager
def model_world(
    replies: list[Behaviour] | None = None, *, key: Callable[[], str | None] | None = None, name: str = "fake"
) -> Iterator[ModelWorld]:
    server = FakeModel(replies).start()
    directory = Path(tempfile.mkdtemp(prefix="model-world-"))
    clock = SystemClock()
    captures = CaptureStore(directory / "captures")
    gateway = Gateway(
        captures,
        DispatchJournal(directory / "dispatch.jsonl"),
        retry_policy=RetryPolicy(base_s=0.05, cap_s=0.2, rate_limit_default_wait_s=0.1),
        rng=random.Random(1),
    )
    attached = model_gateway.attach_model(
        gateway,
        model_gateway.Endpoint(name, server.url),
        key or (lambda: KEY),
        clock,
        breaker=BreakerConfig(consecutive_failures=50),
        min_attempt_s=1.0,
        allow_insecure_loopback=True,
    )
    pool = ThreadExecutor(4)
    runner = Runner(gateway, {attached.adapter.provider: attached.transport}, clock, pool)
    loop = threading.Thread(target=runner.run_forever, daemon=True)
    loop.start()
    try:
        yield ModelWorld(server, captures, runner, clock, attached.adapter.provider, attached)
    finally:
        runner.stop()
        loop.join(timeout=3)
        pool.shutdown()
        server.stop()
        captures.close()
        import shutil

        shutil.rmtree(directory, ignore_errors=True)
