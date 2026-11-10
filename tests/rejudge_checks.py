"""Checks of judging the stored time-stamp answers again (``hoshitori.rejudge``), runnable against a mutant: real tokens signed by test CAs
are asked for through the real gateway and stored as captures, and the attestation rebuilt from the captures alone must say what the live one
said."""

from __future__ import annotations

import hashlib
import tempfile
import types
from datetime import timedelta
from pathlib import Path
from typing import Any

from hoshitori import attest as attest_module
from hoshitori import canonical, commitment_store
from hoshitori.capture import RawCapture
from hoshitori.checkpoint import parse_text
from hoshitori.commit import commit_batch
from hoshitori.note import split_note
from hoshitori.timeline import from_ms, to_ms
from hoshitori.tsa import PinnedRoot, TokenPolicy
from tests import tsa_pki
from tests.attest_checks import CLOCK, DIGEST, Case, good_specs, now, rules_for, run, world
from tests.commit_checks import TickClock, attested_lines, ledger_in, record
from tests.fake_tsa import GRANTED, rejection
from tests.ryo_fake_server import Behaviour


def policy_of(sites: list[Any]) -> TokenPolicy:
    return TokenPolicy(tuple(PinnedRoot(s.chain.root.fingerprint, s.operator) for s in sites))


def rebuilt(mod: types.ModuleType, store: Any, sites: list[Any], rules: Any, committed: int, digest: bytes = DIGEST) -> Any:
    return mod.attestation_from_captures(store, digest, committed, policy=policy_of(sites), rules=rules)


def same(live: Any, got: Any) -> dict[str, object]:
    """What a rebuilt attestation must share with the one the live run made."""
    if got is None:
        return {"none": True}
    return {
        "level": got.level == live.level,
        "attested": got.attested_at_ms == live.attested_at_ms,
        "eligible": got.eligible_after_ms == live.eligible_after_ms,
        "origin": got.origin_ms == live.origin_ms,
        "uncertainty": got.uncertainty_ms == live.uncertainty_ms,
        "chosen": got.timing.chosen == live.timing.chosen,
        "tokens": got.tokens() == live.tokens(),
        "commit": (got.committed_at_ms, got.digest, got.closes_at_ms) == (live.committed_at_ms, live.digest, live.closes_at_ms),
    }


ALL_SAME = dict.fromkeys(("level", "attested", "eligible", "origin", "uncertainty", "chosen", "tokens", "commit"), True)


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("the look-back is an hour", mod.LOOK_BACK_MS, 3_600_000)
    c.eq("a stamp's source is the authority's provider", mod.PROVIDER_PREFIX, "tsa-")
    return c.bad


def two_specs() -> list[Any]:
    return good_specs()[:2]


def check_same_as_live(mod: types.ModuleType) -> list[str]:
    """Every answer that was stored is judged again against the same digest and commit time and gives the same attestation."""
    c = Case(mod)
    with world(two_specs()) as (runner, store, sites, authorities):
        rules = rules_for(sites)
        committed = to_ms(now())
        live = run(attest_module, runner, store, sites, authorities, rules=rules, committed=committed)
        c.eq("the live attestation reached C3", live.level, "C3")
        got = rebuilt(mod, store, sites, rules, committed)
        c.eq("the rebuilt attestation says what the live one said", same(live, got), ALL_SAME)
        c.eq(
            "the nonces are the ones the requests carried",
            {s.authority: s.nonce for s in got.stamps},
            {s.authority: s.nonce for s in live.stamps},
        )
        c.eq("nothing failed and nothing was left unfinished, because nothing is known of it", (got.failures, got.unfinished), ((), ()))
        later = got.timing_at(committed + 120_000, rules)
        c.eq(
            "it judges against another record's commit time as the live one does",
            later.level,
            live.timing_at(committed + 120_000, rules).level,
        )
        c.eq("that later commit time is outside the window: no level", later.level != "C3", True)
    return c.bad


def check_other_digest_or_nothing(mod: types.ModuleType) -> list[str]:
    """Only answers to this digest count; with none there is no attestation, and an answer to another digest is not borrowed."""
    c = Case(mod)
    with world(good_specs()) as (runner, store, sites, authorities):
        rules = rules_for(sites)
        committed = to_ms(now())
        run(attest_module, runner, store, sites, authorities, rules=rules, committed=committed)
        c.eq("another digest has none", rebuilt(mod, store, sites, rules, committed, bytes(range(1, 33))), None)
    with world(good_specs()) as (runner, store, sites, authorities):
        c.eq("an empty store has none", rebuilt(mod, store, sites, rules_for(sites), to_ms(now())), None)
    return c.bad


def check_one_authority_failed(mod: types.ModuleType) -> list[str]:
    """An authority that refused leaves no usable answer: the rest are judged, and the level is what they support (below C3 with one)."""
    c = Case(mod)
    specs: list[Any] = [
        ("alpha", "op-a", "Org A", None, None),
        ("beta", "op-b", "Org B", None, lambda seen: _refuse()),
        ("gamma", "op-c", "Org C", None, lambda seen: _refuse()),
    ]
    with world(specs) as (runner, store, sites, authorities):
        rules = rules_for(sites)
        committed = to_ms(now())
        live = run(attest_module, runner, store, sites, authorities, rules=rules, committed=committed)
        got = rebuilt(mod, store, sites, rules, committed)
        c.eq(
            "one token is below C3, live and rebuilt alike", (live.level != "C3", got is not None and got.level == live.level), (True, True)
        )
        c.eq("what was kept is what the one authority said", got.tokens() == live.tokens() and sorted(got.tokens()) == ["alpha"], True)
        c.eq("the refusals left nothing to judge", [e.name for e in got.evidence], ["alpha"])
    return c.bad


def _refuse() -> Any:
    return Behaviour(200, rejection(["bad_alg"]), GRANTED)


def check_rejected_token_is_kept_and_reasoned(mod: types.ModuleType) -> list[str]:
    """A token that fails the policy (signed by a root nobody pinned) is a rejected answer with its reason, never a counted one."""
    c = Case(mod)
    with world(two_specs()) as (runner, store, sites, authorities):
        rules = rules_for(sites)
        committed = to_ms(now())
        run(attest_module, runner, store, sites, authorities, rules=rules, committed=committed)
        strangers = TokenPolicy(tuple(PinnedRoot(s.chain.root.fingerprint, s.operator) for s in sites[:1]))
        got = mod.attestation_from_captures(store, DIGEST, committed, policy=strangers, rules=rules)
        c.eq("one pinned root: one counted token at most, so no C3", got.level != "C3", True)
        rejected = [e for e in got.evidence if e.verified is None]
        c.eq(
            "the other answers are kept with a reason",
            (len(rejected), all(e.reason for e in rejected), len(got.stamps) >= 2),
            (len(got.stamps) - 1, True, True),
        )
    return c.bad


def check_picks_the_newest_answer_of_a_source(mod: types.ModuleType) -> list[str]:
    """A source that was asked twice for one digest (a retry after a first answer was lost) counts once, by its newest granted answer, and
    a source's own captures of other operations, or that were not granted, are not stamps."""
    c = Case(mod)
    with world(two_specs()) as (runner, store, sites, authorities):
        rules = rules_for(sites)
        committed = to_ms(now())
        live = run(attest_module, runner, store, sites, authorities, rules=rules, committed=committed)
        some = live.stamps[0]
        sent = CLOCK.now()
        junk = [
            RawCapture(
                f"cap-{9_000_001:032x}",
                "tsa-" + some.authority,
                {"operation": "other", "arguments": {"digest": DIGEST.hex(), "nonce": 1}},
                200,
                {},
                b"\x30\x00",
                sent,
                sent,
            ),
            RawCapture(
                f"cap-{9_000_002:032x}",
                "tsa-" + some.authority,
                {"operation": "stamp", "arguments": {"digest": DIGEST.hex(), "nonce": some.nonce}},
                503,
                {},
                b"busy",
                sent,
                sent,
            ),
            RawCapture(
                f"cap-{9_000_003:032x}",
                "ryo",
                {"operation": "stamp", "arguments": {"digest": DIGEST.hex(), "nonce": some.nonce}},
                200,
                {},
                some.response,
                sent,
                sent,
            ),
        ]
        for capture in junk:
            store.append(capture)
        got = rebuilt(mod, store, sites, rules, committed)
        c.eq("the junk changed nothing", same(live, got), ALL_SAME)
        better = tsa_pki.build_token(
            DIGEST, sites[0].chain, tsa_pki.TokenOptions(gen_time=from_ms(committed) + timedelta(seconds=1), nonce=7)
        )
        store.append(
            RawCapture(
                f"cap-{9_000_004:032x}",
                "tsa-" + sites[0].name,
                {"operation": "stamp", "arguments": {"digest": DIGEST.hex(), "nonce": 7}},
                200,
                {},
                better,
                sent,
                sent,
            )
        )
        newest = rebuilt(mod, store, sites, rules, committed)
        c.eq("a newer granted answer of a source replaces its older one", newest.tokens()[sites[0].name], better)
        c.eq(
            "and the others are as they were",
            {k: v for k, v in newest.tokens().items() if k != sites[0].name},
            {k: v for k, v in live.tokens().items() if k != sites[0].name},
        )
    return c.bad


def _stamped_ledger(
    root: Path, runner: Any, store: Any, sites: list[Any], authorities: list[Any], rules: Any, *, register: bool, clock: Any = CLOCK
) -> Any:
    """A real ledger with one batch committed, its checkpoint stamped by the fake authorities through the real attestation. With
    ``register`` False the checkpoint is signed and stamped and the process is taken to have died before it was registered or saved."""
    ledger = ledger_in(root, clock)
    records = [record(n) for n in range(4)]
    policy = policy_of(sites)

    def attestor(digest: bytes, committed_at_ms: int) -> Any:
        return attest_module.attest(runner, store, CLOCK, authorities, digest, committed_at_ms=committed_at_ms, policy=policy, rules=rules)

    if register:
        return ledger, records, commit_batch(ledger, records, attestor=attestor, rules=rules)
    ledger.submit_many(records)
    ledger.append_pending()
    note = ledger.checkpoint()
    text, _ = split_note(note)
    last = canonical.parse_record(ledger.read_record(ledger.size - 1))
    from hoshitori.timeutil import parse_rfc3339

    committed = to_ms(parse_rfc3339(str(last["committed_at"])))
    return ledger, records, (note, attestor(hashlib.sha256(text).digest(), committed))


def check_rejudger(mod: types.ModuleType) -> list[str]:
    """What recovery is given for a real batch: the checkpoint the answers stamped is found, the verdict equals the live one, a C3 checkpoint
    is registered once, and the commitment is saved for a bundle."""
    c = Case(mod)
    with world(two_specs()) as (runner, store, sites, authorities), tempfile.TemporaryDirectory() as tmp:
        rules = rules_for(sites)
        root = Path(tmp)
        ledger, records, commitment = _stamped_ledger(
            root, runner, store, sites, authorities, rules, register=True, clock=TickClock(now(), 0.02)
        )
        keys = [{"source": r["source"], "id": r["id"]} for r in records]
        saved = root / "saved"
        judge = mod.Rejudger(ledger, store, policy=policy_of(sites), rules=rules, store=saved)
        got = judge(keys)
        c.eq("the batch's checkpoint is the one found", got is not None and got.commitment_id == commitment.commitment_id, True)
        live = commitment.attestation
        c.eq(
            "the verdict is the live one",
            got is not None
            and (got.attestation.level, got.attestation.attested_at_ms, got.attestation.eligible_after_ms)
            == (live.level, live.attested_at_ms, live.eligible_after_ms),
            True,
        )
        c.eq("judged at the last record's commit time", got is not None and got.attestation.committed_at_ms == live.committed_at_ms, True)
        c.eq("the rules are carried", got is not None and got.rules is rules, True)
        c.eq("the live run had registered it, and nothing is registered twice", len(attested_lines(root)), 1)
        c.eq(
            "the commitment is saved for a bundle with the stamped checkpoint and its tokens",
            commitment_store.load(saved, commitment.commitment_id).note == commitment.note,
            True,
        )
        again = judge(keys)
        c.eq(
            "asking again changes nothing",
            (again is not None and again.commitment_id, len(attested_lines(root))),
            (commitment.commitment_id, 1),
        )
        c.eq("a record that is not in the log gives no verdict", judge([{"source": keys[0]["source"], "id": "nope"}]), None)
        c.eq("no records give none", judge([]), None)
        ledger.close()
    return c.bad


def check_rejudger_after_a_kill(mod: types.ModuleType) -> list[str]:
    """The process died after the answers were kept and before the checkpoint was registered and saved: the answers register it (once) and
    save the commitment, so the bundle can be made."""
    c = Case(mod)
    with world(two_specs()) as (runner, store, sites, authorities), tempfile.TemporaryDirectory() as tmp:
        rules = rules_for(sites)
        root = Path(tmp)
        ledger, records, (note, live) = _stamped_ledger(root, runner, store, sites, authorities, rules, register=False)
        c.eq("nothing is registered yet", attested_lines(root), [])
        keys = [{"source": r["source"], "id": r["id"]} for r in records]
        saved = root / "saved"
        judge = mod.Rejudger(ledger, store, policy=policy_of(sites), rules=rules, store=saved)
        got = judge(keys)
        size = parse_text(split_note(note)[0]).size
        c.eq("a verdict of C3 as the live run reached", (got is not None and got.attestation.level, live.level), ("C3", "C3"))
        lines = attested_lines(root)
        c.eq(
            "the checkpoint is registered, once, as attested",
            [(x["size"], x["root"]) for x in lines],
            [(size, parse_text(split_note(note)[0]).root.hex())],
        )
        c.eq(
            "with the authorities that carried it as the evidence", lines[0]["evidence"], "rfc3161:" + ",".join(sorted(live.timing.chosen))
        )
        judge(keys)
        c.eq("a second look registers nothing more", len(attested_lines(root)), 1)
        c.eq("the commitment is saved", got is not None and commitment_store.load(saved, got.commitment_id).note == note, True)
        # no store directory is asked for: nothing is saved, the verdict is the same
        quiet = mod.Rejudger(ledger, store, policy=policy_of(sites), rules=rules, store=None)
        c.eq("without a store the verdict is the same", quiet(keys) is not None and quiet(keys).commitment_id == got.commitment_id, True)  # type: ignore[union-attr]
        ledger.close()
    with world(two_specs()) as (runner, store, sites, authorities), tempfile.TemporaryDirectory() as tmp:
        rules = rules_for(sites)
        ledger = ledger_in(Path(tmp), CLOCK)  # type: ignore[arg-type]
        records = [record(n) for n in range(3)]
        ledger.submit_many(records)
        ledger.append_pending()
        ledger.checkpoint()
        keys = [{"source": r["source"], "id": r["id"]} for r in records]
        judge = mod.Rejudger(ledger, store, policy=policy_of(sites), rules=rules, store=None)
        c.eq("a checkpoint nobody stamped has no verdict", judge(keys), None)
        ledger.close()
    return c.bad


def check_rejudger_picks_the_right_checkpoint(mod: types.ModuleType) -> list[str]:
    """Two stamped batches in one log: the cycle's checkpoint is the first that holds all of its forecasts, however many came before, and a
    checkpoint is registered when it is not, even though others are."""
    c = Case(mod)
    with world(two_specs()) as (runner, store, sites, authorities), tempfile.TemporaryDirectory() as tmp:
        rules = rules_for(sites)
        root = Path(tmp)
        policy = policy_of(sites)
        ledger = ledger_in(root, CLOCK)  # type: ignore[arg-type]

        def attestor(digest: bytes, committed_at_ms: int) -> Any:
            return attest_module.attest(
                runner, store, CLOCK, authorities, digest, committed_at_ms=committed_at_ms, policy=policy, rules=rules
            )

        first_records = [record(n) for n in range(3)]
        second_records = [record(3)]
        first = commit_batch(ledger, first_records, attestor=attestor, rules=rules)
        second = commit_batch(ledger, second_records, attestor=attestor, rules=rules)
        c.eq(
            "the two commitments differ and each was registered by the live run",
            (first.commitment_id != second.commitment_id, len(attested_lines(root))),
            (True, 2),
        )
        first_keys = [{"source": r["source"], "id": r["id"]} for r in first_records]
        second_keys = [{"source": r["source"], "id": r["id"]} for r in second_records]
        judge = mod.Rejudger(ledger, store, policy=policy, rules=rules, store=None)
        got_first, got_second, got_both = judge(first_keys), judge(second_keys), judge(first_keys + second_keys)
        c.eq("the first batch is judged under its own checkpoint", got_first is not None and got_first.commitment_id, first.commitment_id)
        c.eq(
            "the second batch under the second, though the first is stamped too",
            got_second is not None and got_second.commitment_id,
            second.commitment_id,
        )
        c.eq(
            "forecasts of both need the checkpoint that holds the last of them",
            got_both is not None and got_both.commitment_id,
            second.commitment_id,
        )
        c.eq("nothing is registered again", len(attested_lines(root)), 2)
        # the file is read in whatever order it is in: the smallest checkpoint that holds the forecasts is still the one
        path = root / "ledger" / "checkpoints.jsonl"
        path.write_text("".join(reversed(path.read_text().splitlines(keepends=True))))
        flipped = judge(first_keys)
        c.eq("a file in the other order gives the same answer", flipped is not None and flipped.commitment_id, first.commitment_id)
        ledger.close()
    # the second checkpoint was stamped and the process died before it was registered, with the first registered: it is registered, once
    with world(two_specs()) as (runner, store, sites, authorities), tempfile.TemporaryDirectory() as tmp:
        rules = rules_for(sites)
        root = Path(tmp)
        policy = policy_of(sites)
        ledger = ledger_in(root, CLOCK)  # type: ignore[arg-type]

        def attestor2(digest: bytes, committed_at_ms: int) -> Any:
            return attest_module.attest(
                runner, store, CLOCK, authorities, digest, committed_at_ms=committed_at_ms, policy=policy, rules=rules
            )

        commit_batch(ledger, [record(n) for n in range(3)], attestor=attestor2, rules=rules)
        later = [record(n) for n in range(3, 6)]
        ledger.submit_many(later)
        ledger.append_pending()
        note = ledger.checkpoint()
        text, _ = split_note(note)
        stamped = attestor2(hashlib.sha256(text).digest(), to_ms(now()))
        c.eq("the second batch was stamped and not registered", (stamped.level, len(attested_lines(root))), ("C3", 1))
        judge = mod.Rejudger(ledger, store, policy=policy, rules=rules, store=None)
        keys = [{"source": r["source"], "id": r["id"]} for r in later]
        judge(keys)
        lines = attested_lines(root)
        c.eq("it is registered though another is", [x["size"] for x in lines], [3, 6])
        judge(keys)
        c.eq("and only once, however many look", len(attested_lines(root)), 2)
        ledger.close()
    return c.bad


def check_judged(mod: types.ModuleType) -> list[str]:
    """What recovery is given: the attestation, the rules, the commitment's id, and the level at any record's own commit time."""
    c = Case(mod)
    c.eq("Judged is frozen", mod.Judged.__dataclass_params__.frozen, True)
    with world(two_specs()) as (runner, store, sites, authorities):
        rules = rules_for(sites)
        committed = to_ms(now())
        live = run(attest_module, runner, store, sites, authorities, rules=rules, committed=committed)
        judged = mod.Judged(live, rules, "cmx")
        c.eq("it keeps what it was given", (judged.attestation, judged.rules, judged.commitment_id), (live, rules, "cmx"))
        c.eq("the level at the batch's commit time", judged.level_at(committed), "C3")
        c.eq("and at a commit time the tokens are too late for", judged.level_at(committed - 120_000) != "C3", True)
    return c.bad


def check_late_answers_are_judged_too(mod: types.ModuleType) -> list[str]:
    """With three authorities the live run stops when two have carried C3; an answer that arrived after it is in the store and is judged as
    well, so the rebuilt attestation holds every answer the live one held, at the same level."""
    c = Case(mod)
    with world(good_specs()) as (runner, store, sites, authorities):
        rules = rules_for(sites)
        committed = to_ms(now())
        live = run(attest_module, runner, store, sites, authorities, rules=rules, committed=committed)
        got = rebuilt(mod, store, sites, rules, committed)
        c.eq("it holds every answer the live one held", set(live.tokens()) <= set(got.tokens()), True)
        c.eq("at the same level", (live.level, got.level), ("C3", "C3"))
    return c.bad


def check_edges(mod: types.ModuleType) -> list[str]:
    """The order stamps are listed in, the exact edge of the look-back, and a request whose nonce is not a whole number."""
    c = Case(mod)
    with world(two_specs()) as (runner, store, sites, authorities):
        rules = rules_for(sites)
        committed = to_ms(now())
        live = run(attest_module, runner, store, sites, authorities, rules=rules, committed=committed)
        # the last authority's answer is stored again as the newest capture, so the newest-first search meets the authorities out of order
        last_stamp = sorted(live.stamps, key=lambda s: s.authority)[-1]
        again = from_ms(committed + 1)
        store.append(
            RawCapture(
                f"cap-{8_000_001:032x}",
                "tsa-" + last_stamp.authority,
                {"operation": "stamp", "arguments": {"digest": DIGEST.hex(), "nonce": last_stamp.nonce}},
                200,
                {},
                last_stamp.response,
                again,
                again,
            )
        )
        got = rebuilt(mod, store, sites, rules, committed)
        c.eq("stamps are listed by authority", [s.authority for s in got.stamps], sorted(s.authority for s in got.stamps))
        c.eq("and there are two", len(got.stamps), 2)
        # a capture received exactly at the look-back edge is inside it (the answer to this digest and a granted one)
        edge = from_ms(committed - mod.LOOK_BACK_MS)
        mine = live.stamps[0]
        for n, nonce in enumerate((True, "7")):
            store.append(
                RawCapture(
                    f"cap-{8_000_010 + n:032x}",
                    "tsa-" + mine.authority,
                    {"operation": "stamp", "arguments": {"digest": DIGEST.hex(), "nonce": nonce}},
                    200,
                    {},
                    mine.response,
                    edge,
                    edge,
                )
            )
        c.eq("a nonce that is not a whole number makes no stamp", same(live, rebuilt(mod, store, sites, rules, committed)), ALL_SAME)
        far = committed + mod.LOOK_BACK_MS + 10_000
        store.append(
            RawCapture(
                f"cap-{8_000_020:032x}",
                "tsa-" + mine.authority,
                {"operation": "stamp", "arguments": {"digest": DIGEST.hex(), "nonce": mine.nonce}},
                200,
                {},
                mine.response,
                from_ms(far - mod.LOOK_BACK_MS),
                from_ms(far - mod.LOOK_BACK_MS),
            )
        )
        at_edge = rebuilt(mod, store, sites, rules, far)
        c.eq(
            "an answer received exactly at the edge of the look-back is found",
            at_edge is not None and mine.authority in at_edge.tokens(),
            True,
        )
    return c.bad


def check_window_of_the_search(mod: types.ModuleType) -> list[str]:
    """Answers older than the look-back before the commit time are not looked for: an old stamp of the same digest is not this commitment's."""
    c = Case(mod)
    with world(good_specs()) as (runner, store, sites, authorities):
        rules = rules_for(sites)
        committed = to_ms(now())
        run(attest_module, runner, store, sites, authorities, rules=rules, committed=committed)
        far = committed + 2 * mod.LOOK_BACK_MS
        c.eq("a commit time two look-backs after the answers finds none of them", rebuilt(mod, store, sites, rules, far), None)
        edge = committed + mod.LOOK_BACK_MS - 30_000
        c.eq("just inside the look-back they are found", rebuilt(mod, store, sites, rules, edge) is not None, True)
    return c.bad


def check_validation(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    with world(good_specs()) as (_, store, sites, _):
        rules = rules_for(sites)
        c.refuses(
            "a digest is 32 bytes",
            lambda: mod.attestation_from_captures(store, b"short", 1, policy=policy_of(sites), rules=rules),
            "the digest is a SHA-256 value (32 bytes)",
        )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_same_as_live,
        check_other_digest_or_nothing,
        check_one_authority_failed,
        check_rejected_token_is_kept_and_reasoned,
        check_picks_the_newest_answer_of_a_source,
        check_judged,
        check_rejudger,
        check_rejudger_after_a_kill,
        check_rejudger_picks_the_right_checkpoint,
        check_late_answers_are_judged_too,
        check_edges,
        check_window_of_the_search,
        check_validation,
    ):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
