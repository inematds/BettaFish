"""
Classe principal do Deep Search Agent
Integra todos os módulos, implementando o fluxo completo de pesquisa profunda
"""

import json
import os
import re
from datetime import datetime
from typing import Optional, Dict, Any, List

from .llms import LLMClient
from .nodes import (
    ReportStructureNode,
    FirstSearchNode,
    ReflectionNode,
    FirstSummaryNode,
    ReflectionSummaryNode,
    ReportFormattingNode
)
from .state import State
from .tools import TavilyNewsAgency, TavilyResponse
from .utils import Settings, format_search_results_for_prompt
from loguru import logger

class DeepSearchAgent:
    """Classe principal do Deep Search Agent"""

    def __init__(self, config: Optional[Settings] = None):
        """
        Inicializa o Deep Search Agent

        Args:
            config: Objeto de configuração; se não fornecido, será carregado automaticamente
        """
        # Carregar configuração
        from .utils.config import settings
        self.config = config or settings

        # Inicializar cliente LLM
        self.llm_client = self._initialize_llm()

        # Inicializar conjunto de ferramentas de busca
        self.search_agency = TavilyNewsAgency(api_key=self.config.TAVILY_API_KEY)

        # Inicializar nós
        self._initialize_nodes()

        # Estado
        self.state = State()

        # Garantir que o diretório de saída exista
        os.makedirs(self.config.OUTPUT_DIR, exist_ok=True)

        logger.info(f"Query Agent inicializado")
        logger.info(f"LLM em uso: {self.llm_client.get_model_info()}")
        logger.info(f"Conjunto de ferramentas de busca: TavilyNewsAgency (suporta 6 ferramentas de busca)")

    def _initialize_llm(self) -> LLMClient:
        """Inicializar cliente LLM"""
        return LLMClient(
            api_key=self.config.QUERY_ENGINE_API_KEY,
            model_name=self.config.QUERY_ENGINE_MODEL_NAME,
            base_url=self.config.QUERY_ENGINE_BASE_URL,
        )

    def _initialize_nodes(self):
        """Inicializar nós de processamento"""
        self.first_search_node = FirstSearchNode(self.llm_client)
        self.reflection_node = ReflectionNode(self.llm_client)
        self.first_summary_node = FirstSummaryNode(self.llm_client)
        self.reflection_summary_node = ReflectionSummaryNode(self.llm_client)
        self.report_formatting_node = ReportFormattingNode(self.llm_client)

    def _validate_date_format(self, date_str: str) -> bool:
        """
        Validar se o formato da data é YYYY-MM-DD

        Args:
            date_str: String de data

        Returns:
            Se o formato é válido
        """
        if not date_str:
            return False

        # Verificar formato
        pattern = r'^\d{4}-\d{2}-\d{2}$'
        if not re.match(pattern, date_str):
            return False

        # Verificar se a data é válida
        try:
            datetime.strptime(date_str, '%Y-%m-%d')
            return True
        except ValueError:
            return False

    def execute_search_tool(self, tool_name: str, query: str, **kwargs) -> TavilyResponse:
        """
        Executar a ferramenta de busca especificada

        Args:
            tool_name: Nome da ferramenta, valores possíveis:
                - "basic_search_news": Busca básica de notícias (rápida, genérica)
                - "deep_search_news": Análise profunda de notícias
                - "search_news_last_24_hours": Notícias das últimas 24 horas
                - "search_news_last_week": Notícias da semana
                - "search_images_for_news": Busca de imagens de notícias
                - "search_news_by_date": Busca de notícias por intervalo de datas
            query: Consulta de busca
            **kwargs: Parâmetros adicionais (como start_date, end_date, max_results)

        Returns:
            Objeto TavilyResponse
        """
        logger.info(f"  → Executando ferramenta de busca: {tool_name}")

        if tool_name == "basic_search_news":
            max_results = kwargs.get("max_results", 7)
            return self.search_agency.basic_search_news(query, max_results)
        elif tool_name == "deep_search_news":
            return self.search_agency.deep_search_news(query)
        elif tool_name == "search_news_last_24_hours":
            return self.search_agency.search_news_last_24_hours(query)
        elif tool_name == "search_news_last_week":
            return self.search_agency.search_news_last_week(query)
        elif tool_name == "search_images_for_news":
            return self.search_agency.search_images_for_news(query)
        elif tool_name == "search_news_by_date":
            start_date = kwargs.get("start_date")
            end_date = kwargs.get("end_date")
            if not start_date or not end_date:
                raise ValueError("A ferramenta search_news_by_date requer os parâmetros start_date e end_date")
            return self.search_agency.search_news_by_date(query, start_date, end_date)
        else:
            logger.warning(f"  ⚠️  Ferramenta de busca desconhecida: {tool_name}, usando busca básica padrão")
            return self.search_agency.basic_search_news(query)

    def research(self, query: str, save_report: bool = True) -> str:
        """
        Executar pesquisa profunda

        Args:
            query: Consulta de pesquisa
            save_report: Se deve salvar o relatório em arquivo

        Returns:
            Conteúdo do relatório final
        """
        logger.info(f"\n{'='*60}")
        logger.info(f"Iniciando pesquisa profunda: {query}")
        logger.info(f"{'='*60}")

        try:
            # Etapa 1: Gerar estrutura do relatório
            self._generate_report_structure(query)

            # Etapa 2: Processar cada parágrafo
            self._process_paragraphs()

            # Etapa 3: Gerar relatório final
            final_report = self._generate_final_report()

            # Etapa 4: Salvar relatório
            if save_report:
                self._save_report(final_report)

            logger.info(f"\n{'='*60}")
            logger.info("Pesquisa profunda concluída!")
            logger.info(f"{'='*60}")

            return final_report

        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            logger.error(f"Erro durante o processo de pesquisa: {str(e)} \nRastreamento de erro: {error_traceback}")
            raise e

    def _generate_report_structure(self, query: str):
        """Gerar estrutura do relatório"""
        logger.info(f"\n[Etapa 1] Gerando estrutura do relatório...")

        # Criar nó de estrutura do relatório
        report_structure_node = ReportStructureNode(self.llm_client, query)

        # Gerar estrutura e atualizar estado
        self.state = report_structure_node.mutate_state(state=self.state)

        _message = f"Estrutura do relatório gerada, {len(self.state.paragraphs)} parágrafos no total:"
        for i, paragraph in enumerate(self.state.paragraphs, 1):
            _message += f"\n  {i}. {paragraph.title}"
        logger.info(_message)

    def _process_paragraphs(self):
        """Processar todos os parágrafos"""
        total_paragraphs = len(self.state.paragraphs)

        for i in range(total_paragraphs):
            logger.info(f"\n[Etapa 2.{i+1}] Processando parágrafo: {self.state.paragraphs[i].title}")
            logger.info("-" * 50)

            # Busca e resumo iniciais
            self._initial_search_and_summary(i)

            # Ciclo de reflexão
            self._reflection_loop(i)

            # Marcar parágrafo como concluído
            self.state.paragraphs[i].research.mark_completed()

            progress = (i + 1) / total_paragraphs * 100
            logger.info(f"Processamento do parágrafo concluído ({progress:.1f}%)")

    def _initial_search_and_summary(self, paragraph_index: int):
        """Executar busca e resumo iniciais"""
        paragraph = self.state.paragraphs[paragraph_index]

        # Preparar entrada de busca
        search_input = {
            "title": paragraph.title,
            "content": paragraph.content
        }

        # Gerar consulta de busca e seleção de ferramenta
        logger.info("  - Gerando consulta de busca...")
        search_output = self.first_search_node.run(search_input)
        search_query = search_output["search_query"]
        search_tool = search_output.get("search_tool", "basic_search_news")  # Ferramenta padrão
        reasoning = search_output["reasoning"]

        logger.info(f"  - Consulta de busca: {search_query}")
        logger.info(f"  - Ferramenta selecionada: {search_tool}")
        logger.info(f"  - Raciocínio: {reasoning}")

        # Executar busca
        logger.info("  - Executando busca na web...")

        # Tratar parâmetros especiais de search_news_by_date
        search_kwargs = {}
        if search_tool == "search_news_by_date":
            start_date = search_output.get("start_date")
            end_date = search_output.get("end_date")

            if start_date and end_date:
                # Validar formato de data
                if self._validate_date_format(start_date) and self._validate_date_format(end_date):
                    search_kwargs["start_date"] = start_date
                    search_kwargs["end_date"] = end_date
                    logger.info(f"  - Intervalo de tempo: {start_date} até {end_date}")
                else:
                    logger.info(f"  ⚠️  Formato de data incorreto (deve ser YYYY-MM-DD), usando busca básica")
                    logger.info(f"      Datas fornecidas: start_date={start_date}, end_date={end_date}")
                    search_tool = "basic_search_news"
            else:
                logger.info(f"  ⚠️  Ferramenta search_news_by_date sem parâmetros de tempo, usando busca básica")
                search_tool = "basic_search_news"

        search_response = self.execute_search_tool(search_tool, search_query, **search_kwargs)

        # Converter para formato compatível
        search_results = []
        if search_response and search_response.results:
            # Cada ferramenta de busca tem sua quantidade específica de resultados, aqui limitamos a 10
            max_results = min(len(search_response.results), 10)
            for result in search_response.results[:max_results]:
                search_results.append({
                    'title': result.title,
                    'url': result.url,
                    'content': result.content,
                    'score': result.score,
                    'raw_content': result.raw_content,
                    'published_date': result.published_date  # Campo adicionado
                })

        if search_results:
            _message = f"  - Encontrados {len(search_results)} resultados de busca"
            for j, result in enumerate(search_results, 1):
                date_info = f" (Publicado em: {result.get('published_date', 'N/A')})" if result.get('published_date') else ""
                _message += f"\n    {j}. {result['title'][:50]}...{date_info}"
            logger.info(_message)
        else:
            logger.info("  - Nenhum resultado de busca encontrado")
        # Atualizar histórico de busca no estado
        paragraph.research.add_search_results(search_query, search_results)

        # Gerar resumo inicial
        logger.info("  - Gerando resumo inicial...")
        summary_input = {
            "title": paragraph.title,
            "content": paragraph.content,
            "search_query": search_query,
            "search_results": format_search_results_for_prompt(
                search_results, self.config.SEARCH_CONTENT_MAX_LENGTH
            )
        }

        # Atualizar estado
        self.state = self.first_summary_node.mutate_state(
            summary_input, self.state, paragraph_index
        )

        logger.info("  - Resumo inicial concluído")

    def _reflection_loop(self, paragraph_index: int):
        """Executar ciclo de reflexão"""
        paragraph = self.state.paragraphs[paragraph_index]

        for reflection_i in range(self.config.MAX_REFLECTIONS):
            logger.info(f"  - Reflexão {reflection_i + 1}/{self.config.MAX_REFLECTIONS}...")

            # Preparar entrada de reflexão
            reflection_input = {
                "title": paragraph.title,
                "content": paragraph.content,
                "paragraph_latest_state": paragraph.research.latest_summary
            }

            # Gerar consulta de busca por reflexão
            reflection_output = self.reflection_node.run(reflection_input)
            search_query = reflection_output["search_query"]
            search_tool = reflection_output.get("search_tool", "basic_search_news")  # Ferramenta padrão
            reasoning = reflection_output["reasoning"]

            logger.info(f"    Consulta de reflexão: {search_query}")
            logger.info(f"    Ferramenta selecionada: {search_tool}")
            logger.info(f"    Raciocínio da reflexão: {reasoning}")

            # Executar busca de reflexão
            # Tratar parâmetros especiais de search_news_by_date
            search_kwargs = {}
            if search_tool == "search_news_by_date":
                start_date = reflection_output.get("start_date")
                end_date = reflection_output.get("end_date")

                if start_date and end_date:
                    # Validar formato de data
                    if self._validate_date_format(start_date) and self._validate_date_format(end_date):
                        search_kwargs["start_date"] = start_date
                        search_kwargs["end_date"] = end_date
                        logger.info(f"    Intervalo de tempo: {start_date} até {end_date}")
                    else:
                        logger.info(f"    ⚠️  Formato de data incorreto (deve ser YYYY-MM-DD), usando busca básica")
                        logger.info(f"        Datas fornecidas: start_date={start_date}, end_date={end_date}")
                        search_tool = "basic_search_news"
                else:
                    logger.info(f"    ⚠️  Ferramenta search_news_by_date sem parâmetros de tempo, usando busca básica")
                    search_tool = "basic_search_news"

            search_response = self.execute_search_tool(search_tool, search_query, **search_kwargs)

            # Converter para formato compatível
            search_results = []
            if search_response and search_response.results:
                # Cada ferramenta de busca tem sua quantidade específica de resultados, aqui limitamos a 10
                max_results = min(len(search_response.results), 10)
                for result in search_response.results[:max_results]:
                    search_results.append({
                        'title': result.title,
                        'url': result.url,
                        'content': result.content,
                        'score': result.score,
                        'raw_content': result.raw_content,
                        'published_date': result.published_date
                    })

            if search_results:
                logger.info(f"    Encontrados {len(search_results)} resultados de busca por reflexão")
                for j, result in enumerate(search_results, 1):
                    date_info = f" (Publicado em: {result.get('published_date', 'N/A')})" if result.get('published_date') else ""
                    logger.info(f"      {j}. {result['title'][:50]}...{date_info}")
            else:
                logger.info("    Nenhum resultado de busca por reflexão encontrado")

            # Atualizar histórico de busca
            paragraph.research.add_search_results(search_query, search_results)

            # Gerar resumo de reflexão
            reflection_summary_input = {
                "title": paragraph.title,
                "content": paragraph.content,
                "search_query": search_query,
                "search_results": format_search_results_for_prompt(
                    search_results, self.config.SEARCH_CONTENT_MAX_LENGTH
                ),
                "paragraph_latest_state": paragraph.research.latest_summary
            }

            # Atualizar estado
            self.state = self.reflection_summary_node.mutate_state(
                reflection_summary_input, self.state, paragraph_index
            )

            logger.info(f"    Reflexão {reflection_i + 1} concluída")

    def _generate_final_report(self) -> str:
        """Gerar relatório final"""
        logger.info(f"\n[Etapa 3] Gerando relatório final...")

        # Preparar dados do relatório
        report_data = []
        for paragraph in self.state.paragraphs:
            report_data.append({
                "title": paragraph.title,
                "paragraph_latest_state": paragraph.research.latest_summary
            })

        # Formatar relatório
        try:
            final_report = self.report_formatting_node.run(report_data)
        except Exception as e:
            logger.error(f"Falha na formatação pelo LLM, usando método alternativo: {str(e)}")
            final_report = self.report_formatting_node.format_report_manually(
                report_data, self.state.report_title
            )

        # Atualizar estado
        self.state.final_report = final_report
        self.state.mark_completed()

        logger.info("Geração do relatório final concluída")
        return final_report

    def _save_report(self, report_content: str):
        """Salvar relatório em arquivo"""
        # Gerar nome do arquivo
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        query_safe = "".join(c for c in self.state.query if c.isalnum() or c in (' ', '-', '_')).rstrip()
        query_safe = query_safe.replace(' ', '_')[:30]

        filename = f"deep_search_report_{query_safe}_{timestamp}.md"
        filepath = os.path.join(self.config.OUTPUT_DIR, filename)

        # Salvar relatório
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(report_content)

        logger.info(f"Relatório salvo em: {filepath}")

        # Salvar estado (se a configuração permitir)
        if self.config.SAVE_INTERMEDIATE_STATES:
            state_filename = f"state_{query_safe}_{timestamp}.json"
            state_filepath = os.path.join(self.config.OUTPUT_DIR, state_filename)
            self.state.save_to_file(state_filepath)
            logger.info(f"Estado salvo em: {state_filepath}")

    def get_progress_summary(self) -> Dict[str, Any]:
        """Obter resumo do progresso"""
        return self.state.get_progress_summary()

    def load_state(self, filepath: str):
        """Carregar estado a partir de arquivo"""
        self.state = State.load_from_file(filepath)
        logger.info(f"Estado carregado de {filepath}")

    def save_state(self, filepath: str):
        """Salvar estado em arquivo"""
        self.state.save_to_file(filepath)
        logger.info(f"Estado salvo em {filepath}")


def create_agent() -> DeepSearchAgent:
    """
    Função auxiliar para criar uma instância do Deep Search Agent

    Returns:
        Instância de DeepSearchAgent
    """
    from .utils.config import Settings
    config = Settings()
    return DeepSearchAgent(config)
