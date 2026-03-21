"""
Nó de formatação do relatório
Responsável por formatar os resultados finais da pesquisa em um relatório Markdown elegante
"""

import json
from typing import List, Dict, Any
from loguru import logger

from .base_node import BaseNode
from ..prompts import SYSTEM_PROMPT_REPORT_FORMATTING
from ..utils.text_processing import (
    remove_reasoning_from_output,
    clean_markdown_tags
)




class ReportFormattingNode(BaseNode):
    """Nó que formata o relatório final"""

    def __init__(self, llm_client):
        """
        Inicializar nó de formatação do relatório

        Args:
            llm_client: Cliente LLM
        """
        super().__init__(llm_client, "ReportFormattingNode")

    def validate_input(self, input_data: Any) -> bool:
        """Validar dados de entrada"""
        if isinstance(input_data, str):
            try:
                data = json.loads(input_data)
                return isinstance(data, list) and all(
                    isinstance(item, dict) and "title" in item and "paragraph_latest_state" in item
                    for item in data
                )
            except:
                return False
        elif isinstance(input_data, list):
            return all(
                isinstance(item, dict) and "title" in item and "paragraph_latest_state" in item
                for item in input_data
            )
        return False

    def run(self, input_data: Any, **kwargs) -> str:
        """
        Chamar LLM para gerar relatório em formato Markdown

        Args:
            input_data: Lista contendo informações de todos os parágrafos
            **kwargs: Parâmetros adicionais

        Returns:
            Relatório formatado em Markdown
        """
        try:
            if not self.validate_input(input_data):
                raise ValueError("Formato de dados de entrada incorreto, necessário lista contendo title e paragraph_latest_state")

            # Preparar dados de entrada
            if isinstance(input_data, str):
                message = input_data
            else:
                message = json.dumps(input_data, ensure_ascii=False)

            logger.info("Formatando relatório final")

            # Chamar LLM (streaming, concatenação segura de UTF-8)
            response = self.llm_client.stream_invoke_to_string(
                SYSTEM_PROMPT_REPORT_FORMATTING,
                message,
            )

            # Processar resposta
            processed_response = self.process_output(response)

            logger.info("Relatório formatado gerado com sucesso")
            return processed_response

        except Exception as e:
            logger.exception(f"Falha na formatação do relatório: {str(e)}")
            raise e

    def process_output(self, output: str) -> str:
        """
        Processar saída do LLM, limpar formato Markdown

        Args:
            output: Saída bruta do LLM

        Returns:
            Relatório Markdown limpo
        """
        try:
            # Limpar texto da resposta
            cleaned_output = remove_reasoning_from_output(output)
            cleaned_output = clean_markdown_tags(cleaned_output)

            # Garantir que o relatório tem estrutura básica
            if not cleaned_output.strip():
                return "# Falha na geração do relatório\n\nNão foi possível gerar conteúdo válido para o relatório."

            # Se não houver título, adicionar um título padrão
            if not cleaned_output.strip().startswith('#'):
                cleaned_output = "# Relatório de pesquisa aprofundada\n\n" + cleaned_output

            return cleaned_output.strip()

        except Exception as e:
            logger.exception(f"Falha ao processar saída: {str(e)}")
            return "# Falha no processamento do relatório\n\nOcorreu um erro durante a formatação do relatório."

    def format_report_manually(self, paragraphs_data: List[Dict[str, str]],
                             report_title: str = "Relatório de pesquisa aprofundada") -> str:
        """
        Formatação manual do relatório (método alternativo)

        Args:
            paragraphs_data: Lista de dados dos parágrafos
            report_title: Título do relatório

        Returns:
            Relatório formatado em Markdown
        """
        try:
            logger.info("Usando método de formatação manual")

            # Construir relatório
            report_lines = [
                f"# {report_title}",
                "",
                "---",
                ""
            ]

            # Adicionar cada parágrafo
            for i, paragraph in enumerate(paragraphs_data, 1):
                title = paragraph.get("title", f"Parágrafo {i}")
                content = paragraph.get("paragraph_latest_state", "")

                if content:
                    report_lines.extend([
                        f"## {title}",
                        "",
                        content,
                        "",
                        "---",
                        ""
                    ])

            # Adicionar conclusão
            if len(paragraphs_data) > 1:
                report_lines.extend([
                    "## Conclusão",
                    "",
                    "Este relatório, através de pesquisa e busca aprofundadas, realizou uma análise abrangente do tema em questão. "
                    "O conteúdo de cada aspecto acima fornece referências importantes para a compreensão deste tema.",
                    ""
                ])

            return "\n".join(report_lines)

        except Exception as e:
            logger.exception(f"Falha na formatação manual: {str(e)}")
            return "# Falha na geração do relatório\n\nNão foi possível completar a formatação do relatório."
