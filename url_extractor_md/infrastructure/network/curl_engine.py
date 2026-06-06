"""curl_cffi network adapter implementing the NetworkPort protocol.

This module provides ``CurlCffiEngine``, a production-grade HTTP client that
wraps *curl_cffi*'s ``AsyncSession`` with TLS fingerprint impersonation,
streaming downloads with progress reporting, session pooling per browser
profile, and proper resource cleanup.

The engine performs a **single download attempt** per call — retry orchestration
is the responsibility of the pipeline layer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable

from ...config import SystemConfig
from ...domain.exceptions import NetworkError
from ...domain.models import BrowserProfile, ProgressEvent, ProxyNode, Stage

# ---------------------------------------------------------------------------
#  Late / conditional import of curl_cffi
# ---------------------------------------------------------------------------
try:
    from curl_cffi.requests import AsyncSession, RequestsError
except ImportError as _exc:  # pragma: no cover
    raise RuntimeError(
        "curl_cffi is required for CurlCffiEngine but is not installed.  "
        "Install it with:  pip install curl_cffi"
    ) from _exc

# Type alias for the optional progress callback accepted by the engine.
ProgressCallback = Callable[[ProgressEvent], Awaitable[None] | None]


class CurlCffiEngine:
    """HTTP client backed by *curl_cffi* with TLS fingerprinting.

    Implements the :class:`NetworkPort` protocol from the domain layer.
    Sessions are lazily created and pooled per ``impersonate_id`` so that
    TLS state (JA3 fingerprints, HTTP/2 settings) is reused across requests
    that share the same browser profile.

    Attributes:
        _config: System configuration (timeouts, chunk size, headers).
        _logger: Logger instance for diagnostic output.
        _sessions: Mapping of impersonate_id → AsyncSession.
        _progress_callback: Optional callback for DOWNLOADING progress events.
        _closed: Whether the engine has been shut down.
    """

    __slots__ = (
        "_config",
        "_logger",
        "_sessions",
        "_progress_callback",
        "_closed",
    )

    def __init__(
        self,
        config: SystemConfig,
        logger: logging.Logger | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        """Initialize the curl_cffi network engine.

        Args:
            config: System configuration with timeout, chunk-size and
                base-header settings.
            logger: Optional logger; a module-level default is created when
                *None*.
            progress_callback: Optional callback invoked during streaming
                downloads to report :class:`ProgressEvent` objects at the
                ``DOWNLOADING`` stage.
        """
        self._config = config
        self._logger = logger or logging.getLogger(__name__)
        self._sessions: dict[str, tuple[AsyncSession, asyncio.AbstractEventLoop]] = {}
        self._progress_callback = progress_callback
        self._closed = False

    # ------------------------------------------------------------------
    #  Session management
    # ------------------------------------------------------------------

    def _get_or_create_session(self, impersonate_id: str) -> AsyncSession:
        """Return a pooled ``AsyncSession`` for the given impersonate profile.

        Sessions are created lazily on first use and reused for subsequent
        requests sharing the same TLS fingerprint.

        Args:
            impersonate_id: curl_cffi TLS profile identifier
                (e.g. ``"chrome110"``).

        Returns:
            An ``AsyncSession`` configured with the requested fingerprint.
        """
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        cached = self._sessions.get(impersonate_id)
        if cached is not None:
            if isinstance(cached, tuple) and len(cached) == 2:
                cached_session, cached_loop = cached
            else:
                cached_session = cached
                cached_loop = current_loop

            if cached_loop is current_loop:
                return cached_session
            else:
                self._logger.debug(
                    "Cached session for profile '%s' is on a different event loop. Creating a new one.",
                    impersonate_id,
                )
                if cached_loop and cached_loop.is_running():
                    try:
                        cached_loop.call_soon_threadsafe(
                            lambda: asyncio.create_task(cached_session.close())
                        )
                    except Exception:
                        pass
                self._sessions.pop(impersonate_id, None)

        session = AsyncSession(impersonate=impersonate_id)
        self._sessions[impersonate_id] = (session, current_loop)
        self._logger.debug(
            "Created new AsyncSession for profile '%s'.", impersonate_id
        )
        return session

    # ------------------------------------------------------------------
    #  Header assembly
    # ------------------------------------------------------------------

    @staticmethod
    def _build_headers(
        config: SystemConfig,
        profile: BrowserProfile,
        extra_headers: dict[str, str] | None,
    ) -> dict[str, str]:
        """Assemble the request header dictionary.

        Merges (in precedence order):
        1. ``extra_headers`` — highest priority (e.g. CAPTCHA tokens).
        2. ``profile.user_agent`` — overrides any UA from base headers.
        3. ``config.base_headers`` — lowest priority defaults.

        Args:
            config: System configuration providing base headers.
            profile: Browser profile providing the User-Agent.
            extra_headers: Optional caller-supplied headers.

        Returns:
            Merged header dictionary ready for the HTTP request.
        """
        headers: dict[str, str] = dict(config.base_headers)
        headers["User-Agent"] = profile.user_agent
        if extra_headers:
            headers.update(extra_headers)
        return headers

    # ------------------------------------------------------------------
    #  Progress emission helper
    # ------------------------------------------------------------------

    async def _emit_progress(self, event: ProgressEvent) -> None:
        """Safely emit a progress event through the callback.

        Catches and logs exceptions so that a failing callback never
        interrupts the download.

        Args:
            event: Progress event to emit.
        """
        if self._progress_callback is None:
            return
        try:
            result = self._progress_callback(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:  # pragma: no cover
            self._logger.warning("Progress callback raised: %s", exc)

    # ------------------------------------------------------------------
    #  Cancel-event helper
    # ------------------------------------------------------------------

    @staticmethod
    def _is_cancelled(cancel_event: object | None) -> bool:
        """Return *True* if *cancel_event* is set.

        Accepts any object with an ``is_set()`` method (e.g.
        ``asyncio.Event``); returns *False* for *None* or objects
        lacking the method.

        Args:
            cancel_event: Optional cancellation signal.

        Returns:
            Whether cancellation has been requested.
        """
        if cancel_event is None:
            return False
        if hasattr(cancel_event, "is_set") and callable(cancel_event.is_set):
            return bool(cancel_event.is_set())
        return False

    # ------------------------------------------------------------------
    #  NetworkPort: download
    # ------------------------------------------------------------------

    async def download(
        self,
        url: str,
        profile: BrowserProfile,
        proxy: ProxyNode | None,
        extra_headers: dict[str, str] | None,
        cancel_event: object | None,
    ) -> tuple[bytes, int, str, dict[str, str]]:
        """Download *url* in a single streaming attempt.

        Performs one HTTP GET request with TLS impersonation, streaming
        the response body in chunks.  Progress events are emitted at the
        ``DOWNLOADING`` stage for each chunk received.

        Args:
            url: Target URL to download.
            profile: Browser profile for TLS/UA configuration.
            proxy: Optional proxy node for egress routing.
            extra_headers: Additional headers (e.g. CAPTCHA cookies).
            cancel_event: Optional ``asyncio.Event`` for cancellation.

        Returns:
            Tuple of ``(raw_bytes, status_code, detected_encoding,
            response_headers)``.

        Raises:
            NetworkError: On curl-level failures (DNS, TLS, timeout,
                connection refused) or when cancelled.
        """
        if self._closed:
            raise NetworkError("CurlCffiEngine is closed.", code="BE-301")

        if self._is_cancelled(cancel_event):
            raise NetworkError("Download cancelled before request.", code="BE-302")

        session = self._get_or_create_session(profile.impersonate_id)
        headers = self._build_headers(self._config, profile, extra_headers)
        proxy_url: str | None = proxy.url if proxy else None

        try:
            resp = await session.get(
                url,
                headers=headers,
                proxy=proxy_url,
                timeout=self._config.request_timeout_sec,
                allow_redirects=True,
                stream=True,
            )
            status_code: int = resp.status_code

            # --- Encoding detection ---
            encoding = self._detect_encoding(resp)

            # --- Response headers ---
            resp_headers = self._extract_headers(resp)

            # --- Streaming body download ---
            chunks: list[bytes] = []
            total_received = 0

            async for chunk in resp.aiter_content(
                chunk_size=self._config.chunk_size_bytes
            ):
                if self._is_cancelled(cancel_event):
                    raise NetworkError(
                        "Download cancelled during streaming.", code="BE-302"
                    )

                if not chunk:
                    continue

                chunks.append(chunk)
                total_received += len(chunk)

                # Emit progress for each significant chunk
                await self._emit_progress(
                    ProgressEvent(
                        stage=Stage.DOWNLOADING,
                        percent=0,  # percent is computed by the pipeline
                        message=f"Descargando… {total_received:,} bytes",
                        meta={
                            "bytes_received": total_received,
                            "chunk_size": len(chunk),
                            "url": url,
                        },
                    )
                )

            payload = b"".join(chunks)
            self._logger.debug(
                "Downloaded %s — %d bytes, status %d.",
                url,
                total_received,
                status_code,
            )
            return payload, status_code, encoding, resp_headers

        except NetworkError:
            raise
        except RequestsError as exc:
            self._logger.warning(
                "curl_cffi error downloading %s: %s", url, exc
            )
            raise NetworkError(
                f"Network failure for {url}: {exc}", code="BE-300"
            ) from exc
        except asyncio.CancelledError:
            raise NetworkError(
                "Download was cancelled (CancelledError).", code="BE-302"
            )
        except Exception as exc:
            self._logger.exception(
                "Unexpected error downloading %s", url
            )
            raise NetworkError(
                f"Unexpected network error for {url}: {exc}", code="BE-300"
            ) from exc

    # ------------------------------------------------------------------
    #  Encoding detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_encoding(resp: object) -> str:
        """Best-effort detection of the response character encoding.

        Inspects the ``Content-Type`` header for a ``charset=`` directive
        and falls back to ``"utf-8"``.

        Args:
            resp: The curl_cffi response object.

        Returns:
            Detected or default encoding string.
        """
        # curl_cffi responses expose a .headers attribute
        content_type: str = ""
        if hasattr(resp, "headers") and resp.headers is not None:
            content_type = resp.headers.get("Content-Type", "")
            content_type = str(content_type)

        if "charset=" in content_type:
            charset_part = content_type.split("charset=")[-1].split(";")[0]
            return charset_part.strip().strip('"').strip("'") or "utf-8"

        # curl_cffi may also provide .encoding
        if hasattr(resp, "encoding") and resp.encoding:
            return str(resp.encoding)

        return "utf-8"

    # ------------------------------------------------------------------
    #  Header extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_headers(resp: object) -> dict[str, str]:
        """Extract response headers as a plain ``dict[str, str]``.

        Handles both header-case conventions (title-cased by curl_cffi
        or lower-cased by some wrappers).

        Args:
            resp: The curl_cffi response object.

        Returns:
            Dictionary of header name → value strings.
        """
        result: dict[str, str] = {}
        if hasattr(resp, "headers") and resp.headers is not None:
            try:
                for key, value in resp.headers.items():
                    result[str(key)] = str(value)
            except Exception:  # pragma: no cover
                # Fallback: try multi-dict interface
                try:
                    for key in resp.headers.keys():
                        result[str(key)] = str(resp.headers.get(key, ""))
                except Exception:
                    pass
        return result

    # ------------------------------------------------------------------
    #  NetworkPort: close
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Clean up all pooled sessions and release resources.

        After calling ``close()`` the engine must not be used for further
        downloads.  This method is idempotent.
        """
        if self._closed:
            return

        self._closed = True

        for profile_id, cached in self._sessions.items():
            if isinstance(cached, tuple) and len(cached) == 2:
                session, _ = cached
            else:
                session = cached

            try:
                await session.close()
                self._logger.debug(
                    "Closed AsyncSession for profile '%s'.", profile_id
                )
            except Exception as exc:  # pragma: no cover
                self._logger.warning(
                    "Error closing session '%s': %s", profile_id, exc
                )

        self._sessions.clear()
