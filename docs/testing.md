# 测试与验证

## 安装与非数据库测试

以下命令在仓库根目录执行，不更新锁文件：

```powershell
uv sync --frozen --extra dev --python 3.13
uv run --frozen --no-sync pytest -q -W error -m "not integration"
uv run --frozen --no-sync ruff check . --no-cache
uv lock --check --offline
uv pip check
```

非数据库测试覆盖配置、安全边界、密码/JWT 和关键词占位评分；不需要运行 MongoDB 或 uvicorn。`--no-sync` 要求事先完成含 dev 依赖的环境同步。

## 集成测试隔离

`tests/conftest.py` 使用 HTTPX 在进程内运行真实 ASGI 路由及应用生命周期，每条测试创建随机数据库 `answer_platform_test_<32 位 UUID>`，上传目录由 pytest 临时目录管理。测试清除应用配置环境变量并避开项目 `.env`。

创建前拒绝已存在的同名数据库，清理前核对本条测试生成的归属标记，仅删除对应数据库。数据库不可用会明确失败，不静默跳过；强制中断可能留下临时库，不要按前缀批量清理。

推荐使用专门的本机测试 MongoDB 实例，避免连接开发或生产服务器。先确认 27018 未被占用，在独立终端从仓库根目录启动：

```powershell
New-Item -ItemType Directory -Force .verification/mongo-data | Out-Null
mongod --dbpath .verification/mongo-data --bind_ip 127.0.0.1 --port 27018
```

另一个终端执行：

```powershell
$env:TEST_MONGODB_URI = 'mongodb://127.0.0.1:27018'
uv run --frozen --no-sync pytest -q -W error -m integration
# 或执行全部测试
uv run --frozen --no-sync pytest -q -W error
Remove-Item Env:TEST_MONGODB_URI
```

停止自己启动的实例使用所在终端的 Ctrl+C，不按进程名称停止其他 MongoDB，不修改或清空开发数据库。

集成覆盖账号、角色权限、班级、数据库索引/读写、配置生命周期和 Web 行为；部分作业/提交测试用于验证班级归属与归档边界，不代表完整业务验收。

## 本地整理验证记录

2026-09-08，在干净克隆的 Python 根目录、独立虚拟环境下实际复验：

| 检查 | 本轮结果 |
| --- | --- |
| `uv sync --frozen --extra dev --python 3.13` | 安装成功，Python 3.13.3 |
| 非数据库测试，`-W error` | 70 passed，198 deselected |
| 集成测试，`-W error` | 198 passed，70 deselected；独立 MongoDB 8.0.6、27018 端口 |
| Ruff，`--no-cache` | 通过 |
| `uv lock --check --offline` / `uv pip check` | 通过；锁文件与原开发版本逐字节一致 |
| 本机 8001 端口 Uvicorn 启停 | 数据库生命周期正常；health/docs/OpenAPI 返回 200，未登录 me 返回 401 |
| 测试清理 | 随机测试库均已清理；验证用 HTTP 服务和独立数据库实例已停止 |
| 候选文件检查 | 本地文档链接有效；运行数据忽略规则有效；常见密钥及个人路径模式检查无异常 |

本轮分两次实际执行了合计 268 项测试，而不是沿用历史通过数量。安装期间遇到执行环境的 uv 缓存访问限制，授权重试后成功；跨磁盘硬链接不可用时 uv 自动复制文件，不影响锁定版本。

未验证：Python 3.12、Linux/macOS、浏览器完整 Swagger CDN 渲染、真实 AI、前端联调、全业务闭环、正式部署、依赖漏洞审计及完整 Git 历史敏感信息审计。当前项目未配置远程 CI，本文不是 GitHub CI 成功证明。
