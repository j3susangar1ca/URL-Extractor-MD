"""Input validator — security-focused validation for URLs, filenames, and directories.

Implements the ValidatorPort protocol from the domain contracts layer.
All checks enforce CWE-22 (Path Traversal) and CWE-20 (Input Validation)
constraints to prevent common attack vectors.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from ...domain.contracts import ValidatorPort
from ...domain.exceptions import SecurityError, StorageError, ValidationError


class InputValidator:
    """Security-focused input validation adapter.

    Validates URLs against forbidden schemes and length limits,
    sanitises filenames to prevent path-traversal attacks, and
    ensures output directories are safe and writable.

    Class Attributes:
        _SAFE_FILENAME_RE: Regex allowing only word characters, dots,
            hyphens, spaces, and underscores in filenames.
        _MAX_URL_LEN: Maximum permitted URL length (2 048 characters).
        _FORBIDDEN_SCHEMES: URL schemes that are never allowed.
    """

    __slots__ = ()

    _SAFE_FILENAME_RE: re.Pattern[str] = re.compile(r"^[\w\-. ]+$")
    _MAX_URL_LEN: int = 2048
    _FORBIDDEN_SCHEMES: set[str] = {"file", "ftp", "javascript", "data"}

    # ────────────────────────────────────────────────────
    #  URL validation
    # ────────────────────────────────────────────────────

    def validate_url(self, url: str) -> None:
        """Validate a URL against security and format constraints.

        Checks, in order:
            1. Non-empty string.
            2. Length does not exceed ``_MAX_URL_LEN``.
            3. Scheme is not in ``_FORBIDDEN_SCHEMES``.
            4. URL has a valid network location (``netloc``).

        Args:
            url: URL to validate.

        Raises:
            ValidationError: If the URL is empty or exceeds the
                maximum allowed length.
            SecurityError: If the URL uses a forbidden scheme.
            ValidationError: If the URL is missing a network location.
        """
        if not url or not url.strip():
            raise ValidationError("URL must not be empty", code="BE-200")

        if len(url) > self._MAX_URL_LEN:
            raise ValidationError(
                f"URL exceeds maximum length of {self._MAX_URL_LEN} characters "
                f"(got {len(url)})",
                code="BE-201",
            )

        parsed = urlparse(url)
        scheme = parsed.scheme.lower()

        if scheme in self._FORBIDDEN_SCHEMES:
            raise SecurityError(
                f"URL scheme '{scheme}' is not allowed "
                f"(forbidden: {', '.join(sorted(self._FORBIDDEN_SCHEMES))})",
                code="BE-210",
            )

        if not parsed.netloc:
            raise ValidationError(
                f"URL must include a valid network location: {url}",
                code="BE-202",
            )

    # ────────────────────────────────────────────────────
    #  Filename validation
    # ────────────────────────────────────────────────────

    def validate_filename(self, name: str) -> str:
        """Validate and sanitise a filename.

        Checks, in order:
            1. Non-empty string.
            2. Basename matches the original (no path traversal — CWE-22).
            3. Characters match ``_SAFE_FILENAME_RE``.

        Args:
            name: Filename to validate.

        Returns:
            Sanitised basename string.

        Raises:
            ValidationError: If the filename is empty.
            SecurityError: If a path-traversal attempt is detected or
                the filename contains illegal characters.
        """
        if not name or not name.strip():
            raise ValidationError("Filename must not be empty", code="BE-220")

        basename = os.path.basename(name)

        # Path-traversal check: basename must equal the original input.
        if basename != name:
            raise SecurityError(
                f"Path traversal detected in filename: {name!r} "
                f"(resolved to {basename!r})",
                code="BE-221",
            )

        # Additional POSIX-level check for sneaky traversals like "..".
        if PurePosixPath(basename).name != basename:
            raise SecurityError(
                f"Path traversal detected in filename: {name!r}",
                code="BE-222",
            )

        if not self._SAFE_FILENAME_RE.match(basename):
            raise SecurityError(
                f"Filename contains illegal characters: {name!r} "
                f"(only alphanumeric, underscore, hyphen, dot, and space allowed)",
                code="BE-223",
            )

        return basename

    # ────────────────────────────────────────────────────
    #  Directory validation
    # ────────────────────────────────────────────────────

    def validate_directory(self, directory: str | Path) -> Path:
        """Validate and create the output directory if needed.

        Resolves the path to an absolute path and creates it
        (including parents) if it does not exist.

        Args:
            directory: Path to the directory (str or Path-like).

        Returns:
            Validated absolute ``Path`` object.

        Raises:
            StorageError: If the directory cannot be created.
        """
        dir_path = Path(str(directory)).resolve()

        try:
            dir_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(
                f"Failed to create directory {dir_path}: {exc}",
                code="BE-230",
            ) from exc

        return dir_path
