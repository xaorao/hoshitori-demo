"""The page a person sees first, before any check has run (contract 35.2, spec ``CLAIM_GRAPH_SPEC``).

It can say only what follows from the person's own text and from what this product is set up to do: which parts of the claim could be
tested and against what, and that none has been. It has no field that can hold a check result, every status on it is UNCHECKED, and its
words come from a closed set of sentences plus the person's own quoted words, so a preliminary page cannot read as a finding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from hoshitori.claim_graph import TYPE_WORDS, UNCHECKED, Graph, NodeType
from hoshitori.claim_sketch import sketch

BANNER: Final = "Preliminary: read by a machine, nothing checked yet."
HEADLINE: Final = "Not yet independently supported."
NEXT_STEP: Final = "Next step: investigate, do not accept as confirmed."
NOTHING_TESTABLE: Final = "Nothing here can be tested against market data, so no check is planned."
FLAGGED: Final = ("supported", "contradicted", "verified", "confirmed", "true", "false", "proven", "corroborated")
# what each kind of part could be tested against; a kind with no entry cannot be tested against market data
TESTED_AGAINST: Final[dict[NodeType, str]] = {
    NodeType.OBSERVATION: "exchange and on-chain records",
    NodeType.FORECAST: "a price series from a named exchange",
    NodeType.CONDITION: "exchange and on-chain records",
}
# the closed set of sentences, with the person's quoted words left out
TEMPLATES: Final = (
    re.escape(HEADLINE),
    re.escape(NEXT_STEP),
    re.escape(NOTHING_TESTABLE),
    r"The [a-z ]+ “” has not been checked; it can be tested against [a-z -]+\.",
    r"The [a-z ]+ “” has not been checked; no source here can test it\.",
    r"The [a-z ]+ “” has not been checked; as worded it names nothing a price series could settle\.",
    r"We think this is implied: “” has not been checked\.",
)
_QUOTED: Final = "“{}”"


@dataclass(frozen=True)
class Part:
    node_id: str
    kind: str
    status: str  # always UNCHECKED: the page has no place for a result
    quote: str  # the person's own words, or ours when the step is inferred
    line: str


@dataclass(frozen=True)
class Preliminary:
    banner: str
    headline: str
    parts: tuple[Part, ...]
    next_step: str
    triage: str  # INVESTIGATE or NOT_MARKET_TESTABLE

    def sentences(self) -> tuple[str, ...]:
        return (self.headline, *(p.line for p in self.parts), self.next_step)


def preliminary(graph: Graph, assets: tuple[str, ...] = ()) -> Preliminary:
    """The preliminary page of a graph; ``assets`` are the symbols the registry knows, so a forecast that names no event can be told."""
    parts = []
    testable = False
    for node in graph.nodes:
        quote = graph.words(node)
        if node.type is NodeType.ASSUMPTION_INFERRED:
            line = f"We think this is implied: {_QUOTED.format(quote)} has not been checked."
        elif node.type is NodeType.FORECAST and sketch(quote, assets).status == "UNRESOLVED":
            what = f"{TYPE_WORDS[node.type].capitalize()} {_QUOTED.format(quote)}"
            line = f"{what} has not been checked; as worded it names nothing a price series could settle."
        elif node.type in TESTED_AGAINST:
            testable = True
            against = TESTED_AGAINST[node.type]
            line = f"{TYPE_WORDS[node.type].capitalize()} {_QUOTED.format(quote)} has not been checked; it can be tested against {against}."
        else:
            line = f"{TYPE_WORDS[node.type].capitalize()} {_QUOTED.format(quote)} has not been checked; no source here can test it."
        parts.append(Part(node.node_id, node.type.value, UNCHECKED, quote, line))
    return Preliminary(
        BANNER, HEADLINE, tuple(parts), NEXT_STEP if testable else NOTHING_TESTABLE, "INVESTIGATE" if testable else "NOT_MARKET_TESTABLE"
    )


def allowed(sentence: str, quotes: tuple[str, ...] = ()) -> bool:
    """Whether a sentence is one of the closed set, once the person's own quoted words are taken out."""
    bare = sentence
    for quote in quotes:  # a quote is replaced with its closing mark, so one quote can never be found inside another
        bare = bare.replace(_QUOTED.format(quote), _QUOTED.format(""))
    return any(re.fullmatch(t, bare) for t in TEMPLATES)


def lint(page: Preliminary) -> tuple[str, ...]:
    """Every way a preliminary page can break its own rule; empty when it does not."""
    out: list[str] = []
    quotes = tuple(p.quote for p in page.parts)
    if page.banner != BANNER:
        out.append("the banner is not the preliminary banner")
    for part in page.parts:
        if part.status != UNCHECKED:
            out.append(f"{part.node_id}: a preliminary page cannot carry a status other than UNCHECKED")
    for sentence in page.sentences():
        if not allowed(sentence, quotes):
            out.append(f"not a sentence a preliminary page may say: {sentence}")
    return tuple(out)
