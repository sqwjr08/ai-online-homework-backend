# 配置与安全限制

从仓库根目录运行，配置由环境变量和 `.env` 读取，环境变量优先。更改配置后重启服务。真实 `.env` 必须留在本地，只有 `.env.example` 可以提交。

| 配置 | 说明 |
| --- | --- |
| ENVIRONMENT | `development`、`test`、`production`，默认 development |
| MONGODB_URI | MongoDB 连接；示例指向本机 27017，无真实远程凭据 |
| DATABASE_NAME | 默认 `answer_platform_dev`；测试不使用此库 |
| JWT_SECRET | 自行生成随机值；生产拒绝占位值和少于 32 字节的密钥 |
| JWT_ALGORITHM | 仅 HS256 |
| JWT_EXPIRE_MINUTES | 1 至 43200，默认 1440 |
| CORS_ORIGINS | 明确 HTTP(S) 来源的 JSON 数组，不允许通配符、凭据和路径 |
| ENABLE_DEV_DEFAULT_ADMIN | 程序默认 false；示例为 true，仅开发环境允许 |
| DEV_ADMIN_USERNAME / DEV_ADMIN_PASSWORD | 本机首次初始化账号；已有同名账号不会覆盖，示例密码必须更换 |
| UPLOAD_DIR / PUBLIC_BASE_URL | 本地图片目录与返回 URL 前缀，默认 uploads 和本机 8000 |
| AI_PROVIDER | 预留值 placeholder，目前不驱动 provider 选择 |

图片只支持 JPEG/PNG，固定输入/输出上限 5 MiB、单边 10,000、总像素 2,000 万；暂无新增配置项。静态 URL 保持公开读取，上传目录应仅允许可信服务进程写入。此限制不替代网关 HTTP 请求体限制，详见 [图片安全与部署边界](images.md)。

## 身份与令牌

新密码使用 Argon2id，旧 bcrypt 密码登录成功后可渐进升级。超出 bcrypt 72 字节限制的旧密码不截断验证，需要重置。Bearer JWT 包含用户身份和令牌版本，受保护请求读取用户当前状态/版本。

管理员停用账号、重置密码会撤销该账号旧令牌；重新启用不恢复旧令牌。管理员账号不能经教师/学生维护接口被修改。`logout` 不维护单令牌黑名单，客户端需要清除 token；没有刷新令牌或忘记密码流程。

## 不是生产部署指南

14c 新增 `AI_MAX_ATTEMPTS=3`（每轮含首次及崩溃接管，范围 1 至 10）和 `AI_RETRY_BASE_SECONDS=5`（指数退避基数，范围 1 至 300 秒）。Web 与所有 worker 必须使用相同配置。有限自动重试不等于外部调用恰好一次，也不替代费用监控。

14b 新增 `AI_JOB_TIMEOUT_SECONDS=60`（整份提交超时）、`AI_LEASE_SECONDS=90`（必须大于超时）和 `AI_POLL_SECONDS=2`（空闲/数据库故障轮询间隔）。Web 和 worker 共用配置；worker 独立运行 `python -m app.worker`，当前仅接受 placeholder provider。更多边界见 [AI 执行机制](ai-execution.md)。

配置校验不能代替上线加固。图片真实内容和应用读取大小检查已完成；当前仍缺登录限流、完善密码策略、安全的生产管理员初始化、网关请求体/并发限制、对象存储、HTTPS 部署及备份恢复验证。现有新密码长度下限较低，不能称为完整强密码策略。不要把开发管理员、公开占位密钥或无认证的 MongoDB 暴露到公网。

上传目录是公开静态资源目录，不应上传敏感信息。CORS 不是权限控制。本仓库历史仍包含旧上传素材和旧硬编码会话配置；删除当前文件不会清除历史，若曾用于真实部署，需要确认相应密钥已经轮换。
