#!/usr/bin/env python3
"""每周时间使用报告 v2 —— 数据计算 + HTML 可视化报告。

架构:
    1  各板块总时长 / 占比 / 与上期对比
    2  每个色块(蓝/紫/灰/黄/绿/粉/红…)的详细分析
    3  本周总结（自动指标，供 Claude 补写自我观察）
    4  下周建议（自动诊断，供 Claude 补写行动项）

用法:
    python3 weekly_report.py                    # 上一自然周(周一~周日)
    python3 weekly_report.py --week this        # 本周至今
    python3 weekly_report.py --start 2026-11-14 --end 2026-11-21
    python3 weekly_report.py --start 2026-11-14 --days 7
    python3 weekly_report.py ... --no-open      # 不自动打开浏览器

输出:
    ~/.claude/skills/weekly-time-report/out/report-<start>.html
    stdout: Markdown 摘要（供 Claude 继续分析）
"""

import argparse
import datetime as dt
import html
import json
import math
import os
import subprocess
import sys
import webbrowser
from collections import defaultdict

# ---------------- 配置 ----------------
TZ = dt.timezone(dt.timedelta(hours=8))          # 本地时区
# 用多个别名匹配，避免用户重命名日历后指标静默归零
BLUE_CAL_HINTS = ("化愤怒为力量", "😡")
PURPLE_CAL_HINTS = ("知行合一",)
GRAY_CAL_HINT = "take care"
ENT_CAL_HINTS = ("无脑行为", "娱乐")
EXCLUDE_CAL_HINTS = ("节假日", "生日", "Siri", "gmail", "提醒事项")

ENT_REDLINE_PER_WEEK = 8.0
PURPLE_NOISE_KEYWORDS = ["驾考", "科目三", "路线图", "考试"]
WORK_HINTS = ["W1.", "W2", "课程", "代码", "简历", "面经", "RAG", "ERP", "算法"]
COURSE_KW = ["W1.", "W2", "课程", "代码", "Makefile", "权限控制", "读写工具"]
JOB_KW = ["简历", "面经", "算法", "刷题"]
LEARNING_KW = ["单词", "英语", "背", "anki", "复习", "学习", "阅读", "读书",
               "课程", "论文", "算法", "刷题", "练习", "journal", "review", "复盘"]
ENT_SUSPECT_KW = ["华为od", "gap 经历", "gap经历", "薪资", "面经", "简历",
                  "求职", "招聘", "创业经历", "经验贴", "岗位"]

BUCKETS = [("凌晨 00-06", 0, 6), ("上午 06-12", 6, 12),
           ("下午 12-18", 12, 18), ("晚上 18-24", 18, 24)]
WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")

# 配色：dataviz 参考调色板（已通过 CVD / 亮度 / 对比度校验）
SLOT = {
    "蓝色": "#2a78d6", "紫色": "#4a3aa7", "青色": "#1baf7a", "绿色": "#008300",
    "黄色": "#eda100", "橙色": "#eb6834", "红色": "#e34948", "粉色": "#e87ba4",
    "灰色": "#898781", "睡眠": "#7f7d76", "生活维持": "#b5b2a8", "其他": "#898781",
}
INK_PRIMARY, INK_SECONDARY, INK_MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, BASELINE, SURFACE = "#e1e0d9", "#c3c2b7", "#fcfcfb"
# --------------------------------------


# ============ 数据层 ============
def run_cli(*args):
    cmd = ["uvx", "mac-calendar-mcp", *args]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        sys.exit(f"mac-calendar-mcp 调用失败:\n{out.stderr[-2000:]}")
    txt = out.stdout.strip()
    return json.loads(txt) if txt else []


def calendar_colors():
    try:
        cals = run_cli("calendars")
    except SystemExit:
        return {}
    return {c["name"]: c.get("color") for c in cals if isinstance(c, dict)}


def parse(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TZ)


def load_period(start, end):
    lo = dt.datetime.combine(start, dt.time(), TZ)
    hi = dt.datetime.combine(end, dt.time(), TZ)
    ev = []
    for e in run_cli("events", start.isoformat(), end.isoformat(), "--limit", "6000"):
        if e.get("all_day"):
            continue
        s, t = max(parse(e["occurrence_start"]), lo), min(parse(e["occurrence_end"]), hi)
        if t > s:
            ev.append({**e, "s": s, "e": t, "h": (t - s).total_seconds() / 3600})
    return ev


CAL_COLOR_OVERRIDE = [
    ("化愤怒为力量", "蓝色"), ("#plan", "蓝色"), ("规划", "蓝色"), ("😡", "蓝色"),
    ("知行合一", "紫色"),
    ("take care", "灰色"),
    ("当下分子", "绿色"), ("锻炼", "绿色"),
    ("感恩日记", "粉色"), ("自我肯定", "粉色"),
    ("family", "黄色"), ("friends", "黄色"),
    ("无脑行为", "红色"), ("娱乐", "红色"),
]


def color_family(color_str, cal_name=""):
    """把日历映射到色系名称：先按已知日历名，再用 r,g,b 兜底。"""
    for kw, fam in CAL_COLOR_OVERRIDE:
        if kw in cal_name:
            return fam
    try:
        r, g, b = [float(x) for x in color_str.split(",")]
    except Exception:
        return "其他"
    mx, mn = max(r, g, b), min(r, g, b)
    if mx - mn < 0.10:
        return "灰色"
    if mx == r:
        h = 60 * ((g - b) / (mx - mn)) % 360
    elif mx == g:
        h = 60 * ((b - r) / (mx - mn) + 2)
    else:
        h = 60 * ((r - g) / (mx - mn) + 4)
    for name, hi in [("红色", 12), ("橙色", 45), ("黄色", 70), ("绿色", 165),
                     ("青色", 200), ("蓝色", 255), ("紫色", 300), ("粉色", 345)]:
        if h < hi:
            return name
    return "红色"


def is_purple_noise(t):
    if any(h in t for h in WORK_HINTS):
        return False
    return any(k in t for k in PURPLE_NOISE_KEYWORDS)


def content_bucket(title):
    if is_purple_noise(title):
        return "驾考"
    if any(k in title for k in COURSE_KW):
        return "课程/代码"
    if any(k in title for k in JOB_KW):
        return "简历/面试准备"
    return "其他"


def gray_bucket(title):
    t = title.lower()

    def has(*ks):
        return any(k in t for k in ks)

    if has("#sleep", "睡觉", "睡不醒", "接着睡"):
        return "睡眠"
    if has("驾校", "大兴总校", "科目三", "课时", "考前准备", "等驾驶"):
        return "驾考事务"
    if has("#meal", "吃饭", "做饭", "#break", "吃"):
        return "吃饭"
    if has("subway", "地铁", "坐车", "班车", "打车", "公交", "通勤"):
        return "交通"
    if has("复诊", "换药", "医院", "体检", "看牙", "手术", "牙周"):
        return "就医"
    if has("洗澡", "洗漱", "#care", "beauty", "指甲油", "身体乳"):
        return "洗漱护理"
    if has("洗碗", "laundry", "晾衣服", "买菜", "备菜", "拆快递", "泡脚",
           "洗衣服", "收拾", "整理衣柜"):
        return "家务"
    if has("等待", "#等", "等班车"):
        return "等待"
    if has("刷牙", "咬胶", "💩"):
        return "口腔/生理"
    if has("prepare", "track"):
        return "准备/记录"
    if has("休息", "发呆", "哭", "换衣服"):
        return "休息/情绪"
    return "其他"


def ent_type(title):
    t = title.lower()

    def has(*ks):
        return any(k in t for k in ks)

    if has("phone", "小红书", "刷", "博主", "微信", "公众号", "vlog"):
        return "刷手机/社交媒体"
    if has("movie", "《", "剧", "show", "综艺", "电影"):
        return "影视"
    if has("shopping", "买", "淘宝", "购物"):
        return "购物"
    if has("news", "新闻"):
        return "新闻"
    if has("podcast", "听"):
        return "播客/音频"
    if has("game", "游戏"):
        return "游戏"
    return "其他"


# 产出型学习检测（只作用于工作块：蓝 + 紫）
# 输出 = 标题里出现"可交付物"类动作；输入 = 纯消费动词。两者都命中时按输出算。
OUTPUT_KW = ["写", "实现", "搭", "跑通", "调试", "重构", "提交", "部署", "发布",
             "测试", "用例", "demo", "Demo", "产出", "交付", "练习", "刷题",
             "笔记", "文档", "讲解", "复盘", "修复", "改写", "迁移", "上线"]
INPUT_KW = ["看", "学", "听", "读", "刷", "浏览", "调研", "视频", "课程", "教程",
            "论文", "资料", "讲义", "讲座"]
ACCEPT_KW = ["通过", "跑通", "验收", "demo", "Demo", "测试", "用例", "演示",
             "讲解", "交付", "提交", "PR", "能用", "完成"]
# 光有数字不算验收标准（W2.4 只是课程编号），要配一个量词
MEASURE_KW = ["题", "问", "答", "个", "条", "页", "行", "次", "张", "篇", "项", "分钟", "小时"]


def is_output(title):
    return any(k in title for k in OUTPUT_KW)


def is_input(title):
    return (not is_output(title)) and any(k in title for k in INPUT_KW)


def has_acceptance(title):
    """产出块是否带可检验标准：验收词，或「数字 + 量词」的指标。"""
    if not is_output(title):
        return False
    if any(k in title for k in ACCEPT_KW):
        return True
    return any(c.isdigit() for c in title) and any(k in title for k in MEASURE_KW)


def is_work(e):
    name = e["calendar_name"]
    return any(k in name for k in BLUE_CAL_HINTS + PURPLE_CAL_HINTS)


def is_gray(e):
    return GRAY_CAL_HINT in e["calendar_name"]


def is_ent(e):
    return any(k in e["calendar_name"] for k in ENT_CAL_HINTS)


def excluded(ev, colors):
    out = []
    for e in ev:
        name = e["calendar_name"]
        if any(k in name for k in EXCLUDE_CAL_HINTS):
            continue
        out.append(e)
    return out


# ============ 指标层 ============
def union_hours(evs):
    iv = sorted((e["s"], e["e"]) for e in evs)
    total, cs, ce = 0.0, None, None
    for s, t in iv:
        if cs is None:
            cs, ce = s, t
        elif s <= ce:
            ce = max(ce, t)
        else:
            total += (ce - cs).total_seconds() / 3600
            cs, ce = s, t
    if cs:
        total += (ce - cs).total_seconds() / 3600
    return total


def fmt_h(x):
    return f"{x:.2f}"


def pct(x, d):
    return f"{x / d * 100:.1f}%" if d else "—"


def build_stats(ev, colors, week_h):
    cats = defaultdict(lambda: {"h": 0.0, "ev": [], "cals": defaultdict(float)})
    for e in ev:
        fam = color_family(colors.get(e["calendar_name"]), e["calendar_name"])
        cats[fam]["h"] += e["h"]
        cats[fam]["ev"].append(e)
        cats[fam]["cals"][e["calendar_name"]] += e["h"]
    for v in cats.values():
        v["ev"].sort(key=lambda x: x["s"])
    return cats


def sleep_main(ev):
    return [e for e in ev if is_gray(e) and e["h"] >= 3
            and any(k in e["title"].lower() for k in ("sleep", "睡觉"))]


def metrics(ev, week_h, days):
    m = {}
    work = [e for e in ev if is_work(e)]
    m["work"] = union_hours(work)
    m["blue"] = union_hours([e for e in ev if any(k in e["calendar_name"] for k in BLUE_CAL_HINTS)])
    m["purple"] = union_hours([e for e in ev if any(k in e["calendar_name"] for k in PURPLE_CAL_HINTS)])
    m["ent"] = union_hours([e for e in ev if is_ent(e)])
    m["gray"] = union_hours([e for e in ev if is_gray(e)])

    sleeps = [e for e in ev if is_gray(e) and any(k in e["title"].lower() for k in ("sleep", "睡觉"))]
    main = [e for e in sleeps if e["h"] >= 3]
    m["sleep"] = union_hours(sleeps)
    m["awake"] = week_h - m["sleep"]

    # 碎片化
    durs = sorted(e["h"] for e in ev if not is_gray(e))
    if durs:
        m["n_sessions"] = len(durs)
        m["frag_ratio"] = sum(1 for h in durs if h <= 0.5) / len(durs)
        m["median_session"] = durs[len(durs) // 2]
        m["frag_hours"] = sum(h for h in durs if h <= 0.5)
        m["longest_session"] = durs[-1]

    # 上下文切换：相邻事件的顶级类别变化
    seq = [e for e in ev if not is_gray(e)]
    seq.sort(key=lambda x: x["s"])
    switches = 0
    for a, b in zip(seq, seq[1:]):
        ca = "工作" if is_work(a) else ("娱乐" if is_ent(a) else a["calendar_name"].split("|")[0][:6])
        cb = "工作" if is_work(b) else ("娱乐" if is_ent(b) else b["calendar_name"].split("|")[0][:6])
        if ca != cb:
            switches += 1
    m["switches"] = switches
    m["switches_per_day"] = switches / days if days else 0

    # 工作块的相邻性与休息
    wsort = sorted(work, key=lambda x: x["s"])
    pairs = list(zip(wsort, wsort[1:]))
    same, gaps = 0, []
    for a, b in pairs:
        if content_bucket(a["title"]) == content_bucket(b["title"]) and a["s"].date() == b["s"].date():
            same += 1
        gaps.append((b["s"] - a["e"]).total_seconds() / 60)
    m["work_pairs"] = len(pairs)
    m["same_bucket_ratio"] = same / len(pairs) if pairs else 0
    m["no_break_pairs"] = sum(1 for g in gaps if 0 <= g < 5)
    m["break_pairs"] = sum(1 for g in gaps if g >= 5)

    # 输入 / 输出（只算工作块）
    out_ev = [e for e in work if is_output(e["title"])]
    in_ev = [e for e in work if is_input(e["title"])]
    m["out_ev"] = sorted(out_ev, key=lambda x: -x["h"])
    m["out_h"] = union_hours(out_ev)
    m["in_h"] = union_hours(in_ev)
    m["out_n"] = len(out_ev)
    m["accept_n"] = sum(1 for e in out_ev if has_acceptance(e["title"]))
    denom = m["out_h"] + m["in_h"]
    m["out_share"] = m["out_h"] / denom if denom else 0.0
    m["cls_h"] = denom

    # 最长连续在忙（非生活维持事件；间隔 < 5 分钟视为连续）
    busy = [e for e in ev if not is_gray(e)]
    busy.sort(key=lambda x: x["s"])
    longest, cur_s, cur_e = 0.0, None, None
    for e in busy:
        if cur_e is None:
            cur_s, cur_e = e["s"], e["e"]
        elif (e["s"] - cur_e).total_seconds() <= 300:
            cur_e = max(cur_e, e["e"])
        else:
            longest = max(longest, (cur_e - cur_s).total_seconds() / 3600)
            cur_s, cur_e = e["s"], e["e"]
    if cur_e:
        longest = max(longest, (cur_e - cur_s).total_seconds() / 3600)
    m["longest_busy"] = longest

    # 睡眠规律性
    if main:
        bed = [((e["s"].hour * 60 + e["s"].minute) - 1080) % 1440 for e in main]
        wake = [e["e"].hour * 60 + e["e"].minute for e in main]
        m["sleep_n"] = len(main)
        m["sleep_avg"] = sum(e["h"] for e in main) / len(main)
        m["bed_mean"] = (sum(bed) / len(bed) + 1080) % 1440
        m["bed_std"] = (sum((b - sum(bed) / len(bed)) ** 2 for b in bed) / len(bed)) ** 0.5
        m["wake_mean"] = sum(wake) / len(wake)
        m["wake_std"] = (sum((w - sum(wake) / len(wake)) ** 2 for w in wake) / len(wake)) ** 0.5
        m["naps"] = sum(e["h"] for e in sleeps if e["h"] < 3)
    return m


def auto_insights(m, cats, week_h, days):
    out = []
    if m.get("bed_std", 99) >= 60:
        out.append(("serious", f"入睡时间波动 ±{m['bed_std']:.0f} 分钟，睡眠不规律",
                    "固定起床时间比固定入睡时间更容易做到，先锚定起床。"))
    if m.get("sleep_avg", 0) and m["sleep_avg"] < 6.5:
        out.append(("serious", f"主睡眠平均仅 {m['sleep_avg']:.1f}h",
                    "睡眠是精力管理的第一层，优先补足。"))
    if m.get("frag_ratio", 0) >= 0.4:
        out.append(("warning", f"{m['frag_ratio'] * 100:.0f}% 的事件 ≤30 分钟，中位段长 {m['median_session'] * 60:.0f} 分钟",
                    "频繁切换带来上下文损耗，建议合并同类任务、设 45–90 分钟专注块。"))
    if m.get("switches_per_day", 0) >= 8:
        out.append(("warning", f"日均上下文切换 {m['switches_per_day']:.0f} 次",
                    "每次切换都有重启成本，按主题批量处理。"))
    if m.get("work_pairs", 0) >= 3 and m["same_bucket_ratio"] < 0.4:
        out.append(("warning", f"相邻工作块只有 {m['same_bucket_ratio'] * 100:.0f}% 是同类任务",
                    "同类任务没有挨着安排，批量处理可以减少切换。"))
    if m.get("no_break_pairs", 0) >= 2:
        out.append(("warning", f"{m['no_break_pairs']} 次工作块之间没有 ≥5 分钟休息",
                    "高强度连续切换会抬高认知负荷，块间插入 5–10 分钟恢复。"))
    if m.get("longest_busy", 0) >= 4:
        out.append(("warning", f"最长连续在忙 {m['longest_busy']:.1f}h",
                    "超过 90–120 分钟的连续输出效率递减，加入主动休息。"))
    if m.get("in_h", 0) + m.get("out_h", 0) >= 2 and m.get("out_share", 0) < 0.33:
        out.append(("warning",
                    f"输入 {m['in_h']:.1f}h : 输出 {m['out_h']:.1f}h，输出只占 {m['out_share'] * 100:.0f}%",
                    "按 2:1 配比，每 3h 学习至少留 1h 做能演示的东西（demo / 测试 / 一页笔记）。"))
    if m.get("out_n") and m.get("accept_n", 0) == 0:
        out.append(("warning", f"{m['out_n']} 个产出块没有一条带验收标准",
                    "标题改成「[产出] 动作+物件+验收」，例如「RAG demo：5 问 5 答带出处」。"))
    if m.get("ent", 0) > ENT_REDLINE_PER_WEEK * days / 7:
        out.append(("warning", f"娱乐 {m['ent']:.1f}h 超过红线",
                    "设硬上限，把观看类娱乐绑定在完成任务之后。"))
    late_ent = sum(e["h"] for e in cats.get("红色", {}).get("ev", []) if e["s"].hour < 3)
    if m.get("ent") and late_ent / m["ent"] >= 0.15:
        out.append(("warning", f"{late_ent / m['ent'] * 100:.0f}% 的娱乐在凌晨 0–3 点",
                    "睡前刷手机挤占睡眠，23:30 后手机不进卧室。"))
    if not out:
        out.append(("good", "各项指标都在健康区间", "保持当前节奏即可。"))
    return out


# ============ 渲染层 ============
def esc(s):
    return html.escape(str(s))


def lum(hex_color):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def label_color(fill):
    return "#ffffff" if lum(fill) < 0.55 else INK_PRIMARY


def stacked_100(segments, width=880, height=54):
    total = sum(v for _, v, _ in segments) or 1
    x, parts = 0.0, []
    gap = 2
    for i, (name, v, color) in enumerate(segments):
        w = width * v / total
        draw_w = max(w - (gap if i else 0), 0.5)
        x0 = x + (gap if i else 0)
        parts.append(f'<rect x="{x0:.1f}" y="0" width="{draw_w:.1f}" height="{height}" '
                     f'fill="{color}" rx="3"><title>{esc(name)} {v:.2f}h（{pct(v, total)}）</title></rect>')
        if w / width >= 0.055:
            parts.append(f'<text x="{x0 + draw_w / 2:.1f}" y="{height / 2 + 4}" text-anchor="middle" '
                         f'font-size="12" fill="{label_color(color)}" '
                         f'style="pointer-events:none">{esc(name)} {pct(v, total)}</text>')
        x += w
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
            f'aria-label="时间占比">{ "".join(parts)}</svg>')


def grouped_bars(rows, width=880):
    """rows: [(label, cur, prev, color)] → 横向双条。"""
    if not rows:
        return ""
    bar_h, pad, left = 12, 18, 190
    row_h = bar_h * 2 + 10 + pad
    height = row_h * len(rows) + 24
    vmax = max(max(c, p) for _, c, p, _ in rows) or 1
    plot_w = width - left - 80
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="本期与上期对比">']
    for i, (name, cur, prev, color) in enumerate(rows):
        y = 12 + i * row_h
        out.append(f'<text x="{left - 12}" y="{y + bar_h + 4}" text-anchor="end" font-size="12.5" '
                   f'fill="{INK_SECONDARY}">{esc(name)}</text>')
        for j, (val, op, tag) in enumerate([(prev, 0.32, "上期"), (cur, 1, "本期")]):
            w = plot_w * val / vmax
            yy = y + j * (bar_h + 4)
            out.append(f'<rect x="{left}" y="{yy}" width="{max(w, 1):.1f}" height="{bar_h}" '
                       f'rx="3" fill="{color}" fill-opacity="{op}">'
                       f'<title>{esc(name)} {tag} {val:.2f}h</title></rect>')
            out.append(f'<text x="{left + w + 6:.1f}" y="{yy + bar_h - 3}" font-size="11" '
                       f'fill="{INK_MUTED}">{val:.2f}h</text>')
    out.append(f'<line x1="{left}" y1="4" x2="{left}" y2="{height - 4}" stroke="{BASELINE}" stroke-width="1"/>')
    out.append("</svg>")
    return "".join(out)


def h_bars(items, color, width=880):
    """单系列横向条形。items: [(label, value)]"""
    if not items:
        return ""
    bar_h, pad, left = 14, 12, 150
    row_h = bar_h + pad
    height = row_h * len(items) + 8
    vmax = max(v for _, v in items) or 1
    plot_w = width - left - 70
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="明细条形">']
    for i, (name, val) in enumerate(items):
        y = 4 + i * row_h
        w = plot_w * val / vmax
        out.append(f'<text x="{left - 10}" y="{y + bar_h - 3}" text-anchor="end" font-size="12" '
                   f'fill="{INK_SECONDARY}">{esc(name)}</text>')
        out.append(f'<rect x="{left}" y="{y}" width="{max(w, 1):.1f}" height="{bar_h}" rx="3" '
                   f'fill="{color}"><title>{esc(name)} {val:.2f}h</title></rect>')
        out.append(f'<text x="{left + w + 6:.1f}" y="{y + bar_h - 3}" font-size="11" '
                   f'fill="{INK_MUTED}">{val:.2f}h</text>')
    out.append(f'<line x1="{left}" y1="2" x2="{left}" y2="{height - 2}" stroke="{BASELINE}" stroke-width="1"/>')
    out.append("</svg>")
    return "".join(out)


def daily_columns(evs, days_start, days, color, width=880, height=110):
    per = defaultdict(float)
    for e in evs:
        per[e["s"].date()] += e["h"]
    vals = [per.get(days_start + dt.timedelta(days=i), 0) for i in range(days)]
    vmax = max(vals) or 1
    n = max(days, 1)
    bw = min(36, (width - 60) / n * 0.55)
    step = (width - 60) / n
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="每日分布">']
    out.append(f'<line x1="30" y1="{height - 20}" x2="{width - 10}" y2="{height - 20}" '
               f'stroke="{BASELINE}" stroke-width="1"/>')
    for i, v in enumerate(vals):
        x = 30 + i * step + (step - bw) / 2
        bh = (height - 32) * v / vmax
        out.append(f'<rect x="{x:.1f}" y="{height - 20 - bh:.1f}" width="{bw:.1f}" height="{max(bh, 1):.1f}" '
                   f'rx="3" fill="{color}"><title>{(days_start + dt.timedelta(days=i)):%m-%d} {v:.2f}h</title></rect>')
        if v > 0:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{height - 20 - bh - 4:.1f}" text-anchor="middle" '
                       f'font-size="10" fill="{INK_MUTED}">{v:.1f}</text>')
        out.append(f'<text x="{x + bw / 2:.1f}" y="{height - 6}" text-anchor="middle" font-size="10" '
                   f'fill="{INK_MUTED}">{(days_start + dt.timedelta(days=i)):%m-%d}</text>')
    out.append("</svg>")
    return "".join(out)


def table(headers, rows, align=None):
    align = align or ["left"] + ["right"] * (len(headers) - 1)
    th = "".join(f'<th style="text-align:{a}">{esc(h)}</th>' for h, a in zip(headers, align))
    body = "".join("<tr>" + "".join(
        f'<td style="text-align:{a}">{c}</td>' for c, a in zip(r, align)) + "</tr>" for r in rows)
    return f'<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'


def legend(entries):
    return '<div class="legend">' + "".join(
        f'<span class="lg"><i style="background:{c}"></i>{esc(n)}</span>' for n, c in entries) + "</div>"


CSS = """
:root{
  --surface:#fcfcfb; --page:#f9f9f7; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --base:#c3c2b7; --border:rgba(11,11,11,.10);
  --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
}
@media (prefers-color-scheme:dark){
  :root{
    --surface:#1a1a19; --page:#0d0d0d; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --base:#383835; --border:rgba(255,255,255,.10);
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
  font:14px/1.65 system-ui,-apple-system,"Segoe UI","PingFang SC",sans-serif;}
main{max-width:960px;margin:0 auto;padding:36px 24px 80px}
h1{font-size:26px;margin:0 0 4px;font-weight:650}
h2{font-size:19px;margin:44px 0 14px;font-weight:650;padding-bottom:8px;border-bottom:1px solid var(--border)}
h3{font-size:15.5px;margin:30px 0 10px;font-weight:650}
h4{font-size:13.5px;margin:18px 0 8px;font-weight:600;color:var(--ink2)}
.sub{color:var(--ink2);margin:0 0 20px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px 22px;margin:14px 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 16px}
.kpi .k{color:var(--muted);font-size:12px}
.kpi .v{font-size:24px;font-weight:650;margin-top:2px}
.kpi .d{font-size:12px;margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0}
th,td{padding:7px 8px;border-bottom:1px solid var(--grid)}
th{color:var(--muted);font-weight:600;font-size:12px}
td{font-variant-numeric:tabular-nums}
.legend{display:flex;flex-wrap:wrap;gap:14px;margin:10px 0 4px;font-size:12px;color:var(--ink2)}
.lg{display:inline-flex;align-items:center;gap:6px}
.lg i{width:12px;height:12px;border-radius:3px;display:inline-block}
.up{color:var(--critical)} .down{color:var(--good)}
.badge{display:inline-flex;align-items:center;gap:6px;font-size:12px;padding:2px 10px;border-radius:999px;border:1px solid var(--border)}
.badge.good{color:var(--good)} .badge.warning{color:#a06a00} .badge.serious{color:#b4522f}
ul{margin:8px 0;padding-left:20px} li{margin:5px 0}
.muted{color:var(--muted);font-size:12.5px}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:6px;background:var(--grid);color:var(--ink2)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:720px){.grid2{grid-template-columns:1fr}}
"""


# ============ 主流程 ============
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", choices=["last", "this"])
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--days", type=int)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()

    today = dt.datetime.now(TZ).date()
    if a.start:
        start = dt.date.fromisoformat(a.start)
        end = dt.date.fromisoformat(a.end) if a.end else start + dt.timedelta(days=a.days or 7)
    else:
        monday = today - dt.timedelta(days=today.weekday())
        if a.week == "this":
            start, end = monday, today + dt.timedelta(days=1)
        else:
            start, end = monday - dt.timedelta(days=7), monday
    if end <= start:
        sys.exit("结束日期必须晚于开始日期")

    days = (end - start).days
    week_h = days * 24
    colors = calendar_colors()
    ev = excluded(load_period(start, end), colors)
    if not ev:
        sys.exit("该时间段没有日历记录")

    prev_s, prev_e = start - dt.timedelta(days=days), start
    try:
        prev_ev = excluded(load_period(prev_s, prev_e), colors)
    except SystemExit:
        prev_ev = []

    cats = build_stats(ev, colors, week_h)
    prev_cats = build_stats(prev_ev, colors, week_h) if prev_ev else {}
    m = metrics(ev, week_h, days)
    pm = metrics(prev_ev, week_h, days) if prev_ev else {}

    # 顶级类别排序（睡眠单列，不参与"生活维持"）
    cat_rows = []
    for fam, v in cats.items():
        if fam == "灰色":
            continue
        cat_rows.append((fam, v["h"], v["ev"]))
    gray = cats.get("灰色")
    if gray:
        sleep_h = sum(e["h"] for e in gray["ev"] if "睡眠" == gray_bucket(e["title"]))
        nonsleep = gray["h"] - sleep_h
        cat_rows.append(("睡眠", sleep_h, [e for e in gray["ev"] if gray_bucket(e["title"]) == "睡眠"]))
        cat_rows.append(("生活维持", nonsleep, [e for e in gray["ev"] if gray_bucket(e["title"]) != "睡眠"]))
    cat_rows.sort(key=lambda x: -x[1])
    shown = cat_rows[:8]
    other_h = sum(h for _, h, _ in cat_rows[8:])
    if other_h > 0:
        shown.append(("其他", other_h, []))

    # ---- HTML 构造 ----
    H = []
    add = H.append
    add('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">')
    add('<meta name="viewport" content="width=device-width,initial-scale=1">')
    add(f'<title>时间周报 {start} ~ {end - dt.timedelta(days=1)}</title>')
    add(f"<style>{CSS}</style></head><body><main>")
    add(f'<h1>时间周报 <span class="muted">{start:%Y-%m-%d}（{WEEKDAY_NAMES[start.weekday()]}）'
        f' ~ {end - dt.timedelta(days=1):%Y-%m-%d}</span></h1>')
    add(f'<p class="sub">记录覆盖率 {pct(sum(v["h"] for v in cats.values()), week_h)} ｜ '
        f'事件 {len(ev)} 条 ｜ 清醒 {m["awake"]:.1f}h</p>')

    # 1 KPI + 占比 + 对比
    add("<h2>一、时间去向</h2>")
    work_share = m["work"] / week_h * 100 if week_h else 0
    add('<div class="kpis">')
    for label, val, sub in [
        ("工作（蓝+紫）", f'{fmt_h(m["work"])}h', f'{work_share:.1f}% · 上期 {fmt_h(pm.get("work", 0))}h'),
        ("睡眠", f'{fmt_h(m["sleep"])}h', f'{pct(m["sleep"], week_h)} · 主睡眠均 {m.get("sleep_avg", 0):.1f}h'),
        ("生活维持", f'{fmt_h(m["gray"] - m["sleep"])}h', f'{pct(m["gray"] - m["sleep"], week_h)}'),
        ("娱乐", f'{fmt_h(m["ent"])}h', f'{pct(m["ent"], week_h)} · 红线 {ENT_REDLINE_PER_WEEK * days / 7:.1f}h'),
    ]:
        add(f'<div class="kpi"><div class="k">{esc(label)}</div><div class="v">{esc(val)}</div>'
            f'<div class="d muted">{esc(sub)}</div></div>')
    add("</div>")

    add('<div class="card"><h3 style="margin-top:0">本周时间占比</h3>')
    segs = [(name, h, SLOT.get(name, SLOT["其他"])) for name, h, _ in shown]
    add(stacked_100(segs))
    add(legend([(n, SLOT.get(n, SLOT["其他"])) for n, _, _ in shown]))
    add('<p class="muted">按色系归并（蓝=维护+规划，紫=成长，灰=生活维持，黄=家人朋友，绿=锻炼，粉=自省，红=娱乐）。</p></div>')

    add('<div class="card"><h3 style="margin-top:0">与上期对比</h3>')
    add(legend([("本期（彩色）", "#2a78d6"), ("上期（浅色）", "#a9c6ec")]))
    rows = []
    for name, h, _ in shown:
        if name in ("生活维持",):
            ph = sum(e["h"] for e in prev_cats.get("灰色", {}).get("ev", [])
                     if gray_bucket(e["title"]) != "睡眠") if prev_cats else 0
        elif name == "睡眠":
            ph = sum(e["h"] for e in prev_cats.get("灰色", {}).get("ev", [])
                     if gray_bucket(e["title"]) == "睡眠") if prev_cats else 0
        elif name == "其他":
            ph = 0
        else:
            ph = prev_cats.get(name, {}).get("h", 0)
        rows.append((name, h, ph, SLOT.get(name, SLOT["其他"])))
    add(grouped_bars(rows))
    # 表视图
    trows = []
    for name, h, ph, _ in rows:
        d = h - ph
        cls = "up" if d > 0 else "down"
        trows.append([esc(name), f"{h:.2f}h", pct(h, week_h), f"{ph:.2f}h",
                      f'<span class="{cls}">{"+" if d >= 0 else ""}{d:.2f}h</span>'])
    add(table(["板块", "本期", "占本周", "上期", "变化"], trows))
    add("</div>")

    # 2 各色块详细（固定顺序：蓝 → 紫 → 灰 → 黄 → 绿 → 粉 → 红）
    add("<h2>二、各色块详细分析</h2>")

    def entry_of(name):
        return next((x for x in shown if x[0] == name), None)

    def ph_of(name):
        if name == "睡眠":
            return sum(e["h"] for e in (prev_cats.get("灰色", {}).get("ev", []) if prev_cats else [])
                       if gray_bucket(e["title"]) == "睡眠")
        if name == "生活维持":
            return sum(e["h"] for e in (prev_cats.get("灰色", {}).get("ev", []) if prev_cats else [])
                       if gray_bucket(e["title"]) != "睡眠")
        if name == "灰色":
            return ph_of("睡眠") + ph_of("生活维持")
        return prev_cats.get(name, {}).get("h", 0)

    gray_all = cats.get("灰色", {}).get("ev", [])
    sections = []
    for n in ["蓝色", "紫色"]:
        if entry_of(n):
            sections.append((n, entry_of(n)[1], entry_of(n)[2]))
    if gray_all:
        sections.append(("灰色", sum(e["h"] for e in gray_all), gray_all))
    for n in ["黄色", "绿色", "粉色", "红色"]:
        if entry_of(n):
            sections.append((n, entry_of(n)[1], entry_of(n)[2]))
    for n, h, evs in shown:
        if n not in ("其他", "睡眠", "生活维持") and n not in [s[0] for s in sections]:
            sections.append((n, h, evs))

    sec = 0
    for name, h, evs in sections:
        sec += 1
        add(f"<h3>2.{sec} {esc(name)}类目</h3>")
        delta = h - ph_of(name)
        show_h = (h - m["sleep"]) if name == "灰色" else h
        note = "（含睡眠；非睡眠部分见下方）" if name == "灰色" else ""
        add(f'<p>合计 <b>{fmt_h(h)}h</b>{note}（占本周 {pct(h, week_h)}，占清醒 {pct(show_h, m["awake"])}），'
            f'较上期 <span class="{"up" if delta > 0 else "down"}">{"+" if delta >= 0 else ""}{delta:.2f}h</span>。</p>')
        # 构成
        comp = defaultdict(float)
        for e in evs:
            if name in ("蓝色", "紫色"):
                comp[content_bucket(e["title"])] += e["h"]
            elif name == "灰色":
                comp[gray_bucket(e["title"])] += e["h"]
            elif name == "红色":
                comp[ent_type(e["title"])] += e["h"]
            else:
                comp[e["title"].split()[0][:14] if e["title"] else "未命名"] += e["h"]
        comp_items = sorted(comp.items(), key=lambda x: -x[1])
        add('<div class="card"><h4>内部构成</h4>')
        add(h_bars(comp_items[:8], SLOT.get(name, SLOT["其他"])))
        if name == "灰色":
            nonsleep = [e for e in evs if gray_bucket(e["title"]) != "睡眠"]
            add('<h4>每日非睡眠灰块</h4>')
            add(daily_columns(nonsleep, start, days, SLOT["生活维持"]))
            main = sorted([e for e in evs if gray_bucket(e["title"]) == "睡眠" and e["h"] >= 3],
                          key=lambda x: x["s"])
            if main:
                add('<h4>每晚主睡眠</h4>')
                add(table(["入睡日", "入睡", "起床", "时长"],
                          [[f"{e['s']:%m-%d %a}", f"{e['s']:%H:%M}", f"{e['e']:%H:%M}", f"{e['h']:.2f}h"]
                           for e in main]))
            add("</div>")
        else:
            add(daily_columns(evs, start, days, SLOT.get(name, SLOT["其他"])))
            add("</div>")
        # 洞察
        tips = []
        if evs and name != "灰色":
            buckets = defaultdict(float)
            for lab, x, y in BUCKETS:
                buckets[lab] = sum(e["h"] for e in evs if x <= e["s"].hour < y)
            top_b = max(buckets.items(), key=lambda x: x[1])
            if top_b[1] > 0:
                tips.append(f"主要发生在「{top_b[0]}」，占该类 {pct(top_b[1], h)}。")
            short = sum(1 for e in evs if e["h"] <= 0.5)
            tips.append(f"{short}/{len(evs)} 段 ≤30 分钟，中位段长 {sorted(e['h'] for e in evs)[len(evs) // 2] * 60:.0f} 分钟。")
            wd = defaultdict(float)
            for e in evs:
                wd[e["s"].weekday()] += e["h"]
            top_d = max(wd.items(), key=lambda x: x[1])[0]
            tips.append(f"最集中的是{WEEKDAY_NAMES[top_d]}。")
        if name == "蓝色" and h:
            course = comp.get("课程/代码", 0)
            if course / h >= 0.5:
                tips.append(f"其中 {pct(course, h)} 是课程/写代码——如果蓝色日历的意图是「维护类」，"
                            "建议把课程单独归类，否则“维护类是否变少”无法度量；"
                            "若日历已改名为学习类（如现在的 😡 日历），这条可以忽略。")
        if name == "紫色" and h:
            noise = comp.get("驾考", 0)
            if noise:
                tips.append(f"其中 {noise:.2f}h 是驾考类非工作内容，剔除后成长类只剩 {h - noise:.2f}h"
                            f"（占清醒 {pct(h - noise, m['awake'])}）。")
        if name == "灰色":
            if m.get("sleep_n"):
                bm, wm = int(m["bed_mean"]), int(m["wake_mean"])
                tips.append(f"主睡眠 {m['sleep_n']} 晚，均值 {m['sleep_avg']:.1f}h；"
                            f"入睡 {bm // 60:02d}:{bm % 60:02d}±{m['bed_std']:.0f} 分钟，"
                            f"起床 {wm // 60:02d}:{wm % 60:02d}±{m['wake_std']:.0f} 分钟。")
                if m.get("naps"):
                    tips.append(f"午睡/补觉合计 {m['naps']:.2f}h。")
            tips.append(f"非睡眠生活维持 {fmt_h(h - m['sleep'])}h = 清醒时间的 "
                        f"{pct(h - m['sleep'], m['awake'])}。")
            transport = comp.get("交通", 0)
            if week_h and transport / week_h >= 0.07:
                tips.append(f"交通 {transport:.2f}h（{transport / days:.1f}h/天），远端固定地点要算总成本。")
            if days and comp.get("吃饭", 0) / days >= 1.8:
                tips.append(f"吃饭 {comp['吃饭'] / days:.1f}h/天，偏长；可尝试专注吃饭、不看视频。")
        if name == "红色":
            sus = [e for e in evs if any(k in e["title"].lower() for k in ENT_SUSPECT_KW)]
            if sus:
                tips.append(f"⚠️ {sum(e['h'] for e in sus):.2f}h 疑似求职/学习内容被误记，建议复核："
                            + "；".join(e["title"][:24] for e in sus[:3]))
            tips.append(f"红线 {ENT_REDLINE_PER_WEEK * days / 7:.1f}h，" +
                        ("未超限 ✅" if h <= ENT_REDLINE_PER_WEEK * days / 7 else "已超限 ⚠️"))
        if tips:
            add('<div class="card"><h4>观察</h4><ul>' +
                "".join(f"<li>{esc(t)}</li>" for t in tips) + "</ul></div>")

    # 3 本周总结
    add("<h2>三、本周总结</h2>")
    ins = auto_insights(m, cats, week_h, days)
    add('<div class="card"><h4>自动指标</h4><ul>')
    for sev, title, hint in ins:
        add(f'<li><span class="badge {sev}">{ {"good": "✓", "warning": "!", "serious": "!!"}[sev] }</span> '
            f'{esc(title)}<span class="muted"> — {esc(hint)}</span></li>')
    add("</ul>")
    add('<h4>关键数字</h4>')
    add(table(["指标", "数值"], [
        ["工作总时长", f'{fmt_h(m["work"])}h（{pct(m["work"], m["awake"])} 清醒）'],
        ["蓝 : 紫（板块合计）", f'{fmt_h(next((x[1] for x in shown if x[0] == "蓝色"), 0))}h : '
                              f'{fmt_h(next((x[1] for x in shown if x[0] == "紫色"), 0))}h'],
        ["输入 : 输出（工作块）", f'{fmt_h(m.get("in_h", 0))}h : {fmt_h(m.get("out_h", 0))}h'
                                f'（输出占 {m.get("out_share", 0) * 100:.0f}%）'],
        ["带验收标准的产出块", f'{m.get("accept_n", 0)} / {m.get("out_n", 0)}'],
        ["睡眠", f'{fmt_h(m["sleep"])}h（{pct(m["sleep"], week_h)}）'],
        ["生活维持（非睡眠）", f'{fmt_h(m["gray"] - m["sleep"])}h（{pct(m["gray"] - m["sleep"], m["awake"])} 清醒）'],
        ["娱乐", f'{fmt_h(m["ent"])}h'],
        ["碎片化", f'{m.get("frag_ratio", 0) * 100:.0f}% 段 ≤30min'],
        ["上下文切换", f'{m.get("switches", 0)} 次（{m.get("switches_per_day", 0):.1f}/天）'],
        ["最长连续在忙", f'{m.get("longest_busy", 0):.1f}h'],
    ]))
    add("</div>")

    # 产出台账：有哪些"能演示的东西"，哪些只是有动作没标准
    add('<div class="card"><h4>本周产出清单</h4>')
    if m.get("out_ev"):
        add('<ul class="prod">')
        for e in m["out_ev"][:10]:
            ok = has_acceptance(e["title"])
            add(f'<li><span class="badge {"good" if ok else "warning"}">{"✓" if ok else "○"}</span> '
                f'<span class="muted">{e["s"]:%m-%d}</span> {esc(e["title"])}'
                f'<span class="muted"> · {e["h"]:.2f}h</span></li>')
        if len(m["out_ev"]) > 10:
            add(f'<li class="muted">…另有 {len(m["out_ev"]) - 10} 个产出块</li>')
        add(f'</ul><p class="muted">✓ = 标题带验收标准；○ = 有产出动作，但没有可检验的标准。'
            f'按 2:1 配比，输入:输出 ≈ 2:1；每周至少要有 3 个 ✓。'
            f'本周已识别 {m.get("cls_h", 0):.2f}h / 工作 {m["work"]:.2f}h，'
            f'其余标题没有出现动作词，未计入。</p>')
    else:
        add('<p class="muted">本周没有检测到产出型事件——工作块的标题里没有出现'
            '「写/实现/跑通/测试/demo/笔记/交付」这类动作词。可以试着把下一周的标题改成产出型。</p>')
    add("</div>")

    add('<div class="card"><h4>自我观察</h4><div id="summary-extra"><!-- CLAUDE_SUMMARY --></div></div>')

    # 4 下周建议
    add("<h2>四、下周建议</h2>")
    add('<div class="card"><h4>自动诊断</h4><ul>')
    for sev, title, hint in ins:
        add(f"<li><b>{esc(title)}</b> — {esc(hint)}</li>")
    add("</ul></div>")
    add('<div class="card"><h4>行动清单</h4><div id="advice-extra"><!-- CLAUDE_ADVICE --></div></div>')
    add("</main></body></html>")

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"report-{start.isoformat()}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(H))
    if not a.no_open:
        try:
            webbrowser.open("file://" + path)
        except Exception:
            pass

    # ---- Markdown 摘要 ----
    print(f"# 周报 {start} ~ {end - dt.timedelta(days=1)}")
    print(f"\nHTML: {path}\n")
    print("| 板块 | 本期 | 占比 | 上期 | 变化 |")
    print("|---|---:|---:|---:|---:|")
    for name, h, ph, _ in rows:
        print(f"| {name} | {fmt_h(h)}h | {pct(h, week_h)} | {fmt_h(ph)}h | "
              f"{'+' if h - ph >= 0 else ''}{h - ph:.2f}h |")
    print("\n## 自动诊断")
    for sev, title, hint in ins:
        print(f"- [{sev}] {title} — {hint}")
    print("\n## 其他指标")
    rh = {n: h for n, h, _, _ in rows}
    print(f"- 蓝 : 紫 = {rh.get('蓝色', 0):.2f}h : {rh.get('紫色', 0):.2f}h（板块合计）")
    print(f"- 输入 : 输出 = {m.get('in_h', 0):.2f}h : {m.get('out_h', 0):.2f}h"
          f"（输出 {m.get('out_share', 0) * 100:.0f}%）；带验收标准 {m.get('accept_n', 0)}/{m.get('out_n', 0)}")
    for e in m.get("out_ev", [])[:8]:
        print(f"  - {'✓' if has_acceptance(e['title']) else '○'} {e['s']:%m-%d} {e['title']} ({e['h']:.2f}h)")
    print(f"- 碎片化 {m.get('frag_ratio', 0) * 100:.0f}% ≤30min；中位段长 {m.get('median_session', 0) * 60:.0f}min")
    print(f"- 切换 {m.get('switches', 0)} 次（{m.get('switches_per_day', 0):.1f}/天）；"
          f"同类相邻 {m.get('same_bucket_ratio', 0) * 100:.0f}%；无休息衔接 {m.get('no_break_pairs', 0)} 次")
    print(f"- 最长连续在忙 {m.get('longest_busy', 0):.1f}h")
    if m.get("sleep_n"):
        print(f"- 主睡眠 {m['sleep_n']} 晚 均 {m['sleep_avg']:.2f}h；入睡 ±{m['bed_std']:.0f}min，起床 ±{m['wake_std']:.0f}min；补觉 {m.get('naps', 0):.2f}h")
    print(f"- 娱乐 {m['ent']:.2f}h（红线 {ENT_REDLINE_PER_WEEK * days / 7:.1f}h）")


if __name__ == "__main__":
    main()
