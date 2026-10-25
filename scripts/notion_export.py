#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["beautifulsoup4==4.14.3"]
# ///
"""Convert a Notion HTML export into a portable, independently hosted site.

    uv run scripts/notion_export.py EXPORT .notion-preview

The export is read-only. Existing output is replaced only with --replace and
only when every file still matches this converter's previous manifest.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag
from notion_metadata import apply_code_metadata, block_id, read_metadata

VERSION = 1
ASSETS = Path(__file__).with_name("notion_assets")
MANIFEST = ".notion-build.json"
UUID = re.compile(r"[0-9a-f]{32}$", re.I)
SUFFIX = re.compile(r" [0-9a-f]{32}$", re.I)
IGNORE = {".DS_Store", "__MACOSX", ".git"}
BLOCK_TAGS = {"p", "div", "ul", "ol", "figure", "blockquote", "details", "h1", "h2", "h3", "table", "hr", "pre"}


def wrap_inline_content(element, soup, class_name):
    """Separate a Notion block's title from its child blocks without editing text."""
    wrapper = None
    for child in list(element.contents):
        if isinstance(child, Tag) and child.name in BLOCK_TAGS:
            wrapper = None
        else:
            if wrapper is None:
                wrapper = soup.new_tag("div", attrs={"class": class_name})
                child.insert_before(wrapper)
            wrapper.append(child.extract())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def url_path(path: str | Path) -> str:
    return quote(str(path).replace(os.sep, "/"), safe="/-._~")


def relative_url(source: Path, target: Path) -> str:
    return url_path(os.path.relpath(target, source.parent))


def key(value: str) -> str:
    return value.replace("-", "").lower()


def fragment_id(value: str, ids: set[str]) -> str:
    """Notion links use compact UUIDs; export element IDs use hyphens."""
    if value in ids:
        return value
    matches = [item for item in ids if key(item) == key(value)]
    return matches[0] if len(matches) == 1 else value


def svg(name: str, cls: str = "") -> str:
    paths = {
        "page": '<path d="M5 2.75h6.5L16 7.25V17a1.25 1.25 0 0 1-1.25 1.25h-9.5A1.25 1.25 0 0 1 4 17V4a1.25 1.25 0 0 1 1-1.25Z"/><path d="M11 3v4.5h4.5M7 11h6M7 14h6"/>',
        "search": '<circle cx="8.6" cy="8.6" r="6.1"/><path d="m13.1 13.1 4.5 4.5"/>',
        "share": '<path d="M6 7H4.5A1.5 1.5 0 0 0 3 8.5v8A1.5 1.5 0 0 0 4.5 18h11a1.5 1.5 0 0 0 1.5-1.5v-8A1.5 1.5 0 0 0 15.5 7H14M10 12V2m-3.5 3.5L10 2l3.5 3.5"/>',
        "more": '<circle cx="4" cy="10" r=".8"/><circle cx="10" cy="10" r=".8"/><circle cx="16" cy="10" r=".8"/>',
    }
    return f'<svg class="{cls}" aria-hidden="true" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">{paths[name]}</svg>'


class Page:
    def __init__(self, source: Path, relative: Path):
        self.source, self.relative = source, relative
        self.soup = BeautifulSoup(source.read_text(encoding="utf-8-sig"), "html.parser")
        self.article = self.soup.select_one("article.page")
        if not self.article or not self.article.select_one(".page-title"):
            raise ValueError(f"Not a Notion HTML page: {relative}")
        self.id = self.article.get("id", "")
        self.title = self.article.select_one(".page-title").get_text()
        self.ids = {e["id"] for e in self.article.select("[id]")} | {self.id}
        self.icon = copy.copy(self.article.select_one("header .page-header-icon"))
        self.route = relative
        self.parent: Page | None = None

    def icon_markup(self, default: bool = False) -> str:
        if self.icon:
            icon = copy.copy(self.icon)
            icon.attrs.pop("style", None)
            icon.attrs.pop("id", None)
            icon["class"] = ["page-link-icon"]
            icon["aria-hidden"] = "true"
            return str(icon)
        return svg("page", "page-link-icon") if default else ""


class Builder:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.source = Path(args.source).expanduser().resolve()
        output = Path(args.output).expanduser()
        if output.is_symlink():
            raise ValueError("Output must not be a symlink. Choose a real directory.")
        self.output = output.resolve()
        self.warnings: list[dict] = []
        self.metadata = read_metadata(args.metadata) if args.metadata else None
        self.metadata_count = self.annotation_count = self.image_metadata_count = 0
        self.layout = json.loads(Path(args.layout).read_text()) if args.layout else {}
        if not isinstance(self.layout, dict):
            raise ValueError("Layout must be a JSON object indexed by page ID.")
        self.layout = self.layout.get("pages", self.layout)
        if not isinstance(self.layout, dict) or any(not isinstance(v, dict) or not isinstance(v.get("format", v), dict) for v in self.layout.values()):
            raise ValueError("Layout page entries must be JSON objects with page-format properties.")
        self.pages: list[Page] = []
        self.routes: dict[Path, Path] = {}
        self.by_id: dict[str, Page] = {}
        self.by_path: dict[Path, Page] = {}

    def icon_markup(self, owner: Page, receiver: Page, default=False) -> str:
        """Icons belong to their source page, even when shown in a breadcrumb."""
        icon = BeautifulSoup(owner.icon_markup(default), "html.parser")
        for element in icon.select("[src], [href]"):
            for attr in ("src", "href"):
                if attr not in element.attrs:
                    continue
                value = self.rewrite(element[attr], owner)
                parsed = urlsplit(value)
                if not parsed.scheme and not parsed.netloc and parsed.path:
                    path = Path(os.path.normpath(owner.route.parent / unquote(parsed.path)))
                    value = urlunsplit(("", "", relative_url(receiver.route, path), parsed.query, parsed.fragment))
                element[attr] = value
        return str(icon)

    def inventory(self):
        if not self.source.is_dir():
            raise ValueError("SOURCE must be an extracted Notion HTML export directory.")
        if self.output == self.source or self.output in self.source.parents or self.source in self.output.parents:
            raise ValueError("Output must be separate from the export (neither inside nor above it).")
        files = sorted(p for p in self.source.rglob("*") if p.is_file()
                       and not any(part in IGNORE or part.startswith(".") for part in p.relative_to(self.source).parts))
        for file in files:
            if not file.resolve().is_relative_to(self.source):
                raise ValueError(f"Export contains a link outside its directory: {file}")
        self.pages = [Page(p, p.relative_to(self.source)) for p in files if p.suffix.lower() == ".html"]
        if not self.pages:
            raise ValueError("No HTML pages found.")
        candidates = [p for p in self.pages if p.relative.parent == Path(".")]
        if self.args.root:
            candidates = [p for p in self.pages if p.relative.as_posix() == self.args.root or key(p.id) == key(self.args.root)]
        if len(candidates) != 1:
            raise ValueError("Cannot identify one homepage. Use --root with its relative HTML filename or page ID.")
        self.root = candidates[0]
        root_folder = SUFFIX.sub("", self.root.relative.stem)
        self.files = files
        for file in files:
            rel = file.relative_to(self.source)
            if rel == self.root.relative:
                route = Path("index.html")
            elif rel.parts[0] == root_folder:
                route = Path("index", *rel.parts[1:])
            else:
                route = rel
            if route in self.routes.values():
                raise ValueError(f"Two export files map to the same URL: {route}")
            if route.parts[0] == "assets" or route.name in {MANIFEST, "build-report.json", ".nojekyll", "CNAME", "sitemap.xml", "robots.txt"}:
                raise ValueError(f"Export uses a reserved output path: {route}")
            self.routes[rel] = route
        for page in self.pages:
            page.route = self.routes[page.relative]
            self.by_path[page.source] = page
            if key(page.id) in self.by_id:
                raise ValueError(f"Duplicate page ID: {page.id}")
            self.by_id[key(page.id)] = page
        containers = {}
        for page in self.pages:
            for name in (SUFFIX.sub("", page.source.stem), page.source.stem):
                containers[page.source.parent / name] = page
        for page in self.pages:
            if page is self.root:
                continue
            page.parent = next((containers[d] for d in page.source.parents if d in containers and containers[d] is not page), self.root)

    def rewrite(self, value: str, page: Page) -> str:
        parts = urlsplit(value)
        target = None
        if parts.scheme in {"http", "https"} or parts.netloc:
            host = (parts.hostname or "").lower()
            if host.endswith(".notion.site") or host in {"notion.so", "www.notion.so", "notion.com", "www.notion.com", "app.notion.com"}:
                match = re.search(r"([0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?:/|$)", parts.path, re.I)
                if match:
                    target = self.by_id.get(key(match.group(1)))
            if target is None:
                return value
            frag = fragment_id(unquote(parts.fragment), target.ids)
            return relative_url(page.route, target.route) + ("#" + quote(frag) if frag else "")
        if parts.scheme or not value or value.startswith("//"):
            return value
        if not parts.path:
            frag = fragment_id(unquote(parts.fragment), page.ids)
            return urlunsplit(("", "", "", parts.query, quote(frag)))
        candidate = (page.source.parent / unquote(parts.path)).resolve()
        if not candidate.is_relative_to(self.source):
            raise ValueError(f"Local reference leaves the export: {page.relative}: {value}")
        rel = candidate.relative_to(self.source)
        if rel not in self.routes:
            raise ValueError(f"Missing local reference: {page.relative}: {value}")
        target = self.by_path.get(candidate)
        frag = fragment_id(unquote(parts.fragment), target.ids) if target else unquote(parts.fragment)
        return urlunsplit(("", "", relative_url(page.route, self.routes[rel]), parts.query, quote(frag)))

    def enhance(self, page: Page):
        article = page.article
        for element in article.select("script, link[rel=stylesheet], style"):
            element.decompose()
        for element in article.select("[href], [src], [poster]"):
            for attr in ("href", "src", "poster"):
                if attr in element.attrs:
                    value = element[attr]
                    if value in {"https://app.notion.com/icons/font_gray.svg", "https://app.notion.com/icons/description_gray.svg"}:
                        element[attr] = relative_url(page.route, Path("assets/notion/vendor", value.rsplit("/", 1)[-1]))
                    else:
                        element[attr] = self.rewrite(value, page)
        for p in article.select("p"):
            if "page-description" in p.get("class", []) and not p.get_text().strip():
                p.decompose()
            elif not p.get_text().strip() and not p.find(["img", "svg", "math"]):
                p["class"] = p.get("class", []) + ["notion-blank"]
        for figure in article.select("figure.link-to-page"):
            anchor = figure.find("a")
            if not anchor:
                continue
            target = self.by_id.get(key(anchor.get("data-notion-page-id", "")))
            label = page.soup.new_tag("span", attrs={"class": "page-link-label"})
            for child in list(anchor.contents):
                label.append(child.extract())
            # Export may already contain the target's emoji in the link label.
            existing = label.select_one(".icon, .page-header-icon")
            if existing:
                existing.extract()
                existing["class"] = [c for c in existing.get("class", []) if c != "page-header-icon"] + ["page-link-icon"]
                existing["aria-hidden"] = "true"
                anchor.append(existing)
            else:
                anchor.append(BeautifulSoup(self.icon_markup(target, page, True) if target else svg("page", "page-link-icon"), "html.parser"))
            anchor.append(label)
        for columns in article.select(".column-list"):
            for column in columns.find_all(class_="column", recursive=False):
                ratio = column.get("data-notion-column-ratio", "")
                match = re.search(r"width\s*:\s*([\d.]+)%", column.get("style", ""))
                try:
                    ratio = float(ratio or (match.group(1) if match else 1))
                    if not 0 < ratio < 10000:
                        raise ValueError()
                except ValueError:
                    ratio = 1
                column["style"] = f"--column-ratio:{ratio:g}"
        for eq in article.select("[data-notion-inline-equation]"):
            eq["role"] = "math"
            eq["aria-label"] = eq["data-notion-inline-equation"]
            eq.attrs.pop("aria-hidden", None)
        for pre in article.select("pre.code"):
            pre["tabindex"] = "0"
            code = pre.find("code")
            if code:
                # Exported wrapping is inaccurate; metadata restores the public
                # setting. Inline styles must not override either CSS mode.
                styles = [s for s in code.get("style", "").split(";") if s.strip() and s.split(":", 1)[0].strip() not in {"white-space", "word-break", "overflow-wrap"}]
                if styles:
                    code["style"] = ";".join(styles)
                else:
                    code.attrs.pop("style", None)
                code["class"] = code.get("class", []) + ["keep-markup", "drop-tokens"]
                lang = pre.get("data-notion-code-syntax", "")
                if lang and not any(c.startswith("language-") for c in code.get("class", [])):
                    code["class"] = code.get("class", []) + ["language-" + lang.lower()]
                if self.metadata is not None:
                    entry = self.metadata["code"].get(block_id(pre))
                    if entry is None:
                        raise ValueError(f"Code block missing from metadata: {block_id(pre)}. Refresh the snapshot for this export.")
                    self.annotation_count += apply_code_metadata(pre, entry, page.soup)
                    self.metadata_count += 1
        for quote_block in article.select("blockquote"):
            wrap_inline_content(quote_block, page.soup, "quote-title")
        for li in article.select("li"):
            wrap_inline_content(li, page.soup, "list-title")
            marker = re.search(r"list-style-type\s*:\s*(disc|circle|square)", li.get("style", ""))
            if marker:
                li["data-list-marker"] = marker.group(1)
        for item in article.select(".page-body ul,.page-body ol,.page-body details"):
            if item.name in {"ul", "ol"}:
                item.attrs.setdefault("role", "list")
            if item.name == "ol":
                # HTML's type attribute is case-insensitive in CSS selectors;
                # a data attribute distinguishes a/A and i/I for our markers.
                item["data-list-type"] = item.get("type", "1")
            for direction, neighbor in (("before", item.find_previous_sibling()), ("after", item.find_next_sibling())):
                if neighbor is not None and neighbor.name in {"ul", "ol", "details"}:
                    item["class"] = item.get("class", []) + [f"notion-group-{direction}"]
        for callout in article.select(".callout"):
            content = callout.find_all("div", recursive=False)
            if len(content) >= 2:
                wrap_inline_content(content[-1], page.soup, "callout-title")
        for figure in article.select("figure.image"):
            image = figure.find("img")
            if image:
                layout = self.metadata.get("images", {}).get(figure.get("id")) if self.metadata else None
                match = re.search(r"(?:^|;)\s*width\s*:\s*([\d.]+)px", image.get("style", ""))
                if match:
                    width = f'{match.group(1)}px'
                    if layout:
                        # src has already been rebased, so map it through the output route.
                        route = Path(os.path.normpath(page.route.parent / unquote(urlsplit(image['src']).path)))
                        original = next((rel for rel, dest in self.routes.items() if dest == route), None)
                        if original is None or digest(self.source / original) != layout['sha256'] or abs(float(match.group(1)) - layout['width']) > .01:
                            raise ValueError(f"Metadata does not match exported image {figure.get('id')}. Refresh the snapshot.")
                        width = '100%' if layout['page_width'] else f'{layout["width"]:g}px'
                        figure['data-alignment'] = layout['alignment']
                        self.image_metadata_count += 1
                    figure["style"] = figure.get("style", "") + f';--image-block-width:{width}'
                    image["style"] = re.sub(r"(?:^|;)\s*width\s*:[^;]*", "", image.get("style", ""))
        for image in article.select("img"):
            if "alt" not in image.attrs:
                caption = image.find_parent("figure")
                caption = caption.find("figcaption") if caption else None
                image["alt"] = caption.get_text(" ", strip=True) if caption else ""
            image["decoding"] = "async"
            if "page-cover-image" not in image.get("class", []) and not image.find_parent("header"):
                image["loading"] = "lazy"
        for source in article.select(".source"):
            self.warnings.append({"page": page.route.as_posix(), "kind": "export-embed-fallback", "text": source.get_text(" ", strip=True)})
        fmt = self.layout.get(page.id, self.layout.get(key(page.id), {}))
        fmt = fmt.get("format", fmt)
        if fmt.get("page_full_width"):
            article["class"].append("full-width")
        if fmt.get("page_small_text"):
            article["class"].append("small-text")
        if article.select_one(".page-cover-image"):
            article["class"].append("has-cover")
        if article.select_one(".page-header-icon"):
            article["class"].append("has-icon")

    def chrome(self, page: Page) -> str:
        parents, seen, parent = [], set(), page
        while parent is not None:
            if parent.id in seen:
                raise ValueError(f"Cyclic page hierarchy at {page.title}")
            seen.add(parent.id)
            parents.append(parent)
            parent = parent.parent
        crumbs = []
        for ancestor in reversed(parents):
            label = self.icon_markup(ancestor, page) + '<span class="crumb-label">' + html.escape(ancestor.title) + "</span>"
            if ancestor is page:
                crumbs.append(f'<span class="crumb" aria-current="page">{label}</span>')
            else:
                crumbs.append(f'<a class="crumb" href="{relative_url(page.route, ancestor.route)}">{label}</a>')
        original = ""
        if self.args.notion_site:
            original = f'<a href="{html.escape(self.args.notion_site.rstrip("/") + "/" + key(page.id), quote=True)}">Open in Notion ↗</a>'
        outline, levels = [], []
        for heading in page.article.select(".page-body h1[id], .page-body h2[id], .page-body h3[id]"):
            level = int(heading.name[1])
            while levels and levels[-1] >= level:
                levels.pop()
            depth = len(levels)
            levels.append(level)
            label = html.escape(heading.get_text(" ", strip=True))
            outline.append(f'<a href="#{heading["id"]}" class="outline-entry level-{level}" style="--depth:{depth}" aria-label="{label}"><span>{label}</span></a>')
        outline_markup = f'<nav class="page-outline" aria-label="On this page">{"".join(outline)}</nav>' if len(outline) > 1 else ""
        return f'''<a class="skip-link" href="#main">Skip to content</a>
<header class="site-topbar"><nav class="breadcrumbs" aria-label="Breadcrumb">{'<span class="crumb-separator" aria-hidden="true">/</span>'.join(crumbs)}</nav>
<div class="site-actions"><button type="button" data-action="search" aria-label="Search this site" title="Search">{svg('search')}</button><button type="button" data-action="share" aria-label="Copy page link" title="Copy page link">{svg('share')}</button><button type="button" data-action="menu" aria-label="More options" aria-expanded="false" aria-controls="site-menu">{svg('more')}</button><a class="notion-cta" href="https://www.notion.com/">Get Notion free</a></div></header>
<div id="site-menu" class="site-menu" hidden>{original}<button type="button" data-theme-choice="light">Light appearance</button><button type="button" data-theme-choice="dark">Dark appearance</button><button type="button" data-theme-choice="auto">Use system appearance</button><button type="button" data-action="print">Print page</button></div>
{outline_markup}
<dialog id="site-search" aria-labelledby="search-title"><div class="search-heading"><h2 id="search-title">Search</h2><button type="button" data-action="close-search" aria-label="Close search">×</button></div><input id="search-input" type="search" placeholder="Search pages…" aria-label="Search pages" autocomplete="off"><ul id="search-results"></ul><p id="search-status" role="status"></p></dialog><div class="toast" id="site-status" role="status"></div>'''

    def render(self, page: Page, stage: Path):
        base_styles = [style.get_text() for style in page.soup.head.find_all("style") if "@import" not in style.get_text()]
        css = "\n".join(base_styles)
        css_name = "export-" + hashlib.sha256(css.encode()).hexdigest()[:12] + ".css"
        (stage / "assets/notion" / css_name).write_text(css, encoding="utf-8")
        self.enhance(page)
        chrome = self.chrome(page)
        body = page.article.select_one(".page-body")
        description = body.get_text(" ", strip=True)[:180] if body else ""
        href = lambda path: relative_url(page.route, Path("assets/notion", path))
        canonical = ""
        if self.args.site_url:
            route = "" if page is self.root else url_path(page.route)
            canonical_url = self.args.site_url.rstrip("/") + "/" + route
            canonical = f'<link rel="canonical" href="{html.escape(canonical_url, quote=True)}"><meta property="og:url" content="{html.escape(canonical_url, quote=True)}">'
        math = f'<link rel="stylesheet" href="{href("vendor/katex/katex.min.css")}">' if page.article.select_one(".katex") else ""
        icons = []
        for original_icon in page.soup.head.select('link[rel~="icon"]'):
            icon = copy.copy(original_icon)
            if icon.get("href"):
                icon["href"] = self.rewrite(icon["href"], page)
            icons.append(str(icon))
        root_url = relative_url(page.route, Path("index.html"))
        doc = f'''<!doctype html><html lang="en" data-theme="{self.args.theme}" data-resolved-theme="{'light' if self.args.theme == 'light' else 'dark'}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(page.title)}</title><meta name="description" content="{html.escape(description, quote=True)}"><meta property="og:title" content="{html.escape(page.title, quote=True)}"><meta property="og:type" content="article">{canonical}{''.join(icons)}<script src="{href('theme.js')}"></script><link rel="stylesheet" href="{href(css_name)}">{math}<link rel="stylesheet" href="{href('notion.css')}"><script defer src="{href('vendor/prism.js')}"></script><script defer src="{href('vendor/prism-keep-markup.js')}"></script><script defer src="{href('search-index.js')}"></script><script defer src="{href('notion.js')}"></script></head><body data-root="{root_url}">{chrome}<main id="main" tabindex="-1">{page.article}</main></body></html>'''
        destination = stage / page.route
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(doc, encoding="utf-8")

    def check_output(self):
        if not self.output.exists():
            return
        if not self.output.is_dir():
            raise ValueError("Output exists and is not a directory.")
        if not list(self.output.iterdir()):
            return
        marker = self.output / MANIFEST
        if not self.args.replace or not marker.is_file():
            raise ValueError("Output is not empty. Choose a new directory; --replace is only for this converter's generated output.")
        manifest = json.loads(marker.read_text())
        if manifest.get("generator") != "notion_export" or manifest.get("version") != VERSION:
            raise ValueError("Output has an unrecognized build manifest.")
        recorded = manifest["files"]
        if any(p.is_symlink() for p in self.output.rglob("*")):
            raise ValueError("Output contains a symlink. Preserve those changes and build into a new directory.")
        actual = {p.relative_to(self.output).as_posix() for p in self.output.rglob("*") if p.is_file()} - {MANIFEST}
        expected_dirs = {parent.as_posix() for name in recorded for parent in Path(name).parents if parent != Path(".")}
        actual_dirs = {p.relative_to(self.output).as_posix() for p in self.output.rglob("*") if p.is_dir()}
        if actual_dirs != expected_dirs:
            raise ValueError("Output contains added or removed directories. Build into a new directory.")
        if actual != set(recorded):
            raise ValueError("Output contains added or removed files. Preserve those changes and build into a new directory.")
        for name, expected in recorded.items():
            p = self.output / name
            if not p.resolve().is_relative_to(self.output) or p.is_symlink() or digest(p) != expected:
                raise ValueError(f"Generated output was edited; refusing to overwrite: {name}")

    def build(self):
        self.inventory()
        self.check_output()
        self.output.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".notion-build-", dir=self.output.parent))
        try:
            shutil.copytree(ASSETS, stage / "assets/notion")
            for source in self.files:
                if source.suffix.lower() != ".html":
                    dest = stage / self.routes[source.relative_to(self.source)]
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, dest)
            index = []
            for page in self.pages:
                searchable = copy.copy(page.article)
                for el in searchable.select("pre, .katex-html, .katex-mathml"):
                    el.decompose()
                index.append({"title": page.title, "url": url_path(page.route), "text": searchable.get_text(" ", strip=True)})
                self.render(page, stage)
            (stage / "assets/notion/search-index.js").write_text("window.NOTION_SEARCH_INDEX=" + json.dumps(index, ensure_ascii=False).replace("</", "<\\/") + ";\n", encoding="utf-8")
            (stage / ".nojekyll").touch()
            if self.args.domain:
                (stage / "CNAME").write_text(self.args.domain + "\n")
            if self.args.site_url:
                urls = [self.args.site_url.rstrip("/") + "/" + ("" if p is self.root else url_path(p.route)) for p in self.pages]
                (stage / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join('<url><loc>' + html.escape(u) + '</loc></url>' for u in urls) + '</urlset>')
            checked = verify_site(stage)
            report = {"pages": len(self.pages), "export_assets": len(self.files) - len(self.pages), "checked_local_references": checked, "theme": self.args.theme, "code_metadata": {"blocks": self.metadata_count, "annotation_ranges": self.annotation_count}, "image_metadata": self.image_metadata_count, "warnings": self.warnings}
            (stage / "build-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            files = {p.relative_to(stage).as_posix(): digest(p) for p in sorted(stage.rglob("*")) if p.is_file()}
            (stage / MANIFEST).write_text(json.dumps({"generator": "notion_export", "version": VERSION, "files": files}, indent=2) + "\n")
            # Validate again immediately before replacing anything; failed builds
            # never touch the previous output. Only manifest-verified files go away.
            self.check_output()
            backup = None
            if self.output.exists():
                backup = stage.with_name(stage.name + "-previous")
                self.output.rename(backup)
            try:
                stage.rename(self.output)
            except BaseException:
                if backup:
                    backup.rename(self.output)
                raise
            if backup:
                shutil.rmtree(backup)
            return report
        finally:
            if stage.exists():
                shutil.rmtree(stage)


def verify_site(directory: Path) -> int:
    """Check every local HTML asset/link and every referenced page fragment."""
    directory = directory.resolve()
    docs = {p: BeautifulSoup(p.read_text(encoding="utf-8"), "html.parser") for p in directory.rglob("*.html")}
    ids = {p: {e["id"] for e in doc.select("[id]")} for p, doc in docs.items()}
    errors, checked = [], 0
    for path, doc in docs.items():
        for e in doc.select("[href], [src], [poster]"):
            for attr in ("href", "src", "poster"):
                if attr not in e.attrs:
                    continue
                value = e[attr]
                u = urlsplit(value)
                if u.scheme or u.netloc or not value:
                    continue
                target = (path.parent / unquote(u.path)).resolve() if u.path else path
                checked += 1
                if not target.is_relative_to(directory) or not target.is_file():
                    errors.append(f"{path.relative_to(directory)}: missing {value}")
                elif u.fragment and target in ids and unquote(u.fragment) not in ids[target]:
                    errors.append(f"{path.relative_to(directory)}: missing fragment {value}")
    for css in directory.rglob("*.css"):
        for value in re.findall(r"url\([\"']?([^\)\"']+)", css.read_text()):
            if urlsplit(value).scheme or value.startswith("#"):
                continue
            target = (css.parent / unquote(urlsplit(value).path)).resolve()
            checked += 1
            if not target.is_relative_to(directory) or not target.is_file():
                errors.append(f"{css.relative_to(directory)}: missing {value}")
    if errors:
        raise ValueError("Output validation failed:\n" + "\n".join(errors[:30]))
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="Extracted Notion HTML export directory")
    parser.add_argument("output", help="New directory for the static site")
    parser.add_argument("--replace", action="store_true", help="Replace unmodified output previously made by this converter")
    parser.add_argument("--root", help="Homepage relative HTML path or Notion page ID (when ambiguous)")
    parser.add_argument("--theme", choices=["dark", "light", "auto"], default="dark")
    parser.add_argument("--layout", help="Optional JSON page-format overrides, indexed by page ID")
    parser.add_argument("--metadata", help="Public formatting snapshot from notion_metadata.py (code annotations, wrapping, image layouts)")
    parser.add_argument("--site-url", help="Public base URL for canonical links and sitemap")
    parser.add_argument("--notion-site", help="Original public Notion site, for Open in Notion links")
    parser.add_argument("--domain", help="Write this custom domain to CNAME for GitHub Pages")
    args = parser.parse_args()
    for attr in ("site_url", "notion_site"):
        value = getattr(args, attr)
        if value and (urlsplit(value).scheme not in {"http", "https"} or not urlsplit(value).netloc or urlsplit(value).query or urlsplit(value).fragment):
            parser.error(f"--{attr.replace('_', '-')} must be an absolute HTTP(S) URL without a query or fragment")
    if args.domain and not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", args.domain):
        parser.error("--domain must be a hostname")
    try:
        report = Builder(args).build()
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(f"Built {report['pages']} pages and {report['export_assets']} export assets in {Path(args.output).expanduser().resolve()}")
    print(f"Verified {report['checked_local_references']} local references. Export embed fallbacks: {len(report['warnings'])}.")
    if report['code_metadata']['blocks']:
        print(f"Restored code wrapping for {report['code_metadata']['blocks']} blocks and {report['code_metadata']['annotation_ranges']} annotation ranges.")
    if report['image_metadata']:
        print(f"Restored {report['image_metadata']} image layouts.")
    if not args.metadata:
        print("Export-only mode: use --metadata to restore formatting omitted by Notion's HTML export.")


if __name__ == "__main__":
    main()
