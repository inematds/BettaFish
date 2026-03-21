"""
Gerenciamento de estado do Report Engine
Define estruturas de dados simplificadas para o processo de geracao de relatorios
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import json
from datetime import datetime


@dataclass
class ReportMetadata:
    """Metadados simplificados do relatorio"""
    query: str = ""                      # Consulta original
    template_used: str = ""              # Nome do template utilizado
    generation_time: float = 0.0         # Tempo de geracao (segundos)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        """Converter para formato de dicionario"""
        return {
            "query": self.query,
            "template_used": self.template_used,
            "generation_time": self.generation_time,
            "timestamp": self.timestamp
        }


@dataclass 
class ReportState:
    """
    Gerenciamento simplificado de estado do relatorio.

    Armazena informacoes basicas da tarefa, entrada, saida e metadados, compartilhados entre Agent e camada Flask.
    """
    # Informacoes basicas
    task_id: str = ""                    # ID da tarefa
    query: str = ""                      # Consulta original
    status: str = "pending"              # Estado: pending, processing, completed, failed
    
    # Dados de entrada
    query_engine_report: str = ""        # QueryEnginerelatorio
    media_engine_report: str = ""        # MediaEnginerelatorio  
    insight_engine_report: str = ""      # InsightEnginerelatorio
    forum_logs: str = ""                 # logs do forum
    
    # Resultados do processamento
    selected_template: str = ""          # Template selecionado
    html_content: str = ""               # Conteudo HTML final
    
    # Metadados
    metadata: ReportMetadata = field(default_factory=ReportMetadata)
    
    def __post_init__(self):
        """Pos-processamento de inicializacao"""
        if not self.task_id:
            self.task_id = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.metadata.query = self.query
    
    def mark_processing(self):
        """Marcado como em processamento，thread de segundo plano comeca a agendar processo de geracao."""
        self.status = "processing"
    
    def mark_completed(self):
        """Marcado como concluido，significando tambem que `html_content` esta disponivel."""
        self.status = "completed"
    
    def mark_failed(self, error_message: str = ""):
        """Marcado como falho，e registra a ultima mensagem de erro."""
        self.status = "failed"
        self.error_message = error_message
    
    def is_completed(self) -> bool:
        """Verificar se esta concluido，incluindo status completed e existencia de conteudo HTML."""
        return self.status == "completed" and bool(self.html_content)
    
    def get_progress(self) -> float:
        """Obter porcentagem de progresso，estimativa aproximada em duas etapas: template/conteudo."""
        if self.status == "completed":
            return 100.0
        elif self.status == "processing":
            # Calculo simples de progresso
            progress = 0.0
            if self.selected_template:
                progress += 30.0
            if self.html_content:
                progress += 70.0
            return progress
        else:
            return 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Converter para formato de dicionario，facilitando serializacao para o frontend."""
        return {
            "task_id": self.task_id,
            "query": self.query,
            "status": self.status,
            "progress": self.get_progress(),
            "selected_template": self.selected_template,
            "has_html_content": bool(self.html_content),
            "html_content_length": len(self.html_content) if self.html_content else 0,
            "metadata": self.metadata.to_dict()
        }
    
    def save_to_file(self, file_path: str):
        """Salvar estado em arquivo, excluindo corpo HTML para controlar tamanho."""
        try:
            state_data = self.to_dict()
            # Nao salvar conteudo HTML completo no arquivo de estado (muito grande)
            state_data.pop("html_content", None)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(state_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Falha ao salvar arquivo de estado: {str(e)}")
    
    @classmethod
    def load_from_file(cls, file_path: str) -> Optional["ReportState"]:
        """Carregar estado do arquivo, restaurando apenas campos chave para depuracao."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Criar objeto ReportState
            state = cls(
                task_id=data.get("task_id", ""),
                query=data.get("query", ""),
                status=data.get("status", "pending"),
                selected_template=data.get("selected_template", "")
            )
            
            # Definir metadados
            metadata_data = data.get("metadata", {})
            state.metadata.template_used = metadata_data.get("template_used", "")
            state.metadata.generation_time = metadata_data.get("generation_time", 0.0)
            
            return state
            
        except Exception as e:
            print(f"Falha ao carregar arquivo de estado: {str(e)}")
            return None
