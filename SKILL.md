---
name: stock-technical-analysis
description: Structure-first stock technical analysis and decision-support reports for individual equities. Use when the user asks about a specific ticker, Chinese stock name, K-line/chart screenshot, support/resistance, trend, timing, stop-loss, take-profit, relative strength, volume, pattern recognition, or technical trade setup for US, A-share, or HK stocks. Produce conditional decision support, not unconditional buy/sell advice.
---

# Stock Technical Analysis

Use this skill to turn verified OHLCV data into a structured technical decision-support report for a single stock. The bundled scripts perform deterministic analysis; the agent's job is to identify the instrument, obtain or request valid local data, run the scripts, and deliver the rendered report without inventing market data.

## Operating Principles

- Use a structure-first framework: swing structure, volume, relative strength, patterns, levels, and risk boundaries come before indicator commentary.
- Treat the scripts in `scripts/` as the analysis engine. Do not recompute swings, RS, indicators, patterns, or sizing in prose when the state JSON already provides them.
- Keep the output as decision support. Prefer "if condition A confirms, setup B is observable; if condition C fails, invalidate it" over unconditional "buy/sell/hold" instructions.
- Do not fabricate OHLCV, benchmark returns, earnings dates, or liquidity. If data is unavailable, ask for CSV input or produce a data-insufficient response.
- Do not write network-fetching code inside the skill. Data fetching is handled by the host environment or by user-provided CSV files.

## Workflow

1. Identify the ticker, market, benchmark, and risk context.
   - US: ticker like `HOOD`, benchmark `SPY`.
   - A-share: six digits like `600519`, benchmark `000300`.
   - HK: five digits like `00700`, benchmark `HSI`.
   - If the name is ambiguous, ask once for the exact ticker/market.

2. Obtain local CSV files before running analysis.
   - Required: daily OHLCV, preferably 120 bars and at least 60 bars.
   - Optional: weekly OHLCV, preferably 60 bars. If omitted, `analyze.py` synthesizes weekly bars from daily data.
   - Optional: benchmark daily OHLCV. If omitted, relative strength falls back to qualitative mode and confirm-level signals are downgraded.
   - Optional: next earnings date in `YYYY-MM-DD`.
   - Read `references/host-adapters.md` for host-specific data paths and fallback behavior.
   - Read `references/data-contract.md` for the exact CSV and JSON contracts.

3. Run the analysis engine.

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

Use `--log-dir /path/to/logs` only when the user explicitly wants signal history persisted. Without `--log-dir`, the script should only write the requested state JSON.

4. Render the report.

```bash
python scripts/render.py \
  --state /tmp/stk/HOOD_state.json \
  --template zh-S1 \
  --out /tmp/stk/HOOD_report.md
```

Show the rendered Markdown to the user. Add at most one short note if relevant context is outside the script output, such as a known macro event or missing data source.

## Report Rules

- Keep the Chinese 8-section structure: 核心定位, 结构分析, 成交量结构, 市场环境, 关键价位, 指标辅助, 基本面事件, 条件信号.
- State that the report is technical decision support, not investment advice.
- Every signal must include a trigger condition, entry/reference range, stop price, target levels, status, expiry, and risk boundary.
- Use "仓位上限/参考曝险" language, not "建议买入仓位".
- If benchmark data is missing, explicitly show "仅定性判断" and preserve the downgrade.
- If earnings or data gaps exist, surface them visibly; do not hide warnings in a footnote.
- Do not add new emoji beyond the state markers already produced by the scripts.

## When To Read References

- `references/data-contract.md`: read before preparing CSV inputs, validating state JSON, or checking CLI behavior.
- `references/host-adapters.md`: read when deciding how to obtain data in Codex, Claude/Cowork, or user-CSV workflows.
- `references/computation.md`: read only when debugging an unexpected classification, signal, threshold, or sizing result.

## Pre-Delivery Checklist

- Ticker and market are correct.
- Daily data is real, ascending by date, and has at least 60 bars.
- Benchmark data is real or the report clearly marks qualitative RS fallback.
- No OHLCV, earnings date, or benchmark values were invented.
- The rendered report uses conditional decision-support language.
- No signal lacks a stop price, trigger, status, or invalidation boundary.
- Data-gap appendix is shown when fallbacks or missing inputs were used.
