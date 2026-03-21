#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MindSpider - Programa principal do projeto de crawler AI
Integra os dois modulos principais: BroadTopicExtraction e DeepSentimentCrawling
"""

import os
import sys
import argparse
import difflib
import re
from datetime import date, datetime
from pathlib import Path
import subprocess
import asyncio
import pymysql
from pymysql.cursors import DictCursor
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy import inspect, text
from config import settings
from loguru import logger
from urllib.parse import quote_plus

# Adicionar diretorio raiz do projeto ao path
project_root = Path(__file__).parent
sys.path.append(str(project_root))

try:
    import config
except ImportError:
    logger.error("Erro: Nao foi possivel importar o arquivo de configuracao config.py")
    logger.error("Certifique-se de que o arquivo config.py existe no diretorio raiz do projeto e contem as configuracoes de banco de dados e API")
    sys.exit(1)

class MindSpider:
    """Programa principal do MindSpider"""

    def __init__(self):
        """Inicializar MindSpider"""
        self.project_root = project_root
        self.broad_topic_path = self.project_root / "BroadTopicExtraction"
        self.deep_sentiment_path = self.project_root / "DeepSentimentCrawling"
        self.schema_path = self.project_root / "schema"

        logger.info("Projeto MindSpider AI Crawler")
        logger.info(f"Caminho do projeto: {self.project_root}")

    def check_config(self) -> bool:
        """Verificar configuracao basica"""
        logger.info("Verificando configuracao basica...")

        # Verificar itens de configuracao do settings
        required_configs = [
            'DB_HOST', 'DB_PORT', 'DB_USER', 'DB_PASSWORD', 'DB_NAME', 'DB_CHARSET',
            'MINDSPIDER_API_KEY', 'MINDSPIDER_BASE_URL', 'MINDSPIDER_MODEL_NAME'
        ]

        missing_configs = []
        for config_name in required_configs:
            if not hasattr(settings, config_name) or not getattr(settings, config_name):
                missing_configs.append(config_name)

        if missing_configs:
            logger.error(f"Configuracoes ausentes: {', '.join(missing_configs)}")
            logger.error("Verifique as variaveis de ambiente no arquivo .env")
            return False

        logger.info("Verificacao de configuracao basica aprovada")
        return True

    def check_database_connection(self) -> bool:
        """Verificar conexao com o banco de dados"""
        logger.info("Verificando conexao com o banco de dados...")

        def build_async_url() -> str:
            dialect = (settings.DB_DIALECT or "mysql").lower()
            if dialect in ("postgresql", "postgres"):
                return f"postgresql+asyncpg://{settings.DB_USER}:{quote_plus(settings.DB_PASSWORD)}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
            # Padrao: usar driver assincrono mysql asyncmy
            return (
                f"mysql+asyncmy://{settings.DB_USER}:{quote_plus(settings.DB_PASSWORD)}"
                f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}?charset={settings.DB_CHARSET}"
            )

        async def _test_connection(db_url: str) -> None:
            engine: AsyncEngine = create_async_engine(db_url, pool_pre_ping=True)
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            finally:
                await engine.dispose()

        try:
            db_url: str = build_async_url()
            asyncio.run(_test_connection(db_url))
            logger.info("Conexao com o banco de dados OK")
            return True
        except Exception as e:
            logger.exception(f"Falha na conexao com o banco de dados: {e}")
            return False

    def check_database_tables(self) -> bool:
        """Verificar se as tabelas do banco de dados existem"""
        logger.info("Verificando tabelas do banco de dados...")

        def build_async_url() -> str:
            dialect = (settings.DB_DIALECT or "mysql").lower()
            if dialect in ("postgresql", "postgres"):
                return f"postgresql+asyncpg://{settings.DB_USER}:{quote_plus(settings.DB_PASSWORD)}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
            return (
                f"mysql+asyncmy://{settings.DB_USER}:{quote_plus(settings.DB_PASSWORD)}"
                f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}?charset={settings.DB_CHARSET}"
            )

        async def _check_tables(db_url: str) -> list[str]:
            engine: AsyncEngine = create_async_engine(db_url, pool_pre_ping=True)
            try:
                async with engine.connect() as conn:
                    def _get_tables(sync_conn):
                        return inspect(sync_conn).get_table_names()
                    tables = await conn.run_sync(_get_tables)
                    return tables
            finally:
                await engine.dispose()

        try:
            db_url: str = build_async_url()
            existing_tables = asyncio.run(_check_tables(db_url))
            required_tables = ['daily_news', 'daily_topics']
            missing_tables = [t for t in required_tables if t not in existing_tables]
            if missing_tables:
                logger.error(f"Tabelas ausentes no banco de dados: {', '.join(missing_tables)}")
                return False
            logger.info("Verificacao de tabelas do banco de dados aprovada")
            return True
        except Exception as e:
            logger.exception(f"Falha ao verificar tabelas do banco de dados: {e}")
            return False

    def initialize_database(self) -> bool:
        """Inicializar banco de dados"""
        logger.info("Inicializando banco de dados...")

        try:
            # Executar script de inicializacao do banco de dados
            init_script = self.schema_path / "init_database.py"
            if not init_script.exists():
                logger.error("Erro: Script de inicializacao do banco de dados nao encontrado")
                return False

            result = subprocess.run(
                [sys.executable, str(init_script)],
                cwd=self.schema_path,
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                logger.info("Banco de dados inicializado com sucesso")
                return True
            else:
                logger.error(f"Falha na inicializacao do banco de dados: {result.stderr}")
                return False

        except Exception as e:
            logger.exception(f"Excecao na inicializacao do banco de dados: {e}")
            return False

    def _ensure_database_ready(self) -> bool:
        """Garantir que as tabelas do banco de dados estejam prontas, inicializando automaticamente se nao existirem"""
        if not self.check_database_connection():
            logger.error("Falha na conexao com o banco de dados, impossivel continuar")
            return False

        if not self.check_database_tables():
            logger.warning("Tabelas do banco de dados nao existem, inicializando automaticamente...")
            if not self.initialize_database():
                logger.error("Falha na inicializacao automatica do banco de dados")
                return False
            logger.info("Tabelas do banco de dados inicializadas automaticamente com sucesso")

        return True

    def check_dependencies(self) -> bool:
        """Verificar ambiente de dependencias"""
        logger.info("Verificando ambiente de dependencias...")

        # Verificar pacotes Python
        required_packages = ['pymysql', 'requests', 'playwright']
        missing_packages = []

        for package in required_packages:
            try:
                __import__(package)
            except ImportError:
                missing_packages.append(package)

        if missing_packages:
            logger.error(f"Pacotes Python ausentes: {', '.join(missing_packages)}")
            logger.info("Execute: pip install -r requirements.txt")
            return False

        # Verificar e instalar dependencias do MediaCrawler
        mediacrawler_path = self.deep_sentiment_path / "MediaCrawler"
        if not mediacrawler_path.exists():
            logger.error("Erro: Diretorio MediaCrawler nao encontrado")
            return False

        # Instalar automaticamente dependencias do MediaCrawler
        self._install_mediacrawler_dependencies()

        logger.info("Verificacao de ambiente de dependencias aprovada")
        return True

    def _install_mediacrawler_dependencies(self) -> bool:
        """Instalar automaticamente dependencias do submodulo MediaCrawler"""
        mediacrawler_req = self.deep_sentiment_path / "MediaCrawler" / "requirements.txt"

        if not mediacrawler_req.exists():
            logger.warning(f"MediaCrawler requirements.txt nao existe: {mediacrawler_req}")
            return False

        # Verificar se ja foi instalado (usando arquivo marcador)
        marker_file = self.deep_sentiment_path / "MediaCrawler" / ".deps_installed"
        req_mtime = mediacrawler_req.stat().st_mtime

        if marker_file.exists():
            marker_mtime = marker_file.stat().st_mtime
            if marker_mtime >= req_mtime:
                logger.debug("Dependencias do MediaCrawler ja instaladas, pulando")
                return True

        logger.info("Instalando dependencias do MediaCrawler...")
        install_commands = [
            [sys.executable, "-m", "pip", "install", "-r", str(mediacrawler_req), "-q"],
            ["uv", "pip", "install", "-r", str(mediacrawler_req), "-q"],
        ]
        try:
            for cmd in install_commands:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300  # Timeout de 5 minutos
                )
                if result.returncode == 0:
                    marker_file.touch()
                    logger.info(f"Dependencias do MediaCrawler instaladas com sucesso (via {cmd[0]})")
                    return True
                logger.debug(f"{cmd[0]} falhou na instalacao, tentando proximo metodo: {result.stderr.strip()}")

            logger.error("Falha na instalacao das dependencias do MediaCrawler: todos os metodos de instalacao falharam")
            return False

        except subprocess.TimeoutExpired:
            logger.error("Timeout na instalacao das dependencias do MediaCrawler")
            return False
        except Exception as e:
            logger.exception(f"Excecao na instalacao das dependencias do MediaCrawler: {e}")
            return False

    def run_broad_topic_extraction(self, extract_date: date = None, keywords_count: int = 100) -> bool:
        """Executar modulo BroadTopicExtraction"""
        logger.info("Executando modulo BroadTopicExtraction...")

        # Verificar e inicializar automaticamente tabelas do banco de dados
        if not self._ensure_database_ready():
            return False

        if not extract_date:
            extract_date = date.today()

        try:
            cmd = [
                sys.executable, "main.py",
                "--keywords", str(keywords_count)
            ]

            logger.info(f"Executando comando: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                cwd=self.broad_topic_path,
                timeout=1800  # Timeout de 30 minutos
            )

            if result.returncode == 0:
                logger.info("Modulo BroadTopicExtraction executado com sucesso")
                return True
            else:
                logger.error(f"Falha na execucao do modulo BroadTopicExtraction, codigo de retorno: {result.returncode}")
                return False

        except subprocess.TimeoutExpired:
            logger.error("Timeout na execucao do modulo BroadTopicExtraction")
            return False
        except Exception as e:
            logger.exception(f"Excecao na execucao do modulo BroadTopicExtraction: {e}")
            return False

    def run_deep_sentiment_crawling(self, target_date: date = None, platforms: list = None,
                                   max_keywords: int = 50, max_notes: int = 50,
                                   test_mode: bool = False) -> bool:
        """Executar modulo DeepSentimentCrawling"""
        logger.info("Executando modulo DeepSentimentCrawling...")

        # Verificar e inicializar automaticamente tabelas do banco de dados
        if not self._ensure_database_ready():
            return False

        # Instalar automaticamente dependencias do MediaCrawler
        self._install_mediacrawler_dependencies()

        if not target_date:
            target_date = date.today()

        try:
            cmd = [sys.executable, "main.py"]

            if target_date:
                cmd.extend(["--date", target_date.strftime("%Y-%m-%d")])

            if platforms:
                cmd.extend(["--platforms"] + platforms)

            cmd.extend([
                "--max-keywords", str(max_keywords),
                "--max-notes", str(max_notes)
            ])

            if test_mode:
                cmd.append("--test")

            logger.info(f"Executando comando: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                cwd=self.deep_sentiment_path,
                timeout=3600  # Timeout de 60 minutos
            )

            if result.returncode == 0:
                logger.info("Modulo DeepSentimentCrawling executado com sucesso")
                return True
            else:
                logger.error(f"Falha na execucao do modulo DeepSentimentCrawling, codigo de retorno: {result.returncode}")
                return False

        except subprocess.TimeoutExpired:
            logger.error("Timeout na execucao do modulo DeepSentimentCrawling")
            return False
        except Exception as e:
            logger.exception(f"Excecao na execucao do modulo DeepSentimentCrawling: {e}")
            return False

    def run_complete_workflow(self, target_date: date = None, platforms: list = None,
                             keywords_count: int = 100, max_keywords: int = 50,
                             max_notes: int = 50, test_mode: bool = False) -> bool:
        """Executar fluxo de trabalho completo"""
        logger.info("Iniciando fluxo de trabalho completo do MindSpider")

        # Verificar e inicializar automaticamente tabelas do banco de dados (garantir inicializacao automatica em chamadas independentes)
        if not self._ensure_database_ready():
            return False

        if not target_date:
            target_date = date.today()

        logger.info(f"Data alvo: {target_date}")
        logger.info(f"Lista de plataformas: {platforms if platforms else 'Todas as plataformas suportadas'}")
        logger.info(f"Modo de teste: {'Sim' if test_mode else 'Nao'}")

        # Primeira etapa: Extracao de topicos
        logger.info("=== Primeira etapa: Extracao de topicos ===")
        if not self.run_broad_topic_extraction(target_date, keywords_count):
            logger.error("Falha na extracao de topicos, fluxo interrompido")
            return False

        # Segunda etapa: Crawling de sentimento
        logger.info("=== Segunda etapa: Crawling de sentimento ===")
        if not self.run_deep_sentiment_crawling(target_date, platforms, max_keywords, max_notes, test_mode):
            logger.error("Falha no crawling de sentimento, mas extracao de topicos foi concluida")
            return False

        logger.info("Fluxo de trabalho completo executado com sucesso!")
        return True

    def show_status(self):
        """Exibir status do projeto"""
        logger.info("Status do projeto MindSpider:")
        logger.info(f"Caminho do projeto: {self.project_root}")

        # Status da configuracao
        config_ok = self.check_config()
        logger.info(f"Status da configuracao: {'Normal' if config_ok else 'Anormal'}")

        # Status do banco de dados
        if config_ok:
            db_conn_ok = self.check_database_connection()
            logger.info(f"Conexao com banco de dados: {'Normal' if db_conn_ok else 'Anormal'}")

            if db_conn_ok:
                db_tables_ok = self.check_database_tables()
                logger.info(f"Tabelas do banco de dados: {'Normal' if db_tables_ok else 'Precisa inicializacao'}")

        # Status das dependencias
        deps_ok = self.check_dependencies()
        logger.info(f"Ambiente de dependencias: {'Normal' if deps_ok else 'Anormal'}")

        # Status dos modulos
        broad_topic_exists = self.broad_topic_path.exists()
        deep_sentiment_exists = self.deep_sentiment_path.exists()
        logger.info(f"Modulo BroadTopicExtraction: {'Presente' if broad_topic_exists else 'Ausente'}")
        logger.info(f"Modulo DeepSentimentCrawling: {'Presente' if deep_sentiment_exists else 'Ausente'}")

    def setup_project(self) -> bool:
        """Configuracao de inicializacao do projeto"""
        logger.info("Iniciando inicializacao do projeto MindSpider...")

        # 1. Verificar configuracao
        if not self.check_config():
            return False

        # 2. Verificar dependencias
        if not self.check_dependencies():
            return False

        # 3. Verificar conexao com banco de dados
        if not self.check_database_connection():
            return False

        # 4. Verificar e inicializar tabelas do banco de dados
        if not self.check_database_tables():
            logger.info("Tabelas do banco de dados precisam ser inicializadas...")
            if not self.initialize_database():
                return False

        logger.info("Inicializacao do projeto MindSpider concluida!")
        return True

PLATFORM_CHOICES = ['xhs', 'dy', 'ks', 'bili', 'wb', 'tieba', 'zhihu']

PLATFORM_ALIASES = {
    'weibo': 'wb', 'webo': 'wb',
    'douyin': 'dy',
    'kuaishou': 'ks',
    'bilibili': 'bili', 'bstation': 'bili',
    'xiaohongshu': 'xhs', 'redbook': 'xhs',
    'zhihu': 'zhihu',
    'tieba': 'tieba',
}

class SuggestiveArgumentParser(argparse.ArgumentParser):
    """Fornece sugestoes de candidatos similares quando ha erro de argumento"""

    def error(self, message: str):
        match = re.search(r"invalid choice: '([^']+)'", message)
        if match:
            bad = match.group(1)
            alias = PLATFORM_ALIASES.get(bad.lower())
            suggestions = difflib.get_close_matches(bad, PLATFORM_CHOICES, n=3, cutoff=0.3)
            if alias:
                print(f"Erro: '{bad}' nao e um codigo de plataforma valido. Voce quis dizer '{alias}'?", file=sys.stderr)
            elif suggestions:
                print(f"Erro: '{bad}' nao e um codigo de plataforma valido. Opcoes mais proximas: {suggestions}", file=sys.stderr)
            else:
                print(f"Erro: '{bad}' nao e um codigo de plataforma valido. Plataformas validas: {PLATFORM_CHOICES}", file=sys.stderr)
            print(f"Erro completo: {message}", file=sys.stderr)
        else:
            print(f"Erro: {message}", file=sys.stderr)
        self.print_usage(sys.stderr)
        sys.exit(2)

def main():
    """Ponto de entrada da linha de comando"""
    parser = SuggestiveArgumentParser(description="MindSpider - Programa principal do projeto de crawler AI")

    # Operacoes basicas
    parser.add_argument("--setup", action="store_true", help="Inicializar configuracao do projeto")
    parser.add_argument("--status", action="store_true", help="Exibir status do projeto")
    parser.add_argument("--init-db", action="store_true", help="Inicializar banco de dados")

    # Execucao de modulos
    parser.add_argument("--broad-topic", action="store_true", help="Executar apenas modulo de extracao de topicos")
    parser.add_argument("--deep-sentiment", action="store_true", help="Executar apenas modulo de crawling de sentimento")
    parser.add_argument("--complete", action="store_true", help="Executar fluxo de trabalho completo")

    # Configuracao de parametros
    parser.add_argument("--date", type=str, help="Data alvo (AAAA-MM-DD), padrao e hoje")
    parser.add_argument("--platforms", type=str, nargs='+',
                       choices=PLATFORM_CHOICES,
                       help="Especificar plataformas de crawling")
    parser.add_argument("--keywords-count", type=int, default=100, help="Quantidade de palavras-chave para extracao de topicos")
    parser.add_argument("--max-keywords", type=int, default=50, help="Quantidade maxima de palavras-chave por plataforma")
    parser.add_argument("--max-notes", type=int, default=50, help="Quantidade maxima de conteudo coletado por palavra-chave")
    parser.add_argument("--test", action="store_true", help="Modo de teste (poucos dados)")

    args = parser.parse_args()

    # Analisar data
    target_date = None
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            logger.error("Erro: Formato de data incorreto, use o formato AAAA-MM-DD")
            return

    # Criar instancia do MindSpider
    spider = MindSpider()

    try:
        # Exibir status
        if args.status:
            spider.show_status()
            return

        # Configuracao do projeto
        if args.setup:
            if spider.setup_project():
                logger.info("Configuracao do projeto concluida, voce pode comecar a usar o MindSpider!")
            else:
                logger.error("Falha na configuracao do projeto, verifique a configuracao e o ambiente")
            return

        # Inicializar banco de dados
        if args.init_db:
            if spider.initialize_database():
                logger.info("Banco de dados inicializado com sucesso")
            else:
                logger.error("Falha na inicializacao do banco de dados")
            return

        # Executar modulos
        if args.broad_topic:
            spider.run_broad_topic_extraction(target_date, args.keywords_count)
        elif args.deep_sentiment:
            spider.run_deep_sentiment_crawling(
                target_date, args.platforms, args.max_keywords, args.max_notes, args.test
            )
        elif args.complete:
            spider.run_complete_workflow(
                target_date, args.platforms, args.keywords_count,
                args.max_keywords, args.max_notes, args.test
            )
        else:
            # Padrao: executar fluxo de trabalho completo
            logger.info("Executando fluxo de trabalho completo do MindSpider...")
            spider.run_complete_workflow(
                target_date, args.platforms, args.keywords_count,
                args.max_keywords, args.max_notes, args.test
            )

    except KeyboardInterrupt:
        logger.info("Operacao interrompida pelo usuario")
    except Exception as e:
        logger.exception(f"Erro na execucao: {e}")

if __name__ == "__main__":
    main()
