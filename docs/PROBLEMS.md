# 问题与解决方法

> **用途**：记录开发、评测、运维中真实遇到过的问题与修复方式，供后续维护者快速定位与避免重犯。
> **最后更新**：2026-11-19　｜　**维护约定**：新问题按「现象 → 根因 → 解决 → 验证」追加到对应分类末尾，并在文末「更新记录」加一行；带 `🔒` 的条目已有回归测试把关，**不要回退**。

## 快速索引

| # | 一句话 | 分类 | 状态 |
| --- | --- | --- | --- |
| B1 | 小文件上传报「切分内容为空」 | 开发缺陷 | 已修复 🔒 |
| B2 | `.md` 首传耗时约 6 分钟 | 开发缺陷 | 已修复 🔒 |
| B3 | `.docx` 上传必定失败 | 开发缺陷 | 已修复 🔒 |
| B4 | 回答开头漏出 `{"answerable":...}` | 开发缺陷 | 已修复 🔒 |
| B5 | 回答完全不引用资料 | 开发缺陷 | 已修复 🔒 |
| B6 | 清空知识库把服务进程带崩 | 开发缺陷 | 已修复 🔒 |
| B7 | 前端构建类型错误 | 开发缺陷 | 已修复 |
| B8 | 检索工具语义被改坏 | 开发缺陷 | 已回滚 |
| T1 | `uv add --dev` 建同名 `dev` 组 | 工具链 | 已规避 |
| T2 | 模型通道与模型名不匹配 | 工具链 | 已规避 |
| T3 | `pytest` 退出码被环境污染 | 工具链 | 已规避 |
| T4 | 端口冲突与进程被回收 | 工具链 | 已知 |
| T5 | `.bat` 文件编码 | 工具链 | 已规避 |
| T6 | `gitignore` 不撤销已跟踪文件 | 工具链 | 已规避 |
| T7 | Neo4j 版本与向量索引 API 变更 | 工具链 | 已知 |
| T8 | 构建拉不到基础镜像（Docker Hub 超时） | 工具链 | 已记录（部署暂缓） |
| E1 | 评测基线虚高（正例密度过高） | 评测 | 已加护栏 |
| E2 | 评测脚本依赖缺失，跑不起来 | 评测 | 已修复 |
| E3 | 端到端题目自动合成，证明不了拒答 | 评测 | 已缓解（补负样本） |
| E4 | 裁判与主模型同源 | 评测 | 待改造 |
| E5 | `recall@1` 与 `hit@1` 看似矛盾 | 评测 | 已澄清 |
| E6 | 用正则穷举拒答措辞 | 评测 | 已改为 LLM 裁判 |
| E7 | 文档级引用覆盖率是错误指标 | 评测 | 已修正 |
| E8 | 正样本混入检索失败，拉低答案分 | 评测 | 已修复 |
| E9 | 规划器短路绕过知识库（13%） | 评测 | 已有开关 |
| E10 | `recall@k` 有结构上限 | 评测 | 已补达成率 |

---

## 一、开发缺陷（改动前先读）

### B1 小文件（md/txt）上传报「切分内容为空」 🔒

- **现象**：上传小文件后解析结果为 0 个 chunk。
- **根因**：`NamedTemporaryFile` 写入后未关闭，缓冲区没落盘，后续读取拿到空文件。
- **解决**：`app/rag/document_handler/processor.py` 写完立即 `close()`，并新增 `_write_bytes()` 显式落盘；同时补加载/切分/入库三段耗时日志。
- **验证**：上传相关测试；新增分阶段耗时日志可直接观察三段耗时。

### B2 `.md` 首次上传耗时约 6 分钟 🔒

- **现象**：`.md` 首次入库极慢，后续正常。
- **根因**：`UnstructuredMarkdownLoader` 首次初始化 nltk/模型开销巨大。
- **解决**：`app/utils/file_handler.py` 改纯文本直读快路径，失败再回落 unstructured；同步版一并修。
- **验证**：文件解析测试 + 手工上传计时。

### B3 `.docx` 上传必定失败 🔒

- **现象**：上传 docx 直接报解析错误。
- **根因**：用 `TextLoader` 读二进制文件。
- **解决**：改 `python-docx` 解析段落 + 表格；`pyproject.toml` 加依赖并 `uv lock`。

### B4 回答开头漏出 `{"answerable":...}` 🔒

- **现象**：回答正文第一段是内部 JSON 片段。
- **根因**：工具内部嵌套 LLM 的 token 被外层 `astream_events` 当作回答转发。
- **解决**：`app/agent/agent.py` 只转发 `metadata.langgraph_node == "model"` 的事件。
- **验证**：`test_get_agent_stream_response_ignores_nested_tool_model_tokens`。

### B5 回答完全不引用资料 🔒

- **现象**：检索明明有结果，回答却完全不提来源。
- **根因**：重写上下文提示词时误删了 `{context}` 占位符，检索结果没进提示词。
- **解决**：`backend/app/prompt/rag_context_prompt.md` 恢复占位符。
- **验证**：`test_get_agent_stream_response_with_rag_context`。**改这个文件前先确认占位符还在。**

### B6 清空知识库把服务进程带崩 🔒

- **现象**：点「清空知识库」后 uvicorn 进程退出。
- **根因**：`shutil.rmtree` 被运行环境拦截，抛的是 `SystemExit(1)` 而不是 `Exception`，常规 `except Exception` 接不住。
- **解决**：`app/utils/image_extractor.py` 新增 `_safe_remove_tree()`，同时吞 `Exception` 与 `SystemExit`；`delete_image_directory` / `delete_user_all_images` 改用它。
- **验证**：`tests/test_image_extractor.py`。

### B7 前端构建类型错误

- **现象**：`npm run build` 报 TS 类型错误。
- **根因**：`tsconfig.app.json` 的 paths 配置、`useSSE.ts` 的 `onInterrupt` 类型、Tailwind 配置文件后缀。
- **解决**：修 `tsconfig.app.json`；清理 `useSSE.ts` 回调类型；`tailwind.config.cjs → .js`。

### B8 检索工具语义被改坏（已回滚）

- **现象**：混合检索结果变差，图谱证据消失。
- **根因**：把 `hybrid_search` 收窄成 `kinds=["doc"]`，并让未知工具兜底到文档检索。
- **解决**：回滚为原语义——`search_knowledge_base → kinds=["doc"]`、`search_graph → 实体+提及`、`hybrid_search → kinds=None`、其余工具本地跳过。
- **教训**：语义是设计的一部分，别以「顺手优化」为名收窄范围。

---

## 二、工具链与环境

### T1 `uv add --dev` 会建同名 `dev` 组，导致依赖装不上

- **现象**：`uv add --dev pyarrow` 后 `uv sync --extra dev` 装不到它。
- **根因**：`uv` 写进 `[dependency-groups] dev`，而项目文档用的是 `[project.optional-dependencies] dev`（`--extra dev` 才认）。两套同名 `dev` 语义分裂。
- **解决**：手动合并进 `[project.optional-dependencies] dev`，删掉 `[dependency-groups] dev`，再 `uv lock && uv sync --extra dev`。
- **注意**：`uv sync` 会修剪不在当前组里的包，本次卸掉了 `en-core-web-sm==3.8.0`（spaCy 模型）。`app/`、`tests/` 未直接引用、spaCy 本体仍在；若走 `unstructured` 回落路径报缺模型，`uv pip install en-core-web-sm`。

### T2 模型通道与模型名必须配对

- **通道解析顺序**：`PLANNER_*`（url+key 成对才生效）→ `EMBED_*` → `OPENAI_*`。
- **典型错误**：只换模型名不换通道。未配 `PLANNER_BASE_URL/KEY` 时会走 `EMBED_*` 通道，模型名必须是该通道可用的 ID，否则每次问答先 404/429 再回落，白等 3~8 秒。
- **规避**：换模型时确认「通道 + 模型名」成对修改；用 `PLANNER_ENABLED=false` 可走确定性单路检索，省 1~3 秒。

### T3 `pytest` 退出码被环境污染

- **现象**：`637 passed` 但退出码非 0，日志尾部是 `_safe_shutil_rmtree → SystemExit: 1`。
- **根因**：环境拦截批量删除，pytest 收尾清理临时目录时被中断。
- **解决**：`uv run pytest -q --basetemp=.pytest_basetemp`，把临时目录指到仓内（已进 `.gitignore`）。**看到 `passed` 就别把退出码当测试失败。**

### T4 端口冲突与进程回收

- 开发进程会随会话结束被回收，接续开发前先探端口：`netstat -ano | findstr :8000`（Windows）或 `lsof -i :8000`。
- 需要并行跑多套环境时错开端口（后端 8000/8011、前端 3000/3100）。

### T5 `.bat` 文件编码

- `start.bat` / `stop.bat` 必须是 **UTF-8 + CRLF + `chcp 65001`**；转回 GBK 会中文乱码。`.gitattributes` 已固定 `.bat` 保留 CRLF 字节。

### T6 `gitignore` 不会撤销已跟踪文件

- **教训**：私有文档一旦提交进历史，之后加 `.gitignore` 也删除不掉历史里的内容。
- **纪律**：提交前确认 `git status` 里没有 `.env`、`docs/private/`、缓存目录；如已误提交且无远端，可删 `.git` 重建干净历史。

### T7 Neo4j 版本与向量索引 API 变更

- 实测版本 **2026.09.0**，`db.index.vector.queryNodes` 已标记 **deprecated**，提示改用 `SEARCH`。
- **影响**：`queryNodes` 的 `WHERE` 是取回 k 个候选**之后**才过滤，候选被大量滤掉会导致召回塌陷；需要真前置过滤时用 `SEARCH`，否则只能放大 `pool` 补偿。
- **注意版本差异**：本地开发库是 2026.09，而 `docker-compose.yml` 用的是 `neo4j:5.26-community`。涉及 `SEARCH` 语法的改动必须在两个版本上分别验证。

### T8 构建时拉不到基础镜像（`registry-1.docker.io` 超时）

- **现象**：`docker compose build` 秒失败，报 `dialing registry-1.docker.io:443 container via direct connection ... failed to respond`（DNS 解析到 IPv6 后不可达）。
- **根因**：Docker Hub 在国内网络不可直连，且 Docker Desktop 未配代理/加速器。
- **解决**：先经镜像站拉取并改回官方 tag，构建时直接用本地镜像：
  ```bash
  M=docker.m.daocloud.io
  for t in python:3.12-slim node:20-slim nginx:alpine mysql:8.0 redis:7-alpine neo4j:5.26-community; do
    docker pull $M/library/$t && docker tag $M/library/$t library/$t
  done
  docker compose build
  ```
- **备选镜像站**：`docker.m.daocloud.io`、`docker.1ms.run`（`curl -s -o /dev/null -w "%{http_code}" https://<mirror>/v2/` 返回 401 即表示可用）。
- **长期做法**：在 Docker Desktop → Settings → Docker Engine 里配 `registry-mirrors`；或用 `--build-arg` 换基础镜像源。

---

## 三、评测

### E1 正例密度过高会造出假基线

- **现象**：`--n-doc 60` 跑出 `recall@10=1.0`、`MRR=1.0`。
- **根因**：采样是「相关文档全量 + 随机负例补到 n-doc」，n-doc 太小时库内 77% 都是正例，怎么排都中。
- **解决**：`eval_ingest.py` 采样后计算 `relevant_ratio`，>20% 打印警告并写入 manifest；`eval_recall.py` 把该值透出到报告并同样告警。
- **结论**：密度高时指标只能做版本间对比，不能当绝对值对外；要报绝对值需放大到 `--n-doc 10000`（约 46 分钟，密度约 4.6%）。

### E2 评测脚本依赖缺失，一行都跑不起来

- **现象**：`pd.read_parquet` 直接报 `ModuleNotFoundError: pyarrow`。
- **根因**：`pandas` 未声明（靠传递依赖碰巧存在），`pyarrow` 完全没装。
- **解决**：`pandas==2.3.3`、`pyarrow==26.0.1` 加入 dev 依赖（注意 T1 的组名问题）。
- **启示**：脚本长期没被执行过，才会缺这种基础依赖。

### E3 端到端题目由源正文自动生成，检索必然命中

- **现状**：`eval_end2end.py` 从语料正文生成 `(question, reference_answer)`，因此只衡量答案质量与延迟，**不衡量检索难度**。
- **已缓解（2026-11-19）**：新增 `--n-negatives`，从 qrels 筛出「相关文档全部不在入库池内」的 query 作为拒答负样本（实测可用 1767 条），零 LLM 成本。同时必须注入空 `WebSearchClient` 关掉联网兜底，否则 `answerable=False` 会触发搜网、拒答判定失真。

### E4 裁判与主模型同源，分数偏乐观

- **现状**：生成与裁判都用同一模型，属自评。
- **改进**：换另一家厂商的模型做裁判，报告中注明裁判模型与日期。当前已在报告里写死 `generator_model` / `judge_model` / `prompt_sha`，便于日后区分。

### E5 `recall@1` 与 `hit@1` 看似矛盾，属正常

- 一条 query 平均有 4.9 篇正解（中位 5、最多 31）。`recall@1` 分母是「该 query 全部正解」，所以 0.31 是正常；`hit@1` 只看 top-1 有没有中一篇，因此 0.98。

### E6 用正则穷举「拒答措辞」是死路

- **现象**：为统计拒答率写了正则，三轮迭代每轮都有新措辞漏网：「知识库中没有」「资料并未涉及」「没有关于…的操作说明」「其中没有关于…的配置说明」。指标在 0.75 上下反复跳，且无法解释波动来源。
- **根因**：拒答是**语义行为**，不是固定短语；把测量工具写成关键词表，等于把指标绑在模型的措辞习惯上。
- **解决**：改为「结构化 `answerable` + 一次 LLM 分类裁判」，把结果分成 `refuse`（明确拒答）/ `hedge`（承认资料不足但给出有保留说明）/ `answer`（无依据硬答）。正则退居交叉校验。
- **收益**：能一眼看出「20% 的 hedge 其实是好行为，真正风险项 unsupported 为 0」，而不是笼统报一个 0.75。

### E7 「文档级引用覆盖率」是个错误的指标

- **现象**：第一版以「上下文中的文档有多少被引用」为主指标，跑出 0.377，看起来像模型不会引用。
- **根因**：上下文平均给 5 篇文档，答案实际只用 1.67 篇。**回答只需要 1–2 篇，把没用到的也列上反而是虚假引用**——这个指标奖励的是"多列"，不是"列对"。
- **解决**：主指标改为「来源块覆盖率 + 引用准确率（无编造引用）」（当前默认配置下为 100% / 97.5%），文档级覆盖改为诊断项；报告里明确标注两者语义差别。
- **附带发现**：换产品提示词前，来源块缺失率是 **100%**（提示词压根没要求写来源），而 faithfulness 几乎不变（4.667 → 4.70）。说明「不引用」是测量工具造成的假象，不是模型能力问题。


### E8 正样本里混入「检索失败」，把答案分拉低

- **现象**：30 条正样本的答案质量均分 4.70，逐条看发现 4 条 `evidence_count=0`，faithfulness 全为 1。
- **根因**：题目由源正文生成，但生成完就跑管线，没有校验「源文档是否真的被召回」。检索失败被算成「答案错误」，直接污染均值。
- **解决**：`eval_end2end.py` 记录 `source_hit`（证据里是否存在源 `source_id`），答案质量只在命中样本上统计，未命中单列 `n_retrieval_miss`。修正后均分 4.70 → 4.85（同批样本），并额外输出 95% 置信区间。
- **教训**：**先把「测量对象」筛对，再谈优化**；否则优化的是噪声。

### E9 规划器「无需检索」短路，让 13% 的问题绕过了知识库

- **现象**：修 E8 时发现 4 条 `ev=0` 不是检索为空，而是根本没检索。日志确认 `无需检索短路` 4 次，且规划来源都是 LLM（非 fallback）。
- **根因**：规划器判 `need_retrieval=false` 时直接返回空证据，随后模型用参数知识作答 —— 这违反「只用检索到的资料作答」的产品承诺。
- **解决**：新增 `PLANNER_REQUIRE_RETRIEVAL` 开关，开启后把短路改写成一次混合检索；默认关闭，原因是寒暄类问题（「你好」）也会被强制检索并可能被判拒答。
- **验证**：`tests/rag/test_agentic_rag_require_retrieval.py` 覆盖「关→不检索」「开→强制混合检索」「原本要检索→不改写计划」三个约定。

### E10 `recall@k` 有结构上限，裸指标会被误读

- **现象**：`recall@5 = 0.856`，看起来「还差 14%」，容易得出「检索不行」的结论。
- **根因**：一条 query 若有 n 篇正解，top-k 最多只能召回 min(k,n) 篇。本数据集平均每 query 有 4.9 篇正解，上限只有 0.902。
- **解决**：`eval_recall.py` 同时输出 `recall@k_ceiling` 与 `recall@k_achievement`。实测达成率 recall@1 98.0%、@5 95.2%、@10 97.7% —— 检索在这组数据上已接近上限。
- **注意**：达成率高是在 **23.1% 正例密度**下取得的；密度降到真实水平（<1%）后负例挤占 top-k，达成率会明显下滑，因此该结论不能外推到真实语料。

---

## 四、运行与部署故障排查

### 4.1 启动类

| 现象 | 排查 |
| --- | --- |
| `Invalid API Key` / `API Key expired` | 查 `.env` 的 `OPENAI_API_KEY`；到服务商控制台确认状态与权限 |
| `Connection refused` / `Access denied` | 查主机/端口/账号密码；确认服务在跑；确认账号权限与防火墙 |
| 图谱页 503「图谱服务不可用」/ `Neo.ClientError` | `net start neo4j`；核对 `NEO4J_URI/USER/PASSWORD`；重启后端看「图 Schema 就绪」日志。**Neo4j 不可用时上传/检索/问答不受影响**，仅图谱证据降级 |
| 前端 `CORS error` / `Network error` | 核对 `CORS_ORIGINS` 白名单是否含前端地址；确认后端在跑、端口对得上 |
| 重排序结果顺序不变（similarity 全 0） | 查 `RERANKER_API_BASE_URL/KEY/MODEL` 与额度；失败会按原顺序降级，不阻塞对话 |
| `Address already in use` | 找占用进程后终止，或换端口 |
| 上传报 `File too large` / `Unsupported file type` | 单文件 20MB、批量总计 200MB；仅支持 `.pdf/.docx/.md/.txt/.pptx` |
| 会话历史取不到 | 查数据库连接、会话 ID、用户权限 |
| 导入报错/包版本不一致 | `uv sync --extra dev`、`npm install`；**不要在服务运行中执行 `uv sync`**（Windows 可能留下半卸载的包） |

### 4.2 Docker

| 现象 | 排查 |
| --- | --- |
| `backend` 反复重启 / 提示后端未就绪 | `docker compose logs -f backend`；MySQL/Redis/Neo4j 首启较慢，通常 1–2 分钟自愈；`docker compose ps` 确认 healthy |
| 容器内连 `localhost` 报 Connection refused | Docker 模式下 `MYSQL_HOST/REDIS_HOST/NEO4J_URI` 由 `docker-compose.yml` 的 `x-app-infra-env` 覆盖为容器名，**不要**改回 `localhost` |
| 改模型 Key | 编辑 `backend/.env`（不进镜像，无需重建）→ `docker compose restart backend` |
| 改数据库 / Neo4j 密码 | 根目录 `.env` 写 `MYSQL_ROOT_PASSWORD` / `NEO4J_PASSWORD` 后 `docker compose up -d`。**Neo4j 密码只在数据卷为空时生效**，已初始化需 `down` + `docker volume rm zhiku_neo4j_data`（会清空图谱） |
| 首次构建很慢 | 后端镜像含 torch/unstructured，首次约 10 分钟属正常；依赖走 `uv.lock` |
| 想全清重来 | `docker compose down -v` 删除数据卷，再 `start.bat` |
| 无 Key 能用什么 | 可启动、可注册登录、可浏览、可上传解析；实体抽取/向量化/问答会失败或降级 |

### 4.3 日志与调试

- 后端日志：`backend/logs/`；前端日志：浏览器控制台。
- 常见错误模式：
  - `【重排序服务】重排序失败: ...` → 查 `RERANKER_*`，会自动降级
  - `HTTP 503: 图谱服务不可用` → 查 Neo4j 与 `NEO4J_URI`
  - `OperationalError: (2003, "Can't connect to MySQL server")` → 查数据库连接
  - `HTTPException: 401 Unauthorized` → 查 token 是否有效/过期
- 开详细日志：`backend/app/core/logger_handler.py` 调 `DEBUG`。
- 交互式接口文档：`http://localhost:8000/docs`。
- 性能：确认 Neo4j 向量/全文索引已建（启动时 `ensure_graph_schema` 幂等创建），再看 CPU/内存与上下文长度配置。

---

## 五、防复发清单

改动前扫一眼，提交前再过一遍：

1. `rag_context_prompt.md` 的 `{context}` 占位符还在。
2. `local_retriever.py` 的工具语义没有被收窄或兜底。
3. 前端仍是 `ReactMarkdown` 渲染，没有「参考依据」区与编号徽标。
4. `agent.py` 仍只转发 `langgraph_node == "model"` 的 token。
5. 量化指标没有编造；`relevant_ratio` 超阈值时没有硬报数字。
6. 提交前 `git status` 无 `.env`、`docs/private/`、缓存。
7. `start.bat` / `stop.bat` 仍是 UTF-8 + CRLF。

---

## 更新记录

| 日期 | 变更 |
| --- | --- |
| 2026-11-18 | 建立文档，收录 B1–B7、T4–T6、Docker 排查 |
| 2026-11-19 | 新增 T1（uv 同名 dev 组）、T3（pytest 退出码）、T7（Neo4j 弃用 queryNodes）、E1–E5（评测）；合并 B8 与防复发清单 |
| 2026-11-19 | 新增 T8（Docker Hub 不可达的镜像站绕行）、E6（拒答判定改用 LLM 裁判）、E7（引用覆盖率指标修正）；E3 标记为已缓解 |
| 2026-11-19 | 新增 E8（正样本混入检索失败）、E9（规划器短路绕过知识库）、E10（recall@k 结构上限） |
