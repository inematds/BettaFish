# Análise de Sentimento Multilíngue - Multilingual Sentiment Analysis

Este módulo utiliza um modelo de análise de sentimento multilíngue do HuggingFace, com suporte a 22 idiomas.

## Informações do Modelo

- **Nome do modelo**: tabularisai/multilingual-sentiment-analysis
- **Modelo base**: distilbert-base-multilingual-cased
- **Idiomas suportados**: 22 idiomas, incluindo:
  - 中文 (Chinês)
  - English (Inglês)
  - Español (Espanhol)
  - 日本語 (Japonês)
  - 한국어 (Coreano)
  - Français (Francês)
  - Deutsch (Alemão)
  - Русский (Russo)
  - العربية (Árabe)
  - हिन्दी (Hindi)
  - Português (Português)
  - Italiano (Italiano)
  - Etc...

- **Categorias de saída**: Classificação de sentimento em 5 níveis
  - Muito Negativo (Very Negative)
  - Negativo (Negative)
  - Neutro (Neutral)
  - Positivo (Positive)
  - Muito Positivo (Very Positive)

## Início Rápido

1. Certifique-se de que as dependências estão instaladas:
```bash
pip install transformers torch
```

2. Execute o programa de predição:
```bash
python predict.py
```

3. Insira texto em qualquer idioma para análise:
```
Digite o texto: I love this product!
Resultado da predição: Muito Positivo (confiança: 0.9456)
```

4. Veja exemplos multilíngues:
```
Digite o texto: demo
```

## Exemplo de Código

```python
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch

# Carregar modelo
model_name = "tabularisai/multilingual-sentiment-analysis"
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForSequenceClassification.from_pretrained(model_name)

# Predição
texts = [
    "今天心情很好",  # Chinês
    "I love this!",  # Inglês
    "¡Me encanta!"   # Espanhol
]

for text in texts:
    inputs = tokenizer(text, return_tensors="pt")
    outputs = model(**inputs)
    prediction = torch.argmax(outputs.logits, dim=1).item()
    sentiment_map = {0: "Muito Negativo", 1: "Negativo", 2: "Neutro", 3: "Positivo", 4: "Muito Positivo"}
    print(f"{text} -> {sentiment_map[prediction]}")
```

## Funcionalidades em Destaque

- **Suporte multilíngue**: Reconhece automaticamente 22 idiomas sem necessidade de especificação
- **Classificação detalhada em 5 níveis**: Análise de sentimento mais refinada que a classificação binária tradicional
- **Alta precisão**: Arquitetura avançada baseada em DistilBERT
- **Cache local**: Após o primeiro download, salva localmente para acelerar o uso subsequente

## Cenários de Aplicação

- Monitoramento de mídias sociais internacionais
- Análise de feedback de clientes multilíngue
- Classificação de sentimento de avaliações de produtos globais
- Rastreamento de sentimento de marca entre idiomas
- Otimização de atendimento ao cliente multilíngue
- Pesquisa de mercado internacional

## Armazenamento do Modelo

- Na primeira execução, o modelo será baixado automaticamente para a pasta `model` no diretório atual
- Execuções subsequentes carregarão diretamente do local, sem necessidade de novo download
- O tamanho do modelo é de aproximadamente 135MB; o primeiro download requer conexão com a internet

## Descrição dos Arquivos

- `predict.py`: Programa principal de predição, usando chamada direta ao modelo
- `README.md`: Instruções de uso

## Observações

- Na primeira execução, o modelo será baixado automaticamente, sendo necessária conexão com a internet
- O modelo será salvo no diretório atual para facilitar o uso posterior
- Suporta aceleração por GPU, detectando automaticamente dispositivos disponíveis
- Para limpar os arquivos do modelo, basta excluir a pasta `model`
- Este modelo foi treinado com dados sintéticos; recomenda-se validação em aplicações reais
