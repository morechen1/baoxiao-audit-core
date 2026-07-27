# API

服务默认监听 `:8000`，交互文档位于 `/docs`，OpenAPI JSON 位于 `/openapi.json`。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health` | 数据库与可选提供者状态 |
| POST/GET | `/sources` | 注册/列出来源 |
| POST | `/collection/url` | 采集公开 URL |
| POST | `/collection/local` | 采集 `DATA_DIR` 内文件 |
| POST | `/parsing/run` | 解析待处理文档 |
| POST | `/validation/run` | 执行确定性校验 |
| POST | `/review/batches` | 导出审核包 |
| POST | `/review/results/import` | 导入 `review_results` 下 JSONL |
| GET | `/records` | 按状态/类型列出文档 |
| GET | `/records/{type}/{id}` | 查看记录与原文 |
| POST | `/knowledge/index-approved` | 标记符合资格的可信记录 |

未捕获异常返回 `{"error":{"code","message","details"}}`。FastAPI 自身的输入错误返回
标准 422 结构；下一阶段可统一转换为同一错误信封。
