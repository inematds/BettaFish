"""
Crawler para TikTok usando Apify.
Requer: pip install apify-client
Credenciais: APIFY_API_TOKEN no .env
"""
from .base import WesternPlatformCrawler, CrawledContent, CrawledComment
from typing import List
from loguru import logger


class TikTokCrawler(WesternPlatformCrawler):
    platform_name = "tiktok"

    def __init__(self):
        # TODO: Implementar com apify-client
        logger.info("TikTokCrawler inicializado (stub - aguardando implementação)")

    def search(self, keywords: List[str], max_results: int = 100) -> List[CrawledContent]:
        logger.warning("TikTokCrawler.search() ainda não implementado")
        return []

    def get_comments(self, content_id: str, max_comments: int = 100) -> List[CrawledComment]:
        logger.warning("TikTokCrawler.get_comments() ainda não implementado")
        return []

    def is_configured(self) -> bool:
        import os
        return bool(os.getenv("APIFY_API_TOKEN"))
