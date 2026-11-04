"""设计令牌与主题工具 —— GUI 主程序与弹窗通知共用的唯一视觉来源。

v1.4.2「胭脂桃夭」主题：中国古典传统色 + 低明度。

取意两重古典浪漫意象：「眉黛朱唇」—— 黛（画眉的青黑）与胭脂（点唇的
暗红）；「桃之夭夭，灼灼其华」—— 桃红（#f47983）是诗经以来最正统的
婚恋之色。全套色板从《中国传统色》体系取色后按暗色界面可读性微调
（原始参考值标注在行尾）：

    玄青/黛  底与卡片             胭脂  主色（原 #9d2933）
    桃红     高光/渐变亮端（原 #f47983）  缟素/宣纸  文字（缟 #f2ecde）
    竹青     正向（#789262）      赭    负向（#955539）
    琥珀     紧迫（#ca6924）

设计原则：浪漫放在意象与层次里，不放在饱和度里。桃花只做「亮声部」——
出现在渐变亮端、分数数字、小高光这些点睛处，绝不铺大面积底色：
「灼灼」是点亮，不是铺满；铺满就回到糖果色的媚俗了。

纯标准库。渐变用 Canvas 逐线画出来，不引任何图片资源。
"""
from __future__ import annotations

import sys

# ---------------------------------------------------------------- 配色
BG = "#22242e"          # 页面底（玄青压墨，玄青 #3d3b4f 的暗面）
CARD = "#2b2e3a"        # 卡片（黛）
CARD_H = "#323647"      # 卡片 hover
ACCENT = "#a93a4c"      # 主色（胭脂，#9d2933 微提明度保证暗底可读）
ACCENT_D = "#8f2f3d"    # 主色 hover（胭脂原色向深）
ACCENT_SOFT = "#3f2c36"  # 选中卡片底（胭脂桃花染黛）
PEACH = "#d98b95"       # 桃红亮面（桃红 #f47983 压饱和降明度）：小字高光专用
GRAD_1 = "#8f2f3d"      # 头部渐变 · 胭脂
GRAD_2 = "#cc7d8a"      # 头部渐变 · 桃花（#f47983 压暗，灼灼其华的那一端）
TEXT = "#ece6da"        # 文字（缟素/宣纸，缟 #f2ecde 微压）
MUTED = "#9b97a0"       # 次要文字（青灰）
FAINT = "#6b6774"       # 更弱（已结束、×权重）
BORDER = "#363a46"
SOFT = "#333744"        # 次级按钮底
SOFT_H = "#3e4352"      # 次级按钮 hover
HEADER_BTN = "#262231"  # 渐变头部上的深色按钮
HEADER_BTN_H = "#332d40"
GOOD = "#87a070"        # 正向（竹青 #789262 调亮度）
GOOD_BG = "#2b3329"
DANGER = "#ab6b56"      # 负向（赭 #955539 微提）
DANGER_BG = "#382d28"
AMBER = "#bd8244"       # 倒计时 · 紧迫（琥珀 #ca6924 压饱和）
LINK = "#8aa8c8"        # 链接（窃蓝系低饱和）
CHIP_BG = "#303441"
CHIP_FG = "#c3bec6"
TRACK = "#383c48"       # 条形图轨道

FONT = "Microsoft YaHei UI"


def band_color(total: float) -> str:
    """分数色带：≥80 竹青（难得的好活动）、达标线 68–79 胭脂、更低灰。"""
    if total >= 80:
        return GOOD
    if total >= 68:
        return PEACH        # 桃红：数字比按钮小，用亮声部才看得清
    return "#7d7987"


def _lerp_hex(c1: str, c2: str, t: float) -> str:
    a = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def draw_gradient(canvas, w: int, h: int, c1: str = GRAD_1, c2: str = GRAD_2,
                  tag: str = "grad") -> None:
    """在 Canvas 上画横向渐变（逐线）。重画前先 delete(tag)。"""
    canvas.delete(tag)
    if w <= 0:
        return
    step = 2                                    # 2px 一线，肉眼平滑且省对象
    for x in range(0, w + step, step):
        canvas.create_line(x, 0, x, h, fill=_lerp_hex(c1, c2, min(x / max(w, 1), 1.0)),
                           width=step + 1, tags=tag)
    canvas.tag_lower(tag)                       # 渐变永远垫底


def enable_dark_titlebar(window) -> None:
    """Windows 10/11：把系统标题栏调成暗色，否则白色标题栏配暗色界面像断头。
    其他平台 / 老系统静默跳过。须在窗口真正创建后调用。"""
    if sys.platform != "win32":
        return
    try:
        from ctypes import byref, c_int, sizeof, windll
        window.update_idletasks()
        hwnd = windll.user32.GetParent(window.winfo_id())
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE（旧版本号 19）
            if windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, byref(c_int(1)), sizeof(c_int)) == 0:
                break
    except Exception:                               # noqa: BLE001
        pass
