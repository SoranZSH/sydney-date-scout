"""采集器注册表模式。新增采集器类型：写函数 + `@collector("type_name")` 装饰即可。"""
from __future__ import annotations

from typing import Callable

REGISTRY: dict[str, Callable] = {}


def collector(name: str):
    def deco(fn: Callable) -> Callable:
        REGISTRY[name] = fn
        return fn
    return deco


def get_collector(type_name: str) -> Callable:
    if type_name not in REGISTRY:
        raise KeyError(f"未知采集器类型: {type_name}（已注册: {sorted(REGISTRY)}）")
    return REGISTRY[type_name]


from . import generic, astro, fixtures, cityofsydney  # noqa: E402,F401  触发注册
