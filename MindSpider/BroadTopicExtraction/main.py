#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modulo BroadTopicExtraction - Programa principal
Integra o fluxo de trabalho completo de extracao de topicos e ferramenta de linha de comando
"""

import sys
import asyncio
import argparse
from datetime import datetime, date
from pathlib import Path
from typing import List, Dict, Optional
from loguru import logger

# Adicionar diretorio raiz do projeto ao path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

try:
    from BroadTopicExtraction.get_today_news import NewsCollector, SOURCE_NAMES
    from BroadTopicExtraction.topic_extractor import TopicExtractor
    from BroadTopicExtraction.database_manager import DatabaseManager
except ImportError as e:
    logger.exception(f"Falha ao importar modulo: {e}")
    logger.error("Certifique-se de executar a partir do diretorio raiz do projeto e que todas as dependencias estao instaladas")
    sys.exit(1)

class BroadTopicExtraction:
    """Fluxo de trabalho principal do BroadTopicExtraction"""

    def __init__(self):
        """Inicializar"""
        self.news_collector = NewsCollector()
        self.topic_extractor = TopicExtractor()
        self.db_manager = DatabaseManager()

        logger.info("BroadTopicExtraction inicializado")

    def close(self):
        """Fechar recursos"""
        if self.news_collector:
            self.news_collector.close()
        if self.db_manager:
            self.db_manager.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.close()

    async def run_daily_extraction(self,
                                  news_sources: Optional[List[str]] = None,
                                  max_keywords: int = 100) -> Dict:
        """
        Executar fluxo de extracao diaria de topicos

        Args:
            news_sources: Lista de fontes de noticias, None significa usar todas as fontes suportadas
            max_keywords: Quantidade maxima de palavras-chave

        Returns:
            Dicionario contendo resultados completos da extracao
        """
        extraction_result_message = ""
        extraction_result_message += "\nMindSpider AI Crawler - Extracao diaria de topicos\n"
        extraction_result_message += f"Hora de execucao: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        extraction_result_message += f"Data alvo: {date.today()}\n"

        if news_sources:
            extraction_result_message += f"Plataformas especificadas: {len(news_sources)}\n"
            for source in news_sources:
                source_name = SOURCE_NAMES.get(source, source)
                extraction_result_message += f"  - {source_name}\n"
        else:
            extraction_result_message += f"Plataformas de coleta: Todas as {len(SOURCE_NAMES)} plataformas\n"

        extraction_result_message += f"Palavras-chave: maximo {max_keywords}\n"

        logger.info(extraction_result_message)

        extraction_result = {
            'success': False,
            'extraction_date': date.today().isoformat(),
            'start_time': datetime.now().isoformat(),
            'news_collection': {},
            'topic_extraction': {},
            'database_save': {},
            'error': None
        }

        try:
            # Etapa 1: Coletar noticias
            logger.info("[Etapa 1] Coletando noticias em destaque...")
            news_result = await self.news_collector.collect_and_save_news(
                sources=news_sources
            )

            extraction_result['news_collection'] = {
                'success': news_result['success'],
                'total_news': news_result.get('total_news', 0),
                'successful_sources': news_result.get('successful_sources', 0),
                'total_sources': news_result.get('total_sources', 0)
            }

            if not news_result['success'] or not news_result['news_list']:
                raise Exception("Falha na coleta de noticias ou nenhuma noticia obtida")

            # Etapa 2: Extrair palavras-chave e gerar resumo
            logger.info("[Etapa 2] Extraindo palavras-chave e gerando resumo...")
            keywords, summary = self.topic_extractor.extract_keywords_and_summary(
                news_result['news_list'],
                max_keywords=max_keywords
            )

            extraction_result['topic_extraction'] = {
                'success': len(keywords) > 0,
                'keywords_count': len(keywords),
                'keywords': keywords,
                'summary': summary
            }

            if not keywords:
                logger.warning("Aviso: Nenhuma palavra-chave valida extraida")

            # Etapa 3: Salvar no banco de dados
            logger.info("[Etapa 3] Salvando resultados da analise no banco de dados...")
            save_success = self.db_manager.save_daily_topics(
                keywords, summary, date.today()
            )

            extraction_result['database_save'] = {
                'success': save_success
            }

            extraction_result['success'] = True
            extraction_result['end_time'] = datetime.now().isoformat()

            logger.info("Fluxo de extracao diaria de topicos concluido!")

            return extraction_result

        except Exception as e:
            logger.exception(f"Falha no fluxo de extracao de topicos: {e}")
            extraction_result['error'] = str(e)
            extraction_result['end_time'] = datetime.now().isoformat()
            return extraction_result

    def print_extraction_results(self, extraction_result: Dict):
        """Imprimir resultados da extracao"""
        extraction_result_message = ""

        # Resultado da coleta de noticias
        news_data = extraction_result.get('news_collection', {})
        extraction_result_message += f"\nColeta de noticias: {news_data.get('total_news', 0)} noticias\n"
        extraction_result_message += f"   Fontes bem-sucedidas: {news_data.get('successful_sources', 0)}/{news_data.get('total_sources', 0)}\n"

        # Resultado da extracao de topicos
        topic_data = extraction_result.get('topic_extraction', {})
        keywords = topic_data.get('keywords', [])
        summary = topic_data.get('summary', '')

        extraction_result_message += f"\nPalavras-chave extraidas: {len(keywords)}\n"
        if keywords:
            # Exibir 5 palavras-chave por linha
            for i in range(0, len(keywords), 5):
                keyword_group = keywords[i:i+5]
                extraction_result_message += f"   {', '.join(keyword_group)}\n"

        extraction_result_message += f"\nResumo das noticias:\n   {summary}\n"

        # Resultado do salvamento no banco de dados
        db_data = extraction_result.get('database_save', {})
        if db_data.get('success'):
            extraction_result_message += f"\nSalvamento no banco de dados: Sucesso\n"
        else:
            extraction_result_message += f"\nSalvamento no banco de dados: Falha\n"

        logger.info(extraction_result_message)

    def get_keywords_for_crawling(self, extract_date: date = None) -> List[str]:
        """
        Obter lista de palavras-chave para crawling

        Args:
            extract_date: Data de extracao, padrao e hoje

        Returns:
            Lista de palavras-chave
        """
        try:
            # Obter analise de topicos do banco de dados
            topics_data = self.db_manager.get_daily_topics(extract_date)

            if not topics_data:
                logger.info(f"Nenhum dado de topico encontrado para {extract_date or date.today()}")
                return []

            keywords = topics_data['keywords']

            # Gerar palavras-chave de busca
            search_keywords = self.topic_extractor.get_search_keywords(keywords)

            logger.info(f"{len(search_keywords)} palavras-chave preparadas para crawling")
            return search_keywords

        except Exception as e:
            logger.error(f"Falha ao obter palavras-chave para crawling: {e}")
            return []

    def get_daily_analysis(self, target_date: date = None) -> Optional[Dict]:
        """Obter resultado de analise para uma data especifica"""
        try:
            return self.db_manager.get_daily_topics(target_date)
        except Exception as e:
            logger.error(f"Falha ao obter analise diaria: {e}")
            return None

    def get_recent_analysis(self, days: int = 7) -> List[Dict]:
        """Obter resultados de analise dos ultimos dias"""
        try:
            return self.db_manager.get_recent_topics(days)
        except Exception as e:
            logger.error(f"Falha ao obter analises recentes: {e}")
            return []

# ==================== Ferramenta de linha de comando ====================

async def run_extraction_command(sources=None, keywords_count=100, show_details=True):
    """Executar comando de extracao de topicos"""

    try:
        async with BroadTopicExtraction() as extractor:
            # Executar extracao de topicos
            result = await extractor.run_daily_extraction(
                news_sources=sources,
                max_keywords=keywords_count
            )

            if result['success']:
                if show_details:
                    # Exibir resultados detalhados
                    extractor.print_extraction_results(result)
                else:
                    # Exibir apenas resultado resumido
                    news_data = result.get('news_collection', {})
                    topic_data = result.get('topic_extraction', {})

                    logger.info(f"Extracao de topicos concluida com sucesso!")
                    logger.info(f"   Noticias coletadas: {news_data.get('total_news', 0)}")
                    logger.info(f"   Palavras-chave extraidas: {len(topic_data.get('keywords', []))}")
                    logger.info(f"   Resumo gerado: {len(topic_data.get('summary', ''))} caracteres")

                # Obter palavras-chave para crawling
                crawling_keywords = extractor.get_keywords_for_crawling()

                if crawling_keywords:
                    logger.info(f"\nPalavras-chave de busca preparadas para DeepSentimentCrawling:")
                    logger.info(f"   {', '.join(crawling_keywords)}")

                    # Salvar palavras-chave em arquivo
                    keywords_file = project_root / "data" / "daily_keywords.txt"
                    keywords_file.parent.mkdir(exist_ok=True)

                    with open(keywords_file, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(crawling_keywords))

                    logger.info(f"   Palavras-chave salvas em: {keywords_file}")

                return True

            else:
                logger.error(f"Falha na extracao de topicos: {result.get('error', 'Erro desconhecido')}")
                return False

    except Exception as e:
        logger.error(f"Erro durante a execucao: {e}")
        return False

def main():
    """Funcao principal"""
    parser = argparse.ArgumentParser(description="Ferramenta de extracao diaria de topicos MindSpider")
    parser.add_argument("--sources", nargs="+", help="Especificar plataformas de fontes de noticias",
                       choices=list(SOURCE_NAMES.keys()))
    parser.add_argument("--keywords", type=int, default=100, help="Quantidade maxima de palavras-chave (padrao 100)")
    parser.add_argument("--quiet", action="store_true", help="Modo de saida simplificada")
    parser.add_argument("--list-sources", action="store_true", help="Exibir fontes de noticias suportadas")

    args = parser.parse_args()

    # Exibir fontes de noticias suportadas
    if args.list_sources:
        logger.info("Plataformas de fontes de noticias suportadas:")
        for source, name in SOURCE_NAMES.items():
            logger.info(f"  {source:<25} {name}")
        return

    # Validar parametros
    if args.keywords < 1 or args.keywords > 200:
        logger.error("A quantidade de palavras-chave deve estar entre 1 e 200")
        sys.exit(1)

    # Executar extracao
    try:
        success = asyncio.run(run_extraction_command(
            sources=args.sources,
            keywords_count=args.keywords,
            show_details=not args.quiet
        ))

        sys.exit(0 if success else 1)

    except KeyboardInterrupt:
        logger.info("Operacao interrompida pelo usuario")
        sys.exit(1)

if __name__ == "__main__":
    main()
