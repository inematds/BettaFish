"""
Montador de capitulos: responsavel por combinar multiplos JSONs de capitulos em um IR completo.

DocumentComposer injeta ancoras ausentes, unifica a ordem e completa metadados em nivel de IR.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Set

from ..ir import IR_VERSION


class DocumentComposer:
    """
    Montador simples que une capitulos no Document IR.

    Funcao:
        - Ordenar capitulos por order, completar chapterId padrao;
        - Prevenir ancoras duplicadas, gerar ancoras globalmente unicas;
        - Injetar versao do IR e timestamp de geracao.
    """

    def __init__(self):
        """Inicializar montador e registrar ancoras utilizadas, evitando duplicatas"""
        self._seen_anchors: Set[str] = set()

    def build_document(
        self,
        report_id: str,
        metadata: Dict[str, object],
        chapters: List[Dict[str, object]],
    ) -> Dict[str, object]:
        """
        Ordenar todos os capitulos por order e injetar ancoras unicas, formando o IR completo.

        Ao mesmo tempo mesclar metadata/themeTokens/assets para consumo direto pelo renderizador.

        Parametros:
            report_id: ID deste relatorio.
            metadata: Meta-informacoes globais (titulo, tema, sumario, etc.).
            chapters: Lista de payloads de capitulos.

        Retorna:
            dict: Document IR que atende as necessidades do renderizador.
        """
        # Construir mapeamento de chapterId para ancora do toc
        toc_anchor_map = self._build_toc_anchor_map(metadata)

        ordered = sorted(chapters, key=lambda c: c.get("order", 0))
        for idx, chapter in enumerate(ordered, start=1):
            chapter.setdefault("chapterId", f"S{idx}")

            # Prioridade: 1. ancora configurada no sumario 2. ancora propria do capitulo 3. ancora padrao
            chapter_id = chapter.get("chapterId")
            anchor = (
                toc_anchor_map.get(chapter_id) or
                chapter.get("anchor") or
                f"section-{idx}"
            )
            chapter["anchor"] = self._ensure_unique_anchor(anchor)
            chapter.setdefault("order", idx * 10)
            if chapter.get("errorPlaceholder"):
                self._ensure_heading_block(chapter)

        document = {
            "version": IR_VERSION,
            "reportId": report_id,
            "metadata": {
                **metadata,
                "generatedAt": metadata.get("generatedAt")
                or datetime.utcnow().isoformat() + "Z",
            },
            "themeTokens": metadata.get("themeTokens", {}),
            "chapters": ordered,
            "assets": metadata.get("assets", {}),
        }
        return document

    def _ensure_unique_anchor(self, anchor: str) -> str:
        """Se houver ancoras duplicadas, anexar numero sequencial para garantir unicidade global."""
        base = anchor
        counter = 2
        while anchor in self._seen_anchors:
            anchor = f"{base}-{counter}"
            counter += 1
        self._seen_anchors.add(anchor)
        return anchor

    def _build_toc_anchor_map(self, metadata: Dict[str, object]) -> Dict[str, str]:
        """
        Construir mapeamento de chapterId para anchor a partir de metadata.toc.customEntries.

        Parametros:
            metadata: Meta-informacoes do documento.

        Retorna:
            dict: Mapeamento de chapterId -> anchor.
        """
        toc_config = metadata.get("toc") or {}
        custom_entries = toc_config.get("customEntries") or []
        anchor_map = {}

        for entry in custom_entries:
            if isinstance(entry, dict):
                chapter_id = entry.get("chapterId")
                anchor = entry.get("anchor")
                if chapter_id and anchor:
                    anchor_map[chapter_id] = anchor

        return anchor_map

    def _ensure_heading_block(self, chapter: Dict[str, object]) -> None:
        """Garantir que o capitulo reservado ainda possua heading block utilizavel no sumario."""
        blocks = chapter.get("blocks")
        if isinstance(blocks, list):
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "heading":
                    return
        heading = {
            "type": "heading",
            "level": 2,
            "text": chapter.get("title") or "Capitulo reservado",
            "anchor": chapter.get("anchor"),
        }
        if isinstance(blocks, list):
            blocks.insert(0, heading)
        else:
            chapter["blocks"] = [heading]


__all__ = ["DocumentComposer"]
