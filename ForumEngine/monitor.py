"""
Monitor de logs - Monitora em tempo real a saída do SummaryNode em três arquivos de log
"""

import os
import time
import threading
from pathlib import Path
from datetime import datetime
import re
import json
from typing import Dict, Optional, List
from threading import Lock
from loguru import logger

# Importar módulo do moderador do fórum
try:
    from .llm_host import generate_host_speech
    HOST_AVAILABLE = True
except ImportError:
    logger.exception("ForumEngine: Módulo do moderador do fórum não encontrado, executando em modo somente monitoramento")
    HOST_AVAILABLE = False

class LogMonitor:
    """Monitor de logs inteligente baseado em alterações de arquivo"""

    def __init__(self, log_dir: str = "logs"):
        """Inicializar monitor de logs"""
        self.log_dir = Path(log_dir)
        self.forum_log_file = self.log_dir / "forum.log"

        # Arquivos de log a serem monitorados
        self.monitored_logs = {
            'insight': self.log_dir / 'insight.log',
            'media': self.log_dir / 'media.log',
            'query': self.log_dir / 'query.log'
        }

        # Estado do monitoramento
        self.is_monitoring = False
        self.monitor_thread = None
        self.file_positions = {}  # Registrar posição de leitura de cada arquivo
        self.file_line_counts = {}  # Registrar número de linhas de cada arquivo
        self.is_searching = False  # Se está em busca
        self.search_inactive_count = 0  # Contador de inatividade de busca
        self.write_lock = Lock()  # Lock de escrita, prevenir conflito de escrita concorrente

        # Estado relacionado ao moderador
        self.agent_speeches_buffer = []  # Buffer de discursos dos agents
        self.host_speech_threshold = 5  # A cada 5 discursos de agent, disparar um discurso do moderador
        self.is_host_generating = False  # Se o moderador está gerando discurso

        # Padrões de identificação do nó alvo
        # 1. Nome da classe (formato antigo pode conter)
        # 2. Caminho completo do módulo (formato real do log, incluindo prefixo do engine)
        # 3. Caminho parcial do módulo (compatibilidade)
        # 4. Texto de identificação chave
        self.target_node_patterns = [
            'FirstSummaryNode',  # Nome da classe
            'ReflectionSummaryNode',  # Nome da classe
            'InsightEngine.nodes.summary_node',  # Caminho completo do InsightEngine
            'MediaEngine.nodes.summary_node',  # Caminho completo do MediaEngine
            'QueryEngine.nodes.summary_node',  # Caminho completo do QueryEngine
            'nodes.summary_node',  # Caminho do módulo (compatibilidade, para correspondência parcial)
            '正在生成首次段落总结',  # Identificação do FirstSummaryNode
            '正在生成反思总结',  # Identificação do ReflectionSummaryNode
        ]

        # Estado de captura de conteúdo multilinha
        self.capturing_json = {}  # Estado de captura JSON de cada app
        self.json_buffer = {}     # Buffer JSON de cada app
        self.json_start_line = {} # Linha de início do JSON de cada app
        self.in_error_block = {}  # Se cada app está em bloco de ERROR

        # Garantir que o diretório de logs exista
        self.log_dir.mkdir(exist_ok=True)

    def clear_forum_log(self):
        """Limpar arquivo forum.log"""
        try:
            if self.forum_log_file.exists():
                self.forum_log_file.unlink()

            # Criar novo arquivo forum.log e escrever marcador de início
            start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            # Usar função write_to_forum_log para escrever marcador de início, garantindo formato consistente
            with open(self.forum_log_file, 'w', encoding='utf-8') as f:
                pass  # Primeiro criar arquivo vazio
            self.write_to_forum_log(f"=== ForumEngine monitoramento iniciado - {start_time} ===", "SYSTEM")

            logger.info(f"ForumEngine: forum.log foi limpo e inicializado")

            # Resetar estado de captura JSON
            self.capturing_json = {}
            self.json_buffer = {}
            self.json_start_line = {}
            self.in_error_block = {}

            # Resetar estado relacionado ao moderador
            self.agent_speeches_buffer = []
            self.is_host_generating = False

        except Exception as e:
            logger.exception(f"ForumEngine: Falha ao limpar forum.log: {e}")

    def write_to_forum_log(self, content: str, source: str = None):
        """Escrever conteúdo no forum.log (thread-safe)"""
        try:
            with self.write_lock:  # Usar lock para garantir segurança de thread
                with open(self.forum_log_file, 'a', encoding='utf-8') as f:
                    timestamp = datetime.now().strftime('%H:%M:%S')
                    # Converter quebras de linha reais no conteúdo para string \n, garantindo que todo o registro fique em uma linha
                    content_one_line = content.replace('\n', '\\n').replace('\r', '\\r')
                    # Se a tag de origem foi fornecida, adicionar após o timestamp
                    if source:
                        f.write(f"[{timestamp}] [{source}] {content_one_line}\n")
                    else:
                        f.write(f"[{timestamp}] {content_one_line}\n")
                    f.flush()
        except Exception as e:
            logger.exception(f"ForumEngine: Falha ao escrever no forum.log: {e}")

    def get_log_level(self, line: str) -> Optional[str]:
        """Detectar o nível da linha de log (INFO/ERROR/WARNING/DEBUG etc.)

        Suporta formato loguru: YYYY-MM-DD HH:mm:ss.SSS | LEVEL | ...

        Returns:
            'INFO', 'ERROR', 'WARNING', 'DEBUG' ou None (não reconhecido)
        """
        # Verificar formato loguru: YYYY-MM-DD HH:mm:ss.SSS | LEVEL | ...
        # Padrão de correspondência: | LEVEL | ou | LEVEL     |
        match = re.search(r'\|\s*(INFO|ERROR|WARNING|DEBUG|TRACE|CRITICAL)\s*\|', line)
        if match:
            return match.group(1)
        return None

    def is_target_log_line(self, line: str) -> bool:
        """Verificar se é uma linha de log alvo (SummaryNode)

        Suporta múltiplos métodos de identificação:
        1. Nome da classe: FirstSummaryNode, ReflectionSummaryNode
        2. Caminho completo do módulo: InsightEngine.nodes.summary_node, MediaEngine.nodes.summary_node, QueryEngine.nodes.summary_node
        3. Caminho parcial do módulo: nodes.summary_node (compatibilidade)
        4. Texto de identificação chave: 正在生成首次段落总结, 正在生成反思总结

        Condições de exclusão:
        - Logs de nível ERROR (logs de erro não devem ser identificados como nós alvo)
        - Logs contendo palavras-chave de erro (falha na análise JSON, falha na correção JSON etc.)
        """
        # Excluir logs de nível ERROR
        log_level = self.get_log_level(line)
        if log_level == 'ERROR':
            return False

        # Compatibilidade com verificação antiga
        if "| ERROR" in line or "| ERROR    |" in line:
            return False

        # Excluir logs contendo palavras-chave de erro
        error_keywords = ["JSON解析失败", "JSON修复失败", "Traceback", "File \""]
        for keyword in error_keywords:
            if keyword in line:
                return False

        # Verificar se contém padrão de nó alvo
        for pattern in self.target_node_patterns:
            if pattern in line:
                return True
        return False

    def is_valuable_content(self, line: str) -> bool:
        """Determinar se é conteúdo valioso (excluir mensagens curtas de status e mensagens de erro)"""
        # Se contém "清理后的输出", considerar como valioso
        if "清理后的输出" in line:
            return True

        # Excluir mensagens curtas comuns de status e mensagens de erro
        exclude_patterns = [
            "JSON解析失败",
            "JSON修复失败",
            "直接使用清理后的文本",
            "JSON解析成功",
            "成功生成",
            "已更新段落",
            "正在生成",
            "开始处理",
            "处理完成",
            "已读取HOST发言",
            "读取HOST发言失败",
            "未找到HOST发言",
            "调试输出",
            "信息记录"
        ]

        for pattern in exclude_patterns:
            if pattern in line:
                return False

        # Se o comprimento da linha for muito curto, também considerar como sem valor
        # Remover timestamp: suporta formato antigo e novo
        clean_line = re.sub(r'\[\d{2}:\d{2}:\d{2}\]', '', line)
        clean_line = re.sub(r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}\s*\|\s*[A-Z]+\s*\|\s*[^|]+?\s*-\s*', '', clean_line)
        clean_line = clean_line.strip()
        if len(clean_line) < 30:  # Limite pode ser ajustado
            return False

        return True

    def is_json_start_line(self, line: str) -> bool:
        """Determinar se é uma linha de início de JSON"""
        return "清理后的输出: {" in line

    def is_json_end_line(self, line: str) -> bool:
        """Determinar se é uma linha de fim de JSON

        Apenas determina linhas de marcador de fim puro, sem qualquer informação de formato de log (timestamp etc.).
        Se a linha contém timestamp, deve ser limpa antes de determinar, mas aqui retorna False indicando que precisa de processamento adicional.
        """
        stripped = line.strip()

        # Se a linha contém timestamp (formato antigo ou novo), significa que não é uma linha de fim pura
        # Formato antigo: [HH:MM:SS]
        if re.match(r'^\[\d{2}:\d{2}:\d{2}\]', stripped):
            return False
        # Formato novo: YYYY-MM-DD HH:mm:ss.SSS
        if re.match(r'^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}', stripped):
            return False

        # Linhas sem timestamp, verificar se é marcador de fim puro
        if stripped == "}" or stripped == "] }":
            return True
        return False

    def extract_json_content(self, json_lines: List[str]) -> Optional[str]:
        """Extrair e analisar conteúdo JSON de múltiplas linhas"""
        try:
            # Encontrar posição de início do JSON
            json_start_idx = -1
            for i, line in enumerate(json_lines):
                if "清理后的输出: {" in line:
                    json_start_idx = i
                    break

            if json_start_idx == -1:
                return None

            # Extrair parte JSON
            first_line = json_lines[json_start_idx]
            json_start_pos = first_line.find("清理后的输出: {")
            if json_start_pos == -1:
                return None

            json_part = first_line[json_start_pos + len("清理后的输出: "):]

            # Se a primeira linha já contém JSON completo, processar diretamente
            if json_part.strip().endswith("}") and json_part.count("{") == json_part.count("}"):
                try:
                    json_obj = json.loads(json_part.strip())
                    return self.format_json_content(json_obj)
                except json.JSONDecodeError:
                    # Falha na análise JSON de linha única, tentar corrigir
                    fixed_json = self.fix_json_string(json_part.strip())
                    if fixed_json:
                        try:
                            json_obj = json.loads(fixed_json)
                            return self.format_json_content(json_obj)
                        except json.JSONDecodeError:
                            pass
                    return None

            # Processar JSON multilinha
            json_text = json_part
            for line in json_lines[json_start_idx + 1:]:
                # Remover timestamp: suporta formato antigo [HH:MM:SS] e formato novo loguru (YYYY-MM-DD HH:mm:ss.SSS | LEVEL | ...)
                # Formato antigo: [HH:MM:SS]
                clean_line = re.sub(r'^\[\d{2}:\d{2}:\d{2}\]\s*', '', line)
                # Formato novo: remover timestamp e informações de nível no formato loguru
                # Formato: YYYY-MM-DD HH:mm:ss.SSS | LEVEL | module:function:line -
                clean_line = re.sub(r'^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}\s*\|\s*[A-Z]+\s*\|\s*[^|]+?\s*-\s*', '', clean_line)
                json_text += clean_line

            # Tentar analisar JSON
            try:
                json_obj = json.loads(json_text.strip())
                return self.format_json_content(json_obj)
            except json.JSONDecodeError:
                # Falha na análise JSON multilinha, tentar corrigir
                fixed_json = self.fix_json_string(json_text.strip())
                if fixed_json:
                    try:
                        json_obj = json.loads(fixed_json)
                        return self.format_json_content(json_obj)
                    except json.JSONDecodeError:
                        pass
                return None

        except Exception as e:
            # Outras exceções também não imprimem mensagens de erro, retornar None diretamente
            return None

    def format_json_content(self, json_obj: dict) -> str:
        """Formatar conteúdo JSON em forma legível"""
        try:
            # Extrair conteúdo principal, priorizar resumo reflexivo, depois resumo inicial
            content = None

            if "updated_paragraph_latest_state" in json_obj:
                content = json_obj["updated_paragraph_latest_state"]
            elif "paragraph_latest_state" in json_obj:
                content = json_obj["paragraph_latest_state"]

            # Se o conteúdo foi encontrado, retornar diretamente (manter quebras de linha como \n)
            if content:
                return content

            # Se os campos esperados não foram encontrados, retornar representação em string de todo o JSON
            return f"Saída após limpeza: {json.dumps(json_obj, ensure_ascii=False, indent=2)}"

        except Exception as e:
            logger.exception(f"ForumEngine: Erro ao formatar JSON: {e}")
            return f"Saída após limpeza: {json.dumps(json_obj, ensure_ascii=False, indent=2)}"

    def extract_node_content(self, line: str) -> Optional[str]:
        """Extrair conteúdo do nó, removendo prefixos como timestamp, nome do nó etc."""
        content = line

        # Remover parte do timestamp: suporta formato antigo e novo
        # Formato antigo: [HH:MM:SS]
        match_old = re.search(r'\[\d{2}:\d{2}:\d{2}\]\s*(.+)', content)
        if match_old:
            content = match_old.group(1).strip()
        else:
            # Formato novo: YYYY-MM-DD HH:mm:ss.SSS | LEVEL | module:function:line -
            match_new = re.search(r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}\s*\|\s*[A-Z]+\s*\|\s*[^|]+?\s*-\s*(.+)', content)
            if match_new:
                content = match_new.group(1).strip()

        if not content:
            return line.strip()

        # Remover todas as tags entre colchetes (incluindo nomes de nó e nomes de aplicação)
        content = re.sub(r'^\[.*?\]\s*', '', content)

        # Continuar removendo possíveis tags consecutivas
        while re.match(r'^\[.*?\]\s*', content):
            content = re.sub(r'^\[.*?\]\s*', '', content)

        # Remover prefixos comuns (como "首次总结: ", "反思总结: " etc.)
        prefixes_to_remove = [
            "首次总结: ",
            "反思总结: ",
            "清理后的输出: "
        ]

        for prefix in prefixes_to_remove:
            if content.startswith(prefix):
                content = content[len(prefix):]
                break

        # Remover possível tag de nome da aplicação (não entre colchetes)
        app_names = ['INSIGHT', 'MEDIA', 'QUERY']
        for app_name in app_names:
            # Remover APP_NAME isolado (no início da linha)
            content = re.sub(rf'^{app_name}\s+', '', content, flags=re.IGNORECASE)

        # Limpar espaços excessivos
        content = re.sub(r'\s+', ' ', content)

        return content.strip()

    def get_file_size(self, file_path: Path) -> int:
        """Obter tamanho do arquivo"""
        try:
            return file_path.stat().st_size if file_path.exists() else 0
        except:
            return 0

    def get_file_line_count(self, file_path: Path) -> int:
        """Obter número de linhas do arquivo"""
        try:
            if not file_path.exists():
                return 0
            with open(file_path, 'r', encoding='utf-8') as f:
                return sum(1 for _ in f)
        except:
            return 0

    def read_new_lines(self, file_path: Path, app_name: str) -> List[str]:
        """Ler novas linhas do arquivo"""
        new_lines = []

        try:
            if not file_path.exists():
                return new_lines

            current_size = self.get_file_size(file_path)
            last_position = self.file_positions.get(app_name, 0)

            # Se o arquivo diminuiu, significa que foi limpo, recomeçar do início
            if current_size < last_position:
                last_position = 0
                # Resetar estado de captura JSON
                self.capturing_json[app_name] = False
                self.json_buffer[app_name] = []
                self.in_error_block[app_name] = False

            if current_size > last_position:
                with open(file_path, 'r', encoding='utf-8') as f:
                    f.seek(last_position)
                    new_content = f.read()
                    new_lines = new_content.split('\n')

                    # Atualizar posição
                    self.file_positions[app_name] = f.tell()

                    # Filtrar linhas vazias
                    new_lines = [line.strip() for line in new_lines if line.strip()]

        except Exception as e:
            logger.exception(f"ForumEngine: Falha ao ler log de {app_name}: {e}")

        return new_lines

    def process_lines_for_json(self, lines: List[str], app_name: str) -> List[str]:
        """Processar linhas para capturar conteúdo JSON multilinha

        Implementa filtragem de bloco ERROR: se encontrar log de nível ERROR, recusar processamento até encontrar próximo log de nível INFO
        """
        captured_contents = []

        # Inicializar estado
        if app_name not in self.capturing_json:
            self.capturing_json[app_name] = False
            self.json_buffer[app_name] = []
        if app_name not in self.in_error_block:
            self.in_error_block[app_name] = False

        for line in lines:
            if not line.strip():
                continue

            # Primeiro verificar nível do log, atualizar estado do bloco ERROR
            log_level = self.get_log_level(line)
            if log_level == 'ERROR':
                # Encontrou ERROR, entrar no estado de bloco ERROR
                self.in_error_block[app_name] = True
                # Se está capturando JSON, parar imediatamente e limpar buffer
                if self.capturing_json[app_name]:
                    self.capturing_json[app_name] = False
                    self.json_buffer[app_name] = []
                # Pular linha atual, não processar
                continue
            elif log_level == 'INFO':
                # Encontrou INFO, sair do estado de bloco ERROR
                self.in_error_block[app_name] = False
            # Outros níveis (WARNING, DEBUG etc.) mantêm estado atual

            # Se está no bloco ERROR, recusar processar todo conteúdo
            if self.in_error_block[app_name]:
                # Se está capturando JSON, parar imediatamente e limpar buffer
                if self.capturing_json[app_name]:
                    self.capturing_json[app_name] = False
                    self.json_buffer[app_name] = []
                # Pular linha atual, não processar
                continue

            # Verificar se é linha de nó alvo e marcador de início JSON
            is_target = self.is_target_log_line(line)
            is_json_start = self.is_json_start_line(line)

            # Apenas a saída JSON do nó alvo (SummaryNode) deve ser capturada
            # Filtrar saída de outros nós como SearchNode (eles não são nós alvo, mesmo tendo JSON não serão capturados)
            if is_target and is_json_start:
                # Iniciar captura JSON (deve ser nó alvo e conter "清理后的输出: {")
                self.capturing_json[app_name] = True
                self.json_buffer[app_name] = [line]
                self.json_start_line[app_name] = line

                # Verificar se é JSON de linha única
                if line.strip().endswith("}"):
                    # JSON de linha única, processar imediatamente
                    content = self.extract_json_content([line])
                    if content:  # Apenas conteúdo analisado com sucesso será registrado
                        # Remover tags duplicadas e formatar
                        clean_content = self._clean_content_tags(content, app_name)
                        captured_contents.append(f"{clean_content}")
                    self.capturing_json[app_name] = False
                    self.json_buffer[app_name] = []

            elif is_target and self.is_valuable_content(line):
                # Outro conteúdo valioso do SummaryNode (deve ser nó alvo e ter valor)
                clean_content = self._clean_content_tags(self.extract_node_content(line), app_name)
                captured_contents.append(f"{clean_content}")

            elif self.capturing_json[app_name]:
                # Linhas subsequentes da captura JSON em andamento
                self.json_buffer[app_name].append(line)

                # Verificar se é fim do JSON
                # Primeiro limpar timestamp, depois determinar se a linha limpa é marcador de fim
                cleaned_line = line.strip()
                # Limpar timestamp formato antigo: [HH:MM:SS]
                cleaned_line = re.sub(r'^\[\d{2}:\d{2}:\d{2}\]\s*', '', cleaned_line)
                # Limpar timestamp formato novo: YYYY-MM-DD HH:mm:ss.SSS | LEVEL | module:function:line -
                cleaned_line = re.sub(r'^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}\s*\|\s*[A-Z]+\s*\|\s*[^|]+?\s*-\s*', '', cleaned_line)
                cleaned_line = cleaned_line.strip()

                # Após limpeza, determinar se é marcador de fim
                if cleaned_line == "}" or cleaned_line == "] }":
                    # Fim do JSON, processar JSON completo
                    content = self.extract_json_content(self.json_buffer[app_name])
                    if content:  # Apenas conteúdo analisado com sucesso será registrado
                        # Remover tags duplicadas e formatar
                        clean_content = self._clean_content_tags(content, app_name)
                        captured_contents.append(f"{clean_content}")

                    # Resetar estado
                    self.capturing_json[app_name] = False
                    self.json_buffer[app_name] = []

        return captured_contents

    def _trigger_host_speech(self):
        """Disparar discurso do moderador (execução síncrona)"""
        if not HOST_AVAILABLE or self.is_host_generating:
            return

        try:
            # Definir flag de geração
            self.is_host_generating = True

            # Obter 5 discursos do buffer
            recent_speeches = self.agent_speeches_buffer[:5]
            if len(recent_speeches) < 5:
                self.is_host_generating = False
                return

            logger.info("ForumEngine: Gerando discurso do moderador...")

            # Chamar moderador para gerar discurso (passando os 5 mais recentes)
            host_speech = generate_host_speech(recent_speeches)

            if host_speech:
                # Escrever discurso do moderador no forum.log
                self.write_to_forum_log(host_speech, "HOST")
                logger.info(f"ForumEngine: Discurso do moderador registrado")

                # Limpar os 5 discursos processados
                self.agent_speeches_buffer = self.agent_speeches_buffer[5:]
            else:
                logger.error("ForumEngine: Falha na geração do discurso do moderador")

            # Resetar flag de geração
            self.is_host_generating = False

        except Exception as e:
            logger.exception(f"ForumEngine: Erro ao disparar discurso do moderador: {e}")
            self.is_host_generating = False

    def _clean_content_tags(self, content: str, app_name: str) -> str:
        """Limpar tags duplicadas e prefixos excessivos no conteúdo"""
        if not content:
            return content

        # Primeiro remover todos os possíveis formatos de tag (incluindo [INSIGHT], [MEDIA], [QUERY] etc.)
        # Usar método de limpeza mais intensivo
        all_app_names = ['INSIGHT', 'MEDIA', 'QUERY']

        for name in all_app_names:
            # Remover formato [APP_NAME] (case insensitive)
            content = re.sub(rf'\[{name}\]\s*', '', content, flags=re.IGNORECASE)
            # Remover formato APP_NAME isolado
            content = re.sub(rf'^{name}\s+', '', content, flags=re.IGNORECASE)

        # Remover quaisquer outras tags entre colchetes
        content = re.sub(r'^\[.*?\]\s*', '', content)

        # Remover possíveis espaços duplicados
        content = re.sub(r'\s+', ' ', content)

        return content.strip()

    def monitor_logs(self):
        """Monitorar arquivos de log de forma inteligente"""
        logger.info("ForumEngine: Criando fórum...")

        # Inicializar contagem de linhas e posições dos arquivos - registrar estado atual como linha de base
        for app_name, log_file in self.monitored_logs.items():
            self.file_line_counts[app_name] = self.get_file_line_count(log_file)
            self.file_positions[app_name] = self.get_file_size(log_file)
            self.capturing_json[app_name] = False
            self.json_buffer[app_name] = []
            self.in_error_block[app_name] = False
            # logger.info(f"ForumEngine: {app_name} linhas de base: {self.file_line_counts[app_name]}")

        while self.is_monitoring:
            try:
                # Detectar alterações nos três arquivos de log simultaneamente
                any_growth = False
                any_shrink = False
                captured_any = False

                # Processar cada arquivo de log independentemente
                for app_name, log_file in self.monitored_logs.items():
                    current_lines = self.get_file_line_count(log_file)
                    previous_lines = self.file_line_counts.get(app_name, 0)

                    if current_lines > previous_lines:
                        any_growth = True
                        # Ler imediatamente o conteúdo adicionado
                        new_lines = self.read_new_lines(log_file, app_name)

                        # Primeiro verificar se precisa disparar busca (disparar apenas uma vez)
                        if not self.is_searching:
                            for line in new_lines:
                                # Verificar se contém padrão de nó alvo (suporta múltiplos formatos)
                                if line.strip() and self.is_target_log_line(line):
                                    # Confirmar ainda mais se é nó de resumo inicial (FirstSummaryNode ou contém "正在生成首次段落总结")
                                    if 'FirstSummaryNode' in line or '正在生成首次段落总结' in line:
                                        logger.info(f"ForumEngine: Detectado primeira publicação de conteúdo no fórum em {app_name}")
                                        self.is_searching = True
                                        self.search_inactive_count = 0
                                        # Limpar forum.log para iniciar nova sessão
                                        self.clear_forum_log()
                                        break  # Encontrar um já é suficiente, sair do loop

                        # Processar todo conteúdo adicionado (se no estado de busca)
                        if self.is_searching:
                            # Usar nova lógica de processamento
                            captured_contents = self.process_lines_for_json(new_lines, app_name)

                            for content in captured_contents:
                                # Converter app_name para maiúsculo como tag (ex: insight -> INSIGHT)
                                source_tag = app_name.upper()
                                self.write_to_forum_log(content, source_tag)
                                # logger.info(f"ForumEngine: Capturado - {content}")
                                captured_any = True

                                # Adicionar discurso ao buffer (formatar como linha de log completa)
                                timestamp = datetime.now().strftime('%H:%M:%S')
                                log_line = f"[{timestamp}] [{source_tag}] {content}"
                                self.agent_speeches_buffer.append(log_line)

                                # Verificar se precisa disparar discurso do moderador
                                if len(self.agent_speeches_buffer) >= self.host_speech_threshold and not self.is_host_generating:
                                    # Disparar discurso do moderador sincronamente
                                    self._trigger_host_speech()

                    elif current_lines < previous_lines:
                        any_shrink = True
                        # logger.info(f"ForumEngine: Detectada redução no log de {app_name}, resetando linha de base")
                        # Resetar posição do arquivo para o fim do novo arquivo
                        self.file_positions[app_name] = self.get_file_size(log_file)
                        # Resetar estado de captura JSON
                        self.capturing_json[app_name] = False
                        self.json_buffer[app_name] = []
                        self.in_error_block[app_name] = False

                    # Atualizar registro de contagem de linhas
                    self.file_line_counts[app_name] = current_lines

                # Verificar se deve encerrar a sessão de busca atual
                if self.is_searching:
                    if any_shrink:
                        # Log diminuiu, encerrar sessão de busca atual, resetar para estado de espera
                        # logger.info("ForumEngine: Log diminuiu, encerrando sessão de busca atual, voltando ao estado de espera")
                        self.is_searching = False
                        self.search_inactive_count = 0
                        # Resetar estado relacionado ao moderador
                        self.agent_speeches_buffer = []
                        self.is_host_generating = False
                        # Escrever marcador de encerramento
                        end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        self.write_to_forum_log(f"=== ForumEngine fórum encerrado - {end_time} ===", "SYSTEM")
                        # logger.info("ForumEngine: Linha de base resetada, aguardando próximo disparo do FirstSummaryNode")
                    elif not any_growth and not captured_any:
                        # Sem crescimento e sem captura de conteúdo, incrementar contador de inatividade
                        self.search_inactive_count += 1
                        if self.search_inactive_count >= 7200:  # Timeout por inatividade, encerrar automaticamente
                            logger.info("ForumEngine: Longo período sem atividade, encerrando fórum")
                            self.is_searching = False
                            self.search_inactive_count = 0
                            # Resetar estado relacionado ao moderador
                            self.agent_speeches_buffer = []
                            self.is_host_generating = False
                            # Escrever marcador de encerramento
                            end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            self.write_to_forum_log(f"=== ForumEngine fórum encerrado - {end_time} ===", "SYSTEM")
                    else:
                        self.search_inactive_count = 0  # Resetar contador

                # Breve pausa
                time.sleep(1)

            except Exception as e:
                logger.exception(f"ForumEngine: Erro durante registro do fórum: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(2)

        logger.info("ForumEngine: Parando arquivo de log do fórum")

    def start_monitoring(self):
        """Iniciar monitoramento inteligente"""
        if self.is_monitoring:
            logger.info("ForumEngine: Fórum já está em execução")
            return False

        try:
            # Iniciar monitoramento
            self.is_monitoring = True
            self.monitor_thread = threading.Thread(target=self.monitor_logs, daemon=True)
            self.monitor_thread.start()

            logger.info("ForumEngine: Fórum iniciado")
            return True

        except Exception as e:
            logger.exception(f"ForumEngine: Falha ao iniciar fórum: {e}")
            self.is_monitoring = False
            return False

    def stop_monitoring(self):
        """Parar monitoramento"""
        if not self.is_monitoring:
            logger.info("ForumEngine: Fórum não está em execução")
            return

        try:
            self.is_monitoring = False

            if self.monitor_thread and self.monitor_thread.is_alive():
                self.monitor_thread.join(timeout=2)

            # Escrever marcador de encerramento
            end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            self.write_to_forum_log(f"=== ForumEngine fórum encerrado - {end_time} ===", "SYSTEM")

            logger.info("ForumEngine: Fórum parado")

        except Exception as e:
            logger.exception(f"ForumEngine: Falha ao parar fórum: {e}")

    def get_forum_log_content(self) -> List[str]:
        """Obter conteúdo do forum.log"""
        try:
            if not self.forum_log_file.exists():
                return []

            with open(self.forum_log_file, 'r', encoding='utf-8') as f:
                return [line.rstrip('\n\r') for line in f.readlines()]

        except Exception as e:
            logger.exception(f"ForumEngine: Falha ao ler forum.log: {e}")
            return []

    def fix_json_string(self, json_text: str) -> str:
        """Corrigir problemas comuns em strings JSON, especialmente aspas duplas não escapadas"""
        try:
            # Tentar analisar diretamente, se bem-sucedido retornar texto original
            json.loads(json_text)
            return json_text
        except json.JSONDecodeError:
            pass

        # Corrigir problema de aspas duplas não escapadas
        # Este é um método de correção mais inteligente, tratando especificamente aspas duplas dentro de valores de string

        try:
            # Usar método de máquina de estados para corrigir JSON
            # Percorrer caracteres, rastreando se está dentro de um valor de string

            fixed_text = ""
            i = 0
            in_string = False
            escape_next = False

            while i < len(json_text):
                char = json_text[i]

                if escape_next:
                    # Processar caractere de escape
                    fixed_text += char
                    escape_next = False
                    i += 1
                    continue

                if char == '\\':
                    # Caractere de escape
                    fixed_text += char
                    escape_next = True
                    i += 1
                    continue

                if char == '"' and not escape_next:
                    # Encontrou aspas duplas
                    if in_string:
                        # Dentro de uma string, verificar próximo caractere
                        # Se próximo caractere é dois-pontos, vírgula ou chave, significa fim da string
                        next_char_pos = i + 1
                        while next_char_pos < len(json_text) and json_text[next_char_pos].isspace():
                            next_char_pos += 1

                        if next_char_pos < len(json_text):
                            next_char = json_text[next_char_pos]
                            if next_char in [':', ',', '}']:
                                # Este é o fim da string, sair do estado de string
                                in_string = False
                                fixed_text += char
                            else:
                                # Estas são aspas dentro da string, precisam ser escapadas
                                fixed_text += '\\"'
                        else:
                            # Fim do arquivo, sair do estado de string
                            in_string = False
                            fixed_text += char
                    else:
                        # Início da string
                        in_string = True
                        fixed_text += char
                else:
                    # Outros caracteres
                    fixed_text += char

                i += 1

            # Tentar analisar JSON corrigido
            try:
                json.loads(fixed_text)
                return fixed_text
            except json.JSONDecodeError:
                # Correção falhou, retornar None
                return None

        except Exception:
            return None

# Instância global do monitor
_monitor_instance = None

def get_monitor() -> LogMonitor:
    """Obter instância global do monitor"""
    global _monitor_instance
    if _monitor_instance is None:
        _monitor_instance = LogMonitor()
    return _monitor_instance

def start_forum_monitoring():
    """Iniciar monitoramento inteligente do ForumEngine"""
    return get_monitor().start_monitoring()

def stop_forum_monitoring():
    """Parar monitoramento do ForumEngine"""
    get_monitor().stop_monitoring()

def get_forum_log():
    """Obter conteúdo do forum.log"""
    return get_monitor().get_forum_log_content()
