# 内容生成服务：视频(SeedDance)、图片(SeedDance)、文字(LLM)
from .prompt_templates import build_video_prompt, build_image_prompt, build_text_prompt
from .generation_service import (
    get_brand_for_store,
    create_video_generation,
    create_image_generation,
    create_text_generation,
    get_generation_by_id,
    list_generations,
    refresh_video_status,
)

__all__ = [
    "build_video_prompt",
    "build_image_prompt",
    "build_text_prompt",
    "get_brand_for_store",
    "create_video_generation",
    "create_image_generation",
    "create_text_generation",
    "get_generation_by_id",
    "list_generations",
    "refresh_video_status",
]


def list_contents():
    return None


def create_content():
    return None