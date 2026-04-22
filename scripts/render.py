"""
Render a StockState JSON into a Markdown report using a template.

Usage:
    python render.py --state /tmp/stk/TSLA_state.json --template zh-S1 --out report.md

v0.2.0 additions:
  - Patterns block (双顶 / 双底 / 盘中假突破 / 区间压缩)
  - Liquidity block (ADV + 分级)
  - Correlated asset note (optional, free-text)
  - Data-gap appendix (explicit warning when weekly synthesized, benchmark missing, etc.)
  - Volatility + liquidity sizing adjustment visible per signal
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
TEMPLATE_DIR = SCRIPT_DIR.parent / "templates"


def zh_pattern(p: str) -> str:
    return {
        "uptrend_confirmed": "上升确认",
        "uptrend_tentative": "上升试探",
        "downtrend_confirmed": "下跌确认",
        "downtrend_tentative": "下跌试探",
        "range": "区间震荡",
        "unclear": "结构模糊",
    }.get(p, p)


def zh_trend(t: str) -> str:
    return {"uptrend": "上升", "downtrend": "下跌", "range": "震荡", "unclear": "模糊"}.get(t, t)


def zh_phase(p: str) -> str:
    return {
        "accumulation": "吸筹",
        "markup": "拉升",
        "late_markup": "拉升末段",
        "distribution": "出货",
        "markdown": "下跌",
        "healthy_pullback": "健康回调",
        "undefined": "未明确",
    }.get(p, p)


def zh_alignment(a: str) -> str:
    return {
        "both_uptrend": "周日线同向向上 ✅",
        "both_downtrend": "周日线同向向下",
        "daily_up_weekly_down": "⚠️ 日线反弹但周线下跌,逆势反弹风险高",
        "daily_down_weekly_up": "周线多头回调,或为机会区",
        "mixed": "周日线不一致",
        "weekly_data_missing": "周线数据缺失",
    }.get(a, a)


def zh_rs_class(c: str) -> str:
    return {
        "strong_outperformer": "强势领涨",
        "outperformer": "跑赢",
        "neutral": "中性",
        "underperformer": "跑输",
        "weak": "弱势",
        "data_insufficient": "数据不足",
    }.get(c, c)


def zh_liquidity(c: str) -> str:
    return {
        "thin": "薄",
        "moderate": "中等",
        "liquid": "充裕",
        "unknown": "未知",
    }.get(c, c)


def render_swing_block(swing: dict | None) -> str:
    if not swing:
        return "_数据不足,无法分析_"
    pivots = swing.get("pivots", [])
    lines = []
    lines.append(f"**形态**: {zh_pattern(swing['pattern'])}(置信度: {swing.get('confidence', '?')})")
    if swing.get("last_pivot"):
        lp = swing["last_pivot"]
        days_ago = swing.get("last_pivot_days_ago")
        ago_str = f",{days_ago} 根前" if days_ago is not None else ""
        lines.append(f"**最近枢轴**: {lp['label']} @ ${lp['price']:.2f}({lp['date']}{ago_str})")
    if pivots:
        seq = " → ".join([f"{p['label']}(${p['price']:.2f})" for p in pivots])
        lines.append(f"**近期序列**: {seq}")
    return "\n\n".join(lines)


def render_volume_table(table: list[dict]) -> str:
    rows = []
    for r in table:
        pct = f"{r['pct_change']:+.2f}%" if r.get("pct_change") is not None else "—"
        ratio = f"{r['volume_ratio']:.2f}x" if r.get("volume_ratio") is not None else "—"
        rows.append(f"| {r['date']} | ${r['close']:.2f} | {pct} | {r['volume']:,} | {ratio} |")
    return "\n".join(rows) if rows else "| 数据不足 | | | | |"


def render_rs_block(rs: dict | None) -> str:
    if not rs or rs.get("classification") == "data_insufficient":
        return "_相对强弱数据不足_"

    r20 = rs.get("20d") or {}
    r50 = rs.get("50d") or {}
    qual = rs.get("qualitative", False)

    def fmt(win: dict) -> str:
        if not win:
            return "数据不足"
        sr = (win.get("stock_ret") or 0) * 100
        br = win.get("bench_ret")
        al = win.get("alpha")
        dec = win.get("decile")
        if br is None or al is None:
            return f"本票 {sr:+.2f}%(基准数据缺失,仅供参考)"
        return (f"本票 {sr:+.2f}% vs 基准 {br*100:+.2f}%,"
                f"超额 {al*100:+.2f}%(分位数 {dec}/10)")

    lines = [
        f"**20日**: {fmt(r20)}",
        f"**50日**: {fmt(r50)}",
    ]
    if not qual:
        lines.append(f"**Beta**: {rs.get('beta') or '—'}")
        lines.append(f"**Beta 调整后 50日超额**: {((rs.get('beta_adjusted_alpha_50d') or 0) * 100):+.2f}%")
    lines.append(f"**分类**: {zh_rs_class(rs.get('classification', '?'))}"
                 + (" ⚠️ 仅定性判断" if qual else ""))

    if rs.get("warning") == "beta_driven_rally":
        lines.append("⚠️ **警告**: 超额收益主要来自 beta,去掉市场因素后纯 alpha 很小,"
                     "请警惕市场回调时回吐更多")
    elif qual:
        lines.append("⚠️ **警告**: 基准 CSV 缺失,相对强度无法验证。"
                     "所有 🟢 确认级信号自动降级为 🟡 试探。")
    return "\n\n".join(lines)


def render_levels_block(levels: list[dict]) -> str:
    if not levels:
        return "_无明显价位_"
    lines = []
    for lv in levels:
        lines.append(
            f"- **${lv['price']:.2f}** ({lv['basis']}) · 重要度 {lv['importance']} · "
            f"距现价 {lv['distance_pct']:+.2f}%"
        )
    return "\n".join(lines)


def render_earnings_block(earnings_days, earnings_date, warnings: list[str]) -> str:
    # Primary: explicit earnings-date
    if earnings_days is not None:
        if earnings_days < 0:
            primary = f"最近财报已发布(距今 {-earnings_days} 天),暂无近期财报风险。"
        elif earnings_days == 0:
            primary = f"⚠️ **今日为财报日({earnings_date})**,隔夜跳空风险极高。"
        elif earnings_days <= 3:
            primary = f"⚠️ **财报 {earnings_days} 天后({earnings_date})**,信号状态已自动设为 🚫 禁追。"
        elif earnings_days <= 14:
            primary = f"⚠️ 财报 {earnings_days} 天后({earnings_date}),交易前请减半仓或用期权对冲。"
        else:
            primary = f"下次财报:{earnings_date}(距今 {earnings_days} 天),暂无近期风险。"
    else:
        primary = "财报日期未提供,执行前需核对披露节奏。"

    # A-share earnings season auto-flag
    season = [w for w in warnings if w.startswith("ashare_earnings_season")]
    if season:
        month = season[0].split(":")[-1]
        primary += f"\n\n⚠️ **A 股披露季({month})**: 一季报 / 中报 / 三季报为法定披露窗口,实际披露日高概率在 14 天内,执行前务必通过交易软件核对。"

    return primary


def render_patterns_block(patterns: dict | None) -> str:
    """Render the double-top/bottom/false-break/compression summary."""
    if not patterns or not patterns.get("notes"):
        return "_近期无显著形态_"
    return "\n\n".join(f"- {n}" for n in patterns["notes"])


def render_liquidity_block(liquidity: dict | None) -> str:
    if not liquidity or liquidity.get("class") == "unknown":
        return "_流动性数据不足_"
    cls_zh = zh_liquidity(liquidity["class"])
    read = liquidity.get("read", "")
    warning = ""
    if liquidity["class"] == "thin":
        warning = " ⚠️ 止损易滑点,仓位需降档"
    return f"{cls_zh}{warning} · {read}"


def render_correlated_asset(correlated: str | None) -> str:
    if not correlated:
        return ""
    return f"\n\n**关联资产**: {correlated}(价格联动需同步监控)\n"


def render_data_gaps(state: dict) -> str:
    """List all material data gaps with user-actionable hints."""
    warnings = state.get("warnings", [])
    ds = state.get("data_summary", {})
    rs = state.get("rs", {}) or {}

    gap_lines: list[str] = []

    if ds.get("weekly_source") == "synthesized_from_daily":
        gap_lines.append(
            "- **周线**: 使用日线 ISO-周聚合合成(周线数据源未提供或失败)。"
            "长期结构结论基本不变,但若周线枢轴关键,需手动核对。"
        )
    if ds.get("benchmark_source") == "missing":
        gap_lines.append(
            "- **基准指数**: 缺失。相对强度已降级为定性判断。"
            "需手动打开行情软件查看创业板指/沪深300/SPY近 20/50 日涨幅作交叉验证。"
        )
    if rs.get("qualitative"):
        if ds.get("benchmark_source") != "missing":
            gap_lines.append(f"- **相对强度**: 降级原因 = {rs.get('warning', '未知')}")

    if state.get("earnings_days_to") is None and state.get("market") == "A":
        if any(w.startswith("ashare_earnings_season") for w in warnings):
            gap_lines.append(
                "- **A 股季报日期**: 财务接口数据缺失,当前为披露窗口月,"
                "执行前请在交易软件核对具体披露日。"
            )

    data_load_fails = [w for w in warnings if w.endswith("_load_failed") or "load_failed" in w]
    for w in data_load_fails:
        gap_lines.append(f"- **加载失败**: {w}")

    if not gap_lines:
        return ""

    return "\n".join([
        "",
        "---",
        "",
        "## 附录 — 数据缺口说明",
        "",
        *gap_lines,
    ])


def render_size_adjustments(adj: dict | None) -> str:
    """One-liner describing sizing adjustments applied."""
    if not adj:
        return ""
    parts = []
    if adj.get("volatility_multiplier") != 1.0:
        parts.append(f"波动率系数 {adj['volatility_multiplier']}")
    if adj.get("liquidity_multiplier") != 1.0:
        parts.append(f"流动性系数 {adj['liquidity_multiplier']}")
    if adj.get("shrunk_by_max_loss_guard"):
        parts.append("止损保护压缩")
    if not parts:
        return ""
    return f"[仓位调整: {', '.join(parts)}]"


def render_signal(sig: dict) -> str:
    status = sig.get("status", "?")
    tier = sig.get("tier", "?")
    side = "多" if sig["side"] == "long" else "空"
    entry = sig["entry_range"]
    adj_line = render_size_adjustments(sig.get("size_adjustments"))
    lines = [
        f"### {tier} · {side} · {status}",
        "",
        f"- **触发条件**: {sig['trigger']}",
        f"- **参考区间**: ${entry[0]} – ${entry[1]}",
        f"- **失效/止损边界**: ${sig['stop_loss']}",
        f"- **观察位**: T1 ${sig['t1']} / T2 ${sig['t2']}(R = {sig['r_reward']})",
        f"- **仓位上限**: {sig['position_pct']*100:.1f}%(约 ${sig['position_usd']:,.0f},"
        f" {sig['shares']} 股),最大亏损 ${sig['max_loss_usd']:,.0f}"
        f"({sig['max_loss_pct_of_account']*100:.2f}% 账户){' ' + adj_line if adj_line else ''}",
        f"- **技术依据**: {sig['rationale']}",
        f"- **有效期**: 至 {sig['expires_at']}",
    ]
    notes = sig.get("notes") or []
    if notes:
        lines.append("- **提醒**:")
        for n in notes:
            lines.append(f"  - {n}")
    if sig.get("earnings_warning"):
        lines.append("- ⚠️ 受财报影响,请审慎执行")
    return "\n".join(lines)


def render_signals_block(signals: list[dict]) -> str:
    if not signals:
        return "当前没有高质量条件信号。等待结构清晰化、量能变化或关键价位重新确认后再评估。"
    return "\n\n".join(render_signal(s) for s in signals)


def render_warnings_list(warnings: list[str]) -> str:
    if not warnings:
        return ""
    shown_prefixes = ("earnings_within_14d", "ashare_earnings_season")
    relevant = [w for w in warnings if not any(w.startswith(p) for p in shown_prefixes)]
    if not relevant:
        return ""
    lines = ["", "**数据/结构警示**:"]
    for w in relevant:
        lines.append(f"- {w}")
    return "\n".join(lines)


def render_earnings_banner(warnings: list[str], earnings_days) -> str:
    if earnings_days is not None and 0 <= earnings_days <= 14:
        return f"> ⚠️ **财报警示**:{earnings_days} 天内将发布财报,所有信号请按报告内风险提示执行。\n"
    season = [w for w in warnings if w.startswith("ashare_earnings_season")]
    if season and earnings_days is None:
        month = season[0].split(":")[-1]
        return (f"> ⚠️ **A 股披露季警示**:当前为 {month},一季报/中报/三季报法定披露期,"
                "执行前请核对本票披露日。\n")
    return ""


def render_exposure_summary(exp: dict) -> str:
    triggered = exp.get("triggered_exposure_pct", 0) * 100
    potential = exp.get("max_potential_exposure_pct", 0) * 100
    banned = exp.get("banned_count", 0)
    banned_part = f",已禁追 {banned}" if banned else ""
    return f"触发中 {triggered:.1f}%,全部条件成立后的参考上限 {potential:.1f}%{banned_part}"


def render_report(state: dict, template: str) -> str:
    vol = state["volume"]
    ind = state["indicators"]

    substitutions = {
        "EARNINGS_WARNING_BANNER": render_earnings_banner(state["warnings"], state["earnings_days_to"]),
        "TICKER": state["ticker"],
        "MARKET": state.get("market", "?"),
        "ASOF": state["asof"],
        "PRICE": f"{state['price']:.2f}",
        "TREND_ZH": zh_trend(state["trend"]),
        "PHASE_ZH": zh_phase(state["phase"]),
        "ONE_LINE_SUMMARY": state["one_line_summary"],
        "WARNINGS_LIST": render_warnings_list(state["warnings"]),
        "PATTERNS_BLOCK": render_patterns_block(state.get("patterns")),
        "WEEKLY_SWING_BLOCK": render_swing_block(state["swings"].get("weekly")),
        "DAILY_SWING_BLOCK": render_swing_block(state["swings"].get("daily")),
        "ALIGNMENT_ZH": zh_alignment(state["swings"].get("alignment", "?")),
        "VOLUME_REGIME": vol.get("regime", "?"),
        "VOLUME_AVG_RATIO": f"{vol.get('avg_ratio_14d', 0):.2f}x" if vol.get("avg_ratio_14d") else "—",
        "VOLUME_MAX_RATIO": f"{vol.get('max_ratio_14d', 0):.2f}x" if vol.get("max_ratio_14d") else "—",
        "VOLUME_DIVERGENCE_LINE": f",量价背离:**{vol['divergence']}**" if vol.get("divergence") else "",
        "VOLUME_TABLE_ROWS": render_volume_table(vol.get("table", [])),
        "RS_BLOCK": render_rs_block(state.get("rs")),
        "LIQUIDITY_BLOCK": render_liquidity_block(state.get("liquidity")),
        "CORRELATED_ASSET_BLOCK": render_correlated_asset(state.get("correlated_asset")),
        "RESISTANCE_BLOCK": render_levels_block(state["levels"].get("resistance", [])),
        "SUPPORT_BLOCK": render_levels_block(state["levels"].get("support", [])),
        "MA10": f"{ind.get('ma10', 0):.2f}",
        "MA20": f"{ind.get('ma20', 0):.2f}",
        "MA50": f"{ind.get('ma50', 0):.2f}",
        "MA_READ": ind.get("ma_read", "—"),
        "RSI14": f"{ind.get('rsi14', 0):.1f}",
        "RSI_READ": ind.get("rsi_read", "—"),
        "MACD_HIST": f"{ind.get('macd_hist', 0):+.3f}",
        "MACD_READ": ind.get("macd_read", "—"),
        "ATR14": f"{ind.get('atr14', 0):.2f}",
        "ATR_PCT": f"{ind.get('atr_pct', 0):.2f}",
        "EARNINGS_BLOCK": render_earnings_block(
            state["earnings_days_to"], state.get("earnings_date"), state.get("warnings", [])
        ),
        "SIGNALS_BLOCK": render_signals_block(state.get("signals", [])),
        "EXPOSURE_SUMMARY": render_exposure_summary(state.get("exposure", {})),
        "RISK_PROFILE": state.get("risk_profile", "aggressive"),
        "ACCOUNT_SIZE": f"{state.get('account_size', 0):,.0f}",
        "SKILL_VERSION": state.get("skill_version", "?"),
        "DATA_GAPS_APPENDIX": render_data_gaps(state),
    }

    tpl_path = TEMPLATE_DIR / f"{template}.md"
    if not tpl_path.exists():
        raise FileNotFoundError(f"template not found: {tpl_path}")
    text = tpl_path.read_text(encoding="utf-8")
    for key, val in substitutions.items():
        text = text.replace("{{" + key + "}}", str(val))

    # Clean up empty leading lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True)
    p.add_argument("--template", default="zh-S1")
    p.add_argument("--out", required=True)
    args = p.parse_args()

    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    md = render_report(state, args.template)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"wrote {out} ({len(md)} chars)")


if __name__ == "__main__":
    main()
