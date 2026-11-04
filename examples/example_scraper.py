#!/usr/bin/env python3
"""外部爬虫的参考实现 —— 演示「交货格式」，不访问任何网站。

本项目只认 docs/event.schema.json 定义的 JSON。真实爬虫用什么工具都行，
只要最后输出成这个样子。两种接法：

    # 收件箱模式（sources.yaml 的 external_inbox，默认已开启）
    python examples/example_scraper.py > data/inbox/example.jsonl

    # 命令模式（sources.yaml 的 external_command，把 command 改成这一行即可）
    command: "python examples/example_scraper.py"

    # 自检：契约是否正确、每条能拿几分
    python run.py validate-feed data/inbox/example.jsonl

写真实爬虫时请把 fetch_events() 换成你的抓取逻辑，并保留 polite_get 里的三件事：
先查 robots.txt、标明 User-Agent、控制请求频率。服务条款禁止自动化访问的站点不要接，
有官方 API 的优先用 API。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.robotparser
from datetime import datetime, timedelta
from urllib.parse import urlsplit

USER_AGENT = "my-date-scout-scraper/0.1 (+your-contact)"   # 换成你自己的联系方式
REQUEST_DELAY_SECONDS = 1.0
_robots: dict[str, urllib.robotparser.RobotFileParser] = {}


def polite_get(url: str):
    """礼貌抓取的骨架：robots.txt 不允许就跳过，每次请求之间留间隔。"""
    import requests                                  # 只在真的联网时才需要

    parts = urlsplit(url)
    root = f"{parts.scheme}://{parts.netloc}"
    rp = _robots.get(root)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser(root + "/robots.txt")
        rp.read()
        _robots[root] = rp
    if not rp.can_fetch(USER_AGENT, url):
        print(f"robots.txt 不允许抓取，跳过：{url}", file=sys.stderr)
        return None
    time.sleep(REQUEST_DELAY_SECONDS)
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)


def fetch_events() -> list[dict]:
    """演示数据：虚构活动，日期相对今天生成，所以任何时候跑都在推荐窗口内。"""
    today = datetime.now().replace(minute=0, second=0, microsecond=0)

    def at(days: int, hour: int, minute: int = 0) -> datetime:
        return (today + timedelta(days=days)).replace(hour=hour, minute=minute)

    sat = (5 - today.weekday()) % 7 or 7            # 下一个周六
    return [
        {   # 字段最全的写法：评分最准
            "title": "海港夜色爵士音乐会",
            "start": at(sat, 19, 30).isoformat(),
            "end": at(sat, 21, 30).isoformat(),
            "url": "https://example.com/events/harbour-jazz-night",
            "venue": "示例海滨剧场", "suburb": "Barangaroo",
            "lat": -33.8637, "lng": 151.2010,
            "price": "From $35",
            "category": ["Music", "Nightlife"],
            "description": "露天座位，面朝海港，中场有饮品吧。",
            "dates": [at(sat, 19, 30).date().isoformat(), at(sat + 1, 19, 30).date().isoformat()],
            "source_name": "example-venue-site",
        },
        {   # 最少字段 + 显式信号：爬虫知道的就直接给
            "title": "双人陶艺拉坯体验课",
            "start": at(sat + 1, 14).isoformat(),
            "url": "https://example.com/events/pottery-for-two",
            "suburb": "Marrickville", "lat": -33.9110, "lng": 151.1550,
            "price": 45, "category": "Workshop",
            "signals": {"interaction": "co_create", "noise_level": "quiet", "has_seating": True},
            "source_name": "example-booking-platform",
        },
        {   # 这条会被「约会适配闸门」剔除：示范 validate-feed 的提示
            "title": "Kids Science Fun Day (ages 5-10)",
            "start": at(3, 10).isoformat(),
            "url": "https://example.com/events/kids-science",
            "price": 0, "category": "Family",
        },
    ]


def main() -> int:
    for rec in fetch_events():
        print(json.dumps(rec, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
