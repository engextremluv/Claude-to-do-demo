"""Paper-trading mode: walks the same simulation engine as the backtester,
bar by bar, printing live-style log lines and writing trades/equity to disk
incrementally as they happen. No real broker order is ever placed.

Since no live feed is wired up, "live" here means streaming a CSV of candles
in order (optionally paced with --speed) as a stand-in for a real-time feed —
this is what lets you watch the strategy trade before pointing it at a real
feed later.
"""

from __future__ import annotations

import csv
import os
import time

import pandas as pd

from .config import RiskConfig, StrategyConfig
from .engine import Trade, simulate
from .metrics import compute_metrics, format_report
from .strategy import generate_signals


def run_paper_trading(
    df: pd.DataFrame,
    strategy_cfg: StrategyConfig,
    risk_cfg: RiskConfig,
    output_dir: str,
    speed: float = 0.0,
    heartbeat_every: int = 60,
) -> dict:
    """Stream `df` bar by bar through the simulation engine.

    speed: seconds to sleep between bars (0 = run as fast as possible).
    heartbeat_every: print a status line every N bars even with no trade event.
    Returns the final metrics dict; also writes trades.csv and equity_curve.csv
    to output_dir (appended to as the run progresses).
    """
    os.makedirs(output_dir, exist_ok=True)
    trades_path = os.path.join(output_dir, "trades.csv")
    equity_path = os.path.join(output_dir, "equity_curve.csv")

    signals_df = generate_signals(df, strategy_cfg)

    trades: list[Trade] = []
    equity_rows = []

    trade_fields = ["direction", "entry_time", "entry_price", "exit_time", "exit_price", "lots", "exit_reason", "pnl"]
    with open(trades_path, "w", newline="", encoding="utf-8") as trades_fh, open(
        equity_path, "w", newline="", encoding="utf-8"
    ) as equity_fh:
        trades_writer = csv.DictWriter(trades_fh, fieldnames=trade_fields)
        trades_writer.writeheader()
        equity_writer = csv.writer(equity_fh)
        equity_writer.writerow(["time", "balance", "equity"])

        print(f"[paper] starting run: {len(df)} bars, initial balance ${risk_cfg.initial_balance:.2f}")

        for event in simulate(signals_df, risk_cfg):
            equity_rows.append({"time": event["time"], "balance": event["balance"], "equity": event["equity"]})
            equity_writer.writerow([event["time"], f"{event['balance']:.2f}", f"{event['equity']:.2f}"])

            if event["opened"] is not None:
                t = event["opened"]
                direction = "LONG" if t.direction == 1 else "SHORT"
                print(
                    f"[paper] {t.entry_time}  OPEN {direction:5s}  "
                    f"entry={t.entry_price:.2f}  sl={t.sl_price:.2f}  tp={t.tp_price:.2f}  lots={t.lots}"
                )

            if event["closed"] is not None:
                t = event["closed"]
                trades.append(t)
                direction = "LONG" if t.direction == 1 else "SHORT"
                print(
                    f"[paper] {t.exit_time}  CLOSE {direction:5s} ({t.exit_reason})  "
                    f"exit={t.exit_price:.2f}  pnl=${t.pnl:.2f}  balance=${event['balance']:.2f}"
                )
                trades_writer.writerow(
                    {
                        "direction": direction.lower(),
                        "entry_time": t.entry_time,
                        "entry_price": t.entry_price,
                        "exit_time": t.exit_time,
                        "exit_price": t.exit_price,
                        "lots": t.lots,
                        "exit_reason": t.exit_reason,
                        "pnl": t.pnl,
                    }
                )
                trades_fh.flush()

            if heartbeat_every and event["index"] % heartbeat_every == 0 and event["opened"] is None and event["closed"] is None:
                print(f"[paper] {event['time']}  close={event['close']:.2f}  equity=${event['equity']:.2f}")

            equity_fh.flush()
            if speed > 0:
                time.sleep(speed)

    equity_curve = pd.DataFrame(equity_rows)
    metrics = compute_metrics(trades, equity_curve, risk_cfg.initial_balance)
    print()
    print(format_report(metrics))
    return metrics
