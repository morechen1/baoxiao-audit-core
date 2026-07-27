# 保销智审：可信数据与审核核心

“保销智审”是保险营销合规审查与消费者权益保护智能体的后端数据底座。本阶段完成
公开/本地材料采集、不可变原件保存、解析、确定性校验、人工审核、修订留痕和可信索引
状态，不调用任何外部 LLM、Embedding 或 OCR 服务。

## 重要数据声明

`data/samples` 全部是 `demo_only` 或 `constructed_for_evaluation`：

- 仅用于验证系统流程；
- 不代表真实处罚、真实监管文件、真实保险产品或真实销售记录；
- 不得标记为 `verified_public`，也不能进入正式可信知识库；
- 正式数据必须具有公开来源、原文证据和人工审核结论。

## 当前范围与架构

FastAPI 和 Typer CLI 共用 service/repository 层。PostgreSQL 保存来源、文档、切片、四类
结构化记录、审核批次/决定和状态历史；`data/raw` 保存按 SHA-256 命名的原件。索引服务
当前只写入可信状态，不做 RAG、全文搜索、向量生成或法律结论。

详细设计见 [架构](docs/architecture.md) 和 [数据模型](docs/data-model.md)。

## 快速启动

要求 Docker / Docker Compose，或本地 Python 3.12。

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec api alembic upgrade head
docker build --target test -t baoxiao-audit-core:test .
docker run --rm baoxiao-audit-core:test
curl http://localhost:8000/health
```

本地开发：

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
# 本地无 PostgreSQL 时可将 DATABASE_URL 改为 sqlite:///./baoxiao.db
.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli.main health-check
```

## CLI

```bash
python -m app.cli.main init-db
python -m app.cli.main register-source --name "示例来源" \
  --base-url "https://example.com" --source-type regulation
python -m app.cli.main collect-url --url "https://example.com/document.pdf" \
  --source-type regulation --source-id 1
python -m app.cli.main collect-directory --path ./data/incoming \
  --source-type regulation
python -m app.cli.main collect-local-manifest \
  --file ./data/import/local_import_manifest.jsonl
python -m app.cli.main parse-pending
python -m app.cli.main import-structured-drafts \
  --file ./data/parsed/structured_drafts.jsonl
python -m app.cli.main validate-pending
python -m app.cli.main export-review-batch --data-type penalty --format jsonl
python -m app.cli.main export-review-bundle --data-type penalty
python -m app.cli.main create-review-result-template --batch-id 1
python -m app.cli.main import-review-results \
  --file ./data/review_results/review_result.jsonl --batch-id 1
python -m app.cli.main list-records --status pending_review
python -m app.cli.main index-approved
python -m app.cli.main repair-status-consistency --dry-run
python -m app.cli.main resubmit-for-review --record-type product_document \
  --record-id 123 --reason "已补充可核验官方来源"
python -m app.cli.main health-check
```

采集器使用明确 User-Agent、逐跳安全验证、20 秒超时、三次温和重试及 50 MiB 限制；
拒绝 localhost、私网、链路本地、云元数据和 DNS 重绑定目标；初始 URL、每次重定向及
最终 URL 都必须匹配登记来源主机或显式允许域名。CLI/API 网络采集与本地官方清单必须
使用已登记、启用且域名/类型匹配的来源。普通目录采集明确标记为“无官方来源声明”；
官方清单会同时保留本地导入信息和可核验 URL occurrence，但两者的初始真实性都固定为
`pending_verification`，不能通过采集参数直接指定 `verified_public`。文档采集接口只
接受监管、处罚和产品资料，评测样本必须走独立导入路径。PDF 离线样例位于
`tests/fixtures/demo.pdf`。

## API

OpenAPI：`http://localhost:8000/docs`。

```bash
curl -X POST http://localhost:8000/sources \
  -H 'Content-Type: application/json' \
  -d '{"name":"示例来源","base_url":"https://example.com","source_type":"regulation"}'

curl -X POST http://localhost:8000/parsing/run
curl -X POST http://localhost:8000/validation/run
curl 'http://localhost:8000/records?status=pending_review'
```

端点明细见 [API 文档](docs/api.md)。

## 审核闭环

1. 采集后状态为 `collected`，原件由 SHA-256 去重。
2. 每次解析、校验、审核导出/导入和索引前都重新流式核对原件 SHA-256 及受管路径。
   疑似扫描 PDF 隔离为 `requires_ocr`，不伪造文本且不进入后续批次。
3. 人工导入经 Pydantic 验证的结构化草稿；仅 `parsed`/`auto_validation_failed`
   可导入，进入待审或任何人工终态后永久锁定普通草稿入口。
4. 确定性校验只会进入 `pending_review` 或 `auto_validation_failed`，绝不会自动批准。
5. 待审记录可导出 JSONL/XLSX；真实资料默认导出可移植 ZIP bundle，内含 manifest、
   哈希绑定的决定模板及 `sources/<sha256>.<ext>` 原件，不暴露主机绝对路径。
6. 只有 `approved_with_revision` 可以携带 corrections；文档修订按
   `structured_record_id` 精确定位，并以对应 Pydantic 草稿模型对完整候选值重新做类型
   校验后写入，保存独立 `post_review_validation` 审计结果。
7. `authenticity_decision` 必须指定一个属于当前文档的合格官方 occurrence、理由和
   `verified_public` 新值，并随已批准的哈希绑定决定提交。待核实文档缺少该决定时不得
   批准，应选择 `pending_source_verification`，补齐来源后再显式重新送审。
8. 索引不修改 `final_review_status`。只有已批准、`verified_public`、解析/校验通过、
   结构化记录和父文档状态一致且引用可在完整原文定位的数据才能被标记为 `indexed`。

完整状态与审核格式见 [审核工作流](docs/review-workflow.md)。

## LLM 后续接入

业务层只依赖 `LLMProvider`、`EmbeddingProvider`、`OCRProvider` 抽象。目前使用
`MockLLMProvider`、`DisabledEmbeddingProvider` 和 `DisabledOCRProvider`。保持
`.env` 中三个 `*_ENABLED=false` 即可无密钥启动。未来新增厂商适配器时不得让 service
层直接依赖厂商 SDK，也不得绕过人工审核闸门。

## 质量命令

```bash
make format
make lint
make typecheck
make test
```

## 当前限制

- 不含 OCR；疑似扫描 PDF 只标记 `requires_ocr`。
- DOCX 文件格式本身不提供可靠页码，因此保留段落和标题、页码统一为 1 并产生警告。
- 不执行 JavaScript，不处理登录后页面，不提供大规模调度。
- 不包含权限、前端、向量、RAG、LLM 推断或自动法律结论。
- API 当前只适合受控网络环境，不包含生产级身份认证。
- LLM、Embedding 和 OCR 保持完全禁用；接口预留不代表已接入这些能力。
- 来源请求间隔已建模，当前单 URL 命令不负责跨任务全局限速。

## 下一阶段

接入真实监管来源白名单与 robots/使用条款核验、OCR 人工复核队列、字段抽取器、
PostgreSQL 全文检索、经批准的 Embedding 管线、权限隔离和审核 UI。任何模型输出仍须
绑定原文证据并经过既有审核状态机。
