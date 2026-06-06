"""Domain contracts — Protocol definitions for dependency inversion.

Every port (interface) is defined as a typing.Protocol so that
concrete implementations can be swapped without inheritance and
test doubles can be created trivially with plain functions or
lightweight classes.

This module has ZERO third-party imports to keep the domain layer
pure and importable in any context (including worker processes).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import (
    BrowserProfile,
    ExtractionResult,
    ProgressEvent,
    ProxyNode,
    ScrapingResult,
)


# ────────────────────────────────────────────────────────
#  Callback type
# ────────────────────────────────────────────────────────

CallbackType = ProgressEvent  # Re-exported for convenience


# ────────────────────────────────────────────────────────
#  Network Port
# ────────────────────────────────────────────────────────


@runtime_checkable
class NetworkPort(Protocol):
    """Contract for HTTP network operations.

    Implementations handle TLS fingerprinting, session management,
    streaming downloads, and backoff retries internally.
    """

    async def download(
        self,
        url: str,
        profile: BrowserProfile,
        proxy: ProxyNode | None,
        extra_headers: dict[str, str] | None,
        cancel_event: object | None,
    ) -> tuple[bytes, int, str, dict[str, str]]:
        """Download a URL and return (payload, status_code, encoding, response_headers).

        Args:
            url: Target URL to download.
            profile: Browser profile for TLS/UA configuration.
            proxy: Optional proxy node for egress.
            extra_headers: Additional headers (e.g., CAPTCHA cookies).
            cancel_event: Optional asyncio.Event for cancellation support.

        Returns:
            Tuple of (raw_bytes, status_code, detected_encoding, response_headers).
        """
        ...

    async def close(self) -> None:
        """Clean up network resources (sessions, connections)."""
        ...


# ────────────────────────────────────────────────────────
#  Extractor Port
# ────────────────────────────────────────────────────────


@runtime_checkable
class ExtractorPort(Protocol):
    """Contract for HTML content extraction.

    Implementations may run in-process or in an isolated worker process
    for safety and GIL avoidance.
    """

    async def extract(
        self, payload: bytes, source_url: str, encoding: str = "utf-8"
    ) -> dict[str, object]:
        """Extract structured content from raw HTML bytes.

        Args:
            payload: Raw HTML bytes.
            source_url: Source URL for metadata resolution.
            encoding: Character encoding for decoding.

        Returns:
            Dictionary with extraction results (must be pickle-safe).
        """
        ...

    def shutdown(self) -> None:
        """Shut down any worker processes or resources."""
        ...


# ────────────────────────────────────────────────────────
#  Storage Port
# ────────────────────────────────────────────────────────


@runtime_checkable
class StoragePort(Protocol):
    """Contract for atomic file persistence.

    Implementations guarantee no partial files through atomic write
    operations (write-to-temp + rename).
    """

    async def save_markdown(self, key: object, content: str) -> str:
        """Save Markdown content atomically.

        Args:
            key: Path-like object identifying the target file.
            content: Complete Markdown string to persist.

        Returns:
            Absolute path of the saved file.
        """
        ...

    async def save_raw(self, key: object, payload: bytes) -> str:
        """Save raw bytes atomically.

        Args:
            key: Path-like object identifying the target file.
            payload: Bytes to persist.

        Returns:
            Absolute path of the saved file.
        """
        ...


# ────────────────────────────────────────────────────────
#  Captcha Resolver Port
# ────────────────────────────────────────────────────────


@runtime_checkable
class CaptchaResolverPort(Protocol):
    """Contract for CAPTCHA/WAF challenge resolution.

    Implementations may use headless browsers, third-party APIs,
    or mock tokens for testing.
    """

    async def resolve(
        self,
        url: str,
        status_code: int,
        response_headers: dict[str, str],
        proxy: ProxyNode | None = None,
    ) -> dict[str, str]:
        """Resolve a CAPTCHA challenge and return additional headers.

        Args:
            url: Challenge URL.
            status_code: HTTP status code of the challenge response.
            response_headers: Headers from the challenge response.
            proxy: Optional proxy for the resolver.

        Returns:
            Dictionary of additional headers (e.g., {"Cookie": "..."}).
        """
        ...


# ────────────────────────────────────────────────────────
#  WAF Detector Port
# ────────────────────────────────────────────────────────


@runtime_checkable
class WAFDetectorPort(Protocol):
    """Contract for Web Application Firewall detection.

    Implementations inspect HTTP responses for WAF signatures.
    """

    def detect(
        self, status_code: int, headers: dict[str, str], body_snippet: str
    ) -> bool:
        """Evaluate whether the response indicates a WAF challenge.

        Args:
            status_code: HTTP status code.
            headers: Response headers dictionary.
            body_snippet: First ~8KB of response body text.

        Returns:
            True if a WAF challenge is detected.
        """
        ...


# ────────────────────────────────────────────────────────
#  Proxy Selector Port
# ────────────────────────────────────────────────────────


@runtime_checkable
class ProxySelectorPort(Protocol):
    """Contract for proxy selection and rotation.

    Implementations manage a pool of proxies with health tracking
    and weighted selection.
    """

    async def select(self, attempt: int) -> ProxyNode | None:
        """Select a healthy proxy for the given attempt number.

        Args:
            attempt: Current attempt number for logging.

        Returns:
            Selected ProxyNode, or None if no proxies available.
        """
        ...

    def mark_dead(self, proxy: ProxyNode | None) -> None:
        """Mark a proxy as dead after persistent failure.

        Args:
            proxy: ProxyNode to mark as dead (no-op if None).
        """
        ...


# ────────────────────────────────────────────────────────
#  Validator Port
# ────────────────────────────────────────────────────────


@runtime_checkable
class ValidatorPort(Protocol):
    """Contract for input validation.

    Implementations enforce security constraints (CWE-22, CWE-20)
    on URLs, filenames, and directories.
    """

    def validate_url(self, url: str) -> None:
        """Validate a URL against security and format constraints.

        Args:
            url: URL to validate.

        Raises:
            ValidationError: If the URL is invalid.
            SecurityError: If the URL uses a forbidden scheme.
        """
        ...

    def validate_filename(self, name: str) -> str:
        """Validate and sanitize a filename.

        Args:
            name: Filename to validate.

        Returns:
            Sanitized basename string.

        Raises:
            ValidationError: If the filename is empty.
            SecurityError: If path traversal or illegal characters detected.
        """
        ...

    def validate_directory(self, directory: str | object) -> object:
        """Validate and create the output directory if needed.

        Args:
            directory: Path to the directory (str or Path-like).

        Returns:
            Validated absolute Path object.

        Raises:
            StorageError: If the directory cannot be created.
        """
        ...
