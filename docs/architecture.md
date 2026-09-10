# 架构与项目来源

## 来源和重构范围

仓库源自早期 JavaScript / Express / MongoDB 答题平台，曾使用仓库名 `003`。本轮 Python 版本沿用业务探索背景，重新组织数据模型、权限和接口，不迁移旧数据，不保持旧 Express API 兼容。旧文件的删除仅表示退出当前版本，历史提交仍保留。

Python 重构已完成运行基线、测试基础、数据库驱动迁移、Web 配置防护、密码令牌加固、账号管理和单班级关系统一。此处描述的是代码中的实际实现范围，不将旧项目全部功能或后续计划计作已完成成果。未提供第三方素材授权或旧代码来源证明，后续复用历史资源前需另行核对。

## 分层

```text
HTTP -> app/api/routes -> app/services + app/core/deps
                      -> Beanie models -> PyMongo Async -> MongoDB
                      -> GradingService -> keyword placeholder
```

- `app/main.py`：FastAPI 工厂、数据库启停、CORS、静态文件和健康检查。
- `app/core/`：环境配置、密码/JWT、当前用户与角色依赖。
- `app/models.py`：持久化模型；`app/schemas.py`：请求和响应契约。
- `app/api/routes/`：业务入口。账号和班级已提取服务；其他部分仍含路由内业务逻辑，不宣称完全服务化。
- `app/services/grading.py`：可替换的评分接口，目前工厂固定返回占位实现。
- `app/services/images.py`：JPEG/PNG 分块读取、Pillow 解码/像素限制、重新编码与排他写入；静态 URL 仍公开，见 [图片说明](images.md)。

## 数据关系

| 模型 | 关系与用途 |
| --- | --- |
| User | 三种角色；学生 `class_id` 可空、单值；`token_version` 用于撤销旧令牌 |
| ClassGroup | 教师拥有多个班级；不维护重复的学生 ID 数组，成员从 User 查询 |
| Question | 简答题题干、参考答案、满分、评分标准、图片 URL 和创建人 |
| Assignment | 面向一个班级；draft/published/archived 状态，revision 条件更新计数、archived_from 归档来源；新 published 内嵌有序 QuestionSnapshot，snapshot_version=1；旧数据保留 ID 引用 |
| Submission | 一个学生对一个作业的答案、占位分数、教师确认及最终成绩；assignment_id/student_id 联合唯一索引 |

用户名称和班级码有唯一索引；`User.class_id` 是非唯一查询索引。并发入班通过针对用户单文档的条件更新保证，不依赖 MongoDB 多文档事务。归档与同时进行的其他跨集合写入尚无强事务保证。

节点 07b 新增内部 `Question.content_locked`：作业创建先原子锁题，编辑/停用检查同一文档的锁条件，旧作业引用在维护入口补查。它不是题面快照或跨集合事务；失败创建可能保留锁，不自动解锁，详见 [题库保护边界](questions.md)。

节点 09 增加草稿编辑、独立发布、归档和状态筛选，以作业状态及 revision 条件更新避免并发覆盖；发布快照和状态一次保存。截止时间统一 UTC，学生只读本班已发布作业，归档不删除提交历史；并发和时间边界见 [作业发布说明](assignment-lifecycle.md)。

## 评分边界

设计目标是学生提交后等待教师复核，再展示最终分数和简短评语。当前评分器按参考答案分隔出的要点做字符串匹配，不理解语义，也不使用模型或评分标准推理。`AI_PROVIDER` 只是预留配置，改动它不会接通模型服务。

节点 08 的 `app/services/assignments.py` 统一读取作业内容及构造角色响应；新作业展示、提交评分和教师确认均使用快照。GradingService 接收独立 QuestionSnapshot，不再以题库文档作为输入契约。旧引用兼容明确标记来源，不回填为历史快照，详见 [快照说明](assignment-snapshots.md)。

节点 10 通过联合唯一索引保证一次提交，并提供本人查询及答案校验；初始化索引前只读检查异常/重复记录，失败不自动修改旧数据，见 [提交说明](submissions.md)。节点 11 的 app/services/submissions.py 提供角色白名单、提交分页聚合及原子确认；以 pending 状态为写入条件，首次确认后锁定，不提供更正，见 [批改说明](teacher-review.md)。

当前仍在保存提交前同步调用评分接口，竞争请求可能重复评分；真实 provider 接入前需要改为先保存答案、再处理评分失败与重试。学生响应已移除 AI 草稿字段，但这不代表完整业务、模型质量或生产运行已验收，见 [路线图](status.md)。
