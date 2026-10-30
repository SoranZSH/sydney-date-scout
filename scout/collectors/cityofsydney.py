"""City of Sydney「What's On」采集器 —— 已从公开仓库移除。

该站点的使用条款禁止对网站使用自动化脚本工具，所以专用适配器不随公开仓库分发。
历史版本保留此占位，使旧版本检出后仍可导入运行；接入真实数据请使用
v1.5.0 起的外部爬虫接口（type: external，见 README「接入真实数据」）。
"""
from __future__ import annotations

from . import collector


@collector("cityofsydney")
def cityofsydney_collector(source: dict, cfg: dict) -> list:
    raise RuntimeError("cityofsydney 采集器已从公开仓库移除（目标站点条款禁止自动化访问），"
                       "请改用 type: external，见 CHANGELOG v1.5.0")
