"""端到端 Agentic RAG 评测：答案质量 + 引用覆盖率 + 拒答准确率 + 延迟。

流程（正样本，衡量「答案质量」）：
1. 从语料取一段文档正文，LLM 自动生成 (question, reference_answer)。
2. AgenticRagService.run(question, user_id) 跑真实管线（规划+检索+可答性+证据融合）。
3. 用答案提示词 + question 让 chat 模型流式生成回答，统计首 token 时间。
4. LLM 裁判对 (question, ref_answer, evidence, answer) 打分：faithfulness / correctness。
5. 统计引用覆盖率：回答里的 `> 来源：知识库《…》` 对上下文实际给出的知识库文档标题的覆盖。

流程（负样本，衡量「拒答能力」）：
1. 从 qrels 里筛出「相关文档全部不在入库池内」的 query —— 对系统而言即「库里没有答案」。
2. 跑同一条真实管线，判定是否拒答：结构化 answerable=False + 回答文本表态 + 未触发外部搜索。

⚠️ 口径提醒：
- 正样本题目由源正文生成，检索必然命中 → 衡量答案质量，不衡量检索难度。
- 负样本「qrels 无相关」不等于「全库绝对无关」，故拒答准确率只作**上界**口径。
- 引用覆盖率的分母取自 `result.context`（已被 6000 字符截断），即模型真正看到的证据。

读：results/eval_retrieval/manifest.json、data/eval/duretrieval/{corpus,queries,qrels}.parquet
写：results/eval_end2end/{report.json, report.md, report_negatives.json}
"""
import argparse
import asyncio
import hashlib
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import pandas as pd  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from app.rag.agentic_rag.service import AgenticRagService  # noqa: E402
from app.utils.factory import ChatModelFactory  # noqa: E402
from app.utils.prompt_loader import load_prompt  # noqa: E402

GEN_PROMPT = (
    "你是评测数据生成器。根据下面这段文档，生成一个真实用户会提出的问题，并给出一段准确、"
    "完整的参考答案（可忠实引用原文）。只返回 JSON（不要任何多余文字）：\n"
    '{{"question": "...", "answer": "..."}}\n\n文档：\n{doc}'
)

JUDGE_PROMPT = (
    "你是严格的 RAG 评测裁判。判断【回答】在多大程度上由【检索证据】支撑（忠实度 faithfulness），"
    "以及它是否正确回答了【问题】（正确性 correctness，以【参考答案】为准）。\n"
    "打分 1-5：5=完全支撑/完全正确，3=部分，1=基本不支撑/错误。\n"
    '只返回 JSON：{{"faithfulness": <1-5>, "correctness": <1-5>, "faithful_reason": "...", "correct_reason": "..."}}\n\n'
    "问题：{question}\n参考答案：{ref_answer}\n检索证据：{evidence}\n回答：{answer}"
)

# 旧口径：自定义提示词，不要求写来源行。保留用于与历史基线对照。
LEGACY_ANSWER_SYSTEM = (
    "你是用户的智能助手。\n\n以下是与用户问题相关的参考资料：\n{context}\n\n"
    "请基于以上资料回答用户的问题。回答时必须区分本地证据（笔记、知识库）与外部搜索证据，"
    "避免把外部搜索内容说成用户本地资料。如果资料中没有足够信息支撑结论，必须明确说明证据不足，"
    "并说明还缺少哪些信息。"
)

# 产品提示词提到「可以调用工具」，而评测脚本没有工具，必要时追加一句避免空转。
PRODUCT_NOTOOL_SUFFIX = "\n\n（本轮你无法调用任何检索工具，请直接基于上面给出的资料作答。）"

# 拒答行为分类裁判：比穷举措辞更稳，也便于区分「谨慎作答」与「无依据硬答」。
JUDGE_REFUSAL_PROMPT = (
    "你在判断一个文档知识库问答系统的行为是否恰当。用户问了一个问题，系统的知识库里可能没有相关资料。\n"
    "请判断系统回答属于哪一类：\n"
    "- refuse：明确说明知识库中没有相关资料/证据不足，拒绝给出具体结论\n"
    "- hedge：承认现有资料不直接相关或不足，但仍基于资料给出有限的、有保留的说明\n"
    "- answer：直接给出具体结论，未声明资料不足\n"
    '只返回 JSON：{{"category": "refuse|hedge|answer", "reason": "不超过 40 字"}}\n\n'
    "问题：{question}\n系统回答：{answer}"
)

# 上下文里的来源头：[1] 来源：知识库《文档名》
_CTX_SOURCE_RE = re.compile(r"^\[(\d+)\]\s*来源：(知识库|知识图谱|外部搜索)《([^》\n]+)》", re.M)
# 回答里的来源块：> 来源：知识库《A》、《B》（允许 ** 加粗变体与全角冒号）
_CITE_LINE_RE = re.compile(
    r"^[ \t]*\**[ \t]*>[ \t]*\**[ \t]*来源[ \t]*[:：][ \t]*\**[ \t]*知识库(.*)$", re.M)
_TITLE_RE = re.compile(r"《([^》\n]{1,100})》")
_EXT_RE = re.compile(r"\.(pdf|docx|md|txt|pptx)$", re.I)
_WRAP_CHARS = "《》\"'「」『』"

# 拒答表态（不作为唯一依据，与结构化判定组合使用）。
# 覆盖实测中模型真实使用过的说法：先否定「知识库资料涉及」，再兜常见短语。
_REFUSAL_RE = re.compile(
    r"知识库[中里](?:的)?(?:现有|已有)?(?:资料|文档|内容)(?:并未|没有|未|无法)(?:涉及|提及|涵盖|包含|找到|检索到)"
    r"|没有检索到|未检索到|未找到相关|证据不足|资料不足|没有相关资料"
    r"|无法(?:根据|从|据此)(?:知识库)?(?:给出|回答|判断)?"
    r"|没有专门(?:说明|提及)|未涉及|没有涉及"
    r"|没有关于[^。]{0,30}的(?:说明|提及|资料|内容)"
)


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"no JSON in: {text[:200]}")
    return json.loads(m.group(0))


def _as_int(v) -> int:
    try:
        return max(1, min(5, int(float(v))))
    except (TypeError, ValueError):
        return 1


async def _ask(chat, messages) -> str:
    resp = await chat.ainvoke(messages)
    content = getattr(resp, "content", resp)
    return content if isinstance(content, str) else str(content)


async def _stream_answer(chat, system_prompt: str, question: str):
    """流式生成完整回答，返回 (full_text, first_token_ms)。"""
    full: list[str] = []
    first_ms = None
    t0 = time.time()
    async for chunk in chat.astream([SystemMessage(content=system_prompt), HumanMessage(content=question)]):
        text = getattr(chunk, "content", chunk)
        if not text:
            continue
        if first_ms is None:
            first_ms = (time.time() - t0) * 1000.0
        if isinstance(text, list):
            text = "".join(c.get("text", "") for c in text if isinstance(c, dict))
        full.append(text)
    return "".join(full), first_ms


def _norm_title(title: str) -> str:
    """标题归一化：去包裹符号、空白、常见扩展名，统一大小写。"""
    t = (title or "").strip().strip(_WRAP_CHARS)
    t = re.sub(r"\s+", "", t)
    t = _EXT_RE.sub("", t)
    return t.casefold()


def _context_titles(context: str) -> tuple[set[str], set[str]]:
    """从证据上下文中解析标题，按来源分组（只取归一化后的非空标题）。"""
    kb: set[str] = set()
    graph: set[str] = set()
    for label, title in ((m.group(2), m.group(3)) for m in _CTX_SOURCE_RE.finditer(context or "")):
        norm = _norm_title(title)
        if not norm:
            continue
        (kb if label == "知识库" else graph).add(norm)
    return kb, graph


def _cited_titles(answer: str) -> list[str]:
    """解析回答里的 `> 来源：知识库《…》` 行，返回归一化标题列表（保留重复）。"""
    titles: list[str] = []
    for m in _CITE_LINE_RE.finditer(answer or ""):
        for raw in _TITLE_RE.findall(m.group(1)):
            norm = _norm_title(raw)
            if norm:
                titles.append(norm)
    return titles


def citation_metrics(answer: str, context: str) -> dict:
    """计算单条样本的引用覆盖情况。

    主指标只用精确匹配；「前缀且长度差 <= 8」记为 loose 命中（单独计数，不进主指标）。
    """
    kb_titles, graph_titles = _context_titles(context)
    cited = _cited_titles(answer)
    cited_set = set(cited)

    exact = cited_set & kb_titles
    loose: set[str] = set()
    for cited_title in cited_set - kb_titles - graph_titles:
        for kb_title in kb_titles:
            if cited_title != kb_title and (kb_title.startswith(cited_title) or cited_title.startswith(kb_title)):
                if abs(len(kb_title) - len(cited_title)) <= 8:
                    loose.add(cited_title)
                    break
    unknown = cited_set - kb_titles - graph_titles - loose

    return {
        "kb_titles": sorted(kb_titles),
        "graph_titles": sorted(graph_titles),
        "cited_titles": sorted(cited_set),
        "cited_total": len(cited_set),
        "cited_matched": len(exact),
        "cited_loose_matched": len(loose),
        "cited_unknown": len(unknown),
        "citation_coverage": (len(exact) / len(kb_titles)) if kb_titles else None,
        "citation_block_present": bool(cited),
    }


def _negative_queries(
    queries_path: Path,
    qrels_path: Path,
    doc_ids: set[str],
    exclude_qids: set[str],
    n: int,
    rng: random.Random,
) -> list[tuple[str, str]]:
    """筛选「相关文档全部不在入库池内」的 query，作为应拒答样本。"""
    queries = pd.read_parquet(queries_path)
    qrels = pd.read_parquet(qrels_path)
    queries["_id"] = queries["_id"].astype(str)
    qrels["query-id"] = qrels["query-id"].astype(str)
    qrels["corpus-id"] = qrels["corpus-id"].astype(str)

    relevant: dict[str, list[str]] = {}
    for qid, cid in zip(qrels["query-id"], qrels["corpus-id"]):
        relevant.setdefault(qid, []).append(cid)

    qtext = dict(zip(queries["_id"], queries["text"].astype(str)))
    candidates = sorted(
        qid for qid, docs in relevant.items()
        if docs and not (set(docs) & doc_ids) and qid not in exclude_qids and qtext.get(qid, "").strip()
    )
    picked = rng.sample(candidates, min(n, len(candidates)))
    return [(qid, qtext[qid].strip()) for qid in picked]


class NullWebSearchClient:
    """空外部搜索：避免 answerable=False 触发联网兜底，污染拒答判定。"""

    async def search(self, query: str, max_results: int = 5):  # noqa: ARG002
        return []


def _prompt_sha() -> str:
    try:
        content = load_prompt("rag_context_prompt")
    except Exception:
        return "unavailable"
    return hashlib.sha1(content.encode("utf-8")).hexdigest()[:8]


def _build_answer_prompt(mode: str, context: str, default_prompt: str) -> str:
    """按口径构造答案 system prompt。

    product：与产品一致——有检索上下文时整段用 rag_context_prompt，否则回落主提示词。
    legacy ：历史脚本口径，仅用于与旧基线对照。
    """
    if mode == "legacy":
        return LEGACY_ANSWER_SYSTEM.format(context=context or "")

    if (context or "").strip():
        prompt = load_prompt("rag_context_prompt").replace("{context}", context)
    else:
        prompt = default_prompt
    if mode == "product_notool":
        prompt += PRODUCT_NOTOOL_SUFFIX
    return prompt


def _judge_scores(j: dict) -> tuple[int, int, str, str]:
    return (
        _as_int(j.get("faithfulness")),
        _as_int(j.get("correctness")),
        str(j.get("faithful_reason") or "")[:200],
        str(j.get("correct_reason") or "")[:200],
    )


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, default=BACKEND / "results" / "eval_retrieval" / "manifest.json")
    ap.add_argument("--corpus", type=Path, default=BACKEND / "data" / "eval" / "duretrieval" / "corpus.parquet")
    ap.add_argument("--queries", type=Path, default=BACKEND / "data" / "eval" / "duretrieval" / "queries.parquet")
    ap.add_argument("--qrels", type=Path, default=BACKEND / "data" / "eval" / "duretrieval" / "qrels.parquet")
    ap.add_argument("--results-dir", type=Path, default=BACKEND / "results" / "eval_end2end")
    ap.add_argument("--n-samples", type=int, default=20)
    ap.add_argument("--n-negatives", type=int, default=20, help="拒答样本数；0 表示不测拒答")
    ap.add_argument("--answer-prompt", choices=["product", "product_notool", "legacy"], default="product")
    ap.add_argument("--allow-web", action="store_true", help="允许外部搜索兜底（默认关闭，否则拒答判定失真）")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--max-evidence-chars", type=int, default=6000)
    args = ap.parse_args()

    m = json.loads(args.manifest.read_text(encoding="utf-8"))
    user_id = m["user_id"]
    doc_ids = set(m["doc_ids"])

    corpus = pd.read_parquet(args.corpus)
    ctext = dict(zip(corpus["_id"].astype(str), corpus["text"].astype(str)))
    pool = [d for d in ctext if d in doc_ids and ctext[d].strip()]
    rng = random.Random(args.seed)
    sample_docs = rng.sample(pool, min(args.n_samples, len(pool)))

    negatives: list[tuple[str, str]] = []
    if args.n_negatives > 0:
        for path in (args.queries, args.qrels):
            if not path.exists():
                raise SystemExit(f"[fatal] 缺少拒答样本数据文件：{path}（可用 --n-negatives 0 跳过拒答评测）")
        negatives = _negative_queries(
            args.queries, args.qrels, doc_ids, set(m.get("query_ids") or []), args.n_negatives, rng)

    print(f"[data] user={user_id} positives={len(sample_docs)} negatives={len(negatives)}")

    chat = ChatModelFactory().generator()
    service = AgenticRagService(web_search_client=None if args.allow_web else NullWebSearchClient())
    default_prompt = load_prompt("main_prompt")
    prompt_sha = _prompt_sha()
    model_name = getattr(chat, "model_name", None) or getattr(chat, "model", "unknown")

    if args.warmup:
        await service.run("预热预取 query", user_id)

    rows = []
    skipped = 0
    for i, doc_id in enumerate(sample_docs, 1):
        doc = ctext[doc_id][:2500]
        try:
            gen_raw = await _ask(chat, [HumanMessage(content=GEN_PROMPT.format(doc=doc))])
            qa = _extract_json(gen_raw)
            q = (qa.get("question") or "").strip()
            ref = (qa.get("answer") or "").strip()
        except Exception as e:
            print(f"[{i}] QA 生成失败, skip: {e}")
            skipped += 1
            continue
        if not q or not ref:
            skipped += 1
            continue

        t0 = time.time()
        try:
            result = await service.run(q, user_id)
        except Exception as e:
            print(f"[{i}] AgenticRAG 失败, skip: {e}")
            skipped += 1
            continue
        rag_ms = (time.time() - t0) * 1000.0
        evidence = (result.context or "")[: args.max_evidence_chars]
        ev_count = len(result.evidences)

        system_prompt = _build_answer_prompt(args.answer_prompt, result.context or "", default_prompt)
        answer, first_ms = await _stream_answer(chat, system_prompt, q)
        if not answer.strip():
            answer = answer or "(empty)"

        cite = citation_metrics(answer, result.context or "")

        try:
            jraw = await _ask(chat, [HumanMessage(content=JUDGE_PROMPT.format(
                question=q, ref_answer=ref[:2000], evidence=evidence, answer=answer[:3000]))])
            faithfulness, correctness, f_reason, c_reason = _judge_scores(_extract_json(jraw))
        except Exception as e:
            print(f"[{i}] 裁判失败, set neutral: {e}")
            faithfulness = correctness = 3
            f_reason = c_reason = "judge parse error"

        rows.append({
            "doc_id": doc_id, "question": q, "reference_answer": ref, "answer": answer,
            "evidence_count": ev_count, "faithfulness": faithfulness, "correctness": correctness,
            "faithful_reason": f_reason, "correct_reason": c_reason,
            "rag_ms": round(rag_ms, 1), "answer_first_token_ms": round(first_ms, 1) if first_ms else None,
            **{k: cite[k] for k in (
                "kb_titles", "graph_titles", "cited_titles", "cited_total", "cited_matched",
                "cited_loose_matched", "cited_unknown", "citation_coverage", "citation_block_present")},
        })
        cov = cite["citation_coverage"]
        cov_txt = "-" if cov is None else f"{cov:.2f}"
        print(f"[{i}/{len(sample_docs)}] fa={faithfulness} corr={correctness} ev={ev_count} "
              f"cite={cov_txt} rag={rag_ms:.0f}ms first={(first_ms or 0):.0f}ms")
        await asyncio.sleep(0.3)  # 缓和rate limit

    neg_rows = []
    for i, (qid, q) in enumerate(negatives, 1):
        t0 = time.time()
        try:
            result = await service.run(q, user_id)
        except Exception as e:
            print(f"[neg {i}] AgenticRAG 失败, skip: {e}")
            continue
        rag_ms = (time.time() - t0) * 1000.0
        answer, first_ms = await _stream_answer(
            chat, _build_answer_prompt(args.answer_prompt, result.context or "", default_prompt), q)

        answerable = None if result.answerability is None else result.answerability.answerable
        text_hit = bool(_REFUSAL_RE.search(answer or ""))
        used_web = bool(result.used_web)
        short_circuit = result.answerability is None
        empty = not (answer or "").strip()
        refused_strict = (answerable is False) and text_hit and not used_web
        refused_loose = ((answerable is False) or text_hit) and not used_web

        top1 = None
        for ev in result.evidences:
            if ev.score is not None:
                top1 = float(ev.score)
                break

        category, cat_reason = "unknown", "judge failed"
        try:
            jraw = await _ask(chat, [HumanMessage(content=JUDGE_REFUSAL_PROMPT.format(
                question=q, answer=(answer or "")[:2000]))])
            judge = _extract_json(jraw)
            category = str(judge.get("category") or "unknown").strip().lower()
            if category not in {"refuse", "hedge", "answer"}:
                category = "unknown"
            cat_reason = str(judge.get("reason") or "")[:120]
        except Exception as e:
            print(f"[neg {i}] 拒答裁判失败: {e}")
        if empty:
            category = "empty"

        neg_rows.append({
            "query_id": qid, "question": q, "answer": answer,
            "answerable": answerable, "used_web": used_web, "short_circuit": short_circuit,
            "text_refusal_hit": text_hit, "empty_answer": empty,
            "refusal_category": category, "refusal_reason": cat_reason,
            "refused_strict": refused_strict, "refused_loose": refused_loose,
            "evidence_count": len(result.evidences), "top1_score": top1,
            "rag_ms": round(rag_ms, 1), "answer_first_token_ms": round(first_ms, 1) if first_ms else None,
        })
        print(f"[neg {i}/{len(negatives)}] answerable={answerable} text={text_hit} "
              f"web={used_web} judge={category}")
        await asyncio.sleep(0.3)

    n = max(1, len(rows))
    covered = [r for r in rows if r["kb_titles"]]
    total_cited = sum(r["cited_total"] for r in rows)
    total_unknown = sum(r["cited_unknown"] for r in rows)
    neg_n = len(neg_rows)
    top1_scores = [r["top1_score"] for r in neg_rows if r["top1_score"] is not None]

    agg = {
        "n_samples": len(rows),
        "n_skipped": skipped,
        "faithfulness_mean": round(sum(r["faithfulness"] for r in rows) / n, 3),
        "correctness_mean": round(sum(r["correctness"] for r in rows) / n, 3),
        "faithfulness_rate_4plus": round(sum(1 for r in rows if r["faithfulness"] >= 4) / n, 3),
        "correctness_rate_4plus": round(sum(1 for r in rows if r["correctness"] >= 4) / n, 3),
        "avg_evidence_count": round(sum(r["evidence_count"] for r in rows) / n, 2),
        "mean_rag_ms": round(statistics.mean(r["rag_ms"] for r in rows), 1),
        "mean_answer_first_token_ms": round(statistics.mean(
            r["answer_first_token_ms"] for r in rows if r["answer_first_token_ms"] is not None), 1),
        "estimated_total": round(statistics.mean(
            (r["rag_ms"] + (r["answer_first_token_ms"] or 0)) for r in rows), 1),
        # 引用覆盖率
        "n_rows_no_kb_ctx": len(rows) - len(covered),
        "avg_cited_docs": round(statistics.mean(r["cited_total"] for r in rows), 2) if rows else None,
        "citation_precision": (
            round(1 - total_unknown / total_cited, 3) if total_cited else None),
        "citation_coverage_mean": (
            round(statistics.mean(r["citation_coverage"] for r in covered), 3) if covered else None),
        "citation_full_rate": (
            round(sum(1 for r in covered if r["citation_coverage"] >= 0.999) / len(covered), 3)
            if covered else None),
        "citation_unknown_rate": round(total_unknown / total_cited, 3) if total_cited else None,
        "citation_block_missing_rate": (
            round(sum(1 for r in covered if not r["citation_block_present"]) / len(covered), 3)
            if covered else None),
        # 拒答（主口径：LLM 分类裁判；regex 作为交叉校验）
        "n_negatives": neg_n,
        "refusal_accuracy": (
            round(sum(1 for r in neg_rows if r["refusal_category"] == "refuse") / neg_n, 3)
            if neg_n else None),
        "refusal_hedge_rate": (
            round(sum(1 for r in neg_rows if r["refusal_category"] == "hedge") / neg_n, 3)
            if neg_n else None),
        "unsupported_answer_rate": (
            round(sum(1 for r in neg_rows if r["refusal_category"] == "answer") / neg_n, 3)
            if neg_n else None),
        "refusal_accuracy_regex": (
            round(sum(1 for r in neg_rows if r["refused_strict"]) / neg_n, 3) if neg_n else None),
        "refusal_accuracy_loose": (
            round(sum(1 for r in neg_rows if r["refused_loose"]) / neg_n, 3) if neg_n else None),
        "used_web_rate": round(sum(1 for r in neg_rows if r["used_web"]) / neg_n, 3) if neg_n else 0.0,
        "refusal_short_circuit_n": sum(1 for r in neg_rows if r["short_circuit"]),
        "empty_answer_n": sum(1 for r in neg_rows if r["empty_answer"]),
        "mean_neg_top1_score": round(statistics.mean(top1_scores), 4) if top1_scores else None,
        # 口径元数据
        "eval_schema": 2,
        "answer_prompt": args.answer_prompt,
        "prompt_sha": prompt_sha,
        "generator_model": model_name,
        "judge_model": model_name,
        "max_evidence_chars": args.max_evidence_chars,
        "web_allowed": bool(args.allow_web),
        "seed": args.seed,
        "n_requested": args.n_samples,
        "relevant_ratio": m.get("relevant_ratio"),
    }

    args.results_dir.mkdir(parents=True, exist_ok=True)
    (args.results_dir / "report.json").write_text(
        json.dumps({"summary": agg, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    if neg_rows:
        (args.results_dir / "report_negatives.json").write_text(
            json.dumps({"summary": {
                "n_negatives": neg_n,
                "refusal_accuracy": agg["refusal_accuracy"],
                "refusal_hedge_rate": agg["refusal_hedge_rate"],
                "unsupported_answer_rate": agg["unsupported_answer_rate"],
                "refusal_accuracy_regex": agg["refusal_accuracy_regex"],
                "refusal_accuracy_loose": agg["refusal_accuracy_loose"],
                "used_web_rate": agg["used_web_rate"],
                "answer_prompt": args.answer_prompt,
                "judge_model": model_name,
            }, "rows": neg_rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    def _fmt(v, nd=3):
        return "-" if v is None else f"{v:.{nd}f}"

    md = ["# Agentic RAG 端到端评测报告", ""]
    md.append(f"- 评测用户：`{user_id}`　正样本：{len(rows)}（跳过 {skipped}）　负样本：{neg_n}")
    md.append(f"- 口径：answer_prompt=`{args.answer_prompt}`（prompt_sha `{prompt_sha}`）／"
              f"web_allowed=`{args.allow_web}`／max_evidence_chars={args.max_evidence_chars}")
    md.append("- 正样本题目从源正文自动生成，检索必然命中 → 衡量**答案质量**，不衡量检索难度")
    md.append("- 负样本取「相关文档全不在入库池内」的 query → 拒答准确率只作**上界**口径")
    md.append("")
    md.append("## 答案质量")
    md.append("")
    md.append("| 指标 | 值 |")
    md.append("|---|---|")
    md.append(f"| 忠实度(1-5) | {agg['faithfulness_mean']:.2f} |")
    md.append(f"| 正确性(1-5) | {agg['correctness_mean']:.2f} |")
    md.append(f"| 忠实度≥4 占比 | {agg['faithfulness_rate_4plus']:.3f} |")
    md.append(f"| 正确性≥4 占比 | {agg['correctness_rate_4plus']:.3f} |")
    md.append(f"| 平均证据条数 | {agg['avg_evidence_count']:.2f} |")
    md.append("")
    md.append("## 引用（来源标注）")
    md.append("")
    md.append("| 指标 | 值 |")
    md.append("|---|---|")
    md.append(f"| 每条回答平均引用文档数 | {_fmt(agg['avg_cited_docs'], 2)} |")
    md.append(f"| 引用准确率（引用的标题确实在上下文中） | {_fmt(agg['citation_precision'])} |")
    md.append(f"| 引用了上下文外标题（编造引用）占比 | {_fmt(agg['citation_unknown_rate'])} |")
    md.append(f"| 有证据但完全没有来源块的样本占比 | {_fmt(agg['citation_block_missing_rate'])} |")
    md.append(f"| 引用广度（诊断项：上下文文档被引用的比例） | {_fmt(agg['citation_coverage_mean'])} |")
    md.append(f"| 无知识库证据的样本数 | {agg['n_rows_no_kb_ctx']} |")
    md.append("")
    md.append("> 引用广度是**诊断项**，不是质量指标：回答只需要用到 1–2 篇文档时，不该把上下文中未使用的文档也列出。")
    md.append("> 判据应看「无编造引用」与「有来源块率」。")
    md.append("")
    md.append("## 拒答能力")
    md.append("")
    md.append("| 指标 | 值 |")
    md.append("|---|---|")
    md.append(f"| 拒答准确率（LLM 裁判判为「明确拒答」） | {_fmt(agg['refusal_accuracy'])} |")
    md.append(f"| 承认资料不足但有限作答（hedge） | {_fmt(agg['refusal_hedge_rate'])} |")
    md.append(f"| 无依据硬答（unsupported，真正风险项） | {_fmt(agg['unsupported_answer_rate'])} |")
    md.append(f"| 交叉校验：正则判定拒答率 | {_fmt(agg['refusal_accuracy_regex'])} |")
    md.append(f"| 交叉校验：正则宽松口径 | {_fmt(agg['refusal_accuracy_loose'])} |")
    md.append(f"| 外部搜索触发率（应为 0） | {_fmt(agg['used_web_rate'])} |")
    md.append(f"| 规划短路（未检索）样本数 | {agg['refusal_short_circuit_n']} |")
    md.append(f"| 空回答数 | {agg['empty_answer_n']} |")
    md.append("")
    md.append("> hedge 是「承认资料不足、但仍给出有保留的说明」，通常不视为失败；真正风险项是 `unsupported`。")
    md.append("> RRF 分是排名分（首位恒为 1/61），不代表相关性，故不作为置信度指标。")
    md.append("")
    md.append("## 延迟（思考/检索 → 首token）")
    md.append("")
    md.append("| 项 | 值(ms) |")
    md.append("|---|---|")
    md.append(f"| 管线(规划+检索+可答性) | {agg['mean_rag_ms']:.1f} |")
    md.append(f"| 回答首token | {agg['mean_answer_first_token_ms']:.1f} |")
    md.append(f"| 用户感知首token ≈ | {agg['estimated_total']:.1f} |")
    md.append("")
    md.append("> 与 `legacy` 口径不可比：两者提示词、引用要求、web 开关均不同，只能与同口径的同版本对比。")
    (args.results_dir / "report.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(agg, ensure_ascii=False, indent=2))
    print(f"[done] -> {args.results_dir / 'report.json'} / report.md")
    if agg["used_web_rate"]:
        print("[warn] 负样本触发了外部搜索，拒答判定已失真，请检查是否漏关 web 兜底")


if __name__ == "__main__":
    asyncio.run(main())
