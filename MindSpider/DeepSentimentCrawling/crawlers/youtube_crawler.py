"""
Crawler para YouTube usando Google API (YouTube Data API v3).
Requer: pip install google-api-python-client
Credenciais: YOUTUBE_API_KEY no .env
"""
from .base import WesternPlatformCrawler, CrawledContent, CrawledComment
from typing import List
from loguru import logger


class YouTubeCrawler(WesternPlatformCrawler):
    platform_name = "youtube"

    def __init__(self):
        # TODO: Implementar com google-api-python-client
        logger.info("YouTubeCrawler inicializado (stub - aguardando implementação)")

    def search(self, keywords: List[str], max_results: int = 100) -> List[CrawledContent]:
        logger.warning("YouTubeCrawler.search() ainda não implementado")
        return []

    def get_comments(self, content_id: str, max_comments: int = 100) -> List[CrawledComment]:
        logger.warning("YouTubeCrawler.get_comments() ainda não implementado")
        return []

    def is_configured(self) -> bool:
        import os
        return bool(os.getenv("YOUTUBE_API_KEY"))
