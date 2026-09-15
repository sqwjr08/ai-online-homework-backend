# 后端接口完整验收：节点 13

## 验收结论

2026-09-09，本地通过真实 TCP/HTTP 跑通已实现业务：管理员创建教师/学生、学生注册、登录、建班/入班、上传图片、出题、编辑草稿、发布、答题、教师批改、学生查分及归档历史读取。最终成绩为预期 **12.5 / 15 分**。

新增 2 项验收测试通过，另运行 70 项直接关联回归通过；Ruff 通过。当前收集 490 项测试，本轮不是全量回归。验收仅代表当前同步关键词占位评分版本的后端流程，不是前端、真实 AI 质量、性能、安全审计或生产就绪证明。

## 一条命令复现

从仓库根目录准备环境：

```powershell
uv sync --frozen --extra dev --python 3.13
```

需要独立测试 MongoDB。先确认 27018 未被其他实例使用，再在独立终端执行：

```powershell
New-Item -ItemType Directory -Force .verification/mongo-data | Out-Null
mongod --dbpath .verification/mongo-data --bind_ip 127.0.0.1 --port 27018
```

已有专用测试实例时不要重复启动，不要指向开发或生产库。在另一终端执行：

```powershell
$env:TEST_MONGODB_URI = 'mongodb://127.0.0.1:27018'
uv run --frozen --no-sync pytest -q -W error tests/integration/test_backend_acceptance.py
```

预期 `2 passed`，不需手工准备真实题目或图片。测试会暂时启动 Uvicorn，监听 127.0.0.1 自动分配的空闲端口，不占用固定 8000；HTTPX 使用真正网络连接，禁用环境代理，不使用 ASGITransport。

测试 fixture 在数据库归属标记保护下初始化应用生命周期；Uvicorn 不重复初始化数据库。仅测试管理员需要一次数据库初始化写入，其余业务数据全部经 API 创建，不覆盖鉴权或评分依赖。每条测试独立随机数据库、JWT 密钥、临时上传目录，不加载开发 .env。

结束自动关闭临时 HTTP 服务及连接，核对归属后清理各自测试库。自行启动的测试 MongoDB 可在其终端 Ctrl+C 关闭，不按进程名称停止其他实例。强制中断可能留下临时库，不按前缀批量删除，清理规则见 [测试说明](testing.md)。

## 实际执行顺序

所有业务接口前缀为 `/api/v1`，需要身份时使用 `Authorization: Bearer <access_token>`。以下也是手工查看 Swagger 时的操作顺序；手工演示需独立测试环境和已配置的本地管理员，不能把测试的初始化方式当作生产管理员接口。

| 阶段 | 请求与检查 | 预期 |
| --- | --- | --- |
| 账号 | 管理员登录；POST /users 创建两名教师及学生 B；POST /auth/register 注册学生 A；分别登录 | 创建 201、登录 200 |
| 班级 | 教师 POST /classes 创建 A/B 班；学生分别 POST /classes/join | 每个学生仅属于一个班 |
| 成员 | GET /auth/me、GET /classes/{id}/members | 学生 A 的班级正确，教师仅看到本班成员 |
| 图片 | 教师 POST /uploads/images 上传生成 PNG；匿名读取返回 URL | 上传、读取均 200 |
| 题库 | POST /questions 创建两题；PATCH 编辑第一题题干；GET /questions?q=database | 满分分别 10、5，图片 URL 可引用，搜索命中 |
| 草稿 | POST /assignments，status=draft；PATCH 修改标题 | 学生列表无草稿，详情/提交草稿 404 |
| 发布 | POST /assignments/{id}/publish | snapshot 来源，教师保留答案/规则，学生无这些字段 |
| 作答 | 学生 A POST /assignments/{id}/submissions | 201、pending_teacher_review；分数隐藏，无 AI 字段 |
| 待批改 | 教师 GET /assignments/{id}/submissions?status=pending_teacher_review | 已提交 1、待确认 1、已确认 0；14a 起 ai_status=pending、草稿为空，教师直接人工评分 |
| 确认 | 教师 POST /submissions/{id}/confirm-grade，每题 8.5、4 分 | confirmed，总分 12.5，首次确认后锁定 |
| 查分 | 学生 GET /submissions/{id} 和 /assignments/{id}/submissions/my | 两个入口均 12.5 分、教师评语，无 AI 字段 |
| 归档 | 教师归档作业和班级，再读取本人提交与图片 | 作业详情 404，历史成绩及公开图片仍可读取 |

两道题为虚构示例：数据库索引、事务的作用。测试参考答案/规则带 SECRET 标记以发现意外泄露，不是真实教学素材；占位评分不作为成绩正确性的判断依据，最终成绩由教师请求明确指定。

## 请求样例

接口会返回实际 ID，后续使用响应中的 ID；下列 `CLASS_ID / QUESTION_ID_*` 为说明性占位符，不能直接发送。

创建草稿：

```json
{
  "title": "数据库基础示例作业",
  "class_id": "CLASS_ID",
  "status": "draft",
  "due_at": null,
  "questions": [{"question_id": "QUESTION_ID_1"}, {"question_id": "QUESTION_ID_2"}]
}
```

正式发布使用空请求体 POST /assignments/{id}/publish。自动验收设置的是当前 UTC 时间后一小时截止，不依赖固定日期；手工样例的 null 表示无截止时间。

学生提交：

```json
{
  "answers": [
    {"question_id": "QUESTION_ID_1", "answer_text": "索引有助于减少查询扫描的数据量。"},
    {"question_id": "QUESTION_ID_2", "answer_text": "事务将一组操作作为一个整体处理。"}
  ]
}
```

教师确认：

```json
{
  "grades": [
    {"question_id": "QUESTION_ID_1", "final_score": 8.5, "final_comment": "概念正确，可补充代价。"},
    {"question_id": "QUESTION_ID_2", "final_score": 4, "final_comment": "概念正确，可补充例子。"}
  ]
}
```

## 权限与错误契约

验收同时核对：学生 A 再加入 B 班 409；学生 B 无法读取/提交 A 班作业 403；其他教师无法读取题目、作业、提交列表或批改 403；学生不能读取教师题库/提交列表或自行确认成绩 403；读取他人提交 403，按作业查找本人的不存在记录 404；重复提交 409；教师和管理员重复确认均 409，成绩不覆盖。

常见错误为 `{ "detail": "说明" }`；请求模型错误 422 的 detail 是字段错误数组，不能一律按字符串读取。未登录 401，权限不足 403；为隐藏草稿/归档作业时返回 404。输入题目匹配、分值范围、截止等业务错误使用 400，状态/并发/数据修复冲突使用 409；图片另有 413/415/503，详见 [图片契约](images.md)。本轮补充关键登录、作业、提交入口的 OpenAPI 错误描述，不改变既有错误返回逻辑。

## 尚未验收的部分

- 真实 AI 尚未接入；节点 14 已完成后台执行、有限重试及 [串联验收](ai-execution-acceptance.md)，但不是实际模型故障或生产可靠性验收；跨集合归档竞态限制不变。
- 图片静态 URL 是公开资源，归档不撤销访问；没有网关限流、请求体入口限额和生产配额的验收。
- 没有前端页面、浏览器 Swagger CDN 完整渲染、生产 TLS、压测、备份恢复或多平台验收。
- 无成绩更正、未提交名单、课程体系或旧数据库迁移；这些不是本轮自动扩展的功能。

2026-09-11 的 14a 已重跑本文件两项真实 HTTP 测试，包含在当轮 49 项集成测试中；14b 又在 62 项精简回归中重跑。以上 72 项为节点 13 历史记录，不是本轮结果。14d 另以 27 项精简测试验收后台执行串联，本文件旧测试未在 14d 重跑；下一节点 15 讨论真实 provider。
