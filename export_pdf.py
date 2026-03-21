#!/usr/bin/env python
"""
Script de exportacao de PDF
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Adicionar caminho do projeto ao sys.path
sys.path.insert(0, '/Users/mayiding/Desktop/GitMy/BettaFish')

def export_pdf(ir_file_path):
    """Exportar PDF"""
    try:
        # Ler arquivo IR
        print(f"Lendo arquivo de relatorio: {ir_file_path}")
        with open(ir_file_path, 'r', encoding='utf-8') as f:
            document_ir = json.load(f)

        # Importar renderizador PDF
        from ReportEngine.renderers.pdf_renderer import PDFRenderer

        # Criar renderizador PDF
        print("Inicializando renderizador PDF...")
        renderer = PDFRenderer()

        # Gerar PDF
        print("Gerando PDF...")
        pdf_bytes = renderer.render_to_bytes(document_ir, optimize_layout=True)

        # Determinar nome do arquivo de saida
        topic = document_ir.get('metadata', {}).get('topic', 'report')
        output_dir = Path('/Users/mayiding/Desktop/GitMy/BettaFish/final_reports/pdf')
        output_dir.mkdir(parents=True, exist_ok=True)

        pdf_filename = f"report_{topic}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        output_path = output_dir / pdf_filename

        # Salvar arquivo PDF
        print(f"Salvando PDF em: {output_path}")
        with open(output_path, 'wb') as f:
            f.write(pdf_bytes)

        print(f"Exportacao de PDF concluida com sucesso!")
        print(f"Localizacao do arquivo: {output_path}")
        print(f"Tamanho do arquivo: {len(pdf_bytes) / 1024 / 1024:.2f} MB")

        return str(output_path)

    except Exception as e:
        print(f"Falha na exportacao do PDF: {str(e)}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    # Usar o arquivo de relatorio mais recente
    latest_report = "/Users/mayiding/Desktop/GitMy/BettaFish/final_reports/ir/report_ir_人工智能行情发展走势_20251119_235407.json"

    if os.path.exists(latest_report):
        print("="*50)
        print("Iniciando exportacao de PDF")
        print("="*50)
        result = export_pdf(latest_report)
        if result:
            print(f"\nArquivo PDF gerado: {result}")
    else:
        print(f"Arquivo de relatorio nao existe: {latest_report}")
