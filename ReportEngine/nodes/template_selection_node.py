"""
模板选择节点。

综合用户查询、三引擎relatorio、logs do forum与本地模板库，
调用LLM挑选最合适的relatorio骨架。
"""

import os
import json
from typing import Dict, Any, List, Optional
from loguru import logger

from .base_node import BaseNode
from ..prompts import SYSTEM_PROMPT_TEMPLATE_SELECTION
from ..utils.json_parser import RobustJSONParser, JSONParseError


class TemplateSelectionNode(BaseNode):
    """
    No de processamento de selecao de template.

    Responsavel por preparar lista de templates candidatos, construir prompts, analisar resultados do LLM,
    e reverter para template integrado em caso de falha.
    """
    
    def __init__(self, llm_client, template_dir: str = "ReportEngine/report_template"):
        """
        初始化模板选择节点

        Args:
            llm_client: Cliente LLM
            template_dir: 模板SumarioCaminho
        """
        super().__init__(llm_client, "TemplateSelectionNode")
        self.template_dir = template_dir
        # 初始化鲁棒JSON解析器，启用所有修复策略
        self.json_parser = RobustJSONParser(
            enable_json_repair=True,
            enable_llm_repair=False,
            max_repair_attempts=3,
        )
        
    def run(self, input_data: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """
        Executar selecao de template.
        
        Args:
            input_data: 包含查询和relatorio内容的字典
                - query: Consulta original
                - reports: 三个子agent的relatorio列表
                - forum_logs: logs do forum内容
                
        Returns:
            选择的模板信息，包含名称、内容与Motivo da selecao
        """
        logger.info("Iniciando selecao de template...")
        
        query = input_data.get('query', '')
        reports = input_data.get('reports', [])
        forum_logs = input_data.get('forum_logs', '')
        
        # Obter templates disponiveis
        available_templates = self._get_available_templates()
        
        if not available_templates:
            logger.info("Nenhum template predefinido encontrado, usando template padrao integrado")
            return self._get_fallback_template()
        
        # Usar LLM para selecao de template
        try:
            llm_result = self._llm_template_selection(query, reports, forum_logs, available_templates)
            if llm_result:
                return llm_result
        except Exception as e:
            logger.exception(f"Falha na selecao de template pelo LLM: {str(e)}")
        
        # Se a selecao do LLM falhar, usar plano alternativo
        return self._get_fallback_template()
    

    
    def _llm_template_selection(self, query: str, reports: List[Any], forum_logs: str, 
                              available_templates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Usando LLM进行模板选择。

        构造模板列表与relatorio摘要 → 调用LLM → 解析JSON →
        验证模板是否存在并返回标准结构。

        Parametros:
            query: 用户输入的主题词。
            reports: 多个分析引擎的relatorio内容。
            forum_logs: logs do forum，可能为空。
            available_templates: 本地可用模板清单。

        Retorna:
            dict | None: 若LLM成功返回合法结果则包含模板信息，否则为None。
        """
        logger.info("Tentando usar LLM para selecao de template...")
        
        # Construir lista de templates
        template_list = "\n".join([f"- {t['name']}: {t['description']}" for t in available_templates])
        
        # Construir resumo do conteudo do relatorio
        reports_summary = ""
        if reports:
            reports_summary = "\n\n=== 分析引擎relatorio内容 ===\n"
            for i, report in enumerate(reports, 1):
                # 获取relatorio内容，支持不同的数据格式
                if isinstance(report, dict):
                    content = report.get('content', str(report))
                elif hasattr(report, 'content'):
                    content = report.content
                else:
                    content = str(report)
                
                # 截断过长的内容，保留前1000个caracteres
                if len(content) > 1000:
                    content = content[:1000] + "...(内容已截断)"
                
                reports_summary += f"\nrelatorio{i}内容:\n{content}\n"
        
        # 构建logs do forum摘要
        forum_summary = ""
        if forum_logs and forum_logs.strip():
            forum_summary = "\n\n=== 三个引擎的讨论内容 ===\n"
            # 截断过长的日志内容，保留前800个caracteres
            if len(forum_logs) > 800:
                forum_content = forum_logs[:800] + "...(讨论内容已截断)"
            else:
                forum_content = forum_logs
            forum_summary += forum_content
        
        user_message = f"""查询内容: {query}

relatorio数量: {len(reports)} 个分析引擎relatorio
logs do forum: {'有' if forum_logs else '无'}
{reports_summary}{forum_summary}

可用模板:
{template_list}

请根据查询内容、relatorio内容和logs do forum的具体情况，选择最合适的模板。"""
        
        # Chamar LLM
        response = self.llm_client.stream_invoke_to_string(SYSTEM_PROMPT_TEMPLATE_SELECTION, user_message)

        # 检查响应是否为空
        if not response or not response.strip():
            logger.error("LLM retornou resposta vazia")
            return None

        logger.info(f"Resposta bruta do LLM: {response}")

        # 尝试解析JSON响应，使用鲁棒解析器
        try:
            result = self.json_parser.parse(
                response,
                context_name="模板选择",
                expected_keys=["template_name", "selection_reason"],
            )

            # Verificar se o template selecionado existe
            selected_template_name = result.get('template_name', '')
            for template in available_templates:
                if template['name'] == selected_template_name or selected_template_name in template['name']:
                    logger.info(f"LLMTemplate selecionado: {selected_template_name}")
                    return {
                        'template_name': template['name'],
                        'template_content': template['content'],
                        'selection_reason': result.get('selection_reason', 'Selecao inteligente do LLM')
                    }

            logger.error(f"Template selecionado pelo LLM nao existe: {selected_template_name}")
            return None

        except JSONParseError as e:
            logger.error(f"Falha na analise JSON: {str(e)}")
            # Tentando extrair informacoes do template da resposta de texto
            return self._extract_template_from_text(response, available_templates)
    

    def _extract_template_from_text(self, response: str, available_templates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Extraido da resposta de texto模板信息。

        当LLM未输出合法JSON时，尝试匹配模板名称关键字做降级。

        Parametros:
            response: 非结构化的LLM文本。
            available_templates: 可选模板列表。

        Retorna:
            dict | None: 匹配成功时返回模板详情，否则为None。
        """
        logger.info("Tentando extrair informacoes do template da resposta de texto")
        
        # 查找响应中是否包含模板名称
        for template in available_templates:
            template_name_variants = [
                template['name'],
                template['name'].replace('.md', ''),
                template['name'].replace('模板', ''),
            ]
            
            for variant in template_name_variants:
                if variant in response:
                    logger.info(f"Template encontrado na resposta: {template['name']}")
                    return {
                        'template_name': template['name'],
                        'template_content': template['content'],
                        'selection_reason': 'Extraido da resposta de texto'
                    }
        
        return None
    
    def _get_available_templates(self) -> List[Dict[str, Any]]:
        """
        Obter lista de templates disponiveis.

        枚举模板Sumario下的 `.md` Arquivo并读取内容与描述字段。

        Retorna:
            list[dict]: 每项包含 name/path/content/description。
        """
        templates = []
        
        if not os.path.exists(self.template_dir):
            logger.error(f"Diretorio de templates nao existe: {self.template_dir}")
            return templates
        
        # Encontrar todos os arquivos de template markdown
        for filename in os.listdir(self.template_dir):
            if filename.endswith('.md'):
                template_path = os.path.join(self.template_dir, filename)
                try:
                    with open(template_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    template_name = filename.replace('.md', '')
                    description = self._extract_template_description(template_name)
                    
                    templates.append({
                        'name': template_name,
                        'path': template_path,
                        'content': content,
                        'description': description
                    })
                except Exception as e:
                    logger.exception(f"Falha ao ler arquivo de template {filename}: {str(e)}")
        
        return templates
    
    def _extract_template_description(self, template_name: str) -> str:
        """Gerar descricao com base no nome do template para ajudar o LLM a entender o posicionamento do template."""
        if '企业品牌' in template_name:
            return "Aplicavel a analise de reputacao e imagem de marca empresarial"
        elif '市场竞争' in template_name:
            return "Aplicavel a analise de cenario competitivo de mercado e concorrentes"
        elif '日常' in template_name or '定期' in template_name:
            return "Aplicavel a monitoramento diario e relatorios periodicos"
        elif '政策' in template_name or '行业' in template_name:
            return "Aplicavel a analise de impacto de politicas e dinamicas da industria"
        elif '热点' in template_name or '社会' in template_name:
            return "Aplicavel a analise de temas quentes sociais e eventos publicos"
        elif '突发' in template_name or '危机' in template_name:
            return "Aplicavel a eventos emergenciais e relacoes publicas de crise"
        
        return "Template de relatorio generico"
    

    
    def _get_fallback_template(self) -> Dict[str, Any]:
        """
        Obter template padrao de fallback (template vazio, deixando o LLM decidir livremente).

        Retorna:
            dict: 结构体字段与LLM返回一致，方便直接替换。
        """
        logger.info("Nenhum template adequado encontrado, usando template vazio para o LLM decidir")
        
        return {
            'template_name': 'Template de livre criacao',
            'template_content': '',
            'selection_reason': '未Encontrado(s)合适的预设模板，让LLM根据内容自行设计relatorio结构'
        }
