"""
Ferramenta de fatiamento de template Markdown.

O LLM precisa de “chamadas por capitulo”, portanto e necessario analisar o template Markdown em uma fila estruturada de capitulos.
Aqui, atraves de regex leve e heuristica de indentacao, compativel com “# Titulo” e
“- **1.0 Titulo** /   - 1.1 Subtitulo” e outras formas de escrita.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional

SECTION_ORDER_STEP = 10


@dataclass
class TemplateSection:
    """
    Entidade de capitulo do template.

    Registra titulo, slug, numero de ordem, nivel, titulo original, numero do capitulo e esbocos,
    facilitando que nos subsequentes os referenciem em prompts e mantenham ancoras consistentes.
    """

    title: str
    slug: str
    order: int
    depth: int
    raw_title: str
    number: str = ""
    chapter_id: str = ""
    outline: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """
        Serializar entidade de capitulo como dicionario.

        Esta estrutura e amplamente usada no contexto de prompts e como entrada para nos de layout/word budget.
        """
        return {
            "title": self.title,
            "slug": self.slug,
            "order": self.order,
            "depth": self.depth,
            "number": self.number,
            "chapterId": self.chapter_id,
            "outline": self.outline,
        }


# Expressao de analise evita intencionalmente o uso de `.*`，para manter determinismo na correspondencia,
# e evitar riscos de regex DoS comuns em textos de template nao confiaveis.
heading_pattern = re.compile(
    r"""
    (?P<marker>\#{1,6})       # Marcador de titulo Markdown
    [ \t]+                    # caractere de espaco obrigatorio
    (?P<title>[^\r\n]+)       # Texto do titulo sem quebra de linha
    """,
    re.VERBOSE,
)
bullet_pattern = re.compile(
    r"""
    (?P<marker>[-*+])         # Marcador de item de lista
    [ \t]+
    (?P<title>[^\r\n]+)
    """,
    re.VERBOSE,
)
number_pattern = re.compile(
    r"""
    (?P<num>
        (?:0|[1-9]\d*)
        (?:\.(?:0|[1-9]\d*))*
    )
    (?:
        (?:[ \t\u00A0\u3000、:：-]+|\.(?!\d))+
        (?P<label>[^\r\n]*)
    )?
    """,
    re.VERBOSE,
)


def parse_template_sections(template_md: str) -> List[TemplateSection]:
    """
    Dividir template Markdown em lista de capitulos (por titulos principais).

    Cada TemplateSection retornado carrega slug/order/numero do capitulo,
    facilitando chamadas por capitulo e geracao de ancoras. A analise e compativel com
    “# Titulo”, “numeracao sem simbolo”, “esboco em lista” e outras formas de escrita.

    Parametros:
        template_md: Texto completo do template Markdown.

    Retorna:
        list[TemplateSection]: Sequencia estruturada de capitulos.
    """

    sections: List[TemplateSection] = []
    current: Optional[TemplateSection] = None
    order = SECTION_ORDER_STEP
    used_slugs = set()

    for raw_line in template_md.splitlines():
        if not raw_line.strip():
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()

        meta = _classify_line(stripped, indent)
        if not meta:
            continue

        if meta["is_section"]:
            slug = _ensure_unique_slug(meta["slug"], used_slugs)
            section = TemplateSection(
                title=meta["title"],
                slug=slug,
                order=order,
                depth=meta["depth"],
                raw_title=meta["raw"],
                number=meta["number"],
            )
            sections.append(section)
            current = section
            order += SECTION_ORDER_STEP
            continue

        # Entrada de esboco
        if current:
            current.outline.append(meta["title"])

    for idx, section in enumerate(sections, start=1):
        # Gerar chapter_id estavel para cada capitulo, facilitando referencia posterior
        section.chapter_id = f"S{idx}"

    return sections


def _classify_line(stripped: str, indent: int) -> Optional[dict]:
    """
    Classificar linhas por indentacao e simbolos.

    Usando regex para determinar se a linha atual e titulo de capitulo, esboco ou item de lista comum,
    e derivar informacoes como depth/slug/number.

    Parametros:
        stripped: Linha original apos remover espacos iniciais e finais.
        indent: Numero de espacos no inicio da linha, usado para distinguir niveis.

    Retorna:
        dict | None: Metadados identificados; retorna None quando nao identificavel.
    """

    heading_match = heading_pattern.fullmatch(stripped)
    if heading_match:
        level = len(heading_match.group("marker"))
        payload = _strip_markup(heading_match.group("title").strip())
        title_info = _split_number(payload)
        slug = _build_slug(title_info["number"], title_info["title"])
        return {
            "is_section": level <= 2,
            "depth": level,
            "title": title_info["display"],
            "raw": payload,
            "number": title_info["number"],
            "slug": slug,
        }

    bullet_match = bullet_pattern.fullmatch(stripped)
    if bullet_match:
        payload = _strip_markup(bullet_match.group("title").strip())
        title_info = _split_number(payload)
        slug = _build_slug(title_info["number"], title_info["title"])
        is_section = indent <= 1
        depth = 1 if indent <= 1 else 2
        return {
            "is_section": is_section,
            "depth": depth,
            "title": title_info["display"],
            "raw": payload,
            "number": title_info["number"],
            "slug": slug,
        }

    # Compativel com“1.1 ...”linhas sem simbolo de prefixo
    number_match = number_pattern.fullmatch(stripped)
    if number_match and number_match.group("label"):
        payload = stripped
        title = number_match.group("label").strip()
        number = number_match.group("num")
        slug = _build_slug(number, title)
        is_section = indent == 0 and number.count(".") <= 1
        depth = 1 if is_section else 2
        display = f"{number} {title}" if title else number
        return {
            "is_section": is_section,
            "depth": depth,
            "title": display,
            "raw": payload,
            "number": number,
            "slug": slug,
        }

    return None


def _strip_markup(text: str) -> str:
    """Remover marcacoes de enfase como **, __, etc., evitando interferencia na correspondencia de titulos."""
    if text.startswith(("**", "__")) and text.endswith(("**", "__")) and len(text) > 4:
        return text[2:-2].strip()
    return text


def _split_number(payload: str) -> dict:
    """
    Dividir numero e titulo.

    Ex: `1.2 Tendencia de mercado` sera dividido em number=1.2, label=Tendencia de mercado,
    e fornece display para repreenchimento do titulo.

    Parametros:
        payload: String de titulo original.

    Retorna:
        dict: Contendo number/title/display.
    """
    match = number_pattern.fullmatch(payload)
    number = match.group("num") if match else ""
    label = match.group("label") if match else payload
    label = (label or "").strip()
    display = f"{number} {label}".strip() if number else label or payload
    title_core = label or payload
    return {
        "number": number,
        "title": title_core,
        "display": display,
    }


def _build_slug(number: str, title: str) -> str:
    """
    Gerar ancora com base em numero/titulo, priorizando reutilizacao do numero; slug-ificar titulo quando ausente.

    Parametros:
        number: Numero do capitulo.
        title: Texto do titulo.

    Retorna:
        str: Slug no formato `section-1-0`.
    """
    if number:
        token = number.replace(".", "-")
    else:
        token = _slugify_text(title)
    token = token or "section"
    return f"section-{token}"


def _slugify_text(text: str) -> str:
    """
    Fazer reducao de ruido e transliteracao de texto arbitrario para obter fragmento slug compativel com URL.

    Normaliza maiusculas/minusculas, remove simbolos especiais e preserva caracteres chineses, garantindo ancoras legiveis.
    """
    text = unicodedata.normalize("NFKD", text)
    text = text.replace("·", "-").replace(" ", "-")
    text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-").lower()


def _ensure_unique_slug(slug: str, used: set) -> str:
    """
    Se o slug for duplicado, anexar numero automaticamente ate ser unico no conjunto used.

    Garante que titulos identicos nao produzam ancoras duplicadas usando sufixos `-2/-3...`.

    Parametros:
        slug: Slug inicial.
        used: Conjunto utilizado.

    Retorna:
        str: Slug deduplicado.
    """
    if slug not in used:
        used.add(slug)
        return slug
    base = slug
    idx = 2
    while slug in used:
        slug = f"{base}-{idx}"
        idx += 1
    used.add(slug)
    return slug


__all__ = ["TemplateSection", "parse_template_sections"]
