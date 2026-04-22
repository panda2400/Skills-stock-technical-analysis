"""
Relative strength vs a benchmark (SPY / 000300 / HSI).

Computes 20-day and 50-day:
  - stock return
  - benchmark return
  - alpha (stock - benchmark)
  - decile rank (1-10) based on alpha magnitude vs typical historical range
  - beta-adjusted alpha (alpha minus the part explained by beta * bench_ret)

Classification:
  strong_outperformer:  both 20d and 50d deciles >= 8
  outperformer:         20d decile >= 6 OR 50d decile >= 7
  neutral:              |alpha| < 1% on both
  underperformer:       20d decile <= 4 OR 50d decile <= 3
  weak:                 both 20d and 50d deciles <= 2

v0.2.0: added a **qualitative fallback** for when the benchmark CSV is missing
or has too few overlapping bars. Instead of returning data_insufficient and
failing the signal-engine gate, it returns a best-effort self-return read
with `qualitative=True` set so downstream consumers know not to treat it
as verified RS.
"""

from __future__ import annotations

import pandas as pd
import numpy as np


def compute_rs(
    stock: pd.DataFrame,
    benchmark: pd.DataFrame | None,
) -> dict:
    if benchmark is None:
        return _qualitative_fallback(stock, reason="benchmark_missing")

    merged = pd.concat(
        {"stock": stock["close"], "bench": benchmark["close"]},
        axis=1,
    ).dropna()

    if len(merged) < 55:
        return _qualitative_fallback(stock, reason=f"insufficient_overlap ({len(merged)} bars)")

    result = {"qualitative": False}
    for window in (20, 50):
        sub = merged.tail(window + 1)
        stock_ret = float(sub["stock"].iloc[-1] / sub["stock"].iloc[0] - 1)
        bench_ret = float(sub["bench"].iloc[-1] / sub["bench"].iloc[0] - 1)
        alpha = stock_ret - bench_ret
        result[f"{window}d"] = {
            "stock_ret": round(stock_ret, 4),
            "bench_ret": round(bench_ret, 4),
            "alpha": round(alpha, 4),
            "decile": _decile_rank(alpha, window),
        }

    # Beta over the 50-day window using daily log-returns
    tail = merged.tail(51)
    stock_lr = np.log(tail["stock"] / tail["stock"].shift(1)).dropna()
    bench_lr = np.log(tail["bench"] / tail["bench"].shift(1)).dropna()
    beta = _beta(stock_lr, bench_lr)
    result["beta"] = round(beta, 3) if beta is not None else None

    # Beta-adjusted alpha on the 50d window
    if beta is not None:
        bench_ret_50 = result["50d"]["bench_ret"]
        beta_adj_alpha = result["50d"]["alpha"] - (beta - 1.0) * bench_ret_50
        result["beta_adjusted_alpha_50d"] = round(beta_adj_alpha, 4)
    else:
        result["beta_adjusted_alpha_50d"] = None

    # Classification
    result["classification"] = _classify(
        result["20d"]["decile"], result["50d"]["decile"],
        result["20d"]["alpha"], result["50d"]["alpha"]
    )

    # Warning if gains are mostly beta
    warning = None
    if (result["50d"]["alpha"] > 0 and beta is not None and beta > 1.3
            and (result.get("beta_adjusted_alpha_50d") or 0) < 0.01):
        warning = "beta_driven_rally"
    result["warning"] = warning

    return result


def _qualitative_fallback(stock: pd.DataFrame, reason: str) -> dict:
    """When we can't compute real RS, provide a best-effort self-return read.

    Signals downstream consumers via `qualitative=True` so they apply
    conservative gates (don't fire 🟢 confirmed longs, etc.).
    """
    if len(stock) < 55:
        return {
            "qualitative": True,
            "classification": "data_insufficient",
            "20d": None,
            "50d": None,
            "beta": None,
            "warning": reason,
            "notes": [
                f"基准数据不足（{reason}），无法计算相对强度，"
                f"同时本票历史也不足 55 根，信号会降级处理"
            ],
        }

    r20 = float(stock["close"].iloc[-1] / stock["close"].iloc[-21] - 1)
    r50 = float(stock["close"].iloc[-1] / stock["close"].iloc[-51] - 1)

    # Crude self-classification based on absolute returns
    # (much less reliable than alpha; only a fallback)
    if r50 > 0.30 and r20 > 0.05:
        classification = "outperformer"  # likely, but not verified
    elif r50 > 0.15:
        classification = "neutral"
    elif r50 < -0.15:
        classification = "underperformer"
    elif r50 < -0.30:
        classification = "weak"
    else:
        classification = "neutral"

    return {
        "qualitative": True,
        "classification": classification,
        "20d": {
            "stock_ret": round(r20, 4),
            "bench_ret": None,
            "alpha": None,
            "decile": None,
        },
        "50d": {
            "stock_ret": round(r50, 4),
            "bench_ret": None,
            "alpha": None,
            "decile": None,
        },
        "beta": None,
        "beta_adjusted_alpha_50d": None,
        "warning": reason,
        "notes": [
            f"⚠️ 基准数据缺失（{reason}），相对强度仅给定性判断；"
            f"20 日绝对涨幅 {r20*100:+.2f}%，50 日 {r50*100:+.2f}%。"
            f"确认信号会自动降级为试探。"
        ],
    }


def _decile_rank(alpha: float, window: int) -> int:
    """Map alpha to a 1-10 decile based on empirical bucket edges."""
    if window == 20:
        edges = [-0.10, -0.05, -0.03, -0.015, -0.005, 0.005, 0.015, 0.03, 0.05, 0.10]
    else:  # 50d
        edges = [-0.20, -0.10, -0.05, -0.025, -0.01, 0.01, 0.025, 0.05, 0.10, 0.20]
    for i, e in enumerate(edges, start=1):
        if alpha < e:
            return i
    return 10


def _beta(stock_lr: pd.Series, bench_lr: pd.Series) -> float | None:
    aligned = pd.concat([stock_lr, bench_lr], axis=1).dropna()
    if len(aligned) < 30:
        return None
    cov = aligned.cov().iloc[0, 1]
    var = aligned.iloc[:, 1].var()
    if var == 0:
        return None
    return float(cov / var)


def _classify(d20: int, d50: int, alpha20: float, alpha50: float) -> str:
    if d20 >= 8 and d50 >= 8:
        return "strong_outperformer"
    if d20 >= 6 or d50 >= 7:
        return "outperformer"
    if d20 <= 2 and d50 <= 2:
        return "weak"
    if d20 <= 4 or d50 <= 3:
        return "underperformer"
    if abs(alpha20) < 0.01 and abs(alpha50) < 0.01:
        return "neutral"
    return "neutral"


if __name__ == "__main__":
    import sys, json
    from load import load_ohlcv
    if len(sys.argv) not in (2, 3):
        print("usage: python rs.py <stock.csv> [benchmark.csv]")
        sys.exit(1)
    s = load_ohlcv(sys.argv[1])
    b = load_ohlcv(sys.argv[2]) if len(sys.argv) == 3 else None
    print(json.dumps(compute_rs(s, b), indent=2, ensure_ascii=False))
