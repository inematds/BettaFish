"""
Conjunto de ferramentas de busca multimodal projetado especialmente para AI Agent

Versão: 1.2
Última atualização: 2026-03-20

Este script fornece múltiplas implementações de busca (Tavily, Bocha, Anspire) com uma interface
unificada para chamadas de AI Agent.
O Agent precisa apenas selecionar a ferramenta adequada com base na intenção da tarefa
(como busca geral, consulta de dados estruturados ou notícias com prazo de validade),
sem precisar entender combinações complexas de parâmetros.

Ferramentas principais:
- comprehensive_search: Executa busca abrangente, retornando páginas web e resumos.
- web_search_only: Executa busca exclusivamente na web, sem solicitar resumo de IA, mais rápida.
- search_last_24_hours: Obtém informações mais recentes das últimas 24 horas.
- search_last_week: Obtém as principais matérias da última semana.
"""

import os
import json
import sys
import datetime
from typing import List, Dict, Any, Optional, Literal

from loguru import logger
from config import settings

# Certifique-se de que a biblioteca requests esteja instalada antes de executar: pip install requests
try:
    import requests
except ImportError:
    raise ImportError("A biblioteca requests não está instalada. Execute `pip install requests` para instalá-la.")

# Adicionar diretório utils ao caminho do Python
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(os.path.dirname(current_dir))
utils_dir = os.path.join(root_dir, 'utils')
if utils_dir not in sys.path:
    sys.path.append(utils_dir)

from retry_helper import with_graceful_retry, SEARCH_API_RETRY_CONFIG

# Tavily client (opcional)
try:
    from tavily import TavilyClient
except ImportError:
    TavilyClient = None

# --- 1. Definição de estruturas de dados ---
from dataclasses import dataclass, field

@dataclass
class WebpageResult:
    """Resultado de busca de página web"""
    name: str
    url: str
    snippet: str
    display_url: Optional[str] = None
    date_last_crawled: Optional[str] = None

@dataclass
class ImageResult:
    """Resultado de busca de imagem"""
    name: str
    content_url: str
    host_page_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None

@dataclass
class ModalCardResult:
    """
    Resultado de dados estruturados de card modal
    Esta é a característica principal da busca Bocha, usada para retornar informações estruturadas de tipos específicos.
    """
    card_type: str  # Exemplo: weather_china, stock, baike_pro, medical_common
    content: Dict[str, Any]  # Conteúdo JSON analisado

@dataclass
class BochaResponse:
    """Encapsula o resultado completo da API Bocha, para transmissão entre ferramentas"""
    query: str
    conversation_id: Optional[str] = None
    answer: Optional[str] = None  # Resposta resumida gerada por IA
    follow_ups: List[str] = field(default_factory=list) # Perguntas de acompanhamento geradas por IA
    webpages: List[WebpageResult] = field(default_factory=list)
    images: List[ImageResult] = field(default_factory=list)
    modal_cards: List[ModalCardResult] = field(default_factory=list)

@dataclass
class AnspireResponse:
    """Encapsula o resultado completo da API Anspire, para transmissão entre ferramentas"""
    query: str
    conversation_id: Optional[str] = None
    score: Optional[float] = None
    webpages: List[WebpageResult] = field(default_factory=list)


# --- 2. Cliente principal e conjunto de ferramentas especializadas ---

class BochaMultimodalSearch:
    """
    Um cliente contendo múltiplas ferramentas de busca multimodal especializadas.
    Cada método público é projetado como uma ferramenta independente para chamada pelo AI Agent.
    """

    BOCHA_BASE_URL = settings.BOCHA_BASE_URL or "https://api.bocha.cn/v1/ai-search"

    def __init__(self, api_key: Optional[str] = None):
        """
        Inicializar cliente.
        Args:
            api_key: Chave da API Bocha; se não fornecida, será lida da variável de ambiente BOCHA_API_KEY.
        """
        if api_key is None:
            api_key = settings.BOCHA_WEB_SEARCH_API_KEY
            if not api_key:
                raise ValueError("Chave da API Bocha não encontrada! Configure a variável de ambiente BOCHA_API_KEY ou forneça na inicialização")

        self._headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'Accept': '*/*'
        }

    def _parse_search_response(self, response_dict: Dict[str, Any], query: str) -> BochaResponse:
        """Analisa o objeto BochaResponse estruturado a partir da resposta bruta em dicionário da API"""

        final_response = BochaResponse(query=query)
        final_response.conversation_id = response_dict.get('conversation_id')

        messages = response_dict.get('messages', [])
        for msg in messages:
            role = msg.get('role')
            if role != 'assistant':
                continue

            msg_type = msg.get('type')
            content_type = msg.get('content_type')
            content_str = msg.get('content', '{}')

            try:
                content_data = json.loads(content_str)
            except json.JSONDecodeError:
                # Se o conteúdo não for uma string JSON válida (por exemplo, texto puro do answer), usar diretamente
                content_data = content_str

            if msg_type == 'answer' and content_type == 'text':
                final_response.answer = content_data

            elif msg_type == 'follow_up' and content_type == 'text':
                final_response.follow_ups.append(content_data)

            elif msg_type == 'source':
                if content_type == 'webpage':
                    web_results = content_data.get('value', [])
                    for item in web_results:
                        final_response.webpages.append(WebpageResult(
                            name=item.get('name'),
                            url=item.get('url'),
                            snippet=item.get('snippet'),
                            display_url=item.get('displayUrl'),
                            date_last_crawled=item.get('dateLastCrawled')
                        ))
                elif content_type == 'image':
                    final_response.images.append(ImageResult(
                        name=content_data.get('name'),
                        content_url=content_data.get('contentUrl'),
                        host_page_url=content_data.get('hostPageUrl'),
                        thumbnail_url=content_data.get('thumbnailUrl'),
                        width=content_data.get('width'),
                        height=content_data.get('height')
                    ))
                # Todos os outros content_type são tratados como cards modais
                else:
                    final_response.modal_cards.append(ModalCardResult(
                        card_type=content_type,
                        content=content_data
                    ))

        return final_response


    @with_graceful_retry(SEARCH_API_RETRY_CONFIG, default_return=BochaResponse(query="falha na busca"))
    def _search_internal(self, **kwargs) -> BochaResponse:
        """Executor de busca genérico interno, todos as ferramentas chamam este método no final"""
        query = kwargs.get("query", "Unknown Query")
        payload = {
            "stream": False,  # Ferramentas de Agent geralmente usam modo não-streaming para obter resultados completos
        }
        payload.update(kwargs)

        try:

            response = requests.post(self.BOCHA_BASE_URL, headers=self._headers, json=payload, timeout=30)
            response.raise_for_status()  # Se o código HTTP for 4xx ou 5xx, lança exceção

            response_dict = response.json()
            if response_dict.get("code") != 200:
                logger.error(f"Erro retornado pela API: {response_dict.get('msg', 'erro desconhecido')}")
                return BochaResponse(query=query)

            return self._parse_search_response(response_dict, query)

        except requests.exceptions.RequestException as e:
            logger.exception(f"Erro de rede durante a busca: {str(e)}")
            raise e  # Deixar o mecanismo de retry capturar e tratar
        except Exception as e:
            logger.exception(f"Erro desconhecido ao processar resposta: {str(e)}")
            raise e  # Deixar o mecanismo de retry capturar e tratar

    # --- Métodos de ferramentas disponíveis para o Agent ---

    def comprehensive_search(self, query: str, max_results: int = 10) -> BochaResponse:
        """
        [Ferramenta] Busca abrangente: Executa uma busca padrão e completa incluindo todos os tipos de informação.
        Retorna páginas web, imagens, resumo de IA, sugestões de perguntas e possíveis cards modais. Esta é a ferramenta de busca genérica mais utilizada.
        O Agent pode fornecer a consulta de busca (query) e opcionalmente o número máximo de resultados (max_results).
        """
        logger.info(f"--- FERRAMENTA: Busca abrangente (query: {query}) ---")
        return self._search_internal(
            query=query,
            count=max_results,
            answer=True  # Ativar resumo de IA
        )

    def web_search_only(self, query: str, max_results: int = 15) -> BochaResponse:
        """
        [Ferramenta] Busca exclusivamente na web: Obtém apenas links e resumos de páginas web, sem solicitar resposta gerada por IA.
        Adequada para cenários que precisam obter rapidamente informações brutas de páginas web, sem análise adicional de IA. Mais rápida e com menor custo.
        """
        logger.info(f"--- FERRAMENTA: Busca exclusivamente na web (query: {query}) ---")
        return self._search_internal(
            query=query,
            count=max_results,
            answer=False # Desativar resumo de IA
        )

    def search_for_structured_data(self, query: str) -> BochaResponse:
        """
        [Ferramenta] Consulta de dados estruturados: Especializada em consultas que podem acionar "cards modais".
        Quando a intenção do Agent é consultar informações estruturadas como clima, ações, câmbio, definições enciclopédicas, passagens de trem, parâmetros de carros, esta ferramenta deve ser priorizada.
        Ela retorna todas as informações, mas o Agent deve focar na parte `modal_cards` dos resultados.
        """
        logger.info(f"--- FERRAMENTA: Consulta de dados estruturados (query: {query}) ---")
        # Na implementação, é igual ao comprehensive_search, mas guia a intenção do Agent através do nome e documentação
        return self._search_internal(
            query=query,
            count=5, # Consultas estruturadas geralmente não precisam de muitos resultados de páginas web
            answer=True
        )

    def search_last_24_hours(self, query: str) -> BochaResponse:
        """
        [Ferramenta] Busca de informações das últimas 24 horas: Obtém as últimas novidades sobre determinado tema.
        Esta ferramenta busca especificamente conteúdo publicado nas últimas 24 horas. Adequada para acompanhar eventos emergenciais ou últimos desenvolvimentos.
        """
        logger.info(f"--- FERRAMENTA: Busca de informações das últimas 24 horas (query: {query}) ---")
        return self._search_internal(query=query, freshness='oneDay', answer=True)

    def search_last_week(self, query: str) -> BochaResponse:
        """
        [Ferramenta] Busca de informações da semana: Obtém as principais matérias sobre determinado tema da última semana.
        Adequada para realizar resumos semanais de opinião pública ou retrospectivas.
        """
        logger.info(f"--- FERRAMENTA: Busca de informações da semana (query: {query}) ---")
        return self._search_internal(query=query, freshness='oneWeek', answer=True)

class AnspireAISearch:
    """
    Cliente Anspire AI Search
    """
    ANSPIRE_BASE_URL = settings.ANSPIRE_BASE_URL or "https://plugin.anspire.cn/api/ntsearch/search"

    def __init__(self, api_key: Optional[str] = None):
        """
        Inicializar cliente.
        Args:
            api_key: Chave da API Anspire; se não fornecida, será lida da variável de ambiente ANSPIRE_API_KEY.
        """
        if api_key is None:
            api_key = settings.ANSPIRE_API_KEY
            if not api_key:
                raise ValueError("Chave da API Anspire não encontrada! Configure a variável de ambiente ANSPIRE_API_KEY ou forneça na inicialização")

        self._headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'Connection': 'keep-alive',
            'Accept': '*/*'
        }

    def _parse_search_response(self, response_dict: Dict[str, Any], query: str) -> AnspireResponse:
        final_response = AnspireResponse(query=query)
        final_response.conversation_id = response_dict.get('Uuid')

        messages = response_dict.get("results", [])
        for msg in messages:
            final_response.score = msg.get("score")
            final_response.webpages.append(WebpageResult(
                name = msg.get("title", ""),
                url = msg.get("url", ""),
                snippet = msg.get("content", ""),
                date_last_crawled = msg.get("date", None)
            ))

        return final_response

    @with_graceful_retry(SEARCH_API_RETRY_CONFIG, default_return=AnspireResponse(query="falha na busca"))
    def _search_internal(self, **kwargs) -> AnspireResponse:
        """Executor de busca genérico interno, todas as ferramentas chamam este método no final"""
        query = kwargs.get("query", "Unknown Query")
        payload = {
            "query": query,
            "top_k": kwargs.get("top_k", 10),
            "Insite": kwargs.get("Insite", ""),
            "FromTime": kwargs.get("FromTime", ""),
            "ToTime": kwargs.get("ToTime", "")
        }

        try:
            response = requests.get(self.ANSPIRE_BASE_URL, headers=self._headers, params=payload, timeout=30)
            response.raise_for_status()  # Se o código HTTP for 4xx ou 5xx, lança exceção

            response_dict = response.json()
            return self._parse_search_response(response_dict, query)
        except requests.exceptions.RequestException as e:
            logger.exception(f"Erro de rede durante a busca: {str(e)}")
            raise e  # Deixar o mecanismo de retry capturar e tratar
        except Exception as e:
            logger.exception(f"Erro desconhecido ao processar resposta: {str(e)}")
            raise e  # Deixar o mecanismo de retry capturar e tratar

    def comprehensive_search(self, query: str, max_results: int = 10) -> AnspireResponse:
        """
        [Ferramenta] Busca abrangente: Obtém informações completas sobre determinado tema, incluindo páginas web.
        Adequada para cenários que necessitam de múltiplas fontes de informação.
        """
        logger.info(f"--- FERRAMENTA: Busca abrangente (query: {query}) ---")
        return self._search_internal(
            query=query,
            top_k=max_results
        )

    def search_last_24_hours(self, query: str, max_results: int = 10) -> AnspireResponse:
        """
        [Ferramenta] Busca de informações das últimas 24 horas: Obtém as últimas novidades sobre determinado tema.
        Esta ferramenta busca especificamente conteúdo publicado nas últimas 24 horas. Adequada para acompanhar eventos emergenciais ou últimos desenvolvimentos.
        """
        logger.info(f"--- FERRAMENTA: Busca de informações das últimas 24 horas (query: {query}) ---")
        to_time = datetime.datetime.now()
        from_time = to_time - datetime.timedelta(days=1)
        return self._search_internal(query=query,
                                     top_k=max_results,
                                     FromTime=from_time.strftime("%Y-%m-%d %H:%M:%S"),
                                     ToTime=to_time.strftime("%Y-%m-%d %H:%M:%S"))

    def search_last_week(self, query: str, max_results: int = 10) -> AnspireResponse:
        """
        [Ferramenta] Busca de informações da semana: Obtém as principais matérias sobre determinado tema da última semana.
        Adequada para realizar resumos semanais de opinião pública ou retrospectivas.
        """
        logger.info(f"--- FERRAMENTA: Busca de informações da semana (query: {query}) ---")
        to_time = datetime.datetime.now()
        from_time = to_time - datetime.timedelta(weeks=1)
        return self._search_internal(query=query,
                                     top_k=max_results,
                                     FromTime=from_time.strftime("%Y-%m-%d %H:%M:%S"),
                                     ToTime=to_time.strftime("%Y-%m-%d %H:%M:%S"))


class TavilyMultimodalSearch:
    """
    Cliente de busca usando a API Tavily.
    Implementa a mesma interface que BochaMultimodalSearch, retornando BochaResponse
    para manter compatibilidade com o restante do sistema.
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Inicializar cliente Tavily.
        Args:
            api_key: Chave da API Tavily; se não fornecida, será lida de settings.TAVILY_API_KEY.
        """
        if TavilyClient is None:
            raise ImportError("A biblioteca tavily-python não está instalada. Execute `pip install tavily-python` para instalá-la.")

        if api_key is None:
            api_key = settings.TAVILY_API_KEY
            if not api_key:
                raise ValueError("Chave da API Tavily não encontrada! Configure a variável de ambiente TAVILY_API_KEY ou forneça na inicialização")

        self._client = TavilyClient(api_key=api_key)

    def _parse_tavily_response(self, response: Dict[str, Any], query: str) -> BochaResponse:
        """Converte a resposta da API Tavily em um BochaResponse para compatibilidade."""
        final_response = BochaResponse(query=query)
        final_response.answer = response.get("answer")

        follow_up = response.get("follow_up_questions")
        if follow_up:
            final_response.follow_ups = follow_up if isinstance(follow_up, list) else [follow_up]

        for result in response.get("results", []):
            final_response.webpages.append(WebpageResult(
                name=result.get("title", ""),
                url=result.get("url", ""),
                snippet=result.get("content", ""),
                display_url=result.get("url"),
                date_last_crawled=result.get("published_date")
            ))

        for img in response.get("images", []):
            if isinstance(img, dict):
                final_response.images.append(ImageResult(
                    name=img.get("description", ""),
                    content_url=img.get("url", ""),
                ))
            elif isinstance(img, str):
                final_response.images.append(ImageResult(
                    name="",
                    content_url=img,
                ))

        return final_response

    @with_graceful_retry(SEARCH_API_RETRY_CONFIG, default_return=BochaResponse(query="falha na busca"))
    def _search_internal(self, query: str, max_results: int = 10,
                         include_answer: bool = True,
                         include_images: bool = True,
                         days: Optional[int] = None) -> BochaResponse:
        """Executor de busca genérico interno via Tavily."""
        try:
            kwargs: Dict[str, Any] = {
                "query": query,
                "max_results": max_results,
                "include_answer": include_answer,
                "include_images": include_images,
            }
            if days is not None:
                kwargs["days"] = days

            response = self._client.search(**kwargs)
            return self._parse_tavily_response(response, query)

        except Exception as e:
            logger.exception(f"Erro durante busca Tavily: {str(e)}")
            raise e

    # --- Métodos de ferramentas disponíveis para o Agent ---

    def comprehensive_search(self, query: str, max_results: int = 10) -> BochaResponse:
        """
        [Ferramenta] Busca abrangente: Executa uma busca padrão e completa incluindo todos os tipos de informação.
        Retorna páginas web, imagens e resumo de IA. Esta é a ferramenta de busca genérica mais utilizada.
        """
        logger.info(f"--- FERRAMENTA (Tavily): Busca abrangente (query: {query}) ---")
        return self._search_internal(
            query=query,
            max_results=max_results,
            include_answer=True,
            include_images=True,
        )

    def web_search_only(self, query: str, max_results: int = 15) -> BochaResponse:
        """
        [Ferramenta] Busca exclusivamente na web: Obtém apenas links e resumos de páginas web, sem solicitar resposta gerada por IA.
        Adequada para cenários que precisam obter rapidamente informações brutas de páginas web. Mais rápida e com menor custo.
        """
        logger.info(f"--- FERRAMENTA (Tavily): Busca exclusivamente na web (query: {query}) ---")
        return self._search_internal(
            query=query,
            max_results=max_results,
            include_answer=False,
            include_images=False,
        )

    def search_last_24_hours(self, query: str) -> BochaResponse:
        """
        [Ferramenta] Busca de informações das últimas 24 horas: Obtém as últimas novidades sobre determinado tema.
        Esta ferramenta busca especificamente conteúdo publicado nas últimas 24 horas.
        """
        logger.info(f"--- FERRAMENTA (Tavily): Busca de informações das últimas 24 horas (query: {query}) ---")
        return self._search_internal(query=query, include_answer=True, days=1)

    def search_last_week(self, query: str) -> BochaResponse:
        """
        [Ferramenta] Busca de informações da semana: Obtém as principais matérias sobre determinado tema da última semana.
        Adequada para realizar resumos semanais ou retrospectivas.
        """
        logger.info(f"--- FERRAMENTA (Tavily): Busca de informações da semana (query: {query}) ---")
        return self._search_internal(query=query, include_answer=True, days=7)


# --- 3. Testes e exemplos de uso ---
def load_agent_from_config():
    """Selecionar e carregar Agent de busca conforme configuração.
    Prioridade: Tavily > Bocha > Anspire."""
    if settings.TAVILY_API_KEY:
        logger.info("Carregando Agent TavilyMultimodalSearch")
        return TavilyMultimodalSearch()
    elif settings.BOCHA_WEB_SEARCH_API_KEY:
        logger.info("Carregando Agent BochaMultimodalSearch")
        return BochaMultimodalSearch()
    elif settings.ANSPIRE_API_KEY:
        logger.info("Carregando Agent AnspireAISearch")
        return AnspireAISearch()
    else:
        raise ValueError("Nenhum Agent de busca válido configurado")

def print_response_summary(response):
    """Função de impressão simplificada para exibir resultados de teste"""
    if not response or not response.query:
        logger.error("Não foi possível obter resposta válida.")
        return

    logger.info(f"\nConsulta: '{response.query}' | ID da sessão: {response.conversation_id}")
    if hasattr(response, 'answer') and response.answer:
        logger.info(f"Resumo de IA: {response.answer[:150]}...")

    logger.info(f"Encontradas {len(response.webpages)} páginas web")
    if hasattr(response, 'images'):
        logger.info(f"Encontradas {len(response.images)} imagens")
    if hasattr(response, 'modal_cards'):
        logger.info(f"Encontrados {len(response.modal_cards)} cards modais")

    if hasattr(response, 'modal_cards') and response.modal_cards:
        first_card = response.modal_cards[0]
        logger.info(f"Tipo do primeiro card modal: {first_card.card_type}")

    if response.webpages:
        first_result = response.webpages[0]
        logger.info(f"Primeiro resultado de página web: {first_result.name}")

    if hasattr(response, 'follow_ups') and response.follow_ups:
        logger.info(f"Sugestões de perguntas: {response.follow_ups}")

    logger.info("-" * 60)


if __name__ == "__main__":
    # Antes de executar, certifique-se de que a variável de ambiente BOCHA_API_KEY esteja configurada

    try:
        # Inicializar cliente de busca multimodal, que contém todas as ferramentas internamente
        search_client = load_agent_from_config()

        # Cenário 1: Agent realiza uma busca abrangente com resumo de IA
        response1 = search_client.comprehensive_search(query="人工智能对未来教育的影响")
        print_response_summary(response1)

        # Cenário 2: Agent precisa consultar informações estruturadas específicas - Clima
        if isinstance(search_client, BochaMultimodalSearch):
            response2 = search_client.search_for_structured_data(query="上海明天天气怎么样")
            print_response_summary(response2)
            # Análise detalhada do primeiro card modal
            if response2.modal_cards and response2.modal_cards[0].card_type == 'weather_china':
                logger.info("Detalhes do card modal de clima:", json.dumps(response2.modal_cards[0].content, indent=2, ensure_ascii=False))


        # Cenário 3: Agent precisa consultar informações estruturadas específicas - Ações
        if isinstance(search_client, BochaMultimodalSearch):
            response3 = search_client.search_for_structured_data(query="东方财富股票")
            print_response_summary(response3)

        # Cenário 4: Agent precisa acompanhar os últimos desenvolvimentos de um evento
        response4 = search_client.search_last_24_hours(query="C929大飞机最新消息")
        print_response_summary(response4)

        # Cenário 5: Agent precisa apenas obter rapidamente informações de páginas web, sem resumo de IA
        if isinstance(search_client, BochaMultimodalSearch):
            response5 = search_client.web_search_only(query="Python dataclasses用法")
            print_response_summary(response5)

        # Cenário 6: Agent precisa revisar notícias da semana sobre determinada tecnologia
        response6 = search_client.search_last_week(query="量子计算商业化")
        print_response_summary(response6)

        '''Saída do programa de teste abaixo:
        --- FERRAMENTA: Busca abrangente (query: 人工智能对未来教育的影响) ---

查询: '人工智能对未来教育的影响' | 会话ID: bf43bfe4c7bb4f7b8a3945515d8ab69e
AI摘要: 人工智能对未来教育有着多方面的影响。

从积极影响来看：
- 在教学资源方面，人工智能有助于教育资源的均衡分配[引用:4]。例如通过人工智能云平台，可以实现优质资源的共享，这对于偏远地区来说意义重大，能让那里的学生也接触到优质的教育内 容，一定程度上缓解师资短缺的问题，因为AI驱动的智能教学助手或虚拟...
找到 10 个网页, 1 张图片, 1 个模态卡。
第一个模态卡类型: video
第一条网页结果: 人工智能如何影响教育变革
建议追问: [['人工智能将如何改变未来的教育模式？', '在未来教育中，人工智能会给教师带来哪些挑战？', '未来教育中，学生如何利用人工智能提升学习效果？']]
------------------------------------------------------------
--- FERRAMENTA: Consulta de dados estruturados (query: 上海明天天气怎么样) ---

查询: '上海明天天气怎么样' | 会话ID: e412aa1548cd43a295430e47a62adda2
AI摘要: 根据所给信息，无法确定上海明天的天气情况。

首先，所提供的信息都是关于2025年8月22日的天气状况，包括当天的气温、降水、风力、湿度以及高温预警等信息[引用:1][引用:2][引用:3][引用:5]。然而，这些信息没有涉及到明天（8月23 日）天气的预测内容。虽然提到了副热带高压一直到8月底高温都...
找到 5 个网页, 1 张图片, 2 个模态卡。
第一个模态卡类型: video
第一条网页结果: 今日冲击38!上海八月高温天数和夏季持续高温天数有望双双破纪录_天气_低压_气象站
建议追问: [['能告诉我上海明天的气温范围吗？', '上海明天会有降雨吗？', '上海明天的天气是晴天还是阴天呢？']]
------------------------------------------------------------
--- FERRAMENTA: Consulta de dados estruturados (query: 东方财富股票) ---

查询: '东方财富股票' | 会话ID: 584d62ed97834473b967127852e1eaa0
AI摘要: 仅根据提供的上下文，无法确切获取东方财富股票的相关信息。

从给出的这些数据来看，并没有直接表明与东方财富股票相关的特定数据。例如，没有东方财富股票的涨跌幅情况、成交量、市值等具体数据[引用:1][引用:3]。也没有涉及东方财富股票在研报 、评级方面的信息[引用:2]。同时，上下文里关于股票价格、成交...
找到 5 个网页, 1 张图片, 2 个模态卡。
第一个模态卡类型: video
第一条网页结果: 股票价格_分时成交_行情_走势图—东方财富网
建议追问: [['东方财富股票近期的走势如何？', '东方财富股票有哪些主要的投资亮点？', '东方财富股票的历史最高和最低股价是多少？']]
------------------------------------------------------------
--- FERRAMENTA: Busca de informações das últimas 24 horas (query: C929大飞机最新消息) ---

查询: 'C929大飞机最新消息' | 会话ID: 5904021dc29d497e938e04db18d7f2e2
AI摘要: 根据提供的上下文，没有关于C929大飞机的直接消息，无法确切给出C929大飞机的最新消息。

目前提供的上下文涵盖了众多航空领域相关事件，但多是围绕波音787、空客A380相关专家的人事变动、国产飞机"C909云端之旅"、科德数控的营收情况、俄制航空发动机供应相关以及其他非C929大飞机相关的内容。...
找到 10 个网页, 1 张图片, 1 个模态卡。
第一个模态卡类型: video
第一条网页结果: 放弃美国千万年薪,波音787顶尖专家回国,或可协助破解C929
建议追问: [['C929大飞机目前的研发进度如何？', '有没有关于C929大飞机预计首飞时间的消息？', 'C929大飞机在技术创新方面有哪些新进展？']]
------------------------------------------------------------
--- FERRAMENTA: Busca exclusivamente na web (query: Python dataclasses用法) ---

查询: 'Python dataclasses用法' | 会话ID: 74c742759d2e4b17b52d8b735ce24537
找到 15 个网页, 1 张图片, 1 个模态卡。
第一个模态卡类型: video
第一条网页结果: 不可不知的dataclasses  python小知识_python dataclasses-CSDN博客
------------------------------------------------------------
--- FERRAMENTA: Busca de informações da semana (query: 量子计算商业化) ---

AI摘要: 量子计算商业化正在逐步推进。

量子计算商业化有着多方面的体现和推动因素。从国际上看，美国能源部橡树岭国家实验室选择IQM Radiance作为其首台本地部署的量子计算机，计划于2025年第三季度交付并集成至高性能计算系统中[引用:4]；英国量子计算公司Oxford Ionics的全栈离子阱量子计算...
找到 10 个网页, 1 张图片, 1 个模态卡。
第一个模态卡类型: video
第一条网页结果: 量子计算商业潜力释放正酣,微美全息(WIMI.US)创新科技卡位"生态高地"
建议追问: [['量子计算商业化目前有哪些成功的案例？', '哪些公司在推动量子计算商业化进程？', '量子计算商业化面临的主要挑战是什么？']]
------------------------------------------------------------'''

    except ValueError as e:
        logger.exception(f"Falha na inicialização: {e}")
        logger.error("Certifique-se de que a variável de ambiente BOCHA_API_KEY esteja configurada corretamente.")
    except Exception as e:
        logger.exception(f"Erro desconhecido durante os testes: {e}")
