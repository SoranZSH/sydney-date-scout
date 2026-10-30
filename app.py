#!/usr/bin/env python3
"""悉尼约会提案助理 —— 打包成 exe 时的统一入口。

同一个 exe 有两种用法，靠参数区分：

    悉尼约会助理.exe            双击打开，浏览当前推荐与历史
    悉尼约会助理.exe --run      跑一次采集，有新推荐就弹窗（给定时任务用）
    悉尼约会助理.exe --run --respect-interval
                                同上，但未满 interval_days 天则直接跳过

开发环境下等价于 `python gui.py` 和 `python run.py run`。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    argv = sys.argv[1:]

    if "--run" in argv:
        import logging
        from datetime import datetime, timedelta

        from scout.config import db_path, load_config
        from scout.paths import data_dir, default_config_path
        from scout.pipeline import run_once
        from scout.store import Store

        # 定时任务是无人值守的，日志落文件方便事后排查
        log_path = data_dir() / "data" / "out" / "scout.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO, filename=str(log_path), encoding="utf-8",
            format="%(asctime)s %(levelname)-7s %(message)s",
        )

        cfg = load_config(str(default_config_path()))

        if "--respect-interval" in argv:
            store = Store(db_path(cfg))
            last = store.last_run_at()
            store.close()
            interval = timedelta(days=cfg.get("interval_days", 3))
            if last and datetime.now() - last < interval:
                logging.info("距上次运行未满 %s，跳过", interval)
                return 0

        stats = run_once(cfg)
        logging.info("完成：推荐 %s 个，批次 %s", stats.get("recommended"), stats.get("batch_id"))
        for err in stats.get("errors", []):
            logging.warning("告警：%s", err)
        return 0

    # 默认（含 --gui）：打开界面
    from gui import ScoutApp
    from scout.paths import default_config_path, enable_dpi_awareness

    enable_dpi_awareness()
    app = ScoutApp(str(default_config_path()))
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
