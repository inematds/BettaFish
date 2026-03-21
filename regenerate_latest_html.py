"""
Reencadernar e renderizar relatorio HTML usando os JSONs de capitulos mais recentes.
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from loguru import logger

# Garantir que os modulos do projeto possam ser encontrados
sys.path.insert(0, str(Path(__file__).parent))

from ReportEngine.core import ChapterStorage, DocumentComposer
from ReportEngine.ir import IRValidator
from ReportEngine.renderers import HTMLRenderer
from ReportEngine.utils.config import settings


def find_latest_run_dir(chapter_root: Path):
    """
    Localizar o diretorio de saida da execucao mais recente sob o diretorio raiz de capitulos.

    Varre todos os subdiretorios de `chapter_root`, filtra os candidatos que contem
    `manifest.json`, e seleciona o mais recente por data de modificacao. Se o diretorio
    nao existir ou nao houver manifest valido, registra o erro e retorna None.

    Parametros:
        chapter_root: Diretorio raiz de saida dos capitulos (geralmente settings.CHAPTER_OUTPUT_DIR)

    Retorno:
        Path | None: Caminho do diretorio de execucao mais recente; None se nao encontrado.
    """
    if not chapter_root.exists():
        logger.error(f"Diretorio de capitulos nao existe: {chapter_root}")
        return None

    run_dirs = []
    for candidate in chapter_root.iterdir():
        if not candidate.is_dir():
            continue
        manifest_path = candidate / "manifest.json"
        if manifest_path.exists():
            run_dirs.append((candidate, manifest_path.stat().st_mtime))

    if not run_dirs:
        logger.error("Nenhum diretorio de capitulos com manifest.json encontrado")
        return None

    latest_dir = sorted(run_dirs, key=lambda item: item[1], reverse=True)[0][0]
    logger.info(f"Diretorio de execucao mais recente encontrado: {latest_dir.name}")
    return latest_dir


def load_manifest(run_dir: Path):
    """
    Ler o manifest.json dentro do diretorio de uma execucao.

    Em caso de sucesso retorna o reportId e o dicionario de metadados; em caso de falha
    na leitura ou parsing, registra o erro e retorna (None, None), permitindo que o
    fluxo superior encerre antecipadamente.

    Parametros:
        run_dir: Diretorio de saida dos capitulos contendo manifest.json

    Retorno:
        tuple[str | None, dict | None]: (report_id, metadata)
    """
    manifest_path = run_dir / "manifest.json"
    try:
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)
        report_id = manifest.get("reportId") or run_dir.name
        metadata = manifest.get("metadata") or {}
        logger.info(f"ID do relatorio: {report_id}")
        if manifest.get("createdAt"):
            logger.info(f"Data de criacao: {manifest['createdAt']}")
        return report_id, metadata
    except Exception as exc:
        logger.error(f"Falha ao ler manifest: {exc}")
        return None, None


def load_chapters(run_dir: Path):
    """
    Ler todos os JSONs de capitulos do diretorio de execucao especificado.

    Reutiliza a capacidade load_chapters do ChapterStorage, ordenando automaticamente por order.
    Apos a leitura, imprime a quantidade de capitulos para confirmacao de integridade.

    Parametros:
        run_dir: Diretorio de capitulos de um unico relatorio

    Retorno:
        list[dict]: Lista de JSONs de capitulos (lista vazia se o diretorio estiver vazio)
    """
    storage = ChapterStorage(settings.CHAPTER_OUTPUT_DIR)
    chapters = storage.load_chapters(run_dir)
    logger.info(f"Capitulos carregados: {len(chapters)}")
    return chapters


def validate_chapters(chapters):
    """
    Realizar validacao rapida da estrutura dos capitulos usando IRValidator.

    Apenas registra os capitulos que nao passaram e os tres primeiros erros, sem interromper
    o fluxo; o objetivo e detectar problemas estruturais potenciais antes da reencadernacao.

    Parametros:
        chapters: Lista de JSONs de capitulos
    """
    validator = IRValidator()
    invalid = []
    for chapter in chapters:
        ok, errors = validator.validate_chapter(chapter)
        if not ok:
            invalid.append((chapter.get("chapterId") or "unknown", errors))

    if invalid:
        logger.warning(f"{len(invalid)} capitulos nao passaram na validacao estrutural, a encadernacao continuara:")
        for chapter_id, errors in invalid:
            preview = "; ".join(errors[:3])
            logger.warning(f"  - {chapter_id}: {preview}")
    else:
        logger.info("Validacao estrutural dos capitulos aprovada")


def stitch_document(report_id, metadata, chapters):
    """
    Encadernar os capitulos e metadados em um Document IR completo.

    Usa o DocumentComposer para tratar uniformemente a ordem dos capitulos, metadados globais,
    etc., e imprime a quantidade de capitulos e graficos da encadernacao concluida.

    Parametros:
        report_id: ID do relatorio (vindo do manifest ou nome do diretorio)
        metadata: Metadados globais do manifest
        chapters: Lista de capitulos carregados

    Retorno:
        dict: Objeto Document IR completo
    """
    composer = DocumentComposer()
    document_ir = composer.build_document(report_id, metadata, chapters)
    logger.info(
        f"Encadernacao concluida: {len(document_ir.get('chapters', []))} capitulos, "
        f"{count_charts(document_ir)} graficos"
    )
    return document_ir


def count_charts(document_ir):
    """
    Contar a quantidade de graficos Chart.js em todo o Document IR.

    Percorre os blocks de cada capitulo, buscando recursivamente componentes do tipo widget
    que comecam com `chart.js`, para percepcao rapida da escala de graficos.

    Parametros:
        document_ir: Document IR completo

    Retorno:
        int: Total de graficos
    """
    chart_count = 0
    for chapter in document_ir.get("chapters", []):
        blocks = chapter.get("blocks", [])
        chart_count += _count_chart_blocks(blocks)
    return chart_count


def _count_chart_blocks(blocks):
    """
    Contar recursivamente a quantidade de componentes Chart.js na lista de blocks.

    Compativel com estruturas aninhadas blocks/list/table, garantindo que graficos em
    todos os niveis sejam contados.

    Parametros:
        blocks: Lista de blocks de qualquer nivel

    Retorno:
        int: Quantidade de graficos chart.js contados
    """
    count = 0
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "widget" and str(block.get("widgetType", "")).startswith("chart.js"):
            count += 1
        nested = block.get("blocks")
        if isinstance(nested, list):
            count += _count_chart_blocks(nested)
        if block.get("type") == "list":
            for item in block.get("items", []):
                if isinstance(item, list):
                    count += _count_chart_blocks(item)
        if block.get("type") == "table":
            for row in block.get("rows", []):
                for cell in row.get("cells", []):
                    if isinstance(cell, dict):
                        cell_blocks = cell.get("blocks", [])
                        if isinstance(cell_blocks, list):
                            count += _count_chart_blocks(cell_blocks)
    return count


def save_document_ir(document_ir, base_name, timestamp):
    """
    Salvar o Document IR reencadernado em disco.

    Nomeado como `report_ir_{slug}_{timestamp}_regen.json` e salvo em
    `settings.DOCUMENT_IR_OUTPUT_DIR`, garantindo que o diretorio exista e retornando o caminho.

    Parametros:
        document_ir: IR completo ja encadernado
        base_name: Fragmento seguro de nome de arquivo gerado a partir do tema/titulo
        timestamp: String de timestamp, usada para diferenciar multiplas regeneracoes

    Retorno:
        Path: Caminho do arquivo IR salvo
    """
    output_dir = Path(settings.DOCUMENT_IR_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    ir_filename = f"report_ir_{base_name}_{timestamp}_regen.json"
    ir_path = output_dir / ir_filename
    ir_path.write_text(json.dumps(document_ir, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"IR salvo: {ir_path}")
    return ir_path


def render_html(document_ir, base_name, timestamp, ir_path=None):
    """
    Renderizar o Document IR em HTML usando HTMLRenderer e salvar.

    Apos renderizacao, salva em `final_reports/html`, imprimindo estatisticas de validacao
    de graficos para observar a situacao de reparo/falha dos dados Chart.js.

    Parametros:
        document_ir: IR completo encadernado
        base_name: Fragmento do nome de arquivo (originado do tema/titulo do relatorio)
        timestamp: String de timestamp
        ir_path: Opcional, caminho do arquivo IR; quando fornecido, salva automaticamente apos reparo

    Retorno:
        Path: Caminho do arquivo HTML gerado
    """
    renderer = HTMLRenderer()
    # Passar ir_file_path para salvar automaticamente apos reparo
    html_content = renderer.render(document_ir, ir_file_path=str(ir_path) if ir_path else None)

    output_dir = Path(settings.OUTPUT_DIR) / "html"
    output_dir.mkdir(parents=True, exist_ok=True)
    html_filename = f"report_html_{base_name}_{timestamp}.html"
    html_path = output_dir / html_filename
    html_path.write_text(html_content, encoding="utf-8")

    file_size_mb = html_path.stat().st_size / (1024 * 1024)
    logger.info(f"HTML gerado com sucesso: {html_path} ({file_size_mb:.2f} MB)")
    logger.info(
        "Estatisticas de validacao de graficos: "
        f"total={renderer.chart_validation_stats.get('total', 0)}, "
        f"valid={renderer.chart_validation_stats.get('valid', 0)}, "
        f"repaired={renderer.chart_validation_stats.get('repaired_locally', 0) + renderer.chart_validation_stats.get('repaired_api', 0)}, "
        f"failed={renderer.chart_validation_stats.get('failed', 0)}"
    )
    return html_path


def build_slug(text):
    """
    Converter tema/titulo em um fragmento seguro para o sistema de arquivos.

    Mantem apenas letras/numeros/espacos/underscores/hifens, substitui espacos por underscores,
    e limita a no maximo 60 caracteres para evitar nomes de arquivo muito longos.

    Parametros:
        text: Tema ou titulo original

    Retorno:
        str: String segura apos limpeza
    """
    text = str(text or "report")
    sanitized = "".join(c for c in text if c.isalnum() or c in (" ", "-", "_")).strip()
    sanitized = sanitized.replace(" ", "_")
    return sanitized[:60] or "report"


def main():
    """
    Ponto de entrada principal: ler os capitulos mais recentes, encadernar IR e renderizar HTML.

    Fluxo:
        1) Encontrar o diretorio de execucao mais recente e ler o manifest;
        2) Carregar capitulos e executar validacao estrutural (apenas aviso);
        3) Encadernar o IR completo e salvar copia do IR;
        4) Renderizar HTML e exibir caminho e estatisticas.

    Retorno:
        int: 0 indica sucesso, outros indicam falha.
    """
    logger.info("Reencadernando e renderizando HTML usando os capitulos LLM mais recentes")

    chapter_root = Path(settings.CHAPTER_OUTPUT_DIR)
    latest_run = find_latest_run_dir(chapter_root)
    if not latest_run:
        return 1

    report_id, metadata = load_manifest(latest_run)
    if not report_id or metadata is None:
        return 1

    chapters = load_chapters(latest_run)
    if not chapters:
        logger.error("Nenhum JSON de capitulo encontrado, impossivel encadernar")
        return 1

    validate_chapters(chapters)

    document_ir = stitch_document(report_id, metadata, chapters)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = build_slug(
        metadata.get("query") or metadata.get("title") or metadata.get("reportId") or report_id
    )

    ir_path = save_document_ir(document_ir, base_name, timestamp)
    # Passar ir_path para que graficos reparados sejam salvos automaticamente no arquivo IR
    html_path = render_html(document_ir, base_name, timestamp, ir_path=ir_path)

    logger.info("")
    logger.info("Encadernacao e renderizacao HTML concluidas")
    logger.info(f"Arquivo IR: {ir_path.resolve()}")
    logger.info(f"Arquivo HTML: {html_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
