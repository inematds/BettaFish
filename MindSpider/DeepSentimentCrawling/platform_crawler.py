#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modulo DeepSentimentCrawling - Gerenciador de crawler por plataforma
Responsavel por configurar e chamar o MediaCrawler para crawling em multiplas plataformas
"""

import os
import sys
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
import json
from loguru import logger

# Adicionar diretorio raiz do projeto ao path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

try:
    import config
except ImportError:
    raise ImportError("Nao foi possivel importar o arquivo de configuracao config.py")

class PlatformCrawler:
    """Gerenciador de crawler por plataforma"""

    def __init__(self):
        """Inicializar gerenciador de crawler por plataforma"""
        self.mediacrawler_path = Path(__file__).parent / "MediaCrawler"
        self.supported_platforms = ['xhs', 'dy', 'ks', 'bili', 'wb', 'tieba', 'zhihu']
        self.crawl_stats = {}

        # Garantir que o submodulo MediaCrawler foi inicializado
        db_config_path = self.mediacrawler_path / "config" / "db_config.py"
        if not self.mediacrawler_path.exists() or not db_config_path.exists():
            logger.error("Submodulo MediaCrawler nao inicializado ou incompleto")
            logger.error("Execute o seguinte comando no diretorio raiz do projeto para inicializar o submodulo:")
            logger.error("   git submodule update --init --recursive")
            raise FileNotFoundError("Submodulo MediaCrawler nao inicializado, execute primeiro: git submodule update --init --recursive")

        logger.info(f"Gerenciador de crawler por plataforma inicializado, caminho do MediaCrawler: {self.mediacrawler_path}")

    def configure_mediacrawler_db(self):
        """Configurar MediaCrawler para usar nosso banco de dados (MySQL ou PostgreSQL)"""
        try:
            # Determinar tipo de banco de dados
            db_dialect = (config.settings.DB_DIALECT or "mysql").lower()
            is_postgresql = db_dialect in ("postgresql", "postgres")

            # Modificar configuracao de banco de dados do MediaCrawler
            db_config_path = self.mediacrawler_path / "config" / "db_config.py"

            # Ler configuracao original
            with open(db_config_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Valores de configuracao PostgreSQL: se usar PostgreSQL, usar configuracao do MindSpider; caso contrario, usar valores padrao ou variaveis de ambiente
            pg_password = config.settings.DB_PASSWORD if is_postgresql else "bettafish"
            pg_user = config.settings.DB_USER if is_postgresql else "bettafish"
            pg_host = config.settings.DB_HOST if is_postgresql else "127.0.0.1"
            pg_port = config.settings.DB_PORT if is_postgresql else 5444
            pg_db_name = config.settings.DB_NAME if is_postgresql else "bettafish"

            # Substituir configuracao de banco de dados - usando configuracao de banco de dados do MindSpider
            new_config = f'''# Aviso: Este codigo e apenas para fins de estudo e pesquisa. Os usuarios devem seguir os seguintes principios:
# 1. Nao deve ser usado para fins comerciais.
# 2. O uso deve respeitar os termos de uso e regras robots.txt da plataforma alvo.
# 3. Nao deve ser feito crawling em larga escala nem causar interferencia operacional na plataforma.
# 4. A frequencia de requisicoes deve ser razoavelmente controlada para evitar carga desnecessaria na plataforma alvo.
# 5. Nao deve ser usado para qualquer finalidade ilegal ou inadequada.
#
# Para termos de licenca detalhados, consulte o arquivo LICENSE no diretorio raiz do projeto.
# Ao usar este codigo, voce concorda em cumprir os principios acima e todos os termos do LICENSE.


import os

# mysql config - usando configuracao de banco de dados do MindSpider
MYSQL_DB_PWD = "{config.settings.DB_PASSWORD}"
MYSQL_DB_USER = "{config.settings.DB_USER}"
MYSQL_DB_HOST = "{config.settings.DB_HOST}"
MYSQL_DB_PORT = {config.settings.DB_PORT}
MYSQL_DB_NAME = "{config.settings.DB_NAME}"

mysql_db_config = {{
    "user": MYSQL_DB_USER,
    "password": MYSQL_DB_PWD,
    "host": MYSQL_DB_HOST,
    "port": MYSQL_DB_PORT,
    "db_name": MYSQL_DB_NAME,
}}


# redis config
REDIS_DB_HOST = "127.0.0.1"  # your redis host
REDIS_DB_PWD = os.getenv("REDIS_DB_PWD", "123456")  # your redis password
REDIS_DB_PORT = os.getenv("REDIS_DB_PORT", 6379)  # your redis port
REDIS_DB_NUM = os.getenv("REDIS_DB_NUM", 0)  # your redis db num

# cache type
CACHE_TYPE_REDIS = "redis"
CACHE_TYPE_MEMORY = "memory"

# sqlite config
SQLITE_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "database", "sqlite_tables.db")

sqlite_db_config = {{
    "db_path": SQLITE_DB_PATH
}}

# mongodb config
MONGODB_HOST = os.getenv("MONGODB_HOST", "localhost")
MONGODB_PORT = os.getenv("MONGODB_PORT", 27017)
MONGODB_USER = os.getenv("MONGODB_USER", "")
MONGODB_PWD = os.getenv("MONGODB_PWD", "")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "media_crawler")

mongodb_config = {{
    "host": MONGODB_HOST,
    "port": int(MONGODB_PORT),
    "user": MONGODB_USER,
    "password": MONGODB_PWD,
    "db_name": MONGODB_DB_NAME,
}}

# postgres config - usando configuracao de banco de dados do MindSpider (se DB_DIALECT for postgresql) ou variaveis de ambiente
POSTGRES_DB_PWD = os.getenv("POSTGRES_DB_PWD", "{pg_password}")
POSTGRES_DB_USER = os.getenv("POSTGRES_DB_USER", "{pg_user}")
POSTGRES_DB_HOST = os.getenv("POSTGRES_DB_HOST", "{pg_host}")
POSTGRES_DB_PORT = os.getenv("POSTGRES_DB_PORT", "{pg_port}")
POSTGRES_DB_NAME = os.getenv("POSTGRES_DB_NAME", "{pg_db_name}")

postgres_db_config = {{
    "user": POSTGRES_DB_USER,
    "password": POSTGRES_DB_PWD,
    "host": POSTGRES_DB_HOST,
    "port": POSTGRES_DB_PORT,
    "db_name": POSTGRES_DB_NAME,
}}

'''

            # Escrever nova configuracao
            with open(db_config_path, 'w', encoding='utf-8') as f:
                f.write(new_config)

            db_type = "PostgreSQL" if is_postgresql else "MySQL"
            logger.info(f"MediaCrawler configurado para usar banco de dados {db_type} do MindSpider")
            return True

        except Exception as e:
            logger.exception(f"Falha ao configurar banco de dados do MediaCrawler: {e}")
            return False

    def create_base_config(self, platform: str, keywords: List[str],
                          crawler_type: str = "search", max_notes: int = 50) -> bool:
        """
        Criar configuracao base do MediaCrawler

        Args:
            platform: Nome da plataforma
            keywords: Lista de palavras-chave
            crawler_type: Tipo de crawling
            max_notes: Quantidade maxima de crawling

        Returns:
            Se a configuracao foi bem-sucedida
        """
        try:
            # Determinar tipo de banco de dados para definir SAVE_DATA_OPTION
            db_dialect = (config.settings.DB_DIALECT or "mysql").lower()
            is_postgresql = db_dialect in ("postgresql", "postgres")
            save_data_option = "postgres" if is_postgresql else "db"

            base_config_path = self.mediacrawler_path / "config" / "base_config.py"

            # Converter lista de palavras-chave em string separada por virgulas
            keywords_str = ",".join(keywords)

            # Ler arquivo de configuracao original
            with open(base_config_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Modificar itens de configuracao chave
            # skip_until_paren: quando a linha original e uma atribuicao multilinha (termina com "(") substituida por linha unica,
            # precisa pular linhas de continuacao subsequentes ate encontrar o ")" correspondente
            lines = content.split('\n')
            new_lines = []
            skip_until_paren = False

            for line in lines:
                # Pular linhas de continuacao de atribuicao multilinha
                if skip_until_paren:
                    if line.strip() == ')':
                        skip_until_paren = False
                    continue

                replaced = None
                if line.startswith('PLATFORM = '):
                    replaced = f'PLATFORM = "{platform}"  # Plataforma: xhs | dy | ks | bili | wb | tieba | zhihu'
                elif line.startswith('KEYWORDS = '):
                    replaced = f'KEYWORDS = "{keywords_str}"  # Configuracao de busca por palavras-chave, separadas por virgula'
                elif line.startswith('CRAWLER_TYPE = '):
                    replaced = f'CRAWLER_TYPE = "{crawler_type}"  # Tipo de crawling: search (busca por palavras-chave) | detail (detalhes da postagem) | creator (dados do perfil do criador)'
                elif line.startswith('SAVE_DATA_OPTION = '):
                    replaced = f'SAVE_DATA_OPTION = "{save_data_option}"  # csv or db or json or sqlite or postgres'
                elif line.startswith('CRAWLER_MAX_NOTES_COUNT = '):
                    replaced = f'CRAWLER_MAX_NOTES_COUNT = {max_notes}'
                elif line.startswith('ENABLE_GET_COMMENTS = '):
                    replaced = 'ENABLE_GET_COMMENTS = True'
                elif line.startswith('CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES = '):
                    replaced = 'CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES = 20'
                elif line.startswith('HEADLESS = '):
                    replaced = 'HEADLESS = True'

                if replaced is not None:
                    new_lines.append(replaced)
                    # Se a linha original e inicio de atribuicao multilinha (termina com "("), pular linhas de continuacao
                    if line.rstrip().endswith('('):
                        skip_until_paren = True
                else:
                    new_lines.append(line)

            # Escrever nova configuracao
            with open(base_config_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(new_lines))

            logger.info(f"Plataforma {platform} configurada, tipo de crawling: {crawler_type}, quantidade de palavras-chave: {len(keywords)}, quantidade maxima de crawling: {max_notes}, metodo de salvamento: {save_data_option}")
            return True

        except Exception as e:
            logger.exception(f"Falha ao criar configuracao base: {e}")
            return False

    def run_crawler(self, platform: str, keywords: List[str],
                   login_type: str = "qrcode", max_notes: int = 50) -> Dict:
        """
        Executar crawler

        Args:
            platform: Nome da plataforma
            keywords: Lista de palavras-chave
            login_type: Metodo de login
            max_notes: Quantidade maxima de crawling

        Returns:
            Estatisticas do resultado do crawling
        """
        if platform not in self.supported_platforms:
            raise ValueError(f"Plataforma nao suportada: {platform}")

        if not keywords:
            raise ValueError("Lista de palavras-chave nao pode estar vazia")

        start_message = f"\nIniciando crawling na plataforma: {platform}"
        start_message += f"\nPalavras-chave: {keywords[:5]}{'...' if len(keywords) > 5 else ''} (total de {len(keywords)})"
        logger.info(start_message)

        start_time = datetime.now()

        try:
            # Configurar banco de dados
            if not self.configure_mediacrawler_db():
                return {"success": False, "error": "Falha na configuracao do banco de dados"}

            # Criar configuracao base
            if not self.create_base_config(platform, keywords, "search", max_notes):
                return {"success": False, "error": "Falha na criacao da configuracao base"}

            # Determinar tipo de banco de dados para definir save_data_option
            db_dialect = (config.settings.DB_DIALECT or "mysql").lower()
            is_postgresql = db_dialect in ("postgresql", "postgres")
            save_data_option = "postgres" if is_postgresql else "db"

            # Construir comando
            cmd = [
                sys.executable, "main.py",
                "--platform", platform,
                "--lt", login_type,
                "--type", "search",
                "--save_data_option", save_data_option,
                "--headless", "false"
            ]

            logger.info(f"Executando comando: {' '.join(cmd)}")

            # Mudar para diretorio do MediaCrawler e executar
            result = subprocess.run(
                cmd,
                cwd=self.mediacrawler_path,
                timeout=3600  # Timeout de 60 minutos
            )

            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()

            # Criar informacoes de estatisticas
            crawl_stats = {
                "platform": platform,
                "keywords_count": len(keywords),
                "duration_seconds": duration,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "return_code": result.returncode,
                "success": result.returncode == 0,
                "notes_count": 0,
                "comments_count": 0,
                "errors_count": 0
            }

            # Salvar informacoes de estatisticas
            self.crawl_stats[platform] = crawl_stats

            if result.returncode == 0:
                logger.info(f"{platform} crawling concluido, duracao: {duration:.1f}s")
            else:
                logger.error(f"{platform} crawling falhou, codigo de retorno: {result.returncode}")

            return crawl_stats

        except subprocess.TimeoutExpired:
            logger.exception(f"{platform} crawling expirou")
            return {"success": False, "error": "Timeout do crawling", "platform": platform}
        except Exception as e:
            logger.exception(f"{platform} excecao no crawling: {e}")
            return {"success": False, "error": str(e), "platform": platform}

    def _parse_crawl_output(self, output_lines: List[str], error_lines: List[str]) -> Dict:
        """Analisar saida do crawling, extrair informacoes de estatisticas"""
        stats = {
            "notes_count": 0,
            "comments_count": 0,
            "errors_count": 0,
            "login_required": False
        }

        # Analisar linhas de saida
        for line in output_lines:
            if "notes" in line.lower() or "content" in line.lower():
                try:
                    # Extrair numeros
                    import re
                    numbers = re.findall(r'\d+', line)
                    if numbers:
                        stats["notes_count"] = int(numbers[0])
                except:
                    pass
            elif "comments" in line.lower():
                try:
                    import re
                    numbers = re.findall(r'\d+', line)
                    if numbers:
                        stats["comments_count"] = int(numbers[0])
                except:
                    pass
            elif "login" in line.lower() or "qrcode" in line.lower():
                stats["login_required"] = True

        # Analisar linhas de erro
        for line in error_lines:
            if "error" in line.lower():
                stats["errors_count"] += 1

        return stats

    def run_multi_platform_crawl_by_keywords(self, keywords: List[str], platforms: List[str],
                                            login_type: str = "qrcode", max_notes_per_keyword: int = 50) -> Dict:
        """
        Crawling multi-plataforma baseado em palavras-chave - cada palavra-chave e coletada em todas as plataformas

        Args:
            keywords: Lista de palavras-chave
            platforms: Lista de plataformas
            login_type: Metodo de login
            max_notes_per_keyword: Quantidade maxima de crawling por palavra-chave em cada plataforma

        Returns:
            Estatisticas gerais de crawling
        """

        start_message = f"\nIniciando crawling de palavras-chave em todas as plataformas"
        start_message += f"\n   Quantidade de palavras-chave: {len(keywords)}"
        start_message += f"\n   Quantidade de plataformas: {len(platforms)}"
        start_message += f"\n   Metodo de login: {login_type}"
        start_message += f"\n   Quantidade maxima de crawling por palavra-chave em cada plataforma: {max_notes_per_keyword}"
        start_message += f"\n   Total de tarefas de crawling: {len(keywords)} x {len(platforms)} = {len(keywords) * len(platforms)}"
        logger.info(start_message)

        total_stats = {
            "total_keywords": len(keywords),
            "total_platforms": len(platforms),
            "total_tasks": len(keywords) * len(platforms),
            "successful_tasks": 0,
            "failed_tasks": 0,
            "total_notes": 0,
            "total_comments": 0,
            "keyword_results": {},
            "platform_summary": {}
        }

        # Inicializar estatisticas por plataforma
        for platform in platforms:
            total_stats["platform_summary"][platform] = {
                "successful_keywords": 0,
                "failed_keywords": 0,
                "total_notes": 0,
                "total_comments": 0
            }

        # Para cada plataforma, coletar todas as palavras-chave de uma vez
        for platform in platforms:
            logger.info(f"\nColetando todas as palavras-chave na plataforma {platform}")
            logger.info(f"   Palavras-chave: {', '.join(keywords[:5])}{'...' if len(keywords) > 5 else ''}")

            try:
                # Passar todas as palavras-chave de uma vez para a plataforma
                result = self.run_crawler(platform, keywords, login_type, max_notes_per_keyword)

                if result.get("success"):
                    total_stats["successful_tasks"] += len(keywords)
                    total_stats["platform_summary"][platform]["successful_keywords"] = len(keywords)

                    notes_count = result.get("notes_count", 0)
                    comments_count = result.get("comments_count", 0)

                    total_stats["total_notes"] += notes_count
                    total_stats["total_comments"] += comments_count
                    total_stats["platform_summary"][platform]["total_notes"] = notes_count
                    total_stats["platform_summary"][platform]["total_comments"] = comments_count

                    # Registrar resultado para cada palavra-chave
                    for keyword in keywords:
                        if keyword not in total_stats["keyword_results"]:
                            total_stats["keyword_results"][keyword] = {}
                        total_stats["keyword_results"][keyword][platform] = result

                    logger.info(f"   Crawling bem-sucedido")
                else:
                    total_stats["failed_tasks"] += len(keywords)
                    total_stats["platform_summary"][platform]["failed_keywords"] = len(keywords)

                    # Registrar resultado de falha para cada palavra-chave
                    for keyword in keywords:
                        if keyword not in total_stats["keyword_results"]:
                            total_stats["keyword_results"][keyword] = {}
                        total_stats["keyword_results"][keyword][platform] = result

                    logger.error(f"   Falha: {result.get('error', 'Erro desconhecido')}")

            except Exception as e:
                total_stats["failed_tasks"] += len(keywords)
                total_stats["platform_summary"][platform]["failed_keywords"] = len(keywords)
                error_result = {"success": False, "error": str(e)}

                # Registrar resultado de excecao para cada palavra-chave
                for keyword in keywords:
                    if keyword not in total_stats["keyword_results"]:
                        total_stats["keyword_results"][keyword] = {}
                    total_stats["keyword_results"][keyword][platform] = error_result

                logger.error(f"   Excecao: {e}")

        # Imprimir estatisticas detalhadas
        finish_message = f"\nCrawling de palavras-chave em todas as plataformas concluido!"
        finish_message += f"\n   Total de tarefas: {total_stats['total_tasks']}"
        finish_message += f"\n   Sucesso: {total_stats['successful_tasks']}"
        finish_message += f"\n   Falha: {total_stats['failed_tasks']}"
        finish_message += f"\n   Taxa de sucesso: {total_stats['successful_tasks']/total_stats['total_tasks']*100:.1f}%"
        logger.info(finish_message)

        platform_summary_message = f"\nEstatisticas por plataforma:"
        for platform, stats in total_stats["platform_summary"].items():
            success_rate = stats["successful_keywords"] / len(keywords) * 100 if keywords else 0
            platform_summary_message += f"\n   {platform}: {stats['successful_keywords']}/{len(keywords)} palavras-chave bem-sucedidas ({success_rate:.1f}%)"
        logger.info(platform_summary_message)

        return total_stats

    def get_crawl_statistics(self) -> Dict:
        """Obter informacoes de estatisticas de crawling"""
        return {
            "platforms_crawled": list(self.crawl_stats.keys()),
            "total_platforms": len(self.crawl_stats),
            "detailed_stats": self.crawl_stats
        }

    def save_crawl_log(self, log_path: str = None):
        """Salvar log de crawling"""
        if not log_path:
            log_path = f"crawl_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        try:
            with open(log_path, 'w', encoding='utf-8') as f:
                json.dump(self.crawl_stats, f, ensure_ascii=False, indent=2)
            logger.info(f"Log de crawling salvo em: {log_path}")
        except Exception as e:
            logger.exception(f"Falha ao salvar log de crawling: {e}")

if __name__ == "__main__":
    # Testar gerenciador de crawler por plataforma
    crawler = PlatformCrawler()

    # Testar configuracao
    test_keywords = ["tecnologia", "AI", "programacao"]
    result = crawler.run_crawler("xhs", test_keywords, max_notes=5)

    logger.info(f"Resultado do teste: {result}")
    logger.info("Teste do gerenciador de crawler por plataforma concluido!")
