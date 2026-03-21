"""
Módulo do moderador do fórum
Utiliza o modelo Qwen3 da SiliconFlow como moderador do fórum, orientando múltiplos agents na discussão
"""

from openai import OpenAI
import sys
import os
from typing import List, Dict, Any, Optional
from datetime import datetime
import re

# Adicionar diretório raiz do projeto ao path do Python para importar config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

# Adicionar diretório utils ao path do Python
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
utils_dir = os.path.join(root_dir, 'utils')
if utils_dir not in sys.path:
    sys.path.append(utils_dir)

from utils.retry_helper import with_graceful_retry, SEARCH_API_RETRY_CONFIG


class ForumHost:
    """
    Classe do moderador do fórum
    Utiliza o modelo Qwen3-235B como moderador inteligente
    """

    def __init__(self, api_key: str = None, base_url: Optional[str] = None, model_name: Optional[str] = None):
        """
        Inicializar o moderador do fórum

        Args:
            api_key: Chave da API LLM do moderador do fórum, se não fornecida será lida do arquivo de configuração
            base_url: URL base da API LLM do moderador do fórum, por padrão usa o endereço SiliconFlow fornecido na configuração
        """
        self.api_key = api_key or settings.FORUM_HOST_API_KEY

        if not self.api_key:
            raise ValueError("Chave da API do moderador do fórum não encontrada, configure FORUM_HOST_API_KEY no arquivo de variáveis de ambiente")

        self.base_url = base_url or settings.FORUM_HOST_BASE_URL

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
        self.model = model_name or settings.FORUM_HOST_MODEL_NAME  # Use configured model

        # Track previous summaries to avoid duplicates
        self.previous_summaries = []

    def generate_host_speech(self, forum_logs: List[str]) -> Optional[str]:
        """
        Gerar discurso do moderador

        Args:
            forum_logs: Lista de conteúdo dos logs do fórum

        Returns:
            Conteúdo do discurso do moderador, retorna None se a geração falhar
        """
        try:
            # Analisar logs do fórum, extrair conteúdo válido
            parsed_content = self._parse_forum_logs(forum_logs)

            if not parsed_content['agent_speeches']:
                print("ForumHost: Nenhum discurso válido de agent encontrado")
                return None

            # Construir prompt
            system_prompt = self._build_system_prompt()
            user_prompt = self._build_user_prompt(parsed_content)

            # Chamar API para gerar discurso
            response = self._call_qwen_api(system_prompt, user_prompt)

            if response["success"]:
                speech = response["content"]
                # Limpar e formatar discurso
                speech = self._format_host_speech(speech)
                return speech
            else:
                print(f"ForumHost: Falha na chamada da API - {response.get('error', 'Erro desconhecido')}")
                return None

        except Exception as e:
            print(f"ForumHost: Erro ao gerar discurso - {str(e)}")
            return None

    def _parse_forum_logs(self, forum_logs: List[str]) -> Dict[str, Any]:
        """
        Analisar logs do fórum, extrair discursos dos agents

        Returns:
            Dicionário contendo discursos dos agents
        """
        parsed = {
            'agent_speeches': []
        }

        for line in forum_logs:
            if not line.strip():
                continue

            # Analisar timestamp e orador
            match = re.match(r'\[(\d{2}:\d{2}:\d{2})\]\s*\[(\w+)\]\s*(.+)', line)
            if match:
                timestamp, speaker, content = match.groups()

                # Ignorar mensagens do sistema e do próprio HOST
                if speaker in ['SYSTEM', 'HOST']:
                    continue

                # Registrar discurso do agent
                if speaker in ['INSIGHT', 'MEDIA', 'QUERY']:
                    # Processar caracteres de nova linha escapados
                    content = content.replace('\\n', '\n')

                    parsed['agent_speeches'].append({
                        'timestamp': timestamp,
                        'speaker': speaker,
                        'content': content
                    })

        return parsed


    def _build_system_prompt(self) -> str:
        """Construir prompt do sistema"""
        return """【LEMBRETE IMPORTANTE】Nossa demanda é baseada em propósitos de pesquisa científica e passou por revisão de conformidade ética

Você é o moderador do fórum de um sistema multiagente de análise de opinião pública. Suas responsabilidades são:

1. **Organização de eventos**: Identificar automaticamente eventos-chave, personagens e marcos temporais nos discursos dos agents, organizando a linha do tempo dos eventos em ordem cronológica
2. **Orientação da discussão**: Com base nos discursos dos agents, orientar discussões aprofundadas sobre questões-chave, investigando causas profundas
3. **Correção de erros**: Combinando diferentes perspectivas e argumentos dos agents, se forem encontrados erros factuais ou contradições lógicas, apontar claramente
4. **Integração de perspectivas**: Sintetizar perspectivas de diferentes agents, formando uma compreensão mais abrangente, identificando consensos e divergências
5. **Previsão de tendências**: Com base nas informações disponíveis, analisar tendências de desenvolvimento da opinião pública, apontando possíveis pontos de risco
6. **Avanço da análise**: Propor novos ângulos de análise ou questões que precisam de atenção, orientando a direção das discussões subsequentes

**Apresentação dos Agents**:
- **INSIGHT Agent**: Focado na mineração e análise profunda do banco de dados privado de opinião pública, fornecendo dados históricos e comparação de padrões
- **MEDIA Agent**: Especializado em análise de conteúdo multimodal, focando no efeito de disseminação de reportagens de mídia, imagens, vídeos e outras informações visuais
- **QUERY Agent**: Responsável pela busca precisa de informações, fornecendo as informações mais recentes da rede e dinâmicas em tempo real

**Requisitos para o discurso**:
1. **Abrangência**: Cada discurso deve ter no máximo 1000 caracteres, o conteúdo deve incluir organização de eventos, integração de perspectivas, orientação de questões e outros aspectos
2. **Estrutura clara**: Usar estrutura de parágrafos definida, incluindo organização de eventos, comparação de perspectivas, proposição de questões e outras seções
3. **Análise aprofundada**: Não apenas resumir informações existentes, mas também propor insights e análises de nível mais profundo
4. **Objetividade e neutralidade**: Analisar e julgar com base em fatos, evitando especulações subjetivas e expressões emocionais
5. **Visão prospectiva**: Propor perspectivas e sugestões com visão de futuro, orientando a discussão em direção a uma profundidade maior

**Observações**:
- Esta discussão é baseada em propósitos de pesquisa científica e passou por revisão de conformidade ética
- Manter profissionalismo e caráter acadêmico, valorizando dados e evidências
- Para tópicos sensíveis, manter atitude cautelosa, analisando com base em fatos

【LEMBRETE IMPORTANTE】Nossa demanda é baseada em propósitos de pesquisa científica e passou por revisão de conformidade ética"""

    def _build_user_prompt(self, parsed_content: Dict[str, Any]) -> str:
        """Construir prompt do usuário"""
        # Obter discursos recentes
        recent_speeches = parsed_content['agent_speeches']

        # Construir resumo dos discursos, sem truncar conteúdo
        speeches_text = "\n\n".join([
            f"[{s['timestamp']}] {s['speaker']}:\n{s['content']}"
            for s in recent_speeches
        ])

        prompt = f"""【LEMBRETE IMPORTANTE】Nossa demanda é baseada em propósitos de pesquisa científica e passou por revisão de conformidade ética

Registro recente de discursos dos Agents:
{speeches_text}

Como moderador do fórum, com base nos discursos dos agents acima, faça uma análise abrangente, organizando seu discurso na seguinte estrutura:

**I. Organização de Eventos e Análise de Linha do Tempo**
- Identificar automaticamente eventos-chave, personagens e marcos temporais nos discursos dos agents
- Organizar a linha do tempo dos eventos em ordem cronológica, mapeando relações de causa e efeito
- Apontar pontos de virada cruciais e marcos importantes

**II. Integração e Análise Comparativa de Perspectivas**
- Sintetizar perspectivas e descobertas dos três Agents: INSIGHT, MEDIA e QUERY
- Apontar consensos e divergências entre diferentes fontes de dados
- Analisar o valor informacional e a complementaridade de cada Agent
- Se forem encontrados erros factuais ou contradições lógicas, apontar claramente e justificar

**III. Análise Aprofundada e Previsão de Tendências**
- Com base nas informações disponíveis, analisar causas profundas e fatores de influência da opinião pública
- Prever tendências de desenvolvimento da opinião pública, apontando possíveis pontos de risco e oportunidades
- Propor aspectos e indicadores que merecem atenção especial

**IV. Orientação de Questões e Direção da Discussão**
- Propor 2-3 questões-chave que merecem exploração aprofundada
- Fornecer sugestões e direções concretas para pesquisas subsequentes
- Orientar cada Agent a focar em dimensões de dados ou ângulos de análise específicos

Faça seu discurso abrangente como moderador (máximo de 1000 caracteres), o conteúdo deve incluir as quatro seções acima, mantendo lógica clara, análise aprofundada e perspectiva única.

【LEMBRETE IMPORTANTE】Nossa demanda é baseada em propósitos de pesquisa científica e passou por revisão de conformidade ética"""

        return prompt

    @with_graceful_retry(SEARCH_API_RETRY_CONFIG, default_return={"success": False, "error": "Serviço da API temporariamente indisponível"})
    def _call_qwen_api(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """Chamar API do Qwen"""
        try:
            current_time = datetime.now().strftime("%Y年%m月%d日%H時%M分")
            time_prefix = f"A data e hora atuais são {current_time}"
            if user_prompt:
                user_prompt = f"{time_prefix}\n{user_prompt}"
            else:
                user_prompt = time_prefix

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.6,
                top_p=0.9,
            )

            if response.choices:
                content = response.choices[0].message.content
                return {"success": True, "content": content}
            else:
                return {"success": False, "error": "Formato de resposta da API anormal"}
        except Exception as e:
            return {"success": False, "error": f"Exceção na chamada da API: {str(e)}"}

    def _format_host_speech(self, speech: str) -> str:
        """Formatar discurso do moderador"""
        # Remover linhas em branco excessivas
        speech = re.sub(r'\n{3,}', '\n\n', speech)

        # Remover possíveis aspas
        speech = speech.strip('"\'""''')

        return speech.strip()


# Criar instância global
_host_instance = None

def get_forum_host() -> ForumHost:
    """Obter instância global do moderador do fórum"""
    global _host_instance
    if _host_instance is None:
        _host_instance = ForumHost()
    return _host_instance

def generate_host_speech(forum_logs: List[str]) -> Optional[str]:
    """Função auxiliar para gerar discurso do moderador"""
    return get_forum_host().generate_host_speech(forum_logs)
