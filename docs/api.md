# API

服务默认监听 `:8000`，交互文档位于 `/docs`，OpenAPI JSON 位于 `/openapi.json`。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health` | 数据库与可选提供者状态 |
| POST/GET | `/sources` | 注册/列出来源 |
| POST | `/collection/url` | 使用已登记来源采集公开 URL |
| POST | `/collection/local` | 采集 `DATA_DIR` 内文件 |
| POST | `/parsing/run` | 解析待处理文档 |
| POST | `/validation/run` | 执行确定性校验 |
| POST | `/structured-drafts/import` | 导入 `DATA_DIR/parsed` 下 JSONL 草稿 |
| POST | `/review/batches` | 导出审核包 |
| POST | `/review/results/import` | 导入 `review_results` 下 JSONL |
| GET | `/records` | 按状态/类型列出文档 |
| GET | `/records/{type}/{id}` | 查看记录与原文 |
| POST | `/knowledge/index-approved` | 标记符合资格的可信记录 |

未捕获异常返回 `{"error":{"code","message","details"}}`。FastAPI 自身的输入错误返回
标准 422 结构；下一阶段可统一转换为同一错误信封。

网络采集必须提供启用的 `source_id`，并匹配来源类型和允许域名；真实性始终从
`pending_verification` 开始。审核结果必须提供有效 `batch_id` 和 `batch_item_id`。

本 API 当前只适用于受控环境，不包含生产级身份认证、权限系统或自动法律结论。
