# 百炼接入：配置、契约与异步适配器

2026-09-28：服务商为阿里云百炼官方、地域为华北 2（北京）。15a 契约与 15b 异步适配器已完成模拟验证；15c 使用 qwen3.8-flash 完成 3 次真实调用，完整/部分/错误答案分别为 10/4/0 分。15d 另用 1 次真实调用完成隔离作业全流程：答案保存、worker 批改、教师确认及学生查分，0 重试；详见 [验证记录](testing.md)。

## 配置

默认保持 `AI_PROVIDER=placeholder`。15b 已接通工厂，设置 `AI_PROVIDER=bailian` 并配置有效 Key 后启动 worker 会向百炼发送题目和答案、产生可能的费用。15c 应使用隔离验证数据库和虚构内容，不要直接启动真实 provider 处理开发库既有 pending/重试任务。

| 变量 | 当前约定 |
| --- | --- |
| BAILIAN_API_KEY | 默认为空；SecretStr 存储且不在配置 repr 中显示。仅放本地 .env/部署环境，不放 Git 或前端 |
| BAILIAN_BASE_URL | 默认 https://dashscope.aliyuncs.com/compatible-mode/v1；也接受北京业务空间专属 HTTPS 地址，拒绝其他域名、地域、用户凭据、查询串和非标准端口 |
| BAILIAN_MODEL | 默认 qwen3.8-flash，可配置模型 ID；15c 已验证当前配置的三次调用及 JSON 输出 |
| BAILIAN_REQUEST_TIMEOUT_SECONDS | 默认 30 秒，范围 (0, 300]；启用 bailian 配置时必须小于整份作业超时 |
| BAILIAN_MAX_OUTPUT_TOKENS | 默认 512，范围 64 至 4096；只是请求输出预算，不是账户费用上限 |

若设置 AI_PROVIDER=bailian，Settings 要求非空 Key，并检查请求超时。placeholder 模式无须 Key，不改变现有运行方式。修改 .env 后重启 worker 才切换实现；本轮未修改真实 .env。

官方北京业务空间地址格式为 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`，原 dashscope 北京地址仍可使用。配置使用 Base URL，不应附加 `/chat/completions`。来源：[百炼官方 Chat API](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)，2026-09-28 核对。

## 提示词

`app/services/grading_contract.py` 提供 `build_grading_messages`，提示词版本为 short-answer-v1。system 消息维护固定批改规则，user 消息通过 JSON 序列化传入题干、参考答案、满分、评分标准和学生文字答案。允许同义表达，不按关键词数量评分；评分标准缺失时依据参考答案的核心概念、正确性和完整性。

不传送账号、姓名、内部 ID、图片 URL 或图片数据，不获取题图。题目仍可向学生展示图片，但依赖图片的判断依据必须由教师写入参考答案/评分标准。题干和答案本身含有的个人信息不会自动脱敏，应避免录入真实身份信息。

明确要求忽略答案中的改分/改角色指令；JSON 数据分隔和提示词不能保证完全抵御提示注入，仍必须教师复核。短评建议一至三句中文，不泄露整份参考答案；中文/句数属于提示要求，不是解析器硬性语义保证。

## 结果契约

`parse_grading_result(content, max_score)` 只解析模型正文，不解析 HTTP 外壳，也不进行网络调用。成功转换成现有 GradeResult，无需改变提交数据模型。

```json
{"score": 8.25, "comment": "主要概念正确，但缺少适用条件。"}
```

- 只接受单个 JSON 对象，字段恰为 score/comment；拒绝缺失、额外字段、重复键、Markdown 包装、非法 JSON。
- score 为有限数字，0 至本题满分，最多两位小数；拒绝布尔值、数字字符串和越界值，不静默裁剪或四舍五入。
- comment 为非空字符串，最多 500 字符；去除首尾空白。正文最多 8192 UTF-8 字节。
- 失败只抛固定错误 Invalid grading response，不携带模型原文；不把格式错误伪装成零分。worker 现有校验继续作为第二层保护。

15b 请求 `response_format={"type":"json_object"}`，保留本地严格校验；JSON 模式不等于正确评分。官方列出 Qwen3.8-Flash 支持结构化输出，15c 三次实调通过本地校验，但不代表所有题型评分质量已验收。来源：[百炼结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)。

## 15b 请求与错误处理

- 使用 HTTPX AsyncClient 调用官方 `/chat/completions`，每个 worker 复用一个连接池；HTTPX 从 dev 移到运行依赖，锁定的第三方包版本未变，无额外 SDK。工厂支持 placeholder/bailian，未知值拒绝；worker 初始化失败或退出均关闭客户端及数据库。
- 单题单次、非流式、纯文字、`enable_thinking=false`、JSON Object 和输出 token 上限；不传图片，不开启工具/搜索，不复用其他学生对话。SDK 兼容参数位置依照 [官方 Chat 文档](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)。
- 禁止重定向，TLS 默认验证，不读取系统代理变量（trust_env=false），transport retries=0。重试仅由原 worker 管理，不重复叠加。按已有指数退避处理，不额外采用 Retry-After；若配额/余额导致反复限流，预算耗尽后人工处理。
- 同时设置 HTTP 超时和单请求墙钟超时，整份提交仍受 worker 总超时限制。多题累计耗时可能超过总预算并重新批改整份提交，真实联调需据题量调整请求/任务超时，且租约仍必须更长。
- 响应以分块方式读取，外层最多 64 KiB；模型正文仍受 8192 字节及分数/评语规则限制。仅接受一条 assistant choice 且 finish_reason=stop；截断、拒答、工具调用、非法内容均拒绝，不把错误当零分。
- 408/429/500/502/503/504 以及传输故障映射为可重试错误（provider_unavailable）；超时为 timeout；401/403 为 provider_authentication，其他非 200 为 provider_request_rejected；不合格响应为 invalid_provider_response。后三者停止自动重试，教师仍可人工评分。只保存固定错误码，不记录服务端错误原文或密钥。
- 调用取消继续向上传播，不转成业务失败或触发额外请求。当前已完成三次真实调用，但没有计费监控或全面评分效果验收；这仍不是生产就绪证明。

## 下一步与验证

- 15b 已完成模拟验证；本轮 13 项单元测试通过（0.58s）、Ruff 通过，覆盖请求参数、成功、限流/认证/重定向、非法/超长响应、超时、取消和资源清理。无数据库或外部 API 请求。
- 15c：已完成用户授权的 3 次真实调用，无自动重试，仅使用虚构文字题目及答案，得分与人工预期一致；不启动 worker、不访问数据库、不修改 .env，默认 provider 仍为 placeholder。额度已用完，后续真实调用需另行授权。实际费用以百炼账单为准。
- 15a 历史记录：7 passed in 0.13s。15b 命令为 `python -m pytest -q -W error tests/test_bailian_adapter.py tests/test_bailian_contract.py tests/test_worker_config.py tests/test_grading.py`。未重跑数据库及全套回归，索引与开发 .env 不变。
