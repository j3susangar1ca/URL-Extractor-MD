"""Scrape pipeline — application-layer orchestration.

This module contains the ScrapePipeline class which orchestrates the
complete scraping workflow by delegating to injected port implementations.
It contains NO direct I/O, NO network code, and NO third-party imports
at the application layer — all infrastructure concerns are injected
through the domain contracts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from pathlib import Path
from typing import Awaitable, Callable, cast

from ..config import SystemConfig
from ..domain.contracts import (
    CaptchaResolverPort,
    ExtractorPort,
    NetworkPort,
    ProxySelectorPort,
    StoragePort,
    ValidatorPort,
    WAFDetectorPort,
)
from ..domain.exceptions import NetworkError
from ..domain.models import (
    BrowserProfile,
    ContentType,
    ExtractionResult,
    ExtractionStatus,
    PageMetadata,
    ProgressEvent,
    ScrapingResult,
    Stage,
)
from .chunking import MarkdownBuilder, RAGPipeline

# Type alias for progress callbacks (sync or async)
ProgressCallback = Callable[[ProgressEvent], Awaitable[None] | None]

# Default browser profiles pool
_PROFILES_POOL: tuple[BrowserProfile, ...] = (
    BrowserProfile(
        "chrome110",
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
        ),
    ),
    BrowserProfile(
        "edge101",
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/101.0.4951.64 Safari/537.36 "
            "Edge/101.0.1210.53"
        ),
    ),
    BrowserProfile(
        "safari15_3",
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/15.3 Safari/605.1.15"
        ),
    ),
)


class CallbackBridge:
    """Thread-safe bridge between the backend event loop and the GUI.

    Automatically detects whether the callback is a coroutine function
    or a regular function and executes it safely using an asyncio.Lock.

    Attributes:
        _callback: User-provided callback (sync or async).
        _lock: asyncio.Lock for mutual exclusion on emissions.
    """

    def __init__(self, callback: ProgressCallback | None) -> None:
        self._callback = callback
        self._lock = asyncio.Lock()

    async def emit(self, event: ProgressEvent) -> None:
        """Emit a progress event to the callback safely.

        Args:
            event: Progress event to emit.
        """
        if self._callback is None:
            return
        async with self._lock:
            try:
                if asyncio.iscoroutinefunction(self._callback):
                    await self._callback(event)
                else:
                    cast(Callable[[ProgressEvent], None], self._callback)(event)
            except Exception as exc:
                logging.getLogger("CallbackBridge").warning(
                    "Callback emit failed: %s", exc
                )


class ScrapePipeline:
    """Application-layer pipeline orchestrator for web extraction.

    Orchestrates validation, download, WAF detection, CAPTCHA resolution,
    content extraction, Markdown building, and atomic persistence — all
    through injected port implementations following the Dependency
    Inversion Principle.

    Usage (async):

        pipeline = ScrapePipeline(
            network=curl_engine,
            extractor=isolated_extractor,
            storage=atomic_storage,
            ...
        )
        result = await pipeline.execute(
            url="https://example.com",
            output_filename="my_file",
            output_directory="/tmp/downloads",
            callback=my_async_callback,
        )

    Attributes:
        _config: System configuration.
        _logger: Logger instance.
        _network: Network port implementation.
        _extractor: Extractor port implementation.
        _storage: Storage port implementation.
        _captcha_resolver: CAPTCHA resolver port implementation.
        _waf_detector: WAF detector port implementation.
        _proxy_selector: Proxy selector port implementation.
        _validator: Validator port implementation.
        _sem: Concurrency-limiting semaphore.
    """

    def __init__(
        self,
        network: NetworkPort,
        extractor: ExtractorPort,
        storage: StoragePort,
        captcha_resolver: CaptchaResolverPort,
        waf_detector: WAFDetectorPort,
        proxy_selector: ProxySelectorPort,
        validator: ValidatorPort,
        config: SystemConfig | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config or SystemConfig()
        self._logger = logger or self._default_logger()
        self._network = network
        self._extractor = extractor
        self._storage = storage
        self._captcha_resolver = captcha_resolver
        self._waf_detector = waf_detector
        self._proxy_selector = proxy_selector
        self._validator = validator
        self._sem = asyncio.Semaphore(self._config.max_concurrency)

    @staticmethod
    def _default_logger() -> logging.Logger:
        """Create a default stdout logger.

        Returns:
            Configured Logger with DEBUG level.
        """
        import sys

        logger = logging.getLogger("ScrapePipeline")
        logger.setLevel(logging.DEBUG)
        if not logger.handlers:
            h = logging.StreamHandler(sys.stdout)
            h.setFormatter(
                logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
            )
            logger.addHandler(h)
        return logger

    async def execute(
        self,
        url: str,
        output_filename: str,
        output_directory: str | Path,
        callback: ProgressCallback | None = None,
        cancel_event: asyncio.Event | None = None,
        preview_only: bool = False,
    ) -> ScrapingResult:
        """Execute the complete scraping pipeline for a single URL.

        Flow: validation -> download -> WAF detection -> CAPTCHA resolution
        -> RAG extraction -> .md document build -> atomic persistence.

        Args:
            url: Target URL (http/https).
            output_filename: Base filename (without extension).
            output_directory: Output directory (absolute or relative).
            callback: Progress callback (sync or async).
            cancel_event: Optional asyncio.Event for thread-safe cancellation.
            preview_only: If True, only preview metadata without saving.

        Returns:
            Immutable ScrapingResult with output_path, metadata, and status.

        Raises:
            ValidationError: If input pre-conditions fail.
            SecurityError: If path traversal or malicious URL detected.
            StorageError: If atomic disk write fails.
            NetworkError: If network retries are exhausted.
        """
        t0 = time.perf_counter()
        bridge = CallbackBridge(callback)

        # --- 1. Pre-conditions ---
        self._validator.validate_url(url)
        safe_name = self._validator.validate_filename(output_filename)
        dest_dir = self._validator.validate_directory(output_directory)

        md_path = Path(dest_dir) / f"{safe_name}.md"

        # --- 2. Emit INIT ---
        await bridge.emit(
            ProgressEvent(
                stage=Stage.INIT,
                percent=0,
                message=f"Inicializando extraccion: {url}",
                meta={"target": str(md_path)},
            )
        )

        waf_detected = False
        captcha_solved = False
        status_code: int | None = None
        profile_used: str | None = None
        proxy_used: str | None = None

        try:
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError("Extraccion cancelada por el usuario.")

            async with self._sem:
                # --- 3. Stochastic profile selection ---
                profile = random.choice(_PROFILES_POOL)
                profile_used = profile.impersonate_id

                # --- 4. Emit CONNECTING ---
                await bridge.emit(
                    ProgressEvent(
                        stage=Stage.CONNECTING,
                        percent=5,
                        message=f"Conectando a {url} (perfil: {profile_used})...",
                        meta={"profile": profile_used},
                    )
                )

                # --- 5. Proxy selection ---
                proxy = await self._proxy_selector.select(attempt=0)
                if proxy:
                    proxy_used = proxy.url

                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError("Extraccion cancelada por el usuario.")

                # --- 6. Download with backoff ---
                payload_bytes, status_code, encoding, resp_headers = (
                    await self._network.download(
                        url=url,
                        profile=profile,
                        proxy=proxy,
                        extra_headers=None,
                        cancel_event=cancel_event,
                    )
                )

                # --- 7. WAF evaluation ---
                body_snippet = ""
                try:
                    if payload_bytes:
                        body_snippet = payload_bytes[:8192].decode(
                            encoding or "utf-8", errors="replace"
                        ).lower()
                except Exception:
                    pass

                if self._waf_detector.detect(
                    status_code or 0, resp_headers, body_snippet
                ):
                    waf_detected = True
                    await bridge.emit(
                        ProgressEvent(
                            stage=Stage.WAF_DETECTED,
                            percent=55,
                            message="Desafio WAF/CAPTCHA detectado. Transicionando a resolucion...",
                            meta={"status_code": status_code},
                        )
                    )

                    if cancel_event is not None and cancel_event.is_set():
                        raise asyncio.CancelledError(
                            "Extraccion cancelada por el usuario."
                        )

                    # --- 8. Resolve CAPTCHA ---
                    await bridge.emit(
                        ProgressEvent(
                            stage=Stage.CAPTCHA_SOLVING,
                            percent=58,
                            message="Resolviendo CAPTCHA...",
                        )
                    )
                    tokens = await self._captcha_resolver.resolve(
                        url=url,
                        status_code=status_code or 0,
                        response_headers=resp_headers,
                        proxy=proxy,
                    )
                    captcha_solved = True
                    await bridge.emit(
                        ProgressEvent(
                            stage=Stage.CAPTCHA_SOLVING,
                            percent=60,
                            message="CAPTCHA resuelto. Reintentando con tokens...",
                        )
                    )

                    if cancel_event is not None and cancel_event.is_set():
                        raise asyncio.CancelledError(
                            "Extraccion cancelada por el usuario."
                        )

                    # --- 9. Re-download with tokens ---
                    payload_bytes, status_code, encoding, resp_headers = (
                        await self._network.download(
                            url=url,
                            profile=profile,
                            proxy=proxy,
                            extra_headers=tokens,
                            cancel_event=cancel_event,
                        )
                    )

                if not (200 <= (status_code or 0) < 300):
                    elapsed = (time.perf_counter() - t0) * 1000
                    await bridge.emit(
                        ProgressEvent(
                            stage=Stage.ERROR,
                            percent=100,
                            message=f"Error HTTP {status_code} en {url}",
                            meta={"status_code": status_code},
                        )
                    )
                    return ScrapingResult(
                        success=False,
                        url=url,
                        status_code=status_code,
                        proxy_used=proxy_used,
                        profile_used=profile_used,
                        waf_detected=waf_detected,
                        captcha_solved=captcha_solved,
                        elapsed_ms=elapsed,
                        error=f"HTTP {status_code}",
                    )

                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError(
                        "Extraccion cancelada por el usuario."
                    )

                # --- 10. Isolated RAG extraction ---
                await bridge.emit(
                    ProgressEvent(
                        stage=Stage.PARSING,
                        percent=70,
                        message="Limpiando ruido y aislando contenido principal...",
                    )
                )

                t_extract_start = time.perf_counter()
                extracted = await self._extractor.extract(
                    payload_bytes, url, encoding=encoding or "utf-8"
                )
                extract_duration_ms = (time.perf_counter() - t_extract_start) * 1000

                # --- 11. Build ExtractionResult ---
                extracted_meta: dict[str, object] = extracted.get(
                    "page_metadata", {}
                )
                try:
                    content_type = ContentType(
                        str(extracted_meta.get("content_type", "unknown"))
                    )
                except ValueError:
                    content_type = ContentType.UNKNOWN

                metadata = PageMetadata(
                    url=url,
                    title=str(extracted_meta.get("title", "")),
                    author=str(extracted_meta.get("author", "")),
                    site_name=str(extracted_meta.get("site_name", "")),
                    description=str(extracted_meta.get("description", "")),
                    content_type=content_type,
                    language=str(extracted_meta.get("language", "")),
                    published_date=str(extracted_meta.get("published_date", "")),
                    keywords=list(extracted_meta.get("keywords", [])),  # type: ignore[arg-type]
                    canonical_url=str(extracted_meta.get("canonical_url", "")),
                    og_image=str(extracted_meta.get("og_image", "")),
                )

                clean_md = str(extracted.get("clean_markdown", ""))
                word_count_val = int(extracted.get("word_count", 0))
                paragraph_count = int(extracted.get("paragraph_count", 0))
                links_found = list(extracted.get("links_found", []))  # type: ignore[arg-type]
                images_found = list(extracted.get("images_found", []))  # type: ignore[arg-type]
                errors_list = list(extracted.get("errors", []))  # type: ignore[arg-type]

                status = ExtractionStatus.SUCCESS
                if errors_list:
                    status = ExtractionStatus.FAILED
                elif word_count_val < 50:
                    status = ExtractionStatus.PARTIAL

                extraction_result = ExtractionResult(
                    metadata=metadata,
                    clean_markdown=clean_md,
                    word_count=word_count_val,
                    char_count=len(clean_md),
                    paragraph_count=paragraph_count,
                    links_found=links_found,
                    images_found=images_found,
                    status=status,
                    errors=errors_list,
                    warnings=[],
                    extraction_time_ms=round(extract_duration_ms, 2),
                )

                word_count = extraction_result.word_count
                content_type_val = extraction_result.metadata.content_type.value

                # --- Preview mode ---
                if preview_only:
                    preview = RAGPipeline.preview(extraction_result)
                    preview_card = (
                        "\n=== PREVISUALIZACION DE EXTRACCION ===\n"
                        f"Titulo:        {preview.title}\n"
                        f"Autor:         {preview.author or 'N/A'}\n"
                        f"Sitio:         {preview.site_name or 'N/A'}\n"
                        f"Descripcion:   {preview.description or 'N/A'}\n"
                        f"Tipo Content:  {preview.content_type}\n"
                        f"Idioma:        {preview.language or 'N/A'}\n"
                        f"Publicado:     {preview.published_date or 'N/A'}\n"
                        f"Keywords:      {', '.join(preview.keywords) if preview.keywords else 'N/A'}\n"
                        f"Palabras Est.: {preview.estimated_word_count}\n"
                        f"Chunks Proy.:  {preview.projected_chunks}\n"
                        "======================================="
                    )
                    self._logger.info(preview_card)
                    elapsed = (time.perf_counter() - t0) * 1000
                    await bridge.emit(
                        ProgressEvent(
                            stage=Stage.COMPLETED,
                            percent=100,
                            message="Previsualizacion completada.",
                        )
                    )
                    return ScrapingResult(
                        success=True,
                        url=url,
                        status_code=status_code,
                        proxy_used=proxy_used,
                        profile_used=profile_used,
                        waf_detected=waf_detected,
                        captcha_solved=captcha_solved,
                        elapsed_ms=elapsed,
                        word_count=word_count,
                        content_type=content_type_val,
                    )

                # --- 12. Build .md document ---
                await bridge.emit(
                    ProgressEvent(
                        stage=Stage.SAVING,
                        percent=85,
                        message="Guardando documento .md...",
                    )
                )

                builder = MarkdownBuilder()
                md_content = builder.build_document(
                    extraction_result, user_filename=safe_name
                )

                # --- 13. Atomic persistence ---
                output_path = await self._storage.save_markdown(md_path, md_content)

                elapsed = (time.perf_counter() - t0) * 1000
                await bridge.emit(
                    ProgressEvent(
                        stage=Stage.COMPLETED,
                        percent=100,
                        message=f"Extraccion completada: {output_path}",
                    )
                )
                return ScrapingResult(
                    success=True,
                    url=url,
                    output_path=output_path,
                    status_code=status_code,
                    proxy_used=proxy_used,
                    profile_used=profile_used,
                    waf_detected=waf_detected,
                    captcha_solved=captcha_solved,
                    elapsed_ms=elapsed,
                    word_count=word_count,
                    content_type=content_type_val,
                )

        except asyncio.CancelledError:
            elapsed = (time.perf_counter() - t0) * 1000
            return ScrapingResult(
                success=False,
                url=url,
                status_code=status_code,
                proxy_used=proxy_used,
                profile_used=profile_used,
                elapsed_ms=elapsed,
                error="Cancelado por el usuario",
            )
        except NetworkError:
            elapsed = (time.perf_counter() - t0) * 1000
            raise
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            await bridge.emit(
                ProgressEvent(
                    stage=Stage.ERROR,
                    percent=100,
                    message=f"Error: {exc}",
                )
            )
            return ScrapingResult(
                success=False,
                url=url,
                status_code=status_code,
                proxy_used=proxy_used,
                profile_used=profile_used,
                elapsed_ms=elapsed,
                error=str(exc),
            )
