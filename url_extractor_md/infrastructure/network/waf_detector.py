"""WAF detection adapter implementing the WAFDetectorPort protocol.

This module provides :class:`WAFDetector`, a stateless, pure-logic
implementation that inspects HTTP responses for signatures commonly
associated with Web Application Firewalls, CAPTCHA challenges, and
bot-mitigation services (Cloudflare, DataDome, hCaptcha, reCAPTCHA,
Turnstile, PerimeterX, Akamai, etc.).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
#  WAF body signatures — substrings found in challenge / interstitial pages
# ---------------------------------------------------------------------------

BODY_SIGS: tuple[str, ...] = (
    # Cloudflare
    "cf-challenge",
    "cf-browser-verification",
    "cloudflare",
    "cf_chl_opt",
    "challenge-platform",
    # Google reCAPTCHA
    "g-recaptcha",
    "recaptcha",
    "recaptcha/api",
    # DataDome
    "datadome",
    "DataDome",
    # Cloudflare Turnstile
    "turnstile",
    "cf-turnstile",
    # hCaptcha
    "hcaptcha",
    "h-captcha",
    # PerimeterX / HUMAN
    "perimeterx",
    "_px3",
    "px-captcha",
    # Akamai / Bot Manager
    "akamai",
    "akamai_bm",
    "bm_sv",
    # Generic challenge pages
    "checking your browser",
    "please wait",
    "just a moment",
    "verify you are human",
    "are you a robot",
    "access denied",
    "blocked",
    "challenge-form",
    "challenge-error",
    # Imperva / Incapsula
    "incapsula",
    "incap_ses",
    "visid_incap",
    # Sucuri
    "sucuri",
    "sucuri-cloudproxy",
)

# ---------------------------------------------------------------------------
#  WAF-revealing response headers (lower-cased for comparison)
# ---------------------------------------------------------------------------

WAF_HEADERS: tuple[str, ...] = (
    # Cloudflare
    "cf-ray",
    "cf-cache-status",
    "cf-mitigated",
    "cf-chl-bypass",
    "cf-waf-tag",
    # DataDome
    "x-datadome",
    "x-dd-b",
    "x-dd-c",
    # Akamai
    "x-akamai-transformed",
    "x-akamai-session-info",
    "akamai-grn",
    # Imperva / Incapsula
    "x-iinfo",
    "x-cdn",
    # Sucuri
    "x-sucuri-id",
    "x-sucuri-cache",
    # Generic WAF headers
    "x-waf-event-info",
    "x-waf",
    "server-timing",
)

# ---------------------------------------------------------------------------
#  Status codes commonly associated with WAF challenges
# ---------------------------------------------------------------------------

_WAF_STATUS_CODES: frozenset[int] = frozenset({403, 429, 503, 508, 520, 521, 522, 523, 524})

# ---------------------------------------------------------------------------
#  Redirect location substrings that indicate a WAF challenge
# ---------------------------------------------------------------------------

_CHALLENGE_REDIRECT_SUBSTRS: tuple[str, ...] = (
    "challenge",
    "captcha",
    "verify",
    "blocked",
    "deny",
    "firewall",
    "datadome",
    "perimeterx",
    "sucuri",
    "incapsula",
    "akamai",
)


class WAFDetector:
    """Stateless WAF / CAPTCHA challenge detector.

    Implements the :class:`WAFDetectorPort` protocol by inspecting the
    HTTP status code, response headers, body content, and redirect
    location for signatures of bot-mitigation services.

    This class has **no mutable state** and is safe to share across
    coroutines without synchronization.

    Usage::

        detector = WAFDetector()
        if detector.detect(503, {"cf-ray": "..."}, "<html>cf-challenge..."):
            # WAF challenge detected — invoke CAPTCHA resolver.
    """

    __slots__ = ()

    def detect(
        self,
        status_code: int,
        headers: dict[str, str],
        body_snippet: str,
    ) -> bool:
        """Evaluate whether the response indicates a WAF challenge.

        The detection heuristic checks four independent signals:

        1. **Status code** — Known challenge codes (403, 429, 503, …).
        2. **Headers** — WAF-revealing header keys.
        3. **Body** — Substring signatures in the response body.
        4. **Redirect location** — Challenge-related redirect URLs.

        A positive match on **any** signal is sufficient to return
        ``True``.

        Args:
            status_code: HTTP status code of the response.
            headers: Response headers dictionary (case-insensitive
                matching is performed internally).
            body_snippet: First ~8 KiB of the response body, lower-cased
                by the caller for best results.

        Returns:
            ``True`` if a WAF challenge is detected, ``False`` otherwise.
        """
        # --- 1. Status code check ---
        if status_code in _WAF_STATUS_CODES:
            # Even with a WAF status code, we require at least one
            # confirming signal (header or body) to avoid false positives
            # on legitimate 403/503 pages.
            if self._headers_match(headers) or self._body_matches(body_snippet):
                return True

        # --- 2. Header-only detection (any status code) ---
        # Certain headers (e.g. x-datadome) are strong signals regardless
        # of the status code.
        if self._headers_match(headers):
            # But only flag as WAF if the status code is non-2xx.
            if status_code >= 400:
                return True

        # --- 3. Body-only detection (any status code) ---
        if self._body_matches(body_snippet) and status_code >= 400:
            return True

        # --- 4. Redirect-location detection ---
        if self._redirect_indicates_challenge(headers):
            return True

        return False

    # ------------------------------------------------------------------
    #  Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _headers_match(headers: dict[str, str]) -> bool:
        """Check whether any WAF-revealing headers are present.

        Comparison is case-insensitive on the header **names**.

        Args:
            headers: Response headers dictionary.

        Returns:
            ``True`` if at least one WAF header is found.
        """
        lower_keys = {k.lower() for k in headers}
        return any(sig in lower_keys for sig in WAF_HEADERS)

    @staticmethod
    def _body_matches(body_snippet: str) -> bool:
        """Check whether the body contains any WAF signature substrings.

        The search is case-insensitive.  Callers should pre-lower-case
        the snippet for performance.

        Args:
            body_snippet: Body text to scan (ideally lower-cased).

        Returns:
            ``True`` if at least one body signature is found.
        """
        lowered = body_snippet.lower()
        return any(sig in lowered for sig in BODY_SIGS)

    @staticmethod
    def _redirect_indicates_challenge(headers: dict[str, str]) -> bool:
        """Check whether the Location header suggests a WAF challenge page.

        Args:
            headers: Response headers dictionary.

        Returns:
            ``True`` if the redirect URL contains a challenge keyword.
        """
        location = headers.get("Location") or headers.get("location")
        if not location:
            return False

        lowered_location = location.lower()
        return any(sub in lowered_location for sub in _CHALLENGE_REDIRECT_SUBSTRS)
