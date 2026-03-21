"""
Funções utilitárias de processamento de texto
Usadas para limpar saída de LLM, analisar JSON etc.
"""

import re
import json
from typing import Dict, Any, List
from json.decoder import JSONDecodeError


def clean_json_tags(text: str) -> str:
    """
    Limpar tags JSON do texto

    Args:
        text: Texto original

    Returns:
        Texto limpo
    """
    # Remover tags ```json e ```
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*$', '', text)
    text = re.sub(r'```', '', text)

    return text.strip()


def clean_markdown_tags(text: str) -> str:
    """
    Limpar tags Markdown do texto

    Args:
        text: Texto original

    Returns:
        Texto limpo
    """
    # Remover tags ```markdown e ```
    text = re.sub(r'```markdown\s*', '', text)
    text = re.sub(r'```\s*$', '', text)
    text = re.sub(r'```', '', text)

    return text.strip()


def remove_reasoning_from_output(text: str) -> str:
    """
    Remover texto de raciocínio da saída

    Args:
        text: Texto original

    Returns:
        Texto limpo
    """
    # Encontrar posição de início do JSON
    json_start = -1

    # Tentar encontrar o primeiro { ou [
    for i, char in enumerate(text):
        if char in '{[':
            json_start = i
            break

    if json_start != -1:
        # Extrair a partir da posição de início do JSON
        return text[json_start:].strip()

    # Se não encontrou marcador JSON, tentar outros métodos
    # Remover identificadores comuns de raciocínio
    patterns = [
        r'(?:reasoning|raciocínio|pensamento|análise)[:：]\s*.*?(?=\{|\[)',  # Remover parte de raciocínio
        r'(?:explanation|explicação|descrição)[:：]\s*.*?(?=\{|\[)',   # Remover parte de explicação
        r'^.*?(?=\{|\[)',  # Remover todo texto antes do JSON
    ]

    for pattern in patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE | re.DOTALL)

    return text.strip()


def extract_clean_response(text: str) -> Dict[str, Any]:
    """
    Extrair e limpar conteúdo JSON da resposta

    Args:
        text: Texto original da resposta

    Returns:
        Dicionário JSON analisado
    """
    # Limpar texto
    cleaned_text = clean_json_tags(text)
    cleaned_text = remove_reasoning_from_output(cleaned_text)

    # Tentar análise direta
    try:
        return json.loads(cleaned_text)
    except JSONDecodeError:
        pass

    # Tentar reparar JSON incompleto
    fixed_text = fix_incomplete_json(cleaned_text)
    if fixed_text:
        try:
            return json.loads(fixed_text)
        except JSONDecodeError:
            pass

    # Tentar encontrar objeto JSON
    json_pattern = r'\{.*\}'
    match = re.search(json_pattern, cleaned_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except JSONDecodeError:
            pass

    # Tentar encontrar array JSON
    array_pattern = r'\[.*\]'
    match = re.search(array_pattern, cleaned_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except JSONDecodeError:
            pass

    # Se todos os métodos falharem, retornar mensagem de erro
    print(f"Não foi possível analisar a resposta JSON: {cleaned_text[:200]}...")
    return {"error": "Falha na análise JSON", "raw_text": cleaned_text}


def fix_incomplete_json(text: str) -> str:
    """
    Reparar resposta JSON incompleta

    Args:
        text: Texto original

    Returns:
        Texto JSON reparado; se não for possível reparar, retorna string vazia
    """
    # Remover vírgulas e espaços em branco extras
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)

    # Verificar se já é um JSON válido
    try:
        json.loads(text)
        return text
    except JSONDecodeError:
        pass

    # Verificar se falta o símbolo de array no início
    if text.strip().startswith('{') and not text.strip().startswith('['):
        # Se começa com objeto, tentar encapsular em array
        if text.count('{') > 1:
            # Múltiplos objetos, encapsular em array
            text = '[' + text + ']'
        else:
            # Único objeto, encapsular em array
            text = '[' + text + ']'

    # Verificar se falta o símbolo de array no final
    if text.strip().endswith('}') and not text.strip().endswith(']'):
        # Se termina com objeto, tentar encapsular em array
        if text.count('}') > 1:
            # Múltiplos objetos, encapsular em array
            text = '[' + text + ']'
        else:
            # Único objeto, encapsular em array
            text = '[' + text + ']'

    # Verificar se as chaves/colchetes estão balanceados
    open_braces = text.count('{')
    close_braces = text.count('}')
    open_brackets = text.count('[')
    close_brackets = text.count(']')

    # Reparar chaves/colchetes não balanceados
    if open_braces > close_braces:
        text += '}' * (open_braces - close_braces)
    if open_brackets > close_brackets:
        text += ']' * (open_brackets - close_brackets)

    # Verificar se o JSON reparado é válido
    try:
        json.loads(text)
        return text
    except JSONDecodeError:
        # Se ainda inválido, tentar reparo mais agressivo
        return fix_aggressive_json(text)


def fix_aggressive_json(text: str) -> str:
    """
    Método de reparo de JSON mais agressivo

    Args:
        text: Texto original

    Returns:
        Texto JSON reparado
    """
    # Encontrar todos os possíveis objetos JSON
    objects = re.findall(r'\{[^{}]*\}', text)

    if len(objects) >= 2:
        # Se há múltiplos objetos, encapsular em array
        return '[' + ','.join(objects) + ']'
    elif len(objects) == 1:
        # Se há apenas um objeto, encapsular em array
        return '[' + objects[0] + ']'
    else:
        # Se nenhum objeto encontrado, retornar array vazio
        return '[]'


def update_state_with_search_results(search_results: List[Dict[str, Any]],
                                   paragraph_index: int, state: Any) -> Any:
    """
    Atualizar resultados de busca no estado

    Args:
        search_results: Lista de resultados de busca
        paragraph_index: Índice do parágrafo
        state: Objeto de estado

    Returns:
        Objeto de estado atualizado
    """
    if 0 <= paragraph_index < len(state.paragraphs):
        # Obter a consulta da última busca (presumindo ser a consulta atual)
        current_query = ""
        if search_results:
            # Inferir consulta a partir dos resultados da busca (precisa ser melhorado para obter a consulta real)
            current_query = "consulta de busca"

        # Adicionar resultados de busca ao estado
        state.paragraphs[paragraph_index].research.add_search_results(
            current_query, search_results
        )

    return state


def validate_json_schema(data: Dict[str, Any], required_fields: List[str]) -> bool:
    """
    Validar se os dados JSON contêm os campos obrigatórios

    Args:
        data: Dados a serem validados
        required_fields: Lista de campos obrigatórios

    Returns:
        Se a validação foi aprovada
    """
    return all(field in data for field in required_fields)


def truncate_content(content: str, max_length: int = 20000) -> str:
    """
    Truncar conteúdo para o comprimento especificado

    Args:
        content: Conteúdo original
        max_length: Comprimento máximo

    Returns:
        Conteúdo truncado
    """
    if len(content) <= max_length:
        return content

    # Tentar truncar na fronteira de palavra
    truncated = content[:max_length]
    last_space = truncated.rfind(' ')

    if last_space > max_length * 0.8:  # Se a posição do último espaço for razoável
        return truncated[:last_space] + "..."
    else:
        return truncated + "..."


def format_search_results_for_prompt(search_results: List[Dict[str, Any]],
                                   max_length: int = 20000) -> List[str]:
    """
    Formatar resultados de busca para uso em prompts

    Args:
        search_results: Lista de resultados de busca
        max_length: Comprimento máximo de cada resultado

    Returns:
        Lista de conteúdos formatados
    """
    formatted_results = []

    for result in search_results:
        content = result.get('content', '')
        if content:
            truncated_content = truncate_content(content, max_length)
            formatted_results.append(truncated_content)

    return formatted_results
