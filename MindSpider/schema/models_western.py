"""
ORM models para plataformas ocidentais (Phase 4 - Western Social Media).

Tabelas: reddit, youtube, twitter, instagram, tiktok (conteúdo + comentários).
Segue o mesmo padrão de models_bigdata.py, usando o Base de models_sa.py.
"""

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Integer, String, BigInteger, Text, ForeignKey

from models_sa import Base


# ==================== Reddit ====================

class RedditPost(Base):
    __tablename__ = "reddit_post"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar: Mapped[str | None] = mapped_column(Text, nullable=True)
    liked_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    share_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    view_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    create_time: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    add_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_modify_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_keyword: Mapped[str | None] = mapped_column(Text, default='', nullable=True)
    topic_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("daily_topics.topic_id", ondelete="SET NULL"), nullable=True)
    crawling_task_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("crawling_tasks.task_id", ondelete="SET NULL"), nullable=True)


class RedditPostComment(Base):
    __tablename__ = "reddit_post_comment"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comment_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    content_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    parent_comment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    liked_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    create_time: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    add_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_modify_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


# ==================== YouTube ====================

class YoutubeVideo(Base):
    __tablename__ = "youtube_video"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar: Mapped[str | None] = mapped_column(Text, nullable=True)
    liked_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    share_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    view_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    create_time: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    add_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_modify_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_keyword: Mapped[str | None] = mapped_column(Text, default='', nullable=True)
    topic_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("daily_topics.topic_id", ondelete="SET NULL"), nullable=True)
    crawling_task_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("crawling_tasks.task_id", ondelete="SET NULL"), nullable=True)


class YoutubeVideoComment(Base):
    __tablename__ = "youtube_video_comment"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comment_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    content_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    parent_comment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    liked_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    create_time: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    add_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_modify_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


# ==================== Twitter ====================

class TwitterPost(Base):
    __tablename__ = "twitter_post"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar: Mapped[str | None] = mapped_column(Text, nullable=True)
    liked_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    share_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    view_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    create_time: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    add_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_modify_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_keyword: Mapped[str | None] = mapped_column(Text, default='', nullable=True)
    topic_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("daily_topics.topic_id", ondelete="SET NULL"), nullable=True)
    crawling_task_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("crawling_tasks.task_id", ondelete="SET NULL"), nullable=True)


class TwitterPostComment(Base):
    __tablename__ = "twitter_post_comment"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comment_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    content_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    parent_comment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    liked_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    create_time: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    add_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_modify_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


# ==================== Instagram ====================

class InstagramPost(Base):
    __tablename__ = "instagram_post"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar: Mapped[str | None] = mapped_column(Text, nullable=True)
    liked_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    share_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    view_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    create_time: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    add_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_modify_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_keyword: Mapped[str | None] = mapped_column(Text, default='', nullable=True)
    topic_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("daily_topics.topic_id", ondelete="SET NULL"), nullable=True)
    crawling_task_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("crawling_tasks.task_id", ondelete="SET NULL"), nullable=True)


class InstagramPostComment(Base):
    __tablename__ = "instagram_post_comment"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comment_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    content_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    parent_comment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    liked_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    create_time: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    add_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_modify_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


# ==================== TikTok ====================

class TiktokVideo(Base):
    __tablename__ = "tiktok_video"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar: Mapped[str | None] = mapped_column(Text, nullable=True)
    liked_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    share_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    view_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    create_time: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    add_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_modify_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_keyword: Mapped[str | None] = mapped_column(Text, default='', nullable=True)
    topic_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("daily_topics.topic_id", ondelete="SET NULL"), nullable=True)
    crawling_task_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("crawling_tasks.task_id", ondelete="SET NULL"), nullable=True)


class TiktokVideoComment(Base):
    __tablename__ = "tiktok_video_comment"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comment_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    content_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    parent_comment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    liked_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    create_time: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    add_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_modify_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
