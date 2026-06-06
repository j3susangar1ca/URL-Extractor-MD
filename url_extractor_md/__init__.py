"""URL Extractor MD — Enterprise-grade web extraction with RAG pipeline.

A modular, hexagonal-architecture Python package for extracting web content
into structured Markdown documents with YAML frontmatter. Features TLS
fingerprinting, WAF evasion, CAPTCHA resolution, and isolated content
extraction in worker processes.

Usage:
    from url_extractor_md import ScrapePipeline, SystemConfig
    from url_extractor_md.infrastructure import ...

    pipeline = ScrapePipeline(...)
    result = await pipeline.execute(url="https://example.com", ...)
"""

from .application.chunking import MarkdownBuilder, RAGChunker, RAGPipeline
from .application.pipeline import CallbackBridge, ScrapePipeline
from .config import SystemConfig
from .domain.contracts import (
    CaptchaResolverPort,
    ExtractorPort,
    NetworkPort,
    ProxySelectorPort,
    StoragePort,
    ValidatorPort,
    WAFDetectorPort,
)
from .domain.exceptions import (
    BackendError,
    ExtractionError,
    NetworkError,
    SecurityError,
    StorageError,
    ValidationError,
)
from .domain.models import (
    BrowserProfile,
    ContentType,
    ExtractionResult,
    ExtractionStatus,
    PageMetadata,
    PreviewResult,
    ProgressEvent,
    ProxyNode,
    RAGChunk,
    ScrapingResult,
    Stage,
    StorageResult,
)

__version__ = "2.0.0"
__all__ = [
    # Pipeline
    "CallbackBridge",
    "ScrapePipeline",
    # Config
    "SystemConfig",
    # Chunking
    "MarkdownBuilder",
    "RAGChunker",
    "RAGPipeline",
    # Domain models
    "BrowserProfile",
    "ContentType",
    "ExtractionResult",
    "ExtractionStatus",
    "PageMetadata",
    "PreviewResult",
    "ProgressEvent",
    "ProxyNode",
    "RAGChunk",
    "ScrapingResult",
    "Stage",
    "StorageResult",
    # Exceptions
    "BackendError",
    "ExtractionError",
    "NetworkError",
    "SecurityError",
    "StorageError",
    "ValidationError",
    # Contracts
    "CaptchaResolverPort",
    "ExtractorPort",
    "NetworkPort",
    "ProxySelectorPort",
    "StoragePort",
    "ValidatorPort",
    "WAFDetectorPort",
]
