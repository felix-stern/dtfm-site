#!/usr/bin/env python3
"""Hermetic static site validator for dtfm-site (stdlib only)."""

from __future__ import annotations

import re
import struct
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_FILES = [
    "index.html",
    "c.html",
    "agent.md",
    "favicon.svg",
    "favicon.ico",
    "README.md",
    "LICENSE",
    "assets/dtfm-logo-dark.jpg",
    "assets/dtfm-logo-light.jpg",
]

# Paths routed by Nginx rather than static files in this repo.
NGINX_ROUTED_PATHS = {"/", "/install", "/install.sh", "/agents.md", "/llms.txt"}


class SiteHTMLParser(HTMLParser):
    """Collect tags, attributes, and script/style contents for static checks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metas: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.srcs: list[tuple[str, str]] = []
        self.hrefs: list[tuple[str, str]] = []
        self.scripts: list[str] = []
        self.styles: list[str] = []
        self._in_script = False
        self._script_buf: list[str] = []
        self._in_style = False
        self._style_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        t = tag.lower()
        if t == "meta":
            self.metas.append(attr_map)
        elif t == "link":
            self.links.append(attr_map)
        if "src" in attr_map:
            self.srcs.append((t, attr_map["src"]))
        if "href" in attr_map:
            self.hrefs.append((t, attr_map["href"]))
        if t == "script":
            self._in_script = True
            self._script_buf = []
        elif t == "style":
            self._in_style = True
            self._style_buf = []

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "script" and self._in_script:
            self._in_script = False
            self.scripts.append("".join(self._script_buf))
        elif t == "style" and self._in_style:
            self._in_style = False
            self.styles.append("".join(self._style_buf))

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._script_buf.append(data)
        elif self._in_style:
            self._style_buf.append(data)


def check_required_files() -> None:
    for rel in REQUIRED_FILES:
        p = ROOT / rel
        if not p.is_file():
            raise AssertionError(f"Missing required file: {rel}")
        if p.stat().st_size == 0:
            raise AssertionError(f"Required file is empty: {rel}")


def check_favicon_ico() -> None:
    ico = (ROOT / "favicon.ico").read_bytes()
    if len(ico) < 6 or ico[:4] != b"\x00\x00\x01\x00":
        raise AssertionError("favicon.ico does not have a valid ICO magic header")
    _, _, count = struct.unpack("<HHH", ico[:6])
    if count < 2:
        raise AssertionError(f"favicon.ico expected >= 2 icon entries, got {count}")


def _resolve_local_asset(rel: str, attr_name: str, url: str) -> None:
    if url.startswith(("http://", "https://", "//")):
        raise AssertionError(f"{rel}: external {attr_name} not allowed: {url}")
    if url.startswith(("data:", "mailto:", "#")):
        return
    if not url.startswith("/"):
        raise AssertionError(f"{rel}: local {attr_name} must start with '/': {url}")
    clean = url.split("#", 1)[0].split("?", 1)[0]
    if clean in NGINX_ROUTED_PATHS or clean.startswith("/c/"):
        return
    target = ROOT / clean.lstrip("/")
    if not target.is_file():
        raise AssertionError(f"{rel}: broken local asset reference {url}")


def check_html_file(rel: str) -> SiteHTMLParser:
    content = (ROOT / rel).read_text(encoding="utf-8")
    parser = SiteHTMLParser()
    parser.feed(content)
    parser.close()

    # Every <script>, <img>, <iframe>, <video>, <audio>, <source> src= must be local and exist
    for tag, src in parser.srcs:
        _resolve_local_asset(rel, f"<{tag} src>", src)

    # Every <link> href= must be local and exist (no external stylesheets/fonts/icons)
    for link in parser.links:
        href = link.get("href", "")
        if href:
            _resolve_local_asset(rel, "<link href>", href)

    # Every <a href="/..."> local path must resolve to an existing file or known Nginx route
    for tag, href in parser.hrefs:
        if tag == "a" and href.startswith("/") and not href.startswith("//"):
            _resolve_local_asset(rel, "<a href>", href)

    # Reject external url(...) or @import inside <style> blocks
    style_text = "\n".join(parser.styles)
    if re.search(r"url\s*\(\s*['\"]?\s*(?:https?:)?//", style_text, re.IGNORECASE):
        raise AssertionError(f"{rel}: external url(...) not allowed in <style>")
    if re.search(r"@import\s+(?:url\s*\(\s*)?['\"]?\s*(?:https?:)?//", style_text, re.IGNORECASE):
        raise AssertionError(f"{rel}: external @import not allowed in <style>")

    return parser


def check_c_html(parser: SiteHTMLParser) -> None:
    has_no_referrer = any(
        m.get("name", "").lower() == "referrer" and m.get("content", "").lower() == "no-referrer"
        for m in parser.metas
    )
    if not has_no_referrer:
        raise AssertionError('c.html must include <meta name="referrer" content="no-referrer">')

    has_noindex = any(
        m.get("name", "").lower() == "robots" and "noindex" in m.get("content", "").lower() and "nofollow" in m.get("content", "").lower()
        for m in parser.metas
    )
    if not has_noindex:
        raise AssertionError('c.html must include <meta name="robots" content="noindex, nofollow">')

    script_text = "\n".join(parser.scripts)
    forbidden_patterns = [
        (r"\binnerHTML\b", "innerHTML sink"),
        (r"\bouterHTML\b", "outerHTML sink"),
        (r"\binsertAdjacentHTML\b", "insertAdjacentHTML sink"),
        (r"\bdocument\.write\b", "document.write sink"),
        (r"\beval\s*\(", "eval() sink"),
        (r"\bfetch\s*\(", "fetch() network API"),
        (r"\bXMLHttpRequest\b", "XMLHttpRequest network API"),
        (r"\bWebSocket\b", "WebSocket network API"),
        (r"\bsendBeacon\b", "navigator.sendBeacon network API"),
        (r"\bEventSource\b", "EventSource network API"),
    ]
    for pattern, desc in forbidden_patterns:
        if re.search(pattern, script_text):
            raise AssertionError(f"c.html script contains forbidden {desc}")


def main() -> int:
    check_required_files()
    check_favicon_ico()
    check_html_file("index.html")
    c_parser = check_html_file("c.html")
    check_c_html(c_parser)
    print("verify_site.py: all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
