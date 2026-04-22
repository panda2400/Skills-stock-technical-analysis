"""
Position-sizing math.

v0.2.0 additions:
  - **Volatility downgrade** (ATR% based)
      If daily ATR% exceeds thresholds, tier targets are scaled down.
      Default thresholds:
        atr_pct <= 3.0          → no change         (multiplier 1.00)
        3.0 < atr_pct <= 5.0    → reduce 20%        (multiplier 0.80)
        5.0 < atr_pct <= 7.0    → reduce 40%        (multiplier 0.60)
        atr_pct > 7.0           → reduce 60%        (multiplier 0.40)
      max_loss caps are independently enforced and NOT scaled.

  - **Liquidity downgrade** (ADV based)
      Stocks with thin average-daily-value trading get position sizes reduced
      because slippage and stop-fill risk grow non-linearly with
      size-vs-ADV ratio.
        liquidity_class='thin'      → multiplier 0.60
        liquidity_class='moderate'  → multiplier 0.85
        liquidity_class='liquid'    → multiplier 1.00

  - Multipliers compose multiplicatively (ATR × liquidity).
  - Every compression emits a note so the user sees *why*.

Three profiles ship, selected by name:
  aggressive (default):  🟡 0.05, 🟢 0.10, 🔵 0.15, hard cap 0.15
  balanced:              🟡 0.03, 🟢 0.07, 🔵 0.10, hard cap 0.12
  conservative:          🟡 0.02, 🟢 0.05, 🔵 0.08, hard cap 0.10

Also enforces a per-signal "max loss % of account" guardrail — if the stop is
far from entry relative to position size, the position is shrunk to keep the
loss within 1.0% of account (probe) / 1.5% (confirm) / 2.0% (compound).
"""

from __future__ import annotations

PROFILES = {
    "aggressive": {
        "🟡 试探": 0.05, "🟢 确认": 0.10, "🔵 加仓": 0.15, "🔴 做空": 0.05,
        "cap": 0.15,
        "max_loss": {"🟡 试探": 0.010, "🟢 确认": 0.015, "🔵 加仓": 0.020, "🔴 做空": 0.010},
    },
    "balanced": {
        "🟡 试探": 0.03, "🟢 确认": 0.07, "🔵 加仓": 0.10, "🔴 做空": 0.03,
        "cap": 0.12,
        "max_loss": {"🟡 试探": 0.008, "🟢 确认": 0.012, "🔵 加仓": 0.015, "🔴 做空": 0.008},
    },
    "conservative": {
        "🟡 试探": 0.02, "🟢 确认": 0.05, "🔵 加仓": 0.08, "🔴 做空": 0.02,
        "cap": 0.10,
        "max_loss": {"🟡 试探": 0.005, "🟢 确认": 0.008, "🔵 加仓": 0.010, "🔴 做空": 0.005},
    },
}


def volatility_multiplier(atr_pct: float | None) -> tuple[float, str | None]:
    """Return (multiplier, note) for an ATR% value.

    None → (1.0, None). Missing ATR means no adjustment.
    """
    if atr_pct is None:
        return 1.0, None
    if atr_pct <= 3.0:
        return 1.0, None
    if atr_pct <= 5.0:
        return 0.80, f"ATR {atr_pct:.1f}% 偏高 → 仓位压缩 20%"
    if atr_pct <= 7.0:
        return 0.60, f"ATR {atr_pct:.1f}% 很高 → 仓位压缩 40%"
    return 0.40, f"ATR {atr_pct:.1f}% 极高 → 仓位压缩 60%"


def liquidity_multiplier(liquidity_class: str | None) -> tuple[float, str | None]:
    """Return (multiplier, note) for a liquidity class string.

    Accepted values: 'thin', 'moderate', 'liquid', or None/unknown.
    """
    if liquidity_class == "thin":
        return 0.60, "流动性薄 → 仓位压缩 40%（避免滑点 + 止损失真）"
    if liquidity_class == "moderate":
        return 0.85, "流动性中等 → 仓位压缩 15%"
    return 1.0, None


def size_positions(
    signals: list[dict],
    account_size: float,
    profile: str = "aggressive",
    atr_pct: float | None = None,
    liquidity_class: str | None = None,
) -> list[dict]:
    """Size positions for each signal with risk + vol + liquidity guards.

    Parameters:
        signals: list of Signal.to_dict() outputs from signals.py
        account_size: account in quote currency
        profile: 'aggressive' | 'balanced' | 'conservative'
        atr_pct: daily ATR as percent of price (e.g. 6.3 for 6.3%)
        liquidity_class: 'thin' | 'moderate' | 'liquid' | None
    """
    cfg = PROFILES.get(profile, PROFILES["aggressive"])
    cap = cfg["cap"]

    vol_mult, vol_note = volatility_multiplier(atr_pct)
    liq_mult, liq_note = liquidity_multiplier(liquidity_class)
    global_mult = vol_mult * liq_mult

    for sig in signals:
        tier = sig["tier"]
        base_tier_pct = cfg.get(tier, 0.05)

        # 1. Apply global (vol × liquidity) multiplier
        tier_pct = base_tier_pct * global_mult

        # 2. Never exceed hard cap
        tier_pct = min(tier_pct, cap)

        entry_mid = (sig["entry_range"][0] + sig["entry_range"][1]) / 2
        stop = sig["stop_loss"]
        if sig["side"] == "long":
            risk_per_share = max(entry_mid - stop, 0.01)
        else:
            risk_per_share = max(stop - entry_mid, 0.01)
        risk_pct_per_share = risk_per_share / entry_mid

        # 3. Max-loss guardrail — independent of vol/liquidity, protects tail
        max_loss_pct = cfg["max_loss"].get(tier, 0.01)
        loss_if_stopped_at_tier_pct = tier_pct * risk_pct_per_share
        shrunk_by_guard = False
        if loss_if_stopped_at_tier_pct > max_loss_pct:
            tier_pct = max_loss_pct / risk_pct_per_share
            shrunk_by_guard = True

        # 4. Final cap + floor
        tier_pct = min(tier_pct, cap)
        tier_pct = max(tier_pct, 0.0)

        position_usd = round(account_size * tier_pct, 2)
        shares = int(position_usd // entry_mid) if entry_mid > 0 else 0
        max_loss_usd = round(shares * risk_per_share, 2)
        max_loss_pct_actual = round(max_loss_usd / account_size, 4)

        notes = sig.setdefault("notes", [])
        if vol_note:
            notes.append(vol_note)
        if liq_note:
            notes.append(liq_note)
        if shrunk_by_guard:
            notes.append(
                f"仓位被止损保护压缩（原目标 {base_tier_pct:.0%} × "
                f"调整系数 {global_mult:.2f} → {tier_pct:.1%}，"
                f"避免单笔亏损超过账户 {max_loss_pct:.1%}）"
            )

        sig["position_pct"] = round(tier_pct, 4)
        sig["position_usd"] = position_usd
        sig["shares"] = shares
        sig["max_loss_usd"] = max_loss_usd
        sig["max_loss_pct_of_account"] = max_loss_pct_actual
        sig["risk_per_share"] = round(risk_per_share, 3)
        sig["size_adjustments"] = {
            "base_tier_pct": base_tier_pct,
            "volatility_multiplier": round(vol_mult, 2),
            "liquidity_multiplier": round(liq_mult, 2),
            "global_multiplier": round(global_mult, 2),
            "shrunk_by_max_loss_guard": shrunk_by_guard,
        }

    return signals


def summary_exposure(signals: list[dict]) -> dict:
    """Total exposure if all long/probe signals filled."""
    triggered = [s for s in signals if s["status"] in ("✅ 触发中",)]
    pending = [s for s in signals if s["status"] == "⏳ 待触发"]
    banned = [s for s in signals if s["status"] == "🚫 禁追"]
    return {
        "triggered_count": len(triggered),
        "pending_count": len(pending),
        "banned_count": len(banned),
        "triggered_exposure_pct": round(sum(s["position_pct"] for s in triggered), 4),
        "max_potential_exposure_pct": round(
            sum(s["position_pct"] for s in signals if s["status"] != "🚫 禁追"), 4
        ),
    }
