# Relatório Completo do Projeto BettaFish BR

## Visão Geral

Portação completa do sistema BettaFish (originalmente chinês) para o mercado brasileiro e latino-americano, incluindo tradução, adaptação de APIs, fontes de dados e interface.

---

## 1. O que é o BettaFish

Sistema multi-agente de análise de opinião pública que utiliza IA para coletar, analisar e gerar relatórios sobre qualquer tema. Originalmente desenvolvido para o mercado chinês pelo repositório [666ghj/BettaFish](https://github.com/666ghj/BettaFish).

### Arquitetura dos Engines

```
Usuário digita pergunta
        │
        ├──→ InsightEngine (banco local)  ──┐
        ├──→ MediaEngine (web multimodal)  ──┼──→ ForumEngine (debate) ──→ ReportEngine (relatório)
        └──→ QueryEngine (web textual)    ──┘
```

| Engine | Porta | Função |
|--------|-------|--------|
| **InsightEngine** | 8501 | Minera o banco de dados local (PostgreSQL). Análise de sentimento. |
| **MediaEngine** | 8502 | Busca multimodal na web (Tavily). Analisa imagens e vídeos. |
| **QueryEngine** | 8503 | Busca textual na web (Tavily). Pesquisa artigos e notícias. |
| **ForumEngine** | - | Moderador de debates entre os agentes. |
| **ReportEngine** | - | Gera relatório final em HTML/PDF com gráficos e tabelas. |
| **MindSpider** | - | Crawler que coleta dados de notícias e redes sociais. |

---

## 2. O que foi feito

### Fase 0 — Tradução completa (161+ arquivos)
- README.md e todos os sub-READMEs traduzidos para PT-BR
- Interface web (templates/index.html) traduzida
- Todos os arquivos Python: comentários, logs, prompts, docstrings
- Arquivos de configuração (.env.example)

### Fase 1 — Busca web global
- MediaEngine agora usa **Tavily** como provedor padrão (substituindo Bocha chinesa)
- Classe `TavilyMultimodalSearch` criada com mesma interface da `BochaMultimodalSearch`
- Prompts atualizados com plataformas ocidentais (Twitter, Instagram, YouTube, TikTok, Reddit)
- Gírias e exemplos brasileiros nos prompts

### Fase 2 — Fontes de notícias internacionais
- **24 fontes RSS** substituindo as 12 chinesas, organizadas por região:
  - **Brasil** (6): G1, Folha, Estadão, UOL, Valor Econômico, InfoMoney
  - **EUA** (4): Reuters, AP, CNN, NYT
  - **Europa** (4): BBC, DW, France24, Euronews
  - **América do Sul** (9): Clarín, EMOL, El Tiempo, El Comercio, El País, ABC Color, El Deber, El Universo, Últimas Notícias
  - **Global** (1): GitHub Trending
- Painel de configuração na interface com toggles por região
- Endpoints `/api/update-news` e `/api/news-status`
- Coleta via RSS feeds (sem dependência de API chinesa)

### Fase 3 — Análise de sentimento em português
- Modelo `tabularisai/multilingual-sentiment-analysis` já suporta português nativamente
- Testes e prompts atualizados para português
- Labels de sentimento em PT-BR: muito negativo, negativo, neutro, positivo, muito positivo

### Infraestrutura Docker
- Imagem Docker local (`bettafish:local`) com código traduzido
- Correção de `host.docker.internal` no Linux via `extra_hosts`
- `.dockerignore` atualizado para excluir db_data e logs do build
- Volumes montados para logs, relatórios e configuração

---

## 3. Configuração e Uso

### Pré-requisitos
- Docker e Docker Compose
- Ollama rodando localmente (ou outra API compatível com OpenAI)

### Instalação rápida

```bash
# Clonar o repositório
git clone git@github.com:inematds/BettaFish.git
cd BettaFish

# Criar arquivo de configuração
cp .env.example .env
# Editar .env com suas configurações (ver seção abaixo)

# Build e iniciar
docker compose build
docker compose up -d
```

### Configuração do .env

```bash
# ====================== BANCO DE DADOS ======================
DB_HOST=db
DB_PORT=5432
DB_USER=bettafish
DB_PASSWORD=bettafish
DB_NAME=bettafish
DB_DIALECT=postgresql

# ====================== POSTGRES CONTAINER ======================
POSTGRES_USER=bettafish
POSTGRES_PASSWORD=bettafish
POSTGRES_DB=bettafish

# ======================= LLMs =======================
# Timeout aumentado para modelos locais
LLM_REQUEST_TIMEOUT=3600

# Com Ollama local (recomendado qwen2.5:32b para 3 engines em paralelo)
INSIGHT_ENGINE_API_KEY=ollama
INSIGHT_ENGINE_BASE_URL=http://host.docker.internal:11434/v1
INSIGHT_ENGINE_MODEL_NAME=qwen2.5:32b

MEDIA_ENGINE_API_KEY=ollama
MEDIA_ENGINE_BASE_URL=http://host.docker.internal:11434/v1
MEDIA_ENGINE_MODEL_NAME=qwen2.5:32b

QUERY_ENGINE_API_KEY=ollama
QUERY_ENGINE_BASE_URL=http://host.docker.internal:11434/v1
QUERY_ENGINE_MODEL_NAME=qwen2.5:32b

REPORT_ENGINE_API_KEY=ollama
REPORT_ENGINE_BASE_URL=http://host.docker.internal:11434/v1
REPORT_ENGINE_MODEL_NAME=qwen2.5:32b

FORUM_HOST_API_KEY=ollama
FORUM_HOST_BASE_URL=http://host.docker.internal:11434/v1
FORUM_HOST_MODEL_NAME=qwen2.5:32b

KEYWORD_OPTIMIZER_API_KEY=ollama
KEYWORD_OPTIMIZER_BASE_URL=http://host.docker.internal:11434/v1
KEYWORD_OPTIMIZER_MODEL_NAME=qwen2.5:32b

# ================== BUSCADORES ====================
TAVILY_API_KEY=sua_chave_tavily_aqui
SEARCH_TOOL_TYPE=TavilyAPI
```

#### Nota sobre modelos
- **qwen2.5:32b** — Recomendado para rodar 3 engines em paralelo com Ollama local
- **qwen2.5:72b** — Melhor qualidade mas causa timeout com 3 engines simultâneos
- Pode usar qualquer API compatível com formato OpenAI (GPT-4o, Claude, DeepSeek, etc.)

### Uso

1. Acesse **http://localhost:5000**
2. Clique no ícone de **engrenagem** (configurações)
3. Clique em **"Salvar e iniciar sistema"** (não apenas "Salvar")
4. Aguarde os 3 engines iniciarem (~30 segundos)
5. Digite sua consulta na caixa de texto e envie
6. Aguarde o processamento (5-30 min dependendo do modelo)
7. O relatório será gerado automaticamente em HTML/PDF

### Painel de Fontes de Notícias
- Na engrenagem, role até "Fontes de Notícias"
- Ative/desative regiões (Brasil, EUA, Europa, América do Sul)
- Clique "Atualizar Notícias" para coletar RSS

### Portas

| Porta | Serviço |
|-------|---------|
| 5000 | Interface web (Flask) |
| 8501 | InsightEngine (Streamlit) |
| 8502 | MediaEngine (Streamlit) |
| 8503 | QueryEngine (Streamlit) |
| 5444 | PostgreSQL |

---

## 4. O que funciona agora

| Recurso | Status |
|---------|--------|
| Busca web global (Tavily) | Funciona |
| Busca multimodal (Tavily) | Funciona |
| Debate entre agentes (ForumEngine) | Funciona |
| Geração de relatórios HTML/PDF | Funciona |
| Coleta de notícias RSS (24 fontes) | Funciona |
| Análise de sentimento em português | Funciona |
| Interface web em português | Funciona |
| LLM via Ollama local | Funciona |

---

## 5. O que ainda não funciona (Fase 4 — Pendente)

| Recurso | Motivo |
|---------|--------|
| Coleta de posts do Twitter/X, Instagram, Reddit, YouTube | Crawlers não implementados |
| Coleta de comentários de redes sociais | Sem crawler para plataformas ocidentais |
| Análise de volume estatístico massivo | InsightEngine depende de dados coletados |
| Comparação entre plataformas | Sem dados de múltiplas redes |

### Plano da Fase 4 — Crawlers Ocidentais

Abordagem híbrida: **Apify + APIs oficiais** (~6 dias, ~$540/mês)

| Plataforma | Ferramenta | Custo/mês |
|-----------|-----------|----------|
| Reddit | PRAW (API oficial) | $0 |
| YouTube | Google API v3 | $0 |
| Twitter/X | Apify (Tweet Scraper) | ~$60 |
| Instagram | Apify (Instagram Scraper) | ~$225 |
| TikTok | Apify (TikTok Scraper) | ~$255 |

Firecrawl foi descartado (bloqueia redes sociais). LinkedIn não recomendado (risco legal).

---

## 6. Problemas conhecidos e soluções

| Problema | Solução |
|----------|---------|
| `host.docker.internal` não resolve no Linux | Adicionado `extra_hosts: host.docker.internal:host-gateway` no docker-compose.yml |
| MediaEngine exigia Bocha API | Adicionado suporte a TavilyAPI no Streamlit app e config |
| Timeout com qwen2.5:72b (3 engines paralelos) | Usar qwen2.5:32b + LLM_REQUEST_TIMEOUT=3600 |
| Interface mostra mensagens antigas após restart | Recarregar página (F5) |
| Logs em chinês no container | Rebuildar imagem com `docker compose build` |
| Sistema não aceita consultas | Clicar "Salvar e iniciar sistema" (não apenas "Salvar") |

---

## 7. Estrutura de arquivos modificados

```
BettaFish/
├── README.md                    # Traduzido + seção de infraestrutura
├── RELATORIO_PROJETO.md         # Este documento
├── .env.example                 # Traduzido
├── .dockerignore                # Atualizado (exclui db_data, logs)
├── docker-compose.yml           # Build local + extra_hosts + volumes
├── config.py                    # TavilyAPI como opção
├── app.py                       # Traduzido + endpoints /api/update-news e /api/news-status
├── templates/index.html         # Interface PT-BR + painel de notícias
├── SingleEngineApp/
│   ├── media_engine_streamlit_app.py   # Suporte TavilyAPI
│   ├── query_engine_streamlit_app.py   # Traduzido
│   └── insight_engine_streamlit_app.py # Traduzido
├── InsightEngine/               # Prompts com plataformas ocidentais
├── MediaEngine/                 # TavilyMultimodalSearch adicionado
├── QueryEngine/                 # Traduzido
├── ReportEngine/                # Traduzido
├── ForumEngine/                 # Traduzido
├── MindSpider/
│   └── BroadTopicExtraction/
│       └── get_today_news.py    # 24 fontes RSS (BR/USA/EU/LATAM)
└── SentimentAnalysisModel/      # Modelo multilíngue suporta PT
```

---

## 8. Repositório

- **Origem**: [666ghj/BettaFish](https://github.com/666ghj/BettaFish) (chinês)
- **Fork adaptado**: [inematds/BettaFish](https://github.com/inematds/BettaFish) (português brasileiro)
