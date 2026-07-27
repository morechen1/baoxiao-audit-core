# 数据来源政策

仅采集无需登录且允许公开访问的 HTTP(S) 页面、PDF、DOCX、TXT 或明确授权的本地文件。
不得绕过验证码、登录、付费墙、访问控制或 robots/站点使用条款，不采用攻击性反爬。

每个正式来源应登记发布者、基础 URL、类型、抓取策略和合理请求间隔。采集保存请求 URL、
最终跳转 URL、HTTP 状态、内容类型、时间、原件和 SHA-256。失败写结构化日志且不终止
同批其他文件。

网络采集执行 `SafeUrlPolicy`：仅允许无用户名/密码的 HTTP(S)，DNS 解析后拒绝 loopback、
私网、链路本地、multicast、reserved、unspecified 和云元数据地址；每次重定向重新检查，
并校验实际连接 peer 与已验证 DNS 结果一致。API 必须提供已登记且启用的 `source_id`，
URL 域名和类型必须与来源登记匹配。

真实性：

- `verified_public`：来源与内容已人工核对；
- `pending_verification`：尚未完成来源核对；
- `constructed_for_evaluation`：仅限人工评测样本；
- `demo_only`：仅用于演示。

后两类及待核验数据不得进入正式知识库。

API 网络采集结果固定为 `pending_verification`，不能由请求者直接声明
`verified_public`。内容 Blob 按 SHA-256 去重，但每个不同来源保存在
`document_occurrences`。
