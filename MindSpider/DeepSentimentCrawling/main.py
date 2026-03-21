#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modulo DeepSentimentCrawling - Fluxo de trabalho principal
Crawling de palavras-chave em todas as plataformas baseado nos topicos extraidos pelo BroadTopicExtraction
"""

import sys
import argparse
from datetime import date, datetime
from pathlib import Path
from typing import List, Dict

# Adicionar diretorio raiz do projeto ao path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from keyword_manager import KeywordManager
from platform_crawler import PlatformCrawler

class DeepSentimentCrawling:
    """Fluxo de trabalho principal do crawling profundo de sentimento"""

    def __init__(self):
        """Inicializar crawling profundo de sentimento"""
        self.keyword_manager = KeywordManager()
        self.platform_crawler = PlatformCrawler()
        self.supported_platforms = ['xhs', 'dy', 'ks', 'bili', 'wb', 'tieba', 'zhihu']

    def run_daily_crawling(self, target_date: date = None, platforms: List[str] = None,
                          max_keywords_per_platform: int = 50,
                          max_notes_per_platform: int = 50,
                          login_type: str = "qrcode") -> Dict:
        """
        Executar tarefa de crawling diario

        Args:
            target_date: Data alvo, padrao e hoje
            platforms: Lista de plataformas para crawling, padrao e todas as plataformas suportadas
            max_keywords_per_platform: Quantidade maxima de palavras-chave por plataforma
            max_notes_per_platform: Quantidade maxima de conteudo coletado por plataforma
            login_type: Metodo de login

        Returns:
            Estatisticas do resultado do crawling
        """
        if not target_date:
            target_date = date.today()

        if not platforms:
            platforms = self.supported_platforms

        print(f"Iniciando tarefa de crawling profundo de sentimento para {target_date}")
        print(f"Plataformas alvo: {platforms}")

        # 1. Obter resumo de palavras-chave
        summary = self.keyword_manager.get_crawling_summary(target_date)
        print(f"Resumo de palavras-chave: {summary}")

        if not summary['has_data']:
            print("Nenhum dado de topico encontrado, impossivel realizar crawling")
            print("Por favor, execute primeiro o seguinte comando para obter os topicos de hoje:")
            print("   uv run main.py --broad-topic")
            return {"success": False, "error": "Sem dados de topicos"}

        # 2. Obter palavras-chave (sem distribuicao, todas as plataformas usam as mesmas palavras-chave)
        print(f"\nObtendo palavras-chave...")
        keywords = self.keyword_manager.get_latest_keywords(target_date, max_keywords_per_platform)

        if not keywords:
            print("Nenhuma palavra-chave encontrada, impossivel realizar crawling")
            return {"success": False, "error": "Sem palavras-chave"}

        print(f"   {len(keywords)} palavras-chave obtidas")
        print(f"   Crawling de cada palavra-chave em {len(platforms)} plataformas")
        print(f"   Total de tarefas de crawling: {len(keywords)} x {len(platforms)} = {len(keywords) * len(platforms)}")

        # 3. Executar crawling de palavras-chave em todas as plataformas
        print(f"\nIniciando crawling de palavras-chave em todas as plataformas...")
        crawl_results = self.platform_crawler.run_multi_platform_crawl_by_keywords(
            keywords, platforms, login_type, max_notes_per_platform
        )

        # 4. Gerar relatorio final
        final_report = {
            "date": target_date.isoformat(),
            "summary": summary,
            "crawl_results": crawl_results,
            "success": crawl_results["successful_tasks"] > 0
        }

        print(f"\nTarefa de crawling profundo de sentimento concluida!")
        print(f"   Data: {target_date}")
        print(f"   Tarefas bem-sucedidas: {crawl_results['successful_tasks']}/{crawl_results['total_tasks']}")
        print(f"   Total de palavras-chave: {crawl_results['total_keywords']}")
        print(f"   Total de plataformas: {crawl_results['total_platforms']}")
        print(f"   Total de conteudo: {crawl_results['total_notes']} itens")

        return final_report

    def run_platform_crawling(self, platform: str, target_date: date = None,
                             max_keywords: int = 50, max_notes: int = 50,
                             login_type: str = "qrcode") -> Dict:
        """
        Executar tarefa de crawling de uma unica plataforma

        Args:
            platform: Nome da plataforma
            target_date: Data alvo
            max_keywords: Quantidade maxima de palavras-chave
            max_notes: Quantidade maxima de conteudo coletado
            login_type: Metodo de login

        Returns:
            Resultado do crawling
        """
        if platform not in self.supported_platforms:
            raise ValueError(f"Plataforma nao suportada: {platform}")

        if not target_date:
            target_date = date.today()

        print(f"Iniciando tarefa de crawling da plataforma {platform} ({target_date})")

        # Obter palavras-chave
        keywords = self.keyword_manager.get_keywords_for_platform(
            platform, target_date, max_keywords
        )

        if not keywords:
            print(f"Nenhuma palavra-chave encontrada para a plataforma {platform}")
            return {"success": False, "error": "Sem palavras-chave"}

        print(f"Preparando crawling de {len(keywords)} palavras-chave")

        # Executar crawling
        result = self.platform_crawler.run_crawler(
            platform, keywords, login_type, max_notes
        )

        return result

    def list_available_topics(self, days: int = 7):
        """Listar topicos disponiveis recentes"""
        print(f"Dados de topicos dos ultimos {days} dias:")

        recent_topics = self.keyword_manager.db_manager.get_recent_topics(days)

        if not recent_topics:
            print("   Sem dados de topicos no momento")
            return

        for topic in recent_topics:
            extract_date = topic['extract_date']
            keywords_count = len(topic.get('keywords', []))
            summary_preview = topic.get('summary', '')[:100] + "..." if len(topic.get('summary', '')) > 100 else topic.get('summary', '')

            print(f"   {extract_date}: {keywords_count} palavras-chave")
            print(f"      Resumo: {summary_preview}")
            print()

    def show_platform_guide(self):
        """Exibir guia de uso das plataformas"""
        print("Guia de crawling por plataforma:")
        print()

        platform_info = {
            'xhs': 'Xiaohongshu - Conteudo de beleza, estilo de vida, moda',
            'dy': 'Douyin - Videos curtos, entretenimento, estilo de vida',
            'ks': 'Kuaishou - Estilo de vida, entretenimento, conteudo rural',
            'bili': 'Bilibili - Tecnologia, aprendizado, jogos, anime',
            'wb': 'Weibo - Noticias em destaque, celebridades, topicos sociais',
            'tieba': 'Baidu Tieba - Discussoes de interesses, jogos, aprendizado',
            'zhihu': 'Zhihu - Perguntas e respostas, discussoes aprofundadas'
        }

        for platform, desc in platform_info.items():
            print(f"   {platform}: {desc}")

        print()
        print("Sugestoes de uso:")
        print("   1. Na primeira vez e necessario escanear QR code para login em cada plataforma")
        print("   2. Recomenda-se testar primeiro com uma unica plataforma para confirmar que o login esta normal")
        print("   3. Nao colete muitos dados para evitar restricoes")
        print("   4. Use o modo --test para testes em pequena escala")

    def close(self):
        """Fechar recursos"""
        if self.keyword_manager:
            self.keyword_manager.close()

def main():
    """Ponto de entrada da linha de comando"""
    parser = argparse.ArgumentParser(description="DeepSentimentCrawling - Crawling profundo de sentimento baseado em topicos")

    # Parametros basicos
    parser.add_argument("--date", type=str, help="Data alvo (AAAA-MM-DD), padrao e hoje")
    parser.add_argument("--platform", type=str, choices=['xhs', 'dy', 'ks', 'bili', 'wb', 'tieba', 'zhihu'],
                       help="Especificar uma unica plataforma para crawling")
    parser.add_argument("--platforms", type=str, nargs='+',
                       choices=['xhs', 'dy', 'ks', 'bili', 'wb', 'tieba', 'zhihu'],
                       help="Especificar multiplas plataformas para crawling")

    # Parametros de crawling
    parser.add_argument("--max-keywords", type=int, default=50,
                       help="Quantidade maxima de palavras-chave por plataforma (padrao: 50)")
    parser.add_argument("--max-notes", type=int, default=50,
                       help="Quantidade maxima de conteudo coletado por plataforma (padrao: 50)")
    parser.add_argument("--login-type", type=str, choices=['qrcode', 'phone', 'cookie'],
                       default='qrcode', help="Metodo de login (padrao: qrcode)")

    # Parametros de funcionalidade
    parser.add_argument("--list-topics", action="store_true", help="Listar dados de topicos recentes")
    parser.add_argument("--days", type=int, default=7, help="Ver topicos dos ultimos dias (padrao: 7)")
    parser.add_argument("--guide", action="store_true", help="Exibir guia de uso das plataformas")
    parser.add_argument("--test", action="store_true", help="Modo de teste (poucos dados)")

    args = parser.parse_args()

    # Analisar data
    target_date = None
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print("Formato de data incorreto, use o formato AAAA-MM-DD")
            return

    # Criar instancia de crawling
    crawler = DeepSentimentCrawling()

    try:
        # Exibir guia
        if args.guide:
            crawler.show_platform_guide()
            return

        # Listar topicos
        if args.list_topics:
            crawler.list_available_topics(args.days)
            return

        # Modo de teste - ajustar parametros
        if args.test:
            args.max_keywords = min(args.max_keywords, 10)
            args.max_notes = min(args.max_notes, 10)
            print("Modo de teste: quantidade de palavras-chave e conteudo limitada")

        # Crawling de plataforma unica
        if args.platform:
            result = crawler.run_platform_crawling(
                args.platform, target_date, args.max_keywords,
                args.max_notes, args.login_type
            )

            if result['success']:
                print(f"\nCrawling de {args.platform} concluido com sucesso!")
            else:
                print(f"\nFalha no crawling de {args.platform}: {result.get('error', 'Erro desconhecido')}")

            return

        # Crawling de multiplas plataformas
        platforms = args.platforms if args.platforms else None
        result = crawler.run_daily_crawling(
            target_date, platforms, args.max_keywords,
            args.max_notes, args.login_type
        )

        if result['success']:
            print(f"\nTarefa de crawling multi-plataforma concluida!")
        else:
            print(f"\nFalha no crawling multi-plataforma: {result.get('error', 'Erro desconhecido')}")

    except KeyboardInterrupt:
        print("\nOperacao interrompida pelo usuario")
    except Exception as e:
        print(f"\nErro na execucao: {e}")
    finally:
        crawler.close()

if __name__ == "__main__":
    main()
