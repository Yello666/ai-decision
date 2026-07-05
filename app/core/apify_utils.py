"""Apify actor.call() 返回值读取辅助（兼容 dict 与 Pydantic Run 模型）。"""
from __future__ import annotations

from typing import Any


def apify_run_field(run: Any, *names: str) -> Any:
    """从 Apify ``ActorClient.call()`` 结果读取字段。

    新版 apify_client 返回 Pydantic ``Run`` 对象（snake_case 属性）；
    旧版返回 dict（camelCase 键）。按 ``names`` 顺序尝试，返回第一个非 None 值。
    """
    for name in names:
        if isinstance(run, dict):
            val = run.get(name)
        else:
            val = getattr(run, name, None)
        if val is not None:
            return val
    return None
