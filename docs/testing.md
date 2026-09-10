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

### 节点 13 最新流程验收

2026-09-09，新增 2 项真实 TCP/HTTP 验收，不通过 ASGITransport、不覆盖认证或评分依赖。测试管理员仅作初始化，其余账号/班级/图片/题目/作业/成绩全部经接口创建。临时 Uvicorn 绑定本机自动分配端口，沿用隔离 fixture 的数据库生命周期。结果 **2 passed in 3.02s**；业务最终分数为预期 12.5/15。

```powershell
$env:TEST_MONGODB_URI = 'mongodb://127.0.0.1:27018'
uv run --frozen --no-sync pytest -q -W error tests/integration/test_backend_acceptance.py
```

另执行直接相关的 **70 项回归通过，14.57s**：

```powershell
uv run --frozen --no-sync pytest -q -W error tests/integration/test_submission_management.py tests/integration/test_teacher_review.py tests/integration/test_assignment_lifecycle.py tests/integration/test_assignment_snapshots.py tests/integration/test_image_uploads.py tests/test_image_validation.py tests/test_grade_validation.py tests/test_submission_validation.py tests/integration/test_classes.py::test_archive_retains_history_but_blocks_new_work tests/integration/test_question_management.py::test_locked_question_still_supports_submission_and_review
```

本轮合计 72 项通过，Ruff 通过；collect-only 收集 **490 项**，不是 490 全部通过。未重跑所有账号/配置/密码等旧测试，不删除旧回归。仅补充关键入口 OpenAPI 错误描述，没有改变业务权限、数据库模型、索引或依赖。

HTTP 验收覆盖完整主流程、两班/三角色、其他教师越权、草稿隔离、重复提交、成绩锁定、查分和归档历史，并读取 docs/OpenAPI 验证错误契约。测试使用生成图片和虚构题目，独立 27018 测试库，不访问原开发数据。复现步骤和未验收范围见 [后端验收文档](backend-acceptance.md)。

临时 HTTP 服务随测试退出，独立 MongoDB 核对路径及数据库清单、无测试库残留后关闭。公开文档链接、git diff --check 通过；依赖没有新增变动，仍仅保留节点 12 引入的 Pillow。未提交或推送。

### 节点 12 历史针对性验证

中断后的收尾复核：代码、测试、锁文件和主体文档均完整，无需补业务实现。使用项目虚拟环境执行下列合并命令，**20 passed in 2.31s**；首次受 pytest 缓存/临时目录权限阻断，授权重跑后通过。Ruff、git diff --check、公开文档链接通过；Pillow 安装版本与锁文件一致，原有第三方包记录未改变。独立 27018 实例核对路径和数据库清单后关闭，无测试库残留。修正两处旧专题文档的下一节点提示，未开始节点 13。

```powershell
$env:TEST_MONGODB_URI = 'mongodb://127.0.0.1:27018'
.\.venv\Scripts\python.exe -m pytest -q -W error tests/test_image_validation.py tests/integration/test_image_uploads.py tests/integration/test_web.py tests/integration/test_isolation.py
```

以下保留首次实施记录；本次仍未执行全部 488 项测试。

2026-09-09，新增 11 项（7 单元、4 接口），调整原 Web 上传用例检查真实生成图片及重新编码结果；没有删除旧测试。实际执行共 **20 项通过**，均带 -W error，Ruff 通过：

```powershell
uv run --frozen --no-sync pytest -q -W error tests/test_image_validation.py
# 7 passed in 0.22s
$env:TEST_MONGODB_URI = 'mongodb://127.0.0.1:27018'
uv run --frozen --no-sync pytest -q -W error tests/integration/test_image_uploads.py tests/integration/test_web.py tests/integration/test_isolation.py
# 13 passed in 2.35s
uv run --frozen --no-sync ruff check . --no-cache
```

新增 Pillow 12.3.0 并执行 uv lock、uv sync --frozen --extra dev；结构化对比锁文件确认全部原有第三方包记录未改变，未全栈升级。测试验证真实格式/损坏/动画拒绝、大小与尺寸、分块读取中止、元数据与尾随内容清理、失败清理和文件碰撞、权限、公开读取、题目引用及 OpenAPI。

collect-only 收集 488 项，**本轮未全量执行**。未验证全部旧业务、浏览器渲染、前端联调、真实 AI 或生产资源防护。仅使用程序生成的小图和临时目录，不上传真实题目，原开发数据库/上传文件未访问或修改。

### 节点 11 历史针对性验证

2026-09-09，新增 14 项（7 非数据库、7 集成），保留旧测试，仅将两处学生 AI 字段断言从 null 调整为字段不存在。实际分两次执行 **53 项通过**，均使用 `-W error`；Ruff 通过。collect-only 收集 477 项，**没有本轮全量回归结论**，历史最近全量仍为节点 09 的 451 项。

```powershell
$env:TEST_MONGODB_URI = 'mongodb://127.0.0.1:27018'
uv run --frozen --no-sync pytest -q -W error tests/test_grade_validation.py tests/integration/test_teacher_review.py tests/integration/test_submission_management.py tests/integration/test_assignment_snapshots.py tests/integration/test_assignment_lifecycle.py tests/integration/test_classes.py::test_archive_retains_history_but_blocks_new_work
# 52 passed in 13.45s
uv run --frozen --no-sync pytest -q -W error tests/integration/test_question_management.py::test_locked_question_still_supports_submission_and_review
# 1 passed in 0.58s
uv run --frozen --no-sync ruff check . --no-cache
```

覆盖首次确认后锁定、并发确认一胜一冲突且保留赢家数据、所有学生提交入口字段白名单、教师/管理员视图、分数/总分与题目校验、不部分写入、评论不自动采用 AI、分页筛选和进度、权限/归档历史及 OpenAPI。必要关联回归覆盖节点 10 并发提交、快照和旧引用、截止和班级归档、题目内容锁。

未重跑账号、密码令牌、全部题库和数据库等不相关旧用例；未验证前端、真实 AI、独立 HTTP 启停及生产部署。测试仅使用独立 27018 MongoDB 和逐条随机归属库，不访问开发数据库，不升级依赖、不改索引或迁移成绩。

结束已核对实例路径及数据库清单，无测试库残留，关闭本轮 MongoDB。公开文档链接及 git diff --check 通过。

### 节点 10 历史针对性验证

2026-09-09，根据用户要求精简执行范围，不重跑全部旧测试。新增 12 项（6 非数据库、6 集成）；以下两次实际运行均带 `-W error`，共 **54 项通过**，Ruff 通过。未升级依赖或锁文件。

```powershell
$env:TEST_MONGODB_URI = 'mongodb://127.0.0.1:27018'
uv run --frozen --no-sync pytest -q -W error tests/test_submission_validation.py tests/integration/test_submission_management.py tests/integration/test_database.py tests/integration/test_assignment_lifecycle.py tests/integration/test_assignment_snapshots.py
# 52 passed in 10.94s
uv run --frozen --no-sync pytest -q -W error tests/integration/test_classes.py::test_archive_retains_history_but_blocks_new_work
# 2 passed in 0.99s
uv run --frozen --no-sync ruff check . --no-cache
```

覆盖真实联合唯一索引、并发提交一胜一冲突、重复请求不覆盖、输入拒绝且不调用评分、本人历史查询与未确认隐藏、索引前旧异常/重复数据检查且不修改记录；关联回归包括数据库读写重连、快照和截止/归档行为。仅连接独立 27018 随机测试库，原开发数据库未访问。

collect-only 确认当前测试集合为 463 项，但**未执行本轮全量回归**，不能写成 463 项全部通过。上一次完整记录仍是节点 09 的 451 项。未复验不相关账号、安全及题库等全部旧用例，也未做前端、真实 AI 或生产部署验收。

补充检查新增 OpenAPI 本人查询入口、404 响应和答案模型限制，以及文档链接、git diff --check 均通过。结束核对测试实例路径和数据库清单，无测试库残留，独立 MongoDB 已关闭。

### 节点 09 历史全量复验

2026-09-09，最终代码执行 `pytest -q -W error`：**451 passed in 51.01s**，含 154 项非数据库测试、297 项集成测试。新增仅 19 项（10 项输入校验、9 项接口组合场景），保留此前 432 项回归；Ruff 和 `git diff --check` 通过，依赖及锁文件未修改。

覆盖草稿编辑、发布快照、幂等归档、历史批改、状态筛选和权限、归档班级、题目替换、缺失 revision 的旧文档、UTC 往返及截止等号边界、发布与编辑/归档的竞争写入，以及评分后重新检查准入。另检查生成的 OpenAPI 包含新增三个写入口、PATCH 模型、状态参数及 409 响应。未重新启动独立 HTTP 服务，不将 ASGI 测试计作浏览器验收。

测试连接独立 27018 MongoDB，逐条随机库并核对归属清理；结束后检查实例路径和数据库清单、关闭该实例。未访问原开发数据库、迁移旧数据、上传真实题目或推送代码。uv 缓存访问受限后经授权重试，未重新安装依赖。

后续单节点可先执行针对性测试，再在完成时跑完整回归；新增测试按风险聚合场景，不为所有角色和字段做重复排列。不能为了减少数量删除已有有效回归。

### 节点 08 历史复验

2026-09-08，最终代码执行 `pytest -q -W error`：**432 passed in 50.29s**，含 144 项非数据库测试、288 项集成测试。相比 07b 新增 10 项模型/响应校验、21 项快照接口测试，原占位评分测试也改用真正的 QuestionSnapshot 输入；Ruff 通过，依赖和锁文件未改动。

覆盖完整快照保存与顺序、教师/学生列表和详情字段白名单、跨班/跨教师/草稿访问、源题修改/停用/删除后的稳定题面与评分、锁前并发编辑、客户端快照注入、旧引用来源标记与不回写、快照缺项/版本异常不回退，以及缺项时确认成绩不改变提交。所有测试均为虚构数据，不代表完整产品验收或真实 AI 质量验收。

使用独立 27018 MongoDB 实例和随机归属测试库；结束后核实无遗留测试库并关闭实例。没有访问原开发数据库、迁移旧作业、改动真实上传文件或重新安装依赖。

### 节点 07b 历史复验

2026-09-08，在 `codex/node-07-question-bank` 上实际执行完整测试：`pytest -q -W error`，**401 passed in 43.54s**，含 134 项非数据库测试及 267 项集成测试。相比 07a 新增 30 项部分编辑输入校验和 44 项维护/引用保护接口测试，Ruff 通过。

新增覆盖：字段省略与显式清空、不可编辑字段、权限与不存在目标、重复/并发停用、独立字段并发编辑、旧题目缺失锁字段、草稿/发布引用保护、复用锁定题目、创建作业与维护题目的两种执行顺序、部分锁定/保存失败，以及锁定后原作答和教师确认继续有效。

测试使用独立 MongoDB 27018、逐条随机数据库及归属标记清理；结束后核实无测试库并关闭该实例。未访问原开发数据库，未修改依赖或锁文件，没有真实题目导入、前端改动或真实 AI 接入。引用协议不等于跨集合事务，失败创建可能留下保守锁，限制见题库文档。

### 节点 07a 历史复验

2026-09-08，在 `codex/node-07-question-bank` 本地开发分支上，新增 34 项非数据库校验测试及 25 项题库接口测试，并适配原题库角色测试的分页响应断言。分两次实际运行：

- 非数据库测试：104 passed，223 deselected，`-W error`。
- MongoDB 集成测试：223 passed，103 deselected，`-W error`；此后仅增加 1 项读取文档示例的非数据库测试，已包含在上行 104 项中。
- Ruff 通过，依赖及锁文件未改变；3 道虚构示例通过请求模型校验。
- 集成测试使用独立 27018 实例，核对归属后清理随机测试库；确认无遗留测试库后关闭本轮实例，未访问原开发数据库。

合计 327 项测试通过，不代表 07b 编辑/停用或全业务闭环已验收。没有真实题目导入、真实 AI 调用或前端改动。

### 此前发布整理复验

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
