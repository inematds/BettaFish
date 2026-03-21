"""
Classe principal do Report Agent.

Este modulo encadeia selecao de template, design de layout, geracao de capitulos, montagem do IR e renderizacao HTML
e todos os subprocessos, sendo o centro de orquestracao do Report Engine. Responsabilidades principais:
1. 管理Dados de entrada与状态，协调三个分析引擎、logs do forum与模板；
2. Acionar sequencialmente selecao de template -> geracao de layout -> planejamento de extensao -> escrita de capitulos -> montagem e renderizacao;
3. 负责Erro(s)兜底、流式事件分发、落盘清单与最终成果保存。
"""

import json
import os
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable, Tuple

from loguru import logger

from .core import (
    ChapterStorage,
    DocumentComposer,
    TemplateSection,
    parse_template_sections,
)
from .ir import IRValidator
from .llms import LLMClient
from .nodes import (
    TemplateSelectionNode,
    ChapterGenerationNode,
    ChapterJsonParseError,
    ChapterContentError,
    ChapterValidationError,
    DocumentLayoutNode,
    WordBudgetNode,
)
from .renderers import HTMLRenderer
from .state import ReportState
from .utils.config import settings, Settings


class StageOutputFormatError(ValueError):
    """Excecao controlada lancada quando a estrutura de saida de uma etapa nao corresponde ao esperado."""


class FileCountBaseline:
    """
    Gerenciador de linha de base de contagem de arquivos.

    该工具用于：
    - 在任务启动时记录 Insight/Media/Query 三个引擎导出的 Markdown 数量；
    - 在后续轮询中快速判断是否有新relatorio落地；
    - 为 Flask 层提供“输入是否准备完毕”的依据。
    """
    
    def __init__(self):
        """
        初始化时优先尝试读取既有的linha de base快照。

        若 `logs/report_baseline.json` 不存在则会自动创建一份空快照，
        以便后续 `initialize_baseline` 在首次运行时写入真实linha de base。
        """
        self.baseline_file = 'logs/report_baseline.json'
        self.baseline_data = self._load_baseline()
    
    def _load_baseline(self) -> Dict[str, int]:
        """
        加载linha de base数据。

        - 当快照Arquivo存在时直接解析JSON；
        - 捕获所有加载异常并返回空字典，保证调用方逻辑简洁。
        """
        try:
            if os.path.exists(self.baseline_file):
                with open(self.baseline_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.exception(f"Falha ao carregar dados de linha de base: {e}")
        return {}
    
    def _save_baseline(self):
        """
        将当前linha de base写入磁盘。

        采用 `ensure_ascii=False` + 缩进格式，方便人工查看；
        若目标Sumario缺失则自动创建。
        """
        try:
            os.makedirs(os.path.dirname(self.baseline_file), exist_ok=True)
            with open(self.baseline_file, 'w', encoding='utf-8') as f:
                json.dump(self.baseline_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception(f"Falha ao salvar dados de linha de base: {e}")
    
    def initialize_baseline(self, directories: Dict[str, str]) -> Dict[str, int]:
        """
        初始化Numero de arquivos量linha de base。

        遍历每个引擎Sumario并Estatisticas `.md` Numero de arquivos量，将结果持久化为
        初始linha de base。后续 `check_new_files` 会据此对比增量。
        """
        current_counts = {}
        
        for engine, directory in directories.items():
            if os.path.exists(directory):
                md_files = [f for f in os.listdir(directory) if f.endswith('.md')]
                current_counts[engine] = len(md_files)
            else:
                current_counts[engine] = 0
        
        # Salvar dados de linha de base
        self.baseline_data = current_counts.copy()
        self._save_baseline()
        
        logger.info(f"Linha de base de contagem de arquivos inicializada: {current_counts}")
        return current_counts
    
    def check_new_files(self, directories: Dict[str, str]) -> Dict[str, Any]:
        """
        检查是否有新Arquivo。

        对比当前SumarioNumero de arquivos与linha de base：
        - Estatisticasnovo(s)数量，并判定是否所有引擎都已准备就绪；
        - 返回详细计数、缺失列表，供 Web 层提示给用户。
        """
        current_counts = {}
        new_files_found = {}
        all_have_new = True
        
        for engine, directory in directories.items():
            if os.path.exists(directory):
                md_files = [f for f in os.listdir(directory) if f.endswith('.md')]
                current_counts[engine] = len(md_files)
                baseline_count = self.baseline_data.get(engine, 0)
                
                if current_counts[engine] > baseline_count:
                    new_files_found[engine] = current_counts[engine] - baseline_count
                else:
                    new_files_found[engine] = 0
                    all_have_new = False
            else:
                current_counts[engine] = 0
                new_files_found[engine] = 0
                all_have_new = False
        
        return {
            'ready': all_have_new,
            'baseline_counts': self.baseline_data,
            'current_counts': current_counts,
            'new_files_found': new_files_found,
            'missing_engines': [engine for engine, count in new_files_found.items() if count == 0]
        }
    
    def get_latest_files(self, directories: Dict[str, str]) -> Dict[str, str]:
        """
        获取每个Sumario的最新Arquivo。

        通过 `os.path.getmtime` 找出最近写入的 Markdown，
        以确保生成流程永远使用最新一版三引擎relatorio。
        """
        latest_files = {}
        
        for engine, directory in directories.items():
            if os.path.exists(directory):
                md_files = [f for f in os.listdir(directory) if f.endswith('.md')]
                if md_files:
                    latest_file = max(md_files, key=lambda x: os.path.getmtime(os.path.join(directory, x)))
                    latest_files[engine] = os.path.join(directory, latest_file)
        
        return latest_files


class ReportAgent:
    """
    Classe principal do Report Agent.

    Responsavel por integrar:
    - Cliente LLM e seus quatro nos de inferencia superiores;
    - Armazenamento de capitulos, montagem IR, renderizadores e cadeia de producao;
    - Gerenciamento de estado, logs, validacao de entrada/saida e persistencia.
    """
    _CONTENT_SPARSE_MIN_ATTEMPTS = 3
    _CONTENT_SPARSE_WARNING_TEXT = "本章LLM生成的内容字数可能过低，必要时可以尝试重新运行程序。"
    _STRUCTURAL_RETRY_ATTEMPTS = 2
    
    def __init__(self, config: Optional[Settings] = None):
        """
        Inicializar Report Agent.
        
        Args:
            config: Objeto de configuracao, carregado automaticamente se nao fornecido
        
        Visao geral das etapas:
            1. 解析配置并接入日志/LLM/渲染等核心组件；
            2. 构造四个推理节点（模板、布局、篇幅、章节）；
            3. 初始化arquivo de linha de base与章节落盘Sumario；
            4. 构建可序列化的状态容器，供外部服务查询。
        """
        # Carregar configuracao
        self.config = config or settings
        
        # Inicializar gerenciador de linha de base de arquivos
        self.file_baseline = FileCountBaseline()
        
        # Inicializar logs
        self._setup_logging()
        
        # Inicializar cliente LLM
        self.llm_client = self._initialize_llm()
        self.json_rescue_clients = self._initialize_rescue_llms()
        
        # Inicializar componentes de armazenamento/validacao/renderizacao de capitulos
        self.chapter_storage = ChapterStorage(self.config.CHAPTER_OUTPUT_DIR)
        self.document_composer = DocumentComposer()
        self.validator = IRValidator()
        self.renderer = HTMLRenderer()
        
        # Inicializar nos
        self._initialize_nodes()
        
        # Inicializar linha de base de contagem de arquivos
        self._initialize_file_baseline()
        
        # Estado
        self.state = ReportState()
        
        # Garantir que o diretorio de saida exista
        os.makedirs(self.config.OUTPUT_DIR, exist_ok=True)
        os.makedirs(self.config.DOCUMENT_IR_OUTPUT_DIR, exist_ok=True)
        
        logger.info("Report Agent inicializado")
        logger.info(f"Usando LLM: {self.llm_client.get_model_info()}")
        
    def _setup_logging(self):
        """
        Configurar logs.

        - 确保日志Sumario存在；
        - 使用独立的 loguru sink 写入 Report Engine 专属 log Arquivo，
          避免与其他子系统混淆。
        - 【修复】Configurar gravacao de logs em tempo real, desabilitar buffer, garantir que frontend veja logs em tempo real
        - 【修复】Prevenir adicao duplicada de handler
        """
        # Garantir que o diretorio de logs exista
        log_dir = os.path.dirname(self.config.LOG_FILE)
        os.makedirs(log_dir, exist_ok=True)

        def _exclude_other_engines(record):
            """
            Filtrar logs gerados por outros motores (Insight/Media/Query/Forum), mantendo todos os demais logs.

            使用Caminho匹配为主，无法获取Caminho时退化到模块名。
            """
            excluded_keywords = ("InsightEngine", "MediaEngine", "QueryEngine", "ForumEngine")
            try:
                file_path = record["file"].path
                if any(keyword in file_path for keyword in excluded_keywords):
                    return False
            except Exception:
                pass

            try:
                module_name = record.get("module", "")
                if isinstance(module_name, str):
                    lowered = module_name.lower()
                    if any(keyword.lower() in lowered for keyword in excluded_keywords):
                        return False
            except Exception:
                pass

            return True

        # [CORRECAO] Verificar se ja foi adicionado este arquivo(s)handler, evitar duplicatas
        # loguru会自动去重，但显式检查更安全
        log_file_path = str(Path(self.config.LOG_FILE).resolve())

        # 检查现有的handlers
        handler_exists = False
        for handler_id, handler_config in logger._core.handlers.items():
            if hasattr(handler_config, 'sink'):
                sink = handler_config.sink
                # 检查是否是Arquivosink且Caminho相同
                if hasattr(sink, '_name') and sink._name == log_file_path:
                    handler_exists = True
                    logger.debug(f"Handler de log ja existe, ignorando adicao: {log_file_path}")
                    break

        if not handler_exists:
            # [CORRECAO] Criar logger dedicado, configurar gravacao em tempo real
            # - enqueue=False: Desabilitar fila assincrona, gravar imediatamente
            # - buffering=1: 行缓冲，每条日志Flush imediato到Arquivo
            # - level="DEBUG": Registrar logs de todos os niveis
            # - encoding="utf-8": Especificar codificacao UTF-8 explicitamente
            # - mode="a": Modo de adicao, preservar logs historicos
            handler_id = logger.add(
                self.config.LOG_FILE,
                level="DEBUG",
                enqueue=False,      # Desabilitar fila assincrona, gravacao sincrona
                buffering=1,        # Buffer de linha, gravar cada linha imediatamente
                serialize=False,    # Formato de texto simples, nao serializar como JSON
                encoding="utf-8",   # Codificacao UTF-8 explicita
                mode="a",           # Modo de adição
                filter=_exclude_other_engines # Filtrar logs dos quatro Engines, manter demais informacoes
            )
            logger.debug(f"Handler de log adicionado (ID: {handler_id}): {self.config.LOG_FILE}")

        # [CORRECAO] 验证日志arquivo gravavel
        try:
            with open(self.config.LOG_FILE, 'a', encoding='utf-8') as f:
                f.write('')  # 尝试写入空string验证权限
                f.flush()    # Flush imediato
        except Exception as e:
            logger.error(f"日志Arquivo无法写入: {self.config.LOG_FILE}, Erro(s): {e}")
            raise
        
    def _initialize_file_baseline(self):
        """
        初始化Numero de arquivos量linha de base。

        将 Insight/Media/Query 三个Sumario传入 `FileCountBaseline`，
        生成一次性的参考值，之后按增量判断三引擎是否产出新relatorio。
        """
        directories = {
            'insight': 'insight_engine_streamlit_reports',
            'media': 'media_engine_streamlit_reports',
            'query': 'query_engine_streamlit_reports'
        }
        self.file_baseline.initialize_baseline(directories)
    
    def _initialize_llm(self) -> LLMClient:
        """
        Inicializar cliente LLM.

        利用配置中的 API Key / 模型 / Base URL 构建统一的
        `LLMClient` 实例，为所有节点提供复用的推理入口。
        """
        return LLMClient(
            api_key=self.config.REPORT_ENGINE_API_KEY,
            model_name=self.config.REPORT_ENGINE_MODEL_NAME,
            base_url=self.config.REPORT_ENGINE_BASE_URL,
        )

    def _initialize_rescue_llms(self) -> List[Tuple[str, LLMClient]]:
        """
        初始化跨引擎章节修复所需的Cliente LLM列表。

        顺序遵循“Report → Forum → Insight → Media”，缺失配置会被自动跳过。
        """
        clients: List[Tuple[str, LLMClient]] = []
        if self.llm_client:
            clients.append(("report_engine", self.llm_client))
        fallback_specs = [
            (
                "forum_engine",
                self.config.FORUM_HOST_API_KEY,
                self.config.FORUM_HOST_MODEL_NAME,
                self.config.FORUM_HOST_BASE_URL,
            ),
            (
                "insight_engine",
                self.config.INSIGHT_ENGINE_API_KEY,
                self.config.INSIGHT_ENGINE_MODEL_NAME,
                self.config.INSIGHT_ENGINE_BASE_URL,
            ),
            (
                "media_engine",
                self.config.MEDIA_ENGINE_API_KEY,
                self.config.MEDIA_ENGINE_MODEL_NAME,
                self.config.MEDIA_ENGINE_BASE_URL,
            ),
        ]
        for label, api_key, model_name, base_url in fallback_specs:
            if not api_key or not model_name:
                continue
            try:
                client = LLMClient(api_key=api_key, model_name=model_name, base_url=base_url)
            except Exception as exc:
                logger.warning(f"{label} LLM初始化失败，跳过该修复通道: {exc}")
                continue
            clients.append((label, client))
        return clients
    
    def _initialize_nodes(self):
        """
        Inicializar nos de processamento.

        顺序实例化模板选择、文档布局、篇幅规划、章节生成四个节点，
        其中章节节点额外依赖 IR 校验器与章节存储器。
        """
        self.template_selection_node = TemplateSelectionNode(
            self.llm_client,
            self.config.TEMPLATE_DIR
        )
        self.document_layout_node = DocumentLayoutNode(self.llm_client)
        self.word_budget_node = WordBudgetNode(self.llm_client)
        self.chapter_generation_node = ChapterGenerationNode(
            self.llm_client,
            self.validator,
            self.chapter_storage,
            fallback_llm_clients=self.json_rescue_clients,
            error_log_dir=self.config.JSON_ERROR_LOG_DIR,
        )
    
    def generate_report(self, query: str, reports: List[Any], forum_logs: str = "",
                        custom_template: str = "", save_report: bool = True,
                        stream_handler: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> str:
        """
        生成综合relatorio（章节JSON → IR → HTML）。

        Etapas principais:
            1. 归一化三引擎relatorio + logs do forum，并输出流式事件；
            2. 模板选择 → 模板切片 → 文档布局 → 篇幅规划；
            3. 结合篇幅目标逐章调用LLM，遇到解析Erro(s)会自动重试；
            4. Montar capitulos no Document IR e entregar ao renderizador HTML para gerar o produto final;
            5. 可选地将HTML/IR/状态落盘，并向外界回传Caminho信息。

        Parametros:
            query: 最终要生成的relatorio主题或提问语句。
            reports: 来自 Query/Media/Insight 等分析引擎的Saida original，允许传入string或更复杂的对象。
            forum_logs: Registros do forum/colaboracao para o LLM entender o contexto de discussao multipla.
            custom_template: Template Markdown especificado pelo usuario; se vazio, a selecao sera feita automaticamente pelo no de template.
            save_report: Se deve gravar automaticamente HTML, IR e estado em disco apos geracao.
            stream_handler: Callback de evento de streaming opcional, recebe tags de etapa e payload, para exibicao em tempo real na UI.

        Retorna:
            dict: 包含 `html_content` 以及HTML/IR/状态ArquivoCaminho的字典；若 `save_report=False` 则仅返回HTMLstring。

        Excecoes:
            Exception: Lancada quando qualquer sub-no ou etapa de renderizacao falhar; o chamador externo e responsavel pelo fallback.
        """
        start_time = datetime.now()
        report_id = f"report-{uuid4().hex[:8]}"
        self.state.task_id = report_id
        self.state.query = query
        self.state.metadata.query = query
        self.state.mark_processing()

        normalized_reports = self._normalize_reports(reports)

        def emit(event_type: str, payload: Dict[str, Any]):
            """面向Report Engine流通道的事件分发器，保证Erro(s)不外泄。"""
            if not stream_handler:
                return
            try:
                stream_handler(event_type, payload)
            except Exception as callback_error:  # pragma: no cover - apenas registrar
                logger.warning(f"Falha no callback de evento de streaming: {callback_error}")

        logger.info(f"Iniciando geracao do relatorio {report_id}: {query}")
        logger.info(f"Dados de entrada - quantidade de relatorios: {len(reports)}, comprimento dos logs do forum: {len(str(forum_logs))}")
        emit('stage', {'stage': 'agent_start', 'report_id': report_id, 'query': query})

        try:
            template_result = self._select_template(query, reports, forum_logs, custom_template)
            template_result = self._ensure_mapping(
                template_result,
                "模板选择结果",
                expected_keys=["template_name", "template_content"],
            )
            self.state.metadata.template_used = template_result.get('template_name', '')
            emit('stage', {
                'stage': 'template_selected',
                'template': template_result.get('template_name'),
                'reason': template_result.get('selection_reason')
            })
            emit('progress', {'progress': 10, 'message': 'Selecao de template concluida'})
            sections = self._slice_template(template_result.get('template_content', ''))
            if not sections:
                raise ValueError("Nao foi possivel analisar capitulos do template, verifique o conteudo do template.")
            emit('stage', {'stage': 'template_sliced', 'section_count': len(sections)})

            template_text = template_result.get('template_content', '')
            template_overview = self._build_template_overview(template_text, sections)
            # 基于模板骨架+三引擎内容设计全局标题、Sumario与视觉主题
            layout_design = self._run_stage_with_retry(
                "Design do documento",
                lambda: self.document_layout_node.run(
                    sections,
                    template_text,
                    normalized_reports,
                    forum_logs,
                    query,
                    template_overview,
                ),
                # Campo toc foi substituido por tocPlan, aqui seleciona/valida pelo Schema mais recente
                expected_keys=["title", "hero", "tocPlan", "tocTitle"],
            )
            emit('stage', {
                'stage': 'layout_designed',
                'title': layout_design.get('title'),
                'toc': layout_design.get('tocTitle')
            })
            emit('progress', {'progress': 15, 'message': 'Design de titulo/sumario do documento concluido'})
            # Usar o design recem-gerado para planejar extensao do livro inteiro, restringindo contagem de palavras e enfase de cada capitulo
            word_plan = self._run_stage_with_retry(
                "Planejamento de extensao dos capitulos",
                lambda: self.word_budget_node.run(
                    sections,
                    layout_design,
                    normalized_reports,
                    forum_logs,
                    query,
                    template_overview,
                ),
                expected_keys=["chapters", "totalWords", "globalGuidelines"],
                postprocess=self._normalize_word_plan,
            )
            emit('stage', {
                'stage': 'word_plan_ready',
                'chapter_targets': len(word_plan.get('chapters', []))
            })
            emit('progress', {'progress': 20, 'message': 'Planejamento de contagem de palavras dos capitulos gerado'})
            # Registrar contagem de palavras alvo/pontos de enfase de cada capitulo, passados ao LLM do capitulo posteriormente
            chapter_targets = {
                entry.get("chapterId"): entry
                for entry in word_plan.get("chapters", [])
                if entry.get("chapterId")
            }

            generation_context = self._build_generation_context(
                query,
                normalized_reports,
                forum_logs,
                template_result,
                layout_design,
                chapter_targets,
                word_plan,
                template_overview,
            )
            # Metadados globais necessarios para IR/renderizacao, com titulo/tema/Sumario/informacoes de extensao
            manifest_meta = {
                "query": query,
                "title": layout_design.get("title") or (f"{query} - Relatorio de analise de opiniao publica" if query else template_result.get("template_name")),
                "subtitle": layout_design.get("subtitle"),
                "tagline": layout_design.get("tagline"),
                "templateName": template_result.get("template_name"),
                "selectionReason": template_result.get("selection_reason"),
                "themeTokens": generation_context.get("theme_tokens", {}),
                "toc": {
                    "depth": 3,
                    "autoNumbering": True,
                    "title": layout_design.get("tocTitle") or "Sumario",
                },
                "hero": layout_design.get("hero"),
                "layoutNotes": layout_design.get("layoutNotes"),
                "wordPlan": {
                    "totalWords": word_plan.get("totalWords"),
                    "globalGuidelines": word_plan.get("globalGuidelines"),
                },
                "templateOverview": template_overview,
            }
            if layout_design.get("themeTokens"):
                manifest_meta["themeTokens"] = layout_design["themeTokens"]
            if layout_design.get("tocPlan"):
                manifest_meta["toc"]["customEntries"] = layout_design["tocPlan"]
            # Inicializar saida de capitulosSumarioe gravar manifesto para persistencia por streaming
            run_dir = self.chapter_storage.start_session(report_id, manifest_meta)
            self._persist_planning_artifacts(run_dir, layout_design, word_plan, template_overview)
            emit('stage', {'stage': 'storage_ready', 'run_dir': str(run_dir)})

            chapters = []
            chapter_max_attempts = max(
                self._CONTENT_SPARSE_MIN_ATTEMPTS, self.config.CHAPTER_JSON_MAX_ATTEMPTS
            )
            total_chapters = len(sections)  # Total de capitulos
            completed_chapters = 0  # Capitulos concluidos

            for section in sections:
                logger.info(f"Gerando capitulo: {section.title}")
                emit('chapter_status', {
                    'chapterId': section.chapter_id,
                    'title': section.title,
                    'status': 'running'
                })
                # Callback de streaming do capitulo: transmitir delta do LLM ao SSE para renderizacao em tempo real no frontend
                def chunk_callback(delta: str, meta: Dict[str, Any], section_ref: TemplateSection = section):
                    """
                    章节内容流式回调。

                    Args:
                        delta: LLM最新输出的增量文本。
                        meta: 节点回传的章节元数据，兜底时使用。
                        section_ref: 默认指向当前章节，保证在缺失元信息时也能定位。
                    """
                    emit('chapter_chunk', {
                        'chapterId': meta.get('chapterId') or section_ref.chapter_id,
                        'title': meta.get('title') or section_ref.title,
                        'delta': delta
                    })

                chapter_payload: Dict[str, Any] | None = None
                attempt = 1
                best_sparse_candidate: Dict[str, Any] | None = None
                best_sparse_score = -1
                fallback_used = False
                while attempt <= chapter_max_attempts:
                    try:
                        chapter_payload = self.chapter_generation_node.run(
                            section,
                            generation_context,
                            run_dir,
                            stream_callback=chunk_callback
                        )
                        break
                    except (ChapterJsonParseError, ChapterContentError, ChapterValidationError) as structured_error:
                        if isinstance(structured_error, ChapterContentError):
                            error_kind = "content_sparse"
                            readable_label = "Densidade de conteudo anormal"
                        elif isinstance(structured_error, ChapterValidationError):
                            error_kind = "validation"
                            readable_label = "Falha na validacao de estrutura"
                        else:
                            error_kind = "json_parse"
                            readable_label = "Falha na analise JSON"
                        if isinstance(structured_error, ChapterContentError):
                            candidate = getattr(structured_error, "chapter_payload", None)
                            candidate_score = getattr(structured_error, "body_characters", 0) or 0
                            if isinstance(candidate, dict) and candidate_score >= 0:
                                if candidate_score > best_sparse_score:
                                    best_sparse_candidate = deepcopy(candidate)
                                    best_sparse_score = candidate_score
                        will_fallback = (
                            isinstance(structured_error, ChapterContentError)
                            and attempt >= chapter_max_attempts
                            and attempt >= self._CONTENT_SPARSE_MIN_ATTEMPTS
                            and best_sparse_candidate is not None
                        )
                        logger.warning(
                            "章节 {title} {label}（第 {attempt}/{total} 次尝试）: {error}",
                            title=section.title,
                            label=readable_label,
                            attempt=attempt,
                            total=chapter_max_attempts,
                            error=structured_error,
                        )
                        status_value = 'retrying' if attempt < chapter_max_attempts or will_fallback else 'error'
                        status_payload = {
                            'chapterId': section.chapter_id,
                            'title': section.title,
                            'status': status_value,
                            'attempt': attempt,
                            'error': str(structured_error),
                            'reason': error_kind,
                        }
                        if isinstance(structured_error, ChapterValidationError):
                            validation_errors = getattr(structured_error, "errors", None)
                            if validation_errors:
                                status_payload['errors'] = validation_errors
                        if will_fallback:
                            status_payload['warning'] = 'content_sparse_fallback_pending'
                        emit('chapter_status', status_payload)
                        if will_fallback:
                            logger.warning(
                                "章节 {title} 达到最大尝试次数，保留字数最多（约 {score} 字）的版本作为兜底输出",
                                title=section.title,
                                score=best_sparse_score,
                            )
                            chapter_payload = self._finalize_sparse_chapter(best_sparse_candidate)
                            fallback_used = True
                            break
                        if attempt >= chapter_max_attempts:
                            raise
                        attempt += 1
                        continue
                    except (AttributeError, TypeError, KeyError, IndexError, ValueError, json.JSONDecodeError) as structure_error:
                        # 捕获因 JSON 结构异常导致的运行时Erro(s)，包装为可重试异常
                        # 包括：
                        # - AttributeError: 如 list.get() 调用失败
                        # - TypeError: 类型不匹配
                        # - KeyError: 字典键缺失
                        # - IndexError: 列表索引越界
                        # - ValueError: 值Erro(s)（如 LLM 返回空内容、缺少必要字段）
                        # - json.JSONDecodeError: JSON 解析失败（未被内部捕获的情况）
                        error_type = type(structure_error).__name__
                        logger.warning(
                            "章节 {title} 生成过程中发生 {error_type}（第 {attempt}/{total} 次尝试），将尝试重新生成: {error}",
                            title=section.title,
                            error_type=error_type,
                            attempt=attempt,
                            total=chapter_max_attempts,
                            error=structure_error,
                        )
                        emit('chapter_status', {
                            'chapterId': section.chapter_id,
                            'title': section.title,
                            'status': 'retrying' if attempt < chapter_max_attempts else 'error',
                            'attempt': attempt,
                            'error': str(structure_error),
                            'reason': 'structure_error',
                            'error_type': error_type
                        })
                        if attempt >= chapter_max_attempts:
                            # 达到最大重试次数，包装为 ChapterJsonParseError 抛出
                            raise ChapterJsonParseError(
                                f"{section.title} 章节因 {error_type} 在 {chapter_max_attempts} 次尝试后仍无法生成: {structure_error}"
                            ) from structure_error
                        attempt += 1
                        continue
                    except Exception as chapter_error:
                        if not self._should_retry_inappropriate_content_error(chapter_error):
                            raise
                        logger.warning(
                            "章节 {title} 触发内容安全限制（第 {attempt}/{total} 次尝试），准备重新生成: {error}",
                            title=section.title,
                            attempt=attempt,
                            total=chapter_max_attempts,
                            error=chapter_error,
                        )
                        emit('chapter_status', {
                            'chapterId': section.chapter_id,
                            'title': section.title,
                            'status': 'retrying' if attempt < chapter_max_attempts else 'error',
                            'attempt': attempt,
                            'error': str(chapter_error),
                            'reason': 'content_filter'
                        })
                        if attempt >= chapter_max_attempts:
                            raise
                        attempt += 1
                        continue
                if chapter_payload is None:
                    raise ChapterJsonParseError(
                        f"{section.title} 章节JSON在 {chapter_max_attempts} 次尝试后仍无法解析"
                    )
                chapters.append(chapter_payload)
                completed_chapters += 1  # 更新Capitulos concluidos
                # 计算当前进度：20% + 80% * (Capitulos concluidos / Total de capitulos)，四舍五入
                chapter_progress = 20 + round(80 * completed_chapters / total_chapters)
                emit('progress', {
                    'progress': chapter_progress,
                    'message': f'章节 {completed_chapters}/{total_chapters} 已完成'
                })
                completion_status = {
                    'chapterId': section.chapter_id,
                    'title': section.title,
                    'status': 'completed',
                    'attempt': attempt,
                }
                if fallback_used:
                    completion_status['warning'] = 'content_sparse_fallback'
                    completion_status['warningMessage'] = self._CONTENT_SPARSE_WARNING_TEXT
                emit('chapter_status', completion_status)

            document_ir = self.document_composer.build_document(
                report_id,
                manifest_meta,
                chapters
            )
            emit('stage', {'stage': 'chapters_compiled', 'chapter_count': len(chapters)})
            html_report = self.renderer.render(document_ir)
            emit('stage', {'stage': 'html_rendered', 'html_length': len(html_report)})

            self.state.html_content = html_report
            self.state.mark_completed()

            saved_files = {}
            if save_report:
                saved_files = self._save_report(html_report, document_ir, report_id)
                emit('stage', {'stage': 'report_saved', 'files': saved_files})

            generation_time = (datetime.now() - start_time).total_seconds()
            self.state.metadata.generation_time = generation_time
            logger.info(f"Geracao do relatorio concluida, tempo decorrido: {generation_time:.2f} 秒")
            emit('metrics', {'generation_seconds': generation_time})
            return {
                'html_content': html_report,
                'report_id': report_id,
                **saved_files
            }

        except Exception as e:
            self.state.mark_failed(str(e))
            logger.exception(f"Erro durante a geracao do relatorio: {str(e)}")
            emit('error', {'stage': 'agent_failed', 'message': str(e)})
            raise
    
    def _select_template(self, query: str, reports: List[Any], forum_logs: str, custom_template: str):
        """
        Selecionar template do relatorio.

        优先使用用户指定的模板；否则将查询、三引擎relatorio与logs do forum
        作为上下文交给 TemplateSelectionNode，由 LLM 返回最契合的
        模板名称、内容及理由，并自动记录在状态中。

        Parametros:
            query: relatorio主题，用于提示词聚焦行业/事件。
            reports: 多来源relatorio原文，帮助LLM判断结构复杂度。
            forum_logs: Texto do forum ou discussao colaborativa correspondente, para complementar contexto.
            custom_template: Template Markdown personalizado do CLI/frontend; quando nao vazio, usado diretamente.

        Retorna:
            dict: Resultado estruturado contendo `template_name`, `template_content` e `selection_reason`, para consumo pelos nos subsequentes.
        """
        logger.info("Selecionando template do relatorio...")
        
        # Se o usuario forneceu template personalizado, usar diretamente
        if custom_template:
            logger.info("Usando template personalizado do usuario")
            return {
                'template_name': 'custom',
                'template_content': custom_template,
                'selection_reason': 'Template personalizado especificado pelo usuario'
            }
        
        template_input = {
            'query': query,
            'reports': reports,
            'forum_logs': forum_logs
        }
        
        try:
            template_result = self.template_selection_node.run(template_input)
            
            # Atualizar estado
            self.state.metadata.template_used = template_result['template_name']
            
            logger.info(f"Template selecionado: {template_result['template_name']}")
            logger.info(f"Motivo da selecao: {template_result['selection_reason']}")
            
            return template_result
        except Exception as e:
            logger.error(f"Falha na selecao de template, usando template padrao: {str(e)}")
            # Usar template de fallback diretamente
            fallback_template = {
                'template_name': 'Template de relatorio de analise de eventos sociais de interesse publico',
                'template_content': self._get_fallback_template_content(),
                'selection_reason': 'Falha na selecao de template, usando template padrao de analise de eventos sociais'
            }
            self.state.metadata.template_used = fallback_template['template_name']
            return fallback_template
    
    def _slice_template(self, template_markdown: str) -> List[TemplateSection]:
        """
        Dividir o template em lista de capitulos; fornecer fallback se vazio.

        Delega a `parse_template_sections` para analisar titulos/numeros Markdown como
        lista de `TemplateSection`, garantindo IDs de capitulo estaveis para geracao subsequente.
        Quando o formato do template e anormal, reverte para estrutura simples integrada para evitar falha.

        Parametros:
            template_markdown: Texto Markdown completo do template.

        Retorna:
            list[TemplateSection]: Sequencia de capitulos analisada; retorna estrutura de fallback de capitulo unico se a analise falhar.
        """
        sections = parse_template_sections(template_markdown)
        if sections:
            return sections
        logger.warning("Nenhum capitulo encontrado no template, usando estrutura padrao")
        fallback = TemplateSection(
            title="1.0 综合分析",
            slug="section-1-0",
            order=10,
            depth=1,
            raw_title="1.0 综合分析",
            number="1.0",
            chapter_id="S1",
            outline=["1.1 摘要", "1.2 数据亮点", "1.3 风险提示"],
        )
        return [fallback]

    def _build_generation_context(
        self,
        query: str,
        reports: Dict[str, str],
        forum_logs: str,
        template_result: Dict[str, Any],
        layout_design: Dict[str, Any],
        chapter_directives: Dict[str, Any],
        word_plan: Dict[str, Any],
        template_overview: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Construir contexto compartilhado necessario para geracao de capitulos.

        将模板名称、布局设计、主题配色、篇幅规划、logs do forum等
        一次性整合为 `generation_context`，后续每章调用 LLM 时
        直接复用，确保所有章节共享一致的语调和视觉约束。

        Parametros:
            query: Palavra de consulta do usuario.
            reports: 归一化后的 query/media/insight relatorio映射。
            forum_logs: Registros de discussao dos tres motores.
            template_result: Meta-informacoes do template retornadas pelo no de template.
            layout_design: 文档布局节点产出的标题/Sumario/主题设计。
            chapter_directives: Mapeamento de diretivas de capitulos retornado pelo no de planejamento de palavras.
            word_plan: Resultado original do planejamento de extensao, contendo restricoes globais de contagem de palavras.
            template_overview: Resumo de estrutura de capitulos refinado a partir das fatias do template.

        Retorna:
            dict: Contexto completo necessario para geracao de capitulos pelo LLM, contendo chaves como cores do tema, layout, restricoes.
        """
        # Usar cores do tema personalizadas do design primeiro; caso contrario, reverter ao tema padrao
        theme_tokens = (
            layout_design.get("themeTokens")
            if layout_design else None
        ) or self._default_theme_tokens()

        return {
            "query": query,
            "template_name": template_result.get("template_name"),
            "reports": reports,
            "forum_logs": self._stringify(forum_logs),
            "theme_tokens": theme_tokens,
            "style_directives": {
                "tone": "analytical",
                "audience": "executive",
                "language": "zh-CN",
            },
            "data_bundles": [],
            "max_tokens": min(self.config.MAX_CONTENT_LENGTH, 6000),
            "layout": layout_design or {},
            "template_overview": template_overview or {},
            "chapter_directives": chapter_directives or {},
            "word_plan": word_plan or {},
        }

    def _normalize_reports(self, reports: List[Any]) -> Dict[str, str]:
        """
        将不同来源的relatorio统一转为string。

        A ordem convencional e Query/Media/Insight; objetos fornecidos pelos motores podem ser
        dicionarios ou tipos personalizados, portanto passam uniformemente por `_stringify` para tolerancia a falhas.

        Parametros:
            reports: 任意类型的relatorio列表，允许缺失或顺序混乱。

        Retorna:
            dict: 包含 `query_engine`/`media_engine`/`insight_engine` 三个string字段的映射。
        """
        keys = ["query_engine", "media_engine", "insight_engine"]
        normalized: Dict[str, str] = {}
        for idx, key in enumerate(keys):
            value = reports[idx] if idx < len(reports) else ""
            normalized[key] = self._stringify(value)
        return normalized

    def _should_retry_inappropriate_content_error(self, error: Exception) -> bool:
        """
        Determinar se a excecao do LLM foi causada por seguranca de conteudo/conteudo inadequado.

        当检测到供应商返回的Erro(s)包含特定关键词时，允许章节生成
        tentar novamente para contornar acionamento acidental de revisao de conteudo.

        Parametros:
            error: Cliente LLM抛出的异常对象。

        Retorna:
            bool: Retorna True se palavras-chave de revisao de conteudo forem correspondidas, caso contrario False.
        """
        message = str(error) if error else ""
        if not message:
            return False
        normalized = message.lower()
        keywords = [
            "inappropriate content",
            "content violation",
            "content moderation",
            "model-studio/error-code",
        ]
        return any(keyword in normalized for keyword in keywords)

    def _run_stage_with_retry(
        self,
        stage_name: str,
        fn: Callable[[], Any],
        expected_keys: Optional[List[str]] = None,
        postprocess: Optional[Callable[[Dict[str, Any], str], Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Executar uma unica etapa LLM e tentar novamente um numero limitado de vezes em caso de anomalia estrutural.

        该方法只针对结构类Erro(s)做本地修复/重试，避免整个Agent重启。
        """
        last_error: Optional[Exception] = None
        for attempt in range(1, self._STRUCTURAL_RETRY_ATTEMPTS + 1):
            try:
                raw_result = fn()
                result = self._ensure_mapping(raw_result, stage_name, expected_keys)
                if postprocess:
                    result = postprocess(result, stage_name)
                return result
            except StageOutputFormatError as exc:
                last_error = exc
                logger.warning(
                    "{stage} 输出结构异常（第 {attempt}/{total} 次），将Tentando reparar或重试: {error}",
                    stage=stage_name,
                    attempt=attempt,
                    total=self._STRUCTURAL_RETRY_ATTEMPTS,
                    error=exc,
                )
                if attempt >= self._STRUCTURAL_RETRY_ATTEMPTS:
                    break
        raise last_error  # type: ignore[misc]

    def _ensure_mapping(
        self,
        value: Any,
        context: str,
        expected_keys: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Garantir que a saida da etapa seja dict; se retornar lista, tentar extrair o elemento mais compativel.
        """
        if isinstance(value, dict):
            return value

        if isinstance(value, list):
            candidates = [item for item in value if isinstance(item, dict)]
            if candidates:
                best = candidates[0]
                if expected_keys:
                    candidates.sort(
                        key=lambda item: sum(1 for key in expected_keys if key in item),
                        reverse=True,
                    )
                    best = candidates[0]
                logger.warning(
                    "{context} 返回列表，已自动提取包含最多预期键的元素继续执行",
                    context=context,
                )
                return best
            raise StageOutputFormatError(f"{context} 返回列表但缺少可用的对象元素")

        if value is None:
            raise StageOutputFormatError(f"{context} 返回空结果")

        raise StageOutputFormatError(
            f"{context} 返回类型 {type(value).__name__}，期望字典"
        )

    def _normalize_word_plan(self, word_plan: Dict[str, Any], stage_name: str) -> Dict[str, Any]:
        """
        Limpar resultado do planejamento de extensao, garantindo seguranca de tipos para chapters/globalGuidelines/totalWords.
        """
        raw_chapters = word_plan.get("chapters", [])
        if isinstance(raw_chapters, dict):
            chapters_iterable = raw_chapters.values()
        elif isinstance(raw_chapters, list):
            chapters_iterable = raw_chapters
        else:
            chapters_iterable = []

        normalized: List[Dict[str, Any]] = []
        for idx, entry in enumerate(chapters_iterable):
            if isinstance(entry, dict):
                normalized.append(entry)
                continue
            if isinstance(entry, list):
                dict_candidate = next((item for item in entry if isinstance(item, dict)), None)
                if dict_candidate:
                    logger.warning(
                        "{stage} 第 {idx} 个章节条目为列表，已提取首个对象用于后续流程",
                        stage=stage_name,
                        idx=idx + 1,
                    )
                    normalized.append(dict_candidate)
                    continue
            logger.warning(
                "{stage} 跳过无法解析的章节条目#{idx}（类型: {type_name}）",
                stage=stage_name,
                idx=idx + 1,
                type_name=type(entry).__name__,
            )

        if not normalized:
            raise StageOutputFormatError(f"{stage_name} 缺少有效的章节规划，无法继续")

        word_plan["chapters"] = normalized

        guidelines = word_plan.get("globalGuidelines")
        if not isinstance(guidelines, list):
            if guidelines is None or guidelines == "":
                word_plan["globalGuidelines"] = []
            else:
                logger.warning(
                    "{stage} globalGuidelines 类型异常，已转换为列表封装",
                    stage=stage_name,
                )
                word_plan["globalGuidelines"] = [guidelines]

        if not isinstance(word_plan.get("totalWords"), (int, float)):
            logger.warning(
                "{stage} totalWords 类型异常，使用默认值 10000",
                stage=stage_name,
            )
            word_plan["totalWords"] = 10000

        return word_plan

    def _finalize_sparse_chapter(self, chapter: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Construir capitulo de fallback para conteudo esparso: copiar payload original e inserir paragrafo de aviso.
        """
        safe_chapter = deepcopy(chapter or {})
        if not isinstance(safe_chapter, dict):
            safe_chapter = {}
        self._ensure_sparse_warning_block(safe_chapter)
        return safe_chapter

    def _ensure_sparse_warning_block(self, chapter: Dict[str, Any]) -> None:
        """
        Inserir paragrafo de aviso apos o titulo do capitulo, alertando o leitor de que a contagem de palavras deste capitulo e baixa.
        """
        warning_block = {
            "type": "paragraph",
            "inlines": [
                {
                    "text": self._CONTENT_SPARSE_WARNING_TEXT,
                    "marks": [{"type": "italic"}],
                }
            ],
            "meta": {"role": "content-sparse-warning"},
        }
        blocks = chapter.get("blocks")
        if isinstance(blocks, list) and blocks:
            inserted = False
            for idx, block in enumerate(blocks):
                if isinstance(block, dict) and block.get("type") == "heading":
                    blocks.insert(idx + 1, warning_block)
                    inserted = True
                    break
            if not inserted:
                blocks.insert(0, warning_block)
        else:
            chapter["blocks"] = [warning_block]
        meta = chapter.get("meta")
        if isinstance(meta, dict):
            meta["contentSparseWarning"] = True
        else:
            chapter["meta"] = {"contentSparseWarning": True}

    def _stringify(self, value: Any) -> str:
        """
        安全地将对象转成string。

        - dict/list 统一序列化为格式化 JSON，便于提示词消费；
        - 其他类型走 `str()`，None 则返回空串，避免 None 传播。

        Parametros:
            value: 任意Python对象。

        Retorna:
            str: 适配提示词/日志的string表现。
        """
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            try:
                return json.dumps(value, ensure_ascii=False, indent=2)
            except Exception:
                return str(value)
        return str(value)

    def _default_theme_tokens(self) -> Dict[str, Any]:
        """
        Construir variaveis de tema padrao, compartilhadas pelo renderizador/LLM.

        当布局节点未返回专属配色时使用该套色板，保持relatorio风格统一。

        Retorna:
            dict: 包含颜色、字体、间距、布尔开关等渲染参数的主题字典。
        """
        return {
            "colors": {
                "bg": "#f8f9fa",
                "text": "#212529",
                "primary": "#007bff",
                "secondary": "#6c757d",
                "card": "#ffffff",
                "border": "#dee2e6",
                "accent1": "#17a2b8",
                "accent2": "#28a745",
                "accent3": "#ffc107",
                "accent4": "#dc3545",
            },
            "fonts": {
                "body": "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif",
                "heading": "'Source Han Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif",
            },
            "spacing": {"container": "1200px", "gutter": "24px"},
            "vars": {
                "header_sticky": True,
                "toc_depth": 3,
                "enable_dark_mode": True,
            },
        }

    def _build_template_overview(
        self,
        template_markdown: str,
        sections: List[TemplateSection],
    ) -> Dict[str, Any]:
        """
        Extrair titulo do template e estrutura de capitulos para referencia unificada em design/planejamento de extensao.

        同时记录章节ID/slug/order等辅助字段，保证多节点对齐。

        Parametros:
            template_markdown: 模板原文，用于解析全局标题。
            sections: `TemplateSection` 列表，作为章节骨架。

        Retorna:
            dict: 包含模板标题与章节元数据的概览结构。
        """
        fallback_title = sections[0].title if sections else ""
        overview = {
            "title": self._extract_template_title(template_markdown, fallback_title),
            "chapters": [],
        }
        for section in sections:
            overview["chapters"].append(
                {
                    "chapterId": section.chapter_id,
                    "title": section.title,
                    "rawTitle": section.raw_title,
                    "number": section.number,
                    "slug": section.slug,
                    "order": section.order,
                    "depth": section.depth,
                    "outline": section.outline,
                }
            )
        return overview

    @staticmethod
    def _extract_template_title(template_markdown: str, fallback: str = "") -> str:
        """
        Tentar extrair o primeiro titulo do Markdown.

        优先返回首个 `#` 语法标题；如果模板首行就是正文，则回退到
        第一行非空文本或调用方提供的 fallback。

        Parametros:
            template_markdown: 模板原文。
            fallback: 备用标题，当文档缺少显式标题时使用。

        Retorna:
            str: 解析到的Texto do titulo.
        """
        for line in template_markdown.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
            if stripped:
                fallback = fallback or stripped
        return fallback or "Relatorio inteligente de analise de opiniao publica"
    
    def _get_fallback_template_content(self) -> str:
        """
        Obter conteudo do template de fallback.

        当模板Sumario不可用或LLM选择失败时使用该 Markdown 模板，
        保证后续流程仍能给出结构化章节。
        """
        return """# Relatorio de analise de eventos sociais de interesse publico

## 执行摘要
本relatorio针对当前社会热点事件进行综合分析，整合了多方信息源的观点和数据。

## 事件概况
### Informacoes basicas
- 事件性质：{event_nature}
- 发生时间：{event_time}
- 涉及范围：{event_scope}

## 舆情态势分析
### 整体趋势
{sentiment_analysis}

### 主要观点分布
{opinion_distribution}

## 媒体报道分析
### 主流媒体态度
{media_analysis}

### 报道重点
{report_focus}

## 社会影响评估
### 直接影响
{direct_impact}

### 潜在影响
{potential_impact}

## 应对建议
### 即时措施
{immediate_actions}

### 长期策略
{long_term_strategy}

## 结论与展望
{conclusion}

---
*relatorio类型：社会公共热点事件分析*
*生成时间：{generation_time}*
"""
    
    def _save_report(self, html_content: str, document_ir: Dict[str, Any], report_id: str) -> Dict[str, Any]:
        """
        保存HTML与IR到Arquivo并返回Caminho信息。

        生成基于查询和时间戳的易读Arquivo名，同时也把运行态的
        `ReportState` 写入 JSON，方便下游排障或断点续跑。

        Parametros:
            html_content: 渲染后的HTML正文。
            document_ir: Document IR结构化数据。
            report_id: 当前任务ID，用于创建独立Arquivo名。

        Retorna:
            dict: 记录HTML/IR/StateArquivo的绝对与相对Caminho信息。
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        query_safe = "".join(
            c for c in self.state.metadata.query if c.isalnum() or c in (" ", "-", "_")
        ).rstrip()
        query_safe = query_safe.replace(" ", "_")[:30] or "report"

        html_filename = f"final_report_{query_safe}_{timestamp}.html"
        html_path = Path(self.config.OUTPUT_DIR) / html_filename
        html_path.write_text(html_content, encoding="utf-8")
        html_abs = str(html_path.resolve())
        html_rel = os.path.relpath(html_abs, os.getcwd())

        ir_path = self._save_document_ir(document_ir, query_safe, timestamp)
        ir_abs = str(ir_path.resolve())
        ir_rel = os.path.relpath(ir_abs, os.getcwd())

        state_filename = f"report_state_{query_safe}_{timestamp}.json"
        state_path = Path(self.config.OUTPUT_DIR) / state_filename
        self.state.save_to_file(str(state_path))
        state_abs = str(state_path.resolve())
        state_rel = os.path.relpath(state_abs, os.getcwd())

        logger.info(f"Relatorio HTML salvo: {html_path}")
        logger.info(f"Document IR salvo: {ir_path}")
        logger.info(f"状态已保存到: {state_path}")
        
        return {
            'report_filename': html_filename,
            'report_filepath': html_abs,
            'report_relative_path': html_rel,
            'ir_filename': ir_path.name,
            'ir_filepath': ir_abs,
            'ir_relative_path': ir_rel,
            'state_filename': state_filename,
            'state_filepath': state_abs,
            'state_relative_path': state_rel,
        }

    def _save_document_ir(self, document_ir: Dict[str, Any], query_safe: str, timestamp: str) -> Path:
        """
        将整本IR写入独立Sumario。

        `Document IR` 与 HTML 解耦保存，便于调试渲染差异以及
        在不重新跑 LLM 的情况下再次渲染或导出其他格式。

        Parametros:
            document_ir: 整本relatorio的IR结构。
            query_safe: 已清洗的查询短语，用于Arquivo命名。
            timestamp: 运行时间戳，保证Arquivo名唯一。

        Retorna:
            Path: 指向保存后的IRArquivoCaminho。
        """
        filename = f"report_ir_{query_safe}_{timestamp}.json"
        ir_path = Path(self.config.DOCUMENT_IR_OUTPUT_DIR) / filename
        ir_path.write_text(
            json.dumps(document_ir, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return ir_path
    
    def _persist_planning_artifacts(
        self,
        run_dir: Path,
        layout_design: Dict[str, Any],
        word_plan: Dict[str, Any],
        template_overview: Dict[str, Any],
    ):
        """
        将Design do documento稿、篇幅规划与模板概览另存成JSON。

        这些中间件Arquivo（document_layout/word_plan/template_overview）
        方便在调试或复盘时快速定位：标题/Sumario/主题是如何确定的、
        字数分配有什么要求，以便后续人工校正。

        Parametros:
            run_dir: 章节输出根Sumario。
            layout_design: 文档布局节点的Saida original。
            word_plan: 篇幅规划节点输出。
            template_overview: 模板概览JSON。
        """
        artifacts = {
            "document_layout": layout_design,
            "word_plan": word_plan,
            "template_overview": template_overview,
        }
        for name, payload in artifacts.items():
            if not payload:
                continue
            path = run_dir / f"{name}.json"
            try:
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                logger.warning(f"写入{name}失败: {exc}")
    
    def get_progress_summary(self) -> Dict[str, Any]:
        """Obter resumo de progresso, retornando diretamente dicionario de estado serializavel para consulta pela camada API."""
        return self.state.to_dict()
    
    def load_state(self, filepath: str):
        """从Arquivo加载状态并覆盖当前state，便于断点恢复。"""
        self.state = ReportState.load_from_file(filepath)
        logger.info(f"状态已从 {filepath} 加载")
    
    def save_state(self, filepath: str):
        """保存状态到Arquivo，通常用于Tarefa concluida后的分析与备份。"""
        self.state.save_to_file(filepath)
        logger.info(f"状态已保存到 {filepath}")
    
    def check_input_files(self, insight_dir: str, media_dir: str, query_dir: str, forum_log_path: str) -> Dict[str, Any]:
        """
        检查输入Arquivo是否准备就绪（基于Numero de arquivos量增加）。
        
        Args:
            insight_dir: InsightEnginerelatorioSumario
            media_dir: MediaEnginerelatorioSumario
            query_dir: QueryEnginerelatorioSumario
            forum_log_path: logs do forumArquivoCaminho
            
        Returns:
            检查结果字典，包含Arquivo计数、缺失列表、最新ArquivoCaminho等
        """
        # 检查各个relatorioSumario的Numero de arquivos量变化
        directories = {
            'insight': insight_dir,
            'media': media_dir,
            'query': query_dir
        }
        
        # 使用arquivo de linha de base管理器检查新Arquivo
        check_result = self.file_baseline.check_new_files(directories)
        
        # 检查logs do forum
        forum_ready = os.path.exists(forum_log_path)
        
        # 构建返回结果
        result = {
            'ready': check_result['ready'] and forum_ready,
            'baseline_counts': check_result['baseline_counts'],
            'current_counts': check_result['current_counts'],
            'new_files_found': check_result['new_files_found'],
            'missing_files': [],
            'files_found': [],
            'latest_files': {}
        }
        
        # 构建详细信息
        for engine, new_count in check_result['new_files_found'].items():
            current_count = check_result['current_counts'][engine]
            baseline_count = check_result['baseline_counts'].get(engine, 0)
            
            if new_count > 0:
                result['files_found'].append(f"{engine}: {current_count} arquivo(s) (novo(s){new_count}个)")
            else:
                result['missing_files'].append(f"{engine}: {current_count} arquivo(s) (linha de base{baseline_count}个，sem novos)")
        
        # 检查logs do forum
        if forum_ready:
            result['files_found'].append(f"forum: {os.path.basename(forum_log_path)}")
        else:
            result['missing_files'].append("forum: Arquivo de log nao existe")
        
        # 获取最新ArquivoCaminho（用于实际relatorio生成）
        if result['ready']:
            result['latest_files'] = self.file_baseline.get_latest_files(directories)
            if forum_ready:
                result['latest_files']['forum'] = forum_log_path
        
        return result
    
    def load_input_files(self, file_paths: Dict[str, str]) -> Dict[str, Any]:
        """
        加载输入Arquivo内容
        
        Args:
            file_paths: ArquivoCaminho字典
            
        Returns:
            加载的内容字典，包含 `reports` 列表与 `forum_logs` string
        """
        content = {
            'reports': [],
            'forum_logs': ''
        }
        
        # 加载arquivo de relatorio
        engines = ['query', 'media', 'insight']
        for engine in engines:
            if engine in file_paths:
                try:
                    with open(file_paths[engine], 'r', encoding='utf-8') as f:
                        report_content = f.read()
                    content['reports'].append(report_content)
                    logger.info(f"Carregado {engine} relatorio: {len(report_content)} caracteres")
                except Exception as e:
                    logger.exception(f"Carregar {engine} relatorio falhou: {str(e)}")
                    content['reports'].append("")
        
        # 加载logs do forum
        if 'forum' in file_paths:
            try:
                with open(file_paths['forum'], 'r', encoding='utf-8') as f:
                    content['forum_logs'] = f.read()
                logger.info(f"已加载logs do forum: {len(content['forum_logs'])} caracteres")
            except Exception as e:
                logger.exception(f"Falha ao carregar logs do forum: {str(e)}")
        
        return content


def create_agent(config_file: Optional[str] = None) -> ReportAgent:
    """
    Funcao conveniente para criar instancia do Report Agent.
    
    Args:
        config_file: 配置ArquivoCaminho
        
    Returns:
        ReportAgent实例

    目前以环境变量驱动 `Settings`，保留 `config_file` 参数便于未来扩展。
    """
    
    config = Settings() # Inicializar com configuracao vazia, em vez de inicializar a partir de variaveis de ambiente
    return ReportAgent(config)
