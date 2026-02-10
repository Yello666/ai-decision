from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import jieba
import requests
import os
from dotenv import load_dotenv

# 加载环境变量（存放大模型、Shopify的密钥等）
load_dotenv()
# --------------------------
# 数据模型定义（请求/响应格式）
# --------------------------
class HotspotEvaluateRequest(BaseModel):
    """热点适配评估的请求参数"""
    merchant_category: str  # 商家品类（如：女装、家居、3C数码）
    merchant_keywords: list[str]  # 商家核心关键词（如：["纯棉T恤", "宽松版型", "夏季新款"]）
    hotspot_title: str  # 热点标题（如："2024夏季多巴胺穿搭趋势"）
    hotspot_keywords: list[str]  # 热点核心关键词（如：["多巴胺穿搭", "夏季", "亮色"]）


class HotspotEvaluateResponse(BaseModel):
    """热点适配评估的响应结果"""
    adapt_score: float  # 适配分数（0-100）
    analysis: str  # 结果分析
    category_match: float  # 品类匹配度（0-1）
    keyword_similarity: float  # 关键词相似度（0-1）


# --------------------------
# 核心算法：热点适配评估
# --------------------------
def calculate_category_match(merchant_category: str, hotspot_title: str) -> float:
    """
    计算品类匹配度：判断商家品类与热点的相关性
    简单实现：匹配品类关键词是否出现在热点标题中，可后续优化为更复杂的分类算法
    """
    # 分词（热点标题）
    hotspot_words = jieba.lcut(hotspot_title)
    # 匹配逻辑：商家品类包含在热点分词中则匹配度1，否则按重叠度计算
    if merchant_category in hotspot_words:
        return 1.0
    # 进阶：按字符重叠率计算（示例）
    overlap_chars = set(merchant_category) & set(hotspot_title)
    match_score = len(overlap_chars) / max(len(merchant_category), 1)
    return round(match_score, 2)


def calculate_keyword_similarity(merchant_keywords: list[str], hotspot_keywords: list[str]) -> float:
    """
    计算关键词相似度：基于TF-IDF+余弦相似度
    """
    # 拼接关键词为文本
    merchant_text = " ".join(merchant_keywords)
    hotspot_text = " ".join(hotspot_keywords)
    # 构建TF-IDF向量
    vectorizer = TfidfVectorizer()
    try:
        tfidf_matrix = vectorizer.fit_transform([merchant_text, hotspot_text])
        # 计算余弦相似度
        similarity = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:2])[0][0]
        return round(similarity, 2)
    except:
        return 0.0


def generate_analysis(score: float, category_match: float, keyword_similarity: float) -> str:
    """生成结果分析文案"""
    analysis_parts = []
    if score >= 80:
        analysis_parts.append("该热点与您的店铺高度适配，建议重点跟进！")
    elif score >= 50:
        analysis_parts.append("该热点与您的店铺中度适配，可结合自身品类优化内容。")
    else:
        analysis_parts.append("该热点与您的店铺适配度较低，暂不建议重点投入。")

    analysis_parts.append(
        f"品类匹配度为{category_match * 100}%：{'品类高度相关' if category_match >= 0.8 else '品类相关性一般' if category_match >= 0.5 else '品类相关性较低'}")
    analysis_parts.append(
        f"关键词相似度为{keyword_similarity * 100}%：{'核心关键词高度重合' if keyword_similarity >= 0.8 else '部分关键词重合' if keyword_similarity >= 0.5 else '关键词重合度低'}")

    return " ".join(analysis_parts)




# class HotspotOut(BaseModel):
#     id: int
#     shopify_store_id: str
#     title: str
#     summary: str
#     created_at: Optional[datetime] = None
#
#     class Config:
#         from_attributes = True
#
#
# class HotspotInput(BaseModel):
#     keyword: str
#     trend: str
#     sentiment: str
#     audience: str


class ShopInput(BaseModel):
    category: str
    brand_tone: str

#
# class AssessmentRequest(BaseModel):
#     hotspot: HotspotInput
#     shop: Optional[ShopInput] = None # Optional, can be filled from DB
#
#
# class AssessmentResponse(BaseModel):
#     match_score: float
#     match_reason: str
#     brand_fit: str
#     conversion_prediction: str
#     content_suggestion: str
#     best_timing: str
#     products_to_promote: List[str]
