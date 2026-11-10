"""Calls the real public sources. Never part of the default set: run with ``pytest -m live``."""

from __future__ import annotations

import datetime as dt
import time

import jsonschema
import pytest

from exit_liquidity_check import OUTPUT_SCHEMA, run
from exit_liquidity_check.registry import SUPPORTED, TOKENS
from exit_liquidity_check.transport import Caller
from exit_liquidity_check.trust import default_transport
from exit_liquidity_check.venues import fetch_depth

pytestmark = pytest.mark.live


@pytest.mark.parametrize("token", SUPPORTED)
def test_the_registry_contract_is_the_base_token_of_live_pools(token: str) -> None:
    caller = Caller(default_transport(), now=dt.datetime.now(dt.UTC), deadline=time.monotonic() + 60)
    depth = fetch_depth(token, caller)
    assert depth.pools, f"no pool passed the filters for {token}: {depth.rejected[:3]}"
    assert depth.address == TOKENS[token]["address"]
    assert depth.total_liquidity_usd and depth.total_liquidity_usd > 1_000_000


def test_a_solana_exit_is_quoted_and_validates() -> None:
    out = run({"token": "SOL", "size_usd": 50_000})
    jsonschema.validate(out, OUTPUT_SCHEMA)
    assert out["status"] == "ok" and out["data"]["basis"] == "quoted"
