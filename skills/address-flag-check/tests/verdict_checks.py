"""Checks of ``address_flag_check.verdict``, runnable against a mutant: the verdict rule of D3 4.3 over every assignment of states, and every
closed sentence of D3 4.5 written out by hand, with the absence rules of D3 5 (no word that means safe; the only "safe" and "safety" are
the two fixed sentences; a stale copy never supports absence; a stale listing still flags).
"""

from __future__ import annotations

import dataclasses
import itertools
import re
import types
from typing import Any

from tests.checks_support import Case

EVM = "0x000000000000000000000000000000000000dEaD"
SHORT = "0x0000…dEaD"
ALWAYS = "This tool reports published lists and issuer freezes only. It does not analyse transactions, counterparties or behaviour."
ABSENCE = "Not being on a list is not evidence that an address is safe."
NOT_RATING = "This is not a safety rating."
BANNED = re.compile(
    r"\b(safe|safety|safely|clean|low[- ]risk|trusted|trustworthy|legit\w*|verified|approved|secure|harmless|fine|ok)\b", re.IGNORECASE
)


def _list(stale: bool = False) -> dict[str, Any]:
    return {
        "name": "OFAC SDN list (advanced XML)",
        "issued": "2026-10-28",
        "issued_age_days": 4,
        "confirmed_current_age_seconds": 30 * 3600 if stale else 3600,
        "max_age_hours": 24,
        "sha256": "d9ba0687",
        "address_strings": 1058,
    }


ENTRY = {
    "entry_id": "47635",
    "party_type": "entity",
    "programs": ["SDGT"],
    "label": "ETH",
    "published_string": "0xE950",
    "reliability": "Confirmed",
    "format_ok": True,
}
ENTRY2 = {
    "entry_id": "9002",
    "party_type": "individual",
    "programs": ["CYBER2", "IFSR"],
    "label": "USDT",
    "published_string": "0xE950",
    "reliability": "Unknown",
    "format_ok": True,
}
BLOCK = {"chain": "ethereum", "number": 26318704, "age_seconds": 13, "pinned": True}


def ofac(
    mod: types.ModuleType, state: str, stale: bool = False, entries: tuple[dict[str, Any], ...] = (ENTRY,), reason: str | None = None
) -> Any:
    listed = state == "listed"
    return mod.CheckRecord(
        source_id="ofac_sdn",
        kind="sanctions_list",
        state=state,
        stale=stale,
        list=None if state == "unavailable" else _list(stale),
        block=None,
        entries=entries if listed else (),
        raw_value=None,
        reason=reason if state == "unavailable" else None,
        source_url="https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.ZIP",
    )


def freeze(mod: types.ModuleType, source_id: str, state: str, reason: str | None = None, block: dict[str, Any] | None = None) -> Any:
    answered = state != "unavailable"
    return mod.CheckRecord(
        source_id=source_id,
        kind="issuer_freeze",
        state=state,
        stale=False,
        list=None,
        block=(block or BLOCK) if answered else None,
        entries=(),
        raw_value=(state == "listed") if answered else None,
        reason=None if answered else reason,
        source_url="https://ethereum-rpc.publicnode.com",
    )


def _rule(states: list[tuple[str, bool]]) -> tuple[str | None, str]:
    """D3 4.3 restated independently."""
    fresh = [s for s, stale in states if s in ("listed", "not_listed") and not stale]
    if any(s == "listed" for s, _ in states):
        verdict: str | None = "flagged"
    elif not fresh:
        verdict = None
    elif len(fresh) == len(states):
        verdict = "no_flag_found"
    else:
        verdict = "incomplete"
    status = "ok" if len(fresh) == len(states) else ("unavailable" if verdict is None else "partial")
    return verdict, status


def check_rule(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    options = [("listed", False), ("listed", True), ("not_listed", False), ("not_listed", True), ("unavailable", False)]
    ids = ("ofac_sdn", "usdt_freeze_ethereum", "usdc_freeze_ethereum")
    for n in (1, 2, 3):
        for combo in itertools.product(options, repeat=n):
            records = [
                mod.CheckRecord(
                    ids[i],
                    "sanctions_list" if i == 0 else "issuer_freeze",
                    s,
                    stale,
                    _list(stale) if s != "unavailable" else None,
                    None,
                    (ENTRY,) if s == "listed" else (),
                    None,
                    None if s != "unavailable" else "timeout",
                    "x",
                )
                for i, (s, stale) in enumerate(combo)
            ]
            got = mod.decide(records)
            want = _rule(list(combo))
            c.eq(f"verdict and status for {combo}", (got[0], got[1]), want)
            counts = {
                "in_scope": n,
                "listed": sum(1 for s, _ in combo if s == "listed"),
                "not_listed": sum(1 for s, _ in combo if s == "not_listed"),
                "stale": sum(1 for _, st in combo if st),
                "unavailable": sum(1 for s, _ in combo if s == "unavailable"),
            }
            c.eq(f"counts for {combo}", got[2], counts)
    c.raises("no check in scope", lambda: mod.decide([]), ValueError, "no check is in scope")
    c.eq(
        "all unavailable gives no verdict",
        mod.decide([ofac(mod, "unavailable", reason="list_not_loaded"), freeze(mod, "usdt_freeze_ethereum", "unavailable", "timeout")])[:2],
        (None, "unavailable"),
    )
    c.eq(
        "a listing wins over an unavailable source",
        mod.decide([ofac(mod, "listed"), freeze(mod, "usdt_freeze_ethereum", "unavailable", "timeout")])[:2],
        ("flagged", "partial"),
    )
    c.eq("a stale listing still flags", mod.decide([ofac(mod, "listed", stale=True)])[:2], ("flagged", "partial"))
    c.eq(
        "a stale absence never supports no flag",
        mod.decide([ofac(mod, "not_listed", stale=True), freeze(mod, "usdt_freeze_ethereum", "not_listed")])[:2],
        ("incomplete", "partial"),
    )
    return c.bad


def check_text_helpers(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for seconds, text in (
        (0, "0 seconds"),
        (1, "1 second"),
        (59, "59 seconds"),
        (60, "1 minute"),
        (119, "1 minute"),
        (120, "2 minutes"),
        (3599, "59 minutes"),
        (3600, "1 hour"),
        (7200, "2 hours"),
        (86399, "23 hours"),
        (86400, "1 day"),
        (4 * 86400 + 5, "4 days"),
        (-5, "0 seconds"),
    ):
        c.eq(f"age of {seconds} s", mod.age_text(seconds), text)
    for days, text in ((0, "less than 1 day"), (1, "1 day"), (4, "4 days"), (-1, "less than 1 day")):
        c.eq(f"issue age of {days} days", mod.issued_text(days), text)
    c.eq("limit of 1 hour", mod.limit_text(1), "1 hour")
    c.eq("limit of 24 hours", mod.limit_text(24), "24 hours")
    c.eq("short EVM address", mod.short_address(EVM), SHORT)
    c.eq("short Tron address", mod.short_address("T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"), "T9yD14…uWwb")
    c.eq("reason texts", mod.reason_text("cooling_down"), "the source is in a cool-down after rate limiting")
    c.eq("the stale reason text", mod.reason_text("stale"), "its copy is older than the age limit")
    return c.bad


def check_points(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    p = mod.check_points
    c.eq(
        "OFAC listed",
        p(ofac(mod, "listed", entries=(ENTRY, ENTRY2)), "ethereum"),
        [
            "OFAC SDN list issued 4 days ago, confirmed current 1 hour ago: this address is listed on entry 47635 (entity; programmes SDGT) under 'Digital Currency Address - ETH'.",
            "OFAC SDN list issued 4 days ago, confirmed current 1 hour ago: this address is listed on entry 9002 (individual; programmes CYBER2, IFSR) under 'Digital Currency Address - USDT'.",
        ],
    )
    no_programmes = {**ENTRY, "programs": []}
    c.eq(
        "OFAC listed on an entry with no programme",
        p(ofac(mod, "listed", entries=(no_programmes,)), "ethereum"),
        [
            "OFAC SDN list issued 4 days ago, confirmed current 1 hour ago: this address is listed on entry 47635 (entity; programmes none published) under 'Digital Currency Address - ETH'."
        ],
    )
    c.eq(
        "OFAC listed, stale",
        p(ofac(mod, "listed", stale=True), "ethereum"),
        [
            "OFAC SDN list last confirmed current 1 day ago (limit 24 hours): this address is listed on entry 47635; the entry may have changed since."
        ],
    )
    c.eq(
        "OFAC not listed",
        p(ofac(mod, "not_listed"), "ethereum"),
        ["OFAC SDN list issued 4 days ago, confirmed current 1 hour ago, 1058 address strings: this address is not on it."],
    )
    c.eq(
        "OFAC not listed, stale",
        p(ofac(mod, "not_listed", stale=True), "ethereum"),
        ["OFAC SDN list last confirmed current 1 day ago, older than the 24 hours limit: its absence result is not used."],
    )
    c.eq(
        "OFAC unavailable",
        p(ofac(mod, "unavailable", reason="list_not_loaded"), "ethereum"),
        ["OFAC SDN list: not checked (no copy of the list has been loaded)."],
    )
    c.eq(
        "USDT frozen",
        p(freeze(mod, "usdt_freeze_ethereum", "listed"), "ethereum"),
        [
            "Tether USDT contract on ethereum, block 26318704: isBlackListed is true for this address. The issuer has frozen it; the contract does not say why."
        ],
    )
    c.eq(
        "USDC not frozen",
        p(freeze(mod, "usdc_freeze_ethereum", "not_listed"), "ethereum"),
        ["Circle USDC contract on ethereum, block 26318704: isBlacklisted is false. This covers Circle USDC on ethereum only."],
    )
    tron_block = {"chain": "tron", "number": 87616025, "age_seconds": 2, "pinned": False}
    c.eq(
        "Tron USDT not frozen",
        p(freeze(mod, "usdt_freeze_tron", "not_listed", block=tron_block), "tron"),
        ["Tether USDT contract on tron, block 87616025: isBlackListed is false. This covers Tether USDT on tron only."],
    )
    c.eq(
        "USDT unavailable",
        p(freeze(mod, "usdt_freeze_tron", "unavailable", "rate_limited"), "tron"),
        ["Tether USDT contract on tron: not checked (the source is rate limiting requests)."],
    )
    f = mod.facts_point
    c.eq(
        "EVM facts, no code",
        f(
            {
                "state": "observed",
                "chain": "ethereum",
                "block": 26318704,
                "code_kind": "no_code",
                "code_bytes": 0,
                "delegate": None,
                "sent_tx_count": 0,
            }
        ),
        "On ethereum at block 26318704: no code at this address; 0 transactions sent from this address.",
    )
    c.eq(
        "EVM facts, contract",
        f(
            {
                "state": "observed",
                "chain": "base",
                "block": 5,
                "code_kind": "contract",
                "code_bytes": 3124,
                "delegate": None,
                "sent_tx_count": 1,
            }
        ),
        "On base at block 5: contract code, 3124 bytes; 1 transaction sent from this address.",
    )
    c.eq(
        "EVM facts, delegation",
        f(
            {
                "state": "observed",
                "chain": "ethereum",
                "block": 7,
                "code_kind": "eip7702_delegation",
                "code_bytes": 23,
                "delegate": "0x63c0c19a282a1B52b07dD5a65b58948A07DAE32B",
                "sent_tx_count": 12,
            }
        ),
        "On ethereum at block 7: an EIP-7702 delegation to 0x63c0c19a282a1B52b07dD5a65b58948A07DAE32B; 12 transactions sent from this address.",
    )
    c.eq(
        "Bitcoin facts",
        f({"state": "observed", "chain": "bitcoin", "height": 974104, "tx_count": 5672, "funded_outputs": 5854, "spent_outputs": 0}),
        "On Bitcoin at height 974104: 5672 confirmed transactions; 5854 outputs received, 0 spent.",
    )
    c.eq(
        "Bitcoin facts, one of each",
        f({"state": "observed", "chain": "bitcoin", "height": 1, "tx_count": 1, "funded_outputs": 1, "spent_outputs": 1}),
        "On Bitcoin at height 1: 1 confirmed transaction; 1 output received, 1 spent.",
    )
    c.eq(
        "facts unavailable",
        f({"state": "unavailable", "chain": "polygon", "reason": "server_error"}),
        "Chain facts on polygon: not available (the source returned a server error).",
    )
    c.eq("facts not requested", f({"state": "not_requested", "chain": "ethereum"}), None)
    n = mod.not_covered_point
    c.eq("not covered", n({"check": "usdc_freeze", "reason": "not_available_on_chain"}, "tron"), "USDC freeze: not covered on tron.")
    c.eq("not requested", n({"check": "chain_facts", "reason": "not_requested"}, "ethereum"), "Chain facts: not requested.")
    return c.bad


def check_headlines(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    h = mod.headline
    three = [ofac(mod, "not_listed"), freeze(mod, "usdt_freeze_ethereum", "not_listed"), freeze(mod, "usdc_freeze_ethereum", "not_listed")]
    c.eq(
        "no flag found",
        h(SHORT, "ethereum", three, []),
        f"{SHORT} on ethereum: no flag found in 3 of 3 sources checked (OFAC SDN list, USDT freeze, USDC freeze). {NOT_RATING}",
    )
    c.eq(
        "flagged",
        h(
            SHORT,
            "ethereum",
            [ofac(mod, "listed"), freeze(mod, "usdt_freeze_ethereum", "listed"), freeze(mod, "usdc_freeze_ethereum", "not_listed")],
            [],
        ),
        f"{SHORT} on ethereum: flagged by 2 of 3 sources that answered (OFAC SDN list, USDT freeze).",
    )
    c.eq(
        "flagged with a source that could not be used",
        h(
            SHORT,
            "ethereum",
            [
                ofac(mod, "listed"),
                freeze(mod, "usdt_freeze_ethereum", "unavailable", "timeout"),
                freeze(mod, "usdc_freeze_ethereum", "listed"),
            ],
            [],
        ),
        f"{SHORT} on ethereum: flagged by 2 of 2 sources that answered (OFAC SDN list, USDC freeze). 1 could not be used (USDT freeze).",
    )
    c.eq(
        "incomplete",
        h(
            SHORT,
            "ethereum",
            [
                ofac(mod, "not_listed", stale=True),
                freeze(mod, "usdt_freeze_ethereum", "not_listed"),
                freeze(mod, "usdc_freeze_ethereum", "unavailable", "timeout"),
            ],
            [],
        ),
        f"{SHORT} on ethereum: no flag found in 1 source; 2 could not be used (OFAC SDN list, USDC freeze). {NOT_RATING}",
    )
    c.eq(
        "no verdict",
        h(
            SHORT,
            "ethereum",
            [ofac(mod, "unavailable", reason="list_not_loaded"), freeze(mod, "usdt_freeze_ethereum", "unavailable", "network_error")],
            [],
        ),
        f"{SHORT} on ethereum: no source could be checked (OFAC SDN list: no copy of the list has been loaded; USDT freeze: the source could not be reached). No verdict.",
    )
    c.eq(
        "no verdict with a stale copy",
        h(SHORT, "bitcoin", [ofac(mod, "not_listed", stale=True)], []),
        f"{SHORT} on bitcoin: no source could be checked (OFAC SDN list: its copy is older than the age limit). No verdict.",
    )
    c.eq(
        "not covered and not requested are named",
        h(
            "1Bitco…59kuE"[:0] + "1Bitco…kuE",
            "bitcoin",
            [ofac(mod, "not_listed")],
            [
                {"check": "usdt_freeze", "reason": "not_available_on_chain"},
                {"check": "usdc_freeze", "reason": "not_available_on_chain"},
                {"check": "chain_facts", "reason": "not_requested"},
            ],
        ),
        f"1Bitco…kuE on bitcoin: no flag found in 1 of 1 sources checked (OFAC SDN list). {NOT_RATING} Not covered on bitcoin: USDT freeze, USDC freeze.",
    )
    c.eq(
        "a primary check that was not requested is named",
        h(
            SHORT,
            "ethereum",
            [ofac(mod, "not_listed")],
            [{"check": "usdt_freeze", "reason": "not_requested"}, {"check": "usdc_freeze", "reason": "not_requested"}],
        ),
        f"{SHORT} on ethereum: no flag found in 1 of 1 sources checked (OFAC SDN list). {NOT_RATING} Not requested: USDT freeze, USDC freeze.",
    )
    c.true(
        "the no-verdict headline never says no flag",
        "no flag" not in h(SHORT, "ethereum", [ofac(mod, "unavailable", reason="timeout")], []).lower(),
    )
    return c.bad


def check_warnings(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = mod.warnings_for
    facts_contract = {
        "state": "observed",
        "chain": "base",
        "block": 5,
        "code_kind": "contract",
        "code_bytes": 3124,
        "delegate": None,
        "sent_tx_count": 1,
    }
    c.eq(
        "a listing on another chain, filed under a label whose format differs, holding code, sent away, from the local copy",
        w(
            chain="base",
            records=[
                ofac(mod, "listed", entries=({**ENTRY, "label": "XBT", "family": "evm"}, {**ENTRY2, "label": "ETH", "family": "evm"}))
            ],
            facts=facts_contract,
            checksum="none_lowercase",
            sent_to=["base-rpc.publicnode.com"],
            data_mode="mixed",
            local_copy_age_seconds=3600,
        ),
        [
            ALWAYS,
            "OFAC listed this string under 'XBT'. The label names an asset, not every chain where the same string is valid.",
            "OFAC listed this string under 'ETH'. The label names an asset, not every chain where the same string is valid.",
            "OFAC published this string under a label whose address format does not match it (XBT).",
            "This address holds contract code on base. A contract at the same address on another chain can be controlled by someone else.",
            "The address has no checksum (all lower case); a typing error cannot be detected.",
            "This call sent the address to: base-rpc.publicnode.com.",
            "The OFAC list came from the local copy confirmed current 1 hour ago.",
        ],
    )
    c.eq(
        "an absence, a stale copy, upper case, injected data",
        w(
            chain="ethereum",
            records=[ofac(mod, "not_listed", stale=True), freeze(mod, "usdt_freeze_ethereum", "not_listed")],
            facts={"state": "not_requested", "chain": "ethereum"},
            checksum="none_uppercase",
            sent_to=[],
            data_mode="unknown",
            local_copy_age_seconds=30 * 3600,
        ),
        [
            ALWAYS,
            ABSENCE,
            "The address has no checksum (all upper case); a typing error cannot be detected.",
            "OFAC SDN list was last confirmed current 1 day ago, older than the 24 hours limit.",
            "Answers came from an injected source (recorded or test data), not a live fetch in this call; data_mode is therefore unknown.",
        ],
    )
    c.eq(
        "a listing on its own chain needs no label warning; code without a listing needs no contract warning",
        w(
            chain="ethereum",
            records=[ofac(mod, "listed", entries=({**ENTRY, "family": "evm"},)), freeze(mod, "usdt_freeze_ethereum", "not_listed")],
            facts=facts_contract,
            checksum="eip55_valid",
            sent_to=["ethereum-rpc.publicnode.com"],
            data_mode="live",
            local_copy_age_seconds=None,
        )[:3],
        [
            ALWAYS,
            ABSENCE,
            "This address holds contract code on ethereum. A contract at the same address on another chain can be controlled by someone else.",
        ],
    )
    c.eq(
        "a freeze alone with code: no contract warning",
        w(
            chain="ethereum",
            records=[freeze(mod, "usdt_freeze_ethereum", "listed")],
            facts=facts_contract,
            checksum="eip55_valid",
            sent_to=[],
            data_mode="live",
            local_copy_age_seconds=None,
        ),
        [ALWAYS],
    )
    for label, family in (("TRX", "tron"), ("XBT", "bitcoin"), ("BCH", "bitcoin"), ("ETH", "evm"), ("ARB", "evm"), ("BSC", "evm")):
        got = w(
            chain="ethereum",
            records=[ofac(mod, "listed", entries=({**ENTRY, "label": label, "family": family},))],
            facts=None,
            checksum="eip55_valid",
            sent_to=[],
            data_mode="live",
            local_copy_age_seconds=None,
        )
        c.eq(f"label {label} with a {family} string: no format warning", any("does not match" in x for x in got), False)
    for label, family in (("TRX", "evm"), ("ETH", "tron"), ("XBT", "evm"), ("ARB", "bitcoin")):
        got = w(
            chain="ethereum",
            records=[ofac(mod, "listed", entries=({**ENTRY, "label": label, "family": family},))],
            facts=None,
            checksum="eip55_valid",
            sent_to=[],
            data_mode="live",
            local_copy_age_seconds=None,
        )
        c.eq(f"label {label} with a {family} string: a format warning", any("does not match" in x for x in got), True)
    for label, chain in (("ETH", "ethereum"), ("ARB", "arbitrum"), ("BSC", "bsc"), ("TRX", "tron"), ("XBT", "bitcoin")):
        got = w(
            chain=chain,
            records=[ofac(mod, "listed", entries=({**ENTRY, "label": label, "family": "evm"},))],
            facts=None,
            checksum="eip55_valid",
            sent_to=[],
            data_mode="live",
            local_copy_age_seconds=None,
        )
        c.eq(f"label {label} on {chain}: no label warning", any("The label names an asset" in x for x in got), False)
    return c.bad


def check_no_safe_words(mod: types.ModuleType) -> list[str]:
    """Every sentence the module can produce, scanned: no word that means safe, except inside the two fixed sentences."""
    c = Case(mod)
    texts: list[str] = []
    for state, stale in (("listed", False), ("listed", True), ("not_listed", False), ("not_listed", True), ("unavailable", False)):
        rec = ofac(mod, state, stale, reason="timeout")
        texts += mod.check_points(rec, "ethereum")
        texts.append(mod.headline(SHORT, "ethereum", [rec], [{"check": "usdt_freeze", "reason": "not_requested"}]))
    for sid in ("usdt_freeze_ethereum", "usdc_freeze_ethereum", "usdt_freeze_tron"):
        for state in ("listed", "not_listed", "unavailable"):
            rec = freeze(mod, sid, state, "bad_response")
            texts += mod.check_points(rec, "ethereum")
            texts.append(mod.headline(SHORT, "ethereum", [ofac(mod, "not_listed"), rec], []))
    for reason in mod.REASON_TEXT:
        texts += mod.check_points(ofac(mod, "unavailable", reason=reason), "ethereum")
    texts.append(
        mod.facts_point(
            {
                "state": "observed",
                "chain": "ethereum",
                "block": 1,
                "code_kind": "contract",
                "code_bytes": 2,
                "delegate": None,
                "sent_tx_count": 0,
            }
        )
        or ""
    )
    texts.append(
        mod.facts_point({"state": "observed", "chain": "bitcoin", "height": 1, "tx_count": 0, "funded_outputs": 0, "spent_outputs": 0})
        or ""
    )
    texts += mod.warnings_for(
        chain="base",
        records=[
            ofac(mod, "listed", True, entries=({**ENTRY, "label": "TRX", "family": "evm"},)),
            freeze(mod, "usdt_freeze_ethereum", "not_listed"),
        ],
        facts={
            "state": "observed",
            "chain": "base",
            "block": 5,
            "code_kind": "contract",
            "code_bytes": 3,
            "delegate": None,
            "sent_tx_count": 1,
        },
        checksum="none_lowercase",
        sent_to=["h"],
        data_mode="unknown",
        local_copy_age_seconds=100000,
    )
    texts += mod.warnings_for(
        chain="base",
        records=[ofac(mod, "not_listed")],
        facts=None,
        checksum="eip55_valid",
        sent_to=[],
        data_mode="mixed",
        local_copy_age_seconds=5,
    )
    c.true("enough sentences were produced to mean something", len(texts) > 40)
    for text in texts:
        rest = text.replace(ABSENCE, "").replace(NOT_RATING, "")
        found = BANNED.findall(rest)
        if found:
            c.bad.append(f"a sentence uses {found}: {text!r}")
    c.true("the absence warning is the only use of 'safe'", ABSENCE in texts)
    return c.bad


OFAC_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.ZIP"


def check_edges(mod: types.ModuleType) -> list[str]:
    """The state of a record decides what is said, whatever else the record carries; every label's format rule; the remaining names."""
    c = Case(mod)
    record = ofac(mod, "not_listed")
    c.raises("a check record cannot be changed", lambda: setattr(record, "state", "listed"), dataclasses.FrozenInstanceError)
    c.eq(
        "an unavailable OFAC record that still carries a list is not checked",
        mod.check_points(
            mod.CheckRecord("ofac_sdn", "sanctions_list", "unavailable", False, _list(), None, (), None, "network_error", OFAC_URL),
            "ethereum",
        ),
        ["OFAC SDN list: not checked (the source could not be reached)."],
    )
    c.eq(
        "an unavailable freeze record that still carries a block is not checked",
        mod.check_points(
            mod.CheckRecord("usdt_freeze_ethereum", "issuer_freeze", "unavailable", False, None, BLOCK, (), None, "network_error", "u"),
            "ethereum",
        ),
        ["Tether USDT contract on ethereum: not checked (the source could not be reached)."],
    )
    c.eq(
        "incomplete with two sources that answered",
        mod.headline(
            SHORT,
            "ethereum",
            [
                ofac(mod, "not_listed"),
                freeze(mod, "usdt_freeze_ethereum", "not_listed"),
                freeze(mod, "usdc_freeze_ethereum", "unavailable", "timeout"),
            ],
            [],
        ),
        f"{SHORT} on ethereum: no flag found in 2 sources; 1 could not be used (USDC freeze). {NOT_RATING}",
    )
    c.eq(
        "the Tron freeze is named USDT freeze",
        mod.headline(SHORT, "tron", [freeze(mod, "usdt_freeze_tron", "not_listed")], []),
        f"{SHORT} on tron: no flag found in 1 of 1 sources checked (USDT freeze). {NOT_RATING}",
    )
    c.eq(
        "the OFAC list not requested is named",
        mod.headline(
            SHORT, "ethereum", [freeze(mod, "usdt_freeze_ethereum", "not_listed")], [{"check": "ofac_sdn", "reason": "not_requested"}]
        ),
        f"{SHORT} on ethereum: no flag found in 1 of 1 sources checked (USDT freeze). {NOT_RATING} Not requested: OFAC SDN list.",
    )
    c.eq(
        "the OFAC point when not requested",
        mod.not_covered_point({"check": "ofac_sdn", "reason": "not_requested"}, "ethereum"),
        "OFAC SDN list: not requested.",
    )

    def warn(records: list[Any]) -> list[str]:
        out: list[str] = mod.warnings_for(
            chain="ethereum", records=records, facts=None, checksum="eip55_valid", sent_to=[], data_mode="live", local_copy_age_seconds=None
        )
        return out

    xbt = {**ENTRY, "label": "XBT", "family": "evm"}
    c.eq(
        "entries on records that are not OFAC listings give no label warning",
        warn(
            [
                mod.CheckRecord("ofac_sdn", "sanctions_list", "not_listed", False, _list(), None, (xbt,), None, None, OFAC_URL),
                mod.CheckRecord("usdt_freeze_ethereum", "issuer_freeze", "listed", False, None, BLOCK, (xbt,), True, None, "u"),
            ]
        ),
        [ALWAYS, ABSENCE],
    )
    mismatch = "OFAC published this string under a label whose address format does not match it"
    for label, family in (
        ("ETH", "evm"),
        ("ETC", "evm"),
        ("ARB", "evm"),
        ("BSC", "evm"),
        ("TRX", "tron"),
        ("XBT", "bitcoin"),
        ("BCH", "bitcoin"),
        ("BSV", "bitcoin"),
    ):
        for given, want in ((family, []), ("other", [f"{mismatch} ({label})."])):
            got = [x for x in warn([ofac(mod, "listed", entries=({**ENTRY, "label": label, "family": given},))]) if x.startswith(mismatch)]
            c.eq(f"{label} listing, {given} family", got, want)
    got = [x for x in warn([ofac(mod, "listed", entries=({**ENTRY, "label": "USDT", "family": "other"},))]) if x.startswith(mismatch)]
    c.eq("a multi-chain label (USDT) has no format rule", got, [])
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    for check in (check_rule, check_text_helpers, check_points, check_headlines, check_warnings, check_no_safe_words, check_edges):
        try:
            out.extend(check(mod))
        except Exception as exc:
            out.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    return out
