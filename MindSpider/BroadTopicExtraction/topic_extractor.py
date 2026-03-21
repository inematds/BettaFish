#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modulo BroadTopicExtraction - Extrator de topicos
Baseado em DeepSeek para extracao direta de palavras-chave e geracao de resumo de noticias
"""

import sys
import json
import re
from pathlib import Path
from typing import List, Dict, Tuple
from openai import OpenAI

# Adicionar diretorio raiz do projeto ao path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

try:
    import config
    from config import settings
except ImportError:
    raise ImportError("Nao foi possivel importar o arquivo de configuracao settings.py")

class TopicExtractor:
    """Extrator de topicos"""

    def __init__(self):
        """Inicializar extrator de topicos"""
        self.client = OpenAI(
            api_key=settings.MINDSPIDER_API_KEY,
            base_url=settings.MINDSPIDER_BASE_URL
        )
        self.model = settings.MINDSPIDER_MODEL_NAME

    def extract_keywords_and_summary(self, news_list: List[Dict], max_keywords: int = 100) -> Tuple[List[str], str]:
        """
        Extrair palavras-chave e gerar resumo a partir da lista de noticias

        Args:
            news_list: Lista de noticias
            max_keywords: Quantidade maxima de palavras-chave

        Returns:
            (lista de palavras-chave, resumo da analise de noticias)
        """
        if not news_list:
            return [], "Sem noticias em destaque hoje"

        # Construir texto resumido das noticias
        news_text = self._build_news_summary(news_list)

        # Construir prompt
        prompt = self._build_analysis_prompt(news_text, max_keywords)

        try:
            # Chamar API DeepSeek
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "Voce e um analista de noticias profissional, especializado em extrair palavras-chave e escrever resumos analiticos a partir de noticias em destaque."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=1500,
                temperature=0.3
            )

            # Analisar resultado retornado
            result_text = response.choices[0].message.content
            keywords, summary = self._parse_analysis_result(result_text)

            print(f"Extraidas com sucesso {len(keywords)} palavras-chave e resumo de noticias gerado")
            return keywords[:max_keywords], summary

        except Exception as e:
            print(f"Falha na extracao de topicos: {e}")
            # Retornar resultado fallback simples
            fallback_keywords = self._extract_simple_keywords(news_list)
            fallback_summary = f"Hoje foram coletadas {len(news_list)} noticias em destaque, cobrindo topicos populares de multiplas plataformas."
            return fallback_keywords[:max_keywords], fallback_summary

    def _build_news_summary(self, news_list: List[Dict]) -> str:
        """Construir texto resumido das noticias"""
        news_items = []

        for i, news in enumerate(news_list, 1):
            title = news.get('title', 'Sem titulo')
            source = news.get('source_platform', news.get('source', 'Desconhecido'))

            # Limpar caracteres especiais do titulo
            title = re.sub(r'[#@]', '', title).strip()

            news_items.append(f"{i}. [{source}] {title}")

        return "\n".join(news_items)

    def _build_analysis_prompt(self, news_text: str, max_keywords: int) -> str:
        """Construir prompt de analise"""
        news_count = len(news_text.split('\n'))

        prompt = f"""
Por favor, analise as seguintes {news_count} noticias em destaque de hoje e complete duas tarefas:

Lista de noticias:
{news_text}

Tarefa 1: Extrair palavras-chave (maximo {max_keywords})
- Extrair palavras-chave que representem os topicos em destaque de hoje
- As palavras-chave devem ser adequadas para busca em plataformas de midia social
- Priorizar topicos com alta popularidade e grande volume de discussao
- Evitar termos muito amplos ou muito especificos

Tarefa 2: Escrever resumo analitico das noticias (150-300 palavras)
- Resumir brevemente o conteudo principal das noticias em destaque de hoje
- Apontar os principais topicos de atencao social atual
- Analisar os fenomenos ou tendencias sociais refletidos por esses destaques
- Linguagem concisa, clara e objetiva

Por favor, produza o resultado estritamente no seguinte formato JSON:
```json
{{
  "keywords": ["palavra-chave1", "palavra-chave2", "palavra-chave3"],
  "summary": "Conteudo do resumo analitico das noticias de hoje..."
}}
```

Por favor, produza diretamente o resultado em formato JSON, sem incluir outras explicacoes textuais.
"""
        return prompt

    def _parse_analysis_result(self, result_text: str) -> Tuple[List[str], str]:
        """Analisar resultado da analise"""
        try:
            # Tentar extrair parte JSON
            json_match = re.search(r'```json\s*(.*?)\s*```', result_text, re.DOTALL)
            if json_match:
                json_text = json_match.group(1)
            else:
                # Se nao houver bloco de codigo, tentar analisar diretamente
                json_text = result_text.strip()

            # Analisar JSON
            data = json.loads(json_text)

            keywords = data.get('keywords', [])
            summary = data.get('summary', '')

            # Validar e limpar palavras-chave
            clean_keywords = []
            for keyword in keywords:
                keyword = str(keyword).strip()
                if keyword and len(keyword) > 1 and keyword not in clean_keywords:
                    clean_keywords.append(keyword)

            # Validar resumo
            if not summary or len(summary.strip()) < 10:
                summary = "As noticias em destaque de hoje cobrem multiplas areas, refletindo os diversos pontos de atencao da sociedade atual."

            return clean_keywords, summary.strip()

        except json.JSONDecodeError as e:
            print(f"Falha ao analisar JSON: {e}")
            print(f"Retorno original: {result_text}")

            # Tentar analise manual
            return self._manual_parse_result(result_text)

        except Exception as e:
            print(f"Falha ao processar resultado da analise: {e}")
            return [], "Falha no processamento do resultado da analise, tente novamente mais tarde."

    def _manual_parse_result(self, text: str) -> Tuple[List[str], str]:
        """Analise manual do resultado (plano de contingencia quando a analise JSON falha)"""
        print("Tentando analise manual do resultado...")

        keywords = []
        summary = ""

        lines = text.split('\n')

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Procurar palavras-chave
            if 'keywords' in line.lower():
                # Extrair palavras-chave
                keyword_match = re.findall(r'[""](.*?)["""]', line)
                if keyword_match:
                    keywords.extend(keyword_match)
                else:
                    # Tentar outros separadores
                    parts = re.split(r'[,，、]', line)
                    for part in parts:
                        clean_part = re.sub(r'[keywords\[\]"]', '', part).strip()
                        if clean_part and len(clean_part) > 1:
                            keywords.append(clean_part)

            # Procurar resumo
            elif 'summary' in line.lower():
                if ':' in line:
                    summary = line.split(':')[-1].strip()

            # Se a linha parecer conteudo de resumo
            elif len(line) > 50:
                if not summary:
                    summary = line

        # Limpar palavras-chave
        clean_keywords = []
        for keyword in keywords:
            keyword = keyword.strip()
            if keyword and len(keyword) > 1 and keyword not in clean_keywords:
                clean_keywords.append(keyword)

        # Se nenhum resumo foi encontrado, gerar um simples
        if not summary:
            summary = "As noticias em destaque de hoje sao ricas em conteudo, cobrindo diversas areas de atencao da sociedade."

        return clean_keywords[:max_keywords], summary

    def _extract_simple_keywords(self, news_list: List[Dict]) -> List[str]:
        """Extracao simples de palavras-chave (plano fallback)"""
        keywords = []

        for news in news_list:
            title = news.get('title', '')

            # Extracao simples de palavras-chave
            # Remover palavras comuns sem significado
            title_clean = re.sub(r'[#@【】\[\]()（）]', ' ', title)
            words = title_clean.split()

            for word in words:
                word = word.strip()
                if (len(word) > 1 and
                    word not in keywords):
                    keywords.append(word)

        return keywords[:10]

    def get_search_keywords(self, keywords: List[str], limit: int = 10) -> List[str]:
        """
        Obter palavras-chave para busca

        Args:
            keywords: Lista de palavras-chave
            limit: Limite de quantidade

        Returns:
            Lista de palavras-chave adequadas para busca
        """
        # Filtrar e otimizar palavras-chave
        search_keywords = []

        for keyword in keywords:
            keyword = str(keyword).strip()

            # Condicoes de filtro
            if (len(keyword) > 1 and
                len(keyword) < 20 and  # Nao pode ser muito longo
                keyword not in search_keywords and
                not keyword.isdigit()):  # Nao pode ser apenas numeros

                search_keywords.append(keyword)

        return search_keywords[:limit]

if __name__ == "__main__":
    # Testar extrator de topicos
    extractor = TopicExtractor()

    # Dados de noticias simulados
    test_news = [
        {"title": "Tecnologia AI avanca rapidamente", "source_platform": "Noticias de Tecnologia"},
        {"title": "Analise do mercado de acoes", "source_platform": "Noticias Financeiras"},
        {"title": "Ultimas novidades de celebridades", "source_platform": "Entretenimento"}
    ]

    keywords, summary = extractor.extract_keywords_and_summary(test_news)

    print(f"Palavras-chave extraidas: {keywords}")
    print(f"Resumo das noticias: {summary}")

    search_keywords = extractor.get_search_keywords(keywords)
    print(f"Palavras-chave de busca: {search_keywords}")
