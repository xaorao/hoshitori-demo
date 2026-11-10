"""Checks of the attestation (``hoshitori.attest``), runnable against a mutant: fake authorities on loopback answer with real tokens signed by
test CAs, through the real gateway, runner, adapter and transport."""

from __future__ import annotations

import random
import tempfile
import threading
import time
import types
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hoshitori import trusted_root
from hoshitori.breaker import BreakerConfig
from hoshitori.capture import CaptureStore
from hoshitori.clock import SystemClock
from hoshitori.connector import ConnectorError, ErrorClass
from hoshitori.gateway import CallResult, DispatchJournal, Gateway, Outcome
from hoshitori.retry import RetryPolicy
from hoshitori.runner import Runner, ThreadExecutor
from hoshitori.timeline import from_ms, to_ms
from hoshitori.tsa import PinnedRoot, TokenPolicy
from hoshitori.tsa_gateway import Authority, attach_tsa
from tests import tsa_pki
from tests.fake_tsa import FakeTsa
from tests.ryo_fake_server import Behaviour

CLOCK = SystemClock()
DIGEST = bytes(range(32))


def now() -> datetime:
    return datetime.fromtimestamp(int(CLOCK.now().timestamp() * 1000) / 1000, tz=UTC)


@dataclass
class Site:
    """One fake authority: its chain, its server and what the trusted root knows about it."""

    name: str
    operator: str
    parent: str
    chain: tsa_pki.Chain
    server: FakeTsa


def rules_for(
    sites: list[Site], *, close_s: int = 3, required: int = 2, before: int = 1, after: int = 60, spread: int = 5, u: int = 5
) -> Any:
    operators = {
        s.operator: trusted_root.Operator(
            s.operator,
            s.parent,
            trusted_root.OwnershipEvidence("ccadb_ca_owner_of_record", f"record {s.operator}", s.chain.root.fingerprint),
        )
        for s in sites
    }
    return trusted_root.TimeStampRules(
        pinned=tuple(PinnedRoot(s.chain.root.fingerprint, s.operator) for s in sites),
        operators=operators,
        evidence_kinds_accepted=frozenset({"ccadb_ca_owner_of_record", "operator_published_root"}),
        required_independent=required,
        window_before_commit_s=before,
        window_after_commit_s=after,
        close_after_commit_s=close_s,
        max_spread_s=spread,
        u_constant_s=u,
    )


@contextmanager
def world(
    specs: list[tuple[str, str, str, Callable[[bytes, int], Any] | None, Callable[[Any], Behaviour | None] | None]],
) -> Iterator[tuple[Any, Any, list[Site], list[Authority]]]:
    """Gateway, runner and one fake authority per spec ``(name, operator, parent, options, script)``."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = CaptureStore(root / "captures")
        gateway = Gateway(
            store,
            DispatchJournal(root / "d.jsonl"),
            retry_policy=RetryPolicy(base_s=0.05, cap_s=0.1, rate_limit_default_wait_s=0.1),
            rng=random.Random(1),
        )
        sites: list[Site] = []
        authorities: list[Authority] = []
        transports = {}
        for name, operator, parent, options, script in specs:
            chain = tsa_pki.Chain.build(name)
            server = FakeTsa(chain, now, script=script, options=options)
            server.start()
            site = Site(name, operator, parent, chain, server)
            sites.append(site)
            authority = Authority(name, server.url)
            authorities.append(authority)
            got = attach_tsa(gateway, authority, CLOCK, breaker=BreakerConfig(consecutive_failures=50), allow_insecure_loopback=True)
            transports[got.adapter.provider] = got.transport
        pool = ThreadExecutor(8)
        runner = Runner(gateway, transports, CLOCK, pool)
        loop = threading.Thread(target=runner.run_forever, daemon=True)
        loop.start()
        try:
            yield runner, store, sites, authorities
        finally:
            runner.stop()
            loop.join(timeout=3)
            pool.shutdown()
            for site in sites:
                site.server.stop()
            store.close()


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str) -> None:
        try:
            call()
        except self.mod.AttestError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def run(
    mod: types.ModuleType,
    runner: Any,
    store: Any,
    sites: list[Site],
    authorities: list[Authority],
    *,
    rules: Any,
    committed: int | None = None,
) -> Any:
    policy = TokenPolicy(tuple(PinnedRoot(s.chain.root.fingerprint, s.operator) for s in sites))
    return mod.attest(
        runner,
        store,
        CLOCK,
        authorities,
        DIGEST,
        committed_at_ms=to_ms(now()) if committed is None else committed,
        policy=policy,
        rules=rules,
    )


def good_specs() -> list[Any]:
    return [("alpha", "op-a", "Org A", None, None), ("beta", "op-b", "Org B", None, None), ("gamma", "op-c", "Org C", None, None)]


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("detail limit", mod.MAX_DETAIL, 200)
    for cls in (mod.Stamp, mod.StampFailure, mod.Attestation):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    return c.bad


def check_good_path(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    with world(good_specs()) as (runner, store, sites, authorities):
        rules = rules_for(sites)
        committed = to_ms(now())
        got = run(mod, runner, store, sites, authorities, rules=rules, committed=committed)
        c.eq("the level is C3", got.level, "C3")
        c.eq("two operators or more carry it", len(got.timing.chosen) >= 2, True)
        c.eq(
            "every answer that arrived is kept as it came, by authority",
            sorted(got.tokens()) == sorted(s.authority for s in got.stamps),
            True,
        )
        c.eq(
            "the digest and the commit time are the caller's",
            (got.digest, got.committed_at_ms, got.closes_at_ms),
            (DIGEST, committed, committed + 3_000),
        )
        c.eq("at least two stamps were taken before it closed", len(got.stamps) >= 2, True)
        c.eq("no authority failed", got.failures, ())
        c.eq(
            "every authority is either stamped or not waited for",
            sorted([s.authority for s in got.stamps] + list(got.unfinished)),
            ["alpha", "beta", "gamma"],
        )
        attested = got.attested_at_ms
        c.eq(
            "attested at the latest chosen time",
            attested,
            max(to_ms(t.gen_time) for t in got.timing.tokens if t.name in got.timing.chosen and t.gen_time),
        )
        c.eq("the uncertainty is the rules'", got.uncertainty_ms, 5_000)
        c.eq(
            "eligible after: attested, plus the uncertainty, plus the spread",
            got.eligible_after_ms,
            (attested or 0) + 5_000 + round((got.timing.spread_s or 0) * 1000),
        )
        c.eq("the origin is a minute close after that", (got.origin_ms > got.eligible_after_ms, got.origin_ms % 60_000), (True, 0))
        stamped = {s.authority: s for s in got.stamps}
        for name, stamp in stamped.items():
            c.eq(f"{name}: a nonce was used and the answer is a response", (stamp.nonce > 0, stamp.response[:1]), (True, b"\x30"))
        seen = [len(s.server.seen) for s in sites]
        c.eq("each authority was asked at most once", all(n <= 1 for n in seen), True)
        c.eq("and the nonces differ from one authority to another", len({s.nonce for s in got.stamps}) == len(got.stamps), True)
    return c.bad


def check_failures_and_rejections(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    # one authority is down: the other two carry the level, the third is listed
    down = Behaviour(500, b"down", {"content-type": "text/plain"})
    specs = [
        ("alpha", "op-a", "Org A", None, None),
        ("beta", "op-b", "Org B", None, None),
        ("gamma", "op-c", "Org C", None, lambda seen: down),
    ]
    with world(specs) as (runner, store, sites, authorities):
        got = run(mod, runner, store, sites, authorities, rules=rules_for(sites))
        c.eq("one authority down: still C3", got.level, "C3")
        c.eq(
            "the one that is down is not among the stamps, and is either listed or not waited for",
            ([x.authority for x in got.stamps], "gamma" in [f.authority for f in got.failures] + list(got.unfinished)),
            (["alpha", "beta"], True),
        )
    # a token for another digest, one with a wrong nonce, one outside the window: none counts
    bad = [
        ("alpha", "op-a", "Org A", lambda d, n: tsa_pki.TokenOptions(gen_time=now(), nonce=n, imprint=bytes(32)), None),
        ("beta", "op-b", "Org B", lambda d, n: tsa_pki.TokenOptions(gen_time=now(), nonce=n + 1), None),
        ("gamma", "op-c", "Org C", lambda d, n: tsa_pki.TokenOptions(gen_time=now() - timedelta(minutes=5), nonce=n), None),
    ]
    with world(bad) as (runner, store, sites, authorities):
        got = run(mod, runner, store, sites, authorities, rules=rules_for(sites, close_s=2))
        c.eq("tokens that fail verification or the window give no level", got.level, "C1")
        c.eq("and all three answers are kept", sorted(got.tokens()), ["alpha", "beta", "gamma"])
        why = {t.name: (t.valid, t.timely, (t.reason or "")[:10]) for t in got.timing.tokens}
        c.eq(
            "alpha and beta are rejected by the verifier, gamma verifies but is not in time",
            (why["alpha"][0], why["beta"][0], why["gamma"][0], why["gamma"][1]),
            (False, False, True, False),
        )
        c.eq(
            "with nothing counted the origin follows the commit time itself",
            (got.attested_at_ms, got.eligible_after_ms),
            (None, got.committed_at_ms),
        )
    # two authorities of one organisation are one operator: not independent
    twins = [("alpha", "op-a", "Same Org", None, None), ("beta", "op-b", "Same Org", None, None)]
    with world(twins) as (runner, store, sites, authorities):
        got = run(mod, runner, store, sites, authorities, rules=rules_for(sites, close_s=2))
        c.eq("two operators with one parent are not independent", (got.level, got.timing.independent_operators), ("C1", 1))
    # two authorities and one of them down: not enough, and the failure says what happened
    pair = [("alpha", "op-a", "Org A", None, None), ("beta", "op-b", "Org B", None, lambda seen: down)]
    with world(pair) as (runner, store, sites, authorities):
        got = run(mod, runner, store, sites, authorities, rules=rules_for(sites, close_s=2))
        c.eq(
            "one of two down: C1, the other stamped, the down one a failure with words",
            (
                got.level,
                [x.authority for x in got.stamps],
                [f.authority for f in got.failures],
                got.unfinished,
                all(f.detail for f in got.failures),
            ),
            ("C1", ["alpha"], ["beta"], (), True),
        )
    # only one authority exists
    with world([("alpha", "op-a", "Org A", None, None)]) as (runner, store, sites, authorities):
        got = run(mod, runner, store, sites, authorities, rules=rules_for(sites, close_s=2))
        c.eq("one authority is not enough", (got.level, len(got.stamps)), ("C1", 1))
    # an authority that never answers in time is not waited for beyond the close
    slow = Behaviour(200, b"", {}, delay_s=5.0)
    specs = [("alpha", "op-a", "Org A", None, None), ("beta", "op-b", "Org B", None, lambda seen: slow)]
    with world(specs) as (runner, store, sites, authorities):
        started = CLOCK.now()
        got = run(mod, runner, store, sites, authorities, rules=rules_for(sites, close_s=2))
        waited = (CLOCK.now() - started).total_seconds()
        c.eq("the phase closes at the close, not when the slow one answers", (waited < 4.0, got.level), (True, "C1"))
        c.eq(
            "the one that did not answer is listed, and nothing is left unfinished",
            ([f.authority for f in got.failures], got.unfinished),
            (["beta"], ()),
        )
    return c.bad


class Spy:
    """Passes calls to the real runner and keeps them."""

    def __init__(self, runner: Any) -> None:
        self.runner, self.calls = runner, []  # type: ignore[var-annotated]

    def submit(self, call: Any) -> Any:
        self.calls.append(call)
        return self.runner.submit(call)


def check_calls(mod: types.ModuleType) -> list[str]:
    """What is asked of the gateway, and that the first nonce is never refused, whatever the random bits are."""
    c = Case(mod)
    with world(good_specs()) as (runner, store, sites, authorities):
        spy = Spy(runner)
        committed = to_ms(now())
        original = mod.secrets.randbits
        asked: list[int] = []
        mod.secrets.randbits = lambda bits: asked.append(bits) or 0
        try:
            got = run(mod, spy, store, sites, authorities, rules=rules_for(sites), committed=committed)
        finally:
            mod.secrets.randbits = original
        c.eq(
            "one call to each authority's own provider, a stamp of the digest, at the attestation class, for the ledger",
            sorted((x.provider, x.operation, dict(x.args)["digest"], x.priority.value, x.caller) for x in spy.calls),
            sorted((a.provider, "stamp", DIGEST.hex(), "PA", "ledger") for a in authorities),
        )
        c.eq("with a deadline at the close of the attestation phase", {x.deadline for x in spy.calls}, {from_ms(committed + 3_000)})
        c.eq("the lowest nonce the generator can give is 1 and it is accepted", sorted({dict(x.args)["nonce"] for x in spy.calls}), [1])
        c.eq("and the answers were stamps", len(got.stamps) >= 2, True)
        c.eq("52 random bits for each nonce", sorted(set(asked)), [52])
        spy2 = Spy(runner)
        mod.attest(
            spy2,
            store,
            CLOCK,
            authorities,
            DIGEST,
            committed_at_ms=to_ms(now()),
            policy=TokenPolicy(tuple(PinnedRoot(s.chain.root.fingerprint, s.operator) for s in sites)),
            rules=rules_for(sites),
            caller="cycle",
        )
        c.eq("the caller can be named", {x.caller for x in spy2.calls}, {"cycle"})
    return c.bad


def check_early_close(mod: types.ModuleType) -> list[str]:
    """Two quick authorities end the phase without waiting for two slow ones, which are listed, in name order, as not waited for."""
    c = Case(mod)
    slow = Behaviour(200, b"", {}, delay_s=2.5)
    specs = [
        ("delta", "op-d", "Org D", None, lambda seen: slow),
        ("alpha", "op-a", "Org A", None, lambda seen: slow),
        ("beta", "op-b", "Org B", None, None),
        ("gamma", "op-c", "Org C", None, None),
    ]
    with world(specs) as (runner, store, sites, authorities):
        started = CLOCK.now()
        got = run(mod, runner, store, sites, authorities, rules=rules_for(sites, close_s=4))
        waited = (CLOCK.now() - started).total_seconds()
        c.eq("C3 from the two quick ones, in well under the time the slow ones need", (got.level, waited < 2.0), ("C3", True))
        c.eq("the two slow ones are not waited for, and are listed by name", (got.unfinished, got.failures), (("alpha", "delta"), ()))
        c.eq("and only the quick ones were stamped", [s.authority for s in got.stamps], ["beta", "gamma"])
    return c.bad


class Stuck:
    """A runner whose calls never complete."""

    def submit(self, call: Any) -> Any:
        return Future()


class Scripted:
    """A runner that completes each call at once with a result chosen by provider."""

    def __init__(self, results: dict[str, Any]) -> None:
        self.results = results

    def submit(self, call: Any) -> Any:
        future: Future[Any] = Future()
        future.set_result(self.results[call.provider])
        return future


def result(outcome: Outcome, reason: str = "", error: ConnectorError | None = None) -> CallResult:
    return CallResult(1, outcome, None, 1, error, reason, 0.0, now())


def check_reasons_and_timing(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    with world([("alpha", "op-a", "Org A", None, None), ("beta", "op-b", "Org B", None, None)]) as (_, store, sites, authorities):
        rules = rules_for(sites, close_s=1)
        # answers that never come: the phase ends at its close, and the two are listed by name
        started = CLOCK.now()
        got = run(mod, Stuck(), store, sites, list(reversed(authorities)), rules=rules)
        waited = (CLOCK.now() - started).total_seconds()
        c.eq("nothing answers: the phase lasts to its close", (waited >= 0.9, waited < 3.0), (True, True))
        c.eq(
            "both are failures with the same words, in name order, and none is merely unfinished",
            ([(f.authority, f.detail) for f in got.failures], got.unfinished),
            ([("alpha", "no answer before the attestation closed"), ("beta", "no answer before the attestation closed")], ()),
        )
        c.eq("with no token the level is C1 and nothing was stamped", (got.level, got.stamps), ("C1", ()))
        # failed calls say why, whitespace folded
        results = {
            "tsa-alpha": result(Outcome.FAILED, "EXHAUSTED", ConnectorError(ErrorClass.UPSTREAM, "the  authority\n  said no")),
            "tsa-beta": result(Outcome.REFUSED, "BAD ARGS"),
        }
        got = run(mod, Scripted(results), store, sites, authorities, rules=rules)
        c.eq(
            "a failure with an error reports the error's words; one without reports the reason",
            [(f.authority, f.detail) for f in got.failures],
            [("alpha", "FAILED: the authority said no"), ("beta", "REFUSED: BAD ARGS")],
        )
        c.eq("they were answers, so nothing is unfinished", (got.unfinished, got.level), ((), "C1"))
        # the figures of an attestation that has two tokens a couple of seconds apart
        committed = to_ms(now())
        att = _attestation_with(mod, committed, [("a", "ta", 1), ("b", "tb", 3)])
        c.eq("attested at the latest token", att.attested_at_ms, committed + 3_000)
        c.eq(
            "eligible after: the attested time, the uncertainty of 5 s and the spread of 2 s",
            att.eligible_after_ms,
            committed + 3_000 + 5_000 + 2_000,
        )
        c.eq("the origin is the next minute close after that", att.origin_ms, ((committed + 10_000) // 60_000 + 1) * 60_000)
        c.eq(
            "judged against a commit time 100 s earlier the same tokens are too late",
            att.timing_at(committed - 100_000, fake_rules()).level,
            "C1",
        )
        c.eq("and against its own commit time, in time", att.timing_at(committed, fake_rules()).level, "C3")
        c.eq(
            "a commitment with no counted token is eligible at once",
            _attestation_with(mod, committed, [("a", "ta", 1)]).eligible_after_ms,
            committed,
        )
    return c.bad


def fake_rules() -> Any:
    from tests.commit_checks import RULES

    return RULES


def _attestation_with(mod: types.ModuleType, committed: int, offsets: list[tuple[str, str, float]]) -> Any:
    """An attestation of the module under test whose tokens are ``(operator, name, seconds after the commit)``."""
    from hoshitori.timing import assess
    from tests.commit_checks import RULES, verified

    evidence = tuple(verified(op, name, committed + round(after * 1000)) for op, name, after in offsets)
    stamps = tuple(mod.Stamp(name, 1, b"\x30\x00") for _, name, _ in offsets)
    return mod.Attestation(
        DIGEST, committed, committed + 90_000, stamps, (), (), assess(from_ms(committed), None, evidence, RULES), 5_000, evidence
    )


def check_order_and_edge(mod: types.ModuleType) -> list[str]:
    """Failures are listed in name order whatever the order they came in; a phase that is already over asks nothing more."""
    from hoshitori.clock import ManualClock

    c = Case(mod)
    names = [f"auth{n:02d}" for n in (7, 3, 11, 0, 9, 5, 2, 10, 1, 8, 4, 6)]
    authorities = [Authority(name, f"https://{name}.example/tsr") for name in names]
    results = {a.provider: result(Outcome.REFUSED, "no") for a in authorities}
    with world([("alpha", "op-a", "Org A", None, None)]) as (_, store, sites, _):
        rules = rules_for(sites, close_s=1)
        policy = TokenPolicy(tuple(PinnedRoot(s.chain.root.fingerprint, s.operator) for s in sites))
        committed = to_ms(now())
        got = mod.attest(Scripted(results), store, CLOCK, authorities, DIGEST, committed_at_ms=committed, policy=policy, rules=rules)
        c.eq("twelve refusals are listed in name order", [f.authority for f in got.failures], sorted(names))
        over = ManualClock(from_ms(committed + 1_000))
        got = mod.attest(Stuck(), store, over, authorities[:2], DIGEST, committed_at_ms=committed, policy=policy, rules=rules)
        c.eq(
            "a phase that closes exactly now waits for nothing",
            ([f.authority for f in got.failures], got.unfinished, got.level),
            (["auth03", "auth07"], (), "C1"),
        )

    def slow(delay_s: float) -> Callable[[bytes, int], Any]:
        def options(digest: bytes, nonce: int) -> Any:
            time.sleep(delay_s)
            return tsa_pki.TokenOptions(gen_time=now(), nonce=nonce)

        return options

    three = [
        ("alpha", "op-a", "Org A", slow(0.9), None),
        ("beta", "op-b", "Org B", slow(0.45), None),
        ("gamma", "op-c", "Org C", slow(0.0), None),
    ]
    with world(three) as (runner, store, sites, authorities):
        got = run(mod, runner, store, sites, authorities, rules=rules_for(sites, close_s=5, required=3))
        c.eq(
            "stamps are listed in name order although the last name answered first",
            ([s.authority for s in got.stamps], got.level),
            (["alpha", "beta", "gamma"], "C3"),
        )
    return c.bad


def check_validation(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    with world([("alpha", "op-a", "Org A", None, None)]) as (runner, store, sites, authorities):
        rules = rules_for(sites)
        c.refuses(
            "a digest of 31 bytes",
            lambda: mod.attest(runner, store, CLOCK, authorities, bytes(31), committed_at_ms=0, policy=TokenPolicy(()), rules=rules),
            "the digest is a SHA-256 value (32 bytes)",
        )
        c.refuses(
            "a digest of 33 bytes",
            lambda: mod.attest(runner, store, CLOCK, authorities, bytes(33), committed_at_ms=0, policy=TokenPolicy(()), rules=rules),
            "the digest is a SHA-256 value (32 bytes)",
        )
        c.refuses(
            "no authorities",
            lambda: mod.attest(runner, store, CLOCK, [], DIGEST, committed_at_ms=0, policy=TokenPolicy(()), rules=rules),
            "authorities are named, and each is named once",
        )
        c.refuses(
            "an authority twice",
            lambda: mod.attest(
                runner, store, CLOCK, [authorities[0], authorities[0]], DIGEST, committed_at_ms=0, policy=TokenPolicy(()), rules=rules
            ),
            "authorities are named, and each is named once",
        )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_good_path,
        check_calls,
        check_early_close,
        check_reasons_and_timing,
        check_order_and_edge,
        check_failures_and_rejections,
        check_validation,
    ):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
