"""Isolated extractor — runs parse_html_unified() in a ProcessPoolExecutor.

This adapter satisfies the ExtractorPort contract by delegating the
CPU-intensive HTML parsing to a separate process.  This avoids GIL
contention and isolates crashes (e.g., segfaults in C extensions).

The worker process initialiser ignores SIGINT so that the parent
process retains control over graceful shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from concurrent.futures import ProcessPoolExecutor

from ...domain.contracts import ExtractorPort
from .unified_parser import parse_html_unified


def _init_worker() -> None:
    """Ignore SIGINT in worker processes so the parent handles shutdown.

    Without this, Ctrl-C in the parent process would also deliver SIGINT
    to each worker, causing messy tracebacks and potential deadlocks in
    the ProcessPoolExecutor.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)


class IsolatedExtractor:
    """Run parse_html_unified in a ProcessPoolExecutor.

    Implements the ExtractorPort protocol from domain.contracts.
    The extraction function executes in a forked subprocess for GIL
    avoidance and crash isolation.

    Attributes:
        _executor: ProcessPoolExecutor used to offload parsing.
        _logger: Module-level logger instance.
    """

    __slots__ = ("_executor", "_logger")

    def __init__(self, max_workers: int | None = None) -> None:
        """Create the executor pool with SIGINT-ignoring workers.

        Args:
            max_workers: Maximum number of worker processes.
                Defaults to ``os.cpu_count()`` or 2.
        """
        workers = max_workers or os.cpu_count() or 2
        self._executor = ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_worker,
        )
        self._logger = logging.getLogger(__name__)

    # ────────────────────────────────────────────────────
    #  ExtractorPort implementation
    # ────────────────────────────────────────────────────

    async def extract(
        self,
        payload: bytes,
        source_url: str,
        encoding: str = "utf-8",
    ) -> dict[str, object]:
        """Extract structured content from raw HTML bytes in an isolated process.

        Args:
            payload: Raw HTML bytes downloaded from the source URL.
            source_url: Source URL used for metadata resolution and
                relative-link expansion.
            encoding: Character encoding for decoding the payload.

        Returns:
            Pickle-safe dictionary with extraction results.  Keys match
            the contract expected by ScrapePipeline:
                title, meta, links, headings, clean_markdown,
                page_metadata, word_count, paragraph_count,
                links_found, images_found, errors.

        Raises:
            RuntimeError: If the subprocess fails or is cancelled.
        """
        loop = asyncio.get_running_loop()

        try:
            result = await loop.run_in_executor(
                self._executor,
                parse_html_unified,
                payload,
                source_url,
                encoding,
            )
        except asyncio.CancelledError:
            self._logger.warning("Extraction cancelled for %s", source_url)
            raise
        except Exception as exc:
            self._logger.error(
                "Isolated extraction failed for %s: %s", source_url, exc
            )
            return _error_dict(source_url, str(exc))

        return result

    # ────────────────────────────────────────────────────
    #  Lifecycle
    # ────────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Orderly shutdown of the process pool.

        Waits for in-flight extractions to complete before terminating
        worker processes.
        """
        self._logger.info("Shutting down IsolatedExecutor process pool")
        self._executor.shutdown(wait=True)


# ────────────────────────────────────────────────────────
#  Module-level helpers
# ────────────────────────────────────────────────────────


def _error_dict(source_url: str, error_msg: str) -> dict[str, object]:
    """Build a minimal error result dictionary.

    Args:
        source_url: Source URL that failed.
        error_msg: Error description.

    Returns:
        Dictionary with empty values and the error recorded.
    """
    return {
        "title": "",
        "meta": {},
        "links": [],
        "headings": [],
        "clean_markdown": "",
        "page_metadata": {
            "title": "",
            "author": "",
            "site_name": "",
            "description": "",
            "content_type": "unknown",
            "language": "",
            "published_date": "",
            "keywords": [],
            "canonical_url": "",
            "og_image": "",
        },
        "word_count": 0,
        "paragraph_count": 0,
        "links_found": [],
        "images_found": [],
        "errors": [error_msg],
    }
