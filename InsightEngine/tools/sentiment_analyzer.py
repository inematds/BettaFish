"""
Ferramenta de análise de sentimentos multilíngue
Baseada no modelo WeiboMultilingualSentiment, fornece funcionalidade de análise de sentimentos para o InsightEngine
"""

import os
import sys
from typing import List, Dict, Any, Optional, Union
from dataclasses import dataclass
import re

try:
    import torch

    TORCH_AVAILABLE = True
    torch.classes.__path__ = []
except ImportError:
    torch = None  # type: ignore
    TORCH_AVAILABLE = False

try:
    from transformers import AutoTokenizer, AutoModelForSequenceClassification

    TRANSFORMERS_AVAILABLE = True
except ImportError:
    AutoTokenizer = None  # type: ignore
    AutoModelForSequenceClassification = None  # type: ignore
    TRANSFORMERS_AVAILABLE = False


# INFO: Para pular a análise de sentimentos, altere manualmente esta flag para False
SENTIMENT_ANALYSIS_ENABLED = True


def _describe_missing_dependencies() -> str:
    missing = []
    if not TORCH_AVAILABLE:
        missing.append("PyTorch")
    if not TRANSFORMERS_AVAILABLE:
        missing.append("Transformers")
    return " / ".join(missing)


# Adicionar diretório raiz do projeto ao caminho para importar WeiboMultilingualSentiment
project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
weibo_sentiment_path = os.path.join(
    project_root, "SentimentAnalysisModel", "WeiboMultilingualSentiment"
)
sys.path.append(weibo_sentiment_path)


@dataclass
class SentimentResult:
    """Classe de dados do resultado de análise de sentimentos"""

    text: str
    sentiment_label: str
    confidence: float
    probability_distribution: Dict[str, float]
    success: bool = True
    error_message: Optional[str] = None
    analysis_performed: bool = True


@dataclass
class BatchSentimentResult:
    """Classe de dados do resultado de análise de sentimentos em lote"""

    results: List[SentimentResult]
    total_processed: int
    success_count: int
    failed_count: int
    average_confidence: float
    analysis_performed: bool = True


class WeiboMultilingualSentimentAnalyzer:
    """
    Analisador de sentimentos multilíngue
    Encapsula o modelo WeiboMultilingualSentiment, fornecendo funcionalidade de análise de sentimentos para AI Agent
    """

    def __init__(self):
        """Inicializar o analisador de sentimentos"""
        self.model = None
        self.tokenizer = None
        self.device = None
        self.is_initialized = False
        self.is_disabled = False
        self.disable_reason: Optional[str] = None

        # Mapeamento de rótulos de sentimento (classificação em 5 níveis)
        self.sentiment_map = {
            0: "muito negativo",
            1: "negativo",
            2: "neutro",
            3: "positivo",
            4: "muito positivo",
        }

        if not SENTIMENT_ANALYSIS_ENABLED:
            self.disable("Análise de sentimentos desabilitada na configuração.")
        elif not (TORCH_AVAILABLE and TRANSFORMERS_AVAILABLE):
            missing = _describe_missing_dependencies() or "dependência desconhecida"
            self.disable(f"Dependências ausentes: {missing}, análise de sentimentos desabilitada.")

        if self.is_disabled:
            reason = self.disable_reason or "Sentiment analysis disabled."
            print(
                f"WeiboMultilingualSentimentAnalyzer inicializado mas desabilitado: {reason}"
            )
        else:
            print(
                "WeiboMultilingualSentimentAnalyzer criado, chame initialize() para carregar o modelo"
            )

    def disable(self, reason: Optional[str] = None, drop_state: bool = False) -> None:
        """Disable sentiment analysis, optionally clearing loaded resources."""
        self.is_disabled = True
        self.disable_reason = reason or "Sentiment analysis disabled."
        if drop_state:
            self.model = None
            self.tokenizer = None
            self.device = None
            self.is_initialized = False

    def enable(self) -> bool:
        """Attempt to enable sentiment analysis; returns True if enabled."""
        if not SENTIMENT_ANALYSIS_ENABLED:
            self.disable("Análise de sentimentos desabilitada na configuração.")
            return False
        if not (TORCH_AVAILABLE and TRANSFORMERS_AVAILABLE):
            missing = _describe_missing_dependencies() or "dependência desconhecida"
            self.disable(f"Dependências ausentes: {missing}, análise de sentimentos desabilitada.")
            return False
        self.is_disabled = False
        self.disable_reason = None
        return True

    def _select_device(self):
        """Select the best available torch device."""
        if not TORCH_AVAILABLE:
            return None
        assert torch is not None
        if torch.cuda.is_available():
            return torch.device("cuda")
        mps_backend = getattr(torch.backends, "mps", None)
        if (
            mps_backend
            and getattr(mps_backend, "is_available", lambda: False)()
            and getattr(mps_backend, "is_built", lambda: False)()
        ):
            return torch.device("mps")
        return torch.device("cpu")

    def initialize(self) -> bool:
        """
        Inicializar o modelo e o tokenizador

        Returns:
            Se a inicialização foi bem-sucedida
        """
        if self.is_disabled:
            reason = self.disable_reason or "Análise de sentimentos desabilitada"
            print(f"Análise de sentimentos desabilitada, pulando carregamento do modelo: {reason}")
            return False

        if not (TORCH_AVAILABLE and TRANSFORMERS_AVAILABLE):
            missing = _describe_missing_dependencies() or "dependência desconhecida"
            self.disable(f"Dependências ausentes: {missing}, análise de sentimentos desabilitada.", drop_state=True)
            print(f"Dependências ausentes: {missing}, não é possível carregar o modelo de análise de sentimentos.")
            return False

        if self.is_initialized:
            print("Modelo já inicializado, não é necessário recarregar")
            return True

        try:
            print("Carregando modelo de análise de sentimentos multilíngue...")
            assert AutoTokenizer is not None
            assert AutoModelForSequenceClassification is not None

            # Usar modelo de análise de sentimentos multilíngue
            model_name = "tabularisai/multilingual-sentiment-analysis"
            local_model_path = os.path.join(weibo_sentiment_path, "model")

            # Verificar se o modelo já existe localmente
            if os.path.exists(local_model_path):
                print("Carregando modelo do armazenamento local...")
                self.tokenizer = AutoTokenizer.from_pretrained(local_model_path)
                self.model = AutoModelForSequenceClassification.from_pretrained(
                    local_model_path
                )
            else:
                print("Primeiro uso, baixando modelo para o armazenamento local...")
                # Baixar e salvar localmente
                self.tokenizer = AutoTokenizer.from_pretrained(model_name)
                self.model = AutoModelForSequenceClassification.from_pretrained(
                    model_name
                )

                # Salvar localmente
                os.makedirs(local_model_path, exist_ok=True)
                self.tokenizer.save_pretrained(local_model_path)
                self.model.save_pretrained(local_model_path)
                print(f"Modelo salvo em: {local_model_path}")

            # Configurar dispositivo
            device = self._select_device()
            if device is None:
                raise RuntimeError("Nenhum dispositivo de computação disponível detectado")

            self.device = device
            self.model.to(self.device)
            self.model.eval()
            self.is_initialized = True
            self.enable()

            device_type = getattr(self.device, "type", str(self.device))
            if device_type == "cuda":
                print("GPU disponível detectada, usando CUDA para inferência com prioridade.")
            elif device_type == "mps":
                print("Dispositivo Apple MPS detectado, usando MPS para inferência.")
            else:
                print("Nenhuma GPU detectada, usando CPU automaticamente para inferência.")

            print(f"Modelo carregado com sucesso! Usando dispositivo: {self.device}")
            print("Idiomas suportados: português, chinês, inglês, espanhol, árabe, japonês, coreano e outros 22 idiomas")
            print("Níveis de sentimento: muito negativo, negativo, neutro, positivo, muito positivo")

            return True

        except Exception as e:
            error_message = f"Falha ao carregar modelo: {e}"
            print(error_message)
            print("Verifique a conexão de rede ou os arquivos do modelo")
            self.disable(error_message, drop_state=True)
            return False

    def _preprocess_text(self, text: str) -> str:
        """
        Pré-processamento de texto

        Args:
            text: Texto de entrada

        Returns:
            Texto processado
        """
        # Limpeza básica de texto
        if not text or not text.strip():
            return ""

        # Remover espaços extras
        text = re.sub(r"\s+", " ", text.strip())

        return text

    def analyze_single_text(self, text: str) -> SentimentResult:
        """
        Realizar análise de sentimentos em um único texto

        Args:
            text: Texto a ser analisado

        Returns:
            Objeto SentimentResult
        """
        if self.is_disabled:
            return SentimentResult(
                text=text,
                sentiment_label="Análise de sentimentos não executada",
                confidence=0.0,
                probability_distribution={},
                success=False,
                error_message=self.disable_reason or "Análise de sentimentos desabilitada",
                analysis_performed=False,
            )

        if not self.is_initialized:
            return SentimentResult(
                text=text,
                sentiment_label="Não inicializado",
                confidence=0.0,
                probability_distribution={},
                success=False,
                error_message="Modelo não inicializado, chame initialize() primeiro",
                analysis_performed=False,
            )

        try:
            # Pré-processar texto
            processed_text = self._preprocess_text(text)

            if not processed_text:
                return SentimentResult(
                    text=text,
                    sentiment_label="Erro de entrada",
                    confidence=0.0,
                    probability_distribution={},
                    success=False,
                    error_message="Texto de entrada vazio ou conteúdo inválido",
                    analysis_performed=False,
                )
            assert self.tokenizer is not None
            # Tokenização e codificação
            inputs = self.tokenizer(
                processed_text,
                max_length=512,
                padding=True,
                truncation=True,
                return_tensors="pt",
            )

            # Transferir para dispositivo
            inputs = {k: v.to(self.device) for k, v in inputs.items()}

            # Predição
            assert torch is not None
            assert self.model is not None
            with torch.no_grad():
                outputs = self.model(**inputs)
                logits = outputs.logits
                probabilities = torch.softmax(logits, dim=1)
                prediction = int(torch.argmax(probabilities, dim=1).item())

            # Construir resultado
            confidence = probabilities[0][prediction].item()
            label = self.sentiment_map[prediction]

            # Construir dicionário de distribuição de probabilidades
            prob_dist = {}
            for label_name, prob in zip(self.sentiment_map.values(), probabilities[0]):
                prob_dist[label_name] = prob.item()

            return SentimentResult(
                text=text,
                sentiment_label=label,
                confidence=confidence,
                probability_distribution=prob_dist,
                success=True,
            )

        except Exception as e:
            return SentimentResult(
                text=text,
                sentiment_label="Análise falhou",
                confidence=0.0,
                probability_distribution={},
                success=False,
                error_message=f"Erro durante a predição: {str(e)}",
                analysis_performed=False,
            )

    def analyze_batch(
        self, texts: List[str], show_progress: bool = True
    ) -> BatchSentimentResult:
        """
        Análise de sentimentos em lote

        Args:
            texts: Lista de textos
            show_progress: Se deve exibir progresso

        Returns:
            Objeto BatchSentimentResult
        """
        if not texts:
            return BatchSentimentResult(
                results=[],
                total_processed=0,
                success_count=0,
                failed_count=0,
                average_confidence=0.0,
                analysis_performed=not self.is_disabled and self.is_initialized,
            )

        if self.is_disabled or not self.is_initialized:
            passthrough_results = [
                SentimentResult(
                    text=text,
                    sentiment_label="Análise de sentimentos não executada",
                    confidence=0.0,
                    probability_distribution={},
                    success=False,
                    error_message=self.disable_reason or "Análise de sentimentos indisponível",
                    analysis_performed=False,
                )
                for text in texts
            ]
            return BatchSentimentResult(
                results=passthrough_results,
                total_processed=len(texts),
                success_count=0,
                failed_count=len(texts),
                average_confidence=0.0,
                analysis_performed=False,
            )

        results = []
        success_count = 0
        total_confidence = 0.0

        for i, text in enumerate(texts):
            if show_progress and len(texts) > 1:
                print(f"Progresso do processamento: {i + 1}/{len(texts)}")

            result = self.analyze_single_text(text)
            results.append(result)

            if result.success:
                success_count += 1
                total_confidence += result.confidence

        average_confidence = (
            total_confidence / success_count if success_count > 0 else 0.0
        )
        failed_count = len(texts) - success_count

        return BatchSentimentResult(
            results=results,
            total_processed=len(texts),
            success_count=success_count,
            failed_count=failed_count,
            average_confidence=average_confidence,
            analysis_performed=True,
        )

    def _build_passthrough_analysis(
        self,
        original_data: List[Dict[str, Any]],
        reason: str,
        texts: Optional[List[str]] = None,
        results: Optional[List[SentimentResult]] = None,
    ) -> Dict[str, Any]:
        """
        Construir resultado de passagem direta quando a análise de sentimentos não está disponível
        """
        total_items = len(texts) if texts is not None else len(original_data)
        response: Dict[str, Any] = {
            "sentiment_analysis": {
                "available": False,
                "reason": reason,
                "total_analyzed": 0,
                "success_rate": f"0/{total_items}",
                "average_confidence": 0.0,
                "sentiment_distribution": {},
                "high_confidence_results": [],
                "summary": f"Análise de sentimentos não executada: {reason}",
                "original_texts": original_data,
            }
        }

        if texts is not None:
            response["sentiment_analysis"]["passthrough_texts"] = texts

        if results is not None:
            response["sentiment_analysis"]["results"] = [
                result.__dict__ if isinstance(result, SentimentResult) else result
                for result in results
            ]

        return response

    def analyze_query_results(
        self,
        query_results: List[Dict[str, Any]],
        text_field: str = "content",
        min_confidence: float = 0.5,
    ) -> Dict[str, Any]:
        """
        Realizar análise de sentimentos nos resultados de consulta
        Especificamente para analisar resultados retornados pelo MediaCrawlerDB

        Args:
            query_results: Lista de resultados de consulta, cada elemento contém conteúdo textual
            text_field: Nome do campo de conteúdo textual, padrão "content"
            min_confidence: Limiar mínimo de confiança

        Returns:
            Dicionário contendo resultados de análise de sentimentos
        """
        if not query_results:
            return {
                "sentiment_analysis": {
                    "total_analyzed": 0,
                    "sentiment_distribution": {},
                    "high_confidence_results": [],
                    "summary": "Nenhum conteúdo para analisar",
                }
            }

        # Extrair conteúdo textual
        texts_to_analyze = []
        original_data = []

        for item in query_results:
            # Tentar múltiplos campos de texto possíveis
            text_content = ""
            for field in [text_field, "title_or_content", "content", "title", "text"]:
                if field in item and item[field]:
                    text_content = str(item[field])
                    break

            if text_content.strip():
                texts_to_analyze.append(text_content)
                original_data.append(item)

        if not texts_to_analyze:
            return {
                "sentiment_analysis": {
                    "total_analyzed": 0,
                    "sentiment_distribution": {},
                    "high_confidence_results": [],
                    "summary": "Nenhum conteúdo textual analisável encontrado nos resultados da consulta",
                }
            }

        if self.is_disabled:
            return self._build_passthrough_analysis(
                original_data=original_data,
                reason=self.disable_reason or "Modelo de análise de sentimentos indisponível",
                texts=texts_to_analyze,
            )

        # Executar análise de sentimentos em lote
        print(f"Realizando análise de sentimentos em {len(texts_to_analyze)} itens de conteúdo...")
        batch_result = self.analyze_batch(texts_to_analyze, show_progress=True)

        if not batch_result.analysis_performed:
            reason = self.disable_reason or "Análise de sentimentos indisponível"
            if batch_result.results:
                candidate_error = next(
                    (r.error_message for r in batch_result.results if r.error_message),
                    None,
                )
                if candidate_error:
                    reason = candidate_error
            return self._build_passthrough_analysis(
                original_data=original_data,
                reason=reason,
                texts=texts_to_analyze,
                results=batch_result.results,
            )

        # Estatísticas de distribuição de sentimentos
        sentiment_distribution = {}
        high_confidence_results = []

        for result, original_item in zip(batch_result.results, original_data):
            if result.success:
                # Contabilizar distribuição de sentimentos
                sentiment = result.sentiment_label
                if sentiment not in sentiment_distribution:
                    sentiment_distribution[sentiment] = 0
                sentiment_distribution[sentiment] += 1

                # Coletar resultados de alta confiança
                if result.confidence >= min_confidence:
                    high_confidence_results.append(
                        {
                            "original_data": original_item,
                            "sentiment": result.sentiment_label,
                            "confidence": result.confidence,
                            "text_preview": result.text[:100] + "..."
                            if len(result.text) > 100
                            else result.text,
                        }
                    )

        # Gerar resumo da análise de sentimentos
        total_analyzed = batch_result.success_count
        if total_analyzed > 0:
            dominant_sentiment = max(sentiment_distribution.items(), key=lambda x: x[1])
            sentiment_summary = f"Total de {total_analyzed} itens analisados, tendência de sentimento predominante: '{dominant_sentiment[0]}' ({dominant_sentiment[1]} itens, representando {dominant_sentiment[1] / total_analyzed * 100:.1f}%)"
        else:
            sentiment_summary = "Análise de sentimentos falhou"

        return {
            "sentiment_analysis": {
                "total_analyzed": total_analyzed,
                "success_rate": f"{batch_result.success_count}/{batch_result.total_processed}",
                "average_confidence": round(batch_result.average_confidence, 4),
                "sentiment_distribution": sentiment_distribution,
                "high_confidence_results": high_confidence_results,  # Retornar todos os resultados de alta confiança, sem limite
                "summary": sentiment_summary,
            }
        }

    def get_model_info(self) -> Dict[str, Any]:
        """
        Obter informações do modelo

        Returns:
            Dicionário de informações do modelo
        """
        return {
            "model_name": "tabularisai/multilingual-sentiment-analysis",
            "supported_languages": [
                "chinês",
                "inglês",
                "espanhol",
                "árabe",
                "japonês",
                "coreano",
                "alemão",
                "francês",
                "italiano",
                "português",
                "russo",
                "holandês",
                "polonês",
                "turco",
                "dinamarquês",
                "grego",
                "finlandês",
                "sueco",
                "norueguês",
                "húngaro",
                "tcheco",
                "búlgaro",
            ],
            "sentiment_levels": list(self.sentiment_map.values()),
            "is_initialized": self.is_initialized,
            "device": str(self.device) if self.device else "não configurado",
        }


# Criar instância global (inicialização tardia)
multilingual_sentiment_analyzer = WeiboMultilingualSentimentAnalyzer()


def enable_sentiment_analysis() -> bool:
    """Public helper to enable sentiment analysis at runtime."""
    return multilingual_sentiment_analyzer.enable()


def disable_sentiment_analysis(
    reason: Optional[str] = None, drop_state: bool = False
) -> None:
    """Public helper to disable sentiment analysis at runtime."""
    multilingual_sentiment_analyzer.disable(reason=reason, drop_state=drop_state)


def analyze_sentiment(
    text_or_texts: Union[str, List[str]], initialize_if_needed: bool = True
) -> Union[SentimentResult, BatchSentimentResult]:
    """
    Função utilitária de análise de sentimentos

    Args:
        text_or_texts: Texto único ou lista de textos
        initialize_if_needed: Se deve inicializar automaticamente caso o modelo não esteja inicializado

    Returns:
        SentimentResult ou BatchSentimentResult
    """
    if (
        initialize_if_needed
        and not multilingual_sentiment_analyzer.is_initialized
        and not multilingual_sentiment_analyzer.is_disabled
    ):
        multilingual_sentiment_analyzer.initialize()

    if isinstance(text_or_texts, str):
        return multilingual_sentiment_analyzer.analyze_single_text(text_or_texts)
    else:
        texts_list = list(text_or_texts)
        return multilingual_sentiment_analyzer.analyze_batch(texts_list)


if __name__ == "__main__":
    # Código de teste
    analyzer = WeiboMultilingualSentimentAnalyzer()

    if analyzer.initialize():
        # Testar texto único
        result = analyzer.analyze_single_text("Que dia lindo hoje, estou me sentindo ótimo!")
        print(
            f"Análise de texto único: {result.sentiment_label} (confiança: {result.confidence:.4f})"
        )

        # Testar textos em lote
        test_texts = [
            "Este restaurante é maravilhoso, a comida é incrível!",
            "O atendimento foi péssimo, estou muito decepcionado",
            "I absolutely love this product!",
            "The customer service was disappointing.",
        ]

        batch_result = analyzer.analyze_batch(test_texts)
        print(
            f"\nAnálise em lote: sucesso {batch_result.success_count}/{batch_result.total_processed}"
        )

        for result in batch_result.results:
            print(
                f"'{result.text[:30]}...' -> {result.sentiment_label} ({result.confidence:.4f})"
            )
    else:
        print("Falha na inicialização do modelo, não é possível realizar testes")
