"""
Framework base para crawlers de plataformas ocidentais.
Cada crawler implementa esta interface para integração com o BettaFish.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
from loguru import logger


@dataclass
class CrawledContent:
    """Conteúdo coletado de uma plataforma"""
    platform: str
    content_id: str
    title: str
    content: str
    author: str
    author_id: Optional[str] = None
    url: Optional[str] = None
    created_at: Optional[datetime] = None
    likes: int = 0
    comments_count: int = 0
    shares: int = 0
    views: int = 0
    source_keyword: Optional[str] = None
    extra_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CrawledComment:
    """Comentário coletado de uma plataforma"""
    platform: str
    comment_id: str
    content_id: str
    content: str
    author: str
    author_id: Optional[str] = None
    created_at: Optional[datetime] = None
    likes: int = 0
    parent_comment_id: Optional[str] = None
    extra_data: Dict[str, Any] = field(default_factory=dict)


class WesternPlatformCrawler(ABC):
    """Classe base abstrata para crawlers de plataformas ocidentais"""

    platform_name: str = "unknown"

    @abstractmethod
    def search(self, keywords: List[str], max_results: int = 100) -> List[CrawledContent]:
        """Buscar conteúdo por palavras-chave"""
        pass

    @abstractmethod
    def get_comments(self, content_id: str, max_comments: int = 100) -> List[CrawledComment]:
        """Obter comentários de um conteúdo específico"""
        pass

    def is_configured(self) -> bool:
        """Verificar se as credenciais estão configuradas"""
        return False

    def get_status(self) -> Dict[str, Any]:
        """Retornar status do crawler"""
        return {
            "platform": self.platform_name,
            "configured": self.is_configured(),
            "available": self.is_configured()
        }
