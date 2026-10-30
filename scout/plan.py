"""约会方案生成：时间线 / 预算拆解 / 备选方案 / 话题 / 适合对象。

可选调用 Anthropic API 对文案做润色（llm.enabled=true 时），任何失败
（未装包 / 无 key / 网络错误）都静默回退到结构化文案，不影响主流程。
"""
from __future__ import annotations

import logging
from datetime import timedelta

from .models import Event

log = logging.getLogger(__name__)

CATEGORY_AUDIENCE = {
    "film": ["影迷", "喜欢安静氛围的两人"],
    "performance": ["文艺爱好者", "想认真打扮一次的约会"],
    "exhibition": ["艺术爱好者", "喜欢慢节奏边看边聊"],
    "festival": ["喜欢热闹人群氛围的两人"],
    "market": ["喜欢边走边逛边尝鲜的两人"],
    "food": ["吃货", "美食探店爱好者"],
    "venue": ["喜欢探索悉尼新地标的两人"],
    "experience": ["喜欢动手互动、留下共同回忆的两人"],
    "sport": ["运动爱好者", "喜欢现场比赛氛围的两人"],
    "nature": ["喜欢户外与自然风光的两人"],
    "sale": ["喜欢淘好物、逛街的两人"],
    "daytrip": ["有大半天空闲、喜欢短途出游的两人"],
}

CATEGORY_TOPICS = {
    "film": ["刚看完的这部片子，你最喜欢哪个角色/镜头？", "如果让你重拍结局，你会怎么改？"],
    "performance": ["你更喜欢哪种现场表演形式？", "有没有想再看一次的演出？"],
    "exhibition": ["这次展览里最打动你的是哪一件作品？", "如果能带走一件展品，你选哪个？"],
    "festival": ["今天最想打卡的是哪个摊位/舞台？", "你参加过最好玩的节庆是什么？"],
    "market": ["你平时喜欢逛这种市集吗？", "今天想不想挑一件礼物送给对方？"],
    "food": ["这家店你觉得哪道菜最惊艳？", "你心目中悉尼最好吃的一家店是哪里？"],
    "venue": ["对这个新地方你的第一印象是什么？", "你希望这里以后能有什么活动？"],
    "experience": ["这次动手做的过程你最喜欢哪一步？", "成品你打算怎么用/放哪？"],
    "sport": ["你平时有关注这项运动吗？", "现场看和看转播感觉差多少？"],
    "nature": ["你喜欢这种远离城市的安静吗？", "下次还想来看什么自然景观？"],
    "sale": ["今天最想入手的是什么？", "你更看重打折力度还是款式？"],
    "daytrip": ["这趟短途你最期待哪一段？", "下次想不想走更远一点？"],
}

WEATHER_SENSITIVE_CATEGORIES = {"festival", "market", "nature", "daytrip", "sport"}


def _area_anchor(cfg: dict, suburb: str) -> dict:
    anchors = cfg.get("area_anchors", {})
    return anchors.get(suburb) or anchors.get("default", {"pre": [], "post": []})


def _recommend_reason(e: Event) -> str:
    dims = [(k, v) for k, v in e.scores.items() if not k.startswith("_")]
    dims.sort(key=lambda kv: kv[1]["score"] * kv[1]["weight"], reverse=True)
    parts = [f"{d['label']} {d['score']}/10（{d['reason']}）" for _, d in dims[:2]]
    return "；".join(parts)


def _build_timeline(e: Event, cfg: dict, anchor: dict) -> list[dict]:
    if not e.start:
        return []
    buffer_min = cfg.get("meet_buffer_minutes", 45)
    meet_time = e.start - timedelta(minutes=buffer_min)
    timeline = [
        {"time": meet_time.strftime("%H:%M"), "label": "碰头 / 前置",
         "detail": f"建议先到「{anchor['pre'][0]}」" if anchor.get("pre") else "在活动场地附近碰头"},
        {"time": e.start.strftime("%H:%M"), "label": "主活动",
         "detail": f"{e.title} @ {e.venue_name or e.suburb}"},
    ]
    if e.duration_hours:
        end_time = e.start + timedelta(hours=e.duration_hours)
        timeline.append({
            "time": end_time.strftime("%H:%M"), "label": "续摊",
            "detail": f"「{anchor['post'][0]}」" if anchor.get("post") else "附近找地方续摊",
        })
    return timeline


def _build_budget(e: Event, cfg: dict) -> dict:
    pre = cfg.get("estimated_pre_cost_aud", 0)
    post = cfg.get("estimated_post_cost_aud", 0)
    transit = cfg.get("estimated_transit_cost_aud", 0)
    main = e.price_per_person or 0
    per_person = pre + main + post + transit
    return {
        "pre_aud": pre, "main_aud": main, "post_aud": post, "transit_aud": transit,
        "per_person_total_aud": round(per_person, 1),
        "couple_total_aud": round(per_person * 2, 1),
    }


def _build_alternates(e: Event, anchor: dict) -> dict | None:
    if e.category not in WEATHER_SENSITIVE_CATEGORIES:
        return None
    rainy = anchor.get("rainy")
    return {"rainy_plan": rainy} if rainy else None


def build_plan(e: Event, cfg: dict) -> Event:
    anchor = _area_anchor(cfg, e.suburb)
    e.audience = list(CATEGORY_AUDIENCE.get(e.category, ["喜欢一起尝试新鲜事的两人"]))
    if e.solo_oriented:
        e.audience.append("更适合先自己探路，再决定要不要一起去")

    plan = {
        "reason": _recommend_reason(e),
        "timeline": _build_timeline(e, cfg, anchor),
        "budget": _build_budget(e, cfg),
        "alternates": _build_alternates(e, anchor),
        "topics": CATEGORY_TOPICS.get(e.category, ["今天感觉怎么样？", "下次还想不想来这种活动？"]),
    }

    if cfg.get("llm", {}).get("enabled"):
        plan = _llm_polish(plan, cfg)

    e.plan = plan
    return e


def _llm_polish(plan: dict, cfg: dict) -> dict:
    import os

    llm_cfg = cfg.get("llm", {})
    api_key = os.environ.get(llm_cfg.get("api_key_env", "ANTHROPIC_API_KEY"))
    if not api_key:
        log.warning("llm.enabled=true 但未设置 %s，跳过润色", llm_cfg.get("api_key_env"))
        return plan
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        prompt = (
            "请把下面这份约会方案改写成 2-3 句自然流畅的中文文案，"
            f"保留关键信息（时间、地点、预算），不要新增没有的信息：\n{plan}"
        )
        resp = client.messages.create(
            model=llm_cfg.get("model", "claude-sonnet-5"),
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        plan["narrative"] = resp.content[0].text.strip()
    except Exception:                                  # noqa: BLE001
        log.warning("LLM 润色失败，回退到结构化文案", exc_info=True)
    return plan
