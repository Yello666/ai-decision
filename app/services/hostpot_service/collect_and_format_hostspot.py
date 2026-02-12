from transformers import pipeline
from app.schemas.hotspot import (
    SentimentCN,
    TrendObject,
    CollectTrendObject
)

# 初始化中文情感分析模型
sentiment_analyzer = pipeline("sentiment-analysis", model="uer/roberta-base-finetuned-jd-binary-chinese")


# 采集并格式化数据（包含新增字段）
def collect_and_format_hot_data(platforms=["youtube", "tiktok"], max_results=5):
    """采集数据并格式化为包含新增字段的TrendObject结构"""
    raw_hot_data = []
    # 采集数据并保存到mysql数据库中

    # YouTube数据

    # for i in range(max_results):
    #     publish_time = (datetime.now() - timedelta(hours=i)).isoformat()
    #     raw_hot_data.append({
    #         "id": f"yt_{i}",  # 唯一ID
    #         "title": f"2026年最新科技发布会{i}",
    #         "summary": f"本次发布会带来了全新的AI硬件产品，搭载最新一代芯片，性能提升300%，支持多模态交互，现场演示了实时语音翻译、图像生成等功能，引发科技圈广泛讨论。{i}",
    #         "tags": ["科技", "AI", "硬件"],
    #         "sentiment_text": "这款产品体验非常好，解决了很多实际问题",
    #         "audience": ["18-45岁", "科技爱好者", "职场人士"],
    #         "jump_url": f"https://www.youtube.com/watch?v=yt_{i}",
    #         "view_count": 1200000 + i * 10000,  # 播放量
    #         "publish_time": publish_time,  # 发布时间
    #         "platform": "youtube"
    #     })
    # # TikTok数据
    # for i in range(max_results):
    #     publish_time = (datetime.now() - timedelta(hours=i + 2)).isoformat()
    #     raw_hot_data.append({
    #         "id": f"tk_{i}",  # 唯一ID
    #         "title": f"春日露营爆款好物推荐{i}",
    #         "summary": f"这款露营椅重量仅1.2kg，承重200斤，折叠后只有矿泉水瓶大小，自带收纳袋，性价比超高，实测户外使用超舒适，已成为露营圈爆款单品。{i}",
    #         "tags": ["露营", "户外", "好物推荐"],
    #         "sentiment_text": "质量一般，价格偏贵，不太推荐",
    #         "audience": ["16-35岁", "露营爱好者", "学生"],
    #         "jump_url": f"https://www.tiktok.com/video/tk_{i}",
    #         "view_count": 800000 + i * 8000,  # 播放量
    #         "publish_time": publish_time,  # 发布时间
    #         "platform": "tiktok"
    #     })

    # 格式转换+情感分析
    trend_objects = []
    for item in raw_hot_data:
        # 情感分析
        try:
            sentiment_result = sentiment_analyzer(item["sentiment_text"])[0]
            sentiment = SentimentCN.positive if sentiment_result["label"] == "positive" else (
                SentimentCN.negative if sentiment_result["label"] == "negative" else SentimentCN.neutral
            )
        except:
            sentiment = SentimentCN.neutral

        # 构建完整的TrendObject对象
        trend_obj = TrendObject(
            title=item["title"],
            summary=item["summary"],
            tags=item["tags"],
            sentiment=sentiment,
            audience=item["audience"],
            view_count=item["view_count"],
            publish_time=item["publish_time"]
        )
        trend_objects.append(trend_obj.dict())

    return trend_objects