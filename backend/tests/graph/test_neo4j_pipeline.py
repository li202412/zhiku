"""抽取管线 Neo4j 主路径端到端测试（需要真实 Neo4j）。

    NEO4J_TEST_URI=bolt://localhost:7687 uv run --extra dev python -m pytest tests/graph/test_neo4j_pipeline.py -v

用 SQLite 承载 GraphDoc/extract_logs，monkeypatch 会话工厂与假 chat/embed 模型，
settings.NEO4J_URI 指向真库 → 工厂返回 Neo4jGraphStore，验证管线完整写入图数据库。
"""
import os
import uuid

import pytest

from app.core import background_init
from app.graph.services.graph_service import _run_extraction, content_hash
from tests.fakes import make_fake_chat_model

pytestmark = pytest.mark.skipif(
    not os.getenv("NEO4J_TEST_URI"), reason="需要真实 Neo4j（设 NEO4J_TEST_URI 启用）")

DIM = 8  # 与 schema 探针对齐


class FakeEmbed:
    """定维假嵌入模型：按文本哈希选正交基方向，同文本同向量。"""

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]

    def embed_query(self, text):
        v = [0.0] * DIM
        v[hash(text) % DIM] = 1.0
        return v


@pytest.fixture
def neo4j_env(monkeypatch):
    from app.core.failed_response import settings

    monkeypatch.setattr(settings, "NEO4J_URI", os.environ["NEO4J_TEST_URI"], raising=False)
    monkeypatch.setattr(background_init.init_manager, "embed_model", FakeEmbed())


@pytest.mark.asyncio
async def test_doc_pipeline_with_prechunks(db_session, session_factory, monkeypatch, neo4j_env, _cleanup):
    from sqlalchemy import select

    from app.graph.storage import neo4j_client
    from app.models.graph import GraphDoc, GraphExtractLog

    uid = "pipe-" + uuid.uuid4().hex[:8]
    db_session.add(GraphDoc(id="md5-x", user_id=uid, filename="doc.pdf"))
    await db_session.commit()
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    monkeypatch.setattr(
        background_init.init_manager, "chat_model",
        make_fake_chat_model([
            '{"entities": [{"name": "报表", "type": "concept", "mentions": ["报表"]}], "relations": []}'
        ]))

    body = "这是一份季度报表，包含营收数据。" * 10
    pre_chunks = [{"chunk_index": 0, "text": body[:50], "page": 1, "image_paths": ["img1.png"]},
                  {"chunk_index": 1, "text": body[50:], "page": 2, "image_paths": []}]
    await _run_extraction("md5-x", uid, "doc.pdf", content_hash(body), body=body,
                          source_type="doc", chunks=pre_chunks)

    log = (await db_session.execute(
        select(GraphExtractLog).where(GraphExtractLog.note_id == "md5-x"))).scalar_one()
    assert log.status == "success"

    driver = neo4j_client.get_neo4j_driver()
    chunk_rows = await driver.execute_query(
        "MATCH (c:Chunk {user_id: $uid}) RETURN c.page AS page, c.image_paths AS imgs, c.text AS text "
        "ORDER BY c.chunk_index", {"uid": uid})
    records = list(chunk_rows.records)
    assert len(records) == 2
    assert records[0]["page"] == 1 and records[0]["imgs"] == ["img1.png"]

    src_mention = await driver.execute_query(
        "MATCH (:Doc)-[:MENTIONS]->(e:Entity) WHERE e.user_id=$uid RETURN count(*) AS c", {"uid": uid})
    assert src_mention.records[0]["c"] >= 1


@pytest.mark.asyncio
async def test_doc_pipeline_creates_missing_log_row(db_session, session_factory, monkeypatch,
                                                    neo4j_env, _cleanup):
    """日志行缺失（抽取期间被清理/从未持久化）时不得崩溃，应自建日志行并落 success。"""
    from sqlalchemy import select

    from app.models.graph import GraphDoc, GraphExtractLog

    uid = "pipe-" + uuid.uuid4().hex[:8]
    db_session.add(GraphDoc(id="md5-nolog", user_id=uid, filename="报告.pdf"))
    await db_session.commit()
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    monkeypatch.setattr(
        background_init.init_manager, "chat_model",
        make_fake_chat_model(
            ['{"entities": [{"name": "Python", "type": "tech", "mentions": ["Python"]}], "relations": []}']))

    await _run_extraction("md5-nolog", uid, "报告.pdf", content_hash("body"),
                          body="用 Python 写", source_type="doc")

    log = (await db_session.execute(select(GraphExtractLog).where(
        GraphExtractLog.note_id == "md5-nolog"))).scalar_one()
    assert log.status == "success" and log.content_hash == content_hash("body")


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_extraction_replaces_stale_relations_keeps_manual(db_session, session_factory,
                                                                monkeypatch, neo4j_env, _cleanup):
    """重抽按来源溯源替换旧关系；无溯源标记的手动关系（图谱页手工连线）不受影响。"""

    from app.graph.schemas.graph import EntityIn, RelationIn
    from app.graph.storage import neo4j_client
    from app.graph.storage.neo4j_graph_store import Neo4jGraphStore
    from app.models.graph import GraphDoc

    uid = "pipe-" + uuid.uuid4().hex[:8]
    db_session.add(GraphDoc(id="md5-rel", user_id=uid, filename="报告.pdf"))
    await db_session.commit()
    monkeypatch.setattr("app.graph.services.graph_service.AsyncSessionLocal", session_factory)
    monkeypatch.setattr(
        background_init.init_manager, "chat_model",
        make_fake_chat_model([
            '{"entities": [{"name": "实体A", "type": "concept", "mentions": ["A"]}, '
            '{"name": "实体B", "type": "concept", "mentions": ["B"]}], '
            '"relations": [{"source": "实体A", "target": "实体B", "relation_type": "新关系"}]}']))

    # 预置：两个实体 + 手动关系（无溯源）+ 旧抽取关系（doc 溯源）
    store = Neo4jGraphStore()
    ea = await store.upsert_entity(uid, EntityIn(name="实体A"))
    eb = await store.upsert_entity(uid, EntityIn(name="实体B"))
    await store.create_relation(uid, RelationIn(
        source_id=ea.id, target_id=eb.id, relation_type="手动关系"))
    await store.set_relations_from_source(uid, "doc", "md5-rel", [
        {"source_id": ea.id, "target_id": eb.id, "relation_type": "旧关系"}])

    await _run_extraction("md5-rel", uid, "报告.pdf", content_hash("body"),
                          body="A 与 B", source_type="doc")

    driver = neo4j_client.get_neo4j_driver()
    rels = await driver.execute_query(
        "MATCH (a:Entity {user_id: $uid})-[r:RELATES_TO]->(b:Entity {user_id: $uid}) "
        "RETURN r.relation_type AS t, r.source_type AS stype", {"uid": uid})
    by_type = {r["t"]: r["stype"] for r in rels.records}
    assert set(by_type) == {"手动关系", "新关系"}
    # 手动关系无溯源标记（source_type 为空）；抽取关系带 doc 溯源
    assert by_type["手动关系"] is None
    assert by_type["新关系"] == "doc"
