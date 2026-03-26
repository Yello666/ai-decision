# Fetch Competitor Info

## Overview

This skill provides a **unified** competitor pricing retrieval pipeline. It
searches for competitor products, cleans the raw results into a standardized
format, caches them in Redis, and returns the top 5 most relevant competitors
per product.

## Data Sources (priority order)

1. **Redis cache** — returns immediately when a fresh cached entry exists.
2. **SerpApi Amazon search** (primary) — structured product data from Amazon.
3. **Tavily web search** (fallback) — used only when SerpApi returns fewer than
   3 results for a given product.

## When to Invoke

- Called for **every product** submitted to the `/analyze_pricing` endpoint.
- Searches are executed **concurrently** across all products (bounded by a
  semaphore of 3 to respect API rate limits).

## Search Strategy

### SerpApi (primary)

| Parameter | Value                          |
|-----------|--------------------------------|
| `engine`  | `amazon`                       |
| `k`       | product name (English / ASCII) |
| `s`       | `exact-aware-popularity-rank`  |
| `page`    | `1`                            |

- Returns up to 10 organic results; only the top 5 with a valid price are kept.
- Non-ASCII product names are skipped for SerpApi (ASCII requirement).

### Tavily (fallback)

- Primary query: `site:amazon.com "{product_name}" price`
- Broader query: `"{product_name}" price (Amazon OR eBay OR Walmart)`
- Amazon URLs are ranked first; non-Amazon URLs fill remaining slots.

## Standardized Output Format

Every competitor item is normalized to the following dict:

| Field      | Type            | Description                                  |
|------------|-----------------|----------------------------------------------|
| `title`    | `str`           | Product title (truncated to 120 chars)        |
| `price`    | `float`         | Price in USD, rounded to 2 decimals           |
| `store`    | `str`           | Platform name: Amazon, eBay, Walmart, etc.    |
| `url`      | `str`           | Source URL of the competitor listing           |
| `old_price`| `float \| None` | Original / strikethrough price if available   |
| `rating`   | `float \| None` | Star rating (e.g. 4.5) if available           |
| `reviews`  | `int \| None`   | Number of reviews if available                |

## Caching

- Backend: Redis (`async redis.Redis`)
- Key pattern: `competitor:<md5(product_name_lower)>`
- TTL: configurable via `COMPETITOR_CACHE_TTL` env var (default 7200 s = 2 h)

## Filtering & Ranking

1. **Discard** results without a valid price.
2. **Discard** results with prices outside $1 – $100,000.
3. Sort Amazon results first, then other stores.
4. Deduplicate by URL.
5. Return only the **top 5** per product.

## Error Handling

- If SerpApi fails for a product, fall back to Tavily automatically.
- If both fail, return an **empty** competitor list for that product.
- Errors are logged but **never block** the overall analysis pipeline.
