import pytest
from url_extractor_md.infrastructure.extraction.slug_generator import SlugGenerator

def test_generate_simple():
    assert SlugGenerator.generate("Hello World") == "hello-world"

def test_generate_accents():
    # Spanish characters/accents
    assert SlugGenerator.generate("Historia de los niños y bebés en España") == "historia-de-los-ninos-y-bebes-en-espana"
    # German/French characters
    assert SlugGenerator.generate("Mädchen über café") == "madchen-uber-cafe"

def test_generate_formatting():
    # Multiple dashes, punctuation, edge cases
    assert SlugGenerator.generate("---Too many   dashes---") == "too-many-dashes"
    assert SlugGenerator.generate("Hello, World!!! How's it going?") == "hello-world-how-s-it-going"

def test_generate_max_length():
    text = "a" * 150
    slug = SlugGenerator.generate(text, max_length=50)
    assert len(slug) == 50
    assert slug == "a" * 50

def test_from_url():
    # standard clean path segment
    assert SlugGenerator.from_url("https://example.com/some/article-title") == "article-title"
    # extension strip
    assert SlugGenerator.from_url("https://example.com/index.html") == "index"
    assert SlugGenerator.from_url("https://example.com/post.php") == "post"
    # ignore digit path segments
    assert SlugGenerator.from_url("https://example.com/blog/2026/06/05/my-first-post") == "my-first-post"
    # fallback to netloc
    assert SlugGenerator.from_url("https://example.com/") == "example-com"
    assert SlugGenerator.from_url("https://sub.domain.org/123/456/") == "sub-domain-org"
