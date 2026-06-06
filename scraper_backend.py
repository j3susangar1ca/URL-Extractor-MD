#!/usr/bin/env python3
"""
================================================================================
Elite Scraper Backend v5.0 — Unified Network Engine + RAG Extraction Pipeline
================================================================================
Arquitectura: ISO 25010 (Mantenibilidad, Seguridad, Fiabilidad)
Perfil:       Backend de misión crítica para aplicaciones de extracción web
Entorno:      GNU/Linux x86_64, Python 3.12+, C-backend nativo

Diseño:
    • API unificada ``scrape_single()`` para consumo por GUI/Frameworks
    • Sistema de callbacks tipados con progreso granular (0-100%)
    • Thread-safe para integración con Qt/Tkinter/Gtk vía asyncio bridges
    • Validación de contratos de entrada (pre-condiciones ISO)
    • Sanitización de paths (prevención de path traversal CWE-22)
    • Resource RAII garantizado (AsyncExitStack + context managers)
    • Integración con ``rag_extract`` para parsing, limpieza y estructuración
    • Salida unificada como .md con YAML frontmatter (NO .html + .json)

Dependencias:
    pip install curl_cffi          (OBLIGATORIO — motor de red con TLS fingerprint)
    pip install selectolax         (recomendado, parsing C-extension)
    pip install playwright         (opcional, resolución CAPTCHA headless)
    pip install uvloop             (opcional, loop POSIX acelerado)

Autor: Principal QA / Arquitecto de Software de Misión Crítica
Versión: 5.0.0 (ISO-25010 Compliant — Unified RAG Pipeline)
================================================================================
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import signal
import sys
import time
import zlib
from abc import ABC, abstractmethod
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from pathlib import Path
from types import MappingProxyType
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Final,
    List,
    Literal,
    Optional,
    Set,
    Tuple,
    Union,
    cast,
)
from urllib.parse import urlparse

# =============================================================================
# 0. RAG EXTRACT INTEGRATION
# =============================================================================
from rag_extract import (
    parse_html_unified,
    MarkdownBuilder,
    ContentExtractor,
    PageMetadata,
    ExtractionResult,
    ContentType,
    ExtractionStatus,
    SlugGenerator,
    StorageResult,
    RAGPipeline,
    PreviewResult,
)

# =============================================================================
# 1. DEPENDENCY GATES (Graceful Degradation)
# =============================================================================

try:
    from curl_cffi.requests import AsyncSession, RequestsError, Response
except ImportError as _imp_err:
    raise RuntimeError(
        "curl_cffi es obligatorio para el motor de red. "
        "Instale con: pip install curl_cffi"
    ) from _imp_err

try:
    from selectolax.parser import HTMLParser as _HTMLParser

    _HAS_SELECTOLAX: bool = True
except ImportError:
    _HAS_SELECTOLAX = False
    _HTMLParser = None  # type: ignore[assignment]

try:
    from playwright.async_api import async_playwright

    _HAS_PLAYWRIGHT: bool = True
except ImportError:
    _HAS_PLAYWRIGHT = False

# =============================================================================
# 2. EXCEPTION HIERARCHY (ISO 25010 — Fiabilidad)
# =============================================================================


class BackendError(Exception):
    """Clase base para fallos del backend.

    Incluye código de error ISO-style para clasificación y diagnóstico.

    Attributes:
        message: Descripción human-readable del fallo.
        code: Código de error categorizado (e.g., ``"BE-000"``).
    """

    def __init__(self, message: str, code: str = "BE-000") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ValidationError(BackendError):
    """Violación de pre-condición de contrato (entrada inválida).

    Se lanza cuando los argumentos de entrada no cumplen las restricciones
    tipadas o de formato requeridas por la API.
    """

    pass


class StorageError(BackendError):
    """Fallo en persistencia atómica (I/O de disco).

    Envuelve errores de escritura atómica, permisos insuficientes,
    o fallos en el sistema de archivos.
    """

    pass


class NetworkError(BackendError):
    """Fallo transitorio o permanente de red.

    Se lanza tras agotar los reintentos configurados o ante errores
    de DNS, TLS, timeout o conexión rechazada.
    """

    pass


class SecurityError(BackendError):
    """Violación de seguridad (path traversal, URL maliciosa, etc.).

    Previene CWE-22 (Path Traversal), CWE-20 (Input Validation)
    y otros vectores de ataque contra el backend.
    """

    pass


# =============================================================================
# 3. STAGE ENUM
# =============================================================================


class Stage(Enum):
    """Etapas deterministas del pipeline de extracción.

    Cada etapa representa un estado discreto del flujo de scraping,
    utilizado para emitir eventos de progreso hacia la GUI.
    """

    INIT = auto()
    CONNECTING = auto()
    DOWNLOADING = auto()
    WAF_DETECTED = auto()
    CAPTCHA_SOLVING = auto()
    PARSING = auto()
    SAVING = auto()
    COMPLETED = auto()
    ERROR = auto()


# =============================================================================
# 4. PROGRESS EVENT MODEL
# =============================================================================


@dataclass(frozen=True)
class ProgressEvent:
    """Evento de progreso emitido hacia la GUI/Frontend.

    Attributes:
        stage: Etapa actual del pipeline.
        percent: Progreso estimado en rango [0, 100].
        message: Mensaje human-readable para la UI.
        meta: Metadatos adicionales (bytes descargados, proxy usado, etc.).
    """

    stage: Stage
    percent: int
    message: str
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serializa el evento a JSON para transporte por IPC/websocket.

        Returns:
            String JSON con stage, percent, message y meta.
        """
        return json.dumps(
            {
                "stage": self.stage.name,
                "percent": self.percent,
                "message": self.message,
                "meta": self.meta,
            },
            ensure_ascii=False,
            default=str,
        )


# =============================================================================
# 5. SCRAPING RESULT MODEL
# =============================================================================


@dataclass(frozen=True)
class ScrapingResult:
    """Resultado inmutable de una operación ``scrape_single()``.

    Attributes:
        success: True si se completó el pipeline exitosamente.
        url: URL objetivo.
        output_path: Path absoluto del archivo .md generado (salida unificada).
        status_code: Código HTTP final de la respuesta.
        proxy_used: URL del proxy de egreso utilizado (o None).
        profile_used: Identificador del perfil TLS/L7 utilizado.
        waf_detected: Indica si se activó el predicado de WAF.
        captcha_solved: Indica si se resolvió un desafío CAPTCHA.
        elapsed_ms: Tiempo total de pared (wall-clock) en milisegundos.
        error: Mensaje de error si ``success`` es False.
        word_count: Número de palabras en el contenido markdown extraído.
        content_type: Tipo de contenido detectado por el extractor RAG.
    """

    success: bool
    url: str
    output_path: Optional[str] = None
    status_code: Optional[int] = None
    proxy_used: Optional[str] = None
    profile_used: Optional[str] = None
    waf_detected: bool = False
    captcha_solved: bool = False
    elapsed_ms: float = 0.0
    error: Optional[str] = None
    word_count: Optional[int] = None
    content_type: Optional[str] = None


# =============================================================================
# 6. CALLBACK BRIDGE (Thread-Safety para GUI Sync/Async)
# =============================================================================

CallbackType = Callable[[ProgressEvent], Awaitable[None]]


class CallbackBridge:
    """Puente thread-safe entre el event loop del backend y la GUI.

    Si la GUI corre en un hilo diferente (Qt, Tkinter), el callback puede ser
    sync. Esta clase detecta automáticamente si el callable es coroutine o
    función regular y lo ejecuta de forma segura usando un ``asyncio.Lock``.

    Attributes:
        _callback: Referencia al callable del usuario (sync o async).
        _loop: Referencia al event loop (lazy initialization).
        _lock: Lock asyncio para exclusión mutua en emisiones.
    """

    def __init__(self, callback: Optional[CallbackType]) -> None:
        self._callback: Optional[CallbackType] = callback
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = asyncio.Lock()

    async def _ensure_loop(self) -> None:
        """Captura la referencia al event loop en la primera llamada."""
        if self._loop is None:
            self._loop = asyncio.get_running_loop()

    async def emit(self, event: ProgressEvent) -> None:
        """Emite un evento de progreso al callback de forma segura.

        Si el callback es una coroutine function, se awaiting; si es
        una función regular, se invoca directamente (bloqueante breve,
        aceptable para UI updates).

        Args:
            event: Evento de progreso a emitir.
        """
        if self._callback is None:
            return
        await self._ensure_loop()
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


# =============================================================================
# 7. SYSTEM CONFIGURATION (Single Source of Truth)
# =============================================================================


@dataclass(frozen=True)
class SystemConfig:
    """Configuración ISO-compliant del backend.

    Attributes:
        base_headers: Headers HTTP por defecto (inmutables vía MappingProxyType).
        max_concurrency: Número máximo de operaciones concurrentes.
        request_timeout_sec: Timeout por request en segundos.
        max_retries: Reintentos máximos antes de declarar fallo.
        backoff_base_ms: Base del backoff exponencial en ms.
        backoff_max_ms: Límite superior del backoff en ms.
        chunk_size_bytes: Tamaño de chunk para progreso de descarga.
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


# =============================================================================
# 8. DOMAIN MODELS (Proxy, Browser Profiles)
# =============================================================================


@dataclass(frozen=True)
class ProxyNode:
    """Nodo de proxy con ponderación y salud.

    Attributes:
        url: URL del proxy (e.g., ``"http://proxy:8080"``).
        protocol: Protocolo del proxy (http, socks5, etc.).
        weight: Peso para selección ponderada (mayor = más probable).
        health_score: Score de salud [0.0, 1.0] para degradación gradual.
    """

    url: str
    protocol: str = "http"
    weight: float = 1.0
    health_score: float = 1.0


@dataclass(frozen=True)
class BrowserProfile:
    """Perfil de navegador para evasión TLS/L7.

    Attributes:
        impersonate_id: Identificador curl_cffi del perfil TLS.
        user_agent: Cadena User-Agent HTTP.
        tls_fingerprint: Tipo de fingerprint TLS (modern, etc.).
    """

    impersonate_id: str
    user_agent: str
    tls_fingerprint: str = "modern"


_PROFILES_POOL: Final[Tuple[BrowserProfile, ...]] = (
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


# =============================================================================
# 9. PROXY ROTATOR (P, ρ)
# =============================================================================


class ProxyRotator:
    """Motor de rotación de proxies con selección ponderada y degradación.

    Gestiona un pool de proxies, selecciona proxies sanos con probabilidad
    proporcional a su peso × salud, y marca proxies como muertos cuando
    fallan de forma persistente.

    Attributes:
        _proxies: Tupla inmutable de nodos proxy disponibles.
        _logger: Logger para diagnóstico.
        _dead: Conjunto de URLs de proxies marcados como muertos.
        _lock: Lock asyncio para operaciones concurrentes.
    """

    def __init__(
        self,
        proxies: Tuple[ProxyNode, ...],
        logger: logging.Logger,
    ) -> None:
        self._proxies = proxies
        self._logger = logger
        self._dead: set[str] = set()
        self._lock = asyncio.Lock()

    def _healthy(self) -> List[ProxyNode]:
        """Filtra proxies vivos con health_score > 0.3.

        Returns:
            Lista de ProxyNode que no están muertos y tienen salud aceptable.
        """
        return [
            p
            for p in self._proxies
            if p.url not in self._dead and p.health_score > 0.3
        ]

    def _weighted_choice(self, candidates: List[ProxyNode]) -> ProxyNode:
        """Selecciona un proxy de forma ponderada (weight × health_score).

        Args:
            candidates: Lista de proxies candidatos saludables.

        Returns:
            ProxyNode seleccionado aleatoriamente con sesgo ponderado.
        """
        weights = [p.weight * p.health_score for p in candidates]
        total = sum(weights)
        if total == 0:
            return random.choice(candidates)
        r = random.uniform(0, total)
        upto = 0.0
        for p, w in zip(candidates, weights):
            upto += w
            if r <= upto:
                return p
        return candidates[-1]

    async def select(self, attempt: int) -> Optional[ProxyNode]:
        """Selecciona un proxy sano para el intento dado.

        Args:
            attempt: Número de intento actual (para logging).

        Returns:
            ProxyNode seleccionado, o None si no hay proxies disponibles.
        """
        async with self._lock:
            healthy = self._healthy()
            if not healthy:
                return None
            chosen = self._weighted_choice(healthy)
            self._logger.debug("Proxy ρ_%d = %s", attempt, chosen.url)
            return chosen

    def mark_dead(self, proxy: Optional[ProxyNode]) -> None:
        """Marca un proxy como muerto tras fallo persistente.

        Args:
            proxy: ProxyNode a marcar como muerto (no-op si es None).
        """
        if proxy is None:
            return
        self._dead.add(proxy.url)
        self._logger.warning("Proxy muerto: %s", proxy.url)


# =============================================================================
# 10. WAF DETECTOR (Φ)
# =============================================================================


class WAFDetector:
    """Detector de Web Application Firewalls y desafíos anti-bot.

    Analiza códigos de estado HTTP, headers y cuerpo de respuesta
    para identificar desafíos WAF (Cloudflare, Datadome, reCAPTCHA, etc.).

    Class Attributes:
        BODY_SIGS: Firmas de texto en el body que indican desafío WAF.
        WAF_HEADERS: Headers HTTP que revelan presencia de WAF.
    """

    BODY_SIGS: Final[Tuple[str, ...]] = (
        "cf-challenge",
        "cf-captcha-bypass",
        "g-recaptcha",
        "data-cf-settings",
        "datadome",
        "challenge-platform",
        "turnstile",
        "hcaptcha",
        "impersonate",
        "checking your browser",
    )
    WAF_HEADERS: Final[Tuple[str, ...]] = (
        "cf-ray",
        "cf-cache-status",
        "x-datadome",
        "x-cache",
        "server",
    )

    @classmethod
    def detect(cls, response: Response) -> bool:
        """Evalúa si la respuesta indica un desafío WAF.

        Inspecciona el código de estado, el cuerpo (primeros 8KB) y los
        headers de redirección para detectar patrones de WAF.

        Args:
            response: Objeto Response de curl_cffi a evaluar.

        Returns:
            True si se detecta un desafío WAF, False en caso contrario.
        """
        status = response.status_code
        
        # Check AWS WAF action header across any status code
        if "x-amzn-waf-action" in response.headers:
            return True

        # Decode body safely
        body = ""
        try:
            if response.content:
                body = response.content[:8192].decode(response.encoding or "utf-8", errors="replace").lower()
        except Exception:
            pass
        if not body and response.text:
            body = response.text[:8192].lower()

        # Check body signatures across any status code
        for sig in cls.BODY_SIGS:
            if sig in body:
                return True

        # Status code-based checks
        if status in (400, 401, 403, 429, 503, 202):
            for h in cls.WAF_HEADERS:
                if h in response.headers:
                    return True
                    
        if status in (301, 302, 307, 308):
            loc = response.headers.get("location", "").lower()
            if "challenge" in loc or "captcha" in loc:
                return True
        return False


# =============================================================================
# 11. CAPTCHA RESOLVERS (Ω, R_cap)
# =============================================================================


class AbstractCaptchaResolver(ABC):
    """Interfaz abstracta para resolutores de CAPTCHA.

    Define el contrato que deben cumplir los resolutores de desafíos
    anti-bot (Playwright, API de terceros, mock para testing, etc.).
    """

    @abstractmethod
    async def resolve(
        self,
        response: Response,
        proxy: Optional[ProxyNode] = None,
    ) -> Dict[str, str]:
        """Resuelve un desafío CAPTCHA y retorna tokens/cookies.

        Args:
            response: Respuesta HTTP que contiene el desafío.
            proxy: Proxy a utilizar para la resolución (opcional).

        Returns:
            Diccionario con headers adicionales (e.g., ``{"Cookie": "..."}``).
        """
        ...


class PlaywrightCaptchaResolver(AbstractCaptchaResolver):
    """Resuelve CAPTCHAs vía navegador headless Playwright.

    Abre la URL del desafío en un navegador Chromium headless, espera
    a que se resuelva automáticamente el desafío JavaScript, y captura
    las cookies de sesión resultantes.

    Attributes:
        _logger: Logger para diagnóstico.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    async def resolve(
        self,
        response: Response,
        proxy: Optional[ProxyNode] = None,
    ) -> Dict[str, str]:
        """Resuelve CAPTCHA vía headless browser y retorna cookies.

        Args:
            response: Respuesta HTTP con el desafío CAPTCHA.
            proxy: Proxy para el navegador headless.

        Returns:
            Diccionario con header ``Cookie`` conteniendo las cookies obtenidas.

        Raises:
            RuntimeError: Si Playwright no está instalado.
        """
        if not _HAS_PLAYWRIGHT:
            raise RuntimeError(
                "Playwright no instalado. Instale con: pip install playwright && playwright install"
            )
        self._logger.info("Dominio Ω: resolviendo CAPTCHA vía headless browser...")
        proxy_url = proxy.url if proxy else None
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                proxy={"server": proxy_url} if proxy_url else None,
            )
            context = await browser.new_context(
                user_agent=(
                    response.request.headers.get("User-Agent", "")
                    if hasattr(response, "request")
                    else ""
                ),
            )
            page = await context.new_page()
            try:
                # wait_until="domcontentloaded" is much faster and reliable than "networkidle" which hangs on trackers/ads.
                await page.goto(str(response.url), wait_until="domcontentloaded", timeout=15000)
            except Exception as e:
                self._logger.warning("Advertencia: Page.goto no completó la carga pero procedemos a capturar cookies: %s", e)
            
            # Wait for any anti-bot JS script execution to settle and store cookies
            await asyncio.sleep(6)
            cookies = await context.cookies()
            await browser.close()
            cookie_str = "; ".join(
                f"{c['name']}={c['value']}" for c in cookies
            )
            return {"Cookie": cookie_str}


class MockCaptchaResolver(AbstractCaptchaResolver):
    """Resolver mock para testing y desarrollo sin navegador.

    Retorna un token ficticio que permite continuar el pipeline
    sin resolución real de CAPTCHA.
    """

    async def resolve(
        self,
        response: Response,
        proxy: Optional[ProxyNode] = None,
    ) -> Dict[str, str]:
        """Retorna un token mock sin resolución real.

        Args:
            response: Respuesta HTTP (ignorada).
            proxy: Proxy (ignorado).

        Returns:
            Diccionario con header ``X-Captcha-Token`` mock.
        """
        return {"X-Captcha-Token": "mock_token_12345"}


# =============================================================================
# 12. ISOLATED EXTRACTOR (η, Π, M) — RAG Pipeline Integration
# =============================================================================


def _init_worker() -> None:
    """Inicializador de procesos worker para ProcessPoolExecutor.

    Ignora SIGINT en los workers para que el proceso padre controle
    el shutdown de forma ordenada.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)


class IsolatedExtractor:
    """Extractor aislado en proceso separado que delega a rag_extract.

    Ejecuta ``parse_html_unified()`` del módulo ``rag_extract`` en un
    ProcessPoolExecutor para aislar el parsing HTML (que puede ser
    CPU-intensivo y con riesgo de segfaults en parsers C) del event
    loop principal.

    Attributes:
        _executor: Pool de procesos para ejecución aislada.
    """

    def __init__(self, max_workers: int = os.cpu_count() or 2) -> None:
        self._executor = ProcessPoolExecutor(
            max_workers=max_workers,
            initializer=_init_worker,
        )

    async def extract(self, payload: bytes, source_url: str, encoding: str = "utf-8") -> dict:
        """Extrae contenido estructurado del HTML en proceso aislado.

        Delega a ``rag_extract.parse_html_unified()`` que realiza:
        limpieza de ruido, extracción de contenido principal, generación
        de markdown y metadata, todo en un proceso separado.

        Args:
            payload: Bytes del documento HTML crudo.
            source_url: URL fuente para resolución de enlaces relativos.
            encoding: Codificación de caracteres detectada para el payload.

        Returns:
            Diccionario con resultado de ``parse_html_unified()``.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, parse_html_unified, payload, source_url, encoding
        )

    def shutdown(self) -> None:
        """Cierra el pool de procesos de forma ordenada."""
        self._executor.shutdown(wait=True)


# =============================================================================
# 13. ATOMIC STORAGE ENGINE (F)
# =============================================================================


class AtomicStorageEngine:
    """Motor de escritura atómica usando os.replace() para integridad.

    Garantiza que los archivos nunca quedan en estado parcial:
    escribe a un archivo temporal ``.tmp`` y realiza un rename atómico
    al path final. Si la escritura falla, el temporal se elimina.

    Attributes:
        _logger: Logger para diagnóstico de operaciones I/O.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def _atomic_write(self, target: Path, payload: Union[bytes, str]) -> Path:
        """Escribe payload a archivo de forma atómica.

        Crea directorios padres si no existen, escribe a un archivo
        temporal, y renombra atómicamente al path final.

        Args:
            target: Path absoluto del archivo destino.
            payload: Contenido a escribir (bytes o str).

        Returns:
            Path del archivo escrito.

        Raises:
            StorageError: Si la escritura o el rename fallan.
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        try:
            if isinstance(payload, bytes):
                tmp.write_bytes(payload)
            else:
                tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, target)
            return target
        except Exception:
            tmp.unlink(missing_ok=True)
            raise StorageError(
                f"Fallo I/O atómico en {target}", code="ST-001"
            )

    async def save_raw(self, key: Path, payload: bytes) -> str:
        """Guarda payload binario de forma atómica.

        Args:
            key: Path destino del archivo.
            payload: Bytes a persistir.

        Returns:
            String con el path absoluto del archivo guardado.
        """
        path = await asyncio.to_thread(self._atomic_write, key, payload)
        return str(path)

    async def save_structured(self, key: Path, data: Dict[str, Any]) -> str:
        """Guarda datos estructurados como JSON de forma atómica.

        Args:
            key: Path destino del archivo .json.
            data: Diccionario a serializar como JSON.

        Returns:
            String con el path absoluto del archivo guardado.
        """
        json_str = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        path = await asyncio.to_thread(self._atomic_write, key, json_str)
        return str(path)

    async def save_markdown(self, key: Path, content: str) -> str:
        """Guarda contenido markdown con YAML frontmatter de forma atómica.

        Este es el método principal de persistencia en el pipeline unificado.
        El archivo resultante contiene YAML frontmatter + contenido markdown
        en un solo archivo .md.

        Args:
            key: Path destino del archivo .md.
            content: String con el documento markdown completo (incluyendo frontmatter).

        Returns:
            String con el path absoluto del archivo .md guardado.
        """
        path = await asyncio.to_thread(self._atomic_write, key, content)
        self._logger.info("Documento .md guardado: %s", path)
        return str(path)


# =============================================================================
# 14. INPUT VALIDATOR (ISO 25010 — Seguridad)
# =============================================================================


class InputValidator:
    """Validador de pre-condiciones para ``scrape_single()``.

    Previene CWE-22 (Path Traversal) y CWE-20 (Input Validation)
    aplicando restricciones estrictas sobre URLs, nombres de archivo
    y directorios de destino.

    Class Attributes:
        _SAFE_FILENAME_RE: Patrón regex para nombres de archivo seguros.
        _MAX_URL_LEN: Longitud máxima permitida para URLs.
        _FORBIDDEN_SCHEMES: Esquemas de URL prohibidos por seguridad.
    """

    _SAFE_FILENAME_RE: Final[re.Pattern] = re.compile(r"^[\w\-. ]+$")
    _MAX_URL_LEN: Final[int] = 2048
    _FORBIDDEN_SCHEMES: Final[Set[str]] = {"file", "ftp", "javascript", "data"}

    @classmethod
    def validate_url(cls, url: str) -> None:
        """Valida que la URL cumpla las restricciones de seguridad.

        Args:
            url: URL a validar.

        Raises:
            ValidationError: Si la URL está vacía, excede longitud, o esquema inválido.
            SecurityError: Si la URL usa un esquema prohibido (file, ftp, javascript, data).
        """
        if not isinstance(url, str) or not url:
            raise ValidationError("URL vacía o tipo inválido.", code="VAL-001")
        if len(url) > cls._MAX_URL_LEN:
            raise ValidationError(
                f"URL excede {cls._MAX_URL_LEN} chars.", code="VAL-002"
            )
        parsed = urlparse(url)
        if parsed.scheme in cls._FORBIDDEN_SCHEMES:
            raise SecurityError(
                f"Esquema prohibido: {parsed.scheme}", code="SEC-001"
            )
        if parsed.scheme not in {"http", "https"}:
            raise ValidationError(
                f"Esquema no soportado: {parsed.scheme}", code="VAL-003"
            )
        if not parsed.netloc:
            raise ValidationError("URL sin host válido.", code="VAL-004")

    @classmethod
    def validate_filename(cls, name: str) -> str:
        """Valida y sanitiza un nombre de archivo.

        Previene path traversal (CWE-22) y caracteres ilegales (CWE-20).

        Args:
            name: Nombre de archivo a validar.

        Returns:
            Nombre de archivo sanitizado (basename únicamente).

        Raises:
            ValidationError: Si el nombre está vacío.
            SecurityError: Si se detecta path traversal o caracteres ilegales.
        """
        if not isinstance(name, str) or not name:
            raise ValidationError("Nombre de archivo vacío.", code="VAL-005")
        base = os.path.basename(name)
        if base != name:
            raise SecurityError(
                "Path traversal detectado en filename.", code="SEC-002"
            )
        if not cls._SAFE_FILENAME_RE.match(base):
            raise SecurityError(
                f"Nombre de archivo contiene caracteres ilegales: {name}",
                code="SEC-003",
            )
        return base

    @classmethod
    def validate_directory(cls, directory: Union[str, Path]) -> Path:
        """Valida y crea el directorio de destino si no existe.

        Args:
            directory: Path del directorio (absoluto o relativo).

        Returns:
            Path absoluto del directorio validado.

        Raises:
            StorageError: Si no se puede crear el directorio.
        """
        p = Path(directory)
        if not p.is_absolute():
            p = p.resolve()
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(
                f"No se puede crear directorio destino: {exc}", code="ST-002"
            ) from exc
        return p


# =============================================================================
# 15. ELITE SCRAPER BACKEND (THE MAIN API)
# =============================================================================


class EliteScraperBackend:
    """Backend de extracción web de misión crítica con pipeline RAG unificado.

    Integra el motor de red (curl_cffi con TLS fingerprinting), evasión
    de WAF, resolución de CAPTCHA, y el pipeline de extracción RAG
    (``rag_extract.parse_html_unified``) para producir un único archivo
    .md con YAML frontmatter.

    Uso típico desde GUI (async):

        backend = EliteScraperBackend()
        result = await backend.scrape_single(
            url="https://example.com",
            output_filename="mi_archivo",
            output_directory="/tmp/descargas",
            callback=mi_callback_async,
        )

    Uso típico desde GUI (sync, ej. Tkinter):

        def sync_callback(event: ProgressEvent):
            root.after(0, lambda: label.config(text=event.message))

        asyncio.run(backend.scrape_single(..., callback=sync_callback))

    Attributes:
        _config: Configuración del sistema.
        _logger: Logger para diagnóstico.
        _storage: Motor de escritura atómica.
        _extractor: Extractor aislado en proceso separado.
        _proxy_rotator: Rotador de proxies.
        _captcha_resolver: Resolver de CAPTCHAs.
        _sem: Semáforo de concurrencia.
        _sessions: Pool de sesiones curl_cffi por perfil.
    """

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
        captcha_resolver: Optional[AbstractCaptchaResolver] = None,
        proxy_rotator: Optional[ProxyRotator] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._config = config or SystemConfig()
        self._logger = logger or self._default_logger()
        self._storage = AtomicStorageEngine(self._logger)
        self._extractor = IsolatedExtractor(
            max_workers=(os.cpu_count() or 2)
        )
        self._proxy_rotator = proxy_rotator or ProxyRotator(
            (), self._logger
        )
        self._captcha_resolver = captcha_resolver or (
            PlaywrightCaptchaResolver(self._logger)
            if _HAS_PLAYWRIGHT
            else MockCaptchaResolver()
        )
        self._sem = asyncio.Semaphore(self._config.max_concurrency)
        self._sessions: Dict[str, AsyncSession] = {}

    # ------------------------------------------------------------------
    @staticmethod
    def _default_logger() -> logging.Logger:
        """Crea un logger por defecto con handler de stdout.

        Returns:
            Logger configurado con nivel DEBUG y formato estándar.
        """
        logger = logging.getLogger("EliteScraperBackend")
        logger.setLevel(logging.DEBUG)
        if not logger.handlers:
            h = logging.StreamHandler(sys.stdout)
            h.setFormatter(
                logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
            )
            logger.addHandler(h)
        return logger

    # ------------------------------------------------------------------
    def clear_cookies(self) -> None:
        """Limpia las cookies de todas las sesiones de curl_cffi activas.

        Evita la contaminación cruzada de cookies entre distintas ejecuciones
        de scraping para mejorar la privacidad y fiabilidad de las peticiones.
        """
        self._logger.info("Purgando cookies de todas las sesiones activas...")
        for pid, session in self._sessions.items():
            try:
                session.cookies.clear()
                self._logger.debug("Cookies limpiadas para perfil '%s'", pid)
            except Exception as e:
                self._logger.warning("No se pudieron limpiar las cookies del perfil '%s': %s", pid, e)

    # ------------------------------------------------------------------
    async def _get_session(self, profile: BrowserProfile) -> AsyncSession:
        """Obtiene o crea una sesión curl_cffi para el perfil dado.

        Las sesiones se reutilizan por perfil para mantener las conexiones
        TCP/TLS vivas y reducir latencia.

        Args:
            profile: Perfil de navegador para la sesión.

        Returns:
            AsyncSession configurada con el impersonate_id del perfil.
        """
        if profile.impersonate_id not in self._sessions:
            self._sessions[profile.impersonate_id] = AsyncSession(
                impersonate=profile.impersonate_id
            )
        return self._sessions[profile.impersonate_id]

    # ------------------------------------------------------------------
    async def _execute_with_backoff(
        self,
        url: str,
        operation: Callable[[], Awaitable[Union[Response, Tuple[bytes, Response]]]],
        max_retries: int,
    ) -> Tuple[Union[Response, Tuple[bytes, Response]], int]:
        """Ejecuta una operación HTTP con backoff exponencial y jitter.

        Reintenta ante errores transitorios (429, 5xx, timeout, DNS)
        con backoff exponencial decorado con jitter aleatorio para
        evitar thundering herd.

        Args:
            url: URL objetivo (para logging).
            operation: Callable async que retorna un Response o (bytes, Response).
            max_retries: Número máximo de reintentos.

        Returns:
            Tupla (Resultado, intentos_realizados).

        Raises:
            NetworkError: Si se agotan los reintentos.
        """
        attempt = 0
        while True:
            try:
                res = await operation()
                if isinstance(res, tuple):
                    _, resp = res
                else:
                    resp = res

                if resp.status_code in {429, 500, 502, 503, 504}:
                    raise RequestsError(
                        f"HTTP transitorio {resp.status_code}"
                    )
                return res, attempt
            except (RequestsError, asyncio.TimeoutError, OSError) as exc:
                if attempt >= max_retries:
                    raise NetworkError(
                        f"Máximos reintentos alcanzados para {url}: {exc}",
                        code="NET-001",
                    ) from exc
                attempt += 1
                delay = min(
                    self._config.backoff_max_ms,
                    self._config.backoff_base_ms * (2 ** (attempt - 1)),
                )
                jitter = random.uniform(0, delay)
                self._logger.debug(
                    "[%s] Backoff %d/%d: %.0fms", url, attempt, max_retries, jitter
                )
                await asyncio.sleep(jitter / 1000.0)

    # ------------------------------------------------------------------
    async def _download_with_progress(
        self,
        url: str,
        session: AsyncSession,
        proxy: Optional[ProxyNode],
        profile: BrowserProfile,
        captcha_tokens: Optional[Dict[str, str]],
        bridge: CallbackBridge,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> Tuple[bytes, Response]:
        """Descarga con monitoreo de progreso real por chunks.

        Emite eventos ``DOWNLOADING`` con porcentaje estimado basado
        en Content-Length o heurística de chunks recibidos.

        Args:
            url: URL a descargar.
            session: Sesión curl_cffi para la request.
            proxy: Proxy a utilizar (o None para conexión directa).
            profile: Perfil de navegador para headers.
            captcha_tokens: Headers adicionales de CAPTCHA resuelto.
            bridge: Bridge para emitir eventos de progreso.
            cancel_event: Evento asyncio opcional para soportar cancelación thread-safe.

        Returns:
            Tupla de (bytes, Response) del payload descargado y la respuesta de red.
        """
        # Verificar cancelación antes de iniciar
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError("Operación cancelada por el usuario antes de la descarga.")

        headers: Dict[str, str] = {
            **self._config.base_headers,
            "User-Agent": profile.user_agent,
        }
        if captcha_tokens:
            headers.update(captcha_tokens)

        proxies = None
        if proxy is not None:
            proxies = {"http": proxy.url, "https": proxy.url}

        async with asyncio.timeout(self._config.request_timeout_sec):
            resp = await session.get(
                url, headers=headers, proxies=proxies, stream=True
            )

        # Si detectamos un código de estado transitorio de error, no descargamos los chunks y retornamos vacío
        if resp.status_code in {429, 500, 502, 503, 504}:
            return b"", resp

        total = resp.headers.get("content-length")
        total_len = int(total) if total else None
        downloaded = 0
        chunks: List[bytes] = []

        async for chunk in resp.aiter_content(
            chunk_size=self._config.chunk_size_bytes
        ):
            # Monitorear cancelación en cada iteración de chunk
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError("Descarga cancelada por el usuario durante la transmisión.")
            
            chunks.append(chunk)
            downloaded += len(chunk)
            if total_len:
                pct = min(100, int((downloaded / total_len) * 100))
                await bridge.emit(
                    ProgressEvent(
                        stage=Stage.DOWNLOADING,
                        percent=pct,
                        message=(
                            f"Descargando {url} — {pct}% "
                            f"({downloaded}/{total_len} bytes)"
                        ),
                        meta={"downloaded": downloaded, "total": total_len},
                    )
                )
            else:
                await bridge.emit(
                    ProgressEvent(
                        stage=Stage.DOWNLOADING,
                        percent=min(50, 10 + downloaded // 1024),
                        message=f"Descargando {url} — {downloaded} bytes recibidos...",
                        meta={"downloaded": downloaded},
                    )
                )

        return b"".join(chunks), resp

    # ------------------------------------------------------------------
    async def _acquire(
        self,
        url: str,
        session: AsyncSession,
        proxy: Optional[ProxyNode],
        profile: BrowserProfile,
        captcha_tokens: Optional[Dict[str, str]],
    ) -> Response:
        """Ejecuta un request GET simple (no-streaming) para status/headers.

        Utilizado para obtener el código de estado y headers necesarios
        para la evaluación de WAF, complementando la descarga streaming.

        Args:
            url: URL objetivo.
            session: Sesión curl_cffi.
            proxy: Proxy a utilizar (o None).
            profile: Perfil de navegador.
            captcha_tokens: Headers de CAPTCHA resuelto.

        Returns:
            Objeto Response con status_code y headers.
        """
        headers: Dict[str, str] = {
            **self._config.base_headers,
            "User-Agent": profile.user_agent,
        }
        if captcha_tokens:
            headers.update(captcha_tokens)
        proxies = None
        if proxy is not None:
            proxies = {"http": proxy.url, "https": proxy.url}
        async with asyncio.timeout(self._config.request_timeout_sec):
            return await session.get(url, headers=headers, proxies=proxies)

    # ------------------------------------------------------------------
    async def scrape_single(
        self,
        url: str,
        output_filename: str,
        output_directory: Union[str, Path],
        callback: Optional[CallbackType] = None,
        cancel_event: Optional[asyncio.Event] = None,
        preview_only: bool = False,
    ) -> ScrapingResult:
        """Pipeline completo de extracción para una única URL.

        Ejecuta el flujo completo: validación → descarga → detección WAF →
        resolución CAPTCHA → extracción RAG → construcción de documento .md →
        persistencia atómica. Produce un ÚNICO archivo .md con YAML
        frontmatter (NO .html + .json por separado).

        Args:
            url: URL objetivo (http/https).
            output_filename: Nombre base del archivo (sin extensión).
            output_directory: Directorio de destino absoluto o relativo.
            callback: Callable async o sync que recibe ProgressEvent.
            cancel_event: Evento asyncio opcional para soportar cancelación thread-safe.
            preview_only: Si es True, sólo se previsualiza la metadata y no se guarda a disco.

        Returns:
            ScrapingResult inmutable con output_path al .md, metadatos y estado.

        Raises:
            ValidationError: Si las pre-condiciones de entrada fallan.
            SecurityError: Si se detecta path traversal o URL maliciosa.
            StorageError: Si falla la escritura atómica en disco.
            NetworkError: Si se agotan los reintentos de red.
        """
        t0 = time.perf_counter()
        bridge = CallbackBridge(callback)

        # --- 1. Pre-condiciones ISO ---
        InputValidator.validate_url(url)
        safe_name = InputValidator.validate_filename(output_filename)
        dest_dir = InputValidator.validate_directory(output_directory)

        md_path = dest_dir / f"{safe_name}.md"

        # --- 2. Emit INIT ---
        await bridge.emit(
            ProgressEvent(
                stage=Stage.INIT,
                percent=0,
                message=f"Inicializando extracción: {url}",
                meta={"target": str(md_path)},
            )
        )

        proxy: Optional[ProxyNode] = None
        waf_detected = False
        captcha_solved = False
        status_code: Optional[int] = None
        profile_used: Optional[str] = None
        proxy_used: Optional[str] = None
        payload_bytes = b""

        try:
            # Verificar cancelación antes de iniciar
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError("Extracción cancelada por el usuario.")

            async with self._sem:
                # --- 3. Perfil estocástico L4/L7 ---
                profile = random.choice(_PROFILES_POOL)
                profile_used = profile.impersonate_id
                session = await self._get_session(profile)

                # --- 4. Emit CONNECTING ---
                await bridge.emit(
                    ProgressEvent(
                        stage=Stage.CONNECTING,
                        percent=5,
                        message=f"Conectando a {url} (perfil: {profile_used})...",
                        meta={"profile": profile_used},
                    )
                )

                # --- 5. Proxy ρ ---
                proxy = await self._proxy_rotator.select(attempt=0)
                if proxy:
                    proxy_used = proxy.url

                # Verificar cancelación
                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError("Extracción cancelada por el usuario.")

                # --- 6. Descarga con progreso en un solo flujo (sin handshake duplicado) ---
                (payload_bytes, resp), retries = await self._execute_with_backoff(
                    url,
                    lambda: self._download_with_progress(
                        url, session, proxy, profile, None, bridge, cancel_event
                    ),
                    self._config.max_retries,
                )
                status_code = resp.status_code

                # --- 7. Evaluación Φ(σ) — WAF ---
                if WAFDetector.detect(resp):
                    waf_detected = True
                    await bridge.emit(
                        ProgressEvent(
                            stage=Stage.WAF_DETECTED,
                            percent=55,
                            message=(
                                "Desafío WAF/CAPTCHA detectado. "
                                "Transicionando a Ω..."
                            ),
                            meta={"status_code": status_code},
                        )
                    )

                    # Verificar cancelación
                    if cancel_event is not None and cancel_event.is_set():
                        raise asyncio.CancelledError("Extracción cancelada por el usuario.")

                    # --- 8. Resolver CAPTCHA ---
                    await bridge.emit(
                        ProgressEvent(
                            stage=Stage.CAPTCHA_SOLVING,
                            percent=58,
                            message="Resolviendo CAPTCHA...",
                        )
                    )
                    tokens = await self._captcha_resolver.resolve(
                        resp, proxy=proxy
                    )
                    captcha_solved = True
                    await bridge.emit(
                        ProgressEvent(
                            stage=Stage.CAPTCHA_SOLVING,
                            percent=60,
                            message="CAPTCHA resuelto. Reintentando con tokens...",
                        )
                    )

                    # Verificar cancelación
                    if cancel_event is not None and cancel_event.is_set():
                        raise asyncio.CancelledError("Extracción cancelada por el usuario.")

                    # --- 9. Re-descarga con tokens ---
                    (payload_bytes, resp), _ = await self._execute_with_backoff(
                        url,
                        lambda: self._download_with_progress(
                            url, session, proxy, profile, tokens, bridge, cancel_event
                        ),
                        1,
                    )
                    status_code = resp.status_code

                if not (200 <= status_code < 300):
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

                # Verificar cancelación
                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError("Extracción cancelada por el usuario.")

                # --- 10. Extracción RAG aislada (Π) ---
                await bridge.emit(
                    ProgressEvent(
                        stage=Stage.PARSING,
                        percent=70,
                        message=(
                            "Limpiando ruido y aislando contenido principal..."
                        ),
                    )
                )

                t_extract_start = time.perf_counter()
                detected_encoding = resp.encoding or "utf-8"
                extracted = await self._extractor.extract(payload_bytes, url, encoding=detected_encoding)
                extract_duration_ms = (time.perf_counter() - t_extract_start) * 1000

                # --- 11. Construir ExtractionResult ---
                extracted_meta = extracted.get("page_metadata", {})
                try:
                    content_type = ContentType(extracted_meta.get("content_type", "unknown"))
                except ValueError:
                    content_type = ContentType.UNKNOWN

                metadata = PageMetadata(
                    url=url,
                    title=extracted_meta.get("title", ""),
                    author=extracted_meta.get("author", ""),
                    site_name=extracted_meta.get("site_name", ""),
                    description=extracted_meta.get("description", ""),
                    content_type=content_type,
                    language=extracted_meta.get("language", ""),
                    published_date=extracted_meta.get("published_date", ""),
                    keywords=extracted_meta.get("keywords", []),
                    canonical_url=extracted_meta.get("canonical_url", ""),
                    og_image=extracted_meta.get("og_image", ""),
                )

                clean_md = extracted.get("clean_markdown", "")
                word_count_val = extracted.get("word_count", 0)
                paragraph_count = extracted.get("paragraph_count", 0)
                links_found = extracted.get("links_found", [])
                images_found = extracted.get("images_found", [])
                errors_list = extracted.get("errors", [])

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
                content_type = extraction_result.metadata.content_type.value

                # --- Manejo del modo de previsualización (Preview) ---
                if preview_only:
                    preview = RAGPipeline.preview(extraction_result)

                    preview_card = (
                        "\n=== PREVISUALIZACIÓN DE EXTRACCIÓN ===\n"
                        f"Título:        {preview.title}\n"
                        f"Autor:         {preview.author or 'N/A'}\n"
                        f"Sitio:         {preview.site_name or 'N/A'}\n"
                        f"Descripción:   {preview.description or 'N/A'}\n"
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
                            message="Previsualización completada con éxito.",
                            meta={
                                "preview": {
                                    "title": preview.title,
                                    "author": preview.author,
                                    "site_name": preview.site_name,
                                    "description": preview.description,
                                    "content_type": preview.content_type,
                                    "language": preview.language,
                                    "published_date": preview.published_date,
                                    "keywords": preview.keywords,
                                    "word_count": preview.estimated_word_count,
                                    "projected_chunks": preview.projected_chunks,
                                },
                                "elapsed_ms": elapsed,
                            },
                        )
                    )

                    return ScrapingResult(
                        success=True,
                        url=url,
                        output_path="",
                        status_code=status_code,
                        proxy_used=proxy_used,
                        profile_used=profile_used,
                        waf_detected=waf_detected,
                        captcha_solved=captcha_solved,
                        elapsed_ms=elapsed,
                        word_count=word_count,
                        content_type=content_type,
                    )

                # --- 12. Construir documento .md con YAML frontmatter ---
                md_document = MarkdownBuilder().build_document(extraction_result)

                # --- 13. Persistencia atómica .md ---
                await bridge.emit(
                    ProgressEvent(
                        stage=Stage.SAVING,
                        percent=90,
                        message=f"Guardando documento .md en {md_path.name}...",
                    )
                )

                # Verificar cancelación
                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError("Extracción cancelada por el usuario antes de guardar.")

                saved_path = await self._storage.save_markdown(
                    md_path, md_document
                )

                elapsed = (time.perf_counter() - t0) * 1000

                # --- 14. Emit COMPLETED ---
                await bridge.emit(
                    ProgressEvent(
                        stage=Stage.COMPLETED,
                        percent=100,
                        message=f"Extracción completada en {elapsed:.0f}ms",
                        meta={
                            "output_path": saved_path,
                            "word_count": word_count,
                            "content_type": content_type,
                            "elapsed_ms": elapsed,
                        },
                    )
                )

                return ScrapingResult(
                    success=True,
                    url=url,
                    output_path=saved_path,
                    status_code=status_code,
                    proxy_used=proxy_used,
                    profile_used=profile_used,
                    waf_detected=waf_detected,
                    captcha_solved=captcha_solved,
                    elapsed_ms=elapsed,
                    word_count=word_count,
                    content_type=content_type,
                )

        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            err_msg = f"{type(exc).__name__}: {exc}"
            self._logger.error("[ERR] %s → %s", url, err_msg)
            if proxy is not None:
                self._proxy_rotator.mark_dead(proxy)
            await bridge.emit(
                ProgressEvent(
                    stage=Stage.ERROR,
                    percent=100,
                    message=f"Fallo: {err_msg}",
                    meta={"exception": type(exc).__name__},
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
                error=err_msg,
            )
        finally:
            self.clear_cookies()

    # ------------------------------------------------------------------
    async def close(self) -> None:
        """Cierra todos los recursos del backend de forma ordenada.

        Cierra las sesiones curl_cffi y el pool de procesos del extractor.
        """
        for s in self._sessions.values():
            await s.close()
        self._sessions.clear()
        self._extractor.shutdown()

    # ------------------------------------------------------------------
    async def __aenter__(self) -> EliteScraperBackend:
        """Entry point del context manager async."""
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Exit point del context manager async. Cierra recursos."""
        await self.close()


# =============================================================================
# 16. DEMO / EJEMPLO DE INTEGRACIÓN GUI
# =============================================================================


async def _demo() -> None:
    """Demostración de uso del backend con callback de progreso.

    Ejecuta una extracción completa contra httpbin.org mostrando
    la barra de progreso en la terminal.
    """

    async def gui_callback(event: ProgressEvent) -> None:
        """Callback de demostración que imprime una barra de progreso."""
        bar = "█" * (event.percent // 5) + "░" * (20 - event.percent // 5)
        print(
            f"[{bar}] {event.percent:3d}% | "
            f"{event.stage.name:15s} | {event.message}"
        )

    backend = EliteScraperBackend()
    async with backend:
        result = await backend.scrape_single(
            url="https://httpbin.org/html",
            output_filename="httpbin_demo",
            output_directory="./gui_output",
            callback=gui_callback,
        )
        print("\n--- RESULTADO FINAL ---")
        print(json.dumps(asdict(result), indent=2, ensure_ascii=False))


# =============================================================================
# 17. ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    if sys.platform != "win32":
        try:
            import uvloop

            uvloop.install()
        except ImportError:
            pass
    try:
        asyncio.run(_demo())
    except KeyboardInterrupt:
        sys.exit(130)
