"""
Pricing analysis service — LangGraph 线性编排。

Nodes:
  fetch_products → fetch_competitors → load_rules → llm_analyze → enrich

Prompt template is now inlined (pricing_agent_prompt skill has been removed).
Rules are loaded from the dedicated `pricing_rules` skill.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Literal, TypedDict

from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import SecretStr

from app.core.config import get_settings
from app.models import Merchant
from app.schemas.pricing import PricingAnalysisResponseLLM
from app.services.product_service import fetch_product, update_product_prices
from app.skills import load_skill
from app.skills.fetch_competitor_info.handler import fetch_competitor_info

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Singleton LLM instance
# ---------------------------------------------------------------------------

_llm_instance: ChatOpenAI | None = None


def _get_llm() -> ChatOpenAI:
    global _llm_instance
    if _llm_instance is not None:
        return _llm_instance

    api_key = settings.VOLCENGINE_API_KEY
    model = settings.AGENT_MODEL_NAME
    base_url = settings.VOLCENGINE_BASE_URL

    if not api_key:
        raise ValueError("Missing VOLCENGINE_API_KEY — set it in .env")
    if not model:
        raise ValueError("Missing AGENT_MODEL_NAME — set it in .env")

    _llm_instance = ChatOpenAI(
        model=model,
        api_key=SecretStr(api_key),
        base_url=base_url,
        timeout=180,
        max_retries=3,
    )
    return _llm_instance


# ---------------------------------------------------------------------------
# Step 1: fetch product info from Shopify
# ---------------------------------------------------------------------------

async def get_products_info(merchant: Merchant, product_ids: List[int]):
    """Fetch product details for the given IDs from Shopify in parallel."""
    tasks = [fetch_product(merchant, pid) for pid in product_ids]
    return list(await asyncio.gather(*tasks))


# ---------------------------------------------------------------------------
# Context builder (consumes standardized competitor data)
# ---------------------------------------------------------------------------

def _build_products_context(products, competitor_data) -> str:
    """Render product + competitor info as text for the LLM.

    ``competitor_data`` uses the **standardized** format produced by
    ``fetch_competitor_info``: each value is a list of dicts with keys
    ``title, price, store, url, old_price, rating, reviews``.
    """
    sections: list[str] = []
    for product in products:
        stock = (
            f"In Stock: {product.inventory} units"
            if product.inventory > 20
            else f"Low Stock: {product.inventory} units"
        )

        competitors = competitor_data.get(product.name, [])
        if competitors:
            prices = [c["price"] for c in competitors]
            avg_price = sum(prices) / len(prices)
            price_range = f"${min(prices):.2f}–${max(prices):.2f}"
            comp_summary = f"Competitors {price_range}, avg ${avg_price:.2f}"
            comp_lines = "\n".join(
                f"    - {c['title']}: ${c['price']:.2f} ({c['store']})"
                + (f" old_price=${c['old_price']:.2f}" if c.get("old_price") else "")
                + (f" rating={c['rating']}" if c.get("rating") else "")
                + (f" reviews={c['reviews']}" if c.get("reviews") else "")
                for c in competitors
            )
        else:
            comp_summary = "No competitor data available"
            comp_lines = "    (none)"

        sections.append(
            f"Product: {product.name}\n"
            f"  Product ID: {product.product_id}\n"
            f"  Current Price: ${product.price:.2f}\n"
            f"  Stock Status: {stock}\n"
            f"  Competitor Summary: {comp_summary}\n"
            f"  Competitor Details:\n{comp_lines}"
        )
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Post-process: inject competitor_info_url
# ---------------------------------------------------------------------------

def _enrich_with_urls(
    llm_result: Dict[str, Any],
    competitor_data: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Add ``competitor_info_url`` to each analysis item from raw competitor data."""
    items = llm_result.get("pricing_analysis", [])
    for item in items:
        name = item.get("product_name", "")
        competitors = competitor_data.get(name, [])
        item["competitor_info_url"] = [
            c["url"] for c in competitors if c.get("url")
        ]
    return llm_result


# ---------------------------------------------------------------------------
# LangGraph state & nodes
# ---------------------------------------------------------------------------


class PricingGraphState(TypedDict, total=False):
    """支持 human-in-the-loop 的完整状态机状态。

    - fetch_products: 从 Shopify + Redis 缓存加载商品信息 → state["products"]
    - fetch_competitors: 优先 SerpAPI (Amazon) + Redis 缓存 → state["competitor_data"]
    - load_rules: 从 pricing_rules skill 加载用户可自定义的调价规则
    - llm_analyze: rules + 竞品信息作为 system_prompt，商品上下文作为 human prompt，chain = prompt | llm | parser，输出经 Pydantic JSON schema 校验
    - enrich: 补充 competitor_info_url（LLM 输出中无此字段）
    - human_review: 触发 interrupt 暂停，持久化 snapshot，等待外部 callback（Approve/Reject/Regenerate）
    - conditional_edges: 根据 Command 路由 — Approve→apply, Reject→cancel, Regenerate→llm_analyze（携带 feedback）
    """

    # 输入（merchant_info 是可序列化的 dict，避免 SQLAlchemy ORM 对象无法被 checkpointer 序列化）
    merchant_info: Dict[str, Any]
    product_ids: List[int]
    thread_id: str  # 用于 checkpointer 标识会话

    # 节点填充
    products: List[Any]
    competitor_data: Dict[str, List[Dict[str, Any]]]
    pricing_rules: str
    result: Dict[str, Any]  # LLM + enrich 后的完整分析结果

    # Human-in-the-loop 支持
    messages: List[Dict[str, Any]]  # 对话历史（可选）
    feedback: str | None  # 用户在 Regenerate 时提供的修正意见
    command: Literal["approve", "reject", "regenerate"] | None


def _restore_merchant(info: Dict[str, Any]):
    """从可序列化的 dict 重建一个轻量代理对象（SimpleNamespace），
    提供 fetch_product 所需的 .shopify_domain / .shopify_access_token 等属性。
    """
    from types import SimpleNamespace
    return SimpleNamespace(**info)


async def _node_fetch_products(state: PricingGraphState) -> dict[str, Any]:
    """1. fetch_products：搜索商家自身的商品信息，可以从redis中获取，填到state的products里面。

    当前实现从 Shopify API 获取，支持后续扩展 Redis 缓存（类似 competitor 的机制）。
    """
    merchant = _restore_merchant(state["merchant_info"])
    product_ids = state["product_ids"]
    logger.info("Graph: fetching product info for %d products", len(product_ids))
    products = await get_products_info(merchant, product_ids)
    return {"products": products}


async def _node_fetch_competitors(state: PricingGraphState) -> dict[str, Any]:
    """2. fetch_competitors：优先从serpAPI中搜索在亚马逊平台的竟品信息，这里也可以从redis中获取, 填到state的competitor_data里面。

    handler.py 已实现 Redis 优先 + SerpApi(Amazon) 主搜 + Tavily 兜底，完美符合要求。
    """
    products = state["products"]
    product_names = [p.name for p in products]
    logger.info("Graph: fetching competitor info for: %s", product_names)
    competitor_data = await fetch_competitor_info(product_names)
    return {"competitor_data": competitor_data}


async def _node_load_rules(state: PricingGraphState) -> dict[str, Any]:
    """3. load_rules：从skill中补充调价的规则（已按用户描述精确更新 SKILL.md，支持用户自定义）。"""
    logger.info("Graph: loading pricing rules from skills")
    rules = await asyncio.to_thread(load_skill, "pricing_rules")
    return {"pricing_rules": rules}


async def _node_llm_analyze(state: PricingGraphState) -> dict[str, Any]:
    """4. llm_analyze:将rules+竟品信息组装到prompt 里面,作为system_prompt,将商品信息作为humanprompt，让大模型解析，再将模型输出结果做json schema校验。
    chain的链条是prompt|_get_llm()|parser。得到模型输出的json结果。

    支持 feedback（Regenerate 时用户修正意见如“降价幅度太小”会附加到 prompt）。
    """
    logger.info("Graph: invoking LLM for analysis (with feedback: %s)", bool(state.get("feedback")))

    parser = JsonOutputParser(pydantic_object=PricingAnalysisResponseLLM)

    feedback = state.get("feedback") or ""
    feedback_section = (
        f"\n\n## 用户反馈（必须严格考虑）\n{feedback}\n"
        if feedback
        else ""
    )

    # 严格按要求组装：rules + 竞品作为 system，商品作为 human
    system_prompt = (
        "You are an expert e-commerce dynamic pricing analyst.\n\n"
        "## Business Rules\n{pricing_rules}\n\n"
        "You MUST respond with ONLY valid JSON matching the schema below. "
        "Do NOT include any text outside the JSON object.\n\n"
        "{format_instructions}"
    ) + feedback_section

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        (
            "human",
            "Analyze the following products and provide pricing "
            "recommendations for each one according to the rules:\n\n"
            "{products_info}\n\n"
            "Return ONLY the JSON object with your pricing_analysis array.",
        ),
    ])

    chain = prompt | _get_llm() | parser

    products_context = _build_products_context(
        state["products"],
        state["competitor_data"],
    )

    result = await chain.ainvoke({
        "pricing_rules": state["pricing_rules"],
        "format_instructions": parser.get_format_instructions(),
        "products_info": products_context,
    })
    return {"result": result}


def _node_enrich(state: PricingGraphState) -> dict[str, Any]:
    """5. enrich节点：用于调整输出结果的。给用户返回的json需要包含竟品链接url，大模型的输出不会包括这个，需要后续补充。"""
    enriched = _enrich_with_urls(state["result"], state["competitor_data"])
    return {"result": enriched}


def _human_review_node(state: PricingGraphState) -> dict[str, Any]:
    """6. human_review节点：
    流程运行至此节点前会触发 `interrupt` 机制。系统会将当前的state snapshot持久化并暂停执行，
    等待外部指令。此时，工作流处于“挂起”状态，不占用计算资源。
    """
    logger.info("Graph: human_review - triggering interrupt for review")

    # 首次执行时抛出 GraphInterrupt 暂停；resume 后返回用户决策
    user_decision = interrupt({
        "analysis": state.get("result"),
        "merchant_id": state["merchant_info"].get("id"),
        "thread_id": state.get("thread_id"),
    })

    # resume 后：user_decision 是 Command(resume=...) 中传入的值
    if isinstance(user_decision, dict):
        cmd = user_decision.get("command", user_decision)
        feedback = user_decision.get("feedback")
    else:
        cmd = user_decision
        feedback = None

    return {"command": cmd, "feedback": feedback}


async def _node_apply(state: PricingGraphState) -> dict[str, Any]:
    """Approve 后执行：调用 Shopify GraphQL API 将 LLM 推荐价格写入 Shopify。"""
    from datetime import datetime, timezone

    logger.info("Graph: apply - updating prices on Shopify")

    merchant = _restore_merchant(state["merchant_info"])
    products = state.get("products", [])
    result = state.get("result", {})
    analysis_items = result.get("pricing_analysis", [])

    product_map = {p.name: p for p in products}

    applied: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for item in analysis_items:
        product_name = item.get("product_name", "")
        recommended_price = item.get("recommended_price")
        current_price = item.get("current_price")
        product = product_map.get(product_name)

        if not product or recommended_price is None:
            errors.append({"product_name": product_name, "error": "product_not_found_or_no_price"})
            continue

        # 推荐价格和当前价格一致时跳过
        if current_price is not None and abs(recommended_price - current_price) < 0.01:
            logger.info("Skipping %s: recommended price equals current price", product_name)
            applied.append({
                "product_name": product_name,
                "product_id": product.product_id,
                "old_price": current_price,
                "new_price": recommended_price,
                "skipped": True,
            })
            continue

        try:
            compare_at = current_price if (current_price and recommended_price < current_price) else None
            await update_product_prices(
                merchant,
                product.product_id,
                recommended_price,
                compare_at_price=compare_at,
            )
            applied.append({
                "product_name": product_name,
                "product_id": product.product_id,
                "old_price": current_price,
                "new_price": recommended_price,
                "skipped": False,
            })
            logger.info("Updated %s (ID %d): $%.2f → $%.2f", product_name, product.product_id, current_price or 0, recommended_price)
        except Exception as exc:
            logger.error("Failed to update price for %s (ID %d): %s", product_name, product.product_id, exc)
            errors.append({"product_name": product_name, "product_id": product.product_id, "error": str(exc)})

    result["status"] = "approved_and_applied"
    result["applied_products"] = applied
    if errors:
        result["apply_errors"] = errors
    result["applied_at"] = datetime.now(timezone.utc).isoformat()

    return {"result": result, "command": "approve"}


def _node_cancel(state: PricingGraphState) -> dict[str, Any]:
    """Reject 后：记录日志并通知用户任务已取消。"""
    logger.info("Graph: cancel - task rejected by user")
    result = state.get("result", {})
    result["status"] = "cancelled_by_user"
    return {"result": result, "command": "reject"}


def _route_after_review(state: PricingGraphState) -> str:
    """7. conditional_edges（条件分支路由）
    当用户通过回调接口做出决策后，系统根据 `Command` 中的指令动态路由至不同分支：
    - Approve（通过）：流转至 `apply` 节点
    - Reject（拒绝）：流转至 `cancel` 节点
    - Regenerate（重算）：流转回`llm_analyze` 节点，携带用户的修正意见
    """
    command = state.get("command")
    if command == "approve":
        return "apply"
    elif command == "reject":
        return "cancel"
    elif command == "regenerate":
        return "llm_analyze"
    # 默认结束或 human_review（安全兜底）
    return "enrich"


def _build_pricing_graph():
    """构建完整支持 interrupt + human review + conditional routing 的状态机。"""
    checkpointer = MemorySaver()

    workflow = StateGraph(PricingGraphState)

    # 添加所有节点
    workflow.add_node("fetch_products", _node_fetch_products)
    workflow.add_node("fetch_competitors", _node_fetch_competitors)
    workflow.add_node("load_rules", _node_load_rules)
    workflow.add_node("llm_analyze", _node_llm_analyze)
    workflow.add_node("enrich", _node_enrich)
    workflow.add_node("human_review", _human_review_node)
    workflow.add_node("apply", _node_apply)
    workflow.add_node("cancel", _node_cancel)

    # 线性主流程直到 human review
    workflow.add_edge(START, "fetch_products")
    workflow.add_edge("fetch_products", "fetch_competitors")
    workflow.add_edge("fetch_competitors", "load_rules")
    workflow.add_edge("load_rules", "llm_analyze")
    workflow.add_edge("llm_analyze", "enrich")
    workflow.add_edge("enrich", "human_review")

    # Conditional routing from human_review
    workflow.add_conditional_edges(
        "human_review",
        _route_after_review,
        {
            "apply": "apply",
            "cancel": "cancel",
            "llm_analyze": "llm_analyze",  # Regenerate 带 feedback 回到分析
            "enrich": END,  # 兜底
        },
    )

    workflow.add_edge("apply", END)
    workflow.add_edge("cancel", END)
    # llm_analyze -> enrich -> human_review 由 conditional 闭环处理

    return workflow.compile(checkpointer=checkpointer)


_pricing_graph = _build_pricing_graph()


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


async def run_pricing_analysis(
    merchant: Merchant,
    product_ids: List[int],
    thread_id: str | None = None,
    command: Command | None = None,
    feedback: str | None = None,
) -> Dict[str, Any]:
    """支持 human-in-the-loop 的入口函数。

    - 首次调用：thread_id=None，执行到 human_review 触发 interrupt。
    - 后续 callback：传入 command=Command(resume=...) 或具体指令，系统根据 Command 路由。
    """
    if not thread_id:
        thread_id = f"pricing-{merchant.id}-{hash(str(product_ids)) % 10000}"

    config = {"configurable": {"thread_id": thread_id}}

    merchant_info: Dict[str, Any] = {
        "id": merchant.id,
        "name": merchant.name,
        "email": merchant.email,
        "shopify_domain": merchant.shopify_domain,
        "shopify_access_token": merchant.shopify_access_token,
        "shopify_store_id": merchant.shopify_store_id,
    }

    input_state: Dict[str, Any] = {
        "merchant_info": merchant_info,
        "product_ids": product_ids,
        "thread_id": thread_id,
    }
    if feedback:
        input_state["feedback"] = feedback

    logger.info(
        "Starting pricing graph for merchant %s (ID: %s), thread=%s, command=%s",
        merchant.name,
        merchant.id,
        thread_id,
        getattr(command, "resume", None) if command else None,
    )

    try:
        if command:
            final = await _pricing_graph.ainvoke(command, config=config)
        else:
            final = await _pricing_graph.ainvoke(input_state, config=config)

        logger.info("Pricing graph completed for merchant %s, thread=%s", merchant.name, thread_id)
        result = final.get("result", final)
        return {"thread_id": thread_id, **result}

    except GraphInterrupt as exc:
        # interrupt 触发时正常返回当前分析结果 + thread_id，前端据此展示并等待用户决策
        logger.info("Pricing graph interrupted (awaiting review) for thread=%s", thread_id)
        interrupt_data = {}
        if exc.args and exc.args[0]:
            first_interrupt = exc.args[0][0]
            interrupt_data = getattr(first_interrupt, "value", {}) or {}
        analysis = interrupt_data.get("analysis", {})
        return {"thread_id": thread_id, "status": "awaiting_review", **analysis}
