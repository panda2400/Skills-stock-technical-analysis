"""
Pattern detectors — things that aren't captured by plain HH/HL/LH/LL labels.

Three pattern families:

1. Double-top / double-bottom (cluster of recent extremes)
   - 2+ swing highs within `tolerance_pct` of each other, AND
   - neither held above the cluster level on a close basis, AND
   - both within the last `lookback_bars` bars.
   - Outputs cluster price, how many touches, and the "neckline" (the HL between them).

2. Intraday false breakout
   - Current bar's high exceeded the prior `nearest_swing_high` (reference level), BUT
   - close is below that level.
   - Signals that a chase entry at current price is probably premature.

3. Range-bound cluster (informational)
   - Recent 20 bars' high and low are within `range_tolerance_pct` of each other.

The detectors are meant to be *advisory* — signals.py can use them to
downgrade tier (double-top vs. a pending breakout signal), or add notes
("今日盘中触及 $X 但未能收在上方").

This module is intentionally standalone so it can be tested in isolation.
"""

from __future__ import annotations

import pandas as pd
from typing import Optional


def detect_recent_highs_cluster(
    daily: pd.DataFrame,
    daily_pivots: list[dict],
    tolerance_pct: float = 0.02,
    lookback_bars: int = 90,
    min_touches: int = 2,
) -> Optional[dict]:
    """Find clusters of recent swing highs at similar prices (double/triple top).

    Returns None if no cluster found, otherwise a dict:
        {
            'kind': 'double_top' | 'triple_top' | ...,
            'cluster_price': float,   # average of the cluster
            'touch_count': int,
            'touches': [{'date':..., 'price':...}, ...],
            'most_recent_touch_bars_ago': int,
            'neckline': float | None,   # lowest HL between the touches
            'confirmed': bool,          # True if price has broken the neckline
        }
    """
    if not daily_pivots or len(daily) < 10:
        return None

    # Only consider pivots within lookback
    last_idx = len(daily) - 1
    recent_highs = [
        p for p in daily_pivots
        if p.get("kind") == "high"
        and (last_idx - p.get("idx", last_idx)) <= lookback_bars
    ]
    if len(recent_highs) < min_touches:
        return None

    # Find the biggest cluster (highs within tolerance of each other)
    # Greedy: sort by price descending, look for clusters
    sorted_highs = sorted(recent_highs, key=lambda p: -p["price"])
    best_cluster = None
    for i, anchor in enumerate(sorted_highs):
        cluster = [anchor]
        for other in sorted_highs[i + 1:]:
            if abs(other["price"] - anchor["price"]) / anchor["price"] <= tolerance_pct:
                cluster.append(other)
        if len(cluster) >= min_touches:
            if best_cluster is None or cluster[0]["price"] > best_cluster[0]["price"]:
                best_cluster = cluster

    if best_cluster is None:
        return None

    cluster_price = sum(p["price"] for p in best_cluster) / len(best_cluster)
    touches = [{"date": p["date"], "price": p["price"], "idx": p.get("idx")} for p in best_cluster]
    touches_sorted = sorted(touches, key=lambda t: t.get("idx") or 0)

    # Check: has close ever been *above* cluster_price after the cluster formed?
    # If yes, this isn't really a resistance cluster — it was broken.
    earliest_idx = touches_sorted[0].get("idx", 0) if touches_sorted[0].get("idx") is not None else 0
    after = daily.iloc[earliest_idx:]
    has_closed_above = bool((after["close"] > cluster_price * 1.002).any())
    if has_closed_above:
        return None  # resistance was broken cleanly, not a cluster anymore

    # Neckline: lowest low between the earliest and latest touch
    latest_idx = touches_sorted[-1].get("idx", last_idx)
    if latest_idx > earliest_idx + 1:
        between = daily.iloc[earliest_idx:latest_idx + 1]
        neckline = float(between["low"].min())
    else:
        neckline = None

    last_close = float(daily["close"].iloc[-1])
    confirmed = bool(neckline is not None and last_close < neckline * 0.995)

    kind_map = {2: "double_top", 3: "triple_top"}
    kind = kind_map.get(len(best_cluster), f"{len(best_cluster)}x_top")

    return {
        "kind": kind,
        "cluster_price": round(cluster_price, 3),
        "touch_count": len(best_cluster),
        "touches": touches_sorted,
        "most_recent_touch_bars_ago": last_idx - (touches_sorted[-1].get("idx") or last_idx),
        "neckline": round(neckline, 3) if neckline else None,
        "confirmed": confirmed,
    }


def detect_recent_lows_cluster(
    daily: pd.DataFrame,
    daily_pivots: list[dict],
    tolerance_pct: float = 0.02,
    lookback_bars: int = 90,
    min_touches: int = 2,
) -> Optional[dict]:
    """Mirror of `detect_recent_highs_cluster` for bottoms."""
    if not daily_pivots or len(daily) < 10:
        return None

    last_idx = len(daily) - 1
    recent_lows = [
        p for p in daily_pivots
        if p.get("kind") == "low"
        and (last_idx - p.get("idx", last_idx)) <= lookback_bars
    ]
    if len(recent_lows) < min_touches:
        return None

    sorted_lows = sorted(recent_lows, key=lambda p: p["price"])
    best_cluster = None
    for i, anchor in enumerate(sorted_lows):
        cluster = [anchor]
        for other in sorted_lows[i + 1:]:
            if abs(other["price"] - anchor["price"]) / anchor["price"] <= tolerance_pct:
                cluster.append(other)
        if len(cluster) >= min_touches:
            if best_cluster is None or cluster[0]["price"] < best_cluster[0]["price"]:
                best_cluster = cluster

    if best_cluster is None:
        return None

    cluster_price = sum(p["price"] for p in best_cluster) / len(best_cluster)
    touches = [{"date": p["date"], "price": p["price"], "idx": p.get("idx")} for p in best_cluster]
    touches_sorted = sorted(touches, key=lambda t: t.get("idx") or 0)

    earliest_idx = touches_sorted[0].get("idx", 0) if touches_sorted[0].get("idx") is not None else 0
    after = daily.iloc[earliest_idx:]
    has_closed_below = bool((after["close"] < cluster_price * 0.998).any())
    if has_closed_below:
        return None

    latest_idx = touches_sorted[-1].get("idx", last_idx)
    if latest_idx > earliest_idx + 1:
        between = daily.iloc[earliest_idx:latest_idx + 1]
        neckline = float(between["high"].max())
    else:
        neckline = None

    last_close = float(daily["close"].iloc[-1])
    confirmed = bool(neckline is not None and last_close > neckline * 1.005)

    kind_map = {2: "double_bottom", 3: "triple_bottom"}
    kind = kind_map.get(len(best_cluster), f"{len(best_cluster)}x_bottom")

    return {
        "kind": kind,
        "cluster_price": round(cluster_price, 3),
        "touch_count": len(best_cluster),
        "touches": touches_sorted,
        "most_recent_touch_bars_ago": last_idx - (touches_sorted[-1].get("idx") or last_idx),
        "neckline": round(neckline, 3) if neckline else None,
        "confirmed": confirmed,
    }


def detect_intraday_false_breakout(
    daily: pd.DataFrame,
    reference_high: Optional[float],
    reference_low: Optional[float] = None,
    min_overshoot_pct: float = 0.001,
) -> dict:
    """Did today's (most recent) bar touch a reference level intraday but fail to close beyond it?

    Useful flag for `signals.py` so it doesn't over-trigger on fresh-looking breakouts
    that actually closed back inside the prior range.

    Returns dict with three keys:
      - 'upside_false_break': True if today's high > reference_high but close < reference_high
      - 'downside_false_break': True if today's low < reference_low but close > reference_low
      - 'notes': human-readable summary (empty string if nothing remarkable)
    """
    if len(daily) < 1:
        return {"upside_false_break": False, "downside_false_break": False, "notes": ""}

    last = daily.iloc[-1]
    today_high = float(last["high"])
    today_low = float(last["low"])
    today_close = float(last["close"])

    upside_fb = (
        reference_high is not None
        and today_high > reference_high * (1 + min_overshoot_pct)
        and today_close < reference_high
    )
    downside_fb = (
        reference_low is not None
        and today_low < reference_low * (1 - min_overshoot_pct)
        and today_close > reference_low
    )

    notes = []
    if upside_fb:
        overshoot = (today_high / reference_high - 1) * 100
        pullback = (today_high - today_close) / today_high * 100
        notes.append(
            f"盘中触及 ${today_high:.2f}（刺穿 ${reference_high:.2f} +{overshoot:.2f}%），"
            f"但收盘 ${today_close:.2f} 回落 {pullback:.2f}% → 上方假突破"
        )
    if downside_fb:
        overshoot = (1 - today_low / reference_low) * 100
        pullback = (today_close - today_low) / today_close * 100
        notes.append(
            f"盘中下破 ${today_low:.2f}（刺穿 ${reference_low:.2f} -{overshoot:.2f}%），"
            f"但收盘 ${today_close:.2f} 反弹 {pullback:.2f}% → 下方假破"
        )

    return {
        "upside_false_break": upside_fb,
        "downside_false_break": downside_fb,
        "notes": "；".join(notes),
    }


def detect_compression(
    daily: pd.DataFrame,
    lookback_bars: int = 20,
    range_tolerance_pct: float = 0.07,
) -> Optional[dict]:
    """Is the stock in a tight range (potential pre-breakout coil)?

    Condition: max(high) / min(low) over `lookback_bars` is <= (1 + range_tolerance_pct).
    Useful to know because both double-top analysis and breakout-entry signals
    behave differently inside a coil.
    """
    if len(daily) < lookback_bars:
        return None
    window = daily.tail(lookback_bars)
    hi = float(window["high"].max())
    lo = float(window["low"].min())
    span_pct = (hi / lo - 1)
    if span_pct > range_tolerance_pct:
        return None
    return {
        "lookback_bars": lookback_bars,
        "range_high": round(hi, 3),
        "range_low": round(lo, 3),
        "span_pct": round(span_pct * 100, 2),
    }


def analyze_patterns(
    daily: pd.DataFrame,
    swings_result: dict,
) -> dict:
    """Top-level entry point. Bundles all pattern detectors into one call.

    Expects swings_result from swings.analyze_swings — uses the daily pivots list
    to find clusters.

    Returns:
        {
          'double_top': {...} | None,
          'double_bottom': {...} | None,
          'intraday_breakout': {...},
          'compression': {...} | None,
          'notes': [str, ...]   # concise human-readable summary lines
        }
    """
    daily_pivots_full = (
        swings_result.get("daily", {}).get("pivots_all")
        or swings_result.get("daily", {}).get("pivots", [])
    )

    double_top = detect_recent_highs_cluster(daily, daily_pivots_full)
    double_bottom = detect_recent_lows_cluster(daily, daily_pivots_full)

    reference_high = swings_result.get("daily", {}).get("nearest_swing_high")
    # For false-break analysis, use the *highest recent* rather than nearest —
    # if price has broken nearest but the chart still shows a prior higher high,
    # the false-break is more meaningful against the higher one.
    recent_high = swings_result.get("daily", {}).get("highest_recent_high")
    check_high = recent_high if recent_high else reference_high

    reference_low = swings_result.get("daily", {}).get("nearest_swing_low")
    fb = detect_intraday_false_breakout(daily, check_high, reference_low)

    compression = detect_compression(daily)

    notes: list[str] = []
    if double_top:
        if double_top["confirmed"]:
            notes.append(
                f"⚠️ **{double_top['kind']} 已确认**：${double_top['cluster_price']:.2f} "
                f"触顶 {double_top['touch_count']} 次，现已跌破颈线 ${double_top['neckline']:.2f}"
            )
        else:
            notes.append(
                f"⚠️ **{double_top['kind']} 雏形**：${double_top['cluster_price']:.2f} "
                f"触顶 {double_top['touch_count']} 次，颈线 "
                f"${double_top['neckline']:.2f if double_top['neckline'] else '—'}"
            )
    if double_bottom:
        if double_bottom["confirmed"]:
            notes.append(
                f"🟢 **{double_bottom['kind']} 已确认**：${double_bottom['cluster_price']:.2f} "
                f"触底 {double_bottom['touch_count']} 次，现已突破颈线 ${double_bottom['neckline']:.2f}"
            )
        else:
            notes.append(
                f"**{double_bottom['kind']} 雏形**：${double_bottom['cluster_price']:.2f} "
                f"触底 {double_bottom['touch_count']} 次"
            )
    if fb["notes"]:
        notes.append(fb["notes"])
    if compression:
        notes.append(
            f"区间压缩：{compression['lookback_bars']} 日幅度仅 "
            f"{compression['span_pct']}%（${compression['range_low']} - ${compression['range_high']}）"
        )

    return {
        "double_top": double_top,
        "double_bottom": double_bottom,
        "intraday_breakout": fb,
        "compression": compression,
        "notes": notes,
    }


if __name__ == "__main__":
    import sys
    import json
    from load import load_ohlcv
    from swings import analyze_swings

    if len(sys.argv) != 2:
        print("usage: python patterns.py <daily.csv>")
        sys.exit(1)
    d = load_ohlcv(sys.argv[1])
    s = analyze_swings(d, None)
    print(json.dumps(analyze_patterns(d, s), indent=2, ensure_ascii=False))
