"""Checks of ``address_screen``: how the league reads the answer of the address-flag skill, and what it makes of it.

The three envelopes in ``tests/fixtures/address_flag`` were produced by the skill itself from recorded public data (the trimmed OFAC extract
and recorded node replies, ``data_mode`` unknown because the sources were injected). Every expectation here is written by hand from them.
The reader is strict: an envelope that contradicts itself (a verdict that its counts do not give, a check list that does not add up to the
counts, another chain or address than the one asked about) is refused, never half-read. A source that did not answer leaves no evidence, and
a screen that is not a flag is never a statement that the address is safe.
"""

from __future__ import annotations

import copy
import json
import types
from pathlib import Path
from typing import Any

from hoshitori.capture import RawCapture
from hoshitori.timeutil import parse_rfc3339

FIXTURES = Path(__file__).parent / "fixtures" / "address_flag"
DEAD = "0x000000000000000000000000000000000000dEaD"
ENTITY = "0xE950DC316b836e4EeFb8308bf32Bf7C72a1358FF"


def envelope(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return loaded


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want or type(got) is not type(want):
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Any, says: str) -> None:
        try:
            call()
        except self.mod.ScreenError as exc:
            if says not in str(exc):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected it to contain {says!r}")
            return
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def read(mod: types.ModuleType, env: Any, chain: str = "ethereum", address: str = DEAD) -> Any:
    return mod.read_screen(env, chain=chain, address=address)


def check_the_recorded_answers(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    s = read(mod, envelope("no_flag_found"))
    c.eq(
        "no flag found",
        (s.verdict, s.status, s.chain, s.address, s.data_mode, s.as_of),
        ("no_flag_found", "ok", "ethereum", "0x000000000000000000000000000000000000dead", "unknown", "2026-11-01T08:24:36Z"),
    )
    c.eq(
        "three sources, each of them answered and none lists the address",
        [(x.source_id, x.kind, x.state, x.stale) for x in s.sources],
        [
            ("ofac_sdn", "sanctions_list", "not_listed", False),
            ("usdt_freeze_ethereum", "issuer_freeze", "not_listed", False),
            ("usdc_freeze_ethereum", "issuer_freeze", "not_listed", False),
        ],
    )
    c.eq(
        "and no source is down", (s.counts, s.hazard), ({"in_scope": 3, "listed": 0, "not_listed": 3, "stale": 0, "unavailable": 0}, False)
    )
    f = read(mod, envelope("flagged"), address=ENTITY)
    c.eq(
        "a flagged address",
        (f.verdict, f.status, f.address, [x.state for x in f.sources], f.hazard),
        ("flagged", "ok", "0xe950dc316b836e4eefb8308bf32bf7c72a1358ff", ["listed", "listed", "listed"], True),
    )
    u = read(mod, envelope("unavailable"))
    c.eq(
        "every source down: no verdict, and it is not a flag",
        (u.verdict, u.status, [x.state for x in u.sources], u.hazard, u.counts["unavailable"]),
        (None, "unavailable", ["unavailable", "unavailable", "unavailable"], False, 3),
    )
    c.eq("the address may be given in either case", read(mod, envelope("no_flag_found"), address=DEAD.lower()).address, DEAD.lower())
    c.eq("the answer is read from bytes as well", read(mod, json.dumps(envelope("flagged")).encode(), address=ENTITY).verdict, "flagged")
    return c.bad


def check_an_address_that_is_not_hexadecimal(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    tron = "TPnW2XhrBhmmVnW3aWkvB5PxaH3rPs9dDx"
    env = envelope("no_flag_found")
    env["data"]["address"].update({"chain": "tron", "normalized": tron})
    c.eq("an address that is not hexadecimal is read as it was written", read(mod, env, chain="tron", address=tron).address, tron)
    c.refuses("and is not read in another case", lambda: read(mod, env, chain="tron", address=tron.lower()), "address")
    return c.bad


def check_the_types_and_words(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("the provider the screen is asked of", mod.PROVIDER, "address-flag")
    c.eq("the chains the skill covers", mod.CHAINS, ("ethereum", "arbitrum", "base", "bsc", "optimism", "polygon", "tron", "bitcoin"))
    c.eq("the answer is frozen", [k.__dataclass_params__.frozen for k in (mod.Screen, mod.Source)], [True, True])
    c.eq("the words of a run", mod.READINGS, ("FLAGGED", "NO_FLAG_FOUND", "INCOMPLETE", "NOT_SCREENED"))
    raw = (FIXTURES / "no_flag_found.json").read_bytes()
    items = mod.screen_items(capture_of(raw), read(mod, raw))
    c.eq(
        "the call an item was made from",
        items[0].request,
        (("address", "0x000000000000000000000000000000000000dead"), ("chain", "ethereum")),
    )
    screen = read(mod, raw)
    broken_chain = mod.Screen(
        "a" * 65, screen.address, screen.status, screen.data_mode, screen.as_of, screen.verdict, screen.counts, screen.sources
    )
    c.refuses(
        "a reading that cannot be evidence is refused, not dropped",
        lambda: mod.screen_items(capture_of(raw), broken_chain),
        "cannot be recorded",
    )
    return c.bad


def check_a_partial_answer(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    env = envelope("no_flag_found")
    env["status"] = "partial"
    env["data"]["verdict"] = "incomplete"
    env["data"]["checks"][1].update({"state": "unavailable", "reason": "timeout", "raw_value": None, "block": None})
    env["data"]["counts"] = {"in_scope": 3, "listed": 0, "not_listed": 2, "stale": 0, "unavailable": 1}
    s = read(mod, env)
    c.eq(
        "one source down is incomplete, never no flag found",
        (s.verdict, s.status, [x.state for x in s.sources], s.hazard),
        ("incomplete", "partial", ["not_listed", "unavailable", "not_listed"], False),
    )
    env = envelope("flagged")
    env["status"] = "partial"
    env["data"]["checks"][2].update({"state": "unavailable", "reason": "timeout", "raw_value": None, "block": None})
    env["data"]["counts"] = {"in_scope": 3, "listed": 2, "not_listed": 0, "stale": 0, "unavailable": 1}
    s = read(mod, env, address=ENTITY)
    c.eq("one source that lists it is a flag whatever the others do", (s.verdict, s.hazard), ("flagged", True))
    env = envelope("flagged")
    env["status"] = "partial"
    env["data"]["checks"][0]["stale"] = True
    env["data"]["counts"]["stale"] = 1
    s = read(mod, env, address=ENTITY)
    c.eq("a flag from a stale list is still a flag", (s.verdict, s.sources[0].stale, s.hazard), ("flagged", True, True))
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)

    def broken(path: str, value: Any, name: str = "no_flag_found") -> dict[str, Any]:
        env = envelope(name)
        node: Any = env
        *head, last = path.split(".")
        for part in head:
            node = node[int(part)] if isinstance(node, list) else node[part]
        if isinstance(node, list):
            node[int(last)] = value
        else:
            node[last] = value
        return env

    c.refuses("not an object", lambda: read(mod, [1, 2]), "object")
    c.refuses("not JSON", lambda: read(mod, b"{not json"), "readable")
    c.refuses("another tool", lambda: read(mod, broken("tool", "check_safety")), "tool")
    c.refuses("another schema version", lambda: read(mod, broken("schema_version", "2.0")), "schema")
    c.refuses("a status that is no status", lambda: read(mod, broken("status", "fine")), "the status is one of")
    c.refuses("a status that the verdict does not give", lambda: read(mod, broken("status", "partial")), "the status does not follow")
    c.refuses("another chain than asked about", lambda: read(mod, envelope("no_flag_found"), chain="base"), "chain")
    c.refuses("another address than asked about", lambda: read(mod, envelope("no_flag_found"), address=ENTITY), "address")
    env = envelope("no_flag_found")
    env["extra"] = 1
    c.refuses("a member the envelope does not have", lambda: read(mod, env), "members")
    env = envelope("no_flag_found")
    del env["data"]
    c.refuses("a member missing", lambda: read(mod, env), "members")
    c.refuses("a verdict that is no verdict", lambda: read(mod, broken("data.verdict", "safe")), "the verdict is one of")
    c.refuses("flagged with nothing listed", lambda: read(mod, broken("data.verdict", "flagged")), "verdict")
    c.refuses("no flag found with a source down", lambda: read(mod, broken("data.counts.unavailable", 1)), "counts")
    c.refuses("no verdict but a status of ok", lambda: read(mod, broken("data.verdict", None)), "verdict")
    c.refuses("a count that does not follow from the checks", lambda: read(mod, broken("data.counts.listed", 1)), "counts")
    c.refuses("a count of sources that is not the list of sources", lambda: read(mod, broken("data.counts.in_scope", 4)), "counts")
    c.refuses("a count that is not a whole number", lambda: read(mod, broken("data.counts.not_listed", "3")), "counts")
    c.refuses("a count that is a boolean", lambda: read(mod, broken("data.counts.stale", False)), "counts")
    c.refuses("a state that is no state", lambda: read(mod, broken("data.checks.0.state", "clean")), "state")
    c.refuses("a kind that is no kind", lambda: read(mod, broken("data.checks.0.kind", "reputation")), "kind")
    c.refuses("a source with no name", lambda: read(mod, broken("data.checks.0.source_id", "")), "source")
    c.refuses("a source named twice", lambda: read(mod, broken("data.checks.1.source_id", "ofac_sdn")), "source")
    c.refuses("a stale flag that is not a boolean", lambda: read(mod, broken("data.checks.0.stale", "no")), "stale")
    c.refuses("no sources at all", lambda: read(mod, broken("data.checks", [])), "at least one source")
    c.refuses("sources that are not a list", lambda: read(mod, broken("data.checks", {"a": 1})), "at least one source")
    c.refuses("a source that is not an object", lambda: read(mod, broken("data.checks.0", "ofac_sdn")), "a check is an object")
    c.refuses("counts that are not an object", lambda: read(mod, broken("data.counts", [3, 0, 3, 0, 0])), "the counts are")
    counts = envelope("no_flag_found")["data"]["counts"]
    c.refuses(
        "counts with a member missing",
        lambda: read(mod, broken("data.counts", {k: v for k, v in counts.items() if k != "stale"})),
        "the counts are",
    )
    c.refuses("counts with a member too many", lambda: read(mod, broken("data.counts", {**counts, "safe": 3})), "the counts are")
    c.refuses("data that is not an object", lambda: read(mod, broken("data", [])), "the members of the data")
    c.refuses("data with a member too many", lambda: read(mod, broken("data.advice", "ignore the rest")), "the members of the data")
    c.refuses("an as_of that is not text", lambda: read(mod, broken("as_of", 5)), "as_of is a time")
    c.refuses("a chain that is not a plain word", lambda: read(mod, envelope("no_flag_found"), chain="Ethereum"), "plain words")
    c.refuses("an address that is not a plain word", lambda: read(mod, envelope("no_flag_found"), address="0x!"), "plain words")
    for mode in ("live", "mixed", "simulated", "unknown"):
        c.eq(f"the data mode {mode} is read", read(mod, broken("data_mode", mode)).data_mode, mode)
    c.refuses("an as_of that is not a time", lambda: read(mod, broken("as_of", "yesterday")), "as_of")
    c.refuses("a data mode that is no mode", lambda: read(mod, broken("data_mode", "guessed")), "data_mode")
    c.refuses("a sentence in place of a verdict", lambda: read(mod, broken("data.verdict", "no flag found, safe")), "verdict")
    return c.bad


def capture_of(raw: bytes) -> RawCapture:
    sent = parse_rfc3339("2026-11-01T08:24:30Z")
    return RawCapture(
        "cap-" + "a" * 32,
        "address-flag",
        {"operation": "check", "args": {"address": DEAD, "chain": "ethereum"}},
        200,
        {},
        raw,
        sent,
        parse_rfc3339("2026-11-01T08:24:40Z"),
    )


def check_the_evidence(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    raw = (FIXTURES / "no_flag_found.json").read_bytes()
    screen = read(mod, raw)
    items = mod.screen_items(capture_of(raw), screen)
    c.eq(
        "one item for each source that answered, in the order the skill gave them",
        [(i.path, i.origin.text, i.kind.value) for i in items],
        [
            ("address_flag/ofac_sdn", "PRIMARY_DOC:ofac", "OBSERVATION"),
            ("address_flag/usdt_freeze_ethereum", "CHAIN:ethereum", "OBSERVATION"),
            ("address_flag/usdc_freeze_ethereum", "CHAIN:ethereum", "OBSERVATION"),
        ],
    )
    first = dict(items[0].fields)
    c.eq(
        "what an item holds: the state, the kind and whether it is stale, and nothing the model could read as safe",
        first,
        {
            "state": "not_listed",
            "kind": "sanctions_list",
            "stale": False,
            "address": "0x000000000000000000000000000000000000dead",
            "chain": "ethereum",
        },
    )
    c.eq(
        "the capture it was read from",
        (items[0].capture_id, items[0].raw_sha256, items[0].tool, items[0].data_mode, items[0].redistribution.value),
        ("cap-" + "a" * 32, capture_of(raw).body_sha256, "address_flag_check", "unknown", "PRIVATE_ONLY"),
    )
    c.eq("known when it was received", (items[0].received_at_ms, items[0].known_at_ms), (1793521480000, 1793521480000))
    c.eq("and the time of the answer is the skill's", items[0].event_time_ms, 1793521476000)
    flagged_raw = (FIXTURES / "flagged.json").read_bytes()
    listed = mod.screen_items(capture_of(flagged_raw), read(mod, flagged_raw, address=ENTITY))
    c.eq("a listing is an item like any other", [dict(i.fields)["state"] for i in listed], ["listed", "listed", "listed"])
    down_raw = (FIXTURES / "unavailable.json").read_bytes()
    c.eq("a source that did not answer leaves no evidence", mod.screen_items(capture_of(down_raw), read(mod, down_raw)), ())
    partial = envelope("no_flag_found")
    partial["status"] = "partial"
    partial["data"]["verdict"] = "incomplete"
    partial["data"]["checks"][1].update({"state": "unavailable", "reason": "timeout", "raw_value": None, "block": None})
    partial["data"]["counts"] = {"in_scope": 3, "listed": 0, "not_listed": 2, "stale": 0, "unavailable": 1}
    raw_partial = json.dumps(partial).encode()
    c.eq(
        "a source that is down among others leaves only the others",
        [i.path for i in mod.screen_items(capture_of(raw_partial), read(mod, raw_partial))],
        ["address_flag/ofac_sdn", "address_flag/usdc_freeze_ethereum"],
    )
    c.eq("each item has an id of its own", len({i.evidence_id for i in items}), 3)
    return c.bad


def check_the_gate_word(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "what a run says of a screen: flagged, a clean read, an incomplete one, none",
        [
            mod.reading(read(mod, envelope("flagged"), address=ENTITY)),
            mod.reading(read(mod, envelope("no_flag_found"))),
            mod.reading(read(mod, envelope("unavailable"))),
        ],
        ["FLAGGED", "NO_FLAG_FOUND", "NOT_SCREENED"],
    )
    env = copy.deepcopy(envelope("no_flag_found"))
    env["status"] = "partial"
    env["data"]["verdict"] = "incomplete"
    env["data"]["checks"][1].update({"state": "unavailable", "reason": "timeout", "raw_value": None, "block": None})
    env["data"]["counts"] = {"in_scope": 3, "listed": 0, "not_listed": 2, "stale": 0, "unavailable": 1}
    c.eq("an incomplete read is its own word", mod.reading(read(mod, env)), "INCOMPLETE")
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_the_recorded_answers,
        check_a_partial_answer,
        check_an_address_that_is_not_hexadecimal,
        check_the_types_and_words,
        check_refusals,
        check_the_evidence,
        check_the_gate_word,
    ):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
