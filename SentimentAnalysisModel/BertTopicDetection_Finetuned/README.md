## Classificação de Tópicos (Base BERT Chinês)

Este diretório fornece uma implementação de classificação de tópicos em chinês usando `google-bert/bert-base-chinese`:
- Processamento automático com lógica de carregamento em três etapas: local/cache/remoto;
- `train.py` para treinamento de fine-tuning; `predict.py` para predição individual ou interativa;
- Todos os modelos e pesos são salvos de forma unificada no diretório `model/` deste diretório.

Cartão de referência do modelo: [google-bert/bert-base-chinese](https://huggingface.co/google-bert/bert-base-chinese)

### Destaques do Dataset

- Aproximadamente **4,1 milhões** de perguntas e respostas pré-filtradas de alta qualidade;
- Cada pergunta corresponde a um "【Tópico】", cobrindo **aproximadamente 28 mil** temas diversos;
- Filtrado a partir de **14 milhões** de perguntas e respostas originais, mantendo apenas respostas com pelo menos **3 curtidas ou mais**, garantindo qualidade e relevância do conteúdo;
- Além da pergunta, tópico e uma ou mais respostas, cada resposta também inclui número de curtidas, ID da resposta e tags do respondente;
- Após limpeza e deduplicação dos dados, dividido em três partes: o conjunto de treinamento tem aproximadamente **4,12 milhões** de exemplos, com conjuntos de validação/teste ajustáveis conforme necessário.

> No treinamento real, utilize os CSVs em `dataset/` como referência; o script reconhece automaticamente nomes de colunas comuns ou permite especificação explícita via parâmetros de comando.

### Estrutura de Diretórios

```
BertTopicDetection_Finetuned/
  ├─ dataset/                   # Dados já inseridos
  ├─ model/                     # Gerado pelo treinamento; também armazena cache do BERT base
  ├─ train.py
  ├─ predict.py
  └─ README.md
```

### Ambiente

```
pip install torch transformers scikit-learn pandas
```

Ou use seu ambiente Conda existente.

### Formato dos Dados

O CSV deve conter pelo menos uma coluna de texto e uma coluna de rótulo; o script tentará identificá-las automaticamente:
- Candidatas para coluna de texto: `text`/`content`/`sentence`/`title`/`desc`/`question`
- Candidatas para coluna de rótulo: `label`/`labels`/`category`/`topic`/`class`

Para especificar explicitamente, use `--text_col` e `--label_col`.

### Treinamento

```
python train.py \
  --train_file ./dataset/web_text_zh_train.csv \
  --valid_file ./dataset/web_text_zh_valid.csv \
  --text_col auto \
  --label_col auto \
  --model_root ./model \
  --save_subdir bert-chinese-classifier \
  --num_epochs 10 --batch_size 16 --learning_rate 2e-5 --fp16
```

Pontos importantes:
- Na primeira execução, o script verifica `model/bert-base-chinese`; se não existir, tenta o cache local e, caso contrário, baixa e salva automaticamente;
- O processo de treinamento avalia e salva por passos (por padrão a cada 1/4 de epoch), mantendo no máximo 5 checkpoints recentes (ajustável pela variável de ambiente `SAVE_TOTAL_LIMIT`);
- Suporta early stopping (paciência padrão de 5 avaliações) e, quando a estratégia de avaliação/salvamento é consistente, reverte automaticamente para o melhor modelo;
- O tokenizador, pesos e `label_map.json` são salvos em `model/bert-chinese-classifier/`.

### Modelos Base Chineses Opcionais (seleção interativa antes do treinamento)

Modelo base padrão: `google-bert/bert-base-chinese`. Ao iniciar o treinamento, se o terminal for interativo, o programa solicitará a seleção entre as seguintes opções (ou a inserção de qualquer ID de modelo do Hugging Face):

1) `google-bert/bert-base-chinese`
2) `hfl/chinese-roberta-wwm-ext-large`
3) `hfl/chinese-macbert-large`
4) `IDEA-CCNL/Erlangshen-DeBERTa-v2-710M-Chinese`
5) `IDEA-CCNL/Erlangshen-DeBERTa-v3-Base-Chinese`
6) `Langboat/mengzi-bert-base`
7) `BAAI/bge-base-zh` (mais adequado para paradigma de recuperação/aprendizado contrastivo)
8) `nghuyong/ernie-3.0-base-zh`

Observações:
- Em ambientes não interativos (como sistemas de agendamento) ou ao definir `NON_INTERACTIVE=1`, o modelo especificado pelo argumento de linha de comando `--pretrained_name` será usado diretamente (padrão: `google-bert/bert-base-chinese`).
- Após a seleção, o modelo base será baixado/armazenado em cache no diretório `model/`, com gerenciamento unificado.

### Predição

Individual:
```
python predict.py --text "这条微博讨论的是哪个话题？" --model_root ./model --finetuned_subdir bert-chinese-classifier
```

Interativa:
```
python predict.py --interactive --model_root ./model --finetuned_subdir bert-chinese-classifier
```

Exemplo de saída:
```
Resultado da predição: Esportes-Futebol (confiança: 0.9412)
```

### Notas

- Tanto o treinamento quanto a predição incluem limpeza básica de texto em chinês integrada.
- O conjunto de rótulos é baseado no conjunto de treinamento; o script gera e salva automaticamente o `label_map.json`.

### Estratégia de Treinamento (Resumo)

- Base: `google-bert/bert-base-chinese`; dimensão da camada de classificação = número de rótulos únicos no conjunto de treinamento.
- Taxa de aprendizado e regularização: `lr=2e-5`, `weight_decay=0.01`, podendo ser ajustado para `1e-5~3e-5` em datasets grandes.
- Comprimento da sequência e batch: `max_length=128`, `batch_size=16`; se houver truncamento excessivo, pode ser aumentado para 256 (com aumento de custo).
- Warmup: se o ambiente suportar, usa `warmup_ratio=0.1`; caso contrário, faz fallback para `warmup_steps=0`.
- Avaliação/Salvamento: calcula passos com base em `--eval_fraction` (padrão 0.25), `save_total_limit=5` para limitar o uso de disco.
- Early stopping: monitora F1 ponderado (quanto maior, melhor), paciência padrão de 5, limiar de melhoria de 0.0.
- Execução estável em GPU única: usa apenas uma GPU por padrão, podendo ser especificada via `--gpu`; o script limpa variáveis de ambiente distribuídas.


### Nota do Autor (sobre classificação multi-classe em larga escala)

- Quando as categorias de tópicos chegam a dezenas de milhares, usar uma única camada de classificação linear após o encoder (softmax grande) geralmente é limitante: categorias de cauda longa são difíceis de aprender, esparsidade semântica, novos tópicos não podem ser adaptados incrementalmente, e o modelo precisa ser retreinado frequentemente após o deploy.
- Abordagens de melhoria (em ordem de prioridade recomendada):
  - Paradigma de recuperação/torre dupla (texto vs. nome/descrição do tópico com aprendizado contrastivo) + busca por vizinhos próximos + re-ranking com cabeçote leve, com suporte natural a expansão incremental de classes e atualização rápida;
  - Classificação hierárquica (primeiro grossa, depois fina), reduzindo significativamente a dificuldade e computação de um único cabeçote;
  - Modelagem conjunta texto-rótulo (usando descrições de rótulos), melhorando a transferibilidade para tópicos sinônimos;
  - Detalhes de treinamento: class-balanced/focal/label smoothing, sampled softmax, pré-treinamento contrastivo, etc.
- Declaração importante: O "fine-tuning com cabeçote de classificação estático" usado neste diretório serve apenas como alternativa e referência de aprendizado. Para cenários de microtextos em inglês/multilíngue, os tópicos mudam extremamente rápido, e classificadores estáticos tradicionais dificilmente conseguem acompanhar. Nosso foco de trabalho está em direções como `TopicGPT` e outras abordagens generativas/autossupervisionadas de descoberta de tópicos e construção de sistemas dinâmicos; esta implementação visa fornecer uma baseline executável e um exemplo de engenharia.

