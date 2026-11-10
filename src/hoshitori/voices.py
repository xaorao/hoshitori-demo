"""Public voices on a token: what is said, by whom, and how many of them are really several.

Three accounts quoting one thesis are one observation wearing a disguise, and counting them as three is the easiest way for an
agent that reads voices to be confidently wrong. So this module counts voices after echo detection: a repost or a quote is an
echo of what it points at; a post whose words are mostly an earlier post's, from another author inside the window, is an echo of
it; every echo is kept with the post it echoes and the reason, so a demotion can be read and not only asserted.

The ideas and the reading of Bluesky's author feed are adapted from Hanko (``hanko/decision/convergence.py`` and
``hanko/sources/bluesky.py``, MIT; credited in ``NOTICE.md``): the keyless AppView feed with its repost and quote structure,
Jaccard similarity with containment for long posts so a padded copy is still found, and the earliest statement keeping its
independence. What is new here: pure functions over bytes (no network), the stance of a post from a small word list read as a
baseline and not as understanding, posts from a person's own connector (``local``) counted apart and never as independent voices
(their provenance is the person's, not ours: contract section 28, SP-3), and a window and thresholds that are priors stated
once.

Stance here is a word list. It reads "bullish" and "dump", not irony, quotes or conditions; it is labelled so wherever it is
shown, and the evidence that carries it says it has not been shown to help a decision (the plan's gate for specialist sources).
"""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Final

from hoshitori.timeutil import TimeError, parse_rfc3339

WINDOW_H: Final = 24  # how far back a mention counts (a prior)
ECHO_WINDOW_MIN: Final = 360  # a later post within this long of an earlier one can be its echo (a prior)
ECHO_SIMILARITY: Final = 0.6  # the share of words two posts must share to be one voice (a prior)
CONTAINMENT_MIN_TOKENS: Final = (
    8  # below this many content words a post is compared by Jaccard alone: "$BTC up" is inside half the posts about BTC
)
MAX_POSTS: Final = 5000
_ACTOR: Final = re.compile(r"(?:did:plc:[a-z0-9]{24}|[A-Za-z0-9][A-Za-z0-9.-]{1,252})")
_WORD: Final = re.compile(r"[a-z0-9$]+")
_STOP: Final = frozenset(
    (
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
    )
)
UP_WORDS: Final = frozenset(
    (
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
    )
)
DOWN_WORDS: Final = frozenset(
    (
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
    )
)
NAMES: Final = {"BTC": ("bitcoin", "btc"), "ETH": ("ethereum", "ether", "eth"), "SOL": ("solana", "sol")}


class VoicesError(ValueError):
    """A feed or an inbox that is not what its source writes."""


@dataclass(frozen=True)
class Post:
    uri: str  # the post's own address (or the connector's id)
    author: str
    at_ms: int | None  # when it was published, as its author or the provider states; none when it says nothing
    text: str
    is_repost: bool = False  # a repost or a quote: an echo by construction
    quoted: str | None = None  # the uri it points at
    local: bool = False  # from a person's own connector, not fetched by this league


@dataclass(frozen=True)
class Echo:
    uri: str
    author: str
    echoes: str
    reason: str


@dataclass(frozen=True)
class Convergence:
    symbol: str
    mentions: int  # posts in the window that name the token
    authors: int  # distinct authors among them
    independent: int  # voices left after echoes are taken out, local posts excluded
    up: int  # of the independent voices, how many lean up by the word list
    down: int
    neutral: int
    local_mentions: int  # posts from a person's own connector that name the token: shown, never counted as independent
    echoes: tuple[Echo, ...]
    posts: tuple[str, ...]  # the uris of the independent voices' posts, in order


def valid_actor(actor: str) -> bool:
    return _ACTOR.fullmatch(actor.lstrip("@")) is not None


def _ms(stamp: object) -> int | None:
    if not isinstance(stamp, str):
        return None
    try:
        return int(parse_rfc3339(stamp).timestamp() * 1000)
    except (TimeError, ValueError, OverflowError):
        return None


def _embedded(post: dict[str, Any]) -> str | None:
    embed = post.get("embed") or {}
    kind = str(embed.get("$type", ""))
    if kind.startswith("app.bsky.embed.record#"):
        return (embed.get("record") or {}).get("uri")
    if kind.startswith("app.bsky.embed.recordWithMedia"):
        return ((embed.get("record") or {}).get("record") or {}).get("uri")
    return None


def parse_feed(body: bytes) -> tuple[Post, ...]:
    """The posts of one author feed (``app.bsky.feed.getAuthorFeed``): own posts, quotes and reposts, each with its time."""
    try:
        doc = json.loads(body.decode("utf-8"))
        feed = doc["feed"]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError):
        raise VoicesError("the feed is not a Bluesky author feed") from None
    if not isinstance(feed, list) or len(feed) > MAX_POSTS:
        raise VoicesError("the feed is a list of at most 5,000 entries")
    out: list[Post] = []
    for entry in feed:
        if not isinstance(entry, dict):
            continue
        post = entry.get("post") or {}
        record = post.get("record") or {}
        uri, text = post.get("uri"), str(record.get("text") or "").strip()
        author = str((post.get("author") or {}).get("handle") or "")
        if not isinstance(uri, str) or not author:
            continue
        reason = entry.get("reason") or {}
        if str(reason.get("$type", "")).endswith("reasonRepost"):
            by = str((reason.get("by") or {}).get("handle") or "")
            if by:
                out.append(Post(f"{uri}#repost-by-{by}", by, _ms(reason.get("indexedAt") or post.get("indexedAt")), text, True, uri))
            continue
        if not text:
            continue
        quoted = _embedded(post)
        out.append(Post(uri, author, _ms(record.get("createdAt") or post.get("indexedAt")), text, quoted is not None, quoted))
    return tuple(out)


_TAG: Final = re.compile(r"<[^>]{0,2000}>")
MAX_FEED_BYTES: Final = 2 * 1024 * 1024


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _feed_ms(text: str | None) -> int | None:
    if not text or not text.strip():
        return None
    value = text.strip()
    stamp = _ms(value)
    if stamp is not None:
        return stamp
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    return int(moment.timestamp() * 1000) if moment.tzinfo is not None else None


def parse_feed_xml(body: bytes, source: str) -> tuple[Post, ...]:
    """The items of an RSS or Atom news feed as posts by ``source``: the title and the summary, tags removed, with the time the feed gives.

    A feed is an outlet speaking, so several outlets repeating one announcement are echoes of the first, as with accounts. A document with a
    DOCTYPE or an entity is refused unread (the standard parser would expand it)."""
    if len(body) > MAX_FEED_BYTES or b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise VoicesError("the feed is too large or declares a document type or an entity")
    if (
        b"\x00" in body
    ):  # a NUL is not XML text, and between the letters of a tag it is how UTF-16 hides a DOCTYPE from the byte search above
        raise VoicesError("the feed declares an encoding the safety check cannot read")
    try:
        root = ET.fromstring(body)  # noqa: S314  (refused above if it declares anything that can expand)
    except ET.ParseError:
        raise VoicesError("the feed is not XML") from None
    kind = _local(root.tag)
    if kind not in ("rss", "feed", "RDF"):
        raise VoicesError("the document is not an RSS or Atom feed")
    out: list[Post] = []
    for item in root.iter():
        if _local(item.tag) not in ("item", "entry"):
            continue
        fields: dict[str, str] = {}
        for child in item:
            name = _local(child.tag)
            if name in ("title", "description", "summary", "pubDate", "published", "updated", "date", "guid", "id"):
                fields.setdefault(name, "".join(child.itertext()))
            elif name == "link":
                fields.setdefault("link", child.attrib.get("href") or "".join(child.itertext()))
        parts = (" ".join(html.unescape(_TAG.sub(" ", fields.get(k, ""))).split()) for k in ("title", "description", "summary"))
        text = " ".join(part for part in parts if part)
        ident = (fields.get("guid") or fields.get("id") or fields.get("link") or "").strip()
        if not text or not ident:
            continue
        stamp = _feed_ms(fields.get("pubDate") or fields.get("published") or fields.get("updated") or fields.get("date"))
        out.append(Post(f"feed:{source}:{ident}", source, stamp, text[:1000], False, None))
        if len(out) >= MAX_POSTS:
            break
    return tuple(out)


_TG_MESSAGE: Final = re.compile(r'data-post="([A-Za-z0-9_]{4,32}/[0-9]{1,12})"')
_TG_TEXT: Final = re.compile(r'<div class="tgme_widget_message_text[^"]{0,60}"[^>]{0,200}>(.{0,20000}?)</div>', re.DOTALL)
_TG_TIME: Final = re.compile(r'<time[^>]{0,200}datetime="([^"]{10,40})"')
CHANNEL: Final = re.compile(r"[A-Za-z0-9_]{4,32}")


def valid_channel(name: str) -> bool:
    return CHANNEL.fullmatch(name) is not None


def parse_telegram_page(body: bytes, channel: str) -> tuple[Post, ...]:
    """The recent posts of a public Telegram channel's web page (``t.me/s/<channel>``), as posts by the channel, with their times.

    A page is read as text: only message blocks of this channel that carry text count, and a block whose time cannot be read is kept
    undated. A page with no message block at all is refused (a channel that is private, missing or changed its markup says nothing)."""
    if not valid_channel(channel):
        raise VoicesError("a channel is 4 to 32 letters, digits and underscores")
    try:
        page = body.decode("utf-8")
    except UnicodeDecodeError:
        raise VoicesError("the page is not text") from None
    parts = page.split("tgme_widget_message_wrap")[1:]
    if not parts:
        raise VoicesError("the page holds no messages")
    out: list[Post] = []
    for part in parts[:MAX_POSTS]:
        ident = _TG_MESSAGE.search(part)
        body_text = _TG_TEXT.search(part)
        if ident is None or body_text is None or not ident.group(1).lower().startswith(channel.lower() + "/"):
            continue
        text = " ".join(html.unescape(_TAG.sub(" ", body_text.group(1))).split())
        if not text:
            continue
        when = _TG_TIME.search(part)
        out.append(Post(f"tg:{ident.group(1)}", channel, _ms(when.group(1)) if when else None, text[:1000], False, None))
    return tuple(out)


HANDLE: Final = re.compile(r"[A-Za-z0-9_]{1,15}")


def valid_handle(name: str) -> bool:
    """An X handle: 1 to 15 letters, digits and underscores, without the at sign."""
    return HANDLE.fullmatch(name) is not None


def parse_x_search(body: bytes, handle: str) -> tuple[Post, ...]:
    """The posts of one account from X's recent-search answer (``/2/tweets/search/recent`` with ``created_at`` and ``referenced_tweets``).

    A repost or a quote names the post it points at, so it is an echo by construction; a reply is read as an ordinary post of the account.
    An error answer (a body with ``errors`` and no ``data``) and anything that is not that shape is refused; an empty result is no posts."""
    if not valid_handle(handle):
        raise VoicesError("an X handle is 1 to 15 letters, digits and underscores")
    try:
        doc = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise VoicesError("the answer is not JSON") from None
    if not isinstance(doc, dict) or not ("data" in doc or "meta" in doc):
        raise VoicesError("the answer is not an X search result")
    rows = doc.get("data", [])
    if not isinstance(rows, list) or len(rows) > MAX_POSTS:
        raise VoicesError("the posts are a list of at most 5,000")
    out: list[Post] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not isinstance(row.get("text"), str):
            continue
        text = " ".join(html.unescape(row["text"]).split())
        refs = row.get("referenced_tweets") or []
        pointed = next(
            (
                r.get("id")
                for r in refs
                if isinstance(r, dict) and r.get("type") in ("retweeted", "quoted") and isinstance(r.get("id"), str)
            ),
            None,
        )
        if text:
            out.append(
                Post(
                    f"x:{handle}/{row['id']}",
                    handle,
                    _ms(row.get("created_at")),
                    text[:1000],
                    pointed is not None,
                    None if pointed is None else f"x:post/{pointed}",
                )
            )
    return tuple(out)


def parse_inbox(body: bytes) -> tuple[Post, ...]:
    """Posts a person fetched with their own connector and left in a file: ``{"posts": [{"id", "author", "text", "created_at"}]}``. They are
    kept as ``local``: the signature of a connector shows who submitted them, not that the platform published them."""
    try:
        posts = json.loads(body.decode("utf-8"))["posts"]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError):
        raise VoicesError('the inbox is {"posts": [...]}') from None
    if not isinstance(posts, list) or len(posts) > MAX_POSTS:
        raise VoicesError("the inbox holds at most 5,000 posts")
    out = []
    for p in posts:
        if not isinstance(p, dict) or not all(isinstance(p.get(k), str) and p[k] for k in ("id", "author", "text")):
            raise VoicesError("each inbox post has an id, an author and a text")
        out.append(Post(f"inbox:{p['id']}", p["author"], _ms(p.get("created_at")), p["text"].strip(), False, None, True))
    return tuple(out)


def mentions(text: str, symbol: str) -> bool:
    """Whether a post names the token: its cashtag, ticker or name as a whole word."""
    low = text.lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", low) for name in NAMES.get(symbol, (symbol.lower(),)))


def stance(text: str) -> int:
    """1 for a lean up, -1 for a lean down, 0 for neither or both, by the word lists (a baseline, not understanding)."""
    words = _WORD.findall(text.lower())
    up, down = sum(w in UP_WORDS for w in words), sum(w in DOWN_WORDS for w in words)
    return (up > down) - (down > up)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1)


def similarity(a: frozenset[str], b: frozenset[str]) -> tuple[float, str]:
    """The larger of Jaccard and (for long posts) containment: padding a copy with a footer cannot hide it."""
    if not a or not b:
        return 0.0, "jaccard"
    jaccard = len(a & b) / len(a | b)
    if min(len(a), len(b)) < CONTAINMENT_MIN_TOKENS:
        return jaccard, "jaccard"
    contained = len(a & b) / min(len(a), len(b))
    return (contained, "containment") if contained > jaccard else (jaccard, "jaccard")


def convergence(posts: Iterable[Post], symbol: str, now_ms: int, window_h: int = WINDOW_H) -> Convergence:
    """How many independent voices name ``symbol`` in the window, and which way they lean; deterministic: the earliest statement keeps its
    independence, undated posts sort last (they can echo a dated post, never the other way), ties go to the uri."""
    since = now_ms - window_h * 3_600_000
    named = [p for p in posts if mentions(p.text, symbol) and (p.at_ms is None or since <= p.at_ms <= now_ms)]
    local = [p for p in named if p.local]
    ours = sorted({p.uri: p for p in named if not p.local}.values(), key=lambda p: (p.at_ms is None, p.at_ms or 0, p.uri))
    window = ECHO_WINDOW_MIN * 60_000
    kept: list[tuple[Post, frozenset[str]]] = []
    echoes: list[Echo] = []
    for post in ours:
        echoed: str | None = None
        reason = ""
        if post.is_repost:
            match = next((e for e, _ in kept if e.uri == post.quoted), None)
            echoed, reason = (match.uri if match else (post.quoted or "unknown")), "a repost or quote of another post"
        else:
            words = _tokens(post.text)
            for earlier, earlier_words in kept:
                if earlier.author == post.author or (post.at_ms and earlier.at_ms and post.at_ms - earlier.at_ms > window):
                    continue
                score, measure = similarity(words, earlier_words)
                if score >= ECHO_SIMILARITY:
                    echoed = earlier.uri
                    reason = f"near-duplicate of a post by {earlier.author} (similarity {score:.2f}, {measure})"
                    break
        if echoed is not None:
            echoes.append(Echo(post.uri, post.author, echoed, reason))
        else:
            kept.append((post, _tokens(post.text)))
    voices: dict[str, Post] = {}
    for post, _ in kept:
        voices.setdefault(post.author, post)  # one voice per author: the author's earliest independent post
    leans = [stance(p.text) for p in voices.values()]
    return Convergence(
        symbol,
        len(named),
        len({p.author for p in named}),
        len(voices),
        leans.count(1),
        leans.count(-1),
        leans.count(0),
        len(local),
        tuple(echoes),
        tuple(p.uri for p in voices.values()),
    )


def all_symbols(posts: Sequence[Post], symbols: Iterable[str], now_ms: int, window_h: int = WINDOW_H) -> dict[str, Convergence]:
    return {s: convergence(posts, s, now_ms, window_h) for s in symbols}


def at(stamp: str) -> int:
    """An RFC 3339 time as milliseconds, for callers and tests that build posts."""
    return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)
