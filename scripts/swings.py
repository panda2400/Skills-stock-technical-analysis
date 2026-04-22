"""
Swing detection + HH / HL / LH / LL labeling.

Algorithm:
  - fractal high: bar whose high is strictly greater than the N bars on both sides
  - fractal low:  bar whose low  is strictly less    than the N bars on both sides
  - daily:  N=2 (a 5-bar pattern; the middle bar is the pivot)
  - weekly: N=1 (a 3-bar pattern)

After pivots are found, we label them in sequence:
  - HH: higher high than the previous high
  - LH: lower  high than the previous high
  - HL: higher low  than the previous low
  - LL: lower  low  than the previous low

Pattern classification based on the last 4 pivots:
  - uptrend_confirmed    — last 2 highs = HH and last 2 lows = HL
  - uptrend_tentative    — up move started, only one HH or HL so far
  - downtrend_confirmed  — last 2 highs = LH and last 2 lows = LL
  - downtrend_tentative  — down move started
  - range                — mixed / oscillating
  - unclear              — not enough pivots yet
"""

from __future__ import annotations

import pandas as pd
from dataclasses import dataclass, asdict
from typing import Literal


PivotType = Literal["high", "low"]
SwingLabel = Literal["HH", "HL", "LH", "LL", "first_high", "first_low"]


@dataclass
class Pivot:
    idx: int
    date: str
    price: float
    kind: PivotType
    label: SwingLabel | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def find_fractals(df: pd.DataFrame, n: int = 2) -> list[Pivot]:
    """Return all fractal pivots in chronological order."""
    pivots: list[Pivot] = []
    if len(df) < 2 * n + 1:
        return pivots

    highs = df["high"].values
    lows = df["low"].values
    dates = df.index

    for i in range(n, len(df) - n):
        left_highs = highs[i - n : i]
        right_highs = highs[i + 1 : i + 1 + n]
        if highs[i] > left_highs.max() and highs[i] > right_highs.max():
            pivots.append(Pivot(
                idx=i,
                date=dates[i].strftime("%Y-%m-%d"),
                price=float(highs[i]),
                kind="high",
            ))

        left_lows = lows[i - n : i]
        right_lows = lows[i + 1 : i + 1 + n]
        if lows[i] < left_lows.min() and lows[i] < right_lows.min():
            pivots.append(Pivot(
                idx=i,
                date=dates[i].strftime("%Y-%m-%d"),
                price=float(lows[i]),
                kind="low",
            ))

    # Sort by bar index (can get two pivots at same bar if day is huge both ways,
    # but fractal definition prevents that in practice; sort anyway for safety)
    pivots.sort(key=lambda p: p.idx)
    return pivots


def label_pivots(pivots: list[Pivot]) -> list[Pivot]:
    """Assign HH/HL/LH/LL labels by comparing each pivot to the previous of its kind."""
    last_high: Pivot | None = None
    last_low: Pivot | None = None
    for p in pivots:
        if p.kind == "high":
            if last_high is None:
                p.label = "first_high"
            elif p.price > last_high.price:
                p.label = "HH"
            else:
                p.label = "LH"
            last_high = p
        else:
            if last_low is None:
                p.label = "first_low"
            elif p.price > last_low.price:
                p.label = "HL"
            else:
                p.label = "LL"
            last_low = p
    return pivots


def classify_pattern(pivots: list[Pivot]) -> str:
    """Use the last few pivots to classify the trend pattern."""
    highs = [p for p in pivots if p.kind == "high" and p.label in ("HH", "LH")]
    lows = [p for p in pivots if p.kind == "low" and p.label in ("HL", "LL")]

    if len(highs) == 0 or len(lows) == 0:
        return "unclear"

    last_high_label = highs[-1].label
    last_low_label = lows[-1].label

    if last_high_label == "HH" and last_low_label == "HL":
        return "uptrend_confirmed"
    if last_high_label == "LH" and last_low_label == "LL":
        return "downtrend_confirmed"
    if last_high_label == "HH" and last_low_label == "LL":
        # Making higher highs but also lower lows — volatile expansion; treat as unclear
        return "range"
    if last_high_label == "LH" and last_low_label == "HL":
        # Lower highs + higher lows = contracting triangle / coil
        return "range"
    # Fall-through: tentative if only one side agrees
    if last_high_label == "HH" or last_low_label == "HL":
        return "uptrend_tentative"
    if last_high_label == "LH" or last_low_label == "LL":
        return "downtrend_tentative"
    return "unclear"


def analyze_swings(daily: pd.DataFrame, weekly: pd.DataFrame | None = None) -> dict:
    """Main entry point. Returns a dict ready for JSON serialization."""
    daily_pivots = label_pivots(find_fractals(daily, n=2))
    daily_pattern = classify_pattern(daily_pivots)

    daily_result = {
        "pivots": [p.to_dict() for p in daily_pivots[-12:]],  # keep last 12 for brevity
        "pivots_all": [p.to_dict() for p in daily_pivots],    # full list for patterns.py
        "pivot_count_total": len(daily_pivots),
        "pattern": daily_pattern,
        "last_pivot": daily_pivots[-1].to_dict() if daily_pivots else None,
        "last_pivot_days_ago": (
            len(daily) - 1 - daily_pivots[-1].idx if daily_pivots else None
        ),
        "confidence": _confidence(daily_pivots, daily_pattern),
    }

    # Key reference levels relative to current close
    last_close = float(daily["close"].iloc[-1])
    highs = [p.price for p in daily_pivots if p.kind == "high"]
    lows = [p.price for p in daily_pivots if p.kind == "low"]

    # Most recent swing high (for breakout trigger / upside target)
    # Prefer: highest swing high still above current price (next resistance overhead)
    # Fallback: most recent swing high of any kind (already broken)
    highs_overhead = [h for h in highs if h > last_close]
    daily_result["nearest_swing_high"] = (
        min(highs_overhead) if highs_overhead else (highs[-1] if highs else None)
    )
    daily_result["highest_recent_high"] = max(highs[-6:]) if len(highs) >= 1 else None

    # Most recent swing low *below* current price (for stop-loss placement)
    lows_below = [p for p in daily_pivots if p.kind == "low" and p.price < last_close]
    if lows_below:
        # Most recent one chronologically
        lows_below.sort(key=lambda p: p.idx)
        daily_result["nearest_swing_low"] = lows_below[-1].price
    else:
        daily_result["nearest_swing_low"] = min(lows) if lows else None

    weekly_result = None
    if weekly is not None and len(weekly) >= 6:
        weekly_pivots = label_pivots(find_fractals(weekly, n=1))
        weekly_pattern = classify_pattern(weekly_pivots)
        weekly_result = {
            "pivots": [p.to_dict() for p in weekly_pivots[-8:]],
            "pivots_all": [p.to_dict() for p in weekly_pivots],
            "pivot_count_total": len(weekly_pivots),
            "pattern": weekly_pattern,
            "last_pivot": weekly_pivots[-1].to_dict() if weekly_pivots else None,
            "confidence": _confidence(weekly_pivots, weekly_pattern),
        }

    alignment = _alignment(daily_result, weekly_result)

    return {
        "daily": daily_result,
        "weekly": weekly_result,
        "alignment": alignment,
    }


def _confidence(pivots: list[Pivot], pattern: str) -> str:
    if len(pivots) < 4:
        return "low"
    if pattern in ("unclear", "range"):
        return "low"
    if pattern.endswith("_tentative"):
        return "medium"
    return "high"


def _alignment(daily: dict, weekly: dict | None) -> str:
    if weekly is None:
        return "weekly_data_missing"
    d, w = daily["pattern"], weekly["pattern"]
    if d.startswith("uptrend") and w.startswith("uptrend"):
        return "both_uptrend"
    if d.startswith("downtrend") and w.startswith("downtrend"):
        return "both_downtrend"
    if "uptrend" in d and "downtrend" in w:
        return "daily_up_weekly_down"  # counter-trend bounce, risky
    if "downtrend" in d and "uptrend" in w:
        return "daily_down_weekly_up"  # weekly trend pullback, opportunity zone
    return "mixed"


if __name__ == "__main__":
    import sys
    import json
    from load import load_ohlcv

    if len(sys.argv) < 2:
        print("usage: python swings.py <daily.csv> [weekly.csv]")
        sys.exit(1)
    d = load_ohlcv(sys.argv[1])
    w = load_ohlcv(sys.argv[2]) if len(sys.argv) >= 3 else None
    print(json.dumps(analyze_swings(d, w), indent=2, ensure_ascii=False))
