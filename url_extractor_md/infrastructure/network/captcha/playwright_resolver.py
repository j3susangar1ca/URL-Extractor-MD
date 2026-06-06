"""Playwright-based CAPTCHA resolver for headless browser challenges.

This module provides :class:`PlaywrightCaptchaResolver`, an adapter that
implements the :class:`CaptchaResolverPort` protocol using Playwright's
headless Chromium to navigate to challenge URLs, allow JavaScript-based
anti-bot checks to settle, and capture the resulting cookies for
subsequent HTTP requests.

If the ``playwright`` package is not installed, a clear
:class:`RuntimeError` with installation instructions is raised at
construction time rather than failing later at resolution time.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ....domain.contracts import CaptchaResolverPort
from ....domain.models import ProxyNode

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page


class PlaywrightCaptchaResolver:
    """CAPTCHA resolver that uses Playwright headless Chromium.

    Implements the :class:`CaptchaResolverPort` protocol by launching a
    headless Chromium browser, navigating to the challenge URL, waiting
    for JavaScript anti-bot scripts to settle, and capturing the cookies
    set by the challenge resolution.

    The resolver requires the ``playwright`` package to be installed.
    If it is missing, :class:`RuntimeError` is raised from ``__init__``
    with installation instructions so that failures are discovered early.

    Attributes:
        _logger: Logger instance for diagnostic output.

    Usage::

        resolver = PlaywrightCaptchaResolver(logger=my_logger)
        headers = await resolver.resolve(
            url="https://example.com/challenge",
            status_code=403,
            response_headers={"cf-ray": "abc123"},
        )
        # headers ≈ {"Cookie": "cf_clearance=...; ..."}
    """

    __slots__ = ("_logger",)

    #: Seconds to wait for JS anti-bot scripts to settle after navigation.
    _SETTLE_WAIT_SECONDS: int = 6

    def __init__(self, logger: logging.Logger) -> None:
        """Initialize the resolver and verify that Playwright is available.

        Args:
            logger: Logger instance for diagnostic and error messages.

        Raises:
            RuntimeError: If the ``playwright`` package is not installed.
                The error message includes installation instructions.
        """
        self._logger = logger

        # --- Graceful gate: fail fast if playwright is missing ----------
        try:
            import playwright  # noqa: F401 — imported only for availability check

            _ = playwright
        except ImportError:
            raise RuntimeError(
                "The 'playwright' package is required for "
                "PlaywrightCaptchaResolver but is not installed.  "
                "Install it with:\n"
                "  pip install playwright\n"
                "  playwright install chromium\n"
                "Then retry."
            ) from None

    async def resolve(
        self,
        url: str,
        status_code: int,
        response_headers: dict[str, str],
        proxy: ProxyNode | None = None,
    ) -> dict[str, str]:
        """Resolve a CAPTCHA challenge via headless Chromium.

        Opens the challenge URL in a fresh headless Chromium browser
        context, waits for JavaScript anti-bot scripts to settle, then
        captures all cookies from the browser context and returns them
        as a ``Cookie`` header string suitable for subsequent HTTP
        requests.

        Args:
            url: Challenge URL to navigate to.
            status_code: HTTP status code of the challenge response
                (used for logging only).
            response_headers: Headers from the challenge response
                (used for logging only).
            proxy: Optional proxy node for egress.  When provided, the
                proxy URL is passed to Playwright's browser context
                launch options.

        Returns:
            Dictionary containing a ``Cookie`` header with all cookies
            captured from the browser context after the settle period.
            Returns an empty dict if no cookies were set.
        """
        from playwright.async_api import async_playwright

        self._logger.info(
            "PlaywrightCaptchaResolver: resolving CAPTCHA for %s "
            "(status_code=%d, proxy=%s)",
            url,
            status_code,
            proxy.url if proxy else None,
        )

        # Build proxy config for Playwright when a proxy node is provided.
        proxy_config: dict[str, str] | None = None
        if proxy is not None:
            proxy_config = {"server": proxy.url}

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(proxy=proxy_config)  # type: ignore[arg-type]

            try:
                page: Page = await context.new_page()

                self._logger.debug(
                    "PlaywrightCaptchaResolver: navigating to %s", url
                )
                await page.goto(url, wait_until="domcontentloaded")

                self._logger.debug(
                    "PlaywrightCaptchaResolver: waiting %d seconds for "
                    "JS anti-bot to settle",
                    self._SETTLE_WAIT_SECONDS,
                )
                await page.wait_for_timeout(self._SETTLE_WAIT_SECONDS * 1000)

                # Capture all cookies from the browser context.
                cookie_string = await self._capture_cookies(context)

                if cookie_string:
                    self._logger.info(
                        "PlaywrightCaptchaResolver: captured %d cookie(s) "
                        "for %s",
                        cookie_string.count("="),
                        url,
                    )
                else:
                    self._logger.warning(
                        "PlaywrightCaptchaResolver: no cookies captured "
                        "for %s — CAPTCHA may not have been solved",
                        url,
                    )

                return {"Cookie": cookie_string} if cookie_string else {}

            finally:
                await context.close()
                await browser.close()

    @staticmethod
    async def _capture_cookies(context: BrowserContext) -> str:
        """Extract cookies from a Playwright browser context.

        Serializes all cookies in the context into a ``Cookie`` header
        string of the form ``"name1=value1; name2=value2"``.

        Args:
            context: Playwright browser context to read cookies from.

        Returns:
            Semicolon-separated cookie string, or an empty string if the
            context has no cookies.
        """
        cookies = await context.cookies()
        if not cookies:
            return ""

        parts: list[str] = []
        for cookie in cookies:
            name = cookie.get("name", "")
            value = cookie.get("value", "")
            if name:
                parts.append(f"{name}={value}")

        return "; ".join(parts)
