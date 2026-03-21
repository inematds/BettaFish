"""
MindSpider - Inicializacao do banco de dados (SQLAlchemy 2.x motor assincrono)

Este script cria as tabelas de extensao do MindSpider (separadas das tabelas originais do MediaCrawler).
Suporta MySQL e PostgreSQL, requer uma instancia de banco de dados ja conectavel.

Localizacao da definicao dos modelos de dados:
- MindSpider/schema/models_sa.py
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional
from urllib.parse import quote_plus
from loguru import logger

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

from models_sa import Base

# Importar models_bigdata para garantir que todas as classes de tabela sejam registradas no Base.metadata
# models_bigdata agora tambem usa o Base de models_sa, entao todas as tabelas estao no mesmo metadata
import models_bigdata  # noqa: F401  # Importar para registrar todas as classes de tabela
import sys
from pathlib import Path

# Adicionar diretorio raiz do projeto ao path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from config import settings

def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(key)
    return v if v not in (None, "") else default


def _build_database_url() -> str:
    # Prioridade para DATABASE_URL
    database_url = settings.DATABASE_URL if hasattr(settings, "DATABASE_URL") else None
    if database_url:
        return database_url

    dialect = (settings.DB_DIALECT or "mysql").lower()
    host = settings.DB_HOST or "localhost"
    port = str(settings.DB_PORT or ("3306" if dialect == "mysql" else "5432"))
    user = settings.DB_USER or "root"
    password = settings.DB_PASSWORD or ""
    password = quote_plus(password)
    db_name = settings.DB_NAME or "mindspider"

    if dialect in ("postgresql", "postgres"):
        return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db_name}"

    return f"mysql+aiomysql://{user}:{password}@{host}:{port}/{db_name}"


async def _create_views_if_needed(engine_dialect: str):
    # Views sao opcionais; criar apenas quando necessario para o negocio. Ambos os lados usam SQL agregado generico para evitar funcoes especificas de dialeto.
    # Se nao precisar de views, pode pular.
    engine_dialect = engine_dialect.lower()
    v_topic_crawling_stats = (
        "CREATE OR REPLACE VIEW v_topic_crawling_stats AS\n"
        "SELECT dt.topic_id, dt.topic_name, dt.extract_date, dt.processing_status,\n"
        "       COUNT(DISTINCT ct.task_id) AS total_tasks,\n"
        "       SUM(CASE WHEN ct.task_status = 'completed' THEN 1 ELSE 0 END) AS completed_tasks,\n"
        "       SUM(CASE WHEN ct.task_status = 'failed' THEN 1 ELSE 0 END) AS failed_tasks,\n"
        "       SUM(COALESCE(ct.total_crawled,0)) AS total_content_crawled,\n"
        "       SUM(COALESCE(ct.success_count,0)) AS total_success_count,\n"
        "       SUM(COALESCE(ct.error_count,0)) AS total_error_count\n"
        "FROM daily_topics dt\n"
        "LEFT JOIN crawling_tasks ct ON dt.topic_id = ct.topic_id\n"
        "GROUP BY dt.topic_id, dt.topic_name, dt.extract_date, dt.processing_status"
    )

    v_daily_summary = (
        "CREATE OR REPLACE VIEW v_daily_summary AS\n"
        "SELECT dn.crawl_date AS crawl_date,\n"
        "       COUNT(DISTINCT dn.news_id) AS total_news,\n"
        "       COUNT(DISTINCT dn.source_platform) AS platforms_covered,\n"
        "       (SELECT COUNT(*) FROM daily_topics WHERE extract_date = dn.crawl_date) AS topics_extracted,\n"
        "       (SELECT COUNT(*) FROM crawling_tasks WHERE scheduled_date = dn.crawl_date) AS tasks_created\n"
        "FROM daily_news dn\n"
        "GROUP BY dn.crawl_date\n"
        "ORDER BY dn.crawl_date DESC"
    )

    # CREATE OR REPLACE VIEW do PostgreSQL tambem funciona; executar em ambos
    from sqlalchemy.ext.asyncio import AsyncEngine
    engine: AsyncEngine = create_async_engine(_build_database_url())
    async with engine.begin() as conn:
        await conn.execute(text(v_topic_crawling_stats))
        await conn.execute(text(v_daily_summary))
    await engine.dispose()


async def main() -> None:
    database_url = _build_database_url()
    engine = create_async_engine(database_url, pool_pre_ping=True, pool_recycle=1800)

    # Como models_bigdata e models_sa agora compartilham o mesmo Base, todas as tabelas estao no mesmo metadata
    # So precisa criar uma vez, o SQLAlchemy trata automaticamente as dependencias entre tabelas
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Manter logica original de criacao de views e liberacao
    dialect_name = engine.url.get_backend_name()
    await _create_views_if_needed(dialect_name)

    await engine.dispose()
    logger.info("[init_database_sa] Criacao de tabelas e views concluida")


if __name__ == "__main__":
    asyncio.run(main())
