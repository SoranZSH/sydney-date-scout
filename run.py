#!/usr/bin/env python3
"""悉尼约会提案助理 CLI。

  python run.py run --dry-run          # 跑一次，只生成 HTML 不发信
  python run.py run                    # 跑一次并发信
  python run.py run --respect-interval # 未到间隔天数则跳过（配合每日 cron）
  python run.py serve                  # 常驻循环，每 interval_days 跑一次
  python run.py check-sources          # 校验 sources.yaml 中各源是否可用
  python run.py explain --uid <uid>    # 打印某活动的逐维度评分依据
  python run.py history                # 已推送项目的分数历史（跨批次可比性自检）
  python run.py test-email             # 诊断 SMTP 连通性并发一封测试邮件
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from scout.config import db_path, load_config
from scout.pipeline import run_once
from scout.store import Store

from scout.paths import default_config_path

DEFAULT_CFG = str(default_config_path())


def _setup_log(verbose: bool) -> None:
    """控制台 + 文件双通道。

    文件通道是必需的：定时任务用 pythonw.exe 运行（不弹控制台窗口），
    这种进程根本没有 stdout/stderr —— 只往控制台打日志的话，
    定时运行一旦出错就是彻底静默，什么都查不到。
    """
    level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)

    try:
        from logging.handlers import RotatingFileHandler

        from scout.paths import data_dir
        log_path = data_dir() / "data" / "out" / "scout.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # 单文件 1MB、留 3 份，长期无人值守也不会把磁盘写满
        fileh = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3,
                                    encoding="utf-8")
        fileh.setFormatter(fmt)
        root.addHandler(fileh)
    except OSError as ex:                                  # 磁盘只读等极端情况
        logging.warning("无法写入日志文件：%s", ex)


def cmd_run(args) -> int:
    cfg = load_config(args.config)
    if args.respect_interval:
        store = Store(db_path(cfg))
        last = store.last_run_at()
        interval = timedelta(days=cfg.get("interval_days", 3))
        if last and datetime.now() - last < interval:
            nxt = last + interval
            logging.info("距上次运行 %s 未满 %s，跳过。下次：%s",
                         last.strftime("%m-%d %H:%M"), interval, nxt.strftime("%m-%d %H:%M"))
            return 0
    stats = run_once(cfg, dry_run=args.dry_run, check_urls=args.check_urls,
                     only_sources=args.source)
    print(json.dumps({k: v for k, v in stats.items() if k != "errors"},
                     ensure_ascii=False, indent=2))
    if stats["errors"]:
        print("采集告警：", *stats["errors"], sep="\n  ")
    return 0


def cmd_serve(args) -> int:
    cfg = load_config(args.config)
    interval = cfg.get("interval_days", 3) * 86400
    while True:
        try:
            run_once(cfg, dry_run=args.dry_run)
        except Exception:                                 # noqa: BLE001
            logging.exception("本轮运行失败，将在下个周期重试")
        logging.info("休眠 %d 天", cfg.get("interval_days", 3))
        time.sleep(interval)


def cmd_check_sources(args) -> int:
    import requests
    cfg = load_config(args.config)
    ok = 0
    for src in cfg["sources"]:
        if src["type"] in ("fixtures", "astro"):
            print(f"  ○ {src['id']:28} 本地源，跳过"); continue
        if not src.get("enabled", True):
            print(f"  – {src['id']:28} 已禁用"); continue
        # 大部分源是单个 url；专用采集器（如 cityofsydney）可能配的是一组 list_urls。
        urls = src.get("list_urls") or ([src["url"]] if src.get("url") else [])
        if not urls:
            print(f"  ? {src['id']:28} 该源没有可校验的 URL（由采集器自行决定抓取地址）")
            continue
        for url in urls:
            label = src["id"] if len(urls) == 1 else f"{src['id']}[{urls.index(url)}]"
            try:
                r = requests.get(url, timeout=15,
                                 headers={"User-Agent": "sydney-date-scout/1.0"})
                mark = "✓" if r.status_code < 400 else "✗"
                ok += r.status_code < 400
                print(f"  {mark} {label:28} {r.status_code} {url}")
            except Exception as ex:                        # noqa: BLE001
                print(f"  ✗ {label:28} {type(ex).__name__}: {ex}")
    print(f"\n可用 URL {ok} 个。不可用的请修正地址或设 enabled:false。")
    return 0


def cmd_explain(args) -> int:
    cfg = load_config(args.config)
    store = Store(db_path(cfg))
    row = store.db.execute("SELECT payload FROM events WHERE uid=?", (args.uid,)).fetchone()
    if not row:
        print("未找到该 uid"); return 1
    data = json.loads(row["payload"])
    print(f"{data['title']}  总分 {data['total']} (rubric 固定锚点)")
    for dim, s in data["scores"].items():
        if dim.startswith("_"):
            continue
        print(f"  {s['label']:6} {s['score']:>4}/10  ×{s['weight']:<5} {s['reason']}")
    return 0


def cmd_history(args) -> int:
    cfg = load_config(args.config)
    store = Store(db_path(cfg))
    rows = store.score_history(args.limit)
    if not rows:
        print("暂无推送历史。"); return 0
    print(f"{'推送日期':<12}{'分数':<7}{'rubric':<9}标题")
    for r in rows:
        print(f"{r['sent_at'][:10]:<12}{r['total']:<7}{r['rubric_version']:<9}{r['title'][:44]}")
    versions = {r["rubric_version"] for r in rows}
    print("\n分数可比性：" + ("✓ 全部同一 rubric 版本，可直接比较"
                              if len(versions) == 1 else f"⚠ 存在多个版本 {versions}，跨版本不可直接比较"))
    return 0


def cmd_test_email(args) -> int:
    """诊断 SMTP 连通性并发一封测试邮件。"""
    import socket

    from scout.config import resolve_env
    from scout.mailer import send_digest

    cfg = load_config(args.config)
    try:
        email_cfg = resolve_env(cfg["email"])
    except RuntimeError as ex:
        print(f"✗ 配置解析失败：{ex}"); return 1

    host, port = email_cfg["smtp_host"], int(email_cfg["smtp_port"])
    proxy = email_cfg.get("proxy")

    print(f"目标 SMTP：{host}:{port}")
    print(f"代理配置：{proxy if proxy else '未配置（直连）'}\n")

    # 1) 先测直连，判断是否被网络限制
    print("[1/2] 测试直连 …")
    try:
        sock = socket.create_connection((host, port), timeout=10)
        sock.close()
        print("  ✓ 直连可用")
    except Exception as ex:                                # noqa: BLE001
        print(f"  ✗ 直连失败：{type(ex).__name__}: {ex}")
        if not proxy:
            print("\n  国内网络到境外 SMTP 端口常被限制。两个解决方向：")
            print("  A. 在 config.yaml 的 email 下加 proxy 配置走本地代理（见 README 第 3 节）")
            print("  B. 换用国内邮箱发信（QQ/163），收件人仍可以是 Gmail")
            return 1

    # 2) 实际发信（走配置里的代理，如果有）
    print("\n[2/2] 发送测试邮件 …")
    html = ("<h2>悉尼约会提案助理 · 测试邮件</h2>"
            "<p>如果你收到这封邮件，说明 SMTP 配置完全正常，可以去设定时任务了。</p>")
    try:
        send_digest({"email": email_cfg}, "悉尼约会提案助理 · 测试邮件", html)
        print(f"  ✓ 已发送至 {email_cfg['to']}，请查收（也看一下垃圾邮件箱）")
        return 0
    except Exception as ex:                                # noqa: BLE001
        print(f"  ✗ 发送失败：{type(ex).__name__}: {ex}")
        return 1


def cmd_train_datefit(args) -> int:
    """Track A：用 GUI 里点出来的 👍/👎 标签训练内容判别模型。"""
    from scout import datefit

    cfg = load_config(args.config)
    store = Store(db_path(cfg))
    samples = store.training_samples()
    n_pos = sum(y for _, y in samples)
    n_neg = len(samples) - n_pos
    if len(samples) < args.min_labels or n_pos < 5 or n_neg < 5:
        print(f"标签还不够：共 {len(samples)} 条（👍 {n_pos} / 👎 {n_neg}）。")
        print(f"建议攒到 ≥{args.min_labels} 条、且两类各 ≥5 条再训练，"
              "平时多在桌面程序里点 👍/👎。")
        return 1
    model = datefit.train(samples)
    path = datefit.personal_model_path(cfg)
    datefit.save_model(model, path)
    print(f"训练完成：{len(samples)} 条标签（👍 {n_pos} / 👎 {n_neg}），"
          f"训练集准确率 {model['train_acc']:.0%}")
    print(f"个人模型已保存：{path}（下轮运行自动生效）")
    if args.freeze:
        fpath = datefit.frozen_model_path(cfg)
        datefit.save_model(model, fpath)
        print(f"已冻结为冷启动模型：{fpath}")
        print("它会随仓库分发给其他用户。记得 git add models/datefit.json 并提交。")
    return 0


def cmd_affinity(args) -> int:
    """Track B：打印当前的类别亲和度（个性化是可解释的，随时能看）。"""
    from scout.personalize import category_affinity

    cfg = load_config(args.config)
    store = Store(db_path(cfg))
    aff = category_affinity(store)
    if not aff:
        print("还没有行为数据（查看/点链接/👍/👎 都会累积）。")
        return 0
    print("类别亲和度（-1 到 +1，影响批内排序，不影响分数）：")
    for cat, a in sorted(aff.items(), key=lambda kv: -kv[1]):
        print(f"  {cat:<12} {a:+.2f}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="悉尼约会提案助理")
    p.add_argument("-c", "--config", default=DEFAULT_CFG)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="执行一次完整管线")
    r.add_argument("--dry-run", action="store_true", help="只生成 HTML，不发信")
    r.add_argument("--check-urls", action="store_true", help="联网校验链接可达性")
    r.add_argument("--respect-interval", action="store_true", help="未到间隔天数则跳过")
    r.add_argument("--source", action="append", help="只跑指定源（可重复）")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("serve", help="常驻定时循环")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_serve)

    sub.add_parser("check-sources", help="校验采集源可用性").set_defaults(func=cmd_check_sources)

    e = sub.add_parser("explain", help="打印评分依据")
    e.add_argument("--uid", required=True)
    e.set_defaults(func=cmd_explain)

    h = sub.add_parser("history", help="推送历史与可比性自检")
    h.add_argument("--limit", type=int, default=50)
    h.set_defaults(func=cmd_history)

    sub.add_parser("test-email", help="诊断 SMTP 连通性并发一封测试邮件").set_defaults(func=cmd_test_email)

    t = sub.add_parser("train-datefit", help="用 👍/👎 标签训练内容判别模型（Track A）")
    t.add_argument("--min-labels", type=int, default=30, help="最少标签数（默认 30）")
    t.add_argument("--freeze", action="store_true",
                   help="同时冻结到 models/datefit.json 作为随仓库分发的冷启动模型")
    t.set_defaults(func=cmd_train_datefit)

    sub.add_parser("affinity", help="查看类别亲和度（Track B 个性化，当前状态）"
                   ).set_defaults(func=cmd_affinity)

    args = p.parse_args()
    _setup_log(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
