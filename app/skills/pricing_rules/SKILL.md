# Pricing Rules

## Overview

This skill defines the business rules that the AI pricing analyst must follow when generating dynamic pricing recommendations for e-commerce products.

## Price Adjustment Triggers

- If the average competitor price is **significantly higher** than the current price (>8%) **AND** inventory is sufficient (>50 units), consider a **moderate price increase** (up to 5%).
- If the average competitor price is **significantly lower** than the current price (>8%) **AND** there is inventory pressure, consider a **price decrease** to stay competitive.
- If competitor prices are **close** to the current price (within ±5%), **maintain** the current price.
- If inventory is **low** (<20 units) and the product is selling well, maintain or slightly increase the price — do NOT discount aggressively.

## Price Constraints

- Recommended prices must be specific numbers rounded to **two decimal places**.
- A single adjustment must **not exceed 10%** of the current price.
- Never recommend a price below estimated cost (assume at least 30% margin from the listed price).

## Confidence Scoring Guidelines

| Range   | Meaning                                                  |
|---------|----------------------------------------------------------|
| 90–100  | Strong data support, clear market signal, high confidence |
| 70–89   | Moderate data, reasonable inference, medium-high confidence |
| 50–69   | Limited data, recommendation based on general heuristics  |
| < 50    | Insufficient data — recommend keeping current price with a note |

## Special Conditions

- If search results are insufficient, provide a **conservative** recommendation and explicitly note the uncertainty.
- For seasonal or trending products, factor in demand elasticity.
- Low-stock items should **not** be discounted aggressively.
- New products (listed < 30 days) should maintain introductory pricing unless market signals are strong.

## Output Requirements

Each product analysis **must** include:

1. `product_name` — the product's display name
2. `current_price` — the current listed price
3. `stock_status` — human-readable inventory status (e.g. "In Stock: 120 units")
4. `competitor_price_summary` — a concise summary of competitor price range and average
5. `recommended_price` — the AI-recommended price
6. `suggested_action` — one of: "Increase price", "Decrease price moderately", "Decrease price significantly", "Keep current price"
7. `detailed_reason` — reasoning that references specific competitor data and inventory levels
8. `confidence_score` — a float between 0 and 100
