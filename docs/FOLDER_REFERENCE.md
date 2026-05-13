# 目录与主要文件说明

本文按 **仓库根目录 → `app/` 子目录** 说明用途，便于检索「该改哪里」。路径均相对于仓库根目录 

---

## 根目录


| 路径                             | 作用                                                                          |
| ------------------------------ | --------------------------------------------------------------------------- |
| `main.py`                      | FastAPI 应用：生命周期（DB/Redis/Postgres checkpoint/定时器）、中间件、异常处理、挂载 `/api/v1` 路由。 |
| `requirements.txt`             | Python 依赖锁（运行与测试前安装）。                                                       |
| `Dockerfile`                   | 容器构建定义。                                                                     |
| `.github/workflows/deploy.yml` | CI/CD 部署工作流。                                                                |
| `db/migrations/`               | SQL 迁移脚本：`001_init.sql` 等，描述核心业务表演进；与 `app/models` 应对照阅读。                   |


---

## `app/` 总览


| 路径                | 作用                                        |
| ----------------- | ----------------------------------------- |
| `app/__init__.py` | 包初始化。                                     |
| `app/api/`        | HTTP 层：依赖注入、路由注册。                         |
| `app/core/`       | 横切能力：配置、安全、日志、统一响应、Cookie 会话、热点预加载与缓存辅助等。 |
| `app/db/`         | MySQL、Redis、Postgres（LangGraph）连接与会话工厂。   |
| `app/models/`     | SQLAlchemy ORM 模型（商户、品牌、热点、生成任务、视频线程等）。   |
| `app/schemas/`    | Pydantic 请求/响应模型，与 API 契约一致。              |
| `app/services/`   | 业务实现与外部系统集成（按子域拆分）。                       |
| `app/skills/`     | Agent / 定价相关「技能」封装与说明文档（竞品抓取、规则等）。        |


---

## `app/api/`


| 路径                              | 作用                                                            |
| ------------------------------- | ------------------------------------------------------------- |
| `app/api/deps.py`               | FastAPI 依赖：`get_current_merchant`、SSE 场景的商户解析等。               |
| `app/api/v1/__init__.py`        | 聚合注册所有 v1 子路由（auth、merchant、hotspot、pricing、products、video…）。 |
| `app/api/v1/auth.py`            | 注册、Shopify OAuth、本地注册、登录、刷新 Token、Cookie 写入与清理。               |
| `app/api/v1/merchant.py`        | 商户信息、品牌信息读/写。                                                 |
| `app/api/v1/products.py`        | Shopify 商品列表/同步相关接口。                                          |
| `app/api/v1/local_products.py`  | 本地（非 Shopify）用户商品 CRUD。                                       |
| `app/api/v1/upload.py`          | 文件上传（如素材走 OSS 的配置在 `core/config`）。                            |
| `app/api/v1/hotspot.py`         | 热点分页、推荐、品牌匹配批处理、邮件订阅计划等。                                      |
| `app/api/v1/pricing_analyze.py` | 动态调价分析请求体与 LangGraph `Command` 恢复入口。                          |
| `app/api/v1/generations.py`     | 按 `generation_id` 或 `thread_id` 查询本地视频生成任务记录。                 |
| `app/api/v1/video_thread.py`    | 视频会话：创建、恢复、状态、历史、**SSE 事件流**。                                 |
| `app/api/v1/video_tasks.py`     | 视频任务状态查询或回调相关 HTTP（与 Seedance 任务 ID 配合）。                      |
| `app/api/v1/seedance2.py`       | Seedance 2.x HTTP 封装入口。                                       |


---

## `app/core/`


| 路径                    | 作用                                                                                    |
| --------------------- | ------------------------------------------------------------------------------------- |
| `config.py`           | `pydantic-settings`：MySQL/Redis/Postgres、JWT、Shopify、LLM、竞品缓存 TTL、热点缓存 TTL、邮件、CORS 等。 |
| `security.py`         | JWT 创建与校验辅助。                                                                          |
| `auth_session.py`     | Refresh Token JTI 注册与吊销（Redis）。                                                       |
| `cookies.py`          | 设置/清除鉴权 Cookie。                                                                       |
| `responses.py`        | 统一成功包装格式。                                                                             |
| `exceptions.py`       | 业务异常与 FastAPI 异常处理器。                                                                  |
| `logger.py`           | 日志配置。                                                                                 |
| `email_sender.py`     | SMTP 发信（热点推荐邮件等，受 `EMAIL_ENABLED` 控制）。                                                |
| `hot_trends_cache.py` | 热点全量缓存加载/预加载（逻辑过期与下游 `get_hot_trends_cached` 配合）。                                     |


其他如 `tiktok_test.py` 等可能是本地实验脚本，**不作为生产主路径依赖**。

---

## `app/db/`


| 路径            | 作用                                                    |
| ------------- | ----------------------------------------------------- |
| `mysql.py`    | SQLAlchemy engine、`get_db` 会话生成器；业务主库。                |
| `redis.py`    | 异步 Redis 客户端单例与关闭。                                    |
| `postgres.py` | LangGraph AsyncPostgresSaver 连接池、checkpoint 初始化与清理任务。 |
| `base.py`     | 如有公共声明基类则在此（与 `models.base` 区分阅读）。                    |


---

## `app/models/`


| 路径                            | 作用                                   |
| ----------------------------- | ------------------------------------ |
| `base.py`                     | Declarative Base。                    |
| `merchant.py`                 | 商户账号（含 Shopify store 与本地账号类型等字段）。    |
| `brand.py`                    | 品牌维度字段，供热点匹配。                        |
| `merchant_local_product.py`   | 本地用户商品。                              |
| `hotspot.py`                  | 热点持久化结构（若启用落库）。                      |
| `generation.py`               | 单次视频/媒体生成任务记录。                       |
| `video_thread.py`             | 视频会话元数据（与 LangGraph `thread_id` 关联）。 |
| `recommend_email_schedule.py` | 热点推荐邮件排程与投递记录。                       |


---

## `app/schemas/`


| 路径                                                            | 作用                        |
| ------------------------------------------------------------- | ------------------------- |
| `auth.py` / `merchant.py` / `product.py` / `local_product.py` | 各域 API 契约。                |
| `hotspot.py`                                                  | 热点查询、匹配请求/响应、推荐与邮件计划 DTO。 |
| `pricing.py`                                                  | 定价分析相关模型。                 |
| `video_thread.py`                                             | 创建会话、恢复人工节点、状态枚举等。        |
| `generations.py`                                              | Generation 查询输出。          |
| `seedance2.py`                                                | Seedance2 API 载荷。         |
| `common.py`                                                   | 分页等通用结构。                  |


---

## `app/services/`（按业务域）

### `auth_service/`


| 路径        | 作用                                    |
| --------- | ------------------------------------- |
| `auth.py` | Shopify OAuth 发起与回调落地、本地注册、密码校验、商户认证。 |


### `merchant_service/`


| 路径                  | 作用             |
| ------------------- | -------------- |
| `merchant_brand.py` | 品牌创建/更新与按商户查询。 |


### `product_service/`


| 路径                    | 作用                            |
| --------------------- | ----------------------------- |
| `shopify_products.py` | 调用 Shopify Admin API 拉取/同步商品。 |
| `local_products.py`   | 本地商品持久化逻辑。                    |
| `catalog.py`          | 目录/列表聚合辅助（若存在）。               |


### `pricing_service/`


| 路径                    | 作用                      |
| --------------------- | ----------------------- |
| `pricing_analysis.py` | 调价分析主流程（Agent / 图执行入口）。 |


### `hotspot_service/`


| 路径                             | 作用                               |
| ------------------------------ | -------------------------------- |
| `collect_hostspot.py`          | 热点采集调度与格式化（慢路径数据源）。              |
| `get_youtube_trends.py`        | YouTube 趋势 API 封装。               |
| `analysis_cache.py`            | LLM 热点分析结果缓存。                    |
| `match_cache.py`               | 品牌–热点匹配结果缓存。                     |
| `analyse_matching_degree.py`   | 批匹配、调用 LLM 产出多维度分数与文案建议。         |
| `recommended_hotspots.py`      | 构建「推荐给商户」的热点列表。                  |
| `recommend_prefs.py`           | 用户偏好同步。                          |
| `recommend_email.py`           | 邮件内容构建与发送触发。                     |
| `recommend_email_scheduler.py` | 定时任务装配（在 `main.py` lifespan 启动）。 |


### `generation_service/`


| 路径                      | 作用                  |
| ----------------------- | ------------------- |
| `records.py`            | Generation 记录的读写。   |
| `prompt_templates.py`   | 文本生成提示词模板。          |
| `trend_video_legacy.py` | 历史/兼容视频生成路径（若仍被引用）。 |


### `video_thread_service/`


| 路径                               | 作用                                               |
| -------------------------------- | ------------------------------------------------ |
| `thread_lifecycle.py`            | 会话创建、列表、恢复、状态聚合。                                 |
| `task_callbacks.py`              | 异步视频任务完成回调处理，回写 DB / 状态机。                        |
| `video_graph/graph.py`           | LangGraph `StateGraph` 编译与 human-in-the-loop 路由。 |
| `video_graph/nodes.py`           | 各节点：意图解析、脚本规划、等待人工、修订、组装提交等。                     |
| `video_graph/state.py`           | `VideoGenerationState` 结构。                       |
| `video_graph/event_bus.py`       | SSE 与内部队列桥接。                                     |
| `video_graph/view_state.py`      | 面向前端的视图态裁剪。                                      |
| `video_graph/llm_utils.py`       | 节点内 LLM 调用辅助。                                    |
| `video_graph/payload_builder.py` | 下游视频 API 载荷拼装。                                   |


### `seedance_service/`


| 路径                 | 作用                      |
| ------------------ | ----------------------- |
| `seedance2.py`     | Seedance2 提交任务、轮询或查询状态。 |
| `legacy_client.py` | 旧版客户端兼容。                |


### `notification_service/`


| 路径                     | 作用          |
| ---------------------- | ----------- |
| `generation_status.py` | 生成状态通知相关逻辑。 |


---

## `app/skills/`


| 路径                       | 作用                                                  |
| ------------------------ | --------------------------------------------------- |
| `fetch_competitor_info/` | 竞品信息抓取 Skill（`handler.py`、`SKILL.md`）；供定价 Agent 使用。 |
| `pricing_rules/SKILL.md` | 定价规则说明文档，供模型或开发者对齐策略语义。                             |


---

## 数据迁移与表意图（`db/migrations/`）


| 文件                      | 典型内容方向          |
| ----------------------- | --------------- |
| `001_init.sql`          | 商户、品牌、商品、基础业务表。 |
| `002_generations.sql`   | 生成任务表结构扩展。      |
| `003_video_threads.sql` | 视频线程与会话相关字段。    |


阅读顺序：**迁移文件 → 对应 `app/models`**，避免 ORM 与数据库真相不一致。

---

## 小结：三大模块 → 首选入口文件

1. **用户信息设置**：`app/api/v1/auth.py`、`merchant.py`、`products.py`、`local_products.py`
2. **动态调价**：`app/api/v1/pricing_analyze.py` → `app/services/pricing_service/pricing_analysis.py`
3. **热点内容生成**：`app/api/v1/hotspot.py` + `app/services/hotspot_service/`*；视频多轮与 SSE：`app/api/v1/video_thread.py` + `app/services/video_thread_service/video_graph/*`

