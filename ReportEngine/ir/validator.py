"""
Validador de estrutura JSON em nivel de capitulo.

Apos o LLM gerar o IR por capitulo, e necessaria validacao rigorosa antes da persistencia e montagem para evitar
falhas estruturais durante a renderizacao. Este modulo implementa logica de validacao leve em Python,
无需依赖jsonschema库即可快速定位Erro(s)。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .schema import (
    ALLOWED_BLOCK_TYPES,
    ALLOWED_INLINE_MARKS,
    ENGINE_AGENT_TITLES,
    IR_VERSION,
)


class IRValidator:
    """
    Validador de estrutura IR de capitulo.

    说明：
        - validate_chapter返回(是否通过, Erro(s)列表)
        - Erro(s)定位采用path语法，便于快速追踪
        - Validacao granular integrada para todos os blocos como heading/paragraph/list/table
    """

    def __init__(self, schema_version: str = IR_VERSION):
        """Registrar versao atual do Schema, facilitando coexistencia de multiplas versoes no futuro"""
        self.schema_version = schema_version

    # ======== 对外接口 ========

    def validate_chapter(self, chapter: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validar campos obrigatorios e estrutura de blocks do objeto de capitulo individual"""
        errors: List[str] = []
        if not isinstance(chapter, dict):
            return False, ["chapter deve ser um objeto"]

        for field in ("chapterId", "title", "anchor", "order", "blocks"):
            if field not in chapter:
                errors.append(f"missing chapter.{field}")

        if not isinstance(chapter.get("blocks"), list) or not chapter.get("blocks"):
            errors.append("chapter.blocksdeve ser um array nao vazio")
            return False, errors

        blocks = chapter.get("blocks", [])
        for idx, block in enumerate(blocks):
            self._validate_block(block, f"blocks[{idx}]", errors)

        return len(errors) == 0, errors

    # ======== 内部工具 ========

    def _validate_block(self, block: Any, path: str, errors: List[str]):
        """Chamar validador diferente de acordo com o tipo de block"""
        if not isinstance(block, dict):
            errors.append(f"{path} deve ser um objeto")
            return

        block_type = block.get("type")
        if block_type not in ALLOWED_BLOCK_TYPES:
            errors.append(f"{path}.type nao suportado: {block_type}")
            return

        validator = getattr(self, f"_validate_{block_type}_block", None)
        if validator:
            validator(block, path, errors)

    def _validate_heading_block(self, block: Dict[str, Any], path: str, errors: List[str]):
        """heading deve ter level/text/anchor"""
        if "level" not in block or not isinstance(block["level"], int):
            errors.append(f"{path}.level deve ser um inteiro")
        if "text" not in block:
            errors.append(f"{path}.text 缺失")
        if "anchor" not in block:
            errors.append(f"{path}.anchor 缺失")

    def _validate_paragraph_block(self, block: Dict[str, Any], path: str, errors: List[str]):
        """paragraph requer inlines nao vazio, validando cada item"""
        inlines = block.get("inlines")
        if not isinstance(inlines, list) or not inlines:
            errors.append(f"{path}.inlines deve ser um array nao vazio")
            return
        for idx, run in enumerate(inlines):
            self._validate_inline_run(run, f"{path}.inlines[{idx}]", errors)

    def _validate_list_block(self, block: Dict[str, Any], path: str, errors: List[str]):
        """Lista requer declaracao de listType e cada item deve ser um array de blocks"""
        if block.get("listType") not in {"ordered", "bullet", "task"}:
            errors.append(f"{path}.listType valor invalido")
        items = block.get("items")
        if not isinstance(items, list) or not items:
            errors.append(f"{path}.items 必须是非空列表")
            return
        for i, item in enumerate(items):
            if not isinstance(item, list):
                errors.append(f"{path}.items[{i}] deve ser um array de blocos")
                continue
            for j, sub_block in enumerate(item):
                self._validate_block(sub_block, f"{path}.items[{i}][{j}]", errors)

    def _validate_table_block(self, block: Dict[str, Any], path: str, errors: List[str]):
        """Tabela deve fornecer rows/cells/blocks, validando conteudo das celulas recursivamente"""
        rows = block.get("rows")
        if not isinstance(rows, list) or not rows:
            errors.append(f"{path}.rows deve ser um array nao vazio")
            return
        for r_idx, row in enumerate(rows):
            cells = row.get("cells") if isinstance(row, dict) else None
            if not isinstance(cells, list) or not cells:
                errors.append(f"{path}.rows[{r_idx}].cells deve ser um array nao vazio")
                continue
            for c_idx, cell in enumerate(cells):
                if not isinstance(cell, dict):
                    errors.append(f"{path}.rows[{r_idx}].cells[{c_idx}] deve ser um objeto")
                    continue
                blocks = cell.get("blocks")
                if not isinstance(blocks, list) or not blocks:
                    errors.append(
                        f"{path}.rows[{r_idx}].cells[{c_idx}].blocks deve ser um array nao vazio"
                    )
                    continue
                for b_idx, sub_block in enumerate(blocks):
                    self._validate_block(
                        sub_block,
                        f"{path}.rows[{r_idx}].cells[{c_idx}].blocks[{b_idx}]",
                        errors,
                    )

    def _validate_swotTable_block(self, block: Dict[str, Any], path: str, errors: List[str]):
        """Tabela SWOT deve fornecer pelo menos um dos quatro quadrantes, cada quadrante como array de entradas"""
        quadrants = ("strengths", "weaknesses", "opportunities", "threats")
        if not any(block.get(name) is not None for name in quadrants):
            errors.append(f"{path} necessita conter pelo menos strengths/weaknesses/opportunities/threats um deles")
        for name in quadrants:
            entries = block.get(name)
            if entries is None:
                continue
            if not isinstance(entries, list):
                errors.append(f"{path}.{name} deve ser um array")
                continue
            for idx, entry in enumerate(entries):
                self._validate_swot_item(entry, f"{path}.{name}[{idx}]", errors)

    # SWOT impact 字段允许的评级值
    ALLOWED_IMPACT_VALUES = {"低", "中低", "中", "中高", "高", "极高"}

    def _validate_swot_item(self, item: Any, path: str, errors: List[str]):
        """Entrada SWOT individual suporta string ou objeto com campos"""
        if isinstance(item, str):
            if not item.strip():
                errors.append(f"{path} nao pode ser uma string vazia")
            return
        if not isinstance(item, dict):
            errors.append(f"{path} deve ser uma string ou objeto")
            return
        title = None
        for key in ("title", "label", "text", "detail", "description"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                title = value
                break
        if title is None:
            errors.append(f"{path} ausencia de campos de texto como title/label/text/description")

        # Validar campo impact: somente valores de classificacao permitidos
        impact = item.get("impact")
        if impact is not None:
            if not isinstance(impact, str) or impact not in self.ALLOWED_IMPACT_VALUES:
                errors.append(
                    f"{path}.impact somente permitido preencher classificacao de impacto (baixo/medio-baixo/medio/medio-alto/alto/muito alto),"
                    f"valor atual: {impact}；para descricoes detalhadas, use o campo detail"
                )

        # # Validar campo score: somente numeros de 0 a 10 (desabilitado)
        # score = item.get("score")
        # if score is not None:
        #     valid_score = False
        #     if isinstance(score, (int, float)):
        #         valid_score = 0 <= score <= 10
        #     elif isinstance(score, str):
        #         # Compativel comstring形式的数字
        #         try:
        #             numeric_score = float(score)
        #             valid_score = 0 <= numeric_score <= 10
        #         except ValueError:
        #             valid_score = False
        #     if not valid_score:
        #         errors.append(
        #             f"{path}.score somente numeros de 0 a 10 permitidos,valor atual: {score}"
        #         )

    def _validate_blockquote_block(
        self, block: Dict[str, Any], path: str, errors: List[str]
    ):
        """Bloco de citacao necessita de pelo menos um sub-block interno"""
        inner = block.get("blocks")
        if not isinstance(inner, list) or not inner:
            errors.append(f"{path}.blocks deve ser um array nao vazio")
            return
        for idx, sub_block in enumerate(inner):
            self._validate_block(sub_block, f"{path}.blocks[{idx}]", errors)

    def _validate_engineQuote_block(
        self, block: Dict[str, Any], path: str, errors: List[str]
    ):
        """Bloco de citacao de motor unico requer anotacao de engine e sub-blocks"""
        engine_raw = block.get("engine")
        engine = engine_raw.lower() if isinstance(engine_raw, str) else None
        if engine not in {"insight", "media", "query"}:
            errors.append(f"{path}.engine valor invalido: {engine_raw}")
        title = block.get("title")
        expected_title = ENGINE_AGENT_TITLES.get(engine) if engine else None
        if title is None:
            errors.append(f"{path}.title 缺失")
        elif not isinstance(title, str):
            errors.append(f"{path}.title deve ser uma string")
        elif expected_title and title != expected_title:
            errors.append(
                f"{path}.title deve ser consistente com engine, usar nome do Agent correspondente: {expected_title}"
            )
        inner = block.get("blocks")
        if not isinstance(inner, list) or not inner:
            errors.append(f"{path}.blocks deve ser um array nao vazio")
            return
        for idx, sub_block in enumerate(inner):
            sub_path = f"{path}.blocks[{idx}]"
            if not isinstance(sub_block, dict):
                errors.append(f"{sub_path} deve ser um objeto")
                continue
            if sub_block.get("type") != "paragraph":
                errors.append(f"{sub_path}.type apenas paragraph permitido")
                continue
            # Reutilizar validacao de estrutura paragraph, mas restringir marks
            inlines = sub_block.get("inlines")
            if not isinstance(inlines, list) or not inlines:
                errors.append(f"{sub_path}.inlines deve ser um array nao vazio")
                continue
            for ridx, run in enumerate(inlines):
                self._validate_inline_run(run, f"{sub_path}.inlines[{ridx}]", errors)
                if not isinstance(run, dict):
                    continue
                marks = run.get("marks") or []
                if not isinstance(marks, list):
                    errors.append(f"{sub_path}.inlines[{ridx}].marks deve ser um array")
                    continue
                for midx, mark in enumerate(marks):
                    mark_type = mark.get("type") if isinstance(mark, dict) else None
                    if mark_type not in {"bold", "italic"}:
                        errors.append(
                            f"{sub_path}.inlines[{ridx}].marks[{midx}].type apenas bold/italic permitidos"
                        )

    def _validate_callout_block(self, block: Dict[str, Any], path: str, errors: List[str]):
        """callout requer declaracao de tone e pelo menos um sub-block"""
        tone = block.get("tone")
        if tone not in {"info", "warning", "success", "danger"}:
            errors.append(f"{path}.tone valor invalido: {tone}")
        blocks = block.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            errors.append(f"{path}.blocks deve ser um array nao vazio")
            return
        for idx, sub_block in enumerate(blocks):
            self._validate_block(sub_block, f"{path}.blocks[{idx}]", errors)

    def _validate_kpiGrid_block(self, block: Dict[str, Any], path: str, errors: List[str]):
        """KPI card requer items nao vazio, cada item contendo label/value"""
        items = block.get("items")
        if not isinstance(items, list) or not items:
            errors.append(f"{path}.items deve ser um array nao vazio")
            return
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"{path}.items[{idx}] deve ser um objeto")
                continue
            if "label" not in item or "value" not in item:
                errors.append(f"{path}.items[{idx}] requer label e value")

    def _validate_widget_block(self, block: Dict[str, Any], path: str, errors: List[str]):
        """widget deve declarar widgetId/type e fornecer dados ou referencia de dados"""
        if "widgetId" not in block:
            errors.append(f"{path}.widgetId 缺失")
        if "widgetType" not in block:
            errors.append(f"{path}.widgetType 缺失")
        if "data" not in block and "dataRef" not in block:
            errors.append(f"{path} requer data ou dataRef")

    def _validate_code_block(self, block: Dict[str, Any], path: str, errors: List[str]):
        """code block deve ter pelo menos content"""
        if "content" not in block:
            errors.append(f"{path}.content 缺失")

    def _validate_math_block(self, block: Dict[str, Any], path: str, errors: List[str]):
        """Bloco matematico requer campo latex"""
        if "latex" not in block:
            errors.append(f"{path}.latex 缺失")

    def _validate_figure_block(
        self, block: Dict[str, Any], path: str, errors: List[str]
    ):
        """figure requer objeto img com pelo menos src"""
        img = block.get("img")
        if not isinstance(img, dict):
            errors.append(f"{path}.img deve ser um objeto")
            return
        if "src" not in img:
            errors.append(f"{path}.img.src 缺失")

    def _validate_inline_run(
        self, run: Any, path: str, errors: List[str]
    ):
        """Validar legalidade de inline run e marks no paragraph"""
        if not isinstance(run, dict):
            errors.append(f"{path} deve ser um objeto")
            return
        if "text" not in run:
            errors.append(f"{path}.text 缺失")
        marks = run.get("marks", [])
        if marks is None:
            return
        if not isinstance(marks, list):
            errors.append(f"{path}.marks deve ser um array")
            return
        for m_idx, mark in enumerate(marks):
            if not isinstance(mark, dict):
                errors.append(f"{path}.marks[{m_idx}] deve ser um objeto")
                continue
            m_type = mark.get("type")
            if m_type not in ALLOWED_INLINE_MARKS:
                errors.append(f"{path}.marks[{m_idx}].type nao suportado: {m_type}")


__all__ = ["IRValidator"]
