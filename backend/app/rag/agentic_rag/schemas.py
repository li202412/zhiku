from typing import Any, Literal

from pydantic import BaseModel, Field

EvidenceSource = Literal["knowledge_base", "graph", "web"]
RetrievalTool = Literal[
    "search_knowledge_base",
    "search_graph",
    "hybrid_search",
    "web_search",
]


class Evidence(BaseModel):
    id: str
    source: EvidenceSource
    title: str
    content: str
    score: float | None = None
    url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalStep(BaseModel):
    tool: RetrievalTool
    query: str
    top_k: int = 5


class RetrievalPlan(BaseModel):
    need_retrieval: bool
    steps: list[RetrievalStep]
    allow_web_fallback: bool = False
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnswerabilityResult(BaseModel):
    answerable: bool
    confidence: float
    reason: str
    web_queries: list[str] = Field(default_factory=list)
    # 可答性评估顺带标出的「真正能用来作答」的证据编号（1-based），用于裁剪无关证据
    relevant_indices: list[int] = Field(default_factory=list)


class AgenticRagResult(BaseModel):
    context: str
    evidences: list[Evidence]
    plan: RetrievalPlan
    answerability: AnswerabilityResult | None = None
    used_web: bool = False
