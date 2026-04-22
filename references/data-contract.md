# Data Contract

Use this reference when preparing inputs, checking script output, or validating a rendered report.

## Input CSV

All OHLCV inputs are local CSV files. The scripts do not fetch data from the internet.

Required columns:

| column | type | notes |
|---|---|---|
| `date` | date-like string | Parsed by pandas; sort ascending before analysis. |
| `open` | number | Split-adjusted when the data source supports it. |
| `high` | number | Must be greater than or equal to open/close for normal bars. |
| `low` | number | Must be less than or equal to open/close for normal bars. |
| `close` | number | Split-adjusted when available. |
| `volume` | integer or float | Share volume. For A-share Eastmoney data, volume may be in hands; `load.py` adjusts liquidity for market `A`. |

Minimum usable input:

- Daily: at least 60 bars; 120 bars recommended.
- Weekly: optional; 60 bars recommended. If missing, `analyze.py` synthesizes weekly bars from daily data.
- Benchmark: optional. If missing or insufficient overlap, relative strength becomes qualitative.

## CLI Contract

Analyze:

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

Stable arguments:

| argument | required | notes |
|---|---:|---|
| `--ticker` | yes | User-facing symbol. |
| `--market` | no | `US`, `A`, or `HK`; default `US`. |
| `--daily` | yes | Daily OHLCV CSV. |
| `--weekly` | no | Weekly OHLCV CSV; synthesized if omitted. |
| `--benchmark` | no | Benchmark daily OHLCV; qualitative RS if omitted. |
| `--earnings-date` | no | `YYYY-MM-DD`; used only for risk warnings. |
| `--correlated-asset` | no | Free-text annotation such as `BTC proxy`. |
| `--account-size` | no | Used for reference exposure math. |
| `--risk-profile` | no | `aggressive`, `balanced`, or `conservative`. |
| `--out` | yes | State JSON path. |
| `--log-dir` | no | If supplied, append `signal_log.jsonl`; otherwise do not persist signal history. |

Render:

```bash
python scripts/render.py \
  --state /tmp/stk/HOOD_state.json \
  --template zh-S1 \
  --out /tmp/stk/HOOD_report.md
```

## State JSON Contract

The state JSON is the canonical interface between deterministic analysis and report rendering. Preserve these top-level fields:

`ticker`, `market`, `asof`, `price`, `trend`, `phase`, `one_line_summary`, `swings`, `volume`, `rs`, `indicators`, `levels`, `patterns`, `liquidity`, `earnings_days_to`, `earnings_date`, `warnings`, `data_summary`, `signals`, `exposure`, `account_size`, `risk_profile`, `skill_version`.

Important interpretation rules:

- `trend` is one of `uptrend`, `downtrend`, `range`, or `unclear`.
- `rs.qualitative == true` means benchmark alpha is not verified; do not describe RS as numeric outperformance.
- `warnings` must be reflected in the report when they affect data quality, earnings risk, volatility, or liquidity.
- `signals` are conditional setups, not instructions to trade.

## Output Contract

The report must be Chinese by default and keep this section order:

1. 核心定位
2. 结构分析
3. 成交量结构
4. 市场环境
5. 关键价位
6. 指标辅助
7. 基本面事件
8. 条件信号

Every conditional signal must include:

- Status marker.
- Trigger condition.
- Reference entry range.
- Explicit stop price.
- T1 and T2 reference levels.
- Position/exposure upper bound.
- Expiry or validity window.
- Notes for earnings, liquidity, volatility, RS fallback, or correlated assets when applicable.
