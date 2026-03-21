"""
Classe base dos nós
Define a interface base de todos os nós de processamento
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from loguru import logger
from ..llms.base import LLMClient
from ..state.state import State


class BaseNode(ABC):
    """Classe base dos nós"""

    def __init__(self, llm_client: LLMClient, node_name: str = ""):
        """
        Inicializar nó

        Args:
            llm_client: Cliente LLM
            node_name: Nome do nó
        """
        self.llm_client = llm_client
        self.node_name = node_name or self.__class__.__name__

    @abstractmethod
    def run(self, input_data: Any, **kwargs) -> Any:
        """
        Executar lógica de processamento do nó

        Args:
            input_data: Dados de entrada
            **kwargs: Parâmetros adicionais

        Returns:
            Resultado do processamento
        """
        pass

    def validate_input(self, input_data: Any) -> bool:
        """
        Validar dados de entrada

        Args:
            input_data: Dados de entrada

        Returns:
            Se a validação foi aprovada
        """
        return True

    def process_output(self, output: Any) -> Any:
        """
        Processar dados de saída

        Args:
            output: Saída bruta

        Returns:
            Saída processada
        """
        return output

    def log_info(self, message: str):
        """Registrar log informativo"""
        logger.info(f"[{self.node_name}] {message}")

    def log_warning(self, message: str):
        """Registrar log de aviso"""
        logger.warning(f"[{self.node_name}] Aviso: {message}")

    def log_error(self, message: str):
        """Registrar log de erro"""
        logger.error(f"[{self.node_name}] Erro: {message}")


class StateMutationNode(BaseNode):
    """Classe base de nó com funcionalidade de modificação de estado"""

    @abstractmethod
    def mutate_state(self, input_data: Any, state: State, **kwargs) -> State:
        """
        Modificar estado

        Args:
            input_data: Dados de entrada
            state: Estado atual
            **kwargs: Parâmetros adicionais

        Returns:
            Estado modificado
        """
        pass
