"""
No de planejamento de extensao dos capitulos.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from loguru import logger

from ..core import TemplateSection
from ..prompts import (
    SYSTEM_PROMPT_WORD_BUDGET,
    build_word_budget_prompt,
)
from ..utils.json_parser import RobustJSONParser, JSONParseError
from .base_node import BaseNode


class WordBudgetNode(BaseNode):
    """
    Planejar contagem de palavras e pontos de enfase de cada capitulo.

    Gerar total de palavras, diretrizes de escrita globais e restricoes target/min/max de palavras para cada capitulo/secao.
    """

    def __init__(self, llm_client):
        """apenas registrarCliente LLM引用，方便run阶段发起请求"""
        super().__init__(llm_client, "WordBudgetNode")
        # 初始化鲁棒JSON解析器，启用所有修复策略
        self.json_parser = RobustJSONParser(
            enable_json_repair=True,
            enable_llm_repair=False,  # Pode ativar reparo LLM conforme necessario
            max_repair_attempts=3,
        )

    def run(
        self,
        sections: List[TemplateSection],
        design: Dict[str, Any],
        reports: Dict[str, str],
        forum_logs: str,
        query: str,
        template_overview: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """
        Planejar contagem de palavras dos capitulos com base no design e todos os materiais, dando ao LLM metas claras de extensao ao escrever.

        Parametros:
            sections: 模板章节列表。
            design: 布局节点返回的设计稿（title/toc/hero等）。
            reports: 三引擎relatorio映射。
            forum_logs: logs do forum原文。
            query: Palavra de consulta do usuario.
            template_overview: 可选的模板概览，含Meta-informacoes do capitulo.

        Retorna:
            dict: Planejamento de extensao dos capitulos结果，包含 `totalWords`、`globalGuidelines` 与逐章 `chapters`。
        """
        # 输入中除了章节骨架外，还包含布局节点输出，方便约束篇幅时参考视觉主次
        payload = {
            "query": query,
            "design": design,
            "sections": [section.to_dict() for section in sections],
            "templateOverview": template_overview
            or {
                "title": sections[0].title if sections else "",
                "chapters": [section.to_dict() for section in sections],
            },
            "reports": reports,
            "forumLogs": forum_logs,
        }
        user = build_word_budget_prompt(payload)
        response = self.llm_client.stream_invoke_to_string(
            SYSTEM_PROMPT_WORD_BUDGET,
            user,
            temperature=0.25,
            top_p=0.85,
        )
        plan = self._parse_response(response)
        logger.info("Planejamento de contagem de palavras dos capitulos gerado")
        return plan

    def _parse_response(self, raw: str) -> Dict[str, Any]:
        """
        Converter texto JSON de saida do LLM para dicionario, indicando anomalia de planejamento em caso de falha.

        使用鲁棒JSON解析器进行多重修复尝试：
        1. 清理markdown标记和思考内容
        2. 本地语法修复（括号平衡、逗号补全、控制caracteres转义等）
        3. 使用json_repair库进行高级修复
        4. 可选的LLM辅助修复

        Parametros:
            raw: LLM返回值，可能包含```包裹、思考内容等。

        Retorna:
            dict: 合法的篇幅规划JSON。

        Excecoes:
            ValueError: 当响应为空或Falha na analise JSON时抛出。
        """
        try:
            result = self.json_parser.parse(
                raw,
                context_name="篇幅规划",
                expected_keys=["totalWords", "globalGuidelines", "chapters"],
            )
            # 验证关键字段的类型
            if not isinstance(result.get("totalWords"), (int, float)):
                logger.warning("篇幅规划缺少totalWords字段或类型Erro(s)，使用默认值")
                result.setdefault("totalWords", 10000)
            if not isinstance(result.get("globalGuidelines"), list):
                logger.warning("篇幅规划缺少globalGuidelines字段或类型Erro(s)，使用空列表")
                result.setdefault("globalGuidelines", [])
            if not isinstance(result.get("chapters"), (list, dict)):
                logger.warning("篇幅规划缺少chapters字段或类型Erro(s)，使用空列表")
                result.setdefault("chapters", [])
            return result
        except JSONParseError as exc:
            # 转换为原有的异常类型以保持向后兼容
            raise ValueError(f"篇幅规划Falha na analise JSON: {exc}") from exc


__all__ = ["WordBudgetNode"]
