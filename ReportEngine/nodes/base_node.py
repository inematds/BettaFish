"""
Classe base dos nos do Report Engine.

Todos os nos de inferencia de alto nivel herdam desta classe, unificando interfaces de log, validacao de entrada e alteracao de estado.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from ..llms.base import LLMClient
from ..state.state import ReportState
from loguru import logger

class BaseNode(ABC):
    """
    Classe base do no.

    Implementacao unificada de ferramentas de log, hooks de entrada/saida e injecao de dependencia do cliente LLM,
    permitindo que todos os nos se concentrem apenas na logica de negocios.
    """
    
    def __init__(self, llm_client: LLMClient, node_name: str = ""):
        """
        Inicializar no
        
        Args:
            llm_client: Cliente LLM
            node_name: Nome do no

        BaseNode salva o nome do no para prefixo unificado de log.
        """
        self.llm_client = llm_client
        self.node_name = node_name or self.__class__.__name__
    
    @abstractmethod
    def run(self, input_data: Any, **kwargs) -> Any:
        """
        Executar logica de processamento do no
        
        Args:
            input_data: Dados de entrada
            **kwargs: Parametros adicionais
            
        Returns:
            Resultado do processamento
        """
        pass
    
    def validate_input(self, input_data: Any) -> bool:
        """
        Validar dados de entrada.
        Aprovacao direta por padrao, subclasses podem sobrescrever para implementar verificacao de campos conforme necessario.
        
        Args:
            input_data: Dados de entrada
            
        Returns:
            Se a validacao foi aprovada
        """
        return True
    
    def process_output(self, output: Any) -> Any:
        """
        Processar dados de saida.
        Subclasses podem sobrescrever para estruturacao ou validacao.
        
        Args:
            output: Saida original
            
        Returns:
            Saida processada
        """
        return output
    
    def log_info(self, message: str):
        """Registrar log de informacao com nome do no como prefixo automaticamente."""
        formatted_message = f"[{self.node_name}] {message}"
        logger.info(formatted_message)
    
    def log_error(self, message: str):
        """Registrar log de erro para facilitar depuracao."""
        formatted_message = f"[{self.node_name}] {message}"
        logger.error(formatted_message)


class StateMutationNode(BaseNode):
    """
    Classe base do no com funcionalidade de modificacao de estado.

    Aplicavel a cenarios onde o no precisa escrever diretamente no ReportState.
    """
    
    @abstractmethod
    def mutate_state(self, input_data: Any, state: ReportState, **kwargs) -> ReportState:
        """
        Modificar estado.

        Subclasses devem retornar novo objeto de estado ou modificar no local e retornar, para registro no pipeline.
        
        Args:
            input_data: Dados de entrada
            state: Estado atual
            **kwargs: Parametros adicionais
            
        Returns:
            Estado modificado
        """
        pass
