"""System configuration — Single Source of Truth.

Immutable configuration dataclass following PEP 585/604 style.
All operational parameters are centralized here and injected
into the pipeline via dependency inversion.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class SystemConfig:
    """ISO-compliant backend configuration.

    Attributes:
        base_headers: Default HTTP headers (immutable via MappingProxyType).
        max_concurrency: Maximum number of concurrent operations.
        request_timeout_sec: Per-request timeout in seconds.
        max_retries: Maximum retries before declaring failure.
        backoff_base_ms: Exponential backoff base in milliseconds.
        backoff_max_ms: Exponential backoff ceiling in milliseconds.
        chunk_size_bytes: Download chunk size for progress reporting.
        browser_profiles: Tuple of available browser profiles.
        extract_workers: Number of worker processes for isolated extraction.
    """

    base_headers: MappingProxyType = field(
        default_factory=lambda: MappingProxyType(
            {
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;"
                    "q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            }
        )
    )
    max_concurrency: int = min(32, (os.cpu_count() or 1) + 4)
    request_timeout_sec: float = 15.0
    max_retries: int = 4
    backoff_base_ms: float = 500.0
    backoff_max_ms: float = 8000.0
    chunk_size_bytes: int = 8192
    extract_workers: int = os.cpu_count() or 2
