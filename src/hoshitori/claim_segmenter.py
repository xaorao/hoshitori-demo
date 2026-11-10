"""The second reader of a claim, which uses no model (spec ``CLAIM_GRAPH_SPEC`` section 3 step 2, contract 34.11).

It cuts the text into clauses and marks the words a claim graph must account for: causal and conditional connectives, words that
speak of the future, numbers with their units (a number read by the claim graph's own rule, in digits) and named assets. A graph that
leaves one of them out, or that ties one node to words from two clauses, is listed as disagreeing. What this reader cannot be sure of it
marks and asks about instead of guessing: a number written in words, a date or a time written in words, and words that
the claim holds twice of which a part quotes one. A disagreement never changes the graph; it is shown to the person, because three runs of
one model can agree and still share a blind spot that a plain reader of the text does not have.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

from hoshitori import claim_graph
from hoshitori.claim_graph import Graph, NodeType

CAUSAL: Final = ("therefore", "because", "hence", "thus", "consequently", "so", "since")
CONDITIONAL: Final = ("if", "unless", "provided", "assuming")
FUTURE: Final = ("will", "may", "might", "could", "should", "would", "going to")
# a clause starts at these words, so "A therefore B" is two clauses; a comma before one is not needed for the cut
STARTS: Final = ("therefore", "because", "hence", "thus", "consequently", "so", "since", "if", "unless", "while", "which", "but", "and")
UNITS: Final = r"(?:%|k\b|m\b|b\b|x\b|bps\b|usdt?\b|btc\b|eth\b|sol\b|days?\b|hours?\b|weeks?\b|months?\b|minutes?\b)"
# A number is read by the claim graph's own rule (``claim_graph._NUMBER``, used here and not copied): a comma only between digits, so
# "150, then" reads 150, and a minus sign written directly before the digits ("-15" is not 15; one after a letter, a digit, "_" or "%",
# as in "10-15%", is a hyphen). Here it may have a dollar sign before it and a unit after it. That rule also reads number words
# ("fifteen"); the plain reader reads digits only, so a match with no digit is passed over.
_NUMBER: Final = re.compile(rf"\$?(?:{claim_graph._NUMBER.pattern})\s?{UNITS}?", re.IGNORECASE)
_WORD: Final = re.compile(r"[A-Za-z']+")
# A date or a time in words: the days, the months that are not also a common word ("may" is a verb of the future and "march" a verb, so
# neither is read as a month), today and its neighbours, a coming or passing stretch of time, and the end of one. Nothing here is turned
# into a date; the words are marked so that a graph that leaves them out is asked about.
_TIME: Final = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|april|june|july|august|september|october|november|december"
    r"|today|tomorrow|tonight|yesterday|noon|midnight"
    r"|(?:next|last|this|every) (?:day|week|weekend|month|quarter|year)"
    r"|end of (?:the )?(?:day|week|month|quarter|year))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Mark:
    kind: str  # CAUSAL, CONDITIONAL, FUTURE, NUMBER, NUMBER_WORD, TIME_WORD or ASSET
    start: int
    end: int


@dataclass(frozen=True)
class Segments:
    clauses: tuple[tuple[int, int], ...]
    marks: tuple[Mark, ...]


@dataclass(frozen=True)
class Disagreement:
    kind: str
    start: int
    end: int
    detail: str


def _clauses(text: str) -> tuple[tuple[int, int], ...]:
    cuts = [0]
    for m in re.finditer(r"[.!?;]+(?=\s+[A-Z\"“(]|\s*$)|;", text):
        if not re.search(r"\b[A-Za-z]\.$", text[: m.end()]):  # an initial or an abbreviation such as "U.S." is not an end
            cuts.append(m.end())
    for m in _WORD.finditer(text):
        if m.group().lower() in STARTS:
            cuts.append(m.start())
    bounds = sorted({*cuts, len(text)})
    out = []
    for a, b in pairwise(bounds):
        chunk = text[a:b]
        lead = len(chunk) - len(chunk.lstrip(" \t\n,;"))
        body = chunk.strip(" \t\n,;")
        if body:
            out.append((a + lead, a + lead + len(body)))
    return tuple(out)


def _marks(text: str, assets: tuple[str, ...]) -> tuple[Mark, ...]:
    out: list[Mark] = []
    for m in _WORD.finditer(text):
        word = m.group().lower()
        if word in CAUSAL:
            out.append(Mark("CAUSAL", m.start(), m.end()))
        elif word in CONDITIONAL:
            out.append(Mark("CONDITIONAL", m.start(), m.end()))
        elif word in FUTURE:
            out.append(Mark("FUTURE", m.start(), m.end()))
        elif m.group() in assets and m.group().isupper():
            out.append(Mark("ASSET", m.start(), m.end()))
    for m in re.finditer(r"going to", text, re.IGNORECASE):
        out.append(Mark("FUTURE", m.start(), m.end()))
    for m in _NUMBER.finditer(text):
        # a number in words is marked as such: its value is not read here, so it is asked about and never guessed
        kind = "NUMBER" if any(c.isdigit() for c in m.group()) else "NUMBER_WORD"
        out.append(Mark(kind, m.start(), m.start() + len(m.group().rstrip())))
    for m in _TIME.finditer(text):
        out.append(Mark("TIME_WORD", m.start(), m.end()))
    return tuple(sorted(out, key=lambda k: (k.start, k.end, k.kind)))


def segment(text: str, assets: tuple[str, ...] = ()) -> Segments:
    """The clauses of the text and the marks in it; ``assets`` are the symbols to look for, written in capitals."""
    return Segments(_clauses(text), _marks(text, assets))


def disagreements(graph: Graph, seg: Segments) -> tuple[Disagreement, ...]:
    """Where the graph and a plain reading of the text differ, in the order of the text."""
    stated = [n for n in graph.nodes if n.type is not NodeType.ASSUMPTION_INFERRED]
    covered = [(a, b) for n in stated for a, b in n.spans]
    links = [(a, b) for n in stated if n.type is NodeType.CAUSAL_LINK for a, b in n.spans]
    found: list[Disagreement] = []
    for mark in seg.marks:
        inside = any(a <= mark.start and mark.end <= b for a, b in covered)
        if mark.kind == "CAUSAL" and not any(a <= mark.start and mark.end <= b for a, b in links):
            found.append(Disagreement("CAUSAL_WORD_WITHOUT_LINK", mark.start, mark.end, "a causal word that no causal-link node contains"))
        elif mark.kind == "NUMBER" and not inside:
            found.append(Disagreement("NUMBER_WITHOUT_NODE", mark.start, mark.end, "a number that no node contains"))
        elif mark.kind == "NUMBER_WORD" and not inside:
            found.append(
                Disagreement(
                    "NUMBER_WORD_WITHOUT_NODE",
                    mark.start,
                    mark.end,
                    "a number written in words that no node contains: the plain reader cannot read its value",
                )
            )
        elif mark.kind == "TIME_WORD" and not inside:
            found.append(
                Disagreement(
                    "TIME_WORD_WITHOUT_NODE",
                    mark.start,
                    mark.end,
                    "a date or a time written in words that no node contains: the plain reader cannot read it",
                )
            )
    for node in stated:
        for a, b in node.spans:
            words = graph.text[a:b]
            places = [m.start() for m in re.finditer(re.escape(words), graph.text)] if words else []
            # another place that holds the same words and that no other part is tied to: which of the two is meant is not told
            loose = [q for q in places if q != a and not any(x <= q and q + len(words) <= y for x, y in covered if (x, y) != (a, b))]
            if loose:
                found.append(
                    Disagreement(
                        "QUOTE_OCCURS_TWICE",
                        a,
                        b,
                        f"{node.node_id}: its words occur {len(places)} times in the claim and only one is tied to it",
                    )
                )
            reached = [c for c in seg.clauses if c[0] < b and a < c[1]]
            if len(reached) > 1:
                found.append(Disagreement("SPAN_CROSSES_CLAUSES", a, b, f"{node.node_id}: one span runs over {len(reached)} clauses"))
    return tuple(sorted(found, key=lambda d: (d.start, d.end, d.kind)))
