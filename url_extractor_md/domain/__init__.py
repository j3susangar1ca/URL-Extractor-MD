"""Domain __init__ — re-exports for clean imports.

Usage:
    from url_extractor_md.domain import PageMetadata, BackendError, NetworkPort
"""

from .contracts import (
    CallbackType,
    CaptchaResolverPort,
    ExtractorPort,
    NetworkPort,
    ProxySelectorPort,
    StoragePort,
    ValidatorPort,
    WAFDetectorPort,
)
from .exceptions import (
    BackendError,
    ExtractionError,
    NetworkError,
    SecurityError,
    StorageError,
    ValidationError,
)
from .models import (
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

__all__ = [
    # Models
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
    "CallbackType",
    "CaptchaResolverPort",
    "ExtractorPort",
    "NetworkPort",
    "ProxySelectorPort",
    "StoragePort",
    "ValidatorPort",
    "WAFDetectorPort",
]
