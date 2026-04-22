"""
Volume structure analysis.

Produces:
  - 14-day table: date | close | pct_change | volume | volume_ratio_vs_20d
  - regime classification using last-14-day behavior (priority order):
      爆量          single-day ratio >= 2.5 (either direction)
      出货放量       falling price + rising volume (avg_ratio >= 1.3, pct_change < -3%)
      吸筹放量       rising price + rising volume
      缩量回调       small drop + low volume for 3+ of last 5 days (bullish continuation)
                   [NEW in v0.2.0 — healthy consolidation inside an uptrend]
      干枯          ratios mostly < 0.7
      平淡          everything else
  - price-volume divergence flag (price up / volume down or vice versa)
"""

from __future__ import annotations

import pandas as pd


def analyze_volume(daily: pd.DataFrame, lookback: int = 14, ma_window: int = 20) -> dict:
    if len(daily) < ma_window + 1:
        return {
            "regime": "data_insufficient",
            "avg_ratio_14d": None,
            "max_ratio_14d": None,
            "trend": None,
            "divergence": None,
            "pullback_signature": None,
            "table": [],
        }

    vol_ma = daily["volume"].rolling(ma_window).mean()
    ratio = (daily["volume"] / vol_ma).round(2)
    pct = (daily["close"].pct_change() * 100).round(2)

    tail = daily.tail(lookback).copy()
    tail["pct_change"] = pct.loc[tail.index]
    tail["volume_ratio"] = ratio.loc[tail.index]

    table = [
        {
            "date": idx.strftime("%Y-%m-%d"),
            "close": round(float(row["close"]), 3),
            "pct_change": None if pd.isna(row["pct_change"]) else float(row["pct_change"]),
            "volume": int(row["volume"]),
            "volume_ratio": None if pd.isna(row["volume_ratio"]) else float(row["volume_ratio"]),
        }
        for idx, row in tail.iterrows()
    ]

    ratios = [r["volume_ratio"] for r in table if r["volume_ratio"] is not None]
    pcts = [r["pct_change"] for r in table if r["pct_change"] is not None]

    avg_ratio = round(sum(ratios) / len(ratios), 2) if ratios else None
    max_ratio = round(max(ratios), 2) if ratios else None
    pct_sum = sum(pcts) if pcts else 0

    up_days_with_vol = sum(1 for r in table
                           if r["pct_change"] and r["pct_change"] > 0
                           and r["volume_ratio"] and r["volume_ratio"] > 1.0)
    down_days_with_vol = sum(1 for r in table
                             if r["pct_change"] and r["pct_change"] < 0
                             and r["volume_ratio"] and r["volume_ratio"] > 1.0)

    pullback_sig = _pullback_signature(table)

    regime = _classify_regime(pct_sum, avg_ratio, max_ratio,
                              up_days_with_vol, down_days_with_vol,
                              pullback_sig)
    trend = _volume_trend(ratios)
    divergence = _divergence(pcts, ratios)

    return {
        "regime": regime,
        "avg_ratio_14d": avg_ratio,
        "max_ratio_14d": max_ratio,
        "trend": trend,
        "divergence": divergence,
        "pullback_signature": pullback_sig,
        "table": table,
        "up_days_with_volume": up_days_with_vol,
        "down_days_with_volume": down_days_with_vol,
    }


def _pullback_signature(table: list[dict]) -> dict | None:
    """Detect 缩量回调 (healthy pullback in an uptrend):
    
    Rule: in the last 5 days, at least 3 had vol_ratio < 0.9 AND
    cumulative price drop is between -8% and -1% (modest, not crash).
    
    Returns:
        {'match': bool, 'low_vol_days': int, 'total_change_pct': float} or None
    """
    if len(table) < 5:
        return None
    last5 = table[-5:]
    low_vol_days = sum(
        1 for r in last5
        if r["volume_ratio"] is not None and r["volume_ratio"] < 0.9
    )
    pct_vals = [r["pct_change"] for r in last5 if r["pct_change"] is not None]
    if not pct_vals:
        return None
    total_change = sum(pct_vals)
    match = (low_vol_days >= 3 and -8.0 <= total_change <= -1.0)
    return {
        "match": match,
        "low_vol_days": low_vol_days,
        "total_change_pct": round(total_change, 2),
    }


def _classify_regime(pct_sum, avg_ratio, max_ratio, up_vol, down_vol,
                     pullback_sig) -> str:
    if max_ratio is None:
        return "data_insufficient"
    # Priority 1: single-day explosive volume (either direction)
    if max_ratio >= 2.5:
        return "爆量"
    # Priority 2: distribution (strong selling with volume)
    if pct_sum < -3 and avg_ratio is not None and avg_ratio >= 1.3 and down_vol > up_vol:
        return "出货放量"
    # Priority 3: accumulation (strong buying with volume)
    if pct_sum > 3 and avg_ratio is not None and avg_ratio >= 1.1 and up_vol > down_vol:
        return "吸筹放量"
    # Priority 4: NEW — healthy pullback (low volume retrace)
    if pullback_sig and pullback_sig.get("match"):
        return "缩量回调"
    # Priority 5: dry up (nothing trading)
    if avg_ratio is not None and avg_ratio < 0.7:
        return "干枯"
    return "平淡"


def _volume_trend(ratios: list[float]) -> str:
    if len(ratios) < 6:
        return "unknown"
    first_half = sum(ratios[: len(ratios) // 2]) / (len(ratios) // 2)
    second_half = sum(ratios[len(ratios) // 2:]) / (len(ratios) - len(ratios) // 2)
    diff = second_half - first_half
    if diff > 0.15:
        return "rising"
    if diff < -0.15:
        return "falling"
    return "flat"


def _divergence(pcts: list[float], ratios: list[float]) -> str | None:
    if len(pcts) < 10 or len(ratios) < 10:
        return None
    price_dir = "up" if sum(pcts) > 0 else "down"
    vol_dir = _volume_trend(ratios)
    if price_dir == "up" and vol_dir == "falling":
        return "price_up_volume_down"  # weakening rally
    if price_dir == "down" and vol_dir == "falling":
        return "price_down_volume_down"  # seller exhaustion (potentially bullish)
    if price_dir == "up" and vol_dir == "rising":
        return None  # healthy
    if price_dir == "down" and vol_dir == "rising":
        return "price_down_volume_up"  # distribution
    return None


if __name__ == "__main__":
    import sys
    import json
    from load import load_ohlcv
    if len(sys.argv) != 2:
        print("usage: python volume.py <daily.csv>")
        sys.exit(1)
    print(json.dumps(analyze_volume(load_ohlcv(sys.argv[1])), indent=2, ensure_ascii=False))
