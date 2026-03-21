#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Módulo BroadTopicExtraction - Coleta e armazenamento de notícias
Integra chamadas a feeds RSS internacionais e armazenamento em banco de dados
"""

import sys
import asyncio
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime, date
from pathlib import Path
from typing import List, Dict, Optional
from loguru import logger

# Adiciona o diretório raiz do projeto ao path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

try:
    from BroadTopicExtraction.database_manager import DatabaseManager
except ImportError as e:
    raise ImportError(f"Falha ao importar módulo: {e}")

# Nomes das fontes de notícias organizados por região
SOURCE_NAMES = {
    # Brasil (fontes primárias)
    "g1": "G1 (Globo)",
    "folha": "Folha de São Paulo",
    "uol": "UOL Notícias",
    "estadao": "O Estado de S. Paulo",
    "r7": "R7 Notícias",
    "bbc-brasil": "BBC Brasil",
    # Estados Unidos
    "reuters": "Reuters",
    "ap-news": "Associated Press",
    "cnn": "CNN",
    "nyt": "New York Times",
    # Europa
    "bbc": "BBC News",
    "dw": "Deutsche Welle",
    "france24": "France 24",
    "euronews": "Euronews",
    # América do Sul
    "clarin": "Clarín (Argentina)",
    "emol": "EMOL (Chile)",
    "eltiempo": "El Tiempo (Colombia)",
    "elcomercio-pe": "El Comercio (Peru)",
    "elpais-uy": "El País (Uruguay)",
    "abc-py": "ABC Color (Paraguay)",
    "eldeber": "El Deber (Bolivia)",
    "eluniverso": "El Universo (Ecuador)",
    "ultimasnoticias": "Últimas Notícias (Venezuela)",
    # Global
    "github-trending": "GitHub Trending",
}

# Mapeamento de cada fonte para sua região
SOURCE_REGIONS = {
    "g1": "brasil",
    "folha": "brasil",
    "uol": "brasil",
    "estadao": "brasil",
    "r7": "brasil",
    "bbc-brasil": "brasil",
    "reuters": "usa",
    "ap-news": "usa",
    "cnn": "usa",
    "nyt": "usa",
    "bbc": "europa",
    "dw": "europa",
    "france24": "europa",
    "euronews": "europa",
    "clarin": "america_do_sul",
    "emol": "america_do_sul",
    "eltiempo": "america_do_sul",
    "elcomercio-pe": "america_do_sul",
    "elpais-uy": "america_do_sul",
    "abc-py": "america_do_sul",
    "eldeber": "america_do_sul",
    "eluniverso": "america_do_sul",
    "ultimasnoticias": "america_do_sul",
    "github-trending": "global",
}

# Rótulos legíveis para cada região
REGION_LABELS = {
    "brasil": "Brasil",
    "usa": "Estados Unidos",
    "europa": "Europa",
    "america_do_sul": "América do Sul",
    "global": "Global",
}

# URLs dos feeds RSS para cada fonte
SOURCE_RSS_FEEDS = {
    # Brasil
    "g1": "https://g1.globo.com/rss/g1/",
    "folha": "https://feeds.folha.uol.com.br/emcimadahora/rss091.xml",
    "uol": "https://rss.uol.com.br/feed/noticias.xml",
    "estadao": "https://www.estadao.com.br/pf/rss/ultimas.xml",
    "r7": "https://noticias.r7.com/feed.xml",
    "bbc-brasil": "https://www.bbc.com/portuguese/index.xml",
    # Estados Unidos
    "reuters": "https://www.rss.reuters.com/news/topNews",
    "ap-news": "https://rsshub.app/apnews/topics/apf-topnews",
    "cnn": "http://rss.cnn.com/rss/edition.rss",
    "nyt": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    # Europa
    "bbc": "http://feeds.bbci.co.uk/news/rss.xml",
    "dw": "https://rss.dw.com/xml/rss-br",
    "france24": "https://www.france24.com/en/rss",
    "euronews": "https://www.euronews.com/rss",
    # América do Sul
    "clarin": "https://www.clarin.com/rss/lo-ultimo/",
    "emol": "https://www.emol.com/rss/noticias.xml",
    "eltiempo": "https://www.eltiempo.com/rss/pages.xml",
    "elcomercio-pe": "https://elcomercio.pe/arcio/rss/",
    "elpais-uy": "https://www.elpais.com.uy/rss/",
    "abc-py": "https://www.abc.com.py/rss/",
    "eldeber": "https://eldeber.com.bo/rss",
    "eluniverso": "https://www.eluniverso.com/rss/",
    "ultimasnoticias": "https://www.ultimasnoticias.com.ve/feed/",
}

# URL da API para GitHub Trending
GITHUB_TRENDING_URL = "https://api.gitterapp.com/repositories?since=daily"


class NewsCollector:
    """Coletor de notícias - Integra parsing de RSS e armazenamento em banco de dados"""

    def __init__(self):
        """Inicializa o coletor de notícias"""
        self.db_manager = DatabaseManager()
        self.supported_sources = list(SOURCE_NAMES.keys())

    def close(self):
        """Libera recursos"""
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

    # ==================== Consultas de região ====================

    @staticmethod
    def get_sources_by_region(region: str) -> List[str]:
        """
        Retorna os IDs das fontes de uma determinada região.

        Args:
            region: Identificador da região (ex: 'brasil', 'usa', 'europa',
                    'america_do_sul', 'global')

        Returns:
            Lista de IDs de fontes pertencentes à região
        """
        return [
            source_id
            for source_id, source_region in SOURCE_REGIONS.items()
            if source_region == region
        ]

    @staticmethod
    def get_available_regions() -> Dict[str, str]:
        """
        Retorna as regiões disponíveis com seus rótulos legíveis.

        Returns:
            Dicionário {id_regiao: rotulo}
        """
        return dict(REGION_LABELS)

    # ==================== Coleta de notícias via RSS ====================

    async def fetch_news(self, source: str) -> dict:
        """
        Obtém as últimas notícias de uma fonte via feed RSS.

        Para a fonte 'github-trending', utiliza a API dedicada.

        Args:
            source: Identificador da fonte (ex: 'g1', 'reuters', 'github-trending')

        Returns:
            Dicionário com status, dados e timestamp da coleta
        """
        if source == "github-trending":
            return await self._fetch_github_trending()

        feed_url = SOURCE_RSS_FEEDS.get(source)
        if not feed_url:
            return {
                "source": source,
                "status": "error",
                "error": f"URL de feed RSS não definida para a fonte: {source}",
                "timestamp": datetime.now().isoformat(),
            }

        headers = {
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8,es;q=0.7",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Connection": "keep-alive",
        }

        try:
            async with httpx.AsyncClient(
                timeout=30.0, follow_redirects=True
            ) as client:
                response = await client.get(feed_url, headers=headers)
                response.raise_for_status()

                # Faz o parsing do XML do feed RSS
                items = self._parse_rss_feed(response.text)

                return {
                    "source": source,
                    "status": "success",
                    "data": {"items": items},
                    "timestamp": datetime.now().isoformat(),
                }

        except httpx.TimeoutException:
            return {
                "source": source,
                "status": "timeout",
                "error": f"Tempo esgotado ao acessar: {source} ({feed_url})",
                "timestamp": datetime.now().isoformat(),
            }
        except httpx.HTTPStatusError as e:
            return {
                "source": source,
                "status": "http_error",
                "error": (
                    f"Erro HTTP: {source} ({feed_url}) - "
                    f"código {e.response.status_code}"
                ),
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            return {
                "source": source,
                "status": "error",
                "error": f"Erro inesperado: {source} ({feed_url}) - {str(e)}",
                "timestamp": datetime.now().isoformat(),
            }

    def _parse_rss_feed(self, xml_text: str) -> List[Dict]:
        """
        Faz o parsing de um feed RSS/XML e extrai os itens de notícia.

        Suporta feeds RSS 2.0 e Atom. Extrai título, link, descrição e
        data de publicação de cada item.

        Args:
            xml_text: Conteúdo XML do feed como string

        Returns:
            Lista de dicionários com as chaves: title, url, description, pubDate
        """
        items = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"Falha ao fazer parsing do XML do feed: {e}")
            return items

        # Detecta namespaces comuns do Atom
        atom_ns = "{http://www.w3.org/2005/Atom}"

        # Tenta encontrar itens RSS 2.0 (<channel><item>)
        rss_items = root.findall(".//item")

        if rss_items:
            for item_elem in rss_items:
                title = self._get_element_text(item_elem, "title")
                link = self._get_element_text(item_elem, "link")
                description = self._get_element_text(item_elem, "description")
                pub_date = self._get_element_text(item_elem, "pubDate")

                if title:
                    items.append({
                        "title": title.strip(),
                        "url": (link or "").strip(),
                        "description": (description or "").strip(),
                        "pubDate": (pub_date or "").strip(),
                    })
        else:
            # Tenta encontrar entradas Atom (<entry>)
            entries = root.findall(f".//{atom_ns}entry")
            if not entries:
                entries = root.findall(".//entry")

            for entry in entries:
                title = (
                    self._get_element_text(entry, f"{atom_ns}title")
                    or self._get_element_text(entry, "title")
                )

                # Em feeds Atom, o link fica no atributo href
                link_elem = entry.find(f"{atom_ns}link")
                if link_elem is None:
                    link_elem = entry.find("link")
                link = (
                    link_elem.get("href", "") if link_elem is not None else ""
                )

                summary = (
                    self._get_element_text(entry, f"{atom_ns}summary")
                    or self._get_element_text(entry, "summary")
                    or self._get_element_text(entry, f"{atom_ns}content")
                    or self._get_element_text(entry, "content")
                )
                updated = (
                    self._get_element_text(entry, f"{atom_ns}updated")
                    or self._get_element_text(entry, "updated")
                    or self._get_element_text(entry, f"{atom_ns}published")
                    or self._get_element_text(entry, "published")
                )

                if title:
                    items.append({
                        "title": title.strip(),
                        "url": (link or "").strip(),
                        "description": (summary or "").strip(),
                        "pubDate": (updated or "").strip(),
                    })

        return items

    @staticmethod
    def _get_element_text(parent, tag: str) -> Optional[str]:
        """
        Obtém o texto de um sub-elemento XML de forma segura.

        Args:
            parent: Elemento XML pai
            tag: Nome da tag filha

        Returns:
            Texto do elemento ou None se não encontrado
        """
        elem = parent.find(tag)
        if elem is not None and elem.text:
            return elem.text
        return None

    async def _fetch_github_trending(self) -> dict:
        """
        Obtém os repositórios em destaque do GitHub Trending.

        Utiliza a API gitterapp para buscar os repositórios mais populares
        do dia.

        Returns:
            Dicionário com status, dados e timestamp da coleta
        """
        headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        }

        try:
            async with httpx.AsyncClient(
                timeout=30.0, follow_redirects=True
            ) as client:
                response = await client.get(
                    GITHUB_TRENDING_URL, headers=headers
                )
                response.raise_for_status()

                repos = response.json()
                items = []
                for repo in repos if isinstance(repos, list) else []:
                    name = repo.get("name", "")
                    author = repo.get("author", "")
                    description = repo.get("description", "")
                    url = repo.get("url", f"https://github.com/{author}/{name}")
                    stars = repo.get("stars", 0)
                    items.append({
                        "title": f"{author}/{name} - {description}",
                        "url": url,
                        "description": f"Estrelas: {stars}. {description}",
                        "pubDate": "",
                    })

                return {
                    "source": "github-trending",
                    "status": "success",
                    "data": {"items": items},
                    "timestamp": datetime.now().isoformat(),
                }

        except Exception as e:
            return {
                "source": "github-trending",
                "status": "error",
                "error": (
                    f"Erro ao buscar GitHub Trending: {str(e)}"
                ),
                "timestamp": datetime.now().isoformat(),
            }

    async def get_popular_news(self, sources: List[str] = None) -> List[dict]:
        """
        Obtém notícias populares de múltiplas fontes.

        Args:
            sources: Lista de IDs de fontes. Se None, usa todas as fontes.

        Returns:
            Lista de resultados de cada fonte
        """
        if sources is None:
            sources = list(SOURCE_NAMES.keys())

        logger.info(
            f"Buscando notícias de {len(sources)} fonte(s)..."
        )
        logger.info("=" * 80)

        results = []
        for source in sources:
            source_name = SOURCE_NAMES.get(source, source)
            logger.info(f"Buscando notícias de {source_name}...")
            result = await self.fetch_news(source)
            results.append(result)

            if result["status"] == "success":
                data = result["data"]
                if "items" in data and isinstance(data["items"], list):
                    count = len(data["items"])
                    logger.info(
                        f"OK {source_name}: coleta bem-sucedida, "
                        f"{count} notícia(s)"
                    )
                else:
                    logger.info(
                        f"OK {source_name}: coleta bem-sucedida"
                    )
            else:
                logger.error(
                    f"FALHA {source_name}: "
                    f"{result.get('error', 'falha na coleta')}"
                )

            # Pausa breve entre requisições para evitar bloqueios
            await asyncio.sleep(0.5)

        return results

    # ==================== Processamento e armazenamento ====================

    async def collect_and_save_news(
        self,
        sources: Optional[List[str]] = None,
        regions: Optional[List[str]] = None,
    ) -> Dict:
        """
        Coleta e salva as notícias do dia.

        Args:
            sources: Lista de IDs de fontes específicas. Se None e regions
                     também for None, usa todas as fontes.
            regions: Lista de regiões para filtrar fontes (ex: ['brasil', 'usa']).
                     Se fornecido, sobrescreve o parâmetro sources.

        Returns:
            Dicionário com o resumo da coleta
        """
        collection_summary_message = ""
        collection_summary_message += "\nIniciando coleta de notícias do dia...\n"
        collection_summary_message += (
            f"Horário: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )

        # Se regiões foram especificadas, determina as fontes a partir delas
        if regions is not None:
            sources = []
            for region in regions:
                sources.extend(self.get_sources_by_region(region))
            if not sources:
                logger.warning(
                    f"Nenhuma fonte encontrada para as regiões: {regions}"
                )
                return {
                    "success": False,
                    "error": f"Nenhuma fonte encontrada para as regiões: {regions}",
                    "news_list": [],
                    "total_news": 0,
                }

        # Se nenhuma fonte foi especificada, usa todas
        if sources is None:
            sources = list(SOURCE_NAMES.keys())

        collection_summary_message += (
            f"Coletando dados de {len(sources)} fonte(s):\n"
        )
        for source in sources:
            source_name = SOURCE_NAMES.get(source, source)
            region = SOURCE_REGIONS.get(source, "desconhecida")
            region_label = REGION_LABELS.get(region, region)
            collection_summary_message += (
                f"  - {source_name} [{region_label}]\n"
            )

        logger.info(collection_summary_message)

        try:
            # Obtém os dados de notícias
            results = await self.get_popular_news(sources)

            # Processa os resultados
            processed_data = self._process_news_results(results)

            # Salva no banco de dados (modo sobrescrever)
            if processed_data["news_list"]:
                saved_count = self.db_manager.save_daily_news(
                    processed_data["news_list"], date.today()
                )
                processed_data["saved_count"] = saved_count

            # Exibe o resumo da coleta
            self._print_collection_summary(processed_data)

            return processed_data

        except Exception as e:
            logger.exception(f"Falha ao coletar notícias: {e}")
            return {
                "success": False,
                "error": str(e),
                "news_list": [],
                "total_news": 0,
            }

    def _process_news_results(self, results: List[Dict]) -> Dict:
        """
        Processa os resultados obtidos das fontes de notícias.

        Args:
            results: Lista de resultados brutos de cada fonte

        Returns:
            Dicionário com a lista de notícias processadas e estatísticas
        """
        news_list = []
        successful_sources = 0
        total_news = 0

        for result in results:
            source = result["source"]
            status = result["status"]

            if status == "success":
                successful_sources += 1
                data = result["data"]

                if "items" in data and isinstance(data["items"], list):
                    source_news_count = len(data["items"])
                    total_news += source_news_count

                    # Processa cada notícia da fonte
                    for i, item in enumerate(data["items"], 1):
                        processed_news = self._process_news_item(
                            item, source, i
                        )
                        if processed_news:
                            news_list.append(processed_news)

        return {
            "success": True,
            "news_list": news_list,
            "successful_sources": successful_sources,
            "total_sources": len(results),
            "total_news": total_news,
            "collection_time": datetime.now().isoformat(),
        }

    def _process_news_item(
        self, item: Dict, source: str, rank: int
    ) -> Optional[Dict]:
        """
        Processa um único item de notícia.

        Args:
            item: Dicionário com os dados brutos da notícia
            source: Identificador da fonte
            rank: Posição/ranking da notícia na fonte

        Returns:
            Dicionário processado ou None em caso de erro
        """
        try:
            if isinstance(item, dict):
                title = item.get("title", "Sem título").strip()
                url = item.get("url", "")

                # Gera um ID único para a notícia
                news_id = f"{source}_{item.get('id', f'rank_{rank}')}"

                return {
                    "id": news_id,
                    "title": title,
                    "url": url,
                    "source": source,
                    "rank": rank,
                }
            else:
                # Trata itens que chegam como string
                title = str(item)[:100] if len(str(item)) > 100 else str(item)
                return {
                    "id": f"{source}_rank_{rank}",
                    "title": title,
                    "url": "",
                    "source": source,
                    "rank": rank,
                }

        except Exception as e:
            logger.exception(f"Falha ao processar item de notícia: {e}")
            return None

    def _print_collection_summary(self, data: Dict):
        """
        Exibe o resumo da coleta no log.

        Args:
            data: Dicionário com as estatísticas da coleta
        """
        collection_summary_message = ""
        collection_summary_message += (
            f"\nTotal de fontes: {data['total_sources']}\n"
        )
        collection_summary_message += (
            f"Fontes com sucesso: {data['successful_sources']}\n"
        )
        collection_summary_message += (
            f"Total de notícias: {data['total_news']}\n"
        )
        if "saved_count" in data:
            collection_summary_message += (
                f"Notícias salvas: {data['saved_count']}\n"
            )
        logger.info(collection_summary_message)

    def get_today_news(self) -> List[Dict]:
        """
        Obtém as notícias do dia armazenadas no banco de dados.

        Returns:
            Lista de dicionários com as notícias do dia
        """
        try:
            return self.db_manager.get_daily_news(date.today())
        except Exception as e:
            logger.exception(f"Falha ao obter notícias do dia: {e}")
            return []


async def main():
    """Testa o coletor de notícias com fontes brasileiras"""
    logger.info("Testando o coletor de notícias...")

    async with NewsCollector() as collector:
        # Exibe as regiões disponíveis
        regioes = collector.get_available_regions()
        logger.info(f"Regiões disponíveis: {regioes}")

        # Exibe as fontes da região Brasil
        fontes_brasil = collector.get_sources_by_region("brasil")
        logger.info(f"Fontes do Brasil: {fontes_brasil}")

        # Coleta notícias apenas do Brasil para teste
        result = await collector.collect_and_save_news(
            regions=["brasil"]
        )

        if result["success"]:
            logger.info(
                f"Coleta bem-sucedida! {result['total_news']} notícia(s) obtidas"
            )
        else:
            logger.error(
                f"Falha na coleta: {result.get('error', 'erro desconhecido')}"
            )


if __name__ == "__main__":
    asyncio.run(main())
