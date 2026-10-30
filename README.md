# 悉尼约会提案助理 (Sydney Date Scout)

每 3 天自动采集悉尼的活动信息，去重核验后按**固定绝对锚点**评分，达到推荐线的项目连同**可直接执行的约会方案**一起**弹窗提示**，随时可以打开桌面程序翻看当前推荐与历史记录。

核心约束：**分数跨批次可比**。同一个活动在任何日期、任何批次、无论同期竞品强弱，得分完全一致；达标的少就少推，一个都不达标就弹一个「本次无推荐」的说明，不降低标准凑数。

---

## v1.2.0（2026-10-30）：补上「像不像约会」这道闸门

v1.1.0 真实运行三个月（22 批次 201 条推送）后复盘，发现推荐被社区中心课程、
图书馆活动、儿童假期营、志愿者培训占据 —— 免费+动手+周末恰好是旧评分表的
满分模板，而七个维度没有一个在问「这像约会吗」。本版修复（回放旧数据验证：
201 条里 55% 会被新规则拦下）：

| 改动 | 位置 | 效果 |
|---|---|---|
| 约会适配闸门 | `verify.py` | 儿童/青少年/老年/志愿者/社区例行项目直接剔除（回放拦下 9%） |
| 停止折叠分类 | `cityofsydney.py` | children-and-family→`family`、community-and-causes→`community`，并硬排除 |
| 社区场地降权 ×0.8 | `rubric.yaml` v1.2.0 | 社区中心/图书馆场地不剔除但打八折（回放拦下 26%） |
| 系列级防重复 | `store.py` | 冷却与 `series_max_sends`（默认 3 次）按「标题+场地」算，周期课不再每场算新活动（回放拦下 20% 重复） |
| 品类多样性 | `pipeline.py` | 单批次每类别最多 `max_per_category`（默认 3）条 |
| 反馈闭环 | `gui.py` | 每条推荐可点「👍 感兴趣 / 👎 不再推这类」，👎 的系列永久不再推 |

新配置项（都有默认值，不改 config.yaml 也能跑）：`series_max_sends`、
`max_per_category`、`exclude_keywords`。数据库升级自动完成（新表 `feedback`、
`sent_history.series_key` 回填）。推送量会明显变少 —— 这是设计目标：
三条真值得去的，好过十条凑数的。

---

## 1. 架构

```
sources.yaml ──► 采集层 collectors/ ──► 去重 dedupe ──► 核验 verify ──► 评分 scoring ──► 阈值筛选
  (声明式源)      rss/ics/json/astro      标题+时间+场地      时间/地点/价格      rubric.yaml       固定阈值
                  fixtures(离线)          合并佐证来源        来源/有效性/预算    (逐条独立)        + 维度下限
                                                                                                      │
                        SQLite store ◄──────── 批次/历史/跨批次去重/运行间隔 ──────────────────────┤
                             │                                                                        ▼
                             ▼                                          弹窗 notifier ◄── 约会方案 plan
                     GUI 桌面程序 gui.py                                 邮件 mailer（可选，默认关闭）
                  （当前推荐 + 历史批次）
```

| 模块 | 职责 |
|---|---|
| `sources.yaml` | 声明式源清单。新增一个源＝加一段 YAML，不用改代码 |
| `scout/collectors/` | `rss` / `ics` / `json` / `astro` / `fixtures` / `cityofsydney` 六种采集器，注册表模式 |
| `scout/collectors/cityofsydney.py` | **当前唯一的真实数据源**。悉尼市政府官方活动库，见第 3 节 |
| `scout/dedupe.py` | 标题模糊匹配 × 时间接近 × 场地一致；合并后把各来源汇入 `evidence` |
| `scout/verify.py` | 时间/地点/价格/来源/有效性 + 预算、距离、类别、排除项过滤 |
| `scout/scoring.py` | 7 维度打分。**函数签名只接受单个 event + rubric**，结构上无法相对化 |
| `rubric.yaml` | 全部锚点、权重、阈值、降权系数，带 `version` |
| `scout/plan.py` | 生成时间线 / 预算拆解 / 备选方案 / 话题；可选 LLM 润色 |
| `scout/store.py` | SQLite：推荐批次、跨批次去重、推送历史、运行间隔守卫 |
| `scout/notifier.py` | 桌面弹窗（纯 tkinter，无额外依赖），可一键拉起 GUI |
| `gui.py` | 桌面程序：左边选批次，右边看完整方案 |
| `app.py` | 打包成 exe 时的统一入口（`--run` 跑采集，无参数开界面）|

---

## 2. 评分规则（rubric v1.1.0）

总分 = Σ(维度分 × 权重) × 10，范围 0–100。**推荐阈值 68 分，固定不变。**

| 维度 | 权重 | 定义 |
|---|---|---|
| 新鲜感 | 0.12 | 对悉尼观众的「第一次」程度 |
| 稀缺性 | 0.14 | 错过后短期内无法再参与的程度 |
| 讨论热度 | 0.04 | 近 30 天独立信息源报道数（绝对计数） |
| 约会氛围 | 0.26 | 5 项检查表各 2 分 |
| 互动性 | 0.18 | 两人产生共同经历的程度 |
| 性价比 | 0.14 | AUD / 人 / 小时 |
| 交通便利度 | 0.12 | 自 `home_suburb` 公交门到门耗时 + 换乘 |

### v1.1.0 为什么要重新配权重

v1.0.0 的权重是拿 `fixtures/sample_events.yaml` 里手写的样例校准的，那些样例带着
`media_mentions: 5`、`first_time_in_city: true` 这类真实世界拿不到的信号。
接上第一个真实源后实测 42 个活动，发现两个维度失去了区分度：

| 维度 | 原权重 | 实测 | 为什么 |
|---|---|---|---|
| 讨论热度 | 0.12 | 恒为 **2.0** | 定义是「独立信息源计数」。只接了一个源，任何活动都只能算 1 家，分数与活动好坏无关 |
| 新鲜感 | 0.18 | 最高 **4.0** | 没有任何官方活动数据会标注「本届首办」，公开数据最多推断出「年度重复举办」 |

合计 30% 的权重被数据可得性锁死在 2.9/10，最高分只有 60.8，68 分线永远不可能触发。
**这不是标准太严，是这两个维度在当前数据下测不出东西。**

所以把权重移到能被真实测量、且确实决定约会体验的维度上（约会氛围、互动性、稀缺性、
性价比、交通），而不是降低阈值 —— 68 分的含义仍然是「值得为它安排一次约会」。
讨论热度保留 0.04 而非归零：接入第二个真实源后，跨源去重会让它重新有效，届时应调回。

改动时数据库里没有任何历史推送记录，所以没有损失任何可比性。**之后再改就要付出这个代价。**

**锚点示例**（完整表见 `rubric.yaml`）

- 新鲜感：首次登陆悉尼=10｜首届=8｜新开业≤30 天=8｜年度活动有新内容=6｜年度沿用=4｜常设无更新=0
- 稀缺性：窗口≤1 天或仅剩 1 场=10｜≤3=8｜≤14 天=6｜≤45 天=4｜>120 天=0；余票紧张 +1
- 讨论热度：≥8 家独立媒体=10｜5–7=8｜3–4=6｜2=4｜1=2｜0=0
- 约会氛围：可交谈 / 有座位 / 出片 / 时段合适 / 可续摊，每项 +2
- 互动性：双人协作=10｜动手=8｜可走动试吃=6｜共同观看可交流=4｜需安静=2｜无=0
- 性价比：免费=10｜≤A$10/h=9｜≤20=7｜≤35=5｜≤60=3｜≤100=1｜>100=0
- 交通：≤15 分钟 0 换乘=10｜≤25/1 换=8｜≤40/1 换=6｜≤55/2 换=4｜>75 分钟=0；末端步行 >12 分钟 −1

**一票否决**：约会氛围 <5、交通 <4、性价比 <3，或信息完整度 <70% —— 无论总分多高都不推。

**降权**：命中单人向关键词（meetup / 讲座 / networking / 读书会…）的项目按固定系数 **0.85** 折算，不剔除。
**硬排除**：电竞、动漫（类别 + 关键词双重拦截）。
**硬剔除**：超预算、超距离、已售罄、已过期、无来源链接、价格未知。

### 可比性是怎么保证的

1. `score_event(event, rubric)` 的入参里没有活动列表、没有均值、没有排名 —— 相对化在代码结构上不可能发生。
2. 阈值、锚点、权重全部写死在 `rubric.yaml`，改动必须升级 `version`。
3. `run.py history` 会检查历史推送是否同一 rubric 版本，跨版本会明确告警「不可直接比较」。
4. `tests/test_scoring.py::test_score_is_independent_of_batch` 把同一活动分别放进「强批次」和「弱批次」，断言分数完全相等。

---

## 3. 快速开始

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

`--dry-run` 首次运行（内置 `fixtures/sample_events.yaml` + `astro` 源，共 14 条样例）会输出：

```json
{
  "collected": 14,
  "after_dedupe": 14,
  "verified_passed": 9,
  "excluded_breakdown": {"硬排除类别": 2, "超预算": 1, "超距离": 2},
  "recommended": 5,
  "top_score": 80.8,
  "below_threshold_scores": [67.6, 63.2, 58.0],
  "vetoed": ["年度品牌折扣清仓周：约会氛围 2.0 低于下限 5"],
  "output_file": "data/out/digest-2026...html"
}
```

注意 `below_threshold_scores` 里的 67.6：**差 0.4 分也不推**（阈值 68）。这是设计，不是 bug。
`python run.py explain --uid <events 表里的 uid>` 可以打印任意一条（包括未达标的）逐维度评分依据。

### 真实数据源的实际情况

`sources.yaml` 里原本那 17 个站点 URL 是**没核实过的模板**。2026-08-01 全部实测过一遍，
结论比预想的差很多：

| 结果 | 数量 | 说明 |
|---|---|---|
| 404 / 域名不通 | 15 | 包括 City of Sydney、歌剧院、NSW 美术馆、MCA、Westfield、Barangaroo、Scoopon、Groupon、SCG、Sydney FC 等。这些站**从来没提供过** RSS，URL 是照着常见路径猜的 |
| 活着但没用 | 2 | Time Out、Concrete Playground 的 feed 确实是 200 + 合法 RSS，但它们是**资讯流不是活动流** |

「活着但没用」这一条是最容易踩的坑：通用 `rss` 采集器把 `entry.published`
（文章发布时间）当成活动开始时间，于是每一条都在核验阶段被判「已过期」——
实测接进来 110 条，可用 0 条，只是白白拖慢每轮运行。所以这两个源**故意保持关闭**，
要用它们做「交叉验证 + 热度计数」，得先写一个能从正文里抽取活动日期的专用适配器。

还查了这些站有没有埋 JSON-LD `Event` 结构化数据（那是标准做法）：**一个都没有**。

**当前唯一可用的真实源是 City of Sydney「What's On」**（`scout/collectors/cityofsydney.py`），
也是唯一能同时给出真实开始时间、价格、场馆经纬度的公开来源。它是 Next.js 应用，
数据内嵌在页面的 `<script id="__NEXT_DATA__">` 里，和浏览器拿到的是同一份：

- 列表页 `/?time=this-week` 等一次给 300 条（robots.txt 明确 `Allow: /?time=*`）
- 详情页 `/events/<slug>`（在官方 sitemap 里公开列出）给出 `priceData` /
  `eventUpcomingTime` / `eventVenue`(lat,lng) / `eventDates` / `eventCategories`

流程是「列表页粗筛 → 只对进入推荐窗口的活动抓详情」，一轮约 170 个请求、3 分钟。

> **`list_urls` 别只填 this-week。** 列表按「最近一场」排序，前几十条会被当天正在
> 展出的常设展占满，真正适合约会的周末市集/工作坊排在后面。实测只抓 60 条详情时
> 0 条过线，覆盖完整推荐窗口后有 9 条过线 —— 看起来像「标准太严」，其实是取样太小。

```bash
python run.py check-sources            # 校验 sources.yaml 里每个 URL 是否可用
```

需要更精细字段的站点，照着 `cityofsydney.py` 写个专用适配器，用 `@collector("your_type")` 注册即可。
其余 16 个源保留在 `sources.yaml` 里但全部 `enabled: false`，每条都注明了实测结果。

合规提醒：只使用站点公开提供的 feed / API，遵守 robots.txt 与各站服务条款（默认每源 1 秒延时、带 User-Agent）。社交媒体请走官方 API，不要抓需要登录的页面。天文/自然类走 `astro` 本地表，每年更新一次即可。

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

## 4. 定时运行

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

## 5. 可配置项

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

## 6. 常用命令

```bash
python gui.py                        # 打开桌面程序（当前推荐 + 历史）
python run.py run --dry-run          # 跑一遍不弹窗不发信，只生成 HTML
python run.py run --check-urls       # 联网校验每条链接可达性
python run.py run --source timeout_sydney   # 只跑指定源，调试用
python run.py explain --uid <uid>    # 打印某活动逐维度评分依据
python run.py history                # 推送历史 + 分数可比性自检
python run.py test-email             # 诊断 SMTP 连通性（仅在启用邮件时需要）
```

## 7. 已知边界

- **交通耗时目前是估算，不是真实行程。** City of Sydney 给出场馆真实经纬度，
  采集器据此算直线距离，再按悉尼市区经验系数（起步 12 分钟 + 每公里 3.2 分钟）
  折算门到门耗时。接 Transport for NSW 的 Trip Planner API 后应整个替换掉
  `cityofsydney.py::_estimate_transit`（在 `verify` 前加一步 enrich 即可）。
- **讨论热度目前恒为 2 分**（1 家独立来源）。这是只接了一个源的必然结果，
  不是 bug。接入第二个带真实活动日期的源后，跨源去重会让它重新有效，
  届时应把 `rubric.yaml` 里的 `buzz` 权重从 0.04 调回去（并升 version）。
- **新鲜感只能推断到「年度重复举办」**。官方数据没有「是否首届」字段，
  采集器只用 CMS 记录的 `createdAt` 做单向推断：记录存在一年以上 = 年度活动。
  反过来「记录新建 = 首届」不成立（可能只是重新录入），所以新记录一律保持未知。
- 通用 RSS 采集器只能尽力解析日期与价格，解析不出的条目会在核验阶段被剔除（宁可漏推，不推错）。
