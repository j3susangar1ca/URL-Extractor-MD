import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

from url_extractor_md.config import SystemConfig
from url_extractor_md.application.pipeline import ScrapePipeline
from url_extractor_md.domain.models import ProgressEvent, Stage, ProxyNode, BrowserProfile
from url_extractor_md.domain.exceptions import ValidationError

@pytest.mark.asyncio
async def test_pipeline_successful_run():
    # Setup mocks
    validator = MagicMock()
    validator.validate_url.return_value = None
    validator.validate_filename.side_effect = lambda f: f
    validator.validate_directory.side_effect = lambda d: Path(d)
    
    proxy_selector = AsyncMock()
    proxy_selector.select.return_value = None
    
    network = AsyncMock()
    network.download.return_value = (b"<html>body</html>", 200, "utf-8", {"Content-Type": "text/html"})
    
    waf_detector = MagicMock()
    waf_detector.detect.return_value = False
    
    captcha_resolver = AsyncMock()
    
    extractor = AsyncMock()
    extractor.extract.return_value = {
        "page_metadata": {
            "title": "Mock Title",
            "author": "Mock Author",
            "site_name": "Mock Site",
            "description": "Mock Description",
            "content_type": "article",
            "language": "en",
            "published_date": "2026-06-05",
            "keywords": ["test"],
            "canonical_url": "https://example.com/mock",
            "og_image": "https://example.com/mock.png"
        },
        "clean_markdown": "# Mock Title\nMock body",
        "word_count": 4,
        "paragraph_count": 1,
        "links_found": [],
        "images_found": [],
        "errors": []
    }
    
    storage = AsyncMock()
    storage.save_markdown.side_effect = lambda path, content: path
    
    pipeline = ScrapePipeline(
        network=network,
        extractor=extractor,
        storage=storage,
        captcha_resolver=captcha_resolver,
        waf_detector=waf_detector,
        proxy_selector=proxy_selector,
        validator=validator,
    )
    
    # Progress callback recorder
    events = []
    def progress_callback(event: ProgressEvent):
        events.append(event)
        
    result = await pipeline.execute(
        url="https://example.com",
        output_filename="test_file",
        output_directory="/tmp",
        callback=progress_callback
    )
    
    assert result.success
    assert result.word_count == 4
    assert result.content_type == "article"
    
    # Verify events contains INIT, CONNECTING, PARSING, SAVING, COMPLETED
    stages = [e.stage for e in events]
    assert Stage.INIT in stages
    assert Stage.CONNECTING in stages
    assert Stage.PARSING in stages
    assert Stage.SAVING in stages
    assert Stage.COMPLETED in stages

@pytest.mark.asyncio
async def test_pipeline_waf_triggered():
    # Setup mocks
    validator = MagicMock()
    validator.validate_url.return_value = None
    validator.validate_filename.side_effect = lambda f: f
    validator.validate_directory.side_effect = lambda d: Path(d)
    
    proxy_selector = AsyncMock()
    proxy_selector.select.return_value = None
    
    network = AsyncMock()
    # First download returns WAF challenge status 403, second returns clean response 200
    network.download.side_effect = [
        (b"<html>challenge</html>", 403, "utf-8", {"Content-Type": "text/html"}),
        (b"<html>payload</html>", 200, "utf-8", {"Content-Type": "text/html"})
    ]
    
    waf_detector = MagicMock()
    # Detect WAF challenge first time, but not second time
    waf_detector.detect.side_effect = [True, False]
    
    captcha_resolver = AsyncMock()
    captcha_resolver.resolve.return_value = {"Cookie": "solved=1"}
    
    extractor = AsyncMock()
    extractor.extract.return_value = {
        "page_metadata": {
            "title": "Mock Title",
            "content_type": "blog",
        },
        "clean_markdown": "solved markdown",
        "word_count": 2,
        "paragraph_count": 1,
        "links_found": [],
        "images_found": [],
        "errors": []
    }
    
    storage = AsyncMock()
    storage.save_markdown.side_effect = lambda path, content: path
    
    pipeline = ScrapePipeline(
        network=network,
        extractor=extractor,
        storage=storage,
        captcha_resolver=captcha_resolver,
        waf_detector=waf_detector,
        proxy_selector=proxy_selector,
        validator=validator,
    )
    
    events = []
    def progress_callback(event: ProgressEvent):
        events.append(event)
        
    result = await pipeline.execute(
        url="https://example.com",
        output_filename="test_file",
        output_directory="/tmp",
        callback=progress_callback
    )
    
    assert result.success
    assert result.waf_detected
    assert result.captcha_solved
    assert captcha_resolver.resolve.called
    
    # Check that it solved WAF and CAPTCHA stages
    stages = [e.stage for e in events]
    assert Stage.WAF_DETECTED in stages
    assert Stage.CAPTCHA_SOLVING in stages

@pytest.mark.asyncio
async def test_pipeline_cancellation():
    validator = MagicMock()
    validator.validate_url.return_value = None
    validator.validate_filename.side_effect = lambda f: f
    validator.validate_directory.side_effect = lambda d: Path(d)
    
    proxy_selector = AsyncMock()
    network = AsyncMock()
    waf_detector = MagicMock()
    captcha_resolver = AsyncMock()
    extractor = AsyncMock()
    storage = AsyncMock()
    
    pipeline = ScrapePipeline(
        network=network,
        extractor=extractor,
        storage=storage,
        captcha_resolver=captcha_resolver,
        waf_detector=waf_detector,
        proxy_selector=proxy_selector,
        validator=validator,
    )
    
    cancel_event = asyncio.Event()
    cancel_event.set()  # set cancellation immediately
    
    result = await pipeline.execute(
        url="https://example.com",
        output_filename="test_file",
        output_directory="/tmp",
        cancel_event=cancel_event
    )
    
    assert not result.success
    assert "Cancelado" in result.error
