"""app/schemas/models.py 的 Pydantic 模型校验测试。"""
import pytest
from pydantic import ValidationError

from app.schemas.models import (
    AgentResponse,
    AgentStep,
    ChunkDetail,
    ChunkInfo,
    DocumentChunksResponse,
    KnowledgeDocument,
    KnowledgeListResponse,
    MD5ListResponse,
    MD5Record,
    QueryRequest,
    ReorderRequest,
    ReorderResponse,
    SessionResponse,
)


def _errors(excinfo: pytest.ExceptionInfo):
    return excinfo.value.errors()


def _fields(excinfo: pytest.ExceptionInfo):
    return {tuple(e["loc"]): e["type"] for e in _errors(excinfo)}


def _has_error_for(excinfo: pytest.ExceptionInfo, field: str):
    return any(field in e["loc"] for e in _errors(excinfo))


def _assert_raises_for(model, payload, field: str):
    with pytest.raises(ValidationError) as excinfo:
        model(**payload)
    assert _has_error_for(excinfo, field), f"期望字段 {field} 校验失败，实际错误: {_errors(excinfo)}"


class TestQueryRequest:
    def test_valid_with_all_fields(self):
        model = QueryRequest(session_id="s1", query="你好")
        assert model.session_id == "s1"
        assert model.query == "你好"

    def test_session_id_defaults_to_none(self):
        model = QueryRequest(query="hi")
        assert model.session_id is None

    def test_query_required(self):
        _assert_raises_for(QueryRequest, {}, "query")

    def test_query_must_be_str(self):
        _assert_raises_for(QueryRequest, {"query": 123}, "query")


class TestSessionResponse:
    def test_valid_tuple_history(self):
        model = SessionResponse(session_id="s1", history=[("user", "hi"), ("assistant", "hello")])
        assert model.history == [("user", "hi"), ("assistant", "hello")]

    def test_history_required(self):
        _assert_raises_for(SessionResponse, {"session_id": "s1"}, "history")

    def test_invalid_history_shape(self):
        with pytest.raises(ValidationError):
            SessionResponse(session_id="s1", history=["not-a-tuple"])


class TestAgentModels:
    def test_agent_step_all_defaults(self):
        step = AgentStep()
        assert step.thought is None
        assert step.tool is None
        assert step.tool_input is None
        assert step.tool_output is None

    def test_agent_step_full(self):
        step = AgentStep(thought="t", tool="search", tool_input={"q": "1"}, tool_output="out")
        assert step.tool_input == {"q": "1"}

    def test_agent_response_requires_response_and_session_id(self):
        _assert_raises_for(AgentResponse, {"session_id": "s1"}, "response")
        _assert_raises_for(AgentResponse, {"response": "r"}, "session_id")

    def test_agent_response_steps_optional(self):
        model = AgentResponse(response="r", session_id="s1")
        assert model.steps is None

    def test_agent_response_with_steps(self):
        model = AgentResponse(response="r", session_id="s1", steps=[AgentStep(thought="t")])
        assert model.steps[0].thought == "t"


class TestReorderModels:
    def test_reorder_request_valid(self):
        model = ReorderRequest(query="q", documents=["d1", "d2"])
        assert model.documents == ["d1", "d2"]

    def test_reorder_request_documents_required(self):
        _assert_raises_for(ReorderRequest, {"query": "q"}, "documents")

    def test_reorder_request_invalid_documents_type(self):
        _assert_raises_for(ReorderRequest, {"query": "q", "documents": "d1"}, "documents")

    def test_reorder_response_documents_must_be_dicts(self):
        with pytest.raises(ValidationError):
            ReorderResponse(documents=[{"a": 1}, "not-dict"])


class TestKnowledgeModels:
    def test_knowledge_document_valid(self):
        model = KnowledgeDocument(
            id="doc1", filename="a.pdf", chunk_count=3, preview="p",
            original_filename="a.pdf", user_id="u1", created_at="2024-01-01",
        )
        assert model.chunk_count == 3

    def test_knowledge_document_optional_fields_default_none(self):
        model = KnowledgeDocument(id="doc1", filename="a.pdf", chunk_count=3, preview="p")
        assert model.original_filename is None
        assert model.user_id is None
        assert model.created_at is None

    def test_knowledge_document_required_fields(self):
        _assert_raises_for(KnowledgeDocument, {"id": "doc1", "filename": "a.pdf"}, "chunk_count")
        _assert_raises_for(KnowledgeDocument, {"filename": "a.pdf", "chunk_count": 1, "preview": "p"}, "id")

    def test_knowledge_list_response(self):
        doc = KnowledgeDocument(id="doc1", filename="a.pdf", chunk_count=1, preview="p")
        model = KnowledgeListResponse(documents=[doc], total_count=1)
        assert model.total_count == 1

    def test_knowledge_list_response_invalid_documents(self):
        with pytest.raises(ValidationError):
            KnowledgeListResponse(documents=[{"bad": "payload"}], total_count=0)


class TestChunkModels:
    def test_chunk_detail_defaults(self):
        chunk = ChunkDetail(chunk_id="c1", index=0, content="text")
        assert chunk.page is None
        assert chunk.images == []

    def test_chunk_detail_missing_content(self):
        _assert_raises_for(ChunkDetail, {"chunk_id": "c1", "index": 0}, "content")

    def test_chunk_info_metadata_required(self):
        _assert_raises_for(ChunkInfo, {"chunk_id": "c1", "index": 0, "content": "x"}, "metadata")

    def test_chunk_info_images_default(self):
        info = ChunkInfo(chunk_id="c1", index=0, content="x", metadata={})
        assert info.images == []

    def test_document_chunks_response(self):
        chunk = ChunkInfo(chunk_id="c1", index=0, content="x", metadata={})
        model = DocumentChunksResponse(filename="a.pdf", total_chunks=1, chunks=[chunk])
        assert model.total_chunks == 1

    def test_document_chunks_response_chunks_required(self):
        _assert_raises_for(DocumentChunksResponse, {"filename": "a.pdf", "total_chunks": 0}, "chunks")


class TestMD5Models:
    def test_md5_record_valid(self):
        model = MD5Record(md5="abc123")
        assert model.filename is None

    def test_md5_record_required(self):
        _assert_raises_for(MD5Record, {}, "md5")

    def test_md5_list_response(self):
        model = MD5ListResponse(records=[MD5Record(md5="abc")], total_count=1)
        assert model.total_count == 1
