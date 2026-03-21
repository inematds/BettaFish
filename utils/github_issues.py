"""
Modulo de ferramentas para GitHub Issues

Fornece funcionalidades para criar URLs de GitHub Issues e exibir mensagens de erro com links
Localizacao da definicao dos modelos de dados:
- Sem modelos de dados
"""

from datetime import datetime
from urllib.parse import quote

# Informacoes do repositorio GitHub
GITHUB_REPO = "666ghj/BettaFish"
GITHUB_ISSUES_URL = f"https://github.com/{GITHUB_REPO}/issues/new"


def create_issue_url(title: str, body: str = "") -> str:
    """
    Criar URL de GitHub Issues com titulo e conteudo pre-preenchidos

    Args:
        title: Titulo da Issue
        body: Conteudo da Issue (opcional)

    Returns:
        URL completa do GitHub Issues
    """
    encoded_title = quote(title)
    encoded_body = quote(body) if body else ""

    if encoded_body:
        return f"{GITHUB_ISSUES_URL}?title={encoded_title}&body={encoded_body}"
    else:
        return f"{GITHUB_ISSUES_URL}?title={encoded_title}"


def error_with_issue_link(
    error_message: str,
    error_details: str = "",
    app_name: str = "Streamlit App"
) -> str:
    """
    Gerar string de mensagem de erro com link para GitHub Issues

    Usado apenas no tratamento de excecoes genericas, nao para erros de configuracao do usuario

    Args:
        error_message: Mensagem de erro
        error_details: Detalhes do erro (opcional, usado para preencher o corpo da Issue)
        app_name: Nome da aplicacao, usado para identificar a origem do erro

    Returns:
        String em formato Markdown contendo a mensagem de erro e o link para GitHub Issues
    """
    issue_title = f"[{app_name}] {error_message[:50]}"
    issue_body = f"## Mensagem de Erro\n\n{error_message}\n\n"

    if error_details:
        issue_body += f"## Detalhes do Erro\n\n```\n{error_details}\n```\n\n"

    issue_body += f"## Informacoes do Ambiente\n\n- Aplicacao: {app_name}\n- Horario: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    issue_url = create_issue_url(issue_title, issue_body)

    # Usar formato markdown para adicionar hiperlink
    error_display = f"{error_message}\n\n[Enviar relatorio de erro]({issue_url})"

    if error_details:
        error_display = f"{error_message}\n\n```\n{error_details}\n```\n\n[Enviar relatorio de erro]({issue_url})"

    return error_display


__all__ = [
    "create_issue_url",
    "error_with_issue_link",
    "GITHUB_REPO",
    "GITHUB_ISSUES_URL",
]
