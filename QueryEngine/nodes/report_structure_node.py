"""
Nó de geração de estrutura do relatório
Responsável por gerar a estrutura geral do relatório com base na consulta
"""

import json
from typing import Dict, Any, List
from json.decoder import JSONDecodeError
from loguru import logger

from .base_node import StateMutationNode
from ..state.state import State
from ..prompts import SYSTEM_PROMPT_REPORT_STRUCTURE
from ..utils.text_processing import (
    remove_reasoning_from_output,
    clean_json_tags,
    extract_clean_response,
    fix_incomplete_json
)


class ReportStructureNode(StateMutationNode):
    """Nó que gera a estrutura do relatório"""

    def __init__(self, llm_client, query: str):
        """
        Inicializar nó de estrutura do relatório

        Args:
            llm_client: Cliente LLM
            query: Consulta do usuário
        """
        super().__init__(llm_client, "ReportStructureNode")
        self.query = query

    def validate_input(self, input_data: Any) -> bool:
        """Validar dados de entrada"""
        return isinstance(self.query, str) and len(self.query.strip()) > 0

    def run(self, input_data: Any = None, **kwargs) -> List[Dict[str, str]]:
        """
        Chamar LLM para gerar estrutura do relatório

        Args:
            input_data: Dados de entrada (não utilizado aqui, usa a query da inicialização)
            **kwargs: Parâmetros adicionais

        Returns:
            Lista da estrutura do relatório
        """
        try:
            logger.info(f"Gerando estrutura do relatório para a consulta: {self.query}")

            # Chamar LLM
            response = self.llm_client.stream_invoke_to_string(SYSTEM_PROMPT_REPORT_STRUCTURE, self.query)

            # Processar resposta
            processed_response = self.process_output(response)

            logger.info(f"Geradas com sucesso {len(processed_response)} estruturas de parágrafo")
            return processed_response

        except Exception as e:
            logger.exception(f"Falha ao gerar estrutura do relatório: {str(e)}")
            raise e

    def process_output(self, output: str) -> List[Dict[str, str]]:
        """
        Processar saída do LLM, extrair estrutura do relatório

        Args:
            output: Saída bruta do LLM

        Returns:
            Lista da estrutura do relatório processada
        """
        try:
            # Limpar texto da resposta
            cleaned_output = remove_reasoning_from_output(output)
            cleaned_output = clean_json_tags(cleaned_output)

            # Registrar saída limpa para depuração
            logger.info(f"Saída após limpeza: {cleaned_output}")

            # Analisar JSON
            try:
                report_structure = json.loads(cleaned_output)
                logger.info("Análise JSON bem-sucedida")
            except JSONDecodeError as e:
                logger.error(f"Falha na análise JSON: {str(e)}")
                # Usar método de extração mais robusto
                report_structure = extract_clean_response(cleaned_output)
                if "error" in report_structure:
                    logger.error("Falha na análise JSON, tentando reparar...")
                    # Tentar reparar JSON
                    fixed_json = fix_incomplete_json(cleaned_output)
                    if fixed_json:
                        try:
                            report_structure = json.loads(fixed_json)
                            logger.info("Reparo do JSON bem-sucedido")
                        except JSONDecodeError:
                            logger.error("Falha no reparo do JSON")
                            # Retornar estrutura padrão
                            return self._generate_default_structure()
                    else:
                        logger.error("Não foi possível reparar o JSON, usando estrutura padrão")
                        return self._generate_default_structure()

            # Validar estrutura
            if not isinstance(report_structure, list):
                logger.info("Estrutura do relatório não é uma lista, tentando converter...")
                if isinstance(report_structure, dict):
                    # Se for um único objeto, encapsular em lista
                    report_structure = [report_structure]
                else:
                    logger.error("Formato da estrutura do relatório inválido, usando estrutura padrão")
                    return self._generate_default_structure()

            # Validar cada parágrafo
            validated_structure = []
            for i, paragraph in enumerate(report_structure):
                if not isinstance(paragraph, dict):
                    logger.warning(f"Parágrafo {i+1} não está em formato de dicionário, ignorando")
                    continue

                title = paragraph.get("title", f"Parágrafo {i+1}")
                content = paragraph.get("content", "")

                if not title or not content:
                    logger.warning(f"Parágrafo {i+1} sem título ou conteúdo, ignorando")
                    continue

                validated_structure.append({
                    "title": title,
                    "content": content
                })

            if not validated_structure:
                logger.warning("Nenhuma estrutura de parágrafo válida, usando estrutura padrão")
                return self._generate_default_structure()

            logger.info(f"Validadas com sucesso {len(validated_structure)} estruturas de parágrafo")
            return validated_structure

        except Exception as e:
            logger.exception(f"Falha ao processar saída: {str(e)}")
            return self._generate_default_structure()

    def _generate_default_structure(self) -> List[Dict[str, str]]:
        """
        Gerar estrutura padrão do relatório

        Returns:
            Lista da estrutura padrão do relatório
        """
        logger.info("Gerando estrutura padrão do relatório")
        return [
            {
                "title": "Visão geral da pesquisa",
                "content": "Visão geral e análise do tema da consulta"
            },
            {
                "title": "Análise aprofundada",
                "content": "Análise aprofundada dos diversos aspectos do tema da consulta"
            }
        ]

    def mutate_state(self, input_data: Any = None, state: State = None, **kwargs) -> State:
        """
        Gravar estrutura do relatório no estado

        Args:
            input_data: Dados de entrada
            state: Estado atual; se None, cria um novo estado
            **kwargs: Parâmetros adicionais

        Returns:
            Estado atualizado
        """
        if state is None:
            state = State()

        try:
            # Gerar estrutura do relatório
            report_structure = self.run(input_data, **kwargs)

            # Definir consulta e título do relatório
            state.query = self.query
            if not state.report_title:
                state.report_title = f"Relatório de pesquisa profunda sobre '{self.query}'"

            # Adicionar parágrafos ao estado
            for paragraph_data in report_structure:
                state.add_paragraph(
                    title=paragraph_data["title"],
                    content=paragraph_data["content"]
                )

            logger.info(f"{len(report_structure)} parágrafos adicionados ao estado")
            return state

        except Exception as e:
            logger.exception(f"Falha na atualização do estado: {str(e)}")
            raise e
