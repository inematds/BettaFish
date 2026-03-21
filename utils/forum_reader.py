"""
Ferramenta de leitura de logs do Forum
Usada para ler as falas mais recentes do HOST no forum.log
"""

import re
from pathlib import Path
from typing import Optional, List, Dict
from loguru import logger

def get_latest_host_speech(log_dir: str = "logs") -> Optional[str]:
    """
    Obter a fala mais recente do HOST no forum.log

    Args:
        log_dir: Caminho do diretorio de logs

    Returns:
        Conteudo da fala mais recente do HOST, ou None se nao houver
    """
    try:
        forum_log_path = Path(log_dir) / "forum.log"

        if not forum_log_path.exists():
            logger.debug("Arquivo forum.log nao existe")
            return None

        with open(forum_log_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        # Procurar de tras para frente a fala mais recente do HOST
        host_speech = None
        for line in reversed(lines):
            # Formato de correspondencia: [horario] [HOST] conteudo
            match = re.match(r'\[(\d{2}:\d{2}:\d{2})\]\s*\[HOST\]\s*(.+)', line)
            if match:
                _, content = match.groups()
                # Processar caracteres de nova linha escapados, restaurando para quebras de linha reais
                host_speech = content.replace('\\n', '\n').strip()
                break

        if host_speech:
            logger.info(f"Fala mais recente do HOST encontrada, comprimento: {len(host_speech)} caracteres")
        else:
            logger.debug("Nenhuma fala do HOST encontrada")

        return host_speech

    except Exception as e:
        logger.error(f"Falha ao ler forum.log: {str(e)}")
        return None


def get_all_host_speeches(log_dir: str = "logs") -> List[Dict[str, str]]:
    """
    Obter todas as falas do HOST no forum.log

    Args:
        log_dir: Caminho do diretorio de logs

    Returns:
        Lista contendo todas as falas do HOST, cada elemento e um dicionario com timestamp e content
    """
    try:
        forum_log_path = Path(log_dir) / "forum.log"

        if not forum_log_path.exists():
            logger.debug("Arquivo forum.log nao existe")
            return []

        with open(forum_log_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        host_speeches = []
        for line in lines:
            # Formato de correspondencia: [horario] [HOST] conteudo
            match = re.match(r'\[(\d{2}:\d{2}:\d{2})\]\s*\[HOST\]\s*(.+)', line)
            if match:
                timestamp, content = match.groups()
                # Processar caracteres de nova linha escapados
                content = content.replace('\\n', '\n').strip()
                host_speeches.append({
                    'timestamp': timestamp,
                    'content': content
                })

        logger.info(f"Encontradas {len(host_speeches)} falas do HOST")
        return host_speeches

    except Exception as e:
        logger.error(f"Falha ao ler forum.log: {str(e)}")
        return []


def get_recent_agent_speeches(log_dir: str = "logs", limit: int = 5) -> List[Dict[str, str]]:
    """
    Obter as falas mais recentes dos Agents no forum.log (excluindo HOST)

    Args:
        log_dir: Caminho do diretorio de logs
        limit: Numero maximo de falas a retornar

    Returns:
        Lista contendo as falas mais recentes dos Agents
    """
    try:
        forum_log_path = Path(log_dir) / "forum.log"

        if not forum_log_path.exists():
            return []

        with open(forum_log_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        agent_speeches = []
        for line in reversed(lines):  # Ler de tras para frente
            # Formato de correspondencia: [horario] [NOME_AGENT] conteudo
            match = re.match(r'\[(\d{2}:\d{2}:\d{2})\]\s*\[(INSIGHT|MEDIA|QUERY)\]\s*(.+)', line)
            if match:
                timestamp, agent, content = match.groups()
                # Processar caracteres de nova linha escapados
                content = content.replace('\\n', '\n').strip()
                agent_speeches.append({
                    'timestamp': timestamp,
                    'agent': agent,
                    'content': content
                })
                if len(agent_speeches) >= limit:
                    break

        agent_speeches.reverse()  # Restaurar ordem cronologica
        return agent_speeches

    except Exception as e:
        logger.error(f"Falha ao ler forum.log: {str(e)}")
        return []


def format_host_speech_for_prompt(host_speech: str) -> str:
    """
    Formatar a fala do HOST para adicionar ao prompt

    Args:
        host_speech: Conteudo da fala do HOST

    Returns:
        Conteudo formatado
    """
    if not host_speech:
        return ""

    return f"""
### Resumo mais recente do moderador do forum
A seguir esta o resumo e orientacao mais recente do moderador do forum sobre as discussoes dos Agents, por favor considere as opinioes e sugestoes contidas:

{host_speech}

---
"""
