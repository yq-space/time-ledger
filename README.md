# Time Ledger · weekly-time-report

**中文** | [English](README.en.md)

把 Mac 日历里的一周，变成一份看得懂、能行动的 **时间使用周报**。

这是一个 [Claude Code](https://claude.com/claude-code) Skill：脚本负责取数、统计和画图，Claude 负责补写「自我观察」和「行动清单」。最终产出一份单文件、可离线打开的 HTML 报告，同时输出 Markdown 摘要。

> 📄 示例报告：[samples/report-2026-11-14.html](samples/report-2026-11-14.html)（下载后浏览器打开）

---

## 它解决什么问题

日历记录了大量真实的时间痕迹，但很少有人会回头统计。这个项目把一次性的「翻日历」变成可重复的流程：

- **按色系统计**：把你自己的日历体系（蓝=维护+规划、紫=成长、灰=睡眠+生活维持、黄=家人朋友、绿=锻炼、粉=自省、红=娱乐）汇总成板块时长、占比，并与上一期对比。
- **逐板块拆解**：每个板块的内部构成、每日分布、观察要点，固定顺序、固定口径。
- **自动诊断**：碎片化（≤30min 占比 / 中位段长）、上下文切换、同类任务相邻度、块间休息、最长连续在忙、睡眠规律、输入与输出配比、娱乐红线。
- **Claude 补写**：在自动指标之上，结合你的日历标题和意图，写「自我观察」和可执行的「行动清单」。

## 效果

报告固定四节：

1. **时间去向** — KPI、100% 堆叠占比条、与上期分组对比图 + 表
2. **各色块详细分析** — 每个色系：总量 / 占比 / 较上期变化、内部构成条形图、每日分布、观察
3. **本周总结** — 自动指标 + 关键数字 + 产出清单 + 自我观察（Claude 补写）
4. **下周建议** — 自动诊断 + 行动清单（Claude 补写）

图表都是内联 SVG，报告是单个 HTML 文件，支持系统深色模式，不依赖任何 CDN 或 JS。

## 工作原理

```
Mac 日历 (EventKit)
   │  uvx mac-calendar-mcp events ...   （只读，本地索引）
   ▼
scripts/weekly_report.py
   ├── 取数 / 按色系归类 / 计算指标（工作、睡眠、娱乐、碎片化、切换、输入输出…）
   ├── 渲染 HTML → out/report-<start>.html
   └── 打印 Markdown 摘要 → stdout
   ▼
Claude 读取 HTML，替换两处占位标记：
   <!-- CLAUDE_SUMMARY -->  → 第 3 节「自我观察」
   <!-- CLAUDE_ADVICE -->   → 第 4 节「行动清单」
```

## 目录结构

```
weekly-time-report/
├── SKILL.md                    # Claude Code Skill 定义（触发词、口径、写作要求）
├── scripts/weekly_report.py    # 取数 + 指标 + HTML 渲染（仅用 Python 标准库）
├── samples/                    # 示例报告
└── out/                        # 运行输出（不提交）
```

## 前置要求

- **macOS**（依赖系统日历，Linux / Windows 不适用）
- [**uv**](https://docs.astral.sh/uv/)（提供 `uvx`）和 [`mac-calendar-mcp`](https://pypi.org/project/mac-calendar-mcp/)
- **Python 3.8+**（脚本只用标准库，无需安装依赖）
- **Claude Code**（用于补写第 3、4 节；只用脚本生成 HTML 也可以）

首次使用前需要授权日历访问：

```bash
uvx mac-calendar-mcp authorize   # 弹窗点「允许」
uvx mac-calendar-mcp index       # 建立本地索引
uvx mac-calendar-mcp calendars   # 核对日历列表
```

## 安装

作为 Claude Code Skill 安装（推荐）：

```bash
git clone https://github.com/yq-space/time-ledger.git
cp -R time-ledger ~/.claude/skills/weekly-time-report
```

之后在 Claude Code 里说「出周报」「看看上周时间怎么用的」即可触发。

也可以只当命令行工具用：

```bash
python3 scripts/weekly_report.py --start 2026-11-14 --end 2026-11-21
```

## 使用

```bash
# 上一自然周（周一 ~ 周日）
python3 scripts/weekly_report.py

# 本周至今
python3 scripts/weekly_report.py --week this

# 指定日期（左闭右开）
python3 scripts/weekly_report.py --start 2026-11-14 --end 2026-11-21

# 从起始日算 7 天
python3 scripts/weekly_report.py --start 2026-11-14 --days 7

# 不自动打开浏览器
python3 scripts/weekly_report.py --no-open
```

输出：

- `out/report-<start>.html`（脚本会尝试自动打开浏览器）
- stdout 的 Markdown 摘要，可喂给 Claude 继续分析

## 自定义配置

脚本的「世界观」默认按作者自己的日历体系写死，**开源使用前请先按自己的日历改造**。配置都在 [scripts/weekly_report.py](scripts/weekly_report.py) 顶部：

| 配置 | 作用 |
| --- | --- |
| `TZ` | 本地时区，所有时间运算都按它换算 |
| `CAL_COLOR_OVERRIDE` | 日历名关键词 → 色系映射（优先匹配）。**最常用**，按自己的日历名改这里即可 |
| `BLUE_CAL_HINTS` / `PURPLE_CAL_HINTS` | 哪些日历算「工作块」，支持多个别名，防止日历改名后指标静默归零 |
| `GRAY_CAL_HINT` / `ENT_CAL_HINTS` | 生活维持（灰）/ 娱乐（红）的日历名 |
| `EXCLUDE_CAL_HINTS` | 排除的日历（节假日、生日、提醒事项等） |
| `ENT_REDLINE_PER_WEEK` | 娱乐红线（小时/周，按天数折算） |
| `*_KW` 系列 | 细分桶关键词：课程/代码、求职、驾考、睡眠、吃饭、交通、洗漱……以及输入/输出/验收词的判定 |
| `WORK_HINTS` | 工作关键词，用于把「驾考」等非工作内容从成长类中剔除 |

色系归类是**两级**的：先按日历名匹配 `CAL_COLOR_OVERRIDE`，匹配不到再用日历颜色的 RGB 自动归类。所以最简单的改法是——把自己的日历在 macOS 日历里染成对应颜色，并把日历名关键词加进 `CAL_COLOR_OVERRIDE`。

更多口径约定（睡眠如何统计、输入输出如何判定、验收标准如何识别）见 [SKILL.md](SKILL.md)。

## 隐私说明

- 日历数据**只在本机读取**，脚本不发起任何网络请求，也不上传任何数据。
- `mac-calendar-mcp` 以只读方式访问日历。
- 生成的报告包含你的日程标题和作息数据，**提交或分享前请自行脱敏**。仓库里的 `samples/` 是作者的示例输出（事件标题已匿名化），仅供预览效果。
- `out/` 已在 `.gitignore` 中，避免误提交个人报告。

## 已知限制

- 仅支持 macOS。
- 关键词判定是中文启发式（看/学/听 = 输入，写/实现/测试 = 输出），标题写得越具体，统计越准。
- 报告里的「自我观察」和「行动清单」依赖 Claude 生成；纯命令行运行会保留占位标记。
- 色系语义（蓝=维护、紫=成长……）是作者的个人体系，请按需改造。

## License

[MIT](LICENSE) © yq-space
