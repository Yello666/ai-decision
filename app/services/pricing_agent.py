from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, List, Optional

from langchain_classic.agents import AgentExecutor, create_react_agent
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import Tool
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_openai import ChatOpenAI


@dataclass
class PricingRecommendation:
    """调价结果的数据结构，便于 API 返回和前端渲染。"""

    product_name: str
    own_price: float
    inventory_status: str
    competitor_prices: List[float]
    recommended_price: float
    action: str
    reasoning: str
    raw_output: str


def get_own_price() -> float:
    """自定义工具 1：获取当前售价。

    这里先返回固定值，后续你可以替换为数据库查询、ERP 接口或商品服务接口。
    """

    return 99.0


def get_inventory_status() -> str:
    """自定义工具 2：获取库存状态。

    这里先返回固定值，后续可替换为真实库存接口。
    """

    return "库存充足，剩余500件"


def _extract_prices(text: str) -> list[float]:
    """从搜索结果中粗略提取价格数字。"""

    prices: list[float] = []
    for match in re.findall(r"(?:¥|￥|USD|\$)?\s?(\d+(?:\.\d+)?)", text):
        try:
            value = float(match)
        except ValueError:
            continue
        if 0 < value < 100000:
            prices.append(value)
    return prices


def build_llm() -> ChatOpenAI:
    """构造兼容 OpenAI 接口的阿里云 Qwen 模型。"""

    api_key = os.getenv("QWEN_API_KEY") or os.getenv("LLM_API_KEY")
    base_url = os.getenv("QWEN_BASE_URL") or os.getenv("LLM_API_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    model = os.getenv("QWEN_MODEL") or os.getenv("LLM_MODEL", "qwen-plus")

    if not api_key:
        raise ValueError(
            "缺少大模型 API Key。请配置环境变量 QWEN_API_KEY 或 LLM_API_KEY。"
        )

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0.2,
    )


def build_search_tool() -> Tool:
    """构造网络搜索工具。

    推荐使用 Tavily：
    - 配置环境变量 `TAVILY_API_KEY`
    - 官网申请 API Key 后写入 `.env`

    如果暂时没有 Tavily Key，也可以改成你自己的 Google Search 实现。
    """

    tavily_api_key = os.getenv("TAVILY_API_KEY")
    if not tavily_api_key:
        raise ValueError(
            "缺少搜索 API Key。请配置环境变量 TAVILY_API_KEY，然后再调用搜索工具。"
        )

    # TavilySearchResults 默认从环境变量读取 `TAVILY_API_KEY`
    os.environ["TAVILY_API_KEY"] = tavily_api_key
    search = TavilySearchResults(max_results=5)

    def _search(query: str) -> str:
        result = search.invoke(query)
        return str(result)

    return Tool(
        name="search_competitor_price",
        func=_search,
        description=(
            "用于搜索竞品价格和市场信息。输入应是类似 "
            "'{商品名} price on Amazon' 或 '{商品名} Amazon competitor price' 的英文/中文检索关键词。"
        ),
    )


def build_tools() -> list[Tool]:
    """构造 Agent 使用的工具集。"""

    def _own_price(_: str = "") -> str:
        return f"当前售价：{get_own_price()} 元"

    def _inventory(_: str = "") -> str:
        return f"库存状态：{get_inventory_status()}"

    return [
        Tool(
            name="get_own_price",
            func=_own_price,
            description="获取当前商品售价，输入可以为空。",
        ),
        Tool(
            name="get_inventory_status",
            func=_inventory,
            description="获取当前商品库存状态，输入可以为空。",
        ),
        build_search_tool(),
    ]


def build_react_prompt() -> PromptTemplate:
    """ReAct 提示词，要求模型先思考，再行动，再总结。"""

    template = """
你是一名资深电商动态调价 Agent，负责基于自有价格、库存与竞品价格给出调价建议。

你必须严格遵循 ReAct 流程：
1. 先分析用户意图，判断是否需要调价。
2. 再依次调用工具获取：当前售价、库存状态、竞品价格。
3. 结合观察结果给出清晰结论，包括推荐价格、建议动作（维持/降价/涨价）和原因。

业务规则：
- 如果竞品均价明显高于自家价格，且库存充足，可考虑小幅涨价。
- 如果竞品均价明显低于自家价格，且库存压力较大，应考虑降价。
- 如果库存一般，竞品价格接近自家价格，可维持原价。
- 推荐价格要给出明确数字，保留两位小数。
- 如果搜索结果不足，也要基于现有信息给出保守建议，并说明不确定性。

可用工具：
{tools}

工具名称列表：
{tool_names}

请按以下格式工作：

Question: 用户输入
Thought: 你的思考
Action: 工具名
Action Input: 工具输入
Observation: 工具返回
...（可重复多轮）
Thought: 我已经有足够信息
Final: 输出最终建议，必须包含：
- 商品名
- 当前售价
- 库存状态
- 竞品价格摘要
- 推荐价格
- 建议动作
- 详细原因

开始！

Question: {input}
Thought:{agent_scratchpad}
"""
    return PromptTemplate.from_template(template)


def build_pricing_agent() -> AgentExecutor:
    """构造 ReAct AgentExecutor。"""

    llm = build_llm()
    tools = build_tools()
    prompt = build_react_prompt()
    agent = create_react_agent(llm=llm, tools=tools, prompt=prompt)
    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=True,
        handle_parsing_errors=True,
        max_iterations=5,
    )


def infer_product_name(user_input: str) -> str:
    """从用户输入中尽量提取商品名，失败时返回默认值。"""

    text = user_input.strip()
    if not text:
        return "商品"
    patterns = [
        r"看看(.+?)(?:是否|要不要|需不需要|价格|调价|调整|需要)",
        r"([A-Za-z0-9\u4e00-\u9fff\-_. ]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1).strip(" ，。！？;；")
            if candidate:
                return candidate[:40]
    return "商品"


def run_pricing_agent(user_input: str) -> PricingRecommendation:
    """执行调价 Agent，并将结果整理成结构化对象。"""

    product_name = infer_product_name(user_input)
    search_keyword = f"{product_name} price on Amazon"
    executor = build_pricing_agent()
    result = executor.invoke(
        {
            "input": (
                f"用户请求：{user_input}\n"
                f"请围绕商品“{product_name}”进行调价分析。\n"
                f"搜索竞品时优先使用关键词：{search_keyword}"
            )
        }
    )
    raw_output = str(result.get("output", ""))

    own_price = get_own_price()
    inventory_status = get_inventory_status()
    competitor_prices = _extract_prices(raw_output)

    recommended_price = own_price
    action = "维持原价"

    if competitor_prices:
        avg_competitor = sum(competitor_prices) / len(competitor_prices)
        if avg_competitor > own_price * 1.08 and "充足" in inventory_status:
            recommended_price = round(min(own_price * 1.05, avg_competitor * 0.98), 2)
            action = "建议小幅涨价"
        elif avg_competitor < own_price * 0.92:
            recommended_price = round(max(own_price * 0.95, avg_competitor * 0.98), 2)
            action = "建议小幅降价"

    reasoning = (
        f"当前售价 {own_price:.2f} 元，库存状态为“{inventory_status}”。"
        f"模型返回的竞品信息中提取到 {len(competitor_prices)} 个价格样本。"
        f"综合竞品均价、库存充足程度以及价格带，给出“{action}”的建议。"
    )

    return PricingRecommendation(
        product_name=product_name,
        own_price=own_price,
        inventory_status=inventory_status,
        competitor_prices=competitor_prices,
        recommended_price=round(recommended_price, 2),
        action=action,
        reasoning=reasoning,
        raw_output=raw_output,
    )
