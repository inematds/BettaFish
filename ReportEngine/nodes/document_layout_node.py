"""
根据模板Sumario与多源relatorio，生成整本relatorio的标题/Sumario/主题设计。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from loguru import logger

from ..core import TemplateSection
from ..prompts import (
    SYSTEM_PROMPT_DOCUMENT_LAYOUT,
    build_document_layout_prompt,
)
from ..utils.json_parser import RobustJSONParser, JSONParseError
from .base_node import BaseNode


class DocumentLayoutNode(BaseNode):
    """
    负责生成全局标题、Sumario与Hero设计。

    结合模板切片、relatorio摘要与论坛讨论，指导整本书的视觉与结构基调。
    """

    def __init__(self, llm_client):
        """记录Cliente LLM并设置节点名字，供BaseNode日志使用"""
        super().__init__(llm_client, "DocumentLayoutNode")
        # 初始化鲁棒JSON解析器，启用所有修复策略
        self.json_parser = RobustJSONParser(
            enable_json_repair=True,
            enable_llm_repair=False,  # Pode ativar reparo LLM conforme necessario
            max_repair_attempts=3,
        )

    def run(
        self,
        sections: List[TemplateSection],
        template_markdown: str,
        reports: Dict[str, str],
        forum_logs: str,
        query: str,
        template_overview: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """
        综合模板+多源内容，生成全书的标题、Sumario结构与主题色板。

        Parametros:
            sections: 模板切片后的章节列表。
            template_markdown: 模板原文，用于LLM理解上下文。
            reports: 三个引擎的内容映射。
            forum_logs: 论坛讨论摘要。
            query: Palavra de consulta do usuario.
            template_overview: 预生成的模板概览，可复用以减少提示词长度。

        Retorna:
            dict: 包含 title/subtitle/toc/hero/themeTokens 等设计信息的字典。
        """
        # 将模板原文、切片结构与多源relatorio一并喂给LLM，便于其理解层级与素材
        payload = {
            "query": query,
            "template": {
                "raw": template_markdown,
                "sections": [section.to_dict() for section in sections],
            },
            "templateOverview": template_overview
            or {
                "title": sections[0].title if sections else "",
                "chapters": [section.to_dict() for section in sections],
            },
            "reports": reports,
            "forumLogs": forum_logs,
        }

        user_message = build_document_layout_prompt(payload)
        response = self.llm_client.stream_invoke_to_string(
            SYSTEM_PROMPT_DOCUMENT_LAYOUT,
            user_message,
            temperature=0.3,
            top_p=0.9,
        )
        design = self._parse_response(response)
        logger.info("Design de titulo/sumario do documento gerado")
        return design

    def _parse_response(self, raw: str) -> Dict[str, Any]:
        """
        解析LLM返回的JSON文本，若失败则抛出友好Erro(s)。

        使用鲁棒JSON解析器进行多重修复尝试：
        1. 清理markdown标记和思考内容
        2. 本地语法修复（括号平衡、逗号补全、控制caracteres转义等）
        3. 使用json_repair库进行高级修复
        4. 可选的LLM辅助修复

        Parametros:
            raw: LLM原始返回string，允许带```包裹、思考内容等。

        Retorna:
            dict: 结构化的设计稿。

        Excecoes:
            ValueError: 当响应为空或Falha na analise JSON时抛出。
        """
        try:
            result = self.json_parser.parse(
                raw,
                context_name="Design do documento",
                # Sumario字段已更名为 tocPlan，这里跟随最新Schema校验
                expected_keys=["title", "tocPlan", "hero"],
            )
            # 验证关键字段的类型
            if not isinstance(result.get("title"), str):
                logger.warning("Design do documento缺少title字段或类型Erro(s)，使用默认值")
                result.setdefault("title", "未命名relatorio")

            # 处理tocPlan字段
            toc_plan = result.get("tocPlan", [])
            if not isinstance(toc_plan, list):
                logger.warning("Design do documento缺少tocPlan字段或类型Erro(s)，使用空列表")
                result["tocPlan"] = []
            else:
                # 清理tocPlan中的description字段
                result["tocPlan"] = self._clean_toc_plan_descriptions(toc_plan)

            if not isinstance(result.get("hero"), dict):
                logger.warning("Design do documento缺少hero字段或类型Erro(s)，使用空对象")
                result.setdefault("hero", {})

            return result
        except JSONParseError as exc:
            # 转换为原有的异常类型以保持向后兼容
            raise ValueError(f"Design do documentoFalha na analise JSON: {exc}") from exc

    def _clean_toc_plan_descriptions(self, toc_plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Limpar campo description de cada entrada no tocPlan, removendo possiveis fragmentos JSON.

        Parametros:
            toc_plan: 原始的Sumario计划列表

        Retorna:
            List[Dict[str, Any]]: 清理后的Sumario计划列表
        """
        import re

        def clean_text(text: Any) -> str:
            """Limpar fragmentos JSON do texto"""
            if not text or not isinstance(text, str):
                return ""

            cleaned = text

            # 移除以逗号+空白+{开头的不完整JSON对象
            cleaned = re.sub(r',\s*\{[^}]*$', '', cleaned)

            # 移除以逗号+空白+[开头的不完整JSON数组
            cleaned = re.sub(r',\s*\[[^\]]*$', '', cleaned)

            # 移除孤立的 { 加上后续内容（如果没有匹配的 }）
            open_brace_pos = cleaned.rfind('{')
            if open_brace_pos != -1:
                close_brace_pos = cleaned.rfind('}')
                if close_brace_pos < open_brace_pos:
                    cleaned = cleaned[:open_brace_pos].rstrip(',，、 \t\n')

            # 移除孤立的 [ 加上后续内容（如果没有匹配的 ]）
            open_bracket_pos = cleaned.rfind('[')
            if open_bracket_pos != -1:
                close_bracket_pos = cleaned.rfind(']')
                if close_bracket_pos < open_bracket_pos:
                    cleaned = cleaned[:open_bracket_pos].rstrip(',，、 \t\n')

            # 移除看起来像JSON键值对的片段
            cleaned = re.sub(r',?\s*"[^"]+"\s*:\s*"[^"]*$', '', cleaned)
            cleaned = re.sub(r',?\s*"[^"]+"\s*:\s*[^,}\]]*$', '', cleaned)

            # 清理末尾的逗号和空白
            cleaned = cleaned.rstrip(',，、 \t\n')

            return cleaned.strip()

        cleaned_plan = []
        for entry in toc_plan:
            if not isinstance(entry, dict):
                continue

            # 清理description字段
            if "description" in entry:
                original_desc = entry["description"]
                cleaned_desc = clean_text(original_desc)

                if cleaned_desc != original_desc:
                    logger.warning(
                        f"清理Sumario项 '{entry.get('display', 'unknown')}' fragmentos JSON no campo description de:\n"
                        f"  Original: {original_desc[:100]}...\n"
                        f"  Apos limpeza: {cleaned_desc[:100]}..."
                    )
                    entry["description"] = cleaned_desc

            cleaned_plan.append(entry)

        return cleaned_plan


__all__ = ["DocumentLayoutNode"]
