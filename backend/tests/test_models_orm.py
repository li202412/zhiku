"""ORM 模型测试：表注册、字段默认值、唯一约束与级联删除。

覆盖裁剪后的三张业务表 + 图谱表（笔记/模板/复习相关表已随功能下线移除）。
"""
import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.models.chat_history import ChatMessage, ChatSession
from app.models.graph import (
    GraphBuildTask,
    GraphDoc,
    GraphEntity,
    GraphEntityNote,
    GraphExtractLog,
)
from app.models.user_model import User


@pytest.mark.asyncio
async def test_create_all_registers_expected_tables(db_engine):
    async with db_engine.begin() as conn:
        tables = await conn.run_sync(lambda sc: inspect(sc).get_table_names())

    for name in (
        "chat_sessions",
        "chat_messages",
        "user_service",
        "graph_entity_types",
        "graph_entities",
        "graph_relations",
        "graph_entity_notes",
        "graph_extract_logs",
        "graph_docs",
        "graph_build_tasks",
    ):
        assert name in tables


class TestChatModels:
    @pytest.mark.asyncio
    async def test_session_defaults(self, db_session):
        session = ChatSession(id="s-1", user_id="u1")
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        assert session.title == "新的对话"
        assert session.created_at is not None
        assert session.updated_at is not None

    @pytest.mark.asyncio
    async def test_user_id_required(self, db_session):
        db_session.add(ChatSession(id="s-2"))
        with pytest.raises(IntegrityError):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_messages_relationship_and_cascade(self, db_session):
        session = ChatSession(id="s-3", user_id="u1", title="对话")
        session.messages = [
            ChatMessage(role="user", content="你好"),
            ChatMessage(role="assistant", content="你好，有什么可以帮你"),
        ]
        db_session.add(session)
        await db_session.commit()

        rows = (await db_session.execute(
            select(ChatMessage).where(ChatMessage.session_id == "s-3"))).scalars().all()
        assert [r.role for r in rows] == ["user", "assistant"]

        await db_session.delete(session)
        await db_session.commit()
        left = (await db_session.execute(
            select(ChatMessage).where(ChatMessage.session_id == "s-3"))).scalars().all()
        assert left == []


class TestUserModel:
    @pytest.mark.asyncio
    async def test_defaults(self, db_session):
        user = User(username="u1", email="u1@example.com", password="hashed")
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        assert user.uuid and len(user.uuid) == 24
        assert user.is_active is False
        assert user.status == 0
        assert user.date_joined is not None

    @pytest.mark.asyncio
    async def test_email_unique(self, db_session):
        db_session.add(User(username="a", email="dup@example.com", password="x"))
        await db_session.commit()

        db_session.add(User(username="b", email="dup@example.com", password="x"))
        with pytest.raises(IntegrityError):
            await db_session.commit()


class TestGraphModels:
    @pytest.mark.asyncio
    async def test_entity_unique_per_user(self, db_session):
        db_session.add(GraphEntity(id="e1", user_id="u1", name="Python", display_name="Python"))
        await db_session.commit()

        db_session.add(GraphEntity(id="e2", user_id="u1", name="Python", display_name="Python"))
        with pytest.raises(IntegrityError):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_entity_same_name_different_user_ok(self, db_session):
        db_session.add(GraphEntity(id="e1", user_id="u1", name="Python", display_name="Python"))
        db_session.add(GraphEntity(id="e2", user_id="u2", name="Python", display_name="Python"))
        await db_session.commit()

        rows = (await db_session.execute(select(GraphEntity))).scalars().all()
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_entity_note_defaults_to_doc(self, db_session):
        note = GraphEntityNote(id="en1", user_id="u1", entity_id="e1", note_id="md5-abc")
        db_session.add(note)
        await db_session.commit()
        await db_session.refresh(note)

        assert note.source_type == "doc"
        assert note.mention_count == 0

    @pytest.mark.asyncio
    async def test_extract_log_unique_per_user_note_type(self, db_session):
        db_session.add(GraphExtractLog(id="l1", user_id="u1", note_id="md5-abc",
                                       source_type="doc", content_hash="h", status="success"))
        await db_session.commit()

        db_session.add(GraphExtractLog(id="l2", user_id="u1", note_id="md5-abc",
                                       source_type="doc", content_hash="h2"))
        with pytest.raises(IntegrityError):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_build_task_unique_per_source(self, db_session):
        db_session.add(GraphBuildTask(id="t1", user_id="u1", source_type="doc",
                                      source_id="md5-abc", content_hash="h"))
        await db_session.commit()

        db_session.add(GraphBuildTask(id="t2", user_id="u1", source_type="doc",
                                      source_id="md5-abc", content_hash="h2"))
        with pytest.raises(IntegrityError):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_doc_filename_required(self, db_session):
        db_session.add(GraphDoc(id="md5-abc", user_id="u1"))
        with pytest.raises(IntegrityError):
            await db_session.commit()
