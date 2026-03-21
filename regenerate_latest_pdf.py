"""
Regenerar o PDF do relatorio mais recente usando a nova funcionalidade de graficos vetoriais SVG
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from loguru import logger

# Adicionar caminho do projeto
sys.path.insert(0, str(Path(__file__).parent))

from ReportEngine.renderers import PDFRenderer

def find_latest_report():
    """
    Encontrar o JSON IR do relatorio mais recente em `final_reports/ir`.

    Seleciona o primeiro por ordem decrescente de data de modificacao; se o diretorio
    ou arquivo nao existir, registra o erro e retorna None.

    Retorno:
        Path | None: Caminho do arquivo IR mais recente; None se nao encontrado.
    """
    ir_dir = Path("final_reports/ir")

    if not ir_dir.exists():
        logger.error(f"Diretorio de relatorios nao existe: {ir_dir}")
        return None

    # Obter todos os arquivos JSON e ordenar por data de modificacao
    json_files = sorted(ir_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)

    if not json_files:
        logger.error("Nenhum arquivo de relatorio encontrado")
        return None

    latest_file = json_files[0]
    logger.info(f"Relatorio mais recente encontrado: {latest_file.name}")

    return latest_file

def load_document_ir(file_path):
    """
    Ler o JSON do Document IR no caminho especificado e contar capitulos/graficos.

    Retorna None em caso de falha no parsing; em caso de sucesso, imprime a quantidade
    de capitulos e graficos para confirmacao da escala do relatorio de entrada.

    Parametros:
        file_path: Caminho do arquivo IR

    Retorno:
        dict | None: Document IR parseado; None em caso de falha.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            document_ir = json.load(f)

        logger.info(f"Relatorio carregado com sucesso: {file_path.name}")

        # Contar quantidade de graficos
        chart_count = 0
        chapters = document_ir.get('chapters', [])

        def count_charts(blocks):
            """Contar recursivamente a quantidade de graficos Chart.js na lista de blocks"""
            count = 0
            for block in blocks:
                if isinstance(block, dict):
                    if block.get('type') == 'widget' and block.get('widgetType', '').startswith('chart.js'):
                        count += 1
                    # Processar recursivamente blocks aninhados
                    nested = block.get('blocks')
                    if isinstance(nested, list):
                        count += count_charts(nested)
            return count

        for chapter in chapters:
            blocks = chapter.get('blocks', [])
            chart_count += count_charts(blocks)

        logger.info(f"O relatorio contem {len(chapters)} capitulos e {chart_count} graficos")

        return document_ir

    except Exception as e:
        logger.error(f"Falha ao carregar relatorio: {e}")
        return None

def generate_pdf_with_vector_charts(document_ir, output_path, ir_file_path=None):
    """
    Renderizar o Document IR em PDF com graficos vetoriais SVG usando PDFRenderer.

    Habilita otimizacao de layout, e apos a geracao exibe o tamanho do arquivo e mensagem
    de sucesso; retorna None em caso de excecao.

    Parametros:
        document_ir: Document IR completo
        output_path: Caminho de destino do PDF
        ir_file_path: Opcional, caminho do arquivo IR; quando fornecido, salva automaticamente apos reparo

    Retorno:
        Path | None: Caminho do PDF gerado em caso de sucesso, None em caso de falha.
    """
    try:
        logger.info("=" * 60)
        logger.info("Iniciando geracao do PDF (com graficos vetoriais)")
        logger.info("=" * 60)

        # Criar renderizador PDF
        renderer = PDFRenderer()

        # Renderizar PDF, passando ir_file_path para salvar apos reparo
        result_path = renderer.render_to_pdf(
            document_ir,
            output_path,
            optimize_layout=True,
            ir_file_path=str(ir_file_path) if ir_file_path else None
        )

        logger.info("=" * 60)
        logger.info(f"PDF gerado com sucesso: {result_path}")
        logger.info("=" * 60)

        # Exibir tamanho do arquivo
        file_size = result_path.stat().st_size
        size_mb = file_size / (1024 * 1024)
        logger.info(f"Tamanho do arquivo: {size_mb:.2f} MB")

        return result_path

    except Exception as e:
        logger.error(f"Falha ao gerar PDF: {e}", exc_info=True)
        return None

def main():
    """
    Ponto de entrada principal: regenerar o PDF vetorial do relatorio mais recente.

    Etapas:
        1) Encontrar o arquivo IR mais recente;
        2) Ler e contar a estrutura do relatorio;
        3) Construir o nome do arquivo de saida e garantir que o diretorio exista;
        4) Chamar a funcao de renderizacao para gerar o PDF, exibir caminho e descricao de recursos.

    Retorno:
        int: 0 indica sucesso, diferente de 0 indica falha.
    """
    logger.info("Regenerando o PDF do relatorio mais recente com graficos vetoriais SVG")
    logger.info("")

    # 1. Encontrar relatorio mais recente
    latest_report = find_latest_report()
    if not latest_report:
        logger.error("Nenhum arquivo de relatorio encontrado")
        return 1

    # 2. Carregar dados do relatorio
    document_ir = load_document_ir(latest_report)
    if not document_ir:
        logger.error("Falha ao carregar relatorio")
        return 1

    # 3. Gerar nome do arquivo de saida
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_name = latest_report.stem.replace("report_ir_", "")
    output_filename = f"report_vector_{report_name}_{timestamp}.pdf"
    output_path = Path("final_reports/pdf") / output_filename

    # Garantir que o diretorio de saida exista
    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Caminho de saida: {output_path}")
    logger.info("")

    # 4. Gerar PDF, passando o caminho do arquivo IR para salvar apos reparo
    result = generate_pdf_with_vector_charts(document_ir, output_path, ir_file_path=latest_report)

    if result:
        logger.info("")
        logger.info("Geracao do PDF concluida!")
        logger.info("")
        logger.info("Descricao dos recursos:")
        logger.info("  - Graficos renderizados em formato vetorial SVG")
        logger.info("  - Suporte a zoom ilimitado sem perda de qualidade")
        logger.info("  - Efeitos visuais completos dos graficos preservados")
        logger.info("  - Graficos de linha, barra, pizza, etc. todos em curvas vetoriais")
        logger.info("")
        logger.info(f"Localizacao do arquivo PDF: {result.absolute()}")
        return 0
    else:
        logger.error("Falha na geracao do PDF")
        return 1

if __name__ == "__main__":
    sys.exit(main())
