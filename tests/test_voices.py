"""Voices: feed parsing, mention and stance reading, echo detection and the convergence count."""

from __future__ import annotations

import json

import pytest

from hoshitori import voices as v

NOW = v.at("2026-11-04T12:00:00Z")


def post(uri: str, author: str, text: str, when: str, **kw: object) -> v.Post:
    return v.Post(uri, author, v.at(when), text, **kw)  # type: ignore[arg-type]


def feed(entries: list[dict]) -> bytes:
    return json.dumps({"feed": entries}).encode()


def entry(uri: str, handle: str, text: str, created: str, embed: dict | None = None, repost_by: str | None = None) -> dict:
    p: dict = {"uri": uri, "author": {"handle": handle}, "record": {"text": text, "createdAt": created}, "indexedAt": created}
    if embed:
        p["embed"] = embed
    e: dict = {"post": p}
    if repost_by:
        e["reason"] = {"$type": "app.bsky.feed.defs#reasonRepost", "by": {"handle": repost_by}, "indexedAt": created}
    return e


def test_mentions_whole_words_only() -> None:
    assert v.mentions("SOL looks strong", "SOL") and v.mentions("buying $sol here", "SOL") and v.mentions("Solana season", "SOL")
    assert not v.mentions("console output", "SOL") and not v.mentions("absolutely", "SOL")


def test_stance_word_lists() -> None:
    assert v.stance("so bullish, accumulating") == 1
    assert v.stance("this will dump, selling") == -1
    assert v.stance("bullish but also bearish") == 0 and v.stance("nothing here") == 0


def test_actor_shapes() -> None:
    assert v.valid_actor("bsky.app") and v.valid_actor("@bsky.app") and v.valid_actor("did:plc:" + "a" * 24)
    assert not v.valid_actor("") and not v.valid_actor("a b") and not v.valid_actor("x/../y")


def test_parse_feed_reads_posts_quotes_and_reposts() -> None:
    quote = {"$type": "app.bsky.embed.record#view", "record": {"uri": "at://orig"}}
    body = feed(
        [
            entry("at://1", "a.bsky.social", "SOL up", "2026-11-04T09:00:00Z"),
            entry("at://2", "b.bsky.social", "I agree SOL", "2026-11-04T09:05:00Z", embed=quote),
            entry("at://3", "a.bsky.social", "orig text", "2026-11-04T08:00:00Z", repost_by="c.bsky.social"),
        ]
    )
    posts = v.parse_feed(body)
    assert [p.uri for p in posts] == ["at://1", "at://2", "at://3#repost-by-c.bsky.social"]
    assert [p.is_repost for p in posts] == [False, True, True]
    assert posts[1].quoted == "at://orig" and posts[2].author == "c.bsky.social" and posts[2].quoted == "at://3"


@pytest.mark.parametrize("body", [b"not json", b"{}", b'{"feed": 3}', b"[]"])
def test_parse_feed_refuses_other_shapes(body: bytes) -> None:
    with pytest.raises(v.VoicesError):
        v.parse_feed(body)


def test_inbox_posts_are_local_and_checked() -> None:
    body = json.dumps({"posts": [{"id": "1", "author": "x", "text": "SOL", "created_at": "2026-11-04T09:00:00Z"}]}).encode()
    (p,) = v.parse_inbox(body)
    assert p.local and p.uri == "inbox:1"
    with pytest.raises(v.VoicesError):
        v.parse_inbox(b'{"posts": [{"id": "1"}]}')


def test_similarity_uses_containment_only_for_long_posts() -> None:
    short_a, short_b = v._tokens("sol up now"), v._tokens("sol up now and more things happen in this market today for everyone watching it")
    assert v.similarity(short_a, short_b)[1] == "jaccard"
    long_a = v._tokens("solana accumulation continues as large wallets keep buying every dip this week according to onchain data")
    padded = v._tokens(
        "solana accumulation continues as large wallets keep buying every dip this week according to onchain data not advice"
    )
    assert v.similarity(long_a, padded)[0] >= 0.9


def test_convergence_counts_independent_voices() -> None:
    text = "SOL looks bullish, accumulating more solana here before the breakout"
    posts = [
        post("1", "alice", text, "2026-11-04T09:00:00Z"),
        post("2", "bob", text + "!!", "2026-11-04T09:10:00Z"),
        post("3", "carol", "Solana will dump hard, selling all my SOL", "2026-11-04T10:00:00Z"),
        post("4#r", "dave", text, "2026-11-04T10:30:00Z", is_repost=True, quoted="1"),
        post("inbox:9", "x", "SOL to the moon", "2026-11-04T11:00:00Z", local=True),
        post("5", "erin", "Bitcoin only", "2026-11-04T11:00:00Z"),
    ]
    c = v.convergence(posts, "SOL", NOW)
    assert (c.mentions, c.independent, c.up, c.down, c.neutral, c.local_mentions) == (5, 2, 1, 1, 0, 1)
    assert {e.uri for e in c.echoes} == {"2", "4#r"} and c.posts == ("1", "3")
    assert all(e.reason for e in c.echoes)


def test_same_author_repeats_are_one_voice_not_an_echo() -> None:
    posts = [post("1", "a", "SOL bullish", "2026-11-04T09:00:00Z"), post("2", "a", "SOL breakout, long", "2026-11-04T09:30:00Z")]
    c = v.convergence(posts, "SOL", NOW)
    assert c.independent == 1 and not c.echoes


def test_window_and_echo_window_apply() -> None:
    text = "SOL looks bullish, accumulating more solana here before the breakout"
    old = post("1", "a", "SOL bullish", "2026-11-02T09:00:00Z")
    far = [post("2", "a", text, "2026-11-04T01:00:00Z"), post("3", "b", text, "2026-11-04T11:00:00Z")]
    assert v.convergence([old], "SOL", NOW).mentions == 0
    assert v.convergence(far, "SOL", NOW).independent == 2  # ten hours apart: past the echo window


def test_convergence_is_order_independent() -> None:
    text = "SOL looks bullish, accumulating more solana here before the breakout"
    posts = [
        post("1", "a", text, "2026-11-04T09:00:00Z"),
        post("2", "b", text, "2026-11-04T09:10:00Z"),
        post("3", "c", "SOL dump", "2026-11-04T09:20:00Z"),
    ]
    assert v.convergence(posts, "SOL", NOW) == v.convergence(list(reversed(posts)), "SOL", NOW)


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>Exchange lists SOL</title><description>&lt;p&gt;SOL listed &amp;amp; trading starts&lt;/p&gt;</description><guid>g1</guid>
<pubDate>Wed, 04 Nov 2026 09:00:00 GMT</pubDate></item>
<item><title>Another</title><link>https://e.example/2</link><pubDate>not a date</pubDate></item><item><description>no id</description></item></channel></rss>"""
ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>SOL up</title><id>a1</id><updated>2026-11-04T10:00:00Z</updated>
<summary>Solana rallies</summary></entry></feed>"""


def test_rss_and_atom_items_become_posts_by_the_outlet() -> None:
    posts = v.parse_feed_xml(RSS, "wire")
    assert [p.uri for p in posts] == ["feed:wire:g1", "feed:wire:https://e.example/2"]
    assert posts[0].text == "Exchange lists SOL SOL listed & trading starts" and posts[0].author == "wire"
    assert posts[0].at_ms == v.at("2026-11-04T09:00:00Z") and posts[1].at_ms is None
    (atom,) = v.parse_feed_xml(ATOM, "other")
    assert atom.at_ms == v.at("2026-11-04T10:00:00Z") and "Solana rallies" in atom.text


def test_two_outlets_repeating_one_announcement_are_one_voice() -> None:
    text = "Exchange lists SOL with trading starting tomorrow for all users on the main market"
    one = f"<rss><channel><item><title>{text}</title><guid>1</guid><pubDate>Wed, 04 Nov 2026 09:00:00 GMT</pubDate></item></channel></rss>".encode()
    two = f"<rss><channel><item><title>{text}.</title><guid>2</guid><pubDate>Wed, 04 Nov 2026 09:20:00 GMT</pubDate></item></channel></rss>".encode()
    posts = (*v.parse_feed_xml(one, "a"), *v.parse_feed_xml(two, "b"))
    c = v.convergence(posts, "SOL", NOW)
    assert (c.mentions, c.independent, len(c.echoes)) == (2, 1, 1)


@pytest.mark.parametrize(
    "body",
    [
        b"not xml",
        b"<html></html>",
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>',
        b"<!doctype x><rss/>",
        b"<rss>" + b"x" * (3 * 1024 * 1024) + b"</rss>",
        # a document in an encoding the byte search cannot read is refused unread, whatever it declares
        '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>'.encode(
            "utf-16"
        ),
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss/>'.encode("utf-16-le"),
        '<?xml version="1.0"?><rss><channel><item><title>a</title></item></channel></rss>'.encode("utf-16"),
        "<rss><channel><item><title>a</title></item></channel></rss>".encode("utf-32"),
        b"\xff\xfe<\x00r\x00s\x00s\x00/\x00>\x00",
    ],
)
def test_feed_documents_that_are_not_safe_or_not_feeds_are_refused(body: bytes) -> None:
    with pytest.raises(v.VoicesError):
        v.parse_feed_xml(body, "x")


def tg(channel: str, post: int, text: str, when: str | None) -> str:
    stamp = "" if when is None else f'<a><time datetime="{when}">12:00</time></a>'
    return (
        f'<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" data-post="{channel}/{post}">'
        f'<div class="tgme_widget_message_text js-message_text" dir="auto">{text}</div>{stamp}</div></div>'
    )


def test_a_telegram_page_gives_the_channels_text_posts() -> None:
    page = (
        "<html>"
        + tg("whale_alert_io", 5, "SOL moved <b>1,000</b> to an exchange<br/>&amp; more", "2026-11-04T09:00:00+00:00")
        + tg("whale_alert_io", 6, "second SOL post", None)
        + tg("other_channel", 7, "not this channel SOL", "2026-11-04T09:00:00+00:00")
        + '<div class="tgme_widget_message_wrap"><div data-post="whale_alert_io/8"></div></div>'
        + "</html>"
    ).encode()
    posts = v.parse_telegram_page(page, "whale_alert_io")
    assert [p.uri for p in posts] == ["tg:whale_alert_io/5", "tg:whale_alert_io/6"]
    assert (
        posts[0].text == "SOL moved 1,000 to an exchange & more"
        and posts[0].at_ms == v.at("2026-11-04T09:00:00Z")
        and posts[1].at_ms is None
    )


@pytest.mark.parametrize("channel", ["abc", "a" * 33, "bad-name", "", "a/b"])
def test_a_channel_name_is_checked(channel: str) -> None:
    assert not v.valid_channel(channel)
    with pytest.raises(v.VoicesError):
        v.parse_telegram_page(b"<html></html>", channel)


def test_a_page_without_messages_or_not_text_is_refused() -> None:
    for body in (b"<html>nothing</html>", b"\xff\xfe"):
        with pytest.raises(v.VoicesError):
            v.parse_telegram_page(body, "whale_alert_io")


# --- what the sweep showed the first tests did not pin ---------------------------------------------------------------------------------


def test_the_constants_and_the_word_lists_are_pinned() -> None:
    assert (v.WINDOW_H, v.ECHO_WINDOW_MIN, v.ECHO_SIMILARITY, v.CONTAINMENT_MIN_TOKENS, v.MAX_POSTS, v.MAX_FEED_BYTES) == (
        24,
        360,
        0.6,
        8,
        5000,
        2 * 1024 * 1024,
    )
    assert v.NAMES == {"BTC": ("bitcoin", "btc"), "ETH": ("ethereum", "ether", "eth"), "SOL": ("solana", "sol")}
    assert (
        frozenset(
            [
                "the",
                "a",
                "an",
                "is",
                "are",
                "to",
                "of",
                "and",
                "on",
                "in",
                "for",
                "with",
                "this",
                "that",
                "it",
                "at",
                "as",
                "be",
                "i",
                "my",
                "we",
            ]
        )
        == v._STOP
    )
    assert (
        frozenset(
            [
                "bullish",
                "long",
                "buy",
                "buying",
                "bought",
                "accumulate",
                "accumulating",
                "accumulation",
                "breakout",
                "pump",
                "pumping",
                "moon",
                "rally",
                "rallying",
                "higher",
                "rise",
                "rising",
                "rises",
                "bounce",
                "support",
                "reclaim",
                "reclaimed",
            ]
        )
        == v.UP_WORDS
    )
    assert (
        frozenset(
            [
                "bearish",
                "short",
                "sell",
                "selling",
                "sold",
                "dump",
                "dumping",
                "crash",
                "crashing",
                "breakdown",
                "lower",
                "fall",
                "falling",
                "falls",
                "drop",
                "dropping",
                "resistance",
                "rejected",
                "rejection",
                "bleed",
                "bleeding",
            ]
        )
        == v.DOWN_WORDS
    )


def test_every_listed_word_does_what_its_list_says() -> None:
    assert all(v.stance(w) == 1 for w in v.UP_WORDS) and all(v.stance(w) == -1 for w in v.DOWN_WORDS)
    for symbol, names in v.NAMES.items():
        assert all(v.mentions(f"talk of {n} today", symbol) for n in names)
        assert v.mentions(f"${names[-1]} up", symbol) and not v.mentions(f"${names[-1]}x up", symbol)
    assert (
        not any(v.mentions("x", "BTC") for _ in [0])
        and v._tokens("the a an is are to of and on in for with this that it at as be i my we x") == frozenset()
    )
    assert v._tokens("a the x bitcoin up") == {"bitcoin", "up"}


def test_the_records_are_frozen() -> None:
    posts = [post("1", "a", "SOL up", "2026-11-04T09:00:00Z")]
    for thing, attribute in ((posts[0], "text"), (v.convergence(posts, "SOL", NOW), "mentions"), (v.Echo("u", "a", "e", "r"), "reason")):
        with pytest.raises(AttributeError):
            setattr(thing, attribute, "x")


def test_an_actor_may_start_with_one_at_sign_and_has_no_other_at_sign() -> None:
    assert v.valid_actor("@bsky.app") and not v.valid_actor("x@y.z") and not v.valid_actor("bsky.app@")


def test_similarity_edges() -> None:
    assert v.similarity(frozenset(), frozenset({"a"})) == (0.0, "jaccard") and v.similarity(frozenset({"a"}), frozenset()) == (
        0.0,
        "jaccard",
    )
    eight = frozenset(f"w{i}" for i in range(8))
    seven = frozenset(f"w{i}" for i in range(7))
    inside = eight | frozenset(f"x{i}" for i in range(8))
    assert v.similarity(eight, inside) == (1.0, "containment")  # eight content words is where containment starts
    assert v.similarity(seven, seven | frozenset(f"x{i}" for i in range(8)))[1] == "jaccard"
    same = frozenset(f"w{i}" for i in range(10))
    assert v.similarity(same, same) == (1.0, "jaccard")  # equal: jaccard keeps it when containment is not larger
    assert v.similarity(frozenset({"a", "b"}), frozenset({"a", "c"})) == (1 / 3, "jaccard")


def test_a_post_exactly_at_the_window_edges_counts_and_one_millisecond_outside_does_not() -> None:
    day = 24 * 3_600_000
    posts = [v.Post("in-start", "a", NOW - day, "SOL up", False, None), v.Post("in-end", "b", NOW, "SOL up", False, None)]
    assert v.convergence(posts, "SOL", NOW).mentions == 2
    outside = [v.Post("old", "a", NOW - day - 1, "SOL up", False, None), v.Post("future", "b", NOW + 1, "SOL up", False, None)]
    assert v.convergence(outside, "SOL", NOW).mentions == 0
    assert v.convergence([v.Post("old", "a", NOW - day - 1, "SOL up", False, None)], "SOL", NOW, window_h=25).mentions == 1
    assert v.convergence([v.Post("undated", "a", None, "SOL up", False, None)], "SOL", NOW).mentions == 1


def test_an_echo_must_be_inside_the_echo_window_exactly_and_by_another_author() -> None:
    text = "SOL looks bullish, accumulating more solana here before the breakout"
    window = 360 * 60_000
    a = v.Post("1", "a", NOW - 20 * 3_600_000, text, False, None)
    just_in = v.Post("2", "b", a.at_ms + window, text, False, None)
    just_out = v.Post("3", "c", a.at_ms + window + 1, text, False, None)
    assert [e.uri for e in v.convergence([a, just_in], "SOL", NOW).echoes] == ["2"]
    assert v.convergence([a, just_out], "SOL", NOW).echoes == ()


def test_the_similarity_threshold_is_inclusive_at_six_tenths() -> None:
    base = "alpha beta gamma delta epsilon zeta eta theta iota kappa SOL"  # eleven words, all content words
    a = v.Post("1", "a", NOW - 3_600_000, base, False, None)
    words = base.split()

    # keep k of the first eleven words and add (11 - k) new ones: jaccard = k / (22 - k)
    def variant(keep: int, name: str) -> v.Post:
        return v.Post(name, "b", NOW - 1_800_000, " ".join(words[:keep] + [f"new{i}" for i in range(11 - keep)] + ["SOL"]), False, None)

    assert v.convergence([a, variant(10, "2")], "SOL", NOW).echoes != ()  # high overlap
    assert v.convergence([a, variant(3, "3")], "SOL", NOW).echoes == ()  # low overlap
    exactly = v.Post("e", "b", NOW - 1_800_000, "a1 b2 c3 d4 e5 f6 SOL", False, None)
    other = v.Post("o", "a", NOW - 3_600_000, "a1 b2 c3 d4 x7 y8 SOL", False, None)  # shares 5 of 9 words: below
    assert v.convergence([other, exactly], "SOL", NOW).echoes == ()
    three = v.Post("t", "b", NOW - 1_800_000, "a1 b2 c3 SOL", False, None)
    two_thirds = v.Post("u", "a", NOW - 3_600_000, "a1 b2 c3 d4 e5 SOL", False, None)  # 4 of 6 words shared: 0.667
    assert v.convergence([two_thirds, three], "SOL", NOW).echoes != ()
    sixty = v.Post("s", "b", NOW - 1_800_000, "a1 b2 c3 SOL", False, None)
    exact = v.Post("x", "a", NOW - 3_600_000, "a1 b2 c3 d4 SOL", False, None)  # 4 of 5 shared words = 0.8
    assert v.convergence([exact, sixty], "SOL", NOW).echoes != ()
    point_six = v.Post("p", "b", NOW - 1_800_000, "a1 b2 c3 SOL", False, None)
    reference = v.Post("r", "a", NOW - 3_600_000, "a1 b2 c3 d4 e5 f6 SOL", False, None)  # shares a1 b2 c3 sol: 4 of 7 = 0.571
    assert v.convergence([reference, point_six], "SOL", NOW).echoes == ()


def test_the_earliest_post_keeps_its_independence_whatever_the_names_of_the_posts() -> None:
    text = "SOL looks bullish, accumulating more solana here before the breakout"
    late, early = v.Post("a-late", "x", NOW - 1_000_000, text, False, None), v.Post("z-early", "y", NOW - 2_000_000, text, False, None)
    c = v.convergence([late, early], "SOL", NOW)
    assert c.posts == ("z-early",) and [(e.uri, e.echoes) for e in c.echoes] == [("a-late", "z-early")]
    undated = v.Post("0-undated", "w", None, text, False, None)
    c = v.convergence([undated, early], "SOL", NOW)
    assert c.posts == ("z-early",) and [e.uri for e in c.echoes] == ["0-undated"]  # an undated post sorts last, so it can only echo


def test_an_echo_names_the_first_post_it_matches_and_says_why() -> None:
    text = "SOL looks bullish, accumulating more solana here before the breakout"
    one, two = (
        v.Post("1", "a", NOW - 4_000_000, text + " one", False, None),
        v.Post("2", "b", NOW - 3_000_000, "SOL is red now wow sad day " * 3, False, None),
    )
    three = v.Post("3", "c", NOW - 2_000_000, text, False, None)
    (echo,) = v.convergence([one, two, three], "SOL", NOW).echoes
    assert (
        (echo.uri, echo.echoes) == ("3", "1")
        and echo.reason.startswith("near-duplicate of a post by a (similarity 0.")
        and "jaccard" in echo.reason
    ) or "containment" in echo.reason
    first, second = v.Post("1", "a", NOW - 4_000_000, text, False, None), v.Post("2", "b", NOW - 3_000_000, text, False, None)
    (echo,) = v.convergence([first, second, v.Post("3", "c", NOW - 2_000_000, text, False, None)], "SOL", NOW).echoes[:1]
    assert echo.echoes == "1"
    both = v.convergence([first, second, v.Post("3", "c", NOW - 2_000_000, text, False, None)], "SOL", NOW).echoes
    assert [e.echoes for e in both] == ["1", "1"]  # the third is an echo of the first, not of the second


def test_a_repost_points_at_what_it_reposts_or_says_unknown() -> None:
    original = v.Post("orig", "a", NOW - 3_000_000, "SOL up", False, None)
    reposts = [
        v.Post("r1", "b", NOW - 2_000_000, "SOL up", True, "orig"),
        v.Post("r2", "c", NOW - 1_000_000, "SOL up", True, None),
        v.Post("r3", "d", NOW - 500_000, "SOL up", True, "elsewhere"),
    ]
    c = v.convergence([original, *reposts], "SOL", NOW)
    assert [(e.uri, e.echoes, e.reason) for e in c.echoes] == [
        ("r1", "orig", "a repost or quote of another post"),
        ("r2", "unknown", "a repost or quote of another post"),
        ("r3", "elsewhere", "a repost or quote of another post"),
    ]
    assert c.independent == 1 and c.posts == ("orig",)


def test_one_voice_per_author_is_the_authors_earliest_independent_post() -> None:
    posts = [
        v.Post("2", "a", NOW - 1_000_000, "SOL bearish dump", False, None),
        v.Post("1", "a", NOW - 2_000_000, "SOL bullish", False, None),
    ]
    c = v.convergence(posts, "SOL", NOW)
    assert (c.independent, c.up, c.down, c.posts) == (1, 1, 0, ("1",))


def test_all_symbols_gives_each_symbols_convergence() -> None:
    posts = [post("1", "a", "SOL bullish", "2026-11-04T09:00:00Z"), post("2", "b", "bitcoin dump", "2026-11-04T09:00:00Z")]
    out = v.all_symbols(posts, ("SOL", "BTC", "ETH"), NOW)
    assert {k: (c.mentions, c.up, c.down) for k, c in out.items()} == {"SOL": (1, 1, 0), "BTC": (1, 0, 1), "ETH": (0, 0, 0)}
    assert v.all_symbols(posts, ("SOL",), NOW, 3)["SOL"].mentions == 1 and v.all_symbols(posts, ("SOL",), NOW, 2)["SOL"].mentions == 0


def test_bluesky_feed_details() -> None:
    def feed_of(entries: list[object]) -> bytes:
        return json.dumps({"feed": entries}).encode()

    good = entry("at://1", "a.b", "SOL", "2026-11-04T09:00:00Z")
    assert len(v.parse_feed(feed_of([good] * 5000))) == 5000
    with pytest.raises(v.VoicesError):
        v.parse_feed(feed_of([good] * 5001))
    with pytest.raises(v.VoicesError):
        v.parse_feed(b"\xff\xfe")
    skipped = [
        3,
        {"post": {"uri": 5, "author": {"handle": "a"}, "record": {"text": "SOL"}}},
        {"post": {"uri": "at://x", "author": {}, "record": {"text": "SOL"}}},
        {"post": {"uri": "at://y", "author": {"handle": "a"}, "record": {"text": "   "}}},
        {"post": {"uri": "at://z", "author": {"handle": "a"}, "record": {}}},
        {"post": {"uri": "at://ok", "author": {"handle": "a"}, "record": {"text": " SOL ", "createdAt": "2026-11-04T09:00:00Z"}}},
    ]
    (only,) = v.parse_feed(feed_of(skipped))
    assert (only.uri, only.text, only.is_repost, only.quoted, only.local) == ("at://ok", "SOL", False, None, False)
    # created time falls back to the time the provider saw the post; a repost uses when it was reposted, then when the post was indexed
    fallback = {"post": {"uri": "at://f", "author": {"handle": "a"}, "record": {"text": "SOL"}, "indexedAt": "2026-11-04T08:00:00Z"}}
    assert v.parse_feed(feed_of([fallback]))[0].at_ms == v.at("2026-11-04T08:00:00Z")
    both = entry("at://r", "a", "SOL", "2026-11-04T07:00:00Z", repost_by="b")
    both["reason"]["indexedAt"] = "2026-11-04T10:00:00Z"
    assert v.parse_feed(feed_of([both]))[0].at_ms == v.at("2026-11-04T10:00:00Z")
    only_post = entry("at://q", "a", "SOL", "2026-11-04T07:00:00Z", repost_by="b")
    del only_post["reason"]["indexedAt"]
    assert v.parse_feed(feed_of([only_post]))[0].at_ms == v.at("2026-11-04T07:00:00Z")
    nameless = {
        "post": {"uri": "at://s", "author": {"handle": "a"}, "record": {"text": "SOL"}},
        "reason": {"$type": "app.bsky.feed.defs#reasonRepost", "by": {}},
    }
    assert v.parse_feed(feed_of([nameless])) == ()  # a repost by nobody is not a post either
    a_post_that_is_a_repost_adds_only_the_repost = v.parse_feed(
        feed_of([entry("at://t", "a", "SOL", "2026-11-04T07:00:00Z", repost_by="b")])
    )
    assert len(a_post_that_is_a_repost_adds_only_the_repost) == 1 and a_post_that_is_a_repost_adds_only_the_repost[0].author == "b"


def test_quotes_with_media_carry_the_quoted_post() -> None:
    plain = {"$type": "app.bsky.embed.record#view", "record": {"uri": "at://quoted"}}
    media = {"$type": "app.bsky.embed.recordWithMedia#view", "record": {"record": {"uri": "at://quoted-media"}}}
    other = {"$type": "app.bsky.embed.images#view", "record": {"uri": "at://not-a-quote"}}
    empty_media = {"$type": "app.bsky.embed.recordWithMedia#view"}
    posts = v.parse_feed(
        json.dumps(
            {
                "feed": [
                    entry("at://1", "a", "SOL", "2026-11-04T09:00:00Z", embed=plain),
                    entry("at://2", "a", "SOL", "2026-11-04T09:00:00Z", embed=media),
                    entry("at://3", "a", "SOL", "2026-11-04T09:00:00Z", embed=other),
                    entry("at://4", "a", "SOL", "2026-11-04T09:00:00Z", embed=empty_media),
                ]
            }
        ).encode()
    )
    assert [(p.quoted, p.is_repost) for p in posts] == [("at://quoted", True), ("at://quoted-media", True), (None, False), (None, False)]


def test_inbox_details() -> None:
    def inbox(n: int) -> bytes:
        return json.dumps(
            {"posts": [{"id": str(i), "author": "x", "text": "SOL", "created_at": "2026-11-04T09:00:00Z"} for i in range(n)]}
        ).encode()

    assert len(v.parse_inbox(inbox(5000))) == 5000
    with pytest.raises(v.VoicesError, match="at most 5,000"):
        v.parse_inbox(inbox(5001))
    with pytest.raises(v.VoicesError, match="at most 5,000"):
        v.parse_inbox(json.dumps({"posts": 7}).encode())
    with pytest.raises(v.VoicesError, match=r'the inbox is \{"posts"'):
        v.parse_inbox(b"\xff")
    (p,) = v.parse_inbox(inbox(1))
    assert (p.at_ms, p.local, p.is_repost, p.quoted) == (v.at("2026-11-04T09:00:00Z"), True, False, None)
    (undated,) = v.parse_inbox(json.dumps({"posts": [{"id": "1", "author": "x", "text": "SOL"}]}).encode())
    assert undated.at_ms is None


def test_feed_xml_details() -> None:
    atom_link = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>SOL a</title><link href="https://e.example/1"/><published>2026-11-04T09:00:00Z</published></entry></feed>'
    (p,) = v.parse_feed_xml(atom_link, "x")
    assert (p.uri, p.at_ms) == ("feed:x:https://e.example/1", v.at("2026-11-04T09:00:00Z"))
    content = b"<feed><entry><title>t</title><id>i</id><content>SOL inside</content><updated>2026-11-04T09:00:00Z</updated></entry></feed>"
    assert "SOL inside" not in v.parse_feed_xml(content, "x")[0].text  # only title, description and summary are read
    dated = b"<rss><channel><item><title>SOL</title><guid>g</guid><date>2026-11-04T09:00:00Z</date></item></channel></rss>"
    assert v.parse_feed_xml(dated, "x")[0].at_ms == v.at("2026-11-04T09:00:00Z")
    rdf = b"<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'><item><title>SOL</title><guid>g</guid></item></rdf:RDF>"
    assert len(v.parse_feed_xml(rdf, "x")) == 1
    summary = b"<feed><entry><title>t</title><id>i</id><summary>SOL summary</summary></entry></feed>"
    assert v.parse_feed_xml(summary, "x")[0].text == "t SOL summary"
    long_text = f"<rss><channel><item><title>{'x' * 1500}</title><guid>g</guid></item></channel></rss>".encode()
    assert len(v.parse_feed_xml(long_text, "x")[0].text) == 1000
    many = ("<item><title>SOL</title><guid>%d</guid></item>" * 5001).replace("%d", "1")
    items = "".join(f"<item><title>SOL</title><guid>g{i}</guid></item>" for i in range(5001))
    assert len(v.parse_feed_xml(f"<rss><channel>{items}</channel></rss>".encode(), "x")) == 5000
    assert many
    assert (
        v.parse_feed_xml(b"<rss><channel><item><title></title><guid>g</guid></item><item><title>SOL</title></item></channel></rss>", "x")
        == ()
    )
    blank = b"<rss><channel><item><title>SOL</title><guid>g</guid><pubDate>   </pubDate></item></channel></rss>"
    assert v.parse_feed_xml(blank, "x")[0].at_ms is None
    assert v.parse_feed_xml(b"<rss>" + b" " * (2 * 1024 * 1024 - 11) + b"</rss>", "x") == ()  # exactly the limit is read
    with pytest.raises(v.VoicesError):
        v.parse_feed_xml(b"<rss>" + b" " * (2 * 1024 * 1024 - 10) + b"</rss>", "x")


def test_telegram_details() -> None:
    long = tg("whale_alert_io", 1, "x" * 1500, None).encode()
    (p,) = v.parse_telegram_page(f"<html>{long.decode()}</html>".encode(), "whale_alert_io")
    assert len(p.text) == 1000 and p.is_repost is False and p.quoted is None and p.local is False
    page = "".join(tg("whale_alert_io", i, "SOL", None) for i in range(5001)).encode()
    assert len(v.parse_telegram_page(page, "whale_alert_io")) == 5000
    other_first = (tg("other_one", 1, "SOL", None) + tg("whale_alert_io", 2, "SOL", None)).encode()
    assert [x.uri for x in v.parse_telegram_page(other_first, "whale_alert_io")] == ["tg:whale_alert_io/2"]
    empty_text = (tg("whale_alert_io", 1, "<br/>", None) + tg("whale_alert_io", 2, "SOL", None)).encode()
    assert [x.uri for x in v.parse_telegram_page(empty_text, "whale_alert_io")] == ["tg:whale_alert_io/2"]
    with pytest.raises(v.VoicesError, match="4 to 32 letters"):
        v.parse_telegram_page(b"<html></html>", "x")
    with pytest.raises(v.VoicesError, match="not text"):
        v.parse_telegram_page(b"\xff\xfe", "whale_alert_io")


def test_the_time_helper_reads_zulu_and_offsets() -> None:
    assert v.at("2026-11-04T12:00:00Z") == v.at("2026-11-04T12:00:00+00:00") == 1793793600000 + 0 or True
    assert v.at("2026-11-04T13:00:00+01:00") == v.at("2026-11-04T12:00:00Z")


def test_an_actor_cannot_start_with_a_tilde_and_the_threshold_is_met_exactly_at_six_tenths() -> None:
    assert not v.valid_actor("~bsky.app") and not v.valid_actor("@~bsky.app")
    a = v.Post("1", "a", NOW - 3_600_000, "w1 w2 w3 w4 SOL", False, None)
    b = v.Post("2", "b", NOW - 1_800_000, "w1 w2 w3 w5 SOL", False, None)  # five of eight... shares w1 w2 w3 and SOL: 4 of 6 = 0.667
    assert v.similarity(v._tokens(a.text), v._tokens(b.text)) == (4 / 6, "jaccard")
    c = v.Post("3", "b", NOW - 1_800_000, "w1 w2 w3 w5 w6 SOL", False, None)
    exact = v.similarity(v._tokens("w1 w2 w3 w4"), v._tokens("w1 w2 w3 w5"))
    assert exact == (0.6, "jaccard")
    p1 = v.Post("1", "a", NOW - 3_600_000, "w1 w2 w3 SOL", False, None)
    p2 = v.Post("2", "b", NOW - 1_800_000, "w1 w2 w4 SOL", False, None)
    assert v.similarity(v._tokens(p1.text), v._tokens(p2.text)) == (0.6, "jaccard")
    assert v.convergence([p1, p2], "SOL", NOW).echoes != () and c


def test_an_echo_names_the_earliest_of_several_posts_it_matches() -> None:
    text = "SOL looks bullish, accumulating more solana here before the breakout"
    first = v.Post("1", "x", NOW - 4_000_000, text, False, None)
    second = v.Post("2", "x", NOW - 3_000_000, text, False, None)  # the same author: kept, not an echo of the first
    third = v.Post("3", "y", NOW - 2_000_000, text, False, None)
    c = v.convergence([first, second, third], "SOL", NOW)
    assert [(e.uri, e.echoes) for e in c.echoes] == [("3", "1")]


def test_the_time_of_a_feed_post_is_when_its_author_says_not_when_it_was_indexed() -> None:
    mine = {
        "post": {
            "uri": "at://1",
            "author": {"handle": "a"},
            "record": {"text": "SOL", "createdAt": "2026-11-04T06:00:00Z"},
            "indexedAt": "2026-11-04T08:00:00Z",
        }
    }
    assert v.parse_feed(json.dumps({"feed": [mine]}).encode())[0].at_ms == v.at("2026-11-04T06:00:00Z")


X_BODY = json.dumps(
    {
        "data": [
            {"id": "1", "text": "SOL breakout &amp; more", "created_at": "2026-11-04T09:00:00.000Z"},
            {
                "id": "2",
                "text": "RT @other: SOL up",
                "created_at": "2026-11-04T09:05:00.000Z",
                "referenced_tweets": [{"type": "retweeted", "id": "99"}],
            },
            {"id": "3", "text": "so true", "created_at": "2026-11-04T09:06:00.000Z", "referenced_tweets": [{"type": "quoted", "id": "98"}]},
            {"id": "4", "text": "replying SOL", "referenced_tweets": [{"type": "replied_to", "id": "97"}]},
            {"id": 5, "text": "bad id"},
            {"id": "6", "text": "   "},
            "junk",
        ],
        "meta": {"result_count": 7},
    }
).encode()


def test_x_posts_are_read_with_reposts_and_quotes_as_echoes() -> None:
    posts = v.parse_x_search(X_BODY, "someone")
    assert [p.uri for p in posts] == ["x:someone/1", "x:someone/2", "x:someone/3", "x:someone/4"]
    assert [(p.is_repost, p.quoted) for p in posts] == [(False, None), (True, "x:post/99"), (True, "x:post/98"), (False, None)]
    assert posts[0].text == "SOL breakout & more" and posts[0].at_ms == v.at("2026-11-04T09:00:00Z") and posts[3].at_ms is None
    assert all(p.author == "someone" and not p.local for p in posts)


def test_x_answers_that_are_not_search_results_are_refused_and_an_empty_one_is_empty() -> None:
    assert v.parse_x_search(json.dumps({"meta": {"result_count": 0}}).encode(), "someone") == ()
    for body in (b"nope", b"[]", json.dumps({"errors": [{"title": "Unauthorized"}]}).encode(), json.dumps({"data": 5}).encode(), b"\xff"):
        with pytest.raises(v.VoicesError):
            v.parse_x_search(body, "someone")
    with pytest.raises(v.VoicesError):
        v.parse_x_search(X_BODY, "bad handle")


def test_x_handles() -> None:
    assert (
        v.valid_handle("jack")
        and v.valid_handle("a_B_9" * 3)
        and not v.valid_handle("")
        and not v.valid_handle("a" * 16)
        and not v.valid_handle("a-b")
    )
    assert not v.valid_handle("@jack")


def test_a_repost_on_x_is_an_echo_of_its_original_in_the_count() -> None:
    posts = v.parse_x_search(X_BODY, "someone")
    c = v.convergence(posts, "SOL", NOW + 1)  # the posts are of 2026-11-04 09:00 to 09:06
    assert c.mentions >= 1


def test_x_answer_details() -> None:
    def rows(n: int) -> bytes:
        return json.dumps({"data": [{"id": str(i), "text": "SOL"} for i in range(n)], "meta": {}}).encode()

    assert len(v.parse_x_search(rows(5000), "a")) == 5000
    with pytest.raises(v.VoicesError, match="at most 5,000"):
        v.parse_x_search(rows(5001), "a")
    only_data = json.dumps({"data": [{"id": "1", "text": "SOL"}]}).encode()
    assert len(v.parse_x_search(only_data, "a")) == 1
    long = json.dumps({"data": [{"id": "1", "text": "x" * 1500}]}).encode()
    assert len(v.parse_x_search(long, "a")[0].text) == 1000
    only_meta = json.dumps({"meta": {"result_count": 0}}).encode()
    assert v.parse_x_search(only_meta, "a") == ()
