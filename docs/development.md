# 开发与部署

本地需要 Python 3.12；生产使用 Docker Compose 的 PostgreSQL 16。

```bash
make install
cp .env.example .env
make lint
make typecheck
make test
make up
make migrate
make seed
make review-demo
```

迁移通过 Alembic 管理。修改模型后创建新 revision，检查 upgrade/downgrade，并运行迁移
测试。测试不得访问真实网站，使用本地 fixture 或 httpx 模拟传输。

日志使用 structlog JSON。禁止提交 `.env`、密钥、原始采集文件、审核结果、本地数据库或
临时下载。发布前必须执行 Ruff、mypy、pytest 和 Docker 健康检查。
