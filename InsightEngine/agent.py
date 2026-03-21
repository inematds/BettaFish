"""
Classe principal do Deep Search Agent
Integra todos os módulos, implementando o fluxo completo de pesquisa profunda
"""

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

import numpy as np
from loguru import logger
from sentence_transformers import SentenceTransformer
from sklearn.cluster import KMeans

from .llms import LLMClient
from .nodes import (
    FirstSearchNode,
    FirstSummaryNode,
    ReflectionNode,
    ReflectionSummaryNode,
    ReportFormattingNode,
    ReportStructureNode,
)
from .state import State
from .tools import (
    DBResponse,
    MediaCrawlerDB,
    keyword_optimizer,
    multilingual_sentiment_analyzer,
)
from .utils import format_search_results_for_prompt
from .utils.config import Settings, settings

ENABLE_CLUSTERING: bool = True  # Habilitar amostragem por clusterização
MAX_CLUSTERED_RESULTS: int = 50  # Número máximo de resultados após clusterização
RESULTS_PER_CLUSTER: int = 5  # Número de resultados por cluster

class DeepSearchAgent:
    """Classe principal do Deep Search Agent"""

    def __init__(self, config: Optional[Settings] = None):
        """
        Inicializar o Deep Search Agent

        Args:
            config: Objeto de configuração opcional (usa settings global se não fornecido)
        """
        self.config = config or settings

        # Inicializar cliente LLM
        self.llm_client = self._initialize_llm()

        # Inicializar conjunto de ferramentas de busca
        self.search_agency = MediaCrawlerDB()

        # Inicializar modelo de clusterização (carregamento sob demanda)
        self._clustering_model = None

        # Inicializar analisador de sentimentos
        self.sentiment_analyzer = multilingual_sentiment_analyzer

        # Inicializar nós
        self._initialize_nodes()

        # Estado
        self.state = State()

        # Garantir que o diretório de saída exista
        os.makedirs(self.config.OUTPUT_DIR, exist_ok=True)

        logger.info(f"Insight Agent inicializado")
        logger.info(f"LLM utilizado: {self.llm_client.get_model_info()}")
        logger.info(f"Ferramentas de busca: MediaCrawlerDB (suporta 5 ferramentas de consulta em banco de dados local)")
        logger.info(f"Análise de sentimentos: WeiboMultilingualSentiment (suporta análise de sentimentos em 22 idiomas, incluindo português)")

    def _initialize_llm(self) -> LLMClient:
        """Inicializar cliente LLM"""
        return LLMClient(
            api_key=self.config.INSIGHT_ENGINE_API_KEY,
            model_name=self.config.INSIGHT_ENGINE_MODEL_NAME,
            base_url=self.config.INSIGHT_ENGINE_BASE_URL,
        )

    def _initialize_nodes(self):
        """Inicializar nós de processamento"""
        self.first_search_node = FirstSearchNode(self.llm_client)
        self.reflection_node = ReflectionNode(self.llm_client)
        self.first_summary_node = FirstSummaryNode(self.llm_client)
        self.reflection_summary_node = ReflectionSummaryNode(self.llm_client)
        self.report_formatting_node = ReportFormattingNode(self.llm_client)

    def _get_clustering_model(self):
        """Carregamento sob demanda do modelo de clusterização"""
        if self._clustering_model is None:
            logger.info("  Carregando modelo de clusterização (paraphrase-multilingual-MiniLM-L12-v2)...")
            self._clustering_model = SentenceTransformer(
                "paraphrase-multilingual-MiniLM-L12-v2"
            )
        return self._clustering_model

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
        pattern = r"^\d{4}-\d{2}-\d{2}$"
        if not re.match(pattern, date_str):
            return False

        # Verificar se a data é válida
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    def _cluster_and_sample_results(
        self,
        results: List,
        max_results: int = MAX_CLUSTERED_RESULTS,
        results_per_cluster: int = RESULTS_PER_CLUSTER,
    ) -> List:
        """
        Clusterizar e amostrar resultados de busca

        Args:
            results: Lista de resultados de busca
            max_results: Número máximo de resultados retornados
            results_per_cluster: Número de resultados por cluster

        Returns:
            Lista de resultados após amostragem
        """
        if len(results) <= max_results:
            return results

        try:
            # Extrair textos
            texts = [r.title_or_content[:500] for r in results]

            # Obter modelo e codificar
            model = self._get_clustering_model()
            embeddings = model.encode(texts, show_progress_bar=False)

            # Calcular número de clusters
            n_clusters = min(max(2, max_results // results_per_cluster), len(results))

            # Clusterização KMeans
            kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
            labels = kmeans.fit_predict(embeddings)

            # Amostrar de cada cluster
            sampled_results = []
            for cluster_id in range(n_clusters):
                cluster_indices = np.flatnonzero(labels == cluster_id)
                cluster_results = [(results[i], i) for i in cluster_indices]
                cluster_results.sort(
                    key=lambda x: x[0].hotness_score or 0, reverse=True
                )

                for result, _ in cluster_results[:results_per_cluster]:
                    sampled_results.append(result)
                    if len(sampled_results) >= max_results:
                        break

                if len(sampled_results) >= max_results:
                    break

            logger.info(
                f"  Clusterização concluída: {len(results)} itens -> {n_clusters} temas -> {len(sampled_results)} resultados representativos"
            )
            return sampled_results

        except Exception as e:
            logger.warning(f"  Clusterização falhou, retornando os primeiros {max_results} itens: {str(e)}")
            return results[:max_results]

    def execute_search_tool(self, tool_name: str, query: str, **kwargs) -> DBResponse:
        """
        Executar a ferramenta de consulta ao banco de dados especificada (integra middleware de otimização de palavras-chave e análise de sentimentos)

        Args:
            tool_name: Nome da ferramenta, valores possíveis:
                - "search_hot_content": Buscar conteúdo em alta
                - "search_topic_globally": Busca global de tópicos
                - "search_topic_by_date": Buscar tópicos por data
                - "get_comments_for_topic": Obter comentários de um tópico
                - "search_topic_on_platform": Busca direcionada por plataforma
                - "analyze_sentiment": Realizar análise de sentimentos nos resultados da consulta
            query: Palavras-chave de busca/tópico
            **kwargs: Parâmetros adicionais (como start_date, end_date, platform, limit, enable_sentiment etc.)
                     enable_sentiment: Se deve realizar análise de sentimentos automaticamente nos resultados de busca (padrão True)

        Returns:
            Objeto DBResponse (pode incluir resultados de análise de sentimentos)
        """
        logger.info(f"  → Executando ferramenta de consulta ao banco de dados: {tool_name}")

        # Para busca de conteúdo em alta, não é necessária otimização de palavras-chave (pois não precisa do parâmetro query)
        if tool_name == "search_hot_content":
            time_period = kwargs.get("time_period", "week")
            limit = kwargs.get("limit", 100)
            response = self.search_agency.search_hot_content(
                time_period=time_period, limit=limit
            )

            # Verificar se é necessário realizar análise de sentimentos
            enable_sentiment = kwargs.get("enable_sentiment", True)
            if enable_sentiment and response.results and len(response.results) > 0:
                logger.info(f"  Iniciando análise de sentimentos do conteúdo em alta...")
                sentiment_analysis = self._perform_sentiment_analysis(response.results)
                if sentiment_analysis:
                    # Adicionar resultados da análise de sentimentos aos parâmetros da resposta
                    response.parameters["sentiment_analysis"] = sentiment_analysis
                    logger.info(f"  Análise de sentimentos concluída")

            return response

        # Ferramenta independente de análise de sentimentos
        if tool_name == "analyze_sentiment":
            texts = kwargs.get("texts", query)  # Pode ser passado via parâmetro texts ou usar query
            sentiment_result = self.analyze_sentiment_only(texts)

            # Construir resposta no formato DBResponse
            return DBResponse(
                tool_name="analyze_sentiment",
                parameters={
                    "texts": texts if isinstance(texts, list) else [texts],
                    **kwargs,
                },
                results=[],  # Análise de sentimentos não retorna resultados de busca
                results_count=0,
                metadata=sentiment_result,
            )

        # Para ferramentas que precisam de termos de busca, usar middleware de otimização de palavras-chave
        optimized_response = keyword_optimizer.optimize_keywords(
            original_query=query, context=f"Consultando com a ferramenta {tool_name}"
        )

        logger.info(f"  Consulta original: '{query}'")
        logger.info(f"  Palavras-chave otimizadas: {optimized_response.optimized_keywords}")

        # Usar palavras-chave otimizadas para múltiplas consultas e integrar resultados
        all_results = []
        total_count = 0

        for keyword in optimized_response.optimized_keywords:
            logger.info(f"    Consultando palavra-chave: '{keyword}'")

            try:
                if tool_name == "search_topic_globally":
                    # Usar valor padrão do arquivo de configuração, ignorando o parâmetro limit_per_table fornecido pelo agent
                    limit_per_table = (
                        self.config.DEFAULT_SEARCH_TOPIC_GLOBALLY_LIMIT_PER_TABLE
                    )
                    response = self.search_agency.search_topic_globally(
                        topic=keyword, limit_per_table=limit_per_table
                    )
                elif tool_name == "search_topic_by_date":
                    start_date = kwargs.get("start_date")
                    end_date = kwargs.get("end_date")
                    # Usar valor padrão do arquivo de configuração, ignorando o parâmetro limit_per_table fornecido pelo agent
                    limit_per_table = (
                        self.config.DEFAULT_SEARCH_TOPIC_BY_DATE_LIMIT_PER_TABLE
                    )
                    if not start_date or not end_date:
                        raise ValueError(
                            "A ferramenta search_topic_by_date requer os parâmetros start_date e end_date"
                        )
                    response = self.search_agency.search_topic_by_date(
                        topic=keyword,
                        start_date=start_date,
                        end_date=end_date,
                        limit_per_table=limit_per_table,
                    )
                elif tool_name == "get_comments_for_topic":
                    # Usar valor padrão do arquivo de configuração, distribuir por número de palavras-chave, mas garantir valor mínimo
                    limit = self.config.DEFAULT_GET_COMMENTS_FOR_TOPIC_LIMIT // len(
                        optimized_response.optimized_keywords
                    )
                    limit = max(limit, 50)
                    response = self.search_agency.get_comments_for_topic(
                        topic=keyword, limit=limit
                    )
                elif tool_name == "search_topic_on_platform":
                    platform = kwargs.get("platform")
                    start_date = kwargs.get("start_date")
                    end_date = kwargs.get("end_date")
                    # Usar valor padrão do arquivo de configuração, distribuir por número de palavras-chave, mas garantir valor mínimo
                    limit = self.config.DEFAULT_SEARCH_TOPIC_ON_PLATFORM_LIMIT // len(
                        optimized_response.optimized_keywords
                    )
                    limit = max(limit, 30)
                    if not platform:
                        raise ValueError("A ferramenta search_topic_on_platform requer o parâmetro platform")
                    response = self.search_agency.search_topic_on_platform(
                        platform=platform,
                        topic=keyword,
                        start_date=start_date,
                        end_date=end_date,
                        limit=limit,
                    )
                else:
                    logger.info(f"    Ferramenta de busca desconhecida: {tool_name}, usando busca global padrão")
                    response = self.search_agency.search_topic_globally(
                        topic=keyword,
                        limit_per_table=self.config.DEFAULT_SEARCH_TOPIC_GLOBALLY_LIMIT_PER_TABLE,
                    )

                # Coletar resultados
                if response.results:
                    logger.info(f"     Encontrados {len(response.results)} resultados")
                    all_results.extend(response.results)
                    total_count += len(response.results)
                else:
                    logger.info(f"     Nenhum resultado encontrado")

            except Exception as e:
                logger.error(f"      Erro ao consultar '{keyword}': {str(e)}")
                continue

        # Deduplicar e integrar resultados
        unique_results = self._deduplicate_results(all_results)
        logger.info(f"  Total encontrado: {total_count} resultados, após deduplicação: {len(unique_results)}")

        if ENABLE_CLUSTERING:
            unique_results = self._cluster_and_sample_results(
                unique_results,
                max_results=MAX_CLUSTERED_RESULTS,
                results_per_cluster=RESULTS_PER_CLUSTER,
            )

        # Construir resposta integrada
        integrated_response = DBResponse(
            tool_name=f"{tool_name}_optimized",
            parameters={
                "original_query": query,
                "optimized_keywords": optimized_response.optimized_keywords,
                "optimization_reasoning": optimized_response.reasoning,
                **kwargs,
            },
            results=unique_results,
            results_count=len(unique_results),
        )

        # Verificar se é necessário realizar análise de sentimentos
        enable_sentiment = kwargs.get("enable_sentiment", True)
        if enable_sentiment and unique_results and len(unique_results) > 0:
            logger.info(f"  Iniciando análise de sentimentos dos resultados de busca...")
            sentiment_analysis = self._perform_sentiment_analysis(unique_results)
            if sentiment_analysis:
                # Adicionar resultados da análise de sentimentos aos parâmetros da resposta
                integrated_response.parameters["sentiment_analysis"] = (
                    sentiment_analysis
                )
                logger.info(f"  Análise de sentimentos concluída")

        return integrated_response

    def _deduplicate_results(self, results: List) -> List:
        """
        Deduplicar resultados de busca
        """
        seen = set()
        unique_results = []

        for result in results:
            # Usar URL ou conteúdo como identificador de deduplicação
            identifier = result.url if result.url else result.title_or_content[:100]
            if identifier not in seen:
                seen.add(identifier)
                unique_results.append(result)

        return unique_results

    def _perform_sentiment_analysis(self, results: List) -> Optional[Dict[str, Any]]:
        """
        Executar análise de sentimentos nos resultados de busca

        Args:
            results: Lista de resultados de busca

        Returns:
            Dicionário com resultados da análise de sentimentos, ou None em caso de falha
        """
        try:
            # Inicializar analisador de sentimentos (se ainda não inicializado e não desabilitado)
            if (
                not self.sentiment_analyzer.is_initialized
                and not self.sentiment_analyzer.is_disabled
            ):
                logger.info("    Inicializando modelo de análise de sentimentos...")
                if not self.sentiment_analyzer.initialize():
                    logger.info("     Falha na inicialização do modelo de análise de sentimentos, texto original será repassado diretamente")
            elif self.sentiment_analyzer.is_disabled:
                logger.info("     Análise de sentimentos desabilitada, texto original será repassado diretamente")

            # Converter resultados da consulta para formato de dicionário
            results_dict = []
            for result in results:
                result_dict = {
                    "content": result.title_or_content,
                    "platform": result.platform,
                    "author": result.author_nickname,
                    "url": result.url,
                    "publish_time": str(result.publish_time)
                    if result.publish_time
                    else None,
                }
                results_dict.append(result_dict)

            # Executar análise de sentimentos
            sentiment_analysis = self.sentiment_analyzer.analyze_query_results(
                query_results=results_dict, text_field="content", min_confidence=0.5
            )

            return sentiment_analysis.get("sentiment_analysis")

        except Exception as e:
            logger.exception(f"    Erro durante a análise de sentimentos: {str(e)}")
            return None

    def analyze_sentiment_only(self, texts: Union[str, List[str]]) -> Dict[str, Any]:
        """
        Ferramenta independente de análise de sentimentos

        Args:
            texts: Texto único ou lista de textos

        Returns:
            Resultado da análise de sentimentos
        """
        logger.info(f"  → Executando análise de sentimentos independente")

        try:
            # Inicializar analisador de sentimentos (se ainda não inicializado e não desabilitado)
            if (
                not self.sentiment_analyzer.is_initialized
                and not self.sentiment_analyzer.is_disabled
            ):
                logger.info("    Inicializando modelo de análise de sentimentos...")
                if not self.sentiment_analyzer.initialize():
                    logger.info("     Falha na inicialização do modelo de análise de sentimentos, texto original será repassado diretamente")
            elif self.sentiment_analyzer.is_disabled:
                logger.warning("     Análise de sentimentos desabilitada, texto original será repassado diretamente")

            # Executar análise
            if isinstance(texts, str):
                result = self.sentiment_analyzer.analyze_single_text(texts)
                result_dict = result.__dict__
                response = {
                    "success": result.success and result.analysis_performed,
                    "total_analyzed": 1
                    if result.analysis_performed and result.success
                    else 0,
                    "results": [result_dict],
                }
                if not result.analysis_performed:
                    response["success"] = False
                    response["warning"] = (
                        result.error_message or "Análise de sentimentos indisponível, texto original retornado diretamente"
                    )
                return response
            else:
                texts_list = list(texts)
                batch_result = self.sentiment_analyzer.analyze_batch(
                    texts_list, show_progress=True
                )
                response = {
                    "success": batch_result.analysis_performed
                    and batch_result.success_count > 0,
                    "total_analyzed": batch_result.total_processed
                    if batch_result.analysis_performed
                    else 0,
                    "success_count": batch_result.success_count,
                    "failed_count": batch_result.failed_count,
                    "average_confidence": batch_result.average_confidence
                    if batch_result.analysis_performed
                    else 0.0,
                    "results": [result.__dict__ for result in batch_result.results],
                }
                if not batch_result.analysis_performed:
                    warning = next(
                        (
                            r.error_message
                            for r in batch_result.results
                            if r.error_message
                        ),
                        "Análise de sentimentos indisponível, texto original retornado diretamente",
                    )
                    response["success"] = False
                    response["warning"] = warning
                return response

        except Exception as e:
            logger.exception(f"    Erro durante a análise de sentimentos: {str(e)}")
            return {"success": False, "error": str(e), "results": []}

    def research(self, query: str, save_report: bool = True) -> str:
        """
        Executar pesquisa profunda

        Args:
            query: Consulta de pesquisa
            save_report: Se deve salvar o relatório em arquivo

        Returns:
            Conteúdo do relatório final
        """
        logger.info(f"\n{'=' * 60}")
        logger.info(f"Iniciando pesquisa profunda: {query}")
        logger.info(f"{'=' * 60}")

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

            logger.info("Pesquisa profunda concluída!")

            return final_report

        except Exception as e:
            logger.exception(f"Erro durante o processo de pesquisa: {str(e)}")
            raise e

    def _generate_report_structure(self, query: str):
        """Gerar estrutura do relatório"""
        logger.info(f"\n[Etapa 1] Gerando estrutura do relatório...")

        # Criar nó de estrutura do relatório
        report_structure_node = ReportStructureNode(self.llm_client, query)

        # Gerar estrutura e atualizar estado
        self.state = report_structure_node.mutate_state(state=self.state)

        _message = f"Estrutura do relatório gerada, total de {len(self.state.paragraphs)} parágrafos:"
        for i, paragraph in enumerate(self.state.paragraphs, 1):
            _message += f"\n  {i}. {paragraph.title}"
        logger.info(_message)

    def _process_paragraphs(self):
        """Processar todos os parágrafos"""
        total_paragraphs = len(self.state.paragraphs)

        for i in range(total_paragraphs):
            logger.info(
                f"\n[Etapa 2.{i + 1}] Processando parágrafo: {self.state.paragraphs[i].title}"
            )
            logger.info("-" * 50)

            # Busca e resumo iniciais
            self._initial_search_and_summary(i)

            # Loop de reflexão
            self._reflection_loop(i)

            # Marcar parágrafo como concluído
            self.state.paragraphs[i].research.mark_completed()

            progress = (i + 1) / total_paragraphs * 100
            logger.info(f"Processamento do parágrafo concluído ({progress:.1f}%)")

    def _initial_search_and_summary(self, paragraph_index: int):
        """Executar busca e resumo iniciais"""
        paragraph = self.state.paragraphs[paragraph_index]

        # Preparar entrada de busca
        search_input = {"title": paragraph.title, "content": paragraph.content}

        # Gerar consulta de busca e seleção de ferramenta
        logger.info("  - Gerando consulta de busca...")
        search_output = self.first_search_node.run(search_input)
        search_query = search_output["search_query"]
        search_tool = search_output.get(
            "search_tool", "search_topic_globally"
        )  # Ferramenta padrão
        reasoning = search_output["reasoning"]

        logger.info(f"  - Consulta de busca: {search_query}")
        logger.info(f"  - Ferramenta selecionada: {search_tool}")
        logger.info(f"  - Raciocínio: {reasoning}")

        # Executar busca
        logger.info("  - Executando consulta ao banco de dados...")

        # Processar parâmetros especiais
        search_kwargs = {}

        # Processar ferramentas que requerem data
        if search_tool in ["search_topic_by_date", "search_topic_on_platform"]:
            start_date = search_output.get("start_date")
            end_date = search_output.get("end_date")

            if start_date and end_date:
                # Validar formato da data
                if self._validate_date_format(
                    start_date
                ) and self._validate_date_format(end_date):
                    search_kwargs["start_date"] = start_date
                    search_kwargs["end_date"] = end_date
                    logger.info(f"  - Intervalo de tempo: {start_date} até {end_date}")
                else:
                    logger.info(f"    Formato de data incorreto (deve ser YYYY-MM-DD), usando busca global")
                    logger.info(
                        f"      Datas fornecidas: start_date={start_date}, end_date={end_date}"
                    )
                    search_tool = "search_topic_globally"
            elif search_tool == "search_topic_by_date":
                logger.info(f"    Ferramenta search_topic_by_date sem parâmetros de data, usando busca global")
                search_tool = "search_topic_globally"

        # Processar ferramentas que requerem parâmetro de plataforma
        if search_tool == "search_topic_on_platform":
            platform = search_output.get("platform")
            if platform:
                search_kwargs["platform"] = platform
                logger.info(f"  - Plataforma especificada: {platform}")
            else:
                logger.warning(
                    f"    Ferramenta search_topic_on_platform sem parâmetro de plataforma, usando busca global"
                )
                search_tool = "search_topic_globally"

        # Processar parâmetros de limite, usando valores padrão do arquivo de configuração em vez dos parâmetros fornecidos pelo agent
        if search_tool == "search_hot_content":
            time_period = search_output.get("time_period", "week")
            limit = self.config.DEFAULT_SEARCH_HOT_CONTENT_LIMIT
            search_kwargs["time_period"] = time_period
            search_kwargs["limit"] = limit
        elif search_tool in ["search_topic_globally", "search_topic_by_date"]:
            if search_tool == "search_topic_globally":
                limit_per_table = (
                    self.config.DEFAULT_SEARCH_TOPIC_GLOBALLY_LIMIT_PER_TABLE
                )
            else:  # search_topic_by_date
                limit_per_table = (
                    self.config.DEFAULT_SEARCH_TOPIC_BY_DATE_LIMIT_PER_TABLE
                )
            search_kwargs["limit_per_table"] = limit_per_table
        elif search_tool in ["get_comments_for_topic", "search_topic_on_platform"]:
            if search_tool == "get_comments_for_topic":
                limit = self.config.DEFAULT_GET_COMMENTS_FOR_TOPIC_LIMIT
            else:  # search_topic_on_platform
                limit = self.config.DEFAULT_SEARCH_TOPIC_ON_PLATFORM_LIMIT
            search_kwargs["limit"] = limit

        search_response = self.execute_search_tool(
            search_tool, search_query, **search_kwargs
        )

        # Converter para formato compatível
        search_results = []
        if search_response and search_response.results:
            # Usar configuração para controlar o número de resultados passados ao LLM, 0 significa sem limite
            if self.config.MAX_SEARCH_RESULTS_FOR_LLM > 0:
                max_results = min(
                    len(search_response.results), self.config.MAX_SEARCH_RESULTS_FOR_LLM
                )
            else:
                max_results = len(search_response.results)  # Sem limite, passar todos os resultados
            for result in search_response.results[:max_results]:
                search_results.append(
                    {
                        "title": result.title_or_content,
                        "url": result.url or "",
                        "content": result.title_or_content,
                        "score": result.hotness_score,
                        "raw_content": result.title_or_content,
                        "published_date": result.publish_time.isoformat()
                        if result.publish_time
                        else None,
                        "platform": result.platform,
                        "content_type": result.content_type,
                        "author": result.author_nickname,
                        "engagement": result.engagement,
                    }
                )

        if search_results:
            _message = f"  - Encontrados {len(search_results)} resultados de busca"
            for j, result in enumerate(search_results, 1):
                date_info = (
                    f" (publicado em: {result.get('published_date', 'N/A')})"
                    if result.get("published_date")
                    else ""
                )
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
                search_results, self.config.MAX_CONTENT_LENGTH
            ),
        }

        # Atualizar estado
        self.state = self.first_summary_node.mutate_state(
            summary_input, self.state, paragraph_index
        )

        logger.info("  - Resumo inicial concluído")

    def _reflection_loop(self, paragraph_index: int):
        """Executar loop de reflexão"""
        paragraph = self.state.paragraphs[paragraph_index]

        for reflection_i in range(self.config.MAX_REFLECTIONS):
            logger.info(f"  - Reflexão {reflection_i + 1}/{self.config.MAX_REFLECTIONS}...")

            # Preparar entrada de reflexão
            reflection_input = {
                "title": paragraph.title,
                "content": paragraph.content,
                "paragraph_latest_state": paragraph.research.latest_summary,
            }

            # Gerar consulta de busca por reflexão
            reflection_output = self.reflection_node.run(reflection_input)
            search_query = reflection_output["search_query"]
            search_tool = reflection_output.get(
                "search_tool", "search_topic_globally"
            )  # Ferramenta padrão
            reasoning = reflection_output["reasoning"]

            logger.info(f"    Consulta de reflexão: {search_query}")
            logger.info(f"    Ferramenta selecionada: {search_tool}")
            logger.info(f"    Raciocínio da reflexão: {reasoning}")

            # Executar busca de reflexão
            # Processar parâmetros especiais
            search_kwargs = {}

            # Processar ferramentas que requerem data
            if search_tool in ["search_topic_by_date", "search_topic_on_platform"]:
                start_date = reflection_output.get("start_date")
                end_date = reflection_output.get("end_date")

                if start_date and end_date:
                    # Validar formato da data
                    if self._validate_date_format(
                        start_date
                    ) and self._validate_date_format(end_date):
                        search_kwargs["start_date"] = start_date
                        search_kwargs["end_date"] = end_date
                        logger.info(f"    Intervalo de tempo: {start_date} até {end_date}")
                    else:
                        logger.info(
                            f"      Formato de data incorreto (deve ser YYYY-MM-DD), usando busca global"
                        )
                        logger.info(
                            f"        Datas fornecidas: start_date={start_date}, end_date={end_date}"
                        )
                        search_tool = "search_topic_globally"
                elif search_tool == "search_topic_by_date":
                    logger.warning(
                        f"      Ferramenta search_topic_by_date sem parâmetros de data, usando busca global"
                    )
                    search_tool = "search_topic_globally"

            # Processar ferramentas que requerem parâmetro de plataforma
            if search_tool == "search_topic_on_platform":
                platform = reflection_output.get("platform")
                if platform:
                    search_kwargs["platform"] = platform
                    logger.info(f"    Plataforma especificada: {platform}")
                else:
                    logger.warning(
                        f"      Ferramenta search_topic_on_platform sem parâmetro de plataforma, usando busca global"
                    )
                    search_tool = "search_topic_globally"

            # Processar parâmetros de limite
            if search_tool == "search_hot_content":
                time_period = reflection_output.get("time_period", "week")
                # Usar valor padrão do arquivo de configuração, não permitir que o agent controle o parâmetro limit
                limit = self.config.DEFAULT_SEARCH_HOT_CONTENT_LIMIT
                search_kwargs["time_period"] = time_period
                search_kwargs["limit"] = limit
            elif search_tool in ["search_topic_globally", "search_topic_by_date"]:
                # Usar valor padrão do arquivo de configuração, não permitir que o agent controle o parâmetro limit_per_table
                if search_tool == "search_topic_globally":
                    limit_per_table = (
                        self.config.DEFAULT_SEARCH_TOPIC_GLOBALLY_LIMIT_PER_TABLE
                    )
                else:  # search_topic_by_date
                    limit_per_table = (
                        self.config.DEFAULT_SEARCH_TOPIC_BY_DATE_LIMIT_PER_TABLE
                    )
                search_kwargs["limit_per_table"] = limit_per_table
            elif search_tool in ["get_comments_for_topic", "search_topic_on_platform"]:
                # Usar valor padrão do arquivo de configuração, não permitir que o agent controle o parâmetro limit
                if search_tool == "get_comments_for_topic":
                    limit = self.config.DEFAULT_GET_COMMENTS_FOR_TOPIC_LIMIT
                else:  # search_topic_on_platform
                    limit = self.config.DEFAULT_SEARCH_TOPIC_ON_PLATFORM_LIMIT
                search_kwargs["limit"] = limit

            search_response = self.execute_search_tool(
                search_tool, search_query, **search_kwargs
            )

            # Converter para formato compatível
            search_results = []
            if search_response and search_response.results:
                # Usar configuração para controlar o número de resultados passados ao LLM, 0 significa sem limite
                if self.config.MAX_SEARCH_RESULTS_FOR_LLM > 0:
                    max_results = min(
                        len(search_response.results),
                        self.config.MAX_SEARCH_RESULTS_FOR_LLM,
                    )
                else:
                    max_results = len(search_response.results)  # Sem limite, passar todos os resultados
                for result in search_response.results[:max_results]:
                    search_results.append(
                        {
                            "title": result.title_or_content,
                            "url": result.url or "",
                            "content": result.title_or_content,
                            "score": result.hotness_score,
                            "raw_content": result.title_or_content,
                            "published_date": result.publish_time.isoformat()
                            if result.publish_time
                            else None,
                            "platform": result.platform,
                            "content_type": result.content_type,
                            "author": result.author_nickname,
                            "engagement": result.engagement,
                        }
                    )

            if search_results:
                _message = f"    Encontrados {len(search_results)} resultados de busca por reflexão"
                for j, result in enumerate(search_results, 1):
                    date_info = (
                        f" (publicado em: {result.get('published_date', 'N/A')})"
                        if result.get("published_date")
                        else ""
                    )
                    _message += f"\n      {j}. {result['title'][:50]}...{date_info}"
                logger.info(_message)
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
                    search_results, self.config.MAX_CONTENT_LENGTH
                ),
                "paragraph_latest_state": paragraph.research.latest_summary,
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
            report_data.append(
                {
                    "title": paragraph.title,
                    "paragraph_latest_state": paragraph.research.latest_summary,
                }
            )

        # Formatar relatório
        try:
            final_report = self.report_formatting_node.run(report_data)
        except Exception as e:
            logger.exception(f"Formatação LLM falhou, usando método alternativo: {str(e)}")
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
        query_safe = "".join(
            c for c in self.state.query if c.isalnum() or c in (" ", "-", "_")
        ).rstrip()
        query_safe = query_safe.replace(" ", "_")[:30]

        filename = f"deep_search_report_{query_safe}_{timestamp}.md"
        filepath = os.path.join(self.config.OUTPUT_DIR, filename)

        # Salvar relatório
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(report_content)

        logger.info(f"Relatório salvo em: {filepath}")

        # Salvar estado (se a configuração permitir)
        if self.config.SAVE_INTERMEDIATE_STATES:
            state_filename = f"state_{query_safe}_{timestamp}.json"
            state_filepath = os.path.join(self.config.OUTPUT_DIR, state_filename)
            self.state.save_to_file(state_filepath)
            logger.info(f"Estado salvo em: {state_filepath}")

    def get_progress_summary(self) -> Dict[str, Any]:
        """Obter resumo de progresso"""
        return self.state.get_progress_summary()

    def load_state(self, filepath: str):
        """Carregar estado de arquivo"""
        self.state = State.load_from_file(filepath)
        logger.info(f"Estado carregado de {filepath}")

    def save_state(self, filepath: str):
        """Salvar estado em arquivo"""
        self.state.save_to_file(filepath)
        logger.info(f"Estado salvo em {filepath}")


def create_agent(config_file: Optional[str] = None) -> DeepSearchAgent:
    """
    Função utilitária para criar instância do Deep Search Agent

    Args:
        config_file: Caminho do arquivo de configuração

    Returns:
        Instância de DeepSearchAgent
    """
    config = Settings()  # Inicializar com configuração vazia, usando variáveis de ambiente
    return DeepSearchAgent(config)
