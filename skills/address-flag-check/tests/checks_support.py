"""Shared helpers of the checks modules: a case that collects failures instead of stopping at the first, and readers of the recorded fixtures.

A checks module's ``failures(mod)`` runs against the real module or against a mutated copy of it (``tests/mutation_sweep.py``), so every
expectation here is written by hand or read from a recorded upstream answer, never taken from the module's own output.
"""

from __future__ import annotations

import json
import pathlib
import types
from collections.abc import Callable
from typing import Any

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


class Case:
    def __init__(self, mod: types.ModuleType | None = None) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want or type(got) is not type(want):
            self.bad.append(f"{label}: got {got!r} ({type(got).__name__}), expected {want!r} ({type(want).__name__})")

    def true(self, label: str, value: object) -> None:
        if value is not True:
            self.bad.append(f"{label}: got {value!r}, expected True")

    def raises(self, label: str, call: Callable[[], object], error: type[BaseException], says: str | None = None) -> BaseException | None:
        try:
            call()
        except error as exc:
            if says is not None and str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return exc
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return None
        self.bad.append(f"{label}: was accepted")
        return None

    def run(self, label: str, call: Callable[[], object]) -> Any:
        """Call and record an unexpected exception as a failure instead of stopping the checks."""
        try:
            return call()
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return None


def recorded(path: str) -> dict[str, Any]:
    """One recorded upstream answer (``tests/fixtures/rpc/...``): the request, the status, the kept headers and the raw body."""
    data: dict[str, Any] = json.loads((FIXTURES / "rpc" / path).read_text())
    return data


def recorded_body(path: str) -> bytes:
    return str(recorded(path)["body"]).encode()


def extract_xml() -> bytes:
    return (FIXTURES / "ofac" / "sdn_advanced_extract.xml").read_bytes()


def synthetic_xml(name: str = "sdn_synthetic.xml") -> bytes:
    return (FIXTURES / "synthetic" / name).read_bytes()


def ofac_meta() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "ofac" / "recorded_file.json").read_text())
    return data


class Recorded:
    """A transport that answers with recorded upstream replies (``tests/fixtures/rpc``), matched by URL, method and request body.

    JSON-RPC requests are matched on method and params, not on the request id; when the code's id differs from the recorded one, the
    recorded body is served with only its ``id`` member set to the code's id (said here because it is the one change made to a recording).
    ``failing`` maps a URL prefix to an exception or a (status, body) pair, for failure cases (synthetic, labelled so by the caller).
    """

    live = False

    def __init__(self, names: list[str], failing: dict[str, Any] | None = None, http_reply: Any = None) -> None:
        if http_reply is None:
            from address_flag_check.transport import HttpReply

            http_reply = HttpReply
        self.reply = http_reply
        self.failing = failing or {}
        self.calls: list[tuple[str, str, bytes | None]] = []
        self.headers: list[dict[str, str]] = []  # the headers of each call, in order
        self.table: list[tuple[str, str, Any, dict[str, Any]]] = []
        for name in names:
            rec = recorded(name)
            body = rec["request_body"]
            self.table.append((rec["source_url"], rec["method"], body, rec))

    def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> Any:
        self.calls.append((method, url, body))
        self.headers.append(dict(headers))
        for prefix, failure in self.failing.items():
            if url.startswith(prefix):
                if isinstance(failure, BaseException):
                    raise failure
                status, data = failure
                return self.reply(status, {}, data)
        sent = json.loads(body) if body else None
        for rec_url, rec_method, rec_body, rec in self.table:
            if rec_url != url or rec_method != method:
                continue
            if isinstance(sent, dict) and isinstance(rec_body, dict) and "jsonrpc" in sent:
                if (sent.get("method"), sent.get("params")) != (rec_body.get("method"), rec_body.get("params")):
                    continue
                data = str(rec["body"]).encode()
                if rec_body.get("id") != sent.get("id"):
                    answer = json.loads(data)
                    answer["id"] = sent.get("id")
                    data = json.dumps(answer, separators=(",", ":")).encode()
                return self.reply(int(rec["status"]), dict(rec["headers"]), data)
            if sent == rec_body:
                return self.reply(int(rec["status"]), dict(rec["headers"]), str(rec["body"]).encode())
        return self.reply(404, {}, b'{"error":"no recorded reply for this request"}')


ETH_DEAD = [
    "ethereum/eth_chain_id.json",
    "ethereum/eth_block_number.json",
    "ethereum/eth_block.json",
    "ethereum/eth_usdt_dead.json",
    "ethereum/eth_usdc_dead.json",
    "ethereum/eth_code_dead.json",
    "ethereum/eth_nonce_dead.json",
]
ETH_ENTITY = [
    *ETH_DEAD[:3],
    "ethereum/eth_usdt_entity.json",
    "ethereum/eth_usdc_entity.json",
    "ethereum/eth_code_entity.json",
    "ethereum/eth_nonce_entity.json",
]
ETH_WETH = [
    *ETH_DEAD[:3],
    "ethereum/eth_usdt_weth.json",
    "ethereum/eth_usdc_weth.json",
    "ethereum/eth_code_weth.json",
    "ethereum/eth_nonce_weth.json",
]
TRON_PN = [
    "tron/tron_pn_block_header.json",
    "tron/tron_pn_usdt_blackhole.json",
    "tron/tron_pn_usdt_entity.json",
    "tron/tron_pn_usdt_entity_usdt_label.json",
]
TRON_TG = [
    "tron/tron_tg_block_header.json",
    "tron/tron_tg_usdt_blackhole.json",
    "tron/tron_tg_usdt_entity.json",
    "tron/tron_tg_usdt_entity_usdt_label.json",
]
BTC = [
    "bitcoin/btc_tip_height.json",
    "bitcoin/btc_address_eater.json",
    "bitcoin/btc_address_bech32_genesis_like.json",
    "bitcoin/btc_address_invalid.json",
]


def other_evm(chain: str) -> list[str]:
    return [f"{chain}/{chain}_{n}.json" for n in ("chain_id", "block_number", "block", "code_dead", "nonce_dead")]


def server_date(name: str) -> Any:
    """The server's Date header of a recorded reply, as an aware datetime (the recording's own clock)."""
    from email.utils import parsedate_to_datetime

    return parsedate_to_datetime(str(recorded(name)["headers"]["date"]))
