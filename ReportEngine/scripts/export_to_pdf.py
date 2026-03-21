#!/usr/bin/env python3
"""
Ferramenta de exportacao PDF - Gera PDF diretamente com Python, sem caracteres corrompidos

Uso:
    python ReportEngine/scripts/export_to_pdf.py <arquivo JSON do IR do relatorio> [caminho de saida do PDF]

Exemplos:
    python ReportEngine/scripts/export_to_pdf.py final_reports/ir/report_ir_xxx.json output.pdf
    python ReportEngine/scripts/export_to_pdf.py final_reports/ir/report_ir_xxx.json
"""

import sys
import json
from pathlib import Path
from loguru import logger

from ReportEngine.renderers import PDFRenderer


def export_to_pdf(ir_json_path: str, output_pdf_path: str = None):
    """
    Gerar PDF a partir do arquivo JSON do IR

    Parametros:
        ir_json_path: Document IR JSONArquivoCaminho
        output_pdf_path: caminho de saida do PDF（opcional, padrao e .pdf com mesmo nome）
    """
    ir_path = Path(ir_json_path)

    if not ir_path.exists():
        logger.error(f"Arquivo nao existe: {ir_path}")
        return False

    # Ler dados do IR
    logger.info(f"Lendo relatorio: {ir_path}")
    with open(ir_path, 'r', encoding='utf-8') as f:
        document_ir = json.load(f)

    # Determinar caminho de saida
    if output_pdf_path is None:
        output_pdf_path = ir_path.parent / f"{ir_path.stem}.pdf"
    else:
        output_pdf_path = Path(output_pdf_path)

    # Gerar PDF
    logger.info(f"Iniciando geracao do PDF...")
    renderer = PDFRenderer()

    try:
        renderer.render_to_pdf(document_ir, output_pdf_path)
        logger.success(f"✓ PDF gerado: {output_pdf_path}")
        return True
    except Exception as e:
        logger.error(f"✗ Falha na geracao do PDF: {e}")
        logger.exception("Informacoes detalhadas do erro:")
        return False


def main():
    """Funcao principal"""
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    ir_json_path = sys.argv[1]
    output_pdf_path = sys.argv[2] if len(sys.argv) > 2 else None

    # Verificar variaveis de ambiente
    import os
    if 'DYLD_LIBRARY_PATH' not in os.environ:
        logger.warning("DYLD_LIBRARY_PATH nao definido, tentando configurar automaticamente...")
        os.environ['DYLD_LIBRARY_PATH'] = '/opt/homebrew/lib'

    success = export_to_pdf(ir_json_path, output_pdf_path)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
