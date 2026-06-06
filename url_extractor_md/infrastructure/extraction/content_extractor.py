"""Content extractor — parses, cleans, and structures raw HTML into ExtractionResult.

This module implements the full content extraction pipeline:
    1. Parse HTML with BeautifulSoup (lxml parser preferred, html.parser fallback).
    2. Extract structured metadata (Open Graph, ld+json, <meta> tags).
    3. Strip noise elements (nav, footer, ads, cookie banners, etc.).
    4. Convert the cleaned HTML fragment to Markdown via html2text.
    5. Assemble a frozen ExtractionResult with word/paragraph counts.

Dependency gates:
    - beautifulsoup4: **required** — ImportError if missing.
    - html2text: optional — falls back to regex tag stripping.
    - lxml: optional — falls back to html.parser.
    - selectolax: optional — not used in this module but recognised.
"""

from __future__ import annotations

import json
import logging
import re
import time
from urllib.parse import urljoin, urlparse

# ────────────────────────────────────────────────────────
#  Dependency gate — beautifulsoup4 is mandatory
# ────────────────────────────────────────────────────────
try:
    from bs4 import BeautifulSoup, Tag
except ImportError as _exc:
    raise ImportError(
        "beautifulsoup4 is required for ContentExtractor. "
        "Install it with: pip install beautifulsoup4"
    ) from _exc

# Optional: html2text for Markdown conversion
try:
    import html2text as _h2t

    _HAS_HTML2TEXT = True
except ImportError:
    _h2t = None  # type: ignore[assignment]
    _HAS_HTML2TEXT = False

# Optional: lxml for faster parsing
try:
    import lxml  # noqa: F401 — presence check only

    _PREFERRED_PARSER = "lxml"
except ImportError:
    _PREFERRED_PARSER = "html.parser"

# ────────────────────────────────────────────────────────
#  Domain imports
# ────────────────────────────────────────────────────────
from ...domain.models import ContentType, ExtractionResult, ExtractionStatus, PageMetadata
from .slug_generator import SlugGenerator

# ────────────────────────────────────────────────────────
#  Module-level constants
# ────────────────────────────────────────────────────────

STRIP_TAGS: list[str] = [
    "nav",
    "footer",
    "aside",
    "header",
    "script",
    "style",
    "noscript",
    "iframe",
    "svg",
    "form",
    "button",
    "input",
    "select",
    "textarea",
    "figcaption",
    "figure",
    "template",
]

NOISE_SELECTORS: list[str] = [
    # Cookie / consent banners
    "[class*='cookie']",
    "[class*='consent']",
    "[class*='gdpr']",
    "[class*='banner']",
    "[id*='cookie']",
    "[id*='consent']",
    "[id*='gdpr']",
    "[id*='banner']",
    # Ad containers
    "[class*='ad-']",
    "[class*='ads-']",
    "[class*='advert']",
    "[id*='ad-']",
    "[id*='ads-']",
    "[id*='advert']",
    "ins.adsbygoogle",
    # Sidebars & related content
    ".sidebar",
    "#sidebar",
    ".side-bar",
    "#side-bar",
    "[class*='sidebar-left']",
    "[class*='sidebar-right']",
    "[id*='sidebar-left']",
    "[id*='sidebar-right']",
    "[class*='related']",
    "[class*='recommend']",
    "[class*='popular']",
    "[class*='trending']",
    # Comments
    "[class*='comment']",
    "[id*='comment']",
    "#comments",
    ".comments",
    # Social sharing
    "[class*='share']",
    "[class*='social']",
    "[class*='newsletter']",
    "[class*='subscribe']",
    # Popups / overlays
    "[class*='popup']",
    "[class*='modal']",
    "[class*='overlay']",
    "[class*='interstitial']",
    # Footers / navigation
    "[class*='footer']",
    "[class*='navigation']",
    "[class*='breadcrumb']",
    "[class*='pagination']",
]

CONTENT_SELECTORS: list[str] = [
    "article",
    "[role='main']",
    "main",
    ".post-content",
    ".article-content",
    ".entry-content",
    ".content",
    "#content",
    ".post-body",
    ".article-body",
    ".story-body",
    ".page-content",
    ".main-content",
    "#main-content",
    ".post-entry",
    ".blog-post",
    ".text-content",
    ".article__body",
    ".post__content",
]

# ────────────────────────────────────────────────────────
#  Regex helpers for fallback tag stripping
# ────────────────────────────────────────────────────────
_RE_HTML_TAG = re.compile(r"<[^>]+>")
_RE_MULTI_NEWLINE = re.compile(r"\n{3,}")
_RE_MULTIPLE_SPACES = re.compile(r" {2,}")
_RE_HTML_ENTITY = re.compile(r"&#?\w+;")


def _strip_tags_regex(text: str) -> str:
    """Remove HTML tags via regex when html2text is unavailable.

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
#  ContentExtractor
# ────────────────────────────────────────────────────────


class ContentExtractor:
    """Parse, clean, and structure raw HTML into an ExtractionResult.

    This is the primary extraction adapter used in-process. For isolated
    (subprocess) execution, see IsolatedExtractor which delegates to
    unified_parser.parse_html_unified.

    The class uses __slots__ for memory efficiency and has no mutable
    instance state beyond the logger.
    """

    __slots__ = ("_logger",)

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Initialise the content extractor.

        Args:
            logger: Optional logger; defaults to module-level logger.
        """
        self._logger = logger or logging.getLogger(__name__)

    # ────────────────────────────────────────────────────
    #  Public API
    # ────────────────────────────────────────────────────

    def extract_from_html(self, html: str, url: str) -> ExtractionResult:
        """Run the full extraction pipeline on raw HTML.

        Args:
            html: Raw HTML string.
            url: Source URL used for metadata resolution.

        Returns:
            Immutable ExtractionResult with metadata, clean Markdown,
            word/paragraph counts, and extraction status.
        """
        t0 = time.perf_counter()
        errors: list[str] = []

        try:
            soup = BeautifulSoup(html, _PREFERRED_PARSER)
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return self._failed_result(url, f"HTML parsing failed: {exc}", elapsed)

        try:
            metadata = self.extract_metadata(soup, url)
        except Exception as exc:
            errors.append(f"Metadata extraction error: {exc}")
            metadata = PageMetadata(
                url=url,
                title="",
                author="",
                site_name="",
                description="",
                content_type=ContentType.UNKNOWN,
                language="",
                published_date="",
                keywords=[],
                canonical_url="",
                og_image="",
            )

        try:
            clean_html, links, images = self.extract_content(soup, url)
        except Exception as exc:
            errors.append(f"Content extraction error: {exc}")
            elapsed = (time.perf_counter() - t0) * 1000
            return self._failed_result(url, f"Content extraction failed: {exc}", elapsed)

        try:
            markdown = self._html_to_markdown(clean_html)
        except Exception as exc:
            errors.append(f"Markdown conversion error: {exc}")
            markdown = _strip_tags_regex(clean_html)

        elapsed_ms = (time.perf_counter() - t0) * 1000

        word_count = len(markdown.split())
        paragraph_count = len(
            [p for p in markdown.split("\n\n") if p.strip()]
        )

        status = ExtractionStatus.SUCCESS
        if errors:
            status = ExtractionStatus.PARTIAL
        if word_count < 20:
            status = ExtractionStatus.PARTIAL

        return ExtractionResult(
            metadata=metadata,
            clean_markdown=markdown,
            word_count=word_count,
            char_count=len(markdown),
            paragraph_count=paragraph_count,
            links_found=links,
            images_found=images,
            status=status,
            errors=errors,
            warnings=[],
            extraction_time_ms=round(elapsed_ms, 2),
        )

    # ────────────────────────────────────────────────────
    #  Metadata extraction
    # ────────────────────────────────────────────────────

    def extract_metadata(self, soup: BeautifulSoup, url: str) -> PageMetadata:
        """Extract structured metadata from a parsed HTML document.

        Resolution order for each field:
            1. Open Graph (og:*) meta tags.
            2. ld+json structured data (first matching block).
            3. Standard HTML <meta> tags.
            4. Heuristic fallbacks.

        Args:
            soup: Parsed BeautifulSoup document.
            url: Source URL for canonical resolution.

        Returns:
            Frozen PageMetadata with all extracted fields.
        """
        # --- Open Graph ---
        def og(prop: str) -> str:
            tag = soup.find("meta", attrs={"property": f"og:{prop}"})
            if tag and isinstance(tag, Tag):
                content = tag.get("content")
                return str(content).strip() if content else ""
            return ""

        # --- Standard meta ---
        def meta_name(name: str) -> str:
            tag = soup.find("meta", attrs={"name": name})
            if tag and isinstance(tag, Tag):
                content = tag.get("content")
                return str(content).strip() if content else ""
            return ""

        # --- ld+json extraction ---
        ld_data: dict[str, object] = {}
        for script in soup.find_all("script", type="application/ld+json"):
            if not isinstance(script, Tag) or not script.string:
                continue
            try:
                parsed = json.loads(script.string)
                if isinstance(parsed, dict):
                    ld_data = parsed
                    break
            except (json.JSONDecodeError, TypeError):
                continue

        def ld_get(key: str) -> str:
            val = ld_data.get(key)
            return str(val).strip() if val else ""

        # --- Title ---
        title = og("title") or ld_get("headline") or ""
        if not title:
            title_tag = soup.find("title")
            if title_tag and isinstance(title_tag, Tag) and title_tag.string:
                title = title_tag.string.strip()

        # --- Author ---
        author = meta_name("author") or ld_get("author") or og("article:author") or ""
        # ld+json author can be a dict with @name
        if not author and "author" in ld_data:
            author_val = ld_data["author"]
            if isinstance(author_val, dict):
                author = str(author_val.get("name", ""))
            elif isinstance(author_val, list) and author_val:
                first = author_val[0]
                if isinstance(first, dict):
                    author = str(first.get("name", ""))
                else:
                    author = str(first)

        # --- Site name ---
        site_name = og("site_name") or ld_get("publisher") or ""
        if not site_name and "publisher" in ld_data:
            pub = ld_data["publisher"]
            if isinstance(pub, dict):
                site_name = str(pub.get("name", ""))

        # --- Description ---
        description = og("description") or meta_name("description") or ld_get("description") or ""

        # --- Language ---
        lang = ""
        html_tag = soup.find("html")
        if html_tag and isinstance(html_tag, Tag):
            lang_val = html_tag.get("lang")
            if lang_val:
                lang = str(lang_val).strip()

        # --- Published date ---
        published_date = (
            meta_name("article:published_time")
            or ld_get("datePublished")
            or meta_name("date")
            or meta_name("pubdate")
            or ""
        )

        # --- Keywords ---
        keywords_str = meta_name("keywords") or meta_name("news_keywords") or ""
        keywords = [k.strip() for k in keywords_str.split(",") if k.strip()] if keywords_str else []
        # ld+json tags
        ld_tags = ld_data.get("keywords") or ld_data.get("articleSection")
        if ld_tags and not keywords:
            if isinstance(ld_tags, list):
                keywords = [str(t) for t in ld_tags]
            elif isinstance(ld_tags, str):
                keywords = [k.strip() for k in ld_tags.split(",") if k.strip()]

        # --- Canonical URL ---
        canonical_url = ""
        canonical_tag = soup.find("link", rel="canonical")
        if canonical_tag and isinstance(canonical_tag, Tag):
            href = canonical_tag.get("href")
            if href:
                canonical_url = urljoin(url, str(href))

        # --- OG image ---
        og_image = og("image") or ""
        if og_image and not urlparse(og_image).scheme:
            og_image = urljoin(url, og_image)

        # --- Content type ---
        content_type = self._infer_content_type(soup, url)

        return PageMetadata(
            url=url,
            title=title,
            author=author,
            site_name=site_name,
            description=description,
            content_type=content_type,
            language=lang,
            published_date=published_date,
            keywords=keywords,
            canonical_url=canonical_url,
            og_image=og_image,
        )

    # ────────────────────────────────────────────────────
    #  Content extraction
    # ────────────────────────────────────────────────────

    def extract_content(
        self, soup: BeautifulSoup, base_url: str
    ) -> tuple[str, list[str], list[str]]:
        """Strip noise from the DOM and return cleaned HTML, links, and images.

        Args:
            soup: Parsed BeautifulSoup document.
            base_url: Base URL for resolving relative links.

        Returns:
            Tuple of (clean_html_fragment, link_urls, image_urls).
        """
        # 1. Decompose strip tags
        for tag_name in STRIP_TAGS:
            for element in soup.find_all(tag_name):
                if isinstance(element, Tag):
                    element.decompose()

        # 2. Remove noise via CSS selectors
        for selector in NOISE_SELECTORS:
            try:
                for element in soup.select(selector):
                    if isinstance(element, Tag):
                        element.decompose()
            except Exception:
                # Some selectors may be invalid; skip gracefully.
                continue

        # 3. Find main content
        content_root = None
        for selector in CONTENT_SELECTORS:
            try:
                found = soup.select_one(selector)
                if found and isinstance(found, Tag):
                    content_root = found
                    break
            except Exception:
                continue

        if content_root is None:
            content_root = soup.find("body") if soup.find("body") else soup

        # 4. Collect links
        links: list[str] = []
        for a_tag in content_root.find_all("a", href=True):
            if not isinstance(a_tag, Tag):
                continue
            href = str(a_tag["href"]).strip()
            if href and not href.startswith(("#", "javascript:", "mailto:")):
                resolved = urljoin(base_url, href)
                if resolved not in links:
                    links.append(resolved)

        # 5. Collect images
        images: list[str] = []
        for img_tag in content_root.find_all("img", src=True):
            if not isinstance(img_tag, Tag):
                continue
            src = str(img_tag["src"]).strip()
            if src and not src.startswith("data:"):
                resolved = urljoin(base_url, src)
                if resolved not in images:
                    images.append(resolved)

        # 6. Serialize clean HTML
        clean_html = str(content_root)

        return clean_html, links, images

    # ────────────────────────────────────────────────────
    #  HTML-to-Markdown conversion
    # ────────────────────────────────────────────────────

    def _html_to_markdown(self, html_fragment: str) -> str:
        """Convert an HTML fragment to Markdown using html2text.

        Falls back to regex-based tag stripping when html2text is
        not installed.

        Args:
            html_fragment: Cleaned HTML string.

        Returns:
            Markdown string with post-processing applied.
        """
        if not _HAS_HTML2TEXT or _h2t is None:
            return _strip_tags_regex(html_fragment)

        converter = _h2t.HTML2Text()
        converter.ignore_links = False
        converter.ignore_images = False
        converter.ignore_emphasis = False
        converter.body_width = 0  # Disable line wrapping
        converter.unicode_snob = True
        converter.protect_links = True
        converter.wrap_links = False
        converter.single_line_break = True
        converter.mark_code = True

        try:
            md = converter.handle(html_fragment)
        except Exception as exc:
            self._logger.warning("html2text conversion failed: %s — using regex fallback", exc)
            return _strip_tags_regex(html_fragment)

        # Post-processing
        md = _RE_MULTI_NEWLINE.sub("\n\n", md)
        md = md.strip()

        return md

    # ────────────────────────────────────────────────────
    #  Content type inference
    # ────────────────────────────────────────────────────

    @staticmethod
    def _infer_content_type(soup: BeautifulSoup, url: str) -> ContentType:
        """Heuristically infer the page's content type.

        Uses ld+json @type, Open Graph type, URL patterns, and
        HTML structure signals.

        Args:
            soup: Parsed BeautifulSoup document.
            url: Source URL for pattern matching.

        Returns:
            Classified ContentType enum value.
        """
        # 1. ld+json @type
        for script in soup.find_all("script", type="application/ld+json"):
            if not isinstance(script, Tag) or not script.string:
                continue
            try:
                data = json.loads(script.string)
                if isinstance(data, dict):
                    type_val = str(data.get("@type", "")).lower()
                    if "article" in type_val:
                        return ContentType.ARTICLE
                    if "newsarticle" in type_val:
                        return ContentType.NEWS
                    if "blogposting" in type_val:
                        return ContentType.BLOG
                    if "techarticle" in type_val:
                        return ContentType.DOCUMENTATION
                    if "tutorial" in type_val:
                        return ContentType.TUTORIAL
            except (json.JSONDecodeError, TypeError):
                continue

        # 2. Open Graph type
        og_type_tag = soup.find("meta", attrs={"property": "og:type"})
        if og_type_tag and isinstance(og_type_tag, Tag):
            og_type = str(og_type_tag.get("content", "")).lower()
            if "article" in og_type:
                return ContentType.ARTICLE
            if "blog" in og_type:
                return ContentType.BLOG

        # 3. HTML structure signals
        if soup.find("article"):
            article_tag = soup.find("article")
            if isinstance(article_tag, Tag):
                classes = " ".join(
                    str(c) for c in (article_tag.get("class") or [])
                ).lower()
                if "post" in classes or "blog" in classes:
                    return ContentType.BLOG
                if "tutorial" in classes:
                    return ContentType.TUTORIAL
                if "news" in classes:
                    return ContentType.NEWS
            return ContentType.ARTICLE

        # 4. URL pattern heuristics
        url_lower = url.lower()
        if "/blog/" in url_lower or "/post/" in url_lower:
            return ContentType.BLOG
        if "/docs/" in url_lower or "/documentation/" in url_lower or "/api/" in url_lower:
            return ContentType.DOCUMENTATION
        if "/tutorial/" in url_lower or "/guide/" in url_lower or "/learn/" in url_lower:
            return ContentType.TUTORIAL
        if "/news/" in url_lower:
            return ContentType.NEWS

        # 5. Meta keywords
        kw_tag = soup.find("meta", attrs={"name": "keywords"})
        if kw_tag and isinstance(kw_tag, Tag):
            kw_content = str(kw_tag.get("content", "")).lower()
            if "tutorial" in kw_content:
                return ContentType.TUTORIAL
            if "documentation" in kw_content or "api" in kw_content:
                return ContentType.DOCUMENTATION
            if "blog" in kw_content:
                return ContentType.BLOG
            if "news" in kw_content:
                return ContentType.NEWS

        return ContentType.UNKNOWN

    # ────────────────────────────────────────────────────
    #  Failure helper
    # ────────────────────────────────────────────────────

    @staticmethod
    def _failed_result(url: str, error_msg: str, elapsed_ms: float) -> ExtractionResult:
        """Build a FAILED ExtractionResult for error cases.

        Args:
            url: Source URL.
            error_msg: Error description.
            elapsed_ms: Elapsed time in milliseconds.

        Returns:
            ExtractionResult with FAILED status and empty content.
        """
        return ExtractionResult(
            metadata=PageMetadata(
                url=url,
                title="",
                author="",
                site_name="",
                description="",
                content_type=ContentType.UNKNOWN,
                language="",
                published_date="",
                keywords=[],
                canonical_url="",
                og_image="",
            ),
            clean_markdown="",
            word_count=0,
            char_count=0,
            paragraph_count=0,
            links_found=[],
            images_found=[],
            status=ExtractionStatus.FAILED,
            errors=[error_msg],
            warnings=[],
            extraction_time_ms=round(elapsed_ms, 2),
        )
