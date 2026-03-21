#!/usr/bin/env python
"""
Report Engine - Versao Linha de Comando

Este e um programa de geracao de relatorios via linha de comando sem necessidade de frontend.
Fluxo principal:
1. Verificar dependencias do PDF
2. Obter os arquivos log e md mais recentes
3. Chamar diretamente o Report Engine para gerar o relatorio (pular revisao de adicao de arquivos)
4. Salvar automaticamente HTML, PDF (se houver dependencias) e Markdown em final_reports/ (Markdown sera gerado apos o PDF)

Modo de uso:
    python report_engine_only.py [opcoes]

Opcoes:
    --query QUERY     Especificar o tema do relatorio (opcional, padrao extraido do nome do arquivo)
    --skip-pdf        Pular geracao do PDF (mesmo com dependencias)
    --skip-markdown   Pular geracao do Markdown
    --verbose         Exibir logs detalhados
    --help            Exibir informacoes de ajuda
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

from loguru import logger

# Configuracao global
VERBOSE = False

# Configurar log
def setup_logger(verbose: bool = False):
    """Configurar o log"""
    global VERBOSE
    VERBOSE = verbose

    logger.remove()  # Remover handler padrao
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="DEBUG" if verbose else "INFO"
    )


def check_dependencies() -> tuple[bool, Optional[str]]:
    """
    Verificar dependencias do sistema necessarias para geracao de PDF

    Returns:
        tuple: (is_available: bool, message: str)
            - is_available: Se a funcionalidade PDF esta disponivel
            - message: Mensagem do resultado da verificacao de dependencias
    """
    logger.info("=" * 70)
    logger.info("Etapa 1/4: Verificar dependencias do sistema")
    logger.info("=" * 70)

    try:
        from ReportEngine.utils.dependency_check import check_pango_available
        is_available, message = check_pango_available()

        if is_available:
            logger.success("Dependencias do PDF verificadas com sucesso, serao gerados arquivos HTML e PDF")
        else:
            logger.warning("Dependencias do PDF ausentes, apenas arquivo HTML sera gerado")
            logger.info("\n" + message)

        return is_available, message
    except Exception as e:
        logger.error(f"Falha na verificacao de dependencias: {e}")
        return False, str(e)


def get_latest_engine_reports() -> Dict[str, str]:
    """
    Obter os arquivos de relatorio mais recentes dos tres diretorios de engines

    Returns:
        Dict[str, str]: Mapeamento de nome do engine para caminho do arquivo
    """
    logger.info("\n" + "=" * 70)
    logger.info("Etapa 2/4: Obter relatorios mais recentes dos engines de analise")
    logger.info("=" * 70)

    # Definir os diretorios dos tres engines
    directories = {
        'insight': 'insight_engine_streamlit_reports',
        'media': 'media_engine_streamlit_reports',
        'query': 'query_engine_streamlit_reports'
    }

    latest_files = {}

    for engine, directory in directories.items():
        if not os.path.exists(directory):
            logger.warning(f"{engine.capitalize()} Engine - diretorio nao existe: {directory}")
            continue

        # Obter todos os arquivos .md
        md_files = [f for f in os.listdir(directory) if f.endswith('.md')]

        if not md_files:
            logger.warning(f"{engine.capitalize()} Engine - nenhum arquivo .md encontrado no diretorio")
            continue

        # Obter arquivo mais recente
        latest_file = max(
            md_files,
            key=lambda x: os.path.getmtime(os.path.join(directory, x))
        )
        latest_path = os.path.join(directory, latest_file)
        latest_files[engine] = latest_path

        logger.info(f"Relatorio mais recente do {engine.capitalize()} Engine encontrado")

    if not latest_files:
        logger.error("Nenhum arquivo de relatorio de engine encontrado, por favor execute primeiro os engines de analise para gerar relatorios")
        sys.exit(1)

    logger.info(f"\nTotal de {len(latest_files)} relatorios mais recentes de engines encontrados")

    return latest_files


def confirm_file_selection(latest_files: Dict[str, str]) -> bool:
    """
    Confirmar com o usuario se os arquivos selecionados estao corretos

    Args:
        latest_files: Mapeamento de nome do engine para caminho do arquivo

    Returns:
        bool: True se o usuario confirmar, False caso contrario
    """
    logger.info("\n" + "=" * 70)
    logger.info("Por favor confirme os arquivos selecionados:")
    logger.info("=" * 70)

    for engine, file_path in latest_files.items():
        filename = os.path.basename(file_path)
        # Obter data de modificacao do arquivo
        mtime = os.path.getmtime(file_path)
        mtime_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')

        logger.info(f"  {engine.capitalize()} Engine:")
        logger.info(f"    Nome do arquivo: {filename}")
        logger.info(f"    Caminho: {file_path}")
        logger.info(f"    Data de modificacao: {mtime_str}")
        logger.info("")

    logger.info("=" * 70)

    # Solicitar confirmacao do usuario
    try:
        response = input("Deseja usar os arquivos acima para gerar o relatorio? [S/n]: ").strip().lower()

        # Padrao e s, portanto entrada vazia ou s indica confirmacao
        if response == '' or response == 's' or response == 'sim':
            logger.success("Usuario confirmou, continuando geracao do relatorio")
            return True
        else:
            logger.warning("Usuario cancelou a operacao")
            return False
    except (KeyboardInterrupt, EOFError):
        logger.warning("\nUsuario cancelou a operacao")
        return False


def load_engine_reports(latest_files: Dict[str, str]) -> list[str]:
    """
    Carregar conteudo dos relatorios dos engines

    Args:
        latest_files: Mapeamento de nome do engine para caminho do arquivo

    Returns:
        list[str]: Lista de conteudos dos relatorios
    """
    reports = []

    for engine, file_path in latest_files.items():
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                reports.append(content)
                logger.debug(f"Relatorio {engine} carregado, comprimento: {len(content)} caracteres")
        except Exception as e:
            logger.error(f"Falha ao carregar relatorio {engine}: {e}")

    return reports


def extract_query_from_reports(latest_files: Dict[str, str]) -> str:
    """
    Extrair o tema da consulta a partir dos nomes dos arquivos de relatorio

    Args:
        latest_files: Mapeamento de nome do engine para caminho do arquivo

    Returns:
        str: Tema da consulta extraido
    """
    # Tentar extrair o tema do nome do arquivo
    for engine, file_path in latest_files.items():
        filename = os.path.basename(file_path)
        # Supondo formato do nome: report_tema_timestamp.md
        if '_' in filename:
            parts = filename.replace('.md', '').split('_')
            if len(parts) >= 2:
                # Extrair a parte central como tema
                topic = '_'.join(parts[1:-1]) if len(parts) > 2 else parts[1]
                if topic:
                    return topic

    # Se nao for possivel extrair, retornar valor padrao
    return "Relatorio de analise abrangente"


def generate_report(reports: list[str], query: str, pdf_available: bool) -> Dict[str, Any]:
    """
    Chamar o Report Engine para gerar o relatorio

    Args:
        reports: Lista de conteudos dos relatorios
        query: Tema do relatorio
        pdf_available: Se a funcionalidade PDF esta disponivel

    Returns:
        Dict[str, Any]: Dicionario contendo os resultados da geracao
    """
    logger.info("\n" + "=" * 70)
    logger.info("Etapa 3/4: Gerar relatorio abrangente")
    logger.info("=" * 70)
    logger.info(f"Tema do relatorio: {query}")
    logger.info(f"Quantidade de relatorios de entrada: {len(reports)}")

    try:
        from ReportEngine.agent import ReportAgent

        # Inicializar Report Agent
        logger.info("Inicializando Report Engine...")
        agent = ReportAgent()

        # Definir handler de eventos de streaming
        def stream_handler(event_type: str, payload: Dict[str, Any]):
            """Processar eventos de streaming do Report Engine"""
            if event_type == 'stage':
                stage = payload.get('stage', '')
                if stage == 'agent_start':
                    logger.info(f"Iniciando geracao do relatorio: {payload.get('report_id', '')}")
                elif stage == 'template_selected':
                    logger.info(f"Template selecionado: {payload.get('template', '')}")
                elif stage == 'template_sliced':
                    logger.info(f"Parsing do template concluido, total de {payload.get('section_count', 0)} capitulos")
                elif stage == 'layout_designed':
                    logger.info(f"Design de layout do documento concluido")
                    logger.info(f"  Titulo: {payload.get('title', '')}")
                elif stage == 'word_plan_ready':
                    logger.info(f"Planejamento de extensao concluido, capitulos alvo: {payload.get('chapter_targets', 0)}")
                elif stage == 'chapters_compiled':
                    logger.info(f"Geracao de capitulos concluida, total de {payload.get('chapter_count', 0)} capitulos")
                elif stage == 'html_rendered':
                    logger.info(f"Renderizacao HTML concluida")
                elif stage == 'report_saved':
                    logger.info(f"Relatorio salvo")
            elif event_type == 'chapter_status':
                chapter_id = payload.get('chapterId', '')
                title = payload.get('title', '')
                status = payload.get('status', '')
                if status == 'generating':
                    logger.info(f"  Gerando capitulo: {title}")
                elif status == 'completed':
                    attempt = payload.get('attempt', 1)
                    warning = payload.get('warning', '')
                    if warning:
                        logger.warning(f"  Capitulo concluido: {title} ({attempt}a tentativa, {payload.get('warningMessage', '')})")
                    else:
                        logger.success(f"  Capitulo concluido: {title}")
            elif event_type == 'error':
                logger.error(f"Erro: {payload.get('message', '')}")

        # Gerar relatorio
        logger.info("Iniciando geracao do relatorio, isso pode levar alguns minutos...")
        result = agent.generate_report(
            query=query,
            reports=reports,
            forum_logs="",  # Nao usar logs do forum
            custom_template="",  # Usar selecao automatica de template
            save_report=True,  # Salvar relatorio automaticamente
            stream_handler=stream_handler
        )

        logger.success("Relatorio gerado com sucesso!")
        return result

    except Exception as e:
        logger.exception(f"Falha na geracao do relatorio: {e}")
        sys.exit(1)


def save_pdf(document_ir_path: str, query: str) -> Optional[str]:
    """
    Gerar e salvar PDF a partir do arquivo IR

    Args:
        document_ir_path: Caminho do arquivo Document IR
        query: Tema do relatorio

    Returns:
        Optional[str]: Caminho do arquivo PDF, ou None em caso de falha
    """
    logger.info("\nGerando arquivo PDF...")

    try:
        # Ler dados IR
        with open(document_ir_path, 'r', encoding='utf-8') as f:
            document_ir = json.load(f)

        # Criar renderizador PDF
        from ReportEngine.renderers import PDFRenderer
        renderer = PDFRenderer()

        # Preparar caminho de saida
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        query_safe = "".join(
            c for c in query if c.isalnum() or c in (" ", "-", "_")
        ).rstrip()
        query_safe = query_safe.replace(" ", "_")[:30] or "report"

        pdf_dir = Path("final_reports") / "pdf"
        pdf_dir.mkdir(parents=True, exist_ok=True)

        pdf_filename = f"final_report_{query_safe}_{timestamp}.pdf"
        pdf_path = pdf_dir / pdf_filename

        # Usar metodo render_to_pdf para gerar PDF diretamente, passando caminho do arquivo IR para salvar apos reparo
        logger.info(f"Iniciando renderizacao do PDF: {pdf_path}")
        result_path = renderer.render_to_pdf(
            document_ir,
            pdf_path,
            optimize_layout=True,
            ir_file_path=document_ir_path
        )

        # Exibir tamanho do arquivo
        file_size = result_path.stat().st_size
        size_mb = file_size / (1024 * 1024)
        logger.success(f"PDF salvo: {pdf_path}")
        logger.info(f"  Tamanho do arquivo: {size_mb:.2f} MB")

        return str(result_path)

    except Exception as e:
        logger.exception(f"Falha na geracao do PDF: {e}")
        return None


def save_markdown(document_ir_path: str, query: str) -> Optional[str]:
    """
    Gerar e salvar Markdown a partir do arquivo IR

    Args:
        document_ir_path: Caminho do arquivo Document IR
        query: Tema do relatorio

    Returns:
        Optional[str]: Caminho do arquivo Markdown, ou None em caso de falha
    """
    logger.info("\nGerando arquivo Markdown...")

    try:
        with open(document_ir_path, 'r', encoding='utf-8') as f:
            document_ir = json.load(f)

        from ReportEngine.renderers import MarkdownRenderer
        renderer = MarkdownRenderer()
        # Passar caminho do arquivo IR para salvar apos reparo
        markdown_content = renderer.render(document_ir, ir_file_path=document_ir_path)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        query_safe = "".join(
            c for c in query if c.isalnum() or c in (" ", "-", "_")
        ).rstrip()
        query_safe = query_safe.replace(" ", "_")[:30] or "report"

        md_dir = Path("final_reports") / "md"
        md_dir.mkdir(parents=True, exist_ok=True)

        md_filename = f"final_report_{query_safe}_{timestamp}.md"
        md_path = md_dir / md_filename

        md_path.write_text(markdown_content, encoding='utf-8')

        file_size_kb = md_path.stat().st_size / 1024
        logger.success(f"Markdown salvo: {md_path}")
        logger.info(f"  Tamanho do arquivo: {file_size_kb:.1f} KB")

        return str(md_path)

    except Exception as e:
        logger.exception(f"Falha na geracao do Markdown: {e}")
        return None


def parse_arguments():
    """Analisar argumentos da linha de comando"""
    parser = argparse.ArgumentParser(
        description="Report Engine - Versao Linha de Comando - Ferramenta de geracao de relatorios sem frontend",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  python report_engine_only.py
  python report_engine_only.py --query "Analise do setor de engenharia civil"
  python report_engine_only.py --skip-pdf --verbose

Observacoes:
  O programa obtera automaticamente os arquivos de relatorio mais recentes dos tres diretorios de engines,
  sem revisao de adicao de arquivos, gerando diretamente o relatorio abrangente, e por padrao
  gerando o Markdown apos o PDF.
        """
    )

    parser.add_argument(
        '--query',
        type=str,
        default=None,
        help='Especificar o tema do relatorio (padrao: extraido automaticamente do nome do arquivo)'
    )

    parser.add_argument(
        '--skip-pdf',
        action='store_true',
        help='Pular geracao do PDF (mesmo que o sistema suporte)'
    )

    parser.add_argument(
        '--skip-markdown',
        action='store_true',
        help='Pular geracao do Markdown'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Exibir informacoes detalhadas de log'
    )

    return parser.parse_args()


def main():
    """Funcao principal"""
    # Analisar argumentos da linha de comando
    args = parse_arguments()

    # Configurar log
    setup_logger(verbose=args.verbose)

    logger.info("\n")
    logger.info("+" + "=" * 68 + "+")
    logger.info("|" + " " * 17 + "Report Engine - Versao Linha de Comando" + " " * 12 + "|")
    logger.info("+" + "=" * 68 + "+")
    logger.info("\n")

    # Etapa 1: Verificar dependencias
    pdf_available, _ = check_dependencies()
    markdown_enabled = not args.skip_markdown

    # Se o usuario especificou pular PDF, desabilitar geracao de PDF
    if args.skip_pdf:
        logger.info("Usuario especificou --skip-pdf, geracao de PDF sera pulada")
        pdf_available = False

    if not markdown_enabled:
        logger.info("Usuario especificou --skip-markdown, geracao de Markdown sera pulada")

    # Etapa 2: Obter arquivos mais recentes
    latest_files = get_latest_engine_reports()

    # Confirmar selecao de arquivos
    if not confirm_file_selection(latest_files):
        logger.info("\nPrograma encerrado")
        sys.exit(0)

    # Carregar conteudo dos relatorios
    reports = load_engine_reports(latest_files)

    if not reports:
        logger.error("Nao foi possivel carregar nenhum conteudo de relatorio")
        sys.exit(1)

    # Extrair ou usar o tema de consulta especificado
    query = args.query if args.query else extract_query_from_reports(latest_files)
    logger.info(f"Usando tema do relatorio: {query}")

    # Etapa 3: Gerar relatorio
    result = generate_report(reports, query, pdf_available)

    # Etapa 4: Salvar arquivos
    logger.info("\n" + "=" * 70)
    logger.info("Etapa 4/4: Salvar arquivos gerados")
    logger.info("=" * 70)

    # HTML ja foi salvo automaticamente em generate_report
    html_path = result.get('report_filepath', '')
    ir_path = result.get('ir_filepath', '')
    pdf_path = None
    markdown_path = None

    if html_path:
        logger.success(f"HTML salvo: {result.get('report_relative_path', html_path)}")

    # Se houver dependencias de PDF, gerar e salvar PDF
    if pdf_available:
        if ir_path and os.path.exists(ir_path):
            pdf_path = save_pdf(ir_path, query)
        else:
            logger.warning("Arquivo IR nao encontrado, impossivel gerar PDF")
    else:
        logger.info("Geracao de PDF pulada (dependencias do sistema ausentes ou usuario especificou pular)")

    # Gerar e salvar Markdown (apos o PDF)
    if markdown_enabled:
        if ir_path and os.path.exists(ir_path):
            markdown_path = save_markdown(ir_path, query)
        else:
            logger.warning("Arquivo IR nao encontrado, impossivel gerar Markdown")
    else:
        logger.info("Geracao de Markdown pulada (especificado pelo usuario)")

    # Resumo
    logger.info("\n" + "=" * 70)
    logger.success("Geracao do relatorio concluida!")
    logger.info("=" * 70)
    logger.info(f"ID do relatorio: {result.get('report_id', 'N/A')}")
    logger.info(f"Arquivo HTML: {result.get('report_relative_path', 'N/A')}")
    if pdf_available:
        if pdf_path:
            logger.info(f"Arquivo PDF: {os.path.relpath(pdf_path, os.getcwd())}")
        else:
            logger.info("Arquivo PDF: Falha na geracao, verifique os logs")
    else:
        logger.info("Arquivo PDF: Pulado")
    if markdown_enabled:
        if markdown_path:
            logger.info(f"Arquivo Markdown: {os.path.relpath(markdown_path, os.getcwd())}")
        else:
            logger.info("Arquivo Markdown: Falha na geracao, verifique os logs")
    else:
        logger.info("Arquivo Markdown: Pulado")
    logger.info("=" * 70)
    logger.info("\nPrograma encerrado")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.warning("\n\nPrograma interrompido pelo usuario")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"\nPrograma encerrado com erro: {e}")
        sys.exit(1)
