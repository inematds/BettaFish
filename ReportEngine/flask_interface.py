"""
Interface Flask do Report Engine.

Este modulo fornece um ponto de entrada HTTP/SSE unificado para o frontend/CLI, responsavel por:
1. Inicializar o ReportAgent e encadear threads de segundo plano;
2. Gerenciar fila de tarefas, consulta de progresso, envio por streaming e download de logs;
3. 提供模板列表、输入Arquivo检查等周边能力。
"""

import os
import json
import threading
import time
from collections import deque, defaultdict
from datetime import datetime
from pathlib import Path
from queue import Queue, Empty
from flask import Blueprint, request, jsonify, Response, send_file, stream_with_context
from typing import Dict, Any, List, Optional
from loguru import logger
from .agent import ReportAgent, create_agent
from .nodes import ChapterJsonParseError
from .utils.config import settings


# Criar Blueprint
report_bp = Blueprint('report_engine', __name__)

# Variaveis globais
report_agent = None
current_task = None
task_lock = threading.Lock()

# ====== 流式推送与任务历史管理 ======
# 通过有界deque缓存最近的事件，方便SSE断线后快速补发
MAX_TASK_HISTORY = 5
STREAM_HEARTBEAT_INTERVAL = 15  # 心跳间隔秒
STREAM_IDLE_TIMEOUT = 120  # 终态后最长保活时间，避免孤儿SSE阻塞
STREAM_TERMINAL_STATUSES = {"completed", "error", "cancelled"}
stream_lock = threading.Lock()
stream_subscribers = defaultdict(list)
tasks_registry: Dict[str, 'ReportTask'] = {}
LOG_STREAM_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
log_stream_handler_id: Optional[int] = None

EXCLUDED_ENGINE_PATH_KEYWORDS = ("ForumEngine", "InsightEngine", "MediaEngine", "QueryEngine")

def _is_excluded_engine_log(record: Dict[str, Any]) -> bool:
    """
    判断日志是否来自其他引擎（Insight/Media/Query/Forum），用于过滤混入的日志。

    Retorna:
        bool: True 表示应当过滤（即不写入/不转发）。
    """
    try:
        file_path = record["file"].path
        if any(keyword in file_path for keyword in EXCLUDED_ENGINE_PATH_KEYWORDS):
            return True
    except Exception:
        pass

    # 兜底：尝试按模块名过滤，防止file信息缺失时误混入
    try:
        module_name = record.get("module", "")
        if isinstance(module_name, str):
            lowered = module_name.lower()
            return any(keyword.lower() in lowered for keyword in EXCLUDED_ENGINE_PATH_KEYWORDS)
    except Exception:
        pass

    return False


def _stream_log_to_task(message):
    """
    将loguru日志同步到当前任务的SSE事件，保证前端实时可见。

    仅在存在运行中的任务时推送，避免无关日志刷屏。
    """
    try:
        record = message.record
        level_name = record["level"].name
        if level_name not in LOG_STREAM_LEVELS:
            return
        if _is_excluded_engine_log(record):
            return

        with task_lock:
            task = current_task

        if not task or task.status not in ("running", "pending"):
            return

        timestamp = record["time"].strftime("%H:%M:%S.%f")[:-3]
        formatted_line = f"[{timestamp}] [{level_name}] {record['message']}"
        task.publish_event(
            "log",
            {
                "line": formatted_line,
                "level": level_name.lower(),
                "timestamp": timestamp,
                "message": record["message"],
                "module": record.get("module", ""),
                "function": record.get("function", ""),
            },
        )
    except Exception:
        # Evitar recursao de log dentro do hook de log
        pass


def _setup_log_stream_forwarder():
    """为当前进程挂载一次性的loguru钩子，用于SSE实时转发。"""
    global log_stream_handler_id
    if log_stream_handler_id is not None:
        return
    log_stream_handler_id = logger.add(
        _stream_log_to_task,
        level="DEBUG",
        enqueue=False,
        catch=True,
    )


def _register_stream(task_id: str) -> Queue:
    """
    为指定任务注册一个事件队列，供SSE监听器消费。

    返回的 Queue 会存入 `stream_subscribers`，SSE 生成器将不断读取。

    Parametros:
        task_id: 需要监听的ID da tarefa.

    Retorna:
        Queue: 线程安全的事件队列。
    """
    queue = Queue()
    with stream_lock:
        stream_subscribers[task_id].append(queue)
    return queue


def _unregister_stream(task_id: str, queue: Queue):
    """
    安全移除事件队列，避免内存泄漏。

    需要在finally中调用，保证异常情况下资源也能释放。

    Parametros:
        task_id: ID da tarefa.
        queue: 之前注册的事件队列。
    """
    with stream_lock:
        listeners = stream_subscribers.get(task_id, [])
        if queue in listeners:
            listeners.remove(queue)
        if not listeners and task_id in stream_subscribers:
            stream_subscribers.pop(task_id, None)


def _broadcast_event(task_id: str, event: Dict[str, Any]):
    """
    将事件推送给所有监听者，失败时做好异常捕获。

    采用浅拷贝监听列表，防止并发移除导致遍历异常。

    Parametros:
        task_id: 待推送的ID da tarefa.
        event: 结构化事件payload。
    """
    with stream_lock:
        listeners = list(stream_subscribers.get(task_id, []))
    for queue in listeners:
        try:
            queue.put(event, timeout=0.1)
        except Exception:
            logger.exception("Falha ao enviar evento de streaming, ignorando fila de escuta atual")


def _prune_task_history_locked():
    """
    在task_lock持有期间调用，清理过多的历史任务。

    仅保留最近 `MAX_TASK_HISTORY` 个任务，避免长时间运行占用过多内存。

    Descricao:
        该函数假设调用方已获取 `task_lock`，否则存在竞态风险。
    """
    if len(tasks_registry) <= MAX_TASK_HISTORY:
        return
    # 按创建时间排序，移除最旧的任务
    sorted_tasks = sorted(tasks_registry.values(), key=lambda t: t.created_at)
    for task in sorted_tasks[:-MAX_TASK_HISTORY]:
        tasks_registry.pop(task.task_id, None)


def _get_task(task_id: str) -> Optional['ReportTask']:
    """
    统一的任务查找方法，优先返回当前任务。

    避免重复写锁逻辑，便于多个API共享。

    Parametros:
        task_id: ID da tarefa.

    Retorna:
        ReportTask | None: 命中时返回任务实例，否则为None。
    """
    with task_lock:
        if current_task and current_task.task_id == task_id:
            return current_task
        return tasks_registry.get(task_id)


def _format_sse(event: Dict[str, Any]) -> str:
    """
    按SSE协议格式化消息。

    输出形如 `id:/event:/data:` 的三段文本，供浏览器端直接消费。

    Parametros:
        event: 事件payload，至少包含 id/type。

    Retorna:
        str: SSE协议要求的string。
    """
    payload = json.dumps(event, ensure_ascii=False)
    event_id = event.get('id', 0)
    event_type = event.get('type', 'message')
    return f"id: {event_id}\nevent: {event_type}\ndata: {payload}\n\n"


def _safe_filename_segment(value: str, fallback: str = "report") -> str:
    """
    生成可用于Arquivo名的安全片段，保留字母数字与常见分隔符。

    Parametros:
        value: 原始string。
        fallback: 兜底文本，当value为空或清洗后为空时使用。
    """
    sanitized = "".join(c for c in str(value) if c.isalnum() or c in (" ", "-", "_")).strip()
    sanitized = sanitized.replace(" ", "_")
    return sanitized or fallback


def initialize_report_engine():
    """
    Inicializar Report Engine.

    Instanciar ReportAgent como singleton, permitindo receber tarefas diretamente apos inicio da API.

    Retorna:
        bool: 初始化成功返回True，异常时返回False。
    """
    global report_agent
    try:
        report_agent = create_agent()
        logger.info("Report Engine inicializado com sucesso")
        _setup_log_stream_forwarder()

        # 检测 PDF 生成依赖（Pango）
        try:
            from .utils.dependency_check import log_dependency_status
            log_dependency_status()
        except Exception as dep_err:
            logger.warning(f"Falha na detecao de dependencias: {dep_err}")

        return True
    except Exception as e:
        logger.exception(f"Falha ao inicializar o Report Engine: {str(e)}")
        return False


class ReportTask:
    """
    relatorio生成任务。

    该对象串联运行状态、进度、事件历史及最终ArquivoCaminho，
    既供后台线程更新，也供HTTP接口读取。
    """

    def __init__(self, query: str, task_id: str, custom_template: str = ""):
        """
        初始化任务对象，记录查询词、自定义模板与运行期元数据。

        Args:
            query: 最终需要生成的relatorio主题
            task_id: 任务唯一ID，通常由时间戳构造
            custom_template: 可选的自定义Markdown模板
        """
        self.task_id = task_id
        self.query = query
        self.custom_template = custom_template
        self.status = "pending"  # 四种状态（pending/running/completed/error）
        self.progress = 0
        self.result = None
        self.error_message = ""
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
        self.html_content = ""
        self.report_file_path = ""
        self.report_file_relative_path = ""
        self.report_file_name = ""
        self.state_file_path = ""
        self.state_file_relative_path = ""
        self.ir_file_path = ""
        self.ir_file_relative_path = ""
        self.markdown_file_path = ""
        self.markdown_file_relative_path = ""
        self.markdown_file_name = ""
        # ====== 流式事件缓存与并发保护 ======
        # 使用deque保存最近的事件，结合锁保证多线程下的安全访问
        self.event_history: deque = deque(maxlen=1000)
        self._event_lock = threading.Lock()
        self.last_event_id = 0

    def update_status(self, status: str, progress: int = None, error_message: str = ""):
        """
        更新任务状态并广播事件。

        会自动刷新 `updated_at`、Erro(s)信息，并触发 `status` 类型的 SSE。

        Parametros:
            status: 任务阶段（pending/running/completed/error/cancelled）。
            progress: 可选的进度百分比。
            error_message: 出错时的人类可读说明。
        """
        self.status = status
        if progress is not None:
            self.progress = progress
        if error_message:
            self.error_message = error_message
        self.updated_at = datetime.now()
        # 推送状态变更事件，方便前端实时刷新
        self.publish_event(
            'status',
            {
                'status': self.status,
                'progress': self.progress,
                'error_message': self.error_message,
                'hint': error_message or '',
                'task': self.to_dict(),
            }
        )

    def to_dict(self) -> Dict[str, Any]:
        """Converter para formato de dicionario，方便直接返回给JSON API。"""
        return {
            'task_id': self.task_id,
            'query': self.query,
            'status': self.status,
            'progress': self.progress,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'has_result': bool(self.html_content),
            'report_file_ready': bool(self.report_file_path),
            'report_file_name': self.report_file_name,
            'report_file_path': self.report_file_relative_path or self.report_file_path,
            'state_file_ready': bool(self.state_file_path),
            'state_file_path': self.state_file_relative_path or self.state_file_path,
            'ir_file_ready': bool(self.ir_file_path),
            'ir_file_path': self.ir_file_relative_path or self.ir_file_path,
            'markdown_file_ready': bool(self.markdown_file_path),
            'markdown_file_name': self.markdown_file_name,
            'markdown_file_path': self.markdown_file_relative_path or self.markdown_file_path
        }

    def publish_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """
        将任意事件放入缓存并广播，所有novo(s)逻辑均配套中文说明。

        Parametros:
            event_type: SSE中的event名称。
            payload: 实际业务数据。
        """
        timestamp = datetime.utcnow().isoformat() + 'Z'
        event: Dict[str, Any] = {
            'id': 0,
            'type': event_type,
            'task_id': self.task_id,
            'timestamp': timestamp,
            'payload': payload,
        }
        with self._event_lock:
            self.last_event_id += 1
            event['id'] = self.last_event_id
            self.event_history.append(event)
        _broadcast_event(self.task_id, event)

    def history_since(self, last_event_id: Optional[int]) -> List[Dict[str, Any]]:
        """
        根据Last-Event-ID补发历史事件，确保断线重连无遗漏。

        Parametros:
            last_event_id: SSE客户端记录的最后一个事件ID。

        Retorna:
            list[dict]: 从 last_event_id 之后的事件列表。
        """
        with self._event_lock:
            if last_event_id is None:
                return list(self.event_history)
            return [evt for evt in self.event_history if evt['id'] > last_event_id]


def check_engines_ready() -> Dict[str, Any]:
    """
    检查三个子引擎是否都有新Arquivo。

    调用 ReportAgent 的linha de base检测逻辑，并附带logs do forum存在性，
    是 /status、/generate 的前置校验。
    """
    directories = {
        'insight': 'insight_engine_streamlit_reports',
        'media': 'media_engine_streamlit_reports',
        'query': 'query_engine_streamlit_reports'
    }

    forum_log_path = 'logs/forum.log'

    if not report_agent:
        return {
            'ready': False,
            'error': 'Report Engine nao inicializado'
        }

    return report_agent.check_input_files(
        directories['insight'],
        directories['media'],
        directories['query'],
        forum_log_path
    )


def run_report_generation(task: ReportTask, query: str, custom_template: str = ""):
    """
    在后台线程中运行relatorio生成。

    包括：检查输入→加载文档→调用ReportAgent→持久化输出→
    推送阶段性事件。出现Erro(s)会自动推送并写状态。

    Parametros:
        task: 本次任务对象，内部持有事件队列。
        query: relatorio主题。
        custom_template: 可选的自定义模板string。
    """
    global current_task

    try:
        # Encapsular logica de push em closure local, facilitando passagem ao ReportAgent
        def stream_handler(event_type: str, payload: Dict[str, Any]):
            """Todos os eventos de etapa sao distribuidos pela mesma interface, garantindo consistencia de logs."""
            task.publish_event(event_type, payload)
            # Se o evento contiver informacoes de progresso, atualizar progresso da tarefa sincronamente
            if event_type == 'progress' and 'progress' in payload:
                task.update_status("running", payload['progress'])

        task.update_status("running", 5)
        task.publish_event('stage', {'message': 'Tarefa iniciada, verificando arquivos de entrada', 'stage': 'prepare'})

        # 检查输入Arquivo
        check_result = check_engines_ready()
        if not check_result['ready']:
            task.update_status("error", 0, f"Arquivos de entrada nao estao prontos: {check_result.get('missing_files', [])}")
            return

        task.publish_event('stage', {
            'message': 'Verificacao de arquivos aprovada, preparando para carregar conteudo',
            'stage': 'io_ready',
            'files': check_result.get('latest_files', {})
        })

        # 加载输入Arquivo
        content = report_agent.load_input_files(check_result['latest_files'])
        task.publish_event('stage', {'message': 'Dados de origem carregados, iniciando processo de geracao', 'stage': 'data_loaded'})

        # 生成relatorio（附带兜底重试，缓解瞬时网络抖动）
        for attempt in range(1, 3):
            try:
                task.publish_event('stage', {
                    'message': f'正在调用ReportAgent生成relatorio（第{attempt}次尝试）',
                    'stage': 'agent_running',
                    'attempt': attempt
                })
                generation_result = report_agent.generate_report(
                    query=query,
                    reports=content['reports'],
                    forum_logs=content['forum_logs'],
                    custom_template=custom_template,
                    save_report=True,
                    stream_handler=stream_handler
                )
                break
            except ChapterJsonParseError as err:
                hint_message = "尝试将Report Engine的API更换为算力更强、上下文更长的LLM"
                task.publish_event('warning', {
                    'message': hint_message,
                    'stage': 'agent_running',
                    'attempt': attempt,
                    'reason': 'chapter_json_parse',
                    'error': str(err),
                    'task': task.to_dict(),
                })
                # 旧逻辑：在Falha na analise JSON后重启Report Engine
                # backoff = min(5 * attempt, 15)
                # task.publish_event('stage', {
                #     'message': f'{backoff} 秒后重试生成任务',
                #     'stage': 'retry_wait',
                #     'wait_seconds': backoff
                # })
                # time.sleep(backoff)
                raise ChapterJsonParseError(hint_message) from err
            except Exception as err:
                # 将Erro(s)即时推送至前端，方便观察重试策略
                task.publish_event('warning', {
                    'message': f'ReportAgent执行失败: {str(err)}',
                    'stage': 'agent_running',
                    'attempt': attempt
                })
                if attempt == 2:
                    raise
                # Backoff exponencial simples para evitar acionar limites de frequencia (em segundos)
                backoff = min(5 * attempt, 15)
                task.publish_event('stage', {
                    'message': f'{backoff} 秒后重试生成任务',
                    'stage': 'retry_wait',
                    'wait_seconds': backoff
                })
                time.sleep(backoff)

        if isinstance(generation_result, dict):
            html_report = generation_result.get('html_content', '')
        else:
            html_report = generation_result

        task.publish_event('stage', {'message': 'Geracao do relatorio concluida, preparando persistencia', 'stage': 'persist'})

        # Salvar resultado
        task.html_content = html_report
        if isinstance(generation_result, dict):
            task.report_file_path = generation_result.get('report_filepath', '')
            task.report_file_relative_path = generation_result.get('report_relative_path', '')
            task.report_file_name = generation_result.get('report_filename', '')
            task.state_file_path = generation_result.get('state_filepath', '')
            task.state_file_relative_path = generation_result.get('state_relative_path', '')
            task.ir_file_path = generation_result.get('ir_filepath', '')
            task.ir_file_relative_path = generation_result.get('ir_relative_path', '')
        task.publish_event('html_ready', {
            'message': 'Renderizacao HTML concluida, atualize para visualizar',
            'report_file': task.report_file_relative_path or task.report_file_path,
            'state_file': task.state_file_relative_path or task.state_file_path,
            'task': task.to_dict(),
        })
        task.update_status("completed", 100)
        task.publish_event('completed', {
            'message': 'Tarefa concluida',
            'duration_seconds': (task.updated_at - task.created_at).total_seconds(),
            'report_file': task.report_file_relative_path or task.report_file_path,
            'task': task.to_dict(),
        })

    except Exception as e:
        logger.exception(f"Erro durante a geracao do relatorio: {str(e)}")
        task.update_status("error", 0, str(e))
        task.publish_event('error', {
            'message': str(e),
            'stage': 'failed',
            'task': task.to_dict(),
        })
        # Limpar tarefa apenas em caso de erro
        with task_lock:
            if current_task and current_task.task_id == task.task_id:
                current_task = None


@report_bp.route('/status', methods=['GET'])
def get_status():
    """
    Obter status do Report Engine, incluindo estado de prontidao dos motores e informacoes da tarefa atual.

    Retorna:
        Response: JSON结构包含initialized/engines_ready/当前任务等。
    """
    try:
        engines_status = check_engines_ready()

        return jsonify({
            'success': True,
            'initialized': report_agent is not None,
            'engines_ready': engines_status['ready'],
            'files_found': engines_status.get('files_found', []),
            'missing_files': engines_status.get('missing_files', []),
            'current_task': current_task.to_dict() if current_task else None
        })
    except Exception as e:
        logger.exception(f"Falha ao obter status do Report Engine: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@report_bp.route('/generate', methods=['POST'])
def generate_report():
    """
    Iniciando geracao do relatorio。

    负责排队、创建后台线程、清空日志并返回SSE地址。

    请求体:
        query: relatorio主题（可选）。
        custom_template: 自定义模板string（可选）。

    Retorna:
        Response: JSON，包含 task_id 与 SSE stream url。
    """
    global current_task

    try:
        # Verificar se ha tarefa em execucao
        with task_lock:
            if current_task and current_task.status == "running":
                return jsonify({
                    'success': False,
                    'error': 'Ja existe uma tarefa de geracao de relatorio em execucao',
                    'current_task': current_task.to_dict()
                }), 400

            # Se houver tarefa concluida, limpa-la
            if current_task and current_task.status in ["completed", "error"]:
                current_task = None

        # Obter parametros da requisicao
        data = request.get_json() or {}
        if not isinstance(data, dict):
            logger.warning("generate_report 接收到非对象JSON负载，已忽略原始内容")
            data = {}
        query = data.get('query', 'Relatorio inteligente de analise de opiniao publica')
        custom_template = data.get('custom_template', '')

        # 清空日志Arquivo
        clear_report_log()

        # Verificar se o Report Engine esta inicializado
        if not report_agent:
            return jsonify({
                'success': False,
                'error': 'Report Engine nao inicializado'
            }), 500

        # 检查输入Arquivo是否准备就绪
        engines_status = check_engines_ready()
        if not engines_status['ready']:
            return jsonify({
                'success': False,
                'error': 'Arquivos de entrada nao estao prontos',
                'missing_files': engines_status.get('missing_files', [])
            }), 400

        # Criar nova tarefa
        task_id = f"report_{int(time.time())}"
        task = ReportTask(query, task_id, custom_template)

        with task_lock:
            current_task = task
            tasks_registry[task_id] = task
            _prune_task_history_locked()

        # Notificar o frontend de que a tarefa foi enfileirada via push ativo de evento pending
        task.publish_event(
            'status',
            {
                'status': task.status,
                'progress': task.progress,
                'message': 'Tarefa enfileirada, aguardando recursos disponiveis',
                'task': task.to_dict(),
            }
        )

        # 在后台线程中运行relatorio生成
        thread = threading.Thread(
            target=run_report_generation,
            args=(task, query, custom_template),
            daemon=True
        )
        thread.start()

        return jsonify({
            'success': True,
            'task_id': task_id,
            'message': 'Geracao do relatorio iniciada',
            'task': task.to_dict(),
            'stream_url': f"/api/report/stream/{task_id}"
        })

    except Exception as e:
        logger.exception(f"Falha ao iniciar geracao do relatorio: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@report_bp.route('/progress/<task_id>', methods=['GET'])
def get_progress(task_id: str):
    """
    获取relatorio生成进度，若任务被清理则返回一个完成态兜底。

    Parametros:
        task_id: 任务唯一标识。

    Retorna:
        Response: JSON包含任务Estado atual。
    """
    try:
        task = _get_task(task_id)
        if not task:
            # 如果Tarefa nao encontrada，可能是历史记录已被清理，回传一个完成态兜底
            return jsonify({
                'success': True,
                'task': {
                    'task_id': task_id,
                    'status': 'completed',
                    'progress': 100,
                    'error_message': '',
                    'has_result': True,
                    'report_file_ready': False,
                    'report_file_name': '',
                    'report_file_path': '',
                    'state_file_ready': False,
                    'state_file_path': ''
                }
            })

        return jsonify({
            'success': True,
            'task': task.to_dict()
        })

    except Exception as e:
        logger.exception(f"Falha ao obter progresso da geracao do relatorio: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@report_bp.route('/stream/<task_id>', methods=['GET'])
def stream_task(task_id: str):
    """
    Interface de push em tempo real baseada em SSE.

    - 自动补发Last-Event-ID之后的历史事件；
    - 周期性发送心跳以防代理中断；
    - 任务结束后自动注销监听。

    Parametros:
        task_id: 任务唯一标识。

    Retorna:
        Response: `text/event-stream` 类型响应。
    """
    task = _get_task(task_id)
    if not task:
        return jsonify({'success': False, 'error': 'Tarefa nao encontrada'}), 404

    last_event_header = request.headers.get('Last-Event-ID')
    try:
        last_event_id = int(last_event_header) if last_event_header else None
    except ValueError:
        last_event_id = None

    def client_disconnected() -> bool:
        """
        尽早探测客户端是否已经断开，避免继续写入触发BrokenPipe。

        eventlet 在 Windows 上会在关闭连接时抛出 ConnectionAbortedError，
        提前退出生成器可以缩减无意义的日志。
        """
        try:
            env_input = request.environ.get('wsgi.input')
            return bool(getattr(env_input, 'closed', False))
        except Exception:
            return False

    def event_generator():
        """
        SSE事件生成器。

        - 负责注册并消费对应任务的事件队列；
        - 先回放历史事件再持续监听实时事件；
        - 周期性发送心跳并在任务结束后自动注销监听。
        """
        queue = _register_stream(task_id)
        last_data_ts = time.time()
        try:
            # 断线重连场景下，先补发历史事件，保证界面状态一致
            history = task.history_since(last_event_id)
            for event in history:
                yield _format_sse(event)
                if event.get('type') != 'heartbeat':
                    last_data_ts = time.time()

            finished = task.status in STREAM_TERMINAL_STATUSES
            while True:
                if finished:
                    break
                if client_disconnected():
                    logger.info(f"SSE客户端已断开，停止推送: {task_id}")
                    break
                event = None
                try:
                    event = queue.get(timeout=STREAM_HEARTBEAT_INTERVAL)
                except Empty:
                    if task.status in STREAM_TERMINAL_STATUSES:
                        logger.info(f"任务 {task_id} 已结束且无新事件，SSE自动收口")
                        break
                    heartbeat = {
                        'id': f"hb-{int(time.time() * 1000)}",
                        'type': 'heartbeat',
                        'task_id': task_id,
                        'timestamp': datetime.utcnow().isoformat() + 'Z',
                        'payload': {'status': task.status}
                    }
                    event = heartbeat
                if event is None:
                    logger.warning(f"SSE推送获取事件失败（task {task_id}），提前结束")
                    break

                try:
                    yield _format_sse(event)
                    if event.get('type') != 'heartbeat':
                        last_data_ts = time.time()
                except GeneratorExit:
                    logger.info(f"SSE生成器关闭，停止任务 {task_id} 推送")
                    break
                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as exc:
                    logger.warning(f"SSE连接被客户端中断（task {task_id}）: {exc}")
                    break
                except Exception as exc:
                    event_type = event.get('type') if isinstance(event, dict) else 'unknown'
                    logger.exception(f"SSE推送失败（task {task_id}, event {event_type}）: {exc}")
                    break

                if event.get('type') in ("completed", "error", "cancelled"):
                    finished = True
                else:
                    finished = finished or task.status in STREAM_TERMINAL_STATUSES

                # 终态下最多保活一段时间，防止前端早已结束但后台循环未退出
                if task.status in STREAM_TERMINAL_STATUSES:
                    idle_for = time.time() - last_data_ts
                    if idle_for > STREAM_IDLE_TIMEOUT:
                        logger.info(f"任务 {task_id} 已终态且空闲 {int(idle_for)}s，主动关闭SSE")
                        break
        finally:
            _unregister_stream(task_id, queue)

    response = Response(
        stream_with_context(event_generator()),
        mimetype='text/event-stream'
    )
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    return response


@report_bp.route('/result/<task_id>', methods=['GET'])
def get_result(task_id: str):
    """
    获取relatorio生成结果。

    Parametros:
        task_id: ID da tarefa.

    Retorna:
        Response: JSON，包含HTML预览与ArquivoCaminho。
    """
    try:
        task = _get_task(task_id)
        if not task:
            return jsonify({
                'success': False,
                'error': 'Tarefa nao encontrada'
            }), 404

        if task.status != "completed":
            return jsonify({
                'success': False,
                'error': 'Relatorio ainda nao concluido',
                'task': task.to_dict()
            }), 400

        return Response(
            task.html_content,
            mimetype='text/html'
        )

    except Exception as e:
        logger.exception(f"Falha ao obter resultado da geracao do relatorio: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@report_bp.route('/result/<task_id>/json', methods=['GET'])
def get_result_json(task_id: str):
    """获取relatorio生成结果（JSON格式）"""
    try:
        task = _get_task(task_id)
        if not task:
            return jsonify({
                'success': False,
                'error': 'Tarefa nao encontrada'
            }), 404

        if task.status != "completed":
            return jsonify({
                'success': False,
                'error': 'Relatorio ainda nao concluido',
                'task': task.to_dict()
            }), 400

        return jsonify({
            'success': True,
            'task': task.to_dict(),
            'html_content': task.html_content
        })

    except Exception as e:
        logger.exception(f"Falha ao obter resultado da geracao do relatorio: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@report_bp.route('/download/<task_id>', methods=['GET'])
def download_report(task_id: str):
    """
    下载已生成的relatorioHTMLArquivo。

    Parametros:
        task_id: ID da tarefa.

    Retorna:
        Response: HTMLArquivo的附件下载响应。
    """
    try:
        task = _get_task(task_id)
        if not task:
            return jsonify({
                'success': False,
                'error': 'Tarefa nao encontrada'
            }), 404

        if task.status != "completed" or not task.report_file_path:
            return jsonify({
                'success': False,
                'error': 'Relatorio ainda nao concluido ou nao salvo'
            }), 400

        if not os.path.exists(task.report_file_path):
            return jsonify({
                'success': False,
                'error': 'Arquivo do relatorio nao existe ou foi excluido'
            }), 404

        download_name = task.report_file_name or os.path.basename(task.report_file_path)
        return send_file(
            task.report_file_path,
            mimetype='text/html',
            as_attachment=True,
            download_name=download_name
        )

    except Exception as e:
        logger.exception(f"Falha ao baixar relatorio: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@report_bp.route('/cancel/<task_id>', methods=['POST'])
def cancel_task(task_id: str):
    """
    取消relatorio生成任务。

    Parametros:
        task_id: 需要被取消的ID da tarefa.

    Retorna:
        Response: JSON，包含取消结果或Erro(s)信息。
    """
    global current_task

    try:
        with task_lock:
            if current_task and current_task.task_id == task_id:
                if current_task.status == "running":
                    current_task.update_status("cancelled", 0, "Tarefa cancelada pelo usuario")
                    current_task.publish_event('cancelled', {
                        'message': 'Tarefa encerrada pelo usuario',
                        'task': current_task.to_dict(),
                    })
                current_task = None
            task = tasks_registry.get(task_id)
            if task and task.status == 'running':
                task.update_status("cancelled", task.progress, "Tarefa cancelada pelo usuario")
                task.publish_event('cancelled', {
                    'message': 'Tarefa encerrada pelo usuario',
                    'task': task.to_dict(),
                })

                return jsonify({
                    'success': True,
                    'message': 'Tarefa cancelada'
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'Tarefa nao existe ou nao pode ser cancelada'
                }), 404

    except Exception as e:
        logger.exception(f"Falha ao cancelar tarefa de geracao do relatorio: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@report_bp.route('/templates', methods=['GET'])
def get_templates():
    """
    Obter lista de templates disponiveis para exibir estruturas Markdown opcionais no frontend.

    Retorna:
        Response: JSON，列出模板名称/描述/大小。
    """
    try:
        if not report_agent:
            return jsonify({
                'success': False,
                'error': 'Report Engine nao inicializado'
            }), 500

        template_dir = settings.TEMPLATE_DIR
        templates = []

        if os.path.exists(template_dir):
            for filename in os.listdir(template_dir):
                if filename.endswith('.md'):
                    template_path = os.path.join(template_dir, filename)
                    try:
                        with open(template_path, 'r', encoding='utf-8') as f:
                            content = f.read()

                        templates.append({
                            'name': filename.replace('.md', ''),
                            'filename': filename,
                            'description': content.split('\n')[0] if content else 'Sem descricao',
                            'size': len(content)
                        })
                    except Exception as e:
                        logger.exception(f"Falha ao ler template {filename}: {str(e)}")

        return jsonify({
            'success': True,
            'templates': templates,
            'template_dir': template_dir
        })

    except Exception as e:
        logger.exception(f"Falha ao obter lista de templates disponiveis: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# Tratamento de erros
@report_bp.errorhandler(404)
def not_found(error):
    """Tratamento 404: garantir que a interface retorne estrutura JSON uniforme"""
    logger.exception(f"Endpoint da API nao encontrado: {str(error)}")
    return jsonify({
        'success': False,
        'error': 'Endpoint da API nao encontrado'
    }), 404


@report_bp.errorhandler(500)
def internal_error(error):
    """Tratamento 500: capturar excecoes nao capturadas ativamente"""
    logger.exception(f"Erro interno do servidor: {str(error)}")
    return jsonify({
        'success': False,
        'error': 'Erro interno do servidor'
    }), 500


def clear_report_log():
    """
    清空report.logArquivo，方便新任务只查看本次运行日志。

    Retorna:
        None
    """
    try:
        log_file = settings.LOG_FILE

        # [CORRECAO] 使用truncate而非重新打开，避免与logger的Arquivo句柄冲突
        # Modo de adição打开，然后truncate，保持Arquivo句柄有效
        with open(log_file, 'r+', encoding='utf-8') as f:
            f.truncate(0)  # 清空Arquivo内容但不关闭Arquivo
            f.flush()      # Flush imediato

        logger.info(f"Arquivo de log limpo: {log_file}")
    except FileNotFoundError:
        # Arquivo nao existe，创建空Arquivo
        try:
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write('')
            logger.info(f"Criando arquivo de log: {log_file}")
        except Exception as e:
            logger.exception(f"Falha ao criar arquivo de log: {str(e)}")
    except Exception as e:
        logger.exception(f"Falha ao limpar arquivo de log: {str(e)}")


@report_bp.route('/log', methods=['GET'])
def get_report_log():
    """
    Obter conteudo do report.log e retornar por linha sem espacos em branco.

    【修复】优化大Arquivo读取，添加Erro(s)处理和Arquivo锁

    Retorna:
        Response: JSON，包含最新日志行数组。
    """
    try:
        log_file = settings.LOG_FILE

        if not os.path.exists(log_file):
            return jsonify({
                'success': True,
                'log_lines': []
            })

        # [CORRECAO] 检查Arquivo大小，避免读取过大Arquivo导致内存问题
        file_size = os.path.getsize(log_file)
        max_size = 10 * 1024 * 1024  # 10MB限制

        if file_size > max_size:
            # Arquivo过大，只读取最后10MB
            with open(log_file, 'rb') as f:
                f.seek(-max_size, 2)  # 从Arquivo末尾往前10MB
                # 跳过可能不完整的第一行
                f.readline()
                content = f.read().decode('utf-8', errors='replace')
            lines = content.splitlines()
            logger.warning(f"Arquivo de log muito grande ({file_size} bytes)，retornando apenas os ultimos {max_size} bytes")
        else:
            # 正常大小，完整读取
            with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()

        # 清理行尾的换行符和空行
        log_lines = [line.rstrip('\n\r') for line in lines if line.strip()]

        return jsonify({
            'success': True,
            'log_lines': log_lines
        })

    except PermissionError as e:
        logger.error(f"Permissao insuficiente para ler logs: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Permissao insuficiente para ler logs'
        }), 403
    except UnicodeDecodeError as e:
        logger.error(f"Erro de codificacao do arquivo de log: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Erro de codificacao do arquivo de log'
        }), 500
    except Exception as e:
        logger.exception(f"Falha ao ler logs: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Falha ao ler logs: {str(e)}'
        }), 500


@report_bp.route('/log/clear', methods=['POST'])
def clear_log():
    """
    Limpar logs manualmente, fornecendo endpoint REST para reset pelo frontend.

    Retorna:
        Response: JSON，标记是否清理成功。
    """
    try:
        clear_report_log()
        return jsonify({
            'success': True,
            'message': 'Logs limpos'
        })
    except Exception as e:
        logger.exception(f"Falha ao limpar logs: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Falha ao limpar logs: {str(e)}'
        }), 500


@report_bp.route('/export/md/<task_id>', methods=['GET'])
def export_markdown(task_id: str):
    """
    导出relatorio为 Markdown 格式。

    基于已保存的 Document IR 调用 MarkdownRenderer，生成Arquivo并返回下载。
    """
    try:
        task = tasks_registry.get(task_id)
        if not task:
            return jsonify({
                'success': False,
                'error': 'Tarefa nao encontrada'
            }), 404

        if task.status != 'completed':
            return jsonify({
                'success': False,
                'error': f'任务未完成，Estado atual: {task.status}'
            }), 400

        if not task.ir_file_path or not os.path.exists(task.ir_file_path):
            return jsonify({
                'success': False,
                'error': 'IRArquivo nao existe，无法生成Markdown'
            }), 404

        with open(task.ir_file_path, 'r', encoding='utf-8') as f:
            document_ir = json.load(f)

        from .renderers import MarkdownRenderer
        renderer = MarkdownRenderer()
        # 传入 ir_file_path，修复后的图表会自动保存到 IR Arquivo
        markdown_text = renderer.render(document_ir, ir_file_path=task.ir_file_path)

        metadata = document_ir.get('metadata') if isinstance(document_ir, dict) else {}
        topic = (metadata or {}).get('topic') or (metadata or {}).get('title') or (metadata or {}).get('query') or task.query
        safe_topic = _safe_filename_segment(topic or 'report')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"report_{safe_topic}_{timestamp}.md"

        output_dir = Path(settings.OUTPUT_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)
        md_path = output_dir / filename
        md_path.write_text(markdown_text, encoding='utf-8')

        task.markdown_file_path = str(md_path.resolve())
        task.markdown_file_relative_path = os.path.relpath(task.markdown_file_path, os.getcwd())
        task.markdown_file_name = filename

        logger.info(f"Exportacao para Markdown concluida: {md_path}")

        return send_file(
            task.markdown_file_path,
            mimetype='text/markdown',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.exception(f"Falha ao exportar Markdown: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Falha ao exportar Markdown: {str(e)}'
        }), 500


@report_bp.route('/export/pdf/<task_id>', methods=['GET'])
def export_pdf(task_id: str):
    """
    导出relatorio为PDF格式。

    从IR JSONArquivo生成优化的PDF，支持自动布局调整。

    Parametros:
        task_id: 任务ID

    查询Parametros:
        optimize: 是否启用布局优化（默认true）

    Retorna:
        Response: PDFArquivo流或Erro(s)信息
    """
    try:
        # 检测 Pango 依赖
        from .utils.dependency_check import check_pango_available
        pango_available, pango_message = check_pango_available()
        if not pango_available:
            return jsonify({
                'success': False,
                'error': 'Funcao de exportacao de PDF indisponivel: dependencias do sistema ausentes',
                'details': '请查看根Sumario README.md “源码启动”的第二步（PDF 导出依赖）了解安装方法',
                'help_url': 'https://github.com/666ghj/BettaFish#2-安装-pdf-导出所需系统依赖可选',
                'system_message': pango_message
            }), 503

        # 获取任务信息
        task = tasks_registry.get(task_id)
        if not task:
            return jsonify({
                'success': False,
                'error': 'Tarefa nao encontrada'
            }), 404

        # 检查任务是否完成
        if task.status != 'completed':
            return jsonify({
                'success': False,
                'error': f'任务未完成，Estado atual: {task.status}'
            }), 400

        # 获取IRArquivoCaminho
        if not task.ir_file_path or not os.path.exists(task.ir_file_path):
            return jsonify({
                'success': False,
                'error': 'IRArquivo nao existe'
            }), 404

        # Ler dados do IR
        with open(task.ir_file_path, 'r', encoding='utf-8') as f:
            document_ir = json.load(f)

        # 检查是否启用布局优化
        optimize = request.args.get('optimize', 'true').lower() == 'true'

        # 创建PDF渲染器并生成PDF
        from .renderers import PDFRenderer
        renderer = PDFRenderer()

        logger.info(f"开始导出PDF，任务ID: {task_id}，布局优化: {optimize}")

        # Gerar PDF字节流
        pdf_bytes = renderer.render_to_bytes(document_ir, optimize_layout=optimize)

        # 确定下载Arquivo名
        topic = document_ir.get('metadata', {}).get('topic', 'report')
        pdf_filename = f"report_{topic}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

        # 返回PDFArquivo
        return Response(
            pdf_bytes,
            mimetype='application/pdf',
            headers={
                'Content-Disposition': f'attachment; filename="{pdf_filename}"',
                'Content-Type': 'application/pdf'
            }
        )

    except Exception as e:
        logger.exception(f"Falha ao exportar PDF: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Falha ao exportar PDF: {str(e)}'
        }), 500


@report_bp.route('/export/pdf-from-ir', methods=['POST'])
def export_pdf_from_ir():
    """
    Exportar PDF diretamente do IR JSON (sem necessidade de ID de tarefa).

    适用于前端直接传递IR数据的场景。

    请求体:
        {
            "document_ir": {...},  // Document IR JSON
            "optimize": true       // 是否启用布局优化（可选）
        }

    Retorna:
        Response: PDFArquivo流或Erro(s)信息
    """
    try:
        # 检测 Pango 依赖
        from .utils.dependency_check import check_pango_available
        pango_available, pango_message = check_pango_available()
        if not pango_available:
            return jsonify({
                'success': False,
                'error': 'Funcao de exportacao de PDF indisponivel: dependencias do sistema ausentes',
                'details': '请查看根Sumario README.md “源码启动”的第二步（PDF 导出依赖）了解安装方法',
                'help_url': 'https://github.com/666ghj/BettaFish#2-安装-pdf-导出所需系统依赖可选',
                'system_message': pango_message
            }), 503

        data = request.get_json() or {}
        if not isinstance(data, dict):
            logger.warning("export_pdf_from_ir 请求体不是JSON对象")
            return jsonify({
                'success': False,
                'error': 'O corpo da requisicao deve ser um objeto JSON'
            }), 400

        if not data or 'document_ir' not in data:
            return jsonify({
                'success': False,
                'error': 'Parametro document_ir ausente'
            }), 400

        document_ir = data['document_ir']
        optimize = data.get('optimize', True)

        # 创建PDF渲染器并生成PDF
        from .renderers import PDFRenderer
        renderer = PDFRenderer()

        logger.info(f"Exportando PDF diretamente do IR, otimizacao de layout: {optimize}")

        # Gerar PDF字节流
        pdf_bytes = renderer.render_to_bytes(document_ir, optimize_layout=optimize)

        # 确定下载Arquivo名
        topic = document_ir.get('metadata', {}).get('topic', 'report')
        pdf_filename = f"report_{topic}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

        # 返回PDFArquivo
        return Response(
            pdf_bytes,
            mimetype='application/pdf',
            headers={
                'Content-Disposition': f'attachment; filename="{pdf_filename}"',
                'Content-Type': 'application/pdf'
            }
        )

    except Exception as e:
        logger.exception(f"Falha ao exportar PDF a partir do IR: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Falha ao exportar PDF: {str(e)}'
        }), 500
