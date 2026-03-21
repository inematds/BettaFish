"""
Persistencia de JSON de capitulos e gerenciamento de manifesto.

每一章在流式生成时会立即写入rawArquivo，完成校验后再写入
chapter.json formatado, registrando metadados no manifesto para montagem posterior.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Generator, List, Optional


@dataclass
class ChapterRecord:
    """
    Metadados de capitulo registrados no manifesto.

    Esta estrutura e usada em `manifest.json` 中追踪每章的状态、Arquivo位置、
    以及可能的Erro(s)列表，方便前端或调试工具读取。
    """

    chapter_id: str
    slug: str
    title: str
    order: int
    status: str
    files: Dict[str, str] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, object]:
        """Converter registro em dicionario serializado conveniente para gravacao em manifest.json"""
        return {
            "chapterId": self.chapter_id,
            "slug": self.slug,
            "title": self.title,
            "order": self.order,
            "status": self.status,
            "files": self.files,
            "errors": self.errors,
            "updatedAt": self.updated_at,
        }


class ChapterStorage:
    """
    Gerenciador de gravacao de JSON de capitulos e manifesto.

    Responsavel por:
        - 为每次relatorio创建独立runSumario与manifest快照；
        - Gravar imediatamente em `stream.raw` durante geracao por streaming do capitulo;
        - Persistir `chapter.json` apos aprovacao na validacao e atualizar status do manifesto.
    """

    def __init__(self, base_dir: str):
        """
        Criar armazenamento de capitulos.

        Args:
            base_dir: 所有输出runSumario的根Caminho
        """
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._manifests: Dict[str, Dict[str, object]] = {}

    # ======== 会话与清单 ========

    def start_session(self, report_id: str, metadata: Dict[str, object]) -> Path:
        """
        为本次relatorio创建独立的章节输出Sumario与manifest。

        Ao mesmo tempo gravar metadata global em `manifest.json`，para consulta de renderizacao/depuracao.

        Parametros:
            report_id: ID da tarefa.
            metadata: Metadados do Report (titulo, tema, etc.).

        Retorna:
            Path: 新建的runSumario。
        """
        run_dir = self.base_dir / report_id
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "reportId": report_id,
            "createdAt": datetime.utcnow().isoformat() + "Z",
            "metadata": metadata,
            "chapters": [],
        }
        self._manifests[self._key(run_dir)] = manifest
        self._write_manifest(run_dir, manifest)
        return run_dir

    def begin_chapter(self, run_dir: Path, chapter_meta: Dict[str, object]) -> Path:
        """
        创建章节子Sumario并在manifest中标记为streaming状态。

        Gera `order-slug` 风格的子Sumario，并提前登记 raw ArquivoCaminho。

        Parametros:
            run_dir: 会话根Sumario。
            chapter_meta: Metadados contendo chapterId/title/slug/order.

        Retorna:
            Path: 章节Sumario。
        """
        slug_value = str(
            chapter_meta.get("slug") or chapter_meta.get("chapterId") or "section"
        )
        chapter_dir = self._chapter_dir(
            run_dir,
            slug_value,
            int(chapter_meta.get("order", 0)),
        )
        record = ChapterRecord(
            chapter_id=str(chapter_meta.get("chapterId")),
            slug=slug_value,
            title=str(chapter_meta.get("title")),
            order=int(chapter_meta.get("order", 0)),
            status="streaming",
            files={"raw": str(self._raw_stream_path(chapter_dir).relative_to(run_dir))},
        )
        self._upsert_record(run_dir, record)
        return chapter_dir

    def persist_chapter(
        self,
        run_dir: Path,
        chapter_meta: Dict[str, object],
        payload: Dict[str, object],
        errors: Optional[List[str]] = None,
    ) -> Path:
        """
        Gravar JSON final e atualizar status do manifesto apos conclusao da geracao por streaming do capitulo.

        若Falha na validacao，Erro(s)信息会被写入manifest，供前端展示。

        Parametros:
            run_dir: 会话根Sumario。
            chapter_meta: Meta-informacoes do capitulo.
            payload: JSON do capitulo aprovado na validacao.
            errors: 可选的Erro(s)列表，用于标记invalid状态。

        Retorna:
            Path: 最终的 `chapter.json` ArquivoCaminho。
        """
        slug_value = str(
            chapter_meta.get("slug") or chapter_meta.get("chapterId") or "section"
        )
        chapter_dir = self._chapter_dir(
            run_dir,
            slug_value,
            int(chapter_meta.get("order", 0)),
        )
        final_path = chapter_dir / "chapter.json"
        final_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        record = ChapterRecord(
            chapter_id=str(chapter_meta.get("chapterId")),
            slug=slug_value,
            title=str(chapter_meta.get("title")),
            order=int(chapter_meta.get("order", 0)),
            status="ready" if not errors else "invalid",
            files={
                "raw": str(self._raw_stream_path(chapter_dir).relative_to(run_dir)),
                "json": str(final_path.relative_to(run_dir)),
            },
            errors=errors or [],
        )
        self._upsert_record(run_dir, record)
        return final_path

    def load_chapters(self, run_dir: Path) -> List[Dict[str, object]]:
        """
        从指定runSumario读取全部chapter.json并按order排序返回。

        Comumente usado pelo DocumentComposer para montar multiplos capitulos no IR completo.

        Parametros:
            run_dir: 会话根Sumario。

        Retorna:
            list[dict]: Lista de payloads de capitulos.
        """
        payloads: List[Dict[str, object]] = []
        for child in sorted(run_dir.iterdir()):
            if not child.is_dir():
                continue
            chapter_path = child / "chapter.json"
            if not chapter_path.exists():
                continue
            try:
                payload = json.loads(chapter_path.read_text(encoding="utf-8"))
                payloads.append(payload)
            except json.JSONDecodeError:
                continue
        payloads.sort(key=lambda x: x.get("order", 0))
        return payloads

    # ======== Arquivo操作 ========

    @contextmanager
    def capture_stream(self, chapter_dir: Path) -> Generator:
        """
        将流式输出实时写入rawArquivo。

        通过 contextmanager 暴露Arquivo句柄，简化章节节点的写入逻辑。

        Parametros:
            chapter_dir: 当前章节Sumario。

        Retorna:
            Generator[TextIO]: 作为上下文管理器使用的Arquivo对象。
        """
        raw_path = self._raw_stream_path(chapter_dir)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        with raw_path.open("w", encoding="utf-8") as fp:
            yield fp

    # ======== 内部工具 ========

    def _chapter_dir(self, run_dir: Path, slug: str, order: int) -> Path:
        """根据slug/order生成稳定Sumario，确保各章分隔存盘且可排序。"""
        safe_slug = self._safe_slug(slug)
        folder = f"{order:03d}-{safe_slug}"
        path = run_dir / folder
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _safe_slug(self, slug: str) -> str:
        """移除危险caracteres，避免生成非法Arquivo夹名。"""
        slug = slug.replace(" ", "-").replace("/", "-")
        return slug or "section"

    def _raw_stream_path(self, chapter_dir: Path) -> Path:
        """返回某章节流式输出对应的rawArquivoCaminho。"""
        return chapter_dir / "stream.raw"

    def _key(self, run_dir: Path) -> str:
        """将runSumario解析为字典缓存的键，避免重复读取磁盘。"""
        return str(run_dir.resolve())

    def _manifest_path(self, run_dir: Path) -> Path:
        """获取manifest.json的实际ArquivoCaminho。"""
        return run_dir / "manifest.json"

    def _write_manifest(self, run_dir: Path, manifest: Dict[str, object]):
        """Gravar snapshot completo do manifesto da memoria de volta ao disco."""
        self._manifest_path(run_dir).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_manifest(self, run_dir: Path) -> Dict[str, object]:
        """
        Ler manifesto existente do disco.

        Pode ser usado para restaurar contexto ao reiniciar processo ou em gravacao multi-instancia.
        """
        manifest_path = self._manifest_path(run_dir)
        if manifest_path.exists():
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        return {"reportId": run_dir.name, "chapters": []}

    def _upsert_record(self, run_dir: Path, record: ChapterRecord):
        """
        Atualizar ou adicionar registro de capitulo no manifesto, garantindo ordem consistente.

        Internamente ordena automaticamente e grava de volta no cache + disco.
        """
        key = self._key(run_dir)
        manifest = self._manifests.get(key) or self._read_manifest(run_dir)
        chapters: List[Dict[str, object]] = manifest.get("chapters", [])
        chapters = [c for c in chapters if c.get("chapterId") != record.chapter_id]
        chapters.append(record.to_dict())
        chapters.sort(key=lambda x: x.get("order", 0))
        manifest["chapters"] = chapters
        manifest.setdefault("updatedAt", datetime.utcnow().isoformat() + "Z")
        self._manifests[key] = manifest
        self._write_manifest(run_dir, manifest)


__all__ = ["ChapterStorage", "ChapterRecord"]
