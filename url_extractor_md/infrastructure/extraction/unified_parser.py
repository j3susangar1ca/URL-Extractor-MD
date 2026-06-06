"""Pickle-safe standalone parser for ProcessPoolExecutor.

This module contains a single top-level function, parse_html_unified,
that MUST remain free of closures, bound methods, and self references
so it can be pickled and sent to a subprocess worker.

Two-phase approach:
    Phase 1 — Selectolax fast metadata extraction (title, meta tags,
              links, headings).  Falls back to BeautifulSoup if
              selectolax is unavailable.
    Phase 2 — Content cleaning & extraction (selectolax with BS4
              fallback), then html2text conversion.

All constants are defined inline for pickle-safety — do NOT import
them from content_extractor or any other module that carries
non-pickleable state.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

# ────────────────────────────────────────────────────────
#  Optional dependency gates
# ────────────────────────────────────────────────────────
try:
    from selectolax.parser import HTMLParser as SelectolaxParser

    _HAS_SELECTOLAX = True
except ImportError:
    SelectolaxParser = None  # type: ignore[assignment,misc]
    _HAS_SELECTOLAX = False

try:
    from bs4 import BeautifulSoup

    _HAS_BS4 = True
except ImportError:
    BeautifulSoup = None  # type: ignore[assignment,misc]
    _HAS_BS4 = False

try:
    import html2text as _h2t

    _HAS_HTML2TEXT = True
except ImportError:
    _h2t = None  # type: ignore[assignment]
    _HAS_HTML2TEXT = False

# ────────────────────────────────────────────────────────
#  Inline constants (pickle-safe — no module-level mutable state)
# ────────────────────────────────────────────────────────

STRIP_TAGS: list[str] = [
    "nav", "footer", "aside", "header", "script", "style", "noscript",
    "iframe", "svg", "form", "button", "input", "select", "textarea",
    "figcaption", "figure", "template",
]

NOISE_SELECTORS: list[str] = [
    "[class*='cookie']", "[class*='consent']", "[class*='gdpr']",
    "[class*='banner']", "[id*='cookie']", "[id*='consent']",
    "[id*='gdpr']", "[id*='banner']",
    "[class*='ad-']", "[class*='ads-']", "[class*='advert']",
    "[id*='ad-']", "[id*='ads-']", "[id*='advert']",
    "ins.adsbygoogle",
    "[class*='sidebar']", "[class*='side-bar']", "[id*='sidebar']",
    "[class*='related']", "[class*='recommend']",
    "[class*='popular']", "[class*='trending']",
    "[class*='comment']", "[id*='comment']",
    "#comments", ".comments",
    "[class*='share']", "[class*='social']",
    "[class*='newsletter']", "[class*='subscribe']",
    "[class*='popup']", "[class*='modal']",
    "[class*='overlay']", "[class*='interstitial']",
    "[class*='footer']", "[class*='navigation']",
    "[class*='breadcrumb']", "[class*='pagination']",
]

CONTENT_SELECTORS: list[str] = [
    "article", "[role='main']", "main",
    ".post-content", ".article-content", ".entry-content",
    ".content", "#content",
    ".post-body", ".article-body", ".story-body",
    ".page-content", ".main-content", "#main-content",
    ".post-entry", ".blog-post", ".text-content",
    ".article__body", ".post__content",
]

# ────────────────────────────────────────────────────────
#  Regex helpers
# ────────────────────────────────────────────────────────
_RE_HTML_TAG = re.compile(r"<[^>]+>")
_RE_MULTI_NEWLINE = re.compile(r"\n{3,}")
_RE_MULTIPLE_SPACES = re.compile(r" {2,}")
_RE_HTML_ENTITY = re.compile(r"&#?\w+;")


# ────────────────────────────────────────────────────────
#  Phase 1 — Fast metadata extraction
# ────────────────────────────────────────────────────────


def _extract_metadata_selectolax(
    html: str, source_url: str
) -> dict[str, object]:
    """Extract metadata using selectolax for speed.

    Args:
        html: Raw HTML string.
        source_url: Source URL for resolving relative links.

    Returns:
        Dictionary with title, meta tags, links, and headings.
    """
    if not _HAS_SELECTOLAX or SelectolaxParser is None:
        return {}

    tree = SelectolaxParser(html)
    result: dict[str, object] = {}

    # Title
    title_node = tree.css_first("title")
    result["title"] = title_node.text(deep=True, separator=" ", strip=True) if title_node else ""

    # Meta tags — collect name/content and property/content pairs
    meta_dict: dict[str, str] = {}
    for node in tree.css("meta"):
        name = (node.attributes.get("name") or node.attributes.get("property") or "").strip()
        content = (node.attributes.get("content") or "").strip()
        if name and content:
            meta_dict[name] = content
    result["meta"] = meta_dict

    # Links (href only, first 200)
    links: list[str] = []
    for node in tree.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if href and not href.startswith(("#", "javascript:", "mailto:")):
            resolved = urljoin(source_url, href)
            if resolved not in links:
                links.append(resolved)
        if len(links) >= 200:
            break
    result["links"] = links

    # Headings (h1–h6, first 50)
    headings: list[str] = []
    for node in tree.css("h1, h2, h3, h4, h5, h6"):
        text = node.text(deep=True, separator=" ", strip=True)
        if text:
            headings.append(text)
        if len(headings) >= 50:
            break
    result["headings"] = headings

    return result


def _extract_metadata_bs4(
    html: str, source_url: str
) -> dict[str, object]:
    """Extract metadata using BeautifulSoup (fallback).

    Args:
        html: Raw HTML string.
        source_url: Source URL for resolving relative links.

    Returns:
        Dictionary with title, meta tags, links, and headings.
    """
    if not _HAS_BS4 or BeautifulSoup is None:
        return {}

    soup = BeautifulSoup(html, "html.parser")
    result: dict[str, object] = {}

    # Title
    title_tag = soup.find("title")
    result["title"] = title_tag.get_text(strip=True) if title_tag else ""

    # Meta tags
    meta_dict: dict[str, str] = {}
    for tag in soup.find_all("meta"):
        name = (tag.get("name") or tag.get("property") or "").strip()
        content = (tag.get("content") or "").strip()
        if name and content:
            meta_dict[str(name)] = str(content)
    result["meta"] = meta_dict

    # Links
    links: list[str] = []
    for a_tag in soup.find_all("a", href=True):
        href = str(a_tag["href"]).strip()
        if href and not href.startswith(("#", "javascript:", "mailto:")):
            resolved = urljoin(source_url, href)
            if resolved not in links:
                links.append(resolved)
        if len(links) >= 200:
            break
    result["links"] = links

    # Headings
    headings: list[str] = []
    for tag in soup.find_all(re.compile(r"^h[1-6]$")):
        text = tag.get_text(strip=True)
        if text:
            headings.append(text)
        if len(headings) >= 50:
            break
    result["headings"] = headings

    return result


# ────────────────────────────────────────────────────────
#  Phase 2 — Content cleaning & extraction
# ────────────────────────────────────────────────────────


def _strip_noise_selectolax(html: str) -> str:
    """Remove noise elements using selectolax.

    Args:
        html: Raw HTML string.

    Returns:
        Cleaned HTML string with noise elements removed.
    """
    if not _HAS_SELECTOLAX or SelectolaxParser is None:
        return html

    tree = SelectolaxParser(html)

    # Remove strip tags
    for tag_name in STRIP_TAGS:
        for node in tree.css(tag_name):
            node.decompose()

    # Remove noise selectors
    for selector in NOISE_SELECTORS:
        try:
            for node in tree.css(selector):
                node.decompose()
        except Exception:
            continue

    # Find main content
    content_root = None
    for selector in CONTENT_SELECTORS:
        try:
            node = tree.css_first(selector)
            if node:
                content_root = node
                break
        except Exception:
            continue

    if content_root:
        return content_root.html
    return tree.body.html if tree.body else tree.html


def _strip_noise_bs4(html: str, source_url: str) -> tuple[str, list[str], list[str]]:
    """Remove noise and extract links/images using BeautifulSoup.

    Args:
        html: Raw HTML string.
        source_url: Source URL for resolving relative links.

    Returns:
        Tuple of (clean_html, links, images).
    """
    if not _HAS_BS4 or BeautifulSoup is None:
        return html, [], []

    soup = BeautifulSoup(html, "html.parser")

    # Remove strip tags
    for tag_name in STRIP_TAGS:
        for element in soup.find_all(tag_name):
            element.decompose()

    # Remove noise selectors
    for selector in NOISE_SELECTORS:
        try:
            for element in soup.select(selector):
                element.decompose()
        except Exception:
            continue

    # Find main content
    content_root = None
    for selector in CONTENT_SELECTORS:
        try:
            found = soup.select_one(selector)
            if found:
                content_root = found
                break
        except Exception:
            continue

    if content_root is None:
        content_root = soup.find("body") or soup

    # Collect links
    links: list[str] = []
    for a_tag in content_root.find_all("a", href=True):
        href = str(a_tag["href"]).strip()
        if href and not href.startswith(("#", "javascript:", "mailto:")):
            resolved = urljoin(source_url, href)
            if resolved not in links:
                links.append(resolved)

    # Collect images
    images: list[str] = []
    for img_tag in content_root.find_all("img", src=True):
        src = str(img_tag["src"]).strip()
        if src and not src.startswith("data:"):
            resolved = urljoin(source_url, src)
            if resolved not in images:
                images.append(resolved)

    clean_html = str(content_root)
    return clean_html, links, images


# ────────────────────────────────────────────────────────
#  Markdown conversion
# ────────────────────────────────────────────────────────


def _html_to_markdown(html_fragment: str) -> str:
    """Convert HTML fragment to Markdown.

    Uses html2text when available; falls back to regex stripping.

    Args:
        html_fragment: Cleaned HTML string.

    Returns:
        Markdown string.
    """
    if not _HAS_HTML2TEXT or _h2t is None:
        return _strip_tags_regex(html_fragment)

    converter = _h2t.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = False
    converter.ignore_emphasis = False
    converter.body_width = 0
    converter.unicode_snob = True
    converter.protect_links = True
    converter.wrap_links = False
    converter.single_line_break = True
    converter.mark_code = True

    try:
        md = converter.handle(html_fragment)
    except Exception:
        return _strip_tags_regex(html_fragment)

    md = _RE_MULTI_NEWLINE.sub("\n\n", md)
    return md.strip()


def _strip_tags_regex(text: str) -> str:
    """Remove HTML tags via regex as a fallback.

    Args:
        text: Raw HTML string.

    Returns:
        Plain text with tags removed and whitespace normalised.
    """
    text = _RE_HTML_TAG.sub("", text)
    text = _RE_HTML_ENTITY.sub(" ", text)
    text = _RE_MULTI_NEWLINE.sub("\n\n", text)
    text = _RE_MULTIPLE_SPACES.sub(" ", text)
    return text.strip()


# ────────────────────────────────────────────────────────
#  Page metadata builder
# ────────────────────────────────────────────────────────


def _build_page_metadata(
    meta_dict: dict[str, str],
    source_url: str,
    title: str,
) -> dict[str, object]:
    """Build a page_metadata dictionary from extracted meta tags.

    Resolution order: OG tags → ld+json → standard meta → defaults.

    Args:
        meta_dict: Flattened name→content meta tag mapping.
        source_url: Source URL for canonical resolution.
        title: Extracted page title.

    Returns:
        Dictionary matching the page_metadata contract consumed by
        ScrapePipeline.
    """
    # OG helpers
    def og(prop: str) -> str:
        return meta_dict.get(f"og:{prop}", "").strip()

    def meta_n(name: str) -> str:
        return meta_dict.get(name, "").strip()

    # Title
    page_title = og("title") or title or ""

    # Author
    author = meta_n("author") or og("article:author") or ""

    # Site name
    site_name = og("site_name") or ""

    # Description
    description = og("description") or meta_n("description") or ""

    # Language — best effort from meta
    language = meta_n("language") or ""

    # Published date
    published_date = (
        meta_n("article:published_time")
        or meta_n("date")
        or meta_n("pubdate")
        or ""
    )

    # Keywords
    kw_str = meta_n("keywords") or meta_n("news_keywords") or ""
    keywords: list[str] = (
        [k.strip() for k in kw_str.split(",") if k.strip()]
        if kw_str
        else []
    )

    # Canonical URL
    canonical_url = meta_n("canonical") or ""
    if canonical_url and not urlparse(canonical_url).scheme:
        canonical_url = urljoin(source_url, canonical_url)

    # OG image
    og_image = og("image") or ""
    if og_image and not urlparse(og_image).scheme:
        og_image = urljoin(source_url, og_image)

    # Content type inference (simplified for pickle-safety)
    content_type = _infer_content_type_simple(meta_dict, source_url)

    return {
        "title": page_title,
        "author": author,
        "site_name": site_name,
        "description": description,
        "content_type": content_type,
        "language": language,
        "published_date": published_date,
        "keywords": keywords,
        "canonical_url": canonical_url,
        "og_image": og_image,
    }


def _infer_content_type_simple(
    meta_dict: dict[str, str], source_url: str
) -> str:
    """Simplified content-type inference for pickle-safe contexts.

    Args:
        meta_dict: Flattened meta tag dictionary.
        source_url: Source URL for pattern matching.

    Returns:
        Content type string (e.g., "article", "blog", "unknown").
    """
    og_type = meta_dict.get("og:type", "").lower()
    if "article" in og_type:
        return "article"
    if "blog" in og_type:
        return "blog"

    url_lower = source_url.lower()
    if "/blog/" in url_lower or "/post/" in url_lower:
        return "blog"
    if "/docs/" in url_lower or "/documentation/" in url_lower or "/api/" in url_lower:
        return "documentation"
    if "/tutorial/" in url_lower or "/guide/" in url_lower:
        return "tutorial"
    if "/news/" in url_lower:
        return "news"

    kw = meta_dict.get("keywords", "").lower()
    if "blog" in kw:
        return "blog"
    if "tutorial" in kw:
        return "tutorial"
    if "documentation" in kw:
        return "documentation"
    if "news" in kw:
        return "news"

    # If there's an og:type of 'website' or similar, mark unknown
    return "unknown"


# ────────────────────────────────────────────────────────
#  Main entry point
# ────────────────────────────────────────────────────────


def parse_html_unified(
    payload: bytes,
    source_url: str,
    encoding: str = "utf-8",
) -> dict[str, object]:
    """Pickle-safe extraction function for ProcessPoolExecutor.

    Two-phase approach:
        Phase 1: Selectolax fast metadata extraction (title, meta tags,
                 links, headings). BS4 fallback if selectolax unavailable.
        Phase 2: Content cleaning & extraction (selectolax with BS4
                 fallback), then html2text Markdown conversion.

    This function MUST contain no closures, no bound methods, and no
    self references — everything is either a local variable, an
    argument, or a module-level constant/function.

    Args:
        payload: Raw HTML bytes.
        source_url: Source URL for metadata resolution and relative-link
            expansion.
        encoding: Character encoding for decoding the payload.

    Returns:
        Dictionary with keys:
            title, meta, links, headings, clean_markdown,
            page_metadata, word_count, paragraph_count,
            links_found, images_found, errors.
    """
    errors: list[str] = []

    # Decode payload
    try:
        html = payload.decode(encoding, errors="replace")
    except (LookupError, UnicodeDecodeError) as exc:
        errors.append(f"Decoding error (encoding={encoding}): {exc}")
        html = payload.decode("utf-8", errors="replace")

    # ── Phase 1: Fast metadata extraction ──
    if _HAS_SELECTOLAX and SelectolaxParser is not None:
        phase1 = _extract_metadata_selectolax(html, source_url)
    elif _HAS_BS4 and BeautifulSoup is not None:
        phase1 = _extract_metadata_bs4(html, source_url)
    else:
        phase1 = {}

    title: str = str(phase1.get("title", ""))
    meta: dict[str, object] = phase1.get("meta", {})  # type: ignore[assignment]
    phase1_links: list[str] = list(phase1.get("links", []))  # type: ignore[arg-type]
    headings: list[str] = list(phase1.get("headings", []))  # type: ignore[arg-type]

    # ── Phase 2: Content cleaning & extraction ──
    clean_html: str = ""
    links_found: list[str] = []
    images_found: list[str] = []

    if _HAS_SELECTOLAX and SelectolaxParser is not None:
        try:
            clean_html = _strip_noise_selectolax(html)
            # Still need BS4 or selectolax for link/image collection
            if _HAS_BS4 and BeautifulSoup is not None:
                _, links_found, images_found = _strip_noise_bs4(
                    html, source_url
                )
            else:
                # Use links from phase 1 as best effort
                links_found = phase1_links
        except Exception as exc:
            errors.append(f"Selectolax content cleaning failed: {exc}")
            if _HAS_BS4 and BeautifulSoup is not None:
                clean_html, links_found, images_found = _strip_noise_bs4(
                    html, source_url
                )
    elif _HAS_BS4 and BeautifulSoup is not None:
        clean_html, links_found, images_found = _strip_noise_bs4(
            html, source_url
        )
    else:
        # No parser available — return raw HTML with error
        errors.append(
            "No HTML parser available (need beautifulsoup4 or selectolax)"
        )
        clean_html = html

    # ── Markdown conversion ──
    try:
        clean_markdown = _html_to_markdown(clean_html)
    except Exception as exc:
        errors.append(f"Markdown conversion failed: {exc}")
        clean_markdown = _strip_tags_regex(clean_html)

    # ── Counts ──
    word_count = len(clean_markdown.split())
    paragraph_count = len(
        [p for p in clean_markdown.split("\n\n") if p.strip()]
    )

    # ── Build page_metadata dict ──
    meta_str_dict: dict[str, str] = {
        k: str(v) for k, v in (meta.items() if isinstance(meta, dict) else [])
    }
    page_metadata = _build_page_metadata(meta_str_dict, source_url, title)

    return {
        "title": title,
        "meta": meta,
        "links": phase1_links,
        "headings": headings,
        "clean_markdown": clean_markdown,
        "page_metadata": page_metadata,
        "word_count": word_count,
        "paragraph_count": paragraph_count,
        "links_found": links_found,
        "images_found": images_found,
        "errors": errors,
    }
