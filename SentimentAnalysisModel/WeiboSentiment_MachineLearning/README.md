# Análise de Sentimento do Weibo - Métodos de Aprendizado de Máquina Tradicionais

## Introdução do Projeto

Este projeto utiliza 5 métodos tradicionais de aprendizado de máquina para classificação binária de sentimento (positivo/negativo) em postagens do Weibo em chinês:

- **Naive Bayes**: Classificação probabilística baseada em modelo bag-of-words
- **SVM**: Máquina de vetores de suporte baseada em características TF-IDF
- **XGBoost**: Árvore de decisão com gradient boosting
- **LSTM**: Rede neural recorrente + vetores de palavras Word2Vec
- **BERT + cabeçote de classificação**: Modelo de linguagem pré-treinado com classificador (que também considero pertencente ao escopo de ML tradicional)

## Desempenho dos Modelos

Desempenho no dataset de sentimento do Weibo (conjunto de treinamento com 10.000 amostras, conjunto de teste com 500 amostras):

| Modelo | Acurácia | AUC | Características |
|------|--------|-----|------|
| Naive Bayes | 85,6% | - | Rápido, baixo consumo de memória |
| SVM | 85,6% | - | Boa capacidade de generalização |
| XGBoost | 86,0% | 90,4% | Desempenho estável, suporta importância de features |
| LSTM | 87,0% | 93,1% | Compreende informação sequencial e contexto |
| BERT + cabeçote de classificação | 87,0% | 92,9% | Forte capacidade de compreensão semântica |

## Configuração do Ambiente

```bash
pip install -r requirements.txt
```

Estrutura dos arquivos de dados:
```
data/
├── weibo2018/
│   ├── train.txt
│   └── test.txt
└── stopwords.txt
```

## Treinar Modelos (podem ser executados diretamente sem argumentos)

### Naive Bayes
```bash
python bayes_train.py
```

### SVM
```bash
python svm_train.py --kernel rbf --C 1.0
```

### XGBoost
```bash
python xgboost_train.py --max_depth 6 --eta 0.3 --num_boost_round 200
```

### LSTM
```bash
python lstm_train.py --epochs 5 --batch_size 100 --hidden_size 64
```

### BERT
```bash
python bert_train.py --epochs 10 --batch_size 100 --learning_rate 1e-3
```

Nota: O modelo BERT baixará automaticamente o modelo pré-treinado em chinês (bert-base-chinese)

## Uso para Predição

### Predição interativa (recomendada)
```bash
python predict.py
```

### Predição via linha de comando
```bash
# Predição com modelo único
python predict.py --model_type bert --text "今天天气真好，心情很棒"

# Predição com ensemble de múltiplos modelos
python predict.py --ensemble --text "这部电影太无聊了"
```

## Estrutura de Arquivos

```
WeiboSentiment_MachineLearning/
├── bayes_train.py           # Treinamento Naive Bayes
├── svm_train.py             # Treinamento SVM
├── xgboost_train.py         # Treinamento XGBoost
├── lstm_train.py            # Treinamento LSTM
├── bert_train.py            # Treinamento BERT
├── predict.py               # Programa de predição unificado
├── base_model.py            # Classe base do modelo
├── utils.py                 # Funções utilitárias
├── requirements.txt         # Dependências
├── model/                   # Diretório de salvamento dos modelos
└── data/                    # Diretório de dados
```

## Observações

1. O **modelo BERT** baixará automaticamente o modelo pré-treinado na primeira execução (~400MB)
2. O **modelo LSTM** tem tempo de treinamento mais longo; recomenda-se o uso de GPU
3. Os **modelos são salvos** no diretório `model/`; certifique-se de ter espaço suficiente em disco
4. **Requisitos de memória**: BERT > LSTM > XGBoost > SVM > Naive Bayes
