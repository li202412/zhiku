# 项目现状

> **用途**：用一页说清「现在能做什么、做到什么程度、证据是什么」，供开发、评审与对外说明时直接引用。
> **最后更新**：2026-11-19　｜　**维护约定**：能力变化时只改「能力状态总览」对应行 + 文末「更新记录」加一行；数字必须来自实跑，不得估算。

## 一、定位

**中文文档知识库问答（Agentic RAG）**：上传文档 → 解析切分 → 混合检索 → 证据评估 → 带来源作答。

- 不绑定行业：制度、手册、规范、课件、说明文档均可。
- 核心承诺：**只用检索到的资料作答**，资料不足时明确拒答，而不是凭常识编造。
- 引用形态：正文用引用块行 `> 来源：知识库《文档名》`，正文不出现 `[1]` 编号。

## 二、能力状态总览

| 能力 | 状态 | 当前程度 / 证据 |
| --- | --- | --- |
| 文档入库 | 可用 | 5 种格式（`.pdf/.docx/.md/.txt/.pptx`），单文件 ≤20MB、批量 ≤200MB，切分 1000/20 |
| 混合检索 | 可用 | 向量 top2k ∪ 全文，RRF `1/(60+rank)` 融合；检索 p95 326ms |
| 图谱证据 | 可用 | 实体抽取 + 提及 chunk 补充；Neo4j 不可用时逐条降级为空 |
| Agentic 编排 | 可用 | 规划 → 检索 → 可答性评估 → 证据融合 → 作答，SSE 流式 |
| 来源引用 | 可用 | 引用块行标注文档名；无资料不写来源块并说明 |
| 证据筛选 | 可用 | 可答性评估标出真正相关的证据编号，只送 1–2 条进上下文（默认开启，`EVIDENCE_GATE_ENABLED` 可关） |
| 强制检索开关 | 可用 | `PLANNER_REQUIRE_RETRIEVAL` 禁止规划器「无需检索」短路，避免绕过知识库作答（默认关闭） |
| 多轮会话 | 可用 | 会话列表、单会话读取/删除、历史消息 |
| 前端界面 | 可用 | 8 个页面：问答、知识库、会话、设置、个人、关于、登录、注册 |
| 一键部署 | 暂缓 | `docker compose` 与脚本已提供，但全栈构建未在开发机验证（基础镜像拉取受限）；当前以本地方式（uvicorn + Vite）为准 |
| 检索质量评测 | 部分可用 | 已有基线（见第五节），但正例密度偏高、仅可做版本对比 |
| 答案质量评测 | 可用 | faithfulness 4.97 / correctness 4.97（30 样本，95% CI ±0.07，裁判与主模型同源） |
| 拒答能力评测 | 可用 | 20 条负样本：明确拒答 80%、谨慎作答 20%、无依据硬答 0% |
| 引用覆盖率 | 可用 | 来源块覆盖率 100%；引用准确率 97.5%（无编造引用为主） |
| 权限控制 | 部分可用 | 仅 `user_id` 级隔离，无文档级私有/共享 |
| 可观测性 | 部分可用 | 结构化日志 + 分阶段耗时；无 `trace_id`、无成本台账 |
| Rerank | 部分可用 | 服务已实现并接在 `/chat/reorder`，**未接入问答主链路** |
| CI 质量门禁 | 可用 | GitHub Actions：ruff + pytest + 前端构建 + compose 语法校验 |
| 安全回归 | 未完成 | 有只读工具、限流、JWT；无注入用例、无审计 |

## 三、功能清单

### 3.1 后端接口（6 组路由）

| 路由组 | 前缀 | 端点 |
| --- | --- | --- |
| 问答 | `/chat` | `POST /agent/query/stream`（SSE）、`GET|DELETE /session/{session_id}`、`GET /sessions`、`GET /sessions/{user_id}`、`POST /reorder` |
| 知识库 | `/knowledge` | `add/single`、`add/multiple`、`add/multiple/stream`、`clean`、`md5/clear`、`md5/delete/{md5}`、`delete/filename`、`md5/list`、`md5/{md5}`、`list`、`detail`、`chunks`、`image/{md5}/{filename}`、`images/all/{md5}` |
| 用户 | `/user` | `login`、`register`、`reset-password`、`refresh-token`、`detail`、`update`、`logout` |
| 文件 | `/file` | `upload` |
| 健康检查 | `/health` | `live`、`ready`，另有 `GET /health` 返回 `{"message":"The service is healthy"}` |
| 图谱 | `/api/graph` | `overview`、`entity/{id}`、`entity/{id}/neighbors`、`entity/{id}/documents`、`docs/{doc_id}/related`、`search`、`extract-logs`、实体/类型/关系的增删改查与合并、`docs/{doc_id}/re-extract`、`events`（SSE） |

### 3.2 前端

| 页面 | 路径 | 作用 |
| --- | --- | --- |
| AIChat | `/chat`、`/chat/:sessionId` | 问答主界面，SSE 流式渲染思考与回答 |
| KnowledgeBase | `/knowledge` | 文档上传、列表、分片查看、删除、清空 |
| Sessions | `/sessions` | 会话历史 |
| Settings | `/settings` | 主题、语言等偏好 |
| Profile | `/profile` | 个人信息 |
| AboutUs | `/about` | 项目说明 |
| Login / Register | `/login`、`/register` | 认证 |

模块划分：`api/`（auth、chat、knowledge、sessions、client、endpoints）、`hooks/`（useSSE、useDebounce）、`stores/`（user、session、theme、language）。

## 四、架构与数据流

### 4.1 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | FastAPI + LangChain / LangGraph + SQLAlchemy |
| 存储 | MySQL（用户/会话/图谱元数据）、Redis（缓存/限流）、Neo4j（chunk + 实体 + 关系） |
| 前端 | React + Vite + TypeScript + Tailwind |
| 部署 | Docker Compose（MySQL/Redis/Neo4j/前后端）或本地 uv + npm |

### 4.2 问答请求流

```
前端 POST /chat/agent/query/stream
  → 规划（Planner）判定是否需要检索、走哪些工具
  → 并行：文本检索（向量 ∪ 全文 → RRF）｜ 图谱检索（实体 → 提及 chunk）
  → 可答性评估（证据是否足以作答；不足则可拒答或转外部搜索）
  → 证据融合（上下文上限 6000 字符）
  → 模型作答 → SSE 帧：thinking / response / done / error
```

### 4.3 入库流

```
上传 → 按扩展名解析（pdf/docx/md/txt/pptx）→ 切分(1000/20)
  → 向量化 → 写 Neo4j（Doc、Chunk、Entity、关系）
  → 异步图谱抽取任务（带 run_token，防旧任务覆盖新结果）
```

### 4.4 容错约定

- embedding 失败 → 退化纯全文检索。
- Neo4j 不可用 → 图谱证据为空，上传/检索/问答主流程不受影响。
- 可答性评估判定证据不足 → 明确拒答并说明。
- 目录清理用 `_safe_remove_tree`，同时吞 `Exception` 与 `SystemExit`。

## 五、质量与评测

### 5.1 质量基线（2026-11-19 实跑）

| 项 | 结果 |
| --- | --- |
| 后端单测 | 637 passed / 24 skipped（`pytest -q --basetemp=.pytest_basetemp`） |
| 静态检查 | `ruff check .` 零告警 |
| 前端构建 | `npm run build` 通过（15.8s） |
| CI | GitHub Actions 三个 job：backend（ruff + pytest）／frontend（build）／compose（配置校验） |

> 注意：直接跑 `pytest -q` 时退出码可能非 0（环境拦截清理临时目录），以输出中的 `passed` 为准，详见 `PROBLEMS.md` T3。

### 5.2 评测基线

数据集：DuRetrieval（ModelScope `mteb/duretrieval` dev 分片）——中文开放域事实问答检索。
抽样：`--n-query 100 --n-doc 2000 --seed 42`，入库 2000 文档 / 2140 chunk，**正例密度 23.1%**。

| 类别 | 指标 | 值 |
| --- | --- | --- |
| 检索 | Recall@1 / @3 / @5 / @10 | 0.3124 / 0.6601 / 0.8563 / 0.9693 |
| 检索 | Hit@1 / Hit@5 / Hit@10 | 0.98 / 1.0 / 1.0 |
| 检索 | MRR / nDCG@10 / MAP | 0.987 / 0.9607 / 0.9222 |
| 检索延迟 | p50 / p90 / p95 | 249 / 295 / 326 ms |
| 答案 | faithfulness / correctness（5 分制，30 样本） | 4.97 / 4.97（95% CI ±0.07） |
| 答案 | ≥4 分占比 | 100% / 100% |
| 引用 | 来源块覆盖率 / 引用准确率（无编造） | 100% / 97.5% |
| 引用 | 每条回答平均引用文档数 | 1.33 篇（上下文平均 1.40 篇） |
| 拒答 | 明确拒答 / 谨慎作答 / 无依据硬答（20 条负样本） | 75% / 25% / **0%** |
| 链路延迟 | RAG 均值 / 首 token / 总耗时 | 2172 / 474 / 2647 ms |

**口径限定（引用数字时必须一并说明）**：
1. 检索侧正例密度 23.1%，远高于真实检索库，Recall/MRR 偏高，**仅可用于版本间对比**；裸 Recall 还受「每条 query 的正解数」约束，应同时看结构上限达成率（recall@5 达成率 95.2%）。
2. 端到端题目由语料正文生成，检索必然命中，衡量答案质量而非检索难度；**答案质量只统计「源文档确实被召回」的样本**，未命中的单列为检索失败。
3. 拒答负样本取「qrels 相关文档全不在入库池内」的 query，qrels 无相关 ≠ 全库绝对无关，故拒答率只作**上界**。
4. 裁判与主模型同为 `deepseek-chat`（prompt_sha `f580d345`），分数偏乐观，且 30 样本的置信区间约 ±0.07，**小于 0.1 的差异不要当结论**。
5. 答案提示词已切为产品同款 `rag_context_prompt`，与旧口径（`legacy/`）不可比。

### 5.2.1 证据筛选 A/B（2026-11-19）

在同样 26 条「源文档被召回」的样本上做配对比较：

| 配置 | 证据条数 | faithfulness | 逐条变化 | 引用广度 | 首 token |
| --- | --- | --- | --- | --- | --- |
| 关闭 | 5.00 | 4.846 | — | 0.33 | 648 ms |
| **开启（现默认）** | **1.46** | **4.967** | 3 改善 / 23 持平 / **0 变差** | **0.95** | **474 ms** |

结论：证据量减少约 **71%**，无一条样本退化，引用广度与首 token 同时改善；但 faithfulness 的 +0.12 仍在置信区间内，**只能说「未变差」，不能说「显著变好」**。评估未给出编号时会自动不裁剪，属安全兜底。

### 5.3 复现方式

```bash
cd backend
uv run python scripts/eval_ingest.py --n-query 100 --n-doc 2000 --clean
uv run python scripts/eval_recall.py --limit 10
uv run python scripts/eval_end2end.py --n-samples 30 --n-negatives 20 --max-evidence-chars 6000
# 旧口径对照（仅供参考，不与上表可比）
uv run python scripts/eval_end2end.py --n-samples 30 --answer-prompt legacy --results-dir results/eval_end2end/legacy
```

产物：`backend/results/eval_retrieval/{manifest,report}.json`、`report.md`；`backend/results/eval_end2end/{report.json, report.md, report_negatives.json}`、`legacy/report.json`。
数据集缓存在 `backend/data/eval/duretrieval/`（`data/` 已被 `.gitignore` 忽略，换机器需重下）。

## 六、运行与部署

| 方式 | 命令 | 端口 |
| --- | --- | --- |
| Docker | `start.bat` 或 `docker compose up -d --build` | 前端 3000 / 后端 8000 |
| 本地 | `uv run uvicorn main:app --reload --port 8000` + `npm run dev` | 同上 |

- 默认演示账号 `admin` / `admin1234`（`SEED_DEMO_USER=true` 首启创建；**公网必须设为 `false`**）。
- 本地开发需先建库：`CREATE DATABASE zhiku CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;`
- 启动脚本为 UTF-8 + CRLF，勿转 GBK。
- Docker 镜像首次构建预计 10 分钟以上（含 torch/unstructured），**当前未验证，暂缓**；本地方式已验证可用。

## 七、版本与仓库

| 项 | 状态 |
| --- | --- |
| 分支 / HEAD | `main` / `b3d2cf6`（README 标准结构重写） |
| 远端 | GitHub 公开仓库，MIT |
| 提交历史 | 初始化 → 评测命令参数修正 → README 重写 |
| 工作区 | 存在未提交改动：评测依赖与脚本、`backend/results/`、定位文案调整、文档重组 |

## 八、已知限制

| 项 | 说明 |
| --- | --- |
| 格式 | 支持 `.docx`，不支持旧版 `.doc`；扫描版 PDF 无 OCR，空文本文档会被跳过 |
| 权限 | 仅 `user_id` 级隔离，无文档级私有/共享；检索为同步调用、单实例 |
| 可观测 | 无 `trace_id`、无成本台账、无 p50/p95 看板 |
| 成本核算 | 仅主模型 token 有记录，规划/评估/嵌入未记账 |
| Rerank | 未接主链路，无对照数据 |
| 安全 | 无提示词注入用例、无审计日志 |
| 部署 | Docker 全栈未验证，2026-11-19 决定暂缓；本地方式已验证可用 |
| 评测 | 拒答准确率、引用覆盖率无数据源 |

## 更新记录

| 日期 | 变更 |
| --- | --- |
| 2026-11-18 | 建立现状文档：功能清单、架构、测试基线 |
| 2026-11-19 | 定位由「企业制度」调整为「中文文档知识库」；新增评测基线（检索 + 端到端）；新增能力状态总览与口径限定 |
| 2026-11-19 | 评测收口：答案提示词切为产品同款，新增引用覆盖率与拒答准确率（20 条负样本）；新增 CI 门禁；能力表更新 3 项 |
| 2026-11-19 | 部署项改为「暂缓」：Docker 全栈构建因基础镜像拉取受限未完成，决定暂不作为当前目标，本地方式为准 |
| 2026-11-19 | 测量修正 + 证据筛选：答案质量改为只统计「源文档被召回」的样本、补 95% 置信区间、检索报告加结构上限达成率；新增证据筛选（默认开启）与强制检索开关，配对 A/B 见 §5.2.1 |
