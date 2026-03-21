"""
Implementação do nó de resumo
Responsável por gerar e atualizar conteúdo de parágrafos com base nos resultados de busca
"""

import json
from typing import Dict, Any, List
from json.decoder import JSONDecodeError
from loguru import logger

from .base_node import StateMutationNode
from ..state.state import State
from ..prompts import SYSTEM_PROMPT_FIRST_SUMMARY, SYSTEM_PROMPT_REFLECTION_SUMMARY
from ..utils.text_processing import (
    remove_reasoning_from_output,
    clean_json_tags,
    extract_clean_response,
    fix_incomplete_json,
    format_search_results_for_prompt
)

# Importar ferramenta de leitura de fórum
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
try:
    from utils.forum_reader import get_latest_host_speech, format_host_speech_for_prompt
    FORUM_READER_AVAILABLE = True
except ImportError:
    FORUM_READER_AVAILABLE = False
    logger.warning("Não foi possível importar o módulo forum_reader, a funcionalidade de leitura de falas do HOST será ignorada")


class FirstSummaryNode(StateMutationNode):
    """Nó que gera o primeiro resumo do parágrafo com base nos resultados de busca"""

    def __init__(self, llm_client):
        """
        Inicializar nó de primeiro resumo

        Args:
            llm_client: Cliente LLM
        """
        super().__init__(llm_client, "FirstSummaryNode")

    def validate_input(self, input_data: Any) -> bool:
        """Validar dados de entrada"""
        if isinstance(input_data, str):
            try:
                data = json.loads(input_data)
                required_fields = ["title", "content", "search_query", "search_results"]
                return all(field in data for field in required_fields)
            except JSONDecodeError:
                return False
        elif isinstance(input_data, dict):
            required_fields = ["title", "content", "search_query", "search_results"]
            return all(field in input_data for field in required_fields)
        return False

    def run(self, input_data: Any, **kwargs) -> str:
        """
        Chamar LLM para gerar resumo do parágrafo

        Args:
            input_data: Dados contendo title, content, search_query e search_results
            **kwargs: Parâmetros adicionais

        Returns:
            Conteúdo do resumo do parágrafo
        """
        try:
            if not self.validate_input(input_data):
                raise ValueError("Formato de dados de entrada incorreto")

            # Preparar dados de entrada
            if isinstance(input_data, str):
                data = json.loads(input_data)
            else:
                data = input_data.copy() if isinstance(input_data, dict) else input_data

            # Ler a fala mais recente do HOST (se disponível)
            if FORUM_READER_AVAILABLE:
                try:
                    host_speech = get_latest_host_speech()
                    if host_speech:
                        # Adicionar fala do HOST aos dados de entrada
                        data['host_speech'] = host_speech
                        logger.info(f"Fala do HOST lida, comprimento: {len(host_speech)} caracteres")
                except Exception as e:
                    logger.exception(f"Falha ao ler fala do HOST: {str(e)}")

            # Converter para string JSON
            message = json.dumps(data, ensure_ascii=False)

            # Se houver fala do HOST, adicionar antes da mensagem como referência
            if FORUM_READER_AVAILABLE and 'host_speech' in data and data['host_speech']:
                formatted_host = format_host_speech_for_prompt(data['host_speech'])
                message = formatted_host + "\n" + message

            logger.info("Gerando primeiro resumo do parágrafo")

            # Chamar LLM para gerar resumo (streaming, concatenação segura UTF-8)
            response = self.llm_client.stream_invoke_to_string(
                SYSTEM_PROMPT_FIRST_SUMMARY,
                message,
            )

            # Processar resposta
            processed_response = self.process_output(response)

            logger.info("Primeiro resumo do parágrafo gerado com sucesso")
            return processed_response

        except Exception as e:
            logger.exception(f"Falha ao gerar primeiro resumo: {str(e)}")
            raise e

    def process_output(self, output: str) -> str:
        """
        Processar saída do LLM, extrair conteúdo do parágrafo

        Args:
            output: Saída bruta do LLM

        Returns:
            Conteúdo do parágrafo
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
                # Tentar reparar JSON
                fixed_json = fix_incomplete_json(cleaned_output)
                if fixed_json:
                    try:
                        result = json.loads(fixed_json)
                        logger.info("Reparo do JSON bem-sucedido")
                    except JSONDecodeError:
                        logger.exception("Falha no reparo do JSON, usando texto limpo diretamente")
                        # Se não estiver em formato JSON, retornar texto limpo diretamente
                        return cleaned_output
                else:
                    logger.exception("Impossível reparar JSON, usando texto limpo diretamente")
                    # Se não estiver em formato JSON, retornar texto limpo diretamente
                    return cleaned_output

            # Extrair conteúdo do parágrafo
            if isinstance(result, dict):
                paragraph_content = result.get("paragraph_latest_state", "")
                if paragraph_content:
                    return paragraph_content

            # Se a extração falhar, retornar texto limpo original
            return cleaned_output

        except Exception as e:
            logger.exception(f"Falha ao processar saída: {str(e)}")
            return "Falha na geração do resumo do parágrafo"

    def mutate_state(self, input_data: Any, state: State, paragraph_index: int, **kwargs) -> State:
        """
        Atualizar o resumo mais recente do parágrafo no estado

        Args:
            input_data: Dados de entrada
            state: Estado atual
            paragraph_index: Índice do parágrafo
            **kwargs: Parâmetros adicionais

        Returns:
            Estado atualizado
        """
        try:
            # Gerar resumo
            summary = self.run(input_data, **kwargs)

            # Atualizar estado
            if 0 <= paragraph_index < len(state.paragraphs):
                state.paragraphs[paragraph_index].research.latest_summary = summary
                logger.info(f"Primeiro resumo do parágrafo {paragraph_index} atualizado")
            else:
                raise ValueError(f"Índice do parágrafo {paragraph_index} fora do intervalo")

            state.update_timestamp()
            return state

        except Exception as e:
            logger.exception(f"Falha na atualização do estado: {str(e)}")
            raise e


class ReflectionSummaryNode(StateMutationNode):
    """Nó que atualiza o resumo do parágrafo com base nos resultados de busca da reflexão"""

    def __init__(self, llm_client):
        """
        Inicializar nó de resumo de reflexão

        Args:
            llm_client: Cliente LLM
        """
        super().__init__(llm_client, "ReflectionSummaryNode")

    def validate_input(self, input_data: Any) -> bool:
        """Validar dados de entrada"""
        if isinstance(input_data, str):
            try:
                data = json.loads(input_data)
                required_fields = ["title", "content", "search_query", "search_results", "paragraph_latest_state"]
                return all(field in data for field in required_fields)
            except JSONDecodeError:
                return False
        elif isinstance(input_data, dict):
            required_fields = ["title", "content", "search_query", "search_results", "paragraph_latest_state"]
            return all(field in input_data for field in required_fields)
        return False

    def run(self, input_data: Any, **kwargs) -> str:
        """
        Chamar LLM para atualizar conteúdo do parágrafo

        Args:
            input_data: Dados contendo informações completas de reflexão
            **kwargs: Parâmetros adicionais

        Returns:
            Conteúdo atualizado do parágrafo
        """
        try:
            if not self.validate_input(input_data):
                raise ValueError("Formato de dados de entrada incorreto")

            # Preparar dados de entrada
            if isinstance(input_data, str):
                data = json.loads(input_data)
            else:
                data = input_data.copy() if isinstance(input_data, dict) else input_data

            # Ler a fala mais recente do HOST (se disponível)
            if FORUM_READER_AVAILABLE:
                try:
                    host_speech = get_latest_host_speech()
                    if host_speech:
                        # Adicionar fala do HOST aos dados de entrada
                        data['host_speech'] = host_speech
                        logger.info(f"Fala do HOST lida, comprimento: {len(host_speech)} caracteres")
                except Exception as e:
                    logger.exception(f"Falha ao ler fala do HOST: {str(e)}")

            # Converter para string JSON
            message = json.dumps(data, ensure_ascii=False)

            # Se houver fala do HOST, adicionar antes da mensagem como referência
            if FORUM_READER_AVAILABLE and 'host_speech' in data and data['host_speech']:
                formatted_host = format_host_speech_for_prompt(data['host_speech'])
                message = formatted_host + "\n" + message

            logger.info("Gerando resumo de reflexão")

            # Chamar LLM para gerar resumo (streaming, concatenação segura UTF-8)
            response = self.llm_client.stream_invoke_to_string(
                SYSTEM_PROMPT_REFLECTION_SUMMARY,
                message,
            )

            # Processar resposta
            processed_response = self.process_output(response)

            logger.info("Resumo de reflexão gerado com sucesso")
            return processed_response

        except Exception as e:
            logger.exception(f"Falha ao gerar resumo de reflexão: {str(e)}")
            raise e

    def process_output(self, output: str) -> str:
        """
        Processar saída do LLM, extrair conteúdo atualizado do parágrafo

        Args:
            output: Saída bruta do LLM

        Returns:
            Conteúdo atualizado do parágrafo
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
                # Tentar reparar JSON
                fixed_json = fix_incomplete_json(cleaned_output)
                if fixed_json:
                    try:
                        result = json.loads(fixed_json)
                        logger.info("Reparo do JSON bem-sucedido")
                    except JSONDecodeError:
                        logger.error("Falha no reparo do JSON, usando texto limpo diretamente")
                        # Se não estiver em formato JSON, retornar texto limpo diretamente
                        return cleaned_output
                else:
                    logger.error("Impossível reparar JSON, usando texto limpo diretamente")
                    # Se não estiver em formato JSON, retornar texto limpo diretamente
                    return cleaned_output

            # Extrair conteúdo atualizado do parágrafo
            if isinstance(result, dict):
                updated_content = result.get("updated_paragraph_latest_state", "")
                if updated_content:
                    return updated_content

            # Se a extração falhar, retornar texto limpo original
            return cleaned_output

        except Exception as e:
            logger.exception(f"Falha ao processar saída: {str(e)}")
            return "Falha na geração do resumo de reflexão"

    def mutate_state(self, input_data: Any, state: State, paragraph_index: int, **kwargs) -> State:
        """
        Gravar resumo atualizado no estado

        Args:
            input_data: Dados de entrada
            state: Estado atual
            paragraph_index: Índice do parágrafo
            **kwargs: Parâmetros adicionais

        Returns:
            Estado atualizado
        """
        try:
            # Gerar resumo atualizado
            updated_summary = self.run(input_data, **kwargs)

            # Atualizar estado
            if 0 <= paragraph_index < len(state.paragraphs):
                state.paragraphs[paragraph_index].research.latest_summary = updated_summary
                state.paragraphs[paragraph_index].research.increment_reflection()
                logger.info(f"Resumo de reflexão do parágrafo {paragraph_index} atualizado")
            else:
                raise ValueError(f"Índice do parágrafo {paragraph_index} fora do intervalo")

            state.update_timestamp()
            return state

        except Exception as e:
            logger.exception(f"Falha na atualização do estado: {str(e)}")
            raise e
