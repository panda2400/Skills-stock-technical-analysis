"""
Top-level orchestrator.

Reads OHLCV CSVs, composes all analysis modules, emits a single StockState
JSON file that render.py (or any downstream consumer) can load.

Usage:
    python analyze.py \
        --ticker TSLA \
        --market US \
        --daily /tmp/stk/TSLA_daily.csv \
        --weekly /tmp/stk/TSLA_weekly.csv \
        --benchmark /tmp/stk/SPY_daily.csv \
        --earnings-date 2026-04-23 \
        --account-size 100000 \
        --risk-profile aggressive \
        --out /tmp/stk/TSLA_state.json

    # Optional, only when signal history should persist:
    python analyze.py ... --log-dir /tmp/stk/logs

v0.2.0 changes:
  - --weekly is now OPTIONAL. If omitted, weekly is synthesized from daily
    (fallback for when the exchange's weekly endpoint is unavailable).
  - --correlated-asset "ETH (price-linked holdings)" — free-text annotation
    shown in the report, no numerical link assumed.
  - A-share earnings season auto-flag: if market=A and current month is
    4 / 8 / 10, adds an earnings_season_risk warning even when
    --earnings-date is not provided.
  - Liquidity classification from load.classify_liquidity fed into risk sizing.
  - Pattern detectors (double-top, intraday false-break, compression) are
    run and their notes are surfaced to the top-level state.
  - Volatility downgrade (ATR%) applied automatically via risk.py.

v0.3.0 changes:
  - Signal history logging is opt-in via --log-dir; by default the script only
    writes the requested state JSON.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import uuid
from pathlib import Path

# Allow running the script directly without a package install
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from load import load_ohlcv, summarize, resample_daily_to_weekly, classify_liquidity
from swings import analyze_swings
from volume import analyze_volume
from rs import compute_rs
from indicators import compute_indicators
from levels import compute_levels
from patterns import analyze_patterns
from signals import generate_signals
from risk import size_positions, summary_exposure


SKILL_VERSION = "0.3.0"

# A-share fixed quarterly disclosure windows (by calendar month):
#   Q1 报告 (一季报): 4 月
#   中报 (Semi-annual): 8 月
#   Q3 报告 (三季报): 10 月
# Companies are legally required to file by end of these months.
ASHARE_EARNINGS_MONTHS = {4, 8, 10}


def build_state(args: argparse.Namespace) -> dict:
    warnings: list[str] = []

    daily = load_ohlcv(args.daily)

    # Weekly: explicit file if given; else synthesize from daily
    if args.weekly:
        try:
            weekly = load_ohlcv(args.weekly)
            weekly_source = "provided"
        except Exception as e:
            warnings.append(f"weekly_load_failed:{type(e).__name__}")
            weekly = resample_daily_to_weekly(daily)
            weekly_source = "synthesized_from_daily"
    else:
        weekly = resample_daily_to_weekly(daily)
        weekly_source = "synthesized_from_daily"
        warnings.append("weekly_synthesized_from_daily")

    # Benchmark
    benchmark = None
    benchmark_source = "missing"
    if args.benchmark:
        try:
            benchmark = load_ohlcv(args.benchmark)
            benchmark_source = "provided"
        except Exception as e:
            warnings.append(f"benchmark_load_failed:{type(e).__name__}")
            benchmark = None
    if benchmark is None:
        warnings.append("benchmark_missing_qualitative_fallback")

    if len(daily) < 60:
        warnings.append(f"daily_bars_insufficient ({len(daily)} bars, need 60+)")

    # Core analysis modules
    swings_result = analyze_swings(daily, weekly)
    volume_result = analyze_volume(daily)
    rs_result = compute_rs(daily, benchmark)
    indicators_result = compute_indicators(daily)
    levels_result = compute_levels(daily, swings_result, indicators_result)
    patterns_result = analyze_patterns(daily, swings_result)
    liquidity_result = classify_liquidity(daily, market=args.market)

    # Derive overall trend and phase from swings + alignment
    trend = _derive_trend(swings_result)
    phase = _derive_phase(trend, volume_result)

    # Earnings handling
    earnings_days = None
    earnings_date = args.earnings_date
    if earnings_date:
        try:
            ed = dt.date.fromisoformat(earnings_date)
            earnings_days = (ed - dt.date.today()).days
            if 0 <= earnings_days <= 14:
                warnings.append(f"earnings_within_14d:{earnings_days}")
        except ValueError:
            warnings.append(f"earnings_date_unparseable:{earnings_date}")

    # A-share earnings-season auto-flag (runs even if --earnings-date absent)
    if args.market == "A":
        today = dt.date.today()
        if today.month in ASHARE_EARNINGS_MONTHS:
            warnings.append(f"ashare_earnings_season:{today.month}月")

    price = float(daily["close"].iloc[-1])
    atr_pct = indicators_result.get("atr_pct")

    # High-volatility flag
    if atr_pct is not None and atr_pct > 5.0:
        warnings.append(f"high_volatility_atr_{atr_pct:.1f}pct")

    # Thin-liquidity flag
    if liquidity_result.get("class") == "thin":
        warnings.append("liquidity_thin")

    one_line_summary = _one_line_summary(
        trend, swings_result, volume_result, rs_result, patterns_result
    )

    state_core = {
        "ticker": args.ticker,
        "market": args.market,
        "asof": daily.index[-1].strftime("%Y-%m-%d"),
        "price": round(price, 3),
        "trend": trend,
        "phase": phase,
        "one_line_summary": one_line_summary,
        "swings": swings_result,
        "volume": volume_result,
        "rs": rs_result,
        "indicators": indicators_result,
        "levels": levels_result,
        "patterns": patterns_result,
        "liquidity": liquidity_result,
        "correlated_asset": args.correlated_asset,
        "earnings_days_to": earnings_days,
        "earnings_date": earnings_date,
        "warnings": warnings,
        "data_summary": {
            "daily": summarize(daily),
            "weekly": summarize(weekly) if weekly is not None else None,
            "benchmark": summarize(benchmark) if benchmark is not None else None,
            "weekly_source": weekly_source,
            "benchmark_source": benchmark_source,
        },
        "skill_version": SKILL_VERSION,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }

    # Signal engine runs on the (incomplete) state and produces raw signals
    raw_signals = generate_signals(state_core)
    # Position sizing applies vol + liquidity downgrades
    sized_signals = size_positions(
        raw_signals,
        args.account_size,
        args.risk_profile,
        atr_pct=atr_pct,
        liquidity_class=liquidity_result.get("class"),
    )
    exposure = summary_exposure(sized_signals)

    state_core["signals"] = sized_signals
    state_core["exposure"] = exposure
    state_core["account_size"] = args.account_size
    state_core["risk_profile"] = args.risk_profile

    return state_core


def _derive_trend(swings_result: dict) -> str:
    """Derive the headline trend from daily + weekly alignment.

    Decision table:
      daily=uptrend_confirmed + weekly uptrend/range/missing  → uptrend
      daily=uptrend_confirmed + weekly downtrend              → unclear (conflict)
      daily=downtrend_confirmed + weekly downtrend/range/missing → downtrend
      daily=downtrend_confirmed + weekly uptrend              → unclear (conflict)
      daily=range → range
      daily=tentative → matching tentative direction
      else → unclear
    """
    alignment = swings_result.get("alignment", "weekly_data_missing")
    d_pat = swings_result["daily"]["pattern"]

    if d_pat == "uptrend_confirmed":
        if alignment in ("both_uptrend", "weekly_data_missing", "mixed",
                         "daily_up_weekly_down"):
            return "uptrend"
    if d_pat == "downtrend_confirmed":
        if alignment in ("both_downtrend", "weekly_data_missing", "mixed",
                         "daily_down_weekly_up"):
            return "downtrend"
    if d_pat == "range":
        return "range"
    if "tentative" in d_pat:
        return "uptrend" if "uptrend" in d_pat else "downtrend"
    return "unclear"


def _derive_phase(trend: str, volume: dict) -> str:
    reg = volume.get("regime")
    if trend == "uptrend":
        if reg == "吸筹放量":
            return "markup"
        if reg == "干枯":
            return "late_markup"
        if reg == "缩量回调":
            return "healthy_pullback"
        return "markup"
    if trend == "downtrend":
        if reg == "出货放量":
            return "markdown"
        return "markdown"
    if trend == "range":
        if reg == "吸筹放量":
            return "accumulation"
        if reg == "出货放量":
            return "distribution"
        return "undefined"
    return "undefined"


def _one_line_summary(trend, swings, volume, rs, patterns) -> str:
    parts = []
    parts.append(f"日线{_zh_pattern(swings['daily']['pattern'])}")
    if swings.get("weekly"):
        parts.append(f"周线{_zh_pattern(swings['weekly']['pattern'])}")
    parts.append(f"量能{volume.get('regime', '未知')}")
    if rs:
        rs_cls = rs.get("classification", "未知")
        qual = "(定性)" if rs.get("qualitative") else ""
        parts.append(f"相对强度{_zh_rs(rs_cls)}{qual}")
    # Surface a critical pattern at the top if found
    dt_pat = patterns.get("double_top")
    if dt_pat and dt_pat.get("confirmed"):
        parts.append(f"⚠️{dt_pat['kind']}已确认")
    elif dt_pat:
        parts.append(f"⚠️{dt_pat['kind']}雏形")
    db_pat = patterns.get("double_bottom")
    if db_pat and db_pat.get("confirmed"):
        parts.append(f"🟢{db_pat['kind']}已确认")
    if patterns.get("intraday_breakout", {}).get("upside_false_break"):
        parts.append("⚠️盘中假突破")
    return ",".join(parts)


def _zh_pattern(p: str) -> str:
    return {
        "uptrend_confirmed": "上升确认",
        "uptrend_tentative": "上升试探",
        "downtrend_confirmed": "下跌确认",
        "downtrend_tentative": "下跌试探",
        "range": "区间震荡",
        "unclear": "模糊",
    }.get(p, p)


def _zh_rs(c: str) -> str:
    return {
        "strong_outperformer": "强势领涨",
        "outperformer": "跑赢",
        "neutral": "中性",
        "underperformer": "跑输",
        "weak": "弱势",
        "data_insufficient": "数据不足",
    }.get(c, c)


def log_signal(signal: dict, state: dict, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "signal_id": str(uuid.uuid4()),
        "ticker": state["ticker"],
        "market": state["market"],
        "asof": state["asof"],
        "issued_at": state["generated_at"],
        "tier": signal["tier"],
        "side": signal["side"],
        "trigger": signal["trigger"],
        "entry_range": signal["entry_range"],
        "stop_loss": signal["stop_loss"],
        "t1": signal["t1"],
        "t2": signal["t2"],
        "r_reward": signal["r_reward"],
        "position_pct": signal["position_pct"],
        "status": signal["status"],
        "price_at_issue": state["price"],
        "trend": state["trend"],
        "phase": state["phase"],
        "volume_regime": state["volume"]["regime"],
        "rs_class": (state["rs"] or {}).get("classification"),
        "rs_qualitative": (state["rs"] or {}).get("qualitative", False),
        "earnings_days_to": state["earnings_days_to"],
        "liquidity_class": state.get("liquidity", {}).get("class"),
        "atr_pct": state.get("indicators", {}).get("atr_pct"),
        "skill_version": state["skill_version"],
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ticker", required=True)
    p.add_argument("--market", default="US", choices=["US", "A", "HK"])
    p.add_argument("--daily", required=True)
    p.add_argument("--weekly", default=None,
                   help="Weekly OHLCV CSV. If omitted, synthesized from daily.")
    p.add_argument("--benchmark", default=None,
                   help="Benchmark CSV (SPY / 000300 / HSI). If omitted, RS falls back to qualitative.")
    p.add_argument("--earnings-date", default=None)
    p.add_argument("--correlated-asset", default=None,
                   help="Free-text note for a correlated asset, e.g. 'ETH treasury proxy'")
    p.add_argument("--account-size", type=float, default=100_000)
    p.add_argument("--risk-profile", default="aggressive",
                   choices=["aggressive", "balanced", "conservative"])
    p.add_argument("--out", required=True)
    p.add_argument("--log-dir", default=None,
                   help="Optional directory for signal_log.jsonl. If omitted, no signal history is written.")
    args = p.parse_args()

    state = build_state(args)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    # Persist signal history only when explicitly requested. This keeps the
    # skill package immutable during normal one-off analysis runs.
    if args.log_dir:
        log_path = Path(args.log_dir) / "signal_log.jsonl"
        for sig in state["signals"]:
            log_signal(sig, state, log_path)

    # Print a compact summary to stdout
    print(json.dumps({
        "status": "ok",
        "ticker": state["ticker"],
        "price": state["price"],
        "trend": state["trend"],
        "one_line_summary": state["one_line_summary"],
        "signals_count": len(state["signals"]),
        "warnings": state["warnings"],
        "out": str(out_path),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
