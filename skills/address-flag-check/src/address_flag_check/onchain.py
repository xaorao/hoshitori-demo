"""On-chain reads through keyless public nodes (D3 7.2): issuer freeze flags and chain facts.

* EVM: one node per chain (PublicNode; the list can be replaced). First ``eth_chainId`` (a node on another chain is never trusted), then
  ``eth_blockNumber`` and that block's header, then every read names that one block, so all answers refer to one stated block.
* USDT (``isBlackListed``) and USDC (``isBlacklisted``) on Ethereum: an ``eth_call`` whose answer must be exactly one 32-byte word, 0 or 1.
* USDT on Tron: ``triggerconstantcontract`` on PublicNode, then TronGrid. A Tron constant call cannot name a block; the block reported is
  the newest one read just before the call, and ``pinned`` says so.
* Chain facts are facts, never a judgement: EVM code kind (none, contract, EIP-7702 delegation) and the count of transactions sent from the
  address; Bitcoin confirmed transaction and output counts from mempool.space.
* Every reply is checked for shape; anything else is ``bad_response``. Upstream text never leaves this module.
* ``sent_to`` lists every host that received a request carrying the address.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .encoding import base58check_decode, eip55
from .errors import SourceError
from .transport import Caller, HttpReply

EVM_NODES: dict[str, tuple[str, ...]] = {
    "ethereum": ("https://ethereum-rpc.publicnode.com",),
    "arbitrum": ("https://arbitrum-one-rpc.publicnode.com",),
    "base": ("https://base-rpc.publicnode.com",),
    "bsc": ("https://bsc-rpc.publicnode.com",),
    "optimism": ("https://optimism-rpc.publicnode.com",),
    "polygon": ("https://polygon-bor-rpc.publicnode.com",),
}
CHAIN_IDS = {"ethereum": 1, "arbitrum": 42161, "base": 8453, "bsc": 56, "optimism": 10, "polygon": 137}
TRON_NODES = ("https://tron-rpc.publicnode.com", "https://api.trongrid.io")
MEMPOOL = "https://mempool.space"
DELEGATION_PREFIX = "ef0100"  # EIP-7702 delegation indicator: 0xef0100 followed by the 20-byte delegate
_HEX = re.compile(r"0x[0-9a-fA-F]+")
_WORD = re.compile(r"0x[0-9a-fA-F]{64}")
_TRON_WORD = re.compile(r"[0-9a-fA-F]{64}")
_DIGITS = re.compile(r"[0-9]+")


@dataclass(frozen=True)
class Freeze:
    check: str
    source_id: str
    chain: str
    contract: str
    function: str
    selector: str


FREEZES: dict[tuple[str, str], Freeze] = {
    ("usdt_freeze", "ethereum"): Freeze(
        "usdt_freeze", "usdt_freeze_ethereum", "ethereum", "0xdac17f958d2ee523a2206206994597c13d831ec7", "isBlackListed", "e47d6060"
    ),
    ("usdc_freeze", "ethereum"): Freeze(
        "usdc_freeze", "usdc_freeze_ethereum", "ethereum", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "isBlacklisted", "fe575a87"
    ),
    ("usdt_freeze", "tron"): Freeze(
        "usdt_freeze", "usdt_freeze_tron", "tron", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "isBlackListed", "isBlackListed(address)"
    ),
}


@dataclass(frozen=True)
class Block:
    chain: str
    number: int
    age_seconds: int
    pinned: bool


@dataclass(frozen=True)
class FreezeResult:
    source_id: str
    state: str  # listed | not_listed | unavailable
    raw_value: bool | None
    block: Block | None
    reason: str | None
    source_url: str


@dataclass(frozen=True)
class Facts:
    state: str  # observed | unavailable
    chain: str
    block: int | None  # EVM block number or Bitcoin height
    code_kind: str | None = None
    code_bytes: int | None = None
    delegate: str | None = None
    sent_tx_count: int | None = None
    tx_count: int | None = None
    funded_outputs: int | None = None
    spent_outputs: int | None = None
    reason: str | None = None
    source_url: str = ""


@dataclass
class ChainRead:
    freezes: list[FreezeResult] = field(default_factory=list)
    facts: Facts | None = None
    sent_to: list[str] = field(default_factory=list)
    block_age_seconds: int | None = None


def _bad() -> SourceError:
    return SourceError("bad_response")


def call_data(selector: str, key: str) -> str:
    return "0x" + selector + "0" * 24 + key[2:]


def decode_flag(result: object) -> bool:
    if not isinstance(result, str) or not _WORD.fullmatch(result):
        raise _bad()
    value = int(result, 16)
    if value > 1:
        raise _bad()
    return value == 1


def decode_quantity(result: object) -> int:
    if not isinstance(result, str) or not _HEX.fullmatch(result):
        raise _bad()
    return int(result, 16)


def code_kind(result: object) -> tuple[str, int, str | None]:
    if not isinstance(result, str) or not result.startswith("0x") or len(result) % 2:
        raise _bad()
    digits = result[2:]
    if not digits:
        return "no_code", 0, None
    if not _HEX.fullmatch(result):
        raise _bad()
    size = len(digits) // 2
    if size == 23 and digits.lower().startswith(DELEGATION_PREFIX):
        return "eip7702_delegation", 23, eip55(digits[6:].lower())
    return "contract", size, None


def tron_parameter(address: str) -> str:
    payload = base58check_decode(address)
    if payload is None or len(payload) != 21:
        raise _bad()
    return payload[1:].hex().rjust(64, "0")


def rpc_result(reply: HttpReply, request_id: int) -> object:
    if reply.status != 200:
        raise _bad()
    try:
        doc = json.loads(reply.body)
    except ValueError:
        raise _bad() from None
    if not isinstance(doc, dict) or doc.get("jsonrpc") != "2.0" or doc.get("id") != request_id or "error" in doc or "result" not in doc:
        raise _bad()
    return doc["result"]


def _json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except ValueError:
        raise _bad() from None


def _count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _bad()
    return value


def decode_tron_flag(body: bytes) -> bool:
    doc = _json(body)
    if not isinstance(doc, dict) or not isinstance(doc.get("result"), dict) or doc["result"].get("result") is not True:
        raise _bad()
    words = doc.get("constant_result")
    if not isinstance(words, list) or len(words) != 1 or not isinstance(words[0], str) or not _TRON_WORD.fullmatch(words[0]):
        raise _bad()
    value = int(words[0], 16)
    if value > 1:
        raise _bad()
    return value == 1


def parse_tron_block(body: bytes) -> tuple[int, int]:
    doc = _json(body)
    try:
        raw = doc["block_header"]["raw_data"]
        return _count(raw["number"]), _count(raw["timestamp"])
    except (KeyError, TypeError):
        raise _bad() from None


def parse_height(body: bytes) -> int:
    text = body.decode("ascii", "replace").strip()
    if not _DIGITS.fullmatch(text):
        raise _bad()
    return int(text)


def parse_mempool(body: bytes, address: str) -> tuple[int, int, int]:
    doc = _json(body)
    if not isinstance(doc, dict) or doc.get("address") != address or not isinstance(doc.get("chain_stats"), dict):
        raise _bad()
    stats = doc["chain_stats"]
    try:
        return _count(stats["tx_count"]), _count(stats["funded_txo_count"]), _count(stats["spent_txo_count"])
    except KeyError:
        raise _bad() from None


def _age(now: dt.datetime, seconds: int) -> int:
    return max(0, int(now.timestamp()) - seconds)


class _Rpc:
    def __init__(self, caller: Caller, chain: str, url: str) -> None:
        self.caller, self.chain, self.url = caller, chain, url
        self.next_id = 0

    def __call__(self, method: str, params: list[Any]) -> object:
        self.next_id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}, separators=(",", ":")).encode()
        reply = self.caller.request(f"evm_rpc_{self.chain}", "POST", self.url, body=body, headers={"Content-Type": "application/json"})
        return rpc_result(reply, self.next_id)


def _host(url: str) -> str:
    return urlsplit(url).hostname or url


def read_evm(
    chain: str, key: str, freezes: list[Freeze], want_facts: bool, caller: Caller, now: dt.datetime, nodes: tuple[str, ...] | None = None
) -> ChainRead:
    """Freeze flags and facts for ``key`` (a lower-case 0x address) on one EVM chain, all at one block."""
    read = ChainRead()
    last = SourceError("network_error")
    found: tuple[_Rpc, int, int] | None = None
    for url in nodes or EVM_NODES[chain]:
        rpc = _Rpc(caller, chain, url)
        try:
            if decode_quantity(rpc("eth_chainId", [])) != CHAIN_IDS[chain]:
                raise _bad()
            number = decode_quantity(rpc("eth_blockNumber", []))
            header = rpc("eth_getBlockByNumber", [hex(number), False])
            if not isinstance(header, dict) or header.get("number") != hex(number):
                raise _bad()
            found = (rpc, number, decode_quantity(header.get("timestamp")))
            break
        except SourceError as err:
            last = err
    if found is None:
        url = (nodes or EVM_NODES[chain])[-1]
        read.freezes = [FreezeResult(f.source_id, "unavailable", None, None, last.reason, url) for f in freezes]
        if want_facts:
            read.facts = Facts("unavailable", chain, None, reason=last.reason, source_url=url)
        return read
    rpc, number, timestamp = found
    tag = hex(number)
    block = Block(chain, number, _age(now, timestamp), True)
    read.block_age_seconds = block.age_seconds
    for freeze in freezes:
        _add(read.sent_to, rpc.url)
        try:
            flag = decode_flag(rpc("eth_call", [{"to": freeze.contract, "data": call_data(freeze.selector, key)}, tag]))
            read.freezes.append(FreezeResult(freeze.source_id, "listed" if flag else "not_listed", flag, block, None, rpc.url))
        except SourceError as err:
            read.freezes.append(FreezeResult(freeze.source_id, "unavailable", None, None, err.reason, rpc.url))
    if want_facts:
        _add(read.sent_to, rpc.url)
        try:
            kind, size, delegate = code_kind(rpc("eth_getCode", [key, tag]))
            sent = decode_quantity(rpc("eth_getTransactionCount", [key, tag]))
            read.facts = Facts("observed", chain, number, kind, size, delegate, sent, source_url=rpc.url)
        except SourceError as err:
            read.facts = Facts("unavailable", chain, None, reason=err.reason, source_url=rpc.url)
    return read


def _add(hosts: list[str], url: str) -> None:
    host = _host(url)
    if host not in hosts:
        hosts.append(host)


def read_tron(address: str, caller: Caller, now: dt.datetime, nodes: tuple[str, ...] = TRON_NODES) -> ChainRead:
    """The USDT freeze flag of a Tron address: the first node that answers both the block header and the constant call is used."""
    read = ChainRead()
    freeze = FREEZES[("usdt_freeze", "tron")]
    parameter = tron_parameter(address)
    last = SourceError("network_error")
    for url in nodes:
        try:
            header = caller.request(
                "tron_rpc", "POST", url + "/wallet/getblock", body=b'{"detail":false}', headers={"Content-Type": "application/json"}
            )
            if header.status != 200:
                raise _bad()
            number, millis = parse_tron_block(header.body)
            body = {
                "owner_address": freeze.contract,
                "contract_address": freeze.contract,
                "function_selector": freeze.selector,
                "parameter": parameter,
                "visible": True,
            }
            _add(read.sent_to, url)
            reply = caller.request(
                "tron_rpc",
                "POST",
                url + "/wallet/triggerconstantcontract",
                body=json.dumps(body, separators=(",", ":")).encode(),
                headers={"Content-Type": "application/json"},
            )
            if reply.status != 200:
                raise _bad()
            flag = decode_tron_flag(reply.body)
        except SourceError as err:
            last = err
            continue
        block = Block("tron", number, _age(now, millis // 1000), False)
        read.freezes = [FreezeResult(freeze.source_id, "listed" if flag else "not_listed", flag, block, None, url)]
        read.block_age_seconds = block.age_seconds
        return read
    read.freezes = [FreezeResult(freeze.source_id, "unavailable", None, None, last.reason, nodes[-1])]
    return read


def read_bitcoin(address: str, caller: Caller, base: str = MEMPOOL) -> ChainRead:
    """Bitcoin facts from mempool.space: the tip height, then the address's confirmed counts."""
    read = ChainRead()
    try:
        tip = caller.request("mempool_space", "GET", base + "/api/blocks/tip/height")
        if tip.status != 200:
            raise _bad()
        height = parse_height(tip.body)
        _add(read.sent_to, base)
        reply = caller.request("mempool_space", "GET", f"{base}/api/address/{address}")
        if reply.status != 200:
            raise _bad()
        tx_count, funded, spent = parse_mempool(reply.body, address)
    except SourceError as err:
        read.facts = Facts("unavailable", "bitcoin", None, reason=err.reason, source_url=base)
        return read
    read.facts = Facts("observed", "bitcoin", height, tx_count=tx_count, funded_outputs=funded, spent_outputs=spent, source_url=base)
    return read
