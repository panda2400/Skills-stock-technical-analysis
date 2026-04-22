# Host Adapters

Use this reference when deciding how to obtain valid inputs for the analysis scripts. The scripts only accept local CSV files; host adapters are responsible for data acquisition.

## Universal Rule

Do not invent historical bars. If a host cannot provide reliable daily OHLCV for the requested ticker, ask the user for CSV files that follow `references/data-contract.md`.

## Codex Workflow

Codex may have file access, browser access, or simple finance quote tools, but those are not guaranteed to provide 120 historical OHLCV bars.

Preferred order:

1. Use user-provided CSV files when attached or available in the workspace.
2. Use an approved, reliable local data source if one exists in the workspace.
3. If only current quote data is available, explain that it is insufficient for this skill and ask for daily OHLCV CSV.

Codex should not treat a spot quote, chart screenshot, or remembered price history as a substitute for OHLCV input. A chart screenshot can guide the conversation, but the script engine still needs CSV data for a full report.

## Claude/Cowork Workflow

When market-data MCPs are available, use them to fetch OHLCV and save local CSV files before running scripts.

Suggested defaults:

| market | primary symbol style | benchmark | notes |
|---|---|---|---|
| US | `HOOD`, `NVDA`, `TSLA` | `SPY` | Use Alpha Vantage or Yahoo Finance style daily bars when available. |
| A-share | `600519`, `000001` | `000300` | Use akshare-style A-share daily bars; handle flaky endpoints with fallbacks. |
| HK | `00700`, `09988` | `HSI` | Delayed data is acceptable if disclosed in data gaps. |

A-share fallback preference:

1. Eastmoney daily bars.
2. Sina daily bars with `sh`/`sz` prefix when needed.
3. User-provided CSV.

For benchmark failure, continue with qualitative RS fallback and make the report disclose it.

## User CSV Workflow

When the user supplies data:

1. Confirm which file is daily, weekly, and benchmark.
2. Normalize headers to `date, open, high, low, close, volume` if needed.
3. Sort ascending by `date`.
4. Run `analyze.py`.
5. If the benchmark is missing, keep qualitative RS fallback instead of fabricating one.

## Earnings Dates

Use earnings dates only when they come from a current, reliable source or from the user. If unavailable, omit `--earnings-date`; do not infer a specific date from memory. A-share disclosure-season warnings are handled by `analyze.py` for months 4, 8, and 10.

## Data-Gap Language

When inputs are missing or synthesized, state the limitation plainly:

- Weekly omitted: "周线由日线聚合生成。"
- Benchmark omitted: "相对强弱仅定性判断，无法验证超额收益。"
- Earnings date missing: "财报日期未提供，执行前需自行核对。"
- Insufficient daily bars: "日线样本不足，结构结论置信度下降。"
