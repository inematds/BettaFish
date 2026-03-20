# Testes de Parsing de Logs do ForumEngine

Este conjunto de testes é utilizado para testar a funcionalidade de parsing de logs em `ForumEngine/monitor.py`, verificando sua correção em diferentes formatos de log.

## Dados de Teste

`forum_log_test_data.py` contém exemplos mínimos de vários formatos de log (dados de teste de logs do fórum):

### Formato antigo ([HH:MM:SS])
- `OLD_FORMAT_SINGLE_LINE_JSON`: JSON em linha única
- `OLD_FORMAT_MULTILINE_JSON`: JSON em múltiplas linhas
- `OLD_FORMAT_FIRST_SUMMARY`: Log contendo FirstSummaryNode
- `OLD_FORMAT_REFLECTION_SUMMARY`: Log contendo ReflectionSummaryNode

### Formato novo (formato padrão loguru)
- `NEW_FORMAT_SINGLE_LINE_JSON`: JSON em linha única
- `NEW_FORMAT_MULTILINE_JSON`: JSON em múltiplas linhas
- `NEW_FORMAT_FIRST_SUMMARY`: Log contendo FirstSummaryNode
- `NEW_FORMAT_REFLECTION_SUMMARY`: Log contendo ReflectionSummaryNode

### Exemplos complexos
- `COMPLEX_JSON_WITH_UPDATED`: JSON contendo updated_paragraph_latest_state
- `COMPLEX_JSON_WITH_PARAGRAPH`: JSON contendo apenas paragraph_latest_state
- `MIXED_FORMAT_LINES`: Linhas de log em formato misto

## Executar os Testes

### Usando pytest (recomendado)

```bash
# Instalar pytest (se ainda não estiver instalado)
pip install pytest

# Executar todos os testes
pytest tests/test_monitor.py -v

# Executar um teste específico
pytest tests/test_monitor.py::TestLogMonitor::test_extract_json_content_new_format_multiline -v
```

### Execução direta

```bash
python tests/test_monitor.py
```

## Cobertura dos Testes

Os testes cobrem as seguintes funções:

1. **is_target_log_line**: Identificar linhas de log do nó alvo
2. **is_json_start_line**: Identificar linhas de início de JSON
3. **is_json_end_line**: Identificar linhas de fim de JSON
4. **extract_json_content**: Extrair conteúdo JSON (linha única e múltiplas linhas)
5. **format_json_content**: Formatar conteúdo JSON (extrair preferencialmente updated_paragraph_latest_state)
6. **extract_node_content**: Extrair conteúdo do nó
7. **process_lines_for_json**: Fluxo de processamento completo
8. **is_valuable_content**: Determinar se o conteúdo é valioso

## Problemas Esperados

O código atual pode não processar corretamente o formato novo do loguru; os principais problemas são:

1. **Remoção de timestamp**: A regex `r'^\[\d{2}:\d{2}:\d{2}\]\s*'` em `extract_json_content()` só consegue corresponder ao formato `[HH:MM:SS]`, não conseguindo corresponder ao formato do loguru `YYYY-MM-DD HH:mm:ss.SSS`

2. **Correspondência de timestamp**: A regex `r'\[\d{2}:\d{2}:\d{2}\]\s*(.+)'` em `extract_node_content()` também só consegue corresponder ao formato antigo

Esses testes ajudarão a identificar esses problemas e guiar as correções de código subsequentes.

