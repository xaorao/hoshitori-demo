"""A call that joins the fetch of an equal call is served at the better of the two priorities: a judge session sharing a fetch that an
on-demand investigation started must not wait in the investigation's place in the queue."""

from __future__ import annotations

from pathlib import Path

from hoshitori import gateway
from hoshitori.governor import Priority as P
from tests.gateway_support import Harness


def _owner_priority(h: Harness, call_id: int) -> P:
    return h.gateway._queue[call_id].call.priority


def test_a_judge_call_that_joins_an_on_demand_fetch_lifts_it_to_the_judge_lane(tmp_path: Path) -> None:
    h = Harness(gateway, tmp_path, limit=100, concurrency=1)
    first = h.submit(priority=P.P2, namespace=gateway.ON_DEMAND, caller="user")
    assert _owner_priority(h, first) is P.P2
    second = h.submit(priority=P.PJ, namespace=gateway.ON_DEMAND, caller="judge")
    assert h.result(second) is None
    assert _owner_priority(h, first) is P.PJ, "the shared fetch was left in the lower lane"


def test_a_lower_priority_joiner_never_lowers_the_fetch(tmp_path: Path) -> None:
    h = Harness(gateway, tmp_path, limit=100, concurrency=1)
    first = h.submit(priority=P.PJ, namespace=gateway.ON_DEMAND, caller="judge")
    h.submit(priority=P.P2, namespace=gateway.ON_DEMAND, caller="user")
    assert _owner_priority(h, first) is P.PJ
