import os
import json
from typing import List, Dict, Any
from openai import OpenAI
from app.schemas.hotspot import (
    SentimentCN,
    CollectTrendObject
)
from app.services.trending_service import get_youtube_trends

# 你可以用任何兼容 OpenAI API 的后端
LLM_CLIENT = OpenAI(
    base_url=os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    api_key=os.getenv("LLM_API_KEY", "sk-b0fc3528ced64aa4b31eca19eb10fb39"),
)
LLM_MODEL = os.getenv("LLM_MODEL", "qwen-plus")

BATCH_SIZE=10

# 采集+清洗数据（核心业务函数）
def collect_and_format_hot_data(platforms: str, max_results: int = 5) -> List[CollectTrendObject]:
    result: List[CollectTrendObject] = []
    
    if platforms == "youtube":
        # 1.获取初始数据
        youtube_trend_list: List[CollectTrendObject] = get_youtube_trends.get_trending_videos(max_results)
        
        # 限制处理数量
        process_list = youtube_trend_list[:max_results]

        # 2. 分批处理（每批 BATCH_SIZE 个）
        for i in range(0, len(process_list), BATCH_SIZE):
            batch = process_list[i:i + BATCH_SIZE]
            
            # 构建批次分析内容
            analysis_inputs = []
            for item in batch:
                input_data = {
                    "id": item.id,
                    "title": item.title,
                    "summary": item.summary,
                    "tags": item.tags
                }
                analysis_inputs.append(input_data)
            
            # 3. 调用大模型批量分析
            batch_results = _analyze_with_llm(analysis_inputs)
            
            if not batch_results or "results" not in batch_results:
                print(f"Batch LLM analysis failed or returned empty for batch starting at {i}")
                continue
                
            # 将结果映射回对象
            results_list = batch_results["results"]
            # 创建 ID 到结果的映射，方便匹配
            res_map = {res["id"]: res for res in results_list if "id" in res}
            
            for item in batch:
                analysis_res = res_map.get(item.id)
                if not analysis_res:
                    continue
                
                # 4. 风险过滤：如果标记为 RED_LINE 或 is_safe_for_marketing 为 false，则跳过
                if analysis_res.get("risk_category") == "RED_LINE" or not analysis_res.get("is_safe_for_marketing"):
                    print(f"Skipping unsafe content: {item.title} (Reason: {analysis_res.get('risk_category')})")
                    continue
                
                # 5. 补齐属性
                item.summary = analysis_res.get("summary", item.summary)
                item.tags = analysis_res.get("tags", item.tags)
                item.sentiment_label = _map_sentiment(analysis_res.get("sentiment_label", "中性"))
                item.sentiment_score = analysis_res.get("sentiment_score", 0.0)
                item.risk_category = analysis_res.get("risk_category")
                item.warning_message = analysis_res.get("warning_message")
                item.audience = analysis_res.get("audience", [])
                
                result.append(item)

    return result


def _analyze_with_llm(content_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    调用大模型进行批量情感分析、风险评估和受众推断
    """
    prompt = f"""
 Role：电商营销风控专家。你将接收一组YouTube热点摘要列表（包含id, title, summary, tags），需逐一分析每个热点的商业机会与风险。 
 
 Analysis Rules (Apply to EACH item in the list):
 1. 对热点进行总结，50-200字最佳；输出所给内容的summary和几个最主要的tags。
 2. Sentiment Analysis:
    - Label: 正面/中性/负面（如果既不是正面，也不是负面，那就是中性；既包含正面，也包含负面，也是中性）
    - Score: -100.0 (极负) 到 100.0 (极正) 
 3. Risk Category (Priority Logic):
    按顺序匹配，命中即止： 
    3.1 RED_LINE 绝对不可营销(is_safe_for_marketing=false): 
       涉及内容：政治斗争、国家政策批评、地缘冲突、台湾问题、中国政治、霸权、谋杀、恐怖主义、儿童剥削、极端暴力、色情、爱泼斯坦案、重大自然灾害。
    3.2 YELLOW_OPPORTUNITY 高商业潜力但需谨慎(is_safe_for_marketing=true): 
       特征：行业质量危机、服务投诉、权益纠纷。公众愤怒但渴求市场替代方案。
    3.3 GREEN_SAFE 低风险，可营销(is_safe_for_marketing=true): 
       特征：正面新闻、知识科普、轻松有趣、自嘲式幽默、搞笑梗等。
 4. Audience Inference: 推断3-5个具体的关注热点的人群标签 (如："18-35岁职场女性", "硬核科技粉")，拒绝泛词。
 
 Output Format:
 - Strict JSON only. No markdown, no explanations. 
 - The output must be a JSON object containing a "results" key, which is a list of objects corresponding exactly to the input items.
 - Schema for EACH object in "results": 
 {{
    "id": "String (Copy exactly from input 'id')",
    "summary": "String (Brief Summary)", 
    "tags": ["String", "String", ...],
    "sentiment_score": Float (-100.0 to 100.0), 
    "sentiment_label": "String (正面、中性、负面)", 
    "risk_category": "Enum[RED_LINE, YELLOW_OPPORTUNITY, GREEN_SAFE]", 
    "is_safe_for_marketing": Boolean, 
    "warning_message": "String", 
    "audience": ["String", "String", ...] 
 }}

 # Input Data 
 {json.dumps(content_list, ensure_ascii=False)}
"""
# json格式的两个大括号不可以变，因为prompt是一个f""字符串，里面的大括号会被解析为站位符，两个大括号可以被转义。
    try:
        response = LLM_CLIENT.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,  # 区间为0-2，意思是是否选择概率最高的词，0则是一定选择，1是不一定选择，
            # 太高输出会不稳定，不按照prompt规定的输出，大于0.5是可以忍受，大于1是不推荐的
            response_format={"type": "json_object"}
        )
        #打印token用量
        if hasattr(response, 'usage') and response.usage:
            usage = response.usage
            print("=" * 50)
            print(f"Token消耗详情（{LLM_MODEL}）：")
            print(f"输入Token（Prompt）：{usage.prompt_tokens}")
            print(f"输出Token（Completion）：{usage.completion_tokens}")
            print(f"总消耗Token：{usage.total_tokens}")
            print("=" * 50)
        else:
            print("⚠️  未获取到Token使用信息，可能是API版本或模型不支持")


        content = response.choices[0].message.content

        return json.loads(content)
    except Exception as e:
        print(f"LLM analysis failed: {e}")
        return {}


def _map_sentiment(label: str) -> SentimentCN:
    """
    将大模型返回的情感标签映射到系统定义的 SentimentCN
    """
    mapping = {
        "正面": SentimentCN.positive,
        "中性": SentimentCN.neutral,
        "负面": SentimentCN.negative,
    }
    return mapping.get(label, SentimentCN.neutral)