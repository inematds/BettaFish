# Testes de Relatórios — BettaFish BR

Histórico de testes realizados durante a portação do sistema para o mercado brasileiro.

## Teste 01 — InsightEngine com banco vazio (2026-03-22)

**Arquivo:** `teste_01_insight_only_banco_vazio.md`

**Consulta:** "O futuro tende a valorizar menos quem apenas executa tarefas e mais quem cria oportunidades e quem orquestra resultados com apoio de IA, automação e tecnologia..."

**Configuração:**
- Modelo: qwen2.5:32b (Ollama local)
- Timeout: 3600s
- Busca: Tavily (configurado mas não usado pelo InsightEngine)

**Engines que rodaram:**
| Engine | Status |
|--------|--------|
| InsightEngine | Completou (banco vazio — dados genéricos gerados pelo LLM) |
| QueryEngine | Não recebeu a consulta |
| MediaEngine | Não recebeu a consulta |

**Resultado:** Relatório de 5 capítulos gerado, porém com dados inventados pelo LLM (Twitter 30mil, LinkedIn 45mil são fictícios). O InsightEngine não encontrou dados reais no PostgreSQL porque o banco está vazio — sem coleta prévia do MindSpider.

**Conclusão:** O InsightEngine funciona end-to-end, mas precisa de dados no banco para gerar relatórios com informações reais. Para resultados úteis, é necessário que o QueryEngine (Tavily) e MediaEngine também processem a consulta.

**Problemas encontrados nesta sessão:**
1. `host.docker.internal` não resolvia no Linux → corrigido com `extra_hosts`
2. MediaEngine exigia Bocha API → corrigido com suporte a TavilyAPI
3. `config.py` não aceitava `TavilyAPI` no Literal → corrigido
4. `media_engine_streamlit_app.py` não roteava TavilyAPI → corrigido
5. qwen2.5:72b causava timeout com 3 engines paralelos → trocado para qwen2.5:32b
