"""
Gerenciamento de estado do Deep Search Agent
Define todas as estruturas de dados de estado e métodos de operação
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import json
from datetime import datetime


@dataclass
class Search:
    """Estado de um único resultado de busca"""
    query: str = ""                    # Consulta de busca
    url: str = ""                      # Link do resultado de busca
    title: str = ""                    # Título do resultado de busca
    content: str = ""                  # Conteúdo retornado pela busca
    score: Optional[float] = None      # Pontuação de relevância
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Converter para formato de dicionário"""
        return {
            "query": self.query,
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "score": self.score,
            "timestamp": self.timestamp
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Search":
        """Criar objeto Search a partir de dicionário"""
        return cls(
            query=data.get("query", ""),
            url=data.get("url", ""),
            title=data.get("title", ""),
            content=data.get("content", ""),
            score=data.get("score"),
            timestamp=data.get("timestamp", datetime.now().isoformat())
        )


@dataclass
class Research:
    """Estado do processo de pesquisa do parágrafo"""
    search_history: List[Search] = field(default_factory=list)     # Lista de histórico de buscas
    latest_summary: str = ""                                       # Resumo mais recente do parágrafo atual
    reflection_iteration: int = 0                                  # Número de iterações de reflexão
    is_completed: bool = False                                     # Se a pesquisa foi concluída

    def add_search(self, search: Search):
        """Adicionar registro de busca"""
        self.search_history.append(search)

    def add_search_results(self, query: str, results: List[Dict[str, Any]]):
        """Adicionar resultados de busca em lote"""
        for result in results:
            search = Search(
                query=query,
                url=result.get("url", ""),
                title=result.get("title", ""),
                content=result.get("content", ""),
                score=result.get("score")
            )
            self.add_search(search)

    def get_search_count(self) -> int:
        """Obter número de buscas"""
        return len(self.search_history)

    def increment_reflection(self):
        """Incrementar contador de reflexões"""
        self.reflection_iteration += 1

    def mark_completed(self):
        """Marcar como concluído"""
        self.is_completed = True

    def to_dict(self) -> Dict[str, Any]:
        """Converter para formato de dicionário"""
        return {
            "search_history": [search.to_dict() for search in self.search_history],
            "latest_summary": self.latest_summary,
            "reflection_iteration": self.reflection_iteration,
            "is_completed": self.is_completed
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Research":
        """Criar objeto Research a partir de dicionário"""
        search_history = [Search.from_dict(search_data) for search_data in data.get("search_history", [])]
        return cls(
            search_history=search_history,
            latest_summary=data.get("latest_summary", ""),
            reflection_iteration=data.get("reflection_iteration", 0),
            is_completed=data.get("is_completed", False)
        )


@dataclass
class Paragraph:
    """Estado de um único parágrafo do relatório"""
    title: str = ""                                                # Título do parágrafo
    content: str = ""                                              # Conteúdo esperado do parágrafo (planejamento inicial)
    research: Research = field(default_factory=Research)          # Progresso da pesquisa
    order: int = 0                                                 # Ordem do parágrafo

    def is_completed(self) -> bool:
        """Verificar se o parágrafo foi concluído"""
        return self.research.is_completed and bool(self.research.latest_summary)

    def get_final_content(self) -> str:
        """Obter conteúdo final"""
        return self.research.latest_summary or self.content

    def to_dict(self) -> Dict[str, Any]:
        """Converter para formato de dicionário"""
        return {
            "title": self.title,
            "content": self.content,
            "research": self.research.to_dict(),
            "order": self.order
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Paragraph":
        """Criar objeto Paragraph a partir de dicionário"""
        research_data = data.get("research", {})
        research = Research.from_dict(research_data) if research_data else Research()

        return cls(
            title=data.get("title", ""),
            content=data.get("content", ""),
            research=research,
            order=data.get("order", 0)
        )


@dataclass
class State:
    """Estado de todo o relatório"""
    query: str = ""                                                # Consulta original
    report_title: str = ""                                         # Título do relatório
    paragraphs: List[Paragraph] = field(default_factory=list)     # Lista de parágrafos
    final_report: str = ""                                         # Conteúdo do relatório final
    is_completed: bool = False                                     # Se foi concluído
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def add_paragraph(self, title: str, content: str) -> int:
        """
        Adicionar parágrafo

        Args:
            title: Título do parágrafo
            content: Conteúdo do parágrafo

        Returns:
            Índice do parágrafo
        """
        order = len(self.paragraphs)
        paragraph = Paragraph(title=title, content=content, order=order)
        self.paragraphs.append(paragraph)
        self.update_timestamp()
        return order

    def get_paragraph(self, index: int) -> Optional[Paragraph]:
        """Obter parágrafo pelo índice especificado"""
        if 0 <= index < len(self.paragraphs):
            return self.paragraphs[index]
        return None

    def get_completed_paragraphs_count(self) -> int:
        """Obter número de parágrafos concluídos"""
        return sum(1 for p in self.paragraphs if p.is_completed())

    def get_total_paragraphs_count(self) -> int:
        """Obter número total de parágrafos"""
        return len(self.paragraphs)

    def is_all_paragraphs_completed(self) -> bool:
        """Verificar se todos os parágrafos foram concluídos"""
        return all(p.is_completed() for p in self.paragraphs) if self.paragraphs else False

    def mark_completed(self):
        """Marcar todo o relatório como concluído"""
        self.is_completed = True
        self.update_timestamp()

    def update_timestamp(self):
        """Atualizar carimbo de data/hora"""
        self.updated_at = datetime.now().isoformat()

    def get_progress_summary(self) -> Dict[str, Any]:
        """Obter resumo de progresso"""
        completed = self.get_completed_paragraphs_count()
        total = self.get_total_paragraphs_count()

        return {
            "total_paragraphs": total,
            "completed_paragraphs": completed,
            "progress_percentage": (completed / total * 100) if total > 0 else 0,
            "is_completed": self.is_completed,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }

    def to_dict(self) -> Dict[str, Any]:
        """Converter para formato de dicionário"""
        return {
            "query": self.query,
            "report_title": self.report_title,
            "paragraphs": [p.to_dict() for p in self.paragraphs],
            "final_report": self.final_report,
            "is_completed": self.is_completed,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }

    def to_json(self, indent: int = 2) -> str:
        """Converter para string JSON"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "State":
        """Criar objeto State a partir de dicionário"""
        paragraphs = [Paragraph.from_dict(p_data) for p_data in data.get("paragraphs", [])]

        return cls(
            query=data.get("query", ""),
            report_title=data.get("report_title", ""),
            paragraphs=paragraphs,
            final_report=data.get("final_report", ""),
            is_completed=data.get("is_completed", False),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat())
        )

    @classmethod
    def from_json(cls, json_str: str) -> "State":
        """Criar objeto State a partir de string JSON"""
        data = json.loads(json_str)
        return cls.from_dict(data)

    def save_to_file(self, filepath: str):
        """Salvar estado em arquivo"""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(self.to_json())

    @classmethod
    def load_from_file(cls, filepath: str) -> "State":
        """Carregar estado de arquivo"""
        with open(filepath, 'r', encoding='utf-8') as f:
            json_str = f.read()
        return cls.from_json(json_str)
