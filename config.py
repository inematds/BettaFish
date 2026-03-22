# -*- coding: utf-8 -*-
"""
Arquivo de configuracao do BettaFish

Este modulo utiliza pydantic-settings para gerenciar a configuracao global, suportando carregamento automatico
a partir de variaveis de ambiente e arquivos .env.
Localizacao da definicao dos modelos de dados:
- Este arquivo - definicao dos modelos de configuracao
"""

from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field, ConfigDict
from typing import Optional, Literal
from loguru import logger


# Calcula a prioridade do .env: prioridade para o diretorio de trabalho atual, depois raiz do projeto
PROJECT_ROOT: Path = Path(__file__).resolve().parent
CWD_ENV: Path = Path.cwd() / ".env"
ENV_FILE: str = str(CWD_ENV if CWD_ENV.exists() else (PROJECT_ROOT / ".env"))


class Settings(BaseSettings):
    """
    Configuracao global; suporta carregamento automatico via .env e variaveis de ambiente.
    Os nomes das variaveis sao os mesmos em maiusculas do config.py original, para facilitar a transicao.
    """
    # ================== Configuracao do Servidor Flask ====================
    HOST: str = Field("0.0.0.0", description="Endereco do host BETTAFISH, ex: 0.0.0.0 ou 127.0.0.1")
    PORT: int = Field(5000, description="Porta do servidor Flask, padrao 5000")

    # ====================== Configuracao do Banco de Dados ======================
    DB_DIALECT: str = Field("postgresql", description="Tipo de banco de dados, opcoes: mysql ou postgresql; configure junto com as demais informacoes de conexao")
    DB_HOST: str = Field("your_db_host", description="Host do banco de dados, ex: localhost ou 127.0.0.1")
    DB_PORT: int = Field(3306, description="Porta do banco de dados, padrao 3306")
    DB_USER: str = Field("your_db_user", description="Usuario do banco de dados")
    DB_PASSWORD: str = Field("your_db_password", description="Senha do banco de dados")
    DB_NAME: str = Field("your_db_name", description="Nome do banco de dados")
    DB_CHARSET: str = Field("utf8mb4", description="Charset do banco de dados, recomendado utf8mb4, compativel com emoji")

    # ======================= Configuracao LLM =======================
    # Nossos patrocinadores de API de modelos LLM: https://aihubmix.com/?aff=8Ds9, oferecendo APIs de modelos muito completas

    # Insight Agent (recomendado Kimi, endereco de solicitacao: https://platform.moonshot.cn/)
    INSIGHT_ENGINE_API_KEY: Optional[str] = Field(None, description="Insight Agent (recomendado kimi-k2, endereco oficial: https://platform.moonshot.cn/) chave API, usada para o LLM principal. Por favor, primeiro solicite e teste com a configuracao recomendada, depois ajuste KEY, BASE_URL e MODEL_NAME conforme necessario.")
    INSIGHT_ENGINE_BASE_URL: Optional[str] = Field("https://api.moonshot.cn/v1", description="BaseUrl do LLM do Insight Agent, personalizavel conforme o provedor")
    INSIGHT_ENGINE_MODEL_NAME: str = Field("kimi-k2-0711-preview", description="Nome do modelo LLM do Insight Agent, ex: kimi-k2-0711-preview")

    # Media Agent (recomendado Gemini, provedor intermediario recomendado: https://aihubmix.com/?aff=8Ds9)
    MEDIA_ENGINE_API_KEY: Optional[str] = Field(None, description="Media Agent (recomendado gemini-2.5-pro, endereco do provedor intermediario: https://aihubmix.com/?aff=8Ds9) chave API")
    MEDIA_ENGINE_BASE_URL: Optional[str] = Field("https://aihubmix.com/v1", description="BaseUrl do LLM do Media Agent, ajustavel conforme o servico intermediario")
    MEDIA_ENGINE_MODEL_NAME: str = Field("gemini-2.5-pro", description="Nome do modelo LLM do Media Agent, ex: gemini-2.5-pro")

    # Query Agent (recomendado DeepSeek, endereco de solicitacao: https://www.deepseek.com/)
    QUERY_ENGINE_API_KEY: Optional[str] = Field(None, description="Query Agent (recomendado deepseek, endereco oficial: https://platform.deepseek.com/) chave API")
    QUERY_ENGINE_BASE_URL: Optional[str] = Field("https://api.deepseek.com", description="BaseUrl do LLM do Query Agent")
    QUERY_ENGINE_MODEL_NAME: str = Field("deepseek-chat", description="Nome do modelo LLM do Query Agent, ex: deepseek-reasoner")

    # Report Agent (recomendado Gemini, provedor intermediario recomendado: https://aihubmix.com/?aff=8Ds9)
    REPORT_ENGINE_API_KEY: Optional[str] = Field(None, description="Report Agent (recomendado gemini-2.5-pro, endereco do provedor intermediario: https://aihubmix.com/?aff=8Ds9) chave API")
    REPORT_ENGINE_BASE_URL: Optional[str] = Field("https://aihubmix.com/v1", description="BaseUrl do LLM do Report Agent, ajustavel conforme o servico intermediario")
    REPORT_ENGINE_MODEL_NAME: str = Field("gemini-2.5-pro", description="Nome do modelo LLM do Report Agent, ex: gemini-2.5-pro")

    # MindSpider Agent (recomendado Deepseek, endereco oficial: https://platform.deepseek.com/)
    MINDSPIDER_API_KEY: Optional[str] = Field(None, description="MindSpider Agent (recomendado deepseek, endereco oficial: https://platform.deepseek.com/) chave API")
    MINDSPIDER_BASE_URL: Optional[str] = Field(None, description="BaseUrl do MindSpider Agent, configuravel conforme o servico escolhido")
    MINDSPIDER_MODEL_NAME: Optional[str] = Field(None, description="Nome do modelo do MindSpider Agent, ex: deepseek-reasoner")

    # Forum Host (modelo mais recente Qwen3, aqui utilizei a plataforma SiliconFlow, endereco de solicitacao: https://cloud.siliconflow.cn/)
    FORUM_HOST_API_KEY: Optional[str] = Field(None, description="Forum Host (recomendado qwen-plus, endereco oficial: https://www.aliyun.com/product/bailian) chave API")
    FORUM_HOST_BASE_URL: Optional[str] = Field(None, description="BaseUrl do LLM do Forum Host, configuravel conforme o servico escolhido")
    FORUM_HOST_MODEL_NAME: Optional[str] = Field(None, description="Nome do modelo LLM do Forum Host, ex: qwen-plus")

    # SQL keyword Optimizer (modelo Qwen3 com poucos parametros, aqui utilizei a plataforma SiliconFlow, endereco de solicitacao: https://cloud.siliconflow.cn/)
    KEYWORD_OPTIMIZER_API_KEY: Optional[str] = Field(None, description="SQL Keyword Optimizer (recomendado qwen-plus, endereco oficial: https://www.aliyun.com/product/bailian) chave API")
    KEYWORD_OPTIMIZER_BASE_URL: Optional[str] = Field(None, description="BaseUrl do Keyword Optimizer, configuravel conforme o servico escolhido")
    KEYWORD_OPTIMIZER_MODEL_NAME: Optional[str] = Field(None, description="Nome do modelo LLM do Keyword Optimizer, ex: qwen-plus")

    # ================== Configuracao de Ferramentas de Rede ====================
    # Tavily API (endereco de solicitacao: https://www.tavily.com/)
    TAVILY_API_KEY: Optional[str] = Field(None, description="Tavily API (endereco de solicitacao: https://www.tavily.com/) chave API, usada para busca web Tavily")

    SEARCH_TOOL_TYPE: Literal["TavilyAPI", "AnspireAPI", "BochaAPI"] = Field("TavilyAPI", description="Tipo de ferramenta de busca web, suporta TavilyAPI, BochaAPI ou AnspireAPI, padrao TavilyAPI")
    # Bocha API (endereco de solicitacao: https://open.bochaai.com/)
    BOCHA_BASE_URL: Optional[str] = Field("https://api.bocha.cn/v1/ai-search", description="BaseUrl de busca AI Bocha ou busca web Bocha")
    BOCHA_WEB_SEARCH_API_KEY: Optional[str] = Field(None, description="Bocha API (endereco de solicitacao: https://open.bochaai.com/) chave API, usada para busca Bocha")

    # Anspire AI Search API (endereco de solicitacao: https://open.anspire.cn/?share_code=3E1FUOUH)
    ANSPIRE_BASE_URL: Optional[str] = Field("https://plugin.anspire.cn/api/ntsearch/search", description="BaseUrl de busca AI Anspire")
    ANSPIRE_API_KEY: Optional[str] = Field(None, description="Anspire AI Search API (endereco de solicitacao: https://open.anspire.cn/?share_code=3E1FUOUH) chave API, usada para busca Anspire")


    # ================== Configuracao de Busca do Insight Engine ====================
    DEFAULT_SEARCH_HOT_CONTENT_LIMIT: int = Field(100, description="Numero maximo padrao de conteudo em alta")
    DEFAULT_SEARCH_TOPIC_GLOBALLY_LIMIT_PER_TABLE: int = Field(50, description="Numero maximo de topicos globais por tabela")
    DEFAULT_SEARCH_TOPIC_BY_DATE_LIMIT_PER_TABLE: int = Field(100, description="Numero maximo de topicos por data")
    DEFAULT_GET_COMMENTS_FOR_TOPIC_LIMIT: int = Field(500, description="Numero maximo de comentarios por topico")
    DEFAULT_SEARCH_TOPIC_ON_PLATFORM_LIMIT: int = Field(200, description="Numero maximo de topicos em busca por plataforma")
    MAX_SEARCH_RESULTS_FOR_LLM: int = Field(0, description="Numero maximo de resultados de busca para o LLM")
    MAX_HIGH_CONFIDENCE_SENTIMENT_RESULTS: int = Field(0, description="Numero maximo de resultados de analise de sentimento com alta confianca")
    MAX_REFLECTIONS: int = Field(3, description="Numero maximo de reflexoes")
    MAX_PARAGRAPHS: int = Field(6, description="Numero maximo de paragrafos")
    SEARCH_TIMEOUT: int = Field(240, description="Timeout de uma unica requisicao de busca")
    MAX_CONTENT_LENGTH: int = Field(500000, description="Comprimento maximo do conteudo de busca")

    model_config = ConfigDict(
        env_file=ENV_FILE,
        env_prefix="",
        case_sensitive=False,
        extra="allow"
    )


# Criar instancia global de configuracao
settings = Settings()


def reload_settings() -> Settings:
    """
    Recarregar configuracao

    Recarrega a configuracao a partir do arquivo .env e variaveis de ambiente, atualizando a instancia global settings.
    Usado para atualizar dinamicamente a configuracao em tempo de execucao.

    Returns:
        Settings: Nova instancia de configuracao criada
    """

    global settings
    settings = Settings()
    return settings
