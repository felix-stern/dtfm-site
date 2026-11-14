#!/usr/bin/env python3
"""Hermetic static site validator for dtfm-site (stdlib only)."""

from __future__ import annotations

import re
import struct
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROOT_RESOLVED = ROOT.resolve()

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
        self.attrs_all: list[tuple[str, dict[str, str]]] = []
        self.scripts: list[str] = []
        self.styles: list[str] = []
        self._in_script = False
        self._script_buf: list[str] = []
        self._in_style = False
        self._style_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        t = tag.lower()
        self.attrs_all.append((t, attr_map))
        if t == "meta":
            self.metas.append(attr_map)
        elif t == "link":
            self.links.append(attr_map)
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
    if len(ico) < 6 + 16 * count:
        raise AssertionError("favicon.ico truncated directory entries")
    for i in range(count):
        entry = ico[6 + 16 * i : 6 + 16 * (i + 1)]
        size, offset = struct.unpack("<II", entry[8:16])
        if size == 0 or offset < 6 + 16 * count or offset + size > len(ico):
            raise AssertionError(f"favicon.ico entry {i} has out-of-bounds offset/size")


def check_favicon_svg() -> None:
    svg_text = (ROOT / "favicon.svg").read_text(encoding="utf-8")
    root = ET.fromstring(svg_text)
    if not root.tag.endswith("svg"):
        raise AssertionError("favicon.svg root element must be <svg>")
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1].lower()
        if tag == "script":
            raise AssertionError("favicon.svg must not contain <script>")
        for attr, val in elem.attrib.items():
            attr_local = attr.rsplit("}", 1)[-1].lower()
            if attr_local in ("href", "src") or attr_local.startswith("on"):
                raise AssertionError(f"favicon.svg disallowed attribute '{attr_local}' on <{tag}>: {val}")
        if elem.text and re.search(r"(?:url\s*\(|@import)", elem.text, re.IGNORECASE):
            if re.search(r"(?:https?:)?//", elem.text, re.IGNORECASE):
                raise AssertionError("favicon.svg must not reference external URLs in <style>")


def _resolve_local_asset(
    rel: str,
    attr_name: str,
    raw_url: str,
    *,
    allow_external: bool = False,
    allow_data_image: bool = False,
) -> None:
    url = raw_url.strip()
    lower = url.lower()
    if lower.startswith(("http://", "https://", "//")):
        if allow_external:
            return
        raise AssertionError(f"{rel}: external {attr_name} not allowed: {raw_url}")
    if lower.startswith("data:"):
        if allow_data_image and lower.startswith("data:image/"):
            return
        raise AssertionError(f"{rel}: disallowed data: URI in {attr_name}: {raw_url}")
    if lower.startswith(("javascript:", "vbscript:")):
        raise AssertionError(f"{rel}: unsafe script URI in {attr_name}: {raw_url}")
    if lower.startswith(("mailto:", "#")):
        return
    if not url.startswith("/"):
        raise AssertionError(f"{rel}: local {attr_name} must start with '/': {raw_url}")
    clean = url.split("#", 1)[0].split("?", 1)[0]
    if clean in NGINX_ROUTED_PATHS or clean.startswith("/c/"):
        return
    target = (ROOT / clean.lstrip("/")).resolve()
    if not target.is_relative_to(ROOT_RESOLVED) or not target.is_file():
        raise AssertionError(f"{rel}: broken or escaping local asset reference {raw_url}")


def check_html_file(rel: str) -> SiteHTMLParser:
    content = (ROOT / rel).read_text(encoding="utf-8")
    parser = SiteHTMLParser()
    parser.feed(content)
    parser.close()

    for tag, attrs in parser.attrs_all:
        # Reject inline event handlers and inline style="..." attributes
        for attr_name in attrs:
            if attr_name.startswith("on"):
                raise AssertionError(f"{rel}: inline event handler '{attr_name}' not allowed on <{tag}>")
            if attr_name == "style":
                raise AssertionError(f"{rel}: inline style attribute not allowed on <{tag}>")

        if tag == "base" and "href" in attrs:
            raise AssertionError(f"{rel}: <base href> not allowed")
        if tag == "meta" and attrs.get("http-equiv", "").strip().lower() == "refresh":
            raise AssertionError(f"{rel}: <meta http-equiv='refresh'> not allowed")

        if "src" in attrs:
            _resolve_local_asset(
                rel,
                f"<{tag} src>",
                attrs["src"],
                allow_data_image=(tag == "img"),
            )
        if "poster" in attrs:
            _resolve_local_asset(rel, f"<{tag} poster>", attrs["poster"])
        if tag == "object" and "data" in attrs:
            _resolve_local_asset(rel, "<object data>", attrs["data"])
        if "srcset" in attrs:
            for item in attrs["srcset"].split(","):
                candidate = item.strip().split()[0] if item.strip() else ""
                if candidate:
                    _resolve_local_asset(rel, f"<{tag} srcset>", candidate, allow_data_image=(tag in {"img", "source"}))
        for act_attr in ("action", "formaction"):
            if act_attr in attrs:
                _resolve_local_asset(rel, f"<{tag} {act_attr}>", attrs[act_attr])
        for href_attr in ("href", "xlink:href"):
            if href_attr in attrs:
                _resolve_local_asset(
                    rel,
                    f"<{tag} {href_attr}>",
                    attrs[href_attr],
                    allow_external=(tag == "a"),
                )

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
        m.get("name", "").lower() == "robots"
        and "noindex" in m.get("content", "").lower()
        and "nofollow" in m.get("content", "").lower()
        for m in parser.metas
    )
    if not has_noindex:
        raise AssertionError('c.html must include <meta name="robots" content="noindex, nofollow">')

    has_csp = any(
        m.get("http-equiv", "").lower() == "content-security-policy" and "default-src 'none'" in m.get("content", "").lower()
        for m in parser.metas
    )
    if not has_csp:
        raise AssertionError("c.html must include a default-src 'none' Content-Security-Policy meta tag")

    script_text = "\n".join(parser.scripts)
    forbidden_patterns = [
        (r"\binnerHTML\b", "innerHTML sink"),
        (r"\bouterHTML\b", "outerHTML sink"),
        (r"\binsertAdjacentHTML\b", "insertAdjacentHTML sink"),
        (r"\bdocument\.writeln?\b", "document.write/writeln sink"),
        (r"\beval\s*\(", "eval() sink"),
        (r"\bFunction\s*\(", "Function() sink"),
        (r"\bimport\s*\(", "dynamic import()"),
        (r"\bnew\s+Image\s*\(", "new Image() network API"),
        (r"\bWorker\s*\(", "Worker()"),
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
    check_favicon_svg()
    check_html_file("index.html")
    c_parser = check_html_file("c.html")
    check_c_html(c_parser)
    print("verify_site.py: all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
