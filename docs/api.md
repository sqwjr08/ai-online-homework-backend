# 接口入口与权限

统一前缀 `/api/v1`。启动后以 `/docs` 和 `/openapi.json` 的模型为准，下表表示接口存在，不表示端到端流程已验收。

| 方法与路径 | 使用者与用途 |
| --- | --- |
| POST /auth/register | 未登录学生注册，不允许注册教师或管理员 |
| POST /auth/login | 用户名和密码登录，返回 Bearer token |
| GET /auth/me | 当前用户 |
| POST /auth/logout | 无鉴权的提示接口；客户端清 token，不撤销单个 JWT |
| POST /users | 管理员创建教师/学生 |
| GET /users | 管理员分页查询，可按角色、状态、用户名筛选 |
| PATCH /users/{user_id}/status | 管理员启停用教师/学生并撤销旧令牌 |
| POST /users/{user_id}/reset-password | 管理员重置教师/学生密码并撤销旧令牌 |
| POST /classes | 教师/管理员创建班级，归属启用的教师 |
| POST /classes/join | 学生用班级码加入；同班重复请求幂等，已属其他班返回 409 |
| GET /classes/my | 自己相关班级，可按启用状态筛选 |
| GET /classes/{class_id}/members | 班级教师/管理员查询成员，分页且可按账号状态筛选 |
| POST /classes/{class_id}/archive | 班级教师/管理员归档，不清除成员或历史数据 |
| POST /questions | 教师/管理员创建简答题，校验文本、满分及评分标准格式 |
| GET /questions | 教师查询自己题库、管理员跨教师查询，题干搜索及分页，默认仅启用 |
| GET /questions/{question_id} | 教师读取自己题目、管理员读取任意题目，含已停用题目 |
| PATCH /questions/{question_id} | 教师/管理员部分编辑未停用、未引用且未锁定的题目 |
| POST /questions/{question_id}/disable | 教师/管理员停用未引用、未锁定题目，重复请求幂等 |
| POST /assignments | 教师/管理员创建作业；published 保存完整快照，draft 仅预览 |
| PATCH /assignments/{assignment_id} | 班级教师/管理员部分编辑草稿，不能改班级和状态 |
| POST /assignments/{assignment_id}/publish | 班级教师/管理员发布草稿，原子保存快照与状态 |
| POST /assignments/{assignment_id}/archive | 班级教师/管理员归档草稿或已发布作业，保留历史提交 |
| GET /assignments/my | 自己相关作业，可按 status 筛选，学生仅可见本班已发布；按角色返回题面 |
| GET /assignments/{assignment_id} | 按班级关系和角色读取快照题面，学生不含参考答案或 rubric |
| POST /assignments/{assignment_id}/submissions | 学生提交答案，待教师复核 |
| GET /assignments/{assignment_id}/submissions | 班级教师/管理员分页查询提交、按状态筛选，含作业整体批改进度 |
| GET /assignments/{assignment_id}/submissions/my | 学生按作业找回本人提交，归档后仍可查询；无本人记录 404 |
| GET /submissions/{submission_id} | 本人学生、班级教师或管理员访问 |
| POST /submissions/{submission_id}/confirm-grade | 班级教师/管理员首次确认并锁定成绩；重复/并发确认 409，无更正接口 |
| POST /uploads/images | 教师/管理员上传 JPEG/PNG，实际解码校验、字节/像素限制及重新编码；返回公开静态 URL |

## 接口使用要点

- 受保护请求使用 `Authorization: Bearer <token>`，缺失或无效身份返回 401；权限不足通常返回 403，具体以路由为准。
- 用户、成员和题库分页响应为 `items / total / page / page_size`，默认每页 20，最多 100。题库由旧数组改为分页对象，见 [题库契约及示例](questions.md)。
- 作业创建先锁定题目内容再保存，题目并发停用可导致 409；已引用或锁定题目禁止编辑/停用，即使操作者是管理员。失败后可能保留锁，详见题库文档。
- 归档班级仍保留历史读取及教师确认入口，阻止新入班、新建作业和新提交；当前没有转班、退班或恢复班级接口。
- 作业响应新增 view、question_source；题目增加题号、题干、图片、满分，学生白名单排除参考答案和 rubric，见 [作业快照契约](assignment-snapshots.md)。学生不能访问教师题库或提交草稿。
- 请求校验错误通常为 422，重复账号/跨班入班等冲突为 409。现阶段错误响应文档尚未全部统一。
- 作业发布后不允许编辑或延期；归档后学生不再看到作业，仍可按提交 ID 读取自己的历史结果。截止时间必须带时区，当前时间达到截止时即拒绝提交；详见 [作业状态与截止契约](assignment-lifecycle.md)。
- 每名学生对每份作业仅可提交一次，联合唯一索引防止并发重复，重复返回 409；答案必须覆盖全部题目、非空白且每题最多 20,000 字符，不接受客户端评分或身份字段。见 [提交契约](submissions.md)。
- 教师提交列表分页默认 20、最多 100，status 可选 pending_teacher_review/confirmed，progress 不受筛选影响。成绩逐题不超过快照满分，最多两位小数，总分后端计算；学生响应以 view=student 白名单排除所有 AI 草稿字段，见 [批改契约](teacher-review.md)。

节点 13 已在真实本机 HTTP 上串联账号、建班、出题、发布、答题、批改和查分。操作顺序、样例、错误体格式和范围边界见 [后端验收](backend-acceptance.md)；此接口清单不代表前端、真实 AI 或生产部署已验收。

图片上传仅支持 image/jpeg、image/png；输入/输出各最多 5 MiB、单边 10,000 像素、总像素 2,000 万。非法内容 400、超限 413、不支持类型 415、存储不可用 503。成功响应保留 url；静态读取不要求登录，见 [图片契约](images.md)。
