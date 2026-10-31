"""Track A：内容判别模型（NLP + 监督学习，v1.3.0）。

目标：用你自己点出来的 👍/👎 标签，训练一个「这条信息对约会有没有价值」的
判别器；用一段时间、攒够标签后 `python run.py train-datefit --freeze` 把模型
固化到 `models/datefit.json`（随 git 分发），成为发布后其他用户的**冷启动模型**。

设计约束：
- 零第三方依赖（纯 Python 逻辑回归 + 特征哈希），打包进 exe 不增加体积；
  后续升级路径：词袋 → sentence embedding → LLM 蒸馏，接口不变。
- 确定性：同样的标签训出同样的模型（不用随机初始化/打乱）。
- 作用在**筛选/排序层**：rubric 分数一个字节都不碰，跨批次可比性不受影响。

模型查找顺序（pipeline 运行时）：
1. `data/datefit-model.json` —— 本机个人模型，继续学习用；
2. `models/datefit.json`     —— 冻结的冷启动模型，随仓库分发。
都不存在时整个环节跳过，行为与 v1.2.0 完全一致。
"""
from __future__ import annotations

import json
import math
import re
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

DIMS = 1 << 15          # 特征哈希桶数
FORMAT = "datefit-logreg"

_TOKEN_RE = re.compile(r"[a-z0-9]+|[一-鿿]", re.I)


# --------------------------------------------------------------------------
# 特征
# --------------------------------------------------------------------------
def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _hash(token: str) -> int:
    return zlib.crc32(token.encode("utf-8")) % DIMS


def extract_features(fields: dict[str, Any]) -> set[int]:
    """fields: title / description / category / venue_name /
    price_per_person / interaction（缺哪个都行）。返回命中的特征桶集合。"""
    toks = _tokens(fields.get("title", "")) + _tokens(fields.get("description", ""))
    feats = {_hash(f"w={t}") for t in toks}
    feats |= {_hash(f"b={a}_{b}") for a, b in zip(toks, toks[1:])}

    if fields.get("category"):
        feats.add(_hash(f"cat={fields['category']}"))
    venue = (fields.get("venue_name") or "").lower()
    for vt in ("community", "library", "centre", "center", "hall", "museum",
               "gallery", "theatre", "bar", "park", "garden", "studio"):
        if vt in venue:
            feats.add(_hash(f"venue~{vt}"))
    price = fields.get("price_per_person")
    if price is not None:
        bucket = "free" if price == 0 else "low" if price <= 20 else \
                 "mid" if price <= 60 else "high"
        feats.add(_hash(f"price={bucket}"))
    if fields.get("interaction"):
        feats.add(_hash(f"inter={fields['interaction']}"))
    return feats


# --------------------------------------------------------------------------
# 训练 / 预测（L2 正则逻辑回归，全批量梯度下降，确定性）
# --------------------------------------------------------------------------
def _sigmoid(z: float) -> float:
    if z < -30:
        return 0.0
    if z > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def train(samples: list[tuple[dict[str, Any], int]], epochs: int = 300,
          lr: float = 0.5, l2: float = 1e-4) -> dict[str, Any]:
    """samples: [(fields, label 0/1), ...]。返回可 JSON 序列化的模型。"""
    featsets = [extract_features(f) for f, _ in samples]
    labels = [y for _, y in samples]
    n = len(samples)
    w: dict[int, float] = {}
    b = 0.0
    for epoch in range(epochs):
        gw: dict[int, float] = {}
        gb = 0.0
        for feats, y in zip(featsets, labels):
            p = _sigmoid(b + sum(w.get(i, 0.0) for i in feats))
            err = p - y
            gb += err
            for i in feats:
                gw[i] = gw.get(i, 0.0) + err
        step = lr / (1 + 0.01 * epoch)
        b -= step * (gb / n)
        for i, g in gw.items():
            w[i] = w.get(i, 0.0) - step * (g / n + l2 * w.get(i, 0.0))

    correct = sum(
        1 for feats, y in zip(featsets, labels)
        if (_sigmoid(b + sum(w.get(i, 0.0) for i in feats)) >= 0.5) == bool(y)
    )
    return {
        "format": FORMAT, "version": 1, "dims": DIMS,
        "bias": round(b, 6),
        "weights": {str(i): round(v, 6) for i, v in w.items() if abs(v) > 1e-6},
        "n_pos": sum(labels), "n_neg": n - sum(labels),
        "train_acc": round(correct / n, 3),
        "trained_at": datetime.now().isoformat(timespec="seconds"),
    }


def predict(model: dict[str, Any], fields: dict[str, Any]) -> float:
    """返回 P(这条内容有价值) ∈ [0,1]。"""
    w = model["weights"]
    z = model["bias"] + sum(w.get(str(i), 0.0) for i in extract_features(fields))
    return _sigmoid(z)


def predict_event(model: dict[str, Any], e: Any) -> float:
    return predict(model, {
        "title": e.title, "description": e.description, "category": e.category,
        "venue_name": e.venue_name, "price_per_person": e.price_per_person,
        "interaction": e.interaction,
    })


# --------------------------------------------------------------------------
# 存取
# --------------------------------------------------------------------------
def personal_model_path(cfg: dict) -> Path:
    return Path(cfg["_data_dir"]) / "data" / "datefit-model.json"


def frozen_model_path(cfg: dict) -> Path:
    return Path(cfg["_base_dir"]) / "models" / "datefit.json"


def load_model(cfg: dict) -> Optional[dict[str, Any]]:
    if not cfg.get("datefit", {}).get("enabled", True):
        return None
    for path in (personal_model_path(cfg), frozen_model_path(cfg)):
        try:
            if path.exists():
                model = json.loads(path.read_text(encoding="utf-8"))
                if model.get("format") == FORMAT:
                    return model
        except (OSError, json.JSONDecodeError):
            continue
    return None


def save_model(model: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
