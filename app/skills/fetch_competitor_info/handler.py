"""
Unified competitor information handler.

Data sources (in priority order):
  1. Redis cache  — if a fresh cached result exists, return immediately
  2. SerpApi Amazon search  — primary source (structured product data)
  3. Tavily web search  — fallback when SerpApi yields < 3 results

Output is a **standardized** list of competitor items per product so that
downstream consumers (pricing_agent) are completely decoupled from the
raw API formats.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import serpapi
from tavily import TavilyClient

from app.core.config import get_settings
from app.db.redis import get_redis_client

logger = logging.getLogger(__name__)
settings = get_settings()

TOP_N = 5
_PRICE_RE = re.compile(r"(?:USD|\$|¥|￥)\s?(\d+(?:\.\d{1,2})?)")

CACHE_KEY_PREFIX = "competitor"

# ---------------------------------------------------------------------------
# Standardized item builder
# ---------------------------------------------------------------------------

def _std_item(
    *,
    title: str,
    price: float,
    store: str,
    url: str,
    old_price: Optional[float] = None,
    rating: Optional[float] = None,
    reviews: Optional[int] = None,
) -> Dict[str, Any]:
    """Build a standardized competitor item dict."""
    return {
        "title": title[:120],
        "price": round(price, 2),
        "store": store,
        "url": url,
        "old_price": old_price,
        "rating": rating,
        "reviews": reviews,
    }


# ---------------------------------------------------------------------------
# Redis cache helpers
# ---------------------------------------------------------------------------

def _cache_key(product_name: str) -> str:
    h = hashlib.md5(product_name.strip().lower().encode()).hexdigest()
    return f"{CACHE_KEY_PREFIX}:{h}"


async def _get_cached(product_name: str) -> Optional[List[Dict[str, Any]]]:
    try:
        client = get_redis_client()
        raw = await client.get(_cache_key(product_name))
        if raw:
            return json.loads(raw)
    except Exception:
        logger.debug("Redis cache read failed for '%s'", product_name, exc_info=True)
    return None


async def _set_cached(product_name: str, items: List[Dict[str, Any]]) -> None:
    try:
        client = get_redis_client()
        ttl = settings.COMPETITOR_CACHE_TTL
        await client.setex(_cache_key(product_name), ttl, json.dumps(items))
    except Exception:
        logger.debug("Redis cache write failed for '%s'", product_name, exc_info=True)


# ---------------------------------------------------------------------------
# SerpApi (primary)
# ---------------------------------------------------------------------------

def _serpapi_search_sync(keyword: str) -> List[Dict[str, Any]]:
    api_key = settings.SERPAPI_API_KEY
    if not api_key:
        return []

    client = serpapi.Client(api_key=api_key)
    results = client.search({
        "engine": "amazon",
        "k": keyword,
        "s": "exact-aware-popularity-rank",
        "page": "1",
    })

    organic = results.get("organic_results")
    if not isinstance(organic, list):
        return []

    items: List[Dict[str, Any]] = []
    for raw in organic[:10]:
        if not isinstance(raw, dict):
            continue
        price = raw.get("extracted_price")
        if not isinstance(price, (int, float)) or not (1.0 <= price <= 100_000.0):
            continue
        items.append(_std_item(
            title=raw.get("title") or "",
            price=float(price),
            store="Amazon",
            url=raw.get("link_clean") or raw.get("link") or "",
            old_price=(
                float(raw["extracted_old_price"])
                if isinstance(raw.get("extracted_old_price"), (int, float))
                else None
            ),
            rating=(
                float(raw["rating"])
                if isinstance(raw.get("rating"), (int, float))
                else None
            ),
            reviews=(
                int(raw["reviews"])
                if isinstance(raw.get("reviews"), (int, float))
                else None
            ),
        ))
    return items[:TOP_N]


# ---------------------------------------------------------------------------
# Tavily (fallback)
# ---------------------------------------------------------------------------

_STORE_DOMAINS = {
    "amazon": "Amazon",
    "ebay": "eBay",
    "walmart": "Walmart",
    "aliexpress": "AliExpress",
    "target.com": "Target",
}


def _detect_store(url: str) -> str:
    url_lower = url.lower()
    for domain, name in _STORE_DOMAINS.items():
        if domain in url_lower:
            return name
    return "Other"


def _extract_price(text: str) -> Optional[float]:
    match = _PRICE_RE.search(text)
    if not match:
        return None
    try:
        price = float(match.group(1))
    except ValueError:
        return None
    if 1.0 <= price <= 100_000.0:
        return round(price, 2)
    return None


def _tavily_search_sync(keyword: str) -> List[Dict[str, Any]]:
    tavily_key = settings.TAVILY_API_KEY
    if not tavily_key:
        return []

    os.environ.setdefault("TAVILY_API_KEY", tavily_key)
    client = TavilyClient(api_key=tavily_key)

    queries = [
        f'site:amazon.com "{keyword}" price',
        f'"{keyword}" price (Amazon OR eBay OR Walmart)',
    ]
    items: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()

    for query in queries:
        try:
            raw = client.search(query=query, max_results=10)
        except Exception:
            logger.warning("Tavily search failed for query: %s", query, exc_info=True)
            continue
        for r in raw.get("results", []):
            url = r.get("url") or ""
            if url in seen_urls:
                continue
            content = r.get("content") or ""
            title = (r.get("title") or "")[:120]
            price = _extract_price(content) or _extract_price(title)
            if price is None:
                continue
            seen_urls.add(url)
            items.append(_std_item(
                title=title,
                price=price,
                store=_detect_store(url),
                url=url,
            ))
        if len(items) >= TOP_N:
            break

    amazon_items = [i for i in items if i["store"] == "Amazon"]
    other_items = [i for i in items if i["store"] != "Amazon"]
    return (amazon_items + other_items)[:TOP_N]


# ---------------------------------------------------------------------------
# Single-product search (cache → SerpApi → Tavily fallback)
# ---------------------------------------------------------------------------

async def _search_single(product_name: str) -> List[Dict[str, Any]]:
    cached = await _get_cached(product_name)
    if cached is not None:
        logger.debug("Cache hit for '%s'", product_name)
        return cached

    items: List[Dict[str, Any]] = []
    try:
        items = await asyncio.to_thread(_serpapi_search_sync, product_name)
    except Exception:
        logger.warning("SerpApi failed for '%s', falling back to Tavily", product_name, exc_info=True)

    if len(items) < 3:
        logger.info("SerpApi returned %d items for '%s', trying Tavily fallback", len(items), product_name)
        try:
            tavily_items = await asyncio.to_thread(_tavily_search_sync, product_name)
            seen_urls = {i["url"] for i in items}
            for ti in tavily_items:
                if ti["url"] not in seen_urls:
                    items.append(ti)
                    seen_urls.add(ti["url"])
                if len(items) >= TOP_N:
                    break
        except Exception:
            logger.warning("Tavily fallback also failed for '%s'", product_name, exc_info=True)

    items = items[:TOP_N]
    await _set_cached(product_name, items)
    return items


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def fetch_competitor_info(
    product_names: List[str],
) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch standardized competitor info for multiple products concurrently.

    Returns ``{product_name: [std_item, ...]}`` with at most TOP_N items each.
    """
    sem = asyncio.Semaphore(3)

    async def _bounded(name: str) -> List[Dict[str, Any]]:
        async with sem:
            return await _search_single(name)

    tasks = [_bounded(name) for name in product_names]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    data: Dict[str, List[Dict[str, Any]]] = {}
    for name, result in zip(product_names, results):
        if isinstance(result, BaseException):
            logger.error("Competitor search error for '%s': %s", name, result)
            data[name] = []
        else:
            data[name] = result
    return data
