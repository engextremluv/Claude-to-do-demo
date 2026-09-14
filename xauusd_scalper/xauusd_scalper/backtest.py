"""Fast, full-run backtest: consumes the simulation engine to completion,
then reports metrics and writes trades/equity CSVs.
"""

from __future__ import annotations

import os

import pandas as pd

from .config import RiskConfig, StrategyConfig
from .engine import Trade, simulate
from .metrics import compute_metrics, format_report
from .strategy import generate_signals


class BacktestResult:
    def __init__(self, trades: list[Trade], equity_curve: pd.DataFrame, metrics: dict):
        self.trades = trades
        self.equity_curve = equity_curve
        self.metrics = metrics

    def trades_dataframe(self) -> pd.DataFrame:
        rows = [
            {
                "direction": "long" if t.direction == 1 else "short",
                "entry_time": t.entry_time,
                "entry_price": t.entry_price,
                "exit_time": t.exit_time,
                "exit_price": t.exit_price,
                "lots": t.lots,
                "exit_reason": t.exit_reason,
                "pnl": t.pnl,
            }
            for t in self.trades
        ]
        return pd.DataFrame(rows)

    def save(self, output_dir: str) -> None:
        os.makedirs(output_dir, exist_ok=True)
        self.trades_dataframe().to_csv(os.path.join(output_dir, "trades.csv"), index=False)
        self.equity_curve.to_csv(os.path.join(output_dir, "equity_curve.csv"), index=False)


def run_backtest(df: pd.DataFrame, strategy_cfg: StrategyConfig, risk_cfg: RiskConfig) -> BacktestResult:
    signals_df = generate_signals(df, strategy_cfg)

    trades: list[Trade] = []
    equity_rows = []
    for event in simulate(signals_df, risk_cfg):
        equity_rows.append({"time": event["time"], "balance": event["balance"], "equity": event["equity"]})
        if event["closed"] is not None:
            trades.append(event["closed"])

    equity_curve = pd.DataFrame(equity_rows)
    metrics = compute_metrics(trades, equity_curve, risk_cfg.initial_balance)
    return BacktestResult(trades, equity_curve, metrics)


__all__ = ["run_backtest", "BacktestResult", "format_report"]
