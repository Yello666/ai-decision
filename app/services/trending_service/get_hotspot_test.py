import json
import re
from typing import List

import requests

BLOCK_KEYWORDS = [
    # 战争 / 军事
    "战争", "交火", "防空", "军队", "航母", "武器",

    # 时政
    "访华", "访美", "总理", "总统", "外交", "国家主席", "政府",

    # 争议话题
    "彩礼", "女权", "男权", "对立",

    # 命案 / 事故
    "死亡", "遗体", "被杀", "命案", "遇害", "坠楼", "车祸","受害","爱泼斯坦","离世"

    # 强争议词
    "怒斥", "崩", "谣言", "辟谣"
]

def get_baidu_hot():
    url = "https://top.baidu.com/api/board?platform=wise&tab=realtime"
    headers = {
        "User-Agent": "Mozilla/5.0"
    }
    return requests.get(url, headers=headers).json()

def filter1(raw_list):
    filtered_list=[]
    for item in raw_list:
        title=item.get("word","")

        if is_valid_topic(title):
            filtered_list.append(item)
    return filtered_list

def is_valid_topic(title:str)->bool:
    # 关键词过滤
    for word in BLOCK_KEYWORDS:
        if word in title:
            return False

    # 过滤过度政治色彩（简单规则）
    if re.search(r"(中国|美国|德国|日本|乌克兰).*?(访|会见|发表)", title):
        return False

    return True


if __name__=='__main__':
    #获取数据
    raw_data=get_baidu_hot()
    raw_hotspot_list=raw_data["data"]["cards"][0]["content"][0]["content"]
    #数据第一步简单清洗
    filled_hotspot_list=filter1(raw_hotspot_list)
    print(len(filled_hotspot_list))
    print(json.dumps(filled_hotspot_list,ensure_ascii=False,indent=2))
