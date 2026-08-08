# 保销智审最终初赛 Demo

## 运行边界

- PostgreSQL 16，默认数据库固定为 `baoxiao_contest_final`。
- 最终数据库已执行 Alembic migration，并由已审核历史归档恢复 15 个可信来源、73 个活跃 KnowledgeChunk。
- `SEMANTIC_SCREENING_ENABLED=false` 是最终比赛默认值；M7 semantic screening 代码保留但不作为正式功能。
- 风险识别只来自确定性筛查；大模型只解释既有 Finding 和已绑定证据。

## 启动

首次在独立源码目录运行时安装本地依赖（不会包含在导出包中）：

```bash
make install
```

随后启动最终工作台：

```bash
make final-demo
```

也可以显式指定安全配置：

```bash
FINAL_DATABASE_NAME=baoxiao_contest_final \
FINAL_DATABASE_URL="postgresql+psycopg://${USER}@localhost:5432/baoxiao_contest_final" \
SEMANTIC_SCREENING_ENABLED=false \
make final-demo
```

启动脚本会应用 migration、验证 15 个可信来源和 73 个 chunk、串行跑三个确定性案例，然后启动 `http://127.0.0.1:8000`。它不会运行 M6 benchmark，也不会启用 semantic screening。

## DeepSeek 受控解释

真实解释需要在本机环境变量或未提交的 `.env` 中配置，禁止将秘密值写入仓库或导出包：

```text
LLM_ENABLED=true
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=<local-only>
LLM_MODEL=deepseek-v4-flash
LLM_TIMEOUT_SECONDS=30
```

没有 API key 时，确定性筛查、可信证据检索、机构基础报告和消费者基础提示仍可运行；真实 Artifact 不会被伪造。配置完成后，预置 High/Boundary 案例会通过现有 API 请求机构版和消费者版受控解释，并继续执行 Schema、Citation、Claim 与 Uncertainty validation。

## 三个演示案例

- High：`高收益承诺宣传`，展示多个确定性 Finding、可信监管证据和双端解释。
- Boundary：`退保价值边界表述`，展示有限且明确的退保风险信号。
- Low：`合同要点说明`，必须保持 0 Finding、0 Provider call、0 Artifact。

预置案例点击后走真实后端链路，不使用静态结果替代本次运行。Evaluation Center 历史页面和 provisional 数据保留，但不在比赛主导航中展示。

## 初始化与故障边界

当前比赛机器上的 `baoxiao_contest_final` 已完成 migration 和可信知识初始化。三个业务演示案例由 final smoke 通过正式 screening API 写入；没有运行会注入 3 个 demo-only 文档的通用 `seed` 命令。若数据库不存在，启动脚本只会在空库中调用经验证的可信知识 rematerialization；若发现非 0/15 的 SourceDocument 数量或非 73 个 chunk，会停止并要求定位集成问题，不会修改 chunker 或注入旧的 3-chunk 构造 fixture。

不要删除或覆盖 `baoxiao_demo`、`baoxiao_demo_release`、`baoxiao_semantic_dev`。不要把 `.env`、API key、数据库 dump、日志或缓存复制到比赛导出包。
