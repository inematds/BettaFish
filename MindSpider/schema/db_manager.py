#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Projeto MindSpider AI Crawler - Ferramenta de gerenciamento de banco de dados
Fornece funcionalidades de visualizacao de status do banco de dados, estatisticas de dados, limpeza, etc.
"""

import os
import sys
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.engine import Engine
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from loguru import logger
from urllib.parse import quote_plus

# Adicionar diretorio raiz do projeto ao path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

try:
    import config
except ImportError:
    logger.error("Erro: Nao foi possivel importar o arquivo de configuracao config.py")
    sys.exit(1)

from config import settings

class DatabaseManager:
    def __init__(self):
        self.engine: Engine = None
        self.connect()

    def connect(self):
        """Conectar ao banco de dados"""
        try:
            dialect = (settings.DB_DIALECT or "mysql").lower()
            if dialect in ("postgresql", "postgres"):
                url = f"postgresql+psycopg://{settings.DB_USER}:{quote_plus(settings.DB_PASSWORD)}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
            else:
                url = f"mysql+pymysql://{settings.DB_USER}:{quote_plus(settings.DB_PASSWORD)}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}?charset={settings.DB_CHARSET}"
            self.engine = create_engine(url, future=True)
            logger.info(f"Conectado com sucesso ao banco de dados: {settings.DB_NAME}")
        except Exception as e:
            logger.error(f"Falha na conexao com o banco de dados: {e}")
            sys.exit(1)

    def close(self):
        """Fechar conexao com o banco de dados"""
        if self.engine:
            self.engine.dispose()

    def show_tables(self):
        """Exibir todas as tabelas"""
        data_list_message = ""
        data_list_message += "\n" + "=" * 60
        data_list_message += "Lista de tabelas do banco de dados"
        data_list_message += "=" * 60
        logger.info(data_list_message)

        inspector = inspect(self.engine)
        tables = inspector.get_table_names()

        if not tables:
            logger.info("Nao ha tabelas no banco de dados")
            return

        # Exibir tabelas por categoria
        mindspider_tables = []
        mediacrawler_tables = []

        for table_name in tables:
            if table_name in ['daily_news', 'daily_topics', 'topic_news_relation', 'crawling_tasks']:
                mindspider_tables.append(table_name)
            else:
                mediacrawler_tables.append(table_name)

        data_list_message += "Tabelas principais do MindSpider:"
        data_list_message += "\n"
        for table in mindspider_tables:
            with self.engine.connect() as conn:
                count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            data_list_message += f"  - {table:<25} ({count:>6} registros)"
            data_list_message += "\n"

        data_list_message += "\nTabelas de plataforma do MediaCrawler:"
        data_list_message += "\n"
        for table in mediacrawler_tables:
            try:
                with self.engine.connect() as conn:
                    count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
                data_list_message += f"  - {table:<25} ({count:>6} registros)"
                data_list_message += "\n"
            except:
                data_list_message += f"  - {table:<25} (falha na consulta)"
                data_list_message += "\n"
        logger.info(data_list_message)

    def show_statistics(self):
        """Exibir estatisticas de dados"""
        data_statistics_message = ""
        data_statistics_message += "\n" + "=" * 60
        data_statistics_message += "Estatisticas de dados"
        data_statistics_message += "=" * 60
        data_statistics_message += "\n"

        try:
            # Estatisticas de noticias
            with self.engine.connect() as conn:
                news_count = conn.execute(text("SELECT COUNT(*) FROM daily_news")).scalar_one()
                news_days = conn.execute(text("SELECT COUNT(DISTINCT crawl_date) FROM daily_news")).scalar_one()
                platforms = conn.execute(text("SELECT COUNT(DISTINCT source_platform) FROM daily_news")).scalar_one()

            data_statistics_message += "Dados de noticias:"
            data_statistics_message += "\n"
            data_statistics_message += f"  - Total de noticias: {news_count}"
            data_statistics_message += "\n"
            data_statistics_message += f"  - Dias cobertos: {news_days}"
            data_statistics_message += "\n"
            data_statistics_message += f"  - Plataformas de noticias: {platforms}"
            data_statistics_message += "\n"
            # Estatisticas de topicos
            with self.engine.connect() as conn:
                topic_count = conn.execute(text("SELECT COUNT(*) FROM daily_topics")).scalar_one()
                topic_days = conn.execute(text("SELECT COUNT(DISTINCT extract_date) FROM daily_topics")).scalar_one()

            data_statistics_message += "Dados de topicos:"
            data_statistics_message += "\n"
            data_statistics_message += f"  - Total de topicos: {topic_count}"
            data_statistics_message += "\n"
            data_statistics_message += f"  - Dias de extracao: {topic_days}"
            data_statistics_message += "\n"

            # Estatisticas de tarefas de crawling
            with self.engine.connect() as conn:
                task_count = conn.execute(text("SELECT COUNT(*) FROM crawling_tasks")).scalar_one()
                task_status = conn.execute(text("SELECT task_status, COUNT(*) FROM crawling_tasks GROUP BY task_status")).all()

            data_statistics_message += "Tarefas de crawling:"
            data_statistics_message += "\n"
            data_statistics_message += f"  - Total de tarefas: {task_count}"
            data_statistics_message += "\n"
            for status, count in task_status:
                data_statistics_message += f"  - {status}: {count}"
                data_statistics_message += "\n"

            # Estatisticas de conteudo por plataforma
            data_statistics_message += "Estatisticas de conteudo por plataforma:"
            data_statistics_message += "\n"
            platform_tables = {
                'xhs_note': 'Xiaohongshu',
                'douyin_aweme': 'Douyin',
                'kuaishou_video': 'Kuaishou',
                'bilibili_video': 'Bilibili',
                'weibo_note': 'Weibo',
                'tieba_note': 'Tieba',
                'zhihu_content': 'Zhihu'
            }

            for table, platform in platform_tables.items():
                try:
                    with self.engine.connect() as conn:
                        count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
                    data_statistics_message += f"  - {platform}: {count}"
                    data_statistics_message += "\n"
                except:
                    data_statistics_message += f"  - {platform}: Tabela nao existe"
                    data_statistics_message += "\n"
            logger.info(data_statistics_message)
        except Exception as e:
            data_statistics_message += f"Falha na consulta de estatisticas: {e}"
            data_statistics_message += "\n"
            logger.error(data_statistics_message)

    def show_recent_data(self, days=7):
        """Exibir dados dos ultimos dias"""
        data_recent_message = ""
        data_recent_message += "\n" + "=" * 60
        data_recent_message += "Dados dos ultimos " + str(days) + " dias"
        data_recent_message += "=" * 60

        from datetime import date, timedelta
        start_date = date.today() - timedelta(days=days)
        # Noticias mais recentes
        with self.engine.connect() as conn:
            news_data = conn.execute(
                text(
                    """
                    SELECT crawl_date, COUNT(*) as news_count, COUNT(DISTINCT source_platform) as platforms
                    FROM daily_news
                    WHERE crawl_date >= :start_date
                    GROUP BY crawl_date
                    ORDER BY crawl_date DESC
                    """
                ),
                {"start_date": start_date},
            ).all()
        if news_data:
            data_recent_message += "Estatisticas diarias de noticias:"
            data_recent_message += "\n"
            for date, count, platforms in news_data:
                data_recent_message += f"  {date}: {count} noticias, {platforms} plataformas"
                data_recent_message += "\n"

        # Topicos mais recentes
        with self.engine.connect() as conn:
            topic_data = conn.execute(
                text(
                    """
                    SELECT extract_date, COUNT(*) as topic_count
                    FROM daily_topics
                    WHERE extract_date >= :start_date
                    GROUP BY extract_date
                    ORDER BY extract_date DESC
                    """
                ),
                {"start_date": start_date},
            ).all()
        if topic_data:
            data_recent_message += "Estatisticas diarias de topicos:"
            data_recent_message += "\n"
            for date, count in topic_data:
                data_recent_message += f"  {date}: {count} topicos"
                data_recent_message += "\n"
        logger.info(data_recent_message)

    def cleanup_old_data(self, days=90, dry_run=True):
        """Limpar dados antigos"""
        cleanup_message = ""
        cleanup_message += "\n" + "=" * 60
        cleanup_message += f"Limpar dados com mais de {days} dias ({'Modo de visualizacao' if dry_run else 'Modo de execucao'})"
        cleanup_message += "=" * 60

        cutoff_date = datetime.now() - timedelta(days=days)

        # Verificar dados a serem excluidos
        cleanup_queries = [
            ("daily_news", f"SELECT COUNT(*) FROM daily_news WHERE crawl_date < '{cutoff_date.date()}'"),
            ("daily_topics", f"SELECT COUNT(*) FROM daily_topics WHERE extract_date < '{cutoff_date.date()}'"),
            ("crawling_tasks", f"SELECT COUNT(*) FROM crawling_tasks WHERE scheduled_date < '{cutoff_date.date()}'")
        ]

        with self.engine.begin() as conn:
            for table, query in cleanup_queries:
                count = conn.execute(text(query)).scalar_one()
                if count > 0:
                    cleanup_message += f"  {table}: {count} registros serao excluidos"
                    cleanup_message += "\n"
                    if not dry_run:
                        delete_query = query.replace("SELECT COUNT(*)", "DELETE")
                        conn.execute(text(delete_query))
                        cleanup_message += f"    {count} registros excluidos"
                        cleanup_message += "\n"
                else:
                    cleanup_message += f"  {table}: Sem necessidade de limpeza"
                    cleanup_message += "\n"

        if dry_run:
            cleanup_message += "\nEste e o modo de visualizacao, nenhum dado foi realmente excluido. Use o parametro --execute para executar a limpeza real."
            cleanup_message += "\n"
        logger.info(cleanup_message)

def main():
    parser = argparse.ArgumentParser(description="Ferramenta de gerenciamento de banco de dados MindSpider")
    parser.add_argument("--tables", action="store_true", help="Exibir todas as tabelas")
    parser.add_argument("--stats", action="store_true", help="Exibir estatisticas de dados")
    parser.add_argument("--recent", type=int, default=7, help="Exibir dados dos ultimos N dias (padrao 7 dias)")
    parser.add_argument("--cleanup", type=int, help="Limpar dados com mais de N dias")
    parser.add_argument("--execute", action="store_true", help="Executar operacao de limpeza real")

    args = parser.parse_args()

    # Se nao houver argumentos, exibir todas as informacoes
    if not any([args.tables, args.stats, args.recent != 7, args.cleanup]):
        args.tables = True
        args.stats = True

    db_manager = DatabaseManager()

    try:
        if args.tables:
            db_manager.show_tables()

        if args.stats:
            db_manager.show_statistics()

        if args.recent != 7 or not any([args.tables, args.stats, args.cleanup]):
            db_manager.show_recent_data(args.recent)

        if args.cleanup:
            db_manager.cleanup_old_data(args.cleanup, dry_run=not args.execute)

    finally:
        db_manager.close()

if __name__ == "__main__":
    main()
