"""CAPTCHA resolver infrastructure adapters."""
from .mock_resolver import MockCaptchaResolver
from .playwright_resolver import PlaywrightCaptchaResolver

__all__ = ["MockCaptchaResolver", "PlaywrightCaptchaResolver"]
