"""Checks of the exploratory root builder (``hoshitori.exploratory_root``), runnable against a mutant. The authorities are fake ones on
loopback, with their own chains; the registry of operators is made to match them (or not)."""

from __future__ import annotations

import base64
import hashlib
import types
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from asn1crypto import tsp

from hoshitori import trusted_root
from hoshitori.clock import SystemClock
from tests import root_support as R
from tests import tsa_pki
from tests.fake_tsa import FakeTsa, rejection
from tests.ryo_fake_server import Behaviour

EXPIRES = datetime(2027, 1, 1, tzinfo=UTC)
KEY = bytes(range(32))
VKEY = R.log_signer(7).verifier.vkey()


def post(url: str, request: bytes) -> bytes:
    sent = urllib.request.Request(url, data=request, headers={"Content-Type": "application/timestamp-query"}, method="POST")  # noqa: S310
    with urllib.request.urlopen(sent, timeout=10) as reply:  # noqa: S310 - loopback
        return bytes(reply.read())


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
        except self.mod.ExploratoryRootError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def registry_for(chains: dict[str, tsa_pki.Chain], **over: Any) -> dict[str, Any]:
    operators: dict[str, Any] = {
        name: {
            "parent": f"Organisation {name}",
            "ownership_evidence": {
                "kind": "ccadb_ca_owner_of_record",
                "ref": f"record {name}",
                "subject_sha256": chain.root.fingerprint,
                "organisation": "extra member that must not reach the root",
            },
        }
        for name, chain in chains.items()
    }
    operators.update(over)
    return {"operators": operators}


def build(mod: types.ModuleType, servers: dict[str, FakeTsa], registry: dict[str, Any], names: list[str], **over: Any) -> dict[str, object]:
    values: dict[str, Any] = {
        "origin": R.LOG_ORIGIN,
        "log_vkey": VKEY,
        "authorities": [(name, servers[name].url) for name in names],
        "registry": registry,
        "post": post,
        "root_public_key": KEY,
        "expires": EXPIRES,
    }
    values.update(over)
    return mod.build(**values)


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("accepted kinds", mod.KINDS, ("ccadb_ca_owner_of_record", "operator_published_root"))
    return c.bad


def check_build(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    chains = {name: tsa_pki.Chain.build(name) for name in ("alpha", "beta", "gamma")}
    servers = {name: FakeTsa(chain, SystemClock().now) for name, chain in chains.items()}
    for server in servers.values():
        server.start()
    try:
        registry = registry_for(chains)
        document = build(mod, servers, registry, ["alpha", "beta"])
        payload = mod.write(document)
        root = trusted_root.parse_root(payload)
        c.eq(
            "a root that parses, version one, with the log and its key",
            (root.version, root.origin, [era.key.vkey() for era in root.eras], root.expires),
            (1, R.LOG_ORIGIN, [VKEY], EXPIRES),
        )
        c.eq(
            "pinned: the roots the authorities sent, by operator",
            sorted((p.operator, p.sha256) for p in root.time_stamp.pinned),
            sorted((n, chains[n].root.fingerprint) for n in ("alpha", "beta")),
        )
        c.eq(
            "each with its certificate",
            sorted(hashlib.sha256(p.der or b"").hexdigest() for p in root.time_stamp.pinned),
            sorted(chains[n].root.fingerprint for n in ("alpha", "beta")),
        )
        c.eq(
            "the operators and their evidence, and only the members the root knows",
            {n: (o.parent, o.evidence.kind, o.evidence.ref, o.evidence.subject_sha256) for n, o in root.time_stamp.operators.items()},
            {n: (f"Organisation {n}", "ccadb_ca_owner_of_record", f"record {n}", chains[n].root.fingerprint) for n in ("alpha", "beta")},
        )
        c.eq(
            "the rules",
            (
                root.time_stamp.required_independent,
                root.time_stamp.window_before_commit_s,
                root.time_stamp.window_after_commit_s,
                root.time_stamp.close_after_commit_s,
                root.time_stamp.max_spread_s,
                root.time_stamp.u_constant_s,
            ),
            (2, 1, 60, 90, 5, 5),
        )
        c.eq("one era for the log, from the start, with no end", [(e.from_size, e.through_size) for e in root.eras], [(0, None)])
        c.eq(
            "no witnesses, and only live data is scored",
            (root.witnesses_required, root.witness_keys, root.scored_requires_data_mode),
            (0, (), "live"),
        )
        c.eq(
            "a root key that is the one given, once",
            [base64.b64encode(k).decode() for k in root.root_keys.values()],
            [base64.b64encode(KEY).decode()],
        )
        three = trusted_root.parse_root(mod.write(build(mod, servers, registry, ["alpha", "beta", "gamma"], required_independent=3)))
        c.eq("three authorities when three are required", (len(three.time_stamp.pinned), three.time_stamp.required_independent), (3, 3))
        # refusals
        c.refuses("too few authorities", lambda: build(mod, servers, registry, ["alpha"]), "2 different authorities are needed to reach C3")
        c.refuses(
            "one authority twice",
            lambda: mod.build(
                origin="o",
                log_vkey=VKEY,
                authorities=[("alpha", servers["alpha"].url)] * 2,
                registry=registry,
                post=post,
                root_public_key=KEY,
                expires=EXPIRES,
            ),
            "2 different authorities are needed to reach C3",
        )
        c.refuses(
            "no evidence",
            lambda: build(mod, servers, registry_for(chains, beta={"parent": "x", "ownership_evidence": None}), ["alpha", "beta"]),
            "beta has no ownership evidence in the registry",
        )
        c.refuses(
            "an operator the registry lacks",
            lambda: build(mod, servers, {"operators": {"alpha": registry["operators"]["alpha"]}}, ["alpha", "beta"]),
            "beta has no ownership evidence in the registry",
        )
        c.refuses(
            "a registry that is not one",
            lambda: build(mod, servers, {}, ["alpha", "beta"]),
            "alpha has no ownership evidence in the registry",
        )
        bad_kind = registry_for(chains)
        bad_kind["operators"]["beta"]["ownership_evidence"]["kind"] = "a_blog_post"
        c.refuses(
            "evidence of a kind that is not accepted",
            lambda: build(mod, servers, bad_kind, ["alpha", "beta"]),
            "the evidence for beta is of a kind that is not accepted",
        )
        wrong = registry_for(chains)
        wrong["operators"]["beta"]["ownership_evidence"]["subject_sha256"] = "0" * 64
        c.refuses(
            "a root other than the one the evidence names",
            lambda: build(mod, servers, wrong, ["alpha", "beta"]),
            "the answer of beta does not contain the root its ownership evidence names",
        )
        servers["beta"].script = lambda seen: Behaviour(200, rejection(["system_failure"]), {"content-type": "application/timestamp-reply"})
        c.eq(
            "an authority that refuses",
            _message(mod, lambda: build(mod, servers, registry, ["alpha", "beta"])).startswith("beta refused the request: "),
            True,
        )
        servers["beta"].script = lambda seen: Behaviour(200, b"not a time-stamp answer", {})
        c.refuses(
            "an answer that is not one",
            lambda: build(mod, servers, registry, ["alpha", "beta"]),
            "beta did not give a readable time-stamp answer: ValueError",
        )
        servers["beta"].script = None
        # an answer with no root in it: the root cannot be pinned from it
        chain_b = chains["beta"]
        servers["beta"].options = lambda digest, nonce: tsa_pki.TokenOptions(
            gen_time=SystemClock().now(), nonce=nonce, certificates=[chain_b.tsa, chain_b.intermediate]
        )
        c.refuses(
            "an answer without the root",
            lambda: build(mod, servers, registry, ["alpha", "beta"]),
            "the answer of beta does not contain the root its ownership evidence names",
        )
        servers["beta"].options = None
        # a granted answer that carries no token
        granted_no_token = tsp.PKIStatusInfo({"status": "granted"}).dump()
        servers["beta"].script = lambda seen: Behaviour(200, b"\x30" + bytes([len(granted_no_token)]) + granted_no_token, {})
        c.refuses(
            "a granted answer with no token",
            lambda: build(mod, servers, registry, ["alpha", "beta"]),
            "beta did not give a readable time-stamp answer: ValueError",
        )

        # granted with modifications is still granted
        def with_mods(seen: Any) -> Behaviour:
            query = tsp.TimeStampReq.load(seen.body)
            real = tsa_pki.build_token(
                query["message_imprint"]["hashed_message"].native,
                chain_b,
                tsa_pki.TokenOptions(gen_time=SystemClock().now(), nonce=query["nonce"].native),
            )
            response = tsp.TimeStampResp.load(real)
            response["status"]["status"] = "granted_with_mods"
            return Behaviour(200, response.dump(force=True), {})

        servers["beta"].script = with_mods
        c.eq(
            "granted with modifications is accepted",
            sorted(p["operator"] for p in build(mod, servers, registry, ["alpha", "beta"])["time_stamp"]["pinned_roots"]),
            ["alpha", "beta"],
        )  # type: ignore[index]
        # an answer with bytes after it
        servers["beta"].script = lambda seen: Behaviour(
            200,
            tsa_pki.build_token(
                tsp.TimeStampReq.load(seen.body)["message_imprint"]["hashed_message"].native,
                chain_b,
                tsa_pki.TokenOptions(gen_time=SystemClock().now(), nonce=tsp.TimeStampReq.load(seen.body)["nonce"].native),
            )
            + b"\x00",
            {},
        )
        c.eq(
            "an answer with trailing bytes is not read",
            _message(mod, lambda: build(mod, servers, registry, ["alpha", "beta"])).startswith(
                "beta did not give a readable time-stamp answer"
            ),
            True,
        )
        servers["beta"].script = None
        c.refuses(
            "an unparsable document is never written",
            lambda: mod.write({"type": "nope"}),
            "the root does not parse: " + _parse_message({"type": "nope"}),
        )
    finally:
        for server in servers.values():
            server.stop()
    return c.bad


def _message(mod: types.ModuleType, call: Callable[[], object]) -> str:
    try:
        call()
    except mod.ExploratoryRootError as exc:
        return str(exc)
    return ""


def _parse_message(document: dict[str, object]) -> str:
    from hoshitori import canonical

    try:
        trusted_root.parse_root(canonical.dumps(document))  # type: ignore[arg-type]
    except trusted_root.RootError as exc:
        return str(exc)
    return ""


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_build):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}")
    return bad
