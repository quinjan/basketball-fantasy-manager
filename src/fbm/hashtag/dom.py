"""A tiny HTML DOM on the standard library (`html.parser`), enough for HT's ASP.NET WebForms pages.

Only what the HT parsers and the postback client need: a tree of elements, find/find_all by tag + attributes,
text content. Tolerant of unclosed tags (end tags close back to the nearest matching open element).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_WS = re.compile(r"\s+")


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str] | None = None, parent: Node | None = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[Node | str] = []
        self.parent = parent

    def get(self, name: str, default: str | None = None) -> str | None:
        return self.attrs.get(name, default)

    @property
    def id(self) -> str:
        return self.attrs.get("id") or ""

    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    def iter(self) -> Iterator[Node]:
        stack: list[Node] = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(c for c in reversed(node.children) if isinstance(c, Node))

    def find_all(self, tag: str | None = None, pred: Callable[[Node], bool] | None = None, **attrs: str) -> list[Node]:
        out = []
        for n in self.iter():
            if n is self:
                continue
            if tag and n.tag != tag:
                continue
            if any(n.attrs.get(k.rstrip("_")) != v for k, v in attrs.items()):
                continue
            if pred and not pred(n):
                continue
            out.append(n)
        return out

    def find(self, tag: str | None = None, pred: Callable[[Node], bool] | None = None, **attrs: str) -> Node | None:
        for n in self.iter():
            if n is self:
                continue
            if tag and n.tag != tag:
                continue
            if any(n.attrs.get(k.rstrip("_")) != v for k, v in attrs.items()):
                continue
            if pred and not pred(n):
                continue
            return n
        return None

    def children_tags(self, tag: str | None = None) -> list[Node]:
        return [c for c in self.children if isinstance(c, Node) and (tag is None or c.tag == tag)]

    def raw_text(self) -> str:
        parts: list[str] = []

        def walk(n: Node) -> None:
            for c in n.children:
                if isinstance(c, str):
                    parts.append(c)
                elif c.tag not in ("script", "style"):
                    walk(c)

        walk(self)
        return "".join(parts)

    def text(self) -> str:
        """Whitespace-collapsed text content."""
        return _WS.sub(" ", self.raw_text()).strip()

    def own_text(self) -> str:
        return _WS.sub(" ", "".join(c for c in self.children if isinstance(c, str))).strip()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.tag} {self.attrs}>"


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        parent = self.stack[-1]
        # implicit closes common in hand-written HTML
        if tag in ("option", "li", "p") and parent.tag == tag:
            self.stack.pop()
            parent = self.stack[-1]
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, parent)
        parent.children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        parent = self.stack[-1]
        parent.children.append(Node(tag, {k: (v if v is not None else "") for k, v in attrs}, parent))

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def parse_html(html: str) -> Node:
    b = _Builder()
    b.feed(html)
    b.close()
    return b.root
