"""规划器「无需检索」短路的行为约定。

知识库问答系统不应绕过知识库凭参数知识作答：
- 开关关闭（默认）：保持原短路行为，不检索、证据为空；
- 开关开启：把短路改写成一次混合检索，保证回答有知识库依据。
"""
from app.core.settings import settings
from app.rag.agentic_rag.schemas import (
    AnswerabilityResult,
    Evidence,
    RetrievalPlan,
    RetrievalStep,
)
from app.rag.agentic_rag.service import AgenticRagService


class FakePlanner:
    def __init__(self, plan: RetrievalPlan):
        self.plan_result = plan

    async def plan(self, query):
        return self.plan_result


class FakeLocalRetriever:
    def __init__(self, evidences=None):
        self.evidences = evidences or []
        self.calls = []

    async def search(self, user_id, steps):
        self.calls.append(list(steps))
        return self.evidences


class FakeEvaluator:
    async def evaluate(self, query, evidences):
        return AnswerabilityResult(answerable=True, confidence=0.9, reason="ok")


class FakeWebSearchClient:
    async def search(self, query, max_results=5):
        return []


def _service(plan: RetrievalPlan, evidences: list[Evidence]):
    retriever = FakeLocalRetriever(evidences)
    service = AgenticRagService(
        planner=FakePlanner(plan),
        local_retriever=retriever,
        evaluator=FakeEvaluator(),
        web_search_client=FakeWebSearchClient(),
    )
    return service, retriever


def _evidence() -> Evidence:
    return Evidence(
        id="doc:d1:0",
        source="knowledge_base",
        title="doc-1",
        content="content-1",
        score=0.016,
        metadata={"source_id": "d1"},
    )


async def test_short_circuit_kept_when_flag_off(monkeypatch):
    monkeypatch.setattr(settings, "PLANNER_REQUIRE_RETRIEVAL", False, raising=False)
    plan = RetrievalPlan(need_retrieval=False, steps=[], reason="no retrieval needed")
    service, retriever = _service(plan, [_evidence()])

    result = await service.run("你好", "u1")

    assert retriever.calls == []
    assert result.evidences == []
    assert result.context == ""


async def test_forced_retrieval_when_flag_on(monkeypatch):
    monkeypatch.setattr(settings, "PLANNER_REQUIRE_RETRIEVAL", True, raising=False)
    plan = RetrievalPlan(need_retrieval=False, steps=[], reason="no retrieval needed")
    service, retriever = _service(plan, [_evidence()])

    result = await service.run("报销流程是什么", "u1")

    assert len(retriever.calls) == 1
    assert retriever.calls[0][0].tool == "hybrid_search"
    assert result.plan.need_retrieval is True
    assert result.evidences, "强制检索后应当拿到证据"


async def test_forced_retrieval_keeps_original_steps(monkeypatch):
    """规划器本来就要求检索时，开关不生效，不重写它的检索计划。"""
    monkeypatch.setattr(settings, "PLANNER_REQUIRE_RETRIEVAL", True, raising=False)
    plan = RetrievalPlan(
        need_retrieval=True,
        steps=[RetrievalStep(tool="search_graph", query="实体查询")],
        reason="normal",
    )
    service, retriever = _service(plan, [])

    result = await service.run("问题", "u1")

    assert [step.tool for step in result.plan.steps] == ["search_graph"]
    # 图检索与文本检索各走一次：前者带 search_graph 步骤，后者步骤为空
    assert [step.tool for step in retriever.calls[0]] == ["search_graph"]
    assert retriever.calls[1] == []
