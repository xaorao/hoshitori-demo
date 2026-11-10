"""Checks of the claim segmenter (``hoshitori.claim_segmenter``), runnable against a mutant."""

from __future__ import annotations

import types
from typing import Any

from hoshitori import claim_graph as cg
from tests.claim_graph_checks import TEXT, golden, span


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def clauses(mod: types.ModuleType, text: str) -> list[str]:
    return [text[a:b] for a, b in mod.segment(text).clauses]


def marks(mod: types.ModuleType, text: str, assets: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    return [(m.kind, text[m.start : m.end]) for m in mod.segment(text, assets).marks]


def check_clauses(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "the golden claim",
        clauses(mod, TEXT),
        ["Whales are accumulating SOL", "therefore institutions are entering", "and SOL will rise 15% this week."],
    )
    c.eq("a decimal point is not an end", clauses(mod, "SOL rose 1.5% today. It fell."), ["SOL rose 1.5% today.", "It fell."])
    c.eq("an initial is not an end", clauses(mod, "The U.S. Treasury sold. It fell."), ["The U.S. Treasury sold.", "It fell."])
    c.eq("a question and an exclamation end a sentence", clauses(mod, "Is it up? Yes! Buy."), ["Is it up?", "Yes!", "Buy."])
    c.eq("a connective starts a clause", clauses(mod, "Prices fell because volume dried up"), ["Prices fell", "because volume dried up"])
    c.eq("a connective at the very start does not cut an empty clause", clauses(mod, "Therefore it fell"), ["Therefore it fell"])
    c.eq("a semicolon ends a clause", clauses(mod, "It rose; it fell"), ["It rose", "it fell"])
    c.eq("a period before a lower-case word is not an end", clauses(mod, "It rose 5 pct. over the week"), ["It rose 5 pct. over the week"])
    c.eq("a quotation opens a clause after a period", clauses(mod, 'He said it. "Buy now"'), ["He said it.", '"Buy now"'])
    c.eq("the end of the text ends a clause", clauses(mod, "It rose."), ["It rose."])
    c.eq("white space and commas around a clause are not part of it", clauses(mod, "  It rose , and it fell  "), ["It rose", "and it fell"])
    c.eq("a word that merely starts like a connective does not cut", clauses(mod, "The sofa and soda"), ["The sofa", "and soda"])
    c.eq("an empty text has no clause", clauses(mod, "   "), [])
    for word in ("therefore", "because", "hence", "thus", "consequently", "so", "since", "if", "unless", "while", "which", "but", "and"):
        c.eq(f"{word} starts a clause", clauses(mod, f"A {word} B"), ["A", f"{word} B"])
    c.eq("a clause is trimmed of tabs, new lines, commas and semicolons", clauses(mod, "\t\nIt rose,;\n\t"), ["It rose"])
    c.eq("only those characters are trimmed", clauses(mod, "~It rose~"), ["~It rose~"])
    c.eq("a comma between two clauses is dropped from both", clauses(mod, "It rose, but it fell"), ["It rose", "but it fell"])
    c.eq(
        "the connectives that start a clause",
        mod.STARTS,
        ("therefore", "because", "hence", "thus", "consequently", "so", "since", "if", "unless", "while", "which", "but", "and"),
    )
    return c.bad


def check_marks(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "the golden claim",
        marks(mod, TEXT, ("SOL",)),
        [("ASSET", "SOL"), ("CAUSAL", "therefore"), ("ASSET", "SOL"), ("FUTURE", "will"), ("NUMBER", "15%"), ("TIME_WORD", "this week")],
    )
    for word in ("therefore", "because", "hence", "thus", "consequently", "so", "since"):
        c.eq(f"causal {word}", marks(mod, f"A {word} B"), [("CAUSAL", word)])
    c.eq("a capitalised connective", marks(mod, "Therefore B"), [("CAUSAL", "Therefore")])
    for word in ("if", "unless", "provided", "assuming"):
        c.eq(f"conditional {word}", marks(mod, f"A {word} B"), [("CONDITIONAL", word)])
    for word in ("will", "may", "might", "could", "should", "would"):
        c.eq(f"future {word}", marks(mod, f"It {word} rise"), [("FUTURE", word)])
    c.eq("going to", marks(mod, "It is going to rise"), [("FUTURE", "going to")])
    c.eq("a word that only looks like a connective", marks(mod, "The sofa and the ifrit"), [])
    for text, found in (
        ("up 15%", "15%"),
        ("cost $1.5m", "$1.5m"),
        ("in 3 days", "3 days"),
        ("above 1,500", "1,500"),
        ("a 20 bps fee", "20 bps"),
        ("10k holders", "10k"),
        ("a 2.5x move", "2.5x"),
        ("after 42", "42"),
        ("for 2 WEEKS", "2 WEEKS"),
    ):
        c.eq(f"number in {text!r}", marks(mod, text), [("NUMBER", found)])
    c.eq("a number is not cut from its unit by a space after it", marks(mod, "15% "), [("NUMBER", "15%")])
    c.eq("a number followed by a word that is not a unit", marks(mod, "5 apples"), [("NUMBER", "5")])
    c.eq("a unit letter inside a longer word is not a unit", marks(mod, "5 market"), [("NUMBER", "5")])
    c.eq("an asset is a listed symbol in capitals", marks(mod, "SOL and sol and Sol and BTC", ("SOL",)), [("ASSET", "SOL")])
    c.eq("an asset is not looked for without a list", marks(mod, "SOL"), [])
    c.eq("a dollar sign before a symbol", marks(mod, "buy $SOL now", ("SOL",)), [("ASSET", "SOL")])
    c.eq("marks come in the order of the text", [m.start for m in mod.segment("It will rise 5% if so").marks], [3, 13, 16, 19])
    return c.bad


def check_disagreements(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    n, t = cg.Node, cg.NodeType
    g = golden(cg)
    seg = mod.segment(TEXT, ("SOL",))
    c.eq("the golden graph agrees with the plain reading", mod.disagreements(g, seg), ())
    keep = tuple(x for x in g.nodes if x.node_id not in ("why", "ident"))
    no_link = cg.build_graph(TEXT, keep, tuple(e for e in g.edges if {"why", "ident"}.isdisjoint((e.start, e.end))))
    d = mod.disagreements(no_link, seg)
    c.eq("a causal word that no node holds", [(x.kind, TEXT[x.start : x.end]) for x in d], [("CAUSAL_WORD_WITHOUT_LINK", "therefore")])
    c.eq("its explanation", d[0].detail, "a causal word that no causal-link node contains")
    obs = n("o", t.OBSERVATION, spans=(span("Whales are accumulating SOL"),))
    link = n("k", t.CAUSAL_LINK, spans=(span("therefore"),))
    d = mod.disagreements(cg.build_graph(TEXT, (obs, link)), seg)
    c.eq(
        "a number that no node holds",
        [(x.kind, TEXT[x.start : x.end]) for x in d if x.kind == "NUMBER_WITHOUT_NODE"],
        [("NUMBER_WITHOUT_NODE", "15%")],
    )
    c.eq("its explanation", d[0].detail, "a number that no node contains")
    one = TEXT.index("15")
    part = cg.build_graph(TEXT, (n("o", t.OBSERVATION, spans=((one, one + 1),)), link))
    c.eq(
        "a node that covers only part of a number does not hold it",
        [x.kind for x in mod.disagreements(part, seg) if x.kind == "NUMBER_WITHOUT_NODE"],
        ["NUMBER_WITHOUT_NODE"],
    )
    cut = TEXT.index("and SOL") - 1
    forecast = n("u", t.FORECAST, spans=(span("SOL will rise 15% this week"),))
    over = cg.build_graph(TEXT, (n("o", t.OBSERVATION, spans=((0, cut),)), link, forecast))
    d = mod.disagreements(over, seg)
    c.eq("a span over two clauses", [(x.kind, x.detail) for x in d], [("SPAN_CROSSES_CLAUSES", "o: one span runs over 2 clauses")])
    c.eq("it is reported at the span", (d[0].start, d[0].end), (0, cut))
    three = cg.build_graph(TEXT, (n("o", t.OBSERVATION, spans=((0, len(TEXT)),)),))
    c.eq(
        "three clauses",
        [x.detail for x in mod.disagreements(three, seg) if x.kind == "SPAN_CROSSES_CLAUSES"],
        ["o: one span runs over 3 clauses"],
    )
    pin = "It will rise 15%"
    exact = cg.build_graph(pin, (n("f", t.FORECAST, spans=((0, 7),)), n("p", t.OBSERVATION, spans=((13, 16),))))
    c.eq(
        "a node that is exactly a number holds it, and one ending at it",
        [x.kind for x in mod.disagreements(exact, mod.segment(pin)) if x.kind == "NUMBER_WITHOUT_NODE"],
        [],
    )
    stray = cg.build_graph(TEXT, (obs, n("k", t.CAUSAL_LINK, spans=((0, 6),))))
    c.eq(
        "a causal-link node elsewhere does not hold a causal word",
        [x.kind for x in mod.disagreements(stray, seg) if x.kind == "CAUSAL_WORD_WITHOUT_LINK"],
        ["CAUSAL_WORD_WITHOUT_LINK"],
    )
    z = TEXT.index("therefore")
    half = cg.build_graph(TEXT, (obs, n("k", t.CAUSAL_LINK, spans=((z, z + 4),))))
    c.eq(
        "a link node over half the word does not hold it",
        [x.kind for x in mod.disagreements(half, seg) if x.kind == "CAUSAL_WORD_WITHOUT_LINK"],
        ["CAUSAL_WORD_WITHOUT_LINK"],
    )
    two_clauses = "It rose. It fell."
    ends = cg.build_graph(two_clauses, (n("o", t.OBSERVATION, spans=((0, 9),)),))
    c.eq(
        "a span that ends where the next clause starts does not reach it",
        [x.kind for x in mod.disagreements(ends, mod.segment(two_clauses))],
        [],
    )
    begins = cg.build_graph(two_clauses, (n("o", t.OBSERVATION, spans=((8, 17),)),))
    c.eq(
        "a span that begins where a clause ends does not reach it",
        [x.kind for x in mod.disagreements(begins, mod.segment(two_clauses))],
        [],
    )
    short = "It rose. It fell."
    touching = cg.build_graph(short, (n("o", t.OBSERVATION, spans=((0, 8),)),))
    c.eq(
        "a span that ends where the next clause begins does not cross",
        [x.kind for x in mod.disagreements(touching, mod.segment(short))],
        [],
    )
    inferred = cg.build_graph(TEXT, (obs, n("i", t.ASSUMPTION_INFERRED, text="They are institutions", cites=("o",)), link, forecast))
    c.eq("an inferred node holds nothing of the text", mod.disagreements(inferred, seg), ())
    both = cg.build_graph(TEXT, (n("o", t.OBSERVATION, spans=((0, cut),)),))
    c.eq(
        "results come in the order of the text",
        [x.kind for x in mod.disagreements(both, seg)],
        ["SPAN_CROSSES_CLAUSES", "CAUSAL_WORD_WITHOUT_LINK", "NUMBER_WITHOUT_NODE", "TIME_WORD_WITHOUT_NODE"],
    )
    words = (mod.CAUSAL, mod.CONDITIONAL, mod.FUTURE)
    want = (
        ("therefore", "because", "hence", "thus", "consequently", "so", "since"),
        ("if", "unless", "provided", "assuming"),
        ("will", "may", "might", "could", "should", "would", "going to"),
    )
    c.eq("the vocabulary", words, want)
    c.eq("frozen", [cls.__dataclass_params__.frozen for cls in (mod.Mark, mod.Segments, mod.Disagreement)], [True, True, True])
    return c.bad


def check_what_it_cannot_read(mod: types.ModuleType) -> list[str]:
    """Where the plain reader cannot be sure of a value it says so and asks, and never guesses: a number in words, a date or a
    time in words, and words that the claim holds twice and a part quotes once."""
    c = Case(mod)
    n, t = cg.Node, cg.NodeType
    for text, found in (
        ("SOL will rise fifteen percent this week", [("NUMBER_WORD", "fifteen"), ("TIME_WORD", "this week")]),
        ("Twenty days, then 5 more", [("NUMBER_WORD", "Twenty days"), ("NUMBER", "5")]),
        ("It rose two hundred", [("NUMBER_WORD", "two"), ("NUMBER_WORD", "hundred")]),
        ("No one noticed", []),
        ("It happens by Friday", [("TIME_WORD", "Friday")]),
        ("The vote is on 15 March", [("NUMBER", "15")]),
        ("It rose in September and in October", [("TIME_WORD", "September"), ("TIME_WORD", "October")]),
        ("It will drop tomorrow", [("TIME_WORD", "tomorrow")]),
        ("Next month it ends", [("TIME_WORD", "Next month")]),
        ("It ends at the end of the quarter", [("TIME_WORD", "end of the quarter")]),
        ("It may happen", []),  # "may" is the verb of the future, not the month
    ):
        c.eq(
            f"the marks of {text!r} that are about numbers and time in words",
            [(m.kind, text[m.start : m.end]) for m in mod.segment(text).marks if m.kind in ("NUMBER_WORD", "TIME_WORD", "NUMBER")],
            found,
        )
    text = "SOL will rise fifteen percent by Friday"
    stated = cg.build_graph(text, (n("a", t.FORECAST, spans=((0, len(text)),)),))
    c.eq("a node that holds them leaves nothing to ask", [d.kind for d in mod.disagreements(stated, mod.segment(text))], [])
    bare = cg.build_graph(text, (n("a", t.FORECAST, spans=((0, 8),)),))
    d = mod.disagreements(bare, mod.segment(text))
    c.eq(
        "words that no node holds are asked about, by what they are",
        [(x.kind, text[x.start : x.end]) for x in d],
        [("NUMBER_WORD_WITHOUT_NODE", "fifteen"), ("TIME_WORD_WITHOUT_NODE", "Friday")],
    )
    c.eq(
        "and explained",
        [x.detail for x in d],
        [
            "a number written in words that no node contains: the plain reader cannot read its value",
            "a date or a time written in words that no node contains: the plain reader cannot read it",
        ],
    )
    twice = "SOL rose, and SOL fell"
    one = cg.build_graph(twice, (n("a", t.OBSERVATION, spans=((0, 3),)),))
    d = mod.disagreements(one, mod.segment(twice))
    c.eq(
        "words the claim holds twice, quoted by one part, are asked about: which of the two is meant",
        [(x.kind, x.start, x.end, x.detail) for x in d],
        [("QUOTE_OCCURS_TWICE", 0, 3, "a: its words occur 2 times in the claim and only one is tied to it")],
    )
    for label, span_b, flagged in (
        ("a part that covers only the left of the other place", (14, 16), True),
        ("a part that covers only the right of the other place", (15, 17), True),
        ("a part that covers the other place and more on each side", (13, 17), False),
        ("a part that covers the other place exactly", (14, 17), False),
        ("a part that lies before the other place", (4, 8), True),
    ):
        got = cg.build_graph(twice, (n("a", t.OBSERVATION, spans=((0, 3),)), n("b", t.OBSERVATION, spans=(span_b,))))
        asked = [x.kind for x in mod.disagreements(got, mod.segment(twice)) if x.kind == "QUOTE_OCCURS_TWICE" and x.start == 0]
        c.eq(f"{label}: the first is {'still asked about' if flagged else 'in no doubt'}", asked, ["QUOTE_OCCURS_TWICE"] if flagged else [])
    both = cg.build_graph(twice, (n("a", t.OBSERVATION, spans=((0, 3),)), n("b", t.OBSERVATION, spans=((14, 17),))))
    c.eq("two parts that take the two occurrences leave nothing to ask", [x.kind for x in mod.disagreements(both, mod.segment(twice))], [])
    longer = "SOL rose, and SOL fell"
    k = longer.index("SOL fell")
    whole = cg.build_graph(longer, (n("a", t.OBSERVATION, spans=((k, k + 8),)),))
    c.eq("a longer quote that occurs once is not in doubt", [x.kind for x in mod.disagreements(whole, mod.segment(longer))], [])
    return c.bad


def check_numbers_by_the_graphs_rule(mod: types.ModuleType) -> list[str]:
    """A number is read by the claim graph's own rule (``claim_graph._NUMBER``), not a copy of it: a comma only between digits, so
    "150, then" reads 150 and "1,500" is one number; a minus sign written directly before the digits is part of the number ("-15" is not
    15), and one after a letter, a digit, "_" or "%" is a hyphen ("10-15%" is 10 and 15%). The plain reader reads digits only, not the
    number words that rule also reads."""
    c = Case(mod)
    c.eq("the pattern is the graph's own, not a copy", cg._NUMBER.pattern in mod._NUMBER.pattern, True)
    fell = "SOL fell to 150, then bounced."
    i = fell.index("SOL fell to 150")
    graph = cg.build_graph(fell, (cg.Node("a", cg.NodeType.OBSERVATION, spans=((i, i + len("SOL fell to 150")),)),))
    c.eq(
        "a node on 'SOL fell to 150' holds the number of 'SOL fell to 150, then bounced.'",
        [(d.kind, fell[d.start : d.end]) for d in mod.disagreements(graph, mod.segment(fell))],
        [],
    )
    for text, found in (
        (fell, ["150"]),
        ("Above 1,500, then more holders", ["1,500"]),
        ("Over 1,500 wallets bought SOL", ["1,500"]),
        ("SOL will move -15% this week", ["-15%"]),
        ("SOL will move \u221215% this week", ["\u221215%"]),
        ("-15% would end it", ["-15%"]),
        ("A week (-15%) would end it", ["-15%"]),
        ("A +15% week is large", ["15%"]),
        ("SOL will rise 10-15% this week", ["10", "15%"]),
        ("SOL will rise 10%-15% this week", ["10%", "15%"]),
        ("The X-15 upgrade ships this week", ["15"]),
        ("SOL will rise fifteen percent this week", []),
        ("Twenty days, then 5 more", ["5"]),
    ):
        c.eq(f"the numbers of {text!r}", [text[m.start : m.end] for m in mod.segment(text).marks if m.kind == "NUMBER"], found)
    n, t = cg.Node, cg.NodeType

    def disputes(text: str, quote: str) -> list[str]:
        k = text.index(quote)
        g = cg.build_graph(text, (n("a", t.OBSERVATION, spans=((k, k + len(quote)),)),))
        return [text[d.start : d.end] for d in mod.disagreements(g, mod.segment(text)) if d.kind == "NUMBER_WITHOUT_NODE"]

    move = "SOL will move -15% this week"
    c.eq("a node without the minus sign does not hold the number", disputes(move, "15% this week"), ["-15%"])
    c.eq("a node with it does", disputes(move, "-15% this week"), [])
    c.eq("a hyphen in a range is not a sign, so the node holds 15%", disputes("SOL will rise 10-15% this week", "15% this week"), ["10"])
    c.eq("a comma between digits is part of the number", disputes("Over 1,500 wallets bought SOL", "Over 1,5"), ["1,500"])
    c.eq("and one after them is not", disputes("Above 1,500, then more holders", "Above 1,500"), [])
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (check_clauses, check_marks, check_disagreements, check_what_it_cannot_read, check_numbers_by_the_graphs_rule)
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
