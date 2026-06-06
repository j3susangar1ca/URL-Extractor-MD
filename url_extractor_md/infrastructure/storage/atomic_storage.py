"""Atomic storage engine — crash-safe file persistence via write-then-rename.

Implements the StoragePort protocol from the domain contracts layer.
Every write operation is atomic: data is first written to a temporary
file (``<target>.tmp``) and then renamed into place with
``os.replace()``, which is guaranteed atomic on POSIX and best-effort
on Windows.  On failure the temporary file is cleaned up so no partial
artefacts remain.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from ...domain.contracts import StoragePort
from ...domain.exceptions import StorageError


class AtomicStorageEngine:
    """Crash-safe file persistence using atomic write-then-rename.

    All public methods are async and delegate the blocking I/O to
    ``asyncio.to_thread`` so the event loop is never blocked.

    Attributes:
        _logger: Logger instance for diagnostics.
    """

    __slots__ = ("_logger",)

    def __init__(self, logger: logging.Logger) -> None:
        """Initialise the atomic storage engine.

        Args:
            logger: Logger instance used for operation diagnostics.
        """
        self._logger = logger

    # ────────────────────────────────────────────────────
    #  Private helpers
    # ────────────────────────────────────────────────────

    def _atomic_write(self, target: Path, payload: bytes | str) -> Path:
        """Write *payload* atomically to *target* via temp-file + rename.

        The sequence is:
            1. Write to ``<target>.tmp``.
            2. ``os.replace()`` the temp file to *target* (atomic on POSIX).
            3. On any failure, attempt to remove the temp file.

        Args:
            target: Destination file path.
            payload: Content to write — ``bytes`` writes in binary mode,
                ``str`` writes in text mode (UTF-8).

        Returns:
            The resolved absolute path of the written file.

        Raises:
            StorageError: If the write or rename fails.
        """
        target = target.resolve()
        tmp_path = target.with_suffix(target.suffix + ".tmp")

        try:
            # Ensure parent directory exists.
            target.parent.mkdir(parents=True, exist_ok=True)

            if isinstance(payload, bytes):
                tmp_path.write_bytes(payload)
            else:
                tmp_path.write_text(payload, encoding="utf-8")

            os.replace(tmp_path, target)
        except BaseException:
            # Clean up the temp file on *any* failure.
            try:
                tmp_path.unlink(missing_ok=True)
            except BaseException:
                pass  # Best-effort cleanup; original error is more important.
            raise

        self._logger.debug("Atomic write succeeded: %s", target)
        return target

    # ────────────────────────────────────────────────────
    #  Public API — implements StoragePort
    # ────────────────────────────────────────────────────

    async def save_markdown(self, key: object, content: str) -> str:
        """Save Markdown content atomically.

        Args:
            key: Path-like object identifying the target file.
            content: Complete Markdown string to persist.

        Returns:
            Absolute path of the saved file as a string.

        Raises:
            StorageError: If the atomic write fails.
        """
        target = Path(str(key))
        self._logger.info("Saving markdown to %s", target)

        try:
            result = await asyncio.to_thread(self._atomic_write, target, content)
        except StorageError:
            raise
        except Exception as exc:
            msg = f"Failed to save markdown to {target}: {exc}"
            self._logger.error(msg)
            raise StorageError(msg, code="BE-100") from exc

        return str(result)

    async def save_raw(self, key: object, payload: bytes) -> str:
        """Save raw bytes atomically.

        Args:
            key: Path-like object identifying the target file.
            payload: Bytes to persist.

        Returns:
            Absolute path of the saved file as a string.

        Raises:
            StorageError: If the atomic write fails.
        """
        target = Path(str(key))
        self._logger.info("Saving raw payload to %s (%d bytes)", target, len(payload))

        try:
            result = await asyncio.to_thread(self._atomic_write, target, payload)
        except StorageError:
            raise
        except Exception as exc:
            msg = f"Failed to save raw payload to {target}: {exc}"
            self._logger.error(msg)
            raise StorageError(msg, code="BE-101") from exc

        return str(result)

    async def save_structured(self, key: object, data: dict[str, object]) -> str:
        """Save structured data as JSON atomically.

        The dictionary is serialised to a UTF-8 JSON string with
        indentation for readability and then written via the atomic
        write mechanism.

        Args:
            key: Path-like object identifying the target file.
            data: Dictionary to serialise and persist.

        Returns:
            Absolute path of the saved file as a string.

        Raises:
            StorageError: If serialisation or the atomic write fails.
        """
        target = Path(str(key))
        self._logger.info("Saving structured data to %s", target)

        try:
            json_payload = json.dumps(data, indent=2, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            msg = f"Failed to serialise structured data for {target}: {exc}"
            self._logger.error(msg)
            raise StorageError(msg, code="BE-102") from exc

        try:
            result = await asyncio.to_thread(self._atomic_write, target, json_payload)
        except StorageError:
            raise
        except Exception as exc:
            msg = f"Failed to save structured data to {target}: {exc}"
            self._logger.error(msg)
            raise StorageError(msg, code="BE-103") from exc

        return str(result)
