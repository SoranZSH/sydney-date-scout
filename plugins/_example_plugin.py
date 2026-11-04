"""Python 插件模板 —— 想在本进程里直接写采集逻辑时用（不想走 JSON 收件箱）。

用法：复制本文件为 plugins/my_source.py（去掉开头的下划线才会被加载），
改好 fetch()，然后在 sources.yaml 加一段：

    - id: my_source
      type: my_source          # 与下面 @collector 的名字一致
      enabled: true
      category_hint: experience

启动时 scout 会自动导入 plugins/ 下所有不以 _ 开头的 .py。
插件只负责「拿到原始数据并映射成契约字典」，校验、推断、去重、评分全部复用主程序：
parse_record() 与外部 JSON 接口走的是同一套契约（docs/event.schema.json）。
"""
from __future__ import annotations

import logging

from scout.collectors import collector
from scout.collectors.external import FeedError, parse_record

log = logging.getLogger(__name__)


def fetch(source: dict, cfg: dict) -> list[dict]:
    """在这里调用你的爬虫库或官方 API，返回契约字典列表。"""
    return []


@collector("my_source")
def my_source_collector(source: dict, cfg: dict):
    events = []
    for rec in fetch(source, cfg):
        try:
            events.append(parse_record(rec, source, cfg))
        except FeedError as ex:
            log.warning("%s：跳过一条记录 —— %s", source["id"], ex)
    return events
