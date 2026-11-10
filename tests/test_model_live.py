"""The model client against the real endpoint: never part of the offline suite (``pytest -m live``, with the key in ``.env``).

It makes a handful of small requests with the fastest model that answered the probe (CF-62) and checks what the product relies on: a
typed answer comes back through the real gateway over TLS, a ceiling that is too small is reported as truncated, and a model that this
account cannot use is a failure of the call, not a crash.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest

from hoshitori import model_client, model_gateway, typed
from hoshitori.capture import CaptureStore
from hoshitori.clock import SystemClock
from hoshitori.gateway import DispatchJournal, Gateway
from hoshitori.runner import Runner, ThreadExecutor
from hoshitori.ryo_transport import key_from_env_file

ENV = Path(__file__).resolve().parents[1] / ".env"
MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"
SCHEMA = typed.Object((typed.Field("symbol", typed.Text(10)), typed.Field("p", typed.Number(Decimal(0), Decimal(1), 3))))


def settings() -> tuple[str, str]:
    base, key = key_from_env_file(ENV, "LLM_BASE_URL"), key_from_env_file(ENV, "LLM_API_KEY")
    if not base or not key:
        pytest.skip("no model endpoint configured in .env")
    return base, key


@contextmanager
def live_client() -> Iterator[model_client.ModelClient]:
    import threading

    base, key = settings()
    directory = Path(tempfile.mkdtemp(prefix="model-live-"))
    clock = SystemClock()
    captures = CaptureStore(directory / "captures")
    gateway = Gateway(captures, DispatchJournal(directory / "dispatch.jsonl"))
    attached = model_gateway.attach_model(gateway, model_gateway.Endpoint("nvidia", base), lambda: key, clock)
    pool = ThreadExecutor(2)
    runner = Runner(gateway, {attached.adapter.provider: attached.transport}, clock, pool)
    loop = threading.Thread(target=runner.run_forever, daemon=True)
    loop.start()
    try:
        yield model_client.ModelClient(model_client.GatewayFetcher(runner, captures, clock, attached.adapter.provider))
    finally:
        runner.stop()
        loop.join(timeout=3)
        pool.shutdown()
        captures.close()


def ask(model: str = MODEL, max_tokens: int = 1500, timeout_s: float = 90) -> model_client.ModelRequest:
    return model_client.ModelRequest(
        stage="live_check",
        prompt_version="v1",
        system="You answer questions about markets from the data given and nothing else.",
        sections=(
            model_client.Section(
                "question", "Give your probability that BTC closes higher in one hour than now. You have no data: say 0.5."
            ),
        ),
        schema=SCHEMA,
        model=model,
        max_tokens=max_tokens,
        temperature="0",
        timeout_s=timeout_s,
        thinking=False,  # the product asks for no reasoning (ADR-009); with it left to the provider the endpoint takes a minute or more
    )


@pytest.mark.live
def test_a_typed_answer_comes_back_through_the_real_gateway() -> None:
    with live_client() as client:
        out = client.generate(ask())
    assert isinstance(out, model_client.Answer), out
    assert out.value["symbol"] and Decimal(0) <= out.value["p"] <= Decimal(1)
    call = out.calls[-1]
    assert call.usage is not None and not call.usage.estimated and call.usage.billed > 0 and call.latency_ms is not None


@pytest.mark.live
def test_a_ceiling_that_is_too_small_is_reported_as_truncated() -> None:
    with live_client() as client:
        out = client.generate(ask(max_tokens=5))
    assert isinstance(out, model_client.Failure) and out.outcome is model_client.Outcome.TRUNCATED, out


@pytest.mark.live
def test_a_model_this_account_cannot_use_is_a_failure_of_the_call() -> None:
    with live_client() as client:
        out = client.generate(ask(model="nvidia/llama-3.1-nemotron-ultra-253b-v1", timeout_s=30))
    assert isinstance(out, model_client.Failure) and out.outcome is model_client.Outcome.UNAVAILABLE, out


@pytest.mark.live
def test_a_claim_is_read_into_a_graph_by_the_real_model() -> None:
    """Not a measure of how well it reads (that needs the labelled set); only that the path works end to end and says what it did."""
    from hoshitori import claim_extract, claim_preliminary

    text = "Whales are accumulating SOL, therefore institutions are entering, and SOL will rise 15% this week."
    with live_client() as client:
        reading = claim_extract.read_claim(client, text, claim_extract.Config(MODEL, max_tokens=2500, timeout_s=120), ("SOL",))
    assert len(reading.extractions) == 3
    for e in reading.extractions:
        assert (e.graph is None) != (e.failure is None)
        if e.graph is not None:
            for node in e.graph.nodes:
                if node.spans:
                    assert e.graph.words(node) in text  # a stated part is always words of the claim
    if reading.page is None:
        assert reading.message == claim_extract.UNREADABLE
    else:
        assert claim_preliminary.lint(reading.page) == ()
