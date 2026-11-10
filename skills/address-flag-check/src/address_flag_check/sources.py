"""The sources the skill reads, and the ones it does not, with the reason (D3 section 3 and 8).

A source that can say an address is absent (a sanctions list or an issuer freeze flag) must be registered as maintained, with the basis
written down, before it may report ``not_listed``: the Chainalysis sanctions oracle shows why, being deprecated and answering "not
sanctioned" for two current OFAC designations. Sources that need a key or an accepted agreement are listed as not used; using one is the
owner's decision.

The terms notes are short readings of the pages named, not legal opinions.
"""

from __future__ import annotations

from dataclasses import dataclass

from .normalize import CHAINS, EVM_CHAINS

KINDS = ("address_reports", "chain_facts", "issuer_freeze", "sanctions_list")
ABSENCE_KINDS = frozenset({"sanctions_list", "issuer_freeze"})
PUBLICNODE_TERMS = "https://publicnode.com/terms"
PUBLICNODE_NOTE = "PublicNode grants a revocable, non-exclusive licence to use its RPC service, as is; the answer is public chain state."


@dataclass(frozen=True)
class Source:
    source_id: str
    name: str
    kind: str
    chains: tuple[str, ...]
    maintained: bool
    maintained_basis: str
    endpoint: str
    terms_url: str
    terms_note: str
    used: bool
    not_used_reason: str | None = None


class RegistrationError(ValueError):
    """A source was registered against the rules."""


class Registry:
    def __init__(self) -> None:
        self._sources: dict[str, Source] = {}

    def register(self, source: Source) -> None:
        sid = source.source_id
        if sid in self._sources:
            raise RegistrationError(f"{sid}: is already registered")
        if source.kind not in KINDS:
            raise RegistrationError(f"{sid}: kind must be one of {list(KINDS)}")
        if source.used and source.kind in ABSENCE_KINDS and (not source.maintained or not source.maintained_basis.strip()):
            raise RegistrationError(
                f"{sid}: a source that can report absence must be registered as maintained, with the basis written down"
            )
        if not source.used and not source.not_used_reason:
            raise RegistrationError(f"{sid}: a source that is not used must say why")
        self._sources[sid] = source

    def get(self, source_id: str) -> Source:
        return self._sources[source_id]

    def all(self) -> tuple[Source, ...]:
        return tuple(self._sources.values())

    def can_report_absence(self, source_id: str) -> bool:
        source = self._sources.get(source_id)
        return source is not None and source.used and source.maintained and source.kind in ABSENCE_KINDS


def _default() -> Registry:
    reg = Registry()
    reg.register(
        Source(
            "ofac_sdn",
            "OFAC SDN list",
            "sanctions_list",
            CHAINS,
            True,
            "OFAC publishes the file itself, with its DateOfIssue; the local copy is confirmed against OFAC's file at most once an hour",
            "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.ZIP",
            "https://www.usa.gov/government-copyright",
            "A US Government work: US copyright is not available for it (17 U.S.C. 105); use must not imply endorsement; "
            "protection outside the US was not verified.",
            True,
        )
    )
    for sid, token, chain, contract, function in (
        ("usdt_freeze_ethereum", "USDT", "ethereum", "0xdAC17F958D2ee523a2206206994597C13D831ec7", "isBlackListed"),
        ("usdc_freeze_ethereum", "USDC", "ethereum", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "isBlacklisted"),
    ):
        reg.register(
            Source(
                sid,
                f"{token} freeze",
                "issuer_freeze",
                (chain,),
                True,
                f"the issuer's own token contract ({contract}, {function}), read on chain at the block named in the answer",
                "https://ethereum-rpc.publicnode.com",
                PUBLICNODE_TERMS,
                PUBLICNODE_NOTE,
                True,
            )
        )
    reg.register(
        Source(
            "usdt_freeze_tron",
            "USDT freeze",
            "issuer_freeze",
            ("tron",),
            True,
            "Tether's own token contract on Tron (TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t, isBlackListed), read on chain",
            "https://tron-rpc.publicnode.com",
            "https://www.trongrid.io/terms-of-service",
            "PublicNode first (its terms as for Ethereum); TronGrid second and at low volume, because it limits calls without a key "
            "by IP and may refuse them.",
            True,
        )
    )
    reg.register(
        Source(
            "chain_facts_evm",
            "Chain facts",
            "chain_facts",
            EVM_CHAINS,
            True,
            "chain state read from one node per chain at a stated block",
            "https://ethereum-rpc.publicnode.com",
            PUBLICNODE_TERMS,
            PUBLICNODE_NOTE,
            True,
        )
    )
    reg.register(
        Source(
            "chain_facts_bitcoin",
            "Chain facts",
            "chain_facts",
            ("bitcoin",),
            True,
            "confirmed counts from mempool.space's public API",
            "https://mempool.space/api",
            "https://mempool.space/terms-of-service",
            "Provided as is, with no guarantee of accuracy; limits are enforced with HTTP 429.",
            True,
        )
    )
    for source in (
        Source(
            "chainalysis_oracle",
            "Chainalysis sanctions oracle",
            "sanctions_list",
            (),
            False,
            "",
            "https://go.chainalysis.com/chainalysis-oracle-docs.html",
            "https://go.chainalysis.com/chainalysis-oracle-docs.html",
            "Chainalysis's page says the oracle is no longer maintained and that no new addresses will be added.",
            False,
            "deprecated: it answered not sanctioned for two current OFAC designations",
        ),
        Source(
            "goplus_address_security",
            "GoPlus address security",
            "address_reports",
            (),
            True,
            "a commercial service",
            "https://api.gopluslabs.io/api/v1/address_security",
            "https://docs.gopluslabs.io/reference/api-license-agreement-new",
            "The licence agreement makes the key the sole means of access and requires attribution and the GoPlus logo.",
            False,
            "needs an API key and acceptance of the GoPlus API licence agreement; that is the owner's decision",
        ),
        Source(
            "chainabuse",
            "Chainabuse reports",
            "address_reports",
            (),
            True,
            "a commercial service",
            "https://api.chainabuse.com/v0/reports",
            "https://docs.chainabuse.com/",
            "Its documentation calls it a proprietary API; a call without a key answered 401.",
            False,
            "a proprietary API that needs a key",
        ),
        Source(
            "etherscan_labels",
            "Etherscan labels",
            "address_reports",
            (),
            True,
            "a commercial service",
            "https://api.etherscan.io/v2/api",
            "https://etherscan.io/terms",
            "Section 8 forbids reproducing its labels and name tags without consent, also when obtained through third parties.",
            False,
            "its terms forbid reusing its labels, and its API needs a key",
        ),
    ):
        reg.register(source)
    return reg


REGISTRY = _default()
