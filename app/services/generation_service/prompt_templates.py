# """
# 结合热点（TrendObject）与品牌（BrandObject）生成适配大模型/SeedDance 的 Prompt 模板。
# 保证生成内容既贴合热点又符合品牌调性。
# """
# from typing import Optional
#
# from app.schemas.generations import ProductObject
# from app.schemas.hotspot import TrendObject, BrandObject
#
# #
# # def build_video_prompt(
# #     trend: TrendObject,
# #     brand: BrandObject,
# #     product: Optional[ProductObject] = None,
# #     user_prompt: str | None = None,
# #     image_url: str | None = None,
# # ) -> str:
# #     """
# #     生成视频描述 prompt，用于 SeedDance text-to-video / image-to-video。
# #     """
# #     tags_str = ", ".join(trend.tags) if trend.tags else "trending"
# #     audience_brand = ", ".join(brand.audience) if brand.audience else brand.tone
# #     product_str = (
# #         f"Product: {product.name}:{product.description},price:{product.price}."
# #         if product
# #         else "Product: (not specified)."
# #     )
# #     base = (
# #         f"Create a short marketing video clip base below information . "
# #         f"Trend context: {trend.title}. {trend.summary}. Tags: {tags_str}. "
# #         f"Brand: {brand.name}, {brand.mainly_sold_products}, tone: {brand.tone}. "
# #         f"{product_str}"
# #         f"Target audience: {audience_brand}. "
# #         f"Style: funny, aligned with the trend and brand. "
# #     )
# #     if brand.core_value:
# #         base += f"Brand value: {brand.core_value}. "
# #     if user_prompt:
# #         base += f"Additional direction: {user_prompt}. "
# #     if image_url:
# #         base+="There is a product inference image."
# #     return base.strip()
#
#
# # def build_image_prompt(
# #     trend: TrendObject,
# #     brand: BrandObject,
# #     user_prompt: str | None = None,
# # ) -> str:
# #     """
# #     生成图片描述 prompt，用于 SeedDance Nano Banana 2.0。
# #     要求：英文、适合营销海报/主图、结合热点与品牌。
# #     """
# #     tags_str = ", ".join(trend.tags) if trend.tags else "trending"
# #     base = (
# #         f"Marketing image or poster. "
# #         f"Trend: {trend.title}. {trend.summary}. Tags: {tags_str}. audience: {trend.audience}"
# #         f"Brand: {brand.name}, {brand.mainly_sold_products}, tone: {brand.tone}. "
# #         f"Visual style: high quality, on-brand, trend-aware, suitable for social or e-commerce. "
# #     )
# #     if brand.core_value:
# #         base += f"Brand message: {brand.core_value}. "
# #     if user_prompt:
# #         base += f"Extra: {user_prompt}. "
# #     return base.strip()
#
# # 病毒短视频 Prompt
# def build_trend_product_video_prompt(
#     trend: TrendObject,
#     brand: BrandObject,
#     product: ProductObject,
#     user_prompt: str | None = None,
# ) -> str:
#     """
#     组装"热点 × 品牌 × 产品"病毒短视频 Prompt。
#     输出英文，供 Seedance 模型直接消费。
#     """
#     tags_str = ", ".join(trend.tags) if trend.tags else "trending"
#     selling_points = f"{product.name}: {product.description}, price: {product.price}$"
#
#     system_instruction = (
#         "You are a creative advertising director who specializes in making viral short videos. "
#         "Based on the following information, write a video generation prompt."
#     )
#
#     input_section = (
#         f"[Trend Background]: {trend.title}. {trend.summary}. Tags: {tags_str}.\n"
#         f"[Brand Persona]: {brand.name} (Style: {brand.tone}). "
#         f"Products: {brand.mainly_sold_products}."
#     )
#     if brand.core_value:
#         input_section += f" Slogan: {brand.core_value}."
#     input_section += f"\n[Product Selling Points]: {selling_points}."
#     if user_prompt:
#         input_section += f"\n[User Requirements]: {user_prompt}."
#
#     generation_rules = (
#         "[Generation Rules]\n"
#         "1. Style: Must be extremely exaggerated, funny, and full of drama. Use fast-paced camera language.\n"
#         "2. Content: Naturally integrate the product into the trending topic while reflecting the brand persona.\n"
#         "3. Format: Output an English description with scene depiction, actions, and atmosphere words "
#         "(e.g.: funny, exaggerated, chaotic, meme-style, viral, over-the-top)."
#     )
#
#     return f"{system_instruction}\n\n{input_section}\n\n{generation_rules}"
#
#
# def build_text_prompt(
#     trend: TrendObject,
#     brand: BrandObject,
#     user_prompt: str | None = None,
# ) -> str:
#     """
#     生成给大模型的系统/用户 prompt，用于生成营销文案（短文案、博文、产品描述等）。
#     输出为中文营销文案，可直接用于店铺/社媒。
#     """
#     tags_str = "、".join(trend.tags) if trend.tags else "热点"
#     audience = "、".join(brand.audience) if brand.audience else "目标用户"
#     instruction = (
#         "你是一位擅长结合热点做品牌营销的文案专家。请根据以下「热点信息」和「品牌信息」，"
#         "生成一段可直接用于店铺或社媒的营销文案（中文）。"
#         "要求：贴合热点、突出品牌调性、有感染力。文案字号全部统一，不要包含markdown格式的**，不要包含文案分析，可以包含一些emoji"
#     )
#     trend_block = (
#         f"【热点信息】\n"
#         f"标题：{trend.title}\n"
#         f"摘要：{trend.summary}\n"
#         f"标签：{tags_str}\n"
#     )
#     if trend.audience:
#         trend_block += f"热点受众：{'、'.join(trend.audience)}\n"
#     brand_block = (
#         f"【品牌信息】\n"
#         f"品牌名：{brand.name}\n"
#         f"主要售卖商品品类：{brand.mainly_sold_products}\n"
#         f"调性：{brand.tone}\n"
#         f"目标受众：{audience}\n"
#     )
#     if brand.core_value:
#         brand_block += f"核心价值/Slogan：{brand.core_value}\n"
#     extra = f"\n【用户补充】\n{user_prompt}\n" if user_prompt else ""
#     return f"{instruction}\n\n{trend_block}\n{brand_block}{extra}".strip()
