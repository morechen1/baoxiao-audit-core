# 兼容入口：比赛演示运行

本项目已收口为完整源码提交版。安装、配置、数据库恢复、一键启动、无 API key 行为和
故障处理统一见 [RUN_PROJECT.md](RUN_PROJECT.md)。

三个预置案例仍保留用于展示：

- High：多项显式高风险营销主张；
- Boundary：退保误导与合同限定并存；
- Low：合规说明，最终 Finding 为 0。

所有案例均走真实后端 API。Low 案例未形成有效 RiskFinding 时不触发下游 Trusted RAG
Explanation Provider；风险发现阶段的 Semantic Parser 仍可执行语义解析。

```bash
./scripts/start_project.sh
```
