"""
Renderizador HTML/PDF baseado em IR de capitulos, implementando interacao e visual consistentes com o relatorio de exemplo.

Pontos principais:
1. Validacao/reparo integrado de dados Chart.js (ChartValidator+fallback LLM), eliminando injecao ou falha por configuracao invalida;
2. Dependencias como MathJax/Chart.js/html2canvas/jspdf embutidas com fallback CDN, compativel com ambientes offline;
3. Fonte Source Han Serif em Base64 (subconjunto) pre-configurada para exportacao unificada PDF/HTML, evitando caracteres ausentes.
"""

from __future__ import annotations

import ast
import copy
import html
import json
import os
import re
import base64
from pathlib import Path
from typing import Any, Dict, List
from loguru import logger

from ReportEngine.ir.schema import ENGINE_AGENT_TITLES
from ReportEngine.utils.chart_validator import (
    ChartValidator,
    ChartRepairer,
    ValidationResult,
    create_chart_validator,
    create_chart_repairer
)
from ReportEngine.utils.chart_repair_api import create_llm_repair_functions
from ReportEngine.utils.chart_review_service import get_chart_review_service


class HTMLRenderer:
    """
    Renderizador Document IR para HTML.

    - Le IR metadata/chapters e mapeia a estrutura para HTML responsivo;
    - Constroi dinamicamente Sumario, ancoras, scripts Chart.js e logica interativa;
    - Fornece variaveis de tema, mapeamento de numeracao e funcoes auxiliares.
    """

    # ===== Guia rapido do fluxo de renderizacao(para facilitar localizacao nos comentarios) =====
    # render(document_ir): unico ponto de entrada publico, reseta estado e encadeia _render_head / _render_body.
    # _render_head: constroi <head> com base nos themeTokens, injeta variaveis CSS, libs inline e CDN fallback.
    # _render_body: monta o esqueleto da pagina (cabecalho/header, Sumario/toc, capitulos/blocks, hidratacao de scripts).
    # _render_header: gera area de botoes no topo, IDs e eventos vinculados em _hydration_script.
    # _render_widget: processa componentes Chart.js/nuvem de palavras, valida e repara dados antes de escrever config <script type="application/json">.
    # _hydration_script: emite JS final, responsavel por interacao de botoes (troca de tema/impressao/exportacao) e instanciacao de graficos.

    CALLOUT_ALLOWED_TYPES = {
        "paragraph",
        "list",
        "table",
        "blockquote",
        "code",
        "math",
        "figure",
        "kpiGrid",
        "swotTable",
        "pestTable",
        "engineQuote",
    }
    INLINE_ARTIFACT_KEYS = {
        "props",
        "widgetId",
        "widgetType",
        "data",
        "dataRef",
        "datasets",
        "labels",
        "config",
        "options",
    }
    TABLE_COMPLEX_CHARS = set(
        "@％%（）()，,。；;：:、？?！!·…-—_+<>[]{}|\\/\"'`~$^&*#"
    )

    def __init__(self, config: Dict[str, Any] | None = None):
        """
        Inicializar cache do renderizador e permitir injecao de configuracoes adicionais.

        Descricao da hierarquia de parametros:
        - config: dict | None, permite ao chamador sobrescrever tema/opcoes de debug temporariamente, prioridade maxima;
          Chaves tipicas:
            - themeOverride: sobrescreve themeTokens dos metadados;
            - enableDebug: bool, se deve emitir logs adicionais.
        Estado interno:
        - self.document/metadata/chapters: armazena o IR de um ciclo de renderizacao;
        - self.widget_scripts: coleta JSON de configuracao de graficos, injetados no final de _render_body;
        - self._lib_cache/_pdf_font_base64: cache de libs locais e fontes, evitando IO repetido;
        - self.chart_validator/chart_repairer: reparadores local e LLM fallback para configuracao Chart.js;
        - self.chart_validation_stats: registra totais/origem de reparo/falhas para auditoria de logs.
        """
        self.config = config or {}
        self.document: Dict[str, Any] = {}
        self.widget_scripts: List[str] = []
        self.chart_counter = 0
        self.toc_entries: List[Dict[str, Any]] = []
        self.heading_counter = 0
        self.metadata: Dict[str, Any] = {}
        self.chapters: List[Dict[str, Any]] = []
        self.chapter_anchor_map: Dict[str, str] = {}
        self.heading_label_map: Dict[str, Dict[str, Any]] = {}
        self.primary_heading_index = 0
        self.secondary_heading_index = 0
        self.toc_rendered = False
        self.hero_kpi_signature: tuple | None = None
        self._current_chapter: Dict[str, Any] | None = None
        self._lib_cache: Dict[str, str] = {}
        self._pdf_font_base64: str | None = None

        # Inicializar validador e reparador de graficos
        self.chart_validator = create_chart_validator()
        llm_repair_fns = create_llm_repair_functions()
        self.chart_repairer = create_chart_repairer(
            validator=self.chart_validator,
            llm_repair_fns=llm_repair_fns
        )
        # Imprimir status das funcoes de reparo LLM
        self._llm_repair_count = len(llm_repair_fns)
        if not llm_repair_fns:
            logger.warning("HTMLRenderer: nenhuma API LLM configurada, funcao de reparo de graficos via API indisponivel")
        else:
            logger.info(f"HTMLRenderer: {len(llm_repair_fns)} funcoes de reparo LLM configuradas")
        # Registrar graficos com reparo falho, evitar disparo repetido de ciclo de reparo LLM
        self._chart_failure_notes: Dict[str, str] = {}
        self._chart_failure_recorded: set[str] = set()

        # Informacoes estatisticas
        self.chart_validation_stats = {
            'total': 0,
            'valid': 0,
            'repaired_locally': 0,
            'repaired_api': 0,
            'failed': 0
        }

    @staticmethod
    def _get_lib_path() -> Path:
        """Obter caminho do diretorio de bibliotecas de terceiros"""
        return Path(__file__).parent / "libs"

    @staticmethod
    def _get_font_path() -> Path:
        """Retornar caminho da fonte necessaria para exportacao PDF (usando fonte subset otimizada)"""
        return Path(__file__).parent / "assets" / "fonts" / "SourceHanSerifSC-Medium-Subset.ttf"

    def _load_lib(self, filename: str) -> str:
        """
        Carregar conteudo do arquivo de biblioteca de terceiros especificado

        Parametros:
            filename: nome do arquivo da biblioteca

        Retorna:
            str: conteudo JavaScript do arquivo da biblioteca
        """
        if filename in self._lib_cache:
            return self._lib_cache[filename]

        lib_path = self._get_lib_path() / filename
        try:
            with open(lib_path, 'r', encoding='utf-8') as f:
                content = f.read()
                self._lib_cache[filename] = content
                return content
        except FileNotFoundError:
            print(f"Aviso: arquivo de biblioteca {filename} nao encontrado, sera usado link CDN de backup")
            return ""
        except Exception as e:
            print(f"Aviso: leitura do arquivo de biblioteca {filename} erro ocorreu: {e}")
            return ""

    def _load_pdf_font_data(self) -> str:
        """Carregar dados Base64 da fonte PDF, evitando leitura repetida de arquivos grandes"""
        if self._pdf_font_base64 is not None:
            return self._pdf_font_base64
        font_path = self._get_font_path()
        try:
            data = font_path.read_bytes()
            self._pdf_font_base64 = base64.b64encode(data).decode("ascii")
            return self._pdf_font_base64
        except FileNotFoundError:
            logger.warning("Arquivo de fonte PDF ausente：%s", font_path)
        except Exception as exc:
            logger.warning("Falha ao ler arquivo de fonte PDF：%s (%s)", font_path, exc)
        self._pdf_font_base64 = ""
        return self._pdf_font_base64

    def _reset_chart_validation_stats(self) -> None:
        """Redefinir estatisticas de validacao de graficos e limpar marcadores de contagem de falhas"""
        self.chart_validation_stats = {
            'total': 0,
            'valid': 0,
            'repaired_locally': 0,
            'repaired_api': 0,
            'failed': 0
        }
        # Manter cache de motivos de falha, mas redefinir contagens desta renderizacao
        self._chart_failure_recorded = set()

    def _build_script_with_fallback(
        self,
        inline_code: str,
        cdn_url: str,
        check_expression: str,
        lib_name: str,
        is_defer: bool = False
    ) -> str:
        """
        Construir tag script com mecanismo de fallback CDN

        Estrategia:
        1. Priorizar embutir codigo da biblioteca local
        2. Adicionar script de deteccao para verificar se a biblioteca foi carregada com sucesso
        3. Se a deteccao falhar, carregar dinamicamente versao CDN como reserva

        Parametros:
            inline_code: conteudo JavaScript da biblioteca local
            cdn_url: link CDN de reserva
            check_expression: expressao JavaScript para detectar se a biblioteca carregou com sucesso
            lib_name: nome da biblioteca (para saida de log)
            is_defer: se deve usar atributo defer

        Retorna:
            str: HTML completo da tag script
        """
        defer_attr = ' defer' if is_defer else ''

        if inline_code:
            # Embutir codigo da biblioteca local e adicionar deteccao de fallback
            return f"""
  <script{defer_attr}>
    // {lib_name} - versao embutida
    try {{
      {inline_code}
    }} catch (e) {{
      console.error('{lib_name} falha ao carregar versao embutida:', e);
    }}
  </script>
  <script{defer_attr}>
    // {lib_name} - deteccao de Fallback CDN
    (function() {{
      var checkLib = function() {{
        if (!({check_expression})) {{
          console.warn('{lib_name} versao local falhou, carregando versao de reserva do CDN...');
          var script = document.createElement('script');
          script.src = '{cdn_url}';
          script.onerror = function() {{
            console.error('{lib_name} carregamento de reserva CDN tambem falhou');
          }};
          script.onload = function() {{
            console.log('{lib_name} versao de reserva CDN carregada com sucesso');
          }};
          document.head.appendChild(script);
        }}
      }};

      // Deteccao atrasada para garantir tempo de execucao do codigo embutido
      if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', function() {{
          setTimeout(checkLib, 100);
        }});
      }} else {{
        setTimeout(checkLib, 100);
      }}
    }})();
  </script>""".strip()
        else:
            # Falha na leitura do arquivo local, usar CDN diretamente
            logger.warning(f"{lib_name}Arquivo local nao encontrado ou falha na leitura, CDN sera usado diretamente")
            return f'  <script{defer_attr} src="{cdn_url}"></script>'

    # ====== Ponto de entrada publico ======

    def render(
        self,
        document_ir: Dict[str, Any],
        ir_file_path: str | None = None
    ) -> str:
        """
        Recebe Document IR, reseta estado interno e emite HTML completo.

        Parametros:
            document_ir: Dados completos do relatorio gerados pelo DocumentComposer.
            ir_file_path: Opcional, caminho do arquivo IR; quando fornecido, salva automaticamente apos reparo.

        Retorna:
            str: Documento HTML completo pronto para gravar em disco.
        """
        self.document = document_ir or {}

        # Usar o ChartReviewService unificado para revisao e reparo de graficos
        # Resultados de reparo sao escritos diretamente no document_ir, evitando reparos duplicados em multiplas renderizacoes
        # review_document retorna estatisticas desta sessao (thread-safe)
        chart_service = get_chart_review_service()
        review_stats = chart_service.review_document(
            self.document,
            ir_file_path=ir_file_path,
            reset_stats=True,
            save_on_repair=bool(ir_file_path)
        )
        # Sincronizar estatisticas localmente (para compatibilidade com _log_chart_validation_stats antigo)
        # Usar objeto ReviewStats retornado, nao o chart_service.stats compartilhado
        self.chart_validation_stats.update(review_stats.to_dict())

        self.widget_scripts = []
        self.chart_counter = 0
        self.heading_counter = 0
        self.metadata = self.document.get("metadata", {}) or {}
        raw_chapters = self.document.get("chapters", []) or []
        self.toc_rendered = False
        self.chapters = self._prepare_chapters(raw_chapters)
        self.chapter_anchor_map = {
            chapter.get("chapterId"): chapter.get("anchor")
            for chapter in self.chapters
            if chapter.get("chapterId") and chapter.get("anchor")
        }
        self.heading_label_map = self._compute_heading_labels(self.chapters)
        self.toc_entries = self._collect_toc_entries(self.chapters)

        metadata = self.metadata
        theme_tokens = metadata.get("themeTokens") or self.document.get("themeTokens", {})
        title = metadata.get("title") or metadata.get("query") or "Relatorio inteligente de analise de opiniao publica"
        hero_kpis = (metadata.get("hero") or {}).get("kpis")
        self.hero_kpi_signature = self._kpi_signature_from_items(hero_kpis)

        head = self._render_head(title, theme_tokens)
        body = self._render_body()

        # Emitir estatisticas de validacao de graficos
        self._log_chart_validation_stats()

        return f"<!DOCTYPE html>\n<html lang=\"pt-BR\" class=\"no-js\">\n{head}\n{body}\n</html>"

    # ====== Cabecalho / Corpo ======

    def _resolve_color_value(self, value: Any, fallback: str) -> str:
        """Extrair valor string do token de cor"""
        if isinstance(value, str):
            value = value.strip()
            return value or fallback
        if isinstance(value, dict):
            for key in ("main", "value", "color", "base", "default"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
            for candidate in value.values():
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
        return fallback

    def _resolve_color_family(self, value: Any, fallback: Dict[str, str]) -> Dict[str, str]:
        """Analisar tres cores principal/claro/escuro, com fallback para valores padrao quando ausentes"""
        result = {
            "main": fallback.get("main", "#007bff"),
            "light": fallback.get("light", fallback.get("main", "#007bff")),
            "dark": fallback.get("dark", fallback.get("main", "#007bff")),
        }
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                result["main"] = stripped
            return result
        if isinstance(value, dict):
            result["main"] = self._resolve_color_value(value.get("main") or value, result["main"])
            result["light"] = self._resolve_color_value(value.get("light") or value.get("lighter"), result["light"])
            result["dark"] = self._resolve_color_value(value.get("dark") or value.get("darker"), result["dark"])
        return result

    def _render_head(self, title: str, theme_tokens: Dict[str, Any]) -> str:
        """
        Renderizar secao <head>, carregar CSS de tema e dependencias de scripts necessarias.

        Parametros:
            title: Conteudo da tag title da pagina.
            theme_tokens: Variaveis de tema para injecao de CSS. Niveis suportados:
              - colors: {primary/secondary/bg/text/card/border/...}
              - typography: {fontFamily, fonts:{body,heading}}，quando body/heading estao vazios, usa fonte do sistema
              - spacing: {container,gutter/pagePadding}

        Retorna:
            str: Fragmento HTML do head.
        """
        css = self._build_css(theme_tokens)

        # Carregar bibliotecas de terceiros
        chartjs = self._load_lib("chart.js")
        chartjs_sankey = self._load_lib("chartjs-chart-sankey.js")
        html2canvas = self._load_lib("html2canvas.min.js")
        jspdf = self._load_lib("jspdf.umd.min.js")
        mathjax = self._load_lib("mathjax.js")
        wordcloud2 = self._load_lib("wordcloud2.min.js")

        # Gerar tags script embutidas com mecanismo de fallback CDN para cada biblioteca
        # Chart.js - biblioteca principal de graficos
        chartjs_tag = self._build_script_with_fallback(
            inline_code=chartjs,
            cdn_url="https://cdn.jsdelivr.net/npm/chart.js",
            check_expression="typeof Chart !== 'undefined'",
            lib_name="Chart.js"
        )

        # Plugin Chart.js Sankey
        sankey_tag = self._build_script_with_fallback(
            inline_code=chartjs_sankey,
            cdn_url="https://cdn.jsdelivr.net/npm/chartjs-chart-sankey@4",
            check_expression="typeof Chart !== 'undefined' && Chart.controllers && Chart.controllers.sankey",
            lib_name="chartjs-chart-sankey"
        )

        # wordcloud2 - renderizacao de nuvem de palavras
        wordcloud_tag = self._build_script_with_fallback(
            inline_code=wordcloud2,
            cdn_url="https://cdnjs.cloudflare.com/ajax/libs/wordcloud2.js/1.2.2/wordcloud2.min.js",
            check_expression="typeof WordCloud !== 'undefined'",
            lib_name="wordcloud2"
        )

        # html2canvas - para captura de tela
        html2canvas_tag = self._build_script_with_fallback(
            inline_code=html2canvas,
            cdn_url="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js",
            check_expression="typeof html2canvas !== 'undefined'",
            lib_name="html2canvas"
        )

        # jsPDF - para exportacao PDF
        jspdf_tag = self._build_script_with_fallback(
            inline_code=jspdf,
            cdn_url="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js",
            check_expression="typeof jspdf !== 'undefined'",
            lib_name="jsPDF"
        )

        # MathJax - renderizacao de formulas matematicas
        mathjax_tag = self._build_script_with_fallback(
            inline_code=mathjax,
            cdn_url="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js",
            check_expression="typeof MathJax !== 'undefined'",
            lib_name="MathJax",
            is_defer=True
        )

        # Dados de fonte PDF nao sao mais embutidos no HTML para reduzir tamanho do arquivo
        pdf_font_script = ""

        return f"""
<head>
  <meta charset="utf-8" />
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{self._escape_html(title)}</title>
  {chartjs_tag}
  {sankey_tag}
  {wordcloud_tag}
  {html2canvas_tag}
  {jspdf_tag}
  <script>
    window.MathJax = {{
      tex: {{
        inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
        displayMath: [['$$','$$'], ['\\\\[','\\\\]']]
      }},
      options: {{
        skipHtmlTags: ['script','noscript','style','textarea','pre','code'],
        processEscapes: true
      }}
    }};
  </script>
  {mathjax_tag}
  {pdf_font_script}
  <style>
{css}
  </style>
  <script>
    document.documentElement.classList.remove('no-js');
    document.documentElement.classList.add('js-ready');
  </script>
</head>""".strip()

    def _render_body(self) -> str:
        """
        Montar estrutura <body>, incluindo cabecalho, navegacao, capitulos e scripts.
        Nova versao: removida secao cover independente, titulo integrado na secao hero.

        Retorna:
            str: Fragmento HTML do body.
        """
        header = self._render_header()
        # cover = self._render_cover()  # cover nao e mais renderizado separadamente
        hero = self._render_hero()
        toc_section = self._render_toc_section()
        chapters = "".join(self._render_chapter(chapter) for chapter in self.chapters)
        widget_scripts = "\n".join(self.widget_scripts)
        hydration = self._hydration_script()
        overlay = """
<div id="export-overlay" class="export-overlay no-print" aria-hidden="true">
  <div class="export-dialog" role="status" aria-live="assertive">
    <div class="export-spinner" aria-hidden="true"></div>
    <p class="export-status">Exportando PDF, por favor aguarde...</p>
    <div class="export-progress" role="progressbar" aria-valuetext="Exportando">
      <div class="export-progress-bar"></div>
    </div>
  </div>
</div>
""".strip()

        return f"""
<body>
{header}
{overlay}
<main>
{hero}
{toc_section}
{chapters}
</main>
{widget_scripts}
{hydration}
</body>""".strip()

    # ====== Cabecalho / Metainformacoes / Sumario ======

    def _render_header(self) -> str:
        """
        Renderizar cabecalho fixo no topo, com titulo, subtitulo e botoes funcionais.

        Descricao de botoes/controles (IDs usados para vincular eventos em _hydration_script):
        - <theme-button id="theme-toggle" value="light" size="1.5">：Web Component personalizado,
          `value` tema inicial(light/dark), `size` controla escala geral; evento `change` passa detail: 'light'/'dark'.
        - <button id="print-btn">：Ao clicar, chama window.print() para exportacao/impressao.
        - <button id="export-btn">：Botao oculto de exportacao PDF, vincula exportPdf() quando visivel.
          Exibido apenas quando dependencias estao prontas ou camada de negocios habilita exportacao.

        Retorna:
            str: HTML do cabecalho.
        """
        metadata = self.metadata
        title = metadata.get("title") or "Relatorio inteligente de analise de opiniao publica"
        subtitle = metadata.get("subtitle") or metadata.get("templateName") or "Gerado automaticamente"
        return f"""
<header class="report-header no-print">
  <div>
    <h1>{self._escape_html(title)}</h1>
    <p class="subtitle">{self._escape_html(subtitle)}</p>
    {self._render_tagline()}
  </div>
  <div class="header-actions">
    <!-- Botao antigo de alternancia dia/noite (estilo Web Component):
    <theme-button value="light" id="theme-toggle" size="1.5"></theme-button>
    -->
    <button id="theme-toggle-btn" class="action-btn theme-toggle-btn" type="button">
      <svg class="btn-icon sun-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="5"></circle>
        <line x1="12" y1="1" x2="12" y2="3"></line>
        <line x1="12" y1="21" x2="12" y2="23"></line>
        <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line>
        <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>
        <line x1="1" y1="12" x2="3" y2="12"></line>
        <line x1="21" y1="12" x2="23" y2="12"></line>
        <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line>
        <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>
      </svg>
      <svg class="btn-icon moon-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: none;">
        <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
      </svg>
      <span class="theme-label">Alternar modo</span>
    </button>
    <button id="print-btn" class="action-btn print-btn" type="button">
      <svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="6 9 6 2 18 2 18 9"></polyline>
        <path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path>
        <rect x="6" y="14" width="12" height="8"></rect>
      </svg>
      <span>Imprimir pagina</span>
    </button>
    <button id="export-btn" class="action-btn" type="button" style="display: none;">⬇️ Exportar PDF</button>
  </div>
</header>
""".strip()

    def _render_tagline(self) -> str:
        """
        Renderizar slogan abaixo do titulo; retorna string vazia se nao houver slogan.

        Retorna:
            str: HTML do tagline ou string vazia.
        """
        tagline = self.metadata.get("tagline")
        if not tagline:
            return ""
        return f'<p class="tagline">{self._escape_html(tagline)}</p>'

    def _render_cover(self) -> str:
        """
        ，“Visao geral do artigo”。

        Retorna:
            str: HTML da secao cover.
        """
        title = self.metadata.get("title") or "Relatorio inteligente de analise de opiniao publica"
        subtitle = self.metadata.get("subtitle") or self.metadata.get("templateName") or ""
        overview_hint = "Visao geral do artigo"
        return f"""
<section class="cover">
  <p class="cover-hint">{overview_hint}</p>
  <h1>{self._escape_html(title)}</h1>
  <p class="cover-subtitle">{self._escape_html(subtitle)}</p>
</section>
""".strip()

    def _render_hero(self) -> str:
        """
        Emitir area de resumo/KPI/destaques com base no campo hero do layout.
        Nova versao: titulo e visao geral combinados, fundo eliptico removido.

        Retorna:
            str: HTML da area hero; string vazia se nao houver dados.
        """
        hero = self.metadata.get("hero") or {}
        if not hero:
            return ""

        # Obter titulo e subtitulo
        title = self.metadata.get("title") or "Relatorio inteligente de analise de opiniao publica"
        subtitle = self.metadata.get("subtitle") or self.metadata.get("templateName") or ""

        summary = hero.get("summary")
        summary_html = f'<p class="hero-summary">{self._escape_html(summary)}</p>' if summary else ""
        highlights = hero.get("highlights") or []
        highlight_html = "".join(
            f'<li><span class="badge">{self._escape_html(text)}</span></li>'
            for text in highlights
        )
        actions = hero.get("actions") or []
        actions_html = "".join(
            f'<button class="ghost-btn" type="button">{self._escape_html(text)}</button>'
            for text in actions
        )
        kpi_cards = ""
        for item in hero.get("kpis", []):
            delta = item.get("delta")
            tone = item.get("tone") or "neutral"
            delta_html = f'<span class="delta {tone}">{self._escape_html(delta)}</span>' if delta else ""
            kpi_cards += f"""
            <div class="hero-kpi">
                <div class="label">{self._escape_html(item.get("label"))}</div>
                <div class="value">{self._escape_html(item.get("value"))}</div>
                {delta_html}
            </div>
            """

        return f"""
<section class="hero-section-combined">
  <div class="hero-header">
    <p class="hero-hint">Visao geral do artigo</p>
    <h1 class="hero-title">{self._escape_html(title)}</h1>
    <p class="hero-subtitle">{self._escape_html(subtitle)}</p>
  </div>
  <div class="hero-body">
    <div class="hero-content">
      {summary_html}
      <ul class="hero-highlights">{highlight_html}</ul>
      <div class="hero-actions">{actions_html}</div>
    </div>
    <div class="hero-side">
      {kpi_cards}
    </div>
  </div>
</section>
""".strip()

    def _render_meta_panel(self) -> str:
        """Requisito atual nao exibe metainformacoes; metodo mantido para extensao futura"""
        return ""

    def _render_toc_section(self) -> str:
        """
        Gerar modulo de Sumario; retorna string vazia se nao houver dados de Sumario.

        Retorna:
            str: Estrutura HTML do sumario.
        """
        if not self.toc_entries:
            return ""
        if self.toc_rendered:
            return ""
        toc_config = self.metadata.get("toc") or {}
        toc_title = toc_config.get("title") or "📚 Sumario"
        toc_items = "".join(
            self._format_toc_entry(entry)
            for entry in self.toc_entries
        )
        self.toc_rendered = True
        return f"""
<nav class="toc">
  <div class="toc-title">{self._escape_html(toc_title)}</div>
  <ul>
    {toc_items}
  </ul>
</nav>
""".strip()

    def _collect_toc_entries(self, chapters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Coletar itens do Sumario com base no tocPlan dos metadados ou headings dos capitulos.

        Parametros:
            chapters: Array de capitulos no Document IR.

        Retorna:
            list[dict]: Entradas de Sumario normalizadas, contendo level/text/anchor/description.
        """
        metadata = self.metadata
        toc_config = metadata.get("toc") or {}
        custom_entries = toc_config.get("customEntries")
        entries: List[Dict[str, Any]] = []

        if custom_entries:
            for entry in custom_entries:
                anchor = entry.get("anchor") or self.chapter_anchor_map.get(entry.get("chapterId"))

                # Verificar se a ancora e valida
                if not anchor:
                    logger.warning(
                        f"Item do Sumario '{entry.get('display') or entry.get('title')}' "
                        f"falta ancora valida, ignorado"
                    )
                    continue

                # Verificar se a ancora esta em chapter_anchor_map ou nos blocks dos capitulos
                anchor_valid = self._validate_toc_anchor(anchor, chapters)
                if not anchor_valid:
                    logger.warning(
                        f"Item do Sumario '{entry.get('display') or entry.get('title')}' "
                        f"a ancora '{anchor}' nao possui capitulo correspondente no documento"
                    )

                # Limpar texto de descricao
                description = entry.get("description")
                if description:
                    description = self._clean_text_from_json_artifacts(description)

                entries.append(
                    {
                        "level": entry.get("level", 2),
                        "text": entry.get("display") or entry.get("title") or "",
                        "anchor": anchor,
                        "description": description,
                    }
                )
            return entries

        for chapter in chapters or []:
            for block in chapter.get("blocks", []):
                if block.get("type") == "heading":
                    anchor = block.get("anchor") or chapter.get("anchor") or ""
                    if not anchor:
                        continue
                    mapped = self.heading_label_map.get(anchor, {})
                    # Limpar texto de descricao
                    description = mapped.get("description")
                    if description:
                        description = self._clean_text_from_json_artifacts(description)
                    entries.append(
                        {
                            "level": block.get("level", 2),
                            "text": mapped.get("display") or block.get("text", ""),
                            "anchor": anchor,
                            "description": description,
                        }
                    )
        return entries

    def _validate_toc_anchor(self, anchor: str, chapters: List[Dict[str, Any]]) -> bool:
        """
        Verificar se a ancora do Sumario possui capitulo ou heading correspondente no documento.

        Parametros:
            anchor: ancora a ser verificada
            chapters: Array de capitulos no Document IR

        Retorna:
            bool: se a ancora e valida
        """
        # Verificar se e ancora de capitulo
        if anchor in self.chapter_anchor_map.values():
            return True

        # Verificar se esta em heading_label_map
        if anchor in self.heading_label_map:
            return True

        # Verificar se esta ancora esta nos blocks dos capitulos
        for chapter in chapters or []:
            chapter_anchor = chapter.get("anchor")
            if chapter_anchor == anchor:
                return True

            for block in chapter.get("blocks", []):
                block_anchor = block.get("anchor")
                if block_anchor == anchor:
                    return True

        return False

    def _prepare_chapters(self, chapters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Copiar capitulos e expandir blocks serializados para evitar renderizacao ausente"""
        prepared: List[Dict[str, Any]] = []
        for chapter in chapters or []:
            chapter_copy = copy.deepcopy(chapter)
            chapter_copy["blocks"] = self._expand_blocks_in_place(chapter_copy.get("blocks", []))
            prepared.append(chapter_copy)
        return prepared

    def _expand_blocks_in_place(self, blocks: List[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
        """Percorrer lista de blocks, decompondo strings JSON embutidas em blocks independentes"""
        expanded: List[Dict[str, Any]] = []
        for block in blocks or []:
            extras = self._extract_embedded_blocks(block)
            expanded.append(block)
            if extras:
                expanded.extend(self._expand_blocks_in_place(extras))
        return expanded

    def _extract_embedded_blocks(self, block: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Buscar dentro do block listas de blocks erroneamente escritas como string e retornar blocks suplementares
        """
        extracted: List[Dict[str, Any]] = []

        def traverse(node: Any) -> None:
            """Percorrer recursivamente arvore de blocks, identificando JSON de blocks aninhados potenciais em campos text"""
            if isinstance(node, dict):
                for key, value in list(node.items()):
                    if key == "text" and isinstance(value, str):
                        decoded = self._decode_embedded_block_payload(value)
                        if decoded:
                            node[key] = ""
                            extracted.extend(decoded)
                        continue
                    traverse(value)
            elif isinstance(node, list):
                for item in node:
                    traverse(item)

        traverse(block)
        return extracted

    def _decode_embedded_block_payload(self, raw: str) -> List[Dict[str, Any]] | None:
        """
        Restaurar descricoes de blocks em formato string para lista estruturada.
        """
        if not isinstance(raw, str):
            return None
        stripped = raw.strip()
        if not stripped or stripped[0] not in "{[":
            return None
        payload: Any | None = None
        decode_targets = [stripped]
        if stripped and stripped[0] != "[":
            decode_targets.append(f"[{stripped}]")
        for candidate in decode_targets:
            try:
                payload = json.loads(candidate)
                break
            except json.JSONDecodeError:
                continue
        if payload is None:
            for candidate in decode_targets:
                try:
                    payload = ast.literal_eval(candidate)
                    break
                except (ValueError, SyntaxError):
                    continue
        if payload is None:
            return None

        blocks = self._collect_blocks_from_payload(payload)
        return blocks or None

    @staticmethod
    def _looks_like_block(payload: Dict[str, Any]) -> bool:
        """Verificacao aproximada se o dict corresponde a estrutura de block"""
        if not isinstance(payload, dict):
            return False
        block_type = payload.get("type")
        if block_type and isinstance(block_type, str):
            # Excluir tipos inline (inlineRun etc.), nao sao elementos de nivel de bloco
            inline_types = {"inlineRun", "inline", "text"}
            if block_type in inline_types:
                return False
            return True
        structural_keys = {"blocks", "rows", "items", "widgetId", "widgetType", "data"}
        return any(key in payload for key in structural_keys)

    def _collect_blocks_from_payload(self, payload: Any) -> List[Dict[str, Any]]:
        """Coletar recursivamente nos de block no payload"""
        collected: List[Dict[str, Any]] = []
        if isinstance(payload, dict):
            block_list = payload.get("blocks")
            block_type = payload.get("type")
            
            # Excluir tipos inline, nao sao elementos de nivel de bloco
            inline_types = {"inlineRun", "inline", "text"}
            if block_type in inline_types:
                return collected
            
            if isinstance(block_list, list) and not block_type:
                for candidate in block_list:
                    collected.extend(self._collect_blocks_from_payload(candidate))
                return collected
            if payload.get("cells") and not block_type:
                for cell in payload["cells"]:
                    if isinstance(cell, dict):
                        collected.extend(self._collect_blocks_from_payload(cell.get("blocks")))
                return collected
            if payload.get("items") and not block_type:
                for item in payload["items"]:
                    collected.extend(self._collect_blocks_from_payload(item))
                return collected
            appended = False
            if block_type or payload.get("widgetId") or payload.get("rows"):
                coerced = self._coerce_block_dict(payload)
                if coerced:
                    collected.append(coerced)
                    appended = True
            items = payload.get("items")
            if isinstance(items, list) and not block_type:
                for item in items:
                    collected.extend(self._collect_blocks_from_payload(item))
                return collected
            if appended:
                return collected
        elif isinstance(payload, list):
            for item in payload:
                collected.extend(self._collect_blocks_from_payload(item))
        elif payload is None:
            return collected
        return collected

    def _coerce_block_dict(self, payload: Any) -> Dict[str, Any] | None:
        """Tentar complementar dict para estrutura de block valida"""
        if not isinstance(payload, dict):
            return None
        block = copy.deepcopy(payload)
        block_type = block.get("type")
        if not block_type:
            if "widgetId" in block:
                block_type = block["type"] = "widget"
            elif "rows" in block or "cells" in block:
                block_type = block["type"] = "table"
                if "rows" not in block and isinstance(block.get("cells"), list):
                    block["rows"] = [{"cells": block.pop("cells")}]
            elif "items" in block:
                block_type = block["type"] = "list"
        return block if block.get("type") else None

    def _format_toc_entry(self, entry: Dict[str, Any]) -> str:
        """
        Converter um item de Sumario em linha HTML com descricao.

        Parametros:
            entry: Entrada de Sumario, deve conter `text` e `anchor`.

        Retorna:
            str: HTML no formato `<li>`.
        """
        desc = entry.get("description")
        # Limpar fragmentos JSON do texto de descricao
        if desc:
            desc = self._clean_text_from_json_artifacts(desc)
        desc_html = f'<p class="toc-desc">{self._escape_html(desc)}</p>' if desc else ""
        level = entry.get("level", 2)
        css_level = 1 if level <= 2 else min(level, 4)
        return f'<li class="level-{css_level}"><a href="#{self._escape_attr(entry["anchor"])}">{self._escape_html(entry["text"])}</a>{desc_html}</li>'

    def _compute_heading_labels(self, chapters: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """
        Pre-calcular numeracao de titulos em cada nivel (capitulo: I, II; secao: 1.1; subsecao: 1.1.1).

        Parametros:
            chapters: Array de capitulos no Document IR.

        Retorna:
            dict: Mapeamento de ancora para numeracao/descricao, facilitando referencia no TOC e texto.
        """
        label_map: Dict[str, Dict[str, Any]] = {}

        for chap_idx, chapter in enumerate(chapters or [], start=1):
            chapter_heading_seen = False
            section_idx = 0
            subsection_idx = 0
            deep_counters: Dict[int, int] = {}

            for block in chapter.get("blocks", []):
                if block.get("type") != "heading":
                    continue
                level = block.get("level", 2)
                anchor = block.get("anchor") or chapter.get("anchor")
                if not anchor:
                    continue

                raw_text = block.get("text", "")
                clean_title = self._strip_order_prefix(raw_text)
                label = None
                display_text = raw_text

                if not chapter_heading_seen:
                    label = f"{self._to_chinese_numeral(chap_idx)}、"
                    display_text = f"{label} {clean_title}".strip()
                    chapter_heading_seen = True
                    section_idx = 0
                    subsection_idx = 0
                    deep_counters.clear()
                elif level <= 2:
                    section_idx += 1
                    subsection_idx = 0
                    deep_counters.clear()
                    label = f"{chap_idx}.{section_idx}"
                    display_text = f"{label} {clean_title}".strip()
                else:
                    if section_idx == 0:
                        section_idx = 1
                    if level == 3:
                        subsection_idx += 1
                        deep_counters.clear()
                        label = f"{chap_idx}.{section_idx}.{subsection_idx}"
                    else:
                        deep_counters[level] = deep_counters.get(level, 0) + 1
                        parts = [str(chap_idx), str(section_idx or 1), str(subsection_idx or 1)]
                        for lvl in sorted(deep_counters.keys()):
                            parts.append(str(deep_counters[lvl]))
                        label = ".".join(parts)
                    display_text = f"{label} {clean_title}".strip()

                label_map[anchor] = {
                    "level": level,
                    "display": display_text,
                    "label": label,
                    "title": clean_title,
                }
        return label_map

    @staticmethod
    def _strip_order_prefix(text: str) -> str:
        """“1.0 ” ou “、” como prefixo，obtendo titulo puro"""
        if not text:
            return ""
        separators = [" ", "、", ".", "．"]
        stripped = text.lstrip()
        for sep in separators:
            parts = stripped.split(sep, 1)
            if len(parts) == 2 and parts[0]:
                return parts[1].strip()
        return stripped.strip()

    @staticmethod
    def _to_chinese_numeral(number: int) -> str:
        """Mapear 1/2/3 para numerais chineses (ate dez)"""
        numerals = ["", "", "", "", "", "", "", "", "", "", ""]
        if number <= 10:
            return numerals[number]
        tens, ones = divmod(number, 10)
        if number < 20:
            return "" + (numerals[ones] if ones else "")
        words = ""
        if tens > 0:
            words += numerals[tens] + ""
        if ones:
            words += numerals[ones]
        return words

    # ====== Renderizacao de capitulos e blocos ======

    def _render_chapter(self, chapter: Dict[str, Any]) -> str:
        """
        Envolver blocks do capitulo em <section> para controle CSS.

        Parametros:
            chapter: JSON de um unico capitulo.

        Retorna:
            str: HTML envolvido em section.
        """
        section_id = self._escape_attr(chapter.get("anchor") or f"chapter-{chapter.get('chapterId', 'x')}")
        prev_chapter = self._current_chapter
        self._current_chapter = chapter
        try:
            blocks_html = self._render_blocks(chapter.get("blocks", []))
        finally:
            self._current_chapter = prev_chapter
        return f'<section id="{section_id}" class="chapter">\n{blocks_html}\n</section>'

    def _render_blocks(self, blocks: List[Dict[str, Any]]) -> str:
        """
        Renderizar todos os blocks do capitulo sequencialmente.

        Parametros:
            blocks: Array de blocks internos do capitulo.

        Retorna:
            str: HTML concatenado.
        """
        return "".join(self._render_block(block) for block in blocks or [])

    def _render_block(self, block: Dict[str, Any]) -> str:
        """
        Despachar para diferentes funcoes de renderizacao com base em block.type.

        Parametros:
            block: Objeto de block unico.

        Retorna:
            str: HTML renderizado; tipos desconhecidos emitem informacoes de depuracao JSON.
        """
        block_type = block.get("type")
        handlers = {
            "heading": self._render_heading,
            "paragraph": self._render_paragraph,
            "list": self._render_list,
            "table": self._render_table,
            "swotTable": self._render_swot_table,
            "pestTable": self._render_pest_table,
            "blockquote": self._render_blockquote,
            "engineQuote": self._render_engine_quote,
            "hr": lambda b: "<hr />",
            "code": self._render_code,
            "math": self._render_math,
            "figure": self._render_figure,
            "callout": self._render_callout,
            "kpiGrid": self._render_kpi_grid,
            "widget": self._render_widget,
            "toc": lambda b: self._render_toc_section(),
        }
        handler = handlers.get(block_type)
        if handler:
            html_fragment = handler(block)
            return self._wrap_error_block(html_fragment, block)
        # Compativel com formato antigo: processar como paragrafo quando falta type mas contem inlines
        if isinstance(block, dict) and block.get("inlines"):
            html_fragment = self._render_paragraph({"inlines": block.get("inlines")})
            return self._wrap_error_block(html_fragment, block)
        # Compativel com cenario de string passada diretamente
        if isinstance(block, str):
            html_fragment = self._render_paragraph({"inlines": [{"text": block}]})
            return self._wrap_error_block(html_fragment, {"meta": {}, "type": "paragraph"})
        if isinstance(block.get("blocks"), list):
            html_fragment = self._render_blocks(block["blocks"])
            return self._wrap_error_block(html_fragment, block)
        fallback = f'<pre class="unknown-block">{self._escape_html(json.dumps(block, ensure_ascii=False, indent=2))}</pre>'
        return self._wrap_error_block(fallback, block)

    def _wrap_error_block(self, html_fragment: str, block: Dict[str, Any]) -> str:
        """Se o block possui metadados de erro, envolver em container de aviso e injetar tooltip."""
        if not html_fragment:
            return html_fragment
        meta = block.get("meta") or {}
        log_ref = meta.get("errorLogRef")
        if not isinstance(log_ref, dict):
            return html_fragment
        raw_preview = (meta.get("rawJsonPreview") or "")[:1200]
        error_message = meta.get("errorMessage") or "Erro de analise do bloco retornado pelo LLM"
        importance = meta.get("importance") or "standard"
        ref_label = ""
        if log_ref.get("relativeFile") and log_ref.get("entryId"):
            ref_label = f"{log_ref['relativeFile']}#{log_ref['entryId']}"
        tooltip = f"{error_message} | {ref_label}".strip()
        attr_raw = self._escape_attr(raw_preview or tooltip)
        attr_title = self._escape_attr(tooltip)
        class_suffix = self._escape_attr(importance)
        return (
            f'<div class="llm-error-block importance-{class_suffix}" '
            f'data-raw="{attr_raw}" title="{attr_title}">{html_fragment}</div>'
        )

    def _render_heading(self, block: Dict[str, Any]) -> str:
        """Renderizar block de heading, garantindo existencia de ancora"""
        original_level = max(1, min(6, block.get("level", 2)))
        if original_level <= 2:
            level = 2
        elif original_level == 3:
            level = 3
        else:
            level = min(original_level, 6)
        anchor = block.get("anchor")
        if anchor:
            anchor_attr = self._escape_attr(anchor)
        else:
            self.heading_counter += 1
            anchor = f"heading-{self.heading_counter}"
            anchor_attr = self._escape_attr(anchor)
        mapping = self.heading_label_map.get(anchor, {})
        display_text = mapping.get("display") or block.get("text", "")
        subtitle = block.get("subtitle")
        subtitle_html = f'<small>{self._escape_html(subtitle)}</small>' if subtitle else ""
        return f'<h{level} id="{anchor_attr}">{self._escape_html(display_text)}{subtitle_html}</h{level}>'

    def _render_paragraph(self, block: Dict[str, Any]) -> str:
        """Renderizar paragrafo, mantendo estilos mistos internamente via inline run"""
        inlines_data = block.get("inlines", [])
        
        # Detectar e pular paragrafos contendo JSON de metadados do documento
        if self._is_metadata_paragraph(inlines_data):
            return ""
        
        # Renderizar diretamente como bloco quando contem apenas uma formula display, evitando <div> dentro de <p>
        if len(inlines_data) == 1:
            standalone = self._render_standalone_math_inline(inlines_data[0])
            if standalone:
                return standalone

        inlines = "".join(self._render_inline(run) for run in inlines_data)
        return f"<p>{inlines}</p>"

    def _is_metadata_paragraph(self, inlines: List[Any]) -> bool:
        """
        Detectar se o paragrafo contem apenas JSON de metadados do documento.
        
        Alguns conteudos gerados por LLM colocam metadados (como xrefs, widgets, footnotes, metadata)
        erroneamente como conteudo de paragrafo; este metodo identifica e marca essa situacao para pular a renderizacao.
        """
        if not inlines or len(inlines) != 1:
            return False
        first = inlines[0]
        if not isinstance(first, dict):
            return False
        text = first.get("text", "")
        if not isinstance(text, str):
            return False
        text = text.strip()
        if not text.startswith("{") or not text.endswith("}"):
            return False
        # Detectar chaves tipicas de metadados
        metadata_indicators = ['"xrefs"', '"widgets"', '"footnotes"', '"metadata"', '"sectionBudgets"']
        return any(indicator in text for indicator in metadata_indicators)

    def _render_standalone_math_inline(self, run: Dict[str, Any] | str) -> str | None:
        """Quando paragrafo contem apenas uma formula display, converter para math-block para evitar quebra de layout inline"""
        if isinstance(run, dict):
            text_value, marks = self._normalize_inline_payload(run)
            if marks:
                return None
            math_id_hint = run.get("mathIds") or run.get("mathId")
        else:
            text_value = "" if run is None else str(run)
            math_id_hint = None
            marks = []

        rendered = self._render_text_with_inline_math(
            text_value,
            math_id_hint,
            allow_display_block=True
        )
        if rendered and rendered.strip().startswith('<div class="math-block"'):
            return rendered
        return None

    def _render_list(self, block: Dict[str, Any]) -> str:
        """Renderizar lista ordenada/nao ordenada/de tarefas"""
        list_type = block.get("listType", "bullet")
        tag = "ol" if list_type == "ordered" else "ul"
        extra_class = "task-list" if list_type == "task" else ""
        items_html = ""
        for item in block.get("items", []):
            content = self._render_blocks(item)
            if not content.strip():
                continue
            items_html += f"<li>{content}</li>"
        class_attr = f' class="{extra_class}"' if extra_class else ""
        return f'<{tag}{class_attr}>{items_html}</{tag}>'

    def _flatten_nested_cells(self, cells: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Aplainar estrutura de celulas erroneamente aninhadas.

        Em alguns dados de tabela gerados por LLM, celulas sao recursivamente aninhadas por engano:
        cells[0] normal, cells[1].cells[0] normal, cells[1].cells[1].cells[0] normal...
        Este metodo aplaina essa estrutura aninhada em array padrao de celulas paralelas.

        Parametros:
            cells: Array de celulas que pode conter estruturas aninhadas.

        Retorna:
            List[Dict]: Array de celulas aplainado.
        """
        if not cells:
            return []

        flattened: List[Dict[str, Any]] = []

        def _extract_cells(cell_or_list: Any) -> None:
            """Extrair recursivamente todas as celulas"""
            if not isinstance(cell_or_list, dict):
                return

            # Se o objeto atual possui blocks, e uma celula valida
            if "blocks" in cell_or_list:
                # Criar copia da celula, removendo cells aninhados
                clean_cell = {
                    k: v for k, v in cell_or_list.items()
                    if k != "cells"
                }
                flattened.append(clean_cell)

            # Se o objeto atual possui cells aninhados, processar recursivamente
            nested_cells = cell_or_list.get("cells")
            if isinstance(nested_cells, list):
                for nested_cell in nested_cells:
                    _extract_cells(nested_cell)

        for cell in cells:
            _extract_cells(cell)

        return flattened

    def _fix_nested_table_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Reparar estrutura de linhas de tabela erroneamente aninhadas.

        Em alguns dados de tabela gerados por LLM, celulas de todas as linhas estao aninhadas na primeira linha,
        resultando em tabela com apenas 1 linha contendo todos os dados. Este metodo detecta e repara essa situacao.

        Parametros:
            rows: Array original de linhas da tabela.

        Retorna:
            List[Dict]: Array de linhas da tabela reparado.
        """
        if not rows:
            return []

        # Funcao auxiliar: obter texto da celula
        def _get_cell_text(cell: Dict[str, Any]) -> str:
            """Obter conteudo de texto da celula"""
            blocks = cell.get("blocks", [])
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "paragraph":
                    inlines = block.get("inlines", [])
                    for inline in inlines:
                        if isinstance(inline, dict):
                            text = inline.get("text", "")
                            if text:
                                return str(text).strip()
            return ""

        def _is_placeholder_cell(cell: Dict[str, Any]) -> bool:
            """Determinar se celula e placeholder (como '--', '-', etc.)"""
            text = _get_cell_text(cell)
            return text in ("--", "-", "—", "——", "", "N/A", "n/a")

        def _is_heading_like_cell(cell: Dict[str, Any]) -> bool:
            """Detectar se e celula de capitulo/titulo erroneamente incluida na tabela"""
            text = _get_cell_text(cell)
            if not text:
                return False
            stripped = text.strip()
            # Formato comum de numero de capitulo, evitando exclusao erronea de valores numericos normais
            heading_patterns = (
                r"^\d{1,2}(?:\.\d{1,2}){1,3}\s+",
                r"^Capitulo [IVXLCDM]+",
            )
            return any(re.match(pat, stripped) for pat in heading_patterns)

        # Fase 1: tratar caso de "linha de cabecalho + dados concatenados em uma linha"
        header_cells = self._flatten_nested_cells((rows[0] or {}).get("cells", []))
        header_count = len(header_cells)
        overflow_fixed = None
        if header_count >= 2:
            rebuilt_rows: List[Dict[str, Any]] = [
                {
                    **{k: v for k, v in (rows[0] or {}).items() if k != "cells"},
                    "cells": header_cells,
                }
            ]
            changed = False
            for row in rows[1:]:
                cells = self._flatten_nested_cells((row or {}).get("cells", []))
                cell_count = len(cells)
                if cell_count <= header_count:
                    rebuilt_rows.append({**{k: v for k, v in (row or {}).items() if k != "cells"}, "cells": cells})
                    continue

                remainder = cell_count % header_count
                trimmed_cells = cells
                if remainder:
                    trailing = cells[-remainder:]
                    if all(_is_placeholder_cell(c) or _is_heading_like_cell(c) for c in trailing):
                        trimmed_cells = cells[:-remainder]
                        remainder = 0

                if remainder == 0 and len(trimmed_cells) >= header_count * 2:
                    for i in range(0, len(trimmed_cells), header_count):
                        chunk = trimmed_cells[i : i + header_count]
                        rebuilt_rows.append({"cells": chunk})
                    changed = True
                else:
                    rebuilt_rows.append({**{k: v for k, v in (row or {}).items() if k != "cells"}, "cells": cells})

            if changed:
                overflow_fixed = rebuilt_rows

        if overflow_fixed is not None:
            rows = overflow_fixed

        if len(rows) != 1:
            # Caso anomalo de apenas uma linha tratado pela logica subsequente; multiplas linhas normais retornam diretamente
            return rows

        first_row = rows[0]
        original_cells = first_row.get("cells", [])

        # Verificar se existe estrutura aninhada
        has_nested = any(
            isinstance(cell.get("cells"), list)
            for cell in original_cells
            if isinstance(cell, dict)
        )

        if not has_nested:
            return rows

        # Aplainar todas as celulas
        all_cells = self._flatten_nested_cells(original_cells)

        if len(all_cells) <= 2:
            # Poucas celulas, nao precisa reorganizar
            return rows

        # Primeiro filtrar celulas placeholder
        all_cells = [c for c in all_cells if not _is_placeholder_cell(c)]

        if len(all_cells) <= 2:
            return rows

        # Detectar numero de colunas do cabecalho: buscar celulas com marcacao bold ou palavras tipicas de cabecalho
        def _is_header_cell(cell: Dict[str, Any]) -> bool:
            """Determinar se celula parece cabecalho (com marcacao bold ou palavras tipicas de cabecalho)"""
            blocks = cell.get("blocks", [])
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "paragraph":
                    inlines = block.get("inlines", [])
                    for inline in inlines:
                        if isinstance(inline, dict):
                            marks = inline.get("marks", [])
                            if any(isinstance(m, dict) and m.get("type") == "bold" for m in marks):
                                return True
            # Tambem verificar palavras tipicas de cabecalho
            text = _get_cell_text(cell)
            header_keywords = {
                "Periodo", "Data", "Nome", "Tipo", "Status", "Quantidade", "Valor", "Proporcao", "Indicador",
                "Plataforma", "Canal", "Fonte", "Descricao", "Observacao", "Nota", "N.", "Codigo",
                "Evento", "Chave", "Dados", "Suporte", "Reacao", "Mercado", "Sentimento", "No",
                "Dimensao", "Pontos-chave", "Detalhes", "Etiqueta", "Impacto", "Tendencia", "Peso", "Categoria",
                "Informacao", "Conteudo", "Estilo", "Preferencia", "Principal", "Usuario", "Central", "Caracteristica",
                "Classificacao", "Escopo", "Objeto", "Projeto", "Fase", "Ciclo", "Frequencia", "Nivel",
            }
            return any(kw in text for kw in header_keywords) and len(text) <= 20

        # Calcular numero de colunas do cabecalho: contar celulas consecutivas de cabecalho
        header_count = 0
        for cell in all_cells:
            if _is_header_cell(cell):
                header_count += 1
            else:
                # Ao encontrar primeira celula nao-cabecalho, area de dados comeca
                break

        # Se cabecalho nao detectado, tentar metodo heuristico
        if header_count == 0:
            # Assumir 4 ou 5 colunas (numeros comuns de colunas de tabela)
            total = len(all_cells)
            for possible_cols in [4, 5, 3, 6, 2]:
                if total % possible_cols == 0:
                    header_count = possible_cols
                    break
            else:
                # Tentar encontrar numero de colunas mais proximo que divide exatamente
                for possible_cols in [4, 5, 3, 6, 2]:
                    remainder = total % possible_cols
                    # Permitir ate 3 celulas extras (possivelmente resumo ou notas no final)
                    if remainder <= 3:
                        header_count = possible_cols
                        break
                else:
                    # Nao foi possivel determinar numero de colunas, retornar dados originais
                    return rows

        # Calcular numero de celulas validas (pode precisar truncar celulas extras no final)
        total = len(all_cells)
        remainder = total % header_count
        if remainder > 0 and remainder <= 3:
            # Truncar celulas extras no final (possivelmente resumo ou notas)
            all_cells = all_cells[:total - remainder]
        elif remainder > 3:
            # Resto muito grande, possivel erro na deteccao de colunas, retornar dados originais
            return rows

        # Reorganizar em multiplas linhas
        fixed_rows: List[Dict[str, Any]] = []
        for i in range(0, len(all_cells), header_count):
            row_cells = all_cells[i:i + header_count]
            # Marcar primeira linha como cabecalho
            if i == 0:
                for cell in row_cells:
                    cell["header"] = True
            fixed_rows.append({"cells": row_cells})

        return fixed_rows

    def _render_table(self, block: Dict[str, Any]) -> str:
        """
        Renderizar tabela, mantendo caption e atributos de celulas.

        Parametros:
            block: Block do tipo table.

        Retorna:
            str: HTML contendo estrutura <table>.
        """
        # Primeiro reparar possiveis problemas de estrutura de linhas aninhadas
        raw_rows = block.get("rows") or []
        fixed_rows = self._fix_nested_table_rows(raw_rows)
        rows = self._normalize_table_rows(fixed_rows)
        rows_html = ""
        for row in rows:
            row_cells = ""
            # Aplainar possiveis estruturas de celulas aninhadas (como protecao extra)
            cells = self._flatten_nested_cells(row.get("cells", []))
            for cell in cells:
                cell_tag = "th" if cell.get("header") or cell.get("isHeader") else "td"
                attr = []
                if cell.get("rowspan"):
                    attr.append(f'rowspan="{int(cell["rowspan"])}"')
                if cell.get("colspan"):
                    attr.append(f'colspan="{int(cell["colspan"])}"')
                if cell.get("align"):
                    attr.append(f'class="align-{cell["align"]}"')
                attr_str = (" " + " ".join(attr)) if attr else ""
                content = self._render_blocks(cell.get("blocks", []))
                row_cells += f"<{cell_tag}{attr_str}>{content}</{cell_tag}>"
            rows_html += f"<tr>{row_cells}</tr>"
        caption = block.get("caption")
        caption_html = f"<caption>{self._escape_html(caption)}</caption>" if caption else ""
        return f'<div class="table-wrap"><table>{caption_html}<tbody>{rows_html}</tbody></table></div>'

    def _render_swot_table(self, block: Dict[str, Any]) -> str:
        """
        Renderizar analise SWOT em quatro quadrantes, gerando dois layouts:
        1. Layout de cartoes (para exibicao HTML) - quatro quadrantes com cantos arredondados
        2. Layout de tabela (para exportacao PDF) - tabela estruturada com suporte a paginacao
        
        Estrategia de paginacao PDF:
        - Usar formato de tabela, cada quadrante S/W/O/T como bloco independente
        - Permitir quebra de pagina entre quadrantes diferentes
        - Manter itens dentro de cada quadrante juntos quando possivel
        """
        title = block.get("title") or "Analise SWOT"
        summary = block.get("summary")
        
        # ========== Layout de cartoes (para HTML) ==========
        card_html = self._render_swot_card_layout(block, title, summary)
        
        # ========== Layout de tabela (para PDF) ==========
        table_html = self._render_swot_pdf_table_layout(block, title, summary)
        
        # Retornar container com ambos os layouts
        return f"""
        <div class="swot-container">
          {card_html}
          {table_html}
        </div>
        """
    
    def _render_swot_card_layout(self, block: Dict[str, Any], title: str, summary: str | None) -> str:
        """Renderizar layout de cartoes SWOT (para exibicao HTML)"""
        quadrants = [
            ("strengths", "Forcas (Strengths)", "S", "strength"),
            ("weaknesses", "Fraquezas (Weaknesses)", "W", "weakness"),
            ("opportunities", "Oportunidades (Opportunities)", "O", "opportunity"),
            ("threats", "Ameacas (Threats)", "T", "threat"),
        ]
        cells_html = ""
        for idx, (key, label, code, css) in enumerate(quadrants):
            items = self._normalize_swot_items(block.get(key))
            caption_text = f"{len(items)}  pontos-chave" if items else "A complementar"
            list_html = "".join(self._render_swot_item(item) for item in items) if items else '<li class="swot-empty">Pontos-chave ainda nao preenchidos</li>'
            first_cell_class = " swot-cell--first" if idx == 0 else ""
            cells_html += f"""
        <div class="swot-cell swot-cell--pageable {css}{first_cell_class}" data-swot-key="{key}">
          <div class="swot-cell__meta">
            <span class="swot-pill {css}">{self._escape_html(code)}</span>
            <div>
              <div class="swot-cell__title">{self._escape_html(label)}</div>
              <div class="swot-cell__caption">{self._escape_html(caption_text)}</div>
            </div>
          </div>
          <ul class="swot-list">{list_html}</ul>
        </div>"""
        summary_html = f'<p class="swot-card__summary">{self._escape_html(summary)}</p>' if summary else ""
        title_html = f'<div class="swot-card__title">{self._escape_html(title)}</div>' if title else ""
        legend = """
            <div class="swot-legend">
              <span class="swot-legend__item strength">S Forcas</span>
              <span class="swot-legend__item weakness">W Fraquezas</span>
              <span class="swot-legend__item opportunity">O Oportunidades</span>
              <span class="swot-legend__item threat">T Ameacas</span>
            </div>
        """
        return f"""
        <div class="swot-card swot-card--html">
          <div class="swot-card__head">
            <div>{title_html}{summary_html}</div>
            {legend}
          </div>
          <div class="swot-grid">{cells_html}</div>
        </div>
        """
    
    def _render_swot_pdf_table_layout(self, block: Dict[str, Any], title: str, summary: str | None) -> str:
        """
        Renderizar layout de tabela SWOT (para exportacao PDF)
        
        Descricao do design:
        - Uma tabela grande contendo linha de titulo e 4 areas de quadrante
        - Cada area de quadrante possui suas proprias linhas de subtitulo e conteudo
        - Usar celulas mescladas para exibir titulos de quadrante
        - Controlar comportamento de paginacao via CSS
        """
        quadrants = [
            ("strengths", "S", "Forcas (Strengths)", "swot-pdf-strength", "#1c7f6e"),
            ("weaknesses", "W", "Fraquezas (Weaknesses)", "swot-pdf-weakness", "#c0392b"),
            ("opportunities", "O", "Oportunidades (Opportunities)", "swot-pdf-opportunity", "#1f5ab3"),
            ("threats", "T", "Ameacas (Threats)", "swot-pdf-threat", "#b36b16"),
        ]
        
        # Titulo e resumo
        summary_row = ""
        if summary:
            summary_row = f"""
            <tr class="swot-pdf-summary-row">
              <td colspan="4" class="swot-pdf-summary">{self._escape_html(summary)}</td>
            </tr>"""
        
        # Gerar conteudo de tabela dos quatro quadrantes
        quadrant_tables = ""
        for idx, (key, code, label, css_class, color) in enumerate(quadrants):
            items = self._normalize_swot_items(block.get(key))
            
            # Gerar linhas de conteudo de cada quadrante
            items_rows = ""
            if items:
                for item_idx, item in enumerate(items):
                    item_title = item.get("title") or item.get("label") or item.get("text") or "Ponto nao nomeado"
                    item_detail = item.get("detail") or item.get("description") or ""
                    item_evidence = item.get("evidence") or item.get("source") or ""
                    item_impact = item.get("impact") or item.get("priority") or ""
                    # item_score = item.get("score")  # funcao de pontuacao desabilitada
                    
                    # Construir conteudo detalhado
                    detail_parts = []
                    if item_detail:
                        detail_parts.append(item_detail)
                    if item_evidence:
                        detail_parts.append(f"Evidencia: {item_evidence}")
                    detail_text = "<br/>".join(detail_parts) if detail_parts else "-"
                    
                    # Construir etiquetas
                    tags = []
                    if item_impact:
                        tags.append(f'<span class="swot-pdf-tag">{self._escape_html(item_impact)}</span>')
                    # if item_score not in (None, ""):  # funcao de pontuacao desabilitada
                    #     tags.append(f'<span class="swot-pdf-tag swot-pdf-tag--score">Pontuacao {self._escape_html(item_score)}</span>')
                    tags_html = " ".join(tags)
                    
                    # Primeira linha precisa mesclar celula do titulo do quadrante
                    if item_idx == 0:
                        rowspan = len(items)
                        items_rows += f"""
            <tr class="swot-pdf-item-row {css_class}">
              <td rowspan="{rowspan}" class="swot-pdf-quadrant-label {css_class}">
                <span class="swot-pdf-code">{code}</span>
                <span class="swot-pdf-label-text">{self._escape_html(label.split()[0])}</span>
              </td>
              <td class="swot-pdf-item-num">{item_idx + 1}</td>
              <td class="swot-pdf-item-title">{self._escape_html(item_title)}</td>
              <td class="swot-pdf-item-detail">{detail_text}</td>
              <td class="swot-pdf-item-tags">{tags_html}</td>
            </tr>"""
                    else:
                        items_rows += f"""
            <tr class="swot-pdf-item-row {css_class}">
              <td class="swot-pdf-item-num">{item_idx + 1}</td>
              <td class="swot-pdf-item-title">{self._escape_html(item_title)}</td>
              <td class="swot-pdf-item-detail">{detail_text}</td>
              <td class="swot-pdf-item-tags">{tags_html}</td>
            </tr>"""
            else:
                # Exibir placeholder quando sem conteudo
                items_rows = f"""
            <tr class="swot-pdf-item-row {css_class}">
              <td class="swot-pdf-quadrant-label {css_class}">
                <span class="swot-pdf-code">{code}</span>
                <span class="swot-pdf-label-text">{self._escape_html(label.split()[0])}</span>
              </td>
              <td class="swot-pdf-item-num">-</td>
              <td colspan="3" class="swot-pdf-empty">Sem pontos-chave no momento</td>
            </tr>"""
            
            # Cada quadrante como tbody independente para controle de paginacao
            quadrant_tables += f"""
          <tbody class="swot-pdf-quadrant {css_class}">
            {items_rows}
          </tbody>"""
        
        return f"""
        <div class="swot-pdf-wrapper">
          <table class="swot-pdf-table">
            <caption class="swot-pdf-caption">{self._escape_html(title)}</caption>
            <thead class="swot-pdf-thead">
              <tr>
                <th class="swot-pdf-th-quadrant">Quadrante</th>
                <th class="swot-pdf-th-num">N.</th>
                <th class="swot-pdf-th-title">Pontos-chave</th>
                <th class="swot-pdf-th-detail">Descricao detalhada</th>
                <th class="swot-pdf-th-tags">Impacto</th>
              </tr>
              {summary_row}
            </thead>
            {quadrant_tables}
          </table>
        </div>
        """

    def _normalize_swot_items(self, raw: Any) -> List[Dict[str, Any]]:
        """Normalizar itens SWOT para estrutura unificada, compativel com formatos string/objeto"""
        normalized: List[Dict[str, Any]] = []
        if raw is None:
            return normalized
        if isinstance(raw, (str, int, float)):
            text = self._safe_text(raw).strip()
            if text:
                normalized.append({"title": text})
            return normalized
        if not isinstance(raw, list):
            return normalized
        for entry in raw:
            if isinstance(entry, (str, int, float)):
                text = self._safe_text(entry).strip()
                if text:
                    normalized.append({"title": text})
                continue
            if not isinstance(entry, dict):
                continue
            title = entry.get("title") or entry.get("label") or entry.get("text")
            detail = entry.get("detail") or entry.get("description")
            evidence = entry.get("evidence") or entry.get("source")
            impact = entry.get("impact") or entry.get("priority")
            # score = entry.get("score")  # funcao de pontuacao desabilitada
            if not title and isinstance(detail, str):
                title = detail
                detail = None
            if not (title or detail or evidence):
                continue
            normalized.append(
                {
                    "title": title,
                    "detail": detail,
                    "evidence": evidence,
                    "impact": impact,
                    # "score": score,  # funcao de pontuacao desabilitada
                }
            )
        return normalized

    def _render_swot_item(self, item: Dict[str, Any]) -> str:
        """Emitir fragmento HTML de um item SWOT individual"""
        title = item.get("title") or item.get("label") or item.get("text") or "Ponto nao nomeado"
        detail = item.get("detail") or item.get("description")
        evidence = item.get("evidence") or item.get("source")
        impact = item.get("impact") or item.get("priority")
        # score = item.get("score")  # funcao de pontuacao desabilitada
        tags: List[str] = []
        if impact:
            tags.append(f'<span class="swot-tag">{self._escape_html(impact)}</span>')
        # if score not in (None, ""):  # funcao de pontuacao desabilitada
        #     tags.append(f'<span class="swot-tag neutral">Pontuacao {self._escape_html(score)}</span>')
        tags_html = f'<span class="swot-item-tags">{"".join(tags)}</span>' if tags else ""
        detail_html = f'<div class="swot-item-desc">{self._escape_html(detail)}</div>' if detail else ""
        evidence_html = f'<div class="swot-item-evidence">Evidencia: {self._escape_html(evidence)}</div>' if evidence else ""
        return f"""
            <li class="swot-item">
              <div class="swot-item-title">{self._escape_html(title)}{tags_html}</div>
              {detail_html}{evidence_html}
            </li>
        """

    # ==================== Bloco de analise PEST ====================
    
    def _render_pest_table(self, block: Dict[str, Any]) -> str:
        """
        Renderizar analise PEST em quatro dimensoes, gerando dois layouts:
        1. Layout de cartoes (para exibicao HTML) - faixas horizontais empilhadas
        2. Layout de tabela (para exportacao PDF) - tabela estruturada com suporte a paginacao
        
        Dimensoes da analise PEST:
        - P: Political (Fatores politicos)
        - E: Economic (Fatores economicos)
        - S: Social (Fatores sociais)
        - T: Technological (Fatores tecnologicos)
        """
        title = block.get("title") or "Analise PEST"
        summary = block.get("summary")
        
        # ========== Layout de cartoes (para HTML) ==========
        card_html = self._render_pest_card_layout(block, title, summary)
        
        # ========== Layout de tabela (para PDF) ==========
        table_html = self._render_pest_pdf_table_layout(block, title, summary)
        
        # Retornar container com ambos os layouts
        return f"""
        <div class="pest-container">
          {card_html}
          {table_html}
        </div>
        """
    
    def _render_pest_card_layout(self, block: Dict[str, Any], title: str, summary: str | None) -> str:
        """Renderizar layout de cartoes PEST (para exibicao HTML) - design de faixas horizontais empilhadas"""
        dimensions = [
            ("political", "Fatores Politicos (Political)", "P", "political"),
            ("economic", "Fatores Economicos (Economic)", "E", "economic"),
            ("social", "Fatores Sociais (Social)", "S", "social"),
            ("technological", "Fatores Tecnologicos (Technological)", "T", "technological"),
        ]
        strips_html = ""
        for idx, (key, label, code, css) in enumerate(dimensions):
            items = self._normalize_pest_items(block.get(key))
            caption_text = f"{len(items)}  pontos-chave" if items else "A complementar"
            list_html = "".join(self._render_pest_item(item) for item in items) if items else '<li class="pest-empty">Pontos-chave ainda nao preenchidos</li>'
            first_strip_class = " pest-strip--first" if idx == 0 else ""
            strips_html += f"""
        <div class="pest-strip pest-strip--pageable {css}{first_strip_class}" data-pest-key="{key}">
          <div class="pest-strip__indicator {css}">
            <span class="pest-code">{self._escape_html(code)}</span>
          </div>
          <div class="pest-strip__content">
            <div class="pest-strip__header">
              <div class="pest-strip__title">{self._escape_html(label)}</div>
              <div class="pest-strip__caption">{self._escape_html(caption_text)}</div>
            </div>
            <ul class="pest-list">{list_html}</ul>
          </div>
        </div>"""
        summary_html = f'<p class="pest-card__summary">{self._escape_html(summary)}</p>' if summary else ""
        title_html = f'<div class="pest-card__title">{self._escape_html(title)}</div>' if title else ""
        legend = """
            <div class="pest-legend">
              <span class="pest-legend__item political">P Politico</span>
              <span class="pest-legend__item economic">E Economico</span>
              <span class="pest-legend__item social">S Social</span>
              <span class="pest-legend__item technological">T Tecnologico</span>
            </div>
        """
        return f"""
        <div class="pest-card pest-card--html">
          <div class="pest-card__head">
            <div>{title_html}{summary_html}</div>
            {legend}
          </div>
          <div class="pest-strips">{strips_html}</div>
        </div>
        """
    
    def _render_pest_pdf_table_layout(self, block: Dict[str, Any], title: str, summary: str | None) -> str:
        """
        Renderizar layout de tabela PEST (para exportacao PDF)
        
        Descricao do design:
        - Uma tabela grande contendo linha de titulo e 4 areas de dimensao
        - Cada dimensao possui suas proprias linhas de subtitulo e conteudo
        - Usar celulas mescladas para exibir titulos de dimensao
        - Controlar comportamento de paginacao via CSS
        """
        dimensions = [
            ("political", "P", "Fatores Politicos (Political)", "pest-pdf-political", "#8e44ad"),
            ("economic", "E", "Fatores Economicos (Economic)", "pest-pdf-economic", "#16a085"),
            ("social", "S", "Fatores Sociais (Social)", "pest-pdf-social", "#e84393"),
            ("technological", "T", "Fatores Tecnologicos (Technological)", "pest-pdf-technological", "#2980b9"),
        ]
        
        # Titulo e resumo
        summary_row = ""
        if summary:
            summary_row = f"""
            <tr class="pest-pdf-summary-row">
              <td colspan="4" class="pest-pdf-summary">{self._escape_html(summary)}</td>
            </tr>"""
        
        # Gerar conteudo de tabela das quatro dimensoes
        dimension_tables = ""
        for idx, (key, code, label, css_class, color) in enumerate(dimensions):
            items = self._normalize_pest_items(block.get(key))
            
            # Gerar linhas de conteudo de cada dimensao
            items_rows = ""
            if items:
                for item_idx, item in enumerate(items):
                    item_title = item.get("title") or item.get("label") or item.get("text") or "Ponto nao nomeado"
                    item_detail = item.get("detail") or item.get("description") or ""
                    item_source = item.get("source") or item.get("evidence") or ""
                    item_trend = item.get("trend") or item.get("impact") or ""
                    
                    # Construir conteudo detalhado
                    detail_parts = []
                    if item_detail:
                        detail_parts.append(item_detail)
                    if item_source:
                        detail_parts.append(f"Fonte: {item_source}")
                    detail_text = "<br/>".join(detail_parts) if detail_parts else "-"
                    
                    # Construir etiquetas
                    tags = []
                    if item_trend:
                        tags.append(f'<span class="pest-pdf-tag">{self._escape_html(item_trend)}</span>')
                    tags_html = " ".join(tags)
                    
                    # Primeira linha precisa mesclar celula do titulo da dimensao
                    if item_idx == 0:
                        rowspan = len(items)
                        items_rows += f"""
            <tr class="pest-pdf-item-row {css_class}">
              <td rowspan="{rowspan}" class="pest-pdf-dimension-label {css_class}">
                <span class="pest-pdf-code">{code}</span>
                <span class="pest-pdf-label-text">{self._escape_html(label.split()[0])}</span>
              </td>
              <td class="pest-pdf-item-num">{item_idx + 1}</td>
              <td class="pest-pdf-item-title">{self._escape_html(item_title)}</td>
              <td class="pest-pdf-item-detail">{detail_text}</td>
              <td class="pest-pdf-item-tags">{tags_html}</td>
            </tr>"""
                    else:
                        items_rows += f"""
            <tr class="pest-pdf-item-row {css_class}">
              <td class="pest-pdf-item-num">{item_idx + 1}</td>
              <td class="pest-pdf-item-title">{self._escape_html(item_title)}</td>
              <td class="pest-pdf-item-detail">{detail_text}</td>
              <td class="pest-pdf-item-tags">{tags_html}</td>
            </tr>"""
            else:
                # Exibir placeholder quando sem conteudo
                items_rows = f"""
            <tr class="pest-pdf-item-row {css_class}">
              <td class="pest-pdf-dimension-label {css_class}">
                <span class="pest-pdf-code">{code}</span>
                <span class="pest-pdf-label-text">{self._escape_html(label.split()[0])}</span>
              </td>
              <td class="pest-pdf-item-num">-</td>
              <td colspan="3" class="pest-pdf-empty">Sem pontos-chave no momento</td>
            </tr>"""
            
            # Cada dimensao como tbody independente para controle de paginacao
            dimension_tables += f"""
          <tbody class="pest-pdf-dimension {css_class}">
            {items_rows}
          </tbody>"""
        
        return f"""
        <div class="pest-pdf-wrapper">
          <table class="pest-pdf-table">
            <caption class="pest-pdf-caption">{self._escape_html(title)}</caption>
            <thead class="pest-pdf-thead">
              <tr>
                <th class="pest-pdf-th-dimension">Dimensao</th>
                <th class="pest-pdf-th-num">N.</th>
                <th class="pest-pdf-th-title">Pontos-chave</th>
                <th class="pest-pdf-th-detail">Descricao detalhada</th>
                <th class="pest-pdf-th-tags">Tendencia/Impacto</th>
              </tr>
              {summary_row}
            </thead>
            {dimension_tables}
          </table>
        </div>
        """

    def _normalize_pest_items(self, raw: Any) -> List[Dict[str, Any]]:
        """Normalizar itens PEST para estrutura unificada, compativel com formatos string/objeto"""
        normalized: List[Dict[str, Any]] = []
        if raw is None:
            return normalized
        if isinstance(raw, (str, int, float)):
            text = self._safe_text(raw).strip()
            if text:
                normalized.append({"title": text})
            return normalized
        if not isinstance(raw, list):
            return normalized
        for entry in raw:
            if isinstance(entry, (str, int, float)):
                text = self._safe_text(entry).strip()
                if text:
                    normalized.append({"title": text})
                continue
            if not isinstance(entry, dict):
                continue
            title = entry.get("title") or entry.get("label") or entry.get("text")
            detail = entry.get("detail") or entry.get("description")
            source = entry.get("source") or entry.get("evidence")
            trend = entry.get("trend") or entry.get("impact")
            if not title and isinstance(detail, str):
                title = detail
                detail = None
            if not (title or detail or source):
                continue
            normalized.append(
                {
                    "title": title,
                    "detail": detail,
                    "source": source,
                    "trend": trend,
                }
            )
        return normalized

    def _render_pest_item(self, item: Dict[str, Any]) -> str:
        """Emitir fragmento HTML de um item PEST individual"""
        title = item.get("title") or item.get("label") or item.get("text") or "Ponto nao nomeado"
        detail = item.get("detail") or item.get("description")
        source = item.get("source") or item.get("evidence")
        trend = item.get("trend") or item.get("impact")
        tags: List[str] = []
        if trend:
            tags.append(f'<span class="pest-tag">{self._escape_html(trend)}</span>')
        tags_html = f'<span class="pest-item-tags">{"".join(tags)}</span>' if tags else ""
        detail_html = f'<div class="pest-item-desc">{self._escape_html(detail)}</div>' if detail else ""
        source_html = f'<div class="pest-item-source">Fonte: {self._escape_html(source)}</div>' if source else ""
        return f"""
            <li class="pest-item">
              <div class="pest-item-title">{self._escape_html(title)}{tags_html}</div>
              {detail_html}{source_html}
            </li>
        """

    def _normalize_table_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Detectar e corrigir tabelas verticais de coluna unica, convertendo para grade padrao.

        Parametros:
            rows: Linhas originais da tabela.

        Retorna:
            list[dict]: Se tabela vertical detectada, retorna linhas transpostas; caso contrario, retorna como esta.
        """
        if not rows:
            return []
        if not all(len((row.get("cells") or [])) == 1 for row in rows):
            return rows
        texts = [self._extract_row_text(row) for row in rows]
        header_span = self._detect_transposed_header_span(rows, texts)
        if not header_span:
            return rows
        normalized = self._transpose_single_cell_table(rows, header_span)
        return normalized or rows

    def _detect_transposed_header_span(self, rows: List[Dict[str, Any]], texts: List[str]) -> int:
        """Inferir numero de linhas do cabecalho vertical para transposicao posterior"""
        max_fields = min(8, len(rows) // 2)
        header_span = 0
        for idx, text in enumerate(texts):
            if idx >= max_fields:
                break
            if self._is_potential_table_header(text):
                header_span += 1
            else:
                break
        if header_span < 2:
            return 0
        remainder = texts[header_span:]
        if not remainder or (len(rows) - header_span) % header_span != 0:
            return 0
        if not any(self._looks_like_table_value(txt) for txt in remainder):
            return 0
        return header_span

    def _is_potential_table_header(self, text: str) -> bool:
        """Determinar se parece campo de cabecalho com base em comprimento e caracteristicas de caracteres"""
        if not text:
            return False
        stripped = text.strip()
        if not stripped or len(stripped) > 12:
            return False
        return not any(ch.isdigit() or ch in self.TABLE_COMPLEX_CHARS for ch in stripped)

    def _looks_like_table_value(self, text: str) -> bool:
        """Determinar se o texto se parece mais com valor de dados para auxiliar na decisao de transposicao"""
        if not text:
            return False
        stripped = text.strip()
        if len(stripped) >= 12:
            return True
        return any(ch.isdigit() or ch in self.TABLE_COMPLEX_CHARS for ch in stripped)

    def _transpose_single_cell_table(self, rows: List[Dict[str, Any]], span: int) -> List[Dict[str, Any]]:
        """Converter tabela de coluna unica com multiplas linhas em cabecalho padrao + linhas de dados"""
        total = len(rows)
        if total <= span or (total - span) % span != 0:
            return []
        header_rows = rows[:span]
        data_rows = rows[span:]
        normalized: List[Dict[str, Any]] = []
        header_cells = []
        for row in header_rows:
            cell = copy.deepcopy((row.get("cells") or [{}])[0])
            cell["header"] = True
            header_cells.append(cell)
        normalized.append({"cells": header_cells})
        for start in range(0, len(data_rows), span):
            group = data_rows[start : start + span]
            if len(group) < span:
                break
            normalized.append(
                {
                    "cells": [
                        copy.deepcopy((item.get("cells") or [{}])[0])
                        for item in group
                    ]
                }
            )
        return normalized

    def _extract_row_text(self, row: Dict[str, Any]) -> str:
        """Extrair texto puro da linha da tabela para analise heuristica"""
        cells = row.get("cells") or []
        if not cells:
            return ""
        cell = cells[0]
        texts: List[str] = []
        for block in cell.get("blocks", []):
            if isinstance(block, dict):
                if block.get("type") == "paragraph":
                    for inline in block.get("inlines") or []:
                        if isinstance(inline, dict):
                            value = inline.get("text")
                        else:
                            value = inline
                        if value is None:
                            continue
                        texts.append(str(value))
        return "".join(texts)

    def _render_blockquote(self, block: Dict[str, Any]) -> str:
        """Renderizar bloco de citacao, podendo aninhar outros blocks"""
        inner = self._render_blocks(block.get("blocks", []))
        return f"<blockquote>{inner}</blockquote>"

    def _render_engine_quote(self, block: Dict[str, Any]) -> str:
        """Renderizar bloco de fala de Engine individual, com cores e titulo proprios"""
        engine_raw = (block.get("engine") or "").lower()
        engine = engine_raw if engine_raw in ENGINE_AGENT_TITLES else "insight"
        expected_title = ENGINE_AGENT_TITLES.get(engine, ENGINE_AGENT_TITLES["insight"])
        title_raw = block.get("title") if isinstance(block.get("title"), str) else ""
        title = title_raw if title_raw == expected_title else expected_title
        inner = self._render_blocks(block.get("blocks", []))
        return (
            f'<div class="engine-quote engine-{self._escape_attr(engine)}">'
            f'  <div class="engine-quote__header">'
            f'    <span class="engine-quote__dot"></span>'
            f'    <span class="engine-quote__title">{self._escape_html(title)}</span>'
            f'  </div>'
            f'  <div class="engine-quote__body">{inner}</div>'
            f'</div>'
        )

    def _render_code(self, block: Dict[str, Any]) -> str:
        """Renderizar bloco de codigo com informacao de linguagem"""
        lang = block.get("lang") or ""
        content = self._escape_html(block.get("content", ""))
        return f'<pre class="code-block" data-lang="{self._escape_attr(lang)}"><code>{content}</code></pre>'

    def _render_math(self, block: Dict[str, Any]) -> str:
        """Renderizar formula matematica; placeholder delegado ao MathJax externo ou pos-processamento"""
        latex_raw = block.get("latex", "")
        latex = self._escape_html(self._normalize_latex_string(latex_raw))
        math_id = self._escape_attr(block.get("mathId", "")) if block.get("mathId") else ""
        id_attr = f' data-math-id="{math_id}"' if math_id else ""
        return f'<div class="math-block"{id_attr}>$$ {latex} $$</div>'

    def _render_figure(self, block: Dict[str, Any]) -> str:
        """Por nova especificacao, nao renderiza imagens externas por padrao; exibe aviso amigavel"""
        caption = block.get("caption") or "Conteudo de imagem omitido (apenas graficos e tabelas nativos HTML permitidos)"
        return f'<div class="figure-placeholder">{self._escape_html(caption)}</div>'

    def _render_callout(self, block: Dict[str, Any]) -> str:
        """
        Renderizar caixa de destaque; tone determina a cor.

        Parametros:
            block: Block do tipo callout.

        Retorna:
            str: HTML do callout; blocos internos nao permitidos serao separados.
        """
        tone = block.get("tone", "info")
        title = block.get("title")
        safe_blocks, trailing_blocks = self._split_callout_content(block.get("blocks"))
        inner = self._render_blocks(safe_blocks)
        title_html = f"<strong>{self._escape_html(title)}</strong>" if title else ""
        callout_html = f'<div class="callout tone-{tone}">{title_html}{inner}</div>'
        trailing_html = self._render_blocks(trailing_blocks) if trailing_blocks else ""
        return callout_html + trailing_html

    def _split_callout_content(
        self, blocks: List[Dict[str, Any]] | None
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Limitar callout a conter apenas conteudo leve; demais blocos separados para camada externa"""
        if not blocks:
            return [], []
        safe: List[Dict[str, Any]] = []
        trailing: List[Dict[str, Any]] = []
        for idx, child in enumerate(blocks):
            child_type = child.get("type")
            if child_type == "list":
                sanitized, overflow = self._sanitize_callout_list(child)
                if sanitized:
                    safe.append(sanitized)
                if overflow:
                    trailing.extend(overflow)
                    trailing.extend(copy.deepcopy(blocks[idx + 1 :]))
                    break
            elif child_type in self.CALLOUT_ALLOWED_TYPES:
                safe.append(child)
            else:
                trailing.extend(copy.deepcopy(blocks[idx:]))
                break
        else:
            return safe, []
        return safe, trailing

    def _sanitize_callout_list(
        self, block: Dict[str, Any]
    ) -> tuple[Dict[str, Any] | None, List[Dict[str, Any]]]:
        """Quando itens de lista contem blocks estruturais, truncar e mover para fora do callout"""
        items = block.get("items") or []
        if not items:
            return block, []
        sanitized_items: List[List[Dict[str, Any]]] = []
        trailing: List[Dict[str, Any]] = []
        for idx, item in enumerate(items):
            safe, overflow = self._split_callout_content(item)
            if safe:
                sanitized_items.append(safe)
            if overflow:
                trailing.extend(overflow)
                for rest in items[idx + 1 :]:
                    trailing.extend(copy.deepcopy(rest))
                break
        if not sanitized_items:
            return None, trailing
        new_block = copy.deepcopy(block)
        new_block["items"] = sanitized_items
        return new_block, trailing

    def _render_kpi_grid(self, block: Dict[str, Any]) -> str:
        """Renderizar grade de cartoes KPI com valores de indicadores e variacoes"""
        if self._should_skip_overview_kpi(block):
            return ""
        cards = ""
        items = block.get("items", [])
        for item in items:
            delta = item.get("delta")
            delta_tone = item.get("deltaTone") or "neutral"
            delta_html = f'<span class="delta {delta_tone}">{self._escape_html(delta)}</span>' if delta else ""
            cards += f"""
            <div class="kpi-card">
              <div class="kpi-value">{self._escape_html(item.get("value", ""))}<small>{self._escape_html(item.get("unit", ""))}</small></div>
              <div class="kpi-label">{self._escape_html(item.get("label", ""))}</div>
              {delta_html}
            </div>
            """
        count_attr = f' data-kpi-count="{len(items)}"' if items else ""
        return f'<div class="kpi-grid"{count_attr}>{cards}</div>'

    def _merge_dicts(
        self, base: Dict[str, Any] | None, override: Dict[str, Any] | None
    ) -> Dict[str, Any]:
        """
        Mesclar dois dicionarios recursivamente, override sobrescreve base, ambos como novas copias para evitar efeitos colaterais.
        """
        result = copy.deepcopy(base) if isinstance(base, dict) else {}
        if not isinstance(override, dict):
            return result
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = self._merge_dicts(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    def _looks_like_chart_dataset(self, candidate: Any) -> bool:
        """Verificacao heuristica se objeto contem estrutura labels/datasets comum do Chart.js"""
        if not isinstance(candidate, dict):
            return False
        labels = candidate.get("labels")
        datasets = candidate.get("datasets")
        return isinstance(labels, list) or isinstance(datasets, list)

    def _coerce_chart_data_structure(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Compativel com configuracao completa Chart.js da saida LLM (contendo type/data/options).
        Se data contem estrutura labels/datasets real aninhada, extrai e retorna essa estrutura.
        """
        if not isinstance(data, dict):
            return {}
        if self._looks_like_chart_dataset(data):
            return data
        for key in ("data", "chartData", "payload"):
            nested = data.get(key)
            if self._looks_like_chart_dataset(nested):
                return copy.deepcopy(nested)
        return data

    def _prepare_widget_payload(
        self, block: Dict[str, Any]
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Pre-processar dados do widget, compativel com blocks que escrevem configuracao Chart.js no campo data.

        Retorna:
            tuple(props, data): props normalizados e dados do grafico
        """
        props = copy.deepcopy(block.get("props") or {})
        raw_data = block.get("data")
        data_copy = copy.deepcopy(raw_data) if isinstance(raw_data, dict) else raw_data
        widget_type = block.get("widgetType") or ""
        chart_like = isinstance(widget_type, str) and widget_type.startswith("chart.js")

        if chart_like and isinstance(data_copy, dict):
            inline_options = data_copy.pop("options", None)
            inline_type = data_copy.pop("type", None)
            normalized_data = self._coerce_chart_data_structure(data_copy)
            if isinstance(inline_options, dict):
                props["options"] = self._merge_dicts(props.get("options"), inline_options)
            if isinstance(inline_type, str) and inline_type and not props.get("type"):
                props["type"] = inline_type
        elif isinstance(data_copy, dict):
            normalized_data = data_copy
        else:
            normalized_data = {}

        return props, normalized_data

    @staticmethod
    def _is_chart_data_empty(data: Dict[str, Any] | None) -> bool:
        """Verificar se dados do grafico estao vazios ou faltam datasets validos"""
        if not isinstance(data, dict):
            return True

        datasets = data.get("datasets")
        if not isinstance(datasets, list) or len(datasets) == 0:
            return True

        for ds in datasets:
            if not isinstance(ds, dict):
                continue
            series = ds.get("data")
            if isinstance(series, list) and len(series) > 0:
                return False

        return True

    def _chart_cache_key(self, block: Dict[str, Any]) -> str:
        """Usar algoritmo de cache do reparador para gerar chave estavel, facilitando compartilhamento de resultados entre fases"""
        if hasattr(self, "chart_repairer") and block:
            try:
                return self.chart_repairer.build_cache_key(block)
            except Exception:
                pass
        return str(id(block))

    def _note_chart_failure(self, cache_key: str, reason: str) -> None:
        """Registrar motivo da falha no reparo; renderizacoes posteriores usam placeholder diretamente"""
        if not cache_key:
            return
        if not reason:
            reason = "Formato das informacoes do grafico retornado pelo LLM incorreto, impossivel exibir corretamente"
        self._chart_failure_notes[cache_key] = reason

    def _record_chart_failure_stat(self, cache_key: str | None = None) -> None:
        """Garantir que contagem de falhas seja registrada apenas uma vez"""
        if cache_key and cache_key in self._chart_failure_recorded:
            return
        self.chart_validation_stats['failed'] += 1
        if cache_key:
            self._chart_failure_recorded.add(cache_key)

    def _apply_cached_review_stats(self, block: Dict[str, Any]) -> None:
        """
        Reacumular estatisticas em graficos ja revisados, evitando reparos duplicados.

        Quando o fluxo de renderizacao redefine estatisticas mas o grafico ja foi revisado (_chart_reviewed=True),
        acumular contagens diretamente com base no status registrado, evitando acionamento repetido do ChartRepairer.
        """
        if not isinstance(block, dict):
            return

        status = block.get("_chart_review_status") or "valid"
        method = (block.get("_chart_review_method") or "none").lower()
        cache_key = self._chart_cache_key(block)

        self.chart_validation_stats['total'] += 1
        if status == "failed":
            self._record_chart_failure_stat(cache_key)
        elif status == "repaired":
            if method == "api":
                self.chart_validation_stats['repaired_api'] += 1
            else:
                self.chart_validation_stats['repaired_locally'] += 1
        else:
            self.chart_validation_stats['valid'] += 1

    def _format_chart_error_reason(
        self,
        validation_result: ValidationResult | None = None,
        fallback_reason: str | None = None
    ) -> str:
        """Concatenar aviso amigavel de falha"""
        base = "O formato das informacoes do grafico retornado pelo LLM esta incorreto; reparo local e multimodelo tentados, mas ainda nao foi possivel exibir corretamente."
        detail = None
        if validation_result:
            if validation_result.errors:
                detail = validation_result.errors[0]
            elif validation_result.warnings:
                detail = validation_result.warnings[0]
        if not detail and fallback_reason:
            detail = fallback_reason
        if detail:
            text = f"{base} Dica: {detail}"
            return text[:180] + ("..." if len(text) > 180 else "")
        return base

    def _render_chart_error_placeholder(
        self,
        title: str | None,
        reason: str,
        widget_id: str | None = None
    ) -> str:
        """Emitir placeholder de aviso quando grafico falha, evitando quebra de layout HTML/PDF"""
        safe_title = self._escape_html(title or "Grafico nao pode ser exibido")
        safe_reason = self._escape_html(reason)
        widget_attr = f' data-widget-id="{self._escape_attr(widget_id)}"' if widget_id else ""
        return f"""
        <div class="chart-card chart-card--error"{widget_attr}>
          <div class="chart-error">
            <div class="chart-error__icon">!</div>
            <div class="chart-error__body">
              <div class="chart-error__title">{safe_title}</div>
              <p class="chart-error__desc">{safe_reason}</p>
            </div>
          </div>
        </div>
        """

    def _has_chart_failure(self, block: Dict[str, Any]) -> tuple[bool, str | None]:
        """Verificar se ja existe registro de falha no reparo"""
        cache_key = self._chart_cache_key(block)
        if block.get("_chart_renderable") is False:
            return True, block.get("_chart_error_reason")
        if cache_key in self._chart_failure_notes:
            return True, self._chart_failure_notes.get(cache_key)
        return False, None

    def _normalize_chart_block(
        self,
        block: Dict[str, Any],
        chapter_context: Dict[str, Any] | None = None,
    ) -> None:
        """
        Completar campos ausentes no block de grafico (como scales, datasets) para melhorar tolerancia a falhas.

        - Mesclar scales erroneamente colocados no nivel superior do block em props.options.
        - Quando data ausente ou datasets vazio, tentar usar data em nivel de capitulo como fallback.
        """

        if not isinstance(block, dict):
            return

        if block.get("type") != "widget":
            return

        widget_type = block.get("widgetType", "")
        if not (isinstance(widget_type, str) and widget_type.startswith("chart.js")):
            return

        # Garantir que props existe
        props = block.get("props")
        if not isinstance(props, dict):
            block["props"] = {}
            props = block["props"]

        # Mesclar scales de nivel superior em options para evitar perda de configuracao
        scales = block.get("scales")
        if isinstance(scales, dict):
            options = props.get("options") if isinstance(props.get("options"), dict) else {}
            props["options"] = self._merge_dicts(options, {"scales": scales})

        # Garantir que data existe
        data = block.get("data")
        if not isinstance(data, dict):
            data = {}
            block["data"] = data

        # Se datasets vazio, tentar preencher com data em nivel de capitulo
        if chapter_context and self._is_chart_data_empty(data):
            chapter_data = chapter_context.get("data") if isinstance(chapter_context, dict) else None
            if isinstance(chapter_data, dict):
                fallback_ds = chapter_data.get("datasets")
                if isinstance(fallback_ds, list) and len(fallback_ds) > 0:
                    merged_data = copy.deepcopy(data)
                    merged_data["datasets"] = copy.deepcopy(fallback_ds)

                    if not merged_data.get("labels") and isinstance(chapter_data.get("labels"), list):
                        merged_data["labels"] = copy.deepcopy(chapter_data["labels"])

                    block["data"] = merged_data

        # Se labels ainda ausentes e pontos de dados contem valores x, gerar automaticamente para fallback e escalas de coordenadas
        data_ref = block.get("data")
        if isinstance(data_ref, dict) and not data_ref.get("labels"):
            datasets_ref = data_ref.get("datasets")
            if isinstance(datasets_ref, list) and datasets_ref:
                first_ds = datasets_ref[0]
                ds_data = first_ds.get("data") if isinstance(first_ds, dict) else None
                if isinstance(ds_data, list):
                    labels_from_data = []
                    for idx, point in enumerate(ds_data):
                        if isinstance(point, dict):
                            label_text = point.get("x") or point.get("label") or f"pt{idx + 1}"
                        else:
                            label_text = f"pt{idx + 1}"
                        labels_from_data.append(str(label_text))

                    if labels_from_data:
                        data_ref["labels"] = labels_from_data

    def _ensure_chart_reviewed(
        self,
        block: Dict[str, Any],
        chapter_context: Dict[str, Any] | None = None,
        *,
        increment_stats: bool = True
    ) -> tuple[bool, str | None]:
        """
        Garantir que grafico foi revisado/reparado e escrever resultados de volta no block original.

        Retorna:
            (renderable, fail_reason)
        """
        if not isinstance(block, dict):
            return True, None

        widget_type = block.get('widgetType', '')
        is_chart = isinstance(widget_type, str) and widget_type.startswith('chart.js')
        if not is_chart:
            return True, None

        is_wordcloud = 'wordcloud' in widget_type.lower() if isinstance(widget_type, str) else False
        cache_key = self._chart_cache_key(block)

        # Registro de falha existente ou marcado explicitamente como nao renderizavel, reutilizar resultado
        if block.get("_chart_renderable") is False:
            if increment_stats:
                self.chart_validation_stats['total'] += 1
                self._record_chart_failure_stat(cache_key)
            reason = block.get("_chart_error_reason")
            block["_chart_reviewed"] = True
            block["_chart_review_status"] = block.get("_chart_review_status") or "failed"
            block["_chart_review_method"] = block.get("_chart_review_method") or "none"
            if reason:
                self._note_chart_failure(cache_key, reason)
            return False, reason

        if block.get("_chart_reviewed"):
            if increment_stats:
                self._apply_cached_review_stats(block)
            failed, cached_reason = self._has_chart_failure(block)
            renderable = not failed and block.get("_chart_renderable", True) is not False
            return renderable, block.get("_chart_error_reason") or cached_reason

        # Primeira revisao: completar estrutura primeiro, depois validar/reparar
        self._normalize_chart_block(block, chapter_context)

        if increment_stats:
            self.chart_validation_stats['total'] += 1

        if is_wordcloud:
            if increment_stats:
                self.chart_validation_stats['valid'] += 1
            block["_chart_reviewed"] = True
            block["_chart_review_status"] = "valid"
            block["_chart_review_method"] = "none"
            return True, None

        validation_result = self.chart_validator.validate(block)

        if not validation_result.is_valid:
            logger.warning(
                f"Grafico {block.get('widgetId', 'unknown')} Falha na validacao: {validation_result.errors}"
            )

            repair_result = self.chart_repairer.repair(block, validation_result)

            if repair_result.success and repair_result.repaired_block:
                # Reparo bem-sucedido，escrever dados reparados de volta
                repaired_block = repair_result.repaired_block
                block.clear()
                block.update(repaired_block)
                method = repair_result.method or "local"
                logger.info(
                    f"Grafico {block.get('widgetId', 'unknown')} Reparo bem-sucedido "
                    f"(metodo: {method}): {repair_result.changes}"
                )

                if increment_stats:
                    if method == 'local':
                        self.chart_validation_stats['repaired_locally'] += 1
                    elif method == 'api':
                        self.chart_validation_stats['repaired_api'] += 1
                block["_chart_review_status"] = "repaired"
                block["_chart_review_method"] = method
                block["_chart_reviewed"] = True
                return True, None

            # Falha no reparo，registrar falha e emitir placeholder de aviso
            fail_reason = self._format_chart_error_reason(validation_result)
            block["_chart_renderable"] = False
            block["_chart_error_reason"] = fail_reason
            block["_chart_review_status"] = "failed"
            block["_chart_review_method"] = "none"
            block["_chart_reviewed"] = True
            self._note_chart_failure(cache_key, fail_reason)
            if increment_stats:
                self._record_chart_failure_stat(cache_key)
            logger.warning(
                f"Grafico {block.get('widgetId', 'unknown')} Falha no reparo，renderizacao ignorada: {fail_reason}"
            )
            return False, fail_reason

        # Validacao aprovada
        if increment_stats:
            self.chart_validation_stats['valid'] += 1
            if validation_result.warnings:
                logger.info(
                    f"Grafico {block.get('widgetId', 'unknown')} Validacao aprovada，"
                    f"mas com avisos: {validation_result.warnings}"
                )
        block["_chart_review_status"] = "valid"
        block["_chart_review_method"] = "none"
        block["_chart_reviewed"] = True
        return True, None

    def review_and_patch_document(
        self,
        document_ir: Dict[str, Any],
        *,
        reset_stats: bool = True,
        clone: bool = False
    ) -> Dict[str, Any]:
        """
        Revisar e reparar graficos globalmente, escrevendo resultados de volta no IR original para evitar reparos duplicados.

        Parametros:
            document_ir: Document IR original
            reset_stats: Se deve redefinir dados estatisticos
            clone: Se deve retornar copia profunda apos reparo (IR original ainda recebe resultados de reparo)

        Retorna:
            IR reparado (pode ser o objeto original ou sua copia profunda)
        """
        if reset_stats:
            self._reset_chart_validation_stats()

        target_ir = document_ir or {}

        def _walk_blocks(blocks: list, chapter_ctx: Dict[str, Any] | None = None) -> None:
            for blk in blocks or []:
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") == "widget":
                    self._ensure_chart_reviewed(blk, chapter_ctx, increment_stats=True)

                nested_blocks = blk.get("blocks")
                if isinstance(nested_blocks, list):
                    _walk_blocks(nested_blocks, chapter_ctx)

                if blk.get("type") == "list":
                    for item in blk.get("items", []):
                        if isinstance(item, list):
                            _walk_blocks(item, chapter_ctx)

                if blk.get("type") == "table":
                    for row in blk.get("rows", []):
                        cells = row.get("cells", [])
                        for cell in cells:
                            if isinstance(cell, dict):
                                cell_blocks = cell.get("blocks", [])
                                if isinstance(cell_blocks, list):
                                    _walk_blocks(cell_blocks, chapter_ctx)

        for chapter in target_ir.get("chapters", []) or []:
            if not isinstance(chapter, dict):
                continue
            _walk_blocks(chapter.get("blocks", []), chapter)

        return copy.deepcopy(target_ir) if clone else target_ir

    def _render_widget(self, block: Dict[str, Any]) -> str:
        """
        Renderizar container placeholder para componentes interativos como Chart.js e registrar JSON de configuracao.

        Realizar validacao e reparo de graficos antes da renderizacao:
        1. validate: ChartValidator verifica estrutura data/props/options do block;
        2. repair: se falhar, reparo local primeiro (fallback para labels/datasets/scale ausentes), depois chama API LLM;
        3. Fallback de falha: define _chart_renderable=False e _chart_error_reason, emite placeholder de erro em vez de lancar excecao.

        Parametros (correspondentes ao nivel IR):
        - block.widgetType: "chart.js/bar"/"chart.js/line"/"wordcloud" etc., determina renderizador e estrategia de validacao;
        - block.widgetId: ID unico do componente, para vinculacao canvas/data script;
        - block.props: passado diretamente para Chart.js options no frontend, ex: props.title / props.options.legend;
        - block.data: {labels, datasets} etc.; quando ausente, tenta completar com chapter.data em nivel de capitulo;
        - block.dataRef: referencia de dados externos, registrado como passagem direta por enquanto.

        Retorna:
            str: HTML contendo canvas e script de configuracao.
        """
        # Ponto de entrada unificado de revisao/reparo, evitando reparos duplicados posteriores
        widget_type = block.get('widgetType', '')
        is_chart = isinstance(widget_type, str) and widget_type.startswith('chart.js')
        is_wordcloud = isinstance(widget_type, str) and 'wordcloud' in widget_type.lower()
        reviewed = bool(block.get("_chart_reviewed"))
        renderable = True
        fail_reason = None

        if is_chart:
            renderable, fail_reason = self._ensure_chart_reviewed(
                block,
                getattr(self, "_current_chapter", None),
                increment_stats=not reviewed
            )

        widget_id = block.get('widgetId')
        props_snapshot = block.get("props") if isinstance(block.get("props"), dict) else {}
        display_title = props_snapshot.get("title") or block.get("title") or widget_id or "Grafico"

        if is_chart and not renderable:
            reason = fail_reason or "Formato das informacoes do grafico retornado pelo LLM incorreto, impossivel exibir corretamente"
            return self._render_chart_error_placeholder(display_title, reason, widget_id)

        # Renderizar HTML do grafico
        self.chart_counter += 1
        canvas_id = f"chart-{self.chart_counter}"
        config_id = f"chart-config-{self.chart_counter}"

        props, normalized_data = self._prepare_widget_payload(block)
        payload = {
            "widgetId": block.get("widgetId"),
            "widgetType": block.get("widgetType"),
            "props": props,
            "data": normalized_data,
            "dataRef": block.get("dataRef"),
        }
        config_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
        self.widget_scripts.append(
            f'<script type="application/json" id="{config_id}">{config_json}</script>'
        )

        title = props.get("title")
        title_html = f'<div class="chart-title">{self._escape_html(title)}</div>' if title else ""
        fallback_html = (
            self._render_wordcloud_fallback(props, block.get("widgetId"), block.get("data"))
            if is_wordcloud
            else self._render_widget_fallback(normalized_data, block.get("widgetId"))
        )
        return f"""
        <div class="chart-card{' wordcloud-card' if is_wordcloud else ''}">
          {title_html}
          <div class="chart-container">
            <canvas id="{canvas_id}" data-config-id="{config_id}"></canvas>
          </div>
          {fallback_html}
        </div>
        """

    def _render_widget_fallback(self, data: Dict[str, Any], widget_id: str | None = None) -> str:
        """Renderizar visao de fallback em texto para dados de graficos, evitando espaco em branco quando Chart.js falha ao carregar"""
        if not isinstance(data, dict):
            return ""
        labels = data.get("labels") or []
        datasets = data.get("datasets") or []
        if not labels or not datasets:
            return ""

        widget_attr = f' data-widget-id="{self._escape_attr(widget_id)}"' if widget_id else ""
        header_cells = "".join(
            f"<th>{self._escape_html(ds.get('label') or f'Serie{idx + 1}')}</th>"
            for idx, ds in enumerate(datasets)
        )
        body_rows = ""
        for idx, label in enumerate(labels):
            row_cells = [f"<td>{self._escape_html(label)}</td>"]
            for ds in datasets:
                series = ds.get("data") or []
                value = series[idx] if idx < len(series) else ""
                row_cells.append(f"<td>{self._escape_html(value)}</td>")
            body_rows += f"<tr>{''.join(row_cells)}</tr>"
        table_html = f"""
        <div class="chart-fallback" data-prebuilt="true"{widget_attr}>
          <table>
            <thead>
              <tr><th>Categoria</th>{header_cells}</tr>
            </thead>
            <tbody>
              {body_rows}
            </tbody>
          </table>
        </div>
        """
        return table_html

    def _render_wordcloud_fallback(
        self,
        props: Dict[str, Any] | None,
        widget_id: str | None = None,
        block_data: Any | None = None,
    ) -> str:
        """Fornecer tabela de fallback para nuvem de palavras, evitando pagina em branco apos falha de renderizacao WordCloud"""
        def _collect_items(raw: Any) -> list[dict]:
            """Normalizar multiplos formatos de entrada de nuvem de palavras (array/objeto/tupla/texto puro) em lista unificada de termos"""
            collected: list[dict] = []
            skip_keys = {"items", "data", "words", "labels", "datasets", "sourceData"}
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict):
                        text = item.get("word") or item.get("text") or item.get("label")
                        weight = item.get("weight")
                        category = item.get("category") or ""
                        if text:
                            collected.append({"word": str(text), "weight": weight, "category": str(category)})
                        # Se contem listas items/words/data aninhadas, extrair recursivamente
                        for nested_key in ("items", "words", "data"):
                            nested = item.get(nested_key)
                            if isinstance(nested, list):
                                collected.extend(_collect_items(nested))
                    elif isinstance(item, (list, tuple)) and item:
                        text = item[0]
                        weight = item[1] if len(item) > 1 else None
                        category = item[2] if len(item) > 2 else ""
                        if text:
                            collected.append({"word": str(text), "weight": weight, "category": str(category)})
                    elif isinstance(item, str):
                        collected.append({"word": item, "weight": 1.0, "category": ""})
            elif isinstance(raw, dict):
                # Se contem lista items/words/data, priorizar extracao recursiva, nao tratar nomes de chaves como palavras
                handled = False
                for nested_key in ("items", "words", "data"):
                    nested = raw.get(nested_key)
                    if isinstance(nested, list):
                        collected.extend(_collect_items(nested))
                        handled = True
                if handled:
                    return collected

                # Quando nao e estrutura Chart e nao contem skip_keys, tratar key/value como itens de nuvem de palavras
                if not {"labels", "datasets"}.intersection(raw.keys()):
                    for text, weight in raw.items():
                        if text in skip_keys:
                            continue
                        collected.append({"word": str(text), "weight": weight, "category": ""})
            return collected

        words: list[dict] = []
        seen: set[str] = set()
        candidates = []
        if isinstance(props, dict):
            # Aceitar apenas campos explicitos de array de termos, evitando confundir items aninhados como termos
            if "data" in props and isinstance(props.get("data"), list):
                candidates.append(props["data"])
            if "words" in props and isinstance(props.get("words"), list):
                candidates.append(props["words"])
            if "items" in props and isinstance(props.get("items"), list):
                candidates.append(props["items"])
        candidates.append((props or {}).get("sourceData"))

        # Permitir usar block.data como fallback, evitando espaco em branco quando props ausentes
        if block_data is not None:
            if isinstance(block_data, dict) and "items" in block_data and isinstance(block_data.get("items"), list):
                candidates.append(block_data["items"])
            else:
                candidates.append(block_data)

        for raw in candidates:
            for item in _collect_items(raw):
                key = f"{item['word']}::{item.get('category','')}"
                if key in seen:
                    continue
                seen.add(key)
                words.append(item)

        if not words:
            return ""

        def _format_weight(value: Any) -> str:
            """Formatar peso uniformemente, suportando porcentagem/numero e fallback para string"""
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if 0 <= value <= 1.5:
                    return f"{value * 100:.1f}%"
                return f"{value:.2f}".rstrip("0").rstrip(".")
            return str(value)

        widget_attr = f' data-widget-id="{self._escape_attr(widget_id)}"' if widget_id else ""
        rows = "".join(
            f"<tr><td>{self._escape_html(item['word'])}</td>"
            f"<td>{self._escape_html(_format_weight(item['weight']))}</td>"
            f"<td>{self._escape_html(item['category'] or '-')}</td></tr>"
            for item in words
        )
        return f"""
        <div class="chart-fallback" data-prebuilt="true"{widget_attr}>
          <table>
            <thead>
              <tr><th>Palavra-chave</th><th>Peso</th><th>Categoria</th></tr>
            </thead>
            <tbody>
              {rows}
            </tbody>
          </table>
        </div>
        """

    def _log_chart_validation_stats(self):
        """Emitir informacoes estatisticas de validacao de graficos"""
        stats = self.chart_validation_stats
        if stats['total'] == 0:
            return

        logger.info("=" * 60)
        logger.info("Estatisticas de validacao de graficos")
        logger.info("=" * 60)
        logger.info(f"Quantidade total de graficos: {stats['total']}")
        logger.info(f"  ✓ Validacao aprovada: {stats['valid']} ({stats['valid']/stats['total']*100:.1f}%)")

        if stats['repaired_locally'] > 0:
            logger.info(
                f"  ⚠ Reparo local: {stats['repaired_locally']} "
                f"({stats['repaired_locally']/stats['total']*100:.1f}%)"
            )

        if stats['repaired_api'] > 0:
            logger.info(
                f"  ⚠ Reparo via API: {stats['repaired_api']} "
                f"({stats['repaired_api']/stats['total']*100:.1f}%)"
            )

        if stats['failed'] > 0:
            logger.warning(
                f"  ✗ Falha no reparo: {stats['failed']} "
                f"({stats['failed']/stats['total']*100:.1f}%) - "
                f"Estes graficos exibirao placeholder de aviso"
            )

        logger.info("=" * 60)

    # ====== Protecao de informacoes preliminares ======

    def _kpi_signature_from_items(self, items: Any) -> tuple | None:
        """Converter array de KPI em assinatura comparavel"""
        if not isinstance(items, list):
            return None
        normalized = []
        for raw in items:
            normalized_item = self._normalize_kpi_item(raw)
            if normalized_item:
                normalized.append(normalized_item)
        return tuple(normalized) if normalized else None

    def _normalize_kpi_item(self, item: Any) -> tuple[str, str, str, str, str] | None:
        """
        Normalizar registro KPI individual em assinatura comparavel.

        Parametros:
            item: Dicionario original no array KPI, pode ter campos ausentes ou tipos mistos.

        Retorna:
            tuple | None: Quintupla (label, value, unit, delta, tone); None se entrada invalida.
        """
        if not isinstance(item, dict):
            return None

        def normalize(value: Any) -> str:
            """Unificar representacao de varios tipos de valores para gerar assinatura estavel"""
            if value is None:
                return ""
            if isinstance(value, (int, float)):
                return str(value)
            return str(value).strip()

        label = normalize(item.get("label"))
        value = normalize(item.get("value"))
        unit = normalize(item.get("unit"))
        delta = normalize(item.get("delta"))
        tone = normalize(item.get("deltaTone") or item.get("tone"))
        return label, value, unit, delta, tone

    def _should_skip_overview_kpi(self, block: Dict[str, Any]) -> bool:
        """Se conteudo KPI e identico a capa, considerar como visao geral duplicada"""
        if not self.hero_kpi_signature:
            return False
        block_signature = self._kpi_signature_from_items(block.get("items"))
        if not block_signature:
            return False
        return block_signature == self.hero_kpi_signature

    # ====== Renderizacao inline ======

    def _normalize_inline_payload(self, run: Dict[str, Any]) -> tuple[str, List[Dict[str, Any]]]:
        """Aplainar nos inline aninhados em texto basico e marks"""
        if not isinstance(run, dict):
            return ("" if run is None else str(run)), []

        # Processar tipo inlineRun: expandir recursivamente seu array inlines
        if run.get("type") == "inlineRun":
            inner_inlines = run.get("inlines") or []
            outer_marks = run.get("marks") or []
            # Mesclar recursivamente texto de todos os inlines internos
            texts = []
            all_marks = list(outer_marks)
            for inline in inner_inlines:
                inner_text, inner_marks = self._normalize_inline_payload(inline)
                texts.append(inner_text)
                all_marks.extend(inner_marks)
            return "".join(texts), all_marks

        marks = list(run.get("marks") or [])
        text_value: Any = run.get("text", "")
        seen: set[int] = set()

        while isinstance(text_value, dict):
            obj_id = id(text_value)
            if obj_id in seen:
                text_value = ""
                break
            seen.add(obj_id)
            nested_marks = text_value.get("marks")
            if nested_marks:
                marks.extend(nested_marks)
            if "text" in text_value:
                text_value = text_value.get("text")
            else:
                text_value = json.dumps(text_value, ensure_ascii=False)
                break

        if text_value is None:
            text_value = ""
        elif isinstance(text_value, (int, float)):
            text_value = str(text_value)
        elif not isinstance(text_value, str):
            try:
                text_value = json.dumps(text_value, ensure_ascii=False)
            except TypeError:
                text_value = str(text_value)

        if isinstance(text_value, str):
            stripped = text_value.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                payload = None
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    try:
                        payload = ast.literal_eval(stripped)
                    except (ValueError, SyntaxError):
                        payload = None
                if isinstance(payload, dict):
                    sentinel_keys = {"xrefs", "widgets", "footnotes", "errors", "metadata"}
                    if set(payload.keys()).issubset(sentinel_keys):
                        text_value = ""
                    else:
                        inline_payload = self._coerce_inline_payload(payload)
                        if inline_payload:
                            # Processar tipo inlineRun
                            if inline_payload.get("type") == "inlineRun":
                                return self._normalize_inline_payload(inline_payload)
                            nested_text = inline_payload.get("text")
                            if nested_text is not None:
                                text_value = nested_text
                            nested_marks = inline_payload.get("marks")
                            if isinstance(nested_marks, list):
                                marks.extend(nested_marks)
                        elif any(key in payload for key in self.INLINE_ARTIFACT_KEYS):
                            text_value = ""

        return text_value, marks

    @staticmethod
    def _normalize_latex_string(raw: Any) -> str:
        """Remover delimitadores matematicos externos, compativel com formatos $...$, $$...$$, \\(\\), \\[\\] etc."""
        if not isinstance(raw, str):
            return ""
        latex = raw.strip()
        patterns = [
            r'^\$\$(.*)\$\$$',
            r'^\$(.*)\$$',
            r'^\\\[(.*)\\\]$',
            r'^\\\((.*)\\\)$',
        ]
        for pat in patterns:
            m = re.match(pat, latex, re.DOTALL)
            if m:
                latex = m.group(1).strip()
                break
        return latex

    def _render_text_with_inline_math(
        self,
        text: Any,
        math_id: str | list | None = None,
        allow_display_block: bool = False
    ) -> str | None:
        """
        Identificar delimitadores matematicos em texto puro e renderizar como math-inline/math-block para melhorar compatibilidade.

        - Suporta $...$, $$...$$, \\(\\), \\[\\].
        - Se nenhuma formula detectada, retorna None.
        """
        if not isinstance(text, str) or not text:
            return None

        pattern = re.compile(r'(\$\$(.+?)\$\$|\$(.+?)\$|\\\((.+?)\\\)|\\\[(.+?)\\\])', re.S)
        matches = list(pattern.finditer(text))
        if not matches:
            return None

        cursor = 0
        parts: List[str] = []
        id_iter = iter(math_id) if isinstance(math_id, list) else None

        for idx, m in enumerate(matches, start=1):
            start, end = m.span()
            prefix = text[cursor:start]
            raw = next(g for g in m.groups()[1:] if g is not None)
            latex = self._normalize_latex_string(raw)
            # Se ja possui math_id, usar diretamente para evitar inconsistencia com ID de injecao SVG; caso contrario, gerar por numeracao local
            if id_iter:
                mid = next(id_iter, f"auto-math-{idx}")
            else:
                mid = math_id or f"auto-math-{idx}"
            id_attr = f' data-math-id="{self._escape_attr(mid)}"'
            is_display = m.group(1).startswith('$$') or m.group(1).startswith('\\[')
            is_standalone = (
                len(matches) == 1 and
                not text[:start].strip() and
                not text[end:].strip()
            )
            use_block = allow_display_block and is_display and is_standalone
            if use_block:
                # Formula display independente, pular espacos em branco laterais, renderizar diretamente como bloco
                parts.append(f'<div class="math-block"{id_attr}>$$ {self._escape_html(latex)} $$</div>')
                cursor = len(text)
                break
            else:
                if prefix:
                    parts.append(self._escape_html(prefix))
                parts.append(f'<span class="math-inline"{id_attr}>\\( {self._escape_html(latex)} \\)</span>')
            cursor = end

        if cursor < len(text):
            parts.append(self._escape_html(text[cursor:]))
        return "".join(parts)

    @staticmethod
    def _coerce_inline_payload(payload: Dict[str, Any]) -> Dict[str, Any] | None:
        """Tentar ao maximo restaurar nos inline em string para dict, reparando omissoes de renderizacao"""
        if not isinstance(payload, dict):
            return None
        inline_type = payload.get("type")
        # Suportar tipo inlineRun: contendo array inlines aninhado
        if inline_type == "inlineRun":
            return payload
        if inline_type and inline_type not in {"inline", "text"}:
            return None
        if "text" not in payload and "marks" not in payload and "inlines" not in payload:
            return None
        return payload

    def _render_inline(self, run: Dict[str, Any]) -> str:
        """
        Renderizar um unico inline run, suportando sobreposicao de multiplos marks.

        Parametros:
            run: No inline contendo text e marks.

        Retorna:
            str: Fragmento HTML com tags/estilos aplicados.
        """
        text_value, marks = self._normalize_inline_payload(run)
        math_mark = next((mark for mark in marks if mark.get("type") == "math"), None)
        if math_mark:
            latex = self._normalize_latex_string(math_mark.get("value"))
            if not isinstance(latex, str) or not latex.strip():
                latex = self._normalize_latex_string(text_value)
            math_id = self._escape_attr(run.get("mathId", "")) if run.get("mathId") else ""
            id_attr = f' data-math-id="{math_id}"' if math_id else ""
            return f'<span class="math-inline"{id_attr}>\\( {self._escape_html(latex)} \\)</span>'

        # Tentar extrair formulas matematicas do texto puro (mesmo sem math mark)
        math_id_hint = run.get("mathIds") or run.get("mathId")
        mathified = self._render_text_with_inline_math(text_value, math_id_hint)
        if mathified is not None:
            return mathified

        text = self._escape_html(text_value)
        styles: List[str] = []
        prefix: List[str] = []
        suffix: List[str] = []
        for mark in marks:
            mark_type = mark.get("type")
            if mark_type == "bold":
                prefix.append("<strong>")
                suffix.insert(0, "</strong>")
            elif mark_type == "italic":
                prefix.append("<em>")
                suffix.insert(0, "</em>")
            elif mark_type == "code":
                prefix.append("<code>")
                suffix.insert(0, "</code>")
            elif mark_type == "highlight":
                prefix.append("<mark>")
                suffix.insert(0, "</mark>")
            elif mark_type == "link":
                href_raw = mark.get("href")
                if href_raw and href_raw != "#":
                    href = self._escape_attr(href_raw)
                    title = self._escape_attr(mark.get("title") or "")
                    prefix.append(f'<a href="{href}" title="{title}" target="_blank" rel="noopener">')
                    suffix.insert(0, "</a>")
                else:
                    prefix.append('<span class="broken-link">')
                    suffix.insert(0, "</span>")
            elif mark_type == "color":
                value = mark.get("value")
                if value:
                    styles.append(f"color: {value}")
            elif mark_type == "font":
                family = mark.get("family")
                size = mark.get("size")
                weight = mark.get("weight")
                if family:
                    styles.append(f"font-family: {family}")
                if size:
                    styles.append(f"font-size: {size}")
                if weight:
                    styles.append(f"font-weight: {weight}")
            elif mark_type == "underline":
                styles.append("text-decoration: underline")
            elif mark_type == "strike":
                styles.append("text-decoration: line-through")
            elif mark_type == "subscript":
                prefix.append("<sub>")
                suffix.insert(0, "</sub>")
            elif mark_type == "superscript":
                prefix.append("<sup>")
                suffix.insert(0, "</sup>")

        if styles:
            style_attr = "; ".join(styles)
            prefix.insert(0, f'<span style="{style_attr}">')
            suffix.append("</span>")

        if not marks and "**" in (run.get("text") or ""):
            return self._render_markdown_bold_fallback(run.get("text", ""))

        return "".join(prefix) + text + "".join(suffix)

    def _render_markdown_bold_fallback(self, text: str) -> str:
        """Conversao de fallback para **negrito** quando LLM nao usa marks"""
        if not text:
            return ""
        result: List[str] = []
        cursor = 0
        while True:
            start = text.find("**", cursor)
            if start == -1:
                result.append(html.escape(text[cursor:]))
                break
            end = text.find("**", start + 2)
            if end == -1:
                result.append(html.escape(text[cursor:]))
                break
            result.append(html.escape(text[cursor:start]))
            bold_content = html.escape(text[start + 2:end])
            result.append(f"<strong>{bold_content}</strong>")
            cursor = end + 2
        return "".join(result)

    # ====== Texto / Ferramentas de seguranca ======

    def _clean_text_from_json_artifacts(self, text: Any) -> str:
        """
        Limpar fragmentos JSON do textoe marcadores de estrutura falsos.

        LLM as vezes mistura fragmentos JSON incompletos em campos de texto, como:
        "texto descritivo，{ \"chapterId\": \"S3"  ou  "texto descritivo，{ \"level\": 2"

        Este metodo:
        1. Remove objetos JSON incompletos (iniciados com { mas nao fechados corretamente)
        2. Remove arrays JSON incompletos (iniciados com [ mas nao fechados corretamente)
        3. Remove fragmentos isolados de pares chave-valor JSON

        Parametros:
            text: Texto que pode conter fragmentos JSON

        Retorna:
            str: Texto puro apos limpeza
        """
        if not text:
            return ""

        text_str = self._safe_text(text)

        # Padrao 1: remover objetos JSON incompletos iniciados com virgula+espaco+{
        # por exemplo: "texto，{ \"key\": \"value\""  ou  "texto，{\\n  \"key\""
        text_str = re.sub(r',\s*\{[^}]*$', '', text_str)

        # Padrao 2: remover arrays JSON incompletos iniciados com virgula+espaco+[
        text_str = re.sub(r',\s*\[[^\]]*$', '', text_str)

        # Padrao 3: remover { isolado e conteudo subsequente (se nao houver } correspondente)
        # Verificar se ha { nao fechado
        open_brace_pos = text_str.rfind('{')
        if open_brace_pos != -1:
            close_brace_pos = text_str.rfind('}')
            if close_brace_pos < open_brace_pos:
                # { apos } ou sem }, significa que nao esta fechado
                # Truncar antes do {
                text_str = text_str[:open_brace_pos].rstrip(',，、 \t\n')

        # Padrao 4: tratamento similar para [
        open_bracket_pos = text_str.rfind('[')
        if open_bracket_pos != -1:
            close_bracket_pos = text_str.rfind(']')
            if close_bracket_pos < open_bracket_pos:
                # [ apos ] ou sem ], significa que nao esta fechado
                text_str = text_str[:open_bracket_pos].rstrip(',，、 \t\n')

        # Padrao 5: remover fragmentos que parecem pares chave-valor JSON, como "chapterId": "S3
        # Este caso geralmente aparece apos os padroes acima
        text_str = re.sub(r',?\s*"[^"]+"\s*:\s*"[^"]*$', '', text_str)
        text_str = re.sub(r',?\s*"[^"]+"\s*:\s*[^,}\]]*$', '', text_str)

        # Limpar virgulas e espacos no final
        text_str = text_str.rstrip(',，、 \t\n')

        return text_str.strip()

    def _safe_text(self, value: Any) -> str:
        """Converter qualquer valor com seguranca para string, tolerante a None e objetos complexos"""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return str(value)
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)

    def _escape_html(self, value: Any) -> str:
        """Escape para contexto de texto HTML"""
        return html.escape(self._safe_text(value), quote=False)

    def _escape_attr(self, value: Any) -> str:
        """Escape para contexto de atributo HTML e remocao de quebras de linha perigosas"""
        escaped = html.escape(self._safe_text(value), quote=True)
        return escaped.replace("\n", " ").replace("\r", " ")

    # ====== CSS / JS (estilos e scripts) ======

    def _build_css(self, tokens: Dict[str, Any]) -> str:
        """Concatenar CSS de pagina inteira com base em tokens de tema, incluindo estilos responsivos e de impressao"""
        # Obter itens de configuracao com seguranca, garantindo que todos sao tipo dicionario
        colors_raw = tokens.get("colors")
        colors = colors_raw if isinstance(colors_raw, dict) else {}

        typography_raw = tokens.get("typography")
        typography = typography_raw if isinstance(typography_raw, dict) else {}

        # Obter fonts com seguranca, garantindo que e tipo dicionario
        fonts_raw = tokens.get("fonts") or typography.get("fonts")
        if isinstance(fonts_raw, dict):
            fonts = fonts_raw
        else:
            # Se fonts e string ou None, construir um dicionario
            font_family = typography.get("fontFamily")
            if isinstance(font_family, str):
                fonts = {"body": font_family, "heading": font_family}
            else:
                fonts = {}

        spacing_raw = tokens.get("spacing")
        spacing = spacing_raw if isinstance(spacing_raw, dict) else {}

        primary_palette = self._resolve_color_family(
            colors.get("primary"),
            {"main": "#1a365d", "light": "#2d3748", "dark": "#0f1a2d"},
        )
        secondary_palette = self._resolve_color_family(
            colors.get("secondary"),
            {"main": "#e53e3e", "light": "#fc8181", "dark": "#c53030"},
        )
        bg = self._resolve_color_value(
            colors.get("bg") or colors.get("background") or colors.get("surface"),
            "#f8f9fa",
        )
        text_color = self._resolve_color_value(
            colors.get("text") or colors.get("onBackground"),
            "#212529",
        )
        card = self._resolve_color_value(
            colors.get("card") or colors.get("surfaceCard"),
            "#ffffff",
        )
        border = self._resolve_color_value(
            colors.get("border") or colors.get("divider"),
            "#dee2e6",
        )
        shadow = "rgba(0,0,0,0.08)"
        container_width = spacing.get("container") or spacing.get("containerWidth") or "1200px"
        gutter = spacing.get("gutter") or spacing.get("pagePadding") or "24px"
        body_font = fonts.get("body") or fonts.get("primary") or "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
        heading_font = fonts.get("heading") or fonts.get("primary") or fonts.get("secondary") or body_font

        return f"""
:root {{ /* Funcao: Area de variaveis do tema claro; Config: ajustar propriedades relevantes dentro deste bloco */
  --bg-color: {bg}; /* Funcao: cor principal de fundo da pagina; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --text-color: {text_color}; /* Funcao: cor base do texto do corpo; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --primary-color: {primary_palette["main"]}; /* Funcao: cor principal (botoes/destaque); Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --primary-color-light: {primary_palette["light"]}; /* Funcao: cor principal clara, para hover/gradiente; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --primary-color-dark: {primary_palette["dark"]}; /* Funcao: cor principal escura, para enfase; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --secondary-color: {secondary_palette["main"]}; /* Funcao: cor secundaria (dicas/etiquetas); Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --secondary-color-light: {secondary_palette["light"]}; /* Funcao: cor secundaria clara; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --secondary-color-dark: {secondary_palette["dark"]}; /* Funcao: cor secundaria escura; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --card-bg: {card}; /* Funcao: cor de fundo de cartao/container; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --border-color: {border}; /* Funcao: cor de borda padrao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --shadow-color: {shadow}; /* Funcao: cor base de sombra; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-insight-bg: #f4f7ff; /* Funcao: fundo do cartao motor Insight; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-insight-border: #dce7ff; /* Funcao: borda do motor Insight; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-insight-text: #1f4b99; /* Funcao: cor de texto do motor Insight; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-media-bg: #fff6ec; /* Funcao: fundo do cartao motor Media; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-media-border: #ffd9b3; /* Funcao: borda do motor Media; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-media-text: #b65a1a; /* Funcao: cor de texto do motor Media; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-query-bg: #f1fbf5; /* Funcao: fundo do cartao motor Query; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-query-border: #c7ebd6; /* Funcao: borda do motor Query; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-query-text: #1d6b3f; /* Funcao: cor de texto do motor Query; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-quote-shadow: 0 12px 30px rgba(0,0,0,0.04); /* Funcao: sombra de citacao do Engine; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-strength: #1c7f6e; /* Funcao: SWOT cor principal de Forcas; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-weakness: #c0392b; /* Funcao: SWOT cor principal de Fraquezas; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-opportunity: #1f5ab3; /* Funcao: SWOT cor principal de Oportunidades; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-threat: #b36b16; /* Funcao: SWOT Ameacascor principal; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-on-light: #0f1b2b; /* Funcao: SWOT cor de texto em fundo claro; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-on-dark: #f7fbff; /* Funcao: SWOT cor de texto em fundo escuro; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-text: var(--text-color); /* Funcao: SWOT cor principal de texto; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-muted: rgba(0,0,0,0.58); /* Funcao: SWOT cor de texto secundario; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-surface: rgba(255,255,255,0.92); /* Funcao: SWOT cor de superficie do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-chip-bg: rgba(0,0,0,0.04); /* Funcao: SWOT cor de fundo de etiqueta; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-tag-border: var(--border-color); /* Funcao: SWOT borda de etiqueta; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-card-bg: linear-gradient(135deg, rgba(76,132,255,0.04), rgba(28,127,110,0.06)), var(--card-bg); /* Funcao: SWOT gradiente de fundo do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-card-border: var(--border-color); /* Funcao: SWOT borda do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-card-shadow: 0 14px 28px var(--shadow-color); /* Funcao: SWOT sombra do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-card-blur: none; /* Funcao: SWOT desfoque do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-base: linear-gradient(135deg, rgba(255,255,255,0.9), rgba(255,255,255,0.5)); /* Funcao: SWOT Quadrantebasecor de fundo; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-border: rgba(0,0,0,0.04); /* Funcao: SWOT Quadranteborda; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-strength-bg: linear-gradient(135deg, rgba(28,127,110,0.07), rgba(255,255,255,0.78)), var(--card-bg); /* Funcao: SWOT ForcasQuadrantecor de fundo; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-weakness-bg: linear-gradient(135deg, rgba(192,57,43,0.07), rgba(255,255,255,0.78)), var(--card-bg); /* Funcao: SWOT FraquezasQuadrantecor de fundo; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-opportunity-bg: linear-gradient(135deg, rgba(31,90,179,0.07), rgba(255,255,255,0.78)), var(--card-bg); /* Funcao: SWOT OportunidadesQuadrantecor de fundo; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-threat-bg: linear-gradient(135deg, rgba(179,107,22,0.07), rgba(255,255,255,0.78)), var(--card-bg); /* Funcao: SWOT Ameacas quadrante cor de fundo; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-strength-border: rgba(28,127,110,0.35); /* Funcao: SWOT borda de Forcas; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-weakness-border: rgba(192,57,43,0.35); /* Funcao: SWOT borda de Fraquezas; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-opportunity-border: rgba(31,90,179,0.35); /* Funcao: SWOT borda de Oportunidades; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-threat-border: rgba(179,107,22,0.35); /* Funcao: SWOT Ameacasborda; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-item-border: rgba(0,0,0,0.05); /* Funcao: item SWOTborda; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  /* Analise PESTvariaveis - tons roxo e ciano */
  --pest-political: #8e44ad; /* Funcao: PEST PoliticoDimensaocor principal; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-economic: #16a085; /* Funcao: PEST EconomicoDimensaocor principal; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-social: #e84393; /* Funcao: PEST SocialDimensaocor principal; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-technological: #2980b9; /* Funcao: PEST Tecnologico dimensao cor principal; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-on-light: #1a1a2e; /* Funcao: PEST cor de texto em fundo claro; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-on-dark: #f8f9ff; /* Funcao: PEST cor de texto em fundo escuro; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-text: var(--text-color); /* Funcao: PEST cor principal de texto; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-muted: rgba(0,0,0,0.55); /* Funcao: PEST cor de texto secundario; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-surface: rgba(255,255,255,0.88); /* Funcao: PEST cor de superficie do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-chip-bg: rgba(0,0,0,0.05); /* Funcao: PEST cor de fundo de etiqueta; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-tag-border: var(--border-color); /* Funcao: PEST borda de etiqueta; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-card-bg: linear-gradient(145deg, rgba(142,68,173,0.03), rgba(22,160,133,0.04)), var(--card-bg); /* Funcao: PEST gradiente de fundo do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-card-border: var(--border-color); /* Funcao: PEST borda do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-card-shadow: 0 16px 32px var(--shadow-color); /* Funcao: PEST sombra do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-card-blur: none; /* Funcao: PEST desfoque do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-base: linear-gradient(90deg, rgba(255,255,255,0.95), rgba(255,255,255,0.7)); /* Funcao: PEST cor base da faixa; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-border: rgba(0,0,0,0.06); /* Funcao: PEST borda da faixa; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-political-bg: linear-gradient(90deg, rgba(142,68,173,0.08), rgba(255,255,255,0.85)), var(--card-bg); /* Funcao: PEST cor de fundo faixa Politica; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-economic-bg: linear-gradient(90deg, rgba(22,160,133,0.08), rgba(255,255,255,0.85)), var(--card-bg); /* Funcao: PEST cor de fundo faixa Economica; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-social-bg: linear-gradient(90deg, rgba(232,67,147,0.08), rgba(255,255,255,0.85)), var(--card-bg); /* Funcao: PEST cor de fundo faixa Social; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-technological-bg: linear-gradient(90deg, rgba(41,128,185,0.08), rgba(255,255,255,0.85)), var(--card-bg); /* Funcao: PEST Tecnologicofaixacor de fundo; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-political-border: rgba(142,68,173,0.4); /* Funcao: PEST borda faixa Politica; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-economic-border: rgba(22,160,133,0.4); /* Funcao: PEST borda faixa Economica; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-social-border: rgba(232,67,147,0.4); /* Funcao: PEST borda faixa Social; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-technological-border: rgba(41,128,185,0.4); /* Funcao: PEST Tecnologicofaixaborda; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-item-border: rgba(0,0,0,0.06); /* Funcao: PEST borda de item; Config: sobrescrever em themeTokens ou alterar este valor padrao */
}} /* fim :root */
.dark-mode {{ /* Funcao: Area de variaveis do tema escuro; Config: ajustar propriedades relevantes dentro deste bloco */
  --bg-color: #121212; /* Funcao: cor principal de fundo da pagina; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --text-color: #e0e0e0; /* Funcao: cor base do texto do corpo; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --primary-color: #6ea8fe; /* Funcao: cor principal (botoes/destaque); Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --primary-color-light: #91caff; /* Funcao: cor principal clara, para hover/gradiente; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --primary-color-dark: #1f6feb; /* Funcao: cor principal escura, para enfase; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --secondary-color: #f28b82; /* Funcao: cor secundaria (dicas/etiquetas); Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --secondary-color-light: #f9b4ae; /* Funcao: cor secundaria clara; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --secondary-color-dark: #d9655c; /* Funcao: cor secundaria escura; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --card-bg: #1f1f1f; /* Funcao: cor de fundo de cartao/container; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --border-color: #2c2c2c; /* Funcao: cor de borda padrao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --shadow-color: rgba(0, 0, 0, 0.4); /* Funcao: cor base de sombra; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-insight-bg: rgba(145, 202, 255, 0.08); /* Funcao: fundo do cartao motor Insight; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-insight-border: rgba(145, 202, 255, 0.45); /* Funcao: borda do motor Insight; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-insight-text: #9dc2ff; /* Funcao: cor de texto do motor Insight; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-media-bg: rgba(255, 196, 138, 0.08); /* Funcao: fundo do cartao motor Media; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-media-border: rgba(255, 196, 138, 0.45); /* Funcao: borda do motor Media; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-media-text: #ffcb9b; /* Funcao: cor de texto do motor Media; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-query-bg: rgba(141, 215, 165, 0.08); /* Funcao: fundo do cartao motor Query; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-query-border: rgba(141, 215, 165, 0.45); /* Funcao: borda do motor Query; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-query-text: #a7e2ba; /* Funcao: cor de texto do motor Query; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-quote-shadow: 0 12px 28px rgba(0, 0, 0, 0.35); /* Funcao: sombra de citacao do Engine; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-strength: #1c7f6e; /* Funcao: SWOT cor principal de Forcas; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-weakness: #e06754; /* Funcao: SWOT cor principal de Fraquezas; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-opportunity: #5a8cff; /* Funcao: SWOT cor principal de Oportunidades; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-threat: #d48a2c; /* Funcao: SWOT Ameacascor principal; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-on-light: #0f1b2b; /* Funcao: SWOT cor de texto em fundo claro; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-on-dark: #e6f0ff; /* Funcao: SWOT cor de texto em fundo escuro; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-text: #e6f0ff; /* Funcao: SWOT cor principal de texto; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-muted: rgba(230,240,255,0.75); /* Funcao: SWOT cor de texto secundario; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-surface: rgba(255,255,255,0.08); /* Funcao: SWOT cor de superficie do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-chip-bg: rgba(255,255,255,0.14); /* Funcao: SWOT cor de fundo de etiqueta; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-tag-border: rgba(255,255,255,0.24); /* Funcao: SWOT borda de etiqueta; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-card-bg: radial-gradient(140% 140% at 18% 18%, rgba(110,168,254,0.18), transparent 55%), radial-gradient(120% 140% at 82% 0%, rgba(28,127,110,0.16), transparent 52%), linear-gradient(160deg, #0b1424 0%, #0b1f31 52%, #0a1626 100%); /* Funcao: SWOT gradiente de fundo do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-card-border: rgba(255,255,255,0.14); /* Funcao: SWOT borda do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-card-shadow: 0 24px 60px rgba(0, 0, 0, 0.58); /* Funcao: SWOT sombra do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-card-blur: blur(12px); /* Funcao: SWOT desfoque do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-base: linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02)); /* Funcao: SWOT Quadrantebasecor de fundo; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-border: rgba(255,255,255,0.2); /* Funcao: SWOT Quadranteborda; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-strength-bg: linear-gradient(150deg, rgba(28,127,110,0.28), rgba(28,127,110,0.12)), var(--swot-cell-base); /* Funcao: SWOT ForcasQuadrantecor de fundo; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-weakness-bg: linear-gradient(150deg, rgba(192,57,43,0.32), rgba(192,57,43,0.14)), var(--swot-cell-base); /* Funcao: SWOT FraquezasQuadrantecor de fundo; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-opportunity-bg: linear-gradient(150deg, rgba(31,90,179,0.28), rgba(31,90,179,0.12)), var(--swot-cell-base); /* Funcao: SWOT OportunidadesQuadrantecor de fundo; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-threat-bg: linear-gradient(150deg, rgba(179,107,22,0.32), rgba(179,107,22,0.14)), var(--swot-cell-base); /* Funcao: SWOT Ameacas quadrante cor de fundo; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-strength-border: rgba(28,127,110,0.65); /* Funcao: SWOT borda de Forcas; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-weakness-border: rgba(192,57,43,0.68); /* Funcao: SWOT borda de Fraquezas; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-opportunity-border: rgba(31,90,179,0.68); /* Funcao: SWOT borda de Oportunidades; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-cell-threat-border: rgba(179,107,22,0.68); /* Funcao: SWOT Ameacasborda; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --swot-item-border: rgba(255,255,255,0.14); /* Funcao: item SWOTborda; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  /* Analise PESTvariaveis - modo escuro */
  --pest-political: #a569bd; /* Funcao: PEST PoliticoDimensaocor principal; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-economic: #48c9b0; /* Funcao: PEST EconomicoDimensaocor principal; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-social: #f06292; /* Funcao: PEST SocialDimensaocor principal; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-technological: #5dade2; /* Funcao: PEST Tecnologico dimensao cor principal; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-on-light: #1a1a2e; /* Funcao: PEST cor de texto em fundo claro; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-on-dark: #f0f4ff; /* Funcao: PEST cor de texto em fundo escuro; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-text: #f0f4ff; /* Funcao: PEST cor principal de texto; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-muted: rgba(240,244,255,0.7); /* Funcao: PEST cor de texto secundario; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-surface: rgba(255,255,255,0.06); /* Funcao: PEST cor de superficie do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-chip-bg: rgba(255,255,255,0.12); /* Funcao: PEST cor de fundo de etiqueta; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-tag-border: rgba(255,255,255,0.22); /* Funcao: PEST borda de etiqueta; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-card-bg: radial-gradient(130% 130% at 15% 15%, rgba(165,105,189,0.16), transparent 50%), radial-gradient(110% 130% at 85% 5%, rgba(72,201,176,0.14), transparent 48%), linear-gradient(155deg, #12162a 0%, #161b30 50%, #0f1425 100%); /* Funcao: PEST gradiente de fundo do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-card-border: rgba(255,255,255,0.12); /* Funcao: PEST borda do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-card-shadow: 0 28px 65px rgba(0, 0, 0, 0.55); /* Funcao: PEST sombra do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-card-blur: blur(10px); /* Funcao: PEST desfoque do cartao; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-base: linear-gradient(90deg, rgba(255,255,255,0.05), rgba(255,255,255,0.02)); /* Funcao: PEST cor base da faixa; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-border: rgba(255,255,255,0.18); /* Funcao: PEST borda da faixa; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-political-bg: linear-gradient(90deg, rgba(142,68,173,0.25), rgba(142,68,173,0.1)), var(--pest-strip-base); /* Funcao: PEST cor de fundo faixa Politica; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-economic-bg: linear-gradient(90deg, rgba(22,160,133,0.25), rgba(22,160,133,0.1)), var(--pest-strip-base); /* Funcao: PEST cor de fundo faixa Economica; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-social-bg: linear-gradient(90deg, rgba(232,67,147,0.25), rgba(232,67,147,0.1)), var(--pest-strip-base); /* Funcao: PEST cor de fundo faixa Social; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-technological-bg: linear-gradient(90deg, rgba(41,128,185,0.25), rgba(41,128,185,0.1)), var(--pest-strip-base); /* Funcao: PEST Tecnologicofaixacor de fundo; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-political-border: rgba(165,105,189,0.6); /* Funcao: PEST borda faixa Politica; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-economic-border: rgba(72,201,176,0.6); /* Funcao: PEST borda faixa Economica; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-social-border: rgba(240,98,146,0.6); /* Funcao: PEST borda faixa Social; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-strip-technological-border: rgba(93,173,226,0.6); /* Funcao: PEST Tecnologicofaixaborda; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --pest-item-border: rgba(255,255,255,0.12); /* Funcao: PEST borda de item; Config: sobrescrever em themeTokens ou alterar este valor padrao */
}} /* fim .dark-mode */
* {{ box-sizing: border-box; }} /* Funcao: modelo de caixa global unificado, evitando erros de calculo de margem/padding; Config: geralmente manter border-box, alterar para content-box se necessario comportamento nativo */
body {{ /* Funcao: configuracao global de tipografia e fundo; Config: ajustar propriedades relevantes dentro deste bloco */
  margin: 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  font-family: {body_font}; /* Funcao: familia de fontes; Config: ajustar valores/cores/variaveis conforme necessario */
  background: linear-gradient(180deg, rgba(0,0,0,0.04), rgba(0,0,0,0)) fixed, var(--bg-color); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--text-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  line-height: 1.7; /* Funcao: altura de linha, melhorar legibilidade; Config: ajustar valores/cores/variaveis conforme necessario */
  min-height: 100vh; /* Funcao: altura minima, evitar colapso; Config: ajustar valores/cores/variaveis conforme necessario */
  transition: background-color 0.45s ease, color 0.45s ease; /* Funcao: duracao/propriedade de animacao de transicao; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim body */
.report-header, main, .hero-section, .chapter, .chart-card, .callout, .engine-quote, .kpi-card, .toc, .table-wrap {{ /* Funcao: animacao de transicao unificada para containers comuns; Config: ajustar propriedades relevantes dentro deste bloco */
  transition: background-color 0.45s ease, color 0.45s ease, border-color 0.45s ease, box-shadow 0.45s ease; /* Funcao: duracao/propriedade de animacao de transicao; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .report-header, main, .hero-section, .chapter, .chart-card, .callout, .engine-quote, .kpi-card, .toc, .table-wrap */
.report-header {{ /* Funcao: area de cabecalho fixo no topo; Config: ajustar propriedades relevantes dentro deste bloco */
  position: sticky; /* Funcao: modo de posicionamento; Config: ajustar valores/cores/variaveis conforme necessario */
  top: 0; /* Funcao: deslocamento superior; Config: ajustar valores/cores/variaveis conforme necessario */
  z-index: 10; /* Funcao: ordem de empilhamento; Config: ajustar valores/cores/variaveis conforme necessario */
  background: var(--card-bg); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 20px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-bottom: 1px solid var(--border-color); /* Funcao: borda inferior; Config: ajustar valores/cores/variaveis conforme necessario */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  align-items: center; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
  justify-content: space-between; /* Funcao: alinhamento do eixo principal flex; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 16px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: 0 2px 6px var(--shadow-color); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .report-header */
.tagline {{ /* Funcao: linha de slogan do titulo; Config: ajustar propriedades relevantes dentro deste bloco */
  margin: 4px 0 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--secondary-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.95rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .tagline */
.hero-section {{ /* Funcao: container principal do resumo da capa; Config: ajustar propriedades relevantes dentro deste bloco */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  flex-wrap: wrap; /* Funcao: estrategia de quebra de linha; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 24px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 24px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 20px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  background: linear-gradient(135deg, rgba(0,123,255,0.1), rgba(23,162,184,0.1)); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid rgba(0,0,0,0.08); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  margin-bottom: 32px; /* Funcao: margin-bottom propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .hero-section */
.hero-content {{ /* Funcao: area de texto esquerda da capa; Config: ajustar propriedades relevantes dentro deste bloco */
  flex: 2; /* Funcao: proporcao de ocupacao flex; Config: ajustar valores/cores/variaveis conforme necessario */
  min-width: 260px; /* Funcao: largura minima; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .hero-content */
.hero-side {{ /* Funcao: coluna KPI direita da capa; Config: ajustar propriedades relevantes dentro deste bloco */
  flex: 1; /* Funcao: proporcao de ocupacao flex; Config: ajustar valores/cores/variaveis conforme necessario */
  min-width: 220px; /* Funcao: largura minima; Config: ajustar valores/cores/variaveis conforme necessario */
  display: grid; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); /* Funcao: modelo de colunas da grade; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 12px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .hero-side */
@media screen {{
  .hero-side {{
    margin-top: 28px; /* Funcao: deslocar para baixo apenas na exibicao em tela, evitando obstrucao; Config: ajustar valores conforme necessario */
  }}
}}
.hero-kpi {{ /* Funcao: cartao KPI da capa; Config: ajustar propriedades relevantes dentro deste bloco */
  background: var(--card-bg); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 14px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 16px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: 0 6px 16px var(--shadow-color); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .hero-kpi */
.hero-kpi .label {{ /* Funcao: .hero-kpi .label area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-size: 0.9rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--secondary-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .hero-kpi .label */
.hero-kpi .value {{ /* Funcao: .hero-kpi .value area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-size: 1.8rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 700; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .hero-kpi .value */
.hero-highlights {{ /* Funcao: lista de destaques da capa; Config: ajustar propriedades relevantes dentro deste bloco */
  list-style: none; /* Funcao: estilo de lista; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 0; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 16px 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  flex-wrap: wrap; /* Funcao: estrategia de quebra de linha; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 10px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .hero-highlights */
.hero-highlights li {{ /* Funcao: .hero-highlights li area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  margin: 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .hero-highlights li */
.badge {{ /* Funcao: etiqueta de emblema; Config: ajustar propriedades relevantes dentro deste bloco */
  display: inline-flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  align-items: center; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 6px 12px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 999px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  background: rgba(0,0,0,0.05); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.9rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .badge */
.broken-link {{ /* Funcao: estilo de aviso de link invalido; Config: ajustar propriedades relevantes dentro deste bloco */
  text-decoration: underline dotted; /* Funcao: decoracao de texto; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--primary-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .broken-link */
.hero-actions {{ /* Funcao: container de botoes de acao da capa; Config: ajustar propriedades relevantes dentro deste bloco */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  flex-wrap: wrap; /* Funcao: estrategia de quebra de linha; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 12px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .hero-actions */
.ghost-btn {{ /* Funcao: estilo de botao secundario; Config: ajustar propriedades relevantes dentro deste bloco */
  border: 1px solid var(--primary-color); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  background: transparent; /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--primary-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 999px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 8px 16px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  cursor: pointer; /* Funcao: estilo de cursor do mouse; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .ghost-btn */
.hero-summary {{ /* Funcao: texto de resumo da capa; Config: ajustar propriedades relevantes dentro deste bloco */
  font-size: 1.05rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 500; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  margin-top: 0; /* Funcao: margin-top propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .hero-summary */
.llm-error-block {{ /* Funcao: container de aviso de erro LLM; Config: ajustar propriedades relevantes dentro deste bloco */
  border: 1px dashed var(--secondary-color); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 12px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 12px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 12px 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  background: rgba(229,62,62,0.06); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  position: relative; /* Funcao: modo de posicionamento; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .llm-error-block */
.llm-error-block.importance-critical {{ /* Funcao: .llm-error-block.importance-critical area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  border-color: var(--secondary-color-dark); /* Funcao: border-color propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  background: rgba(229,62,62,0.12); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .llm-error-block.importance-critical */
.llm-error-block::after {{ /* Funcao: .llm-error-block::after area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  content: attr(data-raw); /* Funcao: content propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  white-space: pre-wrap; /* Funcao: espaco em branco e estrategia de quebra de linha; Config: ajustar valores/cores/variaveis conforme necessario */
  position: absolute; /* Funcao: modo de posicionamento; Config: ajustar valores/cores/variaveis conforme necessario */
  left: 0; /* Funcao: left propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  right: 0; /* Funcao: right propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  bottom: 100%; /* Funcao: bottom propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  max-height: 240px; /* Funcao: max-height propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  overflow: auto; /* Funcao: tratamento de overflow; Config: ajustar valores/cores/variaveis conforme necessario */
  background: rgba(0,0,0,0.85); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  color: #fff; /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.85rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 12px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 10px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  margin-bottom: 8px; /* Funcao: margin-bottom propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  opacity: 0; /* Funcao: opacidade; Config: ajustar valores/cores/variaveis conforme necessario */
  pointer-events: none; /* Funcao: pointer-events propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  transition: opacity 0.2s ease; /* Funcao: duracao/propriedade de animacao de transicao; Config: ajustar valores/cores/variaveis conforme necessario */
  z-index: 20; /* Funcao: ordem de empilhamento; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .llm-error-block::after */
.llm-error-block:hover::after {{ /* Funcao: .llm-error-block:hover::after area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  opacity: 1; /* Funcao: opacidade; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .llm-error-block:hover::after */
.report-header h1 {{ /* Funcao: titulo principal do cabecalho; Config: ajustar propriedades relevantes dentro deste bloco */
  margin: 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 1.6rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--primary-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .report-header h1 */
.report-header .subtitle {{ /* Funcao: subtitulo do cabecalho; Config: ajustar propriedades relevantes dentro deste bloco */
  margin: 4px 0 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--secondary-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .report-header .subtitle */
.header-actions {{ /* Funcao: grupo de botoes do cabecalho; Config: ajustar propriedades relevantes dentro deste bloco */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 12px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  flex-wrap: wrap; /* Funcao: estrategia de quebra de linha; Config: ajustar valores/cores/variaveis conforme necessario */
  align-items: center; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .header-actions */
theme-button {{ /* Funcao: componente de troca de tema; Config: ajustar propriedades relevantes dentro deste bloco */
  display: inline-block; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  vertical-align: middle; /* Funcao: vertical-align propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim theme-button */
.cover {{ /* Funcao: area de capa; Config: ajustar propriedades relevantes dentro deste bloco */
  text-align: center; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 20px 0 40px; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .cover */
.cover h1 {{ /* Funcao: .cover h1 area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-size: 2.4rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 0.4em 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .cover h1 */
.cover-hint {{ /* Funcao: .cover-hint area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  letter-spacing: 0.4em; /* Funcao: espacamento entre caracteres; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--secondary-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.95rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .cover-hint */
.cover-subtitle {{ /* Funcao: .cover-subtitle area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  color: var(--secondary-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .cover-subtitle */
.action-btn {{ /* Funcao: estilo base de botao principal; Config: ajustar propriedades relevantes dentro deste bloco */
  --mouse-x: 50%; /* Funcao: temavariaveis mouse-x; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --mouse-y: 50%; /* Funcao: temavariaveis mouse-y; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  border: none; /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 10px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  background: linear-gradient(135deg, var(--primary-color) 0%, var(--secondary-color) 100%); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  color: #fff; /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 11px 22px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  cursor: pointer; /* Funcao: estilo de cursor do mouse; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.92rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 600; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  letter-spacing: 0.025em; /* Funcao: espacamento entre caracteres; Config: ajustar valores/cores/variaveis conforme necessario */
  transition: all 0.35s cubic-bezier(0.4, 0, 0.2, 1); /* Funcao: duracao/propriedade de animacao de transicao; Config: ajustar valores/cores/variaveis conforme necessario */
  min-width: 140px; /* Funcao: largura minima; Config: ajustar valores/cores/variaveis conforme necessario */
  white-space: nowrap; /* Funcao: espaco em branco e estrategia de quebra de linha; Config: ajustar valores/cores/variaveis conforme necessario */
  display: inline-flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  align-items: center; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
  justify-content: center; /* Funcao: alinhamento do eixo principal flex; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 10px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.12), 0 2px 6px rgba(0, 0, 0, 0.08); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
  position: relative; /* Funcao: modo de posicionamento; Config: ajustar valores/cores/variaveis conforme necessario */
  overflow: hidden; /* Funcao: tratamento de overflow; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .action-btn */
.action-btn::before {{ /* Funcao: .action-btn::before area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  content: ''; /* Funcao: content propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  position: absolute; /* Funcao: modo de posicionamento; Config: ajustar valores/cores/variaveis conforme necessario */
  top: 0; /* Funcao: deslocamento superior; Config: ajustar valores/cores/variaveis conforme necessario */
  left: 0; /* Funcao: left propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  width: 100%; /* Funcao: configuracao de largura; Config: ajustar valores/cores/variaveis conforme necessario */
  height: 100%; /* Funcao: configuracao de altura; Config: ajustar valores/cores/variaveis conforme necessario */
  background: linear-gradient(to bottom, rgba(255,255,255,0.12), transparent); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  opacity: 0; /* Funcao: opacidade; Config: ajustar valores/cores/variaveis conforme necessario */
  transition: opacity 0.35s ease; /* Funcao: duracao/propriedade de animacao de transicao; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .action-btn::before */
.action-btn::after {{ /* Funcao: .action-btn::after area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  content: ''; /* Funcao: content propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  position: absolute; /* Funcao: modo de posicionamento; Config: ajustar valores/cores/variaveis conforme necessario */
  top: var(--mouse-y); /* Funcao: deslocamento superior; Config: ajustar valores/cores/variaveis conforme necessario */
  left: var(--mouse-x); /* Funcao: left propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  width: 0; /* Funcao: configuracao de largura; Config: ajustar valores/cores/variaveis conforme necessario */
  height: 0; /* Funcao: configuracao de altura; Config: ajustar valores/cores/variaveis conforme necessario */
  background: radial-gradient(circle, rgba(255,255,255,0.18) 0%, transparent 70%); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 50%; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  transform: translate(-50%, -50%); /* Funcao: transform propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  transition: width 0.45s ease-out, height 0.45s ease-out; /* Funcao: duracao/propriedade de animacao de transicao; Config: ajustar valores/cores/variaveis conforme necessario */
  pointer-events: none; /* Funcao: pointer-events propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .action-btn::after */
.action-btn:hover {{ /* Funcao: .action-btn:hover area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  transform: translateY(-2px); /* Funcao: transform propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: 0 8px 25px rgba(0, 0, 0, 0.18), 0 4px 10px rgba(0, 0, 0, 0.1); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .action-btn:hover */
.action-btn:hover::before {{ /* Funcao: .action-btn:hover::before area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  opacity: 1; /* Funcao: opacidade; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .action-btn:hover::before */
.action-btn:hover::after {{ /* Funcao: .action-btn:hover::after area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  width: 280%; /* Funcao: configuracao de largura; Config: ajustar valores/cores/variaveis conforme necessario */
  height: 280%; /* Funcao: configuracao de altura; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .action-btn:hover::after */
.action-btn:active {{ /* Funcao: .action-btn:active area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  transform: translateY(0) scale(0.98); /* Funcao: transform propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .action-btn:active */
.action-btn .btn-icon {{ /* Funcao: .action-btn .btn-icon area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  width: 18px; /* Funcao: configuracao de largura; Config: ajustar valores/cores/variaveis conforme necessario */
  height: 18px; /* Funcao: configuracao de altura; Config: ajustar valores/cores/variaveis conforme necessario */
  flex-shrink: 0; /* Funcao: flex-shrink propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  filter: drop-shadow(0 1px 1px rgba(0,0,0,0.15)); /* Funcao: efeito de filtro; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .action-btn .btn-icon */
.theme-toggle-btn .sun-icon,
.theme-toggle-btn .moon-icon {{ /* Funcao: estilo de icone do botao de troca de tema; Config: ajustar propriedades relevantes dentro deste bloco */
  transition: transform 0.3s ease, opacity 0.3s ease; /* Funcao: duracao/propriedade de animacao de transicao; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .theme-toggle-btn icone */
.theme-toggle-btn .sun-icon {{ /* Funcao: estilo do icone de sol; Config: ajustar propriedades relevantes dentro deste bloco */
  color: #F59E0B; /* Funcao: cor do icone de sol; Config: ajustar valores/cores/variaveis conforme necessario */
  stroke: #F59E0B; /* Funcao: cor de contorno do icone de sol; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .theme-toggle-btn .sun-icon */
.theme-toggle-btn .moon-icon {{ /* Funcao: estilo do icone de lua; Config: ajustar propriedades relevantes dentro deste bloco */
  color: #6366F1; /* Funcao: cor do icone de lua; Config: ajustar valores/cores/variaveis conforme necessario */
  stroke: #6366F1; /* Funcao: cor de contorno do icone de lua; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .theme-toggle-btn .moon-icon */
.theme-toggle-btn:hover .sun-icon {{ /* Funcao: efeito do icone de sol ao passar o mouse; Config: ajustar propriedades relevantes dentro deste bloco */
  transform: rotate(15deg); /* Funcao: transformacao de rotacao; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .theme-toggle-btn:hover .sun-icon */
.theme-toggle-btn:hover .moon-icon {{ /* Funcao: efeito do icone de lua ao passar o mouse; Config: ajustar propriedades relevantes dentro deste bloco */
  transform: rotate(-15deg) scale(1.1); /* Funcao: transformacao de rotacao e escala; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .theme-toggle-btn:hover .moon-icon */
body.exporting {{ /* Funcao: body.exporting area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  cursor: progress; /* Funcao: estilo de cursor do mouse; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim body.exporting */
.export-overlay {{ /* Funcao: camada de overlay de exportacao; Config: ajustar propriedades relevantes dentro deste bloco */
  position: fixed; /* Funcao: modo de posicionamento; Config: ajustar valores/cores/variaveis conforme necessario */
  inset: 0; /* Funcao: inset propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  background: rgba(3, 9, 26, 0.55); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  backdrop-filter: blur(2px); /* Funcao: desfoque de fundo; Config: ajustar valores/cores/variaveis conforme necessario */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  align-items: center; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
  justify-content: center; /* Funcao: alinhamento do eixo principal flex; Config: ajustar valores/cores/variaveis conforme necessario */
  opacity: 0; /* Funcao: opacidade; Config: ajustar valores/cores/variaveis conforme necessario */
  pointer-events: none; /* Funcao: pointer-events propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  transition: opacity 0.3s ease; /* Funcao: duracao/propriedade de animacao de transicao; Config: ajustar valores/cores/variaveis conforme necessario */
  z-index: 999; /* Funcao: ordem de empilhamento; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .export-overlay */
.export-overlay.active {{ /* Funcao: .export-overlay.active area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  opacity: 1; /* Funcao: opacidade; Config: ajustar valores/cores/variaveis conforme necessario */
  pointer-events: all; /* Funcao: pointer-events propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .export-overlay.active */
.export-dialog {{ /* Funcao: .export-dialog area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  background: rgba(12, 19, 38, 0.92); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 24px 32px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 18px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  color: #fff; /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  text-align: center; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
  min-width: 280px; /* Funcao: largura minima; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: 0 16px 40px rgba(0,0,0,0.45); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .export-dialog */
.export-spinner {{ /* Funcao: .export-spinner area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  width: 48px; /* Funcao: configuracao de largura; Config: ajustar valores/cores/variaveis conforme necessario */
  height: 48px; /* Funcao: configuracao de altura; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 50%; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 3px solid rgba(255,255,255,0.2); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  border-top-color: var(--secondary-color); /* Funcao: border-top-color propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 0 auto 16px; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  animation: export-spin 1s linear infinite; /* Funcao: animation propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .export-spinner */
.export-status {{ /* Funcao: .export-status area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  margin: 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 1rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .export-status */
.exporting *,
.exporting *::before, /* Funcao: .exporting * propriedade de estilo; Config: /cor/variaveis */
.exporting *::after {{ /* Funcao: .exporting *::after area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  animation: none !important; /* Funcao: animation propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  transition: none !important; /* Funcao: duracao/propriedade de animacao de transicao; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .exporting *::after */
.export-progress {{ /* Funcao: .export-progress area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  width: 220px; /* Funcao: configuracao de largura; Config: ajustar valores/cores/variaveis conforme necessario */
  height: 6px; /* Funcao: configuracao de altura; Config: ajustar valores/cores/variaveis conforme necessario */
  background: rgba(255,255,255,0.25); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 999px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  overflow: hidden; /* Funcao: tratamento de overflow; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 20px auto 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  position: relative; /* Funcao: modo de posicionamento; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .export-progress */
.export-progress-bar {{ /* Funcao: .export-progress-bar area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  position: absolute; /* Funcao: modo de posicionamento; Config: ajustar valores/cores/variaveis conforme necessario */
  top: 0; /* Funcao: deslocamento superior; Config: ajustar valores/cores/variaveis conforme necessario */
  bottom: 0; /* Funcao: bottom propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  width: 45%; /* Funcao: configuracao de largura; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: inherit; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  background: linear-gradient(90deg, var(--primary-color), var(--secondary-color)); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  animation: export-progress 1.4s ease-in-out infinite; /* Funcao: animation propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .export-progress-bar */
@keyframes export-spin {{ /* Funcao: @keyframes export-spin area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  from {{ transform: rotate(0deg); }} /* Funcao: ponto inicial do keyframe，manter angulo de 0°; Config: pode ser alterado para outro estado inicial de rotacao ou escala */
  to {{ transform: rotate(360deg); }} /* Funcao: ponto final do keyframe，rotacao completa; Config: pode ser alterado para angulo/efeito final personalizado */
}} /* fim @keyframes export-spin */
@keyframes export-progress {{ /* Funcao: @keyframes export-progress area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  0% {{ left: -45%; }} /* Funcao: ponto inicial da animacao de progresso，barra entra pela esquerda; Config: ajustar porcentagem left inicial */
  50% {{ left: 20%; }} /* Funcao: ponto medio da animacao de progresso，container; Config: ajustar proporcao de deslocamento conforme necessario */
  100% {{ left: 110%; }} /* Funcao: ponto final da animacao de progresso，barra desliza para a direita; Config: ajustar porcentagem left final */
}} /* fim @keyframes export-progress */
main {{ /* Funcao: container de conteudo principal; Config: ajustar propriedades relevantes dentro deste bloco */
  max-width: {container_width}; /* Funcao: largura maxima; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 40px auto; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: {gutter}; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  background: var(--card-bg); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 16px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: 0 10px 30px var(--shadow-color); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim main */
h1, h2, h3, h4, h5, h6 {{ /* Funcao: estilo geral de titulos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-family: {heading_font}; /* Funcao: familia de fontes; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--text-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  margin-top: 2em; /* Funcao: margin-top propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  margin-bottom: 0.6em; /* Funcao: margin-bottom propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  line-height: 1.35; /* Funcao: altura de linha, melhorar legibilidade; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim h1, h2, h3, h4, h5, h6 */
h2 {{ /* Funcao: h2 area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-size: 1.9rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim h2 */
h3 {{ /* Funcao: h3 area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-size: 1.4rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim h3 */
h4 {{ /* Funcao: h4 area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-size: 1.2rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim h4 */
p {{ /* Funcao: estilo de paragrafo; Config: ajustar propriedades relevantes dentro deste bloco */
  margin: 1em 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  text-align: justify; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim p */
ul, ol {{ /* Funcao: estilo de lista; Config: ajustar propriedades relevantes dentro deste bloco */
  margin-left: 1.5em; /* Funcao: margin-left propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  padding-left: 0; /* Funcao: padding esquerdo/indentacao; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim ul, ol */
img, canvas, svg {{ /* Funcao: limitacao de dimensoes de elementos de midia; Config: ajustar propriedades relevantes dentro deste bloco */
  max-width: 100%; /* Funcao: largura maxima; Config: ajustar valores/cores/variaveis conforme necessario */
  height: auto; /* Funcao: configuracao de altura; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim img, canvas, svg */
.meta-card {{ /* Funcao: cartao de metainformacao; Config: ajustar propriedades relevantes dentro deste bloco */
  background: rgba(0,0,0,0.02); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 12px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 20px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid var(--border-color); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .meta-card */
.meta-card ul {{ /* Funcao: .meta-card ul area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  list-style: none; /* Funcao: estilo de lista; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 0; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .meta-card ul */
.meta-card li {{ /* Funcao: .meta-card li area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  justify-content: space-between; /* Funcao: alinhamento do eixo principal flex; Config: ajustar valores/cores/variaveis conforme necessario */
  border-bottom: 1px dashed var(--border-color); /* Funcao: borda inferior; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 8px 0; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .meta-card li */
.toc {{ /* Funcao: container do Sumario; Config: ajustar propriedades relevantes dentro deste bloco */
  margin-top: 30px; /* Funcao: margin-top propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid var(--border-color); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 12px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 20px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  background: rgba(0,0,0,0.01); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .toc */
.toc-title {{ /* Funcao: .toc-title area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-weight: 600; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  margin-bottom: 10px; /* Funcao: margin-bottom propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .toc-title */
.toc ul {{ /* Funcao: .toc ul area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  list-style: none; /* Funcao: estilo de lista; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 0; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .toc ul */
.toc li {{ /* Funcao: .toc li area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  margin: 4px 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .toc li */
.toc li.level-1 {{ /* Funcao: .toc li.level-1 area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-size: 1.05rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 600; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  margin-top: 12px; /* Funcao: margin-top propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .toc li.level-1 */
.toc li.level-2 {{ /* Funcao: .toc li.level-2 area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  margin-left: 12px; /* Funcao: margin-left propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .toc li.level-2 */
.toc li a {{ /* Funcao: .toc li a area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  color: var(--primary-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  text-decoration: none; /* Funcao: decoracao de texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .toc li a */
.toc li.level-3 {{ /* Funcao: .toc li.level-3 area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  margin-left: 16px; /* Funcao: margin-left propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.95em; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .toc li.level-3 */
.toc-desc {{ /* Funcao: .toc-desc area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  margin: 2px 0 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--secondary-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.9rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .toc-desc */
.toc-desc {{ /* Funcao: .toc-desc area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  margin: 2px 0 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--secondary-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.9rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .toc-desc */
.chapter {{ /* Funcao: container de capitulo; Config: ajustar propriedades relevantes dentro deste bloco */
  margin-top: 40px; /* Funcao: margin-top propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  padding-top: 32px; /* Funcao: padding-top propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  border-top: 1px solid rgba(0,0,0,0.05); /* Funcao: border-top propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .chapter */
.chapter:first-of-type {{ /* Funcao: .chapter:first-of-type area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  border-top: none; /* Funcao: border-top propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  padding-top: 0; /* Funcao: padding-top propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .chapter:first-of-type */
blockquote {{ /* Funcao: bloco de citacao - estilo base PDF; Config: ajustar propriedades relevantes dentro deste bloco */
  padding: 12px 16px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  background: rgba(0,0,0,0.04); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 8px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  border-left: none; /* Funcao: remover barra colorida esquerda; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim blockquote */
/* ==================== Efeito vidro liquido do Blockquote - apenas exibicao em tela ==================== */
@media screen {{
  blockquote {{ /* Funcao: bloco de citacao vidro liquido - design transparente flutuante; Config: ajustar propriedades relevantes dentro deste bloco */
    position: relative; /* Funcao: modo de posicionamento; Config: ajustar valores/cores/variaveis conforme necessario */
    margin: 20px 0; /* Funcao: margem externa aumentada para espaco de flutuacao; Config: ajustar valores/cores/variaveis conforme necessario */
    padding: 18px 22px; /* Funcao: padding; Config: ajustar valores/cores/variaveis conforme necessario */
    border: none; /* Funcao: remover borda padrao; Config: ajustar valores/cores/variaveis conforme necessario */
    border-radius: 20px; /* Funcao: raio grande de bordapara efeito liquido; Config: ajustar valores/cores/variaveis conforme necessario */
    background: linear-gradient(135deg, rgba(255,255,255,0.15) 0%, rgba(255,255,255,0.05) 100%); /* Funcao: gradiente transparente sutil; Config: ajustar valores/cores/variaveis conforme necessario */
    backdrop-filter: blur(24px) saturate(180%); /* Funcao: desfoque forte de fundopara efeito de vidro; Config: ajustar valores/cores/variaveis conforme necessario */
    -webkit-backdrop-filter: blur(24px) saturate(180%); /* Funcao: Safari desfoque de fundo; Config: ajustar valores/cores/variaveis conforme necessario */
    box-shadow: 
      0 8px 32px rgba(0, 0, 0, 0.12),
      0 2px 8px rgba(0, 0, 0, 0.06),
      inset 0 0 0 1px rgba(255, 255, 255, 0.2),
      inset 0 2px 4px rgba(255, 255, 255, 0.15); /* Funcao: sombras em camadas para efeito de flutuacao; Config: ajustar valores/cores/variaveis conforme necessario */
    transform: translateY(0); /* Funcao: posicao inicial; Config: ajustar valores/cores/variaveis conforme necessario */
    transition: transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.4s ease; /* Funcao: animacao de transicao elastica; Config: ajustar valores/cores/variaveis conforme necessario */
    overflow: visible; /* Funcao: permitir overflow de efeito de luz; Config: ajustar valores/cores/variaveis conforme necessario */
    isolation: isolate; /* Funcao: criar contexto de empilhamento; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim blockquote vidro liquidobase */
  blockquote:hover {{ /* Funcao: efeito de flutuacao intensificado ao passar o mouse; Config: ajustar propriedades relevantes dentro deste bloco */
    transform: translateY(-3px); /* Funcao: efeito de flutuacao; Config: ajustar valores/cores/variaveis conforme necessario */
    box-shadow: 
      0 16px 48px rgba(0, 0, 0, 0.15),
      0 4px 16px rgba(0, 0, 0, 0.08),
      inset 0 0 0 1px rgba(255, 255, 255, 0.25),
      inset 0 2px 6px rgba(255, 255, 255, 0.2); /* Funcao: sombra intensificada; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim blockquote:hover */
  blockquote::after {{ /* Funcao: reflexo de destaque superior; Config: ajustar propriedades relevantes dentro deste bloco */
    content: ''; /* Funcao: conteudo do pseudo-elemento; Config: ajustar valores/cores/variaveis conforme necessario */
    position: absolute; /* Funcao: modo de posicionamento; Config: ajustar valores/cores/variaveis conforme necessario */
    top: 0; /* Funcao: posicao superior; Config: ajustar valores/cores/variaveis conforme necessario */
    left: 0; /* Funcao: posicao esquerda; Config: ajustar valores/cores/variaveis conforme necessario */
    right: 0; /* Funcao: posicao direita; Config: ajustar valores/cores/variaveis conforme necessario */
    height: 50%; /* Funcao: cobrir metade superior; Config: ajustar valores/cores/variaveis conforme necessario */
    background: linear-gradient(180deg, rgba(255,255,255,0.15) 0%, transparent 100%); /* Funcao: gradiente de destaque superior; Config: ajustar valores/cores/variaveis conforme necessario */
    border-radius: 20px 20px 0 0; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
    pointer-events: none; /* Funcao: nao responder ao mouse; Config: ajustar valores/cores/variaveis conforme necessario */
    z-index: -1; /* Funcao: posicionar abaixo do conteudo; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim blockquote::after */
  /* modo escuro blockquote vidro liquido */
  .dark-mode blockquote {{ /* Funcao: bloco de citacao vidro liquido em modo escuro; Config: ajustar propriedades relevantes dentro deste bloco */
    background: linear-gradient(135deg, rgba(255,255,255,0.08) 0%, rgba(255,255,255,0.02) 100%); /* Funcao: gradiente transparente escuro; Config: ajustar valores/cores/variaveis conforme necessario */
    box-shadow: 
      0 8px 32px rgba(0, 0, 0, 0.4),
      0 2px 8px rgba(0, 0, 0, 0.2),
      inset 0 0 0 1px rgba(255, 255, 255, 0.1),
      inset 0 2px 4px rgba(255, 255, 255, 0.05); /* Funcao: sombra escura; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .dark-mode blockquote */
  .dark-mode blockquote:hover {{ /* Funcao: efeito de hover escuro; Config: ajustar propriedades relevantes dentro deste bloco */
    box-shadow: 
      0 20px 56px rgba(0, 0, 0, 0.5),
      0 6px 20px rgba(0, 0, 0, 0.25),
      inset 0 0 0 1px rgba(255, 255, 255, 0.15),
      inset 0 2px 6px rgba(255, 255, 255, 0.08); /* Funcao: escurosombra intensificada; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .dark-mode blockquote:hover */
  .dark-mode blockquote::after {{ /* Funcao: destaque superior escuro; Config: ajustar propriedades relevantes dentro deste bloco */
    background: linear-gradient(180deg, rgba(255,255,255,0.06) 0%, transparent 100%); /* Funcao: escurodestaque; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .dark-mode blockquote::after */
}} /* fim @media screen blockquote vidro liquido */
.engine-quote {{ /* Funcao: bloco de fala do motor; Config: ajustar propriedades relevantes dentro deste bloco */
  --engine-quote-bg: var(--engine-insight-bg); /* Funcao: temavariaveis engine-quote-bg; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-quote-border: var(--engine-insight-border); /* Funcao: temavariaveis engine-quote-border; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-quote-text: var(--engine-insight-text); /* Funcao: temavariaveis engine-quote-text; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  margin: 22px 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 16px 18px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 14px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid var(--engine-quote-border); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  background: var(--engine-quote-bg); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: var(--engine-quote-shadow); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
  line-height: 1.65; /* Funcao: altura de linha, melhorar legibilidade; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .engine-quote */
.engine-quote__header {{ /* Funcao: .engine-quote__header area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  align-items: center; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 10px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 650; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--engine-quote-text); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  margin-bottom: 8px; /* Funcao: margin-bottom propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  letter-spacing: 0.02em; /* Funcao: espacamento entre caracteres; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .engine-quote__header */
.engine-quote__dot {{ /* Funcao: .engine-quote__dot area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  width: 10px; /* Funcao: configuracao de largura; Config: ajustar valores/cores/variaveis conforme necessario */
  height: 10px; /* Funcao: configuracao de altura; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 50%; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  background: var(--engine-quote-text); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: 0 0 0 8px rgba(0,0,0,0.02); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .engine-quote__dot */
.engine-quote__title {{ /* Funcao: .engine-quote__title area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-size: 0.98rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .engine-quote__title */
.engine-quote__body > *:first-child {{ margin-top: 0; }} /* Funcao: .engine-quote__body > * propriedade de estilo; Config: /cor/variaveis */
.engine-quote__body > *:last-child {{ margin-bottom: 0; }} /* Funcao: .engine-quote__body > * propriedade de estilo; Config: /cor/variaveis */
.engine-quote.engine-media {{ /* Funcao: .engine-quote.engine-media area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  --engine-quote-bg: var(--engine-media-bg); /* Funcao: temavariaveis engine-quote-bg; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-quote-border: var(--engine-media-border); /* Funcao: temavariaveis engine-quote-border; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-quote-text: var(--engine-media-text); /* Funcao: temavariaveis engine-quote-text; Config: sobrescrever em themeTokens ou alterar este valor padrao */
}} /* fim .engine-quote.engine-media */
.engine-quote.engine-query {{ /* Funcao: .engine-quote.engine-query area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  --engine-quote-bg: var(--engine-query-bg); /* Funcao: temavariaveis engine-quote-bg; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-quote-border: var(--engine-query-border); /* Funcao: temavariaveis engine-quote-border; Config: sobrescrever em themeTokens ou alterar este valor padrao */
  --engine-quote-text: var(--engine-query-text); /* Funcao: temavariaveis engine-quote-text; Config: sobrescrever em themeTokens ou alterar este valor padrao */
}} /* fim .engine-quote.engine-query */
.table-wrap {{ /* Funcao: container de rolagem de tabela; Config: ajustar propriedades relevantes dentro deste bloco */
  overflow-x: auto; /* Funcao: tratamento de overflow horizontal; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 20px 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .table-wrap */
table {{ /* Funcao: estilo base de tabela; Config: ajustar propriedades relevantes dentro deste bloco */
  width: 100%; /* Funcao: configuracao de largura; Config: ajustar valores/cores/variaveis conforme necessario */
  border-collapse: collapse; /* Funcao: border-collapse propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim table */
table th, table td {{ /* Funcao: celula de tabela; Config: ajustar propriedades relevantes dentro deste bloco */
  padding: 12px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid var(--border-color); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim table th, table td */
table th {{ /* Funcao: table th area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  background: rgba(0,0,0,0.03); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim table th */
.align-center {{ text-align: center; }} /* Funcao: .align-center  text-align propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.align-right {{ text-align: right; }} /* Funcao: .align-right  text-align propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-card {{ /* Funcao: container de cartao SWOT; Config: ajustar propriedades relevantes dentro deste bloco */
  margin: 26px 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 18px 18px 14px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 16px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid var(--swot-card-border); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  background: var(--swot-card-bg); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: var(--swot-card-shadow); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--swot-text); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  backdrop-filter: var(--swot-card-blur); /* Funcao: desfoque de fundo; Config: ajustar valores/cores/variaveis conforme necessario */
  position: relative; /* Funcao: modo de posicionamento; Config: ajustar valores/cores/variaveis conforme necessario */
  overflow: hidden; /* Funcao: tratamento de overflow; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-card */
.swot-card__head {{ /* Funcao: .swot-card__head area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  justify-content: space-between; /* Funcao: alinhamento do eixo principal flex; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 16px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  align-items: flex-start; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
  flex-wrap: wrap; /* Funcao: estrategia de quebra de linha; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-card__head */
.swot-card__title {{ /* Funcao: .swot-card__title area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-size: 1.15rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 750; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  margin-bottom: 4px; /* Funcao: margin-bottom propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-card__title */
.swot-card__summary {{ /* Funcao: .swot-card__summary area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  margin: 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--swot-text); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  opacity: 0.82; /* Funcao: opacidade; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-card__summary */
.swot-legend {{ /* Funcao: .swot-legend area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 8px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  flex-wrap: wrap; /* Funcao: estrategia de quebra de linha; Config: ajustar valores/cores/variaveis conforme necessario */
  align-items: center; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-legend */
.swot-legend__item {{ /* Funcao: .swot-legend__item area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  padding: 6px 12px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 999px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 700; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--swot-on-dark); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid var(--swot-tag-border); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: 0 4px 12px rgba(0,0,0,0.16); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
  text-shadow: 0 1px 2px rgba(0,0,0,0.35); /* Funcao: sombra de texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-legend__item */
.swot-legend__item.strength {{ background: var(--swot-strength); }} /* Funcao: .swot-legend__item.strength  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-legend__item.weakness {{ background: var(--swot-weakness); }} /* Funcao: .swot-legend__item.weakness  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-legend__item.opportunity {{ background: var(--swot-opportunity); }} /* Funcao: .swot-legend__item.opportunity  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-legend__item.threat {{ background: var(--swot-threat); }} /* Funcao: .swot-legend__item.threat  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-grid {{ /* Funcao: SWOT Quadrantegrade; Config: ajustar propriedades relevantes dentro deste bloco */
  display: grid; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); /* Funcao: modelo de colunas da grade; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 12px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  margin-top: 14px; /* Funcao: margin-top propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-grid */
.swot-cell {{ /* Funcao: SWOT Quadrantecelula; Config: ajustar propriedades relevantes dentro deste bloco */
  border-radius: 14px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid var(--swot-cell-border); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 12px 12px 10px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  background: var(--swot-cell-base); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.4); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-cell */
.swot-cell.strength {{ border-color: var(--swot-cell-strength-border); background: var(--swot-cell-strength-bg); }} /* Funcao: .swot-cell.strength  border-color propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-cell.weakness {{ border-color: var(--swot-cell-weakness-border); background: var(--swot-cell-weakness-bg); }} /* Funcao: .swot-cell.weakness  border-color propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-cell.opportunity {{ border-color: var(--swot-cell-opportunity-border); background: var(--swot-cell-opportunity-bg); }} /* Funcao: .swot-cell.opportunity  border-color propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-cell.threat {{ border-color: var(--swot-cell-threat-border); background: var(--swot-cell-threat-bg); }} /* Funcao: .swot-cell.threat  border-color propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-cell__meta {{ /* Funcao: .swot-cell__meta area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 10px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  align-items: flex-start; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
  margin-bottom: 8px; /* Funcao: margin-bottom propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-cell__meta */
.swot-pill {{ /* Funcao: .swot-pill area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: inline-flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  align-items: center; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
  justify-content: center; /* Funcao: alinhamento do eixo principal flex; Config: ajustar valores/cores/variaveis conforme necessario */
  width: 36px; /* Funcao: configuracao de largura; Config: ajustar valores/cores/variaveis conforme necessario */
  height: 36px; /* Funcao: configuracao de altura; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 12px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 800; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--swot-on-dark); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid var(--swot-tag-border); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: 0 8px 20px rgba(0,0,0,0.18); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-pill */
.swot-pill.strength {{ background: var(--swot-strength); }} /* Funcao: .swot-pill.strength  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-pill.weakness {{ background: var(--swot-weakness); }} /* Funcao: .swot-pill.weakness  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-pill.opportunity {{ background: var(--swot-opportunity); }} /* Funcao: .swot-pill.opportunity  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-pill.threat {{ background: var(--swot-threat); }} /* Funcao: .swot-pill.threat  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-cell__title {{ /* Funcao: .swot-cell__title area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-weight: 750; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  letter-spacing: 0.01em; /* Funcao: espacamento entre caracteres; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--swot-text); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-cell__title */
.swot-cell__caption {{ /* Funcao: .swot-cell__caption area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-size: 0.9rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--swot-text); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  opacity: 0.7; /* Funcao: opacidade; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-cell__caption */
.swot-list {{ /* Funcao: lista de itens SWOT; Config: ajustar propriedades relevantes dentro deste bloco */
  list-style: none; /* Funcao: estilo de lista; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 0; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  flex-direction: column; /* Funcao: flex direcao do eixo principal; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 8px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-list */
.swot-item {{ /* Funcao: item SWOT; Config: ajustar propriedades relevantes dentro deste bloco */
  padding: 10px 12px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 12px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  background: var(--swot-surface); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid var(--swot-item-border); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: 0 12px 22px rgba(0,0,0,0.08); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-item */
.swot-item-title {{ /* Funcao: .swot-item-title area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  justify-content: space-between; /* Funcao: alinhamento do eixo principal flex; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 8px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 650; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--swot-text); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-item-title */
.swot-item-tags {{ /* Funcao: .swot-item-tags area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: inline-flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 6px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  flex-wrap: wrap; /* Funcao: estrategia de quebra de linha; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.85rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-item-tags */
.swot-tag {{ /* Funcao: .swot-tag area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: inline-block; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 4px 8px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 10px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  background: var(--swot-chip-bg); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--swot-text); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid var(--swot-tag-border); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: 0 6px 14px rgba(0,0,0,0.12); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
  line-height: 1.2; /* Funcao: altura de linha, melhorar legibilidade; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-tag */
.swot-tag.neutral {{ /* Funcao: .swot-tag.neutral area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  opacity: 0.9; /* Funcao: opacidade; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-tag.neutral */
.swot-item-desc {{ /* Funcao: .swot-item-desc area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  margin-top: 4px; /* Funcao: margin-top propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--swot-text); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  opacity: 0.92; /* Funcao: opacidade; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-item-desc */
.swot-item-evidence {{ /* Funcao: .swot-item-evidence area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  margin-top: 4px; /* Funcao: margin-top propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.9rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--secondary-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  opacity: 0.94; /* Funcao: opacidade; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-item-evidence */
.swot-empty {{ /* Funcao: .swot-empty area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  padding: 12px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 12px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px dashed var(--swot-card-border); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  text-align: center; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--swot-muted); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  opacity: 0.7; /* Funcao: opacidade; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-empty */

/* ========== SWOT Estilos de layout de tabela PDF (oculto por padrao)========== */
.swot-pdf-wrapper {{ /* Funcao: SWOT PDF tabelacontainer; Config: ajustar propriedades relevantes dentro deste bloco */
  display: none; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-pdf-wrapper */

/* SWOT Definicao de estilos de tabela PDF (exibido na renderizacao PDF) */
.swot-pdf-table {{ /* Funcao: .swot-pdf-table area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  width: 100%; /* Funcao: configuracao de largura; Config: ajustar valores/cores/variaveis conforme necessario */
  border-collapse: collapse; /* Funcao: border-collapse propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 20px 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 13px; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  table-layout: fixed; /* Funcao: algoritmo de layout de tabela; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-pdf-table */
.swot-pdf-caption {{ /* Funcao: .swot-pdf-caption area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  caption-side: top; /* Funcao: caption-side propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  text-align: left; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 1.15rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 700; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 12px 0; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--text-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-pdf-caption */
.swot-pdf-thead th {{ /* Funcao: .swot-pdf-thead th area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  background: #f8f9fa; /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 10px 8px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  text-align: left; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 600; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid #dee2e6; /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  color: #495057; /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-pdf-thead th */
.swot-pdf-th-quadrant {{ width: 80px; }} /* Funcao: .swot-pdf-th-quadrant  width propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-pdf-th-num {{ width: 50px; text-align: center; }} /* Funcao: .swot-pdf-th-num  width propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-pdf-th-title {{ width: 22%; }} /* Funcao: .swot-pdf-th-title  width propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-pdf-th-detail {{ width: auto; }} /* Funcao: .swot-pdf-th-detail  width propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-pdf-th-tags {{ width: 100px; text-align: center; }} /* Funcao: .swot-pdf-th-tags  width propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-pdf-summary {{ /* Funcao: .swot-pdf-summary area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  padding: 12px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  background: #f8f9fa; /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  color: #666; /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  font-style: italic; /* Funcao: font-style propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid #dee2e6; /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-pdf-summary */
.swot-pdf-quadrant {{ /* Funcao: .swot-pdf-quadrant area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  break-inside: avoid; /* Funcao: break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  page-break-inside: avoid; /* Funcao: page-break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-pdf-quadrant */
.swot-pdf-quadrant-label {{ /* Funcao: .swot-pdf-quadrant-label area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  text-align: center; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
  vertical-align: middle; /* Funcao: vertical-align propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 12px 8px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 700; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid #dee2e6; /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  writing-mode: horizontal-tb; /* Funcao: writing-mode propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-pdf-quadrant-label */
.swot-pdf-quadrant-label.swot-pdf-strength {{ background: rgba(28,127,110,0.15); color: #1c7f6e; border-left: 4px solid #1c7f6e; }} /* Funcao: .swot-pdf-quadrant-label.swot-pdf-strength  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-pdf-quadrant-label.swot-pdf-weakness {{ background: rgba(192,57,43,0.12); color: #c0392b; border-left: 4px solid #c0392b; }} /* Funcao: .swot-pdf-quadrant-label.swot-pdf-weakness  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-pdf-quadrant-label.swot-pdf-opportunity {{ background: rgba(31,90,179,0.12); color: #1f5ab3; border-left: 4px solid #1f5ab3; }} /* Funcao: .swot-pdf-quadrant-label.swot-pdf-opportunity  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-pdf-quadrant-label.swot-pdf-threat {{ background: rgba(179,107,22,0.12); color: #b36b16; border-left: 4px solid #b36b16; }} /* Funcao: .swot-pdf-quadrant-label.swot-pdf-threat  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-pdf-code {{ /* Funcao: .swot-pdf-code area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: block; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 1.5rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 800; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  margin-bottom: 4px; /* Funcao: margin-bottom propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-pdf-code */
.swot-pdf-label-text {{ /* Funcao: .swot-pdf-label-text area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: block; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.75rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 600; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  letter-spacing: 0.02em; /* Funcao: espacamento entre caracteres; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-pdf-label-text */
.swot-pdf-item-row td {{ /* Funcao: .swot-pdf-item-row td area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  padding: 10px 8px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid #dee2e6; /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  vertical-align: top; /* Funcao: vertical-align propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-pdf-item-row td */
.swot-pdf-item-row.swot-pdf-strength td {{ background: rgba(28,127,110,0.03); }} /* Funcao: .swot-pdf-item-row.swot-pdf-strength td  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-pdf-item-row.swot-pdf-weakness td {{ background: rgba(192,57,43,0.03); }} /* Funcao: .swot-pdf-item-row.swot-pdf-weakness td  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-pdf-item-row.swot-pdf-opportunity td {{ background: rgba(31,90,179,0.03); }} /* Funcao: .swot-pdf-item-row.swot-pdf-opportunity td  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-pdf-item-row.swot-pdf-threat td {{ background: rgba(179,107,22,0.03); }} /* Funcao: .swot-pdf-item-row.swot-pdf-threat td  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.swot-pdf-item-num {{ /* Funcao: .swot-pdf-item-num area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  text-align: center; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 600; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: #6c757d; /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-pdf-item-num */
.swot-pdf-item-title {{ /* Funcao: .swot-pdf-item-title area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-weight: 600; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: #212529; /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-pdf-item-title */
.swot-pdf-item-detail {{ /* Funcao: .swot-pdf-item-detail area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  color: #495057; /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  line-height: 1.5; /* Funcao: altura de linha, melhorar legibilidade; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-pdf-item-detail */
.swot-pdf-item-tags {{ /* Funcao: .swot-pdf-item-tags area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  text-align: center; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-pdf-item-tags */
.swot-pdf-tag {{ /* Funcao: .swot-pdf-tag area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: inline-block; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 3px 8px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 4px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.75rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  background: #e9ecef; /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  color: #495057; /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 2px; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-pdf-tag */
.swot-pdf-tag--score {{ /* Funcao: .swot-pdf-tag--score area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  background: #fff3cd; /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  color: #856404; /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-pdf-tag--score */
.swot-pdf-empty {{ /* Funcao: .swot-pdf-empty area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  text-align: center; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
  color: #adb5bd; /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  font-style: italic; /* Funcao: font-style propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .swot-pdf-empty */

/* Controle de paginacao SWOT no modo de impressao (mantendo suporte a impressao do layout de cartoes) */
@media print {{ /* Funcao: estilos do modo de impressao; Config: ajustar propriedades relevantes dentro deste bloco */
  .swot-card {{ /* Funcao: container de cartao SWOT; Config: ajustar propriedades relevantes dentro deste bloco */
    break-inside: auto; /* Funcao: break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-inside: auto; /* Funcao: page-break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .swot-card */
  .swot-card__head {{ /* Funcao: .swot-card__head area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
    break-after: avoid; /* Funcao: break-after propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-after: avoid; /* Funcao: page-break-after propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .swot-card__head */
  .swot-pdf-quadrant {{ /* Funcao: .swot-pdf-quadrant area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
    break-inside: avoid; /* Funcao: break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-inside: avoid; /* Funcao: page-break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .swot-pdf-quadrant */
}} /* fim @media print */

/* ==================== Analise PESTestilo ==================== */
.pest-card {{ /* Funcao: PEST container de cartao; Config: ajustar propriedades relevantes dentro deste bloco */
  margin: 28px 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 20px 20px 16px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 18px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid var(--pest-card-border); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  background: var(--pest-card-bg); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: var(--pest-card-shadow); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--pest-text); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  backdrop-filter: var(--pest-card-blur); /* Funcao: desfoque de fundo; Config: ajustar valores/cores/variaveis conforme necessario */
  position: relative; /* Funcao: modo de posicionamento; Config: ajustar valores/cores/variaveis conforme necessario */
  overflow: hidden; /* Funcao: tratamento de overflow; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-card */
.pest-card__head {{ /* Funcao: .pest-card__head area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  justify-content: space-between; /* Funcao: alinhamento do eixo principal flex; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 16px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  align-items: flex-start; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
  flex-wrap: wrap; /* Funcao: estrategia de quebra de linha; Config: ajustar valores/cores/variaveis conforme necessario */
  margin-bottom: 16px; /* Funcao: margin-bottom propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-card__head */
.pest-card__title {{ /* Funcao: .pest-card__title area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-size: 1.18rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 750; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  margin-bottom: 4px; /* Funcao: margin-bottom propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  background: linear-gradient(135deg, var(--pest-political), var(--pest-technological)); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  -webkit-background-clip: text; /* Funcao: -webkit-background-clip propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  -webkit-text-fill-color: transparent; /* Funcao: -webkit-text-fill-color propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  background-clip: text; /* Funcao: background-clip propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-card__title */
.pest-card__summary {{ /* Funcao: .pest-card__summary area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  margin: 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--pest-text); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  opacity: 0.8; /* Funcao: opacidade; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-card__summary */
.pest-legend {{ /* Funcao: .pest-legend area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 8px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  flex-wrap: wrap; /* Funcao: estrategia de quebra de linha; Config: ajustar valores/cores/variaveis conforme necessario */
  align-items: center; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-legend */
.pest-legend__item {{ /* Funcao: .pest-legend__item area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  padding: 6px 14px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 8px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 700; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.85rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--pest-on-dark); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid var(--pest-tag-border); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: 0 4px 14px rgba(0,0,0,0.18); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
  text-shadow: 0 1px 2px rgba(0,0,0,0.3); /* Funcao: sombra de texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-legend__item */
.pest-legend__item.political {{ background: var(--pest-political); }} /* Funcao: .pest-legend__item.political  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-legend__item.economic {{ background: var(--pest-economic); }} /* Funcao: .pest-legend__item.economic  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-legend__item.social {{ background: var(--pest-social); }} /* Funcao: .pest-legend__item.social  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-legend__item.technological {{ background: var(--pest-technological); }} /* Funcao: .pest-legend__item.technological  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-strips {{ /* Funcao: PEST container de faixas; Config: ajustar propriedades relevantes dentro deste bloco */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  flex-direction: column; /* Funcao: flex direcao do eixo principal; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 14px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-strips */
.pest-strip {{ /* Funcao: PEST faixa; Config: ajustar propriedades relevantes dentro deste bloco */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 14px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid var(--pest-strip-border); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  background: var(--pest-strip-base); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  overflow: hidden; /* Funcao: tratamento de overflow; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: 0 6px 16px rgba(0,0,0,0.06); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
  transition: transform 0.2s ease, box-shadow 0.2s ease; /* Funcao: duracao/propriedade de animacao de transicao; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-strip */
.pest-strip:hover {{ /* Funcao: .pest-strip:hover area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  transform: translateY(-2px); /* Funcao: transform propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: 0 10px 24px rgba(0,0,0,0.1); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-strip:hover */
.pest-strip.political {{ border-color: var(--pest-strip-political-border); background: var(--pest-strip-political-bg); }} /* Funcao: .pest-strip.political  border-color propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-strip.economic {{ border-color: var(--pest-strip-economic-border); background: var(--pest-strip-economic-bg); }} /* Funcao: .pest-strip.economic  border-color propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-strip.social {{ border-color: var(--pest-strip-social-border); background: var(--pest-strip-social-bg); }} /* Funcao: .pest-strip.social  border-color propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-strip.technological {{ border-color: var(--pest-strip-technological-border); background: var(--pest-strip-technological-bg); }} /* Funcao: .pest-strip.technological  border-color propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-strip__indicator {{ /* Funcao: .pest-strip__indicator area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  align-items: center; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
  justify-content: center; /* Funcao: alinhamento do eixo principal flex; Config: ajustar valores/cores/variaveis conforme necessario */
  width: 56px; /* Funcao: configuracao de largura; Config: ajustar valores/cores/variaveis conforme necessario */
  min-width: 56px; /* Funcao: largura minima; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 16px 8px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--pest-on-dark); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  text-shadow: 0 2px 4px rgba(0,0,0,0.25); /* Funcao: sombra de texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-strip__indicator */
.pest-strip__indicator.political {{ background: linear-gradient(180deg, var(--pest-political), rgba(142,68,173,0.8)); }} /* Funcao: .pest-strip__indicator.political  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-strip__indicator.economic {{ background: linear-gradient(180deg, var(--pest-economic), rgba(22,160,133,0.8)); }} /* Funcao: .pest-strip__indicator.economic  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-strip__indicator.social {{ background: linear-gradient(180deg, var(--pest-social), rgba(232,67,147,0.8)); }} /* Funcao: .pest-strip__indicator.social  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-strip__indicator.technological {{ background: linear-gradient(180deg, var(--pest-technological), rgba(41,128,185,0.8)); }} /* Funcao: .pest-strip__indicator.technological  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-code {{ /* Funcao: .pest-code area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-size: 1.6rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 900; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  letter-spacing: 0.02em; /* Funcao: espacamento entre caracteres; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-code */
.pest-strip__content {{ /* Funcao: .pest-strip__content area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  flex: 1; /* Funcao: proporcao de ocupacao flex; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 14px 16px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  min-width: 0; /* Funcao: largura minima; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-strip__content */
.pest-strip__header {{ /* Funcao: .pest-strip__header area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  justify-content: space-between; /* Funcao: alinhamento do eixo principal flex; Config: ajustar valores/cores/variaveis conforme necessario */
  align-items: baseline; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 12px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  margin-bottom: 10px; /* Funcao: margin-bottom propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  flex-wrap: wrap; /* Funcao: estrategia de quebra de linha; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-strip__header */
.pest-strip__title {{ /* Funcao: .pest-strip__title area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-weight: 700; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 1rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--pest-text); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-strip__title */
.pest-strip__caption {{ /* Funcao: .pest-strip__caption area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-size: 0.85rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--pest-text); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  opacity: 0.65; /* Funcao: opacidade; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-strip__caption */
.pest-list {{ /* Funcao: PEST lista de itens; Config: ajustar propriedades relevantes dentro deste bloco */
  list-style: none; /* Funcao: estilo de lista; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 0; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  flex-direction: column; /* Funcao: flex direcao do eixo principal; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 8px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-list */
.pest-item {{ /* Funcao: PEST item; Config: ajustar propriedades relevantes dentro deste bloco */
  padding: 10px 14px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 10px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  background: var(--pest-surface); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid var(--pest-item-border); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: 0 8px 18px rgba(0,0,0,0.06); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-item */
.pest-item-title {{ /* Funcao: .pest-item-title area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  justify-content: space-between; /* Funcao: alinhamento do eixo principal flex; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 8px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 650; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--pest-text); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-item-title */
.pest-item-tags {{ /* Funcao: .pest-item-tags area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: inline-flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 6px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  flex-wrap: wrap; /* Funcao: estrategia de quebra de linha; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.82rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-item-tags */
.pest-tag {{ /* Funcao: .pest-tag area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: inline-block; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 3px 8px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 6px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  background: var(--pest-chip-bg); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--pest-text); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid var(--pest-tag-border); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: 0 4px 10px rgba(0,0,0,0.08); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
  line-height: 1.2; /* Funcao: altura de linha, melhorar legibilidade; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-tag */
.pest-item-desc {{ /* Funcao: .pest-item-desc area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  margin-top: 5px; /* Funcao: margin-top propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--pest-text); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  opacity: 0.88; /* Funcao: opacidade; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.95rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-item-desc */
.pest-item-source {{ /* Funcao: .pest-item-source area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  margin-top: 4px; /* Funcao: margin-top propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.88rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--secondary-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  opacity: 0.9; /* Funcao: opacidade; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-item-source */
.pest-empty {{ /* Funcao: .pest-empty area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  padding: 14px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 10px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px dashed var(--pest-card-border); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  text-align: center; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--pest-muted); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  opacity: 0.65; /* Funcao: opacidade; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-empty */

/* ========== PEST Estilos de layout de tabela PDF (oculto por padrao)========== */
.pest-pdf-wrapper {{ /* Funcao: PEST PDF container; Config: ajustar propriedades relevantes dentro deste bloco */
  display: none; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-pdf-wrapper */

/* PEST Definicao de estilos de tabela PDF (exibido na renderizacao PDF) */
.pest-pdf-table {{ /* Funcao: .pest-pdf-table area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  width: 100%; /* Funcao: configuracao de largura; Config: ajustar valores/cores/variaveis conforme necessario */
  border-collapse: collapse; /* Funcao: border-collapse propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 20px 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 13px; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  table-layout: fixed; /* Funcao: algoritmo de layout de tabela; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-pdf-table */
.pest-pdf-caption {{ /* Funcao: .pest-pdf-caption area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  caption-side: top; /* Funcao: caption-side propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  text-align: left; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 1.15rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 700; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 12px 0; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--text-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-pdf-caption */
.pest-pdf-thead th {{ /* Funcao: .pest-pdf-thead th area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  background: #f5f3f7; /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 10px 8px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  text-align: left; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 600; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid #e0dce3; /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  color: #4a4458; /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-pdf-thead th */
.pest-pdf-th-dimension {{ width: 85px; }} /* Funcao: .pest-pdf-th-dimension  width propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-pdf-th-num {{ width: 50px; text-align: center; }} /* Funcao: .pest-pdf-th-num  width propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-pdf-th-title {{ width: 22%; }} /* Funcao: .pest-pdf-th-title  width propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-pdf-th-detail {{ width: auto; }} /* Funcao: .pest-pdf-th-detail  width propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-pdf-th-tags {{ width: 100px; text-align: center; }} /* Funcao: .pest-pdf-th-tags  width propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-pdf-summary {{ /* Funcao: .pest-pdf-summary area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  padding: 12px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  background: #f8f6fa; /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  color: #666; /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  font-style: italic; /* Funcao: font-style propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid #e0dce3; /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-pdf-summary */
.pest-pdf-dimension {{ /* Funcao: .pest-pdf-dimension area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  break-inside: avoid; /* Funcao: break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  page-break-inside: avoid; /* Funcao: page-break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-pdf-dimension */
.pest-pdf-dimension-label {{ /* Funcao: .pest-pdf-dimension-label area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  text-align: center; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
  vertical-align: middle; /* Funcao: vertical-align propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 12px 8px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 700; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid #e0dce3; /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  writing-mode: horizontal-tb; /* Funcao: writing-mode propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-pdf-dimension-label */
.pest-pdf-dimension-label.pest-pdf-political {{ background: rgba(142,68,173,0.12); color: #8e44ad; border-left: 4px solid #8e44ad; }} /* Funcao: .pest-pdf-dimension-label.pest-pdf-political  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-pdf-dimension-label.pest-pdf-economic {{ background: rgba(22,160,133,0.12); color: #16a085; border-left: 4px solid #16a085; }} /* Funcao: .pest-pdf-dimension-label.pest-pdf-economic  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-pdf-dimension-label.pest-pdf-social {{ background: rgba(232,67,147,0.12); color: #e84393; border-left: 4px solid #e84393; }} /* Funcao: .pest-pdf-dimension-label.pest-pdf-social  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-pdf-dimension-label.pest-pdf-technological {{ background: rgba(41,128,185,0.12); color: #2980b9; border-left: 4px solid #2980b9; }} /* Funcao: .pest-pdf-dimension-label.pest-pdf-technological  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-pdf-code {{ /* Funcao: .pest-pdf-code area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: block; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 1.5rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 800; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  margin-bottom: 4px; /* Funcao: margin-bottom propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-pdf-code */
.pest-pdf-label-text {{ /* Funcao: .pest-pdf-label-text area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: block; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.75rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 600; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  letter-spacing: 0.02em; /* Funcao: espacamento entre caracteres; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-pdf-label-text */
.pest-pdf-item-row td {{ /* Funcao: .pest-pdf-item-row td area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  padding: 10px 8px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid #e0dce3; /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  vertical-align: top; /* Funcao: vertical-align propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-pdf-item-row td */
.pest-pdf-item-row.pest-pdf-political td {{ background: rgba(142,68,173,0.03); }} /* Funcao: .pest-pdf-item-row.pest-pdf-political td  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-pdf-item-row.pest-pdf-economic td {{ background: rgba(22,160,133,0.03); }} /* Funcao: .pest-pdf-item-row.pest-pdf-economic td  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-pdf-item-row.pest-pdf-social td {{ background: rgba(232,67,147,0.03); }} /* Funcao: .pest-pdf-item-row.pest-pdf-social td  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-pdf-item-row.pest-pdf-technological td {{ background: rgba(41,128,185,0.03); }} /* Funcao: .pest-pdf-item-row.pest-pdf-technological td  background propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.pest-pdf-item-num {{ /* Funcao: .pest-pdf-item-num area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  text-align: center; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 600; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: #6c757d; /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-pdf-item-num */
.pest-pdf-item-title {{ /* Funcao: .pest-pdf-item-title area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-weight: 600; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: #212529; /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-pdf-item-title */
.pest-pdf-item-detail {{ /* Funcao: .pest-pdf-item-detail area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  color: #495057; /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  line-height: 1.5; /* Funcao: altura de linha, melhorar legibilidade; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-pdf-item-detail */
.pest-pdf-item-tags {{ /* Funcao: .pest-pdf-item-tags area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  text-align: center; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-pdf-item-tags */
.pest-pdf-tag {{ /* Funcao: .pest-pdf-tag area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: inline-block; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 3px 8px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 4px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.75rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  background: #ece9f1; /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  color: #5a4f6a; /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 2px; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-pdf-tag */
.pest-pdf-empty {{ /* Funcao: .pest-pdf-empty area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  text-align: center; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
  color: #adb5bd; /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  font-style: italic; /* Funcao: font-style propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .pest-pdf-empty */

/* Controle de paginacao PEST no modo de impressao */
@media print {{ /* Funcao: estilos do modo de impressao; Config: ajustar propriedades relevantes dentro deste bloco */
  .pest-card {{ /* Funcao: PEST container de cartao; Config: ajustar propriedades relevantes dentro deste bloco */
    break-inside: auto; /* Funcao: break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-inside: auto; /* Funcao: page-break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .pest-card */
  .pest-card__head {{ /* Funcao: .pest-card__head area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
    break-after: avoid; /* Funcao: break-after propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-after: avoid; /* Funcao: page-break-after propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .pest-card__head */
  .pest-pdf-dimension {{ /* Funcao: .pest-pdf-dimension area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
    break-inside: avoid; /* Funcao: break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-inside: avoid; /* Funcao: page-break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .pest-pdf-dimension */
  .pest-strip {{ /* Funcao: PEST faixa; Config: ajustar propriedades relevantes dentro deste bloco */
    break-inside: avoid; /* Funcao: break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-inside: avoid; /* Funcao: page-break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .pest-strip */
}} /* fim @media print */
.callout {{ /* Funcao: caixa de destaque - PDFbaseestilo; Config: ajustar propriedades relevantes dentro deste bloco */
  padding: 16px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 8px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 20px 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  background: rgba(0,0,0,0.02); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  border-left: none; /* Funcao: remover barra colorida esquerda; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .callout */
.callout.tone-warning {{ border-color: #ff9800; }} /* Funcao: .callout.tone-warning  border-color propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.callout.tone-success {{ border-color: #2ecc71; }} /* Funcao: .callout.tone-success  border-color propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.callout.tone-danger {{ border-color: #e74c3c; }} /* Funcao: .callout.tone-danger  border-color propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
/* ==================== Efeito vidro liquido do Callout - apenas exibicao em tela ==================== */
@media screen {{
  .callout {{ /* Funcao: caixa de destaque vidro liquido - design transparente flutuante; Config: ajustar propriedades relevantes dentro deste bloco */
    --callout-accent: var(--primary-color); /* Funcao: callout cor principal; Config: ajustar valores/cores/variaveis conforme necessario */
    --callout-glow-color: rgba(0, 123, 255, 0.35); /* Funcao: callout cor de brilho; Config: ajustar valores/cores/variaveis conforme necessario */
    position: relative; /* Funcao: modo de posicionamento; Config: ajustar valores/cores/variaveis conforme necessario */
    margin: 24px 0; /* Funcao: aumentar margem externa para efeito de flutuacao; Config: ajustar valores/cores/variaveis conforme necessario */
    padding: 20px 24px; /* Funcao: padding; Config: ajustar valores/cores/variaveis conforme necessario */
    border: none; /* Funcao: remover borda padrao; Config: ajustar valores/cores/variaveis conforme necessario */
    border-radius: 24px; /* Funcao: raio grande de bordapara efeito liquido; Config: ajustar valores/cores/variaveis conforme necessario */
    background: linear-gradient(135deg, rgba(255,255,255,0.12) 0%, rgba(255,255,255,0.04) 100%); /* Funcao: gradiente transparente sutil; Config: ajustar valores/cores/variaveis conforme necessario */
    backdrop-filter: blur(28px) saturate(200%); /* Funcao: desfoque forte de fundopara efeito de vidro; Config: ajustar valores/cores/variaveis conforme necessario */
    -webkit-backdrop-filter: blur(28px) saturate(200%); /* Funcao: Safari desfoque de fundo; Config: ajustar valores/cores/variaveis conforme necessario */
    box-shadow: 
      0 12px 40px rgba(0, 0, 0, 0.1),
      0 4px 12px rgba(0, 0, 0, 0.05),
      inset 0 0 0 1.5px rgba(255, 255, 255, 0.18),
      inset 0 2px 6px rgba(255, 255, 255, 0.12); /* Funcao: sombras em camadas para efeito de flutuacao; Config: ajustar valores/cores/variaveis conforme necessario */
    transform: translateY(0); /* Funcao: posicao inicial; Config: ajustar valores/cores/variaveis conforme necessario */
    transition: transform 0.45s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.45s ease; /* Funcao: animacao de transicao elastica; Config: ajustar valores/cores/variaveis conforme necessario */
    overflow: hidden; /* Funcao: ocultar conteudo excedente; Config: ajustar valores/cores/variaveis conforme necessario */
    isolation: isolate; /* Funcao: criar contexto de empilhamento; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .callout vidro liquidobase */
  .callout:hover {{ /* Funcao: efeito de flutuacao intensificado ao passar o mouse; Config: ajustar propriedades relevantes dentro deste bloco */
    transform: translateY(-4px); /* Funcao: efeito de flutuacao; Config: ajustar valores/cores/variaveis conforme necessario */
    box-shadow: 
      0 20px 56px rgba(0, 0, 0, 0.12),
      0 8px 20px rgba(0, 0, 0, 0.06),
      inset 0 0 0 1.5px rgba(255, 255, 255, 0.22),
      inset 0 3px 8px rgba(255, 255, 255, 0.15); /* Funcao: sombra intensificada; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .callout:hover */
  .callout::after {{ /* Funcao: reflexo de destaque superior em arco; Config: ajustar propriedades relevantes dentro deste bloco */
    content: ''; /* Funcao: conteudo do pseudo-elemento; Config: ajustar valores/cores/variaveis conforme necessario */
    position: absolute; /* Funcao: modo de posicionamento; Config: ajustar valores/cores/variaveis conforme necessario */
    top: 0; /* Funcao: posicao superior; Config: ajustar valores/cores/variaveis conforme necessario */
    left: 0; /* Funcao: posicao esquerda; Config: ajustar valores/cores/variaveis conforme necessario */
    right: 0; /* Funcao: posicao direita; Config: ajustar valores/cores/variaveis conforme necessario */
    height: 55%; /* Funcao: cobrir metade superior; Config: ajustar valores/cores/variaveis conforme necessario */
    background: linear-gradient(180deg, rgba(255,255,255,0.18) 0%, rgba(255,255,255,0.03) 60%, transparent 100%); /* Funcao: gradiente de destaque superior; Config: ajustar valores/cores/variaveis conforme necessario */
    border-radius: 24px 24px 0 0; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
    pointer-events: none; /* Funcao: nao responder ao mouse; Config: ajustar valores/cores/variaveis conforme necessario */
    z-index: -1; /* Funcao: posicionar abaixo do conteudo; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .callout::after */
  /* Callout tone variantes - diferentescorbrilho */
  .callout.tone-info {{ /* Funcao: callout tipo informacao; Config: ajustar propriedades relevantes dentro deste bloco */
    --callout-accent: #3b82f6; /* Funcao: azul informativotom; Config: ajustar valores/cores/variaveis conforme necessario */
    --callout-glow-color: rgba(59, 130, 246, 0.4); /* Funcao: azul informativobrilho; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .callout.tone-info */
  .callout.tone-warning {{ /* Funcao: callout tipo aviso; Config: ajustar propriedades relevantes dentro deste bloco */
    --callout-accent: #f59e0b; /* Funcao: laranja de avisotom; Config: ajustar valores/cores/variaveis conforme necessario */
    --callout-glow-color: rgba(245, 158, 11, 0.4); /* Funcao: laranja de avisobrilho; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .callout.tone-warning */
  .callout.tone-success {{ /* Funcao: callout tipo sucesso; Config: ajustar propriedades relevantes dentro deste bloco */
    --callout-accent: #10b981; /* Funcao: verde de sucessotom; Config: ajustar valores/cores/variaveis conforme necessario */
    --callout-glow-color: rgba(16, 185, 129, 0.4); /* Funcao: verde de sucessobrilho; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .callout.tone-success */
  .callout.tone-danger {{ /* Funcao: callout tipo perigo; Config: ajustar propriedades relevantes dentro deste bloco */
    --callout-accent: #ef4444; /* Funcao: vermelho de perigotom; Config: ajustar valores/cores/variaveis conforme necessario */
    --callout-glow-color: rgba(239, 68, 68, 0.4); /* Funcao: vermelho de perigobrilho; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .callout.tone-danger */
  /* modo escuro callout vidro liquido */
  .dark-mode .callout {{ /* Funcao: modo escuro callout vidro liquido; Config: ajustar propriedades relevantes dentro deste bloco */
    background: linear-gradient(135deg, rgba(255,255,255,0.06) 0%, rgba(255,255,255,0.01) 100%); /* Funcao: gradiente transparente escuro; Config: ajustar valores/cores/variaveis conforme necessario */
    box-shadow: 
      0 12px 40px rgba(0, 0, 0, 0.35),
      0 4px 12px rgba(0, 0, 0, 0.18),
      inset 0 0 0 1.5px rgba(255, 255, 255, 0.08),
      inset 0 2px 6px rgba(255, 255, 255, 0.04); /* Funcao: sombra escura; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .dark-mode .callout */
  .dark-mode .callout:hover {{ /* Funcao: efeito de hover escuro; Config: ajustar propriedades relevantes dentro deste bloco */
    box-shadow: 
      0 24px 64px rgba(0, 0, 0, 0.45),
      0 10px 28px rgba(0, 0, 0, 0.22),
      inset 0 0 0 1.5px rgba(255, 255, 255, 0.12),
      inset 0 3px 8px rgba(255, 255, 255, 0.06); /* Funcao: escurosombra intensificada; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .dark-mode .callout:hover */
  .dark-mode .callout::after {{ /* Funcao: destaque superior escuro; Config: ajustar propriedades relevantes dentro deste bloco */
    background: linear-gradient(180deg, rgba(255,255,255,0.08) 0%, rgba(255,255,255,0.01) 50%, transparent 100%); /* Funcao: escurodestaque; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .dark-mode .callout::after */
  /* modo escurobrilhocorintensificada */
  .dark-mode .callout.tone-info {{ /* Funcao: escuroinformacaotipo; Config: ajustar propriedades relevantes dentro deste bloco */
    --callout-glow-color: rgba(96, 165, 250, 0.5); /* Funcao: escuroinformacaobrilho; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .dark-mode .callout.tone-info */
  .dark-mode .callout.tone-warning {{ /* Funcao: escuroAviso(s)tipo; Config: ajustar propriedades relevantes dentro deste bloco */
    --callout-glow-color: rgba(251, 191, 36, 0.5); /* Funcao: escuroAviso(s)brilho; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .dark-mode .callout.tone-warning */
  .dark-mode .callout.tone-success {{ /* Funcao: escurosucessotipo; Config: ajustar propriedades relevantes dentro deste bloco */
    --callout-glow-color: rgba(52, 211, 153, 0.5); /* Funcao: escurosucessobrilho; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .dark-mode .callout.tone-success */
  .dark-mode .callout.tone-danger {{ /* Funcao: escuroperigotipo; Config: ajustar propriedades relevantes dentro deste bloco */
    --callout-glow-color: rgba(248, 113, 113, 0.5); /* Funcao: escuroperigobrilho; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .dark-mode .callout.tone-danger */
}} /* fim @media screen callout vidro liquido */
.kpi-grid {{ /* Funcao: container de grade KPI; Config: ajustar propriedades relevantes dentro deste bloco */
  display: grid; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); /* Funcao: modelo de colunas da grade; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 16px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 20px 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .kpi-grid */
.kpi-card {{ /* Funcao: cartao KPI; Config: ajustar propriedades relevantes dentro deste bloco */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  flex-direction: column; /* Funcao: flex direcao do eixo principal; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 8px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 16px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 12px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  background: rgba(0,0,0,0.02); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid var(--border-color); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  align-items: flex-start; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .kpi-card */
.kpi-value {{ /* Funcao: .kpi-value area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-size: 2rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 700; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  flex-wrap: nowrap; /* Funcao: estrategia de quebra de linha; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 4px 6px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  line-height: 1.25; /* Funcao: altura de linha, melhorar legibilidade; Config: ajustar valores/cores/variaveis conforme necessario */
  word-break: break-word; /* Funcao: regra de quebra de palavra; Config: ajustar valores/cores/variaveis conforme necessario */
  overflow-wrap: break-word; /* Funcao: quebra de palavras longas; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .kpi-value */
.kpi-value small {{ /* Funcao: .kpi-value small area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-size: 0.65em; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  align-self: baseline; /* Funcao: align-self propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  white-space: nowrap; /* Funcao: espaco em branco e estrategia de quebra de linha; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .kpi-value small */
.kpi-label {{ /* Funcao: .kpi-label area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  color: var(--secondary-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  line-height: 1.35; /* Funcao: altura de linha, melhorar legibilidade; Config: ajustar valores/cores/variaveis conforme necessario */
  word-break: break-word; /* Funcao: regra de quebra de palavra; Config: ajustar valores/cores/variaveis conforme necessario */
  overflow-wrap: break-word; /* Funcao: quebra de palavras longas; Config: ajustar valores/cores/variaveis conforme necessario */
  max-width: 100%; /* Funcao: largura maxima; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .kpi-label */
.delta.up {{ color: #27ae60; }} /* Funcao: .delta.up  color propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.delta.down {{ color: #e74c3c; }} /* Funcao: .delta.down  color propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.delta.neutral {{ color: var(--secondary-color); }} /* Funcao: .delta.neutral  color propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
.delta {{ /* Funcao: .delta area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: block; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  line-height: 1.3; /* Funcao: altura de linha, melhorar legibilidade; Config: ajustar valores/cores/variaveis conforme necessario */
  word-break: break-word; /* Funcao: regra de quebra de palavra; Config: ajustar valores/cores/variaveis conforme necessario */
  overflow-wrap: break-word; /* Funcao: quebra de palavras longas; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .delta */
.chart-card {{ /* Funcao: Graficocontainer de cartao; Config: ajustar propriedades relevantes dentro deste bloco */
  margin: 30px 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 20px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid var(--border-color); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 12px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  background: rgba(0,0,0,0.01); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .chart-card */
.chart-card.chart-card--error {{ /* Funcao: .chart-card.chart-card--error area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  border-style: dashed; /* Funcao: border-style propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  background: linear-gradient(135deg, rgba(0,0,0,0.015), rgba(0,0,0,0.04)); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .chart-card.chart-card--error */
.chart-error {{ /* Funcao: .chart-error area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 12px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 14px 12px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 10px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  align-items: flex-start; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
  background: rgba(0,0,0,0.03); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--secondary-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .chart-error */
.chart-error__icon {{ /* Funcao: .chart-error__icon area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  width: 28px; /* Funcao: configuracao de largura; Config: ajustar valores/cores/variaveis conforme necessario */
  height: 28px; /* Funcao: configuracao de altura; Config: ajustar valores/cores/variaveis conforme necessario */
  flex-shrink: 0; /* Funcao: flex-shrink propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 50%; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  display: inline-flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  align-items: center; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
  justify-content: center; /* Funcao: alinhamento do eixo principal flex; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 700; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--secondary-color-dark); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  background: rgba(0,0,0,0.06); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.9rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .chart-error__icon */
.chart-error__title {{ /* Funcao: .chart-error__title area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  font-weight: 600; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--text-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .chart-error__title */
.chart-error__desc {{ /* Funcao: .chart-error__desc area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  margin: 4px 0 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--secondary-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  line-height: 1.6; /* Funcao: altura de linha, melhorar legibilidade; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .chart-error__desc */
.chart-card.wordcloud-card .chart-container {{ /* Funcao: .chart-card.wordcloud-card .chart-container area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  min-height: 180px; /* Funcao: altura minima, evitar colapso; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .chart-card.wordcloud-card .chart-container */
.chart-container {{ /* Funcao: Grafico canvas container; Config: ajustar propriedades relevantes dentro deste bloco */
  position: relative; /* Funcao: modo de posicionamento; Config: ajustar valores/cores/variaveis conforme necessario */
  min-height: 220px; /* Funcao: altura minima, evitar colapso; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .chart-container */
.chart-fallback {{ /* Funcao: Graficotabela; Config: ajustar propriedades relevantes dentro deste bloco */
  display: none; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  margin-top: 12px; /* Funcao: margin-top propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.85rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  overflow-x: auto; /* Funcao: tratamento de overflow horizontal; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .chart-fallback */
.no-js .chart-fallback {{ /* Funcao: .no-js .chart-fallback area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: block; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .no-js .chart-fallback */
.no-js .chart-container {{ /* Funcao: .no-js .chart-container area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: none; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .no-js .chart-container */
.chart-fallback table {{ /* Funcao: .chart-fallback table area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  width: 100%; /* Funcao: configuracao de largura; Config: ajustar valores/cores/variaveis conforme necessario */
  border-collapse: collapse; /* Funcao: border-collapse propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .chart-fallback table */
.chart-fallback th,
.chart-fallback td {{ /* Funcao: .chart-fallback td area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  border: 1px solid var(--border-color); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 6px 8px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  text-align: left; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .chart-fallback td */
.chart-fallback th {{ /* Funcao: .chart-fallback th area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  background: rgba(0,0,0,0.04); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .chart-fallback th */
.wordcloud-fallback .wordcloud-badges {{ /* Funcao: .wordcloud-fallback .wordcloud-badges area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  flex-wrap: wrap; /* Funcao: estrategia de quebra de linha; Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 6px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  margin-top: 6px; /* Funcao: margin-top propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .wordcloud-fallback .wordcloud-badges */
.wordcloud-badge {{ /* Funcao: emblema de nuvem de palavras; Config: ajustar propriedades relevantes dentro deste bloco */
  display: inline-flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  align-items: center; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
  gap: 4px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 4px 8px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 999px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px solid rgba(74, 144, 226, 0.35); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--text-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  background: linear-gradient(135deg, rgba(74, 144, 226, 0.14) 0%, rgba(74, 144, 226, 0.24) 100%); /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  box-shadow: 0 4px 10px rgba(15, 23, 42, 0.06); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .wordcloud-badge */
.dark-mode .wordcloud-badge {{ /* Funcao: .dark-mode .wordcloud-badge area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.35); /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .dark-mode .wordcloud-badge */
.wordcloud-badge small {{ /* Funcao: .wordcloud-badge small area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  color: var(--secondary-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  font-weight: 600; /* Funcao: peso da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.75rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .wordcloud-badge small */
.chart-note {{ /* Funcao: aviso de degradacao do grafico; Config: ajustar propriedades relevantes dentro deste bloco */
  margin-top: 8px; /* Funcao: margin-top propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.85rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--secondary-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .chart-note */
figure {{ /* Funcao: figure area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  margin: 20px 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
  text-align: center; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim figure */
figure img {{ /* Funcao: figure img area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  max-width: 100%; /* Funcao: largura maxima; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 12px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim figure img */
.figure-placeholder {{ /* Funcao: .figure-placeholder area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  padding: 16px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border: 1px dashed var(--border-color); /* Funcao: estilo de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 12px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  color: var(--secondary-color); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  text-align: center; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 0.95rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 20px 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .figure-placeholder */
.math-block {{ /* Funcao: formula em nivel de bloco; Config: ajustar propriedades relevantes dentro deste bloco */
  text-align: center; /* Funcao: alinhamento de texto; Config: ajustar valores/cores/variaveis conforme necessario */
  font-size: 1.1rem; /* Funcao: tamanho da fonte; Config: ajustar valores/cores/variaveis conforme necessario */
  margin: 24px 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .math-block */
.math-inline {{ /* Funcao: formula inline; Config: ajustar propriedades relevantes dentro deste bloco */
  font-family: {fonts.get("heading", fonts.get("body", "sans-serif"))}; /* Funcao: familia de fontes; Config: ajustar valores/cores/variaveis conforme necessario */
  font-style: italic; /* Funcao: font-style propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  white-space: nowrap; /* Funcao: espaco em branco e estrategia de quebra de linha; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 0 0.15em; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .math-inline */
pre.code-block {{ /* Funcao: bloco de codigo; Config: ajustar propriedades relevantes dentro deste bloco */
  background: #1e1e1e; /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  color: #fff; /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
  padding: 16px; /* Funcao: padding, controla distancia entre conteudo e borda do container; Config: ajustar valores/cores/variaveis conforme necessario */
  border-radius: 12px; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  overflow-x: auto; /* Funcao: tratamento de overflow horizontal; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim pre.code-block */
@media (max-width: 768px) {{ /* Funcao: breakpoint mobileestilo; Config: ajustar propriedades relevantes dentro deste bloco */
  .report-header {{ /* Funcao: area de cabecalho fixo no topo; Config: ajustar propriedades relevantes dentro deste bloco */
    flex-direction: column; /* Funcao: flex direcao do eixo principal; Config: ajustar valores/cores/variaveis conforme necessario */
    align-items: flex-start; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .report-header */
  main {{ /* Funcao: container de conteudo principal; Config: ajustar propriedades relevantes dentro deste bloco */
    margin: 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
    border-radius: 0; /* Funcao: raio de borda; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim main */
}} /* fim @media (max-width: 768px) */
@media print {{ /* Funcao: estilos do modo de impressao; Config: ajustar propriedades relevantes dentro deste bloco */
  .no-print {{ display: none !important; }} /* Funcao: .no-print  display propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  body {{ /* Funcao: configuracao global de tipografia e fundo; Config: ajustar propriedades relevantes dentro deste bloco */
    background: #fff; /* Funcao: cor de fundo ou efeito gradiente; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim body */
  main {{ /* Funcao: container de conteudo principal; Config: ajustar propriedades relevantes dentro deste bloco */
    box-shadow: none; /* Funcao: efeito de sombra; Config: ajustar valores/cores/variaveis conforme necessario */
    margin: 0; /* Funcao: margem externa, controla distancia de elementos vizinhos; Config: ajustar valores/cores/variaveis conforme necessario */
    max-width: 100%; /* Funcao: largura maxima; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim main */
  .chapter > *,
  .hero-section,
  .callout,
  .engine-quote,
  .chart-card,
  .kpi-grid,
.swot-card,
.pest-card,
.table-wrap,
figure,
blockquote {{ /* Funcao: bloco de citacao; Config: ajustar propriedades relevantes dentro deste bloco */
  break-inside: avoid; /* Funcao: break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-inside: avoid; /* Funcao: page-break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    max-width: 100%; /* Funcao: largura maxima; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim blockquote */
  .chapter h2,
  .chapter h3,
  .chapter h4 {{ /* Funcao: .chapter h4 area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
    break-after: avoid; /* Funcao: break-after propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-after: avoid; /* Funcao: page-break-after propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    break-inside: avoid; /* Funcao: break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .chapter h4 */
  .chart-card,
  .table-wrap {{ /* Funcao: container de rolagem de tabela; Config: ajustar propriedades relevantes dentro deste bloco */
    overflow: visible !important; /* Funcao: tratamento de overflow; Config: ajustar valores/cores/variaveis conforme necessario */
    max-width: 100% !important; /* Funcao: largura maxima; Config: ajustar valores/cores/variaveis conforme necessario */
    box-sizing: border-box; /* Funcao: modo de calculo de dimensoes; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .table-wrap */
  .chart-card canvas {{ /* Funcao: .chart-card canvas area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
    width: 100% !important; /* Funcao: configuracao de largura; Config: ajustar valores/cores/variaveis conforme necessario */
    height: auto !important; /* Funcao: configuracao de altura; Config: ajustar valores/cores/variaveis conforme necessario */
    max-width: 100% !important; /* Funcao: largura maxima; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .chart-card canvas */
  .swot-card,
  .swot-cell {{ /* Funcao: SWOT Quadrantecelula; Config: ajustar propriedades relevantes dentro deste bloco */
    break-inside: avoid; /* Funcao: break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-inside: avoid; /* Funcao: page-break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .swot-cell */
  .swot-card {{ /* Funcao: container de cartao SWOT; Config: ajustar propriedades relevantes dentro deste bloco */
    color: var(--swot-text); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
    /* Permitir paginacao interna do cartao, evitando que todo o bloco va para a proxima pagina */
    break-inside: auto !important; /* Funcao: break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-inside: auto !important; /* Funcao: page-break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .swot-card */
  .swot-card__head {{ /* Funcao: .swot-card__head area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
    break-after: avoid; /* Funcao: break-after propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-after: avoid; /* Funcao: page-break-after propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .swot-card__head */
  .swot-grid {{ /* Funcao: SWOT Quadrantegrade; Config: ajustar propriedades relevantes dentro deste bloco */
    break-before: avoid; /* Funcao: break-before propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-before: avoid; /* Funcao: page-break-before propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    break-inside: auto; /* Funcao: break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-inside: auto; /* Funcao: page-break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    display: flex; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
    flex-wrap: wrap; /* Funcao: estrategia de quebra de linha; Config: ajustar valores/cores/variaveis conforme necessario */
    gap: 10px; /* Funcao: espacamento entre elementos filhos; Config: ajustar valores/cores/variaveis conforme necessario */
    align-items: stretch; /* Funcao: alinhamento flex (eixo cruzado); Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .swot-grid */
  .swot-grid .swot-cell {{ /* Funcao: .swot-grid .swot-cell area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
    break-inside: avoid; /* Funcao: break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-inside: avoid; /* Funcao: page-break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .swot-grid .swot-cell */
  .swot-legend {{ /* Funcao: .swot-legend area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
    display: none !important; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .swot-legend */
  .swot-grid .swot-cell {{ /* Funcao: .swot-grid .swot-cell area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
    flex: 1 1 320px; /* Funcao: proporcao de ocupacao flex; Config: ajustar valores/cores/variaveis conforme necessario */
    min-width: 240px; /* Funcao: largura minima; Config: ajustar valores/cores/variaveis conforme necessario */
    height: auto; /* Funcao: configuracao de altura; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .swot-grid .swot-cell */
  /* PEST impressaoestilo */
  .pest-card,
  .pest-strip {{ /* Funcao: PEST faixa; Config: ajustar propriedades relevantes dentro deste bloco */
    break-inside: avoid; /* Funcao: break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-inside: avoid; /* Funcao: page-break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .pest-strip */
  .pest-card {{ /* Funcao: PEST container de cartao; Config: ajustar propriedades relevantes dentro deste bloco */
    color: var(--pest-text); /* Funcao: cor do texto; Config: ajustar valores/cores/variaveis conforme necessario */
    break-inside: auto !important; /* Funcao: break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-inside: auto !important; /* Funcao: page-break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .pest-card */
  .pest-card__head {{ /* Funcao: .pest-card__head area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
    break-after: avoid; /* Funcao: break-after propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-after: avoid; /* Funcao: page-break-after propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .pest-card__head */
  .pest-strips {{ /* Funcao: PEST container de faixas; Config: ajustar propriedades relevantes dentro deste bloco */
    break-before: avoid; /* Funcao: break-before propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-before: avoid; /* Funcao: page-break-before propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    break-inside: auto; /* Funcao: break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
    page-break-inside: auto; /* Funcao: page-break-inside propriedade de estilo; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .pest-strips */
  .pest-legend {{ /* Funcao: .pest-legend area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
    display: none !important; /* Funcao: modo de exibicao de layout; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .pest-legend */
  .pest-strip {{ /* Funcao: PEST faixa; Config: ajustar propriedades relevantes dentro deste bloco */
    flex-direction: row; /* Funcao: flex direcao do eixo principal; Config: ajustar valores/cores/variaveis conforme necessario */
  }} /* fim .pest-strip */
.table-wrap {{ /* Funcao: container de rolagem de tabela; Config: ajustar propriedades relevantes dentro deste bloco */
  overflow-x: auto; /* Funcao: tratamento de overflow horizontal; Config: ajustar valores/cores/variaveis conforme necessario */
  max-width: 100%; /* Funcao: largura maxima; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .table-wrap */
.table-wrap table {{ /* Funcao: .table-wrap table area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  table-layout: fixed; /* Funcao: algoritmo de layout de tabela; Config: ajustar valores/cores/variaveis conforme necessario */
  width: 100%; /* Funcao: configuracao de largura; Config: ajustar valores/cores/variaveis conforme necessario */
  max-width: 100%; /* Funcao: largura maxima; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .table-wrap table */
.table-wrap table th,
.table-wrap table td {{ /* Funcao: .table-wrap table td area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  word-break: break-word; /* Funcao: regra de quebra de palavra; Config: ajustar valores/cores/variaveis conforme necessario */
  overflow-wrap: break-word; /* Funcao: quebra de palavras longas; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim .table-wrap table td */
/* Evitar overflow de imagens e graficos */
img, canvas, svg {{ /* Funcao: limitacao de dimensoes de elementos de midia; Config: ajustar propriedades relevantes dentro deste bloco */
  max-width: 100% !important; /* Funcao: largura maxima; Config: ajustar valores/cores/variaveis conforme necessario */
  height: auto !important; /* Funcao: configuracao de altura; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim img, canvas, svg */
/* Garantir que todos os containers nao excedam a largura da pagina */
* {{ /* Funcao: * area de estilos; Config: ajustar propriedades relevantes dentro deste bloco */
  box-sizing: border-box; /* Funcao: modo de calculo de dimensoes; Config: ajustar valores/cores/variaveis conforme necessario */
  max-width: 100%; /* Funcao: largura maxima; Config: ajustar valores/cores/variaveis conforme necessario */
}} /* fim * */
}} /* fim @media print */

"""

    def _hydration_script(self) -> str:
        """
        Retorna JS do rodape da pagina, responsavel por hidratacao Chart.js, renderizacao de nuvem de palavras e interacao de botoes.

        Organizacao das camadas de interacao:
        1) Troca de tema (#theme-toggle): escuta evento change do componente personalizado, detail e 'light'/'dark',
           Funcao: alternar body.dark-mode, atualizar cores de Chart.js e nuvem de palavras.
        2) Botao de impressao (#print-btn): aciona window.print(), layout controlado por CSS @media print.
        3) Botao de exportacao (#export-btn): chama exportPdf(), usando html2canvas + jsPDF internamente,
           e exibe #export-overlay (overlay, texto de status, barra de progresso).
        4) Hidratacao de graficos: varre todos os canvas com data-config-id, analisa JSON adjacente, instancia Chart.js;
           tabela/emblema de nuvem de palavras， data-chart-state。
        5) Resize da janela: apos debounce, redesenha nuvem de palavras, garantindo responsividade.
        """
        return """
<script>
document.documentElement.classList.remove('no-js');
document.documentElement.classList.add('js-ready');

/* ========== Theme Button Web Component (comentado, substituido por estilo action-btn) ========== */
/*
(() => {
  const themeButtonFunc = (root, initTheme, changeTheme) => {
    const checkbox = root.querySelector('.theme-checkbox');
    // Inicializar estado
    if (initTheme === 'dark') {
      checkbox.checked = true;
    }
    // Interacao principal: alternar dark/light via checkbox, sincronizar tema externamente via callback changeTheme
    checkbox.addEventListener('change', (e) => {
      const isDark = e.target.checked;
      changeTheme(isDark ? 'dark' : 'light');
    });
  };

  class ThemeButton extends HTMLElement {
    constructor() { super(); }
    connectedCallback() {
      const initTheme = this.getAttribute("value") || "light";
      const size = +this.getAttribute("size") || 1.5;
      
      const shadow = this.attachShadow({ mode: "closed" });
      const container = document.createElement("div");
      container.setAttribute("class", "container");
      container.style.fontSize = `${size * 10}px`;

      // Estrutura do componente: checkbox + label, label contem ceu/estrelas/nuvens e lua, visualmente e um botao de alternancia de tema
      container.innerHTML = [
        '<div class="toggle-wrapper">',
        '  <input type="checkbox" class="theme-checkbox" id="theme-toggle-input">',
        '  <label for="theme-toggle-input" class="toggle-label">',
        '    <div class="toggle-background">',
        '      <div class="stars">',
        '        <span class="star"></span>',
        '        <span class="star"></span>',
        '        <span class="star"></span>',
        '        <span class="star"></span>',
        '      </div>',
        '      <div class="clouds">',
        '        <span class="cloud"></span>',
        '        <span class="cloud"></span>',
        '      </div>',
        '    </div>',
        '    <div class="toggle-circle">',
        '      <div class="moon-crater"></div>',
        '      <div class="moon-crater"></div>',
        '      <div class="moon-crater"></div>',
        '    </div>',
        '  </label>',
        '</div>'
      ].join('');

      const style = document.createElement("style");
      style.textContent = [
        '* { box-sizing: border-box; margin: 0; padding: 0; }',
        '.container { display: inline-block; position: relative; width: 5.4em; height: 2.6em; vertical-align: middle; }',
        '.toggle-wrapper { width: 100%; height: 100%; }',
        '.theme-checkbox { display: none; }',
        '.toggle-label { display: block; width: 100%; height: 100%; border-radius: 2.6em; background-color: #87CEEB; cursor: pointer; position: relative; overflow: hidden; transition: background-color 0.5s ease; box-shadow: inset 0 0.1em 0.3em rgba(0,0,0,0.2); }',
        '.theme-checkbox:checked + .toggle-label { background-color: #1F2937; }',
        '.toggle-circle { position: absolute; top: 0.2em; left: 0.2em; width: 2.2em; height: 2.2em; border-radius: 50%; background-color: #FFD700; box-shadow: 0 0.1em 0.2em rgba(0,0,0,0.3); transition: transform 0.5s cubic-bezier(0.4, 0.0, 0.2, 1), background-color 0.5s ease; z-index: 2; }',
        '.theme-checkbox:checked + .toggle-label .toggle-circle { transform: translateX(2.8em); background-color: #F3F4F6; box-shadow: inset -0.2em -0.2em 0.2em rgba(0,0,0,0.1), 0 0.1em 0.2em rgba(255,255,255,0.2); }',
        '.moon-crater { position: absolute; background-color: rgba(200, 200, 200, 0.6); border-radius: 50%; opacity: 0; transition: opacity 0.3s ease; }',
        '.theme-checkbox:checked + .toggle-label .toggle-circle .moon-crater { opacity: 1; }',
        '.moon-crater:nth-child(1) { width: 0.6em; height: 0.6em; top: 0.4em; left: 0.8em; }',
        '.moon-crater:nth-child(2) { width: 0.4em; height: 0.4em; top: 1.2em; left: 0.4em; }',
        '.moon-crater:nth-child(3) { width: 0.3em; height: 0.3em; top: 1.4em; left: 1.2em; }',
        '.toggle-background { position: absolute; top: 0; left: 0; width: 100%; height: 100%; }',
        '.clouds { position: absolute; width: 100%; height: 100%; transition: transform 0.5s ease, opacity 0.5s ease; opacity: 1; }',
        '.theme-checkbox:checked + .toggle-label .clouds { transform: translateY(100%); opacity: 0; }',
        '.cloud { position: absolute; background-color: #fff; border-radius: 2em; opacity: 0.9; }',
        '.cloud::before { content: ""; position: absolute; top: -40%; left: 15%; width: 50%; height: 100%; background-color: inherit; border-radius: 50%; }',
        '.cloud::after { content: ""; position: absolute; top: -55%; left: 45%; width: 50%; height: 120%; background-color: inherit; border-radius: 50%; }',
        '.cloud:nth-child(1) { width: 1.4em; height: 0.5em; top: 0.8em; right: 1.0em; }',
        '.cloud:nth-child(2) { width: 1.0em; height: 0.4em; top: 1.6em; right: 2.0em; opacity: 0.7; }',
        '.stars { position: absolute; width: 100%; height: 100%; transition: transform 0.5s ease, opacity 0.5s ease; transform: translateY(-100%); opacity: 0; }',
        '.theme-checkbox:checked + .toggle-label .stars { transform: translateY(0); opacity: 1; }',
        '.star { position: absolute; background-color: #FFF; border-radius: 50%; width: 0.15em; height: 0.15em; box-shadow: 0 0 0.2em #FFF; animation: twinkle 2s infinite ease-in-out; }',
        '.star:nth-child(1) { top: 0.6em; left: 1.0em; animation-delay: 0s; }',
        '.star:nth-child(2) { top: 1.6em; left: 1.8em; width: 0.1em; height: 0.1em; animation-delay: 0.5s; }',
        '.star:nth-child(3) { top: 0.8em; left: 2.4em; width: 0.12em; height: 0.12em; animation-delay: 1s; }',
        '.star:nth-child(4) { top: 1.8em; left: 0.8em; width: 0.08em; height: 0.08em; animation-delay: 1.5s; }',
        '@keyframes twinkle { 0%, 100% { opacity: 0.4; transform: scale(0.8); } 50% { opacity: 1; transform: scale(1.2); } }'
      ].join(' ');

      const changeThemeWrapper = (detail) => {
        this.dispatchEvent(new CustomEvent("change", { detail }));
      };
      
      themeButtonFunc(container, initTheme, changeThemeWrapper);
      shadow.appendChild(style);
      shadow.appendChild(container);
    }
  }
  customElements.define("theme-button", ThemeButton);
})();
*/
/* ========== End Theme Button Web Component ========== */
 
 const chartRegistry = [];
const wordCloudRegistry = new Map();
const STABLE_CHART_TYPES = ['line', 'bar'];
const CHART_TYPE_LABELS = {
  line: 'Grafico de linhas',
  bar: 'Grafico de barras',
  doughnut: 'Grafico de rosca',
  pie: 'Grafico de pizza',
  radar: 'Grafico de radar',
  polarArea: 'Grafico de area polar'
};

// Regras de substituicao/clareamento de cores consistentes com renderizacao vetorial PDF
const DEFAULT_CHART_COLORS = [
  '#4A90E2', '#E85D75', '#50C878', '#FFB347',
  '#9B59B6', '#3498DB', '#E67E22', '#16A085',
  '#F39C12', '#D35400', '#27AE60', '#8E44AD'
];
const CSS_VAR_COLOR_MAP = {
  'var(--chart-color-green)': '#4BC0C0',
  'var(--chart-color-red)': '#FF6384',
  'var(--chart-color-blue)': '#36A2EB',
  'var(--color-accent)': '#4A90E2',
  'var(--re-accent-color)': '#4A90E2',
  'var(--re-accent-color-translucent)': 'rgba(74, 144, 226, 0.08)',
  'var(--color-kpi-down)': '#E85D75',
  'var(--re-danger-color)': '#E85D75',
  'var(--re-danger-color-translucent)': 'rgba(232, 93, 117, 0.08)',
  'var(--color-warning)': '#FFB347',
  'var(--re-warning-color)': '#FFB347',
  'var(--re-warning-color-translucent)': 'rgba(255, 179, 71, 0.08)',
  'var(--color-success)': '#50C878',
  'var(--re-success-color)': '#50C878',
  'var(--re-success-color-translucent)': 'rgba(80, 200, 120, 0.08)',
  'var(--color-accent-positive)': '#50C878',
  'var(--color-accent-negative)': '#E85D75',
  'var(--color-text-secondary)': '#6B7280',
  'var(--accentPositive)': '#50C878',
  'var(--accentNegative)': '#E85D75',
  'var(--sentiment-positive, #28A745)': '#28A745',
  'var(--sentiment-negative, #E53E3E)': '#E53E3E',
  'var(--sentiment-neutral, #FFC107)': '#FFC107',
  'var(--sentiment-positive)': '#28A745',
  'var(--sentiment-negative)': '#E53E3E',
  'var(--sentiment-neutral)': '#FFC107',
  'var(--color-primary)': '#3498DB',
  'var(--color-secondary)': '#95A5A6'
};
const WORDCLOUD_CATEGORY_COLORS = {
  positive: '#10b981',
  negative: '#ef4444',
  neutral: '#6b7280',
  controversial: '#f59e0b'
};

function normalizeColorToken(color) {
  if (typeof color !== 'string') return color;
  const trimmed = color.trim();
  if (!trimmed) return null;
  // Suporta formato var(--token, fallback), priorizando analise do fallback
  const varWithFallback = trimmed.match(/^var\(\s*--[^,)+]+,\s*([^)]+)\)/i);
  if (varWithFallback && varWithFallback[1]) {
    const fallback = varWithFallback[1].trim();
    const normalizedFallback = normalizeColorToken(fallback);
    if (normalizedFallback) return normalizedFallback;
  }
  if (CSS_VAR_COLOR_MAP[trimmed]) {
    return CSS_VAR_COLOR_MAP[trimmed];
  }
  if (trimmed.startsWith('var(')) {
    if (/accent|primary/i.test(trimmed)) return '#4A90E2';
    if (/danger|down|error/i.test(trimmed)) return '#E85D75';
    if (/warning/i.test(trimmed)) return '#FFB347';
    if (/success|up/i.test(trimmed)) return '#50C878';
    return '#3498DB';
  }
  return trimmed;
}

function hexToRgb(color) {
  if (typeof color !== 'string') return null;
  const normalized = color.replace('#', '');
  if (!(normalized.length === 3 || normalized.length === 6)) return null;
  const hex = normalized.length === 3 ? normalized.split('').map(c => c + c).join('') : normalized;
  const intVal = parseInt(hex, 16);
  if (Number.isNaN(intVal)) return null;
  return [(intVal >> 16) & 255, (intVal >> 8) & 255, intVal & 255];
}

function parseRgbString(color) {
  if (typeof color !== 'string') return null;
  const match = color.match(/rgba?\s*\(([^)]+)\)/i);
  if (!match) return null;
  const parts = match[1].split(',').map(p => parseFloat(p.trim())).filter(v => !Number.isNaN(v));
  if (parts.length < 3) return null;
  return [parts[0], parts[1], parts[2]].map(v => Math.max(0, Math.min(255, v)));
}

function alphaFromColor(color) {
  if (typeof color !== 'string') return null;
  const raw = color.trim();
  if (!raw) return null;
  if (raw.toLowerCase() === 'transparent') return 0;

  const extractAlpha = (source) => {
    const match = source.match(/rgba?\s*\(([^)]+)\)/i);
    if (!match) return null;
    const parts = match[1].split(',').map(p => p.trim());
    if (source.toLowerCase().startsWith('rgba') && parts.length >= 2) {
      const alphaToken = parts[parts.length - 1];
      const isPercent = /%$/.test(alphaToken);
      const alphaVal = parseFloat(alphaToken.replace('%', ''));
      if (!Number.isNaN(alphaVal)) {
        const normalizedAlpha = isPercent ? alphaVal / 100 : alphaVal;
        return Math.max(0, Math.min(1, normalizedAlpha));
      }
    }
    if (parts.length >= 3) return 1;
    return null;
  };

  const rawAlpha = extractAlpha(raw);
  if (rawAlpha !== null) return rawAlpha;

  const normalized = normalizeColorToken(raw);
  if (typeof normalized === 'string' && normalized !== raw) {
    const normalizedAlpha = extractAlpha(normalized);
    if (normalizedAlpha !== null) return normalizedAlpha;
  }

  return null;
}

function rgbFromColor(color) {
  const normalized = normalizeColorToken(color);
  return hexToRgb(normalized) || parseRgbString(normalized);
}

function colorLuminance(color) {
  const rgb = rgbFromColor(color);
  if (!rgb) return null;
  const [r, g, b] = rgb.map(v => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function lightenColor(color, ratio) {
  const rgb = rgbFromColor(color);
  if (!rgb) return color;
  const factor = Math.min(1, Math.max(0, ratio || 0.25));
  const mixed = rgb.map(v => Math.round(v + (255 - v) * factor));
  return `rgb(${mixed[0]}, ${mixed[1]}, ${mixed[2]})`;
}

function ensureAlpha(color, alpha) {
  const rgb = rgbFromColor(color);
  if (!rgb) return color;
  const clamped = Math.min(1, Math.max(0, alpha));
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${clamped})`;
}

function liftDarkColor(color) {
  const normalized = normalizeColorToken(color);
  const lum = colorLuminance(normalized);
  if (lum !== null && lum < 0.12) {
    return lightenColor(normalized, 0.35);
  }
  return normalized;
}

function mixColors(colorA, colorB, amount) {
  const rgbA = rgbFromColor(colorA);
  const rgbB = rgbFromColor(colorB);
  if (!rgbA && !rgbB) return colorA || colorB;
  if (!rgbA) return colorB;
  if (!rgbB) return colorA;
  const t = Math.min(1, Math.max(0, amount || 0));
  const mixed = rgbA.map((v, idx) => Math.round(v * (1 - t) + rgbB[idx] * t));
  return `rgb(${mixed[0]}, ${mixed[1]}, ${mixed[2]})`;
}

function pickComputedColor(keys, fallback, styles) {
  const styleRef = styles || getComputedStyle(document.body);
  for (const key of keys) {
    const val = styleRef.getPropertyValue(key);
    if (val && val.trim()) {
      const normalized = normalizeColorToken(val.trim());
      if (normalized) return normalized;
    }
  }
  return fallback;
}

function resolveWordcloudTheme() {
  const styles = getComputedStyle(document.body);
  const isDark = document.body.classList.contains('dark-mode');
  const text = pickComputedColor(['--text-color'], isDark ? '#e5e7eb' : '#111827', styles);
  const secondary = pickComputedColor(['--secondary-color', '--color-text-secondary'], isDark ? '#cbd5e1' : '#475569', styles);
  const accent = liftDarkColor(
    pickComputedColor(['--primary-color', '--color-accent', '--re-accent-color'], '#4A90E2', styles)
  );
  const cardBg = pickComputedColor(
    ['--card-bg', '--paper-bg', '--bg', '--bg-color', '--background', '--page-bg'],
    isDark ? '#0f172a' : '#ffffff',
    styles
  );
  return { text, secondary, accent, cardBg, isDark };
}

function normalizeDatasetColors(payload, chartType) {
  const changes = [];
  const data = payload && payload.data;
  if (!data || !Array.isArray(data.datasets)) {
    return changes;
  }
  const type = chartType || 'bar';
  const needsArrayColors = type === 'pie' || type === 'doughnut' || type === 'polarArea';
  const MIN_PIE_ALPHA = 0.6;
  const pickColor = (value, fallback) => {
    if (Array.isArray(value) && value.length) return value[0];
    return value || fallback;
  };

  data.datasets.forEach((dataset, idx) => {
    if (!isPlainObject(dataset)) return;
    if (type === 'line') {
      dataset.fill = true;  // Forcar preenchimento em graficos de linhas para comparacao de areas
    }
    const paletteColor = normalizeColorToken(DEFAULT_CHART_COLORS[idx % DEFAULT_CHART_COLORS.length]);
    const borderInput = dataset.borderColor;
    const backgroundInput = dataset.backgroundColor;
    const borderIsArray = Array.isArray(borderInput);
    const bgIsArray = Array.isArray(backgroundInput);
    const baseCandidate = pickColor(borderInput, pickColor(backgroundInput, dataset.color || paletteColor));
    const liftedBase = liftDarkColor(baseCandidate || paletteColor);

    if (needsArrayColors) {
      const labelCount = Array.isArray(data.labels) ? data.labels.length : 0;
      const rawColors = bgIsArray ? backgroundInput : [];
      const dataLength = Array.isArray(dataset.data) ? dataset.data.length : 0;
      const total = Math.max(labelCount, rawColors.length, dataLength, 1);
      const normalizedColors = [];
      let fixedTransparentCount = 0;
      for (let i = 0; i < total; i++) {
        const fallbackColor = DEFAULT_CHART_COLORS[(idx + i) % DEFAULT_CHART_COLORS.length];
        const normalizedRaw = normalizeColorToken(rawColors[i]);
        const alpha = alphaFromColor(normalizedRaw);
        const isInvisible = typeof normalizedRaw === 'string' && normalizedRaw.toLowerCase() === 'transparent';
        if (alpha === 0 || isInvisible) {
          fixedTransparentCount += 1;
        }
        const baseColor = (!normalizedRaw || isInvisible) ? fallbackColor : normalizedRaw;
        const targetAlpha = alpha === null ? 1 : alpha;
        const normalizedColor = ensureAlpha(
          liftDarkColor(baseColor),
          Math.max(MIN_PIE_ALPHA, targetAlpha)
        );
        normalizedColors.push(normalizedColor);
      }
      dataset.backgroundColor = normalizedColors;
      dataset.borderColor = normalizedColors.map(col => ensureAlpha(liftDarkColor(col), 1));
      const changeLabel = fixedTransparentCount
        ? `dataset${idx}: corrigido ${fixedTransparentCount} setores transparentes`
        : `dataset${idx}: cores de setor normalizadas(${normalizedColors.length})`;
      changes.push(changeLabel);
      return;
    }

    if (!borderInput) {
      dataset.borderColor = liftedBase;
      changes.push(`dataset${idx}: completar cor de borda`);
    } else if (borderIsArray) {
      dataset.borderColor = borderInput.map(col => liftDarkColor(col));
    } else {
      dataset.borderColor = liftDarkColor(borderInput);
    }

    const typeAlpha = type === 'line'
      ? (dataset.fill ? 0.25 : 0.18)
      : type === 'radar'
        ? 0.25
        : type === 'scatter' || type === 'bubble'
          ? 0.6
          : type === 'bar'
            ? 0.85
            : null;

    if (typeAlpha !== null) {
      if (bgIsArray && dataset.backgroundColor.length) {
        dataset.backgroundColor = backgroundInput.map(col => ensureAlpha(liftDarkColor(col), typeAlpha));
      } else {
        const bgSeed = pickColor(backgroundInput, pickColor(dataset.borderColor, paletteColor));
        dataset.backgroundColor = ensureAlpha(liftDarkColor(bgSeed), typeAlpha);
      }
      if (dataset.fill || type !== 'line') {
        changes.push(`dataset${idx}: aplicar preenchimento esmaecido para evitar obstrucao`);
      }
    } else if (!dataset.backgroundColor) {
      dataset.backgroundColor = ensureAlpha(liftedBase, 0.85);
    } else if (bgIsArray) {
      dataset.backgroundColor = backgroundInput.map(col => liftDarkColor(col));
    } else if (!bgIsArray) {
      dataset.backgroundColor = liftDarkColor(dataset.backgroundColor);
    }

    if (type === 'line' && !dataset.pointBackgroundColor) {
      dataset.pointBackgroundColor = Array.isArray(dataset.borderColor)
        ? dataset.borderColor[0]
        : dataset.borderColor;
    }
  });

  if (changes.length) {
    payload._colorAudit = changes;
  }
  return changes;
}

function getThemePalette() {
  const styles = getComputedStyle(document.body);
  return {
    text: styles.getPropertyValue('--text-color').trim(),
    grid: styles.getPropertyValue('--border-color').trim()
  };
}

function applyChartTheme(chart) {
  if (!chart) return;
  try {
    chart.update('none');
  } catch (err) {
    console.error('Chart refresh failed', err);
  }
}

function isPlainObject(value) {
  return Object.prototype.toString.call(value) === '[object Object]';
}

function cloneDeep(value) {
  if (Array.isArray(value)) {
    return value.map(cloneDeep);
  }
  if (isPlainObject(value)) {
    const obj = {};
    Object.keys(value).forEach(key => {
      obj[key] = cloneDeep(value[key]);
    });
    return obj;
  }
  return value;
}

function mergeOptions(base, override) {
  const result = isPlainObject(base) ? cloneDeep(base) : {};
  if (!isPlainObject(override)) {
    return result;
  }
  Object.keys(override).forEach(key => {
    const overrideValue = override[key];
    if (Array.isArray(overrideValue)) {
      result[key] = cloneDeep(overrideValue);
    } else if (isPlainObject(overrideValue)) {
      result[key] = mergeOptions(result[key], overrideValue);
    } else {
      result[key] = overrideValue;
    }
  });
  return result;
}

function resolveChartTypes(payload) {
  const explicit = payload && payload.props && payload.props.type;
  const widgetType = payload && payload.widgetType ? payload.widgetType : 'chart.js/bar';
  const derived = widgetType && widgetType.includes('/') ? widgetType.split('/').pop() : widgetType;
  const extra = Array.isArray(payload && payload.preferredTypes) ? payload.preferredTypes : [];
  const pipeline = [explicit, derived, ...extra, ...STABLE_CHART_TYPES].filter(Boolean);
  const result = [];
  pipeline.forEach(type => {
    if (type && !result.includes(type)) {
      result.push(type);
    }
  });
  return result.length ? result : ['bar'];
}

function describeChartType(type) {
  return CHART_TYPE_LABELS[type] || type || 'Grafico';
}

function setChartDegradeNote(card, fromType, toType) {
  if (!card) return;
  card.setAttribute('data-chart-state', 'degraded');
  let note = card.querySelector('.chart-note');
  if (!note) {
    note = document.createElement('p');
    note.className = 'chart-note';
    card.appendChild(note);
  }
  note.textContent = `${describeChartType(fromType)}falhou na renderizacao, alternado automaticamente para ${describeChartType(toType)} para garantir compatibilidade.`;
}

function clearChartDegradeNote(card) {
  if (!card) return;
  card.removeAttribute('data-chart-state');
  const note = card.querySelector('.chart-note');
  if (note) {
    note.remove();
  }
}

function isWordCloudWidget(payload) {
  const type = payload && payload.widgetType;
  return typeof type === 'string' && type.toLowerCase().includes('wordcloud');
}

function hashString(str) {
  let h = 0;
  if (!str) return h;
  for (let i = 0; i < str.length; i++) {
    h = (h << 5) - h + str.charCodeAt(i);
    h |= 0;
  }
  return h;
}

function normalizeWordcloudItems(payload) {
  const sources = [];
  const props = payload && payload.props;
  const dataField = payload && payload.data;
  if (props) {
    ['data', 'items', 'words', 'sourceData'].forEach(key => {
      if (props[key]) sources.push(props[key]);
    });
  }
  if (dataField) {
    sources.push(dataField);
  }

  const seen = new Map();
  const pushItem = (word, weight, category) => {
    if (!word) return;
    let numeric = 1;
    if (typeof weight === 'number' && Number.isFinite(weight)) {
      numeric = weight;
    } else if (typeof weight === 'string') {
      const parsed = parseFloat(weight);
      numeric = Number.isFinite(parsed) ? parsed : 1;
    }
    if (!(numeric > 0)) numeric = 1;
    const cat = (category || '').toString().toLowerCase();
    const key = `${word}__${cat}`;
    const existing = seen.get(key);
    const payloadItem = { word: String(word), weight: numeric, category: cat };
    if (!existing || numeric > existing.weight) {
      seen.set(key, payloadItem);
    }
  };

  const consume = (raw) => {
    if (!raw) return;
    if (Array.isArray(raw)) {
      raw.forEach(item => {
        if (!item) return;
        if (Array.isArray(item)) {
          pushItem(item[0], item[1], item[2]);
        } else if (typeof item === 'object') {
          pushItem(item.word || item.text || item.label, item.weight, item.category);
        } else if (typeof item === 'string') {
          pushItem(item, 1, '');
        }
      });
    } else if (typeof raw === 'object') {
      Object.entries(raw).forEach(([word, weight]) => pushItem(word, weight, ''));
    }
  };

  sources.forEach(consume);

  const items = Array.from(seen.values());
  items.sort((a, b) => (b.weight || 0) - (a.weight || 0));
  return items.slice(0, 150);
}

function wordcloudColor(category) {
  const key = typeof category === 'string' ? category.toLowerCase() : '';
  const palette = resolveWordcloudTheme();
  const base = WORDCLOUD_CATEGORY_COLORS[key] || palette.accent || palette.secondary || '#334155';
  return liftDarkColor(base);
}

function renderWordCloudFallback(canvas, items, reason) {
  // ： canvas，（+Peso），“”
  const card = canvas.closest('.chart-card') || canvas.parentElement;
  if (!card) return;
  const wrapper = canvas.parentElement && canvas.parentElement.classList && canvas.parentElement.classList.contains('chart-container')
    ? canvas.parentElement
    : null;
  if (wrapper) {
    wrapper.style.display = 'none';
  } else {
    canvas.style.display = 'none';
  }
  let fallback = card.querySelector('.chart-fallback[data-dynamic="true"]');
  if (!fallback) {
    fallback = card.querySelector('.chart-fallback');
  }
  if (!fallback) {
    fallback = document.createElement('div');
    card.appendChild(fallback);
  }
  fallback.className = 'chart-fallback wordcloud-fallback';
  fallback.setAttribute('data-dynamic', 'true');
  fallback.style.display = 'block';
  fallback.innerHTML = '';
  card.setAttribute('data-chart-state', 'fallback');
  const buildBadge = (item, maxWeight) => {
    const badge = document.createElement('span');
    badge.className = 'wordcloud-badge';
    const clampedWeight = Math.max(0.5, (item.weight || 1));
    const normalized = Math.min(1, clampedWeight / (maxWeight || 1));
    const fontSize = 0.85 + normalized * 0.9;
    badge.style.fontSize = `${fontSize}rem`;
    badge.style.background = `linear-gradient(135deg, ${lightenColor(wordcloudColor(item.category), 0.05)} 0%, ${lightenColor(wordcloudColor(item.category), 0.15)} 100%)`;
    badge.style.borderColor = lightenColor(wordcloudColor(item.category), 0.25);
    badge.textContent = item.word;
    if (item.weight !== undefined && item.weight !== null) {
      const meta = document.createElement('small');
      meta.textContent = item.weight >= 0 && item.weight <= 1.5
        ? `${(item.weight * 100).toFixed(0)}%`
        : item.weight.toFixed(1).replace(/\.0+$/, '').replace(/0+$/, '').replace(/\.$/, '');
      badge.appendChild(meta);
    }
    return badge;
  };

  if (reason) {
    const notice = document.createElement('p');
    notice.className = 'chart-fallback__notice';
    notice.textContent = `Nuvem de palavras nao pode ser renderizada${reason ? `（${reason}）` : ''}，lista de palavras-chave exibida.`;
    fallback.appendChild(notice);
  }
  if (!items || !items.length) {
    const empty = document.createElement('p');
    empty.textContent = 'Nenhum dado disponivel no momento.';
    fallback.appendChild(empty);
    return;
  }
  const badges = document.createElement('div');
  badges.className = 'wordcloud-badges';
  const maxWeight = items.reduce((max, item) => Math.max(max, item.weight || 0), 1);
  items.forEach(item => {
    badges.appendChild(buildBadge(item, maxWeight));
  });
  fallback.appendChild(badges);
}

function renderWordCloud(canvas, payload, skipRegistry) {
  const items = normalizeWordcloudItems(payload);
  const card = canvas.closest('.chart-card') || canvas.parentElement;
  const container = canvas.parentElement && canvas.parentElement.classList && canvas.parentElement.classList.contains('chart-container')
    ? canvas.parentElement
    : null;
  if (!items.length) {
    renderWordCloudFallback(canvas, items, 'sem dados validos');
    return;
  }
  if (typeof WordCloud === 'undefined') {
    renderWordCloudFallback(canvas, items, 'dependencia de nuvem de palavras nao carregada');
    return;
  }
  const theme = resolveWordcloudTheme();
  const dpr = Math.max(1, window.devicePixelRatio || 1);
  const width = Math.max(260, (container ? container.clientWidth : canvas.clientWidth || canvas.width || 320));
  const height = Math.max(120, Math.round(width / 5)); // proporcao 5:1
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  canvas.style.backgroundColor = 'transparent';

  const resolveBgColor = () => {
    const cardEl = card || container || document.body;
    const style = getComputedStyle(cardEl);
    const tokens = ['--card-bg', '--panel-bg', '--paper-bg', '--bg', '--background', '--page-bg'];
    for (const key of tokens) {
      const val = style.getPropertyValue(key);
      if (val && val.trim() && val.trim() !== 'transparent') return val.trim();
    }
    if (style.backgroundColor && style.backgroundColor !== 'rgba(0, 0, 0, 0)') return style.backgroundColor;
    const bodyStyle = getComputedStyle(document.body);
    for (const key of tokens) {
      const val = bodyStyle.getPropertyValue(key);
      if (val && val.trim() && val.trim() !== 'transparent') return val.trim();
    }
    if (bodyStyle.backgroundColor && bodyStyle.backgroundColor !== 'rgba(0, 0, 0, 0)') {
      return bodyStyle.backgroundColor;
    }
    return 'transparent';
  };
  const bgColor = resolveBgColor() || theme.cardBg || 'transparent';

  const maxWeight = items.reduce((max, item) => Math.max(max, item.weight || 0), 0) || 1;
  const weightLookup = new Map();
  const categoryLookup = new Map();
  items.forEach(it => {
    weightLookup.set(it.word, it.weight || 1);
    categoryLookup.set(it.word, it.category || '');
  });
  const list = items.map(item => [item.word, item.weight && item.weight > 0 ? item.weight : 1]);
  try {
    WordCloud(canvas, {
      list,
      gridSize: Math.max(3, Math.floor(Math.sqrt(canvas.width * canvas.height) / 170)),
      weightFactor: (val) => {
        const normalized = Math.max(0, val) / maxWeight;
        const cap = Math.min(width, height);
        const base = Math.max(9, cap / 5.5);
        const size = base * (0.8 + normalized * 1.3);
        return size * dpr;
      },
      color: (word) => {
        const w = weightLookup.get(word) || 1;
        const ratio = Math.max(0, Math.min(1, w / (maxWeight || 1)));
        const category = categoryLookup.get(word) || '';
        const base = wordcloudColor(category);
        const target = theme.isDark ? '#ffffff' : (theme.text || '#111827');
        const mixAmount = theme.isDark
          ? 0.28 + (1 - ratio) * 0.22
          : 0.12 + (1 - ratio) * 0.35;
        const mixed = mixColors(base, target, mixAmount);
        return ensureAlpha(mixed || base, theme.isDark ? 0.95 : 1);
      },
      rotateRatio: 0,
      rotationSteps: 0,
      shuffle: false,
      shrinkToFit: true,
      drawOutOfBound: false,
      shape: 'square',
      ellipticity: 0.45,
      clearCanvas: true,
      backgroundColor: bgColor
    });
    if (container) {
      container.style.display = '';
      container.style.minHeight = `${height}px`;
      container.style.background = 'transparent';
    }
    const fallback = card && card.querySelector('.chart-fallback');
    if (fallback) {
      fallback.style.display = 'none';
    }
    card && card.removeAttribute('data-chart-state');
    if (!skipRegistry) {
      wordCloudRegistry.set(canvas, () => renderWordCloud(canvas, payload, true));
    }
  } catch (err) {
    console.error('Falha na renderizacao do WordCloud', err);
    renderWordCloudFallback(canvas, items, err && err.message ? err.message : '');
  }
}

function createFallbackTable(labels, datasets) {
  if (!Array.isArray(datasets) || !datasets.length) {
    return null;
  }
  const primaryDataset = datasets.find(ds => Array.isArray(ds && ds.data));
  const resolvedLabels = Array.isArray(labels) && labels.length
    ? labels
    : (primaryDataset && primaryDataset.data ? primaryDataset.data.map((_, idx) => `ponto de dados ${idx + 1}`) : []);
  if (!resolvedLabels.length) {
    return null;
  }
  const table = document.createElement('table');
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  const categoryHeader = document.createElement('th');
  categoryHeader.textContent = 'Categoria';
  headRow.appendChild(categoryHeader);
  datasets.forEach((dataset, index) => {
    const th = document.createElement('th');
    th.textContent = dataset && dataset.label ? dataset.label : `Serie${index + 1}`;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  table.appendChild(thead);
  const tbody = document.createElement('tbody');
  resolvedLabels.forEach((label, rowIdx) => {
    const row = document.createElement('tr');
    const labelCell = document.createElement('td');
    labelCell.textContent = label;
    row.appendChild(labelCell);
    datasets.forEach(dataset => {
      const cell = document.createElement('td');
      const series = dataset && Array.isArray(dataset.data) ? dataset.data[rowIdx] : undefined;
      if (typeof series === 'number') {
        cell.textContent = series.toLocaleString();
      } else if (series !== undefined && series !== null && series !== '') {
        cell.textContent = series;
      } else {
        cell.textContent = '—';
      }
      row.appendChild(cell);
    });
    tbody.appendChild(row);
  });
  table.appendChild(tbody);
  return table;
}

function renderChartFallback(canvas, payload, reason) {
  // Grafico：tabela（categories x series）， fallback 
  const card = canvas.closest('.chart-card') || canvas.parentElement;
  if (!card) return;
  clearChartDegradeNote(card);
  const wrapper = canvas.parentElement && canvas.parentElement.classList && canvas.parentElement.classList.contains('chart-container')
    ? canvas.parentElement
    : null;
  if (wrapper) {
    wrapper.style.display = 'none';
  } else {
    canvas.style.display = 'none';
  }
  let fallback = card.querySelector('.chart-fallback[data-dynamic="true"]');
  let prebuilt = false;
  if (!fallback) {
    fallback = card.querySelector('.chart-fallback');
    if (fallback) {
      prebuilt = fallback.hasAttribute('data-prebuilt');
    }
  }
  if (!fallback) {
    fallback = document.createElement('div');
    fallback.className = 'chart-fallback';
    fallback.setAttribute('data-dynamic', 'true');
    card.appendChild(fallback);
  } else if (!prebuilt) {
    fallback.innerHTML = '';
  }
  const titleFromOptions = payload && payload.props && payload.props.options &&
    payload.props.options.plugins && payload.props.options.plugins.title &&
    payload.props.options.plugins.title.text;
  const fallbackTitle = titleFromOptions ||
    (payload && payload.props && payload.props.title) ||
    (payload && payload.widgetId) ||
    canvas.getAttribute('id') ||
    'Grafico';
  const existingNotice = fallback.querySelector('.chart-fallback__notice');
  if (existingNotice) {
    existingNotice.remove();
  }
  const notice = document.createElement('p');
  notice.className = 'chart-fallback__notice';
  notice.textContent = `${fallbackTitle}：Grafico，tabela${reason ? `（${reason}）` : ''}`;
  fallback.insertBefore(notice, fallback.firstChild || null);
  if (!prebuilt) {
    const table = createFallbackTable(
      payload && payload.data && payload.data.labels,
      payload && payload.data && payload.data.datasets
    );
    if (table) {
      fallback.appendChild(table);
    }
  }
  fallback.style.display = 'block';
  card.setAttribute('data-chart-state', 'fallback');
}

function buildChartOptions(payload) {
  const rawLegend = payload && payload.props ? payload.props.legend : undefined;
  let legendConfig;
  if (isPlainObject(rawLegend)) {
    legendConfig = mergeOptions({
      display: rawLegend.display !== false,
      position: rawLegend.position || 'top'
    }, rawLegend);
  } else {
    legendConfig = {
      display: rawLegend === 'hidden' ? false : true,
      position: typeof rawLegend === 'string' ? rawLegend : 'top'
    };
  }
  const baseOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: legendConfig
    }
  };
  if (payload && payload.props && payload.props.title) {
    baseOptions.plugins.title = {
      display: true,
      text: payload.props.title
    };
  }
  const overrideOptions = payload && payload.props && payload.props.options;
  return mergeOptions(baseOptions, overrideOptions);
}

function validateChartData(payload, type) {
  /**
   * Validacao de dados do grafico no frontend
   * Retorna: { valid: boolean, errors: string[] }
   */
  const errors = [];

  if (!payload || typeof payload !== 'object') {
    errors.push('payload invalido');
    return { valid: false, errors };
  }

  const data = payload.data;
  if (!data || typeof data !== 'object') {
    errors.push('campo data ausente');
    return { valid: false, errors };
  }

  // Tipos especiais de grafico (scatter, bubble)
  const specialTypes = { 'scatter': true, 'bubble': true };
  if (specialTypes[type]) {
    // Estes tipos requerem formato de dados especial {x, y}  ou  {x, y, r}
    // Pular validacao padrao
    return { valid: true, errors };
  }

  // Validacao padrao de tipo de grafico
  const datasets = data.datasets;
  if (!Array.isArray(datasets)) {
    errors.push('datasetsdeve ser um array');
    return { valid: false, errors };
  }

  if (datasets.length === 0) {
    errors.push('array datasets esta vazio');
    return { valid: false, errors };
  }

  // Validar cada dataset
  for (let i = 0; i < datasets.length; i++) {
    const dataset = datasets[i];
    if (!dataset || typeof dataset !== 'object') {
      errors.push(`datasets[${i}] nao e um objeto`);
      continue;
    }

    if (!Array.isArray(dataset.data)) {
      errors.push(`datasets[${i}].data nao e um array`);
    } else if (dataset.data.length === 0) {
      errors.push(`datasets[${i}].data esta vazio`);
    }
  }

  // Tipos de grafico que requerem labels
  const labelRequiredTypes = {
    'line': true, 'bar': true, 'radar': true,
    'polarArea': true, 'pie': true, 'doughnut': true
  };

  if (labelRequiredTypes[type]) {
    const labels = data.labels;
    if (!Array.isArray(labels)) {
      errors.push('array labels ausente');
    } else if (labels.length === 0) {
      errors.push('array labels esta vazio');
    }
  }

  return {
    valid: errors.length === 0,
    errors
  };
}

function instantiateChart(ctx, payload, optionsTemplate, type) {
  if (!ctx) {
    return null;
  }
  if (ctx.canvas && typeof Chart !== 'undefined' && typeof Chart.getChart === 'function') {
    const existing = Chart.getChart(ctx.canvas);
    if (existing) {
      existing.destroy();
    }
  }
  const data = cloneDeep(payload && payload.data ? payload.data : {});
  const config = {
    type,
    data,
    options: cloneDeep(optionsTemplate)
  };
  return new Chart(ctx, config);
}

function debounce(fn, wait) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(null, args), wait || 200);
  };
}

function hydrateCharts() {
  document.querySelectorAll('canvas[data-config-id]').forEach(canvas => {
    const configScript = document.getElementById(canvas.dataset.configId);
    if (!configScript) return;
    let payload;
    try {
      payload = JSON.parse(configScript.textContent);
    } catch (err) {
      console.error('Falha na analise do JSON do Widget', err);
      renderChartFallback(canvas, { widgetId: canvas.dataset.configId }, 'Falha na analise da configuracao');
      return;
    }
    if (isWordCloudWidget(payload)) {
      renderWordCloud(canvas, payload);
      return;
    }
    if (typeof Chart === 'undefined') {
      renderChartFallback(canvas, payload, 'Chart.js nao carregado');
      return;
    }
    const chartTypes = resolveChartTypes(payload);
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      renderChartFallback(canvas, payload, 'Falha na inicializacao do Canvas');
      return;
    }

    // Validacao de dados no frontend
    const desiredType = chartTypes[0];
    const card = canvas.closest('.chart-card') || canvas.parentElement;
    const colorAdjustments = normalizeDatasetColors(payload, desiredType);
    if (colorAdjustments.length && card) {
      card.setAttribute('data-chart-color-fixes', colorAdjustments.join(' | '));
    }
    const validation = validateChartData(payload, desiredType);
    if (!validation.valid) {
      console.warn('Falha na validacao dos dados do grafico:', validation.errors);
      // Falha na validacao mas ainda tenta renderizar, pois pode ter sucesso com degradacao
    }

    const optionsTemplate = buildChartOptions(payload);
    let chartInstance = null;
    let selectedType = null;
    let lastError;
    for (const type of chartTypes) {
      try {
        chartInstance = instantiateChart(ctx, payload, optionsTemplate, type);
        selectedType = type;
        break;
      } catch (err) {
        lastError = err;
        console.error('Falha na renderizacao do grafico', type, err);
      }
    }
    if (chartInstance) {
      chartRegistry.push(chartInstance);
      try {
        applyChartTheme(chartInstance);
      } catch (err) {
        console.error('Falha na sincronizacao do tema', selectedType || desiredType || payload && payload.widgetType || 'chart', err);
      }
      if (selectedType && selectedType !== desiredType) {
        setChartDegradeNote(card, desiredType, selectedType);
      } else {
        clearChartDegradeNote(card);
      }
    } else {
      const reason = lastError && lastError.message ? lastError.message : '';
      renderChartFallback(canvas, payload, reason);
    }
  });
}

function getExportOverlayParts() {
  const overlay = document.getElementById('export-overlay');
  if (!overlay) {
    return null;
  }
  return {
    overlay,
    status: overlay.querySelector('.export-status')
  };
}

function showExportOverlay(message) {
  const parts = getExportOverlayParts();
  if (!parts) return;
  if (message && parts.status) {
    parts.status.textContent = message;
  }
  parts.overlay.classList.add('active');
  document.body.classList.add('exporting');
}

function updateExportOverlay(message) {
  if (!message) return;
  const parts = getExportOverlayParts();
  if (parts && parts.status) {
    parts.status.textContent = message;
  }
}

function hideExportOverlay(delay) {
  const parts = getExportOverlayParts();
  if (!parts) return;
  const close = () => {
    parts.overlay.classList.remove('active');
    document.body.classList.remove('exporting');
  };
  if (delay && delay > 0) {
    setTimeout(close, delay);
  } else {
    close();
  }
}

// exportPdf removido
function exportPdf() {
  // Interacao do botao de exportacao: desabilitar botao+abrir overlay, renderizar main com html2canvas + jsPDF, restaurar botao e overlay
  const target = document.querySelector('main');
  if (!target || typeof jspdf === 'undefined' || typeof jspdf.jsPDF !== 'function') {
    alert('Dependencias de exportacao PDF nao prontas');
    return;
  }
  const exportBtn = document.getElementById('export-btn');
  if (exportBtn) {
    exportBtn.disabled = true;
  }
  showExportOverlay('Exportando PDF, por favor aguarde...');
  document.body.classList.add('exporting');
  const pdf = new jspdf.jsPDF('p', 'mm', 'a4');
  try {
    if (window.pdfFontData) {
      pdf.addFileToVFS('SourceHanSerifSC-Medium.ttf', window.pdfFontData);
      pdf.addFont('SourceHanSerifSC-Medium.ttf', 'SourceHanSerif', 'normal');
      pdf.setFont('SourceHanSerif');
      console.log('Fonte PDF carregada com sucesso');
    } else {
      console.warn('Dados de fonte PDF nao encontrados, sera usada fonte padrao');
    }
  } catch (err) {
    console.warn('Custom PDF font setup failed, fallback to default', err);
  }
  const pageWidth = pdf.internal.pageSize.getWidth();
  const pxWidth = Math.max(
    target.scrollWidth,
    document.documentElement.scrollWidth,
    Math.round(pageWidth * 3.78)
  );
  const restoreButton = () => {
    if (exportBtn) {
      exportBtn.disabled = false;
    }
    document.body.classList.remove('exporting');
  };
  let renderTask;
  try {
    // force charts to rerender at full width before capture
    chartRegistry.forEach(chart => {
      if (chart && typeof chart.resize === 'function') {
        chart.resize();
      }
    });
    wordCloudRegistry.forEach(fn => {
      if (typeof fn === 'function') {
        try {
          fn();
        } catch (err) {
          console.error('Falha na re-renderizacao da nuvem de palavras', err);
        }
      }
    });
    renderTask = pdf.html(target, {
      x: 8,
      y: 12,
      width: pageWidth - 16,
      margin: [12, 12, 20, 12],
      autoPaging: 'text',
      windowWidth: pxWidth,
      html2canvas: {
        scale: Math.min(1.5, Math.max(1.0, pageWidth / (target.clientWidth || pageWidth))),
        useCORS: true,
        scrollX: 0,
        scrollY: -window.scrollY,
        logging: false,
        allowTaint: true,
        backgroundColor: '#ffffff'
      },
      pagebreak: {
        mode: ['css', 'legacy'],
        avoid: [
          '.chapter > *',
          '.callout',
          '.chart-card',
          '.table-wrap',
          '.kpi-grid',
          '.hero-section'
        ],
        before: '.chapter-divider'
      },
      callback: (doc) => doc.save('report.pdf')
    });
  } catch (err) {
    console.error('Falha na exportacao PDF', err);
    updateExportOverlay('Falha na exportacao, tente novamente mais tarde');
    hideExportOverlay(1200);
    restoreButton();
    alert('Falha na exportacao PDF, tente novamente mais tarde');
    return;
  }
  if (renderTask && typeof renderTask.then === 'function') {
    renderTask.then(() => {
      updateExportOverlay('Exportacao concluida, salvando...');
      hideExportOverlay(800);
      restoreButton();
    }).catch(err => {
      console.error('Falha na exportacao PDF', err);
      updateExportOverlay('Falha na exportacao, tente novamente mais tarde');
      hideExportOverlay(1200);
      restoreButton();
      alert('Falha na exportacao PDF, tente novamente mais tarde');
    });
  } else {
    hideExportOverlay();
    restoreButton();
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const rerenderWordclouds = debounce(() => {
    wordCloudRegistry.forEach(fn => {
      if (typeof fn === 'function') {
        fn();
      }
    });
  }, 260);
  // Botao de tema Web Component antigo (comentado)
  // const themeBtn = document.getElementById('theme-toggle');
  // if (themeBtn) {
  //   themeBtn.addEventListener('change', (e) => {
  //     if (e.detail === 'dark') {
  //       document.body.classList.add('dark-mode');
  //     } else {
  //       document.body.classList.remove('dark-mode');
  //     }
  //     chartRegistry.forEach(applyChartTheme);
  //     rerenderWordclouds();
  //   });
  // }

  // Novo botao de tema estilo action-btn
  const themeBtnNew = document.getElementById('theme-toggle-btn');
  if (themeBtnNew) {
    const sunIcon = themeBtnNew.querySelector('.sun-icon');
    const moonIcon = themeBtnNew.querySelector('.moon-icon');
    let isDark = document.body.classList.contains('dark-mode');

    const updateThemeUI = () => {
      if (isDark) {
        sunIcon.style.display = 'none';
        moonIcon.style.display = 'block';
      } else {
        sunIcon.style.display = 'block';
        moonIcon.style.display = 'none';
      }
    };
    updateThemeUI();

    themeBtnNew.addEventListener('click', () => {
      isDark = !isDark;
      if (isDark) {
        document.body.classList.add('dark-mode');
      } else {
        document.body.classList.remove('dark-mode');
      }
      updateThemeUI();
      chartRegistry.forEach(applyChartTheme);
      rerenderWordclouds();
    });
  }
  const printBtn = document.getElementById('print-btn');
  if (printBtn) {
    // Botao de impressao: chama impressao do navegador diretamente, depende de @media print para controle de layout
    printBtn.addEventListener('click', () => window.print());
  }
  // Adicionar efeito de halo com rastreamento de mouse para todos os action-btn
  document.querySelectorAll('.action-btn').forEach(btn => {
    btn.addEventListener('mousemove', (e) => {
      const rect = btn.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width) * 100;
      const y = ((e.clientY - rect.top) / rect.height) * 100;
      btn.style.setProperty('--mouse-x', x + '%');
      btn.style.setProperty('--mouse-y', y + '%');
    });
    btn.addEventListener('mouseleave', () => {
      btn.style.setProperty('--mouse-x', '50%');
      btn.style.setProperty('--mouse-y', '50%');
    });
  });
  const exportBtn = document.getElementById('export-btn');
  if (exportBtn) {
    // Botao de exportacao: chama exportPdf (html2canvas + jsPDF), controla overlay/indicador de progresso
    exportBtn.addEventListener('click', exportPdf);
  }
  window.addEventListener('resize', rerenderWordclouds);
  hydrateCharts();
});
</script>
""".strip()


__all__ = ["HTMLRenderer"]
