"""
Conjunto de ferramentas de busca de opinião pública projetado para AI Agent (Tavily)

Versão: 1.5
Última atualização: 2025-08-22

Este script decompõe as funcionalidades complexas de busca do Tavily em uma série de ferramentas independentes
com objetivos claros e parâmetros mínimos, projetadas especificamente para chamadas por AI Agent.
O Agent só precisa selecionar a ferramenta adequada conforme a intenção da tarefa,
sem necessidade de compreender combinações complexas de parâmetros. Todas as ferramentas buscam "notícias" por padrão (topic='news').

Novidades:
- Adicionada ferramenta `basic_search_news` para executar buscas de notícias padrão e genéricas.
- Cada resultado de busca agora inclui `published_date` (data de publicação da notícia).

Ferramentas principais:
- basic_search_news: (Nova) Executa busca de notícias padrão e rápida.
- deep_search_news: Realiza a análise mais abrangente e profunda de um tema.
- search_news_last_24_hours: Obtém as atualizações mais recentes das últimas 24 horas.
- search_news_last_week: Obtém as principais reportagens da última semana.
- search_images_for_news: Busca imagens relacionadas a temas de notícias.
- search_news_by_date: Busca em um intervalo de datas históricas especificado.
"""

import os
import sys
from typing import List, Dict, Any, Optional

# Adicionar diretório utils ao caminho do Python
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(os.path.dirname(current_dir))
utils_dir = os.path.join(root_dir, 'utils')
if utils_dir not in sys.path:
    sys.path.append(utils_dir)

from retry_helper import with_graceful_retry, SEARCH_API_RETRY_CONFIG
from dataclasses import dataclass, field

# Certifique-se de que a biblioteca Tavily está instalada antes de executar: pip install tavily-python
try:
    from tavily import TavilyClient
except ImportError:
    raise ImportError("Biblioteca Tavily não instalada. Execute `pip install tavily-python` para instalar.")

# --- 1. Definição de estruturas de dados ---

@dataclass
class SearchResult:
    """
    Classe de dados para resultados de busca na web
    Contém o atributo published_date para armazenar a data de publicação da notícia
    """
    title: str
    url: str
    content: str
    score: Optional[float] = None
    raw_content: Optional[str] = None
    published_date: Optional[str] = None

@dataclass
class ImageResult:
    """Classe de dados para resultados de busca de imagens"""
    url: str
    description: Optional[str] = None

@dataclass
class TavilyResponse:
    """Encapsula o resultado completo da API Tavily para transferência entre ferramentas"""
    query: str
    answer: Optional[str] = None
    results: List[SearchResult] = field(default_factory=list)
    images: List[ImageResult] = field(default_factory=list)
    response_time: Optional[float] = None


# --- 2. Cliente principal e conjunto de ferramentas especializadas ---

class TavilyNewsAgency:
    """
    Um cliente contendo múltiplas ferramentas especializadas de busca de notícias e opinião pública.
    Cada método público é projetado como uma ferramenta a ser chamada independentemente pelo AI Agent.
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Inicializar o cliente.
        Args:
            api_key: Chave da API Tavily; se não fornecida, será lida da variável de ambiente TAVILY_API_KEY.
        """
        if api_key is None:
            api_key = os.getenv("TAVILY_API_KEY")
            if not api_key:
                raise ValueError("Chave da API Tavily não encontrada! Configure a variável de ambiente TAVILY_API_KEY ou forneça na inicialização")
        self._client = TavilyClient(api_key=api_key)

    @with_graceful_retry(SEARCH_API_RETRY_CONFIG, default_return=TavilyResponse(query="Falha na busca"))
    def _search_internal(self, **kwargs) -> TavilyResponse:
        """Executor interno genérico de busca; todas as ferramentas chamam este método no final"""
        try:
            kwargs['topic'] = 'general'
            api_params = {k: v for k, v in kwargs.items() if v is not None}
            response_dict = self._client.search(**api_params)

            search_results = [
                SearchResult(
                    title=item.get('title'),
                    url=item.get('url'),
                    content=item.get('content'),
                    score=item.get('score'),
                    raw_content=item.get('raw_content'),
                    published_date=item.get('published_date')
                ) for item in response_dict.get('results', [])
            ]

            image_results = [ImageResult(url=item.get('url'), description=item.get('description')) for item in response_dict.get('images', [])]

            return TavilyResponse(
                query=response_dict.get('query'), answer=response_dict.get('answer'),
                results=search_results, images=image_results,
                response_time=response_dict.get('response_time')
            )
        except Exception as e:
            print(f"Erro durante a busca: {str(e)}")
            raise e  # Deixar o mecanismo de retry capturar e tratar

    # --- Métodos de ferramentas disponíveis para o Agent ---

    def basic_search_news(self, query: str, max_results: int = 7) -> TavilyResponse:
        """
        [Ferramenta] Busca básica de notícias: executa uma busca de notícias padrão e rápida.
        Esta é a ferramenta de busca genérica mais utilizada, adequada quando não se tem certeza de qual tipo específico de busca é necessário.
        O Agent pode fornecer a consulta de busca (query) e opcionalmente o número máximo de resultados (max_results).
        """
        print(f"--- FERRAMENTA: Busca básica de notícias (query: {query}) ---")
        return self._search_internal(
            query=query,
            max_results=max_results,
            search_depth="basic",
            include_answer=False
        )

    def deep_search_news(self, query: str) -> TavilyResponse:
        """
        [Ferramenta] Análise profunda de notícias: realiza a busca mais abrangente e aprofundada sobre um tema.
        Retorna uma resposta detalhada de resumo "avançado" gerada por IA e até 20 resultados de notícias mais relevantes. Adequada para cenários que necessitam compreender completamente o contexto de um evento.
        O Agent só precisa fornecer a consulta de busca (query).
        """
        print(f"--- FERRAMENTA: Análise profunda de notícias (query: {query}) ---")
        return self._search_internal(
            query=query, search_depth="advanced", max_results=20, include_answer="advanced"
        )

    def search_news_last_24_hours(self, query: str) -> TavilyResponse:
        """
        [Ferramenta] Busca de notícias das últimas 24 horas: obtém as atualizações mais recentes sobre um tema.
        Esta ferramenta busca especificamente notícias publicadas nas últimas 24 horas. Adequada para acompanhar eventos urgentes ou últimos desdobramentos.
        O Agent só precisa fornecer a consulta de busca (query).
        """
        print(f"--- FERRAMENTA: Busca de notícias das últimas 24 horas (query: {query}) ---")
        return self._search_internal(query=query, time_range='d', max_results=10)

    def search_news_last_week(self, query: str) -> TavilyResponse:
        """
        [Ferramenta] Busca de notícias da semana: obtém as principais reportagens da última semana sobre um tema.
        Adequada para realizar resumos semanais de opinião pública ou revisões.
        O Agent só precisa fornecer a consulta de busca (query).
        """
        print(f"--- FERRAMENTA: Busca de notícias da semana (query: {query}) ---")
        return self._search_internal(query=query, time_range='w', max_results=10)

    def search_images_for_news(self, query: str) -> TavilyResponse:
        """
        [Ferramenta] Busca de imagens de notícias: busca imagens relacionadas a um tema de notícia.
        Esta ferramenta retorna links de imagens e descrições, adequada para cenários que necessitam de ilustrações para relatórios ou artigos.
        O Agent só precisa fornecer a consulta de busca (query).
        """
        print(f"--- FERRAMENTA: Busca de imagens de notícias (query: {query}) ---")
        return self._search_internal(
            query=query, include_images=True, include_image_descriptions=True, max_results=5
        )

    def search_news_by_date(self, query: str, start_date: str, end_date: str) -> TavilyResponse:
        """
        [Ferramenta] Busca de notícias por intervalo de datas: busca notícias em um período histórico específico.
        Esta é a única ferramenta que requer que o Agent forneça parâmetros detalhados de tempo. Adequada para cenários que necessitam analisar eventos históricos específicos.
        O Agent precisa fornecer a consulta (query), data de início (start_date) e data de fim (end_date), ambas no formato 'YYYY-MM-DD'.
        """
        print(f"--- FERRAMENTA: Busca de notícias por intervalo de datas (query: {query}, de: {start_date}, até: {end_date}) ---")
        return self._search_internal(
            query=query, start_date=start_date, end_date=end_date, max_results=15
        )


# --- 3. Testes e exemplos de uso ---

def print_response_summary(response: TavilyResponse):
    """Função de impressão simplificada para exibir resultados de teste, agora exibindo a data de publicação"""
    if not response or not response.query:
        print("Não foi possível obter uma resposta válida.")
        return

    print(f"\nConsulta: '{response.query}' | Tempo: {response.response_time}s")
    if response.answer:
        print(f"Resumo IA: {response.answer[:120]}...")
    print(f"Encontrados {len(response.results)} páginas web, {len(response.images)} imagens.")
    if response.results:
        first_result = response.results[0]
        date_info = f"(Publicado em: {first_result.published_date})" if first_result.published_date else ""
        print(f"Primeiro resultado: {first_result.title} {date_info}")
    print("-" * 60)


if __name__ == "__main__":
    # Antes de executar, certifique-se de que a variável de ambiente TAVILY_API_KEY está configurada

    try:
        # Inicializar o cliente "agência de notícias", que contém todas as ferramentas internamente
        agency = TavilyNewsAgency()

        # Cenário 1: Agent realiza uma busca rápida e genérica
        response1 = agency.basic_search_news(query="últimos resultados das Olimpíadas", max_results=5)
        print_response_summary(response1)

        # Cenário 2: Agent precisa compreender completamente o contexto da "competição global de tecnologia de chips"
        response2 = agency.deep_search_news(query="competição global de tecnologia de chips")
        print_response_summary(response2)

        # Cenário 3: Agent precisa acompanhar as últimas notícias da "conferência GTC"
        response3 = agency.search_news_last_24_hours(query="Nvidia conferência GTC últimos lançamentos")
        print_response_summary(response3)

        # Cenário 4: Agent precisa encontrar material para um relatório semanal sobre "direção autônoma"
        response4 = agency.search_news_last_week(query="comercialização de direção autônoma")
        print_response_summary(response4)

        # Cenário 5: Agent precisa buscar imagens de notícias do "Telescópio Espacial Webb"
        response5 = agency.search_images_for_news(query="últimas descobertas do Telescópio Espacial Webb")
        print_response_summary(response5)

        # Cenário 6: Agent precisa pesquisar notícias sobre "regulamentação de inteligência artificial" no primeiro trimestre de 2025
        response6 = agency.search_news_by_date(
            query="regulamentação de inteligência artificial",
            start_date="2025-01-01",
            end_date="2025-03-31"
        )
        print_response_summary(response6)

    except ValueError as e:
        print(f"Falha na inicialização: {e}")
        print("Certifique-se de que a variável de ambiente TAVILY_API_KEY está configurada corretamente.")
    except Exception as e:
        print(f"Erro desconhecido durante os testes: {e}")
