# Computation Reference (v0.2.0)

Formulas and thresholds used by the scripts. Consult this when a script's
output looks wrong and you want to understand why.

---

## Swing detection (`swings.py`)

**Fractal pivot definition** (N = 2 for daily, N = 1 for weekly):

- A bar at index `i` is a **fractal high** if `high[i] > max(high[i-N..i-1])` AND `high[i] > max(high[i+1..i+N])`.
- A bar at index `i` is a **fractal low** if `low[i] < min(low[i-N..i-1])` AND `low[i] < min(low[i+1..i+N])`.

**Label sequence**:

After finding fractals chronologically, compare each to the previous pivot of the same kind:
- `HH` = high > previous high
- `LH` = high ≤ previous high
- `HL` = low > previous low
- `LL` = low ≤ previous low
- First high/low of the sequence is labeled `first_high` / `first_low`.

**Pattern classification** (looks at the most recent HH/LH and HL/LL):

| Last high | Last low | Pattern            |
|-----------|----------|---------------------|
| HH        | HL       | uptrend_confirmed   |
| LH        | LL       | downtrend_confirmed |
| HH        | LL       | range (volatile expansion) |
| LH        | HL       | range (contracting coil)   |
| HH only   | —        | uptrend_tentative   |
| LH only   | —        | downtrend_tentative |

**Key levels derived from pivots**:

- `nearest_swing_high`: lowest high above current price (next overhead resistance). Falls back to `highs[-1]` if price is above all highs (i.e. in a breakout).
- `nearest_swing_low`: most recent swing low that is still below current price (stop-loss anchor).
- `highest_recent_high`: max of last 6 pivot highs (for "has this breakout already extended?" check).
- `pivots_all`: full chronological list — consumed by `patterns.py` for cluster detection (v0.2.0).

---

## Volume regime (`volume.py`) — v0.2.0

Inputs: last 14 bars' volume, 20-day SMA of volume, per-bar close and change.

Compute:
- `avg_ratio_14d` = mean of (volume / 20-day-SMA) over last 14 bars
- `max_ratio_14d` = max single-day ratio in that window
- `pct_sum` = sum of last 14 daily pct_change
- `up_days_with_vol` = days with positive pct_change AND volume_ratio > 1.0
- `down_days_with_vol` = days with negative pct_change AND volume_ratio > 1.0
- `pullback_signature` = match when last 5 days have ≥ 3 days of `vol_ratio < 0.9` AND total_change between -8% and -1%

**Classification** (priority order — first match wins):

| Priority | Condition                                                              | Regime     |
|----------|------------------------------------------------------------------------|------------|
| 1        | `max_ratio_14d >= 2.5`                                                 | 爆量        |
| 2        | `pct_sum < -3%` AND `avg_ratio >= 1.3` AND `down_vol > up_vol`         | 出货放量    |
| 3        | `pct_sum > 3%` AND `avg_ratio >= 1.1` AND `up_vol > down_vol`          | 吸筹放量    |
| 4 (NEW)  | pullback_signature matches                                             | 缩量回调    |
| 5        | `avg_ratio < 0.7`                                                      | 干枯        |
| 6        | everything else                                                        | 平淡        |

**Divergence flag**:

- `price_up_volume_down`: sum of pcts > 0 AND volume_trend = "falling" (weakening rally)
- `price_down_volume_up`: sum of pcts < 0 AND volume_trend = "rising" (distribution)
- `price_down_volume_down`: seller exhaustion (potentially bullish)

`volume_trend` is "rising" / "falling" / "flat" based on second-half mean minus first-half mean of ratios (threshold ±0.15).

---

## Relative strength (`rs.py`) — v0.2.0

For each lookback window (20d, 50d):
- `stock_ret` = stock return over window
- `bench_ret` = benchmark return over window
- `alpha` = `stock_ret - bench_ret`

**Beta**: regression of daily stock returns on daily benchmark returns over the last 50 bars (log-returns).

**Beta-adjusted alpha (50d)**: `stock_ret_50d - beta * bench_ret_50d`. Small value = rally was driven by market beta, not stock-specific strength.

**Decile** (1-10): fixed empirical bucket edges on alpha magnitude. For 20-day: edges at -10/-5/-3/-1.5/-0.5/0.5/1.5/3/5/10%. For 50-day: edges at -20/-10/-5/-2.5/-1/1/2.5/5/10/20%.

**Classification** (uses 20d and 50d deciles):

| Condition (priority order) | Classification        |
|----------------------------|------------------------|
| d20 ≥ 8 AND d50 ≥ 8        | strong_outperformer   |
| d20 ≥ 6 OR d50 ≥ 7         | outperformer          |
| d20 ≤ 2 AND d50 ≤ 2        | weak                  |
| d20 ≤ 4 OR d50 ≤ 3         | underperformer        |
| else                        | neutral               |

**Warning flag** `beta_driven_rally`: fires when `alpha_50d > 0` AND `beta > 1.3` AND `beta_adjusted_alpha_50d < 1%`. Signals that the stock's outperformance is mostly market beta and will likely give back on a market correction.

### Qualitative fallback (NEW in v0.2.0)

When `benchmark` is `None` or overlap < 55 bars, `compute_rs` returns a dict with `qualitative=True` and uses the stock's own 20d/50d returns with crude thresholds:

| 50d return | 20d return | classification    |
|------------|------------|-------------------|
| > +30%     | > +5%      | outperformer      |
| +15 to +30%| —          | neutral           |
| < -15%     | —          | underperformer    |
| < -30%     | —          | weak              |
| else       | —          | neutral           |

Downstream: `signals.py` demotes any `🟢 确认` signal to `🟡 试探` when `rs.qualitative=True`, because alpha is unverified.

---

## Indicators (`indicators.py`)

- **MA10 / MA20 / MA50**: simple moving averages of close.
- **RSI(14)**: Wilder's smoothing. Overbought if ≥ 70, oversold if ≤ 30. Extreme overbought ≥ 75 triggers a note on long signals.
- **MACD(12, 26, 9)**: standard. We report only `macd_hist` = MACD − signal.
- **ATR(14)**: Wilder's true range average. Used for stop-loss distance, T1/T2 target math in `signals.py`, AND (new in v0.2.0) for volatility-based position sizing in `risk.py`.

---

## Levels (`levels.py`)

Candidate sources:
- Swing pivots (last 20)
- MAs (10/20/50)
- 20-day high/low
- 52-week high/low
- Round numbers within ±10% of current price (nearest $1/$5/$10/$50/$100 step)

Each candidate scored by:
- `recency` (how recent the touch was)
- `confluence` (how many other candidates within 1% of it)
- `proximity` (closer to current price = higher score)

Clustered within 1% tolerance. Top 4 resistance (above price) + top 4 support (below price) returned, each with `importance` numeric rating and `basis` (why it's a level).

---

## Patterns (`patterns.py`) — NEW in v0.2.0

Four detectors run after swing analysis. All output `notes` that render.py puts at the top of the report, and structured fields that signals.py consumes.

### Double-top / triple-top

**Inputs**: `pivots_all` (full chronological list) from swings.

**Algorithm**:
1. Filter to pivots with `kind=="high"` within the last `lookback_bars = 90` bars.
2. For each anchor high, collect other highs within `tolerance_pct = 2%` of anchor's price.
3. If cluster size ≥ `min_touches = 2`, this is a candidate.
4. Check: did price ever close ≥ 0.2% ABOVE the cluster price after the first touch? If yes, cluster was cleanly broken — return None.
5. Compute neckline = lowest low between earliest and latest touch.
6. Mark `confirmed=True` if current close < neckline × 0.995.

**Returned**:
```
{
  'kind': 'double_top' | 'triple_top' | ...,
  'cluster_price': float,
  'touch_count': int,
  'touches': [{date, price, idx}, ...],
  'neckline': float | None,
  'confirmed': bool,
}
```

Double-bottom is the exact mirror (sign-flipped).

### Intraday false breakout

**Inputs**: the most recent (today's) bar's high/low/close, plus reference levels from swings (`nearest_swing_high` / `highest_recent_high` for upside; `nearest_swing_low` for downside).

**Condition**:
- **Upside false break**: today's high > reference × (1 + 0.001) AND today's close < reference.
- **Downside false break**: today's low < reference × (1 − 0.001) AND today's close > reference.

When this fires, `signals.py` demotes status from `✅ 触发中` to `⏳ 待收盘确认`, preventing premature chase.

### Range compression (coil)

**Condition**: over the last 20 bars, `max(high) / min(low) ≤ 1.07` (span ≤ 7%). Pre-breakout indicator.

---

## Signal engine (`signals.py`) — v0.2.0

Six rules run in sequence. Each produces 0 or 1 signal.

- **Rule 1 (🟢 long confirm)**: `trend=uptrend`, daily pattern=uptrend_confirmed, `rs.classification in {strong_outperformer, outperformer}`, `volume.regime in {吸筹放量, 平淡, 缩量回调}`, NOT a confirmed double-top. Sub-cases (待触发 / 待回踩 / ✅ 触发中 / ⏳ 待收盘确认) decided by price-vs-breakout distance and intraday false-break flag.
- **Rule 2 (🟡 long probe)**: `trend in {uptrend, range}`, pattern in {tentative, range}, RS not weak/underperformer, NOT confirmed double-top.
- **Rule 3 (🔵 long compound)**: uptrend_confirmed, last pivot = HH within 5 bars, volume regime = 吸筹放量, NOT confirmed double-top.
- **Rule 4 (🔴 short break)**: downtrend_confirmed, RS in {weak, underperformer}. Intraday false down-break → status 待收盘确认.
- **Rule 5 (🔴 short double-top, NEW v0.2.0)**: double-top confirmed with valid neckline. T1 = neckline − measured-move; T2 = 1.5×. Entry at retest of neckline.
- **Rule 6 (🟢 long double-bottom, NEW v0.2.0)**: double-bottom confirmed with valid neckline. Mirror of Rule 5.

**Post-rule filters** (applied to every signal):

- **Earnings**: days ≤ 3 → status forced to `🚫 禁追`. Days 4–14 → note added.
- **A-share earnings season**: if warnings contains `ashare_earnings_season`, every long gets a note.
- **Extreme RSI**: ≥ 75 → additional downside-caveat note.
- **爆量 regime**: every signal gets "警惕情绪化,减仓 1/3" note.
- **RS qualitative**: every 🟢 long demoted to 🟡 (because alpha is unverified).
- **Correlated asset**: if `--correlated-asset` supplied, every long gets a linking note.
- **Quality filter**: R < 0.5 → dropped entirely. R < 1.0 on 🟢 → demoted to 🟡.

---

## Position sizing (`risk.py`) — v0.2.0

### Three profiles

| Profile      | 🟡 试探 | 🟢 确认 | 🔵 加仓 | 🔴 做空 | cap  | max_loss (probe/confirm/compound/short) |
|--------------|--------|--------|--------|--------|------|-----------------------------------------|
| aggressive   | 5%     | 10%    | 15%    | 5%     | 15%  | 1.0 / 1.5 / 2.0 / 1.0%                  |
| balanced     | 3%     | 7%     | 10%    | 3%     | 12%  | 0.8 / 1.2 / 1.5 / 0.8%                  |
| conservative | 2%     | 5%     | 8%     | 2%     | 10%  | 0.5 / 0.8 / 1.0 / 0.5%                  |

### Multiplicative downgrades (NEW in v0.2.0)

Tier target is multiplied by two factors before guardrail enforcement:

**Volatility multiplier** (ATR as percent of price):
- atr_pct ≤ 3.0 → 1.00
- 3.0 < atr_pct ≤ 5.0 → 0.80
- 5.0 < atr_pct ≤ 7.0 → 0.60
- atr_pct > 7.0 → 0.40

**Liquidity multiplier** (from `load.classify_liquidity`):
- thin → 0.60
- moderate → 0.85
- liquid → 1.00

Final effective tier% = `base_tier_pct × vol_mult × liq_mult`, then capped at profile's hard cap.

### Max-loss guardrail

Applied AFTER the multiplicative downgrade. For each signal:

```
loss_if_stopped = effective_tier_pct × (entry − stop) / entry
if loss_if_stopped > max_loss_pct_for_tier:
    effective_tier_pct = max_loss_pct_for_tier × entry / (entry − stop)
```

This protects the tail: even if vol/liquidity allow a bigger position, the stop placement alone can force a smaller size. The adjustment appears in `sig["size_adjustments"]["shrunk_by_max_loss_guard"] == True`.

### Liquidity thresholds (NEW in v0.2.0)

From `load.classify_liquidity(daily, market)`. Uses 20-day average daily $ value.

| Market | thin       | moderate         | liquid      |
|--------|------------|------------------|-------------|
| US/HK  | < $1M      | $1M – $50M       | > $50M      |
| A股    | < ¥10M     | ¥10M – ¥500M     | > ¥500M     |

**Note for A-share**: akshare's `volume` field from `eastmoney_direct` is in 手 (100 shares). `classify_liquidity` automatically multiplies by 100 when market=A.

---

## Weekly synthesis (`load.resample_daily_to_weekly`) — NEW v0.2.0

When the exchange weekly OHLCV endpoint is unavailable (common for akshare A-share), `analyze.py` calls `resample_daily_to_weekly(daily, anchor='W-FRI')`:

```
open   = first trading day's open of the week
high   = max high of the week
low    = min low of the week
close  = last trading day's close of the week
volume = sum of the week's volume
```

The synthesized weekly frame is functionally equivalent to a real weekly feed for swing/trend analysis. The state flags `data_summary.weekly_source = "synthesized_from_daily"` and adds a warning, surfaced in the data-gap appendix.

---

## Earnings flags

Three signals that trigger report-level warnings:

- **`earnings_within_14d:<N>`**: when `--earnings-date` is provided and `days_to_earnings ∈ [0, 14]`. Manifest: ⚠️ banner at top of report, per-signal note.
- **`earnings_within_14d:<N>` with N ≤ 3**: ADDITIONALLY forces every signal to `🚫 禁追`.
- **`ashare_earnings_season:<month>`**: when `--market A` and current month ∈ {4, 8, 10}. Manifest: ⚠️ banner at top of report (if no earnings-date provided), per-long-signal note advising to check disclosure calendar.
