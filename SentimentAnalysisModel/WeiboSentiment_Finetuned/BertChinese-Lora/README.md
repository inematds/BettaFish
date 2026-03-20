# Análise de Sentimento do Weibo - Modelo Fine-tuned baseado em BertChinese

Este módulo utiliza um modelo pré-treinado de análise de sentimento do Weibo disponível no HuggingFace.

## Informações do Modelo

- **Nome do modelo**: wsqstar/GISchat-weibo-100k-fine-tuned-bert
- **Tipo de modelo**: Modelo de classificação de sentimento BERT Chinês
- **Dados de treinamento**: 100 mil postagens do Weibo
- **Saída**: Classificação binária (sentimento positivo/negativo)

## Método de Uso

### Método 1: Chamada direta ao modelo (recomendado)
```bash
python predict.py
```

### Método 2: Via Pipeline
```bash
python predict_pipeline.py
```

## Início Rápido

1. Certifique-se de que as dependências estão instaladas:
```bash
pip install transformers torch
```

2. Execute o programa de predição:
```bash
python predict.py
```

3. Insira o texto do Weibo para análise:
```
Digite o conteúdo do Weibo: 今天天气真好，心情特别棒！
Resultado da predição: Sentimento Positivo (confiança: 0.9234)
```

## Exemplo de Código

```python
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch

# Carregar modelo
model_name = "wsqstar/GISchat-weibo-100k-fine-tuned-bert"
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForSequenceClassification.from_pretrained(model_name)

# Predição
text = "今天心情很好"
inputs = tokenizer(text, return_tensors="pt")
outputs = model(**inputs)
prediction = torch.argmax(outputs.logits, dim=1).item()
print("Sentimento Positivo" if prediction == 1 else "Sentimento Negativo")
```

## Descrição dos Arquivos

- `predict.py`: Programa principal de predição, usando chamada direta ao modelo
- `predict_pipeline.py`: Programa de predição usando pipeline
- `README.md`: Instruções de uso

## Armazenamento do Modelo

- Na primeira execução, o modelo será baixado automaticamente para a pasta `model` no diretório atual
- Execuções subsequentes carregarão diretamente do local, sem necessidade de novo download
- O tamanho do modelo é de aproximadamente 400MB; o primeiro download requer conexão com a internet

## Observações

- Na primeira execução, o modelo será baixado automaticamente, sendo necessária conexão com a internet
- O modelo será salvo no diretório atual para facilitar o uso posterior
- Suporta aceleração por GPU, detectando automaticamente dispositivos disponíveis
- Para limpar os arquivos do modelo, basta excluir a pasta `model`
