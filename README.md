# AI Online Homework Backend

面向一门课程、多个班级的简答题作业平台后端，正在从早期 Express 项目重构为 Python 服务。

**状态：开发中。** 本地已通过真实 HTTP 验证建班、出题、发布、答题、批改和查分流程，含跨角色/跨班反例，见 [后端验收](docs/backend-acceptance.md)。这不是生产就绪版本。名称中的 AI 表示规划方向：目前只有关键词匹配占位评分，尚未接入真实模型。

## 技术栈

Python 3.12 / 3.13、FastAPI、Pydantic、MongoDB、Beanie 2、PyMongo Async、pwdlib / Argon2id、PyJWT、uv、pytest / HTTPX、Ruff。依赖版本由 [uv.lock](uv.lock) 锁定，当前不再使用 Motor。

## 已实现的基础能力

- 管理员、教师、学生三种角色；学生注册，管理员创建、查询、启停用及重置教师/学生账号。
- Argon2id 密码哈希、旧 bcrypt 渐进升级；JWT 校验及账号级令牌版本撤销，覆盖停用后重新启用的旧令牌失效。
- 班级创建、班级码加入、成员分页、班级归档；学生最多属于一个班级，以 `User.class_id` 为唯一关系来源，条件更新防止并发入班覆盖。
- 简答题创建校验、按教师归属的详情读取、题干搜索和分页，以及未引用题目的部分编辑、停用；评分标准保持自定义文本，引用题目通过保守锁保护。
- 新建已发布作业保存题面快照，学生获得题干、题号、图片和满分但不含参考答案/评分标准；提交和教师确认使用快照，旧作业明确标记为引用兼容模式。
- 作业草稿编辑、正式发布、归档及状态筛选；发布原子保存快照，条件更新防止并发覆盖，按 UTC 截止时间和班级归属控制学生访问与提交。
- 学生答案校验、一次正式提交的数据库唯一约束，以及按作业找回本人历史提交；重复请求返回冲突，不覆盖已有答案。
- 教师提交分页、待批改筛选及批改进度；逐题分数校验、首次确认后锁定成绩。学生独立响应只展示已确认的最终成绩，不返回 AI 草稿字段。
- JPEG/PNG 图片真实内容校验、5 MiB 和像素限制、重新编码去除元数据、随机文件名及写入失败清理；图片保留公开静态 URL。
- FastAPI 自动接口文档、显式 CORS 来源、生产环境配置检查。
- 隔离的 MongoDB 接口测试：每条测试独立数据库，清理前验证归属标记，不使用开发数据库。

题库、作业、提交、批改与图片上传已通过本地后端流程验收；评分异步化、真实模型、前端及部署资源限制仍待完善。首次确认后的成绩暂不可更正。完整边界见 [开发状态与路线图](docs/status.md)。

## 本地运行

需要安装 Git、uv、Python 3.12 或 3.13，以及可用的 MongoDB。当前本地验证基线为 Python 3.13.3、MongoDB 8.0.6；不宣称所有版本/平台都已验证。以下命令在仓库根目录执行，MongoDB 仅绑定本机。

```powershell
git clone --branch codex/node-07-question-bank https://github.com/sqwjr08/ai-online-homework-backend.git
cd ai-online-homework-backend
uv sync --frozen --extra dev --python 3.13
```

节点 01 至 13 的本次验收版本位于 `codex/node-07-question-bank` 分支；`main` 已包含此前 Python 基础版本，但本次发布不自动合并 main。已经位于当前开发目录时直接从 `uv sync` 开始。

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
uv run --frozen --no-sync python -c "import secrets; print(secrets.token_urlsafe(48))"
```

将生成值填入本地 `.env` 的 `JWT_SECRET`；不要提交 `.env`。示例中的 `admin` 密码和密钥都是公开占位值，启动前更换 `DEV_ADMIN_PASSWORD`。示例显式启用开发管理员，仅用于本机体验；初始化不会覆盖数据库中已有同名账号。详见 [配置说明](docs/configuration.md)。

若本机尚无 MongoDB 服务，在单独终端执行（要求 `mongod` 在 PATH）：

```powershell
New-Item -ItemType Directory -Force .mongo-data | Out-Null
mongod --dbpath .mongo-data --bind_ip 127.0.0.1 --port 27017
```

已有 MongoDB 时不要启动第二个实例或改动其数据目录。确认 `.env` 中连接地址和数据库名后，在另一终端启动后端：

```powershell
uv run --frozen --no-sync uvicorn app.main:app --host 127.0.0.1 --port 8000
```

访问 [Swagger UI](http://127.0.0.1:8000/docs)、[OpenAPI](http://127.0.0.1:8000/openapi.json)、[健康检查](http://127.0.0.1:8000/health)。端口占用时使用其他端口，并同步 `PUBLIC_BASE_URL`。`/health` 仅表示 HTTP 在线，不实时探测数据库；Swagger UI 的脚本/样式依赖外部 CDN。

## 测试

```powershell
uv run --frozen --no-sync pytest -q -W error -m "not integration"
uv run --frozen --no-sync ruff check . --no-cache
```

集成测试需要 MongoDB；运行方法、隔离约束和本轮实际结果见 [测试说明](docs/testing.md)。测试通过不等于题库到批改的完整产品验收。

## 目录与文档

```text
app/              应用入口、配置、模型、请求/响应、路由和服务
tests/            非数据库测试与 MongoDB 接口测试
docs/             公开配置、架构、接口、测试和路线图
.env.example      不含真实凭据的开发配置模板
pyproject.toml    项目与工具配置
uv.lock           可重复安装的依赖锁文件
```

- [架构与项目来源](docs/architecture.md)
- [配置与安全限制](docs/configuration.md)
- [接口入口与权限](docs/api.md)
- [题库接口与虚构示例](docs/questions.md)
- [作业题面与快照](docs/assignment-snapshots.md)
- [作业发布与截止规则](docs/assignment-lifecycle.md)
- [提交规则与历史查询](docs/submissions.md)
- [教师批改与学生结果](docs/teacher-review.md)
- [图片格式与公开访问边界](docs/images.md)
- [后端完整验收与复现步骤](docs/backend-acceptance.md)
- [测试与验证](docs/testing.md)
- [开发状态与路线图](docs/status.md)

前端独立开发，不包含在本仓库。旧 Express 代码保留在 Git 历史中，不迁移旧 MongoDB 数据，也不兼容旧 API。仓库尚未选择开源许可证；公开可读不代表授予任意复用许可。
