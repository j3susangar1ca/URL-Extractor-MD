import pytest
import logging
from url_extractor_md.domain.models import ProxyNode
from url_extractor_md.infrastructure.network.proxy_rotator import ProxyRotator

@pytest.mark.asyncio
async def test_proxy_rotator_empty():
    logger = logging.getLogger("test")
    rotator = ProxyRotator(proxies=(), logger=logger)
    # Empty rotator should return None
    assert await rotator.select(attempt=1) is None

@pytest.mark.asyncio
async def test_proxy_rotator_selection():
    logger = logging.getLogger("test")
    p1 = ProxyNode(url="http://proxy1.com", weight=1.0, health_score=1.0)
    p2 = ProxyNode(url="http://proxy2.com", weight=1.0, health_score=1.0)
    
    rotator = ProxyRotator(proxies=(p1, p2), logger=logger)
    
    selected = await rotator.select(attempt=1)
    assert selected is not None
    assert selected.url in ["http://proxy1.com", "http://proxy2.com"]

@pytest.mark.asyncio
async def test_proxy_rotator_weighted_selection():
    logger = logging.getLogger("test")
    p1 = ProxyNode(url="http://proxy1.com", weight=100.0, health_score=1.0)
    p2 = ProxyNode(url="http://proxy2.com", weight=0.001, health_score=1.0)
    
    rotator = ProxyRotator(proxies=(p1, p2), logger=logger)
    
    # Highly weighted proxy should be selected almost always
    selected = await rotator.select(attempt=1)
    assert selected is not None
    assert selected.url == "http://proxy1.com"

@pytest.mark.asyncio
async def test_proxy_rotator_exclude_dead():
    logger = logging.getLogger("test")
    p1 = ProxyNode(url="http://proxy1.com", weight=1.0, health_score=1.0)
    p2 = ProxyNode(url="http://proxy2.com", weight=1.0, health_score=1.0)
    
    rotator = ProxyRotator(proxies=(p1, p2), logger=logger)
    
    # Mark p1 as dead
    rotator.mark_dead(p1)
    
    # Now only p2 is eligible
    for _ in range(5):
        selected = await rotator.select(attempt=1)
        assert selected is not None
        assert selected.url == "http://proxy2.com"

@pytest.mark.asyncio
async def test_proxy_rotator_exclude_unhealthy():
    logger = logging.getLogger("test")
    # p1 health is below 0.3 threshold (0.2)
    p1 = ProxyNode(url="http://proxy1.com", weight=1.0, health_score=0.2)
    p2 = ProxyNode(url="http://proxy2.com", weight=1.0, health_score=1.0)
    
    rotator = ProxyRotator(proxies=(p1, p2), logger=logger)
    
    for _ in range(5):
        selected = await rotator.select(attempt=1)
        assert selected is not None
        assert selected.url == "http://proxy2.com"
