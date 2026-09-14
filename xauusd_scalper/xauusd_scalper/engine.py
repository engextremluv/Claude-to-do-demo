"""Bar-by-bar trade simulation engine, shared by backtest and paper-trading modes.

Rules enforced here (not in the strategy):
  - A signal computed from bar N's close is only executed at bar N+1's open
    (no lookahead).
  - Entry/exit both pay half the configured spread, so a round trip costs the
    full spread — modeled against a mid-price close series.
  - Stop-loss is checked before take-profit when a single bar's high/low
    would satisfy both (the conservative assumption).
  - Only one position open at a time (max_open_positions is enforced at 1;
    scalping systems that pyramid are out of scope here).
  - Any position still open on the final bar is force-closed at that bar's
    close so metrics/equity always reflect a fully realized P&L.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional

import pandas as pd

from .config import RiskConfig
from .risk import calc_position_size


@dataclass
class Trade:
    direction: int  # 1 = long, -1 = short
    entry_time: pd.Timestamp
    entry_price: float
    lots: float
    sl_price: float
    tp_price: float
    exit_time: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None  # "sl" | "tp" | "eod"
    pnl: Optional[float] = None


def simulate(df: pd.DataFrame, risk_cfg: RiskConfig) -> Iterator[dict]:
    """Run the simulation over `df` (output of strategy.generate_signals).

    Yields one event dict per bar: {index, time, close, balance, equity,
    position, opened, closed}. `opened`/`closed` are Trade objects on the
    bars where those events happen, else None. `position` is the currently
    open Trade (or None) after processing that bar.
    """
    if risk_cfg.max_open_positions != 1:
        raise NotImplementedError("engine currently supports max_open_positions == 1 only")

    balance = risk_cfg.initial_balance
    position: Optional[Trade] = None
    pending_signal: Optional[tuple[int, float, float]] = None

    times = df.index
    n = len(df)
    half_spread = risk_cfg.spread / 2.0

    for i in range(n):
        row = df.iloc[i]
        opened: Optional[Trade] = None
        closed: Optional[Trade] = None

        if pending_signal is not None and position is None:
            direction, sl_dist, tp_dist = pending_signal
            entry_price = float(row["open"]) + direction * half_spread
            lots = calc_position_size(balance, sl_dist, risk_cfg)
            if lots > 0:
                position = Trade(
                    direction=direction,
                    entry_time=times[i],
                    entry_price=entry_price,
                    lots=lots,
                    sl_price=entry_price - direction * sl_dist,
                    tp_price=entry_price + direction * tp_dist,
                )
                opened = position
            pending_signal = None

        if position is not None:
            direction = position.direction
            if direction == 1:
                hit_sl = row["low"] <= position.sl_price
                hit_tp = row["high"] >= position.tp_price
            else:
                hit_sl = row["high"] >= position.sl_price
                hit_tp = row["low"] <= position.tp_price

            force_eod = position is not None and i == n - 1 and not (hit_sl or hit_tp)

            if hit_sl or hit_tp or force_eod:
                if hit_sl:
                    raw_exit, reason = position.sl_price, "sl"
                elif hit_tp:
                    raw_exit, reason = position.tp_price, "tp"
                else:
                    raw_exit, reason = float(row["close"]), "eod"

                exit_price = raw_exit - direction * half_spread
                pnl = direction * (exit_price - position.entry_price) * position.lots * risk_cfg.contract_size
                pnl -= risk_cfg.commission_per_lot * position.lots

                balance += pnl
                position.exit_time = times[i]
                position.exit_price = exit_price
                position.exit_reason = reason
                position.pnl = pnl
                closed = position
                position = None

        if position is None and pending_signal is None and i < n - 1:
            sig = int(row["signal"]) if pd.notna(row["signal"]) else 0
            if sig != 0 and pd.notna(row["sl_dist"]) and pd.notna(row["tp_dist"]):
                pending_signal = (sig, float(row["sl_dist"]), float(row["tp_dist"]))

        if position is not None:
            unrealized = (
                position.direction
                * (float(row["close"]) - position.entry_price)
                * position.lots
                * risk_cfg.contract_size
            )
            equity = balance + unrealized
        else:
            equity = balance

        yield {
            "index": i,
            "time": times[i],
            "close": float(row["close"]),
            "balance": balance,
            "equity": equity,
            "position": position,
            "opened": opened,
            "closed": closed,
        }
