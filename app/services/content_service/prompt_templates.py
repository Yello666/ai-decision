"""
结合热点（TrendObject）与品牌（BrandObject）生成适配大模型/SeedDance 的 Prompt 模板。
保证生成内容既贴合热点又符合品牌调性。
"""
from app.schemas.content import ProductObject
from app.schemas.hotspot import TrendObject, BrandObject


def build_video_prompt(
    trend: TrendObject,
    brand: BrandObject,
    product:ProductObject,
    user_prompt: str | None = None,
    image_url:str | None=None,
) -> str:
    """
    生成视频描述 prompt，用于 SeedDance text-to-video / image-to-video。
    要求：视频中的产品要贴合产品图（如果有），有故事性，很好地贴合热点、产品描述和品牌调性。
    """
    tags_str = ", ".join(trend.tags) if trend.tags else "trending"
    audience_brand = ", ".join(brand.audience) if brand.audience else brand.tone
    # audience_trend = ", ".join(trend.audience) if trend.audience else "general audience"

    base = (
        f"Create a short marketing video clip base below information . "
        f"Trend context: {trend.title}. {trend.summary}. Tags: {tags_str}. "
        f"Brand: {brand.name}, {brand.industry}, tone: {brand.tone}. "
        f"Product: {product.name}:{product.description},price:{product.price}."
        f"Target audience: {audience_brand}. "
        f"Style: funny, aligned with the trend and brand. "
    )
    if brand.core_value:
        base += f"Brand value: {brand.core_value}. "
    if user_prompt:
        base += f"Additional direction: {user_prompt}. "
    if image_url:
        base+="There is a product inference image."
    return base.strip()


def build_image_prompt(
    trend: TrendObject,
    brand: BrandObject,
    user_prompt: str | None = None,
) -> str:
    """
    生成图片描述 prompt，用于 SeedDance Nano Banana 2.0。
    要求：英文、适合营销海报/主图、结合热点与品牌。
    """
    tags_str = ", ".join(trend.tags) if trend.tags else "trending"
    base = (
        f"Marketing image or poster. "
        f"Trend: {trend.title}. {trend.summary}. Tags: {tags_str}. audience: {trend.audience}"
        f"Brand: {brand.name}, {brand.industry}, tone: {brand.tone}. "
        f"Visual style: high quality, on-brand, trend-aware, suitable for social or e-commerce. "
    )
    if brand.core_value:
        base += f"Brand message: {brand.core_value}. "
    if user_prompt:
        base += f"Extra: {user_prompt}. "
    return base.strip()


def build_text_prompt(
    trend: TrendObject,
    brand: BrandObject,
    user_prompt: str | None = None,
) -> str:
    """
    生成给大模型的系统/用户 prompt，用于生成营销文案（短文案、博文、产品描述等）。
    输出为中文营销文案，可直接用于店铺/社媒。
    """
    tags_str = "、".join(trend.tags) if trend.tags else "热点"
    audience = "、".join(brand.audience) if brand.audience else "目标用户"
    instruction = (
        "你是一位擅长结合热点做品牌营销的文案专家。请根据以下「热点信息」和「品牌信息」，"
        "生成一段可直接用于店铺或社媒的营销文案（中文）。"
        "要求：贴合热点、突出品牌调性、有感染力。文案字号全部统一，不要包含markdown格式的**，不要包含文案分析，可以包含一些emoji"
    )
    trend_block = (
        f"【热点信息】\n"
        f"标题：{trend.title}\n"
        f"摘要：{trend.summary}\n"
        f"标签：{tags_str}\n"
    )
    if trend.audience:
        trend_block += f"热点受众：{'、'.join(trend.audience)}\n"
    brand_block = (
        f"【品牌信息】\n"
        f"品牌名：{brand.name}\n"
        f"行业：{brand.industry}\n"
        f"调性：{brand.tone}\n"
        f"目标受众：{audience}\n"
    )
    if brand.core_value:
        brand_block += f"核心价值/Slogan：{brand.core_value}\n"
    extra = f"\n【用户补充】\n{user_prompt}\n" if user_prompt else ""
    return f"{instruction}\n\n{trend_block}\n{brand_block}{extra}".strip()
