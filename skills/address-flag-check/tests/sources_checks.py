"""Checks of ``address_flag_check.sources``, runnable against a mutant: the source registry, and its rule that a source must be registered as
maintained, with the basis written down, before it can report that an address is absent (D3 5: the deprecated Chainalysis oracle answered
"not sanctioned" for two current designations, which is why the rule exists).
"""

from __future__ import annotations

import dataclasses
import types

from tests.checks_support import Case

ALL = ("ethereum", "arbitrum", "base", "bsc", "optimism", "polygon", "tron", "bitcoin")


def _source(mod: types.ModuleType, **over: object) -> object:
    base: dict[str, object] = {
        "source_id": "frozen_list",
        "name": "A frozen list",
        "kind": "sanctions_list",
        "chains": ("ethereum",),
        "maintained": True,
        "maintained_basis": "updated by its publisher",
        "endpoint": "https://example.org/list",
        "terms_url": "https://example.org/terms",
        "terms_note": "a test source",
        "used": True,
        "not_used_reason": None,
    }
    base.update(over)
    return mod.Source(**base)


def check_rule(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    err = mod.RegistrationError
    absence = "frozen_list: a source that can report absence must be registered as maintained, with the basis written down"
    for label, over in (
        ("an unmaintained list", {"maintained": False}),
        ("a maintained list with no basis", {"maintained_basis": "  "}),
        ("an unmaintained freeze flag", {"maintained": False, "kind": "issuer_freeze"}),
    ):
        c.raises(f"{label} is refused at registration", lambda over=over: mod.Registry().register(_source(mod, **over)), err, absence)
    reg = mod.Registry()
    c.run(
        "an unmaintained source of facts may be registered",
        lambda: reg.register(_source(mod, maintained=False, kind="chain_facts", source_id="facts")),
    )
    c.eq("but it cannot report absence", reg.can_report_absence("facts"), False)
    c.run(
        "an unmaintained source that is not used may be registered",
        lambda: reg.register(_source(mod, maintained=False, used=False, not_used_reason="deprecated", source_id="old")),
    )
    c.eq("and cannot report absence", reg.can_report_absence("old"), False)
    c.run("a maintained list", lambda: reg.register(_source(mod)))
    c.eq("a maintained list can report absence", reg.can_report_absence("frozen_list"), True)
    c.raises("a source registered twice", lambda: reg.register(_source(mod)), err, "frozen_list: is already registered")
    c.raises(
        "a source that is not used must say why",
        lambda: mod.Registry().register(_source(mod, used=False)),
        err,
        "frozen_list: a source that is not used must say why",
    )
    c.raises(
        "an unknown kind",
        lambda: mod.Registry().register(_source(mod, kind="vibes")),
        err,
        "frozen_list: kind must be one of ['address_reports', 'chain_facts', 'issuer_freeze', 'sanctions_list']",
    )
    c.raises("an unknown source", lambda: reg.get("nope"), KeyError)
    c.eq("an unknown source cannot report absence", reg.can_report_absence("nope"), False)
    c.eq("registration order is kept", [s.source_id for s in reg.all()], ["facts", "old", "frozen_list"])
    return c.bad


def check_default(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    reg = mod.REGISTRY
    rows = [(s.source_id, s.name, s.kind, s.chains, s.maintained, s.used) for s in reg.all()]
    c.eq(
        "the registered sources",
        rows,
        [
            ("ofac_sdn", "OFAC SDN list", "sanctions_list", ALL, True, True),
            ("usdt_freeze_ethereum", "USDT freeze", "issuer_freeze", ("ethereum",), True, True),
            ("usdc_freeze_ethereum", "USDC freeze", "issuer_freeze", ("ethereum",), True, True),
            ("usdt_freeze_tron", "USDT freeze", "issuer_freeze", ("tron",), True, True),
            ("chain_facts_evm", "Chain facts", "chain_facts", ALL[:6], True, True),
            ("chain_facts_bitcoin", "Chain facts", "chain_facts", ("bitcoin",), True, True),
            ("chainalysis_oracle", "Chainalysis sanctions oracle", "sanctions_list", (), False, False),
            ("goplus_address_security", "GoPlus address security", "address_reports", (), True, False),
            ("chainabuse", "Chainabuse reports", "address_reports", (), True, False),
            ("etherscan_labels", "Etherscan labels", "address_reports", (), True, False),
        ],
    )
    c.eq(
        "absence sources",
        [s.source_id for s in reg.all() if reg.can_report_absence(s.source_id)],
        ["ofac_sdn", "usdt_freeze_ethereum", "usdc_freeze_ethereum", "usdt_freeze_tron"],
    )
    for s in reg.all():
        c.true(f"{s.source_id} has terms written down", bool(s.terms_url.startswith("https://") and s.terms_note.strip()))
        c.true(f"{s.source_id} has an endpoint", bool(s.endpoint.startswith("https://")))
        c.true(f"{s.source_id}: used, or the reason it is not", s.used or bool(s.not_used_reason))
        c.true(f"{s.source_id}: a used source has a basis", (not s.used) or bool(s.maintained_basis.strip()))
    c.eq("a source that is not used claims no chains", [s.chains for s in reg.all() if not s.used], [(), (), (), ()])
    goplus = reg.get("goplus_address_security")
    c.true(
        "GoPlus is not used because it needs a key and an agreement",
        "key" in (goplus.not_used_reason or "") and "agreement" in (goplus.not_used_reason or ""),
    )
    c.true("the oracle is not used because it is deprecated", "deprecated" in (reg.get("chainalysis_oracle").not_used_reason or ""))
    c.raises("a Source cannot be changed", lambda: setattr(reg.get("ofac_sdn"), "used", False), dataclasses.FrozenInstanceError)
    return c.bad


PN_TERMS = "https://publicnode.com/terms"
PN_NOTE = "PublicNode grants a revocable, non-exclusive licence to use its RPC service, as is; the answer is public chain state."
# Every text of the registry, written out: the address_flag_sources tool shows them to the user word for word.
TEXTS = [
    (
        "ofac_sdn",
        "OFAC publishes the file itself, with its DateOfIssue; the local copy is confirmed against OFAC's file at most once an hour",
        "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.ZIP",
        "https://www.usa.gov/government-copyright",
        "A US Government work: US copyright is not available for it (17 U.S.C. 105); use must not imply endorsement; "
        "protection outside the US was not verified.",
        None,
    ),
    (
        "usdt_freeze_ethereum",
        "the issuer's own token contract (0xdAC17F958D2ee523a2206206994597C13D831ec7, isBlackListed), read on chain at the block named in "
        "the answer",
        "https://ethereum-rpc.publicnode.com",
        PN_TERMS,
        PN_NOTE,
        None,
    ),
    (
        "usdc_freeze_ethereum",
        "the issuer's own token contract (0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48, isBlacklisted), read on chain at the block named in "
        "the answer",
        "https://ethereum-rpc.publicnode.com",
        PN_TERMS,
        PN_NOTE,
        None,
    ),
    (
        "usdt_freeze_tron",
        "Tether's own token contract on Tron (TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t, isBlackListed), read on chain",
        "https://tron-rpc.publicnode.com",
        "https://www.trongrid.io/terms-of-service",
        "PublicNode first (its terms as for Ethereum); TronGrid second and at low volume, because it limits calls without a key by IP "
        "and may refuse them.",
        None,
    ),
    (
        "chain_facts_evm",
        "chain state read from one node per chain at a stated block",
        "https://ethereum-rpc.publicnode.com",
        PN_TERMS,
        PN_NOTE,
        None,
    ),
    (
        "chain_facts_bitcoin",
        "confirmed counts from mempool.space's public API",
        "https://mempool.space/api",
        "https://mempool.space/terms-of-service",
        "Provided as is, with no guarantee of accuracy; limits are enforced with HTTP 429.",
        None,
    ),
    (
        "chainalysis_oracle",
        "",
        "https://go.chainalysis.com/chainalysis-oracle-docs.html",
        "https://go.chainalysis.com/chainalysis-oracle-docs.html",
        "Chainalysis's page says the oracle is no longer maintained and that no new addresses will be added.",
        "deprecated: it answered not sanctioned for two current OFAC designations",
    ),
    (
        "goplus_address_security",
        "a commercial service",
        "https://api.gopluslabs.io/api/v1/address_security",
        "https://docs.gopluslabs.io/reference/api-license-agreement-new",
        "The licence agreement makes the key the sole means of access and requires attribution and the GoPlus logo.",
        "needs an API key and acceptance of the GoPlus API licence agreement; that is the owner's decision",
    ),
    (
        "chainabuse",
        "a commercial service",
        "https://api.chainabuse.com/v0/reports",
        "https://docs.chainabuse.com/",
        "Its documentation calls it a proprietary API; a call without a key answered 401.",
        "a proprietary API that needs a key",
    ),
    (
        "etherscan_labels",
        "a commercial service",
        "https://api.etherscan.io/v2/api",
        "https://etherscan.io/terms",
        "Section 8 forbids reproducing its labels and name tags without consent, also when obtained through third parties.",
        "its terms forbid reusing its labels, and its API needs a key",
    ),
]


def check_texts(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    got = [(s.source_id, s.maintained_basis, s.endpoint, s.terms_url, s.terms_note, s.not_used_reason) for s in mod.REGISTRY.all()]
    c.eq("the number of sources", len(got), len(TEXTS))
    for row, want in zip(got, TEXTS, strict=False):
        c.eq(f"the texts of {want[0]}", row, want)
    c.eq("the PublicNode terms", (mod.PUBLICNODE_TERMS, mod.PUBLICNODE_NOTE), (PN_TERMS, PN_NOTE))
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    out: list[str] = []
    for check in (check_rule, check_default, check_texts):
        try:
            out.extend(check(mod))
        except Exception as exc:
            out.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    return out
