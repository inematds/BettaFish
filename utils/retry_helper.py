"""
Modulo de ferramenta de mecanismo de retentativa
Fornece funcionalidade generica de retentativa para requisicoes de rede, aumentando a robustez do sistema
"""

import time
from functools import wraps
from typing import Callable, Any
import requests
from loguru import logger

# Configuracao de log
class RetryConfig:
    """Classe de configuracao de retentativa"""

    def __init__(
        self,
        max_retries: int = 3,
        initial_delay: float = 1.0,
        backoff_factor: float = 2.0,
        max_delay: float = 60.0,
        retry_on_exceptions: tuple = None
    ):
        """
        Inicializar configuracao de retentativa

        Args:
            max_retries: Numero maximo de retentativas
            initial_delay: Atraso inicial em segundos
            backoff_factor: Fator de backoff (o atraso dobra a cada retentativa)
            max_delay: Atraso maximo em segundos
            retry_on_exceptions: Tupla de tipos de excecao que devem ser retentados
        """
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.backoff_factor = backoff_factor
        self.max_delay = max_delay

        # Tipos de excecao padrao para retentativa
        if retry_on_exceptions is None:
            self.retry_on_exceptions = (
                requests.exceptions.RequestException,
                requests.exceptions.ConnectionError,
                requests.exceptions.HTTPError,
                requests.exceptions.Timeout,
                requests.exceptions.TooManyRedirects,
                ConnectionError,
                TimeoutError,
                Exception  # Excecoes gerais que OpenAI e outras APIs podem lancar
            )
        else:
            self.retry_on_exceptions = retry_on_exceptions

# Configuracao padrao
DEFAULT_RETRY_CONFIG = RetryConfig()

def with_retry(config: RetryConfig = None):
    """
    Decorador de retentativa

    Args:
        config: Configuracao de retentativa, usa a configuracao padrao se nao fornecida

    Returns:
        Funcao decoradora
    """
    if config is None:
        config = DEFAULT_RETRY_CONFIG

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None

            for attempt in range(config.max_retries + 1):  # +1 porque a primeira vez nao conta como retentativa
                try:
                    result = func(*args, **kwargs)
                    if attempt > 0:
                        logger.info(f"Funcao {func.__name__} teve sucesso na {attempt + 1}a tentativa")
                    return result

                except config.retry_on_exceptions as e:
                    last_exception = e

                    if attempt == config.max_retries:
                        # A ultima tentativa tambem falhou
                        logger.error(f"Funcao {func.__name__} ainda falhou apos {config.max_retries + 1} tentativas")
                        logger.error(f"Erro final: {str(e)}")
                        raise e

                    # Calcular tempo de atraso
                    delay = min(
                        config.initial_delay * (config.backoff_factor ** attempt),
                        config.max_delay
                    )

                    logger.warning(f"Funcao {func.__name__} falhou na {attempt + 1}a tentativa: {str(e)}")
                    logger.info(f"A {attempt + 2}a tentativa sera feita em {delay:.1f} segundos...")

                    time.sleep(delay)

                except Exception as e:
                    # Excecao nao esta na lista de retentativa, lancar diretamente
                    logger.error(f"Funcao {func.__name__} encontrou uma excecao nao retentavel: {str(e)}")
                    raise e

            # Nao deveria chegar aqui, mas como rede de seguranca
            if last_exception:
                raise last_exception

        return wrapper
    return decorator

def retry_on_network_error(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0
):
    """
    Decorador de retentativa especificamente para erros de rede (versao simplificada)

    Args:
        max_retries: Numero maximo de retentativas
        initial_delay: Atraso inicial em segundos
        backoff_factor: Fator de backoff

    Returns:
        Funcao decoradora
    """
    config = RetryConfig(
        max_retries=max_retries,
        initial_delay=initial_delay,
        backoff_factor=backoff_factor
    )
    return with_retry(config)

class RetryableError(Exception):
    """Excecao retentavel personalizada"""
    pass

def with_graceful_retry(config: RetryConfig = None, default_return=None):
    """
    Decorador de retentativa elegante - para chamadas de API nao criticas
    Apos falha nao lanca excecao, mas retorna o valor padrao, garantindo que o sistema continue funcionando

    Args:
        config: Configuracao de retentativa, usa a configuracao padrao se nao fornecida
        default_return: Valor padrao retornado apos todas as retentativas falharem

    Returns:
        Funcao decoradora
    """
    if config is None:
        config = SEARCH_API_RETRY_CONFIG

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None

            for attempt in range(config.max_retries + 1):  # +1 porque a primeira vez nao conta como retentativa
                try:
                    result = func(*args, **kwargs)
                    if attempt > 0:
                        logger.info(f"API nao critica {func.__name__} teve sucesso na {attempt + 1}a tentativa")
                    return result

                except config.retry_on_exceptions as e:
                    last_exception = e

                    if attempt == config.max_retries:
                        # A ultima tentativa tambem falhou, retornar valor padrao em vez de lancar excecao
                        logger.warning(f"API nao critica {func.__name__} ainda falhou apos {config.max_retries + 1} tentativas")
                        logger.warning(f"Erro final: {str(e)}")
                        logger.info(f"Retornando valor padrao para garantir continuidade do sistema: {default_return}")
                        return default_return

                    # Calcular tempo de atraso
                    delay = min(
                        config.initial_delay * (config.backoff_factor ** attempt),
                        config.max_delay
                    )

                    logger.warning(f"API nao critica {func.__name__} falhou na {attempt + 1}a tentativa: {str(e)}")
                    logger.info(f"A {attempt + 2}a tentativa sera feita em {delay:.1f} segundos...")

                    time.sleep(delay)

                except Exception as e:
                    # Excecao nao esta na lista de retentativa, retornar valor padrao
                    logger.warning(f"API nao critica {func.__name__} encontrou uma excecao nao retentavel: {str(e)}")
                    logger.info(f"Retornando valor padrao para garantir continuidade do sistema: {default_return}")
                    return default_return

            # Nao deveria chegar aqui, mas como rede de seguranca
            return default_return

        return wrapper
    return decorator

def make_retryable_request(
    request_func: Callable,
    *args,
    max_retries: int = 5,
    **kwargs
) -> Any:
    """
    Executar diretamente uma requisicao retentavel (sem usar decorador)

    Args:
        request_func: Funcao de requisicao a ser executada
        *args: Argumentos posicionais passados para a funcao de requisicao
        max_retries: Numero maximo de retentativas
        **kwargs: Argumentos nomeados passados para a funcao de requisicao

    Returns:
        Valor de retorno da funcao de requisicao
    """
    config = RetryConfig(max_retries=max_retries)

    @with_retry(config)
    def _execute():
        return request_func(*args, **kwargs)

    return _execute()

# Configuracoes de retentativa predefinidas de uso comum
LLM_RETRY_CONFIG = RetryConfig(
    max_retries=6,        # Manter retentativas extras
    initial_delay=60.0,   # Esperar pelo menos 1 minuto na primeira vez
    backoff_factor=2.0,   # Continuar usando backoff exponencial
    max_delay=600.0       # Espera maxima de 10 minutos por vez
)

SEARCH_API_RETRY_CONFIG = RetryConfig(
    max_retries=5,        # Aumentar para 5 retentativas
    initial_delay=2.0,    # Aumentar atraso inicial
    backoff_factor=1.6,   # Ajustar fator de backoff
    max_delay=25.0        # Aumentar atraso maximo
)

DB_RETRY_CONFIG = RetryConfig(
    max_retries=5,        # Aumentar para 5 retentativas
    initial_delay=1.0,    # Manter atraso curto para retentativas de banco de dados
    backoff_factor=1.5,
    max_delay=10.0
)
