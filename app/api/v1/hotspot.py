from fastapi import APIRouter, HTTPException
from typing import List

from app.schemas.hotspot import (
    HotspotMatchRequest,
    HotspotMatchResponse,
    CollectTrendObject,
    HotspotTrendRequest,
)
from app.services.hostpot_service.analyse_matching_degree import match_hotspot_v2, batch_match_hotspot_v2
from app.services.hostpot_service.collect_hostspot import collect_and_format_hot_data

router = APIRouter(prefix="/hotspot", tags=["hotspot"])

#1.获取热点数据，展示在前端
# FastAPI接口：返回包含所有字段的JSON数据
@router.post("/hot-trends", response_model=List[CollectTrendObject], summary="获取含完整字段的热点JSON数据")
async def get_hot_trends(request: HotspotTrendRequest):
    """
    获取热点趋势数据
    - platforms: 平台列表，如 ["youtube"]
    - max_results: 每个平台获取的结果数量
    """
    try:
        # 如果没有传入平台，默认使用 youtube
        platforms = request.platforms if request.platforms else ["youtube"]
        
        # 批量获取数据
        hot_trends: List[CollectTrendObject] = []
        for platform in platforms:
            trends = collect_and_format_hot_data(platform, request.max_results)
            hot_trends.extend(trends)
            
        return hot_trends
    except Exception as e:
        msg = str(e).replace('\n', ' ').replace('\\n', ' ').strip()
        raise HTTPException(status_code=500, detail=f"获取热点数据失败：{msg}")



# 2.热点批量匹配
@router.post("/match", response_model=List[HotspotMatchResponse])
def match_hotspot(requests: List[HotspotMatchRequest]):
    """
    批量热点匹配 V2（结构化输入/输出，支持批量处理）
    - 入参：List[HotspotMatchRequest]
    - 出参：List[HotspotMatchResponse]
    """
    try:
        return batch_match_hotspot_v2(requests)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批量匹配失败：{str(e)}")






# # 从数据库获取热点信息给商家看
# @router.get("/list")
# def hotspot_list(
#     skip: int = 0,
#     limit: int = 20,
#     current_merchant=Depends(get_current_merchant),
#     db: Session = Depends(get_db),
# ):
#     items = list_hotspots(db, current_merchant.shopify_store_id, skip, limit)
#     return success(items)
#
# # 获取单个热点的详细信息
# @router.get("/{hotspot_id}")
# def hotspot_detail(
#     hotspot_id: int,
#     current_merchant=Depends(get_current_merchant),
#     db: Session = Depends(get_db),
# ):
#     item = get_hotspot(db, current_merchant.shopify_store_id, hotspot_id)
#     if not item:
#         raise HTTPException(status_code=404, detail="hotspot_not_found")
#     return success(item)

# 在content处做了
# @router.post("/generate-content")
# async def generate_hotspot_content(hotspot_title: str, adapt_analysis: str, merchant_category: str):
#     """
#     对接大模型API生成热点相关图文
#     示例：调用OpenAI API（需替换为你的大模型接口）
#     """
#     try:
#         # 大模型请求参数（可自定义prompt）
#         prompt = f"""
#         基于以下信息为{merchant_category}商家生成热点相关的营销图文：
#         热点标题：{hotspot_title}
#         适配分析：{adapt_analysis}
#         要求：1. 符合商家品类；2. 结合热点关键词；3. 适合Shopify店铺发布；4. 支持商家编辑。
#         """
#         # 调用大模型API（示例：OpenAI）
#         api_key = os.getenv("LLM_API_KEY")
#         api_url = os.getenv("LLM_API_URL")
#         if not api_key or not api_url:
#             raise HTTPException(status_code=400, detail="未配置 LLM_API_KEY / LLM_API_URL，无法生成内容")
#
#         response = requests.post(
#             url=api_url,
#             headers={"Authorization": f"Bearer {api_key}"},
#             json={
#                 "model": "gpt-3.5-turbo",
#                 "messages": [{"role": "user", "content": prompt}],
#                 "temperature": 0.7
#             }
#         )
#         response.raise_for_status()
#         content = response.json()["choices"][0]["message"]["content"]
#         return {"content": content, "editable": True}
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"图文生成失败：{str(e)}")


# --------------------------
# 预留接口：Shopify一键发布（阶段2-3）
# --------------------------
@router.post("/shopify/publish")
async def publish_to_shopify(shopify_store: str, access_token: str, content: str, product_id: str = None):
    """
    Shopify站内一键发布
    - 支持产品关联文案、店铺公告发布
    """
    try:
        # 初始化Shopify客户端（需安装shopify-api-python）
        import shopify
        shopify.ShopifyResource.set_site(f"https://{access_token}@{shopify_store}.myshopify.com/admin/api/2024-01")

        # 1. 发布店铺公告（示例）
        shop = shopify.Shop.current()
        shop.note = content  # 店铺公告可存储在note字段，或使用自定义主题字段
        shop.save()

        # 2. 关联产品文案（如果传入product_id）
        if product_id:
            product = shopify.Product.find(product_id)
            product.body_html = f"<p>{content}</p>"  # 产品描述更新
            product.save()

        return {"status": "success", "message": "发布成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Shopify发布失败：{str(e)}")

# #
# @router.post("/assess")
# async def assess(
#     payload: AssessmentRequest,
#     current_merchant=Depends(get_current_merchant),
# ):
#     # If shop info is missing, fill it from current_merchant
#     if not payload.shop:
#         payload.shop = ShopInput(
#             category=current_merchant.shopify_category or "General",
#             brand_tone=current_merchant.brand_tone or "Professional"
#         )
#
#     result = await assess_hotspot_match(payload)
#     return success(result)


# @router.post("/evaluate", response_model=HotspotEvaluateResponse)
# def evaluate_hotspot_adapt(request: HotspotEvaluateRequest):
#     """
#     热点适配评估接口
#     - 入参：商家品类、商家关键词、热点标题、热点关键词
#     - 出参：适配分数、结果分析、品类匹配度、关键词相似度
#     """
#     try:
#         # 1. 计算品类匹配度
#         category_match = calculate_category_match(request.merchant_category, request.hotspot_title)
#         # 2. 计算关键词相似度
#         keyword_similarity = calculate_keyword_similarity(request.merchant_keywords, request.hotspot_keywords)
#         # 3. 计算综合适配分数（权重可调整：品类70% + 关键词30%）
#         adapt_score = (category_match * 0.7 + keyword_similarity * 0.3) * 100
#         adapt_score = round(adapt_score, 1)
#         # 4. 生成结果分析
#         analysis = generate_analysis(adapt_score, category_match, keyword_similarity)
#
#         return HotspotEvaluateResponse(
#             adapt_score=adapt_score,
#             analysis=analysis,
#             category_match=category_match,
#             keyword_similarity=keyword_similarity
#         )
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"评估失败：{str(e)}")
