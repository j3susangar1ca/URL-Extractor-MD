import asyncio
import logging
from url_extractor_md.config import SystemConfig
from url_extractor_md.application.pipeline import ScrapePipeline
from url_extractor_md.infrastructure.validation.input_validator import InputValidator
from url_extractor_md.infrastructure.network.curl_engine import CurlCffiEngine
from url_extractor_md.infrastructure.network.waf_detector import WAFDetector
from url_extractor_md.infrastructure.network.proxy_rotator import ProxyRotator
from url_extractor_md.infrastructure.storage.atomic_storage import AtomicStorageEngine
from url_extractor_md.infrastructure.extraction.isolated_extractor import IsolatedExtractor
from url_extractor_md.infrastructure.network.captcha.playwright_resolver import PlaywrightCaptchaResolver

async def main():
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger("TestRAG")
    
    config = SystemConfig()
    validator = InputValidator()
    waf_detector = WAFDetector()
    proxy_selector = ProxyRotator(proxies=(), logger=logger)
    storage = AtomicStorageEngine(logger=logger)
    extractor = IsolatedExtractor(max_workers=config.extract_workers)
    network = CurlCffiEngine(config=config, logger=logger)
    
    try:
        captcha_resolver = PlaywrightCaptchaResolver(logger=logger)
    except Exception:
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
    
    url = "https://www.monografias.com/trabajos56/jardin-infantil/jardin-infantil"
    print("--- RUNNING PREVIEW ---")
    res = await pipeline.execute(
        url=url,
        output_filename="test_jardin",
        output_directory="gui_output",
        preview_only=True
    )
    print("Preview success:", res.success)
    print("Preview status_code:", res.status_code)
    print("Preview word_count:", res.word_count)
    print("Preview error:", res.error)
    
    print("\n--- RUNNING FULL EXTRACTION ---")
    res_full = await pipeline.execute(
        url=url,
        output_filename="test_jardin",
        output_directory="gui_output",
        preview_only=False
    )
    print("Full success:", res_full.success)
    print("Full output_path:", res_full.output_path)
    print("Full word_count:", res_full.word_count)
    
    await network.close()

if __name__ == "__main__":
    asyncio.run(main())
