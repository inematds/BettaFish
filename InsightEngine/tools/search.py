"""
Conjunto de ferramentas de consulta ao banco de dados local de opinião pública projetado para AI Agent (MediaCrawlerDB)

Versão: 3.0
Última atualização: 2025-08-23

Este script encapsula funcionalidades complexas de consulta ao banco de dados MySQL local em uma série de ferramentas independentes
com objetivos claros e parâmetros bem definidos, projetadas especificamente para chamadas por AI Agent.
O Agent só precisa selecionar a ferramenta adequada de acordo com a intenção da tarefa (como buscar tendências,
busca global de tópicos, análise por intervalo de tempo, obter comentários), sem necessidade de escrever SQL complexo.

Atualizações principais da V3.0:
- Cálculo inteligente de popularidade: `search_hot_content` não precisa mais do parâmetro `sort_by`, passando a usar
  internamente um algoritmo de ponderação unificado que combina curtidas, comentários, compartilhamentos, visualizações
  etc. para calcular a pontuação de popularidade, tornando os resultados mais inteligentes e alinhados com a popularidade geral.
- Nova ferramenta de busca por plataforma: Adicionada `search_topic_on_platform` como caso especial,
  permitindo ao Agent fazer buscas precisas de tópicos em plataformas específicas (Bilibili, Weibo etc.),
  com suporte a filtro por tempo.
- Otimização estrutural: Ajustadas estruturas de dados e documentação de funções para acomodar as novas funcionalidades.

Ferramentas principais:
- search_hot_content: Buscar conteúdo com maior popularidade geral em um intervalo de tempo especificado.
- search_topic_globally: Buscar globalmente no banco de dados todo conteúdo e comentários relacionados a um tópico específico.
- search_topic_by_date: Buscar conteúdo relacionado a um tópico específico em um intervalo de datas históricas.
- get_comments_for_topic: Extrair especificamente dados de comentários do público sobre um tópico específico.
- search_topic_on_platform: Buscar um tópico específico em uma única plataforma de mídia social.
"""

import os
import json
from loguru import logger
import asyncio
from typing import List, Dict, Any, Optional, Literal
from dataclasses import dataclass, field
from ..utils.db import fetch_all
from datetime import datetime, timedelta, date
from InsightEngine.utils.config import settings

# --- 1. Definição de estruturas de dados ---

@dataclass
class QueryResult:
    """Classe de dados unificada para resultados de consulta ao banco de dados"""
    platform: str
    content_type: str
    title_or_content: str
    author_nickname: Optional[str] = None
    url: Optional[str] = None
    publish_time: Optional[datetime] = None
    engagement: Dict[str, int] = field(default_factory=dict)
    source_keyword: Optional[str] = None
    hotness_score: float = 0.0
    source_table: str = ""

@dataclass
class DBResponse:
    """Encapsula o resultado completo da ferramenta"""
    tool_name: str
    parameters: Dict[str, Any]
    results: List[QueryResult] = field(default_factory=list)
    results_count: int = 0
    error_message: Optional[str] = None

# --- 2. Cliente principal e conjunto de ferramentas especializadas ---

class MediaCrawlerDB:
    """Cliente contendo múltiplas ferramentas especializadas de consulta ao banco de dados de opinião pública"""
    # Definição de pesos
    W_LIKE = 1.0
    W_COMMENT = 5.0
    W_SHARE = 10.0  # Compartilhamento/repost/favorito/moeda e outras interações de alto valor
    W_VIEW = 0.1
    W_DANMAKU = 0.5

    def __init__(self):
        """
        Inicializar o cliente.
        """
        pass

    def _execute_query(self, query: str, params: tuple = None) -> List[Dict[str, Any]]:
        try:
            # Obter ou criar event loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # Executar a corrotina diretamente
            return loop.run_until_complete(fetch_all(query, params))

        except Exception as e:
            logger.exception(f"Erro ao consultar o banco de dados: {e}")
            return []

    @staticmethod
    def _to_datetime(ts: Any) -> Optional[datetime]:
        if not ts: return None
        try:
            if isinstance(ts, datetime): return ts
            if isinstance(ts, date): return datetime.combine(ts, datetime.min.time())
            if isinstance(ts, (int, float)) or str(ts).isdigit():
                val = float(ts)
                return datetime.fromtimestamp(val / 1000 if val > 1_000_000_000_000 else val)
            if isinstance(ts, str):
                return datetime.fromisoformat(ts.split('+')[0].strip())
        except (ValueError, TypeError): return None

    _table_columns_cache = {}
    def _get_table_columns(self, table_name: str) -> List[str]:
        if table_name in self._table_columns_cache: return self._table_columns_cache[table_name]
        results = self._execute_query(f"SHOW COLUMNS FROM `{table_name}`")
        columns = [row['Field'] for row in results] if results else []
        self._table_columns_cache[table_name] = columns
        return columns

    def _extract_engagement(self, row: Dict[str, Any]) -> Dict[str, int]:
        """Extrair e unificar indicadores de interação de uma linha de dados"""
        engagement = {}
        mapping = { 'likes': ['liked_count', 'like_count', 'voteup_count', 'comment_like_count'], 'comments': ['video_comment', 'comments_count', 'comment_count', 'total_replay_num', 'sub_comment_count'], 'shares': ['video_share_count', 'shared_count', 'share_count', 'total_forwards'], 'views': ['video_play_count', 'viewd_count'], 'favorites': ['video_favorite_count', 'collected_count'], 'coins': ['video_coin_count'], 'danmaku': ['video_danmaku'], }
        for key, potential_cols in mapping.items():
            for col in potential_cols:
                if col in row and row[col] is not None:
                    try: engagement[key] = int(row[col])
                    except (ValueError, TypeError): engagement[key] = 0
                    break
        return engagement

    def search_hot_content(
        self,
        time_period: Literal['24h', 'week', 'year'] = 'week',
        limit: int = 50
    ) -> DBResponse:
        """
        [Ferramenta] Buscar conteúdo em alta: Obter o conteúdo com maior popularidade geral em um período recente.

        Args:
            time_period (Literal['24h', 'week', 'year']): Intervalo de tempo, padrão 'week'.
            limit (int): Número máximo de resultados retornados, padrão 50.

        Returns:
            DBResponse: Lista de conteúdo ordenado por popularidade geral.
        """
        params_for_log = {'time_period': time_period, 'limit': limit}
        logger.info(f"--- FERRAMENTA: Buscar conteúdo em alta (params: {params_for_log}) ---")

        now = datetime.now()
        start_time = now - timedelta(days={'24h': 1, 'week': 7}.get(time_period, 365))

        # Definir fragmentos SQL de cálculo de popularidade por plataforma
        hotness_formulas = {
            'bilibili_video': f"(COALESCE(CAST(liked_count AS UNSIGNED), 0) * {self.W_LIKE} + COALESCE(CAST(video_comment AS UNSIGNED), 0) * {self.W_COMMENT} + COALESCE(CAST(video_share_count AS UNSIGNED), 0) * {self.W_SHARE} + COALESCE(CAST(video_favorite_count AS UNSIGNED), 0) * {self.W_SHARE} + COALESCE(CAST(video_coin_count AS UNSIGNED), 0) * {self.W_SHARE} + COALESCE(CAST(video_danmaku AS UNSIGNED), 0) * {self.W_DANMAKU} + COALESCE(CAST(video_play_count AS DECIMAL(20,2)), 0) * {self.W_VIEW})",
            'douyin_aweme':   f"(COALESCE(CAST(liked_count AS UNSIGNED), 0) * {self.W_LIKE} + COALESCE(CAST(comment_count AS UNSIGNED), 0) * {self.W_COMMENT} + COALESCE(CAST(share_count AS UNSIGNED), 0) * {self.W_SHARE} + COALESCE(CAST(collected_count AS UNSIGNED), 0) * {self.W_SHARE})",
            'weibo_note':     f"(COALESCE(CAST(liked_count AS UNSIGNED), 0) * {self.W_LIKE} + COALESCE(CAST(comments_count AS UNSIGNED), 0) * {self.W_COMMENT} + COALESCE(CAST(shared_count AS UNSIGNED), 0) * {self.W_SHARE})",
            'xhs_note':       f"(COALESCE(CAST(liked_count AS UNSIGNED), 0) * {self.W_LIKE} + COALESCE(CAST(comment_count AS UNSIGNED), 0) * {self.W_COMMENT} + COALESCE(CAST(share_count AS UNSIGNED), 0) * {self.W_SHARE} + COALESCE(CAST(collected_count AS UNSIGNED), 0) * {self.W_SHARE})",
            'kuaishou_video': f"(COALESCE(CAST(liked_count AS UNSIGNED), 0) * {self.W_LIKE} + COALESCE(CAST(viewd_count AS DECIMAL(20,2)), 0) * {self.W_VIEW})",
            'zhihu_content':  f"(COALESCE(CAST(voteup_count AS UNSIGNED), 0) * {self.W_LIKE} + COALESCE(CAST(comment_count AS UNSIGNED), 0) * {self.W_COMMENT})",
        }

        all_queries, params = [], []
        for table, formula in hotness_formulas.items():
            time_filter_sql, time_filter_param = "", None
            if table == 'weibo_note': time_filter_sql, time_filter_param = "`create_date_time` >= %s", start_time.strftime('%Y-%m-%d %H:%M:%S')
            elif table in ['kuaishou_video', 'xhs_note', 'douyin_aweme']: time_col = 'time' if table == 'xhs_note' else 'create_time'; time_filter_sql, time_filter_param = f"`{time_col}` >= %s", str(int(start_time.timestamp() * 1000))
            elif table == 'zhihu_content': time_filter_sql, time_filter_param = "CAST(`created_time` AS UNSIGNED) >= %s", str(int(start_time.timestamp()))
            else: time_filter_sql, time_filter_param = "`create_time` >= %s", str(int(start_time.timestamp()))

            content_type = 'note' if table in ['weibo_note', 'xhs_note'] else 'content' if table == 'zhihu_content' else 'video'
            query_template = "SELECT '{platform}' as p, '{type}' as t, {title} as title, {author} as author, {url} as url, {ts} as ts, {formula} as hotness_score, source_keyword, '{tbl}' as tbl FROM `{tbl}` WHERE {time_filter}"

            field_subs = {'platform': table.split('_')[0], 'type': content_type, 'title': 'title', 'author': 'nickname', 'url': 'video_url', 'ts': 'create_time', 'formula': formula, 'tbl': table, 'time_filter': time_filter_sql}
            if table == 'weibo_note': field_subs.update({'title': 'content', 'url': 'note_url', 'ts': 'create_date_time'})
            elif table == 'xhs_note': field_subs.update({'ts': 'time', 'url': 'note_url'})
            elif table == 'zhihu_content': field_subs.update({'author': 'user_nickname', 'url': 'content_url', 'ts': 'created_time'})
            elif table == 'douyin_aweme': field_subs.update({'url': 'aweme_url'})

            all_queries.append(query_template.format(**field_subs))
            params.append(time_filter_param)

        final_query = f"({' ) UNION ALL ( '.join(all_queries)}) ORDER BY hotness_score DESC LIMIT %s"
        raw_results = self._execute_query(final_query, tuple(params) + (limit,))

        formatted_results = [QueryResult(platform=r['p'], content_type=r['t'], title_or_content=r['title'], author_nickname=r.get('author'), url=r['url'], publish_time=self._to_datetime(r['ts']), engagement=self._extract_engagement(r), hotness_score=r.get('hotness_score', 0.0), source_keyword=r.get('source_keyword'), source_table=r['tbl']) for r in raw_results]
        return DBResponse("search_hot_content", params_for_log, results=formatted_results, results_count=len(formatted_results))

    def _wrap_query_field_with_dialect(self, field: str) -> str:
        """Encapsular campo de consulta SQL de acordo com o dialeto do banco de dados"""
        if settings.DB_DIALECT == 'postgresql':
            return f'"{field}"'
        return f'`{field}`'

    def search_topic_globally(self, topic: str, limit_per_table: int = 100) -> DBResponse:
        """
        [Ferramenta] Busca global de tópicos: Buscar um tópico especificado em todo o banco de dados (conteúdo, comentários, tags, palavra-chave de origem).

        Args:
            topic (str): Palavra-chave do tópico a ser buscado.
            limit_per_table (int): Número máximo de registros retornados de cada tabela relevante, padrão 100.

        Returns:
            DBResponse: Lista agregada contendo todos os resultados correspondentes.
        """
        params_for_log = {'topic': topic, 'limit_per_table': limit_per_table}
        logger.info(f"--- FERRAMENTA: Busca global de tópicos (params: {params_for_log}) ---")

        search_term, all_results = f"%{topic}%", []
        search_configs = { 'bilibili_video': {'fields': ['title', 'desc', 'source_keyword'], 'type': 'video'}, 'bilibili_video_comment': {'fields': ['content'], 'type': 'comment'}, 'douyin_aweme': {'fields': ['title', 'desc', 'source_keyword'], 'type': 'video'}, 'douyin_aweme_comment': {'fields': ['content'], 'type': 'comment'}, 'kuaishou_video': {'fields': ['title', 'desc', 'source_keyword'], 'type': 'video'}, 'kuaishou_video_comment': {'fields': ['content'], 'type': 'comment'}, 'weibo_note': {'fields': ['content', 'source_keyword'], 'type': 'note'}, 'weibo_note_comment': {'fields': ['content'], 'type': 'comment'}, 'xhs_note': {'fields': ['title', 'desc', 'tag_list', 'source_keyword'], 'type': 'note'}, 'xhs_note_comment': {'fields': ['content'], 'type': 'comment'}, 'zhihu_content': {'fields': ['title', 'desc', 'content_text', 'source_keyword'], 'type': 'content'}, 'zhihu_comment': {'fields': ['content'], 'type': 'comment'}, 'tieba_note': {'fields': ['title', 'desc', 'source_keyword'], 'type': 'note'}, 'tieba_comment': {'fields': ['content'], 'type': 'comment'}, 'daily_news': {'fields': ['title'], 'type': 'news'}, }

        for table, config in search_configs.items():
            param_dict = {}
            where_clauses = []
            for idx, field in enumerate(config['fields']):
                pname = f"term_{idx}"
                where_clauses.append(f'{self._wrap_query_field_with_dialect(field)} LIKE :{pname}')
                param_dict[pname] = search_term
            param_dict['limit'] = limit_per_table
            where_clause = " OR ".join(where_clauses)
            query = f'SELECT * FROM {self._wrap_query_field_with_dialect(table)} WHERE {where_clause} ORDER BY id DESC LIMIT :limit'
            raw_results = self._execute_query(query, param_dict)
            for row in raw_results:
                content = (row.get('title') or row.get('content') or row.get('desc') or row.get('content_text', ''))
                time_key = row.get('create_time') or row.get('time') or row.get('created_time') or row.get('publish_time') or row.get('crawl_date')
                all_results.append(QueryResult(
                    platform=table.split('_')[0], content_type=config['type'],
                    title_or_content=content if content else '',
                    author_nickname=row.get('nickname') or row.get('user_nickname') or row.get('user_name'),
                    url=row.get('video_url') or row.get('note_url') or row.get('content_url') or row.get('url') or row.get('aweme_url'),
                    publish_time=self._to_datetime(time_key),
                    engagement=self._extract_engagement(row),
                    source_keyword=row.get('source_keyword'),
                    source_table=table
                ))
        return DBResponse("search_topic_globally", params_for_log, results=all_results, results_count=len(all_results))

    def search_topic_by_date(self, topic: str, start_date: str, end_date: str, limit_per_table: int = 100) -> DBResponse:
        """
        [Ferramenta] Buscar tópico por data: Buscar conteúdo relacionado a um tópico específico em um período histórico definido.

        Args:
            topic (str): Palavra-chave do tópico a ser buscado.
            start_date (str): Data de início, formato 'YYYY-MM-DD'.
            end_date (str): Data de fim, formato 'YYYY-MM-DD'.
            limit_per_table (int): Número máximo de registros retornados de cada tabela relevante, padrão 100.

        Returns:
            DBResponse: Lista agregada contendo os resultados encontrados no intervalo de datas especificado.
        """
        params_for_log = {'topic': topic, 'start_date': start_date, 'end_date': end_date, 'limit_per_table': limit_per_table}
        logger.info(f"--- FERRAMENTA: Buscar tópico por data (params: {params_for_log}) ---")

        try:
            start_dt, end_dt = datetime.strptime(start_date, '%Y-%m-%d'), datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
        except ValueError:
            return DBResponse("search_topic_by_date", params_for_log, error_message="Formato de data incorreto, use o formato 'YYYY-MM-DD'.")

        search_term, all_results = f"%{topic}%", []
        search_configs = {
            'bilibili_video': {'fields': ['title', 'desc', 'source_keyword'], 'type': 'video', 'time_col': 'create_time', 'time_type': 'sec'}, 'douyin_aweme': {'fields': ['title', 'desc', 'source_keyword'], 'type': 'video', 'time_col': 'create_time', 'time_type': 'ms'},
            'kuaishou_video': {'fields': ['title', 'desc', 'source_keyword'], 'type': 'video', 'time_col': 'create_time', 'time_type': 'ms'}, 'weibo_note': {'fields': ['content', 'source_keyword'], 'type': 'note', 'time_col': 'create_date_time', 'time_type': 'str'},
            'xhs_note': {'fields': ['title', 'desc', 'tag_list', 'source_keyword'], 'type': 'note', 'time_col': 'time', 'time_type': 'ms'}, 'zhihu_content': {'fields': ['title', 'desc', 'content_text', 'source_keyword'], 'type': 'content', 'time_col': 'created_time', 'time_type': 'sec_str'},
            'tieba_note': {'fields': ['title', 'desc', 'source_keyword'], 'type': 'note', 'time_col': 'publish_time', 'time_type': 'str'}, 'daily_news': {'fields': ['title'], 'type': 'news', 'time_col': 'crawl_date', 'time_type': 'date_str'},
        }

        for table, config in search_configs.items():
            param_dict = {}
            where_clauses = []
            for idx, field in enumerate(config['fields']):
                pname = f"term_{idx}"
                where_clauses.append(f'{self._wrap_query_field_with_dialect(field)} LIKE :{pname}')
                param_dict[pname] = search_term
            param_dict['limit'] = limit_per_table
            where_clause = ' OR '.join(where_clauses)
            query = f'SELECT * FROM {self._wrap_query_field_with_dialect(table)} WHERE {where_clause} ORDER BY id DESC LIMIT :limit'
            raw_results = self._execute_query(query, param_dict)
            for row in raw_results:
                content = (row.get('title') or row.get('content') or row.get('desc') or row.get('content_text', ''))
                time_key = row.get('create_time') or row.get('time') or row.get('created_time') or row.get('publish_time') or row.get('crawl_date')
                all_results.append(QueryResult(
                    platform=table.split('_')[0], content_type=config['type'],
                    title_or_content=content if content else '',
                    author_nickname=row.get('nickname') or row.get('user_nickname') or row.get('user_name'),
                    url=row.get('video_url') or row.get('note_url') or row.get('content_url') or row.get('url') or row.get('aweme_url'),
                    publish_time=self._to_datetime(time_key),
                    engagement=self._extract_engagement(row),
                    source_keyword=row.get('source_keyword'),
                    source_table=table
                ))
        return DBResponse("search_topic_by_date", params_for_log, results=all_results, results_count=len(all_results))

    def get_comments_for_topic(self, topic: str, limit: int = 500) -> DBResponse:
        """
        [Ferramenta] Obter comentários de tópico: Buscar e retornar especificamente dados de comentários do público relacionados a um tópico específico em todas as plataformas.

        Args:
            topic (str): Palavra-chave do tópico a ser buscado.
            limit (int): Limite superior total de comentários retornados, padrão 500.

        Returns:
            DBResponse: Lista contendo os comentários correspondentes.
        """
        params_for_log = {'topic': topic, 'limit': limit}
        logger.info(f"--- FERRAMENTA: Obter comentários de tópico (params: {params_for_log}) ---")

        search_term = f"%{topic}%"
        comment_tables = ['bilibili_video_comment', 'douyin_aweme_comment', 'kuaishou_video_comment', 'weibo_note_comment', 'xhs_note_comment', 'zhihu_comment', 'tieba_comment']

        all_queries = []
        for table in comment_tables:
            cols = self._get_table_columns(table)
            author_col = 'user_nickname' if 'user_nickname' in cols else 'nickname'
            like_col = 'comment_like_count' if 'comment_like_count' in cols else 'like_count' if 'like_count' in cols else None
            time_col = 'publish_time' if 'publish_time' in cols else 'create_date_time' if 'create_date_time' in cols else 'create_time'
            like_select = f"`{like_col}` as likes" if like_col else "'0' as likes"

            query = (f"SELECT '{table.split('_')[0]}' as platform, `content`, `{author_col}` as author, "
                     f"`{time_col}` as ts, {like_select}, '{table}' as source_table "
                     f"FROM `{table}` WHERE `content` LIKE %s")
            all_queries.append(query)

        final_query = f"({' ) UNION ALL ( '.join(all_queries)}) ORDER BY ts DESC LIMIT %s"
        params = (search_term,) * len(comment_tables) + (limit,)
        raw_results = self._execute_query(final_query, params)

        formatted = [QueryResult(platform=r['platform'], content_type='comment', title_or_content=r['content'], author_nickname=r['author'], publish_time=self._to_datetime(r['ts']), engagement={'likes': int(r['likes']) if str(r['likes']).isdigit() else 0}, source_table=r['source_table']) for r in raw_results]
        return DBResponse("get_comments_for_topic", params_for_log, results=formatted, results_count=len(formatted))

    def search_topic_on_platform(
        self,
        platform: Literal['bilibili', 'weibo', 'douyin', 'kuaishou', 'xhs', 'zhihu', 'tieba'],
        topic: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 20
    ) -> DBResponse:
        """
        [Ferramenta] Busca direcionada por plataforma: (Nova) Buscar um tópico específico em uma única plataforma de mídia social.

        Args:
            platform (Literal['bilibili', ...]): Plataforma a ser buscada, deve ser uma das sete plataformas suportadas.
            topic (str): Palavra-chave do tópico a ser buscado.
            start_date (Optional[str]): Data de início, formato 'YYYY-MM-DD'. Padrão None.
            end_date (Optional[str]): Data de fim, formato 'YYYY-MM-DD'. Padrão None.
            limit (int): Número máximo de resultados retornados, padrão 20.

        Returns:
            DBResponse: Lista contendo os resultados encontrados na plataforma.
        """
        params_for_log = {'platform': platform, 'topic': topic, 'start_date': start_date, 'end_date': end_date, 'limit': limit}
        logger.info(f"--- FERRAMENTA: Busca direcionada por plataforma (params: {params_for_log}) ---")

        all_configs = { 'bilibili': [{'table': 'bilibili_video', 'fields': ['title', 'desc', 'source_keyword'], 'type': 'video', 'time_col': 'create_time', 'time_type': 'sec'}, {'table': 'bilibili_video_comment', 'fields': ['content'], 'type': 'comment'}], 'douyin': [{'table': 'douyin_aweme', 'fields': ['title', 'desc', 'source_keyword'], 'type': 'video', 'time_col': 'create_time', 'time_type': 'ms'}, {'table': 'douyin_aweme_comment', 'fields': ['content'], 'type': 'comment'}], 'kuaishou': [{'table': 'kuaishou_video', 'fields': ['title', 'desc', 'source_keyword'], 'type': 'video', 'time_col': 'create_time', 'time_type': 'ms'}, {'table': 'kuaishou_video_comment', 'fields': ['content'], 'type': 'comment'}], 'weibo': [{'table': 'weibo_note', 'fields': ['content', 'source_keyword'], 'type': 'note', 'time_col': 'create_date_time', 'time_type': 'str'}, {'table': 'weibo_note_comment', 'fields': ['content'], 'type': 'comment'}], 'xhs': [{'table': 'xhs_note', 'fields': ['title', 'desc', 'tag_list', 'source_keyword'], 'type': 'note', 'time_col': 'time', 'time_type': 'ms'}, {'table': 'xhs_note_comment', 'fields': ['content'], 'type': 'comment'}], 'zhihu': [{'table': 'zhihu_content', 'fields': ['title', 'desc', 'content_text', 'source_keyword'], 'type': 'content', 'time_col': 'created_time', 'time_type': 'sec_str'}, {'table': 'zhihu_comment', 'fields': ['content'], 'type': 'comment'}], 'tieba': [{'table': 'tieba_note', 'fields': ['title', 'desc', 'source_keyword'], 'type': 'note', 'time_col': 'publish_time', 'time_type': 'str'}, {'table': 'tieba_comment', 'fields': ['content'], 'type': 'comment'}] }

        if platform not in all_configs:
            return DBResponse("search_topic_on_platform", params_for_log, error_message=f"Plataforma não suportada: {platform}")

        search_term, all_results = f"%{topic}%", []
        platform_configs = all_configs[platform]

        time_clause, time_params_tuple = "", ()
        if start_date and end_date:
            try:
                start_dt, end_dt = datetime.strptime(start_date, '%Y-%m-%d'), datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
            except ValueError:
                return DBResponse("search_topic_on_platform", params_for_log, error_message="Formato de data incorreto, use o formato 'YYYY-MM-DD'.")
        else:
            start_dt, end_dt = None, None

        for config in platform_configs:
            table = config['table']
            topic_clause = " OR ".join([f"`{field}` LIKE %s" for field in config['fields']])
            query = f"SELECT * FROM `{table}` WHERE {topic_clause}"
            params = [search_term] * len(config['fields'])

            if start_dt and end_dt and 'time_col' in config:
                time_col, time_type = config['time_col'], config['time_type']
                if time_type == 'sec': t_params = (int(start_dt.timestamp()), int(end_dt.timestamp()))
                elif time_type == 'ms': t_params = (int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000))
                elif time_type in ['str', 'date_str']: t_params = (start_dt.strftime('%Y-%m-%d'), end_dt.strftime('%Y-%m-%d'))
                else: t_params = (str(int(start_dt.timestamp())), str(int(end_dt.timestamp())))

                t_clause = f"`{time_col}` >= %s AND `{time_col}` < %s"
                if table == 'zhihu_content': t_clause = f"CAST(`{time_col}` AS UNSIGNED) >= %s AND CAST(`{time_col}` AS UNSIGNED) < %s"

                query += f" AND ({t_clause})"
                params.extend(t_params)

            query += f" ORDER BY id DESC LIMIT %s"
            params.append(limit)

            raw_results = self._execute_query(query, tuple(params))
            for row in raw_results:
                content = (row.get('title') or row.get('content') or row.get('desc') or row.get('content_text', ''))
                time_key = config.get('time_col') and row.get(config.get('time_col'))
                all_results.append(QueryResult(platform=platform, content_type=config['type'], title_or_content=content if content else '', author_nickname=row.get('nickname') or row.get('user_nickname'), url=row.get('video_url') or row.get('note_url') or row.get('content_url') or row.get('url') or row.get('aweme_url'), publish_time=self._to_datetime(time_key), engagement=self._extract_engagement(row), source_keyword=row.get('source_keyword'), source_table=table))

        return DBResponse("search_topic_on_platform", params_for_log, results=all_results, results_count=len(all_results))

# --- 3. Testes e exemplos de uso ---
def print_response_summary(response: DBResponse):
    """Função simplificada de impressão para exibir resultados de teste"""
    if response.error_message:
        logger.info(f"Ferramenta '{response.tool_name}' executou com erro: {response.error_message}")
        return

    params_str = ", ".join(f"{k}='{v}'" for k, v in response.parameters.items())
    logger.info(f"Consulta: ferramenta='{response.tool_name}', parâmetros=[{params_str}]")
    logger.info(f"Encontrados {response.results_count} registros relacionados.")

    # Unificar em uma única saída
    output_lines = []
    output_lines.append("==== Prévia dos resultados (máximo 5 primeiros) ====")
    if response.results and len(response.results) > 0:
        for idx, res in enumerate(response.results[:5], 1):
            content_preview = (res.title_or_content.replace('\n', ' ')[:70] + '...') if res.title_or_content and len(res.title_or_content) > 70 else (res.title_or_content or '')
            author_str = res.author_nickname or "N/A"
            publish_time_str = res.publish_time.strftime('%Y-%m-%d %H:%M') if res.publish_time else "N/A"
            hotness_str = f", popularidade: {res.hotness_score:.2f}" if getattr(res, "hotness_score", 0) > 0 else ""
            engagement_dict = getattr(res, "engagement", {}) or {}
            engagement_str = ", ".join(f"{k}: {v}" for k, v in engagement_dict.items() if v)
            output_lines.append(
                f"{idx}. [{res.platform.upper()}/{res.content_type}] {content_preview}\n"
                f"   Autor: {author_str} | Data: {publish_time_str}"
                f"{hotness_str} | Palavra-chave de origem: '{res.source_keyword or 'N/A'}'\n"
                f"   Link: {res.url or 'N/A'}\n"
                f"   Dados de interação: {{{engagement_str}}}"
            )
    else:
        output_lines.append("Nenhum conteúdo relacionado no momento.")
    output_lines.append("=" * 60)
    logger.info('\n'.join(output_lines))

if __name__ == "__main__":

    try:
        db_agent_tools = MediaCrawlerDB()
        logger.info("Ferramentas de banco de dados inicializadas com sucesso, iniciando cenários de teste...\n")

        # Cenário 1: (Novo) Buscar conteúdo com maior popularidade geral na última semana (não precisa mais de sort_by)
        response1 = db_agent_tools.search_hot_content(time_period='week', limit=5)
        print_response_summary(response1)

        # Cenário 2: Buscar conteúdo com maior popularidade geral nas últimas 24 horas
        response2 = db_agent_tools.search_hot_content(time_period='24h', limit=5)
        print_response_summary(response2)

        # Cenário 3: Busca global de "Luo Yonghao"
        response3 = db_agent_tools.search_topic_globally(topic="罗永浩", limit_per_table=2)
        print_response_summary(response3)

        # Cenário 4: (Novo) Busca precisa de "dissertação" no Bilibili
        response4 = db_agent_tools.search_topic_on_platform(platform='bilibili', topic="论文", limit=5)
        print_response_summary(response4)

        # Cenário 5: (Novo) Busca precisa de "Xu Kai" no Weibo em um dia específico
        response5 = db_agent_tools.search_topic_on_platform(platform='weibo', topic="许凯", start_date='2025-08-22', end_date='2025-08-22', limit=5)
        print_response_summary(response5)

    except ValueError as e:
        logger.exception(f"Falha na inicialização: {e}")
        logger.exception("Verifique se as variáveis de ambiente do banco de dados estão configuradas corretamente, ou forneça as informações de conexão diretamente no código.")
    except Exception as e:
        logger.exception(f"Erro desconhecido durante os testes: {e}")
