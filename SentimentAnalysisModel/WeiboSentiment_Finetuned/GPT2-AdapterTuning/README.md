# Modelo de Reconhecimento de Sentimento do Weibo - Fine-tuning GPT2-Adapter

## Descrição do Projeto
Este é um modelo de classificação binária de sentimento do Weibo baseado em GPT2, utilizando a técnica de fine-tuning com Adapter. Através do fine-tuning com Adapter, é necessário treinar apenas uma pequena quantidade de parâmetros para adaptar o modelo à tarefa de análise de sentimento, reduzindo significativamente os requisitos de recursos computacionais e o tamanho do modelo.

## Dataset
Utiliza o dataset de sentimento do Weibo (weibo_senti_100k), contendo aproximadamente 100 mil postagens do Weibo com anotação de sentimento, com cerca de 50 mil comentários positivos e 50 mil negativos. Rótulos do dataset:
- Rótulo 0: Sentimento negativo
- Rótulo 1: Sentimento positivo

## Estrutura de Arquivos
```
GPT2-Adpter-tuning/
├── adapter.py              # Implementação da camada Adapter
├── gpt2_adapter.py         # Implementação do Adapter para o modelo GPT2
├── train.py                # Script de treinamento
├── predict.py              # Script de predição simplificado (uso interativo)
├── models/                 # Modelos pré-treinados armazenados localmente
│   └── gpt2-chinese/       # Modelo GPT2 chinês e configurações
├── dataset/                # Diretório do dataset
│   └── weibo_senti_100k.csv  # Dataset de sentimento do Weibo
└── best_weibo_sentiment_model.pth  # Melhor modelo treinado
```

## Características Técnicas

1. **Fine-tuning eficiente em parâmetros**: Comparado ao fine-tuning completo, treina apenas cerca de 3% dos parâmetros
2. **Manutenção do desempenho**: Mantém bom desempenho de classificação mesmo treinando poucos parâmetros
3. **Adequado para ambientes com recursos limitados**: Modelo compacto com inferência rápida

## Dependências do Ambiente
- Python 3.6+
- PyTorch
- Transformers
- Pandas
- NumPy
- Scikit-learn
- Tqdm

## Método de Uso

### Treinar o Modelo
```bash
python train.py
```
O processo de treinamento executará automaticamente:
- Download e salvamento local do modelo pré-treinado GPT2 chinês
- Carregamento do dataset de sentimento do Weibo
- Treinamento do modelo e salvamento do melhor modelo

### Predição de Análise de Sentimento
```bash
python predict.py
```
Após a execução, entrará no modo interativo:
- Digite o texto do Weibo a ser analisado no console
- O sistema retornará o resultado da análise de sentimento (positivo/negativo) e a confiança
- Digite 'q' para sair do programa

## Estrutura do Modelo
- Modelo base: modelo pré-treinado chinês `uer/gpt2-chinese-cluecorpussmall`
- Caminho local de salvamento do modelo: `./models/gpt2-chinese/`
- Fine-tuning realizado adicionando camadas Adapter após cada GPT2Block
- Parâmetros originais do GPT2 congelados, treinando apenas os parâmetros do classificador e das camadas Adapter

## Tecnologia Adapter
Adapter é uma técnica de fine-tuning eficiente em parâmetros que, ao inserir pequenas camadas gargalo nas camadas Transformer, permite adaptar-se a tarefas downstream com poucos parâmetros. Principais características:

1. **Eficiência em parâmetros**: Comparado ao fine-tuning completo, o Adapter precisa treinar apenas uma pequena fração dos parâmetros
2. **Prevenção de esquecimento**: Mantém os parâmetros do modelo pré-treinado original inalterados, evitando esquecimento catastrófico
3. **Adaptação a múltiplas tarefas**: É possível treinar Adapters diferentes para tarefas diferentes, compartilhando o mesmo modelo base

Neste projeto, adicionamos uma camada Adapter após cada GPT2Block, com tamanho da camada oculta do Adapter de 64, muito menor que o tamanho da camada oculta do modelo original (geralmente 768 ou 1024).

## Exemplo de Uso
```
Dispositivo utilizado: cuda
Carregando modelo: best_weibo_sentiment_model.pth

============= Análise de Sentimento do Weibo =============
Digite o conteúdo do Weibo para análise (digite 'q' para sair):

Digite o conteúdo do Weibo: 这部电影真是太好看了，我非常喜欢！
Resultado da predição: Sentimento Positivo (confiança: 0.9876)

Digite o conteúdo do Weibo: 服务态度差，价格还贵，一点都不推荐
Resultado da predição: Sentimento Negativo (confiança: 0.9742)
```

## Observações
- O script de predição usa o caminho local do modelo, não sendo necessário download online
- Certifique-se de que o diretório `models/gpt2-chinese/` contém os arquivos do modelo salvos durante o processo de treinamento
- Na primeira execução do train.py, o modelo será baixado e salvo automaticamente; certifique-se de ter conexão com a internet
