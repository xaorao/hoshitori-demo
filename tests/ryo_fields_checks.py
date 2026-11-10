"""Checks of the evidence-class table and the data walker, runnable against the real module or a mutant."""

from __future__ import annotations

import copy
import types
from typing import Any

from hoshitori import jsonnum
from tests import ryo_data as D
from tests.ryo_support import SIX


def num(text: str) -> Any:
    return jsonnum.Num(text)


def numbers(value: Any) -> Any:
    """The parsed form of a synthetic structure: numbers become Num, as the reader returns them."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return num(repr(value))
    if isinstance(value, list):
        return [numbers(v) for v in value]
    return {k: numbers(v) for k, v in value.items()}


def data_of(tool: str, edit: Any = None) -> dict[str, Any]:
    data = copy.deepcopy(D.DATA[tool]())
    if edit is not None:
        edit(data)
    return numbers(data)  # type: ignore[no-any-return]


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    E = mod.ErrorClass
    OBS, INT, META = mod.FieldClass.OBSERVATION, mod.FieldClass.INTERPRETATION, mod.FieldClass.META

    def walk(tool: str, edit: Any = None) -> Any:
        return mod.walk(tool, data_of(tool, edit))

    def refuses(label: str, tool: str, edit: Any, error_class: Any = None) -> None:
        try:
            mod.walk(tool, data_of(tool, edit))
            bad.append(f"{label}: accepted")
        except mod.ConnectorError as exc:
            if exc.error_class is not (error_class or E.CONTRACT_DRIFT):
                bad.append(f"{label}: classed {exc.error_class.value} ({exc.message})")
        except Exception as exc:
            bad.append(f"{label}: raised {type(exc).__name__}: {exc}")

    def by_path(walked: Any) -> dict[str, Any]:
        return {f.path: f for f in walked.fields}

    # --- a valid answer of every tool is read completely ------------------------------------------------------------------------------------
    for tool in SIX:
        try:
            walked = walk(tool)
        except Exception as exc:
            bad.append(f"{tool}: a valid answer was refused ({exc})")
            continue
        if walked.missing or walked.unmapped:
            bad.append(f"{tool}: a valid answer left missing {walked.missing} unmapped {walked.unmapped}")
        paths = [f.path for f in walked.fields]
        if len(paths) != len(set(paths)):
            bad.append(f"{tool}: a path was produced twice")
        if not walked.observations():
            bad.append(f"{tool}: no observations")

    a = by_path(walk("analyze_token"))
    expected = {
        "asset.symbol": ("SOL", OBS),
        "asset.rank": ("7", OBS),
        "market.price_usd": ("100.5", OBS),
        "market.market_cap_usd": ("59000000000.0", OBS),
        "technical_analysis.rsi_14": ("39.8", OBS),
        "technical_analysis.trend": ("down", INT),
        "verdict": ("avoid", INT),
        "intelligence.narrative": ("A large-cap platform token.", INT),
        "performance.change_24h_pct": ("-1.02", OBS),
        "asset.chain": (None, OBS),
        "intelligence.catalysts[0]": ("Deep liquidity", INT),
    }
    for path, (value, klass) in expected.items():
        got = a.get(path)
        if got is None:
            bad.append(f"analyze_token: {path} was not read")
        elif got.value != value or got.klass is not klass:
            bad.append(f"analyze_token {path}: {got.value!r} {got.klass.value}, expected {value!r} {klass.value}")
    if a["asset.chain"].value is not None:
        bad.append("a null must stay unknown")

    # --- numbers are decimal text from the raw spelling ----------------------------------------------------------------------------------------
    walked = mod.walk(
        "analyze_token",
        {
            **data_of("analyze_token"),
            "market": {
                "price_usd": num("1.0E+2"),
                "market_cap_usd": num("-0.0"),
                "volume_24h_usd": num("2.2e+11"),
                "fully_diluted_value_usd": num("5e-05"),
            },
        },
    )
    vals = by_path(walked)
    for path, text in (
        ("market.price_usd", "100"),
        ("market.market_cap_usd", "0"),
        ("market.volume_24h_usd", "220000000000"),
        ("market.fully_diluted_value_usd", "0.00005"),
    ):
        if vals[path].value != text:
            bad.append(f"{path}: {vals[path].value!r}, expected {text!r}")

    huge = data_of("analyze_token")
    huge["market"]["price_usd"] = num("1e999")
    try:
        mod.walk("analyze_token", huge)
        bad.append("an absurd exponent was accepted")
    except mod.ConnectorError as exc:
        if exc.error_class is not E.MALFORMED:
            bad.append(f"an absurd exponent was classed {exc.error_class.value}")

    # --- text is NFC ---------------------------------------------------------------------------------------------------------------------------------
    decomposed = data_of("analyze_token", lambda d: d["asset"].update(name="Café"))
    if by_path(mod.walk("analyze_token", decomposed))["asset.name"].value != "Café":
        bad.append("text is not normalised to NFC")

    # --- the classification of what an arm may see --------------------------------------------------------------------------------------------------
    forbidden = (
        "verdict",
        "confluence",
        "trade_plan",
        "token_profile",
        "conclusion",
        "winner",
        "narrative",
        "catalysts",
        "risks",
        "reason",
        "momentum_score",
        "rank",
        "regime",
        "trend",
        "crowding_state",
        "pressure_state",
        "phase",
        "sentiment_regime",
        "material_shift",
        "veto",
        "squeeze_risk",
        "funding_trend",
        "liquidation_pressure",
        "established_asset",
        "change_direction",
        "summary",
        "scores",
        "confidence",
    )
    for tool in SIX:
        for f in walk(tool).observations():
            leaf = f.path.rsplit(".", 1)[-1]
            if f.path == "asset.rank" or (f.path.endswith(".rank") and "candidates" not in f.path):
                continue  # an asset's market-cap rank is a measurement; a candidate's rank is RYO's ordering
            if any(leaf == word or leaf.startswith(word + "[") for word in forbidden):
                bad.append(f"{tool}: {f.path} is exposed to the arms but looks like an interpretation")
    scan = by_path(walk("scan_market"))
    for path, klass in (
        ("candidates[0].rank", INT),
        ("candidates[0].symbol", OBS),
        ("candidates[0].price_usd", OBS),
        ("candidates[0].momentum_score", INT),
        ("candidates[0].reason", INT),
        ("candidate_count", META),
        ("selection_method", META),
    ):
        if scan[path].klass is not klass:
            bad.append(f"scan_market {path} is {scan[path].klass.value}")
    deep = walk("deep_analysis")
    dp = by_path(deep)
    for path, klass in (
        ("derivatives.funding_rate_bps", OBS),
        ("derivatives.funding_trend", INT),
        ("derivatives.veto", INT),
        ("verdict.call", INT),
        ("confluence.score", INT),
        ("performance.h24", OBS),
        ("market.mcap_usd", OBS),
    ):
        if dp[path].klass is not klass:
            bad.append(f"deep_analysis {path} is {dp[path].klass.value}")
    if sorted(deep.opaque) != ["confluence.gates[0].detail", "token_profile", "trade_plan"]:
        bad.append(f"deep_analysis opaque members: {deep.opaque}")
    mon = by_path(walk("monitor_market_sentiment_shift"))
    for path, klass in (
        ("evidence.fear_greed.value", OBS),
        ("evidence.fear_greed.material_shift", INT),
        ("evidence.funding.latest_bps", OBS),
        ("evidence.funding.crowding_state", INT),
        ("evidence.altseason.index", OBS),
        ("evidence.altseason.phase", INT),
        ("summary", INT),
        ("diagnostics.execution.elapsed_ms", META),
    ):
        if mon[path].klass is not klass:
            bad.append(f"monitor {path} is {mon[path].klass.value}")
    cmp_ = by_path(walk("compare_tokens"))
    for path, klass in (
        ("tokens[0].price_usd", OBS),
        ("tokens[0].metrics.trend", INT),
        ("tokens[0].metrics.rsi_14", OBS),
        ("factors[0].scores{SOL}", INT),
        ("conclusion.pick", INT),
        ("coverage_by_token{SOL}.market_data", META),
        ("winner", INT),
    ):
        if cmp_[path].klass is not klass:
            bad.append(f"compare_tokens {path} is {cmp_[path].klass.value}")
    mo = by_path(walk("market_overview"))
    for path, klass in (
        ("regime", INT),
        ("sentiment.fear_greed_index", OBS),
        ("market.breadth", OBS),
        ("top_movers.gainers[0].symbol", OBS),
        ("market.breadth_details.method", META),
    ):
        if mo[path].klass is not klass:
            bad.append(f"market_overview {path} is {mo[path].klass.value}")

    # --- missing, unmapped, null ----------------------------------------------------------------------------------------------------------------------
    w = walk("analyze_token", lambda d: d["market"].pop("volume_24h_usd"))
    if w.missing != ["market.volume_24h_usd"] or "market.volume_24h_usd" in by_path(w):
        bad.append(f"a missing optional member: {w.missing}")
    w = walk("analyze_token", lambda d: d["market"].update(surprise=1))
    if w.unmapped != ["market.surprise"] or "market.surprise" in by_path(w):
        bad.append(f"an unmapped member: {w.unmapped}")
    w = walk("analyze_token", lambda d: d.update(extra_section={"a": 1}))
    if w.unmapped != ["extra_section"]:
        bad.append(f"an unmapped top-level member: {w.unmapped}")
    w = walk("analyze_token", lambda d: d["market"].update(price_usd=None))
    if by_path(w)["market.price_usd"].value is not None:
        bad.append("a null price must stay unknown")
    w = walk("analyze_token", lambda d: (d["market"].pop("price_usd"), d["market"].pop("market_cap_usd")))
    if w.missing != ["market.market_cap_usd", "market.price_usd"]:
        bad.append(f"missing members are listed in name order: {w.missing}")
    extras = ("h", "g", "f", "e", "d", "c", "b", "a")
    w = walk("analyze_token", lambda d: d.update(dict.fromkeys(extras, 1)))
    if w.unmapped != sorted(extras):
        bad.append(f"unmapped members are listed in name order: {w.unmapped}")
    w = walk("analyze_token", lambda d: d.pop("intelligence"))
    if w.missing != ["intelligence"]:
        bad.append(f"a missing section: {w.missing}")
    w = walk("deep_analysis", lambda d: d.pop("trade_plan"))
    if "trade_plan" in w.opaque or "trade_plan" not in w.missing:
        bad.append("an absent opaque member is missing, not opaque")

    # --- shape changes are CONTRACT_DRIFT --------------------------------------------------------------------------------------------------------------
    refuses("a required object missing", "analyze_token", lambda d: d.pop("asset"))
    refuses("a required symbol missing", "analyze_token", lambda d: d["asset"].pop("symbol"))
    refuses("a required symbol null", "analyze_token", lambda d: d["asset"].update(symbol=None))
    refuses("candidates missing", "scan_market", lambda d: d.pop("candidates"))
    refuses("candidate_count missing", "scan_market", lambda d: d.pop("candidate_count"))
    refuses("a candidate without a symbol", "scan_market", lambda d: d["candidates"][0].pop("symbol"))
    refuses("a candidate with a null symbol", "scan_market", lambda d: d["candidates"][0].update(symbol=None))
    refuses("tokens missing", "compare_tokens", lambda d: d.pop("tokens"))
    refuses("a token with a null symbol", "compare_tokens", lambda d: d["tokens"][0].update(symbol=None))
    refuses("a token without a symbol", "compare_tokens", lambda d: d["tokens"][0].pop("symbol"))
    refuses("a deep analysis without its asset", "deep_analysis", lambda d: d.pop("asset"))
    refuses("a null catalyst", "analyze_token", lambda d: d["intelligence"].update(catalysts=[None]))
    refuses("a null risk", "analyze_token", lambda d: d["intelligence"].update(risks=[None]))
    for label, edit in (
        ("a number given as text", lambda d: d["market"].update(price_usd="100.5")),
        ("a number given as a boolean", lambda d: d["market"].update(price_usd=True)),
        ("a number given as an object", lambda d: d["market"].update(price_usd={})),
        ("a number given as a list", lambda d: d["market"].update(price_usd=[1])),
        ("text given as a number", lambda d: d.update(verdict=5)),
        ("text given as a boolean", lambda d: d.update(verdict=False)),
        ("text given as an object", lambda d: d.update(verdict={"call": "avoid"})),
        ("text given as a list", lambda d: d.update(verdict=["avoid"])),
        ("an object given as text", lambda d: d.update(market="x")),
        ("an object given as a list", lambda d: d.update(market=[])),
        ("an object given as null", lambda d: d.update(performance=None)),
        ("a list given as an object", lambda d: d["intelligence"].update(catalysts={})),
        ("a list given as text", lambda d: d["intelligence"].update(catalysts="a")),
        ("a null inside a text list", lambda d: d["intelligence"].update(risks=[None])),
        ("a number inside a text list", lambda d: d["intelligence"].update(risks=[5])),
        ("a text price", lambda d: d["market"].update(market_cap_usd="1.2B")),
    ):
        refuses(label, "analyze_token", edit)
    refuses("a verdict that became an object", "analyze_token", lambda d: d.update(verdict={"call": "avoid", "headline": "h"}))
    refuses("a verdict that became text", "deep_analysis", lambda d: d.update(verdict="avoid"))
    w = walk("deep_analysis", lambda d: d.update(derivatives=None))
    absent = sorted(m for m in w.missing if m.startswith("derivatives"))
    if len(absent) != 9 or any(f.path.startswith("derivatives") for f in w.fields):
        bad.append(f"a derivatives section left null (include_perp false) is nine absent fields and none read: {absent}")
    refuses("a derivatives section given as text", "deep_analysis", lambda d: d.update(derivatives="none"))
    refuses("a derivatives section given as a list", "deep_analysis", lambda d: d.update(derivatives=[]))
    refuses("a boolean given as text", "deep_analysis", lambda d: d["derivatives"].update(veto="false"))
    refuses("a boolean given as a number", "deep_analysis", lambda d: d["derivatives"].update(veto=0))
    refuses("a gate flag given as text", "deep_analysis", lambda d: d["confluence"]["gates"][0].update(passed="yes"))
    refuses("the established-asset flag given as text", "scan_market", lambda d: d["candidates"][0].update(established_asset="no"))
    refuses("a keyed value of the wrong type", "compare_tokens", lambda d: d["factors"][0]["scores"].update(SOL="high"))
    refuses("a keyed object given as a list", "compare_tokens", lambda d: d.update(coverage_by_token=[]))
    refuses("a keyed value given as text", "compare_tokens", lambda d: d["coverage_by_token"].update(SOL="available"))
    refuses("a funding rate given as text", "monitor_market_sentiment_shift", lambda d: d["evidence"]["funding"].update(latest_bps="0.4"))
    refuses("an altseason index given as text", "monitor_market_sentiment_shift", lambda d: d["evidence"]["altseason"].update(index="22"))
    refuses("a window given as text", "monitor_market_sentiment_shift", lambda d: d["evidence"]["funding"].update(current_7d_window="week"))
    refuses(
        "a fear and greed value given as text", "monitor_market_sentiment_shift", lambda d: d["evidence"]["fear_greed"].update(value="31")
    )
    refuses("a breadth given as text", "market_overview", lambda d: d["market"].update(breadth="0.38"))
    refuses("movers given as an object", "market_overview", lambda d: d["top_movers"].update(gainers={}))
    refuses("the regime given as a number", "market_overview", lambda d: d.update(regime=3))
    refuses("a candidate list given as text", "scan_market", lambda d: d.update(candidates="none"))
    refuses("a candidate given as text", "scan_market", lambda d: d.update(candidates=["x"]))
    refuses("a token given as text", "compare_tokens", lambda d: d.update(tokens=["SOL"]))

    # --- limits and unknown tools -----------------------------------------------------------------------------------------------------------------------
    refuses(
        "501 candidates", "scan_market", lambda d: d.update(candidates=[copy.deepcopy(d["candidates"][0]) for _ in range(501)]), E.MALFORMED
    )
    try:
        walked = mod.walk(
            "scan_market", data_of("scan_market", lambda d: d.update(candidates=[copy.deepcopy(d["candidates"][0]) for _ in range(500)]))
        )
        if len([f for f in walked.fields if f.path.endswith(".symbol")]) != 500:
            bad.append("500 candidates should all be read")
    except mod.ConnectorError:
        bad.append("500 candidates are allowed")
    try:
        mod.walk(
            "compare_tokens",
            data_of(
                "compare_tokens",
                lambda d: d["coverage_by_token"].update({f"T{i}": {"market_data": "a", "token_profile": "b"} for i in range(498)}),
            ),
        )
    except mod.ConnectorError:
        bad.append("500 keyed members are allowed")
    refuses(
        "501 keyed members",
        "compare_tokens",
        lambda d: d["coverage_by_token"].update({f"T{i}": {"market_data": "a", "token_profile": "b"} for i in range(501)}),
        E.MALFORMED,
    )
    try:
        mod.walk("check_safety", {})
        bad.append("an unknown tool was walked")
    except mod.ConnectorError as exc:
        if exc.error_class is not E.CONTRACT_DRIFT:
            bad.append("an unknown tool should be drift")
    try:
        mod.walk("analyze_token", [])  # type: ignore[arg-type]
        bad.append("data that is a list was walked")
    except mod.ConnectorError as exc:
        if exc.error_class is not E.CONTRACT_DRIFT:
            bad.append("data that is a list should be drift")

    # --- keyed paths and list paths ------------------------------------------------------------------------------------------------------------------------
    keyed = by_path(walk("compare_tokens", lambda d: d["factors"][0]["scores"].update({"A.B": 0.5, "é": 0.1})))
    if "factors[0].scores{A.B}" not in keyed or "factors[0].scores{é}" not in keyed:
        bad.append(f"keyed paths: {sorted(k for k in keyed if 'scores' in k)}")
    order = [
        f.path
        for f in walk("compare_tokens", lambda d: d["factors"][0].update(scores={"C": 0.3, "A": 0.1, "B": 0.2})).fields
        if "scores" in f.path
    ]
    if order != ["factors[0].scores{A}", "factors[0].scores{B}", "factors[0].scores{C}"]:
        bad.append(f"keyed members are read in key order: {order}")
    multi = by_path(walk("scan_market", lambda d: d.update(candidates=[d["candidates"][0], dict(d["candidates"][0], symbol="BBB")])))
    if multi["candidates[1].symbol"].value != "BBB" or "candidates[0].symbol" not in multi:
        bad.append("list items are indexed in order")

    # --- the table -----------------------------------------------------------------------------------------------------------------------------------------------
    for tool in SIX:
        rows = mod.classification_table(tool)
        patterns = {path for path, _k, _t in rows}
        if len(patterns) != len(rows):
            bad.append(f"{tool}: a path pattern twice in the table")
        for f in walk(tool).fields:
            generic = _generic(f.path)
            if generic not in patterns:
                bad.append(f"{tool}: {f.path} ({generic}) is not in the table")
        if tool == "deep_analysis" and {p: kd for p, _c, kd in rows}["trade_plan"] != "opaque":
            bad.append("the trade plan is an opaque member in the table")
        kinds = {kind for _p, _c, kind in rows}
        if not kinds <= {"text", "number", "bool", "opaque"}:
            bad.append(f"{tool}: kinds {kinds}")
    for tool in SIX:
        for path, klass, kind in mod.classification_table(tool):
            if kind == "opaque" and klass != "INTERPRETATION":
                bad.append(f"{tool}: the opaque member {path} must be an interpretation")
        walked = walk(tool)
        if [f for f in walked.fields if f.klass is OBS] != walked.observations():
            bad.append(f"{tool}: observations() returned something other than the observation fields")
        if any(f.klass is not OBS for f in walked.observations()):
            bad.append(f"{tool}: an arm would see a field that is not an observation")
        classes = {klass for tool in SIX for _p, klass, _k in mod.classification_table(tool)}
    if classes != {"OBSERVATION", "INTERPRETATION", "META"}:
        bad.append(f"classes in the table: {classes}")
    ao = {p: c for p, c, _k in mod.classification_table("analyze_token")}
    if ao["market.price_usd"] != "OBSERVATION" or ao["verdict"] != "INTERPRETATION" or ao["intelligence.catalysts[]"] != "INTERPRETATION":
        bad.append("the table rows of analyze_token")
    sha = mod.table_sha256()
    if len(sha) != 64 or sha != mod.table_sha256():
        bad.append("the table hash is not stable")
    original_envelope = dict(mod.ENVELOPE_CLASSES)
    try:
        mod.ENVELOPE_CLASSES["warnings[]"] = INT
        if mod.table_sha256() == sha:
            bad.append("the table hash ignores the envelope classes")
    finally:
        mod.ENVELOPE_CLASSES.clear()
        mod.ENVELOPE_CLASSES.update(original_envelope)
    original_spec = mod.SPECS["analyze_token"]
    try:
        members = dict(original_spec.members)
        members["verdict"] = mod.Leaf("text", OBS)
        mod.SPECS["analyze_token"] = mod.Obj(members, original_spec.required)
        if mod.table_sha256() == sha:
            bad.append("the table hash ignores a field's class")
    finally:
        mod.SPECS["analyze_token"] = original_spec
    if mod.table_sha256() != sha:
        bad.append("the table hash did not return after the specification was restored")
    if mod.ENVELOPE_CLASSES != {"summary.headline": INT, "summary.key_points[]": INT, "availability{}": META, "warnings[]": META}:
        bad.append(f"envelope classes: {mod.ENVELOPE_CLASSES}")

    # --- the words of a drift, the shapes of the specs, the order of what is listed, and the pinned table ------------------------------
    for label, value, words in (
        ("null", None, "null"),
        ("a boolean", True, "a boolean"),
        ("text", "x", "text"),
        ("an object", {}, "an object"),
        ("a list", [], "a list"),
        ("a number", num("1"), "a number"),
        ("anything else is named by its type", 1.5, "float"),
    ):
        if mod._describe(value) != words:
            bad.append(f"the description of {label}: got {mod._describe(value)!r}, expected {words!r}")
    if mod._drift("a.b", "is x").message != "RYO's data.a.b is x" or mod._drift("", "is x").message != "RYO's data is x":
        bad.append(f"the words of a drift: {mod._drift('a.b', 'is x').message!r}, {mod._drift('', 'is x').message!r}")
    for cls in (mod.FieldValue, mod.Keyed, mod.Leaf, mod.Obj, mod.Opaque, mod.Seq):
        if cls.__dataclass_params__.frozen is not True:  # type: ignore[attr-defined]
            bad.append(f"{cls.__name__} is not frozen: a specification must not change after it is read")
    leaf = mod._text()
    if (leaf.nullable, mod._section({"x": leaf}).optional, mod._obj({"x": leaf}).optional) != (True, True, False):
        bad.append("the defaults of a leaf and the optional flag of a section and of an object")
    unsorted_members = mod.Obj({"b": leaf, "a": leaf})
    gone = mod.Walked()
    mod._absent("x", unsorted_members, gone)
    if gone.missing != ["x.a", "x.b"]:
        bad.append(f"the fields of a section left out are listed in order: {gone.missing}")
    rows: list[tuple[str, str, str]] = []
    mod._rows("", unsorted_members, rows)
    if [r[0] for r in rows] != ["a", "b"]:
        bad.append(f"the rows of the table are listed in order: {[r[0] for r in rows]}")
    pinned = (
        "1c419cb214fc81c1522ece0267988dd99c247bb1b04433eae8653b4872ca0be5"  # a deliberate change of the table updates this and the protocol
    )
    if mod.table_sha256() != pinned:
        bad.append(f"the table is not the pinned one: {mod.table_sha256()}")
    saved_specs, saved_env = mod.SPECS, mod.ENVELOPE_CLASSES
    try:
        mod.SPECS = dict(reversed(list(saved_specs.items())))
        mod.ENVELOPE_CLASSES = dict(reversed(list(saved_env.items())))
        if mod.table_sha256() != pinned:
            bad.append("the hash of the table depends on the order the tools and members were written in")
    finally:
        mod.SPECS, mod.ENVELOPE_CLASSES = saved_specs, saved_env
    return bad


def _generic(path: str) -> str:
    """A concrete path as its table pattern: list indexes become [], keyed members become {}."""
    out, i = [], 0
    while i < len(path):
        ch = path[i]
        if ch == "[":
            j = path.index("]", i)
            out.append("[]")
            i = j + 1
        elif ch == "{":
            depth, j = 1, i + 1
            while depth:
                depth += (path[j] == "{") - (path[j] == "}")
                j += 1
            out.append("{}")
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)
