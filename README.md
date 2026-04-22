# Skills-stock-technical-analysis

A Claude / Codex Skill for producing **structure-first, conditional technical decision-support reports** on individual stocks (US / A-share / HK).

The scripts do the math; the skill's job is to pick the right ticker, feed in verified OHLCV data, run the engine, and deliver the rendered report **without inventing market data**.

> ⚠️ Output is technical decision support, **not investment advice**. Every signal carries an explicit trigger, stop, invalidation, and expiry.

## What it does

Given daily OHLCV (optionally weekly + benchmark), the skill produces an 8-section Chinese report:

1. 核心定位 — ticker snapshot, trend classification
2. 结构分析 — swing highs/lows, HH/HL or LH/LL structure
3. 成交量结构 — accumulation / distribution read
4. 市场环境 — relative strength vs. benchmark
5. 关键价位 — support, resistance, invalidation
6. 指标辅助 — MA / RSI / MACD / ATR as confirmation only
7. 基本面事件 — earnings window / A-share季报 auto-flag
8. 条件信号 — conditional setups with full trigger / stop / target / expiry

## Repo layout

```
.
├── SKILL.md                    # Skill manifest and workflow
├── agents/
│   └── openai.yaml             # Codex agent interface
├── scripts/                    # Deterministic analysis engine
│   ├── analyze.py              # Orchestrator → StockState JSON
│   ├── render.py               # StockState JSON → Markdown report
│   ├── load.py                 # CSV loading + liquidity classification
│   ├── swings.py               # Swing-point / structure detection
│   ├── patterns.py             # Chart-pattern detection
│   ├── levels.py               # Support / resistance
│   ├── volume.py               # Volume regime
│   ├── rs.py                   # Relative strength vs. benchmark
│   ├── indicators.py           # MA / RSI / MACD / ATR
│   ├── signals.py              # Conditional setup assembly
│   └── risk.py                 # Position sizing / exposure caps
├── references/
│   ├── data-contract.md        # CSV + StockState JSON contract
│   ├── host-adapters.md        # Codex / Claude-Cowork / user-CSV paths
│   └── computation.md          # Threshold & scoring details (debug only)
└── templates/
    └── zh-S1.md                # Chinese 8-section report template
```

## Quick start

### 1. Prepare OHLCV CSVs

Daily data is required (≥ 60 bars, 120 preferred). Weekly and benchmark are optional — if omitted, `analyze.py` synthesizes weekly from daily and downgrades RS to qualitative mode.

CSV schema: `date, open, high, low, close, volume`, sorted ascending. See `references/data-contract.md`.

### 2. Run the engine

```bash
python scripts/analyze.py \
  --ticker HOOD \
  --market US \
  --daily /tmp/stk/HOOD_daily.csv \
  --weekly /tmp/stk/HOOD_weekly.csv \
  --benchmark /tmp/stk/SPY_daily.csv \
  --earnings-date 2026-05-07 \
  --account-size 100000 \
  --risk-profile balanced \
  --out /tmp/stk/HOOD_state.json
```

Markets: `US` (benchmark `SPY`) · `A` (6-digit code, benchmark `000300`) · `HK` (5-digit code, benchmark `HSI`).

### 3. Render the report

```bash
python scripts/render.py \
  --state /tmp/stk/HOOD_state.json \
  --template zh-S1 \
  --out /tmp/stk/HOOD_report.md
```

## Design principles

- **Structure-first**: swings, volume, RS, patterns, levels and risk boundaries are computed before indicator commentary. Indicators are confirmation, not the primary signal.
- **Deterministic engine + thin LLM wrapper**: all numbers come from the scripts' StockState JSON. The model doesn't redo math in prose.
- **Conditional language**: "若 A 确认 → 触发 B；若 C 失守 → 失效" instead of unconditional buy/sell.
- **Fail loud, not silent**: missing benchmark, missing earnings date, or fallback weekly data show up in the report — they are not hidden in footnotes.
- **No network inside the skill**: data fetching is the host environment's responsibility. The skill consumes CSV files only.

## Using as a Skill

- **Claude / Claude Code**: drop this repo into a `skills/` directory. The `SKILL.md` frontmatter activates the skill when the user asks about a ticker, chart, support/resistance, or trade setup.
- **Codex**: `agents/openai.yaml` registers the agent entry point.
- Read `references/host-adapters.md` for host-specific data paths and fallback behavior.

## Pre-delivery checklist (enforced by SKILL.md)

- Ticker and market are correct
- Daily data is real, ascending, ≥ 60 bars
- Benchmark is real, or the report clearly marks qualitative RS fallback
- No OHLCV, earnings date, or benchmark value was invented
- Every signal has a trigger, stop, status, and invalidation
- Data-gap appendix is shown when fallbacks were used

## Disclaimer

Educational and decision-support use only. Markets can invalidate any pattern; always respect the stop and invalidation level stated in the report.
