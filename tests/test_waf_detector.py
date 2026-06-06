import pytest
from url_extractor_md.infrastructure.network.waf_detector import WAFDetector

def test_waf_detector_clean():
    detector = WAFDetector()
    # Legitimate pages should return False
    assert not detector.detect(200, {"content-type": "text/html"}, "<html>Hello world</html>")
    assert not detector.detect(404, {"content-type": "text/html"}, "<html>Not Found</html>")

def test_waf_detector_by_status_and_header():
    detector = WAFDetector()
    # 403 status code with Cloudflare CF-Ray header
    assert detector.detect(403, {"cf-ray": "12345abcd"}, "<html>normal html?</html>")
    # 503 status code with Sucuri header
    assert detector.detect(503, {"x-sucuri-id": "cache-id"}, "<html>normal html?</html>")

def test_waf_detector_by_status_and_body():
    detector = WAFDetector()
    # 403 status code with CF Turnstile signature in body
    assert detector.detect(403, {"content-type": "text/html"}, "<html>cf-turnstile-something</html>")

def test_waf_detector_header_non_2xx():
    detector = WAFDetector()
    # Header match with status code 400
    assert detector.detect(400, {"x-datadome": "1"}, "<html></html>")
    # Header match with status 200 (should be False unless redirect)
    assert not detector.detect(200, {"x-datadome": "1"}, "<html></html>")

def test_waf_detector_body_non_2xx():
    detector = WAFDetector()
    # Body signature matches with status code 403
    assert detector.detect(403, {}, "just a moment, verifying browser...")
    # Body signature with status 200 should not trigger WAF
    assert not detector.detect(200, {}, "just a moment, verifying browser...")

def test_waf_detector_redirect():
    detector = WAFDetector()
    # Redirect URL contains challenge keywords
    assert detector.detect(302, {"Location": "/challenge/verify?id=123"}, "<html></html>")
    assert detector.detect(301, {"location": "/captcha-solved"}, "<html></html>")
    # Redirect to normal URL should be False
    assert not detector.detect(302, {"Location": "/welcome"}, "<html></html>")
