"""
Ferramentas genéricas de banco de dados (assíncrono)

Este módulo fornece encapsulamento de acesso ao banco de dados baseado no motor assíncrono do SQLAlchemy 2.x, com suporte a MySQL e PostgreSQL.
Localização das definições de modelos de dados:
- Nenhuma (este módulo fornece apenas ferramentas de conexão e consulta, não define modelos de dados)
"""

from __future__ import annotations
from urllib.parse import quote_plus
import asyncio
import os
from typing import Any, Dict, Iterable, List, Optional, Union

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy import text
from InsightEngine.utils.config import settings

__all__ = [
    "get_async_engine",
    "fetch_all",
]


_engine: Optional[AsyncEngine] = None


def _build_database_url() -> str:
    dialect: str = (settings.DB_DIALECT or "mysql").lower()
    host: str = settings.DB_HOST or ""
    port: str = str(settings.DB_PORT or "")
    user: str = settings.DB_USER or ""
    password: str = settings.DB_PASSWORD or ""
    db_name: str = settings.DB_NAME or ""

    if os.getenv("DATABASE_URL"):
        return os.getenv("DATABASE_URL")  # Usar diretamente a URL completa fornecida externamente

    password = quote_plus(password)

    if dialect in ("postgresql", "postgres"):
        # PostgreSQL usa driver asyncpg
        return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db_name}"

    # MySQL padrão usa driver aiomysql
    return f"mysql+aiomysql://{user}:{password}@{host}:{port}/{db_name}"


def get_async_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        database_url: str = _build_database_url()
        _engine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
    return _engine


async def fetch_all(query: str, params: Optional[Union[Iterable[Any], Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """
    Executar consulta somente leitura e retornar lista de dicionários.
    """
    engine: AsyncEngine = get_async_engine()
    async with engine.connect() as conn:
        result = await conn.execute(text(query), params or {})
        rows = result.mappings().all()
        # Converter RowMapping para dicionários comuns
        return [dict(row) for row in rows]
