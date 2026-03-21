"""
Middleware de otimização de palavras-chave
Usa Qwen AI para otimizar os termos de busca gerados pelo Agent em palavras-chave mais adequadas para consulta ao banco de dados de opinião pública
"""

from openai import OpenAI
import json
import sys
import os
from typing import List, Dict, Any
from dataclasses import dataclass

# Adicionar diretório raiz do projeto ao caminho Python para importar config
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from config import settings
from loguru import logger

# Adicionar diretório utils ao caminho Python
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(os.path.dirname(current_dir))
utils_dir = os.path.join(root_dir, 'utils')
if utils_dir not in sys.path:
    sys.path.append(utils_dir)

from retry_helper import with_graceful_retry, SEARCH_API_RETRY_CONFIG

@dataclass
class KeywordOptimizationResponse:
    """Resposta de otimização de palavras-chave"""
    original_query: str
    optimized_keywords: List[str]
    reasoning: str
    success: bool
    error_message: str = ""

class KeywordOptimizer:
    """
    Otimizador de palavras-chave
    Usa o modelo Qwen3 da SiliconFlow para otimizar os termos de busca gerados pelo Agent em palavras-chave mais próximas da opinião pública real
    """

    def __init__(self, api_key: str = None, base_url: str = None, model_name: str = None):
        """
        Inicializar o otimizador de palavras-chave

        Args:
            api_key: Chave API da SiliconFlow, se não fornecida será lida do arquivo de configuração
            base_url: Endereço base da interface, padrão usa o endereço SiliconFlow fornecido na configuração
        """
        self.api_key = api_key or settings.KEYWORD_OPTIMIZER_API_KEY

        if not self.api_key:
            raise ValueError("Chave API da SiliconFlow não encontrada, configure KEYWORD_OPTIMIZER_API_KEY em config.py")

        self.base_url = base_url or settings.KEYWORD_OPTIMIZER_BASE_URL

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
        self.model = model_name or settings.KEYWORD_OPTIMIZER_MODEL_NAME

    def optimize_keywords(self, original_query: str, context: str = "") -> KeywordOptimizationResponse:
        """
        Otimizar palavras-chave de busca

        Args:
            original_query: Consulta de busca original gerada pelo Agent
            context: Informações de contexto adicionais (como título do parágrafo, descrição do conteúdo etc.)

        Returns:
            KeywordOptimizationResponse: Lista de palavras-chave otimizadas
        """
        logger.info(f"Middleware de otimização de palavras-chave: Processando consulta '{original_query}'")

        try:
            # Construir prompt de otimização
            system_prompt = self._build_system_prompt()
            user_prompt = self._build_user_prompt(original_query, context)

            # Chamar API Qwen
            response = self._call_qwen_api(system_prompt, user_prompt)

            if response["success"]:
                # Analisar resposta
                content = response["content"]
                try:
                    # Tentar analisar resposta em formato JSON
                    if content.strip().startswith('{'):
                        parsed = json.loads(content)
                        keywords = parsed.get("keywords", [])
                        reasoning = parsed.get("reasoning", "")
                    else:
                        # Se não estiver em formato JSON, tentar extrair palavras-chave do texto
                        keywords = self._extract_keywords_from_text(content)
                        reasoning = content

                    # Validar qualidade das palavras-chave
                    validated_keywords = self._validate_keywords(keywords)

                    logger.info(
                        f"Otimização bem-sucedida: {len(validated_keywords)} palavras-chave" +
                        ("" if not validated_keywords else "\n" +
                         "\n".join([f"   {i}. '{k}'" for i, k in enumerate(validated_keywords, 1)]))
                    )



                    return KeywordOptimizationResponse(
                        original_query=original_query,
                        optimized_keywords=validated_keywords,
                        reasoning=reasoning,
                        success=True
                    )

                except Exception as e:
                    logger.exception(f"Falha ao analisar resposta, usando plano alternativo: {str(e)}")
                    # Plano alternativo: extrair palavras-chave da consulta original
                    fallback_keywords = self._fallback_keyword_extraction(original_query)
                    return KeywordOptimizationResponse(
                        original_query=original_query,
                        optimized_keywords=fallback_keywords,
                        reasoning="Falha ao analisar resposta da API, usando extração alternativa de palavras-chave",
                        success=True
                    )
            else:
                logger.error(f"Falha na chamada da API: {response['error']}")
                # Usar plano alternativo
                fallback_keywords = self._fallback_keyword_extraction(original_query)
                return KeywordOptimizationResponse(
                    original_query=original_query,
                    optimized_keywords=fallback_keywords,
                    reasoning="Falha na chamada da API, usando extração alternativa de palavras-chave",
                    success=True,
                    error_message=response['error']
                )

        except Exception as e:
            logger.error(f"Falha na otimização de palavras-chave: {str(e)}")
            # Plano alternativo final
            fallback_keywords = self._fallback_keyword_extraction(original_query)
            return KeywordOptimizationResponse(
                original_query=original_query,
                optimized_keywords=fallback_keywords,
                reasoning="Erro do sistema, usando extração alternativa de palavras-chave",
                success=False,
                error_message=str(e)
            )

    def _build_system_prompt(self) -> str:
        """Construir prompt de sistema"""
        return """Você é um especialista profissional em mineração de dados de opinião pública. Sua tarefa é otimizar a consulta de busca fornecida pelo usuário em palavras-chave mais adequadas para busca em bancos de dados de opinião pública de mídias sociais.

**Princípios centrais**:
1. **Próximo da linguagem dos internautas**: Usar vocabulário que usuários comuns usariam nas mídias sociais
2. **Evitar terminologia profissional**: Não usar termos oficiais como "opinião pública", "propagação", "tendência", "perspectiva"
3. **Simples e específico**: Cada palavra-chave deve ser muito concisa e clara, facilitando a correspondência no banco de dados
4. **Rico em emoções**: Incluir vocabulário de expressão emocional comumente usado por internautas
5. **Controle de quantidade**: Fornecer no mínimo 10 e no máximo 20 palavras-chave
6. **Evitar repetições**: Não se desviar do tema da consulta inicial

**Aviso importante**: Cada palavra-chave deve ser um termo independente e indivisível, proibido incluir espaços dentro do termo. Por exemplo, usar "turma do Lei Jun polêmica" em vez do incorreto "turma do Lei Jun polêmica".


**Formato de saída**:
Retorne o resultado em formato JSON:
{
    "keywords": ["palavra-chave1", "palavra-chave2", "palavra-chave3"],
    "reasoning": "Motivo da escolha dessas palavras-chave"
}

**Exemplo**:
Entrada: "gestão de opinião pública da Universidade de Wuhan perspectivas futuras tendências de desenvolvimento"
Saída:
{
    "keywords": ["Wuda", "Universidade de Wuhan", "gestão escolar", "educação na Wuda"],
    "reasoning": "Escolhidos 'Wuda' e 'Universidade de Wuhan' como termos centrais, pois são os nomes mais usados pelos internautas; 'gestão escolar' é mais próximo do cotidiano que 'gestão de opinião pública'; evitados termos profissionais como 'perspectivas futuras' e 'tendências de desenvolvimento' que internautas raramente usam"
}"""

    def _build_user_prompt(self, original_query: str, context: str) -> str:
        """Construir prompt do usuário"""
        prompt = f"Otimize a seguinte consulta de busca em palavras-chave adequadas para consulta ao banco de dados de opinião pública:\n\nConsulta original: {original_query}"

        if context:
            prompt += f"\n\nInformações de contexto: {context}"

        prompt += "\n\nLembre-se: use vocabulário que os internautas realmente usam nas mídias sociais, evite terminologia oficial e profissional."

        return prompt

    @with_graceful_retry(SEARCH_API_RETRY_CONFIG, default_return={"success": False, "error": "Serviço de otimização de palavras-chave temporariamente indisponível"})
    def _call_qwen_api(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """Chamar API Qwen"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
            )

            if response.choices:
                content = response.choices[0].message.content
                return {"success": True, "content": content}
            else:
                return {"success": False, "error": "Formato de retorno da API anormal"}
        except Exception as e:
            return {"success": False, "error": f"Exceção na chamada da API: {str(e)}"}

    def _extract_keywords_from_text(self, text: str) -> List[str]:
        """Extrair palavras-chave do texto (usado quando a análise JSON falha)"""
        # Lógica simples de extração de palavras-chave
        lines = text.split('\n')
        keywords = []

        for line in lines:
            line = line.strip()
            # Buscar possíveis palavras-chave
            if '：' in line or ':' in line:
                parts = line.split('：') if '：' in line else line.split(':')
                if len(parts) > 1:
                    potential_keywords = parts[1].strip()
                    # Tentar dividir palavras-chave
                    if '、' in potential_keywords:
                        keywords.extend([k.strip() for k in potential_keywords.split('、')])
                    elif ',' in potential_keywords:
                        keywords.extend([k.strip() for k in potential_keywords.split(',')])
                    else:
                        keywords.append(potential_keywords)

        # Se não encontrar, tentar outros métodos
        if not keywords:
            # Buscar conteúdo entre aspas
            import re
            quoted_content = re.findall(r'["""\'](.*?)["""\']', text)
            keywords.extend(quoted_content)

        # Limpar e validar palavras-chave
        cleaned_keywords = []
        for keyword in keywords[:20]:  # Máximo 20
            keyword = keyword.strip().strip('"\'""''')
            if keyword and len(keyword) <= 20:  # Comprimento razoável
                cleaned_keywords.append(keyword)

        return cleaned_keywords[:20]

    def _validate_keywords(self, keywords: List[str]) -> List[str]:
        """Validar e limpar palavras-chave"""
        validated = []

        # Palavras-chave indesejáveis (muito profissionais ou oficiais)
        bad_keywords = {
            '态度分析', '公众反应', '情绪倾向',
            '未来展望', '发展趋势', '战略规划', '政策导向', '管理机制'
        }

        for keyword in keywords:
            if isinstance(keyword, str):
                keyword = keyword.strip().strip('"\'""''')

                # Validação básica
                if (keyword and
                    len(keyword) <= 20 and
                    len(keyword) >= 1 and
                    not any(bad_word in keyword for bad_word in bad_keywords)):
                    validated.append(keyword)

        return validated[:20]  # Máximo 20 palavras-chave

    def _fallback_keyword_extraction(self, original_query: str) -> List[str]:
        """Plano alternativo de extração de palavras-chave"""
        # Lógica simples de extração de palavras-chave
        # Remover palavras comuns inúteis
        stop_words = {'、'}

        # Dividir a consulta
        import re
        # Dividir por espaços e pontuação
        tokens = re.split(r'[\s，。！？；：、]+', original_query)

        keywords = []
        for token in tokens:
            token = token.strip()
            if token and token not in stop_words and len(token) >= 2:
                keywords.append(token)

        # Se não houver palavras-chave válidas, usar a primeira palavra da consulta original
        if not keywords:
            first_word = original_query.split()[0] if original_query.split() else original_query
            keywords = [first_word] if first_word else ["热门"]

        return keywords[:20]

# Instância global
keyword_optimizer = KeywordOptimizer()
