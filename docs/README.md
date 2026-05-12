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
| 热点内容生成 | 热点采集与缓存、LLM 清洗/分析、**品牌–热点匹配度**、脚本多轮打磨、**SSE 进度**、Seedance 视频任务 | `/hotspot`、`/video-thread`、`/generations`、`/video-tasks`、`/seedance2` | `app/services/hotspot_service`、`generation_service`、`video_thread_service`、`seedance_service` |

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

- **热点采集**：`app/services/hotspot_service/collect_hostspot.py`、`get_youtube_trends.py` 等；列表接口 `app/api/v1/hotspot.py` 通过 `get_hot_trends_cached` 减轻慢 API 压力。
- **LLM 分析 / 匹配**：`analyse_matching_degree.py`（批匹配、多维度分数与营销建议）、`analysis_cache.py`、`match_cache.py`。
- **推荐与邮件**：`recommended_hotspots.py`、`recommend_prefs.py`、`recommend_email*.py`、定时器 `recommend_email_scheduler.py`。
- **视频脚本多轮对话**：LangGraph 图定义 `video_graph/graph.py`，节点实现 `video_graph/nodes.py`，状态 `state.py`，SSE `event_bus.py` 与 `app/api/v1/video_thread.py` 的 `stream`。
- **视频生成**：`app/services/seedance_service/seedance2.py`、回调 `video_thread_service/task_callbacks.py`，任务查询 `app/api/v1/video_tasks.py`、`app/api/v1/seedance2.py`。

---

## 4. 推荐阅读顺序（给 AI / 新人）

1. `main.py` → `app/core/config.py`（环境变量与 TTL 语义）
2. `app/api/v1/__init__.py`（全站路由清单）
3. 按任务深入：`app/services/<模块>/` 与对应 `app/api/v1/<模块>.py`
4. 数据表：`db/migrations/*.sql` 与 `app/models/*.py`

---

## 5. 相关文件

- [FOLDER_REFERENCE.md](./FOLDER_REFERENCE.md) — 仓库目录与主要文件职责表
