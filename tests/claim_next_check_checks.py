"""Checks of the next check of a claim (``hoshitori.claim_next_check``), runnable against a mutant."""

from __future__ import annotations

import types
from typing import Any

from hoshitori import claim_graph as cg
from tests.claim_graph_checks import TEXT, golden, span

N, E, T = cg.Node, cg.Edge, cg.NodeType


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def summary(r: Any) -> tuple[str, str | None, str | None, str | None]:
    return (r.status, r.node_id, r.check.check_id if r.check else None, r.reason)


def check_golden(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    g = golden(cg)
    c.eq(
        "a fresh claim: the first required part in dependency order",
        summary(mod.select_next_check(g, {})),
        ("CHECK", "obs", "compare_exchange_candles", None),
    )
    first = mod.select_next_check(g, {})
    c.eq(
        "it says what the part says and what to do",
        (first.words, first.check.instruction, first.check.source, first.check.mode),
        (
            "Whales are accumulating SOL",
            "Compare the figure with the exchange's own candles for the same period.",
            "the exchange's public price chart",
            "PERSON",
        ),
    )
    c.eq("the same state gives the same answer", mod.select_next_check(g, {}), mod.select_next_check(g, {}))
    c.eq(
        "a premise looked at and unanswered is not asked again with the same check",
        summary(mod.select_next_check(g, {"obs": "NOT_FOUND_IN_SOURCES_CHECKED"})),
        ("NO_AVAILABLE_CHECK", "obs", None, "the check that exists was tried and gave no answer"),
    )
    for status in ("NOT_CHECKABLE", "SOURCE_UNAVAILABLE"):
        c.eq(f"{status} is a tried part", mod.select_next_check(g, {"obs": status}).status, "NO_AVAILABLE_CHECK")
    for status in ("MIXED", "UNRESOLVED", "UNCHECKED"):
        c.eq(f"{status} is still open for the check", mod.select_next_check(g, {"obs": status}).status, "CHECK")
    after = mod.select_next_check(g, {"obs": "SUPPORTED"})
    c.eq(
        "after the observation holds, the implied step is next and has no check",
        summary(after),
        ("NO_AVAILABLE_CHECK", "ident", None, "an implied step has no source; confirm or reject it"),
    )
    c.eq("with its words", after.words, "The large wallets belong to institutions")
    interp = mod.select_next_check(g, {"obs": "SUPPORTED", "ident": "SUPPORTED_BY_EVIDENCE"})
    c.eq(
        "then the interpretation, which nothing here can check",
        summary(interp),
        ("NO_AVAILABLE_CHECK", "who", None, "what lies behind a movement (who, why) cannot be checked with the sources available here"),
    )
    done = mod.select_next_check(g, {"obs": "SUPPORTED", "ident": "SUPPORTED_BY_EVIDENCE", "who": "SUPPORTED_BY_EVIDENCE"})
    c.eq(
        "nothing left once every required part is decided",
        summary(done),
        ("NOTHING_LEFT", None, None, "every part the claim needs has been checked"),
    )
    c.eq("and says nothing of a part", (done.words, done.check), (None, None))
    bad = mod.select_next_check(g, {"obs": "CONTRADICTED"})
    c.eq(
        "a required part that does not hold ends it",
        summary(bad),
        ("NOTHING_LEFT", "obs", None, "a part the claim needs has been checked and does not hold"),
    )
    c.eq("with its words", bad.words, "Whales are accumulating SOL")
    c.eq(
        "a causal link and a forecast that nothing requires are never the next check",
        mod.select_next_check(
            g,
            {
                "obs": "SUPPORTED",
                "ident": "SUPPORTED_BY_EVIDENCE",
                "who": "SUPPORTED_BY_EVIDENCE",
                "why": "MECHANISM_UNTESTED",
                "up": "UNRESOLVED",
            },
        ).status,
        "NOTHING_LEFT",
    )
    c.eq("a contradicted part nothing requires does not end it", mod.select_next_check(g, {"why": "ORDER_INCONSISTENT"}).node_id, "obs")
    return c.bad


def check_other_graphs(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    a, b = N("a", T.OBSERVATION, spans=(span("institutions"),)), N("b", T.OBSERVATION, spans=(span("Whales"),))
    g = cg.build_graph(TEXT, (a, b), (E("a", "b", cg.EdgeKind.DEPENDS_ON),))
    c.eq(
        "a premise listed second is still checked first",
        summary(mod.select_next_check(g, {})),
        ("CHECK", "b", "compare_exchange_candles", None),
    )
    c.eq(
        "a premise that holds hands over to the part built on it",
        summary(mod.select_next_check(g, {"b": "SUPPORTED"})),
        ("CHECK", "a", "compare_exchange_candles", None),
    )
    expected = {
        T.OBSERVATION: ("CHECK", "compare_exchange_candles", None),
        T.CONDITION: ("CHECK", "find_primary_announcement", None),
        T.PROMOTION_SIGNAL: ("CHECK", "look_for_disclosure", None),
        T.CORRECTION_REF: ("CHECK", "find_corrected_claim", None),
        T.INTERPRETATION: (
            "NO_AVAILABLE_CHECK",
            None,
            "what lies behind a movement (who, why) cannot be checked with the sources available here",
        ),
        T.CAUSAL_LINK: ("NO_AVAILABLE_CHECK", None, "a market cause cannot be checked here, only whether the order in time fits"),
        T.FORECAST: ("NO_AVAILABLE_CHECK", None, "a forecast is checked by waiting for its outcome"),
        T.OPINION: ("NO_AVAILABLE_CHECK", None, "an opinion has no check"),
    }
    for kind, (status, check_id, reason) in expected.items():
        single = cg.build_graph(TEXT, (N("x", kind, spans=(span("Whales"),)),))
        c.eq(f"a lone {kind.value}", summary(mod.select_next_check(single, {})), (status, "x", check_id, reason))
    c.eq(
        "the registered checks",
        [(k.check_id, k.node_type.value, k.mode) for k in mod.REGISTRY],
        [
            ("compare_exchange_candles", "OBSERVATION", "PERSON"),
            ("find_primary_announcement", "CONDITION", "PERSON"),
            ("look_for_disclosure", "PROMOTION_SIGNAL", "PERSON"),
            ("find_corrected_claim", "CORRECTION_REF", "PERSON"),
        ],
    )
    c.eq(
        "their words",
        [(k.instruction, k.source) for k in mod.REGISTRY[1:]],
        [
            ("Find the announcement itself, not a report of it.", "the issuer's or regulator's own announcement page"),
            ("Look for a disclosure of payment or holdings by the author.", "the author's own profile and the post"),
            ("Find the claim this corrects and see whether it is the one being read.", "the author's own earlier post"),
        ],
    )
    c.eq(
        "the constants",
        (mod.PERSON, mod.CHECK, mod.NO_AVAILABLE_CHECK, mod.NOTHING_LEFT),
        ("PERSON", "CHECK", "NO_AVAILABLE_CHECK", "NOTHING_LEFT"),
    )
    c.eq("the tried statuses", sorted(mod.TRIED), ["NOT_CHECKABLE", "NOT_FOUND_IN_SOURCES_CHECKED", "SOURCE_UNAVAILABLE"])
    c.eq(
        "the open statuses",
        sorted(mod.OPEN),
        sorted(
            [
                "NOT_CHECKABLE",
                "NOT_FOUND_IN_SOURCES_CHECKED",
                "SOURCE_UNAVAILABLE",
                "UNCHECKED",
                "UNPROVEN",
                "UNRESOLVED",
                "MIXED",
                "MECHANISM_UNTESTED",
                "UNKNOWN",
                "NOT_FOUND",
            ]
        ),
    )
    c.eq("decided against", sorted(mod.DECIDED_AGAINST), ["CONTRADICTED", "ORDER_INCONSISTENT"])
    c.eq(
        "a status that is a result is not open",
        [
            mod.select_next_check(cg.build_graph(TEXT, (N("x", T.OBSERVATION, spans=(span("Whales"),)),)), {"x": s}).status
            for s in ("SUPPORTED",)
        ],
        ["NOTHING_LEFT"],
    )
    c.eq("frozen", [cls.__dataclass_params__.frozen for cls in (mod.Check, mod.NextCheck)], [True, True])
    return c.bad


def menu_registry(mod: types.ModuleType, **over: Any) -> Any:
    from hoshitori import desk_menu
    from tests.desk_menu_checks import menu

    return mod.registry_from_menu(menu(desk_menu, **over))


OBS_IDS = [
    "INDEPENDENT_PRICE_CHECK:binance-spot:candles",
    "INDEPENDENT_PRICE_CHECK:binance-usdm:candles",
    "INDEPENDENT_PRICE_CHECK:kraken:candles",
    "DERIVATIVES_CONTEXT:binance-usdm:funding",
    "RYO_TOKEN_READ:ryo:analyze_token",
]


def check_menu_mode(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    g = golden(cg)
    reg = menu_registry(mod)
    c.eq("the checks that can resolve an observation, in the order they are offered", [k.check_id for k in reg], OBS_IDS)
    c.eq("all are gateway checks on observations", {(k.mode, k.node_type.value) for k in reg}, {("GATEWAY", "OBSERVATION")})
    first = mod.select_next_check(g, {}, reg, frozenset())
    c.eq("the first open part, with the cheapest preferred price check", summary(first), ("CHECK", "obs", OBS_IDS[0], None))
    c.eq("the alternatives are the rest, in order", first.alternatives, tuple(OBS_IDS[1:]))
    c.eq(
        "the explanation says which rule chose",
        first.explanation,
        "dependency order: the first open part is offered; uncalibrated, so the preferred check type and then the cheaper one is offered",
    )
    c.eq(
        "the instruction names the provider and the operation",
        (first.check.instruction, first.check.source),
        ("Call binance-spot candles for BTC.", "binance-spot"),
    )
    c.eq("the same state gives the same answer", mod.select_next_check(g, {}, reg, frozenset()), first)
    c.eq("the part is required", first.required, True)
    # a check that was tried is not offered again; the next one is
    second = mod.select_next_check(g, {"obs": "NOT_FOUND_IN_SOURCES_CHECKED"}, reg, frozenset({OBS_IDS[0]}))
    c.eq("a tried check gives way to the next", summary(second), ("CHECK", "obs", OBS_IDS[1], None))
    c.eq("and is no longer among the alternatives", OBS_IDS[0] in second.alternatives, False)
    all_tried = mod.select_next_check(g, {"obs": "NOT_FOUND_IN_SOURCES_CHECKED"}, reg, frozenset(OBS_IDS))
    c.eq(
        "every check tried: nothing is invented",
        summary(all_tried),
        ("NO_AVAILABLE_CHECK", "obs", None, "every check on the menu for this part was tried"),
    )
    # the part after the observation has no menu check, and says why
    after = mod.select_next_check(g, {"obs": "SUPPORTED"}, reg, frozenset())
    c.eq("the implied step has no source", summary(after)[:3], ("NO_AVAILABLE_CHECK", "ident", None))
    c.eq(
        "dependency order holds: no downstream part before an open upstream one",
        mod.select_next_check(g, {"ident": "UNPROVEN"}, reg, frozenset()).node_id,
        "obs",
    )
    # a menu with nothing that can resolve an observation
    empty = menu_registry(mod, providers={}, ryo_available=frozenset())
    c.eq("an empty registry", empty, ())
    none_left = mod.select_next_check(g, {}, empty, frozenset())
    c.eq(
        "no check on the menu can resolve the part",
        summary(none_left),
        ("NO_AVAILABLE_CHECK", "obs", None, "no check on the menu can resolve this part"),
    )
    # the person's registry is unchanged by the new parameters
    c.eq("the default path is the person registry", mod.select_next_check(g, {}), mod.select_next_check(g, {}, mod.REGISTRY, None))
    c.eq(
        "and offers no alternatives or explanation",
        (mod.select_next_check(g, {}).alternatives, mod.select_next_check(g, {}).explanation),
        ((), ""),
    )
    person_tried = mod.select_next_check(g, {"obs": "NOT_FOUND"}, mod.REGISTRY, frozenset({"compare_exchange_candles"}))
    c.eq("a tried person check is not repeated either", person_tried.status, "NO_AVAILABLE_CHECK")
    return c.bad


def check_menu_ranking(mod: types.ModuleType) -> list[str]:
    import dataclasses

    from hoshitori import desk_menu

    c = Case(mod)
    from tests.desk_menu_checks import menu

    costs = desk_menu.CostTable.from_latencies(
        {
            ("kraken", "candles"): [10.0],
            ("binance-usdm", "candles"): [9.0],
            ("binance-usdm", "funding"): [0.2],
            ("ryo", "analyze_token"): [1.0],
        }
    )
    m = menu(desk_menu, costs=costs)
    c.eq(
        "within a kind the faster check first (ten seconds is slower than nine), a check with no recorded latency after those with one",
        [k.check_id for k in mod.registry_from_menu(m)],
        [
            "INDEPENDENT_PRICE_CHECK:binance-usdm:candles",
            "INDEPENDENT_PRICE_CHECK:kraken:candles",
            "INDEPENDENT_PRICE_CHECK:binance-spot:candles",
            "DERIVATIVES_CONTEXT:binance-usdm:funding",
            "RYO_TOKEN_READ:ryo:analyze_token",
        ],
    )
    odd = dataclasses.replace(next(k for k in m.checks if k.check_type == "RYO_DEEP_ANALYSIS"), node_type="OBSERVATION")
    kept = dataclasses.replace(m, checks=(*m.checks, odd))
    c.eq(
        "a check kind outside the preference list is never offered for a part",
        "RYO_DEEP_ANALYSIS:ryo:deep_analysis" in [k.check_id for k in mod.registry_from_menu(kept)],
        False,
    )
    single = menu(desk_menu, providers={"kraken": frozenset({"candles"})}, ryo_available=frozenset())
    one = mod.registry_from_menu(single)
    c.eq(
        "a menu with one check",
        [(k.check_id, k.instruction) for k in one],
        [("INDEPENDENT_PRICE_CHECK:kraken:candles", "Call kraken candles for BTC.")],
    )
    c.eq("a default result is for a required part", mod.NextCheck("CHECK", "x", "w", None, None).required, True)
    return c.bad


def check_off_menu(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    reg = menu_registry(mod)
    c.eq("a check on the menu is found", mod.resolve_request(reg, OBS_IDS[2]).check_id, OBS_IDS[2])
    for label, asked in (
        ("an id from a plan", "RYO_DEEP_ANALYSIS:ryo:deep_analysis"),
        ("an invented id", "ask_the_whales"),
        ("an empty id", ""),
    ):
        try:
            mod.resolve_request(reg, asked)
        except mod.NotOnMenu as exc:
            c.eq(f"{label} is refused with its id", str(exc), f"not on the menu: {asked!r}")
        else:
            c.bad.append(f"{label}: was accepted")
    return c.bad


def check_outcomes(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    g = golden(cg)
    reg = menu_registry(mod)
    done = mod.select_next_check(
        g, {"obs": "SUPPORTED", "ident": "SUPPORTED_BY_EVIDENCE", "who": "SUPPORTED_BY_EVIDENCE"}, reg, frozenset()
    )
    against = mod.select_next_check(g, {"obs": "CONTRADICTED"}, reg, frozenset())
    unresolved = mod.select_next_check(g, {"obs": "SUPPORTED"}, reg, frozenset())
    check = mod.select_next_check(g, {}, reg, frozenset())
    c.eq("a check to run continues", mod.outcome(check), None)
    c.eq("every required part decided is a stop", mod.outcome(done), ("STOP", "ALL_MENU_OPENED"))
    c.eq(
        "a required part that does not hold is an abstention on conflict", mod.outcome(against), ("ABSTAIN", "EVIDENCE_CONFLICT_UNRESOLVED")
    )
    c.eq(
        "a required part nothing can check is an unproven essential premise",
        mod.outcome(unresolved),
        ("ABSTAIN", "ESSENTIAL_PREMISE_UNPROVEN"),
    )
    tried = mod.select_next_check(g, {"obs": "NOT_FOUND"}, reg, frozenset(OBS_IDS))
    c.eq("every check tried is an unproven essential premise", mod.outcome(tried), ("ABSTAIN", "ESSENTIAL_PREMISE_UNPROVEN"))
    loose = mod.NextCheck("NO_AVAILABLE_CHECK", "x", "words", None, "r", required=False)
    c.eq("a part nothing needs does not make the premise essential", mod.outcome(loose), ("STOP", "ALL_MENU_OPENED"))
    c.eq("the closed words", (mod.STOP, mod.ABSTAIN), ("STOP", "ABSTAIN"))
    c.eq(
        "stop and abstain words in the lists of the lifecycle",
        {"EVIDENCE_CONFLICT_UNRESOLVED", "ESSENTIAL_PREMISE_UNPROVEN"} <= set(lifecycle_reasons()),
        True,
    )
    return c.bad


def lifecycle_reasons() -> tuple[str, ...]:
    from hoshitori import lifecycle

    return tuple(lifecycle.NO_CALL_REASONS)


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (check_golden, check_other_graphs, check_menu_mode, check_menu_ranking, check_off_menu, check_outcomes)
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
