"""
Pricing analysis service — pipeline-based (no ReAct agent).

Flow:
  1. get_products_info  →  fetch product details from Shopify
  2. fetch_competitor_info  →  SerpApi (primary) + Tavily (fallback), with Redis cache
  3. load pricing_rules skill  →  business rules injected into prompt
  4. LLM chain with JsonOutputParser  →  structured JSON output
  5. Post-process  →  inject competitor_info_url from raw competitor data
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import SecretStr

from app.core.config import get_settings
from app.models import Merchant
from app.schemas.pricing import PricingAnalysisResponseLLM
from app.services.product_service import fetch_product
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
# Step 2 context builder (consumes standardized competitor data)
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
# Main pipeline
# ---------------------------------------------------------------------------

async def run_pricing_analysis(
    merchant: Merchant,
    product_ids: List[int],
) -> Dict[str, Any]:
    """Execute the full pricing analysis pipeline and return structured JSON."""
    logger.info("Starting pricing analysis for merchant %s (ID: %s)", merchant.name, merchant.id)

    # Step 1: fetch product info
    logger.info("Step 1: Fetching product info for %d products", len(product_ids))
    products = await get_products_info(merchant, product_ids)
    product_names = [p.name for p in products]

    # Step 2: fetch competitor info (SerpApi → Tavily fallback, with Redis cache)
    logger.info("Step 2: Fetching competitor info for: %s", product_names)
    competitor_data = await fetch_competitor_info(product_names)

    # Step 3: load business rules from skill
    logger.info("Step 3: Loading pricing rules from skills")
    pricing_rules = load_skill("pricing_rules")

    # Step 4: build LLM chain
    logger.info("Step 4: Building LLM chain and prompt")
    parser = JsonOutputParser(pydantic_object=PricingAnalysisResponseLLM)

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "You are an expert e-commerce dynamic pricing analyst.\n\n"
            "## Business Rules\n{pricing_rules}\n\n"
            "You MUST respond with ONLY valid JSON matching the schema below. "
            "Do NOT include any text outside the JSON object.\n\n"
            "{format_instructions}",
        ),
        (
            "human",
            "Analyze the following products and provide pricing "
            "recommendations for each one:\n\n"
            "{products_info}\n\n"
            "Return ONLY the JSON object with your pricing_analysis array.",
        ),
    ])

    chain = prompt | _get_llm() | parser

    # Step 5: invoke
    products_context = _build_products_context(products, competitor_data)
    logger.info("Step 5: Invoking LLM for analysis")

    result = await chain.ainvoke({
        "pricing_rules": pricing_rules,
        "format_instructions": parser.get_format_instructions(),
        "products_info": products_context,
    })

    # Step 6: post-process — inject competitor URLs
    result = _enrich_with_urls(result, competitor_data)

    logger.info("Pricing analysis completed successfully for merchant %s", merchant.name)
    return result
