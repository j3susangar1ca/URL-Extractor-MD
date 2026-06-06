import asyncio
import logging
import pytest
from unittest.mock import MagicMock, AsyncMock, call

from url_extractor_md.config import SystemConfig
from url_extractor_md.domain.models import BrowserProfile, ProxyNode, Stage, ProgressEvent
from url_extractor_md.domain.exceptions import NetworkError

from curl_cffi.requests import RequestsError
from url_extractor_md.infrastructure.network.curl_engine import CurlCffiEngine

@pytest.fixture
def logger():
    return logging.getLogger("test_curl_engine")

@pytest.fixture
def config():
    return SystemConfig(
        base_headers={"Accept": "text/html"},
        request_timeout_sec=10,
        chunk_size_bytes=1024
    )

@pytest.fixture
def profile():
    return BrowserProfile(
        impersonate_id="chrome110",
        user_agent="Mozilla/5.0 Test",
        tls_fingerprint="modern"
    )

@pytest.mark.asyncio
async def test_download_happy_path(logger, config, profile):
    # Arrange
    mock_progress_callback = AsyncMock()
    engine = CurlCffiEngine(config=config, logger=logger, progress_callback=mock_progress_callback)

    mock_session = AsyncMock()
    # Mock the internal dictionary directly since _get_or_create_session just returns from it
    engine._sessions[profile.impersonate_id] = mock_session

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"Content-Type": "text/html; charset=utf-8"}
    mock_response.encoding = "utf-8"

    # Create an async generator for chunks
    async def mock_aiter_content(chunk_size):
        yield b"chunk1"
        yield b"chunk2"

    mock_response.aiter_content = mock_aiter_content
    mock_session.get.return_value = mock_response

    proxy = ProxyNode(url="http://proxy:8080")
    extra_headers = {"X-Extra": "value"}

    # Act
    payload, status_code, encoding, resp_headers = await engine.download(
        url="https://example.com",
        profile=profile,
        proxy=proxy,
        extra_headers=extra_headers,
        cancel_event=None
    )

    # Assert
    assert payload == b"chunk1chunk2"
    assert status_code == 200
    assert encoding == "utf-8"
    assert resp_headers == {"Content-Type": "text/html; charset=utf-8"}

    mock_session.get.assert_called_once_with(
        "https://example.com",
        headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0 Test", "X-Extra": "value"},
        proxy="http://proxy:8080",
        timeout=10,
        allow_redirects=True,
        stream=True
    )

    assert mock_progress_callback.call_count == 2
    args_1 = mock_progress_callback.call_args_list[0][0][0]
    assert args_1.stage == Stage.DOWNLOADING
    assert args_1.meta["bytes_received"] == 6

@pytest.mark.asyncio
async def test_download_engine_closed(logger, config, profile):
    # Arrange
    engine = CurlCffiEngine(config=config, logger=logger)
    # Using object.__setattr__ since __slots__ without setter makes it read-only
    object.__setattr__(engine, "_closed", True)

    # Act & Assert
    with pytest.raises(NetworkError) as exc_info:
        await engine.download("https://example.com", profile, None, None, None)
    assert exc_info.value.code == "BE-301"

@pytest.mark.asyncio
async def test_download_cancelled_before_request(logger, config, profile):
    # Arrange
    engine = CurlCffiEngine(config=config, logger=logger)
    cancel_event = MagicMock()
    cancel_event.is_set.return_value = True

    # Act & Assert
    with pytest.raises(NetworkError) as exc_info:
        await engine.download("https://example.com", profile, None, None, cancel_event)
    assert exc_info.value.code == "BE-302"

@pytest.mark.asyncio
async def test_download_cancelled_during_streaming(logger, config, profile):
    # Arrange
    engine = CurlCffiEngine(config=config, logger=logger)
    mock_session = AsyncMock()
    engine._sessions[profile.impersonate_id] = mock_session

    mock_response = MagicMock()
    mock_response.status_code = 200

    cancel_event = MagicMock()
    # Initially False (before request), then True (during streaming)
    cancel_event.is_set.side_effect = [False, True]

    async def mock_aiter_content(chunk_size):
        yield b"chunk1"
        yield b"chunk2"

    mock_response.aiter_content = mock_aiter_content
    mock_session.get.return_value = mock_response

    # Act & Assert
    with pytest.raises(NetworkError) as exc_info:
        await engine.download("https://example.com", profile, None, None, cancel_event)
    assert exc_info.value.code == "BE-302"

@pytest.mark.asyncio
async def test_download_requests_error_mapping(logger, config, profile):
    # Arrange
    engine = CurlCffiEngine(config=config, logger=logger)
    mock_session = AsyncMock()
    engine._sessions[profile.impersonate_id] = mock_session
    mock_session.get.side_effect = RequestsError("timeout")

    # Act & Assert
    with pytest.raises(NetworkError) as exc_info:
        await engine.download("https://example.com", profile, None, None, None)
    assert exc_info.value.code == "BE-300"

@pytest.mark.asyncio
async def test_download_cancelled_error(logger, config, profile):
    # Arrange
    engine = CurlCffiEngine(config=config, logger=logger)
    mock_session = AsyncMock()
    engine._sessions[profile.impersonate_id] = mock_session
    mock_session.get.side_effect = asyncio.CancelledError()

    # Act & Assert
    with pytest.raises(NetworkError) as exc_info:
        await engine.download("https://example.com", profile, None, None, None)
    assert exc_info.value.code == "BE-302"

@pytest.mark.asyncio
async def test_download_unexpected_error_mapping(logger, config, profile):
    # Arrange
    engine = CurlCffiEngine(config=config, logger=logger)
    mock_session = AsyncMock()
    engine._sessions[profile.impersonate_id] = mock_session
    mock_session.get.side_effect = ValueError("Some weird error")

    # Act & Assert
    with pytest.raises(NetworkError) as exc_info:
        await engine.download("https://example.com", profile, None, None, None)
    assert exc_info.value.code == "BE-300"

def test_detect_encoding_fallback():
    # Arrange
    class MockResp:
        pass
    resp = MockResp()

    # Case 1: No headers
    assert CurlCffiEngine._detect_encoding(resp) == "utf-8"

    # Case 2: Content-Type with charset
    resp.headers = {"Content-Type": "text/html; charset=iso-8859-1"}
    assert CurlCffiEngine._detect_encoding(resp) == "iso-8859-1"

    # Case 3: Encoding attribute
    resp.headers = {"Content-Type": "text/html"}
    resp.encoding = "windows-1252"
    assert CurlCffiEngine._detect_encoding(resp) == "windows-1252"

def test_build_headers_merge(config, profile):
    # Arrange
    config_copy = SystemConfig(
        base_headers={"Accept": "text/html", "Accept-Encoding": "gzip"},
        request_timeout_sec=10,
        chunk_size_bytes=1024
    )
    extra_headers = {"Authorization": "Bearer token"}

    # Act
    headers = CurlCffiEngine._build_headers(config_copy, profile, extra_headers)

    # Assert
    assert headers["Accept"] == "text/html"
    assert headers["Accept-Encoding"] == "gzip"
    assert headers["User-Agent"] == "Mozilla/5.0 Test"
    assert headers["Authorization"] == "Bearer token"


@pytest.mark.asyncio
async def test_close_engine(logger, config, profile):
    # Arrange
    engine = CurlCffiEngine(config=config, logger=logger)
    mock_session = AsyncMock()
    engine._sessions[profile.impersonate_id] = mock_session

    # Act
    await engine.close()

    # Assert
    assert engine._closed is True
    mock_session.close.assert_called_once()
    assert len(engine._sessions) == 0

    # Idempotency check
    await engine.close() # Shouldn't raise or call close again
    assert mock_session.close.call_count == 1

def test_extract_headers_fallback():
    # Arrange
    class MockResp:
        pass
    resp = MockResp()

    # Test valid dict-like headers
    resp.headers = {"Content-Type": "text/html"}
    assert CurlCffiEngine._extract_headers(resp) == {"Content-Type": "text/html"}

    # Test no headers
    resp.headers = None
    assert CurlCffiEngine._extract_headers(resp) == {}

    # Test multi-dict fallback (if .items() fails)
    class BadHeaders:
        def keys(self):
            return ["X-Test"]
        def get(self, key, default):
            return "Value"
    resp.headers = BadHeaders()
    assert CurlCffiEngine._extract_headers(resp) == {"X-Test": "Value"}

def test_get_or_create_session(logger, config):
    engine = CurlCffiEngine(config=config, logger=logger)

    # First call creates the session
    session1 = engine._get_or_create_session("chrome110")
    assert "chrome110" in engine._sessions

    # Second call returns the exact same object
    session2 = engine._get_or_create_session("chrome110")
    assert session1 is session2


def test_is_cancelled():
    # Case 1: None
    assert CurlCffiEngine._is_cancelled(None) is False

    # Case 2: Object without is_set
    assert CurlCffiEngine._is_cancelled(object()) is False

    # Case 3: Object with is_set returning False
    cancel_event_false = MagicMock()
    cancel_event_false.is_set.return_value = False
    assert CurlCffiEngine._is_cancelled(cancel_event_false) is False

    # Case 4: Object with is_set returning True
    cancel_event_true = MagicMock()
    cancel_event_true.is_set.return_value = True
    assert CurlCffiEngine._is_cancelled(cancel_event_true) is True


@pytest.mark.asyncio
async def test_emit_progress_sync(logger, config):
    # Arrange
    mock_cb = MagicMock()
    engine = CurlCffiEngine(config=config, logger=logger, progress_callback=mock_cb)
    event = ProgressEvent(stage=Stage.DOWNLOADING, percent=0, message="")

    # Act
    await engine._emit_progress(event)

    # Assert
    mock_cb.assert_called_once_with(event)

@pytest.mark.asyncio
async def test_download_empty_chunk(logger, config, profile):
    # Arrange
    engine = CurlCffiEngine(config=config, logger=logger)
    mock_session = AsyncMock()
    engine._sessions[profile.impersonate_id] = mock_session

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.encoding = "utf-8"

    # Yield an empty chunk
    async def mock_aiter_content(chunk_size):
        yield b""
        yield b"chunk"

    mock_response.aiter_content = mock_aiter_content
    mock_session.get.return_value = mock_response

    # Act
    payload, status_code, encoding, resp_headers = await engine.download(
        "https://example.com", profile, None, None, None
    )

    # Assert
    assert payload == b"chunk" # Empty chunk was ignored
