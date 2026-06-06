"""Mock CAPTCHA resolver for testing and development.

This module provides :class:`MockCaptchaResolver`, a zero-I/O stub that
implements the :class:`CaptchaResolverPort` protocol.  It returns a
deterministic mock token header without performing any network requests,
making it ideal for unit tests and local development where real CAPTCHA
solving is neither needed nor desirable.
"""

from __future__ import annotations

from ....domain.contracts import CaptchaResolverPort
from ....domain.models import ProxyNode


class MockCaptchaResolver:
    """Stub CAPTCHA resolver that returns a fixed mock token.

    Implements the :class:`CaptchaResolverPort` protocol by returning a
    hard-coded ``X-Captcha-Token`` header on every call.  No I/O is
    performed, making this resolver deterministic and safe for parallel
    test execution.

    Usage::

        resolver = MockCaptchaResolver()
        headers = await resolver.resolve(
            url="https://example.com/challenge",
            status_code=403,
            response_headers={"cf-ray": "abc123"},
        )
        assert headers == {"X-Captcha-Token": "mock_token_12345"}
    """

    __slots__ = ()

    async def resolve(
        self,
        url: str,
        status_code: int,
        response_headers: dict[str, str],
        proxy: ProxyNode | None = None,
    ) -> dict[str, str]:
        """Return a mock CAPTCHA token header.

        This method performs no I/O and returns immediately with a
        deterministic result, regardless of the input parameters.

        Args:
            url: Challenge URL (ignored).
            status_code: HTTP status code of the challenge response
                (ignored).
            response_headers: Headers from the challenge response
                (ignored).
            proxy: Optional proxy node for the resolver (ignored).

        Returns:
            Dictionary containing a single ``X-Captcha-Token`` entry
            with the fixed mock value ``"mock_token_12345"``.
        """
        return {"X-Captcha-Token": "mock_token_12345"}
