# 保销智审 V2

“保销智审”是一套面向保险营销材料的智能合规审核系统。系统通过规则与 Semantic Parser
双通道发现风险候选，再由确定性安全机制形成系统拥有的 `RiskFinding`，并基于可信监管
知识生成可追溯的机构端与消费者端解释。

## 核心能力

- 双通道风险发现：确定性规则与受控 Semantic Parser 协同产生候选。
- 确定性安全门禁：原文 Quote、否定/教育语境、角色对象、歧义、置信度和重复候选融合。
- 系统拥有的 `RiskFinding`：模型不能决定 Finding、severity 或证据归属。
- 可信监管知识：15 份已审核来源，物化为 73 个可验证 `KnowledgeChunk`。
- `EvidenceLink`：Finding 与可信知识之间保存可复核的来源、定位和证据快照。
- Controlled RAG：只解释既有 Finding 与已绑定 Evidence，不重新筛查。
- 机构/消费者双端解释：两个 audience 的 Artifact 与 Citation 独立绑定。
- Citation / Claim / Uncertainty validation：非法输出失败关闭，不降级为自由文本。
- 完整审核流程可视化：工作台、材料审核、原文高亮、Finding 切换、双端视图和检测效果页。
- 冻结验证资产：156 条逐 case 数据、预测、汇总指标和离线复核命令完整保留。
- 多格式接入：TXT、Markdown、DOCX、文本型 PDF，以及 CSV 文案列表。
- 长文语义筛查：自然边界分块、原文 offset 回映、跨块融合与调用预算上限。
- 轻量批量审核：最多 20 份材料顺序处理，单项失败独立记录并可进入详情。
- 审计报告导出：真实 HTML 与 JSON 报告，包含 Finding、Evidence、双端 Artifact 和验证状态。

## 系统架构

```text
Text / TXT / MD / DOCX / text PDF
        │
        ▼
Normalization / paragraph segmentation / bounded semantic chunks
        │
        ├── Deterministic rules ── rule context gates ──┐
        │                                                │
        └── Semantic Parser ── semantic safety gates ────┤
                                                         ▼
                                                Candidate fusion
                                                         │
                                                System-owned RiskFinding
                                                         │
                                      Trusted regulatory knowledge retrieval
                                                         │
                                                   EvidenceLink
                                                         │
                                                  Controlled RAG
                                              ┌──────────┴──────────┐
                                      Institution Artifact   Consumer Artifact
                                              └──────────┬──────────┘
                                      Citation / Claim / Uncertainty validation
                                                         │
                                                 Report / Batch output
```

`SEMANTIC_SCREENING_ENABLED=false` 是固定安全配置；它关闭的是旧 semantic screening
实验路径，不是当前 `Semantic Parser`。Semantic Parser 只生成候选，最终 Finding、severity、
原文 span、EvidenceLink 和 Citation 均由系统控制。

## 技术栈

- Python 3.12+
- FastAPI / Uvicorn
- Pydantic v2 / pydantic-settings
- SQLAlchemy 2 / Alembic
- PostgreSQL 16 / psycopg 3
- 原生 HTML、CSS、JavaScript
- pytest、Ruff、mypy
- OpenAI-compatible `/chat/completions` Provider（当前配置为 DeepSeek）

## 快速启动

完整安装、配置、数据库恢复和故障处理见 [RUN_PROJECT.md](RUN_PROJECT.md)。

macOS 双击：

```text
一键启动.command
```

通用终端：

```bash
./scripts/start_project.sh
```

首次启动会创建本地 `.venv`、安装依赖、创建 `config/local.env`（API key 为空）、连接
PostgreSQL、执行 migration，并在空数据库中恢复可信知识。发布 ZIP 不包含 `.venv`、Git
历史、日志或本机秘密。

## 配置

正式配置模板为 [config/local.env.example](config/local.env.example)。首次启动会将其复制为
未跟踪的 `config/local.env`。若需启用完整 DeepSeek 语义能力，请仅在本机填写：

```text
LLM_ENABLED=true
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_API_KEY=
SEMANTIC_PARSER_ENABLED=true
SEMANTIC_SCREENING_ENABLED=false
```

API key 不随源码或 ZIP 分发。未配置 key 时应用仍可启动；Semantic Parser 与受控解释在
Provider 不可用时 fail closed，确定性规则、可信知识、基础报告和前端仍可使用。

## V2 文档、批量与报告

- 单材料上传：`POST /api/v2/screenings/upload`，文件只在内存解析，不执行内容、不写入任意路径。
- 批量审核：`POST /api/v2/batches`，最多 20 项；CSV 可从 `text`、`content`、
  `marketing_text`、`文案`、`营销文案` 或 `材料` 列读取。
- 报告导出：`GET /api/v2/screenings/{run_id}/reports/html` 与 `/reports/json`。
- 每份文件最多 10MB；扫描图片 PDF 会明确返回不支持 OCR，不伪装为空白合规结果。
- Semantic Parser 2.0 记录分块数、Provider 调用、缓存命中、延迟与部分覆盖状态；
  原文 span 仍由系统从逐字 Quote 确定。

## 数据与可信知识

正式发布资产包含：

- 15 个 `SourceDocument`：3 份监管文件、2 份产品条款、10 份处罚来源；
- 73 个 active `KnowledgeChunk`：regulation 33、product 6、penalty 34；
- 34 条处罚记录；
- 15 个不可变原件与 15 个解析产物；
- 5 个已验证历史审计归档及 `SHA256SUMS`。

启动器只在目标数据库为空时恢复这些资产。若检测到非空但不完整的状态，会失败关闭，
不会覆盖用户已有数据。`RegulatoryCase` 不进入可信检索证据。

## 冻结验证结果

系统冻结后一次性执行了 156 条项目内部扩展冻结验证样本：

| 指标 | 结果 |
|---|---:|
| Micro Precision | 79.50% |
| Micro Recall | 88.89% |
| Micro F1 | 83.93% |
| Macro Precision | 83.47% |
| Macro Recall | 88.46% |
| Macro F1 | 84.73% |
| Exact-set Accuracy | 73.08% |
| Positive case hit rate | 90.00% |
| Negative false-alarm rate | 16.67% |
| Quote integrity | 100% |
| Hallucinated quote accepted | 0 |

这是项目内部构建并冻结的验证集，不等同于第三方独立 Benchmark 或人工逐条 Ground
Truth。逐 case 预测、数据 SHA、RAG smoke 和统计定义位于
`data/evaluations/final_extended_frozen_validation_v1/`。无需调用 Provider 即可复核：

```bash
make verify-validation
```

## 安全边界

LLM 不能：

- 最终决定或修改 `RiskFinding`；
- 修改 severity、原文 span 或新增风险类别；
- 自由搜索数据库或绕过可信知识准入；
- 创造 source quote、URL、locator 或伪造 Citation；
- 绕过 Schema、Citation、Claim 或 Uncertainty validation；
- 在验证失败时降级为无引用自由文本。

未形成有效 `RiskFinding` 时，不触发下游 Trusted RAG Explanation Provider 调用；风险发现
阶段的 Semantic Parser 仍可执行语义解析。

## 项目目录

```text
app/                    API、模型、业务服务、Provider、规则、Prompt 与前端
scripts/                启动、数据库恢复、验证复核、秘密扫描与发布构建
migrations/             Alembic 数据库迁移
tests/                  单元、集成与前端契约测试
docs/                   架构、数据模型、可信检索和受控 RAG 文档
data/evaluations/       冻结验证数据、逐 case 预测和汇总
release_assets/         15 个可信原件与对应解析产物
knowledge_archives/     5 个经 SHA-256 验证的审计归档
config/                 本地运行配置模板
```

## 质量命令

```bash
make lint
make typecheck
make test
make verify-validation
./检查完整性.command
```

## 明确限制

- 不提供 OCR 生产服务、Embedding、向量数据库或自动法律结论。
- 可信检索使用受控的 PostgreSQL 中文词法排序，不宣称标准 BM25。
- 当前发布未包含生产级身份认证、密钥托管或多租户隔离。
- 审核结果是风险信号和复核辅助，不构成违法认定或最终法律意见。
