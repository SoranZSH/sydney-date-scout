# 运维手册：安装、定时运行、配置与排障

> README 讲「是什么、为什么」，这里讲「怎么跑起来、出了问题怎么查」。
> 接入真实数据见 README「接入真实数据」。

## 1. 安装与运行

### 用打包好的 exe（不需要装 Python）

```
悉尼约会助理.exe                        双击打开，浏览当前推荐与历史
悉尼约会助理.exe --run                  跑一次采集，有新推荐就弹窗
悉尼约会助理.exe --run --respect-interval   同上，但未满 3 天则跳过（给定时任务用）
```

首次运行会在 exe 旁边自动生成 `config.yaml`（改预算、住址、区域锚点都在这里）
和 `data/`（数据库与运行日志）。这两样在 exe 外面，换新版 exe 不会丢历史。

自己重新打包：

```bash
pip install pyinstaller
pyinstaller build_exe.spec --noconfirm    # 产物在 dist/
```

### 用源码

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml     # 改 home_suburb / 预算 / 距离 / 偏好

python gui.py                          # 打开桌面程序
python run.py run --dry-run            # 用内置离线样例跑通全链路，不弹窗不发信
python tests/test_scoring.py           # 跑不变式测试
```

`--dry-run` 首次运行（内置 `fixtures/sample_events.yaml` 13 条 + `astro` 源 1 条）输出类似：

```json
{
  "collected": 14,
  "after_dedupe": 14,
  "verified_passed": 8,
  "excluded_breakdown": {"硬排除类别": 2, "超预算": 1, "超距离": 2, "已过期": 1},
  "recommended": 4,
  "top_score": 78.4,
  "below_threshold_scores": [66.6, 65.8, 61.8],
  "vetoed": ["年度品牌折扣清仓周：约会氛围 2.0 低于下限 5"],
  "output_file": "data/out/digest-2026...html"
}
```

注意 `below_threshold_scores` 里的 66.6：**差 1.4 分也不推**（阈值 68）。这是设计，不是 bug。
「已过期」那条是 `sources.yaml` 里的天文日历示例，每年按当年日期更新即可。
`python run.py explain --uid <events 表里的 uid>` 可以打印任意一条（包括未达标的）逐维度评分依据。

### 通知方式

默认是**桌面弹窗**：跑完有新推荐就在屏幕右下角弹出，列出分数与标题，
点「查看详情」直接打开桌面程序看完整方案。在 `config.yaml` 里调：

```yaml
notify:
  enabled: true
  auto_close_seconds: 0     # 0 = 一直显示到手动关闭；设 30 则 30 秒后自动消失
```

**邮件是可选的，默认关闭**。国内网络直连 Gmail 的 SMTP 端口（587/465）常被限制，
表现为 `TimeoutError / WinError 10060`——注意浏览器能打开 Gmail 不代表这里能连上，
Python 发信走原始 socket，不读 `HTTP_PROXY` 环境变量。想启用先诊断：

```bash
python run.py test-email      # 分别测直连与代理，并发一封测试邮件
```

连不通的两个解法：在 `config.yaml` 的 `email` 下加 `proxy` 走本地代理
（需 `pip install PySocks`），或者改用国内邮箱（QQ/163）发信、收件人仍填 Gmail。

---

## 2. 定时运行

所有方案都用 `--respect-interval`：**每天触发一次，但程序内部的间隔守卫保证只有距上次
运行满 `interval_days`（默认 3 天）才真正执行**。好处是电脑关机错过某天也能自动补上，
而多次触发不会重复推送。

### 长期关机后会不会一次弹一堆窗

不会。四层保障，每一层都验证过：

| 机制 | 效果 |
|---|---|
| `notify()` 每次运行只建一个 tkinter 窗口 | 一轮推荐多少条都只有**一个**弹窗（列前 5 条 + 「另有 N 个」） |
| `max_items: 10` | 单批次最多 10 条 |
| 任务的 `MultipleInstances: IgnoreNew` | 上一个实例还在跑时，新触发直接忽略，不会叠加 |
| `StartWhenAvailable` | 语义是「错过后尽快补跑**一次**」，不是把错过的每天都排队补 |

所以关机 60 天后开机 = 补跑 1 次 = 1 个弹窗；之后 3 天内的每日触发被间隔守卫静默跳过。

> **`record_run()` 必须排在 `notify()` 之前**（见 `pipeline.py`）。
> `notify()` 最后是 `root.mainloop()`，`auto_close_seconds: 0` 时弹窗不关就一直阻塞。
> 早期版本把 `record_run()` 放在它后面，结果是：夜里跑完没人关窗 → 这次运行永远不被记录
> → 间隔守卫下次读到的还是上上次的时间 → **采集白跑一轮，日志里还看不出问题**。
> 这个 bug 在数据库里留下过痕迹：`batches` 表有 8/1 23:43 的批次，`runs` 表却没有那条记录。
> 一轮是否真的跑完，与用户有没有点掉弹窗无关。

**Windows（任务计划程序）**

```powershell
powershell -ExecutionPolicy Bypass -File deploy\register-task.ps1 -UseSource   # 源码版（本机推荐）
powershell -ExecutionPolicy Bypass -File deploy\register-task.ps1              # 有 exe 时优先用 exe
```

> ⚠ **exe 版和源码版的数据目录不是同一个。**
> `scout/paths.py::data_dir()` 在打包后返回 **exe 自己所在的目录**，所以
> `dist\悉尼约会助理.exe` 用的是 `dist\config.yaml` 和 `dist\data\scout.db`；
> 源码版用的是项目根下的 `config.yaml` 和 `data\scout.db`。
> 两边的配置和历史推送记录**各自独立、互不可见** —— 双击 exe 却发现「没有历史推荐」
> 通常就是这个原因。
>
> 本机已装 Python，所以用 `-UseSource`：配置和历史只有一份，
> 而且改了代码不需要重新 `pyinstaller`（改了代码却忘了重新打包，
> 定时任务会一直跑旧逻辑，这种问题很难查）。
> 要用 exe 的话，把 `config.yaml` 和 `data\` 一起复制到 `dist\` 下面。

`-UseSource` 注册的任务**直接调用 `pythonw.exe run.py run --respect-interval`**，
不经过 `deploy\run-scout.bat`。原因是本项目路径含中文：

> cmd.exe 解析 .bat 用的是**当前控制台代码页**（不是文件自身的编码）。
> 简中系统上这个值可能是 936 也可能是 65001，取决于 shell 怎么启动的，
> 所以 .bat 里任何非 ASCII 字节 —— 包括写死的中文路径 —— 都会被解错：
> 行在多字节字符处被截断，`REM` 注释行被当成命令执行，`cd /d` 失效。
> 实测症状是**退出码 1、连日志文件都创建不出来**，纯静默失败。
> 计划任务的动作存在任务 XML 里是 Unicode，直接指向 pythonw.exe 就没有这个问题。

`run-scout.bat` 保留给手动/便携场景，已改成**纯 ASCII**，项目目录由 `%~dp0..` 推导
（不再写死路径），两种代码页下实测都能跑。**改这个文件时不要往里加中文。**

日志由程序自己写（`run.py::_setup_log` → `data\out\scout.log`，1MB 滚动保留 3 份），
不依赖 shell 重定向 —— 定时任务用 `pythonw.exe` 运行，没有 stdout，
只往控制台打日志的话出了错什么都查不到。

```powershell
Start-ScheduledTask -TaskName SydneyDateScout        # 立即测试跑一次
Get-ScheduledTaskInfo -TaskName SydneyDateScout      # 看上次运行结果
Get-Content data\out\scout.log -Tail 30              # 看运行日志
Unregister-ScheduledTask -TaskName SydneyDateScout -Confirm:$false   # 取消定时
```

**Linux / macOS**

```bash
# A. cron
cp deploy/crontab.example /tmp/ct && crontab /tmp/ct

# B. systemd timer
sudo cp deploy/sydney-date-scout.{service,timer} /etc/systemd/system/
sudo systemctl enable --now sydney-date-scout.timer

# C. 常驻进程 / Docker
python run.py serve
docker build -f deploy/Dockerfile -t date-scout . && docker run -d --env-file .env date-scout
```

---

## 3. 可配置项

| 配置 | 说明 |
|---|---|
| `home_suburb` | 交通便利度的计算起点 |
| `max_distance_km` | 超出即剔除（做短途主题时调到 150） |
| `budget_per_person_aud` | 人均预算上限 |
| `categories` | 订阅类别，删掉某项即不再推送 |
| `area_anchors` | 各区的前置咖啡 / 续摊地点，直接影响生成的约会方案 |
| `lookahead_days` | 推荐窗口（默认未来 21 天） |
| `resend_after_days` | 同一活动多久内不重复推（时间/价格变动会重置） |
| `send_when_empty` | 无达标项目时是否仍弹「本次无推荐」说明 |
| `notify.enabled` | 是否弹窗（默认开） |
| `notify.auto_close_seconds` | 弹窗自动关闭秒数，0 = 不自动关 |
| `email.enabled` | 是否同时发邮件（默认关，见第 3 节） |
| `email.proxy` | 发信走本地代理，国内连 Gmail 常需要 |
| `llm.enabled` | 是否用 Anthropic API 润色方案文案（默认关闭） |

---

## 4. 常用命令

```bash
python gui.py                        # 打开桌面程序（当前推荐 + 历史）
python run.py run --dry-run          # 跑一遍不弹窗不发信，只生成 HTML
python run.py run --check-urls       # 联网校验每条链接可达性
python run.py run --source external_inbox   # 只跑指定源，调试用
python run.py explain --uid <uid>    # 打印某活动逐维度评分依据
python run.py history                # 推送历史 + 分数可比性自检
python run.py test-email             # 诊断 SMTP 连通性（仅在启用邮件时需要）
```

## 5. 已知边界

- **交通耗时目前是估算，不是真实行程。** 外部数据给出场馆经纬度后，
  `scout/geo.py` 算直线距离，再按悉尼市区经验系数（起步 12 分钟 + 每公里 3.2 分钟）
  折算门到门耗时。接 Transport for NSW 的 Trip Planner API 后应整个替换掉
  `geo.estimate_transit`。
- **讨论热度目前恒为 2 分**（1 家独立来源）。这是只接了一个源的必然结果，
  不是 bug。接入第二个带真实活动日期的源后，跨源去重会让它重新有效，
  届时应把 `rubric.yaml` 里的 `buzz` 权重从 0.04 调回去（并升 version）。
- **新鲜感通常拿不到**。公开活动数据很少标注「是否首届 / 首次来悉尼」，
  外部爬虫没给 `signals.first_time_in_city` 等字段时，这一维走保守默认分。
- 通用 RSS 采集器只能尽力解析日期与价格，解析不出的条目会在核验阶段被剔除（宁可漏推，不推错）。
