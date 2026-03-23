"""
Crawler para Reddit usando PRAW (Python Reddit API Wrapper).
Requer: pip install praw
Credenciais: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET no .env
"""
from .base import WesternPlatformCrawler, CrawledContent, CrawledComment
from typing import List
from loguru import logger


class RedditCrawler(WesternPlatformCrawler):
    platform_name = "reddit"

    def __init__(self):
        # TODO: Implementar com PRAW
        logger.info("RedditCrawler inicializado (stub - aguardando implementação)")

    def search(self, keywords: List[str], max_results: int = 100) -> List[CrawledContent]:
        logger.warning("RedditCrawler.search() ainda não implementado")
        return []

    def get_comments(self, content_id: str, max_comments: int = 100) -> List[CrawledComment]:
        logger.warning("RedditCrawler.get_comments() ainda não implementado")
        return []

    def is_configured(self) -> bool:
        import os
        return bool(os.getenv("REDDIT_CLIENT_ID") and os.getenv("REDDIT_CLIENT_SECRET"))
