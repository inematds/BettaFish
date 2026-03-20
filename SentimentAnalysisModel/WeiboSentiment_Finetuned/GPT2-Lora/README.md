# Modelo de Reconhecimento de Sentimento do Weibo - Fine-tuning GPT2-LoRA

## Descrição do Projeto
Este é um modelo de classificação binária de sentimento do Weibo baseado em GPT2, utilizando a técnica de fine-tuning LoRA (Low-Rank Adaptation). Através do LoRA implementado pela biblioteca PEFT, é necessário treinar uma quantidade extremamente pequena de parâmetros para adaptar o modelo à tarefa de análise de sentimento, reduzindo significativamente os requisitos de recursos computacionais e o tamanho do modelo.

## Dataset
Utiliza o dataset de sentimento do Weibo (weibo_senti_100k), contendo aproximadamente 100 mil postagens do Weibo com anotação de sentimento, com cerca de 50 mil comentários positivos e 50 mil negativos. Rótulos do dataset:
- Rótulo 0: Sentimento negativo
- Rótulo 1: Sentimento positivo

## Estrutura de Arquivos
```
GPT2-Lora/
├── train.py                  # Script de treinamento (implementação LoRA baseada na biblioteca PEFT)
├── predict.py                # Script de predição (uso interativo)
├── requirements.txt          # Lista de dependências
├── models/                   # Modelos pré-treinados armazenados localmente
│   └── gpt2-chinese/        # Modelo GPT2 chinês e configurações
├── dataset/                  # Diretório do dataset
│   └── weibo_senti_100k.csv # Dataset de sentimento do Weibo
└── best_weibo_sentiment_lora/ # Pesos LoRA treinados (gerado após o treinamento)
```

## Características Técnicas

1. **Extremamente eficiente em parâmetros**: Comparado ao fine-tuning completo, treina apenas cerca de 0,1%-1% dos parâmetros
2. **Utiliza a biblioteca PEFT**: Baseado na biblioteca oficial de fine-tuning eficiente em parâmetros da Hugging Face, estável e confiável
3. **Manutenção do desempenho**: Mantém bom desempenho de classificação mesmo treinando pouquíssimos parâmetros
4. **Amigável para deploy**: Arquivos de pesos LoRA são pequenos, facilitando o deploy e compartilhamento do modelo

## Vantagens da Tecnologia LoRA

LoRA (Low-Rank Adaptation) é atualmente a técnica de fine-tuning eficiente em parâmetros mais popular:

1. **Quantidade ultra baixa de parâmetros**: Através da decomposição de posto baixo, decompõe matrizes grandes no produto de duas matrizes menores
2. **Design plug-in**: Pesos LoRA podem ser carregados e descarregados dinamicamente, um modelo base suporta múltiplas tarefas
3. **Treinamento rápido**: Poucos parâmetros, tempo de treinamento curto, baixo consumo de memória
4. **Modelo original intacto**: Os pesos do modelo pré-treinado original permanecem inalterados, evitando esquecimento catastrófico

## Dependências do Ambiente

Instale as dependências necessárias:
```bash
pip install -r requirements.txt
```

Principais dependências:
- Python 3.8+
- PyTorch 1.13+
- Transformers 4.28+
- PEFT 0.4+
- Pandas, NumPy, Scikit-learn

## Método de Uso

### 1. Instalar Dependências
```bash
pip install -r requirements.txt
```

### 2. Treinar o Modelo
```bash
python train.py
```

O processo de treinamento executará automaticamente:
- Download e salvamento local do modelo pré-treinado GPT2 chinês
- Carregamento do dataset de sentimento do Weibo
- Treinamento do modelo usando técnica LoRA
- Salvamento dos melhores pesos LoRA em `./best_weibo_sentiment_lora/`

### 3. Predição de Análise de Sentimento
```bash
python predict.py
```

Após a execução, entrará no modo interativo:
- Digite o texto do Weibo a ser analisado no console
- O sistema retornará o resultado da análise de sentimento (positivo/negativo) e a confiança
- Digite 'q' para sair do programa

## Configuração do Modelo

- **Modelo base**: modelo pré-treinado chinês `uer/gpt2-chinese-cluecorpussmall`
- **Caminho local de salvamento do modelo**: `./models/gpt2-chinese/`
- **Configuração LoRA**:
  - rank (r): 8 - Posto da matriz de posto baixo
  - alpha: 32 - Fator de escala
  - target_modules: ["c_attn", "c_proj"] - Camadas lineares alvo
  - dropout: 0.1 - Prevenção de overfitting

## Comparação de Desempenho

| Método | Proporção de parâmetros treináveis | Tamanho do arquivo do modelo | Tempo de treinamento | Velocidade de inferência |
|------|----------------|--------------|----------|----------|
| Fine-tuning completo | 100% | ~500MB | Longo | Lenta |
| Fine-tuning Adapter | ~3% | ~50MB | Médio | Média |
| **Fine-tuning LoRA** | **~0,5%** | **~2MB** | **Curto** | **Rápida** |

## Exemplo de Uso

```
Dispositivo utilizado: cuda
Modelo LoRA carregado com sucesso!

============= Análise de Sentimento do Weibo (versão LoRA) =============
Digite o conteúdo do Weibo para análise (digite 'q' para sair):

Digite o conteúdo do Weibo: 这部电影真是太好看了，我非常喜欢！
Resultado da predição: Sentimento Positivo (confiança: 0.9876)

Digite o conteúdo do Weibo: 服务态度差，价格还贵，一点都不推荐
Resultado da predição: Sentimento Negativo (confiança: 0.9742)

Digite o conteúdo do Weibo: q
```

## Observações

1. **Primeira execução**: Na primeira execução do `train.py`, o modelo pré-treinado será baixado automaticamente; certifique-se de ter conexão com a internet
2. **GPU recomendada**: Embora o LoRA tenha poucos parâmetros, recomenda-se usar GPU para acelerar o treinamento
3. **Carregamento do modelo**: Para a predição, é necessário ter os arquivos de pesos LoRA treinados previamente
4. **Compatibilidade**: Baseado na biblioteca PEFT, totalmente compatível com o ecossistema Hugging Face

## Funcionalidades Estendidas

- **Suporte a múltiplas tarefas**: É possível treinar pesos LoRA diferentes para tarefas diferentes, compartilhando o mesmo modelo base
- **Fusão de pesos**: É possível fundir múltiplos pesos LoRA ou fundir pesos LoRA ao modelo base
- **Alternância dinâmica**: Suporta carregamento e alternância dinâmica de diferentes pesos LoRA em tempo de execução

## Princípio Técnico

O LoRA adiciona duas matrizes pequenas A e B ao lado da camada linear original, de modo que:
```
h = W₀x + BAx
```
Onde:
- W₀ são os pesos pré-treinados congelados
- B ∈ ℝᵈˣʳ, A ∈ ℝʳˣᵏ são matrizes de posto baixo treináveis
- r << min(d,k), reduzindo significativamente a quantidade de parâmetros

Esse design preserva o conhecimento do modelo pré-treinado e permite a adaptação eficiente a novas tarefas.
