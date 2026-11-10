"""A tiny DER tree for building malformed variants of a token: parse a structure, change one element, encode it again.

Constructed elements (the constructed bit of the tag) are parsed into children; everything else stays opaque bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Node:
    tag: int
    children: list[Node] | None = None  # constructed
    value: bytes = b""  # primitive
    style: str = "der"  # length encoding when written: der, padded, leading_zero, indefinite
    raw_tag: bytes | None = None  # replaces the tag octet when written (for multi-byte tags)
    extra: list[Node] = field(default_factory=list)

    def content(self) -> bytes:
        if self.children is None:
            return self.value
        return b"".join(child.encode() for child in self.children)

    def encode(self) -> bytes:
        body = self.content()
        tag = self.raw_tag if self.raw_tag is not None else bytes([self.tag])
        if self.style == "indefinite":
            return tag + b"\x80" + body + b"\x00\x00"
        if len(body) < 0x80 and self.style == "der":
            return tag + bytes([len(body)]) + body
        width = max(1, (len(body).bit_length() + 7) // 8)
        raw = len(body).to_bytes(width, "big")
        if self.style == "padded" and len(body) < 0x80:
            return tag + b"\x81" + raw + body
        if self.style == "leading_zero":
            return tag + bytes([0x80 | (width + 1)]) + b"\x00" + raw + body
        return tag + bytes([0x80 | width]) + raw + body


def parse(data: bytes) -> Node:
    node, end = _read(data, 0)
    assert end == len(data), "trailing data"
    return node


def _read(data: bytes, offset: int) -> tuple[Node, int]:
    tag, first = data[offset], data[offset + 1]
    header = 2
    length = first
    if first & 0x80:
        count = first & 0x7F
        length = int.from_bytes(data[offset + 2 : offset + 2 + count], "big")
        header = 2 + count
    start = offset + header
    end = start + length
    if tag & 0x20:
        children: list[Node] = []
        position = start
        while position < end:
            child, position = _read(data, position)
            children.append(child)
        return Node(tag, children=children), end
    return Node(tag, value=data[start:end]), end


def find(node: Node, predicate) -> Node | None:  # type: ignore[no-untyped-def]
    """The first element, depth first, for which ``predicate`` is true."""
    if predicate(node):
        return node
    for child in node.children or []:
        found = find(child, predicate)
        if found is not None:
            return found
    return None
