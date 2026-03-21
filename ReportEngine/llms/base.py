"""
Encapsulamento padrao do cliente LLM compativel com OpenAI do Report Engine.

Fornece chamadas unificadas nao-streaming/streaming, retry opcional, concatenacao segura de bytes e consulta de meta-informacoes do modelo.
"""

import os
import sys
from typing import Any, Dict, Optional, Generator
from loguru import logger

from openai import OpenAI

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
utils_dir = os.path.join(project_root, "utils")
if utils_dir not in sys.path:
    sys.path.append(utils_dir)

try:
    from retry_helper import with_retry, LLM_RETRY_CONFIG
except ImportError:
    def with_retry(config=None):
        """Placeholder simplificado do with_retry, implementa a mesma assinatura do decorador real"""
        def decorator(func):
            """Retorna a funcao original diretamente, garantindo que o codigo funcione sem dependencia de retry"""
            return func
        return decorator

    LLM_RETRY_CONFIG = None


class LLMClient:
    """Encapsulamento leve para a API de Chat Completion da OpenAI, unificando o ponto de entrada do Report Engine."""

    def __init__(self, api_key: str, model_name: str, base_url: Optional[str] = None):
        """
        Inicializar cliente LLM e salvar informacoes basicas de conexao.

        Args:
            api_key: Token de API para autenticacao
            model_name: ID do modelo especifico, para localizar capacidade do fornecedor
            base_url: Endereco de interface compativel personalizado, padrao e OpenAI oficial
        """
        if not api_key:
            raise ValueError("Report Engine LLM API key is required.")
        if not model_name:
            raise ValueError("Report Engine model name is required.")

        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name
        self.provider = model_name
        timeout_fallback = os.getenv("LLM_REQUEST_TIMEOUT") or os.getenv("REPORT_ENGINE_REQUEST_TIMEOUT") or "3000"
        try:
            self.timeout = float(timeout_fallback)
        except ValueError:
            self.timeout = 3000.0

        client_kwargs: Dict[str, Any] = {
            "api_key": api_key,
            "max_retries": 0,
        }
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = OpenAI(**client_kwargs)

    @with_retry(LLM_RETRY_CONFIG)
    def invoke(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        """
        Chamar LLM de forma nao-streaming e retornar resposta completa de uma vez.

        Args:
            system_prompt: Prompt de papel do sistema
            user_prompt: Instrucao de alta prioridade do usuario
            **kwargs: Permite transmissao direta de parametros de amostragem como temperature/top_p

        Returns:
            Texto de resposta do LLM apos remover espacos em branco iniciais e finais
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        allowed_keys = {"temperature", "top_p", "presence_penalty", "frequency_penalty", "stream"}
        extra_params = {key: value for key, value in kwargs.items() if key in allowed_keys and value is not None}

        timeout = kwargs.pop("timeout", self.timeout)

        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            timeout=timeout,
            **extra_params,
        )

        if response.choices and response.choices[0].message:
            return self.validate_response(response.choices[0].message.content)
        return ""

    def stream_invoke(self, system_prompt: str, user_prompt: str, **kwargs) -> Generator[str, None, None]:
        """
        Chamar LLM em modo streaming, retornando conteudo da resposta gradualmente.
        
        Parametros:
            system_prompt: Prompt do sistema.
            user_prompt: Prompt do usuario.
            **kwargs: Parametros de amostragem (temperature, top_p, etc.).
            
        Saida:
            str: Cada yield de um trecho de texto delta, facilitando renderizacao em tempo real pela camada superior.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        allowed_keys = {"temperature", "top_p", "presence_penalty", "frequency_penalty"}
        extra_params = {key: value for key, value in kwargs.items() if key in allowed_keys and value is not None}
        # Forcar uso de streaming
        extra_params["stream"] = True

        timeout = kwargs.pop("timeout", self.timeout)

        try:
            stream = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                timeout=timeout,
                **extra_params,
            )
            
            for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield delta.content
        except Exception as e:
            logger.error(f"Falha na requisicao de streaming: {str(e)}")
            raise e
    
    @with_retry(LLM_RETRY_CONFIG)
    def stream_invoke_to_string(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        """
        Chamar LLM em modo streaming e concatenar em string completa de forma segura (evitando truncamento de caracteres multibyte UTF-8).
        
        Parametros:
            system_prompt: Prompt do sistema.
            user_prompt: Prompt do usuario.
            **kwargs: Configuracao de amostragem ou timeout.
            
        Retorna:
            str: Resposta completa apos concatenar todos os deltas.
        """
        # Coletar todos os blocos em formato de bytes
        byte_chunks = []
        for chunk in self.stream_invoke(system_prompt, user_prompt, **kwargs):
            byte_chunks.append(chunk.encode('utf-8'))
        
        # Concatenar todos os bytes e decodificar de uma vez
        if byte_chunks:
            return b''.join(byte_chunks).decode('utf-8', errors='replace')
        return ""

    @staticmethod
    def validate_response(response: Optional[str]) -> str:
        """Tratamento de fallback para None/string，evitando falha na logica superior"""
        if response is None:
            return ""
        return response.strip()

    def get_model_info(self) -> Dict[str, Any]:
        """Retorna informacoes do modelo/provedor/URL base do cliente atual em formato de dicionario"""
        return {
            "provider": self.provider,
            "model": self.model_name,
            "api_base": self.base_url or "default",
        }
