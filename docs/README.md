# 电商运营 Agent 后端 — 文档索引

本文档面向 **AI 大模型与新人开发者**，用于快速建立对仓库结构、三大业务模块与数据流的认知。更细的「目录 ↔ 职责」见 [FOLDER_REFERENCE.md](./FOLDER_REFERENCE.md)。

---

## 1. 项目定位（摘要）

本仓库实现 **Shopify 商家后台可用的电商运营 Agent 后端**（FastAPI），目标覆盖运营闭环中的 **市场洞察、动态定价建议、热点内容 / 视频生成** 等能力，并通过 **MySQL 持久化、Redis 缓存、Postgres（LangGraph Checkpoint）** 与外部 API（Shopify、搜索/竞品、趋势、LLM、视频生成）协作运转。

**三大功能模块与代码落点：**

| 模块 | 用户价值 | 主要 HTTP 前缀（均在 `API_V1_PREFIX`，默认 `/api/v1`） | 核心服务目录 |
|------|-----------|--------------------------------------------------------|---------------|
| 用户信息设置 | Shopify OAuth / 本地注册、商户资料、**品牌信息**（用于热点匹配）、商品同步或本地上传 | `/auth`、`/merchant`、`/products`、`/local-products`、`/upload` | `app/services/auth_service`、`merchant_service`、`product_service` |
| 动态调价 | 基于竞品与规则的分析，生成调价建议（可对接 Agent 工作流） | `/pricing-analyze` | `app/services/pricing_service`、`app/skills/fetch_competitor_info`、`app/skills/pricing_rules` |
| 热点内容生成 | 热点采集与缓存、LLM 清洗/分析、**品牌–热点匹配度**、脚本多轮打磨、**SSE 进度**、Seedance 视频任务 | `/hotspot`（含 `POST /hotspot/tiktok/hashtag`）、`/own-hotspot`、`/tiktok`、`/video-thread`、`/generations`、`/video-tasks`、`/seedance2` | `app/services/hotspot_service`、`generation_service`、`video_thread_service`、`seedance_service` |

---

## 2. 系统架构（与代码对应）

```
Shopify / 独立前端
        │
        ▼
   main.py（FastAPI 应用入口）
        │
        ├── app/api/v1/*          REST API 路由层
        ├── app/services/*        业务与编排（含 LangGraph、外部 API 调用）
        ├── app/models + app/db   持久化与连接
        └── app/core              配置、鉴权、缓存策略、异常与响应格式
```

- **入口与生命周期**：`main.py` 注册路由、CORS、异常处理；在 `lifespan` 中初始化 MySQL 表、Redis、Postgres Checkpointer、可选热点预加载、热点推荐邮件定时任务。
- **LangGraph**：视频脚本/生成流程的状态与 Checkpointer 存 **PostgreSQL**（见 `app/db/postgres.py` 与 `app/services/video_thread_service/video_graph/`）。
- **热点加速**：逻辑 TTL、分析缓存、匹配缓存等由 `app/core/hot_trends_cache.py`、`app/services/hotspot_service/*_cache.py` 与 `app/core/config.py` 中的 TTL 配置共同约束。

---

## 3. 模块功能详解（与产品文档对齐）

### 3.1 用户信息设置

- **注册登录**：Shopify 商户走 OAuth 注册链路（`app/api/v1/auth.py` → `app/services/auth_service/auth.py`）；非 Shopify **本地用户**走 `localRegister` 等接口，`account_type` 区分数据源。
- **品牌信息**：名称、核心价值、主营商品、调性、受众等，对应 `app/api/v1/merchant.py` 与 `app/services/merchant_service/merchant_brand.py`、`app/models/brand.py`，供热点匹配 LLM 使用。
- **商品信息**：Shopify 商品同步（`app/services/product_service/shopify_products.py`、`app/api/v1/products.py`）；本地用户商品（`local_products.py`、`merchant_local_product` 模型）。

### 3.2 动态调价

- **分析入口**：`app/api/v1/pricing_analyze.py` 调用 `app/services/pricing_service/pricing_analysis.py`（及 Volcengine / LangGraph `Command` 等，视实现版本而定）。
- **竞品与规则技能**：`app/skills/fetch_competitor_info`（竞品信息拉取与缓存 TTL 相关配置见 `config.COMPETITOR_CACHE_TTL`）、`app/skills/pricing_rules`（定价规则说明/技能文档）。

### 3.3 热点内容生成

- **YouTube 等全量缓存热点**：`collect_hostspot.py` + `get_youtube_trends.py`；分页列表 `POST /hotspot/hot-trends`（`hotspot.py`）经 `get_hot_trends_cached` 减轻慢数据源与 LLM 压力。
- **TikTok Hashtag 热点**：`get_tiktok_trends.py`，入口 `POST /hotspot/tiktok/hashtag`（与同文件中的 YouTube 列表分离）；另有一套面向「搜索 / 主页 / hashtag」视频的通用 TikTok HTTP：`app/api/v1/tiktok.py`。
- **商户自有热点**：`app/api/v1/own_hotspot.py`（`/own-hotspot`）与 `hotspot_service/own_hotspot.py`，与全局 YouTube 缓存隔离、按商户维度存储与推荐。
- **LLM 分析 / 匹配**：`analyse_matching_degree.py`（批匹配、多维度分数与营销建议）；热点分析缓存 `analysis_cache.py`、匹配缓存 `match_cache.py`。热点采集侧的 LLM 清洗与 `analyze_collect_trend_items_async` 等均可能写入用量日志（见下）。
- **推荐与邮件**：`recommended_hotspots.py`、`recommend_prefs.py`、`recommend_email.py`、定时器 `recommend_email_scheduler.py`。
- **视频脚本多轮对话**：LangGraph `video_graph/graph.py`、`nodes.py`、`state.py`；SSE `event_bus.py` 与 `app/api/v1/video_thread.py` 的 `stream`。
- **视频生成**：`seedance_service/seedance2.py`、回调 `video_thread_service/task_callbacks.py`；HTTP `app/api/v1/video_tasks.py`、`app/api/v1/seedance2.py`。

### 3.4 日志与用量成本记录

- **应用日志**：`app/core/logger.py` 中 `configure_logging()` 在进程启动与 `lifespan` 内调用（见 `main.py`）。控制台与可选文件日志使用 **北京时间**；文件落盘为 `logs/YYYY-MM-DD/YYYY-MM-DD-NN.log` 形式，单日按 `LOG_FILE_MAX_BYTES` 递增序号轮转。可通过 `LOG_FILE_ENABLED`、`LOG_FILE_DIR`、`LOG_LEVEL` 等环境变量控制（定义见 `app/core/config.py`）。
- **成本 / token 专用日志**：`app/core/cost_log.py` 写入独立 logger **`cost`**（不汇入 root）：LLM（`log_llm_usage`，用于热点采集、品牌匹配、`video_graph/llm_utils` 等）与 Seedance（`try_log_seedance_usage`，成功任务且带回 `usage` 时；Redis NX 防抖重复记账）。启用文件日志时，cost 条目写入 **`logs/cost/`** 下同名按日轮转规则，并经 **队列 + 后台写盘线程**（`LOG_COST_QUEUE_MAXSIZE`）降低并发写锁竞争。应用在 `lifespan` 结束时调用 **`shutdown_cost_queue_logging()`** 排空队列。

---

## 4. 推荐阅读顺序（给 AI / 新人）

1. `main.py` → `app/core/config.py`（环境变量与 TTL 语义）
2. `app/api/v1/__init__.py`（全站路由清单）
3. 按任务深入：`app/services/<模块>/` 与对应 `app/api/v1/<模块>.py`
4. 数据表：`db/migrations/*.sql` 与 `app/models/*.py`
5. 日志与用量：`app/core/logger.py`、`app/core/cost_log.py`

---

## 5. 相关文件

- [FOLDER_REFERENCE.md](./FOLDER_REFERENCE.md) — 仓库目录与主要文件职责表
