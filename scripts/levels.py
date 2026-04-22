"""
Key support / resistance levels.

Strategy:
  1. Collect candidate levels from recent pivots (swings) + MA values + the
     round numbers nearby + the prior-20-day range high/low + 52w high/low.
  2. Cluster levels within 1% of each other (relative clustering).
  3. Score by:
     - how recently each underlying event happened (more recent = +score)
     - how many types of levels co-locate (pivot + MA + round number in the
       same cluster = higher importance)
     - distance from current price (closer = more relevant for near-term)
  4. Split into support (<price) and resistance (>price), return top 4 each.

Output is a sorted list the template can print directly.
"""

from __future__ import annotations

import pandas as pd
from typing import Iterable


def compute_levels(
    daily: pd.DataFrame,
    swings_result: dict,
    indicators_result: dict,
) -> dict:
    last_close = float(daily["close"].iloc[-1])

    candidates: list[dict] = []

    # 1. Pivots from swings
    for p in swings_result.get("daily", {}).get("pivots", []):
        candidates.append({
            "price": float(p["price"]),
            "basis": f"pivot ({p['label']} {p['date']})",
            "score_base": 2.0,
            "days_ago": None,  # unknown; days_ago = (len(daily)-1 - p['idx']) if needed
        })

    # 2. Moving averages
    for name in ("ma10", "ma20", "ma50"):
        v = indicators_result.get(name)
        if v is not None:
            candidates.append({
                "price": float(v),
                "basis": name.upper(),
                "score_base": 1.5,
            })

    # 3. 20-day range extremes
    window = daily.tail(20)
    if len(window) >= 10:
        candidates.append({
            "price": float(window["high"].max()),
            "basis": "20d high",
            "score_base": 2.5,
        })
        candidates.append({
            "price": float(window["low"].min()),
            "basis": "20d low",
            "score_base": 2.5,
        })

    # 4. 52-week extremes (use what we have)
    yr = daily.tail(252)
    if len(yr) >= 100:
        candidates.append({
            "price": float(yr["high"].max()),
            "basis": f"{len(yr)}-bar high",
            "score_base": 3.0,
        })
        candidates.append({
            "price": float(yr["low"].min()),
            "basis": f"{len(yr)}-bar low",
            "score_base": 3.0,
        })

    # 5. Round numbers close to price (±10%)
    for rnd in _round_numbers_near(last_close):
        candidates.append({
            "price": float(rnd),
            "basis": "round number",
            "score_base": 0.5,
        })

    # Cluster within 1%
    clusters = _cluster_levels(candidates, tolerance_pct=0.01)

    # Build final records
    levels = []
    for cluster in clusters:
        avg_price = sum(c["price"] for c in cluster) / len(cluster)
        bases = list({c["basis"] for c in cluster})
        base_score = sum(c["score_base"] for c in cluster)
        # Co-location bonus
        confluence_bonus = (len(set(b.split()[0] for b in bases)) - 1) * 1.0
        # Proximity bonus: within 5% of current price gets a boost
        dist_pct = abs(avg_price - last_close) / last_close
        proximity_bonus = max(0, (0.05 - dist_pct) * 20) if dist_pct < 0.05 else 0
        importance = round(base_score + confluence_bonus + proximity_bonus, 2)

        levels.append({
            "price": round(avg_price, 3),
            "type": "resistance" if avg_price > last_close else "support",
            "basis": " + ".join(bases),
            "importance": importance,
            "distance_pct": round(dist_pct * 100, 2),
        })

    # Split & sort
    resistance = sorted(
        [l for l in levels if l["type"] == "resistance"],
        key=lambda x: (x["price"])
    )[:4]
    support = sorted(
        [l for l in levels if l["type"] == "support"],
        key=lambda x: (-x["price"])
    )[:4]

    return {
        "last_close": last_close,
        "resistance": resistance,
        "support": support,
    }


def _cluster_levels(candidates: list[dict], tolerance_pct: float = 0.01) -> list[list[dict]]:
    if not candidates:
        return []
    sorted_c = sorted(candidates, key=lambda x: x["price"])
    clusters: list[list[dict]] = [[sorted_c[0]]]
    for c in sorted_c[1:]:
        last_cluster = clusters[-1]
        cluster_avg = sum(x["price"] for x in last_cluster) / len(last_cluster)
        if abs(c["price"] - cluster_avg) / cluster_avg < tolerance_pct:
            last_cluster.append(c)
        else:
            clusters.append([c])
    return clusters


def _round_numbers_near(px: float) -> Iterable[float]:
    """Nearby round numbers: multiples of 5, 10, 50, 100 within ±10% of price."""
    lo = px * 0.9
    hi = px * 1.1
    found = set()
    for step in (1, 5, 10, 50, 100):
        if px < step * 2:
            continue
        start = int(lo // step) * step
        for v in range(start, int(hi) + step, step):
            if lo <= v <= hi and v != int(px):
                found.add(v)
    return found


if __name__ == "__main__":
    import sys, json
    from load import load_ohlcv
    from swings import analyze_swings
    from indicators import compute_indicators
    if len(sys.argv) != 2:
        print("usage: python levels.py <daily.csv>")
        sys.exit(1)
    d = load_ohlcv(sys.argv[1])
    s = analyze_swings(d, None)
    i = compute_indicators(d)
    print(json.dumps(compute_levels(d, s, i), indent=2, ensure_ascii=False))
