"""The skill: validate, read the in-scope sources, decide, and return the RYO-style envelope (D3 4 and 7).

* Input is refused before any network call.
* The OFAC check reads the local copy (confirmed against OFAC at most once an hour; the address never leaves this machine for it).
* Freeze flags and chain facts are read from keyless public nodes; ``data.sent_to`` names every host that received the address.
* One source failing never changes another's state. With every source down there is no verdict, and the attempts are all listed.
* ``status`` and ``verdict`` come from ``verdict.decide``; ``summary`` and ``warnings`` are generated from the data.
* ``data_mode`` is earned: ``live`` when every answer was fetched in this call, ``mixed`` when the OFAC list came from the local copy, and
  ``unknown`` when the transport was injected (recorded or test data).
"""

from __future__ import annotations

import datetime as dt
import pathlib
import time
from collections.abc import Callable
from typing import Any

from . import list_store
from .normalize import EVM_CHAINS, Address
from .onchain import FREEZES, ChainRead, Facts, read_bitcoin, read_evm, read_tron
from .schema import SOURCES_TOOL_NAME, TOOL_NAME, validate_input, validate_sources_input
from .sources import REGISTRY
from .transport import Caller, Transport
from .trust import default_transport
from .verdict import (
    CheckRecord,
    age_text,
    check_points,
    decide,
    facts_point,
    headline,
    issued_text,
    not_covered_point,
    short_address,
    warnings_for,
)

SCHEMA_VERSION = "1.0"
TOTAL_BUDGET_SECONDS = 25.0
LIST_NAME = "OFAC SDN list (advanced XML)"
FAMILY = {**dict.fromkeys(EVM_CHAINS, "evm"), "tron": "tron", "bitcoin": "bitcoin"}
NO_SCAM_SOURCE = "No scam-report source is used: the ones found need a key or an accepted agreement."
NO_COPY = "No copy of the OFAC list has been loaded on this machine; run address-flag-check update-list, or make one check."
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _issued_days(now: dt.datetime, issued: str) -> int:
    return max(0, (now.date() - dt.date.fromisoformat(issued)).days)


def _confirmed_age(now: dt.datetime, stored: list_store.StoredList) -> int:
    return max(0, int((now - list_store.parse_stamp(stored.confirmed_at)).total_seconds()))


def _ofac_record(state: list_store.ListState, address: Address, now: dt.datetime, max_age_hours: int) -> CheckRecord:
    url = list_store.DEFAULT_URL
    stored = state.stored
    if stored is None:
        reason = state.error.reason if state.error is not None else "list_not_loaded"
        return CheckRecord("ofac_sdn", "sanctions_list", "unavailable", False, None, None, (), None, reason, url)
    age = _confirmed_age(now, stored)
    listing = {
        "name": LIST_NAME,
        "issued": stored.index.date_of_issue,
        "issued_age_days": _issued_days(now, stored.index.date_of_issue),
        "confirmed_current_age_seconds": age,
        "max_age_hours": max_age_hours,
        "sha256": stored.zip_sha256,
        "address_strings": stored.index.address_strings,
        "copy": state.action,
    }
    family = FAMILY[address.chain]
    entries = tuple(
        {
            "entry_id": m.entry_id,
            "party_type": m.party_type,
            "programs": list(m.programs),
            "label": m.label,
            "published_string": m.published,
            "family": m.family,
            "format_ok": m.format_ok,
            "reliability": m.reliability,
        }
        for m in stored.index.lookup(address.normalized)
        if m.family == family
    )
    return CheckRecord(
        "ofac_sdn",
        "sanctions_list",
        "listed" if entries else "not_listed",
        age > max_age_hours * 3600,
        listing,
        None,
        entries,
        None,
        None,
        url,
    )


def _record_dict(r: CheckRecord) -> dict[str, Any]:
    return {
        "source_id": r.source_id,
        "kind": r.kind,
        "state": r.state,
        "stale": r.stale,
        "list": r.list,
        "block": r.block,
        "entry": r.entries[0] if r.entries else None,
        "entries": list(r.entries),
        "raw_value": r.raw_value,
        "reason": r.reason,
        "source_url": r.source_url,
    }


def _facts_dict(facts: Facts) -> dict[str, Any]:
    if facts.state != "observed":
        return {"state": facts.state, "chain": facts.chain, "reason": facts.reason, "source_url": facts.source_url}
    if facts.chain == "bitcoin":
        return {
            "state": "observed",
            "chain": "bitcoin",
            "height": facts.block,
            "tx_count": facts.tx_count,
            "funded_outputs": facts.funded_outputs,
            "spent_outputs": facts.spent_outputs,
            "source_url": facts.source_url,
        }
    return {
        "state": "observed",
        "chain": facts.chain,
        "block": facts.block,
        "code_kind": facts.code_kind,
        "code_bytes": facts.code_bytes,
        "delegate": facts.delegate,
        "sent_tx_count": facts.sent_tx_count,
        "source_url": facts.source_url,
    }


def _availability(
    checks: list[str], not_covered: list[dict[str, str]], records: list[CheckRecord], facts: dict[str, Any]
) -> dict[str, str]:
    absent = {x["check"]: x["reason"] for x in not_covered}
    ofac = [r for r in records if r.source_id == "ofac_sdn"]
    if ofac:
        sanctions = "unavailable" if ofac[0].state == "unavailable" else ("stale" if ofac[0].stale else "available")
    else:
        sanctions = "not_requested"
    freezes = [r for r in records if r.kind == "issuer_freeze"]
    if freezes:
        answered = sum(1 for r in freezes if r.state != "unavailable")
        freeze = "available" if answered == len(freezes) else ("unavailable" if answered == 0 else "partial")
    else:
        reasons = {absent.get("usdt_freeze"), absent.get("usdc_freeze")}
        freeze = "not_requested" if "not_requested" in reasons else "not_covered"
    state = facts["state"]
    chain_facts = {"observed": "available", "unavailable": "unavailable"}.get(state, state)
    return {"sanctions_list": sanctions, "issuer_freeze": freeze, "chain_facts": chain_facts}


def _envelope(
    tool: str,
    *,
    status: str,
    data_mode: str,
    now: dt.datetime,
    request: dict[str, Any],
    data: dict[str, Any],
    head: str,
    points: list[str],
    availability: dict[str, str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": tool,
        "status": status,
        "data_mode": data_mode,
        "as_of": now.strftime(TIME_FORMAT),
        "request": request,
        "data": data,
        "summary": {"headline": head, "key_points": points},
        "availability": availability,
        "warnings": warnings,
    }


def run(
    args: object,
    *,
    now: dt.datetime | None = None,
    transport: Transport | None = None,
    list_dir: pathlib.Path | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[float], float] | None = None,
    cooldowns: dict[str, float] | None = None,
    budget_seconds: float = TOTAL_BUDGET_SECONDS,
    refresh_list: bool = True,
) -> dict[str, Any]:
    """Check one address. Raises InputError for malformed arguments, before any network call or file access."""
    a = validate_input(args)
    address: Address = a["address"]
    chain: str = a["chain"]
    checks: list[str] = a["checks"]
    now = now or dt.datetime.now(dt.UTC)
    transport = transport or default_transport()
    caller = Caller(transport, now=now, deadline=clock() + budget_seconds, clock=clock, sleep=sleep, jitter=jitter, cooldowns=cooldowns)
    records: list[CheckRecord] = []
    list_state: list_store.ListState | None = None
    if "ofac_sdn" in checks:
        list_state = list_store.ensure(list_dir or list_store.default_dir(), caller, now, refresh=refresh_list)
        records.append(_ofac_record(list_state, address, now, a["max_list_age_hours"]))
    freezes = [FREEZES[(name, chain)] for name in ("usdt_freeze", "usdc_freeze") if name in checks]
    want_facts = "chain_facts" in checks
    read = ChainRead()
    if chain in EVM_CHAINS and (freezes or want_facts):
        read = read_evm(chain, address.normalized, freezes, want_facts, caller, now)
    elif chain == "tron" and freezes:
        read = read_tron(address.normalized, caller, now)
    elif chain == "bitcoin" and want_facts:
        read = read_bitcoin(address.normalized, caller)
    for r in read.freezes:
        block = (
            None
            if r.block is None
            else {"chain": r.block.chain, "number": r.block.number, "age_seconds": r.block.age_seconds, "pinned": r.block.pinned}
        )
        records.append(CheckRecord(r.source_id, "issuer_freeze", r.state, False, None, block, (), r.raw_value, r.reason, r.source_url))
    not_covered: list[dict[str, str]] = a["not_covered"]
    if read.facts is not None:
        facts = _facts_dict(read.facts)
    else:
        absent = {x["check"]: x["reason"] for x in not_covered}
        facts = {"state": "not_covered" if absent.get("chain_facts") == "not_available_on_chain" else "not_requested", "chain": chain}
    verdict, status, counts = decide(records)
    copy_used = list_state is not None and list_state.stored is not None
    if not transport.live:
        data_mode = "unknown"
    elif copy_used and list_state is not None and list_state.action != "downloaded":
        data_mode = "mixed"
    else:
        data_mode = "live"
    copy_age = _confirmed_age(now, list_state.stored) if list_state is not None and list_state.stored is not None else None
    points = [p for r in records for p in check_points(r, chain)]
    fact = facts_point(facts)
    if fact is not None:
        points.append(fact)
    points += [not_covered_point(x, chain) for x in not_covered]
    data = {
        "address": {
            "normalized": address.normalized,
            "display": address.display,
            "chain": chain,
            "format": address.format,
            "checksum": address.checksum,
        },
        "verdict": verdict,
        "counts": counts,
        "checks": [_record_dict(r) for r in records],
        "not_covered": not_covered,
        "chain_facts": facts,
        "sent_to": list(read.sent_to),
        "source_calls": [c.as_dict() for c in caller.records],
    }
    request = {
        "address": address.display,
        "chain": chain,
        "checks": checks,
        "max_list_age_hours": a["max_list_age_hours"],
        "defaults_applied": a["defaults_applied"],
        "notes": a["notes"],
    }
    warnings = warnings_for(
        chain=chain,
        records=records,
        facts=facts,
        checksum=address.checksum,
        sent_to=read.sent_to,
        data_mode=data_mode,
        local_copy_age_seconds=copy_age,
    )
    return _envelope(
        TOOL_NAME,
        status=status,
        data_mode=data_mode,
        now=now,
        request=request,
        data=data,
        head=headline(short_address(address.display), chain, records, not_covered),
        points=points,
        availability=_availability(checks, not_covered, records, facts),
        warnings=warnings,
    )


def run_sources(args: object, *, now: dt.datetime | None = None, list_dir: pathlib.Path | None = None) -> dict[str, Any]:
    """Describe the sources (and the local list copy) without sending anything anywhere."""
    a = validate_sources_input(args)
    chain = a["chain"]
    now = now or dt.datetime.now(dt.UTC)
    stored = list_store.load(list_dir or list_store.default_dir())
    rows: list[dict[str, Any]] = []
    for s in REGISTRY.all():
        if s.used and chain is not None and chain not in s.chains:
            continue
        is_list = s.source_id == "ofac_sdn" and stored is not None
        rows.append(
            {
                "source_id": s.source_id,
                "name": s.name,
                "kind": s.kind,
                "chains": list(s.chains),
                "maintained": s.maintained,
                "maintained_basis": s.maintained_basis,
                "used": s.used,
                "not_used_reason": s.not_used_reason,
                "endpoint": s.endpoint,
                "terms_url": s.terms_url,
                "terms_note": s.terms_note,
                "list_issued": stored.index.date_of_issue if is_list and stored else None,
                "list_issued_age_days": _issued_days(now, stored.index.date_of_issue) if is_list and stored else None,
                "confirmed_current_age_seconds": _confirmed_age(now, stored) if is_list and stored else None,
                "address_strings": stored.index.address_strings if is_list and stored else None,
                "families": dict(stored.index.families) if is_list and stored else None,
            }
        )
    used = [r for r in rows if r["used"]]
    where = f" on {chain}" if chain else ""
    points = [f"{r['name']} ({r['kind']}): {', '.join(r['chains'])}." for r in used]
    points += [f"{r['name']}: not used ({r['not_used_reason']})." for r in rows if not r["used"]]
    warnings = [NO_SCAM_SOURCE]
    if stored is not None:
        age = _confirmed_age(now, stored)
        head = (
            f"address_flag_check reads {len(used)} sources{where}; the OFAC list copy was issued "
            f"{issued_text(_issued_days(now, stored.index.date_of_issue))} ago and confirmed current {age_text(age)} ago."
        )
        warnings.append(f"The OFAC list came from the local copy confirmed current {age_text(age)} ago.")
        status, data_mode, availability = "ok", "mixed", {"sanctions_list_copy": "available"}
    else:
        head = f"address_flag_check reads {len(used)} sources{where}; no copy of the OFAC list has been loaded on this machine."
        warnings.append(NO_COPY)
        status, data_mode, availability = "partial", "unknown", {"sanctions_list_copy": "unavailable"}
    return _envelope(
        SOURCES_TOOL_NAME,
        status=status,
        data_mode=data_mode,
        now=now,
        request={"chain": chain},
        data={"chain": chain, "sources": rows},
        head=head,
        points=points,
        availability=availability,
        warnings=warnings,
    )
