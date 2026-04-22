"""
Technical indicators — supporting role only.

Returns an IndicatorSnapshot (latest values) plus a one-line read per indicator.
Kept intentionally simple:
  MA10 / MA20 / MA50
  RSI(14)
  MACD(12, 26, 9)
  ATR(14)

Each indicator also gets a 'read' field: a string like 'price above MA50',
so the report can cite it without the LLM redoing the check.
"""

from __future__ import annotations

import pandas as pd
import numpy as np


def compute_indicators(daily: pd.DataFrame) -> dict:
    if len(daily) < 50:
        return {"error": "need at least 50 daily bars"}

    close = daily["close"]
    high = daily["high"]
    low = daily["low"]

    # Moving averages
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()

    # RSI 14
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)

    # MACD(12,26,9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - signal_line

    # ATR 14
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()

    last_close = float(close.iloc[-1])
    last_ma10 = float(ma10.iloc[-1])
    last_ma20 = float(ma20.iloc[-1])
    last_ma50 = float(ma50.iloc[-1])
    last_rsi = float(rsi.iloc[-1])
    last_macd = float(macd_line.iloc[-1])
    last_signal = float(signal_line.iloc[-1])
    last_hist = float(macd_hist.iloc[-1])
    last_atr = float(atr.iloc[-1])

    # Reads
    ma_read = _ma_read(last_close, last_ma10, last_ma20, last_ma50)
    rsi_read = _rsi_read(last_rsi)
    macd_read = _macd_read(last_macd, last_signal, last_hist, macd_hist)

    return {
        "price": round(last_close, 3),
        "ma10": round(last_ma10, 3),
        "ma20": round(last_ma20, 3),
        "ma50": round(last_ma50, 3),
        "ma_alignment": ma_read["alignment"],
        "ma_read": ma_read["read"],
        "rsi14": round(last_rsi, 1),
        "rsi_read": rsi_read,
        "macd": round(last_macd, 3),
        "macd_signal": round(last_signal, 3),
        "macd_hist": round(last_hist, 3),
        "macd_read": macd_read,
        "atr14": round(last_atr, 3),
        "atr_pct": round(last_atr / last_close * 100, 2) if last_close else None,
    }


def _ma_read(px, m10, m20, m50) -> dict:
    above = [name for name, v in [("MA10", m10), ("MA20", m20), ("MA50", m50)] if px > v]
    alignment = None
    if m10 > m20 > m50:
        alignment = "bullish_stack"
    elif m10 < m20 < m50:
        alignment = "bearish_stack"
    else:
        alignment = "mixed"
    if len(above) == 3:
        read = f"price above all MAs ({alignment})"
    elif len(above) == 0:
        read = f"price below all MAs ({alignment})"
    else:
        read = f"price above {', '.join(above)} ({alignment})"
    return {"alignment": alignment, "read": read}


def _rsi_read(rsi: float) -> str:
    if rsi >= 70:
        return f"overbought at {rsi:.1f}"
    if rsi <= 30:
        return f"oversold at {rsi:.1f}"
    if rsi > 55:
        return f"bullish momentum ({rsi:.1f})"
    if rsi < 45:
        return f"bearish momentum ({rsi:.1f})"
    return f"neutral ({rsi:.1f})"


def _macd_read(macd, signal, hist, hist_series) -> str:
    direction = "bullish" if macd > signal else "bearish"
    # Crossover in last 5 bars?
    crossed = False
    if len(hist_series) >= 6:
        prev = hist_series.iloc[-6:-1]
        if (prev.iloc[0] * hist > 0) is False:  # sign changed somewhere in window
            crossed = True
    state = "recent crossover" if crossed else "trend continues"
    return f"{direction} ({state}, hist={hist:+.3f})"


if __name__ == "__main__":
    import sys, json
    from load import load_ohlcv
    if len(sys.argv) != 2:
        print("usage: python indicators.py <daily.csv>")
        sys.exit(1)
    print(json.dumps(compute_indicators(load_ohlcv(sys.argv[1])), indent=2))
