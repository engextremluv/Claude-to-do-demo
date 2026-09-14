"""Position sizing for XAUUSD (quoted USD/oz, 1 standard lot = 100 oz)."""

from __future__ import annotations

from .config import RiskConfig


def calc_position_size(balance: float, stop_distance: float, cfg: RiskConfig) -> float:
    """Return the lot size that risks `risk_per_trade_pct` of `balance` if the
    stop-loss (`stop_distance` USD away from entry) is hit, rounded down to the
    nearest `lot_step` and clamped to [min_lot, max_lot].
    """
    if stop_distance <= 0:
        return 0.0

    risk_amount = balance * (cfg.risk_per_trade_pct / 100.0)
    raw_lots = risk_amount / (stop_distance * cfg.contract_size)

    steps = int(raw_lots / cfg.lot_step)
    lots = steps * cfg.lot_step
    lots = max(cfg.min_lot, min(cfg.max_lot, lots))
    return round(lots, 8)
