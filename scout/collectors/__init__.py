"""采集器注册表模式。新增采集器类型：写函数 + `@collector("type_name")` 装饰即可。

接真实数据有两条路（详见 README「接入真实数据」）：
1. 不写 Python：让任意爬虫按 docs/event.schema.json 产出 JSON，用内置的
   `type: external` 读取（collectors/external.py）。
2. 写 Python 插件：在项目根的 `plugins/` 目录放一个 .py，用 `@collector("名字")`
   注册，启动时自动加载，不用改本包任何代码。以 `_` 开头的文件会被跳过（留作示例）。
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Callable, Iterable

log = logging.getLogger(__name__)

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


_loaded_plugins: set[str] = set()


def load_plugins(dirs: Iterable[Path]) -> list[str]:
    """导入 plugins/ 目录下的采集器插件（幂等）。单个插件出错只告警，不拖垮整轮运行。"""
    loaded: list[str] = []
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.py")):
            key = str(f.resolve())
            if f.name.startswith("_") or key in _loaded_plugins:
                continue
            _loaded_plugins.add(key)
            try:
                spec = importlib.util.spec_from_file_location(f"scout_plugin_{f.stem}", f)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)                 # type: ignore[union-attr]
                loaded.append(f.name)
                log.info("已加载采集器插件 %s", f.name)
            except Exception as ex:                          # noqa: BLE001
                log.warning("采集器插件 %s 加载失败：%s: %s", f.name, type(ex).__name__, ex)
    return loaded


from . import generic, astro, fixtures, external  # noqa: E402,F401  触发注册
