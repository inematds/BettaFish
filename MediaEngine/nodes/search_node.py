"""
Implementação do nó de busca
Responsável por gerar consultas de busca e consultas de reflexão
"""

import json
from typing import Dict, Any
from json.decoder import JSONDecodeError
from loguru import logger

from .base_node import BaseNode
from ..prompts import SYSTEM_PROMPT_FIRST_SEARCH, SYSTEM_PROMPT_REFLECTION
from ..utils.text_processing import (
    remove_reasoning_from_output,
    clean_json_tags,
    extract_clean_response,
    fix_incomplete_json
)


class FirstSearchNode(BaseNode):
    """Nó que gera a primeira consulta de busca para o parágrafo"""

    def __init__(self, llm_client):
        """
        Inicializar nó de primeira busca

        Args:
            llm_client: Cliente LLM
        """
        super().__init__(llm_client, "FirstSearchNode")

    def validate_input(self, input_data: Any) -> bool:
        """Validar dados de entrada"""
        if isinstance(input_data, str):
            try:
                data = json.loads(input_data)
                return "title" in data and "content" in data
            except JSONDecodeError:
                return False
        elif isinstance(input_data, dict):
            return "title" in input_data and "content" in input_data
        return False

    def run(self, input_data: Any, **kwargs) -> Dict[str, str]:
        """
        Chamar LLM para gerar consulta de busca e justificativa

        Args:
            input_data: String ou dicionário contendo title e content
            **kwargs: Parâmetros adicionais

        Returns:
            Dicionário contendo search_query e reasoning
        """
        try:
            if not self.validate_input(input_data):
                raise ValueError("Formato de dados de entrada incorreto, necessário conter campos title e content")

            # Preparar dados de entrada
            if isinstance(input_data, str):
                message = input_data
            else:
                message = json.dumps(input_data, ensure_ascii=False)

            logger.info("Gerando primeira consulta de busca")

            # Chamar LLM
            response = self.llm_client.stream_invoke_to_string(SYSTEM_PROMPT_FIRST_SEARCH, message)

            # Processar resposta
            processed_response = self.process_output(response)

            logger.info(f"Consulta de busca gerada: {processed_response.get('search_query', 'N/A')}")
            return processed_response

        except Exception as e:
            logger.exception(f"Falha ao gerar primeira consulta de busca: {str(e)}")
            raise e

    def process_output(self, output: str) -> Dict[str, str]:
        """
        Processar saída do LLM, extrair consulta de busca e raciocínio

        Args:
            output: Saída bruta do LLM

        Returns:
            Dicionário contendo search_query e reasoning
        """
        try:
            # Limpar texto da resposta
            cleaned_output = remove_reasoning_from_output(output)
            cleaned_output = clean_json_tags(cleaned_output)

            # Registrar saída limpa para depuração
            logger.info(f"Saída limpa: {cleaned_output}")

            # Analisar JSON
            try:
                result = json.loads(cleaned_output)
                logger.info("Análise JSON bem-sucedida")
            except JSONDecodeError as e:
                logger.error(f"Falha na análise JSON: {str(e)}")
                # Usar método de extração mais robusto
                result = extract_clean_response(cleaned_output)
                if "error" in result:
                    logger.error("Falha na análise JSON, tentando reparar...")
                    # Tentar reparar JSON
                    fixed_json = fix_incomplete_json(cleaned_output)
                    if fixed_json:
                        try:
                            result = json.loads(fixed_json)
                            logger.info("Reparo do JSON bem-sucedido")
                        except JSONDecodeError:
                            logger.error("Falha no reparo do JSON")
                            # Retornar consulta padrão
                            return self._get_default_search_query()
                    else:
                        logger.error("Impossível reparar JSON, usando consulta padrão")
                        return self._get_default_search_query()

            # Validar e limpar resultado
            search_query = result.get("search_query", "")
            reasoning = result.get("reasoning", "")

            if not search_query:
                logger.warning("Consulta de busca não encontrada, usando consulta padrão")
                return self._get_default_search_query()

            return {
                "search_query": search_query,
                "reasoning": reasoning
            }

        except Exception as e:
            self.log_error(f"Falha ao processar saída: {str(e)}")
            # Retornar consulta padrão
            return self._get_default_search_query()

    def _get_default_search_query(self) -> Dict[str, str]:
        """
        Obter consulta de busca padrão

        Returns:
            Dicionário de consulta de busca padrão
        """
        return {
            "search_query": "pesquisa sobre tema relacionado",
            "reasoning": "Devido a falha na análise, usando consulta de busca padrão"
        }


class ReflectionNode(BaseNode):
    """Nó que reflete sobre o parágrafo e gera nova consulta de busca"""

    def __init__(self, llm_client):
        """
        Inicializar nó de reflexão

        Args:
            llm_client: Cliente LLM
        """
        super().__init__(llm_client, "ReflectionNode")

    def validate_input(self, input_data: Any) -> bool:
        """Validar dados de entrada"""
        if isinstance(input_data, str):
            try:
                data = json.loads(input_data)
                required_fields = ["title", "content", "paragraph_latest_state"]
                return all(field in data for field in required_fields)
            except JSONDecodeError:
                return False
        elif isinstance(input_data, dict):
            required_fields = ["title", "content", "paragraph_latest_state"]
            return all(field in input_data for field in required_fields)
        return False

    def run(self, input_data: Any, **kwargs) -> Dict[str, str]:
        """
        Chamar LLM para refletir e gerar consulta de busca

        Args:
            input_data: String ou dicionário contendo title, content e paragraph_latest_state
            **kwargs: Parâmetros adicionais

        Returns:
            Dicionário contendo search_query e reasoning
        """
        try:
            if not self.validate_input(input_data):
                raise ValueError("Formato de dados de entrada incorreto, necessário conter campos title, content e paragraph_latest_state")

            # Preparar dados de entrada
            if isinstance(input_data, str):
                message = input_data
            else:
                message = json.dumps(input_data, ensure_ascii=False)

            logger.info("Realizando reflexão e gerando nova consulta de busca")

            # Chamar LLM
            response = self.llm_client.stream_invoke_to_string(SYSTEM_PROMPT_REFLECTION, message)

            # Processar resposta
            processed_response = self.process_output(response)

            logger.info(f"Consulta de busca gerada pela reflexão: {processed_response.get('search_query', 'N/A')}")
            return processed_response

        except Exception as e:
            logger.exception(f"Falha ao gerar consulta de busca na reflexão: {str(e)}")
            raise e

    def process_output(self, output: str) -> Dict[str, str]:
        """
        Processar saída do LLM, extrair consulta de busca e raciocínio

        Args:
            output: Saída bruta do LLM

        Returns:
            Dicionário contendo search_query e reasoning
        """
        try:
            # Limpar texto da resposta
            cleaned_output = remove_reasoning_from_output(output)
            cleaned_output = clean_json_tags(cleaned_output)

            # Registrar saída limpa para depuração
            logger.info(f"Saída limpa: {cleaned_output}")

            # Analisar JSON
            try:
                result = json.loads(cleaned_output)
                logger.info("Análise JSON bem-sucedida")
            except JSONDecodeError as e:
                logger.error(f"Falha na análise JSON: {str(e)}")
                # Usar método de extração mais robusto
                result = extract_clean_response(cleaned_output)
                if "error" in result:
                    logger.error("Falha na análise JSON, tentando reparar...")
                    # Tentar reparar JSON
                    fixed_json = fix_incomplete_json(cleaned_output)
                    if fixed_json:
                        try:
                            result = json.loads(fixed_json)
                            logger.info("Reparo do JSON bem-sucedido")
                        except JSONDecodeError:
                            logger.error("Falha no reparo do JSON")
                            # Retornar consulta padrão
                            return self._get_default_reflection_query()
                    else:
                        logger.error("Impossível reparar JSON, usando consulta padrão")
                        return self._get_default_reflection_query()

            # Validar e limpar resultado
            search_query = result.get("search_query", "")
            reasoning = result.get("reasoning", "")

            if not search_query:
                logger.warning("Consulta de busca não encontrada, usando consulta padrão")
                return self._get_default_reflection_query()

            return {
                "search_query": search_query,
                "reasoning": reasoning
            }

        except Exception as e:
            logger.exception(f"Falha ao processar saída: {str(e)}")
            # Retornar consulta padrão
            return self._get_default_reflection_query()

    def _get_default_reflection_query(self) -> Dict[str, str]:
        """
        Obter consulta de busca de reflexão padrão

        Returns:
            Dicionário de consulta de busca de reflexão padrão
        """
        return {
            "search_query": "informação complementar de pesquisa aprofundada",
            "reasoning": "Devido a falha na análise, usando consulta de busca de reflexão padrão"
        }
