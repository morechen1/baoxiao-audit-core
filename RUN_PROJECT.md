# 保销智审完整项目运行指南

## 1. 系统要求

- macOS 12+ 或常见 Linux；
- Python 3.12 或更高版本；
- PostgreSQL 16（含 `psql`、`pg_isready`、`createdb`）；
- 首次安装 Python 依赖时可访问 PyPI；
- 使用 DeepSeek 完整语义能力时可访问 `api.deepseek.com`。

## 2. PostgreSQL 要求

默认数据库为 `baoxiao_contest_final`。macOS 可使用 Homebrew：

```bash
brew install postgresql@16
brew services start postgresql@16
pg_isready -h 127.0.0.1 -p 5432
```

一键启动器会尝试启动 Homebrew PostgreSQL。数据库不存在时自动创建；已有非空但不完整
数据库不会被覆盖。

## 3. Python 要求

确认版本：

```bash
python3 --version
```

启动器依次寻找 `python3.13`、`python3.12`、`python3`，并拒绝低于 3.12 的解释器。

## 4. DeepSeek 配置

首次启动会创建 `config/local.env`。也可以手动执行：

```bash
cp config/local.env.example config/local.env
```

编辑 `config/local.env`，只在本机填写：

```text
LLM_API_KEY=
```

变量名、endpoint、model、timeout 和安全开关均以模板为准。不要将该文件上传、提交或
放入 ZIP。

## 5. 首次安装

推荐直接运行启动器，它只在 `.venv` 不存在或依赖描述发生变化时安装依赖：

```bash
./scripts/start_project.sh
```

手动安装开发依赖：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
```

## 6. 一键启动

macOS Finder 双击：

```text
一键启动.command
```

启动成功后访问 `http://127.0.0.1:8000/`。运行日志仅写入本机 `runtime/logs/`，该目录不在
提交 ZIP 中。

## 7. 手动启动

通用终端：

```bash
./scripts/start_project.sh
```

也可以在已准备环境中运行：

```bash
set -a
source config/local.env
set +a
./scripts/start_final_demo.sh
```

## 8. 停止服务

双击 `停止程序.command`，或在终端中结束前台 `start_project.sh`。只有通过一键启动器创建
的进程才写入 `runtime/project.pid`。

## 9. 数据库与可信知识恢复

启动流程会：

1. 创建 `baoxiao_contest_final`（若不存在）；
2. 执行 `alembic upgrade head`；
3. 将 `release_assets/trusted_data/` 复制到本机 `runtime/data/`；
4. 空库恢复 15 个可信来源、73 个 KnowledgeChunk 和关联记录；
5. 执行 High、Boundary、Low 三案例零解释预检；
6. 启动 FastAPI。

若 `SourceDocument` 数量非 0 且非 15，或 chunk / hash 校验失败，启动会停止，不会自动
修复或覆盖部分状态。

## 10. 无 API key 时的行为

系统可以无 key 启动。Provider 状态会显示不可用：

- 确定性规则、数据库、可信知识、基础报告和前端可用；
- Semantic Parser 调用失败关闭，不会伪装成成功；
- Controlled RAG 不会降级为无引用文本；
- Fixture 不冒充真实模型输出。

未形成有效 RiskFinding 时，不触发下游 Explanation Provider；风险发现阶段的 Semantic
Parser 仍可能执行。

## 11. 常见错误

- `需要 Python 3.12`：安装或指定 `BAOXIAO_PYTHON`。
- `PostgreSQL 未就绪`：启动 PostgreSQL 16 并检查 5432 端口。
- `依赖安装失败`：检查网络、证书和磁盘空间后重新运行；启动器不会无限重试。
- `数据库处于不完整状态`：不要删除用户数据，先核对数据库名和可信知识统计。
- `Provider unavailable`：检查 key、HTTPS endpoint、model、外网和 timeout。
- `端口 8000 被占用`：停止旧进程或修改 `FINAL_DEMO_PORT`。

## 12. 验证系统状态

```bash
curl http://127.0.0.1:8000/health
python3 scripts/verify_final_validation.py
./检查完整性.command
```

完整开发质量检查：

```bash
make lint
make typecheck
make test
```

## 13. 最终项目版本

- 算法系统冻结：`1176d10e1fd62b7778324157e585b1f22b3cb035`
- 156 条评测 checkpoint：`b6bdd4c15d6795106341104400d13b7b8b2e3184`
- 发布前展示 checkpoint：`e1be3eb895afc20eade576177f70fc915f4792cd`
- 最终发布 checkpoint、文件 SHA 和构建时间：见交付包根目录 `RELEASE_MANIFEST.json` 与
  `SHA256SUMS`。
