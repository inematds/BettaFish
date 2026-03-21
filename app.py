"""
Aplicação principal Flask - Gerenciamento unificado dos três aplicativos Streamlit
"""

import os
import sys

# [Correção] Definir variáveis de ambiente o mais cedo possível para garantir que todos os módulos usem modo sem buffer
os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['PYTHONUTF8'] = '1'
os.environ['PYTHONUNBUFFERED'] = '1'  # Desabilitar buffer de saída do Python para garantir logs em tempo real

import subprocess
import time
import threading
from datetime import datetime
from queue import Queue
from flask import Flask, render_template, request, jsonify, Response
from flask_socketio import SocketIO, emit
import atexit
import requests
from loguru import logger
import importlib
from pathlib import Path
from MindSpider.main import MindSpider

# Importar ReportEngine
try:
    from ReportEngine.flask_interface import report_bp, initialize_report_engine
    REPORT_ENGINE_AVAILABLE = True
except ImportError as e:
    logger.error(f"Falha ao importar ReportEngine: {e}")
    REPORT_ENGINE_AVAILABLE = False

app = Flask(__name__)
app.config['SECRET_KEY'] = 'Dedicated-to-creating-a-concise-and-versatile-public-opinion-analysis-platform'
socketio = SocketIO(app, cors_allowed_origins="*")

# O eventlet ocasionalmente lança ConnectionAbortedError quando o cliente desconecta ativamente.
# Aqui fazemos um wrapper defensivo para evitar poluição desnecessária do log com stack traces
# (ativado apenas quando o eventlet está disponível).
def _patch_eventlet_disconnect_logging():
    try:
        import eventlet.wsgi  # type: ignore
    except Exception as exc:  # pragma: no cover - efetivo apenas em produção
        logger.debug(f"eventlet não disponível, ignorando patch de desconexão: {exc}")
        return

    try:
        original_finish = eventlet.wsgi.HttpProtocol.finish  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover
        logger.debug(f"eventlet sem HttpProtocol.finish, ignorando patch de desconexão: {exc}")
        return

    def _safe_finish(self, *args, **kwargs):  # pragma: no cover - acionado apenas em tempo de execução
        try:
            return original_finish(self, *args, **kwargs)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
            try:
                environ = getattr(self, 'environ', {}) or {}
                method = environ.get('REQUEST_METHOD', '')
                path = environ.get('PATH_INFO', '')
                logger.warning(f"Cliente desconectou ativamente, ignorando exceção: {method} {path} ({exc})")
            except Exception:
                logger.warning(f"Cliente desconectou ativamente, ignorando exceção: {exc}")
            return

    eventlet.wsgi.HttpProtocol.finish = _safe_finish  # type: ignore[attr-defined]
    logger.info("Proteção de segurança aplicada para interrupção de conexão do eventlet")

_patch_eventlet_disconnect_logging()

# Registrar Blueprint do ReportEngine
if REPORT_ENGINE_AVAILABLE:
    app.register_blueprint(report_bp, url_prefix='/api/report')
    logger.info("Interface do ReportEngine registrada")
else:
    logger.info("ReportEngine não disponível, registro de interface ignorado")

# Criar diretório de logs
LOG_DIR = Path('logs')
LOG_DIR.mkdir(exist_ok=True)

CONFIG_MODULE_NAME = 'config'
CONFIG_FILE_PATH = Path(__file__).resolve().parent / 'config.py'
CONFIG_KEYS = [
    'HOST',
    'PORT',
    'DB_DIALECT',
    'DB_HOST',
    'DB_PORT',
    'DB_USER',
    'DB_PASSWORD',
    'DB_NAME',
    'DB_CHARSET',
    'INSIGHT_ENGINE_API_KEY',
    'INSIGHT_ENGINE_BASE_URL',
    'INSIGHT_ENGINE_MODEL_NAME',
    'MEDIA_ENGINE_API_KEY',
    'MEDIA_ENGINE_BASE_URL',
    'MEDIA_ENGINE_MODEL_NAME',
    'QUERY_ENGINE_API_KEY',
    'QUERY_ENGINE_BASE_URL',
    'QUERY_ENGINE_MODEL_NAME',
    'REPORT_ENGINE_API_KEY',
    'REPORT_ENGINE_BASE_URL',
    'REPORT_ENGINE_MODEL_NAME',
    'FORUM_HOST_API_KEY',
    'FORUM_HOST_BASE_URL',
    'FORUM_HOST_MODEL_NAME',
    'KEYWORD_OPTIMIZER_API_KEY',
    'KEYWORD_OPTIMIZER_BASE_URL',
    'KEYWORD_OPTIMIZER_MODEL_NAME',
    'TAVILY_API_KEY',
    'SEARCH_TOOL_TYPE',
    'BOCHA_WEB_SEARCH_API_KEY',
    'ANSPIRE_API_KEY'
]


def _load_config_module():
    """Load or reload the config module to ensure latest values are available."""
    importlib.invalidate_caches()
    module = sys.modules.get(CONFIG_MODULE_NAME)
    try:
        if module is None:
            module = importlib.import_module(CONFIG_MODULE_NAME)
        else:
            module = importlib.reload(module)
    except ModuleNotFoundError:
        return None
    return module


def read_config_values():
    """Return the current configuration values that are exposed to the frontend."""
    try:
        # Recarregar configuração para obter a instância mais recente de Settings
        from config import reload_settings, settings
        reload_settings()

        values = {}
        for key in CONFIG_KEYS:
            # Ler valor da instância Pydantic Settings
            value = getattr(settings, key, None)
            # Convert to string for uniform handling on the frontend.
            if value is None:
                values[key] = ''
            else:
                values[key] = str(value)
        return values
    except Exception as exc:
        logger.exception(f"Falha ao ler configuração: {exc}")
        return {}


def _serialize_config_value(value):
    """Serialize Python values back to a config.py assignment-friendly string."""
    if isinstance(value, bool):
        return 'True' if value else 'False'
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return 'None'

    value_str = str(value)
    escaped = value_str.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


def write_config_values(updates):
    """Persist configuration updates to .env file (Pydantic Settings source)."""
    from pathlib import Path

    # Determinar o caminho do arquivo .env (consistente com a lógica em config.py)
    project_root = Path(__file__).resolve().parent
    cwd_env = Path.cwd() / ".env"
    env_file_path = cwd_env if cwd_env.exists() else (project_root / ".env")

    # Ler conteúdo existente do arquivo .env
    env_lines = []
    env_key_indices = {}  # Registrar a posição de índice de cada chave no arquivo
    if env_file_path.exists():
        env_lines = env_file_path.read_text(encoding='utf-8').splitlines()
        # Extrair chaves existentes e seus índices
        for i, line in enumerate(env_lines):
            line_stripped = line.strip()
            if line_stripped and not line_stripped.startswith('#'):
                if '=' in line_stripped:
                    key = line_stripped.split('=')[0].strip()
                    env_key_indices[key] = i

    # Atualizar ou adicionar itens de configuração
    for key, raw_value in updates.items():
        # Formatar valor para arquivo .env (sem aspas, exceto para strings com espaços)
        if raw_value is None or raw_value == '':
            env_value = ''
        elif isinstance(raw_value, (int, float)):
            env_value = str(raw_value)
        elif isinstance(raw_value, bool):
            env_value = 'True' if raw_value else 'False'
        else:
            value_str = str(raw_value)
            # Se contém espaços ou caracteres especiais, precisa de aspas
            if ' ' in value_str or '\n' in value_str or '#' in value_str:
                escaped = value_str.replace('\\', '\\\\').replace('"', '\\"')
                env_value = f'"{escaped}"'
            else:
                env_value = value_str

        # Atualizar ou adicionar item de configuração
        if key in env_key_indices:
            # Atualizar linha existente
            env_lines[env_key_indices[key]] = f'{key}={env_value}'
        else:
            # Adicionar nova linha ao final do arquivo
            env_lines.append(f'{key}={env_value}')

    # Escrever arquivo .env
    env_file_path.parent.mkdir(parents=True, exist_ok=True)
    env_file_path.write_text('\n'.join(env_lines) + '\n', encoding='utf-8')

    # Recarregar módulo de configuração (isso relê o arquivo .env e cria nova instância de Settings)
    _load_config_module()


system_state_lock = threading.Lock()
system_state = {
    'started': False,
    'starting': False,
    'shutdown_in_progress': False
}


def _set_system_state(*, started=None, starting=None):
    """Safely update the cached system state flags."""
    with system_state_lock:
        if started is not None:
            system_state['started'] = started
        if starting is not None:
            system_state['starting'] = starting


def _get_system_state():
    """Return a shallow copy of the system state flags."""
    with system_state_lock:
        return system_state.copy()


def _prepare_system_start():
    """Mark the system as starting if it is not already running or starting."""
    with system_state_lock:
        if system_state['started']:
            return False, 'Sistema já iniciado'
        if system_state['starting']:
            return False, 'Sistema está iniciando'
        system_state['starting'] = True
        return True, None

def _mark_shutdown_requested():
    """Marcar que o desligamento foi solicitado; retorna False se já houver um processo de desligamento."""
    with system_state_lock:
        if system_state.get('shutdown_in_progress'):
            return False
        system_state['shutdown_in_progress'] = True
        return True


def initialize_system_components():
    """Iniciar todos os componentes dependentes (sub-aplicações Streamlit, ForumEngine, ReportEngine)."""
    logs = []
    errors = []

    spider = MindSpider()
    if spider.initialize_database():
        logger.info("Banco de dados inicializado com sucesso")
    else:
        logger.error("Falha na inicialização do banco de dados")

    try:
        stop_forum_engine()
        logs.append("ForumEngine monitor parado para evitar conflito de arquivos")
    except Exception as exc:  # pragma: no cover - captura de segurança
        message = f"Exceção ao parar ForumEngine: {exc}"
        logs.append(message)
        logger.exception(message)

    processes['forum']['status'] = 'stopped'

    for app_name, script_path in STREAMLIT_SCRIPTS.items():
        logs.append(f"Verificando arquivo: {script_path}")
        if os.path.exists(script_path):
            success, message = start_streamlit_app(app_name, script_path, processes[app_name]['port'])
            logs.append(f"{app_name}: {message}")
            if success:
                startup_success, startup_message = wait_for_app_startup(app_name, 30)
                logs.append(f"{app_name} verificação de inicialização: {startup_message}")
                if not startup_success:
                    errors.append(f"{app_name} falha na inicialização: {startup_message}")
            else:
                errors.append(f"{app_name} falha na inicialização: {message}")
        else:
            msg = f"Arquivo não encontrado: {script_path}"
            logs.append(f"Erro: {msg}")
            errors.append(f"{app_name}: {msg}")

    forum_started = False
    try:
        start_forum_engine()
        processes['forum']['status'] = 'running'
        logs.append("ForumEngine inicialização concluída")
        forum_started = True
    except Exception as exc:  # pragma: no cover - captura de segurança
        error_msg = f"Falha ao iniciar ForumEngine: {exc}"
        logs.append(error_msg)
        errors.append(error_msg)

    if REPORT_ENGINE_AVAILABLE:
        try:
            if initialize_report_engine():
                logs.append("ReportEngine inicializado com sucesso")
            else:
                msg = "Falha na inicialização do ReportEngine"
                logs.append(msg)
                errors.append(msg)
        except Exception as exc:  # pragma: no cover
            msg = f"Exceção na inicialização do ReportEngine: {exc}"
            logs.append(msg)
            errors.append(msg)

    if errors:
        cleanup_processes()
        processes['forum']['status'] = 'stopped'
        if forum_started:
            try:
                stop_forum_engine()
            except Exception:  # pragma: no cover
                logger.exception("Falha ao parar ForumEngine")
        return False, logs, errors

    return True, logs, []

# Inicializar arquivo forum.log do ForumEngine
def init_forum_log():
    """Inicializar arquivo forum.log"""
    try:
        forum_log_file = LOG_DIR / "forum.log"
        # Verificar se o arquivo não existe para criá-lo com uma linha inicial; se existir, limpar e escrever linha inicial
        if not forum_log_file.exists():
            with open(forum_log_file, 'w', encoding='utf-8') as f:
                start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                f.write(f"=== ForumEngine inicialização do sistema - {start_time} ===\n")
            logger.info(f"ForumEngine: forum.log inicializado")
        else:
            with open(forum_log_file, 'w', encoding='utf-8') as f:
                start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                f.write(f"=== ForumEngine inicialização do sistema - {start_time} ===\n")
            logger.info(f"ForumEngine: forum.log inicializado")
    except Exception as e:
        logger.exception(f"ForumEngine: Falha ao inicializar forum.log: {e}")

# Inicializar forum.log
init_forum_log()

# Iniciar monitoramento inteligente do ForumEngine
def start_forum_engine():
    """Iniciar fórum do ForumEngine"""
    try:
        from ForumEngine.monitor import start_forum_monitoring
        logger.info("ForumEngine: Iniciando fórum...")
        success = start_forum_monitoring()
        if not success:
            logger.info("ForumEngine: Falha ao iniciar fórum")
    except Exception as e:
        logger.exception(f"ForumEngine: Falha ao iniciar fórum: {e}")

# Parar monitoramento inteligente do ForumEngine
def stop_forum_engine():
    """Parar fórum do ForumEngine"""
    try:
        from ForumEngine.monitor import stop_forum_monitoring
        logger.info("ForumEngine: Parando fórum...")
        stop_forum_monitoring()
        logger.info("ForumEngine: Fórum parado")
    except Exception as e:
        logger.exception(f"ForumEngine: Falha ao parar fórum: {e}")

def parse_forum_log_line(line):
    """Analisar linha do forum.log e extrair informações de conversa"""
    import re

    # Corresponder formato: [hora] [origem] conteúdo (origem permite maiúsculas/minúsculas e espaços)
    pattern = r'\[(\d{2}:\d{2}:\d{2})\]\s*\[([^\]]+)\]\s*(.*)'
    match = re.match(pattern, line)

    if not match:
        return None

    timestamp, raw_source, content = match.groups()
    source = raw_source.strip().upper()

    # Filtrar mensagens do sistema e conteúdo vazio
    if source == 'SYSTEM' or not content.strip():
        return None

    # Suportar três Agents e o moderador
    if source not in ['QUERY', 'INSIGHT', 'MEDIA', 'HOST']:
        return None

    # Decodificar quebras de linha escapadas no log, preservando formato multilinha
    cleaned_content = content.replace('\\n', '\n').replace('\\r', '').strip()

    # Determinar tipo de mensagem e remetente com base na origem
    if source == 'HOST':
        message_type = 'host'
        sender = 'Forum Host'
    else:
        message_type = 'agent'
        sender = f'{source.title()} Engine'

    return {
        'type': message_type,
        'sender': sender,
        'content': cleaned_content,
        'timestamp': timestamp,
        'source': source
    }

# Listener de logs do Forum
# Armazenar posição do histórico de logs enviado para cada cliente
forum_log_positions = {}

def monitor_forum_log():
    """Monitorar alterações no arquivo forum.log e enviar para o frontend"""
    import time
    from pathlib import Path

    forum_log_file = LOG_DIR / "forum.log"
    last_position = 0
    processed_lines = set()  # Para rastrear linhas já processadas e evitar duplicatas

    # Se o arquivo existir, obter posição inicial mas não pular conteúdo
    if forum_log_file.exists():
        with open(forum_log_file, 'r', encoding='utf-8', errors='ignore') as f:
            # Registrar tamanho do arquivo, mas não adicionar a processed_lines
            # Assim o usuário pode obter histórico ao abrir a aba do fórum
            f.seek(0, 2)  # Mover para o final do arquivo
            last_position = f.tell()

    while True:
        try:
            if forum_log_file.exists():
                with open(forum_log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    f.seek(last_position)
                    new_lines = f.readlines()

                    if new_lines:
                        for line in new_lines:
                            line = line.rstrip('\n\r')
                            if line.strip():
                                line_hash = hash(line.strip())

                                # Evitar processar a mesma linha duas vezes
                                if line_hash in processed_lines:
                                    continue

                                processed_lines.add(line_hash)

                                # Analisar linha do log e enviar mensagem do fórum
                                parsed_message = parse_forum_log_line(line)
                                if parsed_message:
                                    socketio.emit('forum_message', parsed_message)

                                # Enviar mensagem do console apenas quando o fórum estiver sendo exibido
                                timestamp = datetime.now().strftime('%H:%M:%S')
                                formatted_line = f"[{timestamp}] {line}"
                                socketio.emit('console_output', {
                                    'app': 'forum',
                                    'line': formatted_line
                                })

                        last_position = f.tell()

                        # Limpar conjunto processed_lines para evitar vazamento de memória (manter hashes das últimas 1000 linhas)
                        if len(processed_lines) > 1000:
                            # Manter hashes das últimas 500 linhas
                            recent_hashes = list(processed_lines)[-500:]
                            processed_lines = set(recent_hashes)

            time.sleep(1)  # Verificar a cada segundo
        except Exception as e:
            logger.error(f"Erro no monitoramento de logs do Forum: {e}")
            time.sleep(5)

# Iniciar thread de monitoramento de logs do Forum
forum_monitor_thread = threading.Thread(target=monitor_forum_log, daemon=True)
forum_monitor_thread.start()

# Variáveis globais para armazenar informações dos processos
processes = {
    'insight': {'process': None, 'port': 8501, 'status': 'stopped', 'output': [], 'log_file': None, 'healthcheck_started_at': None},
    'media': {'process': None, 'port': 8502, 'status': 'stopped', 'output': [], 'log_file': None, 'healthcheck_started_at': None},
    'query': {'process': None, 'port': 8503, 'status': 'stopped', 'output': [], 'log_file': None, 'healthcheck_started_at': None},
    'forum': {'process': None, 'port': None, 'status': 'stopped', 'output': [], 'log_file': None}  # Marcado como running após inicialização
}

STREAMLIT_SCRIPTS = {
    'insight': 'SingleEngineApp/insight_engine_streamlit_app.py',
    'media': 'SingleEngineApp/media_engine_streamlit_app.py',
    'query': 'SingleEngineApp/query_engine_streamlit_app.py'
}

def _log_shutdown_step(message: str):
    """Registrar etapa de desligamento de forma unificada para facilitar diagnóstico."""
    logger.info(f"[Shutdown] {message}")


def _describe_running_children():
    """Listar subprocessos ativos no momento."""
    running = []
    for name, info in processes.items():
        proc = info.get('process')
        if proc is not None and proc.poll() is None:
            port_desc = f", port={info.get('port')}" if info.get('port') else ""
            running.append(f"{name}(pid={proc.pid}{port_desc})")
    return running

# Filas de saída
output_queues = {
    'insight': Queue(),
    'media': Queue(),
    'query': Queue(),
    'forum': Queue()
}

def write_log_to_file(app_name, line):
    """Escrever log no arquivo"""
    try:
        log_file_path = LOG_DIR / f"{app_name}.log"
        with open(log_file_path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
            f.flush()
    except Exception as e:
        logger.error(f"Error writing log for {app_name}: {e}")

def read_log_from_file(app_name, tail_lines=None):
    """Ler log do arquivo"""
    try:
        log_file_path = LOG_DIR / f"{app_name}.log"
        if not log_file_path.exists():
            return []

        with open(log_file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            lines = [line.rstrip('\n\r') for line in lines if line.strip()]

            if tail_lines:
                return lines[-tail_lines:]
            return lines
    except Exception as e:
        logger.exception(f"Error reading log for {app_name}: {e}")
        return []

def read_process_output(process, app_name):
    """Ler saída do processo e escrever no arquivo"""
    import select
    import sys

    while True:
        try:
            if process.poll() is not None:
                # Processo encerrado, ler saída restante
                remaining_output = process.stdout.read()
                if remaining_output:
                    lines = remaining_output.decode('utf-8', errors='replace').split('\n')
                    for line in lines:
                        line = line.strip()
                        if line:
                            timestamp = datetime.now().strftime('%H:%M:%S')
                            formatted_line = f"[{timestamp}] {line}"
                            write_log_to_file(app_name, formatted_line)
                            socketio.emit('console_output', {
                                'app': app_name,
                                'line': formatted_line
                            })
                break

            # Usar leitura não-bloqueante
            if sys.platform == 'win32':
                # No Windows usar método diferente
                output = process.stdout.readline()
                if output:
                    line = output.decode('utf-8', errors='replace').strip()
                    if line:
                        timestamp = datetime.now().strftime('%H:%M:%S')
                        formatted_line = f"[{timestamp}] {line}"

                        # Escrever no arquivo de log
                        write_log_to_file(app_name, formatted_line)

                        # Enviar para o frontend
                        socketio.emit('console_output', {
                            'app': app_name,
                            'line': formatted_line
                        })
                else:
                    # Pausa breve quando não há saída
                    time.sleep(0.1)
            else:
                # Sistemas Unix usam select
                ready, _, _ = select.select([process.stdout], [], [], 0.1)
                if ready:
                    output = process.stdout.readline()
                    if output:
                        line = output.decode('utf-8', errors='replace').strip()
                        if line:
                            timestamp = datetime.now().strftime('%H:%M:%S')
                            formatted_line = f"[{timestamp}] {line}"

                            # Escrever no arquivo de log
                            write_log_to_file(app_name, formatted_line)

                            # Enviar para o frontend
                            socketio.emit('console_output', {
                                'app': app_name,
                                'line': formatted_line
                            })

        except Exception as e:
            error_msg = f"Error reading output for {app_name}: {e}"
            logger.exception(error_msg)
            write_log_to_file(app_name, f"[{datetime.now().strftime('%H:%M:%S')}] {error_msg}")
            break

def start_streamlit_app(app_name, script_path, port):
    """Iniciar aplicação Streamlit"""
    try:
        if processes[app_name]['process'] is not None:
            return False, "Aplicação já está em execução"

        # Verificar se o arquivo existe
        if not os.path.exists(script_path):
            return False, f"Arquivo não encontrado: {script_path}"

        # Limpar arquivo de log anterior
        log_file_path = LOG_DIR / f"{app_name}.log"
        if log_file_path.exists():
            log_file_path.unlink()

        # Criar log de inicialização
        start_msg = f"[{datetime.now().strftime('%H:%M:%S')}] Iniciando aplicação {app_name}..."
        write_log_to_file(app_name, start_msg)

        cmd = [
            sys.executable, '-m', 'streamlit', 'run',
            script_path,
            '--server.port', str(port),
            '--server.headless', 'true',
            '--browser.gatherUsageStats', 'false',
            # '--logger.level', 'debug',  # Aumentar nível de detalhamento do log
            '--logger.level', 'info',
            '--server.enableCORS', 'false'
        ]

        # Definir variáveis de ambiente para garantir codificação UTF-8 e reduzir buffer
        env = os.environ.copy()
        env.update({
            'PYTHONIOENCODING': 'utf-8',
            'PYTHONUTF8': '1',
            'LANG': 'en_US.UTF-8',
            'LC_ALL': 'en_US.UTF-8',
            'PYTHONUNBUFFERED': '1',  # Desabilitar buffer do Python
            'STREAMLIT_BROWSER_GATHER_USAGE_STATS': 'false'
        })

        # Usar diretório de trabalho atual em vez do diretório do script
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,  # Sem buffer
            universal_newlines=False,
            cwd=os.getcwd(),
            env=env,
            encoding=None,  # Tratar codificação manualmente
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )

        processes[app_name]['process'] = process
        processes[app_name]['status'] = 'starting'
        processes[app_name]['output'] = []
        processes[app_name]['healthcheck_started_at'] = time.time()

        # Iniciar thread de leitura de saída
        output_thread = threading.Thread(
            target=read_process_output,
            args=(process, app_name),
            daemon=True
        )
        output_thread.start()

        return True, f"Aplicação {app_name} iniciando..."

    except Exception as e:
        error_msg = f"Falha ao iniciar: {str(e)}"
        write_log_to_file(app_name, f"[{datetime.now().strftime('%H:%M:%S')}] {error_msg}")
        return False, error_msg

def stop_streamlit_app(app_name):
    """Parar aplicação Streamlit"""
    try:
        process = processes[app_name]['process']
        if process is None:
            _log_shutdown_step(f"{app_name} não está em execução, ignorando parada")
            return False, "Aplicação não está em execução"

        try:
            pid = process.pid
        except Exception:
            pid = 'unknown'

        _log_shutdown_step(f"Parando {app_name} (pid={pid})")
        process.terminate()

        # Aguardar encerramento do processo
        try:
            process.wait(timeout=5)
            _log_shutdown_step(f"{app_name} encerrado, returncode={process.returncode}")
        except subprocess.TimeoutExpired:
            _log_shutdown_step(f"{app_name} timeout na terminação, tentando forçar encerramento (pid={pid})")
            process.kill()
            process.wait()
            _log_shutdown_step(f"{app_name} encerrado forçadamente, returncode={process.returncode}")

        processes[app_name]['process'] = None
        processes[app_name]['status'] = 'stopped'
        processes[app_name]['healthcheck_started_at'] = None

        return True, f"Aplicação {app_name} parada"

    except Exception as e:
        _log_shutdown_step(f"Falha ao parar {app_name}: {e}")
        return False, f"Falha ao parar: {str(e)}"

HEALTHCHECK_PATH = "/_stcore/health"
HEALTHCHECK_PROXIES = {'http': None, 'https': None}
HEALTHCHECK_GRACE_SECONDS = 15


def _build_healthcheck_url(port):
    return f"http://127.0.0.1:{port}{HEALTHCHECK_PATH}"


def _healthcheck_grace_active(app_name: str) -> bool:
    started_at = processes.get(app_name, {}).get('healthcheck_started_at')
    if not started_at:
        return False
    return (time.time() - started_at) < HEALTHCHECK_GRACE_SECONDS


def _log_healthcheck_failure(app_name: str, exc: Exception):
    if _healthcheck_grace_active(app_name):
        logger.debug(f"Iniciando {app_name}, por favor aguarde")
        return
    logger.warning(f"Falha na verificação de saúde de {app_name}: {exc}")


def check_app_status():
    """Verificar status das aplicações"""
    for app_name, info in processes.items():
        if info['process'] is not None:
            if info['process'].poll() is None:
                # Processo ainda em execução, verificar se a porta está acessível
                try:
                    response = requests.get(
                        _build_healthcheck_url(info['port']),
                        timeout=2,
                        proxies=HEALTHCHECK_PROXIES
                    )
                    if response.status_code == 200:
                        info['status'] = 'running'
                    else:
                        info['status'] = 'starting'
                except Exception as exc:
                    _log_healthcheck_failure(app_name, exc)
                    info['status'] = 'starting'
            else:
                # Processo encerrado
                info['process'] = None
                info['status'] = 'stopped'
                info['healthcheck_started_at'] = None

def wait_for_app_startup(app_name, max_wait_time=90):
    """Aguardar conclusão da inicialização da aplicação"""
    import time
    start_time = time.time()
    while time.time() - start_time < max_wait_time:
        info = processes[app_name]
        if info['process'] is None:
            return False, "Processo parado"

        if info['process'].poll() is not None:
            return False, "Falha na inicialização do processo"

        try:
            response = requests.get(
                _build_healthcheck_url(info['port']),
                timeout=2,
                proxies=HEALTHCHECK_PROXIES
            )
            if response.status_code == 200:
                info['status'] = 'running'
                return True, "Inicialização bem-sucedida"
        except Exception as exc:
            _log_healthcheck_failure(app_name, exc)

        time.sleep(1)

    return False, "Timeout na inicialização"

def cleanup_processes():
    """Limpar todos os processos"""
    _log_shutdown_step("Iniciando limpeza serial dos subprocessos")
    for app_name in STREAMLIT_SCRIPTS:
        stop_streamlit_app(app_name)

    processes['forum']['status'] = 'stopped'
    try:
        stop_forum_engine()
    except Exception:  # pragma: no cover
        logger.exception("Falha ao parar ForumEngine")
    _log_shutdown_step("Limpeza dos subprocessos concluída")
    _set_system_state(started=False, starting=False)

def cleanup_processes_concurrent(timeout: float = 6.0):
    """Limpar todos os subprocessos de forma concorrente, forçar encerramento dos restantes após timeout."""
    _log_shutdown_step(f"Iniciando limpeza concorrente dos subprocessos (timeout {timeout}s)")
    _log_shutdown_step("Apenas terminando subprocessos iniciados e registrados pelo console atual, sem varredura de portas")
    running_before = _describe_running_children()
    if running_before:
        _log_shutdown_step("Subprocessos ativos: " + ", ".join(running_before))
    else:
        _log_shutdown_step("Nenhum subprocesso ativo detectado, ainda assim enviando comando de encerramento")

    threads = []

    # Encerrar subprocessos Streamlit de forma concorrente
    for app_name in STREAMLIT_SCRIPTS:
        t = threading.Thread(target=stop_streamlit_app, args=(app_name,), daemon=True)
        threads.append(t)
        t.start()

    # Encerrar ForumEngine de forma concorrente
    forum_thread = threading.Thread(target=stop_forum_engine, daemon=True)
    threads.append(forum_thread)
    forum_thread.start()

    # Aguardar conclusão de todas as threads, no máximo timeout segundos
    end_time = time.time() + timeout
    for t in threads:
        remaining = end_time - time.time()
        if remaining <= 0:
            break
        t.join(timeout=remaining)

    # Segunda verificação: forçar encerramento de subprocessos ainda ativos
    for app_name in STREAMLIT_SCRIPTS:
        proc = processes[app_name]['process']
        if proc is not None and proc.poll() is None:
            try:
                _log_shutdown_step(f"Processo {app_name} ainda ativo, acionando segunda terminação (pid={proc.pid})")
                proc.terminate()
                proc.wait(timeout=1)
            except Exception:
                try:
                    _log_shutdown_step(f"Segunda terminação de {app_name} falhou, tentando kill (pid={proc.pid})")
                    proc.kill()
                    proc.wait(timeout=1)
                except Exception:
                    logger.warning(f"Falha ao forçar encerramento do processo {app_name}, continuando desligamento")
            finally:
                processes[app_name]['process'] = None
                processes[app_name]['status'] = 'stopped'

    processes['forum']['status'] = 'stopped'
    _log_shutdown_step("Limpeza concorrente concluída, marcando sistema como não iniciado")
    _set_system_state(started=False, starting=False)

def _schedule_server_shutdown(delay_seconds: float = 0.1):
    """Sair o mais rápido possível após a limpeza, evitando bloquear a requisição atual."""
    def _shutdown():
        time.sleep(delay_seconds)
        try:
            socketio.stop()
        except Exception as exc:  # pragma: no cover
            logger.warning(f"Exceção ao parar SocketIO, continuando saída: {exc}")
        _log_shutdown_step("Comando de parada do SocketIO enviado, saindo do processo principal")
        os._exit(0)

    threading.Thread(target=_shutdown, daemon=True).start()

def _start_async_shutdown(cleanup_timeout: float = 3.0):
    """Acionar limpeza assíncrona e forçar saída, evitando bloqueio da requisição HTTP."""
    _log_shutdown_step(f"Comando de desligamento recebido, iniciando limpeza assíncrona (timeout {cleanup_timeout}s)")

    def _force_exit():
        _log_shutdown_step("Timeout de desligamento, acionando saída forçada")
        os._exit(0)

    # Proteção de timeout rígido, garante saída mesmo se a thread de limpeza falhar
    hard_timeout = cleanup_timeout + 2.0
    force_timer = threading.Timer(hard_timeout, _force_exit)
    force_timer.daemon = True
    force_timer.start()

    def _cleanup_and_exit():
        try:
            cleanup_processes_concurrent(timeout=cleanup_timeout)
        except Exception as exc:  # pragma: no cover
            logger.exception(f"Exceção na limpeza de desligamento: {exc}")
        finally:
            _log_shutdown_step("Thread de limpeza concluída, agendando saída do processo principal")
            _schedule_server_shutdown(0.05)

    threading.Thread(target=_cleanup_and_exit, daemon=True).start()

# Registrar função de limpeza
atexit.register(cleanup_processes)

@app.route('/')
def index():
    """Página inicial"""
    return render_template('index.html')

@app.route('/api/status')
def get_status():
    """Obter status de todas as aplicações"""
    check_app_status()
    return jsonify({
        app_name: {
            'status': info['status'],
            'port': info['port'],
            'output_lines': len(info['output'])
        }
        for app_name, info in processes.items()
    })

@app.route('/api/start/<app_name>')
def start_app(app_name):
    """Iniciar aplicação especificada"""
    if app_name not in processes:
        return jsonify({'success': False, 'message': 'Aplicação desconhecida'})

    if app_name == 'forum':
        try:
            start_forum_engine()
            processes['forum']['status'] = 'running'
            return jsonify({'success': True, 'message': 'ForumEngine iniciado'})
        except Exception as exc:  # pragma: no cover
            logger.exception("Falha ao iniciar ForumEngine manualmente")
            return jsonify({'success': False, 'message': f'Falha ao iniciar ForumEngine: {exc}'})

    script_path = STREAMLIT_SCRIPTS.get(app_name)
    if not script_path:
        return jsonify({'success': False, 'message': 'Esta aplicação não suporta operação de inicialização'})

    success, message = start_streamlit_app(
        app_name,
        script_path,
        processes[app_name]['port']
    )

    if success:
        # Aguardar inicialização da aplicação
        startup_success, startup_message = wait_for_app_startup(app_name, 15)
        if not startup_success:
            message += f" mas a verificação de inicialização falhou: {startup_message}"

    return jsonify({'success': success, 'message': message})

@app.route('/api/stop/<app_name>')
def stop_app(app_name):
    """Parar aplicação especificada"""
    if app_name not in processes:
        return jsonify({'success': False, 'message': 'Aplicação desconhecida'})

    if app_name == 'forum':
        try:
            stop_forum_engine()
            processes['forum']['status'] = 'stopped'
            return jsonify({'success': True, 'message': 'ForumEngine parado'})
        except Exception as exc:  # pragma: no cover
            logger.exception("Falha ao parar ForumEngine manualmente")
            return jsonify({'success': False, 'message': f'Falha ao parar ForumEngine: {exc}'})

    success, message = stop_streamlit_app(app_name)
    return jsonify({'success': success, 'message': message})

@app.route('/api/output/<app_name>')
def get_output(app_name):
    """Obter saída da aplicação"""
    if app_name not in processes:
        return jsonify({'success': False, 'message': 'Aplicação desconhecida'})

    # Tratamento especial para Forum Engine
    if app_name == 'forum':
        try:
            forum_log_content = read_log_from_file('forum')
            return jsonify({
                'success': True,
                'output': forum_log_content,
                'total_lines': len(forum_log_content)
            })
        except Exception as e:
            return jsonify({'success': False, 'message': f'Falha ao ler log do forum: {str(e)}'})

    # Ler log completo do arquivo
    output_lines = read_log_from_file(app_name)

    return jsonify({
        'success': True,
        'output': output_lines
    })

@app.route('/api/test_log/<app_name>')
def test_log(app_name):
    """Testar funcionalidade de escrita de log"""
    if app_name not in processes:
        return jsonify({'success': False, 'message': 'Aplicação desconhecida'})

    # Escrever mensagem de teste
    test_msg = f"[{datetime.now().strftime('%H:%M:%S')}] Mensagem de teste de log - {datetime.now()}"
    write_log_to_file(app_name, test_msg)

    # Enviar via Socket.IO
    socketio.emit('console_output', {
        'app': app_name,
        'line': test_msg
    })

    return jsonify({
        'success': True,
        'message': f'Mensagem de teste escrita no log de {app_name}'
    })

@app.route('/api/forum/start')
def start_forum_monitoring_api():
    """Iniciar fórum do ForumEngine manualmente"""
    try:
        from ForumEngine.monitor import start_forum_monitoring
        success = start_forum_monitoring()
        if success:
            return jsonify({'success': True, 'message': 'Fórum do ForumEngine iniciado'})
        else:
            return jsonify({'success': False, 'message': 'Falha ao iniciar fórum do ForumEngine'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Falha ao iniciar fórum: {str(e)}'})

@app.route('/api/forum/stop')
def stop_forum_monitoring_api():
    """Parar fórum do ForumEngine manualmente"""
    try:
        from ForumEngine.monitor import stop_forum_monitoring
        stop_forum_monitoring()
        return jsonify({'success': True, 'message': 'Fórum do ForumEngine parado'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Falha ao parar fórum: {str(e)}'})

@app.route('/api/forum/log')
def get_forum_log():
    """Obter conteúdo do forum.log do ForumEngine"""
    try:
        forum_log_file = LOG_DIR / "forum.log"
        if not forum_log_file.exists():
            return jsonify({
                'success': True,
                'log_lines': [],
                'parsed_messages': [],
                'total_lines': 0
            })

        with open(forum_log_file, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            lines = [line.rstrip('\n\r') for line in lines if line.strip()]

        # Analisar cada linha do log e extrair informações de conversa
        parsed_messages = []
        for line in lines:
            parsed_message = parse_forum_log_line(line)
            if parsed_message:
                parsed_messages.append(parsed_message)

        return jsonify({
            'success': True,
            'log_lines': lines,
            'parsed_messages': parsed_messages,
            'total_lines': len(lines)
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'Falha ao ler forum.log: {str(e)}'})

@app.route('/api/forum/log/history', methods=['POST'])
def get_forum_log_history():
    """Obter histórico de logs do Forum (suporta início a partir de posição especificada)"""
    try:
        data = request.get_json()
        start_position = data.get('position', 0)  # Posição onde o cliente parou da última vez
        max_lines = data.get('max_lines', 1000)   # Número máximo de linhas a retornar

        forum_log_file = LOG_DIR / "forum.log"
        if not forum_log_file.exists():
            return jsonify({
                'success': True,
                'log_lines': [],
                'position': 0,
                'has_more': False
            })

        with open(forum_log_file, 'r', encoding='utf-8', errors='ignore') as f:
            # Ler a partir da posição especificada
            f.seek(start_position)
            lines = []
            line_count = 0

            for line in f:
                if line_count >= max_lines:
                    break
                line = line.rstrip('\n\r')
                if line.strip():
                    # Adicionar timestamp
                    timestamp = datetime.now().strftime('%H:%M:%S')
                    formatted_line = f"[{timestamp}] {line}"
                    lines.append(formatted_line)
                    line_count += 1

            # Registrar posição atual
            current_position = f.tell()

            # Verificar se há mais conteúdo
            f.seek(0, 2)  # Mover para o final do arquivo
            end_position = f.tell()
            has_more = current_position < end_position

        return jsonify({
            'success': True,
            'log_lines': lines,
            'position': current_position,
            'has_more': has_more
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'Falha ao ler histórico do forum: {str(e)}'})

@app.route('/api/search', methods=['POST'])
def search():
    """Interface de busca unificada"""
    data = request.get_json()
    query = data.get('query', '').strip()

    if not query:
        return jsonify({'success': False, 'message': 'A consulta de busca não pode estar vazia'})

    # O fórum do ForumEngine já está rodando em segundo plano e detectará automaticamente a atividade de busca
    # logger.info("ForumEngine: Requisição de busca recebida, o fórum detectará automaticamente alterações no log")

    # Verificar quais aplicações estão em execução
    check_app_status()
    running_apps = [name for name, info in processes.items() if info['status'] == 'running']

    if not running_apps:
        return jsonify({'success': False, 'message': 'Nenhuma aplicação em execução'})

    # Enviar requisição de busca para as aplicações em execução
    results = {}
    api_ports = {'insight': 8501, 'media': 8502, 'query': 8503}

    for app_name in running_apps:
        try:
            api_port = api_ports[app_name]
            # Chamar endpoint da API da aplicação Streamlit
            response = requests.post(
                f"http://localhost:{api_port}/api/search",
                json={'query': query},
                timeout=10
            )
            if response.status_code == 200:
                results[app_name] = response.json()
            else:
                results[app_name] = {'success': False, 'message': 'Falha na chamada da API'}
        except Exception as e:
            results[app_name] = {'success': False, 'message': str(e)}

    # Após a busca, pode-se optar por parar o monitoramento ou deixá-lo rodando para capturar logs de processamento subsequentes
    # Aqui mantemos o monitoramento rodando; o usuário pode parar manualmente por outra interface

    return jsonify({
        'success': True,
        'query': query,
        'results': results
    })


@app.route('/api/config', methods=['GET'])
def get_config():
    """Expose selected configuration values to the frontend."""
    try:
        config_values = read_config_values()
        return jsonify({'success': True, 'config': config_values})
    except Exception as exc:
        logger.exception("Falha ao ler configuração")
        return jsonify({'success': False, 'message': f'Falha ao ler configuração: {exc}'}), 500


@app.route('/api/config', methods=['POST'])
def update_config():
    """Update configuration values and persist them to config.py."""
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or not payload:
        return jsonify({'success': False, 'message': 'O corpo da requisição não pode estar vazio'}), 400

    updates = {}
    for key, value in payload.items():
        if key in CONFIG_KEYS:
            updates[key] = value if value is not None else ''

    if not updates:
        return jsonify({'success': False, 'message': 'Nenhum item de configuração para atualizar'}), 400

    try:
        write_config_values(updates)
        updated_config = read_config_values()
        return jsonify({'success': True, 'config': updated_config})
    except Exception as exc:
        logger.exception("Falha ao atualizar configuração")
        return jsonify({'success': False, 'message': f'Falha ao atualizar configuração: {exc}'}), 500


@app.route('/api/system/status')
def get_system_status():
    """Retornar status de inicialização do sistema."""
    state = _get_system_state()
    return jsonify({
        'success': True,
        'started': state['started'],
        'starting': state['starting']
    })


@app.route('/api/system/start', methods=['POST'])
def start_system():
    """Iniciar o sistema completo após receber a requisição."""
    allowed, message = _prepare_system_start()
    if not allowed:
        return jsonify({'success': False, 'message': message}), 400

    try:
        success, logs, errors = initialize_system_components()
        if success:
            _set_system_state(started=True)
            return jsonify({'success': True, 'message': 'Sistema iniciado com sucesso', 'logs': logs})

        _set_system_state(started=False)
        return jsonify({
            'success': False,
            'message': 'Falha na inicialização do sistema',
            'logs': logs,
            'errors': errors
        }), 500
    except Exception as exc:  # pragma: no cover - captura de segurança
        logger.exception("Exceção durante a inicialização do sistema")
        _set_system_state(started=False)
        return jsonify({'success': False, 'message': f'Exceção na inicialização do sistema: {exc}'}), 500
    finally:
        _set_system_state(starting=False)

@app.route('/api/system/shutdown', methods=['POST'])
def shutdown_system():
    """Parar todos os componentes de forma graciosa e encerrar o processo do servidor."""
    state = _get_system_state()
    if state['starting']:
        return jsonify({'success': False, 'message': 'Sistema está iniciando/reiniciando, por favor aguarde'}), 400

    target_ports = [
        f"{name}:{info['port']}"
        for name, info in processes.items()
        if info.get('port')
    ]

    # Quando já há uma requisição de desligamento em andamento, retornar subprocessos ativos para o frontend avaliar o progresso
    if not _mark_shutdown_requested():
        running = _describe_running_children()
        detail = 'Comando de desligamento já enviado, por favor aguarde...'
        if running:
            detail = f"Comando de desligamento já enviado, aguardando encerramento dos processos: {', '.join(running)}"
        if target_ports:
            detail = f"{detail} (portas: {', '.join(target_ports)})"
        return jsonify({'success': True, 'message': detail, 'ports': target_ports})

    running = _describe_running_children()
    if running:
        _log_shutdown_step("Iniciando desligamento do sistema, aguardando encerramento dos subprocessos: " + ", ".join(running))
    else:
        _log_shutdown_step("Iniciando desligamento do sistema, nenhum subprocesso ativo detectado")

    try:
        _set_system_state(started=False, starting=False)
        _start_async_shutdown(cleanup_timeout=6.0)
        message = 'Comando de desligamento enviado, parando processos'
        if running:
            message = f"{message}: {', '.join(running)}"
        if target_ports:
            message = f"{message} (portas: {', '.join(target_ports)})"
        return jsonify({'success': True, 'message': message, 'ports': target_ports})
    except Exception as exc:  # pragma: no cover - captura de segurança
        logger.exception("Exceção durante o desligamento do sistema")
        return jsonify({'success': False, 'message': f'Exceção no desligamento do sistema: {exc}'}), 500

@app.route('/api/update-news', methods=['POST'])
def update_news():
    try:
        data = request.get_json() or {}
        sources = data.get('sources', None)
        regions = data.get('regions', None)

        # Import news collector
        from MindSpider.BroadTopicExtraction.get_today_news import NewsCollector, SOURCE_NAMES, SOURCE_REGIONS, REGION_LABELS

        # Filter sources by region if specified
        if regions and not sources:
            sources = [s for s, r in SOURCE_REGIONS.items() if r in regions]

        # Collect news
        import asyncio
        collector = NewsCollector()
        result = asyncio.run(collector.collect_and_save_news(sources=sources))
        collector.close()

        return jsonify({
            'success': result.get('success', False),
            'total_news': result.get('total_news', 0),
            'saved_count': result.get('saved_count', 0),
            'message': f"Coletadas {result.get('total_news', 0)} notícias de {result.get('successful_sources', 0)} fontes"
        })
    except Exception as e:
        logger.exception(f"Erro ao atualizar notícias: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/news-status', methods=['GET'])
def news_status():
    try:
        from MindSpider.BroadTopicExtraction.get_today_news import SOURCE_NAMES, SOURCE_REGIONS, REGION_LABELS, RSS_FEEDS

        sources_by_region = {}
        for source_id, region in SOURCE_REGIONS.items():
            if region not in sources_by_region:
                sources_by_region[region] = []
            sources_by_region[region].append({
                'id': source_id,
                'name': SOURCE_NAMES.get(source_id, source_id)
            })

        return jsonify({
            'regions': REGION_LABELS,
            'sources_by_region': sources_by_region,
            'default_region': 'brasil'
        })
    except Exception as e:
        logger.exception(f"Erro ao obter status de notícias: {e}")
        return jsonify({'error': str(e)}), 500

@socketio.on('connect')
def handle_connect():
    """Conexão do cliente"""
    emit('status', 'Connected to Flask server')

@socketio.on('request_status')
def handle_status_request():
    """Solicitar atualização de status"""
    check_app_status()
    emit('status_update', {
        app_name: {
            'status': info['status'],
            'port': info['port']
        }
        for app_name, info in processes.items()
    })

if __name__ == '__main__':
    # Ler HOST e PORT do arquivo de configuração
    from config import settings
    HOST = settings.HOST
    PORT = settings.PORT

    logger.info("Aguardando confirmação de configuração, o sistema iniciará os componentes após comando do frontend...")
    logger.info(f"Servidor Flask iniciado, acesse: http://{HOST}:{PORT}")

    try:
        socketio.run(app, host=HOST, port=PORT, debug=False)
    except KeyboardInterrupt:
        logger.info("\nEncerrando aplicação...")
        cleanup_processes()


