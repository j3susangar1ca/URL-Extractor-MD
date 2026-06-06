"""RAG Content Extraction Engine — clean library module (NO network I/O).

This module provides a complete pipeline for parsing, cleaning, and structuring
HTML content into RAG-ready artifacts: clean Markdown, metadata, and chunked
documents.  It is designed to be imported by ``elite_scraper_backend.py`` and
must remain **pickle-safe** for use inside :class:`concurrent.futures.ProcessPoolExecutor`.

Key design principles
---------------------
* **No network I/O** — every public function accepts raw HTML or pre-fetched
  data; fetching is the caller's responsibility.
* **Graceful degradation** — optional dependencies (beautifulsoup4, html2text,
  lxml, selectolax) are gated so the module still imports without them.
* **Pickle-safe workers** — the top-level :func:`parse_html_unified` function
  contains no closures or bound methods and can be shipped to worker processes.
* **Modern type hints** — PEP 484/585/604 style throughout.
"""

from __future__ import annotations

import hashlib
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# 1. Dependency Gates (Graceful Degradation)
# ---------------------------------------------------------------------------

_MISSING_DEPS: List[str] = []

try:
    from bs4 import BeautifulSoup, Tag  # type: ignore[import-untyped]
except ImportError:
    _MISSING_DEPS.append("beautifulsoup4")

try:
    import html2text as _html2text_mod  # type: ignore[import-untyped]
except ImportError:
    _MISSING_DEPS.append("html2text")

try:
    import lxml  # type: ignore[import-untyped]  # noqa: F401 — BS4 parser backend
except ImportError:
    _MISSING_DEPS.append("lxml")

try:
    from selectolax.parser import HTMLParser as _SelectolaxParser  # type: ignore[import-untyped]
except ImportError:
    _MISSING_DEPS.append("selectolax")


# ---------------------------------------------------------------------------
# 2. Constants
# ---------------------------------------------------------------------------

STRIP_TAGS: List[str] = [
    "nav", "footer", "aside", "header", "script", "style", "noscript",
    "iframe", "form", "button", "input", "select", "textarea", "svg",
    "canvas", "video", "audio", "source", "dialog", "menu", "menuitem",
]

NOISE_SELECTORS: List[str] = [
    # Cookies / GDPR banners
    ".cookie-banner", ".cookie-notice", ".cookie-consent", ".cookie-popup",
    ".cookie-bar", ".cookie-wrapper", "#cookie-banner", "#cookie-notice",
    "#cookie-consent", "#cookie-popup", "[class*='cookie']",
    "[id*='cookie']", "[class*='gdpr']", "[id*='gdpr']",
    # Banners / popups / overlays
    ".banner", ".popup", ".overlay", ".modal", ".lightbox",
    "[class*='popup']", "[class*='overlay']", "[class*='modal-backdrop']",
    "[class*='interstitial']", "[class*='paywall']",
    # Sidebars & widgets
    ".sidebar", "#sidebar", ".widget", "[class*='sidebar']",
    "[class*='widget']", "[class*='rail']",
    # Advertisements
    ".ad", ".ads", ".advert", ".advertisement", ".sponsored",
    "[class*='ad-container']", "[class*='ad-wrapper']", "[class*='ad-slot']",
    "[id*='ad-']", "[id*='google_ads']",
    # Social sharing / follow bars
    ".social", ".share", ".share-bar", ".share-buttons",
    "[class*='social-share']", "[class*='share-widget']",
    "[class*='facebook']", "[class*='twitter']", "[class*='linkedin']",
    # Comments
    ".comments", "#comments", ".comment-list", "[class*='comment-section']",
    "[id*='comment']", ".disqus", "#disqus_thread",
    # Newsletter / subscription prompts
    ".newsletter", ".subscribe", "[class*='newsletter']",
    "[class*='subscribe']", "[class*='signup-prompt']",
    # Floating / sticky bars
    ".sticky-bar", ".floating-bar", "[class*='sticky-header']",
    "[class*='floating-action']", ".top-bar", ".bottom-bar",
    # Related / recommended (not main content)
    ".related", ".recommend", "[class*='related-posts']",
    "[class*='recommended']", "[class*='you-may-also']",
    # Footers that embed navigation
    ".site-footer", "#colophon",
]

CONTENT_SELECTORS: List[str] = [
    "article",
    "[role='main']",
    "main",
    ".post-content",
    ".article-content",
    ".entry-content",
    ".content-body",
    ".page-content",
    ".story-body",
    "#content",
    "#main-content",
    ".markdown-body",
]

CHUNK_MIN_WORDS: int = 500
CHUNK_MAX_WORDS: int = 800

_ACCENT_TRANSLATION_MAP: Dict[int, str] = {
    ord("\u00e0"): "a", ord("\u00e1"): "a", ord("\u00e2"): "a",
    ord("\u00e3"): "a", ord("\u00e4"): "a", ord("\u00e5"): "a",
    ord("\u00e8"): "e", ord("\u00e9"): "e", ord("\u00ea"): "e",
    ord("\u00eb"): "e",
    ord("\u00ec"): "i", ord("\u00ed"): "i", ord("\u00ee"): "i",
    ord("\u00ef"): "i",
    ord("\u00f2"): "o", ord("\u00f3"): "o", ord("\u00f4"): "o",
    ord("\u00f5"): "o", ord("\u00f6"): "o",
    ord("\u00f9"): "u", ord("\u00fa"): "u", ord("\u00fb"): "u",
    ord("\u00fc"): "u",
    ord("\u00f1"): "n",
    ord("\u00e7"): "c",
    ord("\u00df"): "ss",
    ord("\u00c0"): "A", ord("\u00c1"): "A", ord("\u00c2"): "A",
    ord("\u00c3"): "A", ord("\u00c4"): "A", ord("\u00c5"): "A",
    ord("\u00c8"): "E", ord("\u00c9"): "E", ord("\u00ca"): "E",
    ord("\u00cb"): "E",
    ord("\u00cc"): "I", ord("\u00cd"): "I", ord("\u00ce"): "I",
    ord("\u00cf"): "I",
    ord("\u00d2"): "O", ord("\u00d3"): "O", ord("\u00d4"): "O",
    ord("\u00d5"): "O", ord("\u00d6"): "O",
    ord("\u00d9"): "U", ord("\u00da"): "U", ord("\u00db"): "U",
    ord("\u00dc"): "U",
    ord("\u00d1"): "N",
    ord("\u00c7"): "C",
}

# Compiled regex used by html2text post-processing
_RE_FOUR_BLANKS = re.compile(r"\n{4,}")
_RE_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff]")


# ---------------------------------------------------------------------------
# 3. Enums & Dataclasses
# ---------------------------------------------------------------------------

class ContentType(Enum):
    """Classification of the extracted page's content type."""

    ARTICLE = "article"
    POST = "post"
    DOCUMENTATION = "documentation"
    TUTORIAL = "tutorial"
    BLOG = "blog"
    NEWS = "news"
    UNKNOWN = "unknown"


class ExtractionStatus(Enum):
    """Status of a single extraction run."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PageMetadata:
    """Structured metadata extracted from a page's HTML and Open Graph tags."""

    url: str
    title: str
    author: str
    site_name: str
    description: str
    content_type: ContentType
    language: str
    published_date: str
    keywords: List[str]
    canonical_url: str
    og_image: str


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Complete output of the content extraction pipeline."""

    metadata: PageMetadata
    clean_markdown: str
    word_count: int
    char_count: int
    paragraph_count: int
    links_found: List[str]
    images_found: List[str]
    status: ExtractionStatus
    errors: List[str]
    warnings: List[str]
    extraction_time_ms: float


@dataclass(frozen=True, slots=True)
class RAGChunk:
    """A single RAG-ready chunk derived from an :class:`ExtractionResult`."""

    index: int
    content: str
    word_count: int
    source_url: str
    source_title: str
    chunk_id: str = field(default="", kw_only=True)

    def __post_init__(self) -> None:
        """Auto-generate ``chunk_id`` via SHA-256 when left empty."""
        if not self.chunk_id:
            digest = hashlib.sha256(
                f"{self.source_url}::{self.index}::{self.content[:200]}".encode(
                    "utf-8", errors="replace"
                )
            ).hexdigest()
            object.__setattr__(self, "chunk_id", digest)


@dataclass(frozen=True, slots=True)
class StorageResult:
    """Outcome of persisting an extraction result to disk."""

    success: bool
    file_path: str
    file_size: int
    checksum_sha256: str
    chunks_generated: int
    error: str


# ---------------------------------------------------------------------------
# 4. SlugGenerator
# ---------------------------------------------------------------------------

class SlugGenerator:
    """Unicode-safe slug generation with accent translation.

    All public methods are stateless class-methods so the generator never
    needs to be instantiated.
    """

    _RE_SEPARATOR = re.compile(r"[^\w\-]+")
    _RE_DASHES = re.compile(r"-{2,}")
    _RE_EDGE_DASHES = re.compile(r"^-+|-+$")

    def __init__(self) -> None:
        """Prevent instantiation — all methods are class methods."""

    @classmethod
    def generate(cls, text: str, max_length: int = 120) -> str:
        """Generate a URL-safe slug from arbitrary text.

        Args:
            text: Input string (may contain Unicode, accents, spaces).
            max_length: Maximum number of characters in the resulting slug.

        Returns:
            A lowercase, hyphen-separated slug.
        """
        text = text.translate(_ACCENT_TRANSLATION_MAP)
        text = unicodedata.normalize("NFKD", text)
        # Keep only ASCII after decomposition
        text = text.encode("ascii", "ignore").decode("ascii")
        text = text.lower()
        text = cls._RE_SEPARATOR.sub("-", text)
        text = cls._RE_DASHES.sub("-", text)
        text = cls._RE_EDGE_DASHES.sub("", text)
        return text[:max_length].rstrip("-")

    @classmethod
    def from_url(cls, url: str) -> str:
        """Generate a slug from the path segments of a URL.

        Args:
            url: Absolute or relative URL.

        Returns:
            A slug derived from the last meaningful path segment.
        """
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        segments = [s for s in path.split("/") if s and not s.isdigit()]
        if not segments:
            return cls.generate(parsed.netloc)
        # Use the last non-numeric segment; fall back to netloc
        candidate = segments[-1]
        # Strip common extensions
        candidate = re.sub(r"\.(html?|php|asp|aspx|jsp)$", "", candidate)
        return cls.generate(candidate)


# ---------------------------------------------------------------------------
# 5. ContentExtractor (NO network I/O)
# ---------------------------------------------------------------------------

class ContentExtractor:
    """Parse, clean, and structure raw HTML into :class:`ExtractionResult`.

    This class performs **no** network requests.  Callers must supply the
    raw HTML string and the source URL.
    """

    def __init__(self) -> None:
        """Initialise the extractor (no state beyond config)."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_from_html(self, html: str, url: str) -> ExtractionResult:
        """Run the full extraction pipeline on raw HTML.

        Args:
            html: Raw HTML string (already fetched by the caller).
            url: The source URL — used for metadata resolution and
                link absolutisation.

        Returns:
            An :class:`ExtractionResult` with all extracted data.
        """
        t0 = time.monotonic()
        errors: List[str] = []
        warnings: List[str] = []

        if "beautifulsoup4" in _MISSING_DEPS:
            return self._failed_result(url, "beautifulsoup4 not installed", t0)

        try:
            soup = BeautifulSoup(html, "lxml" if "lxml" not in _MISSING_DEPS else "html.parser")
        except Exception as exc:  # noqa: BLE001
            return self._failed_result(url, str(exc), t0)

        # 1. Metadata
        try:
            metadata = self.extract_metadata(soup, url)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Metadata extraction partial: {exc}")
            metadata = PageMetadata(
                url=url, title="", author="", site_name="",
                description="", content_type=ContentType.UNKNOWN,
                language="", published_date="", keywords=[],
                canonical_url="", og_image="",
            )

        # 2. Content
        try:
            clean_md, links, images = self.extract_content(soup, url)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Content extraction failed: {exc}")
            clean_md, links, images = "", [], []

        # 3. Statistics
        word_count = len(clean_md.split())
        char_count = len(clean_md)
        paragraphs = [p for p in clean_md.split("\n\n") if p.strip()]
        paragraph_count = len(paragraphs)

        status = ExtractionStatus.SUCCESS
        if errors:
            status = ExtractionStatus.FAILED
        elif warnings or word_count < 50:
            status = ExtractionStatus.PARTIAL

        elapsed_ms = (time.monotonic() - t0) * 1000

        return ExtractionResult(
            metadata=metadata,
            clean_markdown=clean_md,
            word_count=word_count,
            char_count=char_count,
            paragraph_count=paragraph_count,
            links_found=links,
            images_found=images,
            status=status,
            errors=errors,
            warnings=warnings,
            extraction_time_ms=round(elapsed_ms, 2),
        )

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def extract_metadata(self, soup: BeautifulSoup, url: str) -> PageMetadata:
        """Extract page metadata from Open Graph, meta, and ld+json tags.

        Args:
            soup: Parsed BeautifulSoup document.
            url: Source URL for canonical resolution.

        Returns:
            A fully populated :class:`PageMetadata` instance.
        """
        title = self._meta_content(soup, "og:title") or self._tag_text(soup, "title") or ""
        author = (
            self._meta_content(soup, "author")
            or self._meta_content(soup, "article:author")
            or self._jsonld_author(soup)
            or ""
        )
        site_name = self._meta_content(soup, "og:site_name") or ""
        description = (
            self._meta_content(soup, "og:description")
            or self._meta_content(soup, "description")
            or ""
        )
        language = (
            soup.html.get("lang", "") if soup.html and soup.html.get("lang") else ""
        )
        published_date = (
            self._meta_content(soup, "article:published_time")
            or self._meta_content(soup, "date")
            or self._jsonld_date(soup)
            or ""
        )
        raw_keywords = self._meta_content(soup, "keywords") or ""
        keywords = [k.strip() for k in raw_keywords.split(",") if k.strip()] if raw_keywords else []

        canonical = ""
        canon_tag = soup.find("link", rel="canonical")
        if canon_tag and canon_tag.get("href"):
            canonical = str(canon_tag["href"])

        og_image = self._meta_content(soup, "og:image") or ""

        content_type = self._infer_content_type(soup, url)

        return PageMetadata(
            url=url,
            title=title,
            author=author,
            site_name=site_name,
            description=description,
            content_type=content_type,
            language=language,
            published_date=published_date,
            keywords=keywords,
            canonical_url=canonical,
            og_image=og_image,
        )

    # ------------------------------------------------------------------
    # Content extraction
    # ------------------------------------------------------------------

    def extract_content(
        self, soup: BeautifulSoup, base_url: str
    ) -> Tuple[str, List[str], List[str]]:
        """Strip noise, locate main content, and convert to Markdown.

        Args:
            soup: Parsed BeautifulSoup document.
            base_url: Base URL for resolving relative links.

        Returns:
            A tuple of ``(clean_markdown, links, images)``.
        """
        # 1. Strip noise tags
        for tag_name in STRIP_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        # 2. Remove noise via CSS selectors
        for selector in NOISE_SELECTORS:
            try:
                for tag in soup.select(selector):
                    tag.decompose()
            except Exception:  # noqa: BLE001, S110
                # Invalid selector — skip silently
                pass

        # 3. Locate main content area
        content_el: Tag | None = None
        for selector in CONTENT_SELECTORS:
            found = soup.select_one(selector)
            if found:
                content_el = found
                break

        if content_el is None:
            content_el = soup.body or soup

        # 4. Extract links and images before markdown conversion
        links: List[str] = []
        images: List[str] = []
        for a_tag in content_el.find_all("a", href=True):
            links.append(str(a_tag["href"]))
        for img_tag in content_el.find_all("img", src=True):
            images.append(str(img_tag["src"]))

        # 5. Convert to markdown
        raw_html = str(content_el)
        markdown = self._html_to_markdown(raw_html)

        return markdown, links, images

    # ------------------------------------------------------------------
    # html2text conversion
    # ------------------------------------------------------------------

    def _html_to_markdown(self, html_fragment: str) -> str:
        """Convert an HTML fragment to clean Markdown using html2text.

        Configuration is tuned for RAG extraction: no body-width wrapping,
        Unicode-friendly output, and link/image preservation.

        Args:
            html_fragment: A well-formed HTML string.

        Returns:
            Cleaned Markdown text with normalised whitespace.
        """
        if "html2text" in _MISSING_DEPS:
            # Fallback: strip tags naively
            return re.sub(r"<[^>]+>", "", html_fragment)

        h2t = _html2text_mod.HTML2Text()
        h2t.body_width = 0
        h2t.unicode_snob = True
        h2t.ignore_links = False
        h2t.ignore_images = False
        h2t.protect_links = True
        h2t.wrap_links = False
        h2t.mark_code = True
        h2t.inline_links = True
        h2t.ignore_tables = False

        md = h2t.handle(html_fragment)

        # Post-processing
        md = "\n".join(line.rstrip() for line in md.split("\n"))
        md = _RE_FOUR_BLANKS.sub("\n\n", md)
        md = _RE_ZERO_WIDTH.sub("", md)

        return md.strip()

    # ------------------------------------------------------------------
    # Content type inference
    # ------------------------------------------------------------------

    def _infer_content_type(self, soup: BeautifulSoup, url: str) -> ContentType:
        """Infer the content type from URL patterns and ld+json schema.

        Args:
            soup: Parsed BeautifulSoup document.
            url: Source URL.

        Returns:
            The best-matching :class:`ContentType`.
        """
        # Check ld+json first
        for script in soup.find_all("script", type="application/ld+json"):
            text = script.string or ""
            low = text.lower()
            if '"@type"' not in low:
                continue
            if "newsarticle" in low or "news" in low:
                return ContentType.NEWS
            if "blogposting" in low or "blog" in low:
                return ContentType.BLOG
            if "techarticle" in low or "documentation" in low:
                return ContentType.DOCUMENTATION
            if "tutorial" in low:
                return ContentType.TUTORIAL
            if "article" in low:
                return ContentType.ARTICLE
            if "discussionforumposting" in low or "socialmediaposting" in low:
                return ContentType.POST

        # Fall back to URL heuristics
        lower = url.lower()
        if any(p in lower for p in ("/blog/", "/post/", "/posts/")):
            return ContentType.BLOG
        if any(p in lower for p in ("/docs/", "/documentation/", "/api/", "/reference/", "/manual/")):
            return ContentType.DOCUMENTATION
        if any(p in lower for p in ("/tutorial/", "/learn/", "/guide/")):
            return ContentType.TUTORIAL
        if any(p in lower for p in ("/news/", "/article/", "/story/")):
            return ContentType.NEWS

        return ContentType.UNKNOWN

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _meta_content(soup: BeautifulSoup, attr_value: str) -> str:
        """Retrieve content from a ``<meta>`` tag by property or name.

        Args:
            soup: Parsed BeautifulSoup document.
            attr_value: The ``property`` or ``name`` attribute value to match.

        Returns:
            The ``content`` attribute value, or empty string.
        """
        tag = soup.find("meta", attrs={"property": attr_value}) or soup.find(
            "meta", attrs={"name": attr_value}
        )
        if tag and tag.get("content"):
            return str(tag["content"])
        return ""

    @staticmethod
    def _tag_text(soup: BeautifulSoup, tag_name: str) -> str:
        """Get stripped text of the first matching tag.

        Args:
            soup: Parsed BeautifulSoup document.
            tag_name: HTML tag name.

        Returns:
            Stripped text content, or empty string.
        """
        tag = soup.find(tag_name)
        return tag.get_text(strip=True) if tag else ""

    @staticmethod
    def _jsonld_author(soup: BeautifulSoup) -> str:
        """Extract author name from ld+json schema markup.

        Args:
            soup: Parsed BeautifulSoup document.

        Returns:
            Author name string, or empty string.
        """
        import json

        for script in soup.find_all("script", type="application/ld+json"):
            text = script.string or ""
            try:
                data = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                continue
            author = data.get("author")
            if isinstance(author, dict):
                return str(author.get("name", ""))
            if isinstance(author, str):
                return author
        return ""

    @staticmethod
    def _jsonld_date(soup: BeautifulSoup) -> str:
        """Extract published date from ld+json schema markup.

        Args:
            soup: Parsed BeautifulSoup document.

        Returns:
            Date string, or empty string.
        """
        import json

        for script in soup.find_all("script", type="application/ld+json"):
            text = script.string or ""
            try:
                data = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                continue
            for key in ("datePublished", "dateCreated", "dateModified"):
                val = data.get(key)
                if val:
                    return str(val)
        return ""

    @staticmethod
    def _failed_result(url: str, error_msg: str, t0: float) -> ExtractionResult:
        """Build a FAILED :class:`ExtractionResult` quickly.

        Args:
            url: Source URL.
            error_msg: Error description.
            t0: Start time from :func:`time.monotonic`.

        Returns:
            An :class:`ExtractionResult` with :attr:`status` FAILED.
        """
        elapsed_ms = (time.monotonic() - t0) * 1000
        return ExtractionResult(
            metadata=PageMetadata(
                url=url, title="", author="", site_name="",
                description="", content_type=ContentType.UNKNOWN,
                language="", published_date="", keywords=[],
                canonical_url="", og_image="",
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


# ---------------------------------------------------------------------------
# 6. RAGChunker
# ---------------------------------------------------------------------------

class RAGChunker:
    """Split an :class:`ExtractionResult` into RAG-ready word-chunks.

    Chunks respect paragraph boundaries where possible and fall back to
    sentence-level splitting for oversized paragraphs.
    """

    _RE_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

    def __init__(self, min_words: int = CHUNK_MIN_WORDS, max_words: int = CHUNK_MAX_WORDS) -> None:
        """Initialise chunker with word-count boundaries.

        Args:
            min_words: Minimum words per chunk.
            max_words: Maximum words per chunk.
        """
        self.min_words = min_words
        self.max_words = max_words

    def chunk(self, result: ExtractionResult) -> List[RAGChunk]:
        """Chunk an extraction result into RAG-ready segments.

        The algorithm:
        1. Split Markdown text into paragraphs (``\\n\\n`` delimiter).
        2. Accumulate paragraphs into the current chunk until
           ``min_words`` is reached.
        3. If a single paragraph exceeds ``max_words``, split at sentence
           boundaries.
        4. Emit a new :class:`RAGChunk` when the current accumulation
           crosses ``min_words`` or exceeds ``max_words``.

        Args:
            result: A fully populated extraction result.

        Returns:
            A list of :class:`RAGChunk` instances with auto-generated IDs.
        """
        paragraphs = [p.strip() for p in result.clean_markdown.split("\n\n") if p.strip()]
        chunks: List[RAGChunk] = []
        buffer: List[str] = []
        buffer_words = 0
        idx = 0

        for para in paragraphs:
            words = para.split()
            para_wc = len(words)

            # Oversized paragraph — sentence-split
            if para_wc > self.max_words:
                # Flush existing buffer first
                if buffer:
                    chunks.append(self._make_chunk(idx, buffer, result))
                    idx += 1
                    buffer = []
                    buffer_words = 0

                # Split paragraph by sentences
                sub_chunks = self._split_sentences(para)
                for sub in sub_chunks:
                    sub_wc = len(sub.split())
                    if sub_wc == 0:
                        continue
                    chunks.append(self._make_chunk(idx, [sub], result))
                    idx += 1
                continue

            # Would adding this paragraph exceed max?
            if buffer_words + para_wc > self.max_words and buffer:
                chunks.append(self._make_chunk(idx, buffer, result))
                idx += 1
                buffer = []
                buffer_words = 0

            buffer.append(para)
            buffer_words += para_wc

            # Emit if we've crossed min threshold
            if buffer_words >= self.min_words:
                chunks.append(self._make_chunk(idx, buffer, result))
                idx += 1
                buffer = []
                buffer_words = 0

        # Flush remaining buffer
        if buffer:
            chunks.append(self._make_chunk(idx, buffer, result))

        return chunks

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _split_sentences(self, text: str) -> List[str]:
        """Split an oversized paragraph into sentence-level sub-strings.

        Each sub-string is grown until it reaches ``min_words`` or runs
        out of sentences.

        Args:
            text: Paragraph text.

        Returns:
            List of sentence-grouped sub-strings.
        """
        sentences = self._RE_SENTENCE_END.split(text)
        result: List[str] = []
        buf: List[str] = []
        buf_wc = 0

        for sent in sentences:
            wc = len(sent.split())
            if buf_wc + wc > self.max_words and buf:
                result.append(" ".join(buf))
                buf = []
                buf_wc = 0
            buf.append(sent)
            buf_wc += wc
            if buf_wc >= self.min_words:
                result.append(" ".join(buf))
                buf = []
                buf_wc = 0

        if buf:
            result.append(" ".join(buf))

        return result

    @staticmethod
    def _make_chunk(index: int, paragraphs: List[str], result: ExtractionResult) -> RAGChunk:
        """Create a :class:`RAGChunk` from accumulated paragraphs.

        Args:
            index: Zero-based chunk index.
            paragraphs: List of paragraph strings.
            result: Parent extraction result (for URL/title).

        Returns:
            A new :class:`RAGChunk`.
        """
        content = "\n\n".join(paragraphs)
        return RAGChunk(
            index=index,
            content=content,
            word_count=len(content.split()),
            source_url=result.metadata.url,
            source_title=result.metadata.title,
        )


# ---------------------------------------------------------------------------
# 7. MarkdownBuilder
# ---------------------------------------------------------------------------

class MarkdownBuilder:
    """Build structured Markdown documents from extraction results and chunks.

    Two output formats are supported:
    * Full document with YAML frontmatter and sections.
    * Chunk-specific document for vector-store ingestion.
    """

    def __init__(self) -> None:
        """Initialise the builder (no state)."""

    def build_document(
        self, result: ExtractionResult, user_filename: str | None = None
    ) -> str:
        """Build a full Markdown document with YAML frontmatter.

        The document structure is:
        1. YAML frontmatter (metadata_version, timestamp, source_url, etc.)
        2. Title heading
        3. Resumen section (description or auto-generated summary)
        4. Contenido extraído section (clean Markdown)
        5. Enlaces de referencia section (links and images)

        Args:
            result: A fully populated extraction result.
            user_filename: Optional user-supplied filename for frontmatter.

        Returns:
            Complete Markdown string.
        """
        meta = result.metadata
        now = datetime.now(tz=timezone.utc).isoformat()

        frontmatter = (
            f"---\n"
            f'metadata_version: "1.0"\n'
            f"timestamp: {_yaml_escape(now)}\n"
            f"source_url: {_yaml_escape(meta.url)}\n"
            f"site_name: {_yaml_escape(meta.site_name)}\n"
            f"author: {_yaml_escape(meta.author)}\n"
            f"content_type: {_yaml_escape(meta.content_type.value)}\n"
            f"language: {_yaml_escape(meta.language)}\n"
            f"published_date: {_yaml_escape(meta.published_date)}\n"
            f"description: {_yaml_escape(meta.description)}\n"
            f"keywords: [{', '.join(_yaml_escape(k) for k in meta.keywords)}]\n"
            f"word_count: {result.word_count}\n"
            f"paragraph_count: {result.paragraph_count}\n"
            f"extraction_status: {_yaml_escape(result.status.value)}\n"
        )
        if user_filename:
            frontmatter += f"user_filename: {_yaml_escape(user_filename)}\n"
        frontmatter += "---\n\n"

        title = meta.title or SlugGenerator.from_url(meta.url)
        doc = f"{frontmatter}# {title}\n\n"

        # Resumen
        summary = meta.description or "(Sin descripción disponible)"
        doc += f"## Resumen\n\n{summary}\n\n"

        # Contenido extraído
        doc += f"## Contenido extraído\n\n{result.clean_markdown}\n\n"

        # Enlaces de referencia
        doc += "## Enlaces de referencia\n\n"
        if result.links_found:
            doc += "### Enlaces\n\n"
            for link in result.links_found[:50]:
                doc += f"- {_yaml_escape(link)}\n"
            doc += "\n"
        if result.images_found:
            doc += "### Imágenes\n\n"
            for img in result.images_found[:30]:
                doc += f"- {_yaml_escape(img)}\n"
            doc += "\n"

        if not result.links_found and not result.images_found:
            doc += "(Sin enlaces de referencia)\n"

        return doc

    def build_chunk_document(self, chunk: RAGChunk, total_chunks: int) -> str:
        """Build a Markdown document for a single RAG chunk.

        Args:
            chunk: The :class:`RAGChunk` to render.
            total_chunks: Total number of chunks in the parent document.

        Returns:
            Markdown string with chunk metadata and content.
        """
        now = datetime.now(tz=timezone.utc).isoformat()
        doc = (
            f"---\n"
            f'metadata_version: "1.0"\n'
            f"timestamp: {_yaml_escape(now)}\n"
            f"source_url: {_yaml_escape(chunk.source_url)}\n"
            f"source_title: {_yaml_escape(chunk.source_title)}\n"
            f"chunk_index: {chunk.index}\n"
            f"total_chunks: {total_chunks}\n"
            f"chunk_id: {_yaml_escape(chunk.chunk_id)}\n"
            f"word_count: {chunk.word_count}\n"
            f"---\n\n"
            f"# Chunk {chunk.index + 1}/{total_chunks}\n\n"
            f"{chunk.content}\n"
        )
        return doc


# ---------------------------------------------------------------------------
# 8. Helper — YAML escaping
# ---------------------------------------------------------------------------

def _yaml_escape(text: str) -> str:
    """Escape a string for safe embedding in a YAML double-quoted value.

    Handles the characters that YAML requires to be escaped inside
    double-quoted scalars: backslash, double-quote, newline, carriage
    return, and tab.

    Args:
        text: Raw string to escape.

    Returns:
        YAML-safe string enclosed in double quotes.
    """
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


# ---------------------------------------------------------------------------
# 9. Pickle-safe standalone function for ProcessPoolExecutor
# ---------------------------------------------------------------------------

def parse_html_unified(payload: bytes, source_url: str) -> dict:
    """Unified extraction function for CPU-isolated processing.

    Performs selectolax metadata extraction + BeautifulSoup cleaning +
    html2text conversion in a single pass.  This function **must** be
    pickle-safe — no closures, no instance methods, no ``self``.

    Args:
        payload: Raw HTML bytes.
        source_url: The URL from which the HTML was fetched.

    Returns:
        A flat dict with the following keys:

        - ``title`` (str)
        - ``meta`` (dict[str, str])
        - ``links`` (list[str])
        - ``headings`` (list[dict[str, str]])
        - ``clean_markdown`` (str)
        - ``page_metadata`` (dict[str, Any])
        - ``word_count`` (int)
        - ``paragraph_count`` (int)
        - ``links_found`` (list[str])
        - ``images_found`` (list[str])
        - ``errors`` (list[str])
    """
    errors: List[str] = []
    html = payload.decode("utf-8", errors="replace")

    # ---- Phase 1: Selectolax fast metadata ----
    title = ""
    meta: Dict[str, str] = {}
    links: List[str] = []
    headings: List[Dict[str, str]] = []

    try:
        from selectolax.parser import HTMLParser as _SP  # type: ignore[import-untyped]

        tree = _SP(html)
        title_node = tree.css_first("title")
        if title_node:
            title = title_node.text(strip=True)

        for meta_tag in tree.css("meta"):
            prop = meta_tag.attributes.get("property") or meta_tag.attributes.get("name") or ""
            content = meta_tag.attributes.get("content") or ""
            if prop and content:
                meta[prop] = content

        for a_tag in tree.css("a[href]"):
            href = a_tag.attributes.get("href", "")
            if href:
                links.append(href)

        for tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            for h_node in tree.css(tag_name):
                headings.append({"level": tag_name, "text": h_node.text(strip=True)})
    except Exception as exc:  # noqa: BLE001
        errors.append(f"selectolax phase failed: {exc}")

    # ---- Phase 2: BeautifulSoup deep cleaning ----
    clean_md = ""
    images_found: List[str] = []
    links_found: List[str] = []

    try:
        from bs4 import BeautifulSoup as _BS  # type: ignore[import-untyped]

        soup = _BS(html, "lxml" if "lxml" not in _MISSING_DEPS else "html.parser")

        # Strip noise tags
        for tag_name in STRIP_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        # Remove noise selectors
        for selector in NOISE_SELECTORS:
            try:
                for tag in soup.select(selector):
                    tag.decompose()
            except Exception:  # noqa: BLE001, S110
                pass

        # Locate main content
        content_el = None
        for selector in CONTENT_SELECTORS:
            found = soup.select_one(selector)
            if found:
                content_el = found
                break
        if content_el is None:
            content_el = soup.body or soup

        # Extract links and images
        for a_tag in content_el.find_all("a", href=True):
            links_found.append(str(a_tag["href"]))
        for img_tag in content_el.find_all("img", src=True):
            images_found.append(str(img_tag["src"]))

        # ---- Phase 3: html2text conversion ----
        raw_html_fragment = str(content_el)

        try:
            import html2text as _h2t  # type: ignore[import-untyped]

            converter = _h2t.HTML2Text()
            converter.body_width = 0
            converter.unicode_snob = True
            converter.ignore_links = False
            converter.ignore_images = False
            converter.protect_links = True
            converter.wrap_links = False
            converter.mark_code = True
            converter.inline_links = True
            converter.ignore_tables = False
            clean_md = converter.handle(raw_html_fragment)

            # Post-process
            clean_md = "\n".join(line.rstrip() for line in clean_md.split("\n"))
            clean_md = _RE_FOUR_BLANKS.sub("\n\n", clean_md)
            clean_md = _RE_ZERO_WIDTH.sub("", clean_md)
            clean_md = clean_md.strip()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"html2text conversion failed: {exc}")
            # Fallback: strip tags
            clean_md = re.sub(r"<[^>]+>", "", raw_html_fragment)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"BeautifulSoup phase failed: {exc}")

    # ---- Build PageMetadata dict ----
    # Merge selectolax meta with dedicated extraction
    resolved_title = meta.get("og:title") or title or ""
    author = (
        meta.get("author")
        or meta.get("article:author")
        or ""
    )
    site_name = meta.get("og:site_name") or ""
    description = meta.get("og:description") or meta.get("description") or ""
    language = ""
    # Quick language extraction without BS4
    lang_match = re.search(r'<html[^>]*\blang=["\']([^"\']+)', html, re.IGNORECASE)
    if lang_match:
        language = lang_match.group(1)

    published_date = meta.get("article:published_time") or meta.get("date") or ""
    raw_keywords = meta.get("keywords") or ""
    keywords = [k.strip() for k in raw_keywords.split(",") if k.strip()]

    canonical_url = ""
    canon_match = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)', html, re.IGNORECASE)
    if canon_match:
        canonical_url = canon_match.group(1)

    og_image = meta.get("og:image") or ""

    # Infer content type
    content_type_str = "unknown"
    lower_url = source_url.lower()
    if any(p in lower_url for p in ("/blog/", "/post/", "/posts/")):
        content_type_str = "blog"
    elif any(p in lower_url for p in ("/docs/", "/documentation/", "/api/", "/reference/")):
        content_type_str = "documentation"
    elif any(p in lower_url for p in ("/tutorial/", "/learn/", "/guide/")):
        content_type_str = "tutorial"
    elif any(p in lower_url for p in ("/news/", "/article/", "/story/")):
        content_type_str = "news"

    # Check ld+json
    for script_match in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.IGNORECASE | re.DOTALL,
    ):
        import json

        try:
            data = json.loads(script_match.group(1))
            low = str(data).lower()
            if "newsarticle" in low or "news" in low:
                content_type_str = "news"
            elif "blogposting" in low or "blog" in low:
                content_type_str = "blog"
            elif "techarticle" in low or "documentation" in low:
                content_type_str = "documentation"
            elif "tutorial" in low:
                content_type_str = "tutorial"
            elif "article" in low:
                content_type_str = "article"
            # Try to extract author from ld+json
            author_data = data.get("author")
            if isinstance(author_data, dict) and not author:
                author = str(author_data.get("name", ""))
            elif isinstance(author_data, str) and not author:
                author = author_data
            # Try date
            if not published_date:
                for dk in ("datePublished", "dateCreated", "dateModified"):
                    dv = data.get(dk)
                    if dv:
                        published_date = str(dv)
                        break
        except (json.JSONDecodeError, ValueError):
            pass

    page_metadata: Dict[str, Any] = {
        "url": source_url,
        "title": resolved_title,
        "author": author,
        "site_name": site_name,
        "description": description,
        "content_type": content_type_str,
        "language": language,
        "published_date": published_date,
        "keywords": keywords,
        "canonical_url": canonical_url,
        "og_image": og_image,
    }

    word_count = len(clean_md.split())
    paragraph_count = len([p for p in clean_md.split("\n\n") if p.strip()])

    return {
        "title": resolved_title,
        "meta": meta,
        "links": links,
        "headings": headings,
        "clean_markdown": clean_md,
        "page_metadata": page_metadata,
        "word_count": word_count,
        "paragraph_count": paragraph_count,
        "links_found": links_found,
        "images_found": images_found,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# 10. verify_dependencies
# ---------------------------------------------------------------------------

def verify_dependencies() -> Tuple[bool, List[str]]:
    """Check that all required and optional dependencies are available.

    Returns:
        A tuple of ``(all_ok, missing)`` where *all_ok* is ``True`` when
        every required dependency is importable and *missing* is the list
        of package names that could not be loaded.
    """
    required = ["beautifulsoup4", "html2text", "lxml"]
    optional = ["selectolax"]

    missing = [dep for dep in required if dep in _MISSING_DEPS]
    # Optional deps are reported but don't cause failure
    all_ok = len(missing) == 0

    combined_missing = missing + [
        dep for dep in optional if dep in _MISSING_DEPS
    ]

    return all_ok, combined_missing
