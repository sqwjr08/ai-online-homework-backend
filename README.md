# AI Online Homework Backend

面向一门课程、多个班级的简答题作业平台后端，正在从早期 Express 项目重构为 Python 服务。

**状态：开发中。** 当前重点是账号、权限、单班级关系和可重复的接口测试。题库、作业、提交和教师确认已有基础接口，但完整业务流程尚未验收，不能作为生产就绪版本部署。名称中的 AI 表示规划方向：目前只有关键词匹配占位评分，尚未接入真实模型。

## 技术栈

Python 3.12 / 3.13、FastAPI、Pydantic、MongoDB、Beanie 2、PyMongo Async、pwdlib / Argon2id、PyJWT、uv、pytest / HTTPX、Ruff。依赖版本由 [uv.lock](uv.lock) 锁定，当前不再使用 Motor。

## 已实现的基础能力

- 管理员、教师、学生三种角色；学生注册，管理员创建、查询、启停用及重置教师/学生账号。
- Argon2id 密码哈希、旧 bcrypt 渐进升级；JWT 校验及账号级令牌版本撤销，覆盖停用后重新启用的旧令牌失效。
- 班级创建、班级码加入、成员分页、班级归档；学生最多属于一个班级，以 `User.class_id` 为唯一关系来源，条件更新防止并发入班覆盖。
- FastAPI 自动接口文档、显式 CORS 来源、生产环境配置检查。
- 隔离的 MongoDB 接口测试：每条测试独立数据库，清理前验证归属标记，不使用开发数据库。

题目创建/查询、作业创建/查询、答案提交、占位评分、教师确认和本地图片上传属于**待完善模块**。未确认成绩会向学生隐藏，但确认后的响应字段隔离等问题仍待处理。完整边界见 [开发状态与路线图](docs/status.md)。

## 本地运行

需要安装 Git、uv、Python 3.12 或 3.13，以及可用的 MongoDB。当前本地验证基线为 Python 3.13.3、MongoDB 8.0.6；不宣称所有版本/平台都已验证。以下命令在仓库根目录执行，MongoDB 仅绑定本机。

```powershell
git clone https://github.com/sqwjr08/ai-online-homework-backend.git
cd ai-online-homework-backend
uv sync --frozen --extra dev --python 3.13
```

Python 整理版本位于 `codex/python-backend-showcase` 分支。若 `main` 尚未合并新版本，请在克隆后、安装依赖前执行 `git switch codex/python-backend-showcase`。已经位于 Python 整理目录时直接从 `uv sync` 开始。

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
- [测试与验证](docs/testing.md)
- [开发状态与路线图](docs/status.md)

前端独立开发，不包含在本仓库。旧 Express 代码保留在 Git 历史中，不迁移旧 MongoDB 数据，也不兼容旧 API。仓库尚未选择开源许可证；公开可读不代表授予任意复用许可。
