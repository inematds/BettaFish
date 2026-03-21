"""
Interface Web Streamlit
Fornece uma interface Web amigavel para o Insight Agent
"""

import os
import sys
import streamlit as st
from datetime import datetime
import json
import locale
from loguru import logger

# Configurar ambiente de codificacao UTF-8
os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['PYTHONUTF8'] = '1'

# Configurar codificacao do sistema
try:
    locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_ALL, 'C.UTF-8')
    except locale.Error:
        pass

# Adicionar diretorio src ao caminho do Python
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from InsightEngine import DeepSearchAgent, Settings
from config import settings
from utils.github_issues import error_with_issue_link


def main():
    """Funcao principal"""
    st.set_page_config(
        page_title="Insight Agent",
        page_icon="",
        layout="wide"
    )

    st.title("Insight Agent")
    st.markdown("Agente de IA para analise profunda do banco de dados privado de opiniao publica")
    st.markdown("Coleta automatica 24 horas de dados de opiniao publica de 13 plataformas de midia social e foruns de tecnologia, incluindo Weibo, Zhihu, GitHub, Coolapk, entre outros")

    # Verificar parametros de URL
    try:
        # Tentar usar a versao mais recente de query_params
        query_params = st.query_params
        auto_query = query_params.get('query', '')
        auto_search = query_params.get('auto_search', 'false').lower() == 'true'
    except AttributeError:
        # Compatibilidade com versao anterior
        query_params = st.experimental_get_query_params()
        auto_query = query_params.get('query', [''])[0]
        auto_search = query_params.get('auto_search', ['false'])[0].lower() == 'true'

    # ----- Configuracao codificada -----
    # Forcar uso do Kimi
    model_name = settings.INSIGHT_ENGINE_MODEL_NAME or "kimi-k2-0711-preview"
    # Configuracao avancada padrao
    max_reflections = 2
    max_content_length = 500000  # Kimi suporta textos longos

    # Area simplificada de exibicao da consulta de pesquisa

    # Se houver consulta automatica, usar como valor padrao; caso contrario, exibir placeholder
    display_query = auto_query if auto_query else "Aguardando conteudo de analise da pagina principal..."

    # Area de exibicao de consulta somente leitura
    st.text_area(
        "Consulta atual",
        value=display_query,
        height=100,
        disabled=True,
        help="O conteudo da consulta e controlado pela barra de busca da pagina principal",
        label_visibility="hidden"
    )

    # Logica de busca automatica
    start_research = False
    query = auto_query

    if auto_search and auto_query and 'auto_search_executed' not in st.session_state:
        st.session_state.auto_search_executed = True
        start_research = True
    elif auto_query and not auto_search:
        st.warning("Aguardando sinal de inicio da busca...")

    # Validar configuracao
    if start_research:
        if not query.strip():
            st.error("Por favor, insira uma consulta de pesquisa")
            logger.error("Por favor, insira uma consulta de pesquisa")
            return

        # Verificar chave de API do LLM na configuracao
        if not settings.INSIGHT_ENGINE_API_KEY:
            st.error("Por favor, defina INSIGHT_ENGINE_API_KEY nas suas variaveis de ambiente")
            logger.error("Por favor, defina INSIGHT_ENGINE_API_KEY nas suas variaveis de ambiente")
            return

        # Usar automaticamente a chave de API e configuracao do banco de dados do arquivo de configuracao
        db_host = settings.DB_HOST
        db_user = settings.DB_USER
        db_password = settings.DB_PASSWORD
        db_name = settings.DB_NAME
        db_port = settings.DB_PORT
        db_charset = settings.DB_CHARSET

        # Criar configuracao Settings (campos devem ser em maiusculas para compatibilidade com a classe Settings)
        config = Settings(
            INSIGHT_ENGINE_API_KEY=settings.INSIGHT_ENGINE_API_KEY,
            INSIGHT_ENGINE_BASE_URL=settings.INSIGHT_ENGINE_BASE_URL,
            INSIGHT_ENGINE_MODEL_NAME=model_name,
            DB_HOST=db_host,
            DB_USER=db_user,
            DB_PASSWORD=db_password,
            DB_NAME=db_name,
            DB_PORT=db_port,
            DB_CHARSET=db_charset,
            DB_DIALECT=settings.DB_DIALECT,
            MAX_REFLECTIONS=max_reflections,
            MAX_CONTENT_LENGTH=max_content_length,
            OUTPUT_DIR="insight_engine_streamlit_reports"
        )

        # Executar pesquisa
        execute_research(query, config)


def execute_research(query: str, config: Settings):
    """Executar pesquisa"""
    try:
        # Criar barra de progresso
        progress_bar = st.progress(0)
        status_text = st.empty()

        # Inicializar Agent
        status_text.text("Inicializando Agent...")
        agent = DeepSearchAgent(config)
        st.session_state.agent = agent

        progress_bar.progress(10)

        # Gerar estrutura do relatorio
        status_text.text("Gerando estrutura do relatorio...")
        agent._generate_report_structure(query)
        progress_bar.progress(20)

        # Processar paragrafos
        total_paragraphs = len(agent.state.paragraphs)
        for i in range(total_paragraphs):
            status_text.text(f"Processando paragrafo {i + 1}/{total_paragraphs}: {agent.state.paragraphs[i].title}")

            # Busca inicial e resumo
            agent._initial_search_and_summary(i)
            progress_value = 20 + (i + 0.5) / total_paragraphs * 60
            progress_bar.progress(int(progress_value))

            # Ciclo de reflexao
            agent._reflection_loop(i)
            agent.state.paragraphs[i].research.mark_completed()

            progress_value = 20 + (i + 1) / total_paragraphs * 60
            progress_bar.progress(int(progress_value))

        # Gerar relatorio final
        status_text.text("Gerando relatorio final...")
        final_report = agent._generate_final_report()
        progress_bar.progress(90)

        # Salvar relatorio
        status_text.text("Salvando relatorio...")
        agent._save_report(final_report)
        progress_bar.progress(100)

        status_text.text("Pesquisa concluida!")

        # Exibir resultados
        display_results(agent, final_report)

    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        error_display = error_with_issue_link(
            f"Ocorreu um erro durante a pesquisa: {str(e)}",
            error_traceback,
            app_name="Insight Engine Streamlit App"
        )
        st.error(error_display)
        logger.exception(f"Ocorreu um erro durante a pesquisa: {str(e)}")


def display_results(agent: DeepSearchAgent, final_report: str):
    """Exibir resultados da pesquisa"""
    st.header("Trabalho Concluido")

    # Abas de resultados (opcao de download removida)
    tab1, tab2 = st.tabs(["Resumo da Pesquisa", "Informacoes de Referencia"])

    with tab1:
        st.markdown(final_report)

    with tab2:
        # Detalhes dos paragrafos
        st.subheader("Detalhes dos Paragrafos")
        for i, paragraph in enumerate(agent.state.paragraphs):
            with st.expander(f"Paragrafo {i + 1}: {paragraph.title}"):
                st.write("**Conteudo esperado:**", paragraph.content)
                st.write("**Conteudo final:**", paragraph.research.latest_summary[:300] + "..."
                if len(paragraph.research.latest_summary) > 300
                else paragraph.research.latest_summary)
                st.write("**Numero de buscas:**", paragraph.research.get_search_count())
                st.write("**Numero de reflexoes:**", paragraph.research.reflection_iteration)

        # Historico de buscas
        st.subheader("Historico de Buscas")
        all_searches = []
        for paragraph in agent.state.paragraphs:
            all_searches.extend(paragraph.research.search_history)

        if all_searches:
            for i, search in enumerate(all_searches):
                with st.expander(f"Busca {i + 1}: {search.query}"):
                    st.write("**URL:**", search.url)
                    st.write("**Titulo:**", search.title)
                    st.write("**Previa do conteudo:**",
                             search.content[:200] + "..." if len(search.content) > 200 else search.content)
                    if search.score:
                        st.write("**Pontuacao de relevancia:**", search.score)


if __name__ == "__main__":
    main()
