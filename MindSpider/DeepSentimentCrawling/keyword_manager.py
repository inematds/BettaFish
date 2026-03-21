#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modulo DeepSentimentCrawling - Gerenciador de palavras-chave
Obtem palavras-chave do modulo BroadTopicExtraction e distribui para diferentes plataformas para crawling
"""

import sys
import json
from datetime import date, timedelta, datetime
from pathlib import Path
from typing import List, Dict, Optional
import random
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# Adicionar diretorio raiz do projeto ao path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

try:
    import config
except ImportError:
    raise ImportError("Nao foi possivel importar o arquivo de configuracao config.py")

from config import settings
from loguru import logger

class KeywordManager:
    """Gerenciador de palavras-chave"""

    def __init__(self):
        """Inicializar gerenciador de palavras-chave"""
        self.engine: Engine = None
        self.connect()

    def connect(self):
        """Conectar ao banco de dados"""
        try:
            dialect = (settings.DB_DIALECT or "mysql").lower()
            if dialect in ("postgresql", "postgres"):
                url = f"postgresql+psycopg://{settings.DB_USER}:{settings.DB_PASSWORD}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
            else:
                url = f"mysql+pymysql://{settings.DB_USER}:{settings.DB_PASSWORD}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}?charset={settings.DB_CHARSET}"
            self.engine = create_engine(url, future=True)
            logger.info(f"Gerenciador de palavras-chave conectado com sucesso ao banco de dados: {settings.DB_NAME}")
        except ModuleNotFoundError as e:
            missing: str = str(e)
            if "psycopg" in missing:
                logger.error("Falha na conexao com o banco de dados: driver PostgreSQL psycopg nao instalado. Instale: psycopg[binary]. Comando: uv pip install psycopg[binary]")
            elif "pymysql" in missing:
                logger.error("Falha na conexao com o banco de dados: driver MySQL pymysql nao instalado. Instale: pymysql. Comando: uv pip install pymysql")
            else:
                logger.error(f"Falha na conexao com o banco de dados (driver ausente): {e}")
            raise
        except Exception as e:
            logger.exception(f"Falha na conexao do gerenciador de palavras-chave com o banco de dados: {e}")
            raise

    def get_latest_keywords(self, target_date: date = None, max_keywords: int = 100) -> List[str]:
        """
        Obter a lista de palavras-chave mais recente

        Args:
            target_date: Data alvo, padrao e hoje
            max_keywords: Quantidade maxima de palavras-chave

        Returns:
            Lista de palavras-chave
        """
        if not target_date:
            target_date = date.today()

        logger.info(f"Obtendo palavras-chave de {target_date}...")

        # Primeiro tentar obter palavras-chave da data especificada
        topics_data = self.get_daily_topics(target_date)

        if topics_data and topics_data.get('keywords'):
            keywords = topics_data['keywords']
            logger.info(f"Obtidas com sucesso {len(keywords)} palavras-chave de {target_date}")

            # Se houver muitas palavras-chave, selecionar aleatoriamente a quantidade especificada
            if len(keywords) > max_keywords:
                keywords = random.sample(keywords, max_keywords)
                logger.info(f"Selecionadas aleatoriamente {max_keywords} palavras-chave")

            return keywords

        # Se nao houver palavras-chave para o dia, tentar obter dos ultimos dias
        logger.info(f"Sem dados de palavras-chave para {target_date}, tentando obter palavras-chave recentes...")
        recent_topics = self.get_recent_topics(days=7)

        if recent_topics:
            # Combinar palavras-chave dos ultimos dias
            all_keywords = []
            for topic in recent_topics:
                if topic.get('keywords'):
                    all_keywords.extend(topic['keywords'])

            # Remover duplicatas e limitar quantidade
            unique_keywords = list(set(all_keywords))
            if len(unique_keywords) > max_keywords:
                unique_keywords = random.sample(unique_keywords, max_keywords)

            logger.info(f"Obtidas {len(unique_keywords)} palavras-chave dos dados dos ultimos 7 dias")
            return unique_keywords

        # Se nenhuma foi encontrada, retornar palavras-chave padrao
        logger.info("Nenhum dado de palavras-chave encontrado, usando palavras-chave padrao")
        return self._get_default_keywords()

    def get_daily_topics(self, extract_date: date = None) -> Optional[Dict]:
        """
        Obter analise de topicos diarios

        Args:
            extract_date: Data de extracao, padrao e hoje

        Returns:
            Dados de analise de topicos, None se nao existir
        """
        if not extract_date:
            extract_date = date.today()

        try:
            with self.engine.connect() as conn:
                result = conn.execute(
                    text("SELECT * FROM daily_topics WHERE extract_date = :d"),
                    {"d": extract_date},
                ).mappings().first()

            if result:
                # Converter para dict mutavel antes de atribuir
                result = dict(result)
                result['keywords'] = json.loads(result['keywords']) if result.get('keywords') else []
                return result
            else:
                return None

        except Exception as e:
            logger.exception(f"Falha ao obter analise de topicos: {e}")
            return None

    def get_recent_topics(self, days: int = 7) -> List[Dict]:
        """
        Obter analise de topicos dos ultimos dias

        Args:
            days: Numero de dias

        Returns:
            Lista de analises de topicos
        """
        try:
            start_date = date.today() - timedelta(days=days)
            with self.engine.connect() as conn:
                results = conn.execute(
                    text(
                        """
                        SELECT * FROM daily_topics
                        WHERE extract_date >= :start_date
                        ORDER BY extract_date DESC
                        """
                    ),
                    {"start_date": start_date},
                ).mappings().all()

            # Converter para lista de dicts mutaveis antes de processar
            results = [dict(r) for r in results]
            for result in results:
                result['keywords'] = json.loads(result['keywords']) if result.get('keywords') else []

            return results

        except Exception as e:
            logger.exception(f"Falha ao obter analises de topicos recentes: {e}")
            return []

    def _get_default_keywords(self) -> List[str]:
        """Obter lista de palavras-chave padrao"""
        return [
            "tecnologia", "inteligencia artificial", "AI", "programacao", "internet",
            "empreendedorismo", "investimento", "financas", "bolsa de valores", "economia",
            "educacao", "aprendizado", "concurso", "universidade", "emprego",
            "saude", "bem-estar", "esportes", "gastronomia", "turismo",
            "moda", "beleza", "compras", "estilo de vida", "decoracao",
            "cinema", "musica", "jogos", "entretenimento", "celebridades",
            "noticias", "destaques", "sociedade", "politicas", "meio ambiente"
        ]

    def get_all_keywords_for_platforms(self, platforms: List[str], target_date: date = None,
                                      max_keywords: int = 100) -> List[str]:
        """
        Obter a mesma lista de palavras-chave para todas as plataformas

        Args:
            platforms: Lista de plataformas
            target_date: Data alvo
            max_keywords: Quantidade maxima de palavras-chave

        Returns:
            Lista de palavras-chave (compartilhada por todas as plataformas)
        """
        keywords = self.get_latest_keywords(target_date, max_keywords)

        if keywords:
            logger.info(f"Preparadas as mesmas {len(keywords)} palavras-chave para {len(platforms)} plataformas")
            logger.info(f"Cada palavra-chave sera coletada em todas as plataformas")

        return keywords

    def get_keywords_for_platform(self, platform: str, target_date: date = None,
                                max_keywords: int = 50) -> List[str]:
        """
        Obter palavras-chave para uma plataforma especifica (agora todas as plataformas usam as mesmas palavras-chave)

        Args:
            platform: Nome da plataforma
            target_date: Data alvo
            max_keywords: Quantidade maxima de palavras-chave

        Returns:
            Lista de palavras-chave (mesma que outras plataformas)
        """
        keywords = self.get_latest_keywords(target_date, max_keywords)

        logger.info(f"Preparadas {len(keywords)} palavras-chave para plataforma {platform} (mesmas que outras plataformas)")
        return keywords

    def _filter_keywords_by_platform(self, keywords: List[str], platform: str) -> List[str]:
        """
        Filtrar palavras-chave de acordo com as caracteristicas da plataforma

        Args:
            keywords: Lista de palavras-chave original
            platform: Nome da plataforma

        Returns:
            Lista de palavras-chave filtrada
        """
        # Mapeamento de palavras-chave por caracteristicas da plataforma (ajustavel conforme necessidade)
        platform_preferences = {
            'xhs': ['beleza', 'moda', 'estilo de vida', 'gastronomia', 'turismo', 'compras', 'saude', 'bem-estar'],
            'dy': ['entretenimento', 'musica', 'danca', 'humor', 'gastronomia', 'estilo de vida', 'tecnologia', 'educacao'],
            'ks': ['estilo de vida', 'humor', 'rural', 'gastronomia', 'artesanato', 'musica', 'entretenimento'],
            'bili': ['tecnologia', 'jogos', 'anime', 'aprendizado', 'programacao', 'digital', 'divulgacao cientifica'],
            'wb': ['destaques', 'noticias', 'entretenimento', 'celebridades', 'sociedade', 'atualidades', 'tecnologia'],
            'tieba': ['jogos', 'anime', 'aprendizado', 'estilo de vida', 'interesses', 'discussoes'],
            'zhihu': ['conhecimento', 'aprendizado', 'tecnologia', 'carreira', 'investimento', 'educacao', 'reflexao']
        }

        # Se a plataforma tem preferencias especificas, priorizar palavras-chave relacionadas
        preferred_keywords = platform_preferences.get(platform, [])

        if preferred_keywords:
            # Selecionar primeiro palavras-chave preferidas da plataforma
            filtered = []
            remaining = []

            for keyword in keywords:
                if any(pref in keyword for pref in preferred_keywords):
                    filtered.append(keyword)
                else:
                    remaining.append(keyword)

            # Se palavras-chave preferidas nao forem suficientes, complementar com outras
            if len(filtered) < len(keywords) // 2:
                filtered.extend(remaining[:len(keywords) - len(filtered)])

            return filtered

        # Se nao houver preferencias especificas, retornar palavras-chave originais
        return keywords

    def get_crawling_summary(self, target_date: date = None) -> Dict:
        """
        Obter resumo da tarefa de crawling

        Args:
            target_date: Data alvo

        Returns:
            Informacoes de resumo de crawling
        """
        if not target_date:
            target_date = date.today()

        topics_data = self.get_daily_topics(target_date)

        if topics_data:
            return {
                'date': target_date,
                'keywords_count': len(topics_data.get('keywords', [])),
                'summary': topics_data.get('summary', ''),
                'has_data': True
            }
        else:
            return {
                'date': target_date,
                'keywords_count': 0,
                'summary': 'Sem dados no momento',
                'has_data': False
            }

    def close(self):
        """Fechar conexao com o banco de dados"""
        if self.engine:
            self.engine.dispose()
            logger.info("Conexao do gerenciador de palavras-chave com o banco de dados fechada")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

if __name__ == "__main__":
    # Testar gerenciador de palavras-chave
    with KeywordManager() as km:
        # Testar obtencao de palavras-chave
        keywords = km.get_latest_keywords(max_keywords=20)
        logger.info(f"Palavras-chave obtidas: {keywords}")

        # Testar distribuicao por plataforma
        platforms = ['xhs', 'dy', 'bili']
        distribution = km.distribute_keywords_by_platform(keywords, platforms)
        for platform, kws in distribution.items():
            logger.info(f"{platform}: {kws}")

        # Testar resumo de crawling
        summary = km.get_crawling_summary()
        logger.info(f"Resumo de crawling: {summary}")

        logger.info("Teste do gerenciador de palavras-chave concluido!")
