# 运维与评测脚本

一次性维护脚本与评测脚本放在这里。**所有脚本默认使用 `.env` 中的数据库/Neo4j 连接，请在 `backend/` 目录下执行**；涉及写入或删除的脚本，先在测试库上跑一遍。

## 评测（回答「效果好多少」）

| 脚本 | 作用 | 典型命令 |
| --- | --- | --- |
| `eval_ingest.py` | 注入 DuRetrieval 子集（query + 相关文档 + qrels）到 Neo4j，落 manifest | `uv run python scripts/eval_ingest.py --n-query 50 --n-doc 200` |
| `eval_recall.py` | 检索指标：Recall@k / Hit@k / MRR / nDCG@10，附单查询延迟 | `uv run python scripts/eval_recall.py --limit 10` |
| `eval_end2end.py` | 端到端：LLM 合成题 → 跑真实管线 → LLM 裁判 faithfulness/correctness，并统计引用来源块覆盖率与拒答准确率 | `uv run python scripts/eval_end2end.py --n-samples 30 --n-negatives 20 --max-evidence-chars 6000` |
| `eval_build_graph.py` | 给评测语料补建知识图谱（实体/关系/提及），用于图谱增量实验 | `uv run python scripts/eval_build_graph.py --n-docs 50` |
| `eval_graph_entity_recall.py` | 实体级检索评测：命中实体 / 关联来源召回 | `uv run python scripts/eval_graph_entity_recall.py --n 40 --top-k 10` |
| `eval_graph_entity_recall_v2.py` | 同上 v2：用「关系线索题」测真实召回（答案实体不出现在问题里） | `uv run python scripts/eval_graph_entity_recall_v2.py --n 40` |
| `eval_graph_multihop.py` | 多跳问答：GraphRAG 相比纯文本检索的增量 | `uv run python scripts/eval_graph_multihop.py --n 20` |

> 评测会把结果写到 `results/`；基线结果建议提交到仓库（`evals/results/<sha>.json`）便于对比。

### `eval_end2end.py` 的关键参数

| 参数 | 作用 |
| --- | --- |
| `--n-samples` | 正样本数量（从语料正文自动生成题目） |
| `--n-negatives` | 拒答样本数量：从 qrels 筛「相关文档全不在入库池内」的 query；`0` 表示不测 |
| `--answer-prompt` | `product`（默认，与产品一致）/ `product_notool` / `legacy`（旧口径，仅作对照，与 product 不可比） |
| `--max-evidence-chars` | 给裁判看的证据长度，建议 6000（等于产品上下文上限） |
| `--allow-web` | 允许外部搜索兜底；**默认关闭**，否则 answerable=False 会触发搜网、拒答判定失真 |

指标口径：
- **引用**：主指标是「来源块覆盖率」与「引用准确率（无编造引用）」；「文档级覆盖广度」只是诊断项——回答只用到 1–2 篇文档时，不该把上下文中未使用的文档也列出。
- **拒答**：用「结构化 answerable + LLM 分类裁判」判定，输出 `refuse / hedge / answer` 三类。`hedge`（承认资料不足但给出有保留的说明）通常不算失败，真正的风险项是 `answer`（无依据硬答）。

## 图谱维护（回答「数据不对劲怎么办」）

| 脚本 | 作用 | 是否破坏性 |
| --- | --- | --- |
| `check_graph_data.py` | 只读校验：统计 Doc/Chunk/Entity/Relation/日志 数量 | 否 |
| `backfill_graph.py` | 把已有文档回填进 Neo4j 图谱（内容哈希幂等，可重复执行） | 否（`--wipe` 会清空） |
| `backfill_entity_types.py` | 给未分类实体重抽以补 `type_id` | 否（覆盖旧关系） |
| `fix_graph_docs.py` | 补齐回填遗漏的 `graph_docs` 注册行（幂等） | 否 |
| `cleanup_orphan_entities.py` | 清理零度孤立实体 | **是**（删节点） |

## 约定

- 脚本统一 `argparse`，`--help` 可查参数。
- 写操作脚本中，`backfill_graph.py`、`backfill_entity_types.py`、`cleanup_orphan_entities.py` 提供 `--dry-run`；`fix_graph_docs.py` 是幂等补齐，可重复执行。
- 大批量建图用 `eval_build_graph.py --concurrency` 限制并发，避免触发模型侧限流。
- 不要在脚本里硬编码用户 id / 密钥，从环境变量或参数读取。
