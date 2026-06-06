"""Unicode-safe slug generation with accent translation.

All public methods are class methods — the generator never needs
to be instantiated.  This design eliminates the contradictory
__init__ that previously claimed to "prevent instantiation".
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

# Accent translation map for Latin characters
_ACCENT_TRANSLATION_MAP: dict[int, str] = {
    ord("\u00e0"): "a", ord("\u00e1"): "a", ord("\u00e2"): "a",
    ord("\u00e3"): "a", ord("\u00e4"): "a", ord("\u00e5"): "a",
    ord("\u00e8"): "e", ord("\u00e9"): "e", ord("\u00ea"): "e",
    ord("\u00eb"): "e",
    ord("\u00ec"): "i", ord("\u00ed"): "i", ord("\u00ee"): "i",
    ord("\u00ef"): "i",
    ord("\u00f2"): "o", ord("\u00f3"): "o", ord("\u00f4"): "o",
    ord("\u00f5"): "o", ord("\u00f6"): "o",
    ord("\u00f9"): "u", ord("\u00fa"): "u", ord("\u00fb"): "u",
    ord("\u00fc"): "u",
    ord("\u00f1"): "n",
    ord("\u00e7"): "c",
    ord("\u00df"): "ss",
    ord("\u00c0"): "A", ord("\u00c1"): "A", ord("\u00c2"): "A",
    ord("\u00c3"): "A", ord("\u00c4"): "A", ord("\u00c5"): "A",
    ord("\u00c8"): "E", ord("\u00c9"): "E", ord("\u00ca"): "E",
    ord("\u00cb"): "E",
    ord("\u00cc"): "I", ord("\u00cd"): "I", ord("\u00ce"): "I",
    ord("\u00cf"): "I",
    ord("\u00d2"): "O", ord("\u00d3"): "O", ord("\u00d4"): "O",
    ord("\u00d5"): "O", ord("\u00d6"): "O",
    ord("\u00d9"): "U", ord("\u00da"): "U", ord("\u00db"): "U",
    ord("\u00dc"): "U",
    ord("\u00d1"): "N",
    ord("\u00c7"): "C",
}

_RE_SEPARATOR = re.compile(r"[^\w\-]+")
_RE_DASHES = re.compile(r"-{2,}")
_RE_EDGE_DASHES = re.compile(r"^-+|-+$")


class SlugGenerator:
    """Unicode-safe slug generation — all methods are class methods."""

    @classmethod
    def generate(cls, text: str, max_length: int = 120) -> str:
        """Generate a URL-safe slug from arbitrary text.

        Args:
            text: Input string (may contain Unicode, accents, spaces).
            max_length: Maximum characters in the resulting slug.

        Returns:
            Lowercase, hyphen-separated slug string.
        """
        text = text.translate(_ACCENT_TRANSLATION_MAP)
        text = unicodedata.normalize("NFKD", text)
        text = text.encode("ascii", "ignore").decode("ascii")
        text = text.lower()
        text = _RE_SEPARATOR.sub("-", text)
        text = _RE_DASHES.sub("-", text)
        text = _RE_EDGE_DASHES.sub("", text)
        return text[:max_length].rstrip("-")

    @classmethod
    def from_url(cls, url: str) -> str:
        """Generate a slug from the path segments of a URL.

        Args:
            url: Absolute or relative URL.

        Returns:
            Slug derived from the last meaningful path segment.
        """
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        segments = [s for s in path.split("/") if s and not s.isdigit()]
        if not segments:
            return cls.generate(parsed.netloc)
        candidate = segments[-1]
        candidate = re.sub(r"\.(html?|php|asp|aspx|jsp)$", "", candidate)
        return cls.generate(candidate)
