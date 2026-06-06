import sys
import logging
from PySide6.QtWidgets import QApplication

from url_extractor_md.config import SystemConfig
from url_extractor_md.application.pipeline import ScrapePipeline
from url_extractor_md.adapters.qt_frontend import EliteScraperUI

from url_extractor_md.infrastructure.validation.input_validator import InputValidator
from url_extractor_md.infrastructure.network.curl_engine import CurlCffiEngine
from url_extractor_md.infrastructure.network.waf_detector import WAFDetector
from url_extractor_md.infrastructure.network.proxy_rotator import ProxyRotator
from url_extractor_md.infrastructure.storage.atomic_storage import AtomicStorageEngine
from url_extractor_md.infrastructure.extraction.isolated_extractor import IsolatedExtractor
from url_extractor_md.infrastructure.network.captcha.playwright_resolver import PlaywrightCaptchaResolver

def main():
    # Setup logger
    logger = logging.getLogger("EliteScraper")
    logger.setLevel(logging.DEBUG)
    
    # Configure dependencies
    config = SystemConfig()
    validator = InputValidator()
    waf_detector = WAFDetector()
    proxy_selector = ProxyRotator(proxies=(), logger=logger)
    storage = AtomicStorageEngine(logger=logger)
    extractor = IsolatedExtractor(max_workers=config.extract_workers)
    
    # Instantiate curl network engine
    network = CurlCffiEngine(config=config, logger=logger)
    
    # CAPTCHA resolver
    try:
        captcha_resolver = PlaywrightCaptchaResolver(logger=logger)
    except Exception as exc:
        logger.warning(f"Failed to load PlaywrightCaptchaResolver, falling back to mock: {exc}")
        from url_extractor_md.infrastructure.network.captcha.mock_resolver import MockCaptchaResolver
        captcha_resolver = MockCaptchaResolver(logger=logger)
        
    pipeline = ScrapePipeline(
        network=network,
        extractor=extractor,
        storage=storage,
        captcha_resolver=captcha_resolver,
        waf_detector=waf_detector,
        proxy_selector=proxy_selector,
        validator=validator,
        config=config,
        logger=logger,
    )
    
    app = QApplication(sys.argv)
    window = EliteScraperUI(pipeline=pipeline)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
