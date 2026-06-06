"""Domain models — pure data structures with zero external dependencies.

All models are immutable (frozen=True), use __slots__ for memory efficiency,
and follow PEP 585/604 type hint style.  This module must remain importable
without any third-party dependency.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum, auto


# ────────────────────────────────────────────────────────
#  Enums
# ────────────────────────────────────────────────────────


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


class Stage(Enum):
    """Deterministic pipeline stages for progress reporting."""

    INIT = auto()
    CONNECTING = auto()
    DOWNLOADING = auto()
    WAF_DETECTED = auto()
    CAPTCHA_SOLVING = auto()
    PARSING = auto()
    SAVING = auto()
    COMPLETED = auto()
    ERROR = auto()


# ────────────────────────────────────────────────────────
#  Domain data objects
# ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PageMetadata:
    """Structured metadata extracted from a page's HTML and Open Graph tags.

    Attributes:
        url: Source URL of the page.
        title: Page title (from og:title or <title>).
        author: Author name (from meta or ld+json).
        site_name: Site name (from og:site_name).
        description: Page description (from og:description or meta).
        content_type: Classified content type.
        language: HTML lang attribute value.
        published_date: Publication date string.
        keywords: List of keyword strings.
        canonical_url: Canonical URL from <link rel="canonical">.
        og_image: Open Graph image URL.
    """

    url: str
    title: str
    author: str
    site_name: str
    description: str
    content_type: ContentType
    language: str
    published_date: str
    keywords: list[str]
    canonical_url: str
    og_image: str


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Complete output of the content extraction pipeline.

    Attributes:
        metadata: Structured page metadata.
        clean_markdown: Cleaned Markdown content.
        word_count: Number of words in the markdown.
        char_count: Number of characters in the markdown.
        paragraph_count: Number of paragraphs detected.
        links_found: List of hyperlink URLs found.
        images_found: List of image URLs found.
        status: Extraction status (SUCCESS, PARTIAL, FAILED).
        errors: List of error messages.
        warnings: List of warning messages.
        extraction_time_ms: Elapsed extraction time in milliseconds.
    """

    metadata: PageMetadata
    clean_markdown: str
    word_count: int
    char_count: int
    paragraph_count: int
    links_found: list[str]
    images_found: list[str]
    status: ExtractionStatus
    errors: list[str]
    warnings: list[str]
    extraction_time_ms: float


@dataclass(frozen=True, slots=True)
class RAGChunk:
    """A single RAG-ready chunk derived from an ExtractionResult.

    Attributes:
        index: Zero-based chunk index.
        content: Chunk text content.
        word_count: Number of words in the chunk.
        source_url: URL of the source page.
        source_title: Title of the source page.
        chunk_id: Auto-generated SHA-256 identifier.
    """

    index: int
    content: str
    word_count: int
    source_url: str
    source_title: str
    chunk_id: str = field(default="", kw_only=True)

    def __post_init__(self) -> None:
        """Auto-generate chunk_id via SHA-256 when left empty."""
        if not self.chunk_id:
            digest = hashlib.sha256(
                f"{self.source_url}::{self.index}::{self.content[:200]}".encode(
                    "utf-8", errors="replace"
                )
            ).hexdigest()
            object.__setattr__(self, "chunk_id", digest)


@dataclass(frozen=True, slots=True)
class PreviewResult:
    """Preview of the metadata and projected chunking analysis.

    Attributes:
        title: Page title.
        author: Author name.
        site_name: Site name.
        description: Page description.
        content_type: Content type string value.
        language: Language code.
        published_date: Publication date string.
        keywords: List of keyword strings.
        estimated_word_count: Estimated word count.
        projected_chunks: Projected number of RAG chunks.
    """

    title: str
    author: str
    site_name: str
    description: str
    content_type: str
    language: str
    published_date: str
    keywords: list[str]
    estimated_word_count: int
    projected_chunks: int


@dataclass(frozen=True, slots=True)
class StorageResult:
    """Outcome of persisting an extraction result to disk.

    Attributes:
        success: Whether the operation succeeded.
        file_path: Absolute path of the saved file.
        file_size: Size of the file in bytes.
        checksum_sha256: SHA-256 checksum of the file.
        chunks_generated: Number of RAG chunks generated.
        error: Error message if success is False.
    """

    success: bool
    file_path: str
    file_size: int
    checksum_sha256: str
    chunks_generated: int
    error: str


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """Progress event emitted towards the GUI/Frontend.

    Attributes:
        stage: Current pipeline stage.
        percent: Estimated progress in range [0, 100].
        message: Human-readable message for the UI.
        meta: Additional metadata (downloaded bytes, proxy used, etc.).
    """

    stage: Stage
    percent: int
    message: str
    meta: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize the event to JSON for IPC/websocket transport.

        Returns:
            JSON string with stage, percent, message and meta.
        """
        import json as _json

        return _json.dumps(
            {
                "stage": self.stage.name,
                "percent": self.percent,
                "message": self.message,
                "meta": self.meta,
            },
            ensure_ascii=False,
            default=str,
        )


@dataclass(frozen=True, slots=True)
class ScrapingResult:
    """Immutable result of a scrape_single() operation.

    Attributes:
        success: True if the pipeline completed successfully.
        url: Target URL.
        output_path: Absolute path of the generated .md file.
        status_code: Final HTTP status code.
        proxy_used: URL of the egress proxy used (or None).
        profile_used: TLS/L7 profile identifier used.
        waf_detected: Whether WAF detection predicate fired.
        captcha_solved: Whether a CAPTCHA challenge was resolved.
        elapsed_ms: Wall-clock elapsed time in milliseconds.
        error: Error message if success is False.
        word_count: Number of words in the extracted markdown.
        content_type: Content type detected by the RAG extractor.
    """

    success: bool
    url: str
    output_path: str | None = None
    status_code: int | None = None
    proxy_used: str | None = None
    profile_used: str | None = None
    waf_detected: bool = False
    captcha_solved: bool = False
    elapsed_ms: float = 0.0
    error: str | None = None
    word_count: int | None = None
    content_type: str | None = None


@dataclass(frozen=True, slots=True)
class ProxyNode:
    """Proxy node with weighting and health tracking.

    Attributes:
        url: Proxy URL (e.g., "http://proxy:8080").
        protocol: Proxy protocol (http, socks5, etc.).
        weight: Weight for weighted selection (higher = more likely).
        health_score: Health score [0.0, 1.0] for gradual degradation.
    """

    url: str
    protocol: str = "http"
    weight: float = 1.0
    health_score: float = 1.0


@dataclass(frozen=True, slots=True)
class BrowserProfile:
    """Browser profile for TLS/L7 evasion.

    Attributes:
        impersonate_id: curl_cffi profile TLS identifier.
        user_agent: HTTP User-Agent string.
        tls_fingerprint: TLS fingerprint type.
    """

    impersonate_id: str
    user_agent: str
    tls_fingerprint: str = "modern"
