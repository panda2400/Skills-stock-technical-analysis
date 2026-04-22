"""
CSV loader for OHLCV data.

Normalizes any reasonable date/OHLCV CSV into a canonical DataFrame with:
  index: DatetimeIndex (sorted ascending)
  columns: open, high, low, close, volume (all float64)

Handles common variations:
  - 'Date' / 'date' / '日期' as the date column
  - 'Open/High/Low/Close/Volume' in various casings
  - Volume as scientific notation or with 'e' suffix
  - Non-trading-day gaps (preserved; we don't forward-fill)

v0.2.0 additions:
  - `resample_daily_to_weekly()` — fallback when weekly data source is unavailable
  - `classify_liquidity()` — ADV (average daily value) based liquidity tier
"""

from __future__ import annotations

import pandas as pd
from pathlib import Path

CANONICAL_COLS = ["open", "high", "low", "close", "volume"]

COLUMN_ALIASES = {
    "date": "date",
    "Date": "date",
    "时间": "date",
    "日期": "date",
    "trade_date": "date",
    "timestamp": "date",
    "open": "open",
    "Open": "open",
    "开盘": "open",
    "开盘价": "open",
    "high": "high",
    "High": "high",
    "最高": "high",
    "最高价": "high",
    "low": "low",
    "Low": "low",
    "最低": "low",
    "最低价": "low",
    "close": "close",
    "Close": "close",
    "收盘": "close",
    "收盘价": "close",
    "adj_close": "close",  # prefer adjusted close if raw close missing
    "Adj Close": "close",
    "volume": "volume",
    "Volume": "volume",
    "成交量": "volume",
    "vol": "volume",
}


def load_ohlcv(path: str | Path) -> pd.DataFrame:
    """Read a CSV file and return canonical OHLCV DataFrame."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"OHLCV file not found: {path}")

    df = pd.read_csv(path)
    df = df.rename(columns={c: COLUMN_ALIASES.get(c, c.lower()) for c in df.columns})

    # Keep only the canonical columns + date
    needed = ["date"] + CANONICAL_COLS
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(
            f"{path.name}: missing required columns {missing}. "
            f"Found: {list(df.columns)}"
        )
    df = df[needed].copy()

    # Parse dates, coerce to tz-naive
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.tz_localize(None)
    bad = df["date"].isna().sum()
    if bad > 0:
        df = df.dropna(subset=["date"])

    # Coerce numerics
    for col in CANONICAL_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=CANONICAL_COLS)
    df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    df = df.set_index("date")

    if len(df) == 0:
        raise ValueError(f"{path.name}: no valid rows after cleaning")

    return df


def resample_daily_to_weekly(daily: pd.DataFrame, week_anchor: str = "W-FRI") -> pd.DataFrame:
    """Synthesize a weekly OHLCV frame from a daily one.

    Use this as a fallback when the exchange weekly endpoint is down
    (common for A-share eastmoney). Anchor defaults to Friday.

    Semantics:
        open   = first trading day's open of the week
        high   = max high of the week
        low    = min low  of the week
        close  = last trading day's close of the week
        volume = sum of the week's volume

    Weeks containing only NaN (no trading days) are dropped.
    """
    if daily is None or len(daily) == 0:
        return daily
    w = pd.DataFrame({
        "open": daily["open"].resample(week_anchor).first(),
        "high": daily["high"].resample(week_anchor).max(),
        "low": daily["low"].resample(week_anchor).min(),
        "close": daily["close"].resample(week_anchor).last(),
        "volume": daily["volume"].resample(week_anchor).sum(),
    }).dropna()
    return w


def classify_liquidity(
    daily: pd.DataFrame,
    market: str = "US",
    lookback: int = 20,
) -> dict:
    """Compute ADV (average daily dollar value) and bucket into a liquidity class.

    Thresholds (currency native to the market):
        US / HK (USD / HKD):
            thin     < $1M
            moderate $1M-$50M
            liquid   > $50M
        A (CNY):
            thin     < ¥10M  (约 1.4M USD)
            moderate ¥10M-¥500M
            liquid   > ¥500M

    Returns:
        {
            'adv_lookback': int,
            'adv': float,
            'currency_is_cny': bool,
            'class': 'thin' | 'moderate' | 'liquid',
            'read': human-readable summary,
        }
    """
    if len(daily) < 5:
        return {
            "adv_lookback": 0,
            "adv": None,
            "currency_is_cny": market == "A",
            "class": "unknown",
            "read": "流动性数据不足",
        }
    n = min(lookback, len(daily))
    window = daily.tail(n)
    # ADV = mean(close × volume)
    dollar_value = (window["close"] * window["volume"]).mean()
    # A-share volume from akshare 'eastmoney_direct' is in 手 (100 shares).
    # Multiply by 100 to get shares. Signal here: if market=A, assume 手 units.
    if market == "A":
        dollar_value = dollar_value * 100  # shares per 手

    if market == "A":
        thin_threshold = 10_000_000      # ¥10M
        liquid_threshold = 500_000_000   # ¥500M
    else:
        thin_threshold = 1_000_000       # $1M
        liquid_threshold = 50_000_000    # $50M

    if dollar_value < thin_threshold:
        cls = "thin"
    elif dollar_value < liquid_threshold:
        cls = "moderate"
    else:
        cls = "liquid"

    if market == "A":
        read = f"近 {n} 日日均成交额约 ¥{dollar_value/1e8:.2f} 亿 → {cls}"
    else:
        read = f"avg {n}-day dollar volume ${dollar_value/1e6:.1f}M → {cls}"

    return {
        "adv_lookback": n,
        "adv": round(float(dollar_value), 2),
        "currency_is_cny": market == "A",
        "class": cls,
        "read": read,
    }


def summarize(df: pd.DataFrame) -> dict:
    """Quick summary for debugging / state header."""
    return {
        "bars": len(df),
        "first_date": df.index[0].strftime("%Y-%m-%d"),
        "last_date": df.index[-1].strftime("%Y-%m-%d"),
        "last_close": float(df["close"].iloc[-1]),
        "last_volume": float(df["volume"].iloc[-1]),
    }


if __name__ == "__main__":
    import sys
    import json
    if len(sys.argv) < 2:
        print("usage: python load.py <path-to-csv> [market]")
        sys.exit(1)
    d = load_ohlcv(sys.argv[1])
    market = sys.argv[2] if len(sys.argv) >= 3 else "US"
    print(json.dumps({
        "summary": summarize(d),
        "liquidity": classify_liquidity(d, market=market),
    }, indent=2, ensure_ascii=False))
