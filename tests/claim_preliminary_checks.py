"""Checks of the preliminary page (``hoshitori.claim_preliminary``), runnable against a mutant."""

from __future__ import annotations

import dataclasses
import types
from typing import Any

from hoshitori import claim_graph as cg
from tests.claim_graph_checks import TEXT, golden, span

N, T = cg.Node, cg.NodeType


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def check_page(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    page = mod.preliminary(golden(cg))
    c.eq("banner", page.banner, "Preliminary: read by a machine, nothing checked yet.")
    c.eq("headline", page.headline, "Not yet independently supported.")
    c.eq(
        "the sentences",
        list(page.sentences()),
        [
            "Not yet independently supported.",
            "The observation “Whales are accumulating SOL” has not been checked; it can be tested against exchange and on-chain records.",
            "The interpretation “institutions are entering” has not been checked; no source here can test it.",
            "The causal link “therefore” has not been checked; no source here can test it.",
            "The forecast “SOL will rise 15% this week” has not been checked; it can be tested against a price series from a named exchange.",
            "We think this is implied: “The large wallets belong to institutions” has not been checked.",
            "Next step: investigate, do not accept as confirmed.",
        ],
    )
    c.eq("triage", page.triage, "INVESTIGATE")
    c.eq("every status is UNCHECKED", {p.status for p in page.parts}, {"UNCHECKED"})
    c.eq(
        "parts keep the graph's order and kinds",
        [(p.node_id, p.kind) for p in page.parts],
        [("obs", "OBSERVATION"), ("who", "INTERPRETATION"), ("why", "CAUSAL_LINK"), ("up", "FORECAST"), ("ident", "ASSUMPTION_INFERRED")],
    )
    c.eq("a part carries the words", page.parts[0].quote, "Whales are accumulating SOL")
    c.eq(
        "the page has no place for a result",
        [f.name for f in dataclasses.fields(mod.Preliminary)],
        ["banner", "headline", "parts", "next_step", "triage"],
    )
    c.eq(
        "a part has no place for a result either",
        [f.name for f in dataclasses.fields(mod.Part)],
        ["node_id", "kind", "status", "quote", "line"],
    )
    c.eq("frozen", (mod.Part.__dataclass_params__.frozen, mod.Preliminary.__dataclass_params__.frozen), (True, True))
    cond = cg.build_graph(TEXT, (N("c", T.CONDITION, spans=(span("this week"),)),))
    c.eq(
        "a condition can be tested against records",
        mod.preliminary(cond).parts[0].line,
        "The condition “this week” has not been checked; it can be tested against exchange and on-chain records.",
    )
    opinion = cg.build_graph(TEXT, (N("o", T.OPINION, spans=(span("Whales"),)),))
    q = mod.preliminary(opinion)
    c.eq(
        "nothing testable",
        (q.triage, q.next_step),
        ("NOT_MARKET_TESTABLE", "Nothing here can be tested against market data, so no check is planned."),
    )
    c.eq("and it still says nothing was checked", q.parts[0].line, "The opinion “Whales” has not been checked; no source here can test it.")
    c.eq(
        "one testable part is enough",
        mod.preliminary(
            cg.build_graph(
                TEXT, (N("o", T.OPINION, spans=(span("Whales"),)), N("f", T.FORECAST, spans=(span("SOL will rise 15% this week"),)))
            )
        ).triage,
        "INVESTIGATE",
    )
    vague = cg.build_graph("ETH is going to moon", (N("f", T.FORECAST, spans=((0, 20),)),))
    v = mod.preliminary(vague, ("ETH",))
    c.eq(
        "a forecast that names no event says so and is not testable",
        (v.parts[0].line, v.triage, v.next_step),
        (
            "The forecast “ETH is going to moon” has not been checked; as worded it names nothing a price series could settle.",
            "NOT_MARKET_TESTABLE",
            "Nothing here can be tested against market data, so no check is planned.",
        ),
    )
    mixed = cg.build_graph(
        "ETH is going to moon. BTC will fall in 2 days", (N("f", T.FORECAST, spans=((0, 20),)), N("g", T.FORECAST, spans=((22, 45),)))
    )
    m = mod.preliminary(mixed, ("ETH", "BTC"))
    c.eq(
        "one forecast that can be settled is enough",
        ([p.line.split("; ")[1] for p in m.parts], m.triage),
        (
            ["as worded it names nothing a price series could settle.", "it can be tested against a price series from a named exchange."],
            "INVESTIGATE",
        ),
    )
    plain = cg.build_graph("it will rise in 2 days", (N("f", T.FORECAST, spans=((0, 22),)),))
    c.eq("a forecast with a direction and a time can be settled once its asset is chosen", mod.preliminary(plain).triage, "INVESTIGATE")
    c.eq(
        "the vocabulary of flagged words",
        mod.FLAGGED,
        ("supported", "contradicted", "verified", "confirmed", "true", "false", "proven", "corroborated"),
    )
    c.eq(
        "what each kind is tested against",
        {k.value: v for k, v in mod.TESTED_AGAINST.items()},
        {
            "OBSERVATION": "exchange and on-chain records",
            "FORECAST": "a price series from a named exchange",
            "CONDITION": "exchange and on-chain records",
        },
    )
    return c.bad


def check_lint(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    page = mod.preliminary(golden(cg))
    c.eq("a real page has no violation", mod.lint(page), ())
    quotes = tuple(p.quote for p in page.parts)
    c.eq("the headline is allowed", mod.allowed("Not yet independently supported."), True)
    c.eq("an assertion of a result is not", mod.allowed("Verified by independent sources."), False)
    c.eq("nor a confirmation", mod.allowed("Confirmed by two exchanges."), False)
    c.eq(
        "nor a verdict dressed as a line",
        mod.allowed("The observation “Whales are accumulating SOL” is supported by exchange records.", quotes),
        False,
    )
    c.eq(
        "a known quote is taken out, whatever it says",
        mod.allowed(
            "The observation “It is true and verified” has not been checked; no source here can test it.", ("It is true and verified",)
        ),
        True,
    )
    c.eq(
        "an unknown quote is not",
        mod.allowed("The observation “It is true and verified” has not been checked; no source here can test it."),
        False,
    )
    c.eq(
        "two quotes, one beginning like the other",
        mod.allowed("The observation “a b c” has not been checked; no source here can test it.", ("a b", "a b c")),
        True,
    )
    c.eq("a changed ending is not allowed", mod.allowed("The observation “x” has not been checked; it is true.", ("x",)), False)
    c.eq("trailing words are not allowed", mod.allowed("Not yet independently supported. And true."), False)
    c.eq("the next step and the empty plan", (mod.allowed(mod.NEXT_STEP), mod.allowed(mod.NOTHING_TESTABLE)), (True, True))
    c.eq("nothing else", mod.allowed(""), False)
    bad_status = dataclasses.replace(page, parts=(dataclasses.replace(page.parts[0], status="SUPPORTED"), *page.parts[1:]))
    c.eq("a status other than UNCHECKED", mod.lint(bad_status), ("obs: a preliminary page cannot carry a status other than UNCHECKED",))
    c.eq("a changed banner", mod.lint(dataclasses.replace(page, banner="Verified.")), ("the banner is not the preliminary banner",))
    c.eq(
        "a verdict in the headline",
        mod.lint(dataclasses.replace(page, headline="Verified by independent sources.")),
        ("not a sentence a preliminary page may say: Verified by independent sources.",),
    )
    c.eq(
        "a verdict in the next step",
        mod.lint(dataclasses.replace(page, next_step="Accept: confirmed.")),
        ("not a sentence a preliminary page may say: Accept: confirmed.",),
    )
    hostile = dataclasses.replace(page.parts[1], line="The interpretation “institutions are entering” is confirmed.")
    c.eq(
        "a verdict in a part",
        mod.lint(dataclasses.replace(page, parts=(page.parts[0], hostile, *page.parts[2:]))),
        ("not a sentence a preliminary page may say: The interpretation “institutions are entering” is confirmed.",),
    )
    kinds = (T.OBSERVATION, T.INTERPRETATION, T.CAUSAL_LINK, T.FORECAST, T.CONDITION, T.OPINION, T.PROMOTION_SIGNAL, T.CORRECTION_REF)
    visible = [i for i, ch in enumerate(TEXT) if not ch.isspace()]
    every = cg.build_graph(TEXT, tuple(N(f"n{i}", t, spans=((visible[i], visible[i] + 1),)) for i, t in enumerate(kinds)))
    c.eq("a real page of every kind of part has no violation", mod.lint(mod.preliminary(every)), ())
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (check_page, check_lint)
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
