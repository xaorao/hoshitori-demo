"""The verdict and every sentence of the answer (D3 4.3, 4.5 and 5), as pure functions of the check records.

The verdict words are ``flagged``, ``no_flag_found``, ``incomplete`` and ``None`` (no verdict). None of them, and no sentence here, means
safe: the only "safe" is the fixed absence warning and the only "safety" is "This is not a safety rating.". A stale copy never supports
absence; a stale listing still flags, with its age. Chain facts never enter the verdict. Every sentence is a fixed template filled
from data.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .errors import REASON_TEXT

NAMES = {
    "ofac_sdn": "OFAC SDN list",
    "usdt_freeze_ethereum": "USDT freeze",
    "usdc_freeze_ethereum": "USDC freeze",
    "usdt_freeze_tron": "USDT freeze",
}
TOKENS = {
    "usdt_freeze_ethereum": ("Tether USDT", "isBlackListed"),
    "usdc_freeze_ethereum": ("Circle USDC", "isBlacklisted"),
    "usdt_freeze_tron": ("Tether USDT", "isBlackListed"),
}
CHECK_NAMES = {"ofac_sdn": "OFAC SDN list", "usdt_freeze": "USDT freeze", "usdc_freeze": "USDC freeze", "chain_facts": "Chain facts"}
PRIMARY_CHECKS = ("ofac_sdn", "usdt_freeze", "usdc_freeze")
LABEL_CHAIN = {"ETH": "ethereum", "ARB": "arbitrum", "BSC": "bsc", "TRX": "tron", "XBT": "bitcoin"}
LABEL_FAMILIES = {
    "ETH": ("evm",),
    "ETC": ("evm",),
    "ARB": ("evm",),
    "BSC": ("evm",),
    "TRX": ("tron",),
    "XBT": ("bitcoin",),
    "BCH": ("bitcoin",),
    "BSV": ("bitcoin",),
}
STALE_REASON = "its copy is older than the age limit"
NOT_RATING = "This is not a safety rating."
ALWAYS = "This tool reports published lists and issuer freezes only. It does not analyse transactions, counterparties or behaviour."
ABSENCE = "Not being on a list is not evidence that an address is safe."
INJECTED = "Answers came from an injected source (recorded or test data), not a live fetch in this call; data_mode is therefore unknown."


@dataclass(frozen=True)
class CheckRecord:
    """One list or freeze check (D3 4.2): listed, not_listed or unavailable."""

    source_id: str
    kind: str
    state: str
    stale: bool
    list: dict[str, Any] | None
    block: dict[str, Any] | None
    entries: tuple[dict[str, Any], ...]
    raw_value: bool | None
    reason: str | None
    source_url: str


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def age_text(seconds: int) -> str:
    s = max(0, int(seconds))
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if s >= size:
            return _plural(s // size, unit)
    return _plural(s, "second")


def issued_text(days: int) -> str:
    return "less than 1 day" if days < 1 else _plural(days, "day")


def limit_text(hours: int) -> str:
    return _plural(hours, "hour")


def short_address(display: str) -> str:
    return f"{display[:6]}…{display[-4:]}"


def reason_text(reason: str) -> str:
    return STALE_REASON if reason == "stale" else REASON_TEXT[reason]


def decide(records: Sequence[CheckRecord]) -> tuple[str | None, str, dict[str, int]]:
    """(verdict, status, counts) of the in-scope list and freeze checks, exactly D3 4.3."""
    if not records:
        raise ValueError("no check is in scope")
    fresh = [r for r in records if r.state in ("listed", "not_listed") and not r.stale]
    verdict: str | None
    if any(r.state == "listed" for r in records):
        verdict = "flagged"
    elif not fresh:
        verdict = None
    elif len(fresh) == len(records):
        verdict = "no_flag_found"
    else:
        verdict = "incomplete"
    status = "ok" if len(fresh) == len(records) else ("unavailable" if verdict is None else "partial")
    counts = {
        "in_scope": len(records),
        "listed": sum(1 for r in records if r.state == "listed"),
        "not_listed": sum(1 for r in records if r.state == "not_listed"),
        "stale": sum(1 for r in records if r.stale),
        "unavailable": sum(1 for r in records if r.state == "unavailable"),
    }
    return verdict, status, counts


def _ofac_points(r: CheckRecord) -> list[str]:
    if r.state == "unavailable" or r.list is None:
        return [f"OFAC SDN list: not checked ({reason_text(r.reason or 'bad_response')})."]
    lst = r.list
    issued = issued_text(int(lst["issued_age_days"]))
    confirmed = age_text(int(lst["confirmed_current_age_seconds"]))
    limit = limit_text(int(lst["max_age_hours"]))
    if r.state == "listed":
        if r.stale:
            return [
                f"OFAC SDN list last confirmed current {confirmed} ago (limit {limit}): this address is listed on entry {e['entry_id']}; "
                "the entry may have changed since."
                for e in r.entries
            ]
        return [
            f"OFAC SDN list issued {issued} ago, confirmed current {confirmed} ago: this address is listed on entry {e['entry_id']} "
            f"({e['party_type']}; programmes {', '.join(e['programs']) or 'none published'}) "
            f"under 'Digital Currency Address - {e['label']}'."
            for e in r.entries
        ]
    if r.stale:
        return [f"OFAC SDN list last confirmed current {confirmed} ago, older than the {limit} limit: its absence result is not used."]
    return [
        f"OFAC SDN list issued {issued} ago, confirmed current {confirmed} ago, {lst['address_strings']} address strings: "
        "this address is not on it."
    ]


def check_points(record: CheckRecord, chain: str) -> list[str]:
    if record.source_id == "ofac_sdn":
        return _ofac_points(record)
    token, function = TOKENS[record.source_id]
    if record.state == "unavailable" or record.block is None:
        return [f"{token} contract on {chain}: not checked ({reason_text(record.reason or 'bad_response')})."]
    block = record.block["number"]
    if record.state == "listed":
        return [
            f"{token} contract on {chain}, block {block}: {function} is true for this address. "
            "The issuer has frozen it; the contract does not say why."
        ]
    return [f"{token} contract on {chain}, block {block}: {function} is false. This covers {token} on {chain} only."]


def facts_point(facts: dict[str, Any]) -> str | None:
    state, chain = facts["state"], facts["chain"]
    if state == "unavailable":
        return f"Chain facts on {chain}: not available ({reason_text(facts['reason'])})."
    if state != "observed":
        return None
    if chain == "bitcoin":
        return (
            f"On Bitcoin at height {facts['height']}: {_plural(facts['tx_count'], 'confirmed transaction')}; "
            f"{_plural(facts['funded_outputs'], 'output')} received, {facts['spent_outputs']} spent."
        )
    kind = facts["code_kind"]
    if kind == "no_code":
        what = "no code at this address"
    elif kind == "eip7702_delegation":
        what = f"an EIP-7702 delegation to {facts['delegate']}"
    else:
        what = f"contract code, {facts['code_bytes']} bytes"
    return f"On {chain} at block {facts['block']}: {what}; {_plural(facts['sent_tx_count'], 'transaction')} sent from this address."


def not_covered_point(item: dict[str, str], chain: str) -> str:
    name = CHECK_NAMES[item["check"]]
    if item["reason"] == "not_requested":
        return f"{name}: not requested."
    return f"{name}: not covered on {chain}."


def _missing(records: Sequence[CheckRecord]) -> list[CheckRecord]:
    return [r for r in records if r.state == "unavailable" or (r.state == "not_listed" and r.stale)]


def headline(short: str, chain: str, records: Sequence[CheckRecord], not_covered: Sequence[dict[str, str]]) -> str:
    verdict, _, _ = decide(records)
    missing = _missing(records)
    missing_names = ", ".join(NAMES[r.source_id] for r in missing)
    if verdict == "flagged":
        listed = [r for r in records if r.state == "listed"]
        answered = [r for r in records if r.state != "unavailable"]
        names = ", ".join(NAMES[r.source_id] for r in listed)
        text = f"{short} on {chain}: flagged by {len(listed)} of {len(answered)} sources that answered ({names})."
        if missing:
            text += f" {len(missing)} could not be used ({missing_names})."
    elif verdict == "no_flag_found":
        names = ", ".join(NAMES[r.source_id] for r in records)
        text = f"{short} on {chain}: no flag found in {len(records)} of {len(records)} sources checked ({names}). {NOT_RATING}"
    elif verdict == "incomplete":
        found = sum(1 for r in records if r.state == "not_listed" and not r.stale)
        text = (
            f"{short} on {chain}: no flag found in {_plural(found, 'source')}; {len(missing)} could not be used ({missing_names}). "
            f"{NOT_RATING}"
        )
    else:
        reasons = "; ".join(f"{NAMES[r.source_id]}: {reason_text('stale' if r.stale else (r.reason or 'bad_response'))}" for r in records)
        text = f"{short} on {chain}: no source could be checked ({reasons}). No verdict."
    absent = [CHECK_NAMES[x["check"]] for x in not_covered if x["check"] in PRIMARY_CHECKS and x["reason"] == "not_available_on_chain"]
    unasked = [CHECK_NAMES[x["check"]] for x in not_covered if x["check"] in PRIMARY_CHECKS and x["reason"] == "not_requested"]
    if absent:
        text += f" Not covered on {chain}: {', '.join(absent)}."
    if unasked:
        text += f" Not requested: {', '.join(unasked)}."
    return text


def warnings_for(
    *,
    chain: str,
    records: Sequence[CheckRecord],
    facts: dict[str, Any] | None,
    checksum: str,
    sent_to: Sequence[str],
    data_mode: str,
    local_copy_age_seconds: int | None,
) -> list[str]:
    out = [ALWAYS]
    if any(r.state == "not_listed" for r in records):
        out.append(ABSENCE)
    listings = [e for r in records if r.source_id == "ofac_sdn" and r.state == "listed" for e in r.entries]
    labels: list[str] = []
    for entry in listings:
        if LABEL_CHAIN.get(entry["label"]) != chain and entry["label"] not in labels:
            labels.append(entry["label"])
    out += [
        f"OFAC listed this string under '{label}'. The label names an asset, not every chain where the same string is valid."
        for label in labels
    ]
    mismatched: list[str] = []
    for entry in listings:
        allowed = LABEL_FAMILIES.get(entry["label"])
        if allowed is not None and entry.get("family") not in allowed and entry["label"] not in mismatched:
            mismatched.append(entry["label"])
    out += [f"OFAC published this string under a label whose address format does not match it ({label})." for label in mismatched]
    if listings and facts is not None and facts.get("state") == "observed" and facts.get("code_kind") == "contract":
        out.append(
            f"This address holds contract code on {chain}. "
            "A contract at the same address on another chain can be controlled by someone else."
        )
    if checksum in ("none_lowercase", "none_uppercase"):
        form = "all lower case" if checksum == "none_lowercase" else "all upper case"
        out.append(f"The address has no checksum ({form}); a typing error cannot be detected.")
    for r in records:
        if r.stale and r.list is not None:
            out.append(
                f"{NAMES[r.source_id]} was last confirmed current {age_text(int(r.list['confirmed_current_age_seconds']))} ago, "
                f"older than the {limit_text(int(r.list['max_age_hours']))} limit."
            )
    if sent_to:
        out.append(f"This call sent the address to: {', '.join(sent_to)}.")
    if data_mode == "mixed" and local_copy_age_seconds is not None:
        out.append(f"The OFAC list came from the local copy confirmed current {age_text(local_copy_age_seconds)} ago.")
    if data_mode == "unknown":
        out.append(INJECTED)
    return out
