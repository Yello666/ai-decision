from typing import List

import requests

from app.schemas.hotspot import CollectTrendObject, SentimentCN

API_KEY = "AIzaSyByBXtfrN3NwEJ932l26nnP9Zxv8y5Ibjg"
REGION_CODE = "US"

proxies = {
    "http": "http://127.0.0.1:7890",
    "https": "http://127.0.0.1:7890",
}


def get_trending_videos(max_result:int) -> List[CollectTrendObject]:
    """
    获取YouTube热门视频并封装为CollectTrendObject列表
    """
    url = "https://www.googleapis.com/youtube/v3/videos"

    params = {
        "part": "snippet,statistics",
        "chart": "mostPopular",
        "regionCode": REGION_CODE,
        "maxResults": max_result,
        "key": API_KEY,
    }

    try:
        response = requests.get(
            url,
            params=params,
            proxies=proxies,
            timeout=15
        )
        response.raise_for_status()  # 抛出HTTP错误状态码异常
    except requests.exceptions.RequestException as e:
        print(f"请求失败: {e}")
        return []

    print("状态码:", response.status_code)
    data = response.json()

    # 存储封装后的热点数据
    trend_objects = []

    print(f"\n--- 当前 {REGION_CODE} 热门视频 ---\n")

    for index, item in enumerate(data["items"], 1):
        # 提取基础信息
        video_id = item["id"]
        snippet = item["snippet"]
        statistics = item["statistics"]

        # 处理可能为空的字段（避免KeyError）
        view_count = int(statistics.get("viewCount", 0))
        likes = int(statistics.get("likeCount", 0))
        description = snippet.get("description", "无描述")
        # 摘要
        summary = description.strip() if description else "无描述"

        # 构建跳转链接
        jump_url = f"https://www.youtube.com/watch?v={video_id}"

        # 封装为CollectTrendObject
        trend_obj = CollectTrendObject(
            id=video_id,
            title=snippet["title"],
            summary=summary,
            tags=snippet.get("tags", []),  # 标签列表（无则为空）
            sentiment_label=SentimentCN.neutral,  # 默认中性（可根据业务逻辑调整）
            audience=None,  # YouTube API暂未直接返回受众画像
            jump_url=jump_url,
            view_count=view_count,
            likes=likes,
            publish_time=snippet["publishedAt"],  # ISO格式时间
            platform="Youtube"
        )

        trend_objects.append(trend_obj)

        # 打印格式化信息
        print(f"{index}. {trend_obj.title}")
        print(f"视频ID: {trend_obj.id}")
        print(f"播放量: {trend_obj.view_count:,}")
        print(f"点赞数: {trend_obj.likes:,}")
        print(f"发布时间: {trend_obj.publish_time}")
        print(f"跳转链接: {trend_obj.jump_url}")
        print(f"标签: {', '.join(trend_obj.tags)}")
        print("-" * 80)

    return trend_objects



