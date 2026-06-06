"""Exception hierarchy — ISO 25010 compliant error classification.

All exceptions inherit from BackendError which carries an ISO-style
error code for classification and diagnostic purposes.  This hierarchy
maps directly to the CWE references used in the validation layer.
"""

from __future__ import annotations


class BackendError(Exception):
    """Base class for backend failures.

    Attributes:
        message: Human-readable error description.
        code: Categorized error code (e.g., "BE-000").
    """

    def __init__(self, message: str, code: str = "BE-000") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ValidationError(BackendError):
    """Contract pre-condition violation (invalid input).

    Raised when input arguments fail typed or format restrictions
    required by the API contract.
    """

    pass


class StorageError(BackendError):
    """Atomic persistence failure (disk I/O).

    Wraps errors from atomic write operations, insufficient permissions,
    or file-system failures.
    """

    pass


class NetworkError(BackendError):
    """Transient or permanent network failure.

    Raised after exhausting configured retries or encountering
    DNS, TLS, timeout, or connection-refused errors.
    """

    pass


class SecurityError(BackendError):
    """Security violation (path traversal, malicious URL, etc.).

    Prevents CWE-22 (Path Traversal), CWE-20 (Input Validation),
    and other attack vectors against the backend.
    """

    pass


class ExtractionError(BackendError):
    """Content extraction pipeline failure.

    Raised when the HTML parsing, cleaning, or Markdown conversion
    fails in a non-recoverable way.
    """

    pass
