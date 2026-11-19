"""证据筛选（EVIDENCE_GATE_ENABLED）的行为约定。

- 默认关闭：完全保持原证据列表，不做任何裁剪；
- 开启且给出有效编号：只保留被选中的证据。
- 编号为空或全部越界：**不裁剪**（避免评估抖动把上下文清空）。
"""
from app.core.settings import settings
from app.rag.agentic_rag.schemas import AnswerabilityResult, Evidence
from app.rag.agentic_rag.service import AgenticRagService


def _evidence(index: int) -> Evidence:
    return Evidence(
        id=f"doc:d{index}:0",
        source="knowledge_base",
        title=f"doc-{index}",
        content=f"content-{index}",
        score=0.01 * index,
        metadata={"source_id": f"d{index}"},
    )


def _answerability(indices: list[int]) -> AnswerabilityResult:
    return AnswerabilityResult(answerable=True, confidence=0.8, reason="ok", relevant_indices=indices)


def test_gate_disabled_keeps_all(monkeypatch):
    monkeypatch.setattr(settings, "EVIDENCE_GATE_ENABLED", False, raising=False)
    evidences = [_evidence(1), _evidence(2), _evidence(3)]
    kept = AgenticRagService._gate_evidences(evidences, _answerability([2]))
    assert kept == evidences


def test_gate_keeps_selected_indices(monkeypatch):
    monkeypatch.setattr(settings, "EVIDENCE_GATE_ENABLED", True, raising=False)
    evidences = [_evidence(1), _evidence(2), _evidence(3)]
    kept = AgenticRagService._gate_evidences(evidences, _answerability([1, 3]))
    assert [e.metadata["source_id"] for e in kept] == ["d1", "d3"]


def test_gate_falls_back_when_indices_empty(monkeypatch):
    monkeypatch.setattr(settings, "EVIDENCE_GATE_ENABLED", True, raising=False)
    evidences = [_evidence(1), _evidence(2)]
    assert AgenticRagService._gate_evidences(evidences, _answerability([])) == evidences


def test_gate_falls_back_when_indices_out_of_range(monkeypatch):
    monkeypatch.setattr(settings, "EVIDENCE_GATE_ENABLED", True, raising=False)
    evidences = [_evidence(1), _evidence(2)]
    assert AgenticRagService._gate_evidences(evidences, _answerability([9])) == evidences


def test_gate_handles_missing_answerability(monkeypatch):
    monkeypatch.setattr(settings, "EVIDENCE_GATE_ENABLED", True, raising=False)
    evidences = [_evidence(1)]
    assert AgenticRagService._gate_evidences(evidences, None) == evidences
