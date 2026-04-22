"""
Signal engine — turns a StockState into a list of tiered trading signals.

Tiers:
  🟡 试探 (probe)     — early entry on tentative structure, small size
  🟢 确认 (confirm)   — entry on confirmed HH+HL with healthy volume
  🔵 加仓 (compound)  — pyramid add on continuation breakout
  🔴 做空 (short)     — explicit short setup
  🚫 禁追 (banned)    — unsafe regardless of signal (e.g. earnings in 3 days)

Every signal has:
  tier, side, trigger, entry_range, stop_loss, t1, t2, status,
  rationale, r_reward, expires_at, earnings_warning

The rules are deterministic — LLM should not invent signals not produced by
this script, nor should it drop signals the script produced.

v0.2.0 changes:
  - Integrates patterns.analyze_patterns output:
      * Double-top confirmed → no 🟢 confirm long; any pending 🟢 → 🟡
      * Double-top unconfirmed + price near cluster → add warning note
      * Double-bottom confirmed → favor 🟢 entry at bottom break
      * Intraday false break → status '⏳ 待收盘确认' instead of '✅ 触发中'
  - RS qualitative fallback → 🟢 confirm auto-demoted to 🟡 (no verified alpha)
  - Volume regime '缩量回调' in uptrend → adds 🟢 pullback-entry signal
  - Trigger condition language now distinguishes 日收 (close) vs 盘中 (intraday)
  - Adds 'ashare_earnings_season' warning → every long gets a note
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, asdict, field


def zh_rs_short(c: str | None) -> str:
    return {
        "strong_outperformer": "相对强势", "outperformer": "跑赢",
        "neutral": "中性", "underperformer": "跑输", "weak": "弱势",
        "data_insufficient": "数据不足",
    }.get(c or "", c or "?")


def zh_alignment_short(a: str | None) -> str:
    return {
        "both_uptrend": "周日同向",
        "both_downtrend": "周日同向下跌",
        "daily_up_weekly_down": "⚠️ 日上周下(逆势反弹,减仓)",
        "daily_down_weekly_up": "日下周上(回调,机会区)",
        "mixed": "周日不完全同向",
        "weekly_data_missing": "周线数据缺失",
    }.get(a or "", a or "?")


@dataclass
class Signal:
    tier: str
    side: str               # "long" | "short"
    trigger: str
    status: str             # ⏳ 待触发 / ✅ 触发中 / ❌ 已失效 / 🚫 禁追 / ⏳ 待收盘确认
    entry_range: tuple
    stop_loss: float
    t1: float
    t2: float
    r_reward: float
    rationale: str
    expires_at: str
    earnings_warning: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_range"] = list(d["entry_range"])
        return d


def generate_signals(state: dict, horizon_days: int = 7) -> list[dict]:
    price = state["price"]
    trend = state["trend"]
    swings = state["swings"]
    volume = state["volume"]
    rs = state.get("rs") or {}
    indicators = state["indicators"]
    patterns = state.get("patterns") or {}
    earnings_days = state.get("earnings_days_to")
    atr = indicators.get("atr14") or (price * 0.03)

    # Pattern flags we'll consult across rules
    dt_pat = patterns.get("double_top")
    db_pat = patterns.get("double_bottom")
    fb = patterns.get("intraday_breakout") or {}
    has_intraday_false_upbreak = fb.get("upside_false_break", False)
    has_intraday_false_downbreak = fb.get("downside_false_break", False)
    double_top_confirmed = bool(dt_pat and dt_pat.get("confirmed"))
    double_top_shape = bool(dt_pat)  # unconfirmed shape still warns
    double_bottom_confirmed = bool(db_pat and db_pat.get("confirmed"))

    rs_cls = rs.get("classification")
    rs_qualitative = rs.get("qualitative", False)

    signals: list[Signal] = []
    expires = (dt.date.today() + dt.timedelta(days=horizon_days)).isoformat()

    # === RULE 1: Confirmed uptrend → 🟢 long
    if (trend == "uptrend" and
            swings["daily"]["pattern"] == "uptrend_confirmed" and
            rs_cls in ("strong_outperformer", "outperformer") and
            volume["regime"] in ("吸筹放量", "平淡", "缩量回调") and
            not double_top_confirmed):
        nearest_high = swings["daily"].get("nearest_swing_high")
        nearest_low = swings["daily"].get("nearest_swing_low")
        highest_recent = swings["daily"].get("highest_recent_high")

        broken_ref = highest_recent or nearest_high
        dist_above_break_pct = (price - broken_ref) / broken_ref if broken_ref else 0

        if nearest_high and nearest_high > price:
            trigger_px = round(nearest_high * 1.002, 2)
            entry_lo = trigger_px
            entry_hi = round(trigger_px * 1.015, 2)
            status = "⏳ 待触发"
            trigger_desc = f"日收 > ${trigger_px} 且量 ≥ 均量 1.2x"
            ref_for_target = trigger_px
        elif dist_above_break_pct > 0.04:
            entry_lo = round(broken_ref * 1.001, 2)
            entry_hi = round(broken_ref * 1.025, 2)
            trigger_px = entry_hi
            status = "⏳ 待回踩"
            trigger_desc = (f"已突破 ${broken_ref:.2f} 但上涨 {dist_above_break_pct*100:.1f}% 偏快,"
                            f"等待回踩 ${entry_lo}–${entry_hi} 后再评估")
            ref_for_target = entry_hi
        else:
            trigger_px = price
            entry_lo = round(price * 0.99, 2)
            entry_hi = round(price * 1.01, 2)
            # NEW: if intraday broke but close didn't confirm, demote status
            if has_intraday_false_upbreak:
                status = "⏳ 待收盘确认"
                trigger_desc = (
                    f"盘中触及 ${broken_ref:.2f} 上方但收盘回落,需日收 > ${broken_ref:.2f} "
                    f"才算真突破"
                )
            else:
                status = "✅ 触发中"
                trigger_desc = f"已突破 ${broken_ref:.2f},当前价仍在技术参考区间"
            ref_for_target = price

        entry_mid = (entry_lo + entry_hi) / 2
        if nearest_low and nearest_low < entry_mid:
            stop_raw = nearest_low * 0.995
        else:
            stop_raw = entry_mid - 2 * atr
        max_stop_dist = entry_mid * 0.06
        if entry_mid - stop_raw > max_stop_dist:
            stop_raw = entry_mid - max_stop_dist
        stop = round(stop_raw, 2)

        t1_dist = max(2 * atr, ref_for_target * 0.04)
        t1 = round(ref_for_target + t1_dist, 2)
        t2 = round(ref_for_target + 2 * t1_dist, 2)
        r = round((t1 - entry_mid) / max(entry_mid - stop, 0.01), 2)

        alignment = swings.get("alignment", "?")
        sig = Signal(
            tier="🟢 确认",
            side="long",
            trigger=trigger_desc,
            status=status,
            entry_range=(entry_lo, entry_hi),
            stop_loss=stop,
            t1=t1,
            t2=t2,
            r_reward=r,
            rationale=(f"日线 HH+HL 确认({zh_rs_short(rs_cls)})，"
                       f"量能{volume['regime']},{zh_alignment_short(alignment)}"),
            expires_at=expires,
        )
        if alignment == "daily_up_weekly_down":
            sig.notes.append("周线仍在下跌,本次多头条件属于逆势反弹,仓位需减半并设更紧止损")
        elif alignment == "mixed":
            sig.notes.append("周线未形成明确上升,仅日线确认,仓位上限按 2/3 处理")
        if rs_qualitative:
            sig.notes.append("相对强度基于定性判断(基准缺失),本信号自动降级,见下方处理")
        if double_top_shape and not double_top_confirmed:
            sig.notes.append(
                f"⚠️ 价格接近 {dt_pat['kind']} 雏形 ${dt_pat['cluster_price']:.2f},"
                f"若再次触顶回落则信号失效"
            )
        if volume["regime"] == "缩量回调":
            sig.notes.append("量能为缩量回调,回踩确认质量较好(资金非出逃)")
        signals.append(sig)

    # === RULE 2: Tentative uptrend → 🟡 probe
    if (trend in ("uptrend", "range") and
            swings["daily"]["pattern"] in ("uptrend_tentative", "range") and
            rs_cls not in ("weak", "underperformer") and
            not double_top_confirmed):
        nearest_low = swings["daily"].get("nearest_swing_low") or (price - 1.5 * atr)
        stop = round(nearest_low * 0.995, 2)
        t1 = round(price + 2 * atr, 2)
        t2 = round(price + 3 * atr, 2)
        signals.append(Signal(
            tier="🟡 试探",
            side="long",
            trigger=f"当前价 ${price} 附近为试探观察区,上破 ${round(price + atr, 2)} 后再评估加仓条件",
            status="✅ 触发中" if trend == "uptrend" else "⏳ 待触发",
            entry_range=(round(price - 0.5 * atr, 2), round(price + 0.5 * atr, 2)),
            stop_loss=stop,
            t1=t1,
            t2=t2,
            r_reward=round((t1 - price) / max(price - stop, 0.01), 2),
            rationale="结构试探性转多,先 1/3 仓位投石问路",
            expires_at=expires,
        ))

    # === RULE 3: Compound add on new HH
    if (trend == "uptrend" and
            swings["daily"]["pattern"] == "uptrend_confirmed" and
            swings["daily"].get("last_pivot", {}).get("label") == "HH" and
            swings["daily"].get("last_pivot_days_ago", 99) <= 5 and
            volume["regime"] == "吸筹放量" and
            not double_top_confirmed):
        nearest_low = swings["daily"].get("nearest_swing_low") or (price - 2 * atr)
        stop = round(nearest_low, 2)
        t1 = round(price + 3 * atr, 2)
        t2 = round(price + 5 * atr, 2)
        signals.append(Signal(
            tier="🔵 加仓",
            side="long",
            trigger=f"新高延续(最近5天内 HH 成立),加仓条件触发时参考满仓上限",
            status="✅ 触发中",
            entry_range=(round(price - 0.3 * atr, 2), round(price + 0.5 * atr, 2)),
            stop_loss=stop,
            t1=t1,
            t2=t2,
            r_reward=round((t1 - price) / max(price - stop, 0.01), 2),
            rationale="HH + HL + 量能共振,趋势加速期",
            expires_at=expires,
        ))

    # === RULE 4: Confirmed downtrend → 🔴 short candidate
    if (trend == "downtrend" and
            swings["daily"]["pattern"] == "downtrend_confirmed" and
            rs_cls in ("weak", "underperformer")):
        nearest_low = swings["daily"].get("nearest_swing_low")
        nearest_high = swings["daily"].get("nearest_swing_high") or (price + 2 * atr)
        if nearest_low:
            trigger_px = round(nearest_low * 0.998, 2)
            stop = round(nearest_high, 2)
            t1 = round(trigger_px - 2 * atr, 2)
            t2 = round(trigger_px - 4 * atr, 2)
            # Intraday false break on the downside? Hold status to close-confirm
            if has_intraday_false_downbreak:
                status = "⏳ 待收盘确认"
                trigger_desc = (
                    f"盘中跌破 ${nearest_low:.2f} 但收盘回到上方,"
                    f"需日收 < ${trigger_px} 才确认破位"
                )
            else:
                status = "⏳ 待触发"
                trigger_desc = f"日收 < ${trigger_px} 确认破位"
            signals.append(Signal(
                tier="🔴 做空",
                side="short",
                trigger=trigger_desc,
                status=status,
                entry_range=(round(trigger_px * 0.99, 2), trigger_px),
                stop_loss=stop,
                t1=t1,
                t2=t2,
                r_reward=round((trigger_px - t1) / max(stop - trigger_px, 0.01), 2),
                rationale="LH+LL 确认 + 相对强度弱,破位确认做空",
                expires_at=expires,
            ))

    # === RULE 5 (NEW): Double-top confirmed → 🔴 short on neckline break
    if double_top_confirmed and dt_pat.get("neckline"):
        neckline = dt_pat["neckline"]
        cluster = dt_pat["cluster_price"]
        # Target = neckline - (cluster - neckline) — classic measured move
        measured_move = cluster - neckline
        t1 = round(neckline - measured_move, 2)
        t2 = round(neckline - measured_move * 1.5, 2)
        stop = round(cluster * 1.005, 2)
        entry_lo = round(neckline * 0.99, 2)
        entry_hi = round(neckline * 0.998, 2)
        signals.append(Signal(
            tier="🔴 做空",
            side="short",
            trigger=f"{dt_pat['kind']} 已确认,反抽颈线 ${neckline:.2f} 附近为参考区间",
            status="⏳ 待触发",
            entry_range=(entry_lo, entry_hi),
            stop_loss=stop,
            t1=t1,
            t2=t2,
            r_reward=round((neckline - t1) / max(stop - neckline, 0.01), 2),
            rationale=f"{dt_pat['kind']} 颈线破位,测量目标 ${t1:.2f}",
            expires_at=expires,
            notes=[f"双顶 {dt_pat['touch_count']} 次触顶 ${cluster:.2f},颈线 ${neckline:.2f}"],
        ))

    # === RULE 6 (NEW): Double-bottom confirmed → 🟢 long on neckline break
    if double_bottom_confirmed and db_pat.get("neckline"):
        neckline = db_pat["neckline"]
        cluster = db_pat["cluster_price"]
        measured_move = neckline - cluster
        t1 = round(neckline + measured_move, 2)
        t2 = round(neckline + measured_move * 1.5, 2)
        stop = round(cluster * 0.995, 2)
        entry_lo = round(neckline * 1.002, 2)
        entry_hi = round(neckline * 1.01, 2)
        signals.append(Signal(
            tier="🟢 确认",
            side="long",
            trigger=f"{db_pat['kind']} 已确认,突破颈线 ${neckline:.2f}",
            status="✅ 触发中" if price > neckline else "⏳ 待触发",
            entry_range=(entry_lo, entry_hi),
            stop_loss=stop,
            t1=t1,
            t2=t2,
            r_reward=round((t1 - entry_hi) / max(entry_hi - stop, 0.01), 2),
            rationale=f"{db_pat['kind']} 颈线突破,测量目标 ${t1:.2f}",
            expires_at=expires,
            notes=[f"双底 {db_pat['touch_count']} 次触底 ${cluster:.2f},颈线 ${neckline:.2f}"],
        ))

    # === BANNED: earnings ≤ 3 days → status = 🚫 for everything
    for s in signals:
        if earnings_days is not None and earnings_days <= 3:
            s.status = "🚫 禁追"
            s.earnings_warning = True
            s.notes.append(f"财报仅剩 {earnings_days} 天,强制禁追,等财报后重评")
        elif earnings_days is not None and earnings_days <= 14:
            s.earnings_warning = True
            s.notes.append(f"财报 {earnings_days} 天内,仓位需减半或用对冲控制跳空风险")

    # === A-share earnings-season auto-note (applies when explicit date missing)
    season_warnings = [w for w in state.get("warnings", []) if w.startswith("ashare_earnings_season")]
    if season_warnings and (earnings_days is None or earnings_days > 14):
        for s in signals:
            if s.side == "long":
                s.notes.append("⚠️ A 股披露季(4/8/10 月) 季报高概率在 14 天内,执行前请核对披露日")

    # Extreme overbought caveat
    rsi = indicators.get("rsi14")
    if rsi is not None and rsi >= 75:
        for s in signals:
            if s.side == "long":
                s.notes.append(f"RSI {rsi:.1f} 超买,试探仓位再减半,等回踩再加")

    # 爆量 (single-day exhaustion) caveat
    if volume.get("regime") == "爆量":
        for s in signals:
            s.notes.append("最近14天内有单日量比 ≥2.5 爆量,警惕情绪化,减仓 1/3")

    # RS qualitative → demote 🟢 to 🟡
    if rs_qualitative:
        demoted = []
        for s in signals:
            if s.tier.startswith("🟢") and s.side == "long":
                s.tier = "🟡 试探"
                s.notes.append("相对强度仅定性(基准缺失),自动从 🟢 降级到 🟡,仓位减半")
            demoted.append(s)
        signals = demoted

    # Correlated asset note (applies to all longs)
    correlated = state.get("correlated_asset")
    if correlated:
        for s in signals:
            s.notes.append(f"关联资产:{correlated},价格联动需同步监控")

    # === Quality filter: drop signals with R < 0.5, demote 🟢 with R < 1.0
    filtered: list[Signal] = []
    for s in signals:
        if s.r_reward < 0.5:
            continue
        if s.tier.startswith("🟢") and s.r_reward < 1.0:
            s.tier = "🟡 试探"
            s.notes.append(f"R/R 仅 {s.r_reward},自动从 🟢 降级到 🟡,仓位减半")
        filtered.append(s)

    return [s.to_dict() for s in filtered]


if __name__ == "__main__":
    import sys, json
    if len(sys.argv) != 2:
        print("usage: python signals.py <state.json>")
        sys.exit(1)
    with open(sys.argv[1]) as f:
        state = json.load(f)
    print(json.dumps(generate_signals(state), indent=2, ensure_ascii=False))
