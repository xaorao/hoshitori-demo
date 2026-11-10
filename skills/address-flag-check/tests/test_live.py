"""Live checks against the real public sources (OFAC, PublicNode, TronGrid, mempool.space). Not part of the offline suite: run with
``-m live``. They send only well-known burn or black-hole addresses, never a key."""

from __future__ import annotations

import pathlib

import pytest

from address_flag_check import run

pytestmark = pytest.mark.live


def test_live_burn_address_on_ethereum(tmp_path: pathlib.Path) -> None:
    env = run({"address": "0x000000000000000000000000000000000000dEaD", "chain": "ethereum"}, list_dir=tmp_path)
    assert env["data_mode"] == "live" and env["data"]["checks"][0]["list"]["copy"] == "downloaded"
    assert env["status"] == "ok" and env["data"]["verdict"] == "no_flag_found", env["summary"]
    assert env["data"]["sent_to"] == ["ethereum-rpc.publicnode.com"]
    again = run({"address": "0x000000000000000000000000000000000000dEaD", "chain": "ethereum"}, list_dir=tmp_path)
    assert again["data_mode"] == "mixed" and again["data"]["checks"][0]["list"]["copy"] == "recent"


def test_live_black_hole_on_tron_and_eater_on_bitcoin(tmp_path: pathlib.Path) -> None:
    tron = run({"address": "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb", "chain": "tron"}, list_dir=tmp_path)
    assert tron["data"]["verdict"] == "no_flag_found", tron["summary"]
    btc = run({"address": "1BitcoinEaterAddressDontSendf59kuE", "chain": "bitcoin"}, list_dir=tmp_path)
    assert btc["data"]["verdict"] == "no_flag_found" and btc["data"]["chain_facts"]["state"] == "observed", btc["summary"]
