# Time Ledger · weekly-time-report

[中文](README.md) | **English**

Turn a week of macOS Calendar events into a **visual, actionable time report**.

This is a [Claude Code](https://claude.com/claude-code) skill: the Python script fetches events, computes metrics, and renders charts; Claude writes the "self-observation" and "action list" sections. The output is a single self-contained HTML report (plus a Markdown summary on stdout).

> 📄 Sample report: [samples/report-2026-11-14.html](samples/report-2026-11-14.html) (download and open in a browser)

---

## Why

Your calendar already records how you actually spent the week — but almost nobody looks back at it. This project turns that one-off "scroll through the calendar" into a repeatable workflow:

- **Group by color family** — your own calendar system (blue = maintenance + planning, purple = growth, gray = sleep + life upkeep, yellow = family & friends, green = exercise, pink = reflection, red = entertainment) summarized into per-category hours, shares, and period-over-period changes.
- **Break down each category** — internal composition, daily distribution, and observations, in a fixed order with fixed definitions.
- **Automatic diagnostics** — fragmentation (share of ≤30 min blocks, median block length), context switching, same-type task adjacency, breaks between blocks, longest continuous busy stretch, sleep regularity, input-vs-output ratio, entertainment red line.
- **Claude-completed analysis** — on top of the metrics, Claude reads your calendar titles and intents to write a "self-observation" and a concrete "action list".

## What you get

Four fixed sections:

1. **Where the time went** — KPIs, 100% stacked share bar, grouped bars + table comparing with the previous period
2. **Category deep dives** — per color family: total / share / change, composition bars, daily distribution, observations
3. **Weekly summary** — automatic metrics, key numbers, output ledger, self-observation (written by Claude)
4. **Next-week advice** — automatic diagnosis, action list (written by Claude)

All charts are inline SVG. The report is one HTML file, respects the system dark mode, and needs no CDN or JavaScript.

## How it works

```
macOS Calendar (EventKit)
   │  uvx mac-calendar-mcp events ...   (read-only, local index)
   ▼
scripts/weekly_report.py
   ├── fetch / classify by color family / compute metrics
   ├── render HTML → out/report-<start>.html
   └── print Markdown summary → stdout
   ▼
Claude reads the HTML and fills two placeholders:
   <!-- CLAUDE_SUMMARY -->  → Section 3 "Self-observation"
   <!-- CLAUDE_ADVICE -->   → Section 4 "Action list"
```

## Repository layout

```
weekly-time-report/
├── SKILL.md                    # Claude Code skill definition (triggers, definitions, writing rules)
├── scripts/weekly_report.py    # fetch + metrics + HTML rendering (stdlib only)
├── samples/                    # sample output
└── out/                        # generated reports (git-ignored)
```

## Requirements

- **macOS** (uses the system Calendar; Linux/Windows are not supported)
- [**uv**](https://docs.astral.sh/uv/) (provides `uvx`) and [`mac-calendar-mcp`](https://pypi.org/project/mac-calendar-mcp/)
- **Python 3.8+** (standard library only, no dependencies)
- **Claude Code** (for sections 3 & 4; the script can also be used standalone)

Grant Calendar access once:

```bash
uvx mac-calendar-mcp authorize   # click "Allow" in the dialog
uvx mac-calendar-mcp index       # build the local index
uvx mac-calendar-mcp calendars   # verify your calendar list
```

## Install

As a Claude Code skill (recommended):

```bash
git clone https://github.com/yq-space/time-ledger.git
cp -R time-ledger ~/.claude/skills/weekly-time-report
```

Then ask Claude Code for a weekly report — e.g. "generate my weekly report" or "how did I spend last week".

Standalone CLI:

```bash
python3 scripts/weekly_report.py --start 2026-11-14 --end 2026-11-21
```

## Usage

```bash
# Previous calendar week (Mon–Sun)
python3 scripts/weekly_report.py

# Current week to date
python3 scripts/weekly_report.py --week this

# Explicit range (start inclusive, end exclusive)
python3 scripts/weekly_report.py --start 2026-11-14 --end 2026-11-21

# 7 days from the start date
python3 scripts/weekly_report.py --start 2026-11-14 --days 7

# Don't open the browser automatically
python3 scripts/weekly_report.py --no-open
```

Output:

- `out/report-<start>.html` (the script tries to open it in your browser)
- A Markdown summary on stdout, ready to be fed back to Claude

## Configuration

The script's defaults encode the author's own calendar system. **Before using it for your own data, adapt these settings** at the top of [scripts/weekly_report.py](scripts/weekly_report.py):

| Setting | Purpose |
| --- | --- |
| `TZ` | Local timezone used for every time computation |
| `CAL_COLOR_OVERRIDE` | Calendar-name keyword → color family (highest priority). **Start here** |
| `BLUE_CAL_HINTS` / `PURPLE_CAL_HINTS` | Which calendars count as "work blocks". Multiple aliases prevent silently-zero metrics after a rename |
| `GRAY_CAL_HINT` / `ENT_CAL_HINTS` | Life-upkeep (gray) / entertainment (red) calendars |
| `EXCLUDE_CAL_HINTS` | Calendars to ignore (holidays, birthdays, reminders, …) |
| `ENT_REDLINE_PER_WEEK` | Entertainment red line, in hours per week (pro-rated by days) |
| `*_KW` keyword lists | Sub-buckets: course/code, job hunting, driving lessons, sleep, meals, commute, hygiene — plus the input/output/acceptance heuristics |
| `WORK_HINTS` | Work keywords, used to separate non-work items (e.g. driving lessons) from the growth category |

Classification is two-tier: match the calendar name against `CAL_COLOR_OVERRIDE` first; if nothing matches, fall back to the calendar's RGB color. The simplest setup is to color your calendars accordingly in macOS Calendar and add their name keywords to `CAL_COLOR_OVERRIDE`.

More definitions (how sleep is counted, how input/output is detected, what counts as an acceptance criterion) are documented in [SKILL.md](SKILL.md).

## Privacy

- Calendar data is read **locally only**. The script makes no network requests and uploads nothing.
- `mac-calendar-mcp` accesses Calendar in read-only mode.
- Generated reports contain your event titles and sleep schedule — **redact before sharing or committing**. The `samples/` report is the author's own example output with event titles anonymized, included only to preview the result.
- `out/` is git-ignored to avoid committing personal reports by accident.

## Limitations

- macOS only.
- Keyword heuristics are Chinese-first (看/学/听 = input, 写/实现/测试 = output). The more specific your event titles, the better the metrics.
- Sections 3 & 4 are written by Claude; a pure CLI run leaves the placeholders empty.
- The color semantics (blue = maintenance, purple = growth, …) are the author's personal system — change them as you like.

## License

[MIT](LICENSE) © yq-space
