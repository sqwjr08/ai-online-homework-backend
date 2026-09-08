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
| POST /questions | 教师/管理员创建简答题 |
| GET /questions | 教师查询自己题库，管理员查询全部 |
| POST /assignments | 教师/管理员创建班级作业 |
| GET /assignments/my | 自己相关作业，状态隔离尚待完善 |
| GET /assignments/{assignment_id} | 按班级关系检查访问，目前仅返回题目 ID |
| POST /assignments/{assignment_id}/submissions | 学生提交答案，待教师复核 |
| GET /submissions/{submission_id} | 本人学生、班级教师或管理员访问 |
| POST /submissions/{submission_id}/confirm-grade | 班级教师/管理员确认最终成绩 |
| POST /uploads/images | 教师/管理员上传本地题目图片 |

## 接口使用要点

- 受保护请求使用 `Authorization: Bearer <token>`，缺失或无效身份返回 401；权限不足通常返回 403，具体以路由为准。
- 用户和成员分页响应为 `items / total / page / page_size`，默认每页 20，最多 100。
- 归档班级仍保留历史读取及教师确认入口，阻止新入班、新建作业和新提交；当前没有转班、退班或恢复班级接口。
- 学生尚不能取得完整作业题面；不要用向学生开放教师题库接口的方式绕过，参考答案需要隔离。
- 请求校验错误通常为 422，重复账号/跨班入班等冲突为 409。现阶段错误响应文档尚未全部统一。

建议先在 Swagger 中验证登录、账号维护和班级管理。题库到批改的完整演示需等后续节点验收，不应依据此接口清单宣称全流程可用。
